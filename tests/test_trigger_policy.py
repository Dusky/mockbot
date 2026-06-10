"""Tests for the message trigger policy."""
from bot.trigger_policy import evaluate_trigger


def _fixed_roll(value):
    return lambda lo, hi: value


def test_no_triggers_configured_never_responds():
    d = evaluate_trigger(0.0, 0, 0, 0.0, 0)
    assert not d.should_respond
    assert d.reason == "none"
    assert d.roll is None  # no roll taken when random_chance is 0


def test_random_hit():
    d = evaluate_trigger(50.0, 0, 0, 0.0, 0, rng=_fixed_roll(10.0))
    assert d.should_respond
    assert d.reason == "random"
    assert d.roll == 10.0


def test_random_miss_falls_through_to_none():
    d = evaluate_trigger(50.0, 0, 0, 0.0, 0, rng=_fixed_roll(90.0))
    assert not d.should_respond
    assert d.reason == "none"
    assert d.roll == 90.0  # roll recorded for log_dice even on a miss


def test_random_boundary_is_inclusive():
    # roll exactly == chance counts as a hit (roll <= chance)
    d = evaluate_trigger(25.0, 0, 0, 0.0, 0, rng=_fixed_roll(25.0))
    assert d.should_respond and d.reason == "random"


def test_lines_trigger_when_random_misses():
    d = evaluate_trigger(50.0, 100, 100, 0.0, 0, rng=_fixed_roll(99.0))
    assert d.should_respond
    assert d.reason == "lines"
    assert d.roll == 99.0


def test_lines_below_threshold_does_not_trigger():
    d = evaluate_trigger(0.0, 99, 100, 0.0, 0)
    assert not d.should_respond


def test_time_trigger_uses_minutes():
    # time_between is in minutes; elapsed is seconds
    assert evaluate_trigger(0.0, 0, 0, 600.0, 10).reason == "time"      # 10 min reached
    assert not evaluate_trigger(0.0, 0, 0, 599.0, 10).should_respond    # just under


def test_lines_take_precedence_over_time():
    d = evaluate_trigger(0.0, 100, 100, 100000.0, 1)
    assert d.reason == "lines"


def test_random_takes_precedence_over_lines():
    d = evaluate_trigger(100.0, 100, 100, 0.0, 0, rng=_fixed_roll(0.0))
    assert d.reason == "random"
