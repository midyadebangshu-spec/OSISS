"""SM-2 scheduler (srs.py): pure."""

import datetime as dt
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest

import srs
from srs import AGAIN, EASY, GOOD, HARD, State


def run(grades, start=State()):
    state, history = start, []
    for grade in grades:
        state = srs.review(state, grade)
        history.append((state.interval, state.reps, state.ease))
    return state, history


def test_good_answers_follow_the_sm2_intervals():
    state, history = run([GOOD] * 5)
    assert [h[0] for h in history] == [1, 6, 15, 38, 95]  # 1, 6, then x2.5 each time (ease stays 2.5 for "good")
    assert state.ease == 2.5 and state.reps == 5 and state.lapses == 0


def test_ease_moves_with_the_grade_and_has_a_floor():
    assert srs.review(State(), EASY).ease == 2.6
    assert srs.review(State(), HARD).ease == 2.36
    assert srs.review(State(), GOOD).ease == 2.5
    assert srs.review(State(), AGAIN).ease == 2.5  # a failure does not change the ease in SM-2
    worn, _ = run([HARD] * 30)
    assert worn.ease == srs.MIN_EASE


def test_a_failure_starts_over_and_counts_a_lapse_only_after_learning():
    learned, _ = run([GOOD, GOOD, GOOD])
    failed = srs.review(learned, AGAIN)
    assert (failed.interval, failed.reps, failed.lapses) == (1, 0, 1) and failed.ease == learned.ease
    fresh_fail = srs.review(State(), AGAIN)
    assert (fresh_fail.interval, fresh_fail.reps, fresh_fail.lapses) == (1, 0, 0)  # failing a new card is not a lapse
    recovered, history = run([GOOD, GOOD], failed)
    assert [h[0] for h in history] == [1, 6]


def test_hard_still_passes_and_easy_grows_faster():
    _, hard = run([GOOD, GOOD, HARD])
    _, easy = run([GOOD, GOOD, EASY])
    assert (hard[2][0], easy[2][0]) == (14, 16) and hard[2][1] == easy[2][1] == 3  # 6 x 2.36 and 6 x 2.6
    # the interval always grows by at least a day, even with a tiny ease
    state = State(ease=srs.MIN_EASE, interval=1, reps=2)
    assert srs.review(state, GOOD).interval == 2


def test_interval_is_capped():
    state = State(ease=3.0, interval=3000, reps=10)
    assert srs.review(state, EASY).interval == srs.MAX_INTERVAL


def test_due_date_and_preview():
    assert srs.due_date(dt.date(2026, 11, 4), State(interval=6)) == dt.date(2026, 11, 10)
    assert srs.preview(State()) == {AGAIN: 1, HARD: 1, GOOD: 1, EASY: 1}
    assert srs.preview(State(ease=2.5, interval=6, reps=2)) == {AGAIN: 1, HARD: 14, GOOD: 15, EASY: 16}


def test_bad_grade():
    with pytest.raises(ValueError):
        srs.review(State(), 4)
