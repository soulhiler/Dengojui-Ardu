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
Нужен torch+transformers (HF_HUB_OFFLINE=1), близко к роутеру.
Запуск:  python tools/loop_slam.py [IP] [--fwd 4] [--pwm 150] [--cal +1]
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
    print("LOOP-SLAM: ip=%s, вперёд %d шагов + столько же назад (петля).\n" % (IP, FWD_STEPS))

    kfs = []
    pose = np.eye(4)

    def add_kf(c):
        nonlocal pose
        if kfs:
            rel = vo_step(kfs[-1], c)
            if not rel or rel[1] < MIN_INLIERS:
                print("  VO потерян (инлайеров %d) — прерываю проезд" % (rel[1] if rel else 0))
                return False
            pose = pose @ rel[0]
        c["pose"] = pose.copy()
        kfs.append(c)
        d = math.hypot(pose[0, 3], pose[2, 3])
        print("  кадр %d: x=%.2f z=%.2f курс=%.0f° (от старта %.2f м) впереди=%sмм"
              % (len(kfs) - 1, pose[0, 3], pose[2, 3], math.degrees(vo.yaw_of(pose[:3, :3])), d, c["fwd"]))
        return True

    try:
        c = capture(backend)
        if c is None or not add_kf(c):
            stop(); _g("/control?tofprofile=auto", 4.0, 2); return
        # стартовый курс: держим его ОБА плеча (робот не разворачивается, а реверсит),
        # тогда «обратно» повторяет «туда» и петля замыкается физически.
        target = imu_yaw_wait()
        if target is not None:
            print("  удержание курса: цель=%.0f° (CAL=%+g)" % (target, CAL))
        else:
            print("  IMU молчит — еду прямыми импульсами (без удержания курса)")
        # вперёд
        fwd_done = 0
        for s in range(FWD_STEPS):
            if c["fwd"] and 0 < c["fwd"] < STOP_MM:
                print("  препятствие %dмм — разворачиваюсь раньше" % c["fwd"])
                break
            burst(DRIVE_FWD_SIGN, target)
            c = capture(backend)
            if c is None or not add_kf(c):
                break
            fwd_done += 1
        # назад столько же (возврат в старт → петля), тот же курс
        print("  --- разворот: %d шагов назад ---" % fwd_done)
        for s in range(fwd_done):
            burst(-DRIVE_FWD_SIGN, target)
            c = capture(backend)
            if c is None or not add_kf(c):
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
        pg.add_edge(k, k + 1, PG.between(nodes[k], nodes[k + 1]))

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
    model = WorldModel(voxel_m=0.05)
    for k, kf in enumerate(kfs):
        x, y, th = corrected[k]
        model.integrate_frame(list(DF.dense_points_from_depth(kf["Z"], kf["rgb"], kf["intr"],
                                                              Pose(yaw=th, tx=x, tz=y))))
    st = model.stats()
    print("ИТОГ: кадров=%d петель=%d вокселей=%d уверенных=%d" % (n, loops, st["voxels"], st["confident"]))
    here = os.path.dirname(os.path.abspath(__file__))
    model.save(os.path.join(here, "..", "spatial", "loop_slam_world.json.gz"))
    print("Карта сохранена: spatial/loop_slam_world.json.gz")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        stop()
        print("\nпрервано — стоп")
