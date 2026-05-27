#!/usr/bin/env python3
"""
Мост: кнопки в Canvas (uno-motor-control.canvas.tsx) → USB Serial на Arduino.

Читает motorCmd из .canvas.data.json рядом с canvas-файлом.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

try:
    import serial
    from serial.tools import list_ports
except ImportError:
    print("pip install pyserial", file=sys.stderr)
    sys.exit(1)

DEFAULT_CANVAS_DATA = Path(
    r"C:\Users\FabLab\.cursor\projects\c-Users-FabLab-Desktop-It-trane-exp"
    r"\canvases\uno-motor-control.canvas.data.json"
)


def find_port(hint: str | None) -> str:
    if hint:
        return hint
    for p in list_ports.comports():
        d = (p.description or "") + (p.hwid or "")
        if "CH340" in d or "Arduino" in d:
            return p.device
    ports = [p.device for p in list_ports.comports()]
    if len(ports) == 1:
        return ports[0]
    raise SystemExit("Укажи порт: py -3 tools/uno_motor_canvas_bridge.py COM3")


def read_cmd(data_path: Path) -> tuple[int, str] | None:
    if not data_path.is_file():
        return None
    try:
        raw = data_path.read_text(encoding="utf-8")
        data = json.loads(raw) if raw.strip() else {}
    except (OSError, json.JSONDecodeError):
        return None
    mc = data.get("motorCmd")
    if not isinstance(mc, dict):
        return None
    seq = mc.get("seq")
    line = mc.get("line")
    if not isinstance(seq, (int, float)) or not isinstance(line, str):
        return None
    return int(seq), line.strip()


def main() -> int:
    ap = argparse.ArgumentParser(description="Canvas → UNO serial bridge")
    ap.add_argument("port", nargs="?", help="COM3")
    ap.add_argument("--data", type=Path, default=DEFAULT_CANVAS_DATA)
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--poll", type=float, default=0.08)
    args = ap.parse_args()

    port = find_port(args.port)
    ser = serial.Serial(port, args.baud, timeout=0.2)
    time.sleep(2.0)
    while ser.in_waiting:
        print(ser.readline().decode("utf-8", errors="replace").rstrip())

    last_seq = -1
    print(f"Мост: {port} ← {args.data}")
    print("Открой Canvas «uno-motor-control» справа и жми кнопки. Ctrl+C — выход.")

    try:
        while True:
            got = read_cmd(args.data)
            if got:
                seq, line = got
                if seq != last_seq and line:
                    ser.write((line + "\n").encode("ascii"))
                    ser.flush()
                    time.sleep(0.04)
                    while ser.in_waiting:
                        print(ser.readline().decode("utf-8", errors="replace").rstrip())
                    last_seq = seq
            time.sleep(args.poll)
    except KeyboardInterrupt:
        ser.write(b"stop\n")
        ser.flush()
    finally:
        ser.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
