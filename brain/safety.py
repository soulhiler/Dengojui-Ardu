#!/usr/bin/env python3
"""
Safety-governor «мозга». Дублирует аппаратный рефлекс прошивки на уровне
мозга (defense-in-depth — ровно тот пробел, что виден у FOFOCA).

decide() — ЧИСТАЯ функция (без I/O и часов внутри): время передаётся
аргументом, поэтому логика безопасности полностью юнит-тестируема.
"""
from __future__ import annotations

from typing import Tuple

from perception import Intent


class SafetyGovernor:
    def __init__(
        self,
        max_speed: int = 180,
        stop_cm: int = 20,
        perception_timeout_s: float = 1.0,
        watchdog_ms: int = 450,
        tof_stop_mm: int = 150,
        tof_slow_mm: int = 600,
        tof_gov: bool = True,
    ) -> None:
        self.max_speed = max(1, min(255, int(max_speed)))
        self.stop_cm = int(stop_cm)
        self.perception_timeout_s = float(perception_timeout_s)
        self.watchdog_ms = int(watchdog_ms)
        # Регулятор скорости по фронтальному ToF — зеркало прошивки (safeSpeed, B10):
        # defense-in-depth, плюс полностью тестируется без железа. Пороги совпадают
        # с drive_config.h (XIAO_DRIVE_GOV_STOP_MM / SLOW_MM).
        self.tof_stop_mm = int(tof_stop_mm)
        self.tof_slow_mm = int(tof_slow_mm)
        self.tof_gov = bool(tof_gov)
        self.last_reason = "init"

    @property
    def command_interval_s(self) -> float:
        """Темп команд строго меньше watchdog прошивки (с запасом ×2),
        чтобы робот не вставал по watchdog в штатном движении, но вставал,
        если мозг умолк."""
        return min(0.25, (self.watchdog_ms / 1000.0) / 2.0)

    def _mix(self, intent: Intent) -> Tuple[int, int]:
        f = max(-1.0, min(1.0, intent.forward))
        t = max(-1.0, min(1.0, intent.turn))
        left = (f + t) * self.max_speed
        right = (f - t) * self.max_speed
        clamp = lambda v: int(max(-self.max_speed, min(self.max_speed, round(v))))
        return clamp(left), clamp(right)

    def gov_scale(self, tof_mm) -> float:
        """Масштаб скорости 0..1 по фронтальной дистанции (мм). Зеркало
        driveGovScale в прошивке: нет цели/выкл → 1.0; ближе stop → 0; дальше
        slow → 1.0; между — линейно."""
        if not self.tof_gov or not isinstance(tof_mm, (int, float)) or tof_mm <= 0:
            return 1.0  # цели нет / регулятор выкл — не режем
        if tof_mm <= self.tof_stop_mm:
            return 0.0
        if tof_mm >= self.tof_slow_mm:
            return 1.0
        return (tof_mm - self.tof_stop_mm) / float(self.tof_slow_mm - self.tof_stop_mm)

    def decide(
        self,
        intent: Intent,
        telemetry: dict,
        now: float,
        last_perception_ts: float,
    ) -> Tuple[int, int]:
        # 1. Аппаратный рефлекс прошивки (бампер/УЗ-латч) — уважаем всегда.
        if int(telemetry.get("drive_safety", 0)) != 0 or int(telemetry.get("bumper", 0)) != 0:
            self.last_reason = "reflex прошивки (safety/bumper)"
            return 0, 0
        # 2. УЗ-дистанция (us_cm==0 = нет эха / вне диапазона, НЕ препятствие).
        us = int(telemetry.get("us_cm", 0))
        if self.stop_cm > 0 and 0 < us <= self.stop_cm:
            self.last_reason = "УЗ %d см <= %d" % (us, self.stop_cm)
            return 0, 0
        # 3. Перцепция устарела (нет свежего кадра/решения) — стоп.
        if now - last_perception_ts > self.perception_timeout_s:
            self.last_reason = "перцепция устарела"
            return 0, 0
        # 4. Неуверенное решение — не двигаемся.
        if not intent.confident:
            self.last_reason = "неуверенно"
            return 0, 0
        # 5. Норма + регулятор по фронтальному ToF (только движение вперёд).
        self.last_reason = "ok"
        left, right = self._mix(intent)
        if left >= 0 and right >= 0 and (left > 0 or right > 0):
            scale = self.gov_scale(telemetry.get("tof_mm"))
            if scale < 1.0:
                left = int(round(left * scale))
                right = int(round(right * scale))
                self.last_reason = "ToF %s мм → %d%%" % (
                    telemetry.get("tof_mm"), int(round(scale * 100)))
        return left, right
