"""Notes read from a loop's code (loop_notes.py): an update line after the loop,
an update inside an ``if`` that never runs, an endless iterator, and a
catch-all ``except``."""
import ast
import itertools

import pytest

from conftest import make_state
from loop_autopsy import loop_notes
from loop_autopsy.config import LoopStopped, Settings
from loop_autopsy.detectors import check_repeat, check_time
from loop_autopsy.monitor import Monitor


def facts(code):
    """``{line of the loop: LoopFacts}`` for a cell."""
    tree = ast.parse(code)
    by_id = loop_notes.analyse(tree)
    return {node.lineno: by_id[id(node)] for node in ast.walk(tree) if id(node) in by_id}


def update_of(code):
    found = facts(code)
    return next((f.outside_update for f in found.values() if f.outside_update), None)


def notes_of(code):
    return {line: f.notes for line, f in facts(code).items() if f.notes}


# --------------------------------------------------------------------------- 1. update after the loop
@pytest.mark.parametrize("code, expected", [
    ("count = 1\nwhile count <= 5:\n    print(count)\ncount += 1\n", ("count", 4, "count += 1")),
    ("i = 0\nwhile i < 10:\n    total = i\ni = i + 1\n", ("i", 4, "i = i + 1")),
    ("lo, hi = 0, 9\nwhile lo < hi:\n    mid = 4\nlo, hi = mid, hi\n", ("lo", 4, ast.unparse(ast.parse("lo, hi = mid, hi")))),
    ("def f(n):\n    while n > 0:\n        print(n)\n    n -= 1\n", ("n", 4, "n -= 1")),
    ("for row in range(3):\n    j = 0\n    while j < 3:\n        print(row)\n    j += 1\n", ("j", 5, "j += 1")),
])
def test_update_after_the_loop_is_found(code, expected):
    assert update_of(code) == expected

@pytest.mark.parametrize("code", [
    "count = 1\nwhile count <= 5:\n    count += 1\ncount += 1\n",  # changed inside too: the loop is fine
    "x = 0\nwhile x < 5:\n    print(x)\n    break\nx += 1\n",  # has a break: may end on purpose
    "x = 0\nwhile x < 5:\n    if x > 9:\n        raise ValueError\nx += 1\n",  # has a raise
    "c = 1\nwhile c <= 5:\n    bump()\nc += 1\n",  # bump() might change c with `global`
    "c = 1\nwhile c <= 5:\n    print(c)\nd = 1\n",  # the next line does not change the condition
    "c = 1\nwhile c <= 5:\n    print(c)\nprint(c)\nc += 1\n",  # only the very next line counts
    "while True:\n    pass\nx = 1\n",  # nothing to change
    "items = [1]\nwhile items:\n    items.pop()\nitems = [2]\n",  # pop() changes items
    "box = [0]\nwhile box[0] < 3:\n    box[0] += 1\nbox = [9]\n",  # changed through a subscript
    "n = 3\nwhile (n := n - 1) > 0:\n    print(n)\nn = 3\n",  # changed in the condition itself
    "n = 3\nwhile n > 0:\n    print(n)\nn: int\n",  # an annotation alone assigns nothing
])
def test_no_update_hint_when_the_loop_may_be_right(code):
    assert update_of(code) is None


def test_stuck_report_points_at_the_line_after_the_loop():
    state = make_state(outside_update=("x", 4, "x += 1"))
    for values in ({"x": 1}, {"x": 1}, {"x": 1}):
        state.rounds += 1
        state.checks += 1
        finding = check_repeat(state.info, state, values)
    assert finding.kind == "stuck"
    assert "Line 4 (`x += 1`) changes `x`, but it comes after the loop" in finding.hint
    assert "indent it" in finding.hint


def test_time_limit_report_keeps_its_hint_and_adds_the_indent_hint():
    state = make_state(outside_update=("x", 4, "x += 1"), repeat_safe=False)
    hint = check_time(state.info, state, Settings(time_limit=5), now=6.0).hint
    assert hint.startswith("Line 4 (`x += 1`)") and "time_limit=25" in hint
    plain = make_state(repeat_safe=False)
    assert "Line" not in check_time(plain.info, plain, Settings(time_limit=5), now=6.0).hint


def test_long_update_line_is_shortened():
    code = "v = 0\nwhile v < 5:\n    print(v)\nv = " + " + ".join(["v"] * 30) + "\n"
    name, line, src = update_of(code)
    assert (name, line) == ("v", 4) and len(src) == 60 and src.endswith("...")


# --------------------------------------------------------------------------- 2. endless iterators
def test_endless_iterables():
    assert loop_notes.endless_iterable(itertools.count()) == "itertools.count"
    assert loop_notes.endless_iterable(itertools.cycle("ab")) == "itertools.cycle"
    assert loop_notes.endless_iterable(itertools.repeat(1)) == "itertools.repeat"
    assert loop_notes.endless_iterable(itertools.repeat(1, 3)) is None  # stops after 3
    for finite in ([1, 2], range(5), iter([1]), zip(itertools.count(), "ab"), (x for x in "ab")):
        assert loop_notes.endless_iterable(finite) is None


def test_endless_iterator_note_text():
    note = loop_notes.endless_iterator_note("for i in count():", 2, "itertools.count", 60)
    assert "(line 2)" in note and "never runs out" in note and "60 s time limit" in note
    assert "press ■ to stop it" in loop_notes.endless_iterator_note("for i in count():", 2, "itertools.count", 0)


def _for_loop(monitor, exits=False):
    return monitor.register(make_state(kind="for", header="for i in count():", test_src="", cond_names=(),
                                       compare=None, repeat_safe=False, exits=exits).info)


def test_endless_iterator_note_is_shown_on_the_first_round(capsys):
    monitor = Monitor(None, Settings())
    loop_id = _for_loop(monitor)
    monitor.begin(loop_id)
    monitor.sized(loop_id, itertools.count())
    assert capsys.readouterr().out == ""  # nothing yet: the loop has not done a round
    monitor.due(loop_id)
    monitor.check(loop_id, {})
    assert "never runs out" in capsys.readouterr().out


def test_endless_iterator_note_once_across_runs(capsys):
    monitor = Monitor(None, Settings())
    loop_id = _for_loop(monitor)
    for _ in range(3):  # e.g. a function that runs the loop three times
        monitor.begin(loop_id)
        monitor.sized(loop_id, itertools.count())
        monitor.due(loop_id)
        monitor.check(loop_id, {})
    assert capsys.readouterr().out.count("never runs out") == 1


def test_no_endless_note_for_loops_that_can_end(capsys):
    monitor = Monitor(None, Settings())
    with_break = _for_loop(monitor, exits=True)
    plain = _for_loop(monitor)
    for loop_id, iterable in ((with_break, itertools.count()), (plain, range(3)), (plain, itertools.repeat(1, 2))):
        monitor.begin(loop_id)
        monitor.sized(loop_id, iterable)
        monitor.due(loop_id)
        monitor.check(loop_id, {})
    assert capsys.readouterr().out == ""
    monitor.enabled = False
    monitor.begin(plain)
    monitor.sized(plain, itertools.count())
    assert "endless_note" not in monitor.states[plain].scratch


# --------------------------------------------------------------------------- 3. catch-all except
def test_bare_except_inside_a_loop():
    code = "while True:\n    try:\n        x = int(input())\n    except:\n        print('again')\n"
    notes = notes_of(code)
    assert list(notes) == [1]
    assert "`except:` on line 4, inside this loop, also catches the stop button ■" in notes[1][0]
    assert "except Exception:" in notes[1][0]


def test_bare_except_around_a_loop():
    code = "try:\n    k = 1\n    while k < 2:\n        pass\nexcept BaseException:\n    pass\n"
    notes = notes_of(code)
    assert list(notes) == [3]
    assert "runs inside a `try` whose `except BaseException:` (line 5)" in notes[3][0]
    assert "%autopsy last" in notes[3][0]


def test_catch_all_note_goes_to_one_loop_only():
    # The try sits in the outer loop and wraps the inner one: one note, on the outer loop.
    code = ("for a in range(3):\n    try:\n        while a < 5:\n            pass\n"
            "    except (ValueError, BaseException):\n        pass\n")
    assert list(notes_of(code)) == [1]
    # Around two loops: on the first one only.
    code = "try:\n    for a in range(2):\n        pass\n    while True:\n        pass\nexcept:\n    pass\n"
    assert list(notes_of(code)) == [2]


@pytest.mark.parametrize("code", [
    "while True:\n    try:\n        pass\n    except Exception:\n        pass\n",  # lets ■ through
    "while True:\n    try:\n        pass\n    except ValueError:\n        pass\n",
    "while True:\n    try:\n        pass\n    except KeyboardInterrupt:\n        pass\n",  # on purpose
    "while True:\n    try:\n        pass\n    except:\n        raise\n",  # the stop goes on
    "while True:\n    try:\n        pass\n    except:\n        break\n",  # ■ still leaves the loop
    "try:\n    while True:\n        pass\nexcept:\n    print('x')\n    raise\n",
    "try:\n    x = 1\nexcept:\n    pass\n",  # no loop at all
    "try:\n    run()\nexcept:\n    pass\n",  # the loop, if any, is in another cell
    "try:\n    def f():\n        while True:\n            pass\nexcept:\n    pass\n",  # defined, not run here
    "while True:\n    def g():\n        try:\n            pass\n        except:\n            pass\n    break\n",
])
def test_no_catch_all_note(code):
    assert notes_of(code) == {}


def test_catch_all_inside_if_with_and_match_blocks():
    code = "while True:\n    if True:\n        with open('f') as fh:\n            try:\n                pass\n" \
           "            except:\n                pass\n"
    assert list(notes_of(code)) == [1]
    code = "try:\n    if True:\n        for a in 'ab':\n            pass\nexcept:\n    pass\n"
    assert list(notes_of(code)) == [3]


# --------------------------------------------------------------------------- in a real IPython shell
def test_indentation_mistake_in_a_shell(shell, capsys):
    capsys.readouterr()
    error = shell.run_cell("count = 1\nwhile count <= 5:\n    print(count)\ncount += 1\n").error_in_exec
    out = capsys.readouterr().out
    assert isinstance(error, LoopStopped) and error.finding.kind == "stuck"
    assert "Hint: Line 4 (`count += 1`) changes `count`, but it comes after the loop" in out


def test_endless_iterator_in_a_shell(shell, capsys):
    shell.run_cell("%autopsy config time_limit=0.3")
    capsys.readouterr()
    shell.run_cell("import itertools\nfor i in itertools.count():\n    pass\n")
    out = capsys.readouterr().out
    assert out.index("loops over `itertools.count`") < out.index("loop ran too long")
    shell.run_cell("for i in itertools.count():\n    if i == 5:\n        break\n")
    assert "never runs out" not in capsys.readouterr().out
    shell.run_cell("for i in itertools.cycle([]):\n    pass\n")
    assert capsys.readouterr().out == ""


def test_swallowed_stop_in_a_shell(shell, capsys):
    capsys.readouterr()
    result = shell.run_cell("try:\n    k = 1\n    while k < 2:\n        pass\nexcept:\n    print('swallowed')\n")
    out = capsys.readouterr().out
    assert result.error_in_exec is None and "swallowed" in out
    assert "also catches loop-autopsy's stop signal" in out and "Loop autopsy" not in out
    shell.run_cell("%autopsy last")
    assert "stuck loop" in capsys.readouterr().out  # the hidden report is still there


def test_catch_all_note_in_a_correct_loop_in_a_shell(shell, capsys):
    capsys.readouterr()
    shell.run_cell("n = 0\nwhile n < 3:\n    try:\n        n += 1\n    except:\n        pass\n")
    out = capsys.readouterr().out
    assert out.count("also catches the stop button") == 1 and shell.user_ns["n"] == 3


# --------------------------------------------------------------------------- 1b. update in an if that never runs
def stuck_hint(shell, code):
    error = shell.run_cell(code).error_in_exec
    assert isinstance(error, LoopStopped) and error.finding.kind == "stuck", code
    return error.finding.hint


@pytest.mark.parametrize("code, expected", [
    ("i, y = 0, 2\nwhile i < 10:\n    if y > 3:\n        i += 1\n",
     "Line 4 (`i += 1`) changes `i`, but it never runs: it is inside `if y > 3:` (line 3), "
     "and `y > 3` is False every round (`y = 2` never changes)."),
    ("i, done = 0, True\nwhile i < 10:\n    if done:\n        print('waiting')\n    else:\n        i += 1\n",
     "it is in the `else` part of `if done:` (line 3), and `done` is True every round "
     "(`done = True` never changes), so that part is always skipped."),
    ("i = 1\nwhile i < 10:\n    if i % 2 == 0:\n        i += 1\n",  # i only changes after its own check
     "inside `if i % 2 == 0:` (line 3), and `i % 2 == 0` is False every round (`i = 1` never changes)."),
    ("i, a, b = 0, 1, 5\nwhile i < 10:\n    if a > 0:\n        if b < 3:\n            i += 1\n",
     "inside `if b < 3:` (line 4), and `b < 3` is False every round"),
    ("i, mode = 0, 'a'\nwhile i < 10:\n    if mode == 'x':\n        pass\n    elif mode == 'b':\n        i += 1\n",
     "inside `elif mode == 'b':` (line 5)"),
    ("i, mode = 0, 'a'\nwhile i < 10:\n    if mode == 'a':\n        pass\n    elif mode == 'b':\n        i += 1\n",
     "in the `elif` part of `if mode == 'a':` (line 3), and `mode == 'a'` is True every round"),
    ("n, nums = 0, []\nwhile n < 3:\n    if len(nums) > 0:\n        n += 1\n",
     "`len(nums) > 0` is False every round (`nums = []` never changes)"),
])
def test_update_in_an_if_that_never_runs(shell, code, expected):
    hint = stuck_hint(shell, code)
    assert expected in hint, hint
    assert "Check that condition, or move the line out of the `if`" in hint


def test_every_line_that_changes_the_variable_must_be_skipped(shell):
    hint = stuck_hint(shell, "i, m = 0, 'a'\nwhile i < 9:\n    if m == 'b':\n        i += 1\n"
                             "    if m == 'c':\n        i += 2\n")
    assert hint.startswith("Line 4 (`i += 1`)") and "The other lines that change it are skipped" in hint


@pytest.mark.parametrize("code", [
    "i, y = 0, 5\nwhile i < 10:\n    if y > 3:\n        i += 0\n",  # the if runs; the update does nothing
    "i, y = 0, 2\nwhile i < 10:\n    t = y\n    if t > 3:\n        i += 1\n",  # t is set earlier in the round
    "i, y = 0, 2\nwhile i < 10:\n    i = i\n    if y > 3:\n        i += 1\n",  # also changed outside the if
    "i, y = 0, 2\nwhile i < 10:\n    if y > 3:\n        for _ in range(1):\n            i += 1\n",  # in a loop
    "i, y = 0, 2\nwhile i < 10:\n    if y > 3:\n        i += 1\n    else:\n        i += 0\n",  # else runs
    "i, y = 0, 2\nwhile i < 10:\n    if y.real > 3:\n        i += 1\n",  # attributes are not worked out
])
def test_no_branch_hint_unless_the_branch_surely_never_runs(shell, code):
    assert "never runs" not in stuck_hint(shell, code)


def test_no_branch_updates_when_the_loop_calls_user_code():
    found = facts("i = 0\nwhile i < 9:\n    if ok():\n        i += 1\n")
    assert not found or not found[2].branch_updates
    assert not facts("i = 0\nwhile i < 9:\n    bump()\n    if y:\n        i += 1\n")


def test_conditions_are_only_worked_out_on_built_in_values():
    class Sneaky:
        def __gt__(self, other):
            raise AssertionError("user code ran")

    assert loop_notes._evaluate("y > 3", {"y": Sneaky()}) is None
    assert loop_notes._evaluate("y > 3", {}) is None  # not watched
    assert loop_notes._evaluate("y[5] > 3", {"y": [1]}) is None  # the error is caught
    assert loop_notes._evaluate("len(s) == 2 and s[0] == 'a'", {"s": "ab"}) is True
    assert not loop_notes._evaluable(ast.parse("f(y) > 3", mode="eval").body)
    assert not loop_notes._evaluable(ast.parse("(z := y) > 3", mode="eval").body)


def test_branch_hint_needs_the_condition_to_be_false():
    updates = loop_notes.branch_updates(ast.parse("while i < 9:\n    if y > 3:\n        i += 1\n").body[0])
    assert [(u.name, u.line) for u in updates] == [("i", 3)]
    assert loop_notes.branch_hint(updates, {"i": 0, "y": 2}) is not None
    assert loop_notes.branch_hint(updates, {"i": 0, "y": 7}) is None  # the if would run
