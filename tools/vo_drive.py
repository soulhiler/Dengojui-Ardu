#!/usr/bin/env python3
"""tools/vo_drive.py — Этап B.2: карта мира НА ХОДУ (VO + IMU + плотная глубина).

Робот едет вперёд короткими шагами; на каждом ключевом кадре:
  1) плотная метрическая глубина (Depth Anything × ToF);
  2) VO даёт СДВИГ между кадрами (поворот берём из IMU — надёжнее), поза 6-DOF растёт;
  3) плотная глубина вливается в WorldModel в координатах позы.
Так карта строится при ДВИЖЕНИИ (реальная трансляция), а не только поворотом.

⚠ БЕЗОПАСНОСТЬ: едет вперёд ТОЛЬКО если ToF спереди свободен (> STOP_MM). Cliff-
защиты нет (пол не калиброван) — НЕ оставляй у края стола, следи. Короткие шаги,
лимит шагов, гарантированный стоп. Нужен torch+transformers (HF_HUB_OFFLINE=1).

Запуск:  python tools/vo_drive.py [IP] [--steps 6] [--pwm 160]
"""
import io
import json
import math
import os
import sys
import time
import urllib.request

os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "spatial"))

import numpy as np                                          # noqa: E402
from PIL import Image                                       # noqa: E402

import depth_fusion as DF                                   # noqa: E402
import vo                                                   # noqa: E402
from tof_cloud import Pose                                  # noqa: E402
from world_model import WorldModel                          # noqa: E402

_pos = [a for a in sys.argv[1:] if not a.startswith("-")]
IP = _pos[0] if _pos else "192.168.1.104"


def _opt(k, d):
    return sys.argv[sys.argv.index(k) + 1] if k in sys.argv else d


STEPS = int(_opt("--steps", "6"))
PWM = int(_opt("--pwm", "160"))
STOP_MM = 400          # стоп, если впереди ближе (мм)
BURST_S = 0.4          # длительность импульса проезда (шаг ~7-15 см)
MIN_INLIERS = 30       # минимум инлайеров VO, чтобы доверять сдвигу
# Сенсоры (камера+ToF) смотрят ПРОТИВ моторного «+»: drive(+,+) едет НАЗАД относительно
# камеры. Чтобы ехать ТУДА, КУДА СМОТРИМ (ToF защищает путь, картируем впереди) — реверс.
DRIVE_FWD_SIGN = -1


def _g(p, t=8.0, tries=4):
    for _ in range(tries):
        try:
            return urllib.request.urlopen("http://%s%s" % (IP, p), timeout=t).read()
        except Exception:
            time.sleep(0.4)
    return None


def telem():
    r = _g("/telemetry", 4.0, 2)
    try:
        return json.loads(r.decode("utf-8")) if r else {}
    except Exception:
        return {}


def drive(l, r):
    _g("/drive?l=%d&r=%d" % (int(l), int(r)), 3.0, 1)


def stop():
    for _ in range(4):
        _g("/drive?stop=1", 3.0, 1)
        time.sleep(0.05)


def forward_burst():
    """Едем В СТОРОНУ СЕНСОРОВ (камера/ToF смотрят туда) — drive с DRIVE_FWD_SIGN."""
    p = DRIVE_FWD_SIGN * PWM
    t = time.time()
    while time.time() - t < BURST_S:
        drive(p, p)
        time.sleep(0.1)
    stop()


def check_direction():
    """Подтвердить, что едем В СТОРОНУ сенсоров: при коротком проезде ToF спереди
    должен УМЕНЬШИТЬСЯ (приближаемся к тому, что видим). True=ок/неизвестно,
    False=едем ОТ сенсоров (реверс неверен)."""
    d0 = telem().get("tof_mm")
    if not d0 or d0 <= 0:
        print("проверка направления: ToF без цели впереди — пропускаю (доверяю реверсу).")
        return True
    p = DRIVE_FWD_SIGN * PWM
    t = time.time()
    while time.time() - t < 0.25:
        drive(p, p)
        time.sleep(0.1)
    stop()
    time.sleep(0.8)
    d1 = telem().get("tof_mm") or 0
    print("проверка направления: ToF %d → %d мм" % (d0, d1))
    if d1 and d1 > d0 + 40:
        print("!! Едем ОТ сенсоров (ToF растёт) — реверс неверен. СТОП.")
        return False
    return True


def grab(backend):
    jpeg = _g("/capture", 8.0, 4)
    tofr = _g("/tof", 5.0, 4)
    tof = json.loads(tofr.decode("utf-8")) if tofr else {}
    tl = telem()
    md = DF.metric_depth_map(jpeg, tof, backend) if (jpeg and tof) else None
    iy = tl.get("imu_yaw") if tl.get("imu_ok") in (1, "1", True) else None
    return jpeg, md, iy, tl.get("tof_mm")


def main():
    backend = DF.DepthAnythingBackend()
    matcher = vo.OrbMatcher()
    model = WorldModel(voxel_m=0.05)
    _g("/control?tofprofile=accurate", 4.0, 2)   # 8×8 — плотнее глубина и точнее масштаб
    time.sleep(1.2)
    print("VO-DRIVE: ip=%s, шагов≤%d, стоп<%dмм. Едем В СТОРОНУ сенсоров (камера/ToF).\n" % (IP, STEPS, STOP_MM))
    if not check_direction():
        stop()
        _g("/control?tofprofile=auto", 4.0, 2)
        return

    pose = np.eye(4)
    prev = None          # (gray, Z, intr, imu_yaw)
    frames = 0
    try:
        for step in range(STEPS):
            jpeg, md, iy, fwd_mm = grab(backend)
            if md is None:
                print("  шаг %d: нет кадра/глубины — стоп" % step)
                break
            Z, gray, intr = md
            rgb = Image.open(io.BytesIO(jpeg)).convert("RGB")

            if prev is not None:
                p_gray, p_Z, p_intr, p_iy = prev
                imu_d = 0.0
                if isinstance(iy, (int, float)) and isinstance(p_iy, (int, float)):
                    imu_d = ((iy - p_iy + 180) % 360) - 180
                R = vo.R_yaw(math.radians(imu_d))
                rel = vo.relative_pose_rgbd({"gray": p_gray, "depth": p_Z},
                                            {"gray": gray, "depth": Z}, p_intr, matcher, R_prior=R)
                if rel and rel[1] >= MIN_INLIERS:
                    pose = pose @ rel[0]
                else:
                    n = rel[1] if rel else 0
                    print("  шаг %d: VO потерян (инлайеров %d) — стоп, чтобы не врать позе" % (step, n))
                    break

            yaw = vo.yaw_of(pose[:3, :3])
            P = Pose(yaw=yaw, tx=float(pose[0, 3]), tz=float(pose[2, 3]))
            # карвинг луча (origin = камера): чистит свободное место + копит miss-подпись
            model.integrate_frame_rays((P.tx, P.ty, P.tz),
                                       list(DF.dense_points_from_depth(Z, rgb, intr, P)))
            frames += 1
            st = model.stats()
            dist = math.hypot(pose[0, 3], pose[2, 3])
            print("  шаг %d: поза x=%.2f z=%.2f курс=%.0f° (пройдено %.2f м) | вокс=%d увер=%d впереди=%sмм"
                  % (step, pose[0, 3], pose[2, 3], math.degrees(yaw), dist,
                     st["voxels"], st["confident"], fwd_mm))

            prev = (gray, Z, intr, iy)

            # препятствие?
            if fwd_mm and 0 < fwd_mm < STOP_MM:
                print("  препятствие %dмм впереди — дальше не едем" % fwd_mm)
                break
            if step < STEPS - 1:
                forward_burst()
                time.sleep(0.9)              # дать вибрации/IMU успокоиться
    finally:
        stop()
        _g("/control?tofprofile=auto", 4.0, 2)

    st = model.stats()
    dist = math.hypot(pose[0, 3], pose[2, 3])
    print("\nИТОГ: кадров=%d пройдено≈%.2f м вокселей=%d уверенных=%d"
          % (frames, dist, st["voxels"], st["confident"]))
    here = os.path.dirname(os.path.abspath(__file__))
    out = os.path.join(here, "..", "spatial", "vo_drive_world.json.gz")
    model.save(out)
    print("Карта сохранена: spatial/vo_drive_world.json.gz")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        stop()
        print("\nпрервано — стоп")
