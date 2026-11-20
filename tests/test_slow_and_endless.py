"""Tests for the "would take too long" rule (R8) and the note for a
``while True:`` loop with no way out."""
import ast
from collections import deque

from conftest import make_state

from loop_autopsy.config import Settings
from loop_autopsy.detectors import check_estimate, check_time, duration
from loop_autopsy.monitor import Monitor, endless_note
from loop_autopsy.transformer import LoopTransformer, always_true, has_exit


def transform(code):
    infos = []

    def register(info):
        info.loop_id = len(infos)
        infos.append(info)
        return info.loop_id

    tree = LoopTransformer(register).visit(ast.parse(code))
    return infos, ast.unparse(tree)


def for_state(total, rounds, exits=False, **fields):
    state = make_state(kind="for", header="for row in data:", test_src="", cond_names=("data",),
                       watched=("data", "row"), compare=None, exits=exits, **fields)
    state.total, state.rounds = total, rounds
    return state


# --------------------------------------------------------------------------- static analysis
def test_has_exit():
    body = lambda code: ast.parse(code).body  # noqa: E731
    assert not has_exit(body("x += 1\nprint(x)"))
    assert has_exit(body("if x > 3:\n    break"))
    assert has_exit(body("try:\n    work()\nexcept ValueError:\n    break"))
    assert has_exit(body("return x")) and has_exit(body("raise StopIteration"))
    assert has_exit(body("assert x < 10")) and has_exit(body("exit()")) and has_exit(body("sys.exit(1)"))
    # A break inside a nested loop only ends that loop...
    assert not has_exit(body("for i in range(3):\n    break"))
    assert not has_exit(body("while y:\n    break"))
    # ...but its else block, and return / raise inside it, belong to the outer loop too.
    assert has_exit(body("for i in range(3):\n    pass\nelse:\n    break"))
    assert has_exit(body("for i in range(3):\n    return i"))
    # A function defined in the loop does not run as part of it.
    assert not has_exit(body("def stop():\n    return 1\nx = 2"))


def test_always_true():
    expr = lambda code: ast.parse(code, mode="eval").body  # noqa: E731
    assert always_true(expr("True")) and always_true(expr("1"))
    assert not always_true(expr("False")) and not always_true(expr("x")) and not always_true(expr("0"))


def test_transformer_marks_endless_while_loops():
    infos, _ = transform("while True:\n    step()\n")
    assert infos[0].always_true and not infos[0].exits
    infos, _ = transform("while True:\n    if done():\n        break\n")
    assert infos[0].always_true and infos[0].exits
    infos, _ = transform("while x < 3:\n    x += 1\n")
    assert not infos[0].always_true


def test_for_iterable_is_measured_without_changing_the_loop():
    infos, code = transform("for n in nums:\n    total += n\n")
    assert "for n in __loop_autopsy__.sized(0, nums):" in code
    assert infos[0].header == "for n in nums:" and infos[0].iter_name == "nums"
    assert "__loop_autopsy__" not in infos[0].watched
    _, code = transform("for i, c in enumerate(word, 1):\n    pass\n")
    assert "enumerate(__loop_autopsy__.sized(0, word), 1)" in code
    infos, code = transform("for a in xs:\n    for b in ys:\n        pass\n")
    assert "sized(0, ys)" in code and "sized(1, xs)" in code
    assert infos[1].repeat_safe is False and "ys" in infos[1].watched  # outer loop still sees ys


def test_measured_loop_runs_exactly_as_before():
    monitor = Monitor(None, Settings())
    tree = LoopTransformer(monitor.register).visit(ast.parse(
        "out = []\nfor i, c in enumerate('abc'):\n    out.append((i, c))\n"
        "gen = []\nfor v in (x * 2 for x in range(3)):\n    gen.append(v)\n"))
    namespace = {"__loop_autopsy__": monitor}
    exec(compile(tree, "<cell>", "exec"), namespace)
    assert namespace["out"] == [(0, "a"), (1, "b"), (2, "c")] and namespace["gen"] == [0, 2, 4]


# --------------------------------------------------------------------------- monitor
def test_sized_records_the_length_and_returns_the_same_object():
    monitor = Monitor(None, Settings())
    loop_id = monitor.register(for_state(None, 0).info)
    for iterable, total in [(range(10), 10), ([1, 2], 2), ("abc", 3), ({"a": 1}.items(), 1),
                            (deque([1]), 1), (iter([1, 2]), None), ((x for x in "ab"), None),
                            (zip("ab", "cd"), None), (range(10 ** 30), None)]:
        monitor.begin(loop_id)
        assert monitor.sized(loop_id, iterable) is iterable
        assert monitor.states[loop_id].total == total, iterable


def test_sized_skips_a_dataframe_like_object():
    DataFrame = type("DataFrame", (), {"__len__": lambda self: 5, "__module__": "pandas.core.frame"})
    Series = type("Series", (), {"__len__": lambda self: 5, "__module__": "pandas.core.series"})
    monitor = Monitor(None, Settings())
    loop_id = monitor.register(for_state(None, 0).info)
    monitor.begin(loop_id)
    monitor.sized(loop_id, DataFrame())  # a DataFrame loops over its column names, not its rows
    assert monitor.states[loop_id].total is None
    monitor.sized(loop_id, Series())
    assert monitor.states[loop_id].total == 5


def test_endless_note_is_shown_once(capsys):
    monitor = Monitor(None, Settings())
    endless = monitor.register(make_state(header="while True:", test_src="True", cond_names=(),
                                          compare=None, always_true=True, exits=False).info)
    with_break = monitor.register(make_state(header="while True:", always_true=True, exits=True).info)
    for _ in range(3):
        monitor.begin(endless)
        monitor.begin(with_break)
    out = capsys.readouterr().out
    assert out.count("has no `break`, `return` or `raise`") == 1 and "while True:" in out
    monitor.enabled = False
    monitor.register(make_state(always_true=True, exits=False).info)
    monitor.begin(2)
    assert capsys.readouterr().out == ""
    assert "(line 1)" in endless_note(monitor.infos[endless])


# --------------------------------------------------------------------------- rule R8
def test_duration():
    assert duration(45) == "45 s" and duration(600) == "10 min"
    assert duration(5 * 3600) == "5.0 hours" and duration(10 * 86400) == "10 days"


def test_slow_for_loop_is_stopped_with_an_estimate():
    settings = Settings()  # time_limit 60, estimate_after 5
    state = for_state(total=1_000_000, rounds=1_001)  # 1,000 rounds finished
    assert check_estimate(state.info, state, settings, now=4.0) is None  # too early to judge
    finding = check_estimate(state.info, state, settings, now=6.0)
    assert finding.kind == "too_slow" and finding.title == "loop would take too long"
    assert "finished 1,000 of its 1,000,000 rounds (0.1%)" in finding.explanation
    assert "about 1.7 hours" in finding.explanation
    assert "time_limit=7500" in finding.hint and "%%autopsy off" in finding.hint


def test_estimate_leaves_loops_that_will_finish_or_can_end_early():
    settings = Settings()
    assert check_estimate(*_args(for_state(100, 51), settings, 6.0)) is None  # needs 12 s: fine
    assert check_estimate(*_args(for_state(10_000, 101), settings, 6.0)) is not None  # 600 s: stopped
    assert check_estimate(*_args(for_state(1_000, 101), settings, 6.0)) is None  # 60 s: under twice the limit
    assert check_estimate(*_args(for_state(10 ** 6, 11, exits=True), settings, 6.0)) is None  # may break early
    assert check_estimate(*_args(for_state(None, 11), settings, 6.0)) is None  # length unknown
    assert check_estimate(*_args(for_state(10, 50), settings, 6.0)) is None  # the list grew
    assert check_estimate(*_args(for_state(10 ** 6, 1), settings, 6.0)) is None  # no round finished yet
    assert check_estimate(*_args(for_state(10 ** 6, 11, waits_for_user=True), settings, 6.0)) is None
    assert check_estimate(*_args(make_state(), settings, 6.0)) is None  # while loops: not this rule
    assert check_estimate(*_args(for_state(10 ** 6, 11), Settings(estimate_after=0), 6.0)) is None
    assert check_estimate(*_args(for_state(10 ** 6, 11), Settings(time_limit=0), 6.0)) is None


def _args(state, settings, now):
    return state.info, state, settings, now


def test_time_limit_report_shows_progress_and_endless_loops():
    settings = Settings(time_limit=5)
    state = for_state(total=400, rounds=101, exits=True)
    text = check_time(state.info, state, settings, now=6.0).explanation
    assert "finished 100 of its 400 rounds (25.0%)" in text and "about 24 s" in text
    endless = make_state(header="while True:", test_src="True", always_true=True, exits=False)
    assert "could not end on its own" in check_time(endless.info, endless, settings, now=6.0).explanation


# --------------------------------------------------------------------------- in a real IPython shell
def test_slow_loop_in_a_shell(shell, capsys):
    shell.run_cell("%autopsy config time_limit=2 estimate_after=0.2")
    capsys.readouterr()
    shell.run_cell("import time\nfor i in range(1000):\n    time.sleep(0.01)\n")
    out = capsys.readouterr().out
    assert "loop would take too long" in out and "of its 1,000 rounds" in out
    assert shell.user_ns["i"] < 100  # stopped long before the 2 s time limit


def test_slow_loop_with_a_break_gets_the_time_limit(shell, capsys):
    shell.run_cell("%autopsy config time_limit=0.5 estimate_after=0.1")
    capsys.readouterr()
    shell.run_cell("import time\nfor i in range(1000):\n    time.sleep(0.01)\n    if i < 0:\n        break\n")
    out = capsys.readouterr().out
    assert "loop ran too long" in out and "of its 1,000 rounds" in out


def test_endless_note_in_a_shell(shell, capsys):
    capsys.readouterr()
    shell.run_cell("while True:\n    pass\n")
    out = capsys.readouterr().out
    assert out.index("has no `break`") < out.index("Loop autopsy")
    shell.run_cell("n = 0\nwhile True:\n    n += 1\n    if n == 3:\n        break\n")
    assert "has no `break`" not in capsys.readouterr().out
