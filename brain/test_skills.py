#!/usr/bin/env python3
"""
Юнит-тесты навыков и планировщика (Фаза 2, без железа). Запуск:
  py -3 -m unittest discover -s brain
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from perception import Intent                                   # noqa: E402
from safety import SafetyGovernor                               # noqa: E402
from skills import (Avoid, Forward, SkillContext, SkillRegistry,  # noqa: E402
                    Stop, build_default_registry, turn_left, turn_right)
from planner import (NamedSkillPlanner, make_llm_policy,         # noqa: E402
                     reactive_policy, sequence_policy)


def ctx(**telem) -> SkillContext:
    return SkillContext(telemetry=telem)


class SkillTest(unittest.TestCase):
    def test_stop_is_zero_and_done(self) -> None:
        s = Stop()
        self.assertEqual((s.tick(ctx()).forward, s.tick(ctx()).turn), (0.0, 0.0))
        self.assertTrue(s.is_done(ctx()))

    def test_forward_speed(self) -> None:
        self.assertEqual(Forward(0.4).tick(ctx()).forward, 0.4)
        self.assertEqual(Forward(0.0).tick(ctx()).turn, 0.0)

    def test_turn_signs(self) -> None:
        self.assertLess(turn_left().tick(ctx()).turn, 0)    # влево < 0
        self.assertGreater(turn_right().tick(ctx()).turn, 0)  # вправо > 0

    def test_avoid_reacts_only_when_close(self) -> None:
        a = Avoid(near_mm=350)
        self.assertNotEqual(a.tick(ctx(tof_mm=200)).turn, 0.0)   # близко → отворот
        self.assertTrue(a.tick(ctx(tof_mm=200)).confident)
        far = a.tick(ctx(tof_mm=1000))
        self.assertEqual((far.forward, far.turn), (0.0, 0.0))    # далеко → пас
        self.assertFalse(far.confident)
        self.assertEqual(a.tick(ctx(tof_mm=0)).turn, 0.0)        # нет цели → пас

    def test_registry_addresses_by_name(self) -> None:
        reg = build_default_registry()
        for n in ("stop", "forward", "turn_left", "turn_right", "avoid"):
            self.assertIn(n, reg)
            self.assertEqual(reg.get(n).name, n)
        self.assertIsNone(reg.get("нет такого"))


class PlannerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.reg = build_default_registry()

    def test_unknown_skill_falls_back_to_stop(self) -> None:
        p = NamedSkillPlanner(self.reg, lambda c: "несуществующий")
        out = p.tick(ctx())
        self.assertEqual(p.active, "stop")
        self.assertEqual((out.forward, out.turn), (0.0, 0.0))

    def test_sequence_policy_advances_with_clock(self) -> None:
        t = {"v": 0.0}
        pol = sequence_policy([("forward", 1.0), ("turn_right", 1.0)], clock=lambda: t["v"])
        p = NamedSkillPlanner(self.reg, pol)
        t["v"] = 0.5; p.tick(ctx()); self.assertEqual(p.active, "forward")
        t["v"] = 1.5; p.tick(ctx()); self.assertEqual(p.active, "turn_right")
        t["v"] = 2.5; p.tick(ctx()); self.assertEqual(p.active, "forward")  # по кругу

    def test_reactive_policy_switches_on_obstacle(self) -> None:
        p = NamedSkillPlanner(self.reg, reactive_policy("forward", near_mm=350))
        p.tick(ctx(tof_mm=1000)); self.assertEqual(p.active, "forward")
        p.tick(ctx(tof_mm=200));  self.assertEqual(p.active, "avoid")
        p.tick(ctx(tof_mm=0));    self.assertEqual(p.active, "forward")  # нет цели

    def test_llm_policy_seam_picks_named_skill(self) -> None:
        # Фейковый «LLM»: по дистанции называет навык словами — политика извлекает имя.
        def fake_ask(prompt: str) -> str:
            return "думаю, стоит turn_left" if "tof_mm=200" in prompt else "поедем forward"
        p = NamedSkillPlanner(self.reg, make_llm_policy(fake_ask, self.reg))
        p.tick(ctx(tof_mm=200));  self.assertEqual(p.active, "turn_left")
        p.tick(ctx(tof_mm=1500)); self.assertEqual(p.active, "forward")

    def test_llm_policy_bad_reply_falls_back(self) -> None:
        p = NamedSkillPlanner(self.reg, make_llm_policy(lambda _: "бла-бла", self.reg))
        p.tick(ctx()); self.assertEqual(p.active, "stop")

    def test_planner_output_flows_through_governor_safely(self) -> None:
        # Граница соблюдена: Intent планировщика проходит governor; близкая стена
        # по ToF → governor режет скорость (Фаза 1) даже если навык газует.
        gov = SafetyGovernor(max_speed=180)
        p = NamedSkillPlanner(self.reg, lambda c: "forward")
        intent = p.tick(ctx(tof_mm=100))
        l, r = gov.decide(intent, {"tof_mm": 100}, now=1.0, last_perception_ts=1.0)
        self.assertEqual((l, r), (0, 0))   # стена в 100 мм → стоп вперёд


if __name__ == "__main__":
    unittest.main(verbosity=2)
