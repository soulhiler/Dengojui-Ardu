#!/usr/bin/env python3
"""
spatial.build_model — MVP-этап 1: строит цветное облако точек из кадра(ов)
камеры + ToF-сетки XIAO и копит его на сервере (PLY).

XIAO = источник (/capture + /tof), этот скрипт на ПК/сервере = строит модель
(см. docs/hardware/camera-tof-spatial-model.md). ML-глубина (Depth Anything)
подключается этапом 2; здесь — рабочий разреженный цветной 3D без обучения.

Режимы:
  Живой:    py -3 spatial/build_model.py --ip 192.168.1.50 --frames 1 --out room.ply
  Поворот:  py -3 spatial/build_model.py --ip <IP> --frames 12 --yaw-step 30 --out room.ply
            (ручной turntable/scan360: между кадрами повернуть робота на yaw-step°)
  Офлайн:   py -3 spatial/build_model.py --offline session_dir/ --out room.ply
  Тест:     py -3 spatial/build_model.py --synth --out test.ply   (без железа)

Зависимости: numpy не обязателен; Pillow — для цвета из JPEG (иначе колормап
по дистанции). requirements.txt.
"""
from __future__ import annotations

import argparse
import io
import json
import math
import os
import sys
import time

from tof_cloud import CloudConfig, PointCloud, Pose, apply_pose, grid_to_points, zone_pixel

try:
    from PIL import Image
    _HAVE_PIL = True
except ImportError:
    _HAVE_PIL = False


def _dist_colormap(z_m: float, zmax: float = 3.5):
    """Запасная покраска по дистанции (синий близко → красный далеко)."""
    t = max(0.0, min(1.0, z_m / zmax))
    return (int(255 * t), int(80), int(255 * (1 - t)))


def _load_image(jpeg: bytes):
    if not _HAVE_PIL or jpeg is None:
        return None
    try:
        return Image.open(io.BytesIO(jpeg)).convert("RGB")
    except Exception:
        return None


def add_frame(cloud: PointCloud, tof: dict, jpeg: bytes, pose: Pose, cfg: CloudConfig) -> int:
    res = int(tof.get("res", 8))
    grid = tof.get("grid")
    if not grid or len(grid) < res * res:
        return 0
    img = _load_image(jpeg)
    w, h = (img.size if img is not None else (0, 0))
    added = 0
    for (r, c, x, y, z) in grid_to_points(grid, res, cfg):
        if img is not None:
            u, v = zone_pixel(r, c, res, w, h, cfg)
            rgb = img.getpixel((u, v))
        else:
            rgb = _dist_colormap(z)
        wx, wy, wz = apply_pose(x, y, z, pose)
        cloud.add(wx, wy, wz, rgb)
        added += 1
    return added


def _synth_frame(yaw_deg: float):
    """Фейковый кадр: наклонная «стена» 8×8 + градиентная картинка. Для теста без платы."""
    res = 8
    grid = []
    for r in range(res):
        for c in range(res):
            # стена ~1.5 м, слегка дальше к краям и ниже к полу
            mm = 1500 + (c - 3.5) * 40 + (r) * 25
            grid.append(int(mm))
    tof = {"ok": 1, "res": res, "grid": grid, "mm": 1500}
    jpeg = None
    if _HAVE_PIL:
        img = Image.new("RGB", (320, 240))
        px = img.load()
        for j in range(240):
            for i in range(320):
                px[i, j] = (i * 255 // 320, j * 255 // 240, 128)
        buf = io.BytesIO()
        img.save(buf, format="JPEG")
        jpeg = buf.getvalue()
    return tof, jpeg


def main() -> int:
    ap = argparse.ArgumentParser(description="XIAO камера+ToF -> цветное облако точек (PLY)")
    ap.add_argument("--ip", help="IP платы XIAO (живой режим)")
    ap.add_argument("--offline", help="каталог с сохранёнными кадрами frame_NNN.jpg/.tof.json")
    ap.add_argument("--synth", action="store_true", help="синтетический тест без железа")
    ap.add_argument("--frames", type=int, default=1, help="сколько кадров снять (живой режим)")
    ap.add_argument("--interval", type=float, default=1.0, help="пауза между кадрами, с")
    ap.add_argument("--yaw-step", type=float, default=0.0, help="поворот между кадрами, ° (turntable)")
    ap.add_argument("--fov", type=float, default=60.0, help="FoV сенсора по оси, ° (калибровка)")
    ap.add_argument("--voxel", type=float, default=0.03, help="воксель прорежения, м")
    ap.add_argument("--out", default="model.ply", help="выходной PLY")
    ap.add_argument("--session-dir", help="сохранять сырые кадры сюда (живой режим)")
    args = ap.parse_args()

    cfg = CloudConfig(fov_h_deg=args.fov, fov_v_deg=args.fov)
    cloud = PointCloud(voxel_m=args.voxel)

    if args.session_dir:
        os.makedirs(args.session_dir, exist_ok=True)

    frames = []  # (tof, jpeg, yaw_deg)

    if args.synth:
        for k in range(max(1, args.frames)):
            yaw = k * args.yaw_step
            tof, jpeg = _synth_frame(yaw)
            frames.append((tof, jpeg, yaw))
    elif args.offline:
        names = sorted(n for n in os.listdir(args.offline) if n.endswith(".tof.json"))
        for k, n in enumerate(names):
            base = n[:-len(".tof.json")]
            with open(os.path.join(args.offline, n), encoding="utf-8") as f:
                tof = json.load(f)
            jpeg = None
            jp = os.path.join(args.offline, base + ".jpg")
            if os.path.exists(jp):
                with open(jp, "rb") as f:
                    jpeg = f.read()
            frames.append((tof, jpeg, k * args.yaw_step))
    elif args.ip:
        import xiao_client
        for k in range(max(1, args.frames)):
            try:
                jpeg, tof = xiao_client.fetch_frame(args.ip)
            except Exception as e:
                print(f"кадр {k}: ошибка связи — {e}", file=sys.stderr)
                continue
            yaw = k * args.yaw_step
            frames.append((tof, jpeg, yaw))
            if args.session_dir:
                base = os.path.join(args.session_dir, f"frame_{k:03d}")
                with open(base + ".tof.json", "w", encoding="utf-8") as f:
                    json.dump(tof, f)
                if jpeg:
                    with open(base + ".jpg", "wb") as f:
                        f.write(jpeg)
            print(f"кадр {k}: res={tof.get('res')} zones-valid={sum(1 for v in tof.get('grid',[]) if v and v>0)} yaw={yaw:.0f}°")
            if k + 1 < args.frames:
                if args.yaw_step:
                    input(f"  поверни робота на {args.yaw_step:.0f}° и нажми Enter…")
                else:
                    time.sleep(args.interval)
    else:
        ap.error("укажи --ip, --offline или --synth")

    total = 0
    for tof, jpeg, yaw in frames:
        pose = Pose(yaw=math.radians(yaw))
        total += add_frame(cloud, tof, jpeg, pose, cfg)

    n = cloud.write_ply(args.out)
    print(f"готово: {len(frames)} кадр(ов), {total} точек добавлено, "
          f"{n} после прореживания (воксель {args.voxel} м) -> {args.out}")
    if not _HAVE_PIL:
        print("(Pillow не установлен — цвет по дистанции; pip install pillow для цвета камеры)",
              file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
