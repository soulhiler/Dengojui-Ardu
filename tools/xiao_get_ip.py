#!/usr/bin/env python3
"""Прочитать текущий wifi_ip платы из Serial и проверить /telemetry по нему."""
from __future__ import annotations

import json
import re
import sys
import time
import urllib.request


def main() -> int:
    port = sys.argv[1] if len(sys.argv) > 1 else "COM6"
    import serial

    ser = serial.Serial(port, 115200, timeout=0.3)
    blob = ""
    end = time.time() + 10
    while time.time() < end:
        b = ser.read(4096)
        if b:
            blob += b.decode("utf-8", "replace")
    ser.close()
    ips = re.findall(r'"wifi_ip":"([0-9.]+)"', blob)
    ips = [i for i in ips if i not in ("", "0.0.0.0")]
    status = re.findall(r'"wifi_status":"([a-z]+)"', blob)
    print("wifi_ip from serial:", set(ips) or "-")
    print("wifi_status:", set(status) or "-")
    if not ips:
        print("нет IP в Serial — возможно ещё подключается")
        return 1
    ip = ips[-1]
    for _ in range(4):
        try:
            d = json.load(urllib.request.urlopen(f"http://{ip}/telemetry", timeout=8))
            print(f"OK http://{ip}/  fw {d['fw_version']} build {d['fw_build']} rssi {d.get('wifi_rssi')} ssid {d.get('wifi_ssid')!r}")
            return 0
        except Exception as e:
            print(f"http {ip}: {type(e).__name__}")
            time.sleep(2)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
