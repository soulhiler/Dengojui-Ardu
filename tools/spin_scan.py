#!/usr/bin/env python3
"""tools/spin_scan.py — ИМПУЛЬСНЫЙ turntable-скан: робот сам строит панораму.

Энкодеров нет → отслеживаем только ВРАЩЕНИЕ (IMU MPU6050 даёт курс); поступательно
НЕ едем (без энкодеров карта «поехала» бы). Робот вращается на месте — безопасно.

Цикл «шаг»: короткий импульс вращения → СТОП → пауза (вибрация гаснет) → снять
ToF+курс (без моторного шума — чище данные) → влить в карту → релокализация курса
по карте (де-дрейф гиро) → повтор. По завершении/ошибке/Ctrl-C → гарантированный стоп.

Запуск:  python tools/spin_scan.py [IP] [секунд]
"""
import json
import math
import os
import sys
import time
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "spatial"))

from tof_cloud import CloudConfig, grid_to_points          # noqa: E402
from world_model import WorldModel, L_CONFIDENT            # noqa: E402
from world_service import PoseEstimator, frame_to_world, relocalize_yaw  # noqa: E402

_pos = [a for a in sys.argv[1:] if not a.startswith("-")]
IP = _pos[0] if _pos else "192.168.1.104"
DURATION = float(_pos[1]) if len(_pos) > 1 else 60.0

STEP_DEG = 20.0         # целевой шаг поворота (меньше FoV 60° → сканы перекрываются)
BURST_S = 0.12          # стартовая длит. импульса (уточняется по замеренной скорости)
SETTLE_S = 0.4          # пауза после стопа — гасим вибрацию перед замером
RELOC_EVERY = 3         # релокализация раз в N шагов
RELOC_GAIN = 0.25
RELOC_MIN_CONF = 30     # порог демо-скана (у сервиса дефолт 40); guard по margin защищает
RELOC_MIN_MARGIN = 0.35


def _http(path, timeout=4.0):
    try:
        with urllib.request.urlopen("http://%s%s" % (IP, path), timeout=timeout) as r:
            return r.read()
    except Exception:
        return None


def drive(l, r):
    _http("/drive?l=%d&r=%d" % (int(l), int(r)), timeout=3.0)


def stop():
    for _ in range(3):
        _http("/drive?stop=1", timeout=3.0)
        time.sleep(0.05)


def set_tof_profile(name):
    """Профиль ToF (прошивка ≥1.4.1): accurate=8×8 (64 зоны, плотнее карта), auto=вернуть.
    На старых прошивках аргумент игнорируется — безвредно."""
    _http("/control?tofprofile=%s" % name, timeout=3.0)


def telem(retries=2):
    for _ in range(retries):
        raw = _http("/telemetry", timeout=2.5)
        if raw:
            try:
                return json.loads(raw.decode("utf-8"))
            except Exception:
                pass
    return {}


def tof(retries=2):
    for _ in range(retries):
        raw = _http("/tof", timeout=2.5)
        if raw:
            try:
                return json.loads(raw.decode("utf-8"))
            except Exception:
                pass
    return {}


def burst(pwm, direction, dur=BURST_S):
    """Крутнуть импульс, подкармливая watchdog, затем стоп."""
    t = time.time()
    while time.time() - t < dur:
        drive(pwm * direction, -pwm * direction)
        time.sleep(0.1)
    stop()


def confident(model):
    return sum(1 for v in model.vox.values() if v[0] >= L_CONFIDENT)


def main():
    cfg = CloudConfig()
    model = WorldModel(voxel_m=0.05)
    pose_est = PoseEstimator()

    # --- проба: подобрать PWM, при котором импульс реально поворачивает (по Δкурса) ---
    print("IP=%s, скан %.0f c. Проба поворота импульсом..." % (IP, DURATION))
    pwm = 130
    ok = False
    burst_dur = BURST_S
    for _ in range(4):
        y0 = telem().get("imu_yaw")
        burst(pwm, 1, dur=0.35)
        time.sleep(0.4)
        y1 = telem().get("imu_yaw")
        d = None
        if isinstance(y0, (int, float)) and isinstance(y1, (int, float)):
            d = abs(((y1 - y0 + 180) % 360) - 180)
        print("  PWM=%d -> Δкурс=%s° за 0.35 c" % (pwm, None if d is None else round(d, 1)))
        if d is not None and d > 4:
            ok = True
            rate = d / 0.35                                   # °/с
            burst_dur = max(0.08, min(0.3, STEP_DEG / rate))  # импульс под шаг ~STEP_DEG
            break
        pwm = min(255, pwm + 40)
    stop()
    if not ok:
        print("Импульс не поворачивает робота (PWM=%d). Питание моторов? Стоп." % pwm)
        return 1
    set_tof_profile("accurate")   # 8×8 для плотной карты (прошивка ≥1.4.1)

    # СЕРВЕРНЫЙ ZUPT: ноль гиро после ребута может быть кривым (если робот двигался
    # в момент калибровки на старте). Робот сейчас стоит — измеряем дрейф курса и
    # дальше ВЫЧИТАЕМ его линейно (bias ~постоянен за скан). Не нужен идеальный ребут.
    stop()
    time.sleep(0.8)
    ya = telem().get("imu_yaw")
    time.sleep(4.0)
    yb = telem().get("imu_yaw")
    drift_t0 = time.time()
    drift_rate = 0.0
    if isinstance(ya, (int, float)) and isinstance(yb, (int, float)):
        drift_rate = (((yb - ya + 180) % 360) - 180) / 4.0
    tofj = telem()
    print("Дрейф нуля гиро = %.2f °/с -> компенсирую линейно. ToF res=%s."
          % (drift_rate, tofj.get("tof_res")))
    print("Поворот PWM=%d, импульс %.2f c (~%.0f°/шаг). Старт скана.\n"
          % (pwm, burst_dur, STEP_DEG))

    steps = 0
    frames = 0
    reloc_n = 0
    direction = 1
    pose = pose_est.peek_pose()
    t0 = time.time()
    last_report = t0
    try:
        while time.time() - t0 < DURATION:
            burst(pwm, direction, burst_dur)   # шаг поворота + стоп
            time.sleep(SETTLE_S)               # гасим вибрацию
            steps += 1
            # каждые ~360° меняем направление (туда-обратно по панораме)
            if steps % max(1, int(round(360.0 / STEP_DEG))) == 0:
                direction *= -1
            tl = telem()
            tf = tof()
            iy = tl.get("imu_yaw")             # вычесть накопленный дрейф нуля гиро
            if isinstance(iy, (int, float)):
                tl["imu_yaw"] = iy - drift_rate * (time.time() - drift_t0)
            pose = pose_est.update(tl)
            res = int(tf.get("res", 8))
            grid = tf.get("grid") or []
            if len(grid) >= res * res:
                rpts = [(x, y, z) for (r, c, x, y, z) in grid_to_points(grid, res, cfg)]
                conf = confident(model)
                if steps % RELOC_EVERY == 0 and conf >= RELOC_MIN_CONF and len(rpts) >= 4:
                    rl = relocalize_yaw(model, rpts, pose.tx, pose.tz, pose.yaw, window_deg=25)
                    if rl and rl["margin"] >= RELOC_MIN_MARGIN and rl["hits"] >= 4 \
                            and rl["delta_deg"] != 0.0:
                        pose_est.nudge_yaw(rl["delta_rad"], RELOC_GAIN)
                        reloc_n += 1
                        pose = pose_est.peek_pose()
                model.integrate_frame(list(frame_to_world(tf, b"", pose, cfg)))
                frames += 1
            now = time.time()
            if now - last_report > 4.0:
                last_report = now
                print("t=%4.1f шаг=%d курс=%6.1f° вокс=%4d увер=%4d точек=%d reloc#=%d поправка=%+.1f°"
                      % (now - t0, steps, math.degrees(pose_est.yaw), len(model.vox),
                         confident(model), frames, reloc_n,
                         math.degrees(pose_est.yaw_correction)))
    finally:
        stop()
        stop()
        set_tof_profile("auto")   # вернуть авто-профиль ToF

    conf = confident(model)
    print("\nИТОГ: шагов=%d кадров=%d вокселей=%d уверенных=%d reloc=%d поправка_курса=%+.1f°"
          % (steps, frames, len(model.vox), conf, reloc_n,
             math.degrees(pose_est.yaw_correction)))
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "spatial", "spin_scan_world.json.gz")
    try:
        n = model.save(out)
        print("Панорама сохранена: %s (%d вокселей)" % (os.path.abspath(out), n))
    except Exception as e:
        print("сохранить не удалось:", e)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        stop()
        print("\nпрервано — моторы остановлены")
        sys.exit(1)
