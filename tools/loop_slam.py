#!/usr/bin/env python3
"""tools/loop_slam.py — Этап B.3 вживую: проезд-петля + замыкание + согласованная карта.

Робот едет ТУДА-ОБРАТНО (вперёд N шагов, затем столько же назад → возвращается в
старт = петля). На каждом ключевом кадре: плотная глубина (Depth Anything × ToF),
VO-поза (сдвиг из RGB-D, поворот из IMU). После проезда — ОФЛАЙН:
  1) граф поз: узлы=кадры, рёбра-одометрия (между соседними);
  2) ДЕТЕКТ ПЕТЛИ: последние кадры (вернулись в старт) матчатся с первыми (ORB+VO)
     → рёбра-замыкания;
  3) оптимизация (pose_graph) → распределение дрейфа;
  4) пере-вливание плотной карты в ИСПРАВЛЕННЫХ позах → глобально согласованный мир.

Импульсная езда (плата отвечает между импульсами). Вперёд = в сторону сенсоров
(DRIVE_FWD_SIGN). Оба плеча идут с УДЕРЖАНИЕМ КУРСА по IMU (heading-hold, P-регулятор):
«туда» едет прямо, «обратно» ровно реверсит — робот возвращается к старту и петля
замыкается физически (без этого курс уплывает и оптимизировать нечего).
КОНТУР (по умолчанию, `--sides 4`): едем сторону → поворот на месте на 360/sides° →
следующая сторона. После всех сторон робот у старта курсом в исходную сторону —
сильное замыкание, а по периметру копится VO-дрейф, который оптимизация и
распределяет («ЗАМКНУТА»). На повороте VO не гоняем: сдвига нет, относительная поза =
чистый поворот из IMU. `--sides 0` → старый режим туда-обратно.
Нужен torch+transformers (HF_HUB_OFFLINE=1), близко к роутеру.
Запуск:  python tools/loop_slam.py [IP] [--sides 4] [--leg 3] [--pwm 150] [--cal +1]
         python tools/loop_slam.py [IP] --sides 0 [--fwd 5]      # туда-обратно
"""
import io
import json
import math
import os
import sys
import time
import urllib.request

# Windows-консоль (cp1251) не кодирует '→'/'←' и т. п. — принудительно utf-8,
# иначе print финального отчёта падает уже ПОСЛЕ оптимизации (карта не сохранится).
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "spatial"))

import numpy as np                                          # noqa: E402
from PIL import Image                                       # noqa: E402

import depth_fusion as DF                                   # noqa: E402
import pose_graph as PG                                     # noqa: E402
import vo                                                   # noqa: E402
from tof_cloud import Pose                                  # noqa: E402
from world_model import WorldModel                          # noqa: E402

IP = next((a for a in sys.argv[1:] if not a.startswith("-")), "192.168.1.104")


def _opt(k, d):
    return sys.argv[sys.argv.index(k) + 1] if k in sys.argv else d


FWD_STEPS = int(_opt("--fwd", "4"))
PWM = int(_opt("--pwm", "150"))
DRIVE_FWD_SIGN = -1            # вперёд (в сторону сенсоров) = моторный «-»
BURST_S = 0.16          # мельче шаг -> больше перекрытие кадров -> надёжнее VO
STOP_MM = 380
MIN_INLIERS = 16              # одометрия VO
LOOP_MIN_INLIERS = 30        # замыкание петли (строже)
MIN_GAP = 3                  # минимум кадров между i,j для петли
# Удержание курса (heading-hold) на плечах проезда — чтобы «туда» шло прямо, а
# «обратно» ровно реверсило путь (иначе курс уплывает и петля не замыкается физически).
CAL = float(_opt("--cal", "1"))   # знак руления (из heading_drive: s>0 -> курс растёт)
KP_HEAD = 4.0                     # PWM дифференциала на градус ошибки курса
STEER_MAX = 90                    # ограничение руления (форвард должен доминировать)
# Контур-многоугольник: едем сторону → поворот на месте → следующая сторона. После
# SIDES сторон робот возвращается к старту, смотрит в исходную сторону (сильное
# замыкание). За периметр копится VO-дрейф — его и распределяет оптимизация петли.
SIDES = int(_opt("--sides", "4"))      # >=3: контур (4=квадрат); 0/1/2: режим туда-обратно
LEG_STEPS = int(_opt("--leg", "3"))    # прямых импульсов на одну сторону контура
TURN_SIGN = 1.0 if float(_opt("--turnsign", "1")) >= 0 else -1.0   # сторона обхода
TURN_TOL = 6.0                         # допуск достижения курса поворота, °
TURN_MIN_MOVE = 80                     # мин. дифференциал для срыва стикшна на повороте


def _g(p, t=8.0, tries=4):
    for _ in range(tries):
        try:
            return urllib.request.urlopen("http://%s%s" % (IP, p), timeout=t).read()
        except Exception:
            time.sleep(0.4)
    return None


def telem():
    r = _g("/telemetry", 4.0, 3)
    try:
        return json.loads(r.decode("utf-8")) if r else {}
    except Exception:
        return {}


def drive(l, r):
    _g("/drive?l=%d&r=%d" % (int(l), int(r)), 3.0, 1)


def stop():
    for _ in range(3):
        _g("/drive?stop=1", 3.0, 1)
        time.sleep(0.05)


def wrap180(a):
    return ((a + 180) % 360) - 180


def imu_yaw_wait(tries=8):
    """Курс IMU, читаем НА СТОЯНКЕ (под мотором плата отвечает хуже). None если молчит."""
    for _ in range(tries):
        j = telem()
        if j.get("imu_ok") in (1, "1", True) and isinstance(j.get("imu_yaw"), (int, float)):
            return float(j["imu_yaw"])
        time.sleep(0.2)
    return None


def burst(sign, target=None):
    """Импульс привода с УДЕРЖАНИЕМ КУРСА target (P-регулятор по IMU). Курс читаем на
    стоянке перед импульсом; дифференциал s гонит ошибку к нулю. target=None → прямо.
    Дифпривод: ω ∝ (v_r−v_l) = −2s независимо от знака хода, поэтому CAL один и для
    «вперёд», и для «назад»."""
    fwd = sign * PWM
    s = 0.0
    if target is not None:
        y = imu_yaw_wait()
        if y is not None:
            e = wrap180(target - y)
            s = max(-STEER_MAX, min(STEER_MAX, CAL * KP_HEAD * e))
    l, r = fwd + s, fwd - s
    t = time.time()
    while time.time() - t < BURST_S:
        drive(l, r)
        time.sleep(0.1)
    stop()
    time.sleep(0.8)               # плата/вибрация восстанавливаются


def turn_to(target):
    """Поворот НА МЕСТЕ к курсу target (импульсный P-регулятор по IMU). Поворот без
    сдвига: l=+s, r=−s. Длительность импульса ∝ ошибке (не перелетать). Возвращает
    достигнутый курс. Ограничение по числу импульсов — чтобы не зациклиться."""
    reached = 0
    for _ in range(40):
        y = imu_yaw_wait()
        if y is None:
            time.sleep(0.3)
            continue
        e = wrap180(target - y)
        if abs(e) <= TURN_TOL:
            reached += 1
            if reached >= 2:
                break
            stop()
            time.sleep(0.3)
            continue
        reached = 0
        s = max(-STEER_MAX, min(STEER_MAX, CAL * KP_HEAD * e))
        if abs(s) < TURN_MIN_MOVE:           # срыв стикшна на повороте на месте
            s = TURN_MIN_MOVE if s >= 0 else -TURN_MIN_MOVE
        dur = max(0.06, min(0.22, 0.004 * abs(e)))
        t = time.time()
        while time.time() - t < dur:
            drive(s, -s)
            time.sleep(0.08)
        stop()
        time.sleep(0.45)
    return imu_yaw_wait()


def capture(backend):
    jpeg = _g("/capture", 8.0, 4)
    tofr = _g("/tof", 5.0, 4)
    tof = json.loads(tofr.decode("utf-8")) if tofr else {}
    tl = telem()
    if not jpeg or not tof:
        return None
    md = DF.metric_depth_map(jpeg, tof, backend)
    if md is None:
        return None
    Z, gray, intr = md
    iy = tl.get("imu_yaw") if tl.get("imu_ok") in (1, "1", True) else None
    return {"Z": Z, "gray": gray, "intr": intr,
            "rgb": Image.open(io.BytesIO(jpeg)).convert("RGB"),
            "imu": iy, "fwd": tl.get("tof_mm")}


def vo_step(prev, cur):
    """Относительная VO-поза prev->cur (4×4) или None. Поворот из IMU."""
    imu_d = 0.0
    if isinstance(cur["imu"], (int, float)) and isinstance(prev["imu"], (int, float)):
        imu_d = ((cur["imu"] - prev["imu"] + 180) % 360) - 180
    R = vo.R_yaw(math.radians(imu_d))
    return vo.relative_pose_rgbd({"gray": prev["gray"], "depth": prev["Z"]},
                                 {"gray": cur["gray"], "depth": cur["Z"]},
                                 prev["intr"], MATCHER, R_prior=R)


def _imu_delta(prev, cur):
    """Δкурс (°) между кадрами по IMU, или 0 если IMU молчал."""
    if isinstance(cur["imu"], (int, float)) and isinstance(prev["imu"], (int, float)):
        return wrap180(cur["imu"] - prev["imu"])
    return 0.0


def se2(T):
    return [float(T[0, 3]), float(T[2, 3]), vo.yaw_of(T[:3, :3])]


MATCHER = None


def main():
    global MATCHER
    backend = DF.DepthAnythingBackend()
    MATCHER = vo.OrbMatcher()
    _g("/control?tofprofile=accurate", 4.0, 2)
    _g("/control?wifi=1", 4.0, 2)
    time.sleep(1.2)
    mode = ("контур: %d сторон по %d шагов" % (SIDES, LEG_STEPS)) if SIDES >= 3 \
        else ("туда-обратно: %d шагов" % FWD_STEPS)
    print("LOOP-SLAM: ip=%s, режим %s.\n" % (IP, mode))

    kfs = []
    pose = np.eye(4)
    est_step = [0.10]        # бегущая оценка шага (м) — для счисления при потере VO
    dr_count = [0]

    def add_kf(c, pure_rot=False, dr_sign=0):
        """dr_sign != 0: при потере VO НЕ прерываем обход, а счисляем шаг по IMU
        (поворот из IMU + средний шаг dr_sign·est_step) — большой обход не рвётся,
        замыкание петли выправит. dr_sign=0 — старое строгое поведение."""
        nonlocal pose
        dr = False
        if kfs:
            if pure_rot:
                # поворот на месте: относительная поза = чистый поворот из IMU, сдвиг 0.
                pose = pose @ vo.make_T(vo.R_yaw(math.radians(_imu_delta(kfs[-1], c))), np.zeros(3))
            else:
                rel = vo_step(kfs[-1], c)
                if rel and rel[1] >= MIN_INLIERS:
                    pose = pose @ rel[0]
                    t = math.hypot(rel[0][0, 3], rel[0][2, 3])
                    if t > 0.005:
                        est_step[0] = 0.7 * est_step[0] + 0.3 * t   # уточняем оценку шага
                elif dr_sign != 0:
                    pose = pose @ vo.make_T(vo.R_yaw(math.radians(_imu_delta(kfs[-1], c))),
                                            np.array([0.0, 0.0, dr_sign * est_step[0]]))
                    dr = True
                    dr_count[0] += 1
                    print("  VO потерян (инлайеров %d) — счисление IMU+шаг %.2fм"
                          % (rel[1] if rel else 0, est_step[0]))
                else:
                    print("  VO потерян (инлайеров %d) — прерываю проезд" % (rel[1] if rel else 0))
                    return False
        c["dr"] = dr
        c["pose"] = pose.copy()
        kfs.append(c)
        d = math.hypot(pose[0, 3], pose[2, 3])
        tag = " (поворот)" if pure_rot else (" (счисл.)" if dr else "")
        print("  кадр %d%s: x=%.2f z=%.2f курс=%.0f° (от старта %.2f м) впереди=%sмм"
              % (len(kfs) - 1, tag, pose[0, 3], pose[2, 3],
                 math.degrees(vo.yaw_of(pose[:3, :3])), d, c["fwd"]))
        return True

    try:
        c = capture(backend)
        if c is None or not add_kf(c):
            stop(); _g("/control?tofprofile=auto", 4.0, 2); return
        # стартовый курс — опорный для heading-hold (и для расчёта углов поворота).
        target = imu_yaw_wait()
        if target is None:
            print("  IMU молчит — контур/удержание курса невозможны.")

        if SIDES >= 3:
            # ── КОНТУР: сторона → поворот → ... После SIDES сторон робот у старта,
            #    курсом в исходную сторону (сильное замыкание); по периметру копится дрейф.
            turn_deg = 360.0 / SIDES
            print("  КОНТУР: %d сторон по %d шагов, поворот %+.0f° между сторонами, старт-курс=%s°"
                  % (SIDES, LEG_STEPS, TURN_SIGN * turn_deg,
                     ("%.0f" % target) if target is not None else "—"))
            done = False
            for side in range(SIDES):
                for st in range(LEG_STEPS):                     # прямое плечо (heading-hold)
                    if c["fwd"] and 0 < c["fwd"] < STOP_MM:
                        print("  препятствие %dмм — сторона короче" % c["fwd"])
                        break
                    burst(DRIVE_FWD_SIGN, target)
                    c = capture(backend)
                    if c is None or not add_kf(c, dr_sign=1):     # счисление при потере VO
                        done = True
                        break
                if done:
                    break
                if target is not None:                          # поворот к следующей стороне
                    target = wrap180(target + TURN_SIGN * turn_deg)
                    print("  --- сторона %d/%d готова → поворот к курсу %.0f° ---" % (side + 1, SIDES, target))
                    turn_to(target)
                    c = capture(backend)
                    if c is None or not add_kf(c, pure_rot=True):
                        break
        else:
            # ── режим туда-обратно (--sides 0/1/2): прямое плечо + реверс тем же курсом
            if target is not None:
                print("  удержание курса: цель=%.0f° (CAL=%+g)" % (target, CAL))
            fwd_done = 0
            for s in range(FWD_STEPS):
                if c["fwd"] and 0 < c["fwd"] < STOP_MM:
                    print("  препятствие %dмм — разворачиваюсь раньше" % c["fwd"])
                    break
                burst(DRIVE_FWD_SIGN, target)
                c = capture(backend)
                if c is None or not add_kf(c, dr_sign=1):
                    break
                fwd_done += 1
            print("  --- разворот: %d шагов назад ---" % fwd_done)
            for s in range(fwd_done):
                burst(-DRIVE_FWD_SIGN, target)
                c = capture(backend)
                if c is None or not add_kf(c, dr_sign=-1):
                    break
    finally:
        stop()
        _g("/control?tofprofile=auto", 4.0, 2)

    if len(kfs) < 4:
        print("Мало кадров (%d) — нечего оптимизировать." % len(kfs))
        return

    # --- граф поз ---
    nodes = [se2(k["pose"]) for k in kfs]
    pg = PG.PoseGraph()
    for nd in nodes:
        pg.add_node(nd)
    for k in range(len(nodes) - 1):
        # ребро-счисление (любой конец dr) — слабее: пусть его выправит замыкание петли
        soft = kfs[k].get("dr") or kfs[k + 1].get("dr")
        pg.add_edge(k, k + 1, PG.between(nodes[k], nodes[k + 1]),
                    info=np.eye(3) * (0.3 if soft else 1.0))

    # --- детект петли: последние кадры vs первые ---
    n = len(kfs)
    loops = 0
    for j in range(n - 1, max(n - 4, MIN_GAP), -1):
        for i in range(0, min(3, j - MIN_GAP)):
            rel = vo_step(kfs[i], kfs[j])
            if rel and rel[1] >= LOOP_MIN_INLIERS:
                pg.add_edge(i, j, se2(rel[0]), info=np.eye(3) * 5)
                loops += 1
                print("  ЗАМЫКАНИЕ ПЕТЛИ: кадр %d <-> %d (инлайеров %d)" % (i, j, rel[1]))
                break

    drift_before = math.hypot(nodes[-1][0] - nodes[0][0], nodes[-1][1] - nodes[0][1])
    if loops == 0:
        print("Петля не найдена (мало совпадений). Сохраняю карту по сырой VO-позе.")
        corrected = nodes
    else:
        e0 = pg.total_error()
        e1 = pg.optimize(40)
        corrected = pg.nodes
        drift_after = math.hypot(corrected[-1][0] - corrected[0][0], corrected[-1][1] - corrected[0][1])
        # Вердикт честный: при работающем heading-hold дрейф мал ИЗНАЧАЛЬНО —
        # распределять нечего, и это не «слабо», а «уже сошлась».
        if drift_before < 0.08:
            verdict = "уже сошлась — heading-hold удержал курс, дрейфа нет"
        elif drift_after < drift_before * 0.6:
            verdict = "ЗАМКНУТА — дрейф распределён"
        else:
            verdict = "слабо — замыкание не согласуется с одометрией"
        print("\nОПТИМИЗАЦИЯ: ошибка %.3f→%.3f | возврат в старт %.2f→%.2f м\n  петля: %s"
              % (e0, e1, drift_before, drift_after, verdict))

    # --- пере-вливание в исправленных позах ---
    # С КАРВИНГОМ луча (origin = позиция камеры): свободное место чистится, накапливается
    # miss-подпись → одно-карточная динамика (scene_analysis) и чистые стены. --nocarve откл.
    carve = "--nocarve" not in sys.argv
    model = WorldModel(voxel_m=0.05)
    for k, kf in enumerate(kfs):
        x, y, th = corrected[k]
        pose = Pose(yaw=th, tx=x, tz=y)
        pts = list(DF.dense_points_from_depth(kf["Z"], kf["rgb"], kf["intr"], pose))
        if carve:
            model.integrate_frame_rays((pose.tx, pose.ty, pose.tz), pts)
        else:
            model.integrate_frame(pts)
    st = model.stats()
    print("ИТОГ: кадров=%d (счислений=%d) петель=%d вокселей=%d уверенных=%d"
          % (n, dr_count[0], loops, st["voxels"], st["confident"]))
    here = os.path.dirname(os.path.abspath(__file__))
    model.save(os.path.join(here, "..", "spatial", "loop_slam_world.json.gz"))
    print("Карта сохранена: spatial/loop_slam_world.json.gz")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        stop()
        print("\nпрервано — стоп")
