#!/usr/bin/env python3
"""
Сбор телеметрии XIAO: с --http по умолчанию приоритет Wi‑Fi → USB (провод) → BLE, затем устаревшие снимки.
(компактный JSON в GATT, см. xiao_cam_stream.ino). Закрой Serial Monitor — COM занят одним клиентом.

Переменная окружения XIAO_CAM_TELEMETRY_URL — URL снимка (по умолчанию http://xiao-cam.local/telemetry).

Примеры:
  py -3 tools/xiao_serial_telemetry.py --port COM5
  py -3 tools/xiao_serial_telemetry.py --port COM5 --log telemetry.ndjson
  py -3 tools/xiao_serial_telemetry.py --port COM5 --pretty
  py -3 tools/xiao_serial_telemetry.py --port COM5 --http 8897
    → http://127.0.0.1:8897/ (режим --mode auto: Wi‑Fi → USB → BLE)
  py -3 tools/xiao_serial_telemetry.py --http 8897 --mode serial --no-wifi --no-ble
    → только USB
"""
from __future__ import annotations

import argparse
import http.server
import json
import os
import socket
import socketserver
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

LAST_USB: dict = {}
LAST_WIFI: dict = {}
LAST_BLE: dict = {}
TELEM_LOCK = threading.Lock()

WIFI_READER_DIAG: dict = {"wifi_ok": False, "wifi_last_error": "", "wifi_url": ""}
WIFI_READER_DIAG_LOCK = threading.Lock()
BLE_READER_DIAG: dict = {"ble_ok": False, "ble_last_error": ""}
BLE_READER_DIAG_LOCK = threading.Lock()

# Диагностика фонового чтения COM (для /api/telemetry пока нет JSON)
USB_READER_DIAG: dict = {
    "usb_reader_phase": "starting",
    "usb_reader_error": "",
    "usb_bytes_in": 0,
    "usb_lines_json": 0,
    "usb_skipped_nonjson": 0,
    "usb_buf_truncations": 0,
    "usb_last_skip": "",
}

# Без перевода строк буфер COM не должен расти бесконечно (шум/битый поток).
_SERIAL_BUF_MAX_CHARS = 196608
_SERIAL_BUF_TAIL_KEEP = 32768
USB_READER_DIAG_LOCK = threading.Lock()

# Одна строка для заголовка группы Wi‑Fi в HTML/JS: только ASCII «-», иначе
# GROUP_ORDER и groupOf() могут расходиться (разные Unicode-дефисы) и строки не попадут в таблицу.
WIFI_GROUP_LABEL = "Wi-Fi"

# Краткий вид: только главные поля (порядок — как в таблице)
CORE_TELEM_KEYS: tuple[str, ...] = (
    "uptime_ms",
    "reset_reason",
    "led_mode",
    "fw_build",
    "fw_version",
    "chip_model",
    "chip_revision",
    "cpu_mhz",
    "heap_free",
    "heap_min",
    "psram_free_esp",
    "wifi_status",
    "wifi_disc_reason",
    "wifi_ip",
    "wifi_rssi",
    "wifi_ssid",
    "chip_temp_c",
    "ble_adv_name",
    "ble_clients",
    "mic_rms",
    "mic_dbfs",
    "cam_frames_stream",
    "cam_captures",
)


def _filter_core_telemetry(obj: dict) -> dict:
    return {k: obj[k] for k in CORE_TELEM_KEYS if k in obj}


def _normalize_wifi_url(url: str) -> str:
    u = (url or "").strip()
    if not u:
        return "http://xiao-cam.local/telemetry"
    if not u.startswith("http://") and not u.startswith("https://"):
        u = "http://" + u
    if "/telemetry" not in u:
        u = u.rstrip("/") + "/telemetry"
    return u


def _telemetry_channel_ru(via: str) -> str:
    return {
        "wifi": "Wi-Fi",
        "serial": "USB (провод)",
        "ble": "Bluetooth LE",
        "wifi_stale": "Wi-Fi (устар.)",
        "serial_stale": "USB (устар.)",
        "ble_stale": "Bluetooth LE (устар.)",
    }.get(via, via if via else "—")


def _merge_telemetry_unlocked(port: str, baud: int) -> dict:
    """Приоритет: свежий Wi‑Fi → свежий USB → свежий BLE; иначе самый новый «устаревший»."""
    mono = time.monotonic()

    def fresh(d: dict, sec: float) -> bool:
        if not d:
            return False
        return mono - float(d.get("_mono_ts", 0.0)) < sec

    if fresh(LAST_WIFI, 3.2):
        o = dict(LAST_WIFI)
        o["telemetry_via"] = "wifi"
        o["telemetry_channel_ru"] = _telemetry_channel_ru("wifi")
        return o
    if fresh(LAST_USB, 5.0):
        o = dict(LAST_USB)
        o["telemetry_via"] = "serial"
        o["telemetry_channel_ru"] = _telemetry_channel_ru("serial")
        return o
    if fresh(LAST_BLE, 6.5):
        o = dict(LAST_BLE)
        o["telemetry_via"] = "ble"
        o["telemetry_channel_ru"] = _telemetry_channel_ru("ble")
        return o

    best: dict | None = None
    best_ts = -1.0
    best_src = ""
    for src, d in (("wifi", LAST_WIFI), ("serial", LAST_USB), ("ble", LAST_BLE)):
        if not d:
            continue
        ts = float(d.get("_mono_ts", 0.0))
        if ts > best_ts:
            best_ts, best, best_src = ts, dict(d), src
    if best and best_ts > 0:
        via = best_src + "_stale"
        best["telemetry_via"] = via
        best["telemetry_channel_ru"] = _telemetry_channel_ru(via)
        return best

    return {
        "telemetry": "waiting_for_first_update",
        "telemetry_note": "Нет свежих данных: опрос Wi‑Fi → USB Serial → BLE (компакт).",
        "telemetry_via": "",
        "telemetry_channel_ru": "нет данных",
        "usb_port": port,
        "usb_baud": baud,
    }


def _kbd_wants_brief() -> bool:
    """Неблокирующая проверка: пользователь нажал B (краткий снимок в консоли)."""
    if not sys.stdin.isatty():
        return False
    if sys.platform == "win32":
        import msvcrt

        hit = False
        while msvcrt.kbhit():
            c = msvcrt.getch()
            if c in (b"b", b"B"):
                hit = True
        return hit
    import select

    r, _, _ = select.select([sys.stdin], [], [], 0)
    if not r:
        return False
    ch = sys.stdin.read(1)
    return ch.lower() == "b"


def _wifi_poller(url: str, interval: float) -> None:
    import urllib.request

    nu = _normalize_wifi_url(url)
    with WIFI_READER_DIAG_LOCK:
        WIFI_READER_DIAG["wifi_url"] = nu
    while True:
        try:
            req = urllib.request.Request(nu, headers={"Connection": "close", "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=3.0) as resp:
                body = resp.read().decode("utf-8", errors="replace")
            obj = json.loads(body)
            obj["_mono_ts"] = time.monotonic()
            obj["_pc_ts"] = datetime.now(timezone.utc).isoformat()
            with TELEM_LOCK:
                LAST_WIFI.clear()
                LAST_WIFI.update(obj)
            with WIFI_READER_DIAG_LOCK:
                WIFI_READER_DIAG["wifi_ok"] = True
                WIFI_READER_DIAG["wifi_last_error"] = ""
        except Exception as e:
            with WIFI_READER_DIAG_LOCK:
                WIFI_READER_DIAG["wifi_ok"] = False
                WIFI_READER_DIAG["wifi_last_error"] = repr(e)
        time.sleep(max(0.25, float(interval)))


def _ble_reader_thread(name_sub: str, char_uuid: str) -> None:
    import asyncio

    try:
        from bleak import BleakClient, BleakScanner
    except ImportError:
        sys.stderr.write("BLE: установите bleak: py -3 -m pip install bleak\n")
        with BLE_READER_DIAG_LOCK:
            BLE_READER_DIAG["ble_last_error"] = "bleak not installed"
        return

    async def main() -> None:
        uid = char_uuid.lower()
        while True:
            try:
                devices = await BleakScanner.discover(timeout=5.0)
                target = None
                for d in devices:
                    name = d.name or ""
                    if name_sub.lower() in name.lower():
                        target = d
                        break
                if target is None:
                    with BLE_READER_DIAG_LOCK:
                        BLE_READER_DIAG["ble_ok"] = False
                        BLE_READER_DIAG["ble_last_error"] = "device not found"
                    await asyncio.sleep(2.5)
                    continue
                async with BleakClient(target.address) as client:
                    with BLE_READER_DIAG_LOCK:
                        BLE_READER_DIAG["ble_ok"] = True
                        BLE_READER_DIAG["ble_last_error"] = ""
                    while client.is_connected:
                        raw = await client.read_gatt_char(uid)
                        txt = raw.decode("utf-8", errors="replace").strip()
                        if txt.startswith("{"):
                            obj = json.loads(txt)
                            obj["_mono_ts"] = time.monotonic()
                            obj["_pc_ts"] = datetime.now(timezone.utc).isoformat()
                            with TELEM_LOCK:
                                LAST_BLE.clear()
                                LAST_BLE.update(obj)
                        await asyncio.sleep(1.35)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                with BLE_READER_DIAG_LOCK:
                    BLE_READER_DIAG["ble_ok"] = False
                    BLE_READER_DIAG["ble_last_error"] = repr(e)
                await asyncio.sleep(2.0)

    asyncio.run(main())


def _dashboard_html(port: int, com: str, mode_line: str) -> str:
    core_keys_js = json.dumps(list(CORE_TELEM_KEYS), ensure_ascii=False)
    return f"""<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <meta http-equiv="Cache-Control" content="no-cache, no-store, must-revalidate" />
  <meta http-equiv="Pragma" content="no-cache" />
  <title>XIAO — USB телеметрия</title>
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
    #toolbar {{ margin-top: 10px; display: flex; flex-wrap: wrap; align-items: center; gap: 10px; }}
    #btnCore {{
      cursor: pointer;
      font: inherit;
      font-size: 0.82rem;
      padding: 6px 12px;
      border-radius: 6px;
      border: 1px solid #30363d;
      background: #21262d;
      color: var(--text);
    }}
    #btnCore:hover {{ border-color: var(--accent); color: var(--accent); }}
    #btnCore.active {{
      background: #1f3d2f;
      border-color: var(--ok);
      color: var(--ok);
    }}
    .hint {{ font-size: 0.72rem; color: var(--muted); }}
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
    <h1>Телеметрия XIAO</h1>
    <div class="sub">Приоритет: {mode_line}<br />
      COM <code>{com}</code> · локальный UI <code>127.0.0.1:{port}</code> ·
      опрос <code>/api/telemetry</code> каждые 0,5 с<br />
      <span class="hint">Нет колонки «№»? Обнови вкладку с принудительным сбросом кэша (Ctrl+F5) и перезапусти скрипт телеметрии.</span></div>
    <div id="toolbar">
      <button type="button" id="btnCore" title="Только основные поля: MCU, память, Wi‑Fi, температура, камера">Основные</button>
      <span class="hint">полный вид ↔ краткий (одна таблица)</span>
    </div>
    <div id="status" class="ok">ожидание…</div>
  </header>
  <div id="scroll">
    <div id="usbWaitBanner" class="card" style="display:none;margin-bottom:12px;border-color:#f85149;">
      <h2 style="color:#f85149">Нет строки JSON с платы</h2>
      <pre id="usbWaitTxt" style="max-height:220px"></pre>
    </div>
    <div id="panels"></div>
    <div id="rawCard" class="card" style="margin-top:12px">
      <h2>Полный JSON</h2>
      <pre id="raw"></pre>
    </div>
  </div>
  <script>
    const CORE_KEYS = {core_keys_js};
    const elPanels = document.querySelector("#panels");
    const elRaw = document.querySelector("#raw");
    const elRawCard = document.querySelector("#rawCard");
    const elSt = document.querySelector("#status");
    const elBtnCore = document.querySelector("#btnCore");
    let coreOnly = false;

    const GROUP_ORDER = [
      "Прокси", "Система и MCU", "Память", "Flash и OTA", "{WIFI_GROUP_LABEL}",
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
      if (k.startsWith("wifi_")) return "{WIFI_GROUP_LABEL}";
      if (k.startsWith("part_") || k.startsWith("sketch_") || k.startsWith("flash_")) return "Flash и OTA";
      if (k.startsWith("heap_") || k.startsWith("psram_") || k === "stack_watermark" || k === "rtos_task_count")
        return "Память";
      if (k.startsWith("uptime") || k.startsWith("micros") || k.startsWith("reset") || k.startsWith("led_") ||
          k.startsWith("fw_") || k.startsWith("chip_") || k.startsWith("cpu_") || k === "sdk" || k.startsWith("core_") ||
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

    /** Единая нумерация: как в полном виде (порядок секций GROUP_ORDER + лишние группы). */
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

    function renderFull(obj) {{
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

    function renderCore(obj) {{
      const buckets = assignBuckets(obj);
      const rowIdx = rowIndexByKey(buckets);
      const rows = [];
      CORE_KEYS.forEach(function (k) {{
        if (Object.prototype.hasOwnProperty.call(obj, k)) {{
          rows.push({{ k: k, v: fmtVal(obj[k]) }});
        }}
      }});
      let html = '<section class="card"><h2>Основные параметры</h2><table><thead><tr><th class="n">№</th><th class="k">Параметр</th><th class="v">Значение</th></tr></thead><tbody>';
      rows.forEach(function (r) {{
        const num = rowIdx[r.k] != null ? String(rowIdx[r.k]) : "—";
        html += '<tr><td class="n">' + num + '</td><td class="k">' + esc(r.k) + '</td><td class="v">' + esc(r.v) + '</td></tr>';
      }});
      html += "</tbody></table></section>";
      elPanels.innerHTML = html;
      const slim = {{}};
      CORE_KEYS.forEach(function (k) {{
        if (Object.prototype.hasOwnProperty.call(obj, k)) slim[k] = obj[k];
      }});
      if (Object.prototype.hasOwnProperty.call(obj, "_pc_ts")) {{
        slim._pc_ts = obj._pc_ts;
      }}
      elRaw.textContent = JSON.stringify(slim, null, 2);
    }}

    function render(obj) {{
      if (coreOnly) {{
        renderCore(obj);
      }} else {{
        renderFull(obj);
      }}
      elRawCard.style.display = coreOnly ? "none" : "block";
      const ban = document.querySelector("#usbWaitBanner");
      const tx = document.querySelector("#usbWaitTxt");
      if (ban && tx) {{
        if (obj.telemetry === "waiting_for_first_update" || obj.usb === "waiting_for_first_json_line") {{
          ban.style.display = "block";
          const usb = obj.usb_reader_diag || {{}};
          const info = {{
            hint: "По умолчанию: свежий Wi‑Fi → USB → BLE. Проверь URL, COM и bleak. Прошивка: Serial ~1.5 с + GET /telemetry + BLE compact.",
            usb_port: obj.usb_port,
            usb_baud: obj.usb_baud,
            reader_phase: usb.usb_reader_phase,
            reader_error: usb.usb_reader_error || "",
            bytes_from_board: usb.usb_bytes_in || 0,
            json_lines_ok: usb.usb_lines_json || 0,
            skipped_non_json: usb.usb_skipped_nonjson || 0,
            last_skipped_preview: usb.usb_last_skip || "",
            wifi_reader_diag: obj.wifi_reader_diag || {{}},
            ble_reader_diag: obj.ble_reader_diag || {{}}
          }};
          tx.textContent = JSON.stringify(info, null, 2);
        }} else {{
          ban.style.display = "none";
        }}
      }}
    }}

    elBtnCore.addEventListener("click", function () {{
      coreOnly = !coreOnly;
      elBtnCore.classList.toggle("active", coreOnly);
      elBtnCore.textContent = coreOnly ? "Основные ✓" : "Основные";
    }});

    async function tick() {{
      try {{
        const r = await fetch("/api/telemetry?r=" + Date.now(), {{ cache: "no-store" }});
        if (!r.ok) throw new Error("HTTP " + r.status);
        const j = await r.json();
        render(j);
        elSt.textContent = "обновлено " + new Date().toLocaleTimeString() + (coreOnly ? " · краткий вид" : "") +
          (j.telemetry === "waiting_for_first_update" || j.usb === "waiting_for_first_json_line" ? " · ждём данные" : (" · канал: " + (j.telemetry_channel_ru || j.telemetry_via || "?")));
        elSt.className = "ok";
      }} catch (e) {{
        elSt.textContent = "ошибка: " + e;
        elSt.className = "err";
      }}
    }}
    tick();
    setInterval(tick, 500);
  </script>
</body>
</html>"""


def _serial_reader(port: str, baud: int, log_f) -> None:
    import serial

    def _diag(**kwargs) -> None:
        with USB_READER_DIAG_LOCK:
            USB_READER_DIAG.update(kwargs)

    _diag(usb_reader_phase="opening_port", usb_reader_error="")
    try:
        ser = serial.Serial(port, baud, timeout=0.3)
    except Exception as e:
        _diag(usb_reader_phase="open_failed", usb_reader_error=repr(e))
        sys.stderr.write("Serial reader: не удалось открыть %s: %s\n" % (port, e))
        return

    _diag(usb_reader_phase="reading", usb_reader_error="")
    buf = ""
    try:
        while True:
            chunk = ser.read(4096)
            if not chunk:
                continue
            with USB_READER_DIAG_LOCK:
                USB_READER_DIAG["usb_bytes_in"] = int(USB_READER_DIAG.get("usb_bytes_in", 0)) + len(chunk)
            buf += chunk.decode("utf-8", errors="replace")
            if len(buf) > _SERIAL_BUF_MAX_CHARS:
                cut = buf.rfind("\n")
                if cut >= 0:
                    buf = buf[cut + 1 :]
                else:
                    buf = buf[-_SERIAL_BUF_TAIL_KEEP:]
                with USB_READER_DIAG_LOCK:
                    USB_READER_DIAG["usb_buf_truncations"] = int(
                        USB_READER_DIAG.get("usb_buf_truncations", 0)
                    ) + 1
                    USB_READER_DIAG["usb_last_skip"] = "serial_buffer_overflow"
            while "\n" in buf:
                line, buf = buf.split("\n", 1)
                line = line.replace("\r", "").strip()
                if not line:
                    continue
                # Строка может начинаться не с «{» (мусор/USB); ищем первый объект JSON по строке.
                brace = line.find("{")
                if brace < 0:
                    with USB_READER_DIAG_LOCK:
                        USB_READER_DIAG["usb_skipped_nonjson"] = int(USB_READER_DIAG.get("usb_skipped_nonjson", 0)) + 1
                        if len(line) > 120:
                            USB_READER_DIAG["usb_last_skip"] = line[:117] + "..."
                        else:
                            USB_READER_DIAG["usb_last_skip"] = line
                    continue
                json_slice = line[brace:]
                try:
                    obj = json.loads(json_slice)
                except json.JSONDecodeError:
                    with USB_READER_DIAG_LOCK:
                        USB_READER_DIAG["usb_skipped_nonjson"] = int(USB_READER_DIAG.get("usb_skipped_nonjson", 0)) + 1
                        USB_READER_DIAG["usb_last_skip"] = json_slice[:120]
                    continue
                ts = datetime.now(timezone.utc).isoformat()
                out = dict(obj)
                out["_pc_ts"] = ts
                out["_mono_ts"] = time.monotonic()
                with TELEM_LOCK:
                    LAST_USB.clear()
                    LAST_USB.update(out)
                with USB_READER_DIAG_LOCK:
                    USB_READER_DIAG["usb_lines_json"] = int(USB_READER_DIAG.get("usb_lines_json", 0)) + 1
                if log_f:
                    log_f.write(json.dumps(out, ensure_ascii=False) + "\n")
                    log_f.flush()
    except Exception as e:
        _diag(usb_reader_phase="read_loop_failed", usb_reader_error=repr(e))
        sys.stderr.write("Serial reader: ошибка чтения: %s\n" % e)
    finally:
        ser.close()
        _diag(usb_reader_phase="closed")


def _run_http_mode(
    port: str,
    baud: int,
    http_port: int,
    log_path: Path | None,
    mode: str,
    wifi_url: str,
    wifi_poll: float,
    start_wifi: bool,
    start_serial: bool,
    start_ble: bool,
    ble_name: str,
    ble_char_uuid: str,
) -> int:
    import serial  # noqa: F401 — проверка импорта до потока

    log_f = log_path.open("a", encoding="utf-8") if log_path else None
    if log_f:
        log_f.write(
            "# http mode %s port=%s baud=%s http=%s mode=%s wifi=%s serial=%s ble=%s\n"
            % (
                datetime.now(timezone.utc).isoformat(),
                port,
                baud,
                http_port,
                mode,
                start_wifi,
                start_serial,
                start_ble,
            )
        )
        log_f.flush()

    if start_wifi:
        threading.Thread(target=_wifi_poller, args=(wifi_url, wifi_poll), daemon=True).start()
    if start_serial:
        threading.Thread(target=_serial_reader, args=(port, baud, log_f), daemon=True).start()
    if start_ble:
        threading.Thread(target=_ble_reader_thread, args=(ble_name, ble_char_uuid), daemon=True).start()

    parts: list[str] = []
    if start_wifi:
        parts.append(WIFI_GROUP_LABEL)
    if start_serial:
        parts.append("USB")
    if start_ble:
        parts.append("BLE")
    mode_line = " → ".join(parts) if parts else "(каналы выключены)"

    class H(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt, *args):
            sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

        def do_GET(self) -> None:
            path = self.path.split("?", 1)[0]
            try:
                if path == "/api/telemetry":
                    with TELEM_LOCK:
                        payload = _merge_telemetry_unlocked(port, baud)
                    out = dict(payload)
                    with USB_READER_DIAG_LOCK:
                        out["usb_reader_diag"] = dict(USB_READER_DIAG)
                    with WIFI_READER_DIAG_LOCK:
                        out["wifi_reader_diag"] = dict(WIFI_READER_DIAG)
                    with BLE_READER_DIAG_LOCK:
                        out["ble_reader_diag"] = dict(BLE_READER_DIAG)
                    if out.get("telemetry") == "waiting_for_first_update":
                        out["usb_port"] = port
                        out["usb_baud"] = baud
                    body = json.dumps(out, ensure_ascii=False).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("Access-Control-Allow-Origin", "*")
                    self.send_header("Connection", "close")
                    self.end_headers()
                    self.wfile.write(body)
                elif path == "/":
                    html = _dashboard_html(http_port, port, mode_line).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(html)))
                    self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
                    self.send_header("Pragma", "no-cache")
                    self.send_header("Expires", "0")
                    self.send_header("Connection", "close")
                    self.end_headers()
                    self.wfile.write(html)
                else:
                    self.send_error(404)
            except Exception as e:
                sys.stderr.write("HTTP GET %s: %s\n" % (path, e))
                try:
                    msg = ("internal error: %s" % e).encode("utf-8", errors="replace")
                    self.send_response(500)
                    self.send_header("Content-Type", "text/plain; charset=utf-8")
                    self.send_header("Content-Length", str(len(msg)))
                    self.send_header("Connection", "close")
                    self.end_headers()
                    self.wfile.write(msg)
                except Exception:
                    pass

    class Srv(socketserver.ThreadingMixIn, socketserver.TCPServer):
        # True + два запуска на Windows иногда дают «два слушателя» на один порт и
        # браузер получает ERR_EMPTY_RESPONSE / сброс. Один процесс — один bind.
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

    print("Телеметрия → http://127.0.0.1:%d/  (%s)" % (http_port, mode_line), file=sys.stderr)
    if start_serial:
        print("USB Serial %s @ %d — фон." % (port, baud), file=sys.stderr)
    if start_wifi:
        print("%s: %s" % (WIFI_GROUP_LABEL, _normalize_wifi_url(wifi_url)), file=sys.stderr)
    if start_ble:
        print("BLE: имя~%r GATT %s" % (ble_name, ble_char_uuid), file=sys.stderr)
    print("Ctrl+C — остановить сервер.", file=sys.stderr)
    with Srv(("127.0.0.1", http_port), H) as httpd:
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nОстанов.", file=sys.stderr)
    if log_f:
        log_f.close()
    return 0


def _run_console_mode(port: str, baud: int, log_path: Path | None, pretty: bool) -> int:
    import serial

    try:
        ser = serial.Serial(port, baud, timeout=0.3)
    except serial.SerialException as e:
        print("Не удалось открыть %s: %s" % (port, e), file=sys.stderr)
        return 1

    log_f = log_path.open("a", encoding="utf-8") if log_path else None
    if log_f:
        log_f.write("# started %s port=%s baud=%s\n" % (datetime.now(timezone.utc).isoformat(), port, baud))
        log_f.flush()

    print("Слушаю %s @ %d. Клавиша B — краткий JSON (основные поля). Ctrl+C — выход." % (port, baud), file=sys.stderr)
    buf = ""
    last_obj: dict | None = None
    last_pc_ts = ""

    try:
        while True:
            chunk = ser.read(4096)
            if not chunk:
                if _kbd_wants_brief() and last_obj is not None:
                    brief = _filter_core_telemetry(last_obj)
                    brief["_pc_ts"] = last_pc_ts
                    print(json.dumps(brief, ensure_ascii=False), flush=True)
                continue
            buf += chunk.decode("utf-8", errors="replace")
            while "\n" in buf:
                line, buf = buf.split("\n", 1)
                line = line.strip()
                if not line:
                    continue
                brace = line.find("{")
                if brace < 0:
                    continue
                json_slice = line[brace:]
                try:
                    obj = json.loads(json_slice)
                except json.JSONDecodeError:
                    print("skip (not JSON):", json_slice[:120], file=sys.stderr)
                    continue

                ts = datetime.now(timezone.utc).isoformat()
                last_obj = obj
                last_pc_ts = ts
                if pretty:
                    print("--- %s ---" % ts)
                    print(json.dumps(obj, ensure_ascii=False, indent=2))
                else:
                    out = dict(obj)
                    out["_pc_ts"] = ts
                    print(json.dumps(out, ensure_ascii=False))

                if log_f:
                    rec = dict(obj)
                    rec["_pc_ts"] = ts
                    log_f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                    log_f.flush()
    except KeyboardInterrupt:
        print("\nОстанов.", file=sys.stderr)
    finally:
        ser.close()
        if log_f:
            log_f.close()

    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description="XIAO CAM telemetry: с --http по умолчанию Wi‑Fi → USB → BLE (--mode auto)"
    )
    ap.add_argument("--port", default="COM5", help="Serial port (Windows: COM5, …)")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument(
        "--log",
        type=Path,
        metavar="FILE",
        help="Append each valid JSON line (NDJSON) with ISO timestamp",
    )
    ap.add_argument("--pretty", action="store_true", help="Pretty-print JSON to stdout (только без --http)")
    ap.add_argument(
        "--http",
        type=int,
        metavar="PORT",
        nargs="?",
        const=8897,
        help="Поднять http://127.0.0.1:PORT/ для окна браузера (по умолчанию 8897)",
    )
    ap.add_argument(
        "--mode",
        choices=("auto", "wifi", "serial", "ble"),
        default="auto",
        help="Источник в --http: auto = Wi‑Fi → USB → BLE (по умолчанию); иначе один канал",
    )
    ap.add_argument(
        "--wifi-url",
        default=os.environ.get("XIAO_CAM_TELEMETRY_URL", "http://xiao-cam.local/telemetry"),
        help="URL GET /telemetry с платы (переменная XIAO_CAM_TELEMETRY_URL)",
    )
    ap.add_argument("--wifi-poll", type=float, default=0.75, metavar="SEC", help="Интервал опроса Wi‑Fi")
    ap.add_argument("--no-wifi", action="store_true", help="Не опрашивать Wi‑Fi")
    ap.add_argument("--no-serial", action="store_true", help="Не открывать COM")
    ap.add_argument("--no-ble", action="store_true", help="Не читать BLE")
    ap.add_argument("--ble-name", default="xiao-cam", help="Подстрока в рекламном имени BLE")
    ap.add_argument(
        "--ble-char-uuid",
        default="beb5483e-36e1-4688-b7f7-eaa05907848d",
        help="UUID GATT с компактным JSON (см. xiao_cam_stream.ino)",
    )
    args = ap.parse_args()

    try:
        import serial  # noqa: F401
    except ImportError:
        print("Нужен пакет pyserial: py -3 -m pip install pyserial", file=sys.stderr)
        return 2

    if args.mode == "auto":
        start_wifi, start_serial, start_ble = not args.no_wifi, not args.no_serial, not args.no_ble
    elif args.mode == "wifi":
        start_wifi, start_serial, start_ble = True, False, False
    elif args.mode == "serial":
        start_wifi, start_serial, start_ble = False, True, False
    else:
        start_wifi, start_serial, start_ble = False, False, True

    start_wifi = start_wifi and not args.no_wifi
    start_serial = start_serial and not args.no_serial
    start_ble = start_ble and not args.no_ble

    if args.http is not None:
        if not start_wifi and not start_serial and not start_ble:
            print("Ошибка: все каналы выключены (--mode / --no-*)", file=sys.stderr)
            return 2
        return _run_http_mode(
            args.port,
            args.baud,
            args.http,
            args.log,
            args.mode,
            args.wifi_url,
            args.wifi_poll,
            start_wifi,
            start_serial,
            start_ble,
            args.ble_name,
            args.ble_char_uuid,
        )
    return _run_console_mode(args.port, args.baud, args.log, args.pretty)


if __name__ == "__main__":
    raise SystemExit(main())
