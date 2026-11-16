"""Tests for monitor.py: the run-time glue between instrumented loops and the detectors."""
import sys
import types
from types import SimpleNamespace

import pytest

from conftest import make_state
from loop_autopsy.config import LoopStopped, Settings
from loop_autopsy.monitor import Monitor, _skip, _without_our_frames


def new_monitor(**info_fields):
    """A Monitor with one registered loop (``while x < 5:``), without IPython."""
    monitor = Monitor(None, Settings())
    loop_id = monitor.register(make_state(**info_fields).info)
    return monitor, loop_id


def run_loop(monitor, loop_id, values_per_round):
    """Do what instrumented code does: begin once, then due/check every round."""
    monitor.begin(loop_id)
    for values in values_per_round:
        if monitor.due(loop_id):
            monitor.check(loop_id, values)


# --------------------------------------------------------------------------- registering and pausing
def test_register_gives_each_loop_its_own_id():
    monitor = Monitor(None, Settings())
    first = monitor.register(make_state().info)
    second = monitor.register(make_state(lineno=7).info)
    assert (first, second) == (0, 1)
    assert monitor.infos[1].loop_id == 1 and monitor.infos[1].lineno == 7
    assert monitor.states == [None, None]  # no loop has started yet


def test_paused_states():
    monitor = Monitor(None, Settings())
    assert not monitor.is_paused()
    with monitor.paused():
        with monitor.paused():  # nested %%autopsy off cells
            assert monitor.is_paused()
        assert monitor.is_paused()
    assert not monitor.is_paused()
    monitor.enabled = False
    assert monitor.is_paused()


@pytest.mark.parametrize("raw_cell, paused", [
    ("%timeit sum(range(10))", True),
    ("%%timeit\nwhile False: pass", True),
    ("x = 1\n  %timeit x + 1", True),
    ("timeit = 3\nwhile timeit: timeit -= 1", False),
    ("print('%timeit')", False),
])
def test_timeit_cells_are_not_watched(raw_cell, paused):
    monitor = Monitor(None, Settings())
    monitor.pre_run_cell(SimpleNamespace(raw_cell=raw_cell))
    assert monitor.paused_cell is paused and monitor.is_paused() is paused


def test_pre_run_cell_without_info():
    monitor = Monitor(None, Settings())
    monitor.active = object()
    monitor.pre_run_cell()  # older IPython passes nothing
    assert monitor.paused_cell is False and monitor.active is None


# --------------------------------------------------------------------------- the hot path
def test_due_before_begin_and_when_disabled():
    monitor, loop_id = new_monitor()
    assert monitor.due(loop_id) is False  # the loop never called begin()
    monitor.begin(loop_id)
    monitor.enabled = False
    assert monitor.due(loop_id) is False and monitor.states[loop_id].rounds == 0
    monitor.enabled = True
    assert monitor.due(loop_id) is True and monitor.states[loop_id].rounds == 1


def test_stuck_loop_is_stopped_and_remembered():
    monitor, loop_id = new_monitor()
    with pytest.raises(LoopStopped) as stopped:
        run_loop(monitor, loop_id, [{"x": 1}] * 10)
    assert stopped.value.finding.kind == "stuck"
    assert monitor.stats == {"loops": 1, "stopped": 1}
    finding, state, elapsed = monitor.last
    assert finding is stopped.value.finding and state.stopped is finding and elapsed >= 0
    assert state.rounds == 3  # proven after three identical rounds


def test_healthy_loop_runs_to_the_end():
    monitor, loop_id = new_monitor()
    run_loop(monitor, loop_id, [{"x": i} for i in range(5)])
    state = monitor.states[loop_id]
    assert state.rounds == 5 and state.stopped is None
    assert monitor.stats == {"loops": 1, "stopped": 0} and monitor.last is None


def test_begin_starts_a_fresh_run_each_time():
    monitor, loop_id = new_monitor()
    run_loop(monitor, loop_id, [{"x": i} for i in range(5)])
    first = monitor.states[loop_id]
    run_loop(monitor, loop_id, [{"x": i} for i in range(2)])
    assert monitor.states[loop_id] is not first and monitor.states[loop_id].rounds == 2
    assert monitor.stats["loops"] == 2


def test_long_loops_are_checked_less_often():
    monitor, loop_id = new_monitor(compare=(("name", "x"), "<", ("const", 10**9)))
    run_loop(monitor, loop_id, ({"x": i} for i in range(20_000)))
    state = monitor.states[loop_id]
    assert state.rounds == 20_000
    assert state.checks < 3_000  # every round at first, then every 16th
    assert state.stride == 16


def test_only_watched_plain_values_reach_the_detectors():
    monitor, loop_id = new_monitor(watched=("x", "helper", "mod", "missing"))
    monitor.begin(loop_id)
    assert monitor.due(loop_id)
    monitor.check(loop_id, {"x": 1, "helper": len, "mod": sys, "unwatched": 99})
    _, snapshot = monitor.states[loop_id].history[-1]
    assert snapshot == {"x": 1}


def test_skip_ignores_functions_and_modules():
    assert _skip(len) and _skip(lambda: 0) and _skip(types) and _skip(int)
    assert not _skip(3) and not _skip("text") and not _skip([1, 2])


# --------------------------------------------------------------------------- reports and tracebacks
def test_show_last_without_a_report():
    assert Monitor(None, Settings()).show_last() is False


def test_show_last_prints_the_report(capsys):
    monitor, loop_id = new_monitor()
    with pytest.raises(LoopStopped):
        run_loop(monitor, loop_id, [{"x": 1}] * 10)
    assert monitor.show_last() is True
    out = capsys.readouterr().out
    assert "Loop autopsy" in out and "stuck loop" in out and "while x < 5:" in out


def test_traceback_is_cut_where_it_enters_loop_autopsy():
    monitor, loop_id = new_monitor()
    try:
        run_loop(monitor, loop_id, [{"x": 1}] * 10)
    except LoopStopped:
        tb = sys.exc_info()[2]

    def files(tb):
        names = []
        while tb is not None:
            names.append(tb.tb_frame.f_code.co_filename)
            tb = tb.tb_next
        return names

    assert any("monitor.py" in name for name in files(tb))
    cut = files(_without_our_frames(tb))
    assert cut and all("loop_autopsy" not in name.replace("\\", "/").split("/")[-2] for name in cut)
    assert _without_our_frames(None) is None


def test_exception_handler_is_installed_and_removed():
    calls = []
    shell = SimpleNamespace(custom_exceptions=(ValueError,), CustomTB="old handler",
                            set_custom_exc=lambda types_, handler: calls.append((types_, handler)))
    monitor = Monitor(shell, Settings())
    monitor.remove_exception_handler()  # nothing installed yet: must not fail
    monitor.install_exception_handler()
    handled, handler = calls[0]
    assert LoopStopped in handled and KeyboardInterrupt in handled and RecursionError in handled
    assert handler == monitor._handle
    shell.custom_exceptions, shell.CustomTB = handled, handler
    monitor.remove_exception_handler()
    assert shell.custom_exceptions == (ValueError,) and shell.CustomTB == "old handler"


# --------------------------------------------------------------------------- through a real shell
def test_stop_button_shows_the_replay(shell, capsys):
    code = ("i = 0\ntotal = 0\nwhile i < 50:\n    i += 1\n    total += 0\n"
            "    if i == 40:\n        raise KeyboardInterrupt\n")
    error = shell.run_cell(code).error_in_exec
    assert isinstance(error, KeyboardInterrupt)
    out = capsys.readouterr().out
    assert "KeyboardInterrupt" in out  # IPython's own message is kept
    assert "you stopped this loop" in out and "`total`" in out
    shell.run_cell("%autopsy last")
    assert "you stopped this loop" in capsys.readouterr().out


def test_interrupt_outside_a_loop_adds_no_report(shell, capsys):
    error = shell.run_cell("raise KeyboardInterrupt").error_in_exec
    assert isinstance(error, KeyboardInterrupt)
    out = capsys.readouterr().out
    assert "KeyboardInterrupt" in out and "Loop autopsy" not in out


def test_recursion_falls_back_when_the_explanation_fails(shell, capsys, monkeypatch):
    def broken(*args, **kwargs):
        raise ValueError("explanation failed")

    monkeypatch.setattr("loop_autopsy.monitor.recursion.explain", broken)
    error = shell.run_cell("def f(n):\n    return f(n)\nf(1)").error_in_exec
    assert isinstance(error, RecursionError)
    out = capsys.readouterr().out
    assert "RecursionError" in out and "Loop autopsy" not in out and "explanation failed" not in out
