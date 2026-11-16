"""Tests for ``%autopsy memory`` (growth.memory_overview and the magic that calls it)."""
from loop_autopsy import growth


def _fix_memory(monkeypatch, rss, limit):
    monkeypatch.setattr(growth, "current_rss", lambda: rss)
    monkeypatch.setattr(growth, "memory_limit", lambda refresh=False: limit)


def test_overview_shows_total_and_biggest_first(monkeypatch):
    _fix_memory(monkeypatch, 512 * 1024 ** 2, 2 * 1024 ** 3)
    ns = {"small": [1, 2], "big": list(range(100_000)), "label": "x"}
    text = growth.memory_overview(ns, top=2)
    assert text.splitlines()[0] == "Memory: 512.0 MB of 2.0 GB (25%)"
    assert "list (len 100,000)" in text
    assert text.index("big") < text.index("small")
    assert "label" not in text  # only the 2 biggest are listed


def test_overview_hides_underscore_names_modules_and_functions(monkeypatch):
    _fix_memory(monkeypatch, 100 * 1024 ** 2, 1024 ** 3)
    import os

    ns = {"_hidden": list(range(10 ** 5)), "os": os, "f": print, "kept": [1, 2, 3]}
    text = growth.memory_overview(ns)
    assert "kept" in text
    assert "_hidden" not in text and " os " not in text and "\nf " not in text


def test_overview_without_variables(monkeypatch):
    _fix_memory(monkeypatch, 100 * 1024 ** 2, 1024 ** 3)
    assert "No variables to show yet." in growth.memory_overview({"_x": 1})


def test_overview_when_memory_cannot_be_measured(monkeypatch):
    _fix_memory(monkeypatch, None, None)
    assert growth.memory_overview({"a": [1]}).startswith("Memory: cannot be measured")
    _fix_memory(monkeypatch, 64 * 1024 ** 2, None)
    assert growth.memory_overview({"a": [1]}).startswith("Memory: 64.0 MB (limit unknown)")


def test_magic_end_to_end(shell, capsys):
    shell.run_cell("biggest = list(range(200_000))\nsmall = [1]")
    capsys.readouterr()
    shell.run_cell("%autopsy memory 1")
    out = capsys.readouterr().out
    assert "Memory:" in out and "biggest" in out and "small" not in out


def test_magic_rejects_a_bad_number(shell, capsys):
    capsys.readouterr()
    shell.run_cell("%autopsy memory lots")
    assert "Usage: %autopsy memory" in capsys.readouterr().out


def test_help_mentions_memory(shell, capsys):
    capsys.readouterr()
    shell.run_cell("%autopsy help")
    assert "%autopsy memory" in capsys.readouterr().out
