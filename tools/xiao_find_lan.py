#!/usr/bin/env python3
"""ARP-скан 192.168.9.0/24, найти XIAO по MAC и проверить /telemetry."""
from __future__ import annotations

import concurrent.futures
import json
import re
import subprocess
import sys
import urllib.request

MAC_HINTS = ("1c-db-d4-76-24-24", "1cdbd4762424", "1c:db:d4:76:24:24")
SUBNET = "192.168.9."


def ping(ip: str) -> None:
    subprocess.run(["ping", "-n", "1", "-w", "300", ip], capture_output=True)


def main() -> int:
    # Не больше 8 параллельных ping — иначе дешёвый роутер может зависнуть.
    ips = [SUBNET + str(i) for i in range(2, 60)]
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
        list(ex.map(ping, ips))
    arp = subprocess.run(["arp", "-a"], capture_output=True, text=True).stdout
    found = None
    for line in arp.splitlines():
        low = line.lower().replace(" ", "")
        if any(h.replace(":", "-").replace(":", "") in low or h in low for h in MAC_HINTS):
            m = re.search(r"(\d+\.\d+\.\d+\.\d+)", line)
            if m:
                found = m.group(1)
                print("BOARD MAC at IP:", found, "|", line.strip())
                break
    if not found:
        # показать все 192.168.9.x в arp для диагностики
        print("MAC не найден в ARP. Текущая ARP-таблица 192.168.9.x:")
        for line in arp.splitlines():
            if SUBNET in line:
                print("  ", line.strip())
        return 1
    try:
        d = json.load(urllib.request.urlopen(f"http://{found}/telemetry", timeout=6))
        print(f"OK http://{found}/  fw {d['fw_version']} build {d['fw_build']} rssi {d.get('wifi_rssi')} ssid {d.get('wifi_ssid')!r}")
    except Exception as e:
        print(f"http {found}: {type(e).__name__}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
