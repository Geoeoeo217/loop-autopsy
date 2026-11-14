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
