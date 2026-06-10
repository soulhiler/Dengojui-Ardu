#!/usr/bin/env python3
"""Аппаратный сброс XIAO через RTS и наблюдение, чем кончился Wi-Fi."""
from __future__ import annotations

import re
import sys
import time


def main() -> int:
    port = sys.argv[1] if len(sys.argv) > 1 else "COM6"
    secs = float(sys.argv[2]) if len(sys.argv) > 2 else 80.0
    import serial

    ser = serial.Serial(port, 115200, timeout=0.3)
    # сброс: RTS->EN low/high
    ser.setDTR(False)
    ser.setRTS(True)
    time.sleep(0.2)
    ser.setRTS(False)
    time.sleep(0.2)
    end = time.time() + secs
    result = None
    while time.time() < end:
        b = ser.readline()
        if not b:
            continue
        s = b.decode("utf-8", "replace").rstrip()
        if "WiFi OK, IP" in s or "WiFi восстановлен" in s:
            result = "STA_CONNECTED " + s
            print(result)
            break
        if s.startswith("AP:") or "192.168.4.1" in s:
            result = "AP_FALLBACK " + s
            print(result)
            break
        m = re.search(r'"wifi_ip":"([0-9.]+)"', s)
        if m and m.group(1) not in ("", "0.0.0.0", "192.168.4.1"):
            result = "STA_IP " + m.group(1)
            print(result)
            break
    ser.close()
    if not result:
        print("no result within window")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
