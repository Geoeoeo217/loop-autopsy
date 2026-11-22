"""Loops that loop-autopsy did not watch before: comprehensions, list(it) and other
loops in C, async loops, %run scripts, conditions on attributes or with and/or,
NumPy numbers, endless generators, a round that never ends, and code that
swallows the stop signal.

Each correct loop must still run untouched; each broken one must be stopped
(or get a note) with a report that says why.
"""
import io
import signal
import sys
import time

import pytest

from conftest import make_state
from loop_autopsy import loop_notes, monitor as monitor_module
from loop_autopsy.config import LoopStopped, Settings
from loop_autopsy.monitor import Monitor, _plain, _print_note, _read_attr


def run(shell, code):
    return shell.run_cell(code).error_in_exec


def kind(error):
    return error.finding.kind if isinstance(error, LoopStopped) else type(error).__name__ if error else None


@pytest.fixture
def limit_1s(shell):
    run(shell, "%autopsy config time_limit=1")
    yield shell
    run(shell, "%autopsy reset")


# --------------------------------------------------------------------------- comprehensions
def test_a_slow_generator_expression_gets_the_time_limit(limit_1s):
    code = ("import itertools, time\nt0 = time.time()\n"
            "total = sum(1 for _ in itertools.takewhile(lambda _: time.time() - t0 < 5, itertools.count()))")
    started = time.perf_counter()
    assert kind(run(limit_1s, code)) == "time_limit" and time.perf_counter() - started < 3


def test_a_comprehension_slow_on_each_item_gets_the_time_limit(limit_1s):
    code = "import time\nresult = [time.sleep(0.05) for _ in range(100)]"
    error = run(limit_1s, code)
    assert kind(error) == "time_limit" and "time.sleep(0.05)" in error.finding.explanation


@pytest.mark.parametrize("code", [
    "import itertools\nxs = [x for x in itertools.count()]",
    "import itertools\nxs = {x: 1 for x in itertools.cycle('ab')}",
    "import itertools\nxs = list(x * 2 for x in itertools.repeat(1))",
    "import itertools\nxs = [(a, b) for a in range(3) for b in itertools.count()]",
])
def test_a_comprehension_over_an_endless_iterator_is_stopped_before_it_starts(shell, code):
    started = time.perf_counter()
    error = run(shell, code)
    assert kind(error) == "endless" and "never runs out" in error.finding.explanation
    assert time.perf_counter() - started < 1


@pytest.mark.parametrize("code, expected", [
    ("result = [x * 2 for x in range(5)]", [0, 2, 4, 6, 8]),
    ("result = {k: v for k, v in zip('ab', [1, 2])}", {"a": 1, "b": 2}),
    ("result = sorted({x % 3 for x in range(10)})", [0, 1, 2]),
    ("import itertools\nresult = next(x for x in itertools.count() if x > 5)", 6),  # lazy: may stop early
    ("import itertools\nresult = any(x > 5 for x in itertools.count())", True),
    ("result = [[y for y in range(x)] for x in range(3)]", [[], [0], [0, 1]]),
    ("it = iter([1, 2, 3, 4])\nresult = [(a, next(it)) for a in it]", [(1, 2), (3, 4)]),
    ("class K:\n    items = [x * x for x in range(3)]\nresult = K.items", [0, 1, 4]),
])
def test_correct_comprehensions_give_the_same_result(shell, code, expected):
    assert run(shell, code) is None and shell.user_ns["result"] == expected


def test_a_comprehension_variable_in_a_condition_does_not_hide_a_stuck_loop(shell):
    error = run(shell, "vals = [1]\nwhile any(v > 0 for v in vals):\n    pass")
    assert kind(error) == "stuck"


def test_errors_inside_a_comprehension_are_unchanged(shell):
    error = run(shell, "result = [int(s) for s in ['1', 'x']]")
    assert isinstance(error, ValueError)


# --------------------------------------------------------------------------- loops in C: list(it), sum(it), ...
@pytest.mark.parametrize("code", [
    "import itertools\nresult = list(itertools.count())",
    "import itertools\nresult = max(itertools.cycle([1, 2]))",
    "import itertools, collections\nresult = collections.deque(itertools.count(), maxlen=3)",
    "def naturals():\n    n = 0\n    while True:\n        yield n\n        n += 1\nresult = sum(naturals())",
])
def test_a_builtin_over_an_endless_iterator_is_stopped(shell, code):
    assert kind(run(shell, code)) == "endless"


def test_an_iterator_with_no_end_in_sight_gets_the_time_limit(limit_1s):
    started = time.perf_counter()
    assert kind(run(limit_1s, "result = sum(iter(int, 1))")) == "time_limit"
    assert time.perf_counter() - started < 3


@pytest.mark.parametrize("code, expected", [
    ("result = min(3, 4)", 3),
    ("result = sorted(map(abs, [3, -1, 2]))", [1, 2, 3]),
    ("result = dict(zip('ab', range(2)))", {"a": 0, "b": 1}),
    ("result = ', '.join(str(x) for x in range(3))", "0, 1, 2"),
    ("import itertools\nresult = any(itertools.count())", True),  # any() may stop early
    ("def list(x):\n    return x\nit = iter([1])\nresult = list(it) is it\ndel list", True),  # not the built-in
    ("result = sum(range(5), 10)", 20),
])
def test_builtins_with_ordinary_arguments_are_unchanged(shell, code, expected):
    assert run(shell, code) is None and shell.user_ns["result"] == expected


def test_feed_hands_back_anything_that_is_not_an_iterator():
    monitor = Monitor(None, Settings())
    loop_id = monitor.register(make_state(kind="comprehension").info)
    data = [1, 2]
    assert monitor.feed(loop_id, list, data) is data and monitor.feed(loop_id, list, 5) == 5
    assert monitor.feed(loop_id, print, iter(data)) is not None  # not a known built-in: untouched
    assert monitor.watch(99, data) is data  # an unknown id runs unwatched


# --------------------------------------------------------------------------- async loops
def test_a_while_loop_with_await_gets_the_time_limit(limit_1s):
    code = "import asyncio, time\nt0 = time.time()\nwhile time.time() - t0 < 5:\n    await asyncio.sleep(0.01)"
    assert kind(run(limit_1s, code)) == "time_limit"


def test_async_for_over_an_endless_generator_gets_the_time_limit(limit_1s):
    code = ("import asyncio\nasync def ticks():\n    while True:\n        await asyncio.sleep(0.01)\n        yield 1\n"
            "n = 0\nasync for t in ticks():\n    n += t")
    assert kind(run(limit_1s, code)) == "time_limit"


def test_an_await_loop_ended_by_another_task_is_not_stuck(shell):
    code = ("import asyncio\ndone = False\nasync def finish():\n    global done\n    await asyncio.sleep(0.2)\n"
            "    done = True\ntask = asyncio.ensure_future(finish())\nwhile not done:\n    await asyncio.sleep(0.01)\n"
            "result = 'ended'")
    assert run(shell, code) is None and shell.user_ns["result"] == "ended"


# --------------------------------------------------------------------------- %run scripts
def test_a_stuck_loop_in_a_script_run_with_percent_run_is_stopped(shell, tmp_path, capsys):
    script = tmp_path / "spin.py"
    script.write_text("x = 1\nwhile x < 5:\n    pass\n")
    capsys.readouterr()
    run(shell, f"%run {script}")
    finding, state, _ = shell.user_ns["__loop_autopsy__"].last
    assert finding.kind == "stuck" and state.info.header == "while x < 5:"
    assert "Loop autopsy" in capsys.readouterr().out


def test_scripts_that_work_or_fail_behave_as_before(shell, tmp_path, capsys):
    good = tmp_path / "good.py"
    good.write_text("total = 0\nfor i in range(10):\n    total += i\n")
    run(shell, f"%run {good}")
    assert shell.user_ns["total"] == 45
    bad = tmp_path / "bad.py"
    bad.write_text("def f():\n    return 1 / 0\nf()\n")
    capsys.readouterr()
    run(shell, f"%run {bad}")
    assert "ZeroDivisionError" in capsys.readouterr().out  # the traceback is still shown
    leaving = tmp_path / "leaving.py"
    leaving.write_text("import sys\nsys.exit(0)\n")
    assert run(shell, f"%run {leaving}") is None


# --------------------------------------------------------------------------- conditions
@pytest.fixture
def strict(shell):
    run(shell, "%autopsy config direction_after=50")
    yield shell
    run(shell, "%autopsy reset")


@pytest.mark.parametrize("code, expected_kind", [
    ("i, done = 0, False\nwhile i < 10 and not done:\n    i -= 1", "wrong_direction"),
    ("a, b = 0, 0\nwhile a < 10 or b < 5:\n    a -= 1\n    b += 1", "wrong_direction"),
    ("class C:\n    def __init__(self):\n        self.n = 0\nc = C()\nwhile c.n < 5:\n    pass", "stuck"),
    ("class C:\n    def __init__(self):\n        self.n = 0\n    def run(self):\n        while self.n < 10:\n"
     "            self.n -= 1\nC().run()", "wrong_direction"),
    ("class Box:\n    def __init__(self):\n        self.items = [1]\nb = Box()\nwhile len(b.items) < 3:\n"
     "    b.items.append(1)\n    b.items.pop()", "stuck"),
    ("class Counter:\n    n = 0\nwhile Counter.n < 5:\n    pass", "stuck"),
    ("class S:\n    __slots__ = ('n',)\ns = S()\ns.n = 0\nwhile s.n < 5:\n    pass", "stuck"),
])
def test_conditions_with_and_or_and_attributes_are_followed(strict, code, expected_kind):
    assert kind(run(strict, code)) == expected_kind


@pytest.mark.parametrize("code, expected", [
    ("i, n = 5, 10\nwhile 0 <= i < n:\n    i -= 1\nresult = i", -1),  # moving away from one end is towards the other
    ("class Box:\n    def __init__(self):\n        self.items = []\nb = Box()\nwhile len(b.items) < 3:\n"
     "    b.items.append(1)\nresult = len(b.items)", 3),
    ("class P:\n    @property\n    def n(self):\n        return 0\np = P()\nk = 0\nwhile p.n < 5 and k < 20:\n"
     "    k += 1\nresult = k", 20),  # a property is never read: it could run any code
    ("i, flag = 0, True\nwhile i > -100 and flag:\n    i -= 1\n    if i < -60:\n        flag = False\nresult = i", -61),
    ("a, b = 0, 0\nwhile a < 10 or b < 5:\n    a += 1\n    b += 1\nresult = (a, b)", (10, 10)),
])
def test_correct_loops_with_compound_conditions_run_untouched(strict, code, expected):
    assert run(strict, code) is None and strict.user_ns["result"] == expected


def test_reading_attributes_never_runs_the_objects_code():
    class Thing:
        __slots__ = ("a",)

        @property
        def b(self):
            raise AssertionError("a property must not be read")

    thing = Thing()
    thing.a = 3
    assert _read_attr(thing, "a") == 3 and _read_attr(thing, "b") is monitor_module._MISSING
    assert _read_attr(thing, "missing") is monitor_module._MISSING


def test_numpy_numbers_are_read_as_plain_numbers():
    class Int64:  # what matters about a NumPy scalar: its module, ndim 0 and .item()
        __module__ = "numpy"
        ndim = 0

        def item(self):
            return 7

    Int64.__name__ = "int64"
    assert _plain(Int64()) == 7 and _plain(3) == 3 and _plain([1]) == [1]


def test_real_numpy_counters(strict):
    pytest.importorskip("numpy")
    assert kind(run(strict, "import numpy as np\ni = np.int64(0)\nwhile i < 10:\n    pass")) == "stuck"
    assert kind(run(strict, "import numpy as np\nx = np.float64(0)\nwhile x < 10:\n    x -= 1.0")) == "wrong_direction"


# --------------------------------------------------------------------------- growing lists and endless generators
def test_a_list_growing_under_enumerate_is_reported(shell):
    run(shell, "%autopsy config growth_after=200")
    try:
        error = run(shell, "nums = [1, 2, 3]\nfor k, x in enumerate(nums):\n    nums.append(x)")
        assert kind(error) == "growing_iterable"
    finally:
        run(shell, "%autopsy reset")


def test_a_for_loop_over_an_endless_generator_gets_a_note(limit_1s, capsys):
    capsys.readouterr()
    code = "def naturals():\n    n = 0\n    while True:\n        yield n\n        n += 1\nfor v in naturals():\n    pass"
    assert kind(run(limit_1s, code)) == "time_limit"
    assert "loops over `naturals()`, which never runs out" in capsys.readouterr().out


def test_only_generators_that_surely_never_finish_count_as_endless(shell):
    run(shell, "def upto(k):\n    n = 0\n    while True:\n        if n > k:\n            return\n        yield n\n"
               "        n += 1\ndef maybe(stop):\n    if stop:\n        return\n    while True:\n        yield 1\n"
               "def forever():\n    while True:\n        yield 1")
    ns = shell.user_ns
    assert loop_notes.endless_iterable(ns["upto"](3)) is None
    assert loop_notes.endless_iterable(ns["maybe"](True)) is None
    assert loop_notes.endless_iterable(ns["forever"]()) == "forever()"
    finished = ns["forever"]()
    finished.close()
    assert loop_notes.endless_iterable(finished) is None  # a closed generator is over
    assert loop_notes.endless_iterable(x for x in []) is None  # no source to read: no claim


# --------------------------------------------------------------------------- a round that never ends
def test_a_round_stuck_in_sleep_is_stopped_by_the_watchdog(limit_1s):
    started = time.perf_counter()
    error = run(limit_1s, "import time\nfor i in range(3):\n    time.sleep(10)")
    assert isinstance(error, KeyboardInterrupt) and time.perf_counter() - started < 6
    finding, state, _ = limit_1s.user_ns["__loop_autopsy__"].last
    assert finding.title == "loop stuck inside one round" and "time.sleep" in finding.explanation


def test_the_watchdog_leaves_loops_alone_that_make_progress(limit_1s):
    assert run(limit_1s, "import time\nfor i in range(5):\n    time.sleep(0.1)\nresult = i") is None


def test_the_watchdog_decides_only_on_a_quiet_loop_past_its_limit(monkeypatch):
    sent = []
    monkeypatch.setattr(signal, "pthread_kill", lambda *a: sent.append(a), raising=False)
    monkeypatch.setattr(signal, "raise_signal", lambda *a: sent.append(a))
    monkeypatch.setattr(signal, "getsignal", lambda sig: signal.default_int_handler)
    monitor = Monitor(None, Settings(time_limit=1))
    loop_id = monitor.register(make_state().info)
    monitor.begin(loop_id)
    state = monitor.states[loop_id]
    t0 = state.started
    assert monitor.watchdog_tick(now=t0 + 5) is None  # first look: remembers the round counter
    state.rounds += 1
    assert monitor.watchdog_tick(now=t0 + 6) is None  # a new round started: it is making progress
    monitor._waiting = 1
    assert monitor.watchdog_tick(now=t0 + 10) is None  # waiting for input(): not the loop's fault
    monitor._waiting = 0
    finding = monitor.watchdog_tick(now=t0 + 10)
    assert finding is not None and len(sent) == 1
    assert monitor.watchdog_tick(now=t0 + 20) is None and len(sent) == 1  # only once


# --------------------------------------------------------------------------- swallowed stops and other notes
@pytest.mark.parametrize("code, words", [
    ("k = 0\nwhile k < 3:\n    try:\n        k += 1\n    finally:\n        continue", "inside a `finally:` block"),
    ("from contextlib import suppress\nk = 0\nwhile k < 3:\n    with suppress(BaseException):\n        k += 1",
     "also swallows the stop button"),
    ("def f():\n    try:\n        for i in range(2):\n            pass\n    finally:\n        return 1\nf()",
     "whose `finally:` block has a `return`"),
    ("import contextlib\nwith contextlib.suppress(BaseException):\n    for i in range(2):\n        pass",
     "which also swallows loop-autopsy's stop signal"),
])
def test_code_that_swallows_the_stop_gets_a_note(shell, capsys, code, words):
    capsys.readouterr()
    assert run(shell, code) is None
    assert words in capsys.readouterr().out


def test_ordinary_finally_and_suppress_get_no_note(shell, capsys):
    capsys.readouterr()
    run(shell, "from contextlib import suppress\nfor i in range(2):\n    try:\n        pass\n    finally:\n"
               "        x = 1\n    with suppress(ValueError):\n        int('x')")
    assert "Note:" not in capsys.readouterr().out


def test_a_very_high_recursion_limit_gets_a_note(shell, capsys, monkeypatch):
    monkeypatch.setattr(monitor_module, "SAFE_RECURSION", 5000)
    old = sys.getrecursionlimit()
    monitor = shell.user_ns["__loop_autopsy__"]
    monitor.unguard_recursion_limit()
    monitor._noted_limit = False
    monitor.guard_recursion_limit()
    try:
        capsys.readouterr()
        run(shell, "import sys\nsys.setrecursionlimit(1_000_000)")
        assert "can crash the kernel" in capsys.readouterr().out
        assert sys.getrecursionlimit() == 1_000_000  # the limit is still set as asked
    finally:
        sys.setrecursionlimit(old)
        monitor.unguard_recursion_limit()
    assert not getattr(sys.setrecursionlimit, "__loop_autopsy_limit__", False)


def test_a_note_never_crashes_a_console_that_cannot_show_the_stop_sign(monkeypatch):
    raw = io.TextIOWrapper(io.BytesIO(), encoding="cp1252")
    monkeypatch.setattr(sys, "stdout", raw)
    _print_note("pressing the stop button ■ skips")
    raw.flush()
    assert raw.buffer.getvalue().decode("cp1252").strip() == "pressing the stop button skips"
