#!/usr/bin/env python3
"""Проверка собранного робота: Wi-Fi /telemetry, ToF /status, моторы /drive, звук /beep."""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request


def get_json(url: str, timeout: float = 5.0) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return json.load(resp)


def get_text(url: str, timeout: float = 5.0) -> str:
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


def find_board(subnet: str = "192.168.9.") -> str | None:
    for i in range(2, 40):
        ip = subnet + str(i)
        try:
            d = get_json(f"http://{ip}/telemetry", timeout=1.2)
            if "fw_build" in d:
                return ip
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
            pass
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description="XIAO bench test (motors + ToF)")
    ap.add_argument("--ip", help="IP платы (иначе скан 192.168.9.2-39)")
    ap.add_argument("--motors", action="store_true", help="Крутить моторы: вперёд/стоп/назад")
    ap.add_argument("--beep", action="store_true", help="Короткий /beep")
    args = ap.parse_args()

    ip = args.ip or find_board()
    if not ip:
        print("Плата не найдена: нет /telemetry в 192.168.9.x")
        print("Подключи USB (COM) или питание + Wi-Fi Duangdeehouse2 2.4GHz")
        return 1

    print(f"=== IP {ip} ===")
    tel = get_json(f"http://{ip}/telemetry")
    print(
        f"fw {tel.get('fw_version')} build {tel.get('fw_build')} | "
        f"wifi {tel.get('wifi_status')} {tel.get('wifi_ip')} rssi {tel.get('wifi_rssi')}"
    )
    print(
        f"drive_ok={tel.get('drive_ok')} enc_l={tel.get('enc_l')} enc_r={tel.get('enc_r')} | "
        f"tof_ok={tel.get('tof_ok')} tof_mm={tel.get('tof_mm')}"
    )

    try:
        st = get_json(f"http://{ip}/status")
        print(f"/status: tof_mm={st.get('tof_mm')} tof_ok={st.get('tof_ok')}")
    except Exception as e:
        print(f"/status FAIL: {e}")

    if args.beep:
        print("beep...")
        try:
            get_text(f"http://{ip}/beep?ms=120")
            print("beep OK")
        except Exception as e:
            print(f"beep FAIL: {e}")

    if args.motors:
        print("Моторы: вперёд 80 → стоп → назад 80 → стоп (подними колёса!)")
        for l, r, label in ((80, 80, "fwd"), (-80, -80, "rev")):
            url = f"http://{ip}/drive?{urllib.parse.urlencode({'l': l, 'r': r})}"
            get_text(url)
            print(f"  {label} L={l} R={r}")
            time.sleep(1.2)
            get_text(f"http://{ip}/drive?stop=1")
            print("  stop")
            time.sleep(0.5)

    print("=== OK ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
