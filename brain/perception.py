#!/usr/bin/env python3
"""
Распознавание — подключаемое. Архитектура НЕ завязана на конкретную модель:
есть интерфейс `Recognizer` и две реализации:
  * DummyRecognizer    — без зависимостей, скриптовый «обзор» (демо замкнутого цикла);
  * BrightnessRecognizer — Pillow опционально; правит курс по яркости половин кадра.
Реальную модель (YOLO/TFLite/сервер) добавляют новой реализацией Recognizer.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional


@dataclass
class Intent:
    """Высокоуровневое намерение. forward/turn в [-1..1]."""
    forward: float = 0.0
    turn: float = 0.0
    confident: bool = False


class Recognizer:
    name = "base"

    def infer(self, frame_jpeg: Optional[bytes], telemetry: dict) -> Intent:
        raise NotImplementedError


class DummyRecognizer(Recognizer):
    """Без CV: медленно ползём вперёд, периодически плавный поворот.
    Достаточно, чтобы проверить контур perception→safety→drive без модели."""

    name = "dummy"

    def __init__(self, period_s: float = 4.0) -> None:
        self._t0 = time.monotonic()
        self._period = period_s

    def infer(self, frame_jpeg: Optional[bytes], telemetry: dict) -> Intent:
        phase = (time.monotonic() - self._t0) % self._period
        turn = 0.4 if phase < self._period / 2 else -0.4
        return Intent(forward=0.35, turn=turn, confident=True)


class BrightnessRecognizer(Recognizer):
    """Правит курс к более светлой половине кадра. Требует Pillow;
    при его отсутствии .available()==False — мозг откатится на Dummy."""

    name = "brightness"

    def __init__(self) -> None:
        try:
            from PIL import Image  # noqa: F401
            self._ok = True
        except Exception:
            self._ok = False

    def available(self) -> bool:
        return self._ok

    def infer(self, frame_jpeg: Optional[bytes], telemetry: dict) -> Intent:
        if not self._ok or not frame_jpeg:
            return Intent(0.0, 0.0, confident=False)
        import io
        from PIL import Image

        try:
            img = Image.open(io.BytesIO(frame_jpeg)).convert("L")
        except Exception:
            return Intent(0.0, 0.0, confident=False)
        w, h = img.size
        if w < 4:
            return Intent(0.0, 0.0, confident=False)
        px = img.load()
        step = max(1, w // 64)
        left = right = 0
        half = w // 2
        for y in range(0, h, max(1, h // 32)):
            for x in range(0, half, step):
                left += px[x, y]
            for x in range(half, w, step):
                right += px[x, y]
        total = left + right
        if total == 0:
            return Intent(0.2, 0.0, confident=False)
        turn = (right - left) / float(total)  # >0 → правее светлее
        return Intent(forward=0.3, turn=max(-1.0, min(1.0, turn * 2.0)), confident=True)
