#!/usr/bin/env python3
"""Залить прошивку на XIAO по Wi-Fi (HTTP POST /update) — БЕЗ USB.

Прошивка с XIAO_OTA_ENABLE=1 (secrets.h) поднимает endpoint /update?pwd=…&size=N.
Робот должен быть на домашнем Wi-Fi (НЕ в режиме AP-only).

  python tools/xiao_http_ota.py                  # собрать + найти робота + залить + проверить
  python tools/xiao_http_ota.py --ip 192.168.1.112
  python tools/xiao_http_ota.py --bin path.bin --no-build
Пароль OTA: из xiao_cam_stream/secrets.h (XIAO_OTA_PASSWORD), либо --pwd / $XIAO_OTA_PASSWORD.
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import os
import re
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

BOUNDARY = "----XiaoOtaBoundary7e9f"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKETCH = os.path.join(ROOT, "xiao_cam_stream")
FQBN = "esp32:esp32:XIAO_ESP32S3:PSRAM=opi"
CLI = os.path.join(ROOT, "tools", "arduino-cli", "arduino-cli.exe")
BUILD_OUT = os.path.join(SKETCH, "build_out")


def ota_password() -> str:
    sec = os.path.join(SKETCH, "secrets.h")
    if os.path.isfile(sec):
        for line in open(sec, encoding="utf-8", errors="replace"):
            m = re.search(r'XIAO_OTA_PASSWORD\s+"([^"]*)"', line)
            if m:
                return m.group(1)
    return ""


def _probe(addr: str):
    try:
        urllib.request.urlopen("http://%s/telemetry" % addr, timeout=1.2).read()
        return addr
    except Exception:
        return None


def find_robot(ip: str) -> str:
    if ip and _probe(ip):
        return ip
    cfp = os.path.join(ROOT, "camera_ip.txt")
    if os.path.isfile(cfp):
        for line in open(cfp, encoding="utf-8"):
            t = line.strip()
            if t and not t.startswith("#") and _probe(t):
                return t
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.connect(("8.8.8.8", 80))
    base = ".".join(s.getsockname()[0].split(".")[:3])
    s.close()
    print("скан %s.0/24…" % base)
    with cf.ThreadPoolExecutor(max_workers=64) as ex:
        for r in ex.map(_probe, ["%s.%d" % (base, i) for i in range(1, 255)]):
            if r:
                return r
    return ""


def build_bin() -> str:
    print("сборка…")
    subprocess.run([CLI, "compile", "--output-dir", BUILD_OUT, "--fqbn", FQBN, SKETCH], check=True)
    return find_bin()


def find_bin() -> str:
    for path in (
        os.path.join(BUILD_OUT, "xiao_cam_stream.ino.bin"),
        os.path.join(SKETCH, "build", "esp32.esp32.XIAO_ESP32S3", "xiao_cam_stream.ino.bin"),
    ):
        if os.path.isfile(path):
            return path
    raise FileNotFoundError("нет xiao_cam_stream.ino.bin — собери (--build) или укажи --bin")


def post_firmware(ip: str, pwd: str, bin_path: str) -> None:
    data = open(bin_path, "rb").read()
    body = bytearray()
    body.extend(
        f"--{BOUNDARY}\r\n"
        f'Content-Disposition: form-data; name="firmware"; filename="{os.path.basename(bin_path)}"\r\n'
        f"Content-Type: application/octet-stream\r\n\r\n".encode())
    body.extend(data)
    body.extend(f"\r\n--{BOUNDARY}--\r\n".encode())
    url = f"http://{ip}/update?pwd={urllib.parse.quote(pwd)}&size={len(data)}"
    req = urllib.request.Request(url, data=bytes(body), method="POST",
                                 headers={"Content-Type": f"multipart/form-data; boundary={BOUNDARY}"})
    with urllib.request.urlopen(req, timeout=180) as resp:
        print("OTA ->", resp.status, resp.read().decode("utf-8", errors="replace").strip())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ip", default="")
    ap.add_argument("--pwd", default=os.environ.get("XIAO_OTA_PASSWORD", ""))
    ap.add_argument("--bin", default="")
    ap.add_argument("--no-build", action="store_true", help="не собирать — взять готовый .bin")
    args = ap.parse_args()

    pwd = args.pwd.strip() or ota_password()
    if not pwd:
        print("нет XIAO_OTA_PASSWORD (secrets.h) и не задан --pwd", file=sys.stderr)
        return 1
    ip = find_robot(args.ip.strip())
    if not ip:
        print("робот не найден (он должен быть на домашнем Wi-Fi, не в режиме AP-only)", file=sys.stderr)
        return 1
    print("робот:", ip)

    bin_path = args.bin or (find_bin() if args.no_build else build_bin())
    print("заливаю %s (%d байт) по Wi-Fi…" % (os.path.basename(bin_path), os.path.getsize(bin_path)))
    try:
        post_firmware(ip, pwd, bin_path)
    except urllib.error.HTTPError as e:
        print(f"HTTP {e.code}: {e.read().decode('utf-8', errors='replace')}", file=sys.stderr)
        return 1
    except Exception as e:
        print(e, file=sys.stderr)
        return 1

    time.sleep(11)
    try:
        j = json.loads(urllib.request.urlopen("http://%s/telemetry" % ip, timeout=4).read().decode())
        print("после рестарта: fw=%s build=%s" % (j.get("fw_version"), j.get("fw_build")))
    except Exception:
        print("(перезагружается — проверь /telemetry через пару секунд)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
