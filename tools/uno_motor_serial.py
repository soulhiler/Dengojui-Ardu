#!/usr/bin/env python3
"""
Управление моторами Arduino UNO + TB6612 по USB Serial.

Прошивка: arduino_motor_test/arduino_motor_serial.ino (115200).

Примеры:
  py -3 tools/uno_motor_serial.py COM3
  py -3 tools/uno_motor_serial.py COM3 --l 150 --r 150
  py -3 tools/uno_motor_serial.py COM3 --interactive
"""
from __future__ import annotations

import argparse
import sys
import time

try:
    import serial
    from serial.tools import list_ports
except ImportError:
    print("pip install pyserial", file=sys.stderr)
    sys.exit(1)


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
    raise SystemExit("Укажи порт: py -3 tools/uno_motor_serial.py COM3")


def main() -> int:
    ap = argparse.ArgumentParser(description="UNO + TB6612 serial motor control")
    ap.add_argument("port", nargs="?", help="COM3 и т.д.")
    ap.add_argument("--l", type=int, default=None, help="левый -255..255")
    ap.add_argument("--r", type=int, default=None, help="правый -255..255")
    ap.add_argument("--stop", action="store_true", help="команда stop")
    ap.add_argument("--interactive", "-i", action="store_true", help="ввод команд с клавиатуры")
    ap.add_argument("--baud", type=int, default=115200)
    args = ap.parse_args()

    port = find_port(args.port)
    ser = serial.Serial(port, args.baud, timeout=0.3)
    time.sleep(2.0)
    while ser.in_waiting:
        print(ser.readline().decode("utf-8", errors="replace").rstrip())

    def send(cmd: str) -> None:
        ser.write((cmd.strip() + "\n").encode("ascii"))
        time.sleep(0.05)
        deadline = time.time() + 1.0
        while time.time() < deadline:
            if ser.in_waiting:
                line = ser.readline().decode("utf-8", errors="replace").rstrip()
                if line:
                    print(line)
            else:
                time.sleep(0.02)

    if args.interactive:
        print(f"{port} interactive. Команды: L 120 R 120 | stop | stby 0 | x")
        try:
            while True:
                line = input("> ").strip()
                if not line:
                    continue
                if line in ("q", "quit", "exit"):
                    send("stop")
                    break
                send(line)
        except (KeyboardInterrupt, EOFError):
            send("stop")
        ser.close()
        return 0

    if args.stop:
        send("stop")
    elif args.l is not None or args.r is not None:
        l = 0 if args.l is None else max(-255, min(255, args.l))
        r = l if args.r is None else max(-255, min(255, args.r))
        send(f"L {l} R {r}")
    else:
        print("Укажи --l/--r, --stop или --interactive", file=sys.stderr)
        return 1

    ser.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
