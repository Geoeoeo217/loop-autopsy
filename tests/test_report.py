"""Tests for report.py (owner: Teammate 5)."""
from conftest import make_state

from loop_autopsy.config import Finding
from loop_autopsy.report import (Size, interrupt_finding, record, render_html, render_text, replay_table,
                                 replay_value)


def test_replay_value_freezes_small_and_sizes_big():
    assert replay_value(3) == 3
    assert repr(replay_value([1, 2])) == "[1, 2]"
    assert replay_value(list(range(100))) == Size("list", 100)


def test_history_keeps_last_rows_and_finds_unchanged():
    state = make_state(watched=("x", "i"))
    for i in range(10):
        state.rounds = i + 1
        record(state, {"x": 1, "i": i})
    columns, rows, unchanged = replay_table(state)
    assert columns == ["x", "i"] and len(rows) == 6
    assert rows[-1] == ["10", "1", "9"] and unchanged == ["x"]


def test_text_and_html_reports():
    state = make_state()
    for r in range(1, 4):
        state.rounds = r
        record(state, {"x": 1})
    finding = Finding("stuck", "stuck loop", "`x` never changes <b>", "Change `x`.", ("x",))
    text = render_text(finding, state, elapsed=0.01)
    assert text.startswith("Loop autopsy — stuck loop (line 1)")
    assert "while x < 5:" in text and "x (same)" in text and "Hint: Change `x`." in text
    html = render_html(finding, state)
    assert "<code>x</code> never changes &lt;b&gt;" in html and "<table" in html


def test_interrupt_finding_names_frozen_condition():
    state = make_state()
    for r in range(1, 4):
        state.rounds = r
        record(state, {"x": 7})
    finding = interrupt_finding(state)
    assert finding.kind == "interrupt" and "`x` never changed" in finding.explanation
