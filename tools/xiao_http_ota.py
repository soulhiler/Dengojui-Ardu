#!/usr/bin/env python3
"""Залить .bin на XIAO по HTTP POST /update (прошивка 1.2.1+ с XIAO_OTA_PASSWORD)."""
from __future__ import annotations

import argparse
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

BOUNDARY = "----XiaoOtaBoundary7e9f"


def find_bin(project_root: str) -> str:
    candidates = [
        os.path.join(project_root, "xiao_cam_stream", "build_out", "xiao_cam_stream.ino.bin"),
        os.path.join(project_root, "xiao_cam_stream", "build", "esp32.esp32.XIAO_ESP32S3", "xiao_cam_stream.ino.bin"),
    ]
    for path in candidates:
        if os.path.isfile(path):
            return path
    raise FileNotFoundError(
        "Нет xiao_cam_stream.ino.bin — собери: arduino-cli compile --export-binaries "
        "--build-path xiao_cam_stream/build_out …"
    )


def post_firmware(ip: str, pwd: str, bin_path: str) -> None:
    with open(bin_path, "rb") as f:
        data = f.read()
    size = len(data)
    fname = os.path.basename(bin_path)
    body = bytearray()
    body.extend(
        f"--{BOUNDARY}\r\n"
        f'Content-Disposition: form-data; name="firmware"; filename="{fname}"\r\n'
        f"Content-Type: application/octet-stream\r\n\r\n".encode()
    )
    body.extend(data)
    body.extend(f"\r\n--{BOUNDARY}--\r\n".encode())

    url = f"http://{ip}/update?pwd={urllib.parse.quote(pwd)}&size={size}"
    req = urllib.request.Request(
        url,
        data=bytes(body),
        method="POST",
        headers={"Content-Type": f"multipart/form-data; boundary={BOUNDARY}"},
    )
    with urllib.request.urlopen(req, timeout=180) as resp:
        print(resp.read().decode("utf-8", errors="replace"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ip", default="")
    ap.add_argument("--pwd", default=os.environ.get("XIAO_OTA_PASSWORD", ""))
    ap.add_argument("--bin", default="")
    args = ap.parse_args()

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ip = args.ip.strip()
    if not ip:
        cf = os.path.join(root, "camera_ip.txt")
        if os.path.isfile(cf):
            with open(cf, encoding="utf-8") as f:
                for line in f:
                    t = line.strip()
                    if t and not t.startswith("#"):
                        ip = t
                        break
    if not ip:
        print("Укажи --ip или camera_ip.txt", file=sys.stderr)
        return 1
    pwd = args.pwd.strip()
    if not pwd:
        print("Задай --pwd или $env:XIAO_OTA_PASSWORD", file=sys.stderr)
        return 1

    bin_path = args.bin or find_bin(root)
    fsize = os.path.getsize(bin_path)
    print(f"HTTP OTA -> http://{ip}/update  ({fsize} bytes)")
    print(f"bin: {bin_path}")
    try:
        post_firmware(ip, pwd, bin_path)
    except urllib.error.HTTPError as e:
        print(f"HTTP {e.code}: {e.read().decode('utf-8', errors='replace')}", file=sys.stderr)
        return 1
    except Exception as e:
        print(e, file=sys.stderr)
        return 1
    print("OK — плата перезагружается, подожди ~15 с и проверь /telemetry fw_build")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
