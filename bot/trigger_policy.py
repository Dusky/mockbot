"""Trigger policy — decides whether the bot should interject on a chat message.

Extracted from event_message so the core "should I respond?" rule is a pure,
deterministic function (inject `rng` in tests). The three triggers, in order:
  1. random chance — an independent dice roll vs `random_chance` percent
  2. lines        — `line_count` has reached `lines_between`
  3. time         — `elapsed_seconds` has reached `time_between` minutes
"""
from __future__ import annotations

import random
from dataclasses import dataclass


@dataclass(frozen=True)
class TriggerDecision:
    should_respond: bool
    roll: float | None = None   # the random roll, if one was taken (for log_dice)
    reason: str = "none"        # "random" | "lines" | "time" | "none"


def evaluate_trigger(random_chance: float, line_count: int, lines_between: int,
                     elapsed_seconds: float, time_between: int,
                     rng=random.uniform) -> TriggerDecision:
    """Return whether the bot should respond, plus the roll/reason for logging."""
    roll = None
    if random_chance > 0.0:
        roll = rng(0.0, 100.0)
        if roll <= random_chance:
            return TriggerDecision(True, roll, "random")

    if lines_between > 0 and line_count >= lines_between:
        return TriggerDecision(True, roll, "lines")
    if time_between > 0 and elapsed_seconds >= time_between * 60:
        return TriggerDecision(True, roll, "time")

    return TriggerDecision(False, roll, "none")
