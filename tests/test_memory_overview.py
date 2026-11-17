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


# --------------------------------------------------------------------------- output history
BIG = 200_000  # items: about 1.6 MB as a list of ints, above the 1 MB reporting threshold


def test_history_holders_finds_a_big_object_held_by_out_and_underscore_names():
    big = list(range(1000, 1000 + BIG))
    ns = {"Out": {3: big, 4: [1, 2]}, "_3": big, "_": big, "_4": [1, 2], "x": 1}
    [(labels, size, value, users)] = growth.output_history_holders(ns)
    assert labels == ["Out[3]", "_3", "_"] and value is big and users == []
    assert size == growth.size_of(big) >= 1 << 20


def test_history_holders_names_the_variable_that_still_uses_the_object():
    big = list(range(1000, 1000 + BIG))
    ns = {"Out": {1: big}, "_1": big, "keep": big, "_hidden": big}
    [(_, _, _, users)] = growth.output_history_holders(ns)
    assert users == ["keep"]


def test_history_holders_sorted_limited_and_ignores_small_outputs():
    a, b, c = (list(range(1000, 1000 + n)) for n in (BIG, 3 * BIG, 2 * BIG))
    tiny = list(range(10))
    ns = {"Out": {1: a, 2: b, 3: c, 4: tiny, 5: None}}
    assert [v is x for (_, _, v, _), x in zip(growth.output_history_holders(ns), (b, c, a))] == [True] * 3
    assert len(growth.output_history_holders(ns, top=2)) == 2
    assert growth.output_history_holders(ns, min_bytes=10 ** 9) == []
    assert growth.output_history_holders({"Out": {1: tiny}, "_": tiny}) == []
    assert growth.output_history_holders({}) == []
    assert growth.output_history_holders({"Out": "not a dict"}) == []


def test_overview_lists_what_the_history_holds(monkeypatch):
    _fix_memory(monkeypatch, 300 * 1024 ** 2, 2 * 1024 ** 3)
    big = list(range(1000, 1000 + BIG))
    text = growth.memory_overview({"Out": {7: big}, "_7": big, "keep": big, "small": [1]})
    assert "Kept alive by the output history" in text
    assert "Out[7] = _7" in text and "`keep`" in text
    assert "%reset -f out" in text


def test_overview_reports_history_even_when_no_variable_is_big(monkeypatch):
    _fix_memory(monkeypatch, 300 * 1024 ** 2, 2 * 1024 ** 3)
    big = list(range(1000, 1000 + BIG))
    text = growth.memory_overview({"Out": {2: big}, "_2": big})
    assert "No variables to show yet." not in text
    assert "no variable" in text and "variable     size" not in text


def test_overview_says_nothing_about_history_when_it_holds_little(monkeypatch):
    _fix_memory(monkeypatch, 300 * 1024 ** 2, 2 * 1024 ** 3)
    text = growth.memory_overview({"Out": {1: [1, 2, 3]}, "_1": [1, 2, 3], "a": [4, 5]})
    assert "output history" not in text and "reset" not in text


def test_deleted_variable_is_still_held_until_the_history_is_reset(shell, capsys):
    import gc

    def run(code):
        return shell.run_cell(code, store_history=True)

    run("%reset -f out")  # earlier tests may have left outputs behind
    run("import weakref\n"
        "class Blob:\n"
        "    def __init__(self):\n"
        "        self.payload = [float(i) for i in range(200_000)]\n")
    run("blob = Blob()\nblob_ref = weakref.ref(blob)\nblob")  # a cell that shows the object
    run("del blob")
    for n in range(4):
        run(f"filler{n} = {n}\nfiller{n}")  # push it out of _, __ and ___
    gc.collect()
    assert shell.user_ns["blob_ref"]() is not None  # the history still holds it

    capsys.readouterr()
    run("%autopsy memory")
    text = capsys.readouterr().out
    assert "Kept alive by the output history" in text and "Blob" in text and "no variable" in text

    run("%reset -f out")  # the advice the report gives
    gc.collect()
    assert shell.user_ns["blob_ref"]() is None  # now it is really gone
    capsys.readouterr()
    run("%autopsy memory")
    assert "output history" not in capsys.readouterr().out
