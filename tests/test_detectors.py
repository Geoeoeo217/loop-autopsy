"""Tests for detectors.py (owner: Teammate 2)."""
from conftest import make_state, step

from loop_autopsy.config import Settings
from loop_autopsy.detectors import OPAQUE, check_direction, check_repeat, check_time, cheap_fp, full_fp


def test_fingerprints():
    assert cheap_fp(5) == 5 and cheap_fp("hi") == "hi"
    assert cheap_fp(object()) is OPAQUE
    big = list(range(1000))
    assert cheap_fp(big) == ("list", id(big), 1000)
    assert full_fp([1, [2, 3]]) == full_fp([1, [2, 3]])
    assert full_fp([1, 2]) != full_fp([2, 1])
    selfref = []
    selfref.append(selfref)
    assert full_fp(selfref) is OPAQUE


def test_stuck_loop_found_on_third_round():
    state = make_state()
    results = [step(state, check_repeat, {"x": 1}) for _ in range(3)]
    assert results[:2] == [None, None]
    assert results[2].kind == "stuck" and "`x = 1`" in results[2].explanation
    assert "x += 1" in results[2].hint


def test_cycle_found_with_length():
    state = make_state()
    finding = None
    for value in [0, 1, 2] * 5:
        finding = finding or step(state, check_repeat, {"x": value})
    assert finding.kind == "cycle" and "3-round" in finding.title


def test_moving_loop_is_not_flagged():
    state = make_state()
    assert all(step(state, check_repeat, {"x": i}) is None for i in range(500))


def test_big_list_changed_in_place_is_not_a_repeat():
    state = make_state(cond_names=("swapped",), watched=("swapped", "data"), compare=None)
    data = list(range(200, 0, -1))
    for i in range(150):  # one element changes each round, the list object stays the same
        data[i], data[i + 1] = data[i + 1], data[i]
        assert step(state, check_repeat, {"swapped": True, "data": data}) is None


def test_opaque_values_and_unsafe_loops_are_skipped():
    state = make_state(watched=("x", "obj"))
    assert all(step(state, check_repeat, {"x": 1, "obj": object()}) is None for _ in range(5))
    state = make_state(repeat_safe=False)
    assert all(step(state, check_repeat, {"x": 1}) is None for _ in range(5))
    state = make_state(receivers=("random",))  # random is a module, not a built-in value
    assert all(step(state, check_repeat, {"x": 1}) is None for _ in range(5))


def test_wrong_direction():
    settings = Settings(direction_after=20)
    state = make_state()
    finding = None
    for i in range(30):
        finding = finding or step(state, lambda info, st, v: check_direction(info, st, v, settings), {"x": -i})
    assert finding.kind == "wrong_direction" and "away from 5" in finding.explanation


def test_jumped_over():
    settings = Settings(jump_after=10)
    state = make_state(test_src="x != 10", compare=(("name", "x"), "!=", ("const", 10)))
    finding = None
    for i in range(0, 90, 3):
        finding = finding or step(state, lambda info, st, v: check_direction(info, st, v, settings), {"x": i})
    assert finding.kind == "jumped_over" and "from 9 to 12" in finding.explanation


def test_time_limit_and_input_exemption():
    settings = Settings(time_limit=5)
    state = make_state()
    assert check_time(state.info, state, settings, now=4.0) is None
    assert check_time(state.info, state, settings, now=6.0).kind == "time_limit"
    waiting = make_state(waits_for_user=True)
    assert check_time(waiting.info, waiting, settings, now=600.0) is None
