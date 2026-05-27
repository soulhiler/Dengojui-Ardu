#!/usr/bin/env python3
"""Веб-панель управления UNO+TB6612 (не грузит Canvas IDE)."""
from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

try:
    import serial
    from serial.tools import list_ports
except ImportError:
    print("pip install pyserial", file=sys.stderr)
    sys.exit(1)

HTML = """<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, user-scalable=no">
<title>UNO моторы</title>
<style>
  * { box-sizing: border-box; touch-action: none; }
  body { font-family: system-ui, sans-serif; margin: 16px; max-width: 420px;
    background: #1e1e1e; color: #e0e0e0; user-select: none; }
  h1 { font-size: 1.2rem; margin: 0 0 6px; }
  .hint { font-size: 12px; color: #999; margin-bottom: 10px; }
  .row { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; margin: 8px 0; }
  button { padding: 10px 14px; font-size: 14px; cursor: pointer;
    border: 1px solid #444; border-radius: 6px; background: #2d2d2d; color: #eee; }
  button.danger { background: #8b2222; min-width: 72px; }
  .speed button.active { outline: 2px solid #3794ff; }
  input[type=range] { flex: 1; min-width: 120px; }
  .readout { font-family: ui-monospace, monospace; font-size: 14px; margin: 8px 0; }
  .joystick {
    position: relative; width: 240px; height: 240px; margin: 12px auto;
    border-radius: 50%; background: #252526;
    border: 2px solid #444; box-shadow: inset 0 0 24px #111;
  }
  .joystick::before {
    content: ""; position: absolute; inset: 50% auto auto 50%;
    width: 2px; height: 100%; margin-left: -1px; margin-top: -50%;
    background: #333; pointer-events: none;
  }
  .joystick::after {
    content: ""; position: absolute; inset: 50% auto auto 50%;
    width: 100%; height: 2px; margin-left: -50%; margin-top: -1px;
    background: #333; pointer-events: none;
  }
  .joy-knob {
    position: absolute; left: 50%; top: 50%; width: 72px; height: 72px;
    margin: -36px 0 0 -36px; border-radius: 50%;
    background: #0e639c;
    border: 2px solid #6eb3f7; cursor: grab;
  }
  .joy-knob.dragging { cursor: grabbing; z-index: 3; }
  .joy-knob { z-index: 3; }
  .joy-label {
    position: absolute; font-size: 12px; font-weight: 600; color: #8ab4e8;
    pointer-events: none; z-index: 1; letter-spacing: 0.02em;
  }
  .joy-label.fwd { top: 10px; left: 50%; transform: translateX(-50%); }
  .joy-label.back { bottom: 10px; left: 50%; transform: translateX(-50%); }
  .joy-label.left { left: 10px; top: 50%; transform: translateY(-50%); }
  .joy-label.right { right: 10px; top: 50%; transform: translateY(-50%); }
  #status { font-size: 13px; color: #aaa; }
</style>
</head>
<body>
<h1>Управление моторами</h1>
<p class="hint">Круговой джойстик: вверх/вниз — езда, влево/вправо — поворот. Отпусти — стоп.</p>
<p id="status">…</p>
<div class="row">
  <span>Макс. скорость</span>
  <input type="range" id="maxSpd" min="40" max="255" value="180">
  <span id="maxLbl">180</span>
</div>
<div class="row speed" id="speeds"></div>
<div class="joystick" id="joy" aria-label="Джойстик движения">
  <span class="joy-label fwd">Вперёд</span>
  <span class="joy-label back">Назад</span>
  <span class="joy-label left">Влево</span>
  <span class="joy-label right">Вправо</span>
  <div class="joy-knob" id="knob"></div>
</div>
<p class="readout">L: <span id="lv">0</span> &nbsp; R: <span id="rv">0</span></p>
<div class="row">
  <button class="danger" id="btnStop">Стоп</button>
</div>
<script>
const joy = document.getElementById('joy');
const knob = document.getElementById('knob');
const maxSpdEl = document.getElementById('maxSpd');
const maxLbl = document.getElementById('maxLbl');
let maxSpeed = 180;
let dragging = false;
let lastLine = '';
const DEAD = 0.12;

maxSpdEl.oninput = () => {
  maxSpeed = +maxSpdEl.value;
  maxLbl.textContent = maxSpeed;
  document.querySelectorAll('.speed button').forEach(b => b.classList.remove('active'));
};

[80, 120, 180, 220, 255].forEach(v => {
  const b = document.createElement('button');
  b.textContent = v;
  if (v === maxSpeed) b.classList.add('active');
  b.onclick = () => {
    maxSpeed = v; maxSpdEl.value = v; maxLbl.textContent = v;
    document.querySelectorAll('.speed button').forEach(x =>
      x.classList.toggle('active', +x.textContent === v));
  };
  document.getElementById('speeds').appendChild(b);
});

function clamp(v, lo, hi) { return Math.max(lo, Math.min(hi, v)); }

function mix(nx, ny) {
  const mag = Math.hypot(nx, ny);
  if (mag < DEAD) return { l: 0, r: 0 };
  const k = Math.min(1, mag);
  const fx = (nx / mag) * k;
  const fy = (-ny / mag) * k;
  const l = Math.round(maxSpeed * clamp(fy + fx, -1, 1));
  const r = Math.round(maxSpeed * clamp(fy - fx, -1, 1));
  return { l, r };
}

function lineFor(l, r) {
  if (l === 0 && r === 0) return 'stop';
  return 'L ' + l + ' R ' + r;
}

function pushDrive(l, r) {
  document.getElementById('lv').textContent = l;
  document.getElementById('rv').textContent = r;
  const line = lineFor(l, r);
  if (line === lastLine) return;
  lastLine = line;
  fetch('/cmd?line=' + encodeURIComponent(line) + '&q=1').catch(() => {});
}

function setKnob(dx, dy, R) {
  const mag = Math.hypot(dx, dy);
  if (mag > R) { dx = dx * R / mag; dy = dy * R / mag; }
  knob.style.transform = 'translate(' + dx + 'px,' + dy + 'px)';
  const nx = dx / R;
  const ny = dy / R;
  const { l, r } = mix(nx, ny);
  pushDrive(l, r);
}

function centerKnob() {
  knob.style.transform = 'translate(0,0)';
  lastLine = '';
  pushDrive(0, 0);
}

function ptrPos(e) {
  const r = joy.getBoundingClientRect();
  return { x: e.clientX - (r.left + r.width / 2), y: e.clientY - (r.top + r.height / 2), R: r.width * 0.36 };
}

function start(e) {
  dragging = true;
  knob.classList.add('dragging');
  e.preventDefault();
  if (e.target.setPointerCapture) e.target.setPointerCapture(e.pointerId);
  move(e);
}

function move(e) {
  if (!dragging) return;
  const p = ptrPos(e);
  setKnob(p.x, p.y, p.R);
}

function end(e) {
  if (!dragging) return;
  dragging = false;
  knob.classList.remove('dragging');
  if (e.target.releasePointerCapture) try { e.target.releasePointerCapture(e.pointerId); } catch (_) {}
  centerKnob();
  fetch('/cmd?line=stop').catch(() => {});
}

joy.addEventListener('pointerdown', start);
window.addEventListener('pointermove', move);
window.addEventListener('pointerup', end);
window.addEventListener('pointercancel', end);
document.getElementById('btnStop').onclick = () => { dragging = false; centerKnob(); fetch('/cmd?line=stop'); };

fetch('/status').then(r => r.json()).then(j => {
  document.getElementById('status').textContent = 'Порт ' + j.port + ' · джойстик ~20 Гц';
}).catch(() => {});
</script>
</body>
</html>
"""


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
    raise SystemExit("Укажи порт: py -3 tools/uno_motor_web.py COM3")


def open_serial(port: str, baud: int) -> serial.Serial:
    try:
        ser = serial.Serial(port, baud, timeout=0.25)
    except serial.SerialException as e:
        msg = str(e).lower()
        if "access is denied" in msg or "permission" in msg or "отказано" in msg:
            raise SystemExit(
                f"{port} занят.\n"
                "Закрой: Arduino Serial Monitor, второй терминал с uno_motor_web.py,\n"
                "монитор порта. Потом снова: .\\tools\\start_uno_motor_panel.ps1"
            ) from e
        raise
    time.sleep(2.0)
    while ser.in_waiting:
        ser.readline()
    return ser


class SerialBus:
    def __init__(self, port: str, baud: int) -> None:
        self._lock = threading.Lock()
        self.port = port
        self.ser = open_serial(port, baud)

    def send(self, line: str, *, quick: bool = False) -> list[str]:
        out: list[str] = []
        with self._lock:
            try:
                self.ser.write((line.strip() + "\n").encode("ascii"))
                self.ser.flush()
            except serial.SerialException as e:
                raise RuntimeError(
                    f"{self.port} недоступен (закрой другие программы на COM)"
                ) from e
            if quick:
                time.sleep(0.008)
                while self.ser.in_waiting:
                    self.ser.readline()
                return []
            deadline = time.time() + 0.35
            while time.time() < deadline:
                if self.ser.in_waiting:
                    out.append(
                        self.ser.readline().decode("utf-8", errors="replace").rstrip()
                    )
                else:
                    time.sleep(0.02)
        return [x for x in out if x]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("port", nargs="?", help="COM3")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port-http", type=int, default=8765)
    ap.add_argument("--baud", type=int, default=115200)
    args = ap.parse_args()

    port = find_port(args.port)
    bus = SerialBus(port, args.baud)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *a: object) -> None:
            pass

        def _json(self, code: int, obj: dict) -> None:
            body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            u = urlparse(self.path)
            if u.path in ("/", "/index.html"):
                body = HTML.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if u.path == "/status":
                self._json(200, {"ok": True, "port": bus.port})
                return
            if u.path == "/cmd":
                qs = parse_qs(u.query)
                line = (qs.get("line") or ["stop"])[0]
                quick = (qs.get("q") or ["0"])[0] in ("1", "true", "yes")
                try:
                    reply = bus.send(line, quick=quick)
                    self._json(200, {"ok": True, "reply": reply})
                except Exception as e:
                    self._json(500, {"ok": False, "error": str(e)})
                return
            self.send_error(404)

    httpd = HTTPServer((args.host, args.port_http), Handler)
    url = f"http://{args.host}:{args.port_http}/"
    print(f"Панель: {url}")
    print(f"Serial: {port} · Ctrl+C — выход")
    try:
        import webbrowser

        webbrowser.open(url)
    except Exception:
        pass
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        bus.send("stop")
    finally:
        httpd.server_close()
        bus.ser.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
