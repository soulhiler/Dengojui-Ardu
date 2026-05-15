#!/usr/bin/env python3
"""
Прокси XIAO CAM -> localhost (для браузера Cursor / Chrome: картинка с LAN не грузится со страницы 127.0.0.1).

Запуск (ПК в той же Wi-Fi, что и плата):
  py -3 tools/xiao_cam_proxy.py 192.168.1.50

Или впиши IP в файл camera_ip.txt в корне проекта (одна строка, без #).

Без IP прокси всё равно поднимается на 8898 — откроется подсказка (нет ERR_CONNECTION_REFUSED).

Открой: http://127.0.0.1:8898/  (видео + звук с микрофона через «Включить звук»)
  Телеметрия в реальном времени: http://127.0.0.1:8898/telemetry
"""
from __future__ import annotations

import argparse
import html
import http.client
import http.server
import json
import os
import socket
import socketserver
import sys
from pathlib import Path


def load_ip_from_file(root: Path) -> str | None:
    p = root / "camera_ip.txt"
    if not p.is_file():
        return None
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            return line
    return None


def load_mic_tcp_port_from_file(root: Path) -> int:
    """Вторая непустая строка в camera_ip.txt (без #) — целое число, TCP порт PCM на плате (по умолчанию 81)."""
    p = root / "camera_ip.txt"
    if not p.is_file():
        return 81
    lines: list[str] = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        lines.append(line)
    if len(lines) < 2:
        return 81
    try:
        port = int(lines[1], 10)
        return port if 1 <= port <= 65535 else 81
    except ValueError:
        return 81


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    ap = argparse.ArgumentParser(description="XIAO CAM localhost proxy")
    ap.add_argument("camera_ip", nargs="?", help="IP платы в LAN")
    ap.add_argument("--port", type=int, default=8898)
    ap.add_argument(
        "--mic-port",
        type=int,
        default=None,
        metavar="N",
        help="TCP порт сырого PCM на плате (по умолчанию 81 или вторая строка camera_ip.txt)",
    )
    args = ap.parse_args()

    cam_ip: str | None = (args.camera_ip or "").strip() or load_ip_from_file(root)
    mic_tcp_port: int = (
        int(args.mic_port)
        if args.mic_port is not None
        else load_mic_tcp_port_from_file(root)
    )
    if not cam_ip:
        print(
            "No camera IP: listening on 127.0.0.1:%d (set camera_ip.txt or pass IP as argument)."
            % args.port,
            file=sys.stderr,
        )

    class Handler(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt, *a):
            sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % a))

        def do_GET(self) -> None:  # noqa: N802
            path = self.path.split("?", 1)[0]
            if path == "/":
                self._home()
            elif path == "/telemetry":
                self._telemetry_dashboard()
            elif path == "/api/telemetry":
                self._api_telemetry()
            elif path == "/stream":
                self._stream_or_capture("/stream", stream=True)
            elif path == "/capture":
                self._stream_or_capture("/capture", stream=False)
            elif path == "/mic_s16":
                self._mic_tcp_bridge()
            else:
                self.send_error(404)

        def _home(self) -> None:
            if cam_ip:
                self._video_page(cam_ip)
            else:
                self._need_ip_page()

        def _need_ip_page(self) -> None:
            txt = root / "camera_ip.txt"
            body = f"""<!DOCTYPE html>
<html lang="ru"><head><meta charset="utf-8"><title>XIAO прокси</title></head><body>
<h2>Прокси запущен, IP камеры не задан</h2>
<p>Создай файл <code>{txt}</code> одной строкой с IP (после <code>WiFi OK, IP:</code> в Serial 115200)
или запусти:</p>
<pre>py -3 tools\\xiao_cam_proxy.py 192.168.x.x</pre>
<p>Плата сейчас может быть в <code>error_wifi</code> — тогда в LAN нет адреса, сначала Wi‑Fi.</p>
<p><a href="/telemetry">телеметрия (режим без камеры)</a></p>
</body></html>"""
            data = body.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(data)

        def _mic_tcp_bridge(self) -> None:
            if not cam_ip:
                msg = b"no camera IP\r\n"
                self.send_response(503)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Content-Length", str(len(msg)))
                self.end_headers()
                self.wfile.write(msg)
                return
            try:
                upstream = socket.create_connection((cam_ip, mic_tcp_port), timeout=10)
            except OSError as e:
                hint = (
                    "Прокси не достучался до платы по TCP для микрофона (порт %d, IP %s).\n"
                    "Ошибка: %s\n\n"
                    "Что сделать: пересоберите и прошейте xiao_cam_stream (ESP_I2S + TCP PCM), "
                    "в Serial после Wi‑Fi должны быть строки «mic: PDM OK» и «mic: TCP PCM on :81 listening=1». "
                    "Порт можно сменить: py -3 tools/xiao_cam_proxy.py --mic-port 81 <IP> "
                    "или вторая строка в camera_ip.txt.\n"
                ) % (mic_tcp_port, cam_ip, e)
                err = hint.encode("utf-8", errors="replace")
                self.send_response(502)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Content-Length", str(len(err)))
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(err)
                return
            upstream.settimeout(0.35)
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("X-Sample-Rate", "16000")
            self.send_header("X-Sample-Bits", "16")
            self.send_header("X-Channels", "1")
            self.send_header("X-Format", "s16le")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "close")
            self.end_headers()
            try:
                while True:
                    try:
                        chunk = upstream.recv(4096)
                    except socket.timeout:
                        continue
                    if not chunk:
                        break
                    self.wfile.write(chunk)
            except BrokenPipeError:
                pass
            finally:
                try:
                    upstream.close()
                except Exception:
                    pass

        def _video_page(self, ip: str) -> None:
            esc = html.escape(ip, quote=True)
            body = """<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>XIAO — видео и микрофон</title>
  <style>
    body { font-family: system-ui, sans-serif; background:#0f1419; color:#e6edf3; margin:0; padding:16px; }
    h1 { font-size:1.1rem; margin:0 0 8px 0; }
    .meta { color:#8b949e; font-size:0.85rem; margin-bottom:12px; }
    .vid { max-width:100%; width:min(960px,100%); height:auto; border:1px solid #30363d; border-radius:8px; background:#000; }
    .row { display:flex; flex-wrap:wrap; gap:10px; align-items:center; margin:12px 0; }
    button { cursor:pointer; padding:8px 14px; border-radius:6px; border:1px solid #30363d; background:#21262d; color:#e6edf3; font:inherit; }
    button:hover { border-color:#58a6ff; color:#58a6ff; }
    #micSt { font-size:0.8rem; color:#8b949e; }
    a { color:#58a6ff; }
  </style>
</head>
<body>
  <h1>XIAO CAM</h1>
  <div class="meta">Плата <code>""" + esc + """</code> · MJPEG <code>/stream</code> · PCM <code>/mic_s16</code> (16 kHz mono)</div>
  <p><img class="vid" src="/stream" alt="камера" decoding="async"/></p>
  <div class="row">
    <button type="button" id="btnMic">Включить звук с микрофона</button>
    <span id="micSt"></span>
  </div>
  <p><a href="/capture">один кадр</a> · <a href="/telemetry">телеметрия</a></p>
<script>
(function () {
  var btn = document.getElementById("btnMic");
  var micSt = document.getElementById("micSt");
  var ctx = null;
  var proc = null;
  var heap = new Uint8Array(0);
  function merge(u8) {
    var n = new Uint8Array(heap.byteLength + u8.byteLength);
    if (heap.byteLength) n.set(heap, 0);
    n.set(u8, heap.byteLength);
    heap = n;
  }
  btn.addEventListener("click", function () {
    if (ctx) {
      try { proc && proc.disconnect(); } catch (e) {}
      try { ctx.close(); } catch (e) {}
      ctx = null; proc = null; heap = new Uint8Array(0);
      btn.textContent = "Включить звук с микрофона";
      micSt.textContent = "";
      return;
    }
    micSt.textContent = "запрос микрофона…";
    ctx = new (window.AudioContext || window.webkitAudioContext)({ sampleRate: 16000 });
    ctx.resume().then(function () {
      return fetch("/mic_s16");
    }).then(function (res) {
      if (!res.ok) {
        return res.text().then(function (txt) {
          throw new Error("HTTP " + res.status + (txt ? (": " + txt.slice(0, 400)) : ""));
        });
      }
      var reader = res.body.getReader();
      (function pump() {
        reader.read().then(function (r) {
          if (r.done) return;
          if (r.value && r.value.byteLength) merge(r.value);
          return pump();
        }).catch(function () {});
      })();
      var K = 2048;
      proc = ctx.createScriptProcessor(K, 0, 1);
      proc.onaudioprocess = function (ev) {
        var out = ev.outputBuffer.getChannelData(0);
        var need = out.length * 2;
        if (heap.byteLength < need) {
          for (var i = 0; i < out.length; i++) out[i] = 0;
          return;
        }
        var dv = new DataView(heap.buffer, heap.byteOffset, need);
        for (var j = 0; j < out.length; j++) {
          out[j] = dv.getInt16(j * 2, true) / 32768.0;
        }
        heap = heap.subarray(need);
      };
      proc.connect(ctx.destination);
      btn.textContent = "Выключить звук";
      micSt.textContent = "PCM 16 kHz mono (с задержкой буфера)";
    }).catch(function (e) {
      micSt.textContent = "ошибка: " + e;
      try { ctx.close(); } catch (x) {}
      ctx = null;
    });
  });
})();
</script>
</body>
</html>"""
            data = body.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(data)

        def _api_telemetry(self) -> None:
            if not cam_ip:
                payload = {
                    "proxy_error": "no_camera_ip",
                    "hint": "Заполни camera_ip.txt или запусти прокси с аргументом IP. Плата в error_wifi не имеет LAN-IP.",
                    "camera_ip_txt": str(root / "camera_ip.txt"),
                }
                body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)
                return
            self._proxy("/telemetry", stream=False)

        def _stream_or_capture(self, cam_path: str, stream: bool) -> None:
            if not cam_ip:
                msg = b"no camera IP: set camera_ip.txt or run proxy with IP argument\r\n"
                self.send_response(503)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Content-Length", str(len(msg)))
                self.end_headers()
                self.wfile.write(msg)
                return
            self._proxy(cam_path, stream=stream)

        def _telemetry_dashboard(self) -> None:
            try:
                label = cam_ip if cam_ip else "(IP не задан — см. / )"
                # Совпадает с groupOf(): только ASCII «-» в «Wi-Fi», см. xiao_serial_telemetry.WIFI_GROUP_LABEL
                wifi_group_label = "Wi-Fi"
                body = f"""<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <meta http-equiv="Cache-Control" content="no-cache, no-store, must-revalidate" />
  <meta http-equiv="Pragma" content="no-cache" />
  <title>XIAO — телеметрия</title>
  <style>
    :root {{
      --bg: #0f1419;
      --panel: #1a2332;
      --text: #e6edf3;
      --muted: #8b949e;
      --accent: #58a6ff;
      --ok: #3fb950;
      --err: #f85149;
    }}
    * {{ box-sizing: border-box; }}
    html, body {{ height: 100%; margin: 0; }}
    body {{
      font-family: ui-sans-serif, system-ui, sans-serif;
      background: var(--bg);
      color: var(--text);
      display: flex;
      flex-direction: column;
    }}
    header {{
      flex: 0 0 auto;
      padding: 10px 14px;
      background: var(--panel);
      border-bottom: 1px solid #30363d;
    }}
    h1 {{ font-size: 1.05rem; margin: 0 0 4px 0; }}
    .sub {{ color: var(--muted); font-size: 0.8rem; line-height: 1.35; }}
    #status {{ font-size: 0.78rem; margin-top: 6px; }}
    #status.ok {{ color: var(--ok); }}
    #status.err {{ color: var(--err); }}
    a {{ color: var(--accent); }}
    #scroll {{
      flex: 1 1 auto;
      overflow: auto;
      padding: 12px 14px 20px;
      max-width: 1100px;
      width: 100%;
      margin: 0 auto;
    }}
    #panels {{ display: flex; flex-direction: column; gap: 12px; }}
    .card {{
      background: var(--panel);
      border-radius: 8px;
      padding: 10px 12px;
      border: 1px solid #30363d;
    }}
    .card h2 {{
      font-size: 0.72rem;
      text-transform: uppercase;
      letter-spacing: 0.05em;
      color: var(--accent);
      margin: 0 0 8px 0;
      font-weight: 600;
    }}
    table {{ width: 100%; table-layout: fixed; border-collapse: collapse; font-size: 0.8rem; font-variant-numeric: tabular-nums; }}
    td {{ padding: 3px 6px 3px 0; vertical-align: top; border-bottom: 1px solid #252d3a; }}
    tr:last-child td {{ border-bottom: none; }}
    th.n, th.k, th.v {{
      font-size: 0.72rem; font-weight: 600; color: #79c0ff; text-align: left;
      padding: 4px 6px 6px 0; border-bottom: 1px solid #30363d; vertical-align: bottom;
    }}
    th.n {{ text-align: right; width: 3rem; padding-right: 8px; color: #58a6ff; }}
    th.k {{ width: 34%; }}
    td.n {{ color: #e6edf3; width: 3rem; min-width: 3rem; text-align: right; padding-right: 8px; font-weight: 600; font-variant-numeric: tabular-nums; }}
    td.k {{ color: var(--muted); width: 34%; word-break: break-all; }}
    td.v {{ word-break: break-all; }}
    pre {{
      margin: 0;
      font-size: 0.68rem;
      line-height: 1.4;
      white-space: pre-wrap;
      word-break: break-all;
      max-height: min(38vh, 420px);
      overflow: auto;
      color: #c9d1d9;
    }}
  </style>
</head>
<body>
  <header>
    <h1>Телеметрия XIAO — одно окно</h1>
    <div class="sub">Прокси → <code>{label}</code> · <code>/api/telemetry</code> каждые 0,8 с ·
      <a href="/">видео</a><br />
      <span style="font-size:0.72rem;color:var(--muted)">Таблица с колонкой «№» — при старой странице: Ctrl+F5.</span></div>
    <div id="status" class="ok">ожидание…</div>
  </header>
  <div id="scroll">
    <div id="panels"></div>
    <div class="card" style="margin-top:12px">
      <h2>Полный JSON</h2>
      <pre id="raw"></pre>
    </div>
  </div>
  <script>
    const elPanels = document.querySelector("#panels");
    const elRaw = document.querySelector("#raw");
    const elSt = document.querySelector("#status");

    const GROUP_ORDER = [
      "Прокси", "Система и MCU", "Память", "Flash и OTA", "{wifi_group_label}",
      "Bluetooth LE", "Точка доступа (AP)", "Камера", "Микрофон", "Датчики",
      "USB / диагностика", "Прочее"
    ];
    const GROUP_ORDER_SET = new Set(GROUP_ORDER);

    function groupOf(k) {{
      if (k === "proxy_error" || k === "hint" || k === "camera_ip_txt") return "Прокси";
      if (k === "chip_temp_c") return "Датчики";
      if (k.startsWith("ble_")) return "Bluetooth LE";
      if (k.startsWith("mic_")) return "Микрофон";
      if (k.startsWith("cam_")) return "Камера";
      if (k.startsWith("ap_")) return "Точка доступа (AP)";
      if (k.startsWith("wifi_")) return "{wifi_group_label}";
      if (k.startsWith("part_") || k.startsWith("sketch_") || k.startsWith("flash_")) return "Flash и OTA";
      if (k.startsWith("heap_") || k.startsWith("psram_") || k === "stack_watermark" || k === "rtos_task_count")
        return "Память";
      if (k.startsWith("uptime") || k.startsWith("micros") || k.startsWith("reset") || k.startsWith("led_") ||
          k.startsWith("chip_") || k.startsWith("cpu_") || k === "sdk" || k.startsWith("core_") ||
          k.startsWith("arduino") || k.startsWith("efuse"))
        return "Система и MCU";
      if (k === "telemetry" || k === "telemetry_note" || k === "telemetry_via" || k === "telemetry_channel_ru" || k.endsWith("_reader_diag")) return "USB / диагностика";
      if (k === "usb" || k.startsWith("usb_")) return "USB / диагностика";
      if (k === "_pc_ts") return "Прочее";
      return "Прочее";
    }}

    function esc(s) {{
      return String(s).replace(/&/g,"&amp;").replace(/</g,"&lt;");
    }}

    function fmtVal(v) {{
      if (v !== null && typeof v === "object") return JSON.stringify(v);
      return String(v);
    }}

    function assignBuckets(obj) {{
      const buckets = {{}};
      GROUP_ORDER.forEach(function (g) {{ buckets[g] = []; }});
      Object.keys(obj).sort().forEach(function (k) {{
        const g = groupOf(k);
        if (!buckets[g]) buckets[g] = [];
        buckets[g].push({{ k: k, v: fmtVal(obj[k]) }});
      }});
      return buckets;
    }}

    function rowIndexByKey(buckets) {{
      const idx = {{}};
      let n = 0;
      function walk(rows) {{
        if (!rows || !rows.length) return;
        rows.forEach(function (r) {{
          n += 1;
          idx[r.k] = n;
        }});
      }}
      GROUP_ORDER.forEach(function (g) {{ walk(buckets[g]); }});
      Object.keys(buckets).sort().forEach(function (g) {{
        if (GROUP_ORDER_SET.has(g)) return;
        walk(buckets[g]);
      }});
      return idx;
    }}

    function render(obj) {{
      const buckets = assignBuckets(obj);
      const rowIdx = rowIndexByKey(buckets);
      let html = "";
      GROUP_ORDER.forEach(function (g) {{
        const rows = buckets[g];
        if (!rows || !rows.length) return;
        html += '<section class="card"><h2>' + esc(g) + '</h2><table><thead><tr><th class="n">№</th><th class="k">Параметр</th><th class="v">Значение</th></tr></thead><tbody>';
        rows.forEach(function (r) {{
          html += '<tr><td class="n">' + rowIdx[r.k] + '</td><td class="k">' + esc(r.k) + '</td><td class="v">' + esc(r.v) + '</td></tr>';
        }});
        html += "</tbody></table></section>";
      }});
      Object.keys(buckets).sort().forEach(function (g) {{
        if (GROUP_ORDER_SET.has(g)) return;
        const rows = buckets[g];
        if (!rows || !rows.length) return;
        html += '<section class="card"><h2>' + esc(g) + '</h2><table><thead><tr><th class="n">№</th><th class="k">Параметр</th><th class="v">Значение</th></tr></thead><tbody>';
        rows.forEach(function (r) {{
          html += '<tr><td class="n">' + rowIdx[r.k] + '</td><td class="k">' + esc(r.k) + '</td><td class="v">' + esc(r.v) + '</td></tr>';
        }});
        html += "</tbody></table></section>";
      }});
      elPanels.innerHTML = html;
      elRaw.textContent = JSON.stringify(obj, null, 2);
    }}

    async function tick() {{
      try {{
        const r = await fetch("/api/telemetry?r=" + Date.now(), {{ cache: "no-store" }});
        if (!r.ok) throw new Error("HTTP " + r.status);
        const j = await r.json();
        render(j);
        elSt.textContent = "обновлено " + new Date().toLocaleTimeString();
        elSt.className = "ok";
      }} catch (e) {{
        elSt.textContent = "ошибка: " + e;
        elSt.className = "err";
      }}
    }}
    tick();
    setInterval(tick, 800);
  </script>
</body>
</html>"""
                data = body.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
                self.send_header("Pragma", "no-cache")
                self.send_header("Expires", "0")
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(data)
            except Exception as e:
                msg = ("telemetry page error: %s\n" % e).encode("utf-8", errors="replace")
                try:
                    self.send_response(500)
                    self.send_header("Content-Type", "text/plain; charset=utf-8")
                    self.send_header("Content-Length", str(len(msg)))
                    self.send_header("Connection", "close")
                    self.end_headers()
                    self.wfile.write(msg)
                except Exception:
                    pass

        def _proxy(self, cam_path: str, stream: bool) -> None:
            assert cam_ip is not None
            try:
                conn = http.client.HTTPConnection(cam_ip, 80, timeout=300 if stream else 15)
                conn.request("GET", cam_path, headers={"Host": cam_ip, "Connection": "close"})
                resp = conn.getresponse()
                if resp.status != 200:
                    self.send_error(502, "camera HTTP %s" % resp.status)
                    conn.close()
                    return
                self.send_response(200)
                ct = resp.getheader("Content-Type", "application/octet-stream")
                if ct:
                    self.send_header("Content-Type", ct)
                cl = resp.getheader("Content-Length")
                if cl:
                    self.send_header("Content-Length", cl)
                self.send_header("Connection", "close")
                self.end_headers()
                while True:
                    chunk = resp.read(16384 if stream else -1)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                conn.close()
            except BrokenPipeError:
                pass
            except Exception as e:
                try:
                    self.send_error(502, repr(e))
                except Exception:
                    pass

    class ThreadHTTPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
        allow_reuse_address = False
        daemon_threads = True

        def server_bind(self) -> None:
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
            if sys.platform == "win32" and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                try:
                    self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
                except OSError:
                    pass
            super().server_bind()

    with ThreadHTTPServer(("127.0.0.1", args.port), Handler) as httpd:
        print(
            "xiao_cam_proxy PID=%d http=%d mic_tcp→%s:%d"
            % (os.getpid(), args.port, cam_ip or "?", mic_tcp_port),
            file=sys.stderr,
            flush=True,
        )
        if cam_ip:
            print("Открой: http://127.0.0.1:%d/  (камера %s)" % (args.port, cam_ip))
        else:
            print("Открой: http://127.0.0.1:%d/  (режим без IP камеры)" % args.port)
        print("Телеметрия: http://127.0.0.1:%d/telemetry" % args.port)
        httpd.serve_forever()


if __name__ == "__main__":
    raise SystemExit(main())
