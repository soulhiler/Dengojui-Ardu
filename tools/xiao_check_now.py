#!/usr/bin/env python3
"""Быстрая проверка: Serial COM6 (Wi-Fi статус) + HTTP /telemetry."""
from __future__ import annotations

import re
import sys
import time
import urllib.request
import json


def read_serial(port: str, secs: float) -> list[str]:
    try:
        import serial
    except ImportError:
        return ["pyserial not installed"]
    try:
        ser = serial.Serial(port, 115200, timeout=0.3)
    except Exception as e:
        return [f"serial open fail: {e}"]
    end = time.time() + secs
    buf: list[str] = []
    while time.time() < end:
        b = ser.readline()
        if b:
            buf.append(b.decode("utf-8", "replace").rstrip())
    ser.close()
    return buf


def main() -> int:
    port = sys.argv[1] if len(sys.argv) > 1 else "COM6"
    buf = read_serial(port, 28)
    joined = "\n".join(buf)
    status = set(re.findall(r'"wifi_status":"([a-z]+)"', joined))
    ips = set(re.findall(r'"wifi_ip":"([0-9.]+)"', joined))
    reasons = set(re.findall(r"reason=(\d+)", joined))
    print("SERIAL lines:", len(buf))
    print("wifi_status seen:", status or "-")
    print("wifi_ip seen:", ips or "-")
    print("disconnect reasons:", reasons or "-")
    for line in buf[-6:]:
        print("  |", line[:110])

    print("--- HTTP /telemetry ---")
    for ip in ("192.168.9.12",) + tuple(ips - {"0.0.0.0"}):
        if not ip:
            continue
        try:
            d = json.load(urllib.request.urlopen(f"http://{ip}/telemetry", timeout=6))
            print(f"OK {ip}: fw {d['fw_version']} build {d['fw_build']} rssi {d.get('wifi_rssi')} tof_ok {d.get('tof_ok')}")
            return 0
        except Exception as e:
            print(f"FAIL {ip}: {type(e).__name__}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
