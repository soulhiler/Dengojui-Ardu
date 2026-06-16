#!/usr/bin/env python3
"""
spatial.calibrate_live — ЖИВАЯ проверка/калибровка конвейера позиционирования и карты.

Гоняет РЕАЛЬНЫЙ конвейер против платы XIAO, не трогая работающий дашборд:
  /tof + /telemetry + /capture  -> PoseEstimator -> frame_to_world -> WorldModel
и печатает на каждом кадре: курс (IMU/одометрия), сколько точек добавлено,
диапазоны x/y/z облака. В конце — статистика модели, ASCII-вид occupancy сверху
и проверка round-trip сохранения (save -> load -> сверка числа вокселей).

Назначение:
  * «проверь»   — видно, что поза течёт, точки валидны, модель копится и сохраняется;
  * «откалибруй»— крутишь робот, смотришь: курс растёт в ту же сторону, что и сцена,
                  облако не «складывается» зеркально (знак yaw / flip_h/flip_v / FoV).

Запуск:
  python spatial/calibrate_live.py [IP] [секунд] [--fov 60] [--no-cam]
  python spatial/calibrate_live.py 192.168.1.104 12
Только проверка диска (без платы):  python spatial/calibrate_live.py --selftest
"""
from __future__ import annotations

import math
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tof_cloud import CloudConfig, grid_to_points          # noqa: E402
from world_model import WorldModel                          # noqa: E402
from world_service import PoseEstimator, frame_to_world     # noqa: E402
from xiao_client import fetch_capture, fetch_telemetry, fetch_tof  # noqa: E402

R2D = 57.29578


def _fmt_range(vals):
    if not vals:
        return "—"
    return "%.2f..%.2f" % (min(vals), max(vals))


def occupancy_ascii(model: WorldModel, cell_m: float = 0.15, span: int = 12) -> str:
    """Грубый вид сверху (плоскость XZ): робот в центре, '#'=занято, '.'=пусто."""
    grid = model.occupancy_2d(cell_m=cell_m)
    if not grid:
        return "(пусто)"
    lines = []
    for gz in range(span, -span - 1, -1):        # z вперёд -> вверх экрана
        row = []
        for gx in range(-span, span + 1):
            if gx == 0 and gz == 0:
                row.append("R")                  # робот
            elif (gx, gz) in grid:
                row.append("#")
            else:
                row.append("·")
        lines.append("".join(row))
    return "\n".join(lines)


def run_live(ip: str, seconds: float, cfg: CloudConfig, use_cam: bool):
    pose_est = PoseEstimator()
    model = WorldModel(voxel_m=0.05)
    print("ЖИВАЯ ПРОВЕРКА: ip=%s, %.0f c, FoV=%.0f/%.0f, камера=%s"
          % (ip, seconds, cfg.fov_h_deg, cfg.fov_v_deg, "да" if use_cam else "нет"))
    print("t(c)  курс°  источник   +точек  всего  x[м]        y[м]        z[м]")
    print("-" * 76)
    t0 = time.time()
    frames = 0
    yaw_first = yaw_last = None
    while time.time() - t0 < seconds:
        loop_t = time.time()
        try:
            tof = fetch_tof(ip, timeout=4.0)
        except Exception as e:
            print("  ToF ошибка: %r" % e)
            time.sleep(0.5)
            continue
        try:
            telem = fetch_telemetry(ip, timeout=3.0)
        except Exception:
            telem = {}
        jpeg = b""
        if use_cam:
            try:
                jpeg = fetch_capture(ip, timeout=5.0)
            except Exception:
                jpeg = b""
        pose = pose_est.update(telem)
        pts = list(frame_to_world(tof, jpeg, pose, cfg))
        added = model.integrate_frame(pts)
        frames += 1
        yaw_deg = pose.yaw * R2D
        if yaw_first is None:
            yaw_first = yaw_deg
        yaw_last = yaw_deg
        src = "IMU" if pose_est.have_imu else ("одометр" if pose_est.have_odom else "нет")
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        zs = [p[2] for p in pts]
        print("%4.1f  %6.1f  %-9s  %5d  %5d  %-10s  %-10s  %-10s"
              % (time.time() - t0, yaw_deg, src, added, len(model.vox),
                 _fmt_range(xs), _fmt_range(ys), _fmt_range(zs)))
        dt = time.time() - loop_t
        time.sleep(max(0.05, 1.0 - dt))

    print("-" * 76)
    st = model.stats()
    print("ИТОГ: кадров=%d  вокселей=%d  уверенных=%d  max_logodds=%.2f"
          % (frames, st.get("voxels", 0), st.get("confident", 0), st.get("max_logodds", 0)))
    if yaw_first is not None:
        print("курс: старт %.1f° -> финиш %.1f°  (Δ=%.1f°)"
              % (yaw_first, yaw_last, yaw_last - yaw_first))
    print("\nOCCUPANCY сверху (ячейка 0.15 м, R=робот, # = занято):")
    print(occupancy_ascii(model))

    # round-trip сохранения
    tmp = os.path.join(os.path.dirname(os.path.abspath(__file__)), "world_calib_test.json.gz")
    saved = model.save(tmp)
    m2 = WorldModel()
    loaded = m2.load(tmp)
    ok = (loaded == saved == len(model.vox))
    print("\nХРАНЕНИЕ: save=%d  load=%d  round-trip=%s" % (saved, loaded, "OK" if ok else "РАСХОЖДЕНИЕ"))
    try:
        os.remove(tmp)
    except OSError:
        pass
    return ok


def run_track(ip: str, seconds: float, cfg: CloudConfig):
    """Калибровка ЗНАКА курса. Поставь ОДИН объект ~0.5 м перед роботом и медленно
    вращай робот ~45° в одну сторону. Если знак yaw верный — мировые (x,z) объекта
    стоят на месте (объект неподвижен в мире, меняется лишь зона, что его видит).
    Если объект «уплывает» дугой / в другую сторону — знак инвертирован."""
    from tof_cloud import apply_pose
    pose_est = PoseEstimator()
    print("КАЛИБРОВКА ЗНАКА КУРСА: поставь объект ~0.5 м впереди и медленно вращай ~45°.")
    print("Верный знак => мировые x,z объекта почти не меняются.\n")
    print("t(c)  курс°   ближний: даль[м]  азимут_робота°   МИР x[м]  z[м]")
    print("-" * 70)
    t0 = time.time()
    w0 = None
    drift_max = 0.0
    while time.time() - t0 < seconds:
        loop_t = time.time()
        try:
            tof = fetch_tof(ip, timeout=4.0)
            telem = fetch_telemetry(ip, timeout=3.0)
        except Exception as e:
            print("  ошибка: %r" % e)
            time.sleep(0.4)
            continue
        pose = pose_est.update(telem)
        res = int(tof.get("res", 8))
        grid = tof.get("grid") or []
        pts = grid_to_points(grid, res, cfg) if len(grid) >= res * res else []
        if not pts:
            time.sleep(max(0.05, 0.7 - (time.time() - loop_t)))
            continue
        # ближайший валидный отсчёт
        r, c, xr, yr, zr = min(pts, key=lambda p: p[4])
        az = math.degrees(math.atan2(xr, zr))      # азимут в кадре робота
        wx, wy, wz = apply_pose(xr, yr, zr, pose)
        if w0 is None:
            w0 = (wx, wz)
        drift = math.hypot(wx - w0[0], wz - w0[1])
        drift_max = max(drift_max, drift)
        print("%4.1f  %6.1f       %5.2f         %6.1f        %6.2f  %6.2f"
              % (time.time() - t0, pose.yaw * R2D, zr, az, wx, wz))
        time.sleep(max(0.05, 0.7 - (time.time() - loop_t)))
    print("-" * 70)
    print("Макс. уход мировых координат ближнего объекта: %.2f м" % drift_max)
    print("ВЫВОД: < ~0.15 м -> знак курса ВЕРНЫЙ; большой уход дугой -> знак ИНВЕРТИРОВАН.")


def simtest():
    """Детерминированная проверка ЗНАКА курса (без платы). Симулируем неподвижный
    объект прямо впереди и поворот робота ПО ЧАСОВОЙ; по замеру 2026-06-16 курс IMU
    при этом УБЫВАЕТ. Правильный знак => мировые (x,z) объекта инвариантны."""
    from tof_cloud import Pose, apply_pose
    pe = PoseEstimator()
    imu0 = 100.0
    rows = []
    base = None
    spread = 0.0
    print("SIMTEST знака курса: объект неподвижен впереди, робот крутится по часовой.")
    print("deg  курс°   МИР x[м]  z[м]")
    for deg in range(0, 91, 15):
        pe.update({"imu_ok": 1, "imu_yaw": imu0 - deg})   # по часовой -> imu_yaw убывает
        pose = Pose(yaw=pe.yaw)
        th = math.radians(deg)
        xr, zr = -math.sin(th), math.cos(th)              # объект фикс. в мире, в кадре робота уходит влево
        wx, _, wz = apply_pose(xr, 0.0, zr, pose)
        if base is None:
            base = (wx, wz)
        spread = max(spread, math.hypot(wx - base[0], wz - base[1]))
        print("%3d  %6.1f   %7.3f  %7.3f" % (deg, math.degrees(pe.yaw), wx, wz))
        rows.append((wx, wz))
    ok = spread < 0.02
    from world_service import IMU_YAW_SIGN
    print("IMU_YAW_SIGN=%g  макс.уход=%.4f м -> %s"
          % (IMU_YAW_SIGN, spread, "OK (объект неподвижен)" if ok else "FAIL (объект плывёт -> знак неверный)"))
    return ok


def run_service(ip: str, seconds: float, cfg: CloudConfig):
    """Прогон РЕАЛЬНОГО WorldService против платы (интегрированный цикл с
    релокализацией курса по карте). Печатает статус с полями reloc."""
    from world_service import WorldService
    wp = os.path.join(os.path.dirname(os.path.abspath(__file__)), "world_service_test.json.gz")
    if os.path.exists(wp):
        os.remove(wp)
    model = WorldModel(voxel_m=0.05)
    svc = WorldService(model, get_ip=lambda: ip, world_path=wp, interval_s=1.0, cfg=cfg)
    svc.start()
    print("WorldService против %s на %.0f c (релокализация=%s, порог margin=%.2f)"
          % (ip, seconds, svc.reloc_enabled, svc.reloc_min_margin))
    print("Покрути робот в течение прогона — увидишь reloc#/corr, когда геометрия различима.\n")
    t0 = time.time()
    while time.time() - t0 < seconds:
        time.sleep(3.0)
        st = svc.status()
        lr = st.get("last_reloc") or {}
        print("t=%4.1f кадров=%s вокс=%s увер=%s курс=%5s° | reloc#=%s corr=%5s° "
              "посл(δ=%s° margin=%s hits=%s) err=%s"
              % (time.time() - t0, st.get("frames"), st.get("voxels"), st.get("confident"),
                 st.get("heading_deg"), st.get("reloc_count"), st.get("reloc_corr_deg"),
                 lr.get("delta_deg"), lr.get("margin"), lr.get("hits"), st.get("last_error") or "-"))
    svc.stop()
    time.sleep(0.3)
    try:
        os.remove(wp)
    except OSError:
        pass
    print("\nИТОГ: reloc применено %d раз, итоговая поправка курса %.1f°"
          % (svc.reloc_count, math.degrees(svc.pose.yaw_correction)))


def reloctest():
    """Детерминированная проверка РЕЛОКАЛИЗАЦИИ КУРСА ПО КАРТЕ (без платы).
    Строим асимметричную карту-панораму, берём срез-скан в FoV, вносим известный
    дрейф курса и проверяем, что relocalize_yaw находит δ ≈ −дрейф (де-дрейф гиро)."""
    from world_service import relocalize_yaw
    m = WorldModel(voxel_m=0.05)
    # Различимая «панорама»: кластеры на РАЗНЫХ азимутах и дистанциях — жёсткий
    # асимметричный узор, совпадает сам с собой только при δ=0 (в отличие от
    # одиночной плоской стены, которая рот-неоднозначна и даёт размытый пик).
    clusters = [(-25, 1.3), (-8, 0.7), (12, 1.6), (28, 0.9)]
    world = []
    for az_deg, dist in clusters:
        a = math.radians(az_deg)
        cx, cz = dist * math.sin(a), dist * math.cos(a)
        for dx in (-0.03, 0.0, 0.03):
            world.append((cx + dx, 0.0, cz))
    for _ in range(2):                          # влить дважды -> уверенно
        m.integrate_frame([(x, y, z, (180, 180, 180)) for (x, y, z) in world])
    scan = list(world)                          # текущий скан = вся панорама (в FoV)
    print("RELOCTEST: карта=%d вокс, скан=%d точек (likelihood-field, coarse-to-fine)" % (len(m.vox), len(scan)))
    ok_all = True
    # большие дрейфы тоже — проверяем захват coarse-to-fine; допуск суб-градусный
    for drift_deg in (-22, -9, 7, 18):
        rl = relocalize_yaw(m, scan, 0.0, 0.0, math.radians(drift_deg), window_deg=30)
        found = rl["delta_deg"] if rl else None
        want = -drift_deg
        good = bool(rl) and abs(found - want) <= 1.0
        ok_all = ok_all and good
        print("  дрейф=%+3d° -> δ=%s° (ждали %+d°) margin=%.2f hits=%d -> %s"
              % (drift_deg, found, want, rl["margin"] if rl else 0,
                 rl["hits"] if rl else 0, "OK" if good else "FAIL"))
    print("RELOCTEST:", "OK" if ok_all else "FAIL")
    return ok_all


def selftest():
    """Проверка хранения без платы: синтетическое облако -> save -> load -> сверка."""
    m = WorldModel(voxel_m=0.05)
    t = time.time()
    for i in range(50):
        a = i / 50.0 * math.pi
        m.integrate_point(math.cos(a), 0.0, 1.0 + math.sin(a), (200, 100, 50), t)
        m.integrate_point(math.cos(a), 0.0, 1.0 + math.sin(a), (200, 100, 50), t)  # 2-е набл. -> уверенно
    tmp = os.path.join(os.path.dirname(os.path.abspath(__file__)), "world_selftest.json.gz")
    saved = m.save(tmp)
    m2 = WorldModel()
    loaded = m2.load(tmp)
    same_conf = m.stats()["confident"] == m2.stats()["confident"]
    os.remove(tmp)
    print("SELFTEST: вокселей=%d save=%d load=%d уверенных_совпало=%s -> %s"
          % (len(m.vox), saved, loaded, same_conf,
             "OK" if (saved == loaded == len(m.vox) and same_conf) else "FAIL"))
    return saved == loaded and same_conf


def main(argv):
    if "--reloctest" in argv:
        sys.exit(0 if reloctest() else 1)
    if "--simtest" in argv:
        sys.exit(0 if simtest() else 1)
    if "--selftest" in argv:
        sys.exit(0 if selftest() else 1)
    ip = "192.168.1.104"
    seconds = 10.0
    fov = 60.0
    use_cam = "--no-cam" not in argv
    pos = [a for a in argv[1:] if not a.startswith("--")]
    if pos:
        ip = pos[0]
    if len(pos) > 1:
        seconds = float(pos[1])
    if "--fov" in argv:
        fov = float(argv[argv.index("--fov") + 1])
    cfg = CloudConfig(fov_h_deg=fov, fov_v_deg=fov)
    if "--track" in argv:
        run_track(ip, seconds if len(pos) > 1 else 20.0, cfg)
        sys.exit(0)
    if "--service" in argv:
        run_service(ip, seconds if len(pos) > 1 else 30.0, cfg)
        sys.exit(0)
    ok = run_live(ip, seconds, cfg, use_cam)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main(sys.argv)
