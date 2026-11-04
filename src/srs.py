"""Spaced repetition: the SM-2 scheduler. Pure, no services.

SM-2 (SuperMemo, 1987): after each review the card gets a new interval in days and an adjusted "ease". The four buttons
map to SM-2 quality scores: again = 1 (fail), hard = 3, good = 4, easy = 5. A failed card starts over at one day; a
passed card goes 1 day, 6 days, then `interval * ease` (using the ease just updated, so hard, good and easy give
different intervals from the third review on). Ease changes by `0.1 - (5 - q) * (0.08 + (5 - q) * 0.02)` and
never falls below 1.3.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, replace
from typing import Dict

AGAIN, HARD, GOOD, EASY = 0, 1, 2, 3
GRADE_NAMES = {AGAIN: "again", HARD: "hard", GOOD: "good", EASY: "easy"}
QUALITY: Dict[int, int] = {AGAIN: 1, HARD: 3, GOOD: 4, EASY: 5}
START_EASE = 2.5
MIN_EASE = 1.3
MAX_INTERVAL = 3650  # ten years


@dataclass(frozen=True)
class State:
    ease: float = START_EASE
    interval: int = 0  # days until the next review (0 = never reviewed)
    reps: int = 0  # consecutive passes
    lapses: int = 0  # times failed after learning


def review(state: State, grade: int) -> State:
    """The state after answering with `grade` (0 again, 1 hard, 2 good, 3 easy)."""
    if grade not in QUALITY:
        raise ValueError("grade must be 0 (again), 1 (hard), 2 (good) or 3 (easy)")
    quality = QUALITY[grade]
    if quality < 3:
        return replace(state, reps=0, interval=1, lapses=state.lapses + (1 if state.reps > 0 or state.interval > 0 else 0))
    reps = state.reps + 1
    ease = max(MIN_EASE, state.ease + 0.1 - (5 - quality) * (0.08 + (5 - quality) * 0.02))
    if reps == 1:
        interval = 1
    elif reps == 2:
        interval = 6
    else:
        interval = max(state.interval + 1, round(state.interval * ease))  # always moves forward
    return State(ease=round(ease, 3), interval=min(interval, MAX_INTERVAL), reps=reps, lapses=state.lapses)


def due_date(today: dt.date, state: State) -> dt.date:
    return today + dt.timedelta(days=state.interval)


def preview(state: State) -> Dict[int, int]:
    """The interval (days) each button would give, for the labels under the buttons."""
    return {grade: review(state, grade).interval for grade in QUALITY}
