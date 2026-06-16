"""
spatial.world_service — АВТОНОМНЫЙ сервис мировой модели.

Фоновый поток: сам тянет кадры (камера + ToF + телеметрия) с платы, оценивает
позу робота (best-effort, подключаемо), вливает в WorldModel, периодически
сохраняет на диск и логирует сырую сессию (для офлайн-пересборки). Всё, что
пришло — копится, уточняется, сохраняется и доступно для ориентации.

Поза сейчас: КУРС от IMU MPU6050 (imu_yaw, приоритетно; знак калиброван —
IMU_YAW_SIGN), дистанция — дифф-одометрия по энкодерам (когда разведём), ручной
yaw — fallback без датчиков. С IMU turntable-скан и поворот курса автоматические.
Без энкодеров позиция (x,z) не растёт — модель копится поворотом с одной точки
(turntable). Это осознанное ограничение «soft-скаффолда», структура к апгрейду готова.
Проверка/калибровка конвейера: spatial/calibrate_live.py.
"""
from __future__ import annotations

import io
import json
import math
import os
import threading
import time

from tof_cloud import CloudConfig, Pose, apply_pose, grid_to_points, zone_pixel
from world_model import WorldModel
from xiao_client import fetch_capture, fetch_telemetry, fetch_tof

try:
    from PIL import Image
    _HAVE_PIL = True
except ImportError:
    _HAVE_PIL = False


def _dist_color(z_m: float, zmax: float = 3.5):
    t = max(0.0, min(1.0, z_m / zmax))
    return (int(255 * t), 80, int(255 * (1 - t)))


# Знак курса IMU относительно кадра карты (x-вправо, z-вперёд; apply_pose: world_az = robot_az + yaw).
# КАЛИБРОВКА 2026-06-16 (spatial/calibrate_live.py): поворот ПО ЧАСОВОЙ (вправо, вид сверху)
# даёт imu_gz<0 и УМЕНЬШЕНИЕ imu_yaw, а для совпадения карты по часовой нужен +yaw —
# поэтому курс IMU инвертируем. Иначе при повороте сцена «складывается» зеркально.
IMU_YAW_SIGN = -1.0


def _wrap_pi(a: float) -> float:
    """Угол в (-pi, pi]."""
    while a > math.pi:
        a -= 2 * math.pi
    while a <= -math.pi:
        a += 2 * math.pi
    return a


class PoseEstimator:
    """Best-effort поза робота. Источники курса/позиции:
    - IMU MPU6050 (imu_yaw из телеметрии) -> КУРС (приоритетно; абсолютный по гиро,
      медленно дрейфует — связка с энкодерами: гиро ведёт сквозь проскальзывание);
    - энкодеры enc_l/enc_r -> дистанция всегда; курс — только если IMU нет;
    - ручной yaw (turntable/скан) — manual_yaw_deg (fallback без датчиков).
    Wi-Fi-якорь позиции (сброс дрейфа x/z) — отдельно (см. docs/roadmap.md).
    """

    def __init__(self, wheel_base_m: float = 0.13, ticks_per_m: float = 1000.0):
        self.x = 0.0
        self.z = 0.0
        self.yaw = 0.0           # рад, курс в мире
        self.wheel_base = wheel_base_m
        self.ticks_per_m = ticks_per_m
        self._el = None
        self._er = None
        self._imu_yaw0 = None    # опорный yaw IMU (рад) — нулевая отсчётная точка курса
        self._imu_yaw_raw = None  # последний сырой курс IMU (рад) — для recenter()
        self.manual_yaw_deg = 0.0
        self.have_odom = False
        self.have_imu = False

    def recenter(self):
        """Принять ТЕКУЩИЙ курс за ноль («робот смотрит вперёд»).
        Полезно для turntable-скана: опора _imu_yaw0 по умолчанию фиксируется
        на первом кадре сервиса (часто раньше, чем робот нацелен). Сбрасывает
        и накопленный yaw, и ручной yaw-офсет."""
        if self._imu_yaw_raw is not None:
            self._imu_yaw0 = self._imu_yaw_raw
        self.yaw = 0.0
        self.manual_yaw_deg = 0.0
        return round(math.degrees(self._imu_yaw_raw), 1) if self._imu_yaw_raw is not None else None

    def update(self, telem: dict) -> Pose:
        # --- Курс: приоритет IMU (imu_ok + imu_yaw в градусах) ---
        imu_yaw = telem.get("imu_yaw")
        if telem.get("imu_ok") in (1, "1", True) and isinstance(imu_yaw, (int, float)):
            self.have_imu = True
            iy = math.radians(imu_yaw)
            self._imu_yaw_raw = iy
            if self._imu_yaw0 is None:
                self._imu_yaw0 = iy
            self.yaw = _wrap_pi(IMU_YAW_SIGN * (iy - self._imu_yaw0))

        # --- Энкодеры: дистанция всегда; курс — только без IMU ---
        el = telem.get("enc_l")
        er = telem.get("enc_r")
        if isinstance(el, (int, float)) and isinstance(er, (int, float)):
            if self._el is not None and (el != self._el or er != self._er):
                self.have_odom = True
                dl = (el - self._el) / self.ticks_per_m
                dr = (er - self._er) / self.ticks_per_m
                d = (dl + dr) / 2.0
                if not self.have_imu:
                    self.yaw = _wrap_pi(self.yaw + (dr - dl) / self.wheel_base)
                self.x += d * math.sin(self.yaw)
                self.z += d * math.cos(self.yaw)
            self._el = el
            self._er = er
        return Pose(yaw=self.yaw + math.radians(self.manual_yaw_deg), tx=self.x, tz=self.z)


def frame_to_world(tof: dict, jpeg: bytes, pose: Pose, cfg: CloudConfig):
    """Кадр (ToF-сетка + JPEG) + поза -> генератор (x, y, z, (r,g,b)) в мире."""
    res = int(tof.get("res", 8))
    grid = tof.get("grid")
    if not grid or len(grid) < res * res:
        return
    img = None
    if _HAVE_PIL and jpeg:
        try:
            img = Image.open(io.BytesIO(jpeg)).convert("RGB")
        except Exception:
            img = None
    w, h = (img.size if img is not None else (0, 0))
    for (r, c, x, y, z) in grid_to_points(grid, res, cfg):
        if img is not None:
            u, v = zone_pixel(r, c, res, w, h, cfg)
            rgb = img.getpixel((u, v))
        else:
            rgb = _dist_color(z)
        wx, wy, wz = apply_pose(x, y, z, pose)
        yield (wx, wy, wz, rgb)


class WorldService:
    """Фоновый автономный построитель модели."""

    def __init__(self, model: WorldModel, get_ip, world_path: str,
                 sessions_dir: str | None = None, interval_s: float = 2.0,
                 cfg: CloudConfig | None = None):
        self.model = model
        self.get_ip = get_ip                # callable -> ip|None
        self.world_path = world_path
        self.sessions_dir = sessions_dir
        self.interval = interval_s
        self.cfg = cfg or CloudConfig()
        self.pose = PoseEstimator()
        self.lock = threading.Lock()
        self.running = False
        self.paused = False
        self._thread = None
        self._session = None
        self._session_n = 0
        self.last_added = 0
        self.last_error = ""
        self.last_t = 0.0
        self.frames = 0
        self._dirty = 0
        self._last_save = 0.0

    # --- управление ---
    def start(self):
        if self.running:
            return
        self.running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        self.running = False

    def set_paused(self, p: bool):
        self.paused = bool(p)

    def _open_session(self):
        if not self.sessions_dir:
            return
        stamp = time.strftime("%Y%m%d-%H%M%S")
        self._session = os.path.join(self.sessions_dir, stamp)
        os.makedirs(self._session, exist_ok=True)
        self._session_n = 0

    def _log_frame(self, jpeg: bytes, tof: dict, pose: Pose):
        if not self._session:
            return
        try:
            idx = self._session_n
            if jpeg:
                with open(os.path.join(self._session, f"f{idx:04d}.jpg"), "wb") as f:
                    f.write(jpeg)
            with open(os.path.join(self._session, "frames.jsonl"), "a", encoding="utf-8") as f:
                f.write(json.dumps({"i": idx, "t": round(time.time(), 1), "tof": tof,
                                    "pose": [pose.tx, pose.tz, pose.yaw]}) + "\n")
            self._session_n += 1
        except Exception:
            pass

    # --- основной цикл ---
    def _loop(self):
        if self.sessions_dir:
            self._open_session()
        while self.running:
            t0 = time.time()
            if self.paused:
                time.sleep(0.3)
                continue
            ip = None
            try:
                ip = self.get_ip()
            except Exception:
                ip = None
            if not ip:
                self.last_error = "нет IP платы"
                time.sleep(max(1.0, self.interval))
                continue
            try:
                tof = fetch_tof(ip, timeout=5.0)
                jpeg = b""
                try:
                    jpeg = fetch_capture(ip, timeout=6.0)
                except Exception:
                    jpeg = b""  # без камеры — копим геометрию, цвет по дистанции
                telem = {}
                try:
                    telem = fetch_telemetry(ip, timeout=4.0)
                except Exception:
                    telem = {}
                pose = self.pose.update(telem)
                pts = list(frame_to_world(tof, jpeg, pose, self.cfg))
                with self.lock:
                    added = self.model.integrate_frame(pts)
                self.last_added = added
                self.frames += 1
                self._dirty += added
                self.last_error = ""
                self.last_t = time.time()
                self._log_frame(jpeg, tof, pose)
                # периодическое сохранение (раз в ~10 с при наличии новизны)
                if self._dirty > 0 and (self.last_t - self._last_save) > 10.0:
                    with self.lock:
                        self.model.save(self.world_path)
                    self._last_save = self.last_t
                    self._dirty = 0
            except Exception as e:
                self.last_error = repr(e)
            dt = time.time() - t0
            time.sleep(max(0.05, self.interval - dt))

    def status(self) -> dict:
        with self.lock:
            st = self.model.stats()
        st.update({
            "running": self.running,
            "paused": self.paused,
            "frames": self.frames,
            "last_added": self.last_added,
            "last_error": self.last_error,
            "have_odom": self.pose.have_odom,
            "have_imu": self.pose.have_imu,
            "pose": [round(self.pose.x, 3), round(self.pose.z, 3), round(self.pose.yaw, 3)],
            "heading_deg": round(math.degrees(self.pose.yaw), 1),
            "age_s": round(time.time() - self.last_t, 1) if self.last_t else None,
        })
        return st
