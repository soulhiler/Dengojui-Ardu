"""
spatial.xiao_client — забор кадра и ToF-сетки с платы XIAO по HTTP.

Эндпоинты прошивки: GET /capture (image/jpeg), GET /tof (JSON-сетка зон),
GET /telemetry (опц. одометрия). Сетку и кадр берём как можно ближе по времени.
"""
from __future__ import annotations

import json
import urllib.request


def _get(url: str, timeout: float = 5.0) -> bytes:
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return r.read()


def fetch_tof(ip: str, timeout: float = 5.0) -> dict:
    """GET /tof → {'ok','res','mm','grid':[...],...}."""
    raw = _get(f"http://{ip}/tof", timeout)
    return json.loads(raw.decode("utf-8"))


def fetch_capture(ip: str, timeout: float = 8.0) -> bytes:
    """GET /capture → JPEG-байты."""
    return _get(f"http://{ip}/capture", timeout)


def fetch_telemetry(ip: str, timeout: float = 5.0) -> dict:
    """GET /telemetry → полный снимок (энкодеры enc_l/enc_r и пр.)."""
    raw = _get(f"http://{ip}/telemetry", timeout)
    return json.loads(raw.decode("utf-8"))


def fetch_frame(ip: str) -> tuple[bytes, dict]:
    """ToF первым (быстрый), сразу кадр — минимизировать рассинхрон по времени."""
    tof = fetch_tof(ip)
    jpeg = fetch_capture(ip)
    return jpeg, tof
