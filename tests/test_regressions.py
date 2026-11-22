"""Regression tests for bugs found by trying to break the package.

Each group is one bug: a correct loop that was stopped, a correct loop that
crashed, or a notebook command after which loops failed. The thresholds are
the defaults unless a test says otherwise, because the bugs showed up there.
"""
from decimal import Decimal
from fractions import Fraction

import pytest

from conftest import make_state, step
from loop_autopsy import detectors
from loop_autopsy.config import LoopStopped, Settings
from loop_autopsy.detectors import check_direction, check_repeat
from loop_autopsy.monitor import Monitor


def run(shell, code):
    return shell.run_cell(code).error_in_exec


# --------------------------------------------------------------------------- globals used inside a function
@pytest.mark.parametrize("code, expected", [
    ("state = {'i': 0}\ndef go():\n    while state['i'] < 5:\n        state['i'] += 1\ngo()\nresult = state['i']", 5),
    ("n = 0\ndef go():\n    global n\n    while n < 5:\n        n += 1\ngo()\nresult = n", 5),
    ("items = [3, 2, 1]\ndef drain():\n    while items:\n        items.pop()\ndrain()\nresult = items", []),
])
def test_a_loop_in_a_function_that_updates_a_global_is_not_stuck(shell, code, expected):
    assert run(shell, code) is None
    assert shell.user_ns["result"] == expected


def test_a_stuck_loop_on_a_global_is_still_stopped_and_explained(shell):
    error = run(shell, "n = 0\ndef go():\n    global n\n    while n < 5:\n        pass\ngo()")
    assert isinstance(error, LoopStopped) and error.finding.kind == "stuck"
    assert "`n = 0`" in error.finding.explanation


def test_a_local_that_is_not_assigned_yet_does_not_pick_up_a_global(shell):
    code = ("found = 'global'\ndef go():\n    k = 0\n    while k < 3:\n        k += 1\n"
            "        if k == 3:\n            found = 'local'\n    return found\nresult = go()")
    assert run(shell, code) is None and shell.user_ns["result"] == "local"
    monitor = shell.user_ns["__loop_autopsy__"]
    _, snapshot = monitor.states[-1].history[0]
    assert "found" not in snapshot  # not the global's value


def test_a_condition_variable_found_nowhere_switches_the_stuck_rule_off():
    monitor = Monitor(None, Settings())
    loop_id = monitor.register(make_state(cond_names=("x",), watched=("x",)).info)
    monitor.begin(loop_id)
    for _ in range(10):  # x is in neither namespace: identical rounds prove nothing
        if monitor.due(loop_id):
            monitor.check(loop_id, {}, {"other": 1})
    assert monitor.states[loop_id].scratch["rep_off"] == "unseen"


# --------------------------------------------------------------------------- %reset and %reload_ext
def test_loops_still_work_after_reset(shell):
    run(shell, "%reset -f")
    assert run(shell, "i = 0\nwhile i < 3:\n    i += 1\nresult = i") is None
    assert shell.user_ns["result"] == 3
    error = run(shell, "while True:\n    pass")
    assert isinstance(error, LoopStopped)  # still watched, not just silenced
    assert "__loop_autopsy__" not in shell.run_cell("%who_ls").result


def test_a_function_defined_before_reload_keeps_its_own_loop(shell):
    run(shell, "def f():\n    j = 0\n    while j < 3:\n        pass\n")
    run(shell, "%reload_ext loop_autopsy")
    run(shell, "for a in range(2):\n    pass\nwhile False:\n    pass")  # new loops after the reload
    error = run(shell, "f()")
    assert isinstance(error, LoopStopped) and error.finding.kind == "stuck"
    finding, state, _ = shell.user_ns["__loop_autopsy__"].last
    assert state.info.header == "while j < 3:"  # f's loop, not one registered after the reload


def test_unknown_loop_ids_run_unwatched():
    monitor = Monitor(None, Settings())
    monitor.begin(7)
    assert monitor.due(7) is False
    assert monitor.sized(7, [1, 2]) == [1, 2]
    monitor.check(7, {"x": 1})  # nothing to do, and no error


# --------------------------------------------------------------------------- moving away, then turning back
def test_a_thrown_ball_is_not_moving_the_wrong_way(shell):
    code = "h, v = 1.0, 10.0\nrounds = 0\nwhile h > 0:\n    h += v\n    v -= 0.01\n    rounds += 1\nresult = rounds"
    assert run(shell, code) is None and shell.user_ns["result"] > 2000


def run_direction(xs, **settings):
    state, cfg = make_state(), Settings(**settings)
    return [step(state, lambda info, st, v: check_direction(info, st, v, cfg), {"x": x}) for x in xs]


def test_shrinking_steps_away_are_not_proof():
    xs, x, v = [], 0, -40
    for _ in range(40):
        xs.append(x)
        x, v = x + v, v + 1  # away from 5 for 40 rounds, slower each time
    assert run_direction(xs, direction_after=10) == [None] * 40


def test_steady_or_growing_steps_away_are_still_stopped():
    assert any(run_direction(range(0, -30, -1), direction_after=10))  # x -= 1
    assert any(run_direction([-(2 ** k) for k in range(30)], direction_after=10))  # x *= 2
    assert any(run_direction([-0.1 * k for k in range(30)], direction_after=10))  # float rounding


def test_a_jump_followed_by_slowing_down_is_not_proof():
    state, cfg = make_state(test_src="x != 5", compare=(("name", "x"), "!=", ("const", 5))), Settings(jump_after=10)
    xs, x, v = [], 0.0, 7.5  # jumps over 5, then drifts away more and more slowly
    for _ in range(40):
        xs.append(x)
        x, v = x + v, v * 0.8
    findings = [step(state, lambda info, st, val: check_direction(info, st, val, cfg), {"x": x}) for x in xs]
    assert findings == [None] * 40


# --------------------------------------------------------------------------- hash collisions
def test_states_with_equal_hashes_are_not_a_cycle(shell):
    assert hash(-1) == hash(-2)  # the CPython quirk that caused the false report
    code = ("i, j = 0, 0\nwhile i > -3:\n    j -= 1\n    if j < -2:\n        j = 0\n        i -= 1\n"
            "result = (i, j)")
    assert run(shell, code) is None and shell.user_ns["result"] == (-3, 0)


def test_full_fingerprints_are_compared_not_just_hashed():
    state = make_state(cond_names=("i", "j"), watched=("i", "j"), compare=None)
    rounds = [(i, j) for i in (0, -1, -2) for j in (0, -1, -2)]
    findings = [step(state, check_repeat, {"i": i, "j": j}) for i, j in rounds]
    assert findings == [None] * len(rounds)


def test_stored_fingerprints_are_capped(monkeypatch):
    monkeypatch.setattr(detectors, "STORED_ELEMENTS", 100)
    state = make_state(cond_names=("x",), watched=("x",), compare=None)
    big = [0] * 60  # too big for the cheap fingerprint to see inside, so every round reaches the full one
    for k in range(6):
        big[0] = k
        step(state, check_repeat, {"x": big})
    assert len(state.scratch["rep_full"]) == 1  # a second 61-element fingerprint would pass the cap
    assert state.scratch["rep_stored"] == 61


# --------------------------------------------------------------------------- rules must not crash the loop
@pytest.mark.parametrize("code, expected", [
    ("from decimal import Decimal\nd = Decimal(0)\nwhile d < 10.0:\n    d += Decimal('0.5')\nresult = d",
     Decimal(10)),
    ("from fractions import Fraction\nfrom decimal import Decimal\nf = Fraction(0)\nlimit = Decimal(3)\n"
     "while f < limit:\n    f += Fraction(1, 2)\nresult = f", Fraction(3)),
])
def test_decimal_mixed_with_float_or_fraction(shell, code, expected):
    assert run(shell, code) is None and shell.user_ns["result"] == expected


def test_wrong_direction_still_works_for_a_decimal_against_a_float():
    state, cfg = make_state(compare=(("name", "x"), "<", ("const", 5.0))), Settings(direction_after=10)
    findings = [step(state, lambda info, st, v: check_direction(info, st, v, cfg), {"x": Decimal(-k)})
                for k in range(30)]
    assert any(f and f.kind == "wrong_direction" for f in findings)


def test_a_rule_that_fails_is_switched_off_and_the_others_still_run(monkeypatch):
    def broken(*args):
        raise ArithmeticError("unexpected value")
    monkeypatch.setattr(detectors, "check_direction", broken)
    monitor = Monitor(None, Settings())
    loop_id = monitor.register(make_state().info)
    monitor.begin(loop_id)
    with pytest.raises(LoopStopped) as stopped:
        for _ in range(10):
            if monitor.due(loop_id):
                monitor.check(loop_id, {"x": 1})
    assert stopped.value.finding.kind == "stuck"
    assert monitor.states[loop_id].scratch["broken_rules"] == {"direction"}


@pytest.mark.parametrize("key, raw", [
    ("direction_after", "0"), ("jump_after", "0"), ("growth_after", "0.5"), ("replay_rows", "0"),
    ("max_watched", "0"), ("memory_stop", "0"), ("memory_warn", "1.5"), ("mem_red", "0"),
])
def test_settings_that_would_break_the_rules_are_refused(key, raw):
    settings = Settings()
    before = getattr(settings, key)
    with pytest.raises(ValueError):
        settings.update(key, raw)
    assert getattr(settings, key) == before


def test_edge_values_that_make_sense_are_accepted():
    settings = Settings()
    assert settings.update("direction_after", "1") == 1
    assert settings.update("memory_stop", "1") == 1.0
    assert settings.update("time_limit", "0") == 0.0  # 0 still means "no time limit"
