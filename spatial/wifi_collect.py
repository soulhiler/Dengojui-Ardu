#!/usr/bin/env python3
"""
spatial.wifi_collect — сбор/использование Wi-Fi-радиокарты (этап 3, якорь позиции).

Строит радиокарту В КООРДИНАТАХ ПОЗЫ робота без ручного обмера: на каждом шаге
тянет с платы скан AP (GET /wifiscan) + телеметрию (поза из энкодеров/IMU через
PoseEstimator) и тегирует RSSI-вектор координатой. Потом по новому скану даёт
грубый абсолютный фикс (SE-WKNN) — для сброса дрейфа (см. wifi_anchor.py).

  Сбор:    py -3 spatial/wifi_collect.py --ip <IP> --map home.wifi --collect 30
           (между точками — поездить роботом; /wifiscan кратко роняет связь)
  Фикс:    py -3 spatial/wifi_collect.py --ip <IP> --map home.wifi --locate
  Тест:    py -3 spatial/wifi_collect.py --synth
"""
from __future__ import annotations

import argparse
import sys
import time

from wifi_anchor import WifiMap, scan_from_wifiscan

try:
    from world_service import PoseEstimator
    _HAVE_POSE = True
except Exception:
    _HAVE_POSE = False


def _live_scan_and_pose(ip, pose_est):
    import xiao_client
    js = xiao_client.fetch_tof  # noqa: F841  (заглушка импорта пакета)
    import urllib.request
    import json
    with urllib.request.urlopen(f"http://{ip}/wifiscan", timeout=10) as r:
        scan = scan_from_wifiscan(json.loads(r.read().decode("utf-8")))
    telem = xiao_client.fetch_telemetry(ip, timeout=4.0)
    pose = pose_est.update(telem) if pose_est else None
    return scan, pose


def main() -> int:
    ap = argparse.ArgumentParser(description="Wi-Fi радиокарта в координатах позы (якорь)")
    ap.add_argument("--ip", help="IP платы XIAO")
    ap.add_argument("--map", default="home.wifi", help="файл радиокарты (JSON)")
    ap.add_argument("--collect", type=int, default=0, help="сколько точек собрать")
    ap.add_argument("--interval", type=float, default=2.0, help="пауза между точками, с")
    ap.add_argument("--locate", action="store_true", help="один скан → фикс позиции")
    ap.add_argument("--synth", action="store_true", help="тест без железа")
    args = ap.parse_args()

    wmap = WifiMap()
    wmap.load(args.map)

    if args.synth:
        # три места, потом запрос с просевшим уровнем (дрейф) рядом с первым
        wmap.add(0.0, 0.0, {"ap1": -40, "ap2": -70, "ap3": -85})
        wmap.add(2.0, 0.0, {"ap1": -65, "ap2": -50, "ap3": -80})
        wmap.add(0.0, 2.0, {"ap1": -80, "ap2": -75, "ap3": -45})
        loc = wmap.locate({"ap1": -47, "ap2": -77, "ap3": -92}, k=2)
        print("synth фикс:", loc, "(ожидали ~0,0)")
        print(f"карта: {len(wmap)} точек")
        return 0

    if not args.ip:
        ap.error("нужен --ip (или --synth)")
    pose_est = PoseEstimator() if _HAVE_POSE else None

    if args.collect > 0:
        for k in range(args.collect):
            try:
                scan, pose = _live_scan_and_pose(args.ip, pose_est)
            except Exception as e:
                print(f"точка {k}: ошибка — {e}", file=sys.stderr)
                continue
            x = pose.tx if pose else 0.0
            z = pose.tz if pose else 0.0
            ok = wmap.add(x, z, scan)
            print(f"точка {k}: x={x:.2f} z={z:.2f} AP={len(scan)} {'+' if ok else 'мало AP'}")
            if k + 1 < args.collect:
                time.sleep(args.interval)
        n = wmap.save(args.map)
        print(f"сохранено {n} точек → {args.map}")
        return 0

    if args.locate:
        try:
            scan, _ = _live_scan_and_pose(args.ip, pose_est)
        except Exception as e:
            print(f"ошибка скана: {e}", file=sys.stderr)
            return 1
        loc = wmap.locate(scan)
        if loc:
            x, z, conf = loc
            print(f"Wi-Fi фикс: x={x:.2f} z={z:.2f} conf={conf:.2f} (AP={len(scan)}, карта={len(wmap)})")
        else:
            print(f"матч не найден (AP={len(scan)}, карта={len(wmap)}) — собери карту --collect")
        return 0

    ap.error("укажи --collect N, --locate или --synth")


if __name__ == "__main__":
    raise SystemExit(main())
