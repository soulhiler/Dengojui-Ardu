#!/usr/bin/env python3
"""
brain.planner — выбор активного навыка (секвенсор). Шов под Behavior Trees / LLM.

`NamedSkillPlanner` параметризован ПОЛИТИКОЙ `policy(ctx) -> имя навыка`:
  * скриптовая последовательность (`sequence_policy`) — секвенсор / BT-lite (F);
  * реактивное правило (`reactive_policy`) — переключение по сенсору;
  * LLM (`make_llm_policy`) — политика спрашивает модель, какой навык запустить (H).

Все три используют ОДИН планировщик — меняется только политика. Планировщик
тикает выбранный навык и возвращает `Intent`; **моторов не касается** (A2) —
дальше Intent проходит через `SafetyGovernor`. Полноценный BT/LLM здесь не
реализуется: это шов, чтобы их можно было подключить, не трогая исполнение.
"""
from __future__ import annotations

from typing import Callable, List, Optional, Tuple

from perception import Intent
from skills import SkillContext, SkillRegistry

Policy = Callable[[SkillContext], str]


class NamedSkillPlanner:
    """Тикает навык, выбранный политикой по ИМЕНИ из реестра. Неизвестное имя →
    fallback (по умолчанию `stop` — безопасно). `active` хранит имя для логов."""

    def __init__(self, registry: SkillRegistry, policy: Policy, fallback: str = "stop") -> None:
        self.registry = registry
        self.policy = policy
        self.fallback = fallback
        self.active = fallback

    def tick(self, ctx: SkillContext) -> Intent:
        name = self.policy(ctx)
        if name not in self.registry:
            name = self.fallback
        self.active = name
        skill = self.registry.get(name)
        if skill is None:
            return Intent(0.0, 0.0, confident=False)
        return skill.tick(ctx)


def sequence_policy(steps: List[Tuple[str, float]], clock: Callable[[], float]) -> Policy:
    """Скриптовый секвенсор: список (имя_навыка, секунды), повтор по кругу.
    `clock()` — источник монотонного времени (в тестах подменяется фейком).
    Это простейший «BT-lite»: последовательность листьев по времени."""
    total = sum(max(0.0, s) for _, s in steps) or 1.0
    state = {"t0": None}

    def policy(ctx: SkillContext) -> str:
        if not steps:
            return "stop"
        now = clock()
        if state["t0"] is None:
            state["t0"] = now
        elapsed = (now - state["t0"]) % total
        acc = 0.0
        for name, secs in steps:
            acc += max(0.0, secs)
            if elapsed < acc:
                return name
        return steps[-1][0]

    return policy


def reactive_policy(default: str, avoid: str = "avoid", near_mm: int = 350) -> Policy:
    """Реактивное правило: близкое препятствие по `tof_mm` → `avoid`, иначе
    `default`. Демонстрирует выбор навыка по сенсору (без секвенса/времени)."""
    def policy(ctx: SkillContext) -> str:
        d = ctx.telemetry.get("tof_mm", 0)
        if isinstance(d, (int, float)) and 0 < d < near_mm:
            return avoid
        return default

    return policy


def _format_prompt(ctx: SkillContext, skill_names: List[str]) -> str:
    """Промпт для LLM-политики: сводка телеметрии + доступные навыки. Модель
    должна ответить ИМЕНЕМ навыка из списка."""
    t = ctx.telemetry or {}
    keys = ("tof_mm", "tof_valid", "imu_yaw", "drive_safety", "bumper", "us_cm")
    tele = ", ".join("%s=%s" % (k, t.get(k)) for k in keys if k in t)
    return (
        "Ты планировщик мобильного робота. Доступные навыки: %s.\n"
        "Телеметрия: %s.\n"
        "Ответь ОДНИМ именем навыка, который выполнить сейчас."
        % (", ".join(skill_names), tele or "нет данных")
    )


def make_llm_policy(ask: Callable[[str], str], registry: SkillRegistry,
                    fallback: str = "stop") -> Policy:
    """Шов под LLM-планировщик (H). `ask(prompt) -> ответ модели` инъектируется
    (реальный LLM-клиент или фейк в тестах — без сетевых зависимостей в brain/).
    Из ответа извлекается имя навыка из реестра; иначе `fallback`."""
    names = registry.names()

    def policy(ctx: SkillContext) -> str:
        prompt = _format_prompt(ctx, names)
        try:
            reply = (ask(prompt) or "").strip().lower()
        except Exception:
            return fallback
        for n in names:               # берём первое совпавшее имя навыка
            if n in reply:
                return n
        return fallback

    return policy
