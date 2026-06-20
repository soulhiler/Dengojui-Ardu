#!/usr/bin/env python3
"""tools/heading_drive.py — управляемая езда: держим КУРС по IMU (closed-loop).

Робота уводит вбок (дисбаланс моторов) — «прямо» едет дугой. Здесь P-регулятор:
ошибка курса (IMU) → дифференциал моторов. Знак руления авто-калибруется. Едем
вперёд = В СТОРОНУ сенсоров (DRIVE_FWD_SIGN), с ToF-стопом перед препятствием.

Статус (2026-06-19): работает в принципе — поворот на месте сводит ошибку курса
(45°→~9°), калибровка знака надёжна. ОГРАНИЧЕНИЕ: IMU (MPU6050) дёргается при
бросках тока моторов (общее питание с моторами) — у закрытого контура дропауты
курса. Полная стабильность требует РАЗВЯЗКИ ПИТАНИЯ (декаплинг-конденсатор на
MPU6050 / отдельное питание моторов) или более ёмкой/заряженной батареи. Калибровку
руления делаем мягко на месте (fwd=0), не на форварде (там диф давал ~240 PWM =
бросок тока). Строительный блок для B.2 (картирование в управляемом проезде).

Запуск:
  python tools/heading_drive.py [IP] [--sec 6] [--base 140] [--turn ГРАД]
    --turn: повернуться на ГРАД (на месте) и держать; иначе держим текущий курс и едем.
"""
import json
import math
import sys
import time
import urllib.request

IP = next((a for a in sys.argv[1:] if not a.startswith("-")), "192.168.1.104")


def _opt(k, d):
    return sys.argv[sys.argv.index(k) + 1] if k in sys.argv else d


SEC = float(_opt("--sec", "6"))
BASE = int(_opt("--base", "140"))
TURN = _opt("--turn", None)          # если задан — поворот на месте на N° и удержание
DRIVE_FWD_SIGN = -1                  # сенсоры смотрят против моторного «+» (см. vo_drive.py)
KP = 4.0                             # PWM на градус ошибки курса
STEER_MAX = 130                      # ограничение дифференциала
MIN_MOVE = 78                        # мин. PWM, чтобы мотор тронулся (стикшн) — для поворота на месте
TOL_DEG = 4.0                        # допуск: ошибку меньше не давим (без чаттера)
STOP_MM = 400


def _g(p, t=3.0, tries=2):
    for _ in range(tries):
        try:
            return urllib.request.urlopen("http://%s%s" % (IP, p), timeout=t).read()
        except Exception:
            time.sleep(0.2)
    return None


def telem():
    r = _g("/telemetry", 4.0, 2)
    try:
        return json.loads(r.decode("utf-8")) if r else {}
    except Exception:
        return {}


def imu_yaw():
    j = telem()
    if j.get("imu_ok") in (1, "1", True) and isinstance(j.get("imu_yaw"), (int, float)):
        return float(j["imu_yaw"])
    return None


def imu_yaw_wait(tries=10):
    for _ in range(tries):
        y = imu_yaw()
        if y is not None:
            return y
        time.sleep(0.2)
    return None


def drive(l, r):
    l = max(-255, min(255, int(l)))
    r = max(-255, min(255, int(r)))
    _g("/drive?l=%d&r=%d" % (l, r), 3.0, 1)


def stop():
    for _ in range(4):
        _g("/drive?stop=1", 3.0, 1)
        time.sleep(0.05)


def wrap180(a):
    return ((a + 180) % 360) - 180


def calibrate_steer_sign(fwd):
    """Дифференциал s>0 (l=fwd+s, r=fwd-s) -> курс растёт или падает? Возвращает
    cal=+1/-1 так, что s = cal·Kp·error гонит ошибку к нулю. None если IMU молчит."""
    y0 = imu_yaw_wait()
    if y0 is None:
        return None
    D = 90
    t = time.time()
    while time.time() - t < 0.5:
        drive(fwd + D, fwd - D)
        time.sleep(0.1)
    stop()
    time.sleep(0.7)
    y1 = imu_yaw_wait()
    if y1 is None:
        return None
    dy = wrap180(y1 - y0)
    print("калибровка руления: s=+90 -> Δкурс=%.1f°" % dy)
    if abs(dy) < 2:
        return None
    return 1.0 if dy > 0 else -1.0


def control(target, seconds, fwd, cal, log_label="едем"):
    """P-удержание курса target в течение seconds. fwd=0 → поворот на месте.
    Возвращает (max|ошибка|, реально). ToF-стоп при езде."""
    t0 = time.time()
    last_report = t0
    errs = []
    last_l = last_r = 0
    reached = 0
    while time.time() - t0 < seconds:
        y = imu_yaw()
        if y is not None:
            e = wrap180(target - y)
            errs.append(abs(e))
            if abs(e) <= TOL_DEG:
                s = 0.0
                reached += 1
            else:
                reached = 0
                s = max(-STEER_MAX, min(STEER_MAX, cal * KP * e))
                if fwd == 0 and abs(s) < MIN_MOVE:     # стикшн: поворот на месте требует мин. PWM
                    s = MIN_MOVE if s > 0 else -MIN_MOVE
            last_l, last_r = fwd + s, fwd - s
            if fwd == 0 and reached >= 4:               # поворот: цель удержана — готово
                break
        drive(last_l, last_r)          # на дропауте IMU держим прошлую команду
        if fwd != 0:
            fm = telem().get("tof_mm")
            if fm and 0 < fm < STOP_MM:
                stop()
                print("  препятствие %dмм — стоп" % fm)
                break
        now = time.time()
        if now - last_report > 1.5:
            last_report = now
            print("  t=%.1f курс=%s цель=%.0f ошибка=%s°" % (
                now - t0, ("%.0f" % y) if y is not None else "—", target,
                ("%.0f" % wrap180(target - y)) if y is not None else "—"))
        time.sleep(0.12)
    stop()
    return max(errs) if errs else None


def main():
    _g("/control?wifi=1", 4.0, 2)       # без эко-сна — меньше лаг
    y0 = imu_yaw_wait()
    if y0 is None:
        print("IMU молчит — не могу держать курс."); return
    print("текущий курс=%.0f°" % y0)

    cal = calibrate_steer_sign(0)        # ВСЕГДА на месте (мягкий ток); знак тот же для езды
    if cal is None:
        print("калибровка руления не удалась (IMU дёрнулся / не повернулось). Стоп.")
        stop(); return
    print("знак руления cal=%+d" % cal)

    if TURN is not None:
        target = wrap180(y0 + float(TURN))
        print("ПОВОРОТ на месте на %s° -> цель курс=%.0f°" % (TURN, target))
        me = control(target, max(4.0, SEC), 0, cal)
    else:
        target = imu_yaw_wait()           # держим курс ПОСЛЕ калибровки
        print("ЕДЕМ ПРЯМО, держим курс=%.0f° на %.0f с" % (target, SEC))
        me = control(target, SEC, DRIVE_FWD_SIGN * BASE, cal)
    yf = imu_yaw_wait()
    print("\nИТОГ: курс старт=%.0f финиш=%s макс|ошибка|=%s° (раньше open-loop уводило на десятки°)"
          % (y0, ("%.0f" % yf) if yf is not None else "—",
             ("%.1f" % me) if me is not None else "—"))


if __name__ == "__main__":
    try:
        main()
    finally:
        stop()
