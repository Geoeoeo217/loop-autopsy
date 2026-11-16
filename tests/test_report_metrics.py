from types import SimpleNamespace

from loop_autopsy.report_metrics import (
    changed_variables,
    format_summary,
    history_summary,
)


def make_state(history, rounds=3):
    return SimpleNamespace(history=history, rounds=rounds)


def test_history_summary_counts_snapshots_and_variables():
    state = make_state([
        (1, {"x": 1, "y": 2}),
        (2, {"x": 2, "y": 2}),
    ])

    assert history_summary(state) == {
        "snapshots": 2,
        "variables": 2,
        "rounds": 3,
    }


def test_changed_variables_finds_only_values_that_changed():
    state = make_state([
        (1, {"x": 1, "y": 2}),
        (2, {"x": 2, "y": 2}),
        (3, {"x": 3, "y": 2}),
    ])

    assert changed_variables(state) == ["x"]


def test_changed_variables_detects_missing_values():
    state = make_state([
        (1, {"x": 1}),
        (2, {"y": 2}),
    ])

    assert changed_variables(state) == ["x", "y"]


def test_format_summary_is_compact_and_readable():
    state = make_state([(1, {"x": 1}), (2, {"x": 2})], rounds=12)

    assert format_summary(state) == "2 snapshots, 1 variables, 12 rounds"
