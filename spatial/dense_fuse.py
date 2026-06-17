#!/usr/bin/env python3
"""spatial.dense_fuse — Этап A: плотная геометрия из ВИДЕО в воксель-мир.

Грабит RGB+ToF с платы; моно-глубина (масштаб по ToF) → тысячи цветных
МЕТРИЧЕСКИХ точек на кадр (вместо 64 ToF-зон) → WorldModel. Видео начинает
нести ГЕОМЕТРИЮ, а не только цвет.

Бэкенд глубины:
  synth         — без torch/сети (форма синтетическая; цвет/масштаб/поза реальные);
  depthanything — Depth Anything V2 (нужен `pip install torch transformers`).

Запуск:
  python spatial/dense_fuse.py --selftest                 # без платы и torch
  python spatial/dense_fuse.py 192.168.1.104 --frames 1   # synth на живом кадре
  python spatial/dense_fuse.py 192.168.1.104 --frames 6 --backend depthanything
"""
import json
import os
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import depth_fusion as DF                                   # noqa: E402
from tof_cloud import CloudConfig, Pose                     # noqa: E402
from world_model import WorldModel                          # noqa: E402
from world_service import frame_to_world                    # noqa: E402


def _fetch(ip, path, timeout=6.0):
    with urllib.request.urlopen("http://%s%s" % (ip, path), timeout=timeout) as r:
        return r.read()


def _opt(argv, key, default):
    return argv[argv.index(key) + 1] if key in argv else default


def _backend(name):
    if name == "depthanything":
        return DF.DepthAnythingBackend()
    return DF.SynthBackend()


def selftest():
    """Без платы и torch: синтет-кадр + плоский ToF (1 м) → плотные точки на z≈1 м."""
    import io
    from PIL import Image
    img = Image.new("RGB", (80, 60), (120, 120, 120))
    buf = io.BytesIO()
    img.save(buf, "JPEG")
    jpeg = buf.getvalue()
    tof = {"res": 4, "grid": [1000] * 16}          # все зоны 1.0 м
    pts = list(DF.frame_to_dense_world(jpeg, tof, Pose(), DF.SynthBackend(), max_points=3000))
    zs = [p[2] for p in pts]
    ok = len(pts) > 500 and zs and abs(sum(zs) / len(zs) - 1.0) < 0.15
    print("DENSE selftest: точек=%d  z_сред=%.3f м (ToF=1.0)  -> %s"
          % (len(pts), (sum(zs) / len(zs)) if zs else 0.0, "OK" if ok else "FAIL"))
    return ok


def run_live(ip, frames, backend_name, hfov):
    backend = _backend(backend_name)
    model = WorldModel(voxel_m=0.05)
    pose = Pose()   # Этап A: стационарно, одна точка обзора — показываем ПЛОТНОСТЬ
    cfg = CloudConfig()
    print("Плотная глубина из видео: ip=%s, кадров=%d, бэкенд=%s, FoV=%.0f°"
          % (ip, frames, backend_name, hfov))
    sparse_sum = dense_sum = 0
    for i in range(frames):
        try:
            jpeg = _fetch(ip, "/capture", 8.0)
            tof = json.loads(_fetch(ip, "/tof", 5.0).decode("utf-8"))
        except Exception as e:
            print("  кадр %d: ошибка %r" % (i, e))
            continue
        sp = list(frame_to_world(tof, b"", pose, cfg))         # 64 ToF-зоны (для сравнения)
        dn = list(DF.frame_to_dense_world(jpeg, tof, pose, backend, cam_hfov_deg=hfov))
        model.integrate_frame(dn)
        sparse_sum += len(sp)
        dense_sum += len(dn)
        print("  кадр %d: sparse=%d  dense=%d  (вокселей=%d)" % (i, len(sp), len(dn), len(model.vox)))
        time.sleep(0.3)
    st = model.stats()
    f = max(1, frames)
    print("\nИТОГ: точек/кадр  sparse≈%d → dense≈%d (×%.0f)  |  вокселей=%d уверенных=%d"
          % (sparse_sum // f, dense_sum // f, (dense_sum / max(1, sparse_sum)),
             st["voxels"], st["confident"]))
    here = os.path.dirname(os.path.abspath(__file__))
    model.save(os.path.join(here, "dense_world.json.gz"))
    n = model.write_ply(os.path.join(here, "dense_world.ply"))
    print("Сохранено: dense_world.json.gz + dense_world.ply (%d точек)" % n)


def main(argv):
    if "--selftest" in argv:
        sys.exit(0 if selftest() else 1)
    pos = [a for a in argv[1:] if not a.startswith("--")]
    ip = pos[0] if pos else "192.168.1.104"
    run_live(ip, int(_opt(argv, "--frames", "1")),
             _opt(argv, "--backend", "synth"), float(_opt(argv, "--hfov", "65")))


if __name__ == "__main__":
    main(sys.argv)
