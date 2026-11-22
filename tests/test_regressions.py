"""Regression tests for bugs found by trying to break the package.

Each group is one bug: a correct loop that was stopped, a correct loop that
crashed, or a notebook command after which loops failed. The thresholds are
the defaults unless a test says otherwise, because the bugs showed up there.
"""
import time
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


# =========================================================================== second round of review fixes
# --------------------------------------------------------------------------- memory kept by loop-autopsy
def test_the_replay_history_does_not_keep_big_values_alive():
    from loop_autopsy.report import Long, replay_value

    for big in ("x" * 5000, b"y" * 5000, 10 ** 2000):
        marker = replay_value(big)
        assert isinstance(marker, Long) and marker == replay_value(big)
        assert len(repr(marker)) < 80
    assert replay_value("x" * 5000) != replay_value("z" * 5000)  # a change still shows
    assert replay_value(("ab", 1)) == ("ab", 1)  # small values are kept as they are
    assert not isinstance(replay_value(("x" * 5000,)), tuple)  # also inside a tuple


def test_a_deleted_string_is_not_held_by_the_report(shell):
    code = "s = ''\nwhile len(s) < 30_000:\n    s += 'x' * 10_000\n"
    assert run(shell, code) is None
    run(shell, "del s")
    state = shell.user_ns["__loop_autopsy__"].states[-1]
    held = [v for _, snap in state.history for v in snap.values() if isinstance(v, str)]
    assert all(len(v) <= 1000 for v in held)


def test_long_strings_are_fingerprinted_without_being_stored():
    fp = detectors.full_fp("x" * 5000)
    assert fp == ("str", 5000, hash("x" * 5000)) and detectors.full_fp("short") == "short"


def test_fingerprints_of_finished_loops_are_dropped_before_the_next_cell(shell):
    run(shell, "big = [0] * 60\nk = 0\nwhile k < 50:\n    big[0] = k\n    k += 1\n")
    monitor = shell.user_ns["__loop_autopsy__"]
    state = monitor.states[-1]
    run(shell, "pass")  # the next cell
    assert "rep_full" not in state.scratch and state.history  # the report's table stays


# --------------------------------------------------------------------------- the estimate and waiting for input
def for_state(total, rounds):
    state = make_state(kind="for", header="for row in data:", test_src="", cond_names=("data",),
                       watched=("data", "row"), compare=None, exits=False)
    state.total, state.rounds = total, rounds
    return state


def test_a_slow_first_round_is_not_taken_as_the_speed():
    settings = Settings(time_limit=10, estimate_after=1)
    state = for_state(100, 2)  # one round done, and it took 2.5 s (loading something)
    assert detectors.check_estimate(state.info, state, settings, now=2.5) is None
    for done in range(2, 60):  # then each round takes a millisecond
        state.rounds = done + 1
        assert detectors.check_estimate(state.info, state, settings, now=2.5 + done / 1000) is None


def test_a_loop_that_stays_slow_is_still_estimated():
    settings = Settings(time_limit=10, estimate_after=1)
    state = for_state(100, 2)
    findings = []
    for done in range(1, 6):  # 2.5 s every round
        state.rounds = done + 1
        findings.append(detectors.check_estimate(state.info, state, settings, now=2.5 * done))
    assert any(f and f.kind == "too_slow" for f in findings)


def test_time_waiting_for_input_through_a_helper_does_not_count(shell):
    run(shell, "%autopsy config time_limit=1")
    run(shell, "import builtins, time\nreal_input = builtins.input\n"
               "def slow_input(prompt=''):\n    time.sleep(0.3)\n    return '1'\nbuiltins.input = slow_input")
    try:
        code = ("def ask():\n    return int(input('guess: '))\nguess = 0\n"
                "while guess != 6:\n    guess += ask()\nresult = guess")
        assert run(shell, code) is None and shell.user_ns["result"] == 6  # 1.8 s, all of it waiting
    finally:
        run(shell, "builtins.input = real_input")
        run(shell, "%autopsy reset")


def test_unloading_puts_input_back():
    import builtins

    from IPython.testing.globalipapp import start_ipython
    from IPython import get_ipython

    ip = start_ipython() or get_ipython()
    ip.run_cell("%unload_ext loop_autopsy")
    original = builtins.input
    ip.run_cell("%load_ext loop_autopsy")
    assert getattr(builtins.input, "__loop_autopsy_wait__", False)
    ip.run_cell("%unload_ext loop_autopsy")
    assert builtins.input is original


# --------------------------------------------------------------------------- watched-name limit
def test_a_changing_variable_past_the_watch_limit_is_not_called_stuck(shell):
    names = "".join(f"    v{k} = {k}\n" for k in range(30))
    code = ("total = 0\nk = 0\nwhile total < 100:\n" + names +
            "    k += 1\n    if k > 10:\n        total = 1000\nresult = k")
    assert run(shell, code) is None and shell.user_ns["result"] == 11


def test_the_watch_limit_keeps_changing_names_first():
    import ast

    from loop_autopsy.transformer import LoopTransformer

    infos = []
    transformer = LoopTransformer(lambda info: infos.append(info) or len(infos) - 1, lambda: 3)
    reads = " + ".join(f"r{k}" for k in range(6))
    transformer.visit(ast.parse(f"while i < 10:\n    x = {reads}\n    i += 1\n"))
    info = infos[0]
    assert len(info.watched) == 3 and {"i", "x"} <= set(info.watched)
    assert not info.unwatched_changes  # only names that are just read were dropped
    infos.clear()
    sets = "\n".join(f"    c{k} = {k}" for k in range(6))
    transformer.visit(ast.parse(f"while i < 10:\n{sets}\n    i += 1\n"))
    assert infos[0].unwatched_changes  # a changing name had to be left out
    state = make_state(**{f: getattr(infos[0], f) for f in ("watched", "cond_names", "unwatched_changes")})
    assert step(state, check_repeat, {"i": 1}) is None and state.scratch["rep_off"] == "partial"


# --------------------------------------------------------------------------- recursion
def test_a_recursive_search_gets_the_time_limit(shell):
    run(shell, "%autopsy config time_limit=1 estimate_after=0")
    try:
        code = ("import time\ndef f(d):\n    for i in range(3):\n        time.sleep(0.002)\n"
                "        if d < 12:\n            f(d + 1)\nf(0)")
        started = time.perf_counter()
        error = run(shell, code)
        assert isinstance(error, LoopStopped) and error.finding.kind == "time_limit"
        assert time.perf_counter() - started < 5  # it would run for minutes
    finally:
        run(shell, "%autopsy reset")


def test_after_a_recursive_call_returns_the_outer_run_is_current_again(shell):
    run(shell, "def f(d):\n    for i in range(2):\n        if d < 3:\n            f(d + 1)\nf(0)")
    monitor = shell.user_ns["__loop_autopsy__"]
    loop_id = len(monitor.infos) - 1
    assert monitor.stacks[loop_id] == []  # every run ended and was removed
    assert monitor.states[loop_id].rounds == 2  # the outermost run, not the deepest one


def test_runaway_recursion_over_a_big_list_is_explained_quickly(shell):
    started = time.perf_counter()
    error = run(shell, "big = list(range(20000))\ndef f(lst):\n    return f(lst)\nf(big)")
    assert isinstance(error, RecursionError)
    finding, _, _ = shell.user_ns["__loop_autopsy__"].last
    assert finding.kind == "recursion_stuck"
    assert time.perf_counter() - started < 2  # was about 6 s


# --------------------------------------------------------------------------- threads and other handlers
def test_a_loop_stopped_in_a_thread_gets_a_report(shell, capsys):
    capsys.readouterr()
    run(shell, "import threading\ndef spin():\n    x = 1\n    while x < 5:\n        pass\n"
               "t = threading.Thread(target=spin)\nt.start(); t.join(5)")
    captured = capsys.readouterr()
    assert "Loop autopsy" in captured.out and "stuck loop" in captured.out
    assert "Exception in thread" not in captured.err


def test_the_same_loop_in_two_threads_only_uses_the_time_rules():
    import threading

    monitor = Monitor(None, Settings())
    loop_id = monitor.register(make_state().info)
    worker = threading.Thread(target=monitor.begin, args=(loop_id,))
    worker.start()
    worker.join()  # its run never ended: it is still on the stack
    monitor.begin(loop_id)
    first, second = monitor.stacks[loop_id]
    assert first.scratch["shared"] and second.scratch["shared"]
    for _ in range(10):  # identical rounds: alone this would be "stuck"
        if monitor.due(loop_id):
            monitor.check(loop_id, {"x": 1})


def test_an_earlier_custom_exception_handler_keeps_working(shell):
    from loop_autopsy.config import LoopStopped as Stopped

    seen = []

    def handler(sh, etype, value, tb, tb_offset=None):
        seen.append(etype)
        return ["handled elsewhere"]

    run(shell, "%unload_ext loop_autopsy")
    shell.set_custom_exc((ValueError,), handler)
    try:
        run(shell, "%load_ext loop_autopsy")
        run(shell, "raise ValueError('not ours')")
        assert seen == [ValueError]
        assert isinstance(run(shell, "while True:\n    pass"), Stopped)  # ours still works
    finally:
        run(shell, "%unload_ext loop_autopsy")
        assert shell.custom_exceptions == (ValueError,)  # given back on unload
        shell.set_custom_exc((), None)
        run(shell, "%load_ext loop_autopsy")  # the fixture unloads it at the end


# --------------------------------------------------------------------------- output without newlines
def test_output_without_newlines_is_muted_too():
    import io

    from loop_autopsy.heatmap import OutputGuard

    sink = io.StringIO()
    guard = OutputGuard(sink, max_lines=100)
    for i in range(100_000):
        guard.write(f"{i} ")  # print(i, end=" ")
    assert guard.muted and len(sink.getvalue()) < 100 * OutputGuard.LINE_CHARS + 500
    sink, guard = io.StringIO(), OutputGuard(io.StringIO(), max_lines=100)
    guard._stream = sink
    guard.write("z" * 10_000_000)  # one huge print
    assert guard.muted and len(sink.getvalue()) < 100 * OutputGuard.LINE_CHARS + 500


def test_normal_output_is_counted_as_before():
    import io

    from loop_autopsy.heatmap import OutputGuard

    sink = io.StringIO()
    guard = OutputGuard(sink, max_lines=5)
    guard.write("a\nb\n")
    guard.write("c" * 999)  # just under one long line
    assert guard.lines == 2 and not guard.muted
    guard.write("\nd\ne\n")
    assert guard.muted and sink.getvalue().startswith("a\nb\n" + "c" * 999 + "\nd\ne\n")


def test_changed_names_sees_every_way_a_loop_changes_a_variable():
    import ast

    from loop_autopsy.transformer import changed_names

    body = ast.parse("a = 1\nb += 1\nc[0] = 1\nd.e.f = 2\ndel g[1]\nh.append(3)\nprint(i)\nj[k] = m").body
    assert changed_names(body) == {"a", "b", "c", "d", "g", "h", "j"}


def test_other_thread_errors_and_handlers_are_passed_on():
    import threading
    from types import SimpleNamespace

    passed_on = []
    monitor = Monitor(SimpleNamespace(custom_exceptions=(ValueError, KeyboardInterrupt),
                                      CustomTB=lambda *a, **k: passed_on.append(a[0]) or ["old"],
                                      set_custom_exc=lambda types, handler: None), Settings())
    monitor.install_exception_handler()
    try:
        assert monitor._handle(None, ValueError, ValueError(), None) == ["old"]
        assert monitor._handle(None, TypeError, TypeError(), None) is None  # nobody handles it
        assert monitor._handle(None, KeyboardInterrupt, KeyboardInterrupt(), None) == ["old"]
        assert passed_on == [ValueError, KeyboardInterrupt]
        hooked = []
        monitor._old_thread_hook = hooked.append
        error = SimpleNamespace(exc_type=ValueError, exc_value=ValueError(), exc_traceback=None, thread=None)
        threading.excepthook(error)  # not ours: the earlier hook gets it
        assert hooked == [error]
    finally:
        monitor.remove_exception_handler()
    assert threading.excepthook is not monitor._thread_hook
