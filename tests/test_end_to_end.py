"""End-to-end tests through a real IPython shell (shared by the whole team)."""
import pytest

from loop_autopsy.config import LoopStopped


def run(shell, code):
    result = shell.run_cell(code)
    return result.error_in_exec


@pytest.mark.parametrize("code, kind", [
    ("count = 1\nwhile count <= 5:\n    print(count)\n", "stuck"),
    ("while True:\n    pass\n", "stuck"),
    ("a = 0\nwhile a < 5:\n    a = (a + 1) % 3\n", "cycle"),
    ("x = 0\nwhile x != 10:\n    x += 3\n", "jumped_over"),
    ("x = 0.0\nwhile x != 1.0:\n    x += 0.1\n", "jumped_over"),
    ("def countdown(n):\n    while n > 0:\n        print(n)\ncountdown(3)\n", "stuck"),
])
def test_infinite_loops_are_stopped(shell, code, kind, capsys):
    error = run(shell, code)
    assert isinstance(error, LoopStopped) and error.finding.kind == kind
    assert "Loop autopsy" in capsys.readouterr().out


def test_wrong_direction_and_growing_list(shell):
    run(shell, "%autopsy config direction_after=50 growth_after=200")
    error = run(shell, "i = 0\nwhile i < 10:\n    i -= 1\n")
    assert error.finding.kind == "wrong_direction"
    error = run(shell, "nums = [1, 2, 3]\nfor n in nums:\n    nums.append(n)\n")
    assert error.finding.kind == "growing_iterable"


@pytest.mark.parametrize("code, expected", [
    ("i = 0\nwhile i < 1000:\n    i += 1\nresult = i", 1000),
    ("n = 27\nwhile n != 1:\n    n = n // 2 if n % 2 == 0 else 3 * n + 1\nresult = n", 1),
    ("lo, hi = 0, 10**6\nwhile lo < hi:\n    mid = (lo + hi) // 2\n    if mid < 777: lo = mid + 1\n"
     "    else: hi = mid\nresult = lo", 777),
    ("data = list(range(300, 0, -1))\nswapped = True\nwhile swapped:\n    swapped = False\n"
     "    for k in range(len(data) - 1):\n        if data[k] > data[k + 1]:\n"
     "            data[k], data[k + 1] = data[k + 1], data[k]\n            swapped = True\n"
     "result = data == sorted(data)", True),
    ("def step(v):\n    return v + 1\nk = 0\nwhile k < 10:\n    k = step(k)\nresult = k", 10),
    ("def g():\n    j = 0\n    while True:\n        yield j\n        j += 1\nit = g()\nresult = [next(it) for _ in range(3)]",
     [0, 1, 2]),
])
def test_normal_loops_run_untouched(shell, code, expected):
    assert run(shell, code) is None
    assert shell.user_ns["result"] == expected


def test_input_loop_is_not_stopped(shell):
    code = ("from unittest import mock\nseen = []\n"
            "with mock.patch('builtins.input', side_effect=['a', '', 'quit']):\n"
            "    while True:\n        s = input()\n        if s == 'quit': break\n        seen.append(s)\n")
    assert run(shell, code) is None and shell.user_ns["seen"] == ["a", ""]


def test_cell_magic_off_and_timeit(shell):
    assert run(shell, "%%autopsy off\nq = 0\nwhile q < 5000:\n    q += 1\n") is None
    assert run(shell, "%timeit -n 2 -r 1 sum(range(100))") is None


def test_line_magic_commands(shell, capsys):
    run(shell, "%autopsy off")
    assert run(shell, "c = 1\nwhile c < 2 and c > 0:\n    c = c\n    break\n") is None
    run(shell, "%autopsy on")
    run(shell, "%autopsy config time_limit=30 nonsense=1")
    out = capsys.readouterr().out
    assert "now OFF" in out and "time_limit = 30.0" in out and "Could not set 'nonsense'" in out
    run(shell, "w = 0\nwhile w < 1:\n    pass\n")
    capsys.readouterr()
    run(shell, "%autopsy last")
    assert "stuck loop" in capsys.readouterr().out


def test_session_summary(shell, capsys):
    def cell(code):
        return shell.run_cell(code, store_history=True)

    cell("total = sum(range(1000))")
    cell("count = 1\nwhile count <= 5:\n    pass\n")
    cell("1 / 0")
    cell("%autopsy")  # our own command: not part of the summary
    shell.run_cell("hidden = 1")  # no [number]: not part of the summary
    capsys.readouterr()
    cell("%autopsy summary all")
    out = capsys.readouterr().out
    assert "Session summary: 3 cells run" in out
    assert "total = sum(range(1000))" in out and "hidden" not in out
    assert "1 stopped" in out and "error" in out
    cell("%autopsy summary")
    assert "Worst cells first" in capsys.readouterr().out
    cell("%autopsy summary clear")
    cell("%autopsy summary")
    assert "No cells recorded yet" in capsys.readouterr().out
    cell("%autopsy summary nonsense")
    assert "Usage: %autopsy summary" in capsys.readouterr().out


def test_stderr_and_display_floods_are_muted(shell, capsys):
    run(shell, "%autopsy config max_output_lines=20 max_displays=5")
    capsys.readouterr()
    assert run(shell, "import sys\nfor i in range(300):\n    print('warn', i, file=sys.stderr)\n") is None
    err = capsys.readouterr().err
    assert "warn 19" in err and "warn 20\n" not in err and err.count("Output muted") == 1
    # The end of the flood is shown once the cell is over.
    assert "280 lines were muted. The last 20:" in err and err.rstrip().endswith("warn 299")
    assert "warn 279\n" not in err
    assert run(shell, "from IPython.display import display\nfor i in range(300):\n    display(i)\n") is None
    out = capsys.readouterr().out
    assert out.count("Display output muted after 5 items") == 1 and "299" not in out
    # The next cell starts with a clean slate, and a stopped loop's report still shows.
    assert run(shell, "print('fresh'); print('err ok', file=sys.stderr); display('shown')") is None
    captured = capsys.readouterr()
    assert "fresh" in captured.out and "shown" in captured.out and "err ok" in captured.err
    run(shell, "for i in range(300):\n    display(i)\nk = 1\nwhile k <= 5:\n    pass\n")
    out = capsys.readouterr().out
    assert "Display output muted" in out and "Loop autopsy" in out


def test_error_messages_are_never_muted(shell, capsys):
    """Outside Jupyter, tracebacks are printed to sys.stdout: a muted flood of
    print() must not swallow the error that ended the cell."""
    run(shell, "%autopsy config max_output_lines=20")
    capsys.readouterr()
    error = shell.run_cell("for i in range(300):\n    print(i)\n1 / 0\n", store_history=True).error_in_exec
    out = capsys.readouterr().out
    assert isinstance(error, ZeroDivisionError)
    assert out.count("Output muted") == 1 and "ZeroDivisionError" in out
    # The last values printed come just before the error, not after it.
    assert out.index("299\n") < out.index("ZeroDivisionError")
    code = "t = 0\nwhile t < 500:\n    t += 1\n    print(t)\n    if t == 400:\n        raise KeyboardInterrupt\n"
    shell.run_cell(code, store_history=True)
    out = capsys.readouterr().out
    assert "Output muted" in out and "KeyboardInterrupt" in out and "you stopped this loop" in out
    shell.run_cell("%autopsy summary all")
    assert capsys.readouterr().out.count("output muted") == 2  # both cells are still marked
    assert "showtraceback" not in vars(shell)  # the real method is back after each cell


def test_recursion_limit_guard_can_be_taken_over_and_removed(monkeypatch):
    """A Monitor that was never unloaded must not leave its wrapper on
    sys.setrecursionlimit for good: the next Monitor takes it over and can remove it."""
    import sys

    from loop_autopsy import monitor as monitor_module
    from loop_autopsy.config import Settings
    from loop_autopsy.monitor import Monitor

    monkeypatch.setattr(monitor_module, "SAFE_RECURSION", 5000)
    original = sys.setrecursionlimit
    old, new = Monitor(None, Settings()), Monitor(None, Settings())
    try:
        old.guard_recursion_limit()  # never unguarded, like a forgotten Monitor
        new.guard_recursion_limit()
        assert sys.setrecursionlimit.__self_monitor__ is new
        new.unguard_recursion_limit()
        assert sys.setrecursionlimit is original
    finally:
        sys.setrecursionlimit = original
