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
from world_model import WorldModel, L_CONFIDENT
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
        self.yaw_correction = 0.0  # поправка курса от релокализации по карте (рад)
        self.have_odom = False
        self.have_imu = False

    def _compose_pose(self) -> Pose:
        """Текущая поза с учётом поправки релокализации и ручного yaw."""
        eff = _wrap_pi(self.yaw + self.yaw_correction)
        return Pose(yaw=eff + math.radians(self.manual_yaw_deg), tx=self.x, tz=self.z)

    def peek_pose(self) -> Pose:
        """Поза по текущему состоянию, без потребления телеметрии (после nudge_yaw)."""
        return self._compose_pose()

    def nudge_yaw(self, delta_rad: float, gain: float = 0.25):
        """Подвинуть поправку курса на долю найденной релокализацией невязки δ.
        Малый gain + демпфирование: карта мягко де-дрейфит гиро, не дёргая позу."""
        self.yaw_correction = _wrap_pi(self.yaw_correction + gain * delta_rad)
        return self.yaw_correction

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
                eff = _wrap_pi(self.yaw + self.yaw_correction)
                self.x += d * math.sin(eff)
                self.z += d * math.cos(eff)
            self._el = el
            self._er = er
        return self._compose_pose()


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


def relocalize_yaw(model: WorldModel, robot_pts, tx: float, tz: float, base_yaw: float,
                   window_deg: float = 30.0, coarse_deg: float = 3.0,
                   fine_deg: float = 0.5, sigma_m: float = 0.06):
    """РЕЛОКАЛИЗАЦИЯ КУРСА ПО КАРТЕ (correlative scan matching, Olson 2009).
    Ищет поправку δ (рад) вокруг base_yaw, при которой текущий скан (robot_pts:
    (x,y,z) в кадре робота) лучше всего ложится на карту. Поза ~1-DOF (turntable),
    поэтому перебор по одному углу.

    Метод: COARSE-TO-FINE по likelihood-field скору (гладкое поле, шире бассейн) +
    ПАРАБОЛИЧЕСКАЯ интерполяция пика → суб-градусная точность. Грубый проход даёт
    margin (уверенность пика); тонкий — точный угол.

    Возвращает dict {delta_rad, delta_deg, score, margin, hits, n} или None.
    margin = (пик − медиана)/пик ∈ [0..1] — насколько пик выделяется (для guard).
    """
    if not robot_pts:
        return None

    def score_at(deg):
        p = Pose(yaw=base_yaw + math.radians(deg), tx=tx, tz=tz)
        wpts = [apply_pose(x, y, z, p) for (x, y, z) in robot_pts]
        return model.likelihood_score(wpts, sigma_m=sigma_m)

    # --- грубый проход по всему окну ---
    nc = max(1, int(round(window_deg / coarse_deg)))
    coarse = []
    best = (-1.0, 0.0, 0)        # (score, deg, hits)
    for i in range(-nc, nc + 1):
        deg = i * coarse_deg
        s, h = score_at(deg)
        coarse.append(s)
        if s > best[0]:
            best = (s, deg, h)
    if best[0] <= 0.0:
        return None
    ordered = sorted(coarse)
    median = ordered[len(ordered) // 2]
    margin = (best[0] - median) / best[0] if best[0] > 0 else 0.0

    # --- тонкий проход вокруг грубого пика ---
    nf = max(1, int(round(coarse_deg / fine_deg)))
    fine = []
    fbest_i, fbest = 0, (-1.0, 0.0, 0)
    for k in range(-nf, nf + 1):
        deg = best[1] + k * fine_deg
        s, h = score_at(deg)
        fine.append((deg, s))
        if s > fbest[0]:
            fbest, fbest_i = (s, deg, h), len(fine) - 1

    # --- параболическая интерполяция вершины (суб-градус) ---
    deg_best = fbest[1]
    if 0 < fbest_i < len(fine) - 1:
        sm, s0, sp = fine[fbest_i - 1][1], fine[fbest_i][1], fine[fbest_i + 1][1]
        den = sm - 2.0 * s0 + sp
        if den < 0.0:                      # вогнутость = настоящий максимум
            off = 0.5 * (sm - sp) / den
            off = max(-1.0, min(1.0, off))
            deg_best = fine[fbest_i][0] + off * fine_deg

    return {
        "delta_rad": math.radians(deg_best),
        "delta_deg": round(deg_best, 2),
        "score": round(fbest[0], 2),
        "margin": round(margin, 3),
        "hits": fbest[2],
        "n": len(robot_pts),
    }


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
        # --- релокализация курса по карте (де-дрейф гиро) ---
        self.reloc_enabled = True
        self.reloc_every = 5            # пробовать раз в N кадров
        self.reloc_gain = 0.25          # доля найденной невязки, применяемая за раз (демпфирование)
        self.reloc_min_conf = 40        # минимум уверенных вокселей карты, чтобы было с чем матчить
        self.reloc_min_pts = 4          # минимум валидных точек в скане
        self.reloc_min_margin = 0.35    # минимальная уверенность пика: отсекает рот-неоднозначную
                                        # геометрию (плоская стена ~0.23 -> НЕ корректируем; различимая ~0.9)
        self.reloc_window_deg = 25.0
        self.last_reloc = {}            # последний результат (для статуса/диагностики)
        self.reloc_count = 0

    def _maybe_relocalize(self, robot_pts, pose: Pose) -> Pose:
        """Если включено и накоплено достаточно карты — поправить курс по карте.
        Вызывать ДО вливания текущего кадра (иначе скан тривиально матчит сам себя)."""
        if not (self.reloc_enabled and robot_pts and len(robot_pts) >= self.reloc_min_pts):
            return pose
        with self.lock:
            conf = sum(1 for v in self.model.vox.values() if v[0] >= L_CONFIDENT)
            if conf < self.reloc_min_conf:
                return pose
            rl = relocalize_yaw(self.model, robot_pts, pose.tx, pose.tz, pose.yaw,
                                window_deg=self.reloc_window_deg)
        if not rl:
            return pose
        self.last_reloc = rl
        if rl["margin"] >= self.reloc_min_margin and rl["hits"] >= self.reloc_min_pts \
                and rl["delta_deg"] != 0.0:
            self.pose.nudge_yaw(rl["delta_rad"], self.reloc_gain)
            self.reloc_count += 1
            return self.pose.peek_pose()
        return pose

    def relocalize_once(self) -> dict:
        """Ручной одиночный матч по последнему кадру — для кнопки/эндпоинта.
        Применяет поправку с полным усилением (gain=1), чтобы было видно эффект."""
        ip = None
        try:
            ip = self.get_ip()
        except Exception:
            ip = None
        if not ip:
            return {"ok": 0, "error": "нет IP платы"}
        try:
            tof = fetch_tof(ip, timeout=4.0)
            telem = fetch_telemetry(ip, timeout=3.0)
        except Exception as e:
            return {"ok": 0, "error": repr(e)}
        pose = self.pose.update(telem)
        res = int(tof.get("res", 8))
        grid = tof.get("grid") or []
        robot_pts = ([(x, y, z) for (r, c, x, y, z) in grid_to_points(grid, res, self.cfg)]
                     if len(grid) >= res * res else [])
        with self.lock:
            conf = sum(1 for v in self.model.vox.values() if v[0] >= L_CONFIDENT)
            rl = relocalize_yaw(self.model, robot_pts, pose.tx, pose.tz, pose.yaw,
                                window_deg=self.reloc_window_deg) if robot_pts else None
        if not rl:
            return {"ok": 0, "error": "мало данных", "conf": conf, "n": len(robot_pts)}
        applied = False
        if rl["margin"] >= self.reloc_min_margin and rl["hits"] >= self.reloc_min_pts:
            self.pose.nudge_yaw(rl["delta_rad"], 1.0)
            self.reloc_count += 1
            applied = True
        self.last_reloc = rl
        return {"ok": 1, "applied": applied, "conf": conf, **rl}

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
                # Релокализация курса по карте (де-дрейф гиро) — ДО вливания кадра,
                # раз в reloc_every кадров, чтобы скан не матчил сам себя.
                if self.reloc_enabled and (self.frames % self.reloc_every == 0):
                    res = int(tof.get("res", 8))
                    grid = tof.get("grid") or []
                    if len(grid) >= res * res:
                        robot_pts = [(x, y, z) for (r, c, x, y, z)
                                     in grid_to_points(grid, res, self.cfg)]
                        pose = self._maybe_relocalize(robot_pts, pose)
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
        eff = _wrap_pi(self.pose.yaw + self.pose.yaw_correction)  # курс с поправкой релокализации
        st.update({
            "running": self.running,
            "paused": self.paused,
            "frames": self.frames,
            "last_added": self.last_added,
            "last_error": self.last_error,
            "have_odom": self.pose.have_odom,
            "have_imu": self.pose.have_imu,
            "pose": [round(self.pose.x, 3), round(self.pose.z, 3), round(eff, 3)],
            "heading_deg": round(math.degrees(eff), 1),
            "reloc_enabled": self.reloc_enabled,
            "reloc_count": self.reloc_count,
            "reloc_corr_deg": round(math.degrees(self.pose.yaw_correction), 1),
            "last_reloc": self.last_reloc or None,
            "age_s": round(time.time() - self.last_t, 1) if self.last_t else None,
        })
        return st
