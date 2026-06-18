#!/usr/bin/env python3
"""
Юнит-тесты safety-governor (без железа). Запуск:
  py -3 -m unittest discover -s brain
  или: py -3 brain/test_safety.py
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from perception import Intent          # noqa: E402
from safety import SafetyGovernor      # noqa: E402

GO = Intent(forward=1.0, turn=0.0, confident=True)


class SafetyGovernorTest(unittest.TestCase):
    def setUp(self) -> None:
        self.g = SafetyGovernor(max_speed=180, stop_cm=20, perception_timeout_s=1.0)

    def test_firmware_reflex_overrides(self) -> None:
        self.assertEqual(self.g.decide(GO, {"drive_safety": 2}, 10.0, 10.0), (0, 0))
        self.assertEqual(self.g.decide(GO, {"bumper": 1}, 10.0, 10.0), (0, 0))

    def test_ultrasonic_stop_zone(self) -> None:
        self.assertEqual(self.g.decide(GO, {"us_cm": 15}, 10.0, 10.0), (0, 0))

    def test_ultrasonic_zero_is_not_obstacle(self) -> None:
        # us_cm==0 = нет эха / вне диапазона — НЕ препятствие.
        l, r = self.g.decide(GO, {"us_cm": 0}, 10.0, 10.0)
        self.assertGreater(l, 0)
        self.assertGreater(r, 0)

    def test_ultrasonic_far_is_ok(self) -> None:
        l, r = self.g.decide(GO, {"us_cm": 90}, 10.0, 10.0)
        self.assertGreater(l, 0)

    def test_stale_perception_stops(self) -> None:
        self.assertEqual(self.g.decide(GO, {}, now=12.0, last_perception_ts=10.0), (0, 0))
        self.assertEqual(self.g.last_reason, "перцепция устарела")

    def test_low_confidence_stops(self) -> None:
        self.assertEqual(
            self.g.decide(Intent(1.0, 0.0, confident=False), {}, 10.0, 10.0), (0, 0)
        )

    def test_normal_forward_clamped(self) -> None:
        l, r = self.g.decide(GO, {}, 10.0, 10.0)
        self.assertEqual((l, r), (180, 180))

    def test_turn_is_asymmetric(self) -> None:
        l, r = self.g.decide(Intent(0.5, 0.5, confident=True), {}, 10.0, 10.0)
        self.assertGreater(l, r)

    def test_cadence_below_watchdog(self) -> None:
        # Команды должны обновляться раньше watchdog прошивки (450 мс).
        self.assertLess(self.g.command_interval_s, 0.450)
        self.assertLessEqual(self.g.command_interval_s, 0.25)

    # --- Регулятор скорости по фронтальному ToF (B10, зеркало прошивки) ---

    def test_tof_far_full_speed(self) -> None:
        # Далеко (>= slow 600 мм) — полный газ, регулятор не режет.
        l, r = self.g.decide(GO, {"tof_mm": 1000}, 10.0, 10.0)
        self.assertEqual((l, r), (180, 180))

    def test_tof_close_stops_forward(self) -> None:
        # Ближе stop (150 мм) — стоп движения вперёд.
        self.assertEqual(self.g.decide(GO, {"tof_mm": 100}, 10.0, 10.0), (0, 0))

    def test_tof_midband_scales(self) -> None:
        # Середина полосы (375 мм из 150..600) — примерно половина скорости.
        l, r = self.g.decide(GO, {"tof_mm": 375}, 10.0, 10.0)
        self.assertEqual(l, r)
        self.assertAlmostEqual(l, 90, delta=2)

    def test_tof_zero_is_not_obstacle(self) -> None:
        # tof_mm==0 / отсутствует = нет цели → не режем.
        self.assertEqual(self.g.decide(GO, {"tof_mm": 0}, 10.0, 10.0), (180, 180))
        self.assertEqual(self.g.decide(GO, {}, 10.0, 10.0), (180, 180))

    def test_tof_monotonic(self) -> None:
        # Чем ближе стена — тем ниже скорость (монотонность регулятора).
        speeds = [self.g.decide(GO, {"tof_mm": d}, 10.0, 10.0)[0]
                  for d in (200, 300, 450, 600)]
        self.assertEqual(speeds, sorted(speeds))

    def test_tof_does_not_touch_reverse(self) -> None:
        # Реверс к близкой стене регулятор не трогает (только движение вперёд).
        l, r = self.g.decide(Intent(forward=-1.0, turn=0.0, confident=True),
                             {"tof_mm": 100}, 10.0, 10.0)
        self.assertEqual((l, r), (-180, -180))

    def test_tof_gov_disabled(self) -> None:
        g = SafetyGovernor(max_speed=180, tof_gov=False)
        self.assertEqual(g.decide(GO, {"tof_mm": 100}, 10.0, 10.0), (180, 180))


if __name__ == "__main__":
    unittest.main(verbosity=2)
