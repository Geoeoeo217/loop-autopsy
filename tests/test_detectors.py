import pytest
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


# --------------------------------------------------------------------------- fingerprints, in detail
def test_cheap_fingerprint_of_small_containers_uses_their_content():
    assert cheap_fp(None) is None and cheap_fp(True) is True and cheap_fp(2.5) == 2.5
    assert cheap_fp(range(2, 10, 3)) == ("range", 2, 10, 3)
    items = [1, 2]
    assert cheap_fp(items) == ("list", id(items), 1, 2)  # a list is tied to its identity
    assert cheap_fp((1, "a")) == ("tuple", 0, 1, "a")  # tuples are compared by value only
    assert cheap_fp(frozenset({7})) == ("frozenset", 0, 7)
    table = {"k": 1}
    assert cheap_fp(table) == ("dict", id(table), ("k", 1))
    long_text = "x" * 100
    assert cheap_fp(long_text) == ("str", 100, hash(long_text))  # long strings are not stored
    assert cheap_fp(b"ab") == b"ab"


def test_cheap_fingerprint_gives_up_on_values_it_cannot_read():
    assert cheap_fp([1, object()]) is OPAQUE
    assert cheap_fp({"k": object()}) is OPAQUE
    assert cheap_fp({object(): 1}) is OPAQUE
    assert cheap_fp([[[object()]]]) is OPAQUE  # also when it is nested deeply
    nested = [[[[1, 2]]]]  # below three levels a container is only identified, not read
    innermost = nested[0][0][0]
    assert cheap_fp(nested)[2][2][2] == ("list", id(innermost))


def test_cheap_fingerprint_changes_when_a_small_list_changes():
    items = [1, 2, 3]
    before = cheap_fp(items)
    items[1] = 99
    assert cheap_fp(items) != before
    big = list(range(1000))  # a big list is only fingerprinted by size here ...
    before = cheap_fp(big)
    big[5] = -1
    assert cheap_fp(big) == before
    assert full_fp(big) != full_fp(list(range(1000)))  # ... the full fingerprint sees the change


def test_full_fingerprint_reads_every_container_type():
    assert full_fp(range(5)) == ("range", 0, 5, 1)
    assert full_fp({"a": [1, 2]}) == ("dict", (("a", ("list", (1, 2))),))
    assert full_fp({3, 1, 2}) == full_fp({1, 2, 3})  # sets ignore order
    assert full_fp({1, 2}) != full_fp(frozenset({1, 2}))  # but not the type
    assert full_fp((1, 2)) != full_fp([1, 2])
    assert full_fp({"a": 1, "b": 2}) != full_fp({"b": 2, "a": 1})  # dicts keep insertion order
    assert full_fp(bytearray(b"ab")) == ("bytearray", (97, 98))
    assert hash(full_fp({"k": {1, 2}, "v": [(1, 2), "text"]})) is not None  # always hashable


def test_full_fingerprint_gives_up_when_needed():
    assert full_fp(object()) is OPAQUE
    assert full_fp([1, [2, object()]]) is OPAQUE
    assert full_fp({"k": object()}) is OPAQUE
    assert full_fp({1, 2, 3}, budget=[2]) is OPAQUE  # more elements than the budget allows
    assert full_fp([[1, 2], [3, 4]], budget=[5]) is OPAQUE  # the budget is shared by nested lists
    assert full_fp([[1, 2], [3, 4]], budget=[6]) is not OPAQUE


# --------------------------------------------------------------------------- stuck and cycle, in detail
def test_big_list_with_unreadable_items_switches_the_rule_off():
    state = make_state(watched=("x", "data"))
    data = [object() for _ in range(100)]  # too big to read cheaply, impossible to read fully
    assert all(step(state, check_repeat, {"x": 1, "data": data}) is None for _ in range(6))
    assert state.scratch["rep_off"] is True


def test_for_loops_are_never_called_stuck():
    state = make_state(kind="for")
    assert all(step(state, check_repeat, {"x": 1}) is None for _ in range(6))


def test_method_calls_on_built_in_values_are_trusted():
    state = make_state(watched=("x", "items"), receivers=("items",))  # e.g. items.count(0)
    items = [0]  # one list, as in a real loop: a new list each round has a new id()
    results = [step(state, check_repeat, {"x": 1, "items": items}) for _ in range(3)]
    assert results[2].kind == "stuck"
    hidden = make_state(receivers=("gen",))  # the receiver is not even among the values
    assert all(step(hidden, check_repeat, {"x": 1}) is None for _ in range(5))
    assert hidden.scratch["rep_off"] == "calls"


def test_sampled_loop_is_measured_again_before_reporting():
    """A long loop is only checked every few rounds. A repeat seen that way gives
    no exact cycle length, so the rule starts over, checking every round."""
    state = make_state()
    state.stride = 16
    assert [step(state, check_repeat, {"x": 1}) for _ in range(3)] == [None, None, None]
    assert state.scratch["rep_refine"] is True and state.stride == 1
    assert state.next_check == state.rounds + 1
    results = [step(state, check_repeat, {"x": 1}) for _ in range(3)]
    assert results[:2] == [None, None] and results[2].kind == "stuck"


def test_stuck_messages_depend_on_the_condition():
    def stuck(**info):
        state = make_state(**info)
        return [step(state, check_repeat, {"x": 1}) for _ in range(3)][2]

    down = stuck(test_src="x > 0", header="while x > 0:", compare=(("name", "x"), ">", ("const", 0)))
    assert "x -= 1" in down.hint and down.culprits == ("x",)
    not_equal = stuck(test_src="x != 9", compare=(("name", "x"), "!=", ("const", 9)))
    assert "for example" not in not_equal.hint  # no single obvious fix to suggest
    constant_left = stuck(test_src="0 < x", compare=(("const", 0), "<", ("name", "x")))
    assert "for example" not in constant_left.hint
    forever = stuck(test_src="True", header="while True:", cond_names=(), compare=None)
    assert "no `break` is ever reached" in forever.explanation and "`break`" in forever.hint
    assert forever.culprits == ()


def test_cycle_message_shows_the_values_and_length():
    state = make_state()
    finding = None
    for value in [4, 2] * 4:
        finding = finding or step(state, check_repeat, {"x": value})
    assert finding.kind == "cycle" and finding.title == "loop stuck in a 2-round cycle"
    assert "Every 2 rounds" in finding.explanation and "`x = " in finding.explanation


# --------------------------------------------------------------------------- direction, in detail
def run_direction(state, xs, **settings):
    cfg = Settings(**settings)
    return [step(state, lambda info, st, v: check_direction(info, st, v, cfg), {"x": x}) for x in xs]


def test_direction_rule_needs_a_simple_numeric_comparison():
    assert run_direction(make_state(kind="for"), range(0, -40, -1), direction_after=5) == [None] * 40
    assert run_direction(make_state(compare=None), range(0, -40, -1), direction_after=5) == [None] * 40
    assert run_direction(make_state(), ["a", "b", "c"], direction_after=1) == [None] * 3  # strings
    assert run_direction(make_state(), [True, False, True], direction_after=1) == [None] * 3  # booleans


def test_wrong_direction_for_a_greater_than_condition():
    state = make_state(test_src="x > 0", compare=(("name", "x"), ">", ("const", 0)))
    finding = [f for f in run_direction(state, range(1, 40), direction_after=10) if f][0]
    assert finding.kind == "wrong_direction" and finding.culprits == ("x",)
    assert "needs `x` to reach 0" in finding.explanation and "from 1 to 11" in finding.explanation


def test_wrong_direction_with_the_variable_on_the_right():
    state = make_state(test_src="0 < x", compare=(("const", 0), "<", ("name", "x")))
    finding = [f for f in run_direction(state, range(1, 40), direction_after=10) if f][0]
    assert finding.kind == "wrong_direction" and finding.culprits == ("x",)


def two_variables(op, readings, **settings):
    """Run ``while lo <op> hi`` through the rule; ``readings`` are (lo, hi) pairs."""
    cfg = Settings(**settings)
    state = make_state(test_src=f"lo {op} hi", cond_names=("lo", "hi"), watched=("lo", "hi"),
                       compare=(("name", "lo"), op, ("name", "hi")))
    finding = None
    for lo, hi in readings:
        finding = finding or step(state, lambda info, st, v: check_direction(info, st, v, cfg),
                                  {"lo": lo, "hi": hi})
    return finding


def test_wrong_direction_names_the_right_hand_variable_when_it_is_the_one_moving():
    finding = two_variables("<", [(0, 10 + i) for i in range(30)], direction_after=10)
    assert finding.kind == "wrong_direction" and finding.culprits == ("hi",)
    assert "needs `hi` to reach `lo` (0)" in finding.explanation
    assert "`hi` went from 10 to 20" in finding.explanation
    assert "from 0 to 0" not in finding.explanation  # the old report blamed `lo`, which never moved
    assert "updates `hi`" in finding.hint and "`-=` instead of `+=`" in finding.hint


def test_wrong_direction_names_the_left_hand_variable_when_it_is_the_one_moving():
    finding = two_variables("<", [(-i, 10) for i in range(30)], direction_after=10)
    assert finding.culprits == ("lo",)
    assert "needs `lo` to reach `hi` (10)" in finding.explanation
    assert "`lo` went from 0 to -10" in finding.explanation
    assert "updates `lo`" in finding.hint and "`+=` instead of `-=`" in finding.hint


def test_wrong_direction_names_both_variables_when_both_move():
    finding = two_variables("<", [(-i, 10 + 2 * i) for i in range(30)], direction_after=10)
    assert finding.kind == "wrong_direction" and finding.culprits == ("lo", "hi")
    assert "needs `lo` and `hi` to meet" in finding.explanation
    assert "`lo` went from 0 to -10 and `hi` from 10 to 30" in finding.explanation
    assert "update `lo` and `hi`" in finding.hint


def test_wrong_direction_hint_suggests_the_step_that_would_help():
    """The example in the hint must point the right way: a value that climbs
    needs ``-=``, a value that falls needs ``+=``."""
    rising = make_state(test_src="x > 0", compare=(("name", "x"), ">", ("const", 0)))
    finding = [f for f in run_direction(rising, range(1, 40), direction_after=10) if f][0]
    assert "`-=` instead of `+=`" in finding.hint
    falling = make_state()  # while x < 5
    finding = [f for f in run_direction(falling, range(0, -40, -1), direction_after=10) if f][0]
    assert "`+=` instead of `-=`" in finding.hint


def test_jumped_over_names_the_right_hand_variable_when_it_is_the_one_moving():
    finding = two_variables("!=", [(10, 3 * i) for i in range(30)], jump_after=10)
    assert finding.kind == "jumped_over" and finding.culprits == ("hi",)
    assert "when `hi` is exactly `lo` (10)" in finding.explanation
    assert "`hi` went from 9 to 12, jumping over `lo` (10)" in finding.explanation


def test_jumped_over_names_both_variables_when_both_move():
    # lo climbs by 2 and hi falls by 1: 10 and 11, then 12 and 10. They are never equal.
    finding = two_variables("!=", [(2 * i, 16 - i) for i in range(40)], jump_after=10)
    assert finding.kind == "jumped_over" and finding.culprits == ("lo", "hi")
    assert "when `lo` and `hi` are exactly equal" in finding.explanation
    assert "`lo` went from 10 to 12 and `hi` from 11 to 10" in finding.explanation
    assert "passing each other without ever being equal" in finding.explanation
    assert "land on the same value" in finding.hint


def with_length(test_src, compare, readings, **settings):
    """Run a condition that uses ``len(nums)`` through the rule; ``readings`` are (i, nums) pairs."""
    cfg = Settings(**settings)
    state = make_state(test_src=test_src, cond_names=("i", "nums"), watched=("i", "nums"), compare=compare)
    finding = None
    for i, nums in readings:
        finding = finding or step(state, lambda info, st, v: check_direction(info, st, v, cfg),
                                  {"i": i, "nums": nums})
    return finding


I_BELOW_LEN = (("name", "i"), "<", ("len", "nums"))


def test_wrong_direction_against_the_length_of_a_list():
    nums = [1, 2, 3, 4, 5]
    finding = with_length("i < len(nums)", I_BELOW_LEN, [(-k, nums) for k in range(30)], direction_after=10)
    assert finding.kind == "wrong_direction" and finding.culprits == ("i",)
    assert "needs `i` to reach `len(nums)` (5)" in finding.explanation
    assert "`i` went from 0 to -10" in finding.explanation and "`+=` instead of `-=`" in finding.hint


def test_wrong_direction_when_the_length_is_on_the_left():
    finding = with_length("len(nums) > i", (("len", "nums"), ">", ("name", "i")),
                          [(-k, "abc") for k in range(30)], direction_after=10)
    assert finding.culprits == ("i",) and "needs `i` to reach `len(nums)` (3)" in finding.explanation


def test_jumped_over_the_length_of_a_list():
    finding = with_length("i != len(nums)", (("name", "i"), "!=", ("len", "nums")),
                          [(3 * k, (1,) * 10) for k in range(30)], jump_after=10)
    assert finding.kind == "jumped_over" and finding.culprits == ("i",)
    assert "when `i` is exactly `len(nums)` (10)" in finding.explanation


def test_a_list_that_grows_away_is_not_proof():
    # A queue can grow for a long time before it empties (breadth-first search),
    # so a length that moves away from its target is never reported.
    growing = [(0, list(range(k))) for k in range(1, 60)]
    assert with_length("len(nums) > i", (("len", "nums"), ">", ("name", "i")), growing,
                       direction_after=10) is None
    both = [(-k, list(range(k))) for k in range(60)]
    assert with_length("i < len(nums)", I_BELOW_LEN, both, direction_after=10) is None
    jumping = [(10, list(range(3 * k))) for k in range(40)]
    assert with_length("i != len(nums)", (("name", "i"), "!=", ("len", "nums")), jumping, jump_after=10) is None


def test_length_of_values_it_cannot_measure_safely_is_skipped():
    class Sized:
        def __len__(self):
            return 5  # could run any code: not measured

    for nums in (Sized(), 5, None):
        assert with_length("i < len(nums)", I_BELOW_LEN, [(-k, nums) for k in range(30)],
                           direction_after=10) is None


def test_a_step_in_the_right_direction_resets_the_count():
    state = make_state()  # while x < 5
    zigzag = [0, -1, -2, -3, -2, -3, -4, -5, -4] * 6  # never more than 3 wrong steps in a row
    assert run_direction(state, zigzag, direction_after=4) == [None] * len(zigzag)
    assert state.scratch["dir_streak"] == 0


def test_jumping_over_and_coming_back_is_not_reported():
    state = make_state(test_src="x != 10", compare=(("name", "x"), "!=", ("const", 10)))
    xs = [0, 4, 8, 12] + [12 - 0.01 * k for k in range(1, 150)]  # overshoots, then creeps back
    assert run_direction(state, xs, jump_after=10) == [None] * len(xs)
    # Both sides of the comparison are remembered: (x, 10) just before and just after the jump.
    assert state.scratch["jump_crossed"] == ((8, 10), (12, 10)) and state.scratch["jump_streak"] == 0


def test_jumped_over_with_floats_names_both_sides_of_the_jump():
    state = make_state(test_src="x != 1.0", compare=(("name", "x"), "!=", ("const", 1.0)))
    xs, x = [], 0.0
    for _ in range(40):
        xs.append(x)
        x += 0.1
    finding = [f for f in run_direction(state, xs, jump_after=10) if f][0]
    assert finding.kind == "jumped_over" and "jumping over 1.0" in finding.explanation
    assert "0.9999999999999999" in finding.explanation  # the value just before the jump
    assert "`<` or `>` instead of `!=`" in finding.hint


# --------------------------------------------------------------------------- time limit, in detail
def test_time_limit_explains_why_nothing_could_be_proven():
    settings = Settings(time_limit=5)

    def reason(state):
        state.rounds = 1234
        return check_time(state.info, state, settings, now=7.0).explanation

    plain = reason(make_state())
    assert "running for 7 s (1,234 rounds)" in plain and "(it " not in plain
    assert "calls functions" in reason(make_state(repeat_safe=False))
    calls = make_state()
    calls.scratch["rep_off"] = "calls"
    assert "calls functions" in reason(calls)
    opaque = make_state()
    opaque.scratch["rep_off"] = True
    assert "cannot look inside" in reason(opaque)
    assert "(it " not in reason(make_state(kind="for", repeat_safe=False))  # only said of while loops


def test_time_limit_can_be_switched_off_and_suggests_a_longer_one():
    state = make_state()
    assert check_time(state.info, state, Settings(time_limit=0), now=10 ** 6) is None
    finding = check_time(state.info, state, Settings(time_limit=30), now=31.0)
    assert "%autopsy config time_limit=150" in finding.hint
    assert check_time(state.info, state, Settings(time_limit=30), now=29.9) is None
