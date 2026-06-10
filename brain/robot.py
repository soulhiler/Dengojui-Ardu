#!/usr/bin/env python3
"""
Клиент платы для ИИ-«мозга»: переиспользует существующий HTTP-контракт
(`/telemetry`, `/capture`, `/drive`) + опциональный токен из Фазы 1 (`?t=`).
Прошивку менять не требуется. Только stdlib.
"""
from __future__ import annotations

import http.client
import json
import urllib.parse
from typing import Optional


class RobotError(RuntimeError):
    """Сбой связи с платой (нет ответа / не-200 / таймаут)."""


class RobotClient:
    def __init__(self, host: str, token: str = "", timeout: float = 4.0) -> None:
        # host: "192.168.1.50" или "192.168.1.50:80"
        if ":" in host:
            self.host, port = host.split(":", 1)
            self.port = int(port)
        else:
            self.host, self.port = host, 80
        self.token = token
        self.timeout = timeout

    def _suffix(self, sep: str) -> str:
        if not self.token:
            return ""
        return sep + "t=" + urllib.parse.quote(self.token, safe="")

    def _get(self, path: str, read_limit: int = 262144) -> bytes:
        try:
            conn = http.client.HTTPConnection(self.host, self.port, timeout=self.timeout)
            conn.request("GET", path, headers={"Host": self.host, "Connection": "close"})
            resp = conn.getresponse()
            body = resp.read(read_limit)
            conn.close()
        except OSError as e:
            raise RobotError("сеть: %s" % e) from e
        if resp.status != 200:
            raise RobotError("HTTP %s на %s" % (resp.status, path))
        return body

    def telemetry(self) -> dict:
        try:
            return json.loads(self._get("/telemetry").decode("utf-8", "replace"))
        except (ValueError, RobotError) as e:
            raise RobotError("телеметрия: %s" % e) from e

    def capture(self) -> bytes:
        """Один кадр JPEG (проще и надёжнее long-lived MJPEG для цикла ~5 Гц)."""
        return self._get("/capture", read_limit=512 * 1024)

    def drive(self, left: int, right: int) -> None:
        left = max(-255, min(255, int(left)))
        right = max(-255, min(255, int(right)))
        self._get("/drive?l=%d&r=%d%s" % (left, right, self._suffix("&")))

    def stop(self) -> None:
        self._get("/drive?stop=1%s" % self._suffix("&"))

    def stop_quiet(self) -> None:
        """Аварийный стоп — не бросает (для finally / потери связи)."""
        try:
            self.stop()
        except RobotError:
            pass
