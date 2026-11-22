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
