"""Regression tests for problems found in the pre-release package review (0.4.0)."""
import time

from loop_autopsy.magics import CellFailed


def run(shell, code):
    return shell.run_cell(code)


def test_and_condition_with_a_big_list_is_not_slowed_down_or_stopped(shell):
    """`while i < n and data:` used to fingerprint all of `data` on every check, so a
    correct 2,000-round loop over a big list ran out of time and was stopped."""
    run(shell, "%autopsy config time_limit=5")
    try:
        run(shell, "data = list(range(900_000))")
        started = time.perf_counter()
        result = run(shell, "i = 0\nwhile i < 2000 and data:\n    i += 1\n")
        assert result.error_in_exec is None
        assert shell.user_ns["i"] == 2000
        assert time.perf_counter() - started < 2
    finally:
        run(shell, "%autopsy reset")


def test_and_condition_still_catches_a_wrong_direction(shell):
    run(shell, "%autopsy config direction_after=20")
    try:
        result = run(shell, "flag = True\ni = 0\nwhile i < 10 and flag:\n    i -= 1\n")
        assert result.error_in_exec is not None
        assert result.error_in_exec.finding.kind == "wrong_direction"
    finally:
        run(shell, "%autopsy reset")


def test_error_in_an_unwatched_cell_fails_the_cell(shell, capsys):
    """An error inside `%%autopsy off` used to report success, so "Run All" went on."""
    capsys.readouterr()
    result = run(shell, "%%autopsy off\n1 / 0")
    out = capsys.readouterr().out
    assert not result.success and isinstance(result.error_in_exec, CellFailed)
    assert isinstance(result.error_in_exec.error, ZeroDivisionError)
    assert out.count("ZeroDivisionError") >= 1 and "see above" in out


def test_unwatched_cell_without_an_error_still_succeeds(shell):
    result = run(shell, "%%autopsy off\nq = 0\nwhile q < 1000:\n    q += 1\n")
    assert result.success and shell.user_ns["q"] == 1000


def test_empty_cycle_is_not_called_endless(shell):
    result = run(shell, "import itertools\nempty = list(itertools.cycle([]))\nalso = [v for v in itertools.cycle(())]")
    assert result.error_in_exec is None
    assert shell.user_ns["empty"] == [] and shell.user_ns["also"] == []


def test_non_empty_cycle_is_still_stopped_before_it_starts(shell):
    result = run(shell, "import itertools\nxs = list(itertools.cycle('ab'))")
    assert result.error_in_exec is not None and result.error_in_exec.finding.kind == "endless"


def test_watchdog_leaves_a_suspended_async_loop_alone(monkeypatch):
    """A background asyncio task waiting in `await` is not stuck; interrupting the main
    thread would stop an unrelated cell instead (it did: a plain time.sleep cell)."""
    import signal

    from conftest import make_state
    from loop_autopsy.config import Settings
    from loop_autopsy.monitor import Monitor

    sent = []
    monkeypatch.setattr(signal, "pthread_kill", lambda *a: sent.append(a), raising=False)
    monkeypatch.setattr(signal, "raise_signal", lambda *a: sent.append(a))
    monkeypatch.setattr(signal, "getsignal", lambda sig: signal.default_int_handler)
    for suspends, expected in ((True, 0), (False, 1)):
        sent.clear()
        monitor = Monitor(None, Settings(time_limit=1))
        loop_id = monitor.register(make_state(suspends=suspends).info)
        monitor.begin(loop_id)
        t0 = monitor.states[loop_id].started
        monitor.watchdog_tick(now=t0 + 5)
        monitor.watchdog_tick(now=t0 + 10)
        assert len(sent) == expected


def test_objects_with_a_slow_len_are_not_measured_on_every_check(shell):
    """len() of a Dask DataFrame runs its whole computation. It used to be called on
    every check, so a quick for loop over one ran into the time limit."""
    run(shell, "class SlowLen:\n"
               "    calls = 0\n"
               "    def __len__(self):\n"
               "        SlowLen.calls += 1\n"
               "        return 3000\n"
               "    def __iter__(self):\n"
               "        return iter(range(3000))\n"
               "items = SlowLen()")
    result = run(shell, "total = 0\nfor x in items:\n    total += x\n")
    assert result.error_in_exec is None and shell.user_ns["total"] == sum(range(3000))
    assert shell.user_ns["SlowLen"].calls == 0


def test_replay_still_shows_the_size_of_built_in_containers():
    from loop_autopsy.report import Size, replay_value
    assert replay_value(list(range(20))) == Size("list", 20)
    assert repr(replay_value(object())) == "<object>"


def test_a_loop_that_speeds_up_is_not_stopped_by_the_estimate(shell, capsys):
    """The estimate used to stop a loop early. A loop whose first rounds are slow and
    the rest instant was stopped as "would take too long" although it finished in time."""
    run(shell, "%autopsy config time_limit=2 estimate_after=0.2")
    try:
        capsys.readouterr()
        result = run(shell, "import time\nfor i in range(100_000):\n    if i < 40:\n        time.sleep(0.01)\n")
        out = capsys.readouterr().out
        assert result.error_in_exec is None and shell.user_ns["i"] == 99_999
        assert "Note: loop would take too long" in out  # still told early, as a note
    finally:
        run(shell, "%autopsy reset")
