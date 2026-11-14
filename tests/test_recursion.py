"""Tests for recursion.py: explaining runaway recursion."""
import pytest

from loop_autopsy.recursion import analyse, argument_names, build_state
from loop_autopsy.report import render_html, render_text


def calls_of(values, name="n"):
    return [{name: v} for v in values]


# --------------------------------------------------------------------------- analyse (pure)
def test_same_arguments_every_call():
    finding = analyse("countdown", calls_of([5] * 900))
    assert finding.kind == "recursion_stuck" and finding.culprits == ("n",)
    assert "900 nested calls" in finding.explanation and "`n = 5`" in finding.explanation


def test_no_arguments():
    finding = analyse("loop", [{}] * 900)
    assert finding.kind == "recursion_stuck" and "takes no arguments" in finding.explanation


def test_went_past_base_case():
    finding = analyse("countdown", calls_of(range(5, -995, -2)))
    assert finding.kind == "recursion_past_base"
    assert "from 5 to -993" in finding.explanation and "`n <= 0` is safer" in finding.hint


def test_wrong_way():
    finding = analyse("countdown", calls_of(range(5, 1000)))
    assert finding.kind == "recursion_wrong_way" and "further from 0" in finding.explanation
    assert "`n + 1` instead of `n - 1`" in finding.hint


def test_too_deep_but_maybe_correct():
    finding = analyse("total", calls_of(range(100_000, 99_000, -1)), limit=1000)
    assert finding.kind == "recursion_too_deep" and "may be correct" in finding.explanation
    assert "loop" in finding.hint


def test_cycle_of_arguments():
    finding = analyse("bounce", calls_of([1, 2, 3] * 300))
    assert finding.kind == "recursion_cycle" and "every 3 calls" in finding.explanation


def test_list_length_is_followed():
    finding = analyse("grow", calls_of([[0] * k for k in range(1, 900)], name="items"))
    assert finding.kind == "recursion_wrong_way" and "Length of `items`" in finding.explanation
    assert "`items[1:]`" in finding.hint and "items + 1" not in finding.hint


def test_mutual_recursion_names_both_functions():
    finding = analyse("is_even", calls_of(range(-1, -900, -2)), companions=("is_odd",))
    assert "`is_even` and `is_odd` kept calling each other" in finding.explanation


def test_unreadable_arguments_fall_back():
    finding = analyse("walk", calls_of([object() for _ in range(900)], name="node"))
    assert finding.kind == "recursion" and "first and last calls" in finding.explanation


# --------------------------------------------------------------------------- report
def test_report_shows_first_and_last_calls():
    def countdown(n, step=1):
        return countdown(n - step)

    calls = [{"n": 5 - i, "step": 1} for i in range(990)]
    state = build_state(countdown.__code__, calls, replay_rows=6, max_watched=25)
    assert argument_names(countdown.__code__) == ("n", "step")
    assert [rnd for rnd, _ in state.history] == [1, 2, 3, 988, 989, 990]
    finding = analyse("countdown", calls)
    text = render_text(finding, state)
    assert "def countdown(n, step):" in text and "First and last calls:" in text
    header = next(line for line in text.splitlines() if line.startswith(("call", "round")))
    assert header.split()[:3] == ["call", "n", "step"] and "990 nested calls" in text
    assert "First and last calls:" in render_html(finding, state)


# --------------------------------------------------------------------------- end to end
def run(shell, code):
    return shell.run_cell(code, store_history=True).error_in_exec


@pytest.mark.parametrize("code, kind", [
    ("def f(n):\n    return f(n)\nf(5)", "recursion_stuck"),
    ("def countdown(n):\n    if n == 0:\n        return 0\n    return countdown(n - 2)\ncountdown(5)",
     "recursion_past_base"),
    ("def countdown(n):\n    if n == 0:\n        return 0\n    return countdown(n + 1)\ncountdown(5)",
     "recursion_wrong_way"),
    ("def total(n):\n    if n == 0:\n        return 0\n    return n + total(n - 1)\ntotal(100000)",
     "recursion_too_deep"),
])
def test_recursion_is_explained(shell, code, kind, capsys):
    error = run(shell, code)
    assert isinstance(error, RecursionError)
    out = capsys.readouterr().out
    assert "Loop autopsy" in out and "First and last calls:" in out
    assert shell.user_ns["__loop_autopsy__"].last[0].kind == kind


def test_mutual_recursion_end_to_end(shell, capsys):
    run(shell, "def is_even(n):\n    return True if n == 0 else is_odd(n - 1)\n"
               "def is_odd(n):\n    return False if n == 0 else is_even(n - 1)\nis_even(-1)")
    out = capsys.readouterr().out
    assert "kept calling each other" in out


def test_caught_recursion_error_is_left_alone(shell, capsys):
    code = "def f(n):\n    return f(n)\ntry:\n    f(1)\nexcept RecursionError:\n    caught = True\n"
    assert run(shell, code) is None and shell.user_ns["caught"] is True
    assert "Loop autopsy" not in capsys.readouterr().out


def test_off_shows_normal_traceback_and_last_works(shell, capsys):
    run(shell, "%autopsy off")
    capsys.readouterr()
    run(shell, "def g(n):\n    return g(n)\ng(1)")
    out = capsys.readouterr().out
    assert "Loop autopsy" not in out and "RecursionError" in out
    run(shell, "%autopsy on")
    run(shell, "def h(n):\n    return h(n + 1)\nh(1)")
    capsys.readouterr()
    run(shell, "%autopsy last")
    assert "recursion going the wrong way" in capsys.readouterr().out
