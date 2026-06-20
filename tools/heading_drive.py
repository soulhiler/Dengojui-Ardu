#!/usr/bin/env python3
"""tools/heading_drive.py — управляемая езда: держим КУРС по IMU (closed-loop).

Робота уводит вбок (дисбаланс моторов) — «прямо» едет дугой. Здесь P-регулятор:
ошибка курса (IMU) → дифференциал моторов. Знак руления авто-калибруется. Едем
вперёд = В СТОРОНУ сенсоров (DRIVE_FWD_SIGN), с ToF-стопом перед препятствием.

Статус (2026-06-20): алгоритм работает — ИМПУЛЬСНЫЙ контур (курс читаем на стоянке
между импульсами, т.к. под мотором плата не отвечает) держит курс точно (на шаге
ошибка 0°, цель=факт). Знак руления cal=+1 известен (спин-калибровку пропускаем —
она садила плату). ОГРАНИЧЕНИЕ — ЖЕЛЕЗО: при rssi≈−59 (робот далеко от точки
доступа) бросок тока мотора роняет WiFi платы на ~секунды → между импульсами не
успевает восстановиться, многошаговая езда рвётся. При rssi≈−49 (близко) vo_drive
ехал нормально. Нужно: ближе к AP / лучше антенна + РАЗВЯЗКА ПИТАНИЯ (bulk-конд. на
VM драйвера и рейле платы, декаплинг на MPU6050) + заряженная батарея. Строительный
блок для B.2 (картирование в управляемом проезде).

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
CAL = _opt("--cal", None)            # знак руления (по умолч. +1 из прошлых калибровок)
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
    D = 130          # уверенно преодолеть стикшн при старте спина
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
    errs = []
    reached = 0
    step = 0
    # ИМПУЛЬСНЫЙ цикл: курс читаем НА СТОЯНКЕ (плата отвечает только без нагрузки —
    # просадка питания при моторе), затем короткий импульс привода и снова стоп.
    while time.time() - t0 < seconds:
        y = imu_yaw_wait(8)
        if y is None:
            time.sleep(0.3)
            continue
        e = wrap180(target - y)
        errs.append(abs(e))
        if abs(e) <= TOL_DEG:
            reached += 1
            s = 0.0
            if fwd == 0 and reached >= 2:           # поворот достигнут и удержан
                break
        else:
            reached = 0
            s = max(-STEER_MAX, min(STEER_MAX, cal * KP * e))
            if fwd == 0 and abs(s) < MIN_MOVE:        # стикшн ТОЛЬКО для поворота на месте
                s = MIN_MOVE if s >= 0 else -MIN_MOVE
        step += 1
        print("  шаг %d: курс=%.0f цель=%.0f ошибка=%+.0f° s=%+.0f" % (step, y, target, e, s))
        # короткий импульс + стоп. Для поворота длительность ∝ ошибке (не перелетать);
        # для езды — фикс короткий (руление мягкое на фоне форварда).
        if fwd == 0:
            dur = max(0.06, min(0.22, 0.004 * abs(e)))
        else:
            dur = 0.22
        l, r = fwd + s, fwd - s
        tb = time.time()
        while time.time() - tb < dur:
            drive(l, r)
            time.sleep(0.08)
        stop()
        if fwd != 0:                                  # препятствие?
            fm = telem().get("tof_mm")
            if fm and 0 < fm < STOP_MM:
                print("  препятствие %dмм — стоп" % fm)
                break
        time.sleep(0.45)                              # пауза: плата отвечает, вибрация гаснет
    stop()
    return max(errs) if errs else None


def main():
    _g("/control?wifi=1", 4.0, 2)       # без эко-сна — меньше лаг
    y0 = imu_yaw_wait()
    if y0 is None:
        print("IMU молчит — не могу держать курс."); return
    print("текущий курс=%.0f°" % y0)

    if CAL is not None:
        cal = float(CAL)
        print("знак руления cal=%+g (задан)" % cal)
    elif "--autocal" in sys.argv:
        cal = calibrate_steer_sign(0)
        if cal is None:
            print("автокалибровка не удалась (просадка/IMU). Стоп."); stop(); return
        print("знак руления cal=%+d" % cal)
    else:
        cal = 1.0                        # из прошлых калибровок (s>0 -> курс растёт); --autocal перемерить
        print("знак руления cal=+1 (известен; --autocal чтобы перемерить)")

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
