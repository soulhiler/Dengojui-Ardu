#!/usr/bin/env python3
"""
Сбор телеметрии XIAO: с --http по умолчанию приоритет Wi‑Fi → USB (провод) → BLE, затем устаревшие снимки.
(компактный JSON в GATT, см. xiao_cam_stream.ino). Закрой Serial Monitor — COM занят одним клиентом.

Переменные окружения:
  XIAO_CAM_TELEMETRY_URL — полный URL снимка (приоритетнее файла с IP).
  XIAO_BOARD_IP — только IP платы в LAN (если нет URL выше): подставится http://IP/telemetry.

На Windows имя xiao-cam.local часто не резолвится: положите в корень репозитория или в tools/
файл camera_ip.txt (одна строка: IPv4 платы) или запустите с --board-ip 192.168.x.x.

Примеры:
  py -3 tools/xiao_serial_telemetry.py --port COM5
  py -3 tools/xiao_serial_telemetry.py --port COM5 --log telemetry.ndjson
  py -3 tools/xiao_serial_telemetry.py --port COM5 --pretty
  py -3 tools/xiao_serial_telemetry.py --port COM5 --http 8897
    → UI: http://127.0.0.1:8897/live (корень / редиректит на /live; Chrome кэширует только /)
    → проверка версии: http://127.0.0.1:8897/api/ui-meta
  py -3 tools/xiao_serial_telemetry.py --http 8897 --board-ip 192.168.1.50
    → опрос http://192.168.1.50/telemetry (удобно, если mDNS не работает)
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

# --- spatial: накопленное цветное облако (камера+ToF) для 3D-визуализатора ---
# Переиспользуем геометрию модуля spatial/ (та же, что в build_model.py CLI).
_SPATIAL_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "spatial")
if os.path.isdir(_SPATIAL_DIR) and _SPATIAL_DIR not in sys.path:
    sys.path.insert(0, _SPATIAL_DIR)
try:
    import math as _math
    from tof_cloud import CloudConfig, PointCloud, Pose  # noqa: E402
    from build_model import add_frame  # noqa: E402  (есть __main__-guard, main не запускается)
    from xiao_client import fetch_frame  # noqa: E402
    from world_model import WorldModel  # noqa: E402
    from world_service import WorldService  # noqa: E402
    _HAVE_SPATIAL = True
except Exception:
    _HAVE_SPATIAL = False

_CLOUD_LOCK = threading.Lock()
_CLOUD = {
    "pc": PointCloud(voxel_m=0.03) if _HAVE_SPATIAL else None,
    "cfg": CloudConfig() if _HAVE_SPATIAL else None,
}

# Автономная персистентная модель пространства (сервис стартует в _run_http_mode).
_WORLD = WorldModel(voxel_m=0.05) if _HAVE_SPATIAL else None
_WORLD_SVC = None  # WorldService — создаётся при старте HTTP
_WORLD_PATH = os.path.join(_SPATIAL_DIR, "world", "room.world.gz")
_WORLD_SESSIONS = os.path.join(_SPATIAL_DIR, "sessions")

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

# Краткий вид: 10 полей — порядок, ключ JSON, русская подпись, расшифровка (подсказка).
CORE_TELEM_MAIN: tuple[tuple[str, str, str], ...] = (
    (
        "uptime_ms",
        "Время с перезагрузки",
        "uptime_ms — сколько миллисекунд MCU работает с последнего сброса (не календарные часы).",
    ),
    (
        "fw_version",
        "Версия прошивки",
        "fw_version — человекочитаемый номер сборки; рядом в полном JSON есть fw_build (счётчик).",
    ),
    (
        "wifi_status",
        "Wi‑Fi: статус",
        "wifi_status — текстовое состояние STA (например WL_CONNECTED); по коду см. wifi_status_code.",
    ),
    (
        "wifi_ssid",
        "Имя сети (SSID)",
        "wifi_ssid — к какой точке доступа подключена плата в режиме клиента (STA).",
    ),
    (
        "wifi_ip",
        "IP в сети",
        "wifi_ip — адрес платы в вашей LAN; по нему же открываются /telemetry и веб-камера.",
    ),
    (
        "wifi_rssi",
        "Сигнал Wi‑Fi (RSSI)",
        "wifi_rssi — мощность сигнала в dBm (обычно от −30 до −90; чем ближе к нулю, тем сильнее).",
    ),
    (
        "heap_free",
        "Свободная куча (SRAM)",
        "heap_free — свободная внутренняя RAM для malloc в байтах; мало свободного — риск нестабильности.",
    ),
    (
        "psram_free_esp",
        "Свободная PSRAM",
        "psram_free_esp — свободная внешняя PSRAM (байты), у Sense обычно под буферы камеры/потока.",
    ),
    (
        "cam_frames_stream",
        "Кадров MJPEG всего",
        "cam_frames_stream — сколько JPEG-кадров уже отдали по /stream с момента запуска (накопительный счётчик).",
    ),
    (
        "mic_dbfs",
        "Уровень микрофона (dBFS)",
        "mic_dbfs — громкость с PDM в децибелах относительно полной шкалы; тише ≈ отрицательные значения.",
    ),
)

CORE_TELEM_KEYS: tuple[str, ...] = tuple(row[0] for row in CORE_TELEM_MAIN)


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


def _looks_like_ipv4(token: str) -> bool:
    parts = token.strip().split(".")
    if len(parts) != 4:
        return False
    try:
        return all(0 <= int(x) <= 255 for x in parts)
    except ValueError:
        return False


def _discover_board_ip_from_files() -> str | None:
    """Ищет IPv4 в camera_ip.txt / board_ip.txt / xiao_ip.txt рядом со скриптом или в cwd."""
    names = ("camera_ip.txt", "board_ip.txt", "xiao_ip.txt")
    here = Path(__file__).resolve()
    roots = (here.parent.parent, here.parent, Path.cwd())
    for root in roots:
        for name in names:
            p = root / name
            try:
                raw = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for line in raw.splitlines():
                line = line.split("#", 1)[0].strip()
                if not line:
                    continue
                token = line.split()[0]
                if _looks_like_ipv4(token):
                    return token
    return None


def resolve_telemetry_wifi_url(cli_wifi_url: str | None, cli_board_ip: str | None) -> str:
    """Цепочка: --wifi-url → XIAO_CAM_TELEMETRY_URL → --board-ip / XIAO_BOARD_IP → файлы → xiao-cam.local."""
    if cli_wifi_url:
        return _normalize_wifi_url(cli_wifi_url)
    env_full = os.environ.get("XIAO_CAM_TELEMETRY_URL", "").strip()
    if env_full:
        return _normalize_wifi_url(env_full)
    bip = (cli_board_ip or "").strip() or os.environ.get("XIAO_BOARD_IP", "").strip()
    if bip:
        return _normalize_wifi_url("http://" + bip + "/telemetry")
    discovered = _discover_board_ip_from_files()
    if discovered:
        return _normalize_wifi_url("http://" + discovered + "/telemetry")
    return _normalize_wifi_url("http://xiao-cam.local/telemetry")


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
        "proxy_hint": "Если COM недоступен, а xiao-cam.local не открывается (Windows): "
        "запустите с --board-ip IP_платы или положите camera_ip.txt с IP в корень репозитория; "
        "для BLE: py -3 -m pip install bleak.",
        "usb_port": port,
        "usb_baud": baud,
    }


def _board_ip_for_control(port: str, baud: int) -> str | None:
    """IP платы из свежей телеметрии (для GET /board/control → плата /control)."""
    with TELEM_LOCK:
        d = _merge_telemetry_unlocked(port, baud)
    ip = d.get("wifi_ip")
    if isinstance(ip, str):
        t = ip.strip()
        if t and t not in ("0.0.0.0",):
            return t
    bip = os.environ.get("XIAO_BOARD_IP", "").strip()
    if bip and _looks_like_ipv4(bip):
        return bip
    discovered = _discover_board_ip_from_files()
    if discovered:
        return discovered
    return None


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


def _dashboard_html(port: int, com: str, mode_line: str, ui_session_rev: str) -> str:
    core_keys_js = json.dumps(list(CORE_TELEM_KEYS), ensure_ascii=False)
    core_titles_js = json.dumps({row[0]: row[1] for row in CORE_TELEM_MAIN}, ensure_ascii=False)
    core_hints_js = json.dumps({row[0]: row[2] for row in CORE_TELEM_MAIN}, ensure_ascii=False)
    script_mtime = str(int(Path(__file__).stat().st_mtime))
    return f"""<!DOCTYPE html>
<!-- ui-build: {ui_session_rev} mtime={script_mtime} path=/live -->
<html lang="ru">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <meta http-equiv="Cache-Control" content="no-cache, no-store, must-revalidate" />
  <meta http-equiv="Pragma" content="no-cache" />
  <meta name="ui-session-rev" content="{ui_session_rev}" />
  <meta name="ui-script-mtime" content="{script_mtime}" />
  <title>Тлм Монеткожуй</title>
  <style>
    :root {{
      --bg: #0a0e14;
      --bg2: #111822;
      --panel: #151d2b;
      --panel2: #1c2738;
      --text: #f0f3f6;
      --muted: #8b9cb3;
      --accent: #5cadff;
      --accent-dim: #3d7ab8;
      --ok: #3ddc84;
      --err: #ff6b6b;
      --led-on: #2ea043;
      --led-off: #c93c37;
      --ring: rgba(92, 173, 255, 0.35);
    }}
    * {{ box-sizing: border-box; }}
    html, body {{ height: 100%; margin: 0; }}
    body {{
      font-family: "Segoe UI", ui-sans-serif, system-ui, sans-serif;
      background: radial-gradient(1200px 600px at 10% -10%, #1a2840 0%, var(--bg) 45%);
      color: var(--text);
      display: flex;
      flex-direction: column;
    }}
    .app-header {{
      flex: 0 0 auto;
      padding: 14px 16px 12px;
      background: linear-gradient(180deg, var(--panel2) 0%, var(--panel) 100%);
      border-bottom: 1px solid #2a3548;
      box-shadow: 0 8px 32px rgba(0,0,0,0.35);
    }}
    .app-header__top {{
      display: flex;
      flex-wrap: wrap;
      align-items: flex-start;
      justify-content: space-between;
      gap: 14px;
    }}
    .brand h1 {{
      font-size: 1.25rem;
      font-weight: 700;
      letter-spacing: -0.02em;
      margin: 0 0 6px 0;
      background: linear-gradient(90deg, #e6edf3, #8ec8ff);
      -webkit-background-clip: text;
      background-clip: text;
      color: transparent;
    }}
    .brand .sub {{
      color: var(--muted);
      font-size: 0.78rem;
      line-height: 1.45;
      margin: 0;
      max-width: 52ch;
    }}
    .brand code {{
      font-size: 0.85em;
      padding: 1px 5px;
      border-radius: 4px;
      background: rgba(0,0,0,0.35);
      border: 1px solid #30363d;
    }}
    .conn-wrap {{
      flex: 0 0 auto;
      min-width: 108px;
      padding: 10px 12px;
      text-align: center;
      border-radius: 12px;
      border: 1px solid #30363d;
      background: rgba(0,0,0,0.25);
      transition: border-color 0.2s, box-shadow 0.2s;
    }}
    .conn-wrap.linked {{
      border-color: rgba(61, 220, 132, 0.45);
      box-shadow: 0 0 0 1px rgba(61, 220, 132, 0.12), 0 0 24px rgba(61, 220, 132, 0.15);
    }}
    .conn-wrap.nolink {{
      border-color: rgba(255, 107, 107, 0.35);
      box-shadow: 0 0 0 1px rgba(255, 107, 107, 0.08);
    }}
    .conn-led {{
      width: 26px;
      height: 26px;
      margin: 0 auto 8px;
      border-radius: 50%;
      border: 2px solid rgba(255,255,255,0.2);
      box-shadow:
        inset 0 2px 8px rgba(0,0,0,0.5),
        0 0 16px rgba(0,0,0,0.4);
      transition: background 0.25s, box-shadow 0.25s, transform 0.2s;
    }}
    .conn-wrap.linked .conn-led {{
      transform: scale(1.02);
    }}
    .conn-led.on {{
      background: radial-gradient(circle at 32% 28%, #a8ffc4, var(--led-on) 52%, #0d3d1a);
      box-shadow:
        inset 0 -3px 8px rgba(0,0,0,0.35),
        0 0 20px rgba(46, 160, 67, 0.65);
    }}
    .conn-led.off {{
      background: radial-gradient(circle at 32% 28%, #ffc9c9, var(--led-off) 52%, #3d1010);
      box-shadow:
        inset 0 -3px 8px rgba(0,0,0,0.35),
        0 0 16px rgba(201, 60, 55, 0.45);
    }}
    .conn-label {{
      font-size: 0.65rem;
      line-height: 1.25;
      color: var(--muted);
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.06em;
    }}
    .conn-wrap.linked .conn-label {{ color: var(--ok); }}
    .conn-wrap.nolink .conn-label {{ color: var(--err); }}
    .conn-channel {{
      margin-top: 4px;
      font-size: 0.72rem;
      line-height: 1.25;
      color: #b8c9dc;
      font-weight: 500;
      word-break: break-word;
    }}
    .conn-wrap.nolink .conn-channel {{ color: #d4a08b; }}
    #status {{
      font-size: 0.76rem;
      margin-top: 10px;
      padding: 6px 10px;
      border-radius: 8px;
      background: rgba(0,0,0,0.2);
      border: 1px solid #2a3548;
    }}
    #status.ok {{ color: #9fe8bf; }}
    #status.err {{ color: #ffb4b4; }}
    .app-header__controls {{
      margin-top: 14px;
      display: flex;
      flex-wrap: wrap;
      align-items: stretch;
      gap: 12px;
    }}
    .toolbar-left {{
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      gap: 10px;
      flex: 1 1 auto;
    }}
    #btnCore {{
      cursor: pointer;
      font: inherit;
      font-size: 0.8rem;
      font-weight: 600;
      padding: 8px 14px;
      border-radius: 8px;
      border: 1px solid #3d4f66;
      background: linear-gradient(180deg, #2a3548, #1e2838);
      color: var(--text);
    }}
    #btnCore:hover {{
      border-color: var(--accent);
      color: #cfe8ff;
      box-shadow: 0 0 0 3px var(--ring);
    }}
    #btnCore.active {{
      background: linear-gradient(180deg, #1a3d2e, #132a22);
      border-color: rgba(61, 220, 132, 0.55);
      color: var(--ok);
    }}
    .hint {{ font-size: 0.72rem; color: var(--muted); max-width: 36ch; }}
    .dev-rail-wrap {{
      flex: 1 1 280px;
      margin-left: auto;
      min-width: min(100%, 320px);
    }}
    .dev-rail-label {{
      font-size: 0.62rem;
      text-transform: uppercase;
      letter-spacing: 0.08em;
      color: var(--muted);
      margin-bottom: 6px;
      font-weight: 600;
    }}
    #devBar {{
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      justify-content: flex-end;
    }}
    .dev-btn {{
      display: inline-flex;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      gap: 6px;
      cursor: pointer;
      font: inherit;
      font-size: 0.68rem;
      font-weight: 600;
      padding: 10px 10px 8px;
      min-width: 76px;
      border-radius: 10px;
      border: 1px solid #3d4f66;
      background: linear-gradient(180deg, #222d3f, #1a2332);
      color: #d8e4f5;
      transition: border-color 0.15s, box-shadow 0.15s, transform 0.1s;
    }}
    .dev-btn:hover:not([disabled]) {{
      border-color: var(--accent);
      box-shadow: 0 0 0 3px var(--ring);
      transform: translateY(-1px);
    }}
    .dev-btn:active:not([disabled]) {{ transform: translateY(0); }}
    .dev-btn[disabled] {{ opacity: 0.42; cursor: not-allowed; filter: grayscale(0.3); }}
    .dev-btn .dev-lamp {{
      width: 22px;
      height: 22px;
      border-radius: 50%;
      border: 2px solid rgba(255,255,255,0.2);
      background: #2d3849;
      box-shadow: inset 0 2px 6px rgba(0,0,0,0.55);
    }}
    .dev-btn .dev-lamp.on {{
      background: radial-gradient(circle at 35% 28%, #c8ffd4, #2ea043 55%, #0f3d18);
      box-shadow: 0 0 14px rgba(46, 160, 67, 0.55);
    }}
    .dev-btn .dev-lamp.off {{
      background: radial-gradient(circle at 35% 28%, #ffd0d0, #a83232 55%, #2a0c0c);
      box-shadow: 0 0 10px rgba(248, 81, 73, 0.35);
    }}
    .dev-btn.hidden {{ display: none !important; }}
    #scroll {{
      flex: 1 1 auto;
      overflow: auto;
      padding: 16px 16px 28px;
      max-width: 1120px;
      width: 100%;
      margin: 0 auto;
    }}
    #panels {{ display: flex; flex-direction: column; gap: 14px; }}
    .card {{
      background: linear-gradient(165deg, var(--panel2) 0%, var(--panel) 100%);
      border-radius: 12px;
      padding: 14px 16px;
      border: 1px solid #2a3548;
      box-shadow: 0 4px 24px rgba(0,0,0,0.25);
    }}
    .card h2 {{
      font-size: 0.7rem;
      text-transform: uppercase;
      letter-spacing: 0.12em;
      color: var(--accent);
      margin: 0 0 12px 0;
      font-weight: 700;
    }}
    .card--core h2 {{ margin-bottom: 14px; }}
    .core-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(158px, 1fr));
      gap: 10px;
    }}
    .core-tile {{
      background: rgba(0,0,0,0.28);
      border: 1px solid #2f3d52;
      border-radius: 10px;
      padding: 12px 12px 10px;
      min-height: 76px;
      display: flex;
      flex-direction: column;
      justify-content: center;
      gap: 4px;
    }}
    .core-tile-k {{
      font-size: 0.72rem;
      color: #dbe7f7;
      font-weight: 700;
      line-height: 1.25;
      word-break: break-word;
    }}
    .core-tile-key {{
      font-size: 0.58rem;
      color: var(--muted);
      font-family: ui-monospace, monospace;
      letter-spacing: 0.02em;
      margin-top: 2px;
    }}
    .core-tile-v {{
      font-size: 1.05rem;
      font-weight: 700;
      font-variant-numeric: tabular-nums;
      color: #f6f8fa;
      line-height: 1.2;
      word-break: break-word;
    }}
    table {{ width: 100%; table-layout: fixed; border-collapse: collapse; font-size: 0.8rem; font-variant-numeric: tabular-nums; }}
    td {{ padding: 5px 8px 5px 0; vertical-align: top; border-bottom: 1px solid #252d3a; }}
    tr:last-child td {{ border-bottom: none; }}
    th.n, th.k, th.v {{
      font-size: 0.72rem; font-weight: 600; color: #8ec8ff; text-align: left;
      padding: 6px 8px 8px 0; border-bottom: 1px solid #30363d; vertical-align: bottom;
    }}
    th.n {{ text-align: right; width: 3rem; padding-right: 8px; color: #58a6ff; }}
    th.k {{ width: 34%; }}
    td.n {{ color: #e6edf3; width: 3rem; min-width: 3rem; text-align: right; padding-right: 8px; font-weight: 600; font-variant-numeric: tabular-nums; }}
    td.k {{ color: var(--muted); width: 34%; word-break: break-all; }}
    td.v {{ word-break: break-all; }}
    pre {{
      margin: 0;
      font-size: 0.68rem;
      line-height: 1.45;
      white-space: pre-wrap;
      word-break: break-all;
      max-height: min(38vh, 420px);
      overflow: auto;
      color: #c5d4e6;
    }}
  </style>
</head>
<body>
  <header class="app-header">
    <div class="app-header__top">
      <div class="brand">
        <h1>Тлм Монеткожуй</h1>
        <p class="sub">Каналы: <strong>{mode_line}</strong><br />
          Порт <code>{com}</code> · UI <code>127.0.0.1:{port}</code> · опрос API ~2 с (вкладка на паузе, если окно скрыто)</p>
      </div>
      <div class="conn-wrap nolink" id="connWrap" aria-live="polite">
        <div class="conn-led off" id="connLed" role="img" aria-label="Связь с платой"></div>
        <div class="conn-label" id="connLabel">ожидание</div>
        <div class="conn-channel" id="connChannel"></div>
      </div>
    </div>
    <div class="app-header__controls">
      <div class="toolbar-left">
        <button type="button" id="btnCore" title="10 ключевых полей плитками; полный JSON — кнопка ниже">Сводка (10)</button>
        <span class="hint">Полный список полей — отключите «Сводка»</span>
      </div>
      <div class="dev-rail-wrap">
        <div class="dev-rail-label">Периферия · нажми лампу</div>
        <div id="devBar" aria-label="Управление по Wi‑Fi на плату">
          <button type="button" class="dev-btn" data-ctrl="wifi" id="devWifi" title="Радио Wi‑Fi: вкл — без эко‑сна, выкл — энергосбережение. STA не отключается.">
            <span class="dev-lamp on" aria-hidden="true"></span><span>Wi‑Fi</span>
          </button>
          <button type="button" class="dev-btn" data-ctrl="ble" id="devBle" title="Bluetooth LE — реклама">
            <span class="dev-lamp on" aria-hidden="true"></span><span>BLE</span>
          </button>
          <button type="button" class="dev-btn" data-ctrl="cam" id="devCam" title="Камера: /stream и /capture">
            <span class="dev-lamp on" aria-hidden="true"></span><span>Камера</span>
          </button>
          <button type="button" class="dev-btn" data-ctrl="mic" id="devMic" title="Микрофон TCP :81 и уровень">
            <span class="dev-lamp on" aria-hidden="true"></span><span>Микрофон</span>
          </button>
        </div>
      </div>
    </div>
    <div id="status" class="ok">ожидание…</div>
  </header>
  <div id="scroll">
    <p class="hint" style="margin:0 0 12px 0;">Сессия UI <code>{ui_session_rev}</code> · mtime скрипта <code>{script_mtime}</code>
      — проверка: <a href="/api/ui-meta" target="_blank" rel="noopener">/api/ui-meta</a> (путь к <code>.py</code> должен совпадать с консолью).
      Chrome кэширует только <code>/</code>; интерфейс открывайте как <code>/live</code> (корень редиректит сюда).</p>
    <div id="usbWaitBanner" class="card" style="display:none;margin-bottom:12px;border-color:#f85149;">
      <h2 style="color:#f85149">Нет строки JSON с платы</h2>
      <pre id="usbWaitTxt" style="max-height:220px"></pre>
    </div>
    <div id="robotCard" class="card" style="margin-bottom:12px">
      <h2>Робот · UNO-стек на XIAO</h2>
      <p class="hint" style="margin:0 0 10px">Джойстик и скан идут на плату по Wi‑Fi (<code>/board/…</code>). VL53L7CX (мультизонный): SDA GPIO8, SCL GPIO9.</p>
      <div style="display:flex;flex-wrap:wrap;gap:16px;align-items:flex-start">
        <div>
          <canvas id="tof3d" width="340" height="280" style="background:#0d1117;border-radius:8px;border:1px solid #30363d;touch-action:none;cursor:grab"></canvas>
          <div id="radarDist" style="text-align:center;font-size:0.85rem;color:#8b9cb3;margin-top:6px">—</div>
          <div style="font-size:0.75rem;color:#8b9cb3;margin-top:4px">Профиль: <span id="tofProfile">—</span> · сетка <span id="tofRes">—</span> · замеров: <span id="tofCount">0</span></div>
          <div style="margin-top:6px;display:flex;gap:8px;align-items:center;flex-wrap:wrap">
            <button type="button" id="btnFloorCal">Калибровать пол</button>
            <span id="floorInfo" style="font-size:0.75rem;color:#8b9cb3">пол: не калиброван</span>
          </div>
          <div style="font-size:0.7rem;color:#6b7785;margin-top:2px">3D-глубина · тяни мышью · ближе=красный, дальше=синий · <span style="color:#ff2fd0">обрыв</span> · <span style="color:#ff9a3d">бугор</span>. Калибровка: поставь робота на ровный пол, путь свободен.</div>
        </div>
        <div>
          <div id="joy" style="width:168px;height:168px;border-radius:50%;background:#1c2738;border:2px solid #30363d;position:relative;touch-action:none">
            <div style="position:absolute;left:50%;top:6px;transform:translateX(-50%);color:#8b9cb3;font-size:14px;line-height:1;pointer-events:none;user-select:none">&#9650;</div>
            <div style="position:absolute;left:50%;bottom:6px;transform:translateX(-50%);color:#8b9cb3;font-size:14px;line-height:1;pointer-events:none;user-select:none">&#9660;</div>
            <div style="position:absolute;left:8px;top:50%;transform:translateY(-50%);color:#8b9cb3;font-size:14px;line-height:1;pointer-events:none;user-select:none">&#9664;</div>
            <div style="position:absolute;right:8px;top:50%;transform:translateY(-50%);color:#8b9cb3;font-size:14px;line-height:1;pointer-events:none;user-select:none">&#9654;</div>
            <div id="knob" style="width:44px;height:44px;border-radius:50%;background:#5cadff;position:absolute;left:62px;top:62px;box-shadow:0 0 12px rgba(92,173,255,0.5)"></div>
          </div>
          <div id="joyVal" style="text-align:center;font-size:0.75rem;color:#8b9cb3;margin-top:6px">L 0 &middot; R 0</div>
          <div style="margin-top:10px;width:168px">
            <div style="font-size:0.72rem;color:#8b9cb3">Мощность: <span id="maxSpdVal">180</span>/255</div>
            <input type="range" id="maxSpd" min="40" max="255" value="180" style="width:100%">
            <div style="font-size:0.72rem;color:#8b9cb3;margin-top:4px">Пение (скважность): <span id="audGainVal">10</span>%</div>
            <input type="range" id="audGain" min="10" max="100" value="10" style="width:100%">
          </div>
        </div>
        <div>
          <canvas id="mapGrid" width="320" height="320" style="image-rendering:pixelated;background:#0d1117;border-radius:8px;border:1px solid #30363d"></canvas>
          <div style="margin-top:8px;display:flex;gap:8px;flex-wrap:wrap">
            <button type="button" id="btnScan360">Скан 360°</button>
            <button type="button" id="btnMapClear">Очистить карту</button>
          </div>
          <div id="mapStatus" style="font-size:0.75rem;color:#8b9cb3;margin-top:6px">Карта</div>
        </div>
      </div>
      <div style="margin-top:10px;display:flex;gap:8px;flex-wrap:wrap">
        <button type="button" id="btnRobotStop">Стоп</button>
        <button type="button" id="btnBeep">Beep A</button>
        <button type="button" id="btnMel1">Мелодия 1</button>
        <button type="button" id="btnSayPrivet">Привет</button>
        <button type="button" id="btnMelStop">Стоп звук</button>
      </div>
    </div>
    <div id="cloudCard" class="card" style="margin-bottom:12px">
      <h2>Модель пространства (камера + ToF, авто)</h2>
      <p class="hint" style="margin:0 0 8px">Система <b>сама</b> копит модель: фоновый сервис непрерывно вливает кадры камеры+ToF, уверенность вокселей растёт от повторных наблюдений, модель сохраняется на диск и переживает рестарт (<code>spatial/world_*</code>). Поза — best-effort (одометрия по энкодерам когда разведём, ручной yaw для скана).</p>
      <div style="display:flex;flex-wrap:wrap;gap:16px;align-items:flex-start">
        <canvas id="cloud3d" width="420" height="320" style="background:#0d1117;border-radius:8px;border:1px solid #30363d;touch-action:none;cursor:grab"></canvas>
        <div style="min-width:190px">
          <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center">
            <button type="button" id="btnWorldPause">Пауза</button>
            <button type="button" id="btnWorldSave">Сохранить</button>
            <button type="button" id="btnWorldClear">Очистить</button>
            <button type="button" id="btnWorldRecenter" title="Принять текущий курс IMU за 0° (робот смотрит вперёд)">Курс=0</button>
          </div>
          <div style="margin-top:8px;font-size:0.8rem;color:#8b9cb3">ручной yaw, °:
            <input type="number" id="cloudYaw" value="0" step="15" style="width:64px;background:#0d1117;color:#c9d1d9;border:1px solid #30363d;border-radius:4px;padding:2px 4px">
            <button type="button" id="btnWorldYaw">→</button></div>
          <div style="margin-top:6px;font-size:0.8rem;color:#8b9cb3">вокселей: <span id="cloudCount">0</span> · уверенных: <span id="worldConf">0</span></div>
          <div id="cloudStatus" style="margin-top:4px;font-size:0.75rem;color:#8b9cb3">—</div>
          <div id="worldPose" style="margin-top:2px;font-size:0.72rem;color:#6b7785">поза: —</div>
          <div style="margin-top:8px"><a id="cloudPly" href="/world/ply" download="world.ply" style="font-size:0.8rem;color:#58a6ff">Скачать PLY</a></div>
          <div style="margin-top:6px;font-size:0.7rem;color:#6b7785">тяни мышью — повернуть · колесо — масштаб · ярче = увереннее · обновляется само</div>
        </div>
        <div>
          <div style="font-size:0.75rem;color:#8b9cb3;margin-bottom:4px">вид сверху (карта для ориентации)</div>
          <canvas id="worldTop" width="220" height="220" style="background:#0d1117;border-radius:8px;border:1px solid #30363d"></canvas>
          <div style="font-size:0.7rem;color:#6b7785;margin-top:4px">робот ▲ в центре смотрит вверх · занято = ярче · сетка 0.5 м</div>
        </div>
      </div>
    </div>
    <div id="panels"></div>
    <div id="rawCard" class="card" style="margin-top:12px">
      <h2>Полный JSON</h2>
      <pre id="raw"></pre>
    </div>
  </div>
  <script>
    const CORE_KEYS = {core_keys_js};
    const CORE_TITLES = {core_titles_js};
    const CORE_HINTS = {core_hints_js};
    const elPanels = document.querySelector("#panels");
    const elRaw = document.querySelector("#raw");
    const elRawCard = document.querySelector("#rawCard");
    const elSt = document.querySelector("#status");
    const elBtnCore = document.querySelector("#btnCore");
    const elDevBar = document.querySelector("#devBar");
    const elConnWrap = document.querySelector("#connWrap");
    const elConnLed = document.querySelector("#connLed");
    const elConnLabel = document.querySelector("#connLabel");
    const elConnChannel = document.querySelector("#connChannel");
    let coreOnly = true;

    function setConnState(linked, channelRu) {{
      if (!elConnWrap || !elConnLed || !elConnLabel) return;
      elConnWrap.classList.toggle("linked", linked);
      elConnWrap.classList.toggle("nolink", !linked);
      elConnLed.classList.toggle("on", linked);
      elConnLed.classList.toggle("off", !linked);
      elConnLabel.textContent = linked ? "есть связь" : "нет связи";
      if (elConnChannel) {{
        elConnChannel.textContent = linked
          ? (channelRu || "канал неизвестен")
          : ((channelRu || "").trim() || "нет данных с платы · Wi‑Fi / USB / BLE");
      }}
    }}

    const GROUP_ORDER = [
      "Прокси", "Система и MCU", "Память", "Flash и OTA", "{WIFI_GROUP_LABEL}",
      "Управление", "Bluetooth LE", "Точка доступа (AP)", "Камера", "Микрофон", "Датчики",
      "USB / диагностика", "Прочее"
    ];
    const GROUP_ORDER_SET = new Set(GROUP_ORDER);

    function groupOf(k) {{
      if (k === "proxy_error" || k === "hint" || k === "camera_ip_txt" || k === "proxy_hint") return "Прокси";
      if (k.startsWith("ctrl_")) return "Управление";
      if (k === "chip_temp_c") return "Датчики";
      if (k.startsWith("ble_")) return "Bluetooth LE";
      if (k.startsWith("mic_")) return "Микрофон";
      if (k.startsWith("cam_")) return "Камера";
      if (k.startsWith("ap_")) return "Точка доступа (AP)";
      if (k.startsWith("tof_")) return "Датчики";
      if (k.startsWith("drive_") || k === "enc_l" || k === "enc_r") return "Привод";
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

    function escAttr(s) {{
      return String(s).replace(/&/g,"&amp;").replace(/"/g,"&quot;").replace(/</g,"&lt;");
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
      const rows = [];
      CORE_KEYS.forEach(function (k) {{
        if (Object.prototype.hasOwnProperty.call(obj, k)) {{
          rows.push({{ k: k, v: fmtVal(obj[k]) }});
        }}
      }});
      let html = '<section class="card card--core"><h2>Сводка — 10 основных показателей</h2><div class="core-grid">';
      rows.forEach(function (r) {{
        const title = (CORE_TITLES && CORE_TITLES[r.k]) ? CORE_TITLES[r.k] : r.k;
        const hint = (CORE_HINTS && CORE_HINTS[r.k]) ? CORE_HINTS[r.k] : "";
        html += '<div class="core-tile" title="' + escAttr(hint) + '"><div class="core-tile-k">' + esc(title) +
          '</div><div class="core-tile-key">' + esc(r.k) + '</div><div class="core-tile-v">' + esc(r.v) + "</div></div>";
      }});
      html += "</div></section>";
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

    function tofIsValid(j) {{
      if (j.tof_valid === 0 || j.tof_valid === false) return false;
      const mm = j.tof_mm;
      return mm != null && mm >= 20 && mm < 8190;
    }}

    // ===== 3D-вид глубины: матрица зон VL53L7CX как поверхность, что видит сенсор =====
    // Сенсор в начале координат, смотрит вперёд +Z, вверх +Y. FoV ~64° на ось (90° по диагонали).
    const TOF3D = {{ fovDeg: 64, maxMm: 2000, yaw: 0.7, pitch: 0.42, autoYaw: 0.0045,
                     drag: false, lastX: 0, lastY: 0, lastInput: 0 }};
    let tofGrid = null, tofSide = 0, tofCls = null;

    function tofColor(mm, alpha) {{
      const t = Math.max(0, Math.min(1, (mm - 150) / (TOF3D.maxMm - 150)));
      const hue = t * 215;  // ближе=красный(0), дальше=синий(215)
      return "hsla(" + hue.toFixed(0) + ",85%,55%," + (alpha == null ? 1 : alpha) + ")";
    }}
    function tofProject(p) {{
      const pivotZ = 1000;
      const X = p.x, Y = p.y, Z = p.z - pivotZ;
      const cy = Math.cos(TOF3D.yaw), sy = Math.sin(TOF3D.yaw);
      const x1 = X * cy + Z * sy, z1 = -X * sy + Z * cy, y1 = Y;
      const cp = Math.cos(TOF3D.pitch), sp = Math.sin(TOF3D.pitch);
      const y2 = y1 * cp - z1 * sp, z2 = y1 * sp + z1 * cp, x2 = x1;
      const D = 2700, f = 360, depth = z2 + D;
      if (depth < 1) return null;
      return {{ sx: x2 * f / depth, sy: -y2 * f / depth, depth: depth }};
    }}
    function tofZonePoint(r, c, side, mm) {{
      const half = (TOF3D.fovDeg / 2) * Math.PI / 180;
      const az = (side < 2) ? 0 : ((c / (side - 1)) - 0.5) * 2 * half;
      const el = (side < 2) ? 0 : (0.5 - (r / (side - 1))) * 2 * half;
      const ce = Math.cos(el);
      return {{ x: mm * ce * Math.sin(az), y: mm * Math.sin(el), z: mm * ce * Math.cos(az) }};
    }}
    function drawTof3d() {{
      const canvas = document.getElementById("tof3d");
      if (!canvas) return;
      const ctx = canvas.getContext("2d");
      const w = canvas.width, h = canvas.height, cx = w / 2, cy = h / 2;
      ctx.fillStyle = "#0d1117"; ctx.fillRect(0, 0, w, h);
      const half = (TOF3D.fovDeg / 2) * Math.PI / 180;
      // опорные рамки глубины 0.5/1/1.5/2 м + лучи FoV из сенсора
      const corner = (dm, sx, sy) => {{ const ce = Math.cos(sy * half);
        return tofProject({{ x: dm * ce * Math.sin(sx * half), y: dm * Math.sin(sy * half), z: dm * ce * Math.cos(sx * half) }}); }};
      ctx.lineWidth = 1; ctx.strokeStyle = "rgba(120,140,160,0.18)";
      const ring = {{}};
      [500, 1000, 1500, 2000].forEach(dm => {{
        const cs = [corner(dm,-1,1), corner(dm,1,1), corner(dm,1,-1), corner(dm,-1,-1)];
        if (cs.some(p => !p)) return; ring[dm] = cs;
        ctx.beginPath(); cs.forEach((p,i) => {{ const X=cx+p.sx, Y=cy+p.sy; i?ctx.lineTo(X,Y):ctx.moveTo(X,Y); }});
        ctx.closePath(); ctx.stroke();
      }});
      const o = tofProject({{ x:0, y:0, z:0 }}), far = ring[2000];
      if (o && far) {{ ctx.strokeStyle = "rgba(120,140,160,0.13)"; far.forEach(p => {{
        ctx.beginPath(); ctx.moveTo(cx+o.sx, cy+o.sy); ctx.lineTo(cx+p.sx, cy+p.sy); ctx.stroke(); }}); }}
      if (o) {{ ctx.fillStyle = "#8b9cb3"; ctx.beginPath(); ctx.arc(cx+o.sx, cy+o.sy, 3, 0, Math.PI*2); ctx.fill(); }}

      if (!tofGrid || tofSide < 2) {{
        ctx.fillStyle = "#6b7785"; ctx.font = "12px sans-serif"; ctx.textAlign = "center";
        ctx.fillText("ожидание данных ToF…", cx, cy); ctx.textAlign = "start"; return;
      }}
      const side = tofSide;
      const proj = [];
      for (let r=0; r<side; r++) {{ proj.push([]);
        for (let c=0; c<side; c++) {{
          const mm = tofGrid[r*side+c];
          if (mm == null || mm < 20) {{ proj[r].push(null); continue; }}
          const pr = tofProject(tofZonePoint(r, c, side, Math.min(mm, TOF3D.maxMm*1.25)));
          const cls = (tofCls && tofCls.length === side*side) ? tofCls[r*side+c] : 0;
          proj[r].push(pr ? {{ sx: pr.sx, sy: pr.sy, depth: pr.depth, mm: mm, cls: cls }} : null);
        }}
      }}
      // меш-квадраты, back→front
      const quads = [];
      for (let r=0; r<side-1; r++) for (let c=0; c<side-1; c++) {{
        const a=proj[r][c], b=proj[r][c+1], d=proj[r+1][c+1], e=proj[r+1][c];
        if (!a||!b||!d||!e) continue;
        quads.push({{ a,b,d,e, mm:(a.mm+b.mm+d.mm+e.mm)/4, depth:(a.depth+b.depth+d.depth+e.depth)/4 }});
      }}
      quads.sort((p,q) => q.depth - p.depth);
      quads.forEach(q => {{
        ctx.beginPath(); ctx.moveTo(cx+q.a.sx, cy+q.a.sy); ctx.lineTo(cx+q.b.sx, cy+q.b.sy);
        ctx.lineTo(cx+q.d.sx, cy+q.d.sy); ctx.lineTo(cx+q.e.sx, cy+q.e.sy); ctx.closePath();
        ctx.fillStyle = tofColor(q.mm, 0.5); ctx.fill();
        ctx.strokeStyle = "rgba(255,255,255,0.10)"; ctx.lineWidth = 1; ctx.stroke();
      }});
      // точки зон поверх; обрыв=малиновый, бугор=оранжевый, иначе по дистанции
      for (let r=0; r<side; r++) for (let c=0; c<side; c++) {{
        const p = proj[r][c]; if (!p) continue;
        const special = (p.cls === 1 || p.cls === 2);
        const rad = special ? Math.max(4, 9 - (p.depth - 2200) / 500)
                            : Math.max(2.5, 7 - (p.depth - 2200) / 500);
        ctx.beginPath(); ctx.arc(cx+p.sx, cy+p.sy, rad, 0, Math.PI*2);
        ctx.fillStyle = p.cls === 1 ? "#ff2fd0" : p.cls === 2 ? "#ff9a3d" : tofColor(p.mm, 1);
        ctx.fill();
        if (special) {{ ctx.lineWidth = 1.5; ctx.strokeStyle = "#ffffff"; ctx.stroke(); }}
      }}
    }}
    function tof3dLoop() {{
      if (!TOF3D.drag && (Date.now() - TOF3D.lastInput > 3500)) TOF3D.yaw += TOF3D.autoYaw;
      drawTof3d();
      requestAnimationFrame(tof3dLoop);
    }}
    function tof3dBindInput() {{
      const cv = document.getElementById("tof3d"); if (!cv) return;
      cv.addEventListener("pointerdown", e => {{ TOF3D.drag = true; TOF3D.lastX = e.clientX; TOF3D.lastY = e.clientY; cv.setPointerCapture(e.pointerId); }});
      cv.addEventListener("pointermove", e => {{ if (!TOF3D.drag) return;
        TOF3D.yaw += (e.clientX - TOF3D.lastX) * 0.01;
        TOF3D.pitch = Math.max(-0.2, Math.min(1.3, TOF3D.pitch + (e.clientY - TOF3D.lastY) * 0.008));
        TOF3D.lastX = e.clientX; TOF3D.lastY = e.clientY; TOF3D.lastInput = Date.now(); }});
      const end = () => {{ TOF3D.drag = false; TOF3D.lastInput = Date.now(); }};
      cv.addEventListener("pointerup", end); cv.addEventListener("pointercancel", end);
    }}
    async function tofPoll() {{
      try {{ const g = await boardFetch("tof", 2000); if (g && g.grid && g.res) {{ tofGrid = g.grid; tofSide = g.res; tofCls = g.cls || null; }} }} catch (e) {{}}
      setTimeout(tofPoll, 220);
    }}
    function updateRobotTof(j) {{
      const elP = document.getElementById("tofProfile");
      const elC = document.getElementById("tofCount");
      const elR = document.getElementById("tofRes");
      const elD = document.getElementById("radarDist");
      if (elP) elP.textContent = (j.tof_profile || "—") + (j.tof_auto ? " · авто" : "");
      if (elC) elC.textContent = j.tof_count ?? 0;
      if (elR) elR.textContent = j.tof_res ? (j.tof_res + "×" + j.tof_res) : "—";
      if (elD) elD.textContent = tofIsValid(j) ? ("ближайшее: " + j.tof_mm + " мм") : "нет цели";
      const elF = document.getElementById("floorInfo");
      if (elF) {{
        if (j.tof_floor_cal) elF.textContent = "пол: откалиброван · обрывов " + (j.tof_cliff ?? 0) + " · бугров " + (j.tof_bump ?? 0);
        else elF.textContent = "пол: не калиброван";
      }}
    }}
    if (document.getElementById("tof3d")) {{ tof3dBindInput(); requestAnimationFrame(tof3dLoop); tofPoll(); }}
    const MAP_W = 80, MAP_H = 80, CELL_MM = 50, ROBOT_CX = 40, ROBOT_CY = 40;
    const mapLog = new Float32Array(MAP_W * MAP_H);
    const mapCanvas = document.getElementById("mapGrid");
    const mapCtx = mapCanvas ? mapCanvas.getContext("2d") : null;
    const mapImg = mapCanvas ? mapCtx.createImageData(MAP_W, MAP_H) : null;
    function mapIdx(x, y) {{ return (x < 0 || y < 0 || x >= MAP_W || y >= MAP_H) ? -1 : y * MAP_W + x; }}
    function mapAddLog(x, y, d) {{ const i = mapIdx(x, y); if (i >= 0) mapLog[i] = Math.max(-4, Math.min(4, mapLog[i] + d)); }}
    function mapRay(ang, mm, valid) {{
      const rad = (ang - 90) * Math.PI / 180, cells = Math.min(mm / CELL_MM, 38);
      const x1 = Math.round(ROBOT_CX + Math.cos(rad) * cells), y1 = Math.round(ROBOT_CY + Math.sin(rad) * cells);
      const pts = []; let x0 = ROBOT_CX, y0 = ROBOT_CY;
      const dx = Math.abs(x1 - x0), dy = -Math.abs(y1 - y0), sx = x0 < x1 ? 1 : -1, sy = y0 < y1 ? 1 : -1;
      let err = dx + dy;
      for (;;) {{ pts.push([x0, y0]); if (x0 === x1 && y0 === y1) break;
        const e2 = 2 * err; if (e2 >= dy) {{ err += dy; x0 += sx; }} if (e2 <= dx) {{ err += dx; y0 += sy; }} }}
      for (let i = 0; i < pts.length - 1; i++) mapAddLog(pts[i][0], pts[i][1], -0.45);
      if (valid && pts.length) mapAddLog(pts[pts.length - 1][0], pts[pts.length - 1][1], 0.85);
    }}
    function drawMapGrid() {{
      if (!mapCanvas || !mapCtx || !mapImg) return;
      const d = mapImg.data;
      for (let i = 0; i < mapLog.length; i++) {{
        const v = mapLog[i]; let r, g, b;
        if (v > 0.35) {{ r = 230; g = 230; b = 230; }} else if (v < -0.35) {{ r = 40; g = 200; b = 120; }} else {{ r = 35; g = 38; b = 42; }}
        const p = i * 4; d[p] = r; d[p+1] = g; d[p+2] = b; d[p+3] = 255;
      }}
      const off = document.createElement("canvas"); off.width = MAP_W; off.height = MAP_H;
      off.getContext("2d").putImageData(mapImg, 0, 0);
      mapCtx.imageSmoothingEnabled = false; mapCtx.clearRect(0, 0, mapCanvas.width, mapCanvas.height);
      mapCtx.drawImage(off, 0, 0, mapCanvas.width, mapCanvas.height);
      const sc = mapCanvas.width / MAP_W;
      mapCtx.fillStyle = "#0e639c"; mapCtx.beginPath();
      mapCtx.arc(ROBOT_CX * sc, ROBOT_CY * sc, 5, 0, Math.PI * 2); mapCtx.fill();
    }}
    async function boardFetch(path, timeoutMs) {{
      const ac = new AbortController();
      const to = setTimeout(() => ac.abort(), timeoutMs || 8000);
      try {{
        const r = await fetch("/board/" + path + "&_ts=" + Date.now(), {{ cache: "no-store", signal: ac.signal }});
        const t = await r.text();
        if (!r.ok) throw new Error("HTTP " + r.status + " " + t.slice(0, 120));
        return JSON.parse(t);
      }} finally {{ clearTimeout(to); }}
    }}
    async function boardDrive(l, r) {{
      await boardFetch("drive?l=" + l + "&r=" + r + "&q=1", 3000);
    }}
    const joy = document.getElementById("joy"), knob = document.getElementById("knob");
    const joyVal = document.getElementById("joyVal");
    // Регуляторы (как на UNO-стенде): мощность моторов и скважность «пения».
    const maxSpdEl = document.getElementById("maxSpd"), maxSpdValEl = document.getElementById("maxSpdVal");
    const audGainEl = document.getElementById("audGain"), audGainValEl = document.getElementById("audGainVal");
    function sliderInit(el, lbl, key, defv) {{
      if (!el) return;
      el.value = localStorage.getItem(key) || defv;
      if (lbl) lbl.textContent = el.value;
      el.addEventListener("input", () => {{ if (lbl) lbl.textContent = el.value; localStorage.setItem(key, el.value); }});
    }}
    sliderInit(maxSpdEl, maxSpdValEl, "joy_max_spd", "180");
    sliderInit(audGainEl, audGainValEl, "audio_gain", "10");
    function maxSpdV() {{ return maxSpdEl ? (parseInt(maxSpdEl.value, 10) || 180) : 180; }}
    function gainV() {{ return audGainEl ? (parseInt(audGainEl.value, 10) || 10) : 10; }}
    let dragging = false, joyL = 0, joyR = 0, driveTimer = null;
    function joyValShow() {{ if (joyVal) joyVal.textContent = "L " + joyL + " · R " + joyR; }}
    function centerKnob() {{ if (!knob) return; knob.style.left = "62px"; knob.style.top = "62px"; joyL = 0; joyR = 0; joyValShow(); }}
    if (joy && knob) {{
      const R = 62;
      joy.addEventListener("pointerdown", (e) => {{
        dragging = true; e.preventDefault();
        if (driveTimer) clearInterval(driveTimer);
        driveTimer = setInterval(() => {{ if (dragging) boardDrive(joyL, joyR).catch(() => {{}}); }}, 180);
      }});
      window.addEventListener("pointermove", (e) => {{
        if (!dragging) return;
        const rect = joy.getBoundingClientRect(), cx = rect.left + rect.width/2, cy = rect.top + rect.height/2;
        let dx = e.clientX - cx, dy = e.clientY - cy;
        const d = Math.hypot(dx, dy); if (d > R) {{ dx *= R/d; dy *= R/d; }}
        knob.style.left = (62 + dx) + "px"; knob.style.top = (62 + dy) + "px";
        const y = -dy / R, x = dx / R, sp = maxSpdV();
        joyL = Math.round(sp * Math.max(-1, Math.min(1, y + x)));
        joyR = Math.round(sp * Math.max(-1, Math.min(1, y - x)));
        joyValShow();
      }});
      window.addEventListener("pointerup", () => {{
        if (!dragging) return; dragging = false;
        if (driveTimer) clearInterval(driveTimer);
        centerKnob(); boardFetch("drive?stop=1", 3000).catch(() => {{}});
      }});
    }}
    const btnScan = document.getElementById("btnScan360");
    if (btnScan) btnScan.onclick = async () => {{
      btnScan.disabled = true;
      document.getElementById("mapStatus").textContent = "Скан… ~30–60 с";
      try {{
        const j = await boardFetch("scan360?steps=30", 130000);
        if (!j.ok) throw new Error(j.error || "scan");
        (j.points || []).forEach(p => {{ if (p.valid) mapRay(p.ang, p.mm, true); }});
        drawMapGrid();
        document.getElementById("mapStatus").textContent = "Скан: " + (j.points||[]).length + " лучей";
      }} catch (e) {{
        document.getElementById("mapStatus").textContent = "Ошибка: " + e;
      }} finally {{ btnScan.disabled = false; }}
    }};
    const btnMapClr = document.getElementById("btnMapClear");
    if (btnMapClr) btnMapClr.onclick = () => {{ mapLog.fill(0); drawMapGrid(); }};
    const btnFloor = document.getElementById("btnFloorCal");
    if (btnFloor) btnFloor.onclick = async () => {{
      btnFloor.disabled = true;
      const elF = document.getElementById("floorInfo");
      if (elF) elF.textContent = "пол: калибрую…";
      try {{
        const r = await boardFetch("floorcal", 4000);
        if (elF) elF.textContent = (r && r.ok) ? ("пол: откалиброван (" + r.zones + " зон)") : "пол: калибровка не удалась (нет валидных зон)";
      }} catch (e) {{ if (elF) elF.textContent = "пол: ошибка " + e; }}
      finally {{ btnFloor.disabled = false; }}
    }};
    drawMapGrid();
    document.getElementById("btnRobotStop")?.addEventListener("click", () => boardFetch("drive?stop=1", 3000));
    document.getElementById("btnBeep")?.addEventListener("click", () => boardFetch("beep?hz=880&ms=250&ch=A&gain=" + gainV(), 5000));
    document.getElementById("btnMel1")?.addEventListener("click", () => boardFetch("melody?id=1&ch=A&gain=" + gainV(), 5000));
    document.getElementById("btnSayPrivet")?.addEventListener("click", () => boardFetch("melody?id=9&ch=A&gain=" + gainV(), 15000));
    document.getElementById("btnMelStop")?.addEventListener("click", () => boardFetch("melody?id=0", 3000));

    // ===== 3D-визуализатор АВТО-модели пространства (камера+ToF, /world) =====
    (function() {{
      const cv = document.getElementById("cloud3d");
      if (!cv) return;
      const ctx = cv.getContext("2d");
      let pts = [];
      let yaw = 0.7, pitch = 0.35, zoom = 1.0;
      let cxw = 0, cyw = 0, czw = 0, scl = 120;
      let drag = false, lx = 0, ly = 0;
      const statusEl = document.getElementById("cloudStatus");
      const countEl = document.getElementById("cloudCount");
      const confEl = document.getElementById("worldConf");
      const poseEl = document.getElementById("worldPose");
      const bPause = document.getElementById("btnWorldPause");
      let paused = false;
      function autofit() {{
        if (!pts.length) return;
        let mnx=1e9,mxx=-1e9,mny=1e9,mxy=-1e9,mnz=1e9,mxz=-1e9;
        for (const p of pts) {{ mnx=Math.min(mnx,p[0]);mxx=Math.max(mxx,p[0]);mny=Math.min(mny,p[1]);mxy=Math.max(mxy,p[1]);mnz=Math.min(mnz,p[2]);mxz=Math.max(mxz,p[2]); }}
        cxw=(mnx+mxx)/2; cyw=(mny+mxy)/2; czw=(mnz+mxz)/2;
        const span = Math.max(0.3, mxx-mnx, mxy-mny, mxz-mnz);
        scl = (Math.min(cv.width, cv.height) * 0.42) / span;
      }}
      function draw() {{
        ctx.fillStyle = "#0d1117"; ctx.fillRect(0,0,cv.width,cv.height);
        const cx = cv.width/2, cy = cv.height/2;
        if (!pts.length) {{ ctx.fillStyle="#6b7785"; ctx.font="13px sans-serif"; ctx.fillText("модель пуста — система копит по мере наблюдений", 14, cy); return; }}
        const cyaw=Math.cos(yaw), syaw=Math.sin(yaw), cpit=Math.cos(pitch), spit=Math.sin(pitch);
        const s = scl*zoom;
        function P(x, y, z) {{
          const x1 = cyaw*x + syaw*z, z1 = -syaw*x + cyaw*z, y1 = y;
          const y2 = cpit*y1 - spit*z1, z2 = spit*y1 + cpit*z1;
          const depth = z2 + 6.0;
          if (depth <= 0.1) return null;
          return [cx + x1*s, cy - y2*s, depth];
        }}
        // сетка пола (опорная плоскость на уровне нижних точек) — чтобы читалось пространство
        let mny=1e9; for (const p of pts) mny=Math.min(mny,p[1]);
        const floor = mny - cyw, half = 1.5;
        ctx.strokeStyle="rgba(120,140,170,0.16)"; ctx.lineWidth=1;
        for (let g=-half; g<=half+1e-6; g+=0.5) {{
          const a=P(g,floor,-half), b=P(g,floor,half);
          if (a&&b) {{ ctx.beginPath(); ctx.moveTo(a[0],a[1]); ctx.lineTo(b[0],b[1]); ctx.stroke(); }}
          const c2=P(-half,floor,g), d=P(half,floor,g);
          if (c2&&d) {{ ctx.beginPath(); ctx.moveTo(c2[0],c2[1]); ctx.lineTo(d[0],d[1]); ctx.stroke(); }}
        }}
        // точки с затенением по уверенности (log-odds в p[6])
        const proj = [];
        for (const p of pts) {{
          const pr = P(p[0]-cxw, p[1]-cyw, p[2]-czw);
          if (!pr) continue;
          proj.push([pr[0], pr[1], pr[2], p[3], p[4], p[5], (p.length>6?p[6]:2.0)]);
        }}
        proj.sort((a,b)=>b[2]-a[2]);
        for (const q of proj) {{
          const conf = Math.max(0, Math.min(1, (q[6]-0.85)/3.0));
          ctx.globalAlpha = 0.4 + 0.6*conf;
          const rad = Math.max(1.2, (2.3 + 1.5*conf) - (q[2]-6)/3);
          ctx.beginPath(); ctx.arc(q[0], q[1], rad, 0, Math.PI*2);
          ctx.fillStyle = "rgb("+q[3]+","+q[4]+","+q[5]+")"; ctx.fill();
        }}
        ctx.globalAlpha = 1;
        // маркер робота в начале координат (0,0,0) + направление вперёд (+z)
        const o = P(-cxw, -cyw, -czw), fwd = P(-cxw, -cyw, 0.4-czw);
        if (o) {{
          if (fwd) {{ ctx.strokeStyle="#5cadff"; ctx.lineWidth=2; ctx.beginPath(); ctx.moveTo(o[0],o[1]); ctx.lineTo(fwd[0],fwd[1]); ctx.stroke(); }}
          ctx.fillStyle="#0e639c"; ctx.beginPath(); ctx.arc(o[0],o[1],5,0,Math.PI*2); ctx.fill();
        }}
      }}
      async function pollData() {{
        try {{ const r = await fetch("/world/data?_ts="+Date.now(), {{cache:"no-store"}}); const j = await r.json();
          if (j.ok) {{ const had=pts.length; pts = j.points || []; if (!had && pts.length) autofit(); draw(); }} }} catch(e) {{}}
      }}
      async function pollStatus() {{
        try {{ const r = await fetch("/world/status?_ts="+Date.now(), {{cache:"no-store"}}); const j = await r.json();
          if (!j.ok) return;
          if (countEl) countEl.textContent = j.voxels ?? 0;
          if (confEl) confEl.textContent = j.confident ?? 0;
          paused = !!j.paused;
          if (bPause) bPause.textContent = paused ? "Продолжить" : "Пауза";
          if (statusEl) {{
            const run = j.running ? (paused ? "пауза" : "копит") : "стоп";
            const age = (j.age_s!=null) ? (", кадр "+j.age_s+"с назад") : "";
            const err = j.last_error ? (" · "+j.last_error) : "";
            statusEl.textContent = run + " · кадров " + (j.frames??0) + age + err;
          }}
          if (poseEl && j.pose) {{
            const hd = (j.heading_deg!=null) ? j.heading_deg : Math.round((j.pose[2]||0)*57.3);
            const src = j.have_imu ? " (курс IMU)" : (j.have_odom ? " (одометрия)" : " (без позы)");
            poseEl.textContent = "поза: x="+j.pose[0]+" z="+j.pose[1]+" курс="+hd+"°"+src;
          }}
        }} catch(e) {{}}
      }}
      cv.addEventListener("pointerdown", e => {{ drag=true; lx=e.clientX; ly=e.clientY; try {{ cv.setPointerCapture(e.pointerId); }} catch(_){{}} }});
      cv.addEventListener("pointermove", e => {{ if(!drag) return; yaw += (e.clientX-lx)*0.01; pitch += (e.clientY-ly)*0.01; pitch=Math.max(-1.4,Math.min(1.4,pitch)); lx=e.clientX; ly=e.clientY; draw(); }});
      cv.addEventListener("pointerup", () => {{ drag=false; }});
      cv.addEventListener("wheel", e => {{ e.preventDefault(); zoom *= (e.deltaY<0?1.12:0.89); zoom=Math.max(0.2,Math.min(6,zoom)); draw(); }}, {{passive:false}});
      if (bPause) bPause.onclick = async () => {{ try {{ await fetch("/world/pause?on="+(paused?"0":"1")+"&_ts="+Date.now(), {{cache:"no-store"}}); }} catch(e) {{}} pollStatus(); }};
      const bSave = document.getElementById("btnWorldSave");
      if (bSave) bSave.onclick = async () => {{ if(statusEl) statusEl.textContent="сохраняю…"; try {{ const r=await fetch("/world/save?_ts="+Date.now(), {{cache:"no-store"}}); const j=await r.json(); if(statusEl) statusEl.textContent="сохранено "+(j.saved??0)+" вокселей"; }} catch(e) {{ if(statusEl) statusEl.textContent="ошибка сохранения"; }} }};
      const bClr = document.getElementById("btnWorldClear");
      if (bClr) bClr.onclick = async () => {{ if(!confirm("Очистить накопленную модель пространства?")) return; try {{ await fetch("/world/clear?_ts="+Date.now(), {{cache:"no-store"}}); }} catch(e) {{}} pts=[]; if(countEl) countEl.textContent="0"; if(confEl) confEl.textContent="0"; draw(); }};
      const bYaw = document.getElementById("btnWorldYaw");
      if (bYaw) bYaw.onclick = async () => {{ const y=parseFloat(document.getElementById("cloudYaw").value)||0; try {{ await fetch("/world/yaw?deg="+y+"&_ts="+Date.now(), {{cache:"no-store"}}); }} catch(e) {{}} if(statusEl) statusEl.textContent="ручной yaw → "+y+"°"; }};
      const bRc = document.getElementById("btnWorldRecenter");
      if (bRc) bRc.onclick = async () => {{ try {{ const r=await fetch("/world/recenter?_ts="+Date.now(), {{cache:"no-store"}}); const j=await r.json(); if(statusEl) statusEl.textContent = j.have_imu ? ("курс обнулён (был "+(j.was_deg)+"°)") : "курс=0 недоступен (нет IMU)"; }} catch(e) {{}} pollStatus(); }};
      // вид сверху (occupancy) — карта для ориентации: x вправо, z вперёд (вверх)
      const top = document.getElementById("worldTop");
      const tctx = top ? top.getContext("2d") : null;
      function drawTop(cells, cellM) {{
        if (!tctx) return;
        const W=top.width, H=top.height, cx=W/2, cy=H/2, range=3.0, ppm=(Math.min(W,H)/2)/range;
        tctx.fillStyle="#0d1117"; tctx.fillRect(0,0,W,H);
        tctx.strokeStyle="rgba(120,140,170,0.15)"; tctx.lineWidth=1;
        for (let g=-range; g<=range+1e-6; g+=0.5) {{
          const sx=cx+g*ppm, sy=cy+g*ppm;
          tctx.beginPath(); tctx.moveTo(sx,0); tctx.lineTo(sx,H); tctx.stroke();
          tctx.beginPath(); tctx.moveTo(0,sy); tctx.lineTo(W,sy); tctx.stroke();
        }}
        for (const c of (cells||[])) {{
          const wx=c[0]*cellM, wz=c[1]*cellM, conf=Math.max(0,Math.min(1,(c[2]-0.85)/3.0));
          const sx=cx+wx*ppm, sy=cy-wz*ppm, sz=Math.max(2, cellM*ppm);
          tctx.fillStyle="rgba("+Math.round(90+165*conf)+","+Math.round(120+50*conf)+",70,"+(0.45+0.55*conf)+")";
          tctx.fillRect(sx-sz/2, sy-sz/2, sz, sz);
        }}
        tctx.fillStyle="#5cadff"; tctx.beginPath();
        tctx.moveTo(cx, cy-7); tctx.lineTo(cx-5, cy+5); tctx.lineTo(cx+5, cy+5); tctx.closePath(); tctx.fill();
      }}
      async function pollOccupancy() {{
        try {{ const r=await fetch("/world/occupancy?_ts="+Date.now(), {{cache:"no-store"}}); const j=await r.json();
          if (j.ok) drawTop(j.cells, j.cell_m); }} catch(e) {{}}
      }}
      draw(); pollStatus(); pollData(); pollOccupancy();
      setInterval(function() {{ if (!document.hidden) {{ pollStatus(); pollData(); pollOccupancy(); }} }}, 2500);
    }})();

    function render(obj) {{
      updateRobotTof(obj);
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

    elBtnCore.classList.add("active");
    elBtnCore.textContent = "Сводка (10) ✓";

    elBtnCore.addEventListener("click", function () {{
      coreOnly = !coreOnly;
      elBtnCore.classList.toggle("active", coreOnly);
      elBtnCore.textContent = coreOnly ? "Сводка (10) ✓" : "Все поля";
    }});

    function ctrlOn(v) {{
      return v === 1 || v === true || v === "1";
    }}

    function syncDevBar(j) {{
      if (!elDevBar) return;
      const map = [
        ["wifi", "ctrl_wifi", "devWifi"],
        ["ble", "ctrl_ble", "devBle"],
        ["cam", "ctrl_cam", "devCam"],
        ["mic", "ctrl_mic", "devMic"]
      ];
      map.forEach(function (row) {{
        const id = row[2];
        const key = row[1];
        const btn = document.getElementById(id);
        if (!btn) return;
        const lamp = btn.querySelector(".dev-lamp");
        if (id === "devBle") {{
          btn.classList.toggle("hidden", j.ble_hw !== 1);
        }}
        if (id === "devMic") {{
          btn.classList.toggle("hidden", j.mic_hw !== 1);
        }}
        if (btn.classList.contains("hidden")) {{
          return;
        }}
        const raw = j[key];
        const on = raw === undefined ? true : ctrlOn(raw);
        if (lamp) {{
          lamp.classList.toggle("on", on);
          lamp.classList.toggle("off", !on);
        }}
        btn.setAttribute("aria-pressed", on ? "true" : "false");
        btn.disabled = j.telemetry === "waiting_for_first_update" || j.usb === "waiting_for_first_json_line" || !j.wifi_ip;
      }});
    }}

    if (elDevBar) {{
      elDevBar.addEventListener("click", async function (ev) {{
        const btn = ev.target.closest(".dev-btn");
        if (!btn || btn.disabled || btn.classList.contains("hidden")) return;
        const key = btn.getAttribute("data-ctrl");
        if (!key) return;
        const lamp = btn.querySelector(".dev-lamp");
        const curOn = lamp && lamp.classList.contains("on");
        const next = curOn ? 0 : 1;
        try {{
          const rac = new AbortController();
          const rto = setTimeout(function () {{ rac.abort(); }}, 12000);
          let r;
          try {{
            r = await fetch("/board/control?" + encodeURIComponent(key) + "=" + next + "&_ts=" + Date.now(), {{
              cache: "no-store",
              signal: rac.signal
            }});
          }} finally {{
            clearTimeout(rto);
          }}
          const t = await r.text();
          if (!r.ok) {{
            elSt.textContent = "control: HTTP " + r.status + " " + t.slice(0, 200);
            elSt.className = "err";
            return;
          }}
          try {{
            const o = JSON.parse(t);
            if (o.ok !== 1 && o.ok !== "1") {{
              elSt.textContent = "control: " + t.slice(0, 240);
              elSt.className = "err";
              return;
            }}
          }} catch (e2) {{}}
          elSt.textContent = "команда отправлена " + new Date().toLocaleTimeString();
          elSt.className = "ok";
          setTimeout(function () {{ tick(); }}, 400);
        }} catch (e) {{
          elSt.textContent = "control: " + e;
          elSt.className = "err";
        }}
      }});
    }}

    let tickBusy = false;
    async function tick() {{
      if (tickBusy) return;
      tickBusy = true;
      const ac = new AbortController();
      const to = setTimeout(function () {{ ac.abort(); }}, 10000);
      try {{
        const r = await fetch("/api/telemetry?r=" + Date.now(), {{ cache: "no-store", signal: ac.signal }});
        if (!r.ok) throw new Error("HTTP " + r.status);
        const j = await r.json();
        render(j);
        syncDevBar(j);
        const waiting =
          j.telemetry === "waiting_for_first_update" || j.usb === "waiting_for_first_json_line";
        const linked = !waiting;
        const ch = (j.telemetry_channel_ru || j.telemetry_via || "").trim();
        setConnState(linked, ch);
        elSt.textContent = "обновлено " + new Date().toLocaleTimeString() + (coreOnly ? " · сводка" : " · все поля") +
          (waiting ? " · ждём данные" : (" · " + (ch || "?")));
        elSt.className = "ok";
      }} catch (e) {{
        setConnState(false, String(e && e.message ? e.message : e));
        elSt.textContent = "ошибка: " + e;
        elSt.className = "err";
      }} finally {{
        clearTimeout(to);
        tickBusy = false;
      }}
    }}
    let pollMs = 2000;
    let pollTimer = null;
    function schedulePoll() {{
      if (pollTimer) clearInterval(pollTimer);
      pollTimer = setInterval(function () {{
        if (document.hidden) return;
        tick();
      }}, pollMs);
    }}
    document.addEventListener("visibilitychange", function () {{
      if (!document.hidden) tick();
    }});
    tick();
    schedulePoll();
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

    # Автономный построитель модели пространства: грузим сохранённую модель и
    # запускаем фоновый ingest (камера+ToF+телеметрия -> WorldModel -> диск).
    global _WORLD_SVC
    if _HAVE_SPATIAL and _WORLD is not None:
        try:
            loaded = _WORLD.load(_WORLD_PATH)
            if loaded:
                sys.stderr.write("world: загружена модель %d вокселей из %s\n" % (loaded, _WORLD_PATH))
        except Exception as e:
            sys.stderr.write("world: не удалось загрузить модель: %r\n" % e)
        _WORLD_SVC = WorldService(
            _WORLD,
            get_ip=lambda: _board_ip_for_control(port, baud),
            world_path=_WORLD_PATH,
            sessions_dir=_WORLD_SESSIONS,
            interval_s=2.0,
        )
        _WORLD_SVC.start()
        sys.stderr.write("world: автономный сервис модели пространства запущен (интервал 2с)\n")

    parts: list[str] = []
    if start_wifi:
        parts.append(WIFI_GROUP_LABEL)
    if start_serial:
        parts.append("USB")
    if start_ble:
        parts.append("BLE")
    mode_line = " → ".join(parts) if parts else "(каналы выключены)"
    http_ui_rev = str(int(time.time()))

    class H(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        # Сокет-таймаут на соединение: зависший клиент (закрытая вкладка,
        # оборванный Wi-Fi телефона) не блокирует поток обработчика навечно.
        timeout = 30

        def log_message(self, fmt, *args):
            line = fmt % args
            if (
                "GET /api/telemetry" in line
                or "GET /favicon.ico" in line
                or "GET /live" in line
                or "GET /api/ui-meta" in line
            ):
                return
            sys.stderr.write("%s - %s\n" % (self.address_string(), line))

        def do_GET(self) -> None:
            path = self.path.split("?", 1)[0]
            try:
                if path == "/favicon.ico":
                    self.send_response(204)
                    self.send_header("Connection", "close")
                    self.end_headers()
                    return
                if path == "/api/ui-meta":
                    p = Path(__file__).resolve()
                    st = p.stat()
                    meta = {
                        "telemetry_script": str(p),
                        "script_mtime": int(st.st_mtime),
                        "http_boot_id": http_ui_rev,
                        "core_telem_keys": list(CORE_TELEM_KEYS),
                        "dashboard_title": "Тлм Монеткожуй",
                        "open_here": "/live?boot=" + http_ui_rev,
                    }
                    body = json.dumps(meta, ensure_ascii=False).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("Access-Control-Allow-Origin", "*")
                    self.send_header("Connection", "close")
                    self.end_headers()
                    self.wfile.write(body)
                    return
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
                elif path in ("/app/version.json", "/app/apk"):
                    # Самообновление Android-приложения по LAN: версия из
                    # dist/app-version.txt (то же число читает gradle при сборке),
                    # APK — самый свежий *.apk в dist/.
                    import glob as _glob

                    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                    dist_dir = os.path.join(repo_root, "dist")
                    apks = sorted(
                        _glob.glob(os.path.join(dist_dir, "*.apk")),
                        key=os.path.getmtime,
                        reverse=True,
                    )
                    if path == "/app/version.json":
                        try:
                            with open(os.path.join(dist_dir, "app-version.txt"), "r", encoding="utf-8") as vf:
                                ver = int(vf.read().strip())
                        except Exception:
                            ver = 0
                        body = json.dumps(
                            {"versionCode": ver, "apk": "/app/apk", "apk_present": 1 if apks else 0}
                        ).encode("utf-8")
                        self.send_response(200)
                        self.send_header("Content-Type", "application/json; charset=utf-8")
                        self.send_header("Content-Length", str(len(body)))
                        self.send_header("Cache-Control", "no-store")
                        self.send_header("Connection", "close")
                        self.end_headers()
                        self.wfile.write(body)
                    else:
                        if not apks:
                            self.send_error(404, "no apk in dist/ (download CI artifact there)")
                            return
                        with open(apks[0], "rb") as af:
                            data = af.read()
                        self.send_response(200)
                        self.send_header("Content-Type", "application/vnd.android.package-archive")
                        self.send_header("Content-Length", str(len(data)))
                        self.send_header("Content-Disposition", 'attachment; filename="xiao-robot.apk"')
                        self.send_header("Cache-Control", "no-store")
                        self.send_header("Connection", "close")
                        self.end_headers()
                        self.wfile.write(data)
                elif path.startswith("/world/"):
                    # Автономная персистентная модель пространства (сервис копит сам).
                    from urllib.parse import parse_qsl

                    def _wjson(obj, code=200):
                        b = json.dumps(obj, ensure_ascii=False).encode("utf-8")
                        self.send_response(code)
                        self.send_header("Content-Type", "application/json; charset=utf-8")
                        self.send_header("Content-Length", str(len(b)))
                        self.send_header("Cache-Control", "no-store")
                        self.send_header("Access-Control-Allow-Origin", "*")
                        self.send_header("Connection", "close")
                        self.end_headers()
                        self.wfile.write(b)

                    def _wq(name, default=None):
                        if "?" in self.path:
                            for k, v in parse_qsl(self.path.split("?", 1)[1]):
                                if k == name:
                                    return v
                        return default

                    if not _HAVE_SPATIAL or _WORLD is None:
                        _wjson({"ok": 0, "error": "spatial unavailable"}, 503)
                        return
                    svc = _WORLD_SVC
                    wlock = svc.lock if svc else None
                    wsub = path[7:]
                    if wsub.startswith("status"):
                        st = svc.status() if svc else _WORLD.stats()
                        st["ok"] = 1
                        _wjson(st)
                    elif wsub.startswith("data"):
                        if wlock:
                            wlock.acquire()
                        try:
                            pts = _WORLD.confident_points(cap=8000)
                        finally:
                            if wlock:
                                wlock.release()
                        # 7-й элемент — log-odds (уверенность зоны) для затенения в 3D.
                        arr = [[round(p[0], 3), round(p[1], 3), round(p[2], 3), p[3], p[4], p[5], round(p[6], 2)]
                               for p in pts]
                        _wjson({"ok": 1, "count": len(arr), "points": arr})
                    elif wsub.startswith("occupancy"):
                        if wlock:
                            wlock.acquire()
                        try:
                            grid = _WORLD.occupancy_2d(cell_m=0.10)
                        finally:
                            if wlock:
                                wlock.release()
                        cells = [[gx, gz, round(lo, 2)] for (gx, gz), lo in grid.items()]
                        _wjson({"ok": 1, "cell_m": 0.10, "count": len(cells), "cells": cells})
                    elif wsub.startswith("pause"):
                        on = (_wq("on", "1") not in ("0", "false", ""))
                        if svc:
                            svc.set_paused(on)
                        _wjson({"ok": 1, "paused": (svc.paused if svc else None)})
                    elif wsub.startswith("yaw"):
                        try:
                            deg = float(_wq("deg", "0") or 0)
                        except ValueError:
                            deg = 0.0
                        if svc:
                            svc.pose.manual_yaw_deg = deg
                        _wjson({"ok": 1, "yaw_deg": deg})
                    elif wsub.startswith("recenter"):
                        # Принять текущий курс IMU за 0° (робот «смотрит вперёд»).
                        was = svc.pose.recenter() if svc else None
                        _wjson({"ok": 1, "have_imu": bool(svc and svc.pose.have_imu),
                                "was_deg": was})
                    elif wsub.startswith("save"):
                        if wlock:
                            wlock.acquire()
                        try:
                            n = _WORLD.save(_WORLD_PATH)
                        finally:
                            if wlock:
                                wlock.release()
                        _wjson({"ok": 1, "saved": n})
                    elif wsub.startswith("clear"):
                        if wlock:
                            wlock.acquire()
                        try:
                            _WORLD.clear()
                            _WORLD.save(_WORLD_PATH)
                        finally:
                            if wlock:
                                wlock.release()
                        _wjson({"ok": 1, "total": 0})
                    elif wsub.startswith("ply"):
                        if wlock:
                            wlock.acquire()
                        try:
                            rows = _WORLD.confident_points(cap=10 ** 9)
                        finally:
                            if wlock:
                                wlock.release()
                        out = ["ply", "format ascii 1.0", "element vertex %d" % len(rows),
                               "property float x", "property float y", "property float z",
                               "property uchar red", "property uchar green", "property uchar blue",
                               "end_header"]
                        for x, y, z, r, g, b, _lo in rows:
                            out.append("%.4f %.4f %.4f %d %d %d" % (x, y, z, int(r), int(g), int(b)))
                        body = ("\n".join(out) + "\n").encode("ascii")
                        self.send_response(200)
                        self.send_header("Content-Type", "application/octet-stream")
                        self.send_header("Content-Disposition", 'attachment; filename="world.ply"')
                        self.send_header("Content-Length", str(len(body)))
                        self.send_header("Cache-Control", "no-store")
                        self.send_header("Connection", "close")
                        self.end_headers()
                        self.wfile.write(body)
                    else:
                        self.send_error(404)
                elif path.startswith("/cloud/"):
                    # Накопленное цветное облако точек (камера+ToF) для 3D-визуализатора.
                    from urllib.parse import parse_qsl

                    def _cloud_json(obj, code=200):
                        b = json.dumps(obj, ensure_ascii=False).encode("utf-8")
                        self.send_response(code)
                        self.send_header("Content-Type", "application/json; charset=utf-8")
                        self.send_header("Content-Length", str(len(b)))
                        self.send_header("Cache-Control", "no-store")
                        self.send_header("Access-Control-Allow-Origin", "*")
                        self.send_header("Connection", "close")
                        self.end_headers()
                        self.wfile.write(b)

                    csub = path[7:]
                    if not _HAVE_SPATIAL:
                        _cloud_json({"ok": 0, "error": "spatial module unavailable"}, 503)
                        return
                    if csub.startswith("clear"):
                        with _CLOUD_LOCK:
                            _CLOUD["pc"] = PointCloud(voxel_m=0.03)
                        _cloud_json({"ok": 1, "total": 0})
                    elif csub.startswith("capture"):
                        yaw = 0.0
                        if "?" in self.path:
                            for k, v in parse_qsl(self.path.split("?", 1)[1]):
                                if k == "yaw":
                                    try:
                                        yaw = float(v)
                                    except ValueError:
                                        pass
                        board_ip = _board_ip_for_control(port, baud)
                        if not board_ip:
                            _cloud_json({"ok": 0, "error": "no_board_ip"}, 503)
                            return
                        try:
                            jpeg, tof = fetch_frame(board_ip)
                        except Exception as e:
                            _cloud_json({"ok": 0, "error": repr(e)}, 502)
                            return
                        with _CLOUD_LOCK:
                            pc = _CLOUD["pc"]
                            added = add_frame(pc, tof, jpeg, Pose(yaw=_math.radians(yaw)), _CLOUD["cfg"])
                            total = len(pc)
                        _cloud_json({"ok": 1, "added": added, "total": total, "res": tof.get("res")})
                    elif csub.startswith("data"):
                        with _CLOUD_LOCK:
                            pts = list(_CLOUD["pc"]._vox.values())
                        cap = 6000  # не топить браузер: прорежаем равномерно
                        if len(pts) > cap:
                            step = len(pts) // cap + 1
                            pts = pts[::step]
                        arr = [[round(p[0], 3), round(p[1], 3), round(p[2], 3),
                                int(p[3]), int(p[4]), int(p[5])] for p in pts]
                        _cloud_json({"ok": 1, "count": len(arr), "points": arr})
                    elif csub.startswith("ply"):
                        with _CLOUD_LOCK:
                            rows = list(_CLOUD["pc"]._vox.values())
                        out = ["ply", "format ascii 1.0", "element vertex %d" % len(rows),
                               "property float x", "property float y", "property float z",
                               "property uchar red", "property uchar green", "property uchar blue",
                               "end_header"]
                        for x, y, z, r, g, b in rows:
                            out.append("%.4f %.4f %.4f %d %d %d" % (x, y, z, int(r), int(g), int(b)))
                        body = ("\n".join(out) + "\n").encode("ascii")
                        self.send_response(200)
                        self.send_header("Content-Type", "application/octet-stream")
                        self.send_header("Content-Disposition", 'attachment; filename="room.ply"')
                        self.send_header("Content-Length", str(len(body)))
                        self.send_header("Cache-Control", "no-store")
                        self.send_header("Connection", "close")
                        self.end_headers()
                        self.wfile.write(body)
                    else:
                        self.send_error(404)
                elif path.startswith("/board/"):
                    import urllib.request
                    from urllib.parse import parse_qsl, urlencode

                    # Кэш-бастер вырезаем ПО ИМЕНИ ключа (_ts/_), а не срезом строки:
                    # старый split("&r=") отрезал параметр правого колеса r=... у /drive,
                    # и повороты не работали (оба колеса получали команду левого).
                    qs = ""
                    if "?" in self.path:
                        raw_qs = self.path.split("?", 1)[1]
                        pairs = [(k, v) for (k, v) in parse_qsl(raw_qs, keep_blank_values=True)
                                 if k not in ("_", "_ts")]
                        qs = urlencode(pairs)
                    sub = path[7:]
                    if sub.startswith("control"):
                        board_path = "control"
                        if qs:
                            board_path += "?" + qs
                    elif sub.startswith("drive"):
                        board_path = "drive" + ("?" + qs if qs else "")
                    elif sub.startswith("scan360"):
                        board_path = "scan360" + ("?" + qs if qs else "")
                    elif sub.startswith("beep"):
                        board_path = "beep" + ("?" + qs if qs else "")
                    elif sub.startswith("melody"):
                        board_path = "melody" + ("?" + qs if qs else "")
                    elif sub.startswith("status"):
                        board_path = "status"
                    elif sub.startswith("floorcal"):
                        board_path = "floorcal"  # калибровка эталона пола
                    elif sub.startswith("tof"):
                        board_path = "tof"  # сетка зон для 3D-вида
                    else:
                        self.send_error(404)
                        return
                    board_ip = _board_ip_for_control(port, baud)
                    if not board_ip:
                        err = json.dumps(
                            {"ok": 0, "error": "no_board_ip", "hint": "Нужен wifi_ip в телеметрии (плата в сети)."},
                            ensure_ascii=False,
                        ).encode("utf-8")
                        self.send_response(503)
                        self.send_header("Content-Type", "application/json; charset=utf-8")
                        self.send_header("Content-Length", str(len(err)))
                        self.send_header("Cache-Control", "no-store")
                        self.send_header("Access-Control-Allow-Origin", "*")
                        self.send_header("Connection", "close")
                        self.end_headers()
                        self.wfile.write(err)
                        return
                    url = "http://%s/%s" % (board_ip, board_path)
                    timeout = 130.0 if board_path.startswith("scan360") else 8.0
                    try:
                        req = urllib.request.Request(url, headers={"Connection": "close", "Accept": "application/json"})
                        with urllib.request.urlopen(req, timeout=timeout) as resp:
                            body = resp.read()
                        ct = resp.headers.get("Content-Type") or "application/json; charset=utf-8"
                    except Exception as e:
                        err = json.dumps({"ok": 0, "error": repr(e)}, ensure_ascii=False).encode("utf-8")
                        self.send_response(502)
                        self.send_header("Content-Type", "application/json; charset=utf-8")
                        self.send_header("Content-Length", str(len(err)))
                        self.send_header("Cache-Control", "no-store")
                        self.send_header("Access-Control-Allow-Origin", "*")
                        self.send_header("Connection", "close")
                        self.end_headers()
                        self.wfile.write(err)
                        return
                    self.send_response(200)
                    self.send_header("Content-Type", ct.split(";")[0].strip() + "; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("Access-Control-Allow-Origin", "*")
                    self.send_header("Connection", "close")
                    self.end_headers()
                    self.wfile.write(body)
                elif path == "/":
                    self.send_response(307)
                    self.send_header("Location", "/live?boot=" + http_ui_rev)
                    self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
                    self.send_header("Pragma", "no-cache")
                    self.send_header("Expires", "0")
                    self.send_header("Connection", "close")
                    self.end_headers()
                    return
                elif path == "/live":
                    html = _dashboard_html(http_port, port, mode_line, http_ui_rev).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(html)))
                    self.send_header(
                        "Cache-Control",
                        "private, no-store, no-cache, max-age=0, must-revalidate, s-maxage=0",
                    )
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

    # Многопоточный HTTP: один зависший хендлер (мёртвая плата у /board/*,
    # долгая раздача APK, залипший long-poll вкладки) не должен блокировать
    # остальные запросы. Прошлую проблему «ThreadingMixIn + частый опрос =
    # зависание» лечит timeout=30 на соединении (см. класс H) + daemon-потоки:
    # застрявшие клиенты отваливаются сами и не копятся.
    class Srv(socketserver.ThreadingMixIn, socketserver.TCPServer):
        daemon_threads = True
        allow_reuse_address = False
        request_queue_size = 512

        def server_bind(self) -> None:
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
            if sys.platform == "win32" and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                try:
                    self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
                except OSError:
                    pass
            super().server_bind()

    print("Тлм Монеткожуй → открой в Chrome: http://127.0.0.1:%d/live?boot=%s" % (http_port, http_ui_rev), file=sys.stderr)
    print("Проверка версии (JSON): http://127.0.0.1:%d/api/ui-meta" % http_port, file=sys.stderr)
    print("Файл скрипта: %s" % Path(__file__).resolve(), file=sys.stderr)
    print(
        "Подсказка: встроенная вкладка Cursor иногда «висит» на localhost — "
        "откройте тот же URL в Chrome/Edge или Ctrl+Shift+P → Simple Browser: Show.",
        file=sys.stderr,
    )
    if start_serial:
        print("USB Serial %s @ %d — фон." % (port, baud), file=sys.stderr)
    if start_wifi:
        print("%s: %s" % (WIFI_GROUP_LABEL, _normalize_wifi_url(wifi_url)), file=sys.stderr)
    if start_ble:
        print("BLE: имя~%r GATT %s" % (ble_name, ble_char_uuid), file=sys.stderr)
    print("Ctrl+C — остановить сервер.", file=sys.stderr)
    _lan_ip = _local_lan_ip()
    if _lan_ip:
        print("LAN (телефон, «Обновить»): http://%s:%d/app/version.json" % (_lan_ip, http_port), file=sys.stderr)
    _try_register_mdns(http_port, _lan_ip)
    # 0.0.0.0: телефон качает APK (/app/apk) и видит панель по LAN — на
    # 127.0.0.1 «Обновить» с телефона физически не работало.
    with Srv(("0.0.0.0", http_port), H) as httpd:
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


def _local_lan_ip() -> str:
    """IP этого ПК в LAN (без трафика: UDP connect не шлёт пакетов)."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
        finally:
            s.close()
    except OSError:
        return ""


def _try_register_mdns(http_port: int, lan_ip: str) -> None:
    """mDNS-анонс «xiao-dash»: телефон находит ПК сам (кнопка «Обновить»).

    Необязательная зависимость: pip install zeroconf. Без неё — просто подсказка.
    """
    if not lan_ip:
        return
    try:
        from zeroconf import ServiceInfo, Zeroconf  # type: ignore[import-not-found]

        info = ServiceInfo(
            "_http._tcp.local.",
            "xiao-dash._http._tcp.local.",
            addresses=[socket.inet_aton(lan_ip)],
            port=http_port,
            properties={"path": "/app/version.json"},
        )
        Zeroconf().register_service(info)
        print("mDNS: xiao-dash → %s:%d (автопоиск ПК с телефона)" % (lan_ip, http_port), file=sys.stderr)
    except ImportError:
        print("mDNS: выкл — для автопоиска ПК с телефона: pip install zeroconf", file=sys.stderr)
    except Exception as e:  # noqa: BLE001 - анонс не должен ронять дашборд
        print("mDNS: ошибка анонса (%s)" % e, file=sys.stderr)


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
        default=None,
        help="URL GET /telemetry с платы (иначе XIAO_CAM_TELEMETRY_URL, --board-ip, camera_ip.txt, xiao-cam.local)",
    )
    ap.add_argument(
        "--board-ip",
        default=None,
        metavar="IP",
        help="IP платы в LAN → http://IP/telemetry (если не задан --wifi-url и XIAO_CAM_TELEMETRY_URL)",
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
        wifi_url = resolve_telemetry_wifi_url(args.wifi_url, args.board_ip)
        return _run_http_mode(
            args.port,
            args.baud,
            args.http,
            args.log,
            args.mode,
            wifi_url,
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
