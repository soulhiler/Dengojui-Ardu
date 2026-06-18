#!/usr/bin/env python3
"""
brain.skills — примитивы-навыки (Фаза 2: задел под секвенсор/LLM).

Навык-примитив = «инструмент на границе решать↔исполнять» (принцип A2): мелкое
самодостаточное поведение, которое по телеметрии/кадру выдаёт `Intent`
(forward/turn в [-1..1]). Навыки **не трогают моторы** — это делает
`SafetyGovernor` (граница сохранена).

Навыки адресуются ПО ИМЕНИ через `SkillRegistry` — отсюда их может выбирать
ЛЮБОЙ планировщик: скриптовый, реактивный или LLM (он называет имя навыка). Это
и есть шов под Behavior Trees (навыки = листья дерева) и LLM-планировщик (H).
Сам BT/LLM здесь НЕ реализуется — только интерфейс и базовый набор примитивов.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Optional

from perception import Intent, Recognizer


@dataclass
class SkillContext:
    """Вход навыка: телеметрия платы + опц. кадр. Чистые данные, без I/O."""
    telemetry: dict = field(default_factory=dict)
    frame: Optional[bytes] = None


class Skill:
    """База примитива. `tick()` -> Intent; `is_done()` — для секвенсора/BT."""
    name = "skill"

    def tick(self, ctx: SkillContext) -> Intent:
        raise NotImplementedError

    def is_done(self, ctx: SkillContext) -> bool:
        return False

    def reset(self) -> None:
        """Сброс внутреннего состояния (секвенсор вызывает при входе в навык)."""


class Stop(Skill):
    name = "stop"

    def tick(self, ctx: SkillContext) -> Intent:
        return Intent(0.0, 0.0, confident=True)

    def is_done(self, ctx: SkillContext) -> bool:
        return True


class Forward(Skill):
    name = "forward"

    def __init__(self, speed: float = 0.35) -> None:
        self.speed = max(0.0, min(1.0, speed))

    def tick(self, ctx: SkillContext) -> Intent:
        return Intent(forward=self.speed, turn=0.0, confident=True)


class Turn(Skill):
    """Поворот на месте. sign +1 = вправо (turn>0), -1 = влево (turn<0)."""

    def __init__(self, rate: float = 0.6, sign: int = 1, name: str = "turn") -> None:
        self.rate = max(0.0, min(1.0, rate))
        self.sign = 1 if sign >= 0 else -1
        self.name = name

    def tick(self, ctx: SkillContext) -> Intent:
        return Intent(forward=0.0, turn=self.sign * self.rate, confident=True)


def turn_left(rate: float = 0.6) -> Turn:
    return Turn(rate, sign=-1, name="turn_left")


def turn_right(rate: float = 0.6) -> Turn:
    return Turn(rate, sign=+1, name="turn_right")


class Explore(Skill):
    """Обёртка распознавателя в навык: курс ведёт perception. Мост старого пути
    (`Recognizer`) в новый интерфейс навыков."""
    name = "explore"

    def __init__(self, recognizer: Recognizer) -> None:
        self.rec = recognizer

    def tick(self, ctx: SkillContext) -> Intent:
        return self.rec.infer(ctx.frame, ctx.telemetry)


class Avoid(Skill):
    """Реактивный отворот от близкого фронтального препятствия по `tof_mm`.
    Сенсорный примитив: близко (< near_mm) — крутимся прочь; иначе «не моя
    ситуация» (confident=False — планировщик передаст ход другому навыку)."""
    name = "avoid"

    def __init__(self, near_mm: int = 350, rate: float = 0.7, sign: int = 1) -> None:
        self.near_mm = int(near_mm)
        self.rate = max(0.0, min(1.0, rate))
        self.sign = 1 if sign >= 0 else -1

    def _too_close(self, ctx: SkillContext) -> bool:
        d = ctx.telemetry.get("tof_mm", 0)
        return isinstance(d, (int, float)) and 0 < d < self.near_mm

    def tick(self, ctx: SkillContext) -> Intent:
        if self._too_close(ctx):
            return Intent(forward=0.0, turn=self.sign * self.rate, confident=True)
        return Intent(0.0, 0.0, confident=False)

    def is_done(self, ctx: SkillContext) -> bool:
        return not self._too_close(ctx)


class SkillRegistry:
    """Навыки по имени — общий «каталог инструментов» для любого планировщика."""

    def __init__(self) -> None:
        self._skills: Dict[str, Skill] = {}

    def add(self, skill: Skill) -> "SkillRegistry":
        self._skills[skill.name] = skill
        return self

    def get(self, name: str) -> Optional[Skill]:
        return self._skills.get(name)

    def names(self):
        return sorted(self._skills)

    def __contains__(self, name: str) -> bool:
        return name in self._skills

    def __len__(self) -> int:
        return len(self._skills)


def build_default_registry(recognizer: Optional[Recognizer] = None) -> SkillRegistry:
    """Базовый набор примитивов. Если дан recognizer — добавляется навык
    `explore` (мост к perception)."""
    reg = SkillRegistry()
    reg.add(Stop()).add(Forward()).add(turn_left()).add(turn_right()).add(Avoid())
    if recognizer is not None:
        reg.add(Explore(recognizer))
    return reg
