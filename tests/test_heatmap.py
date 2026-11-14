"""Tests for heatmap.py"""
import io
from types import SimpleNamespace

from loop_autopsy.config import Settings
from loop_autopsy.heatmap import (CellHealth, CellRecord, OutputGuard, build_bar, build_summary,
                                  first_line, level_of, only_autopsy_commands, record_level,
                                  should_show)

GB = 1024 ** 3
MB = 1024 ** 2


def make_record(number, elapsed=0.1, delta=0, **fields):
    defaults = dict(number=number, source=f"cell_{number}()", elapsed=elapsed, delta=delta,
                    rss=200 * MB, limit=GB)
    defaults.update(fields)
    return CellRecord(**defaults)


def run_fake_cell(health, number, raw_cell="x = 1", store_history=True, error=None):
    """Drive the pre/post hooks the way IPython does for one cell."""
    health.pre_run_cell(SimpleNamespace(raw_cell=raw_cell, store_history=store_history))
    health.post_run_cell(SimpleNamespace(execution_count=number, error_in_exec=error,
                                         error_before_exec=None))


def test_output_guard_mutes_after_limit():
    out = io.StringIO()
    guard = OutputGuard(out, max_lines=3)
    for i in range(10):
        guard.write(f"line {i}\n")
    text = out.getvalue()
    assert "line 2" in text and "line 3" not in text
    assert text.count("Output muted") == 1 and guard.muted


def test_output_guard_unlimited():
    out = io.StringIO()
    guard = OutputGuard(out, max_lines=0)
    for i in range(100):
        guard.write("x\n")
    assert out.getvalue().count("x") == 100


def test_levels():
    assert level_of(0.5, 1, 10) == 0 and level_of(2, 1, 10) == 1 and level_of(12, 1, 10) == 2


def test_bar_colours_and_text():
    settings = Settings()
    level, html, text = build_bar(0.1, int(0.2 * GB), GB, 1024, 1, 0, settings)
    assert level == 0 and "healthy" in text and "1 loop watched" in text
    level, _, _ = build_bar(0.1, int(0.6 * GB), GB, 0, 0, 0, settings)
    assert level == 1
    level, html, text = build_bar(15, int(0.9 * GB), GB, 0, 0, 0, settings,
                                  biggest=[("df_merged", int(0.7 * GB))])
    assert level == 2 and "Warning" in text and "df_merged" in text and "df_merged" in html


def test_should_show_modes():
    assert should_show("on", 0, 0) and not should_show("off", 2, 0)
    assert not should_show("auto", 0, 1024)
    assert should_show("auto", 1, 0)
    assert should_show("auto", 0, 200 * 1024 * 1024)


# --------------------------------------------------------------------------- session summary
def test_first_line_and_command_only_cells():
    assert first_line("\n\n  total = 0  \nfor i in range(3): pass") == "total = 0"
    assert first_line("x" * 100) == "x" * 37 + "..."
    assert first_line("") == ""
    assert only_autopsy_commands("%autopsy summary\n\n%autopsy last")
    assert not only_autopsy_commands("%autopsy heatmap on\nbig = [0] * 10")
    assert not only_autopsy_commands("%%autopsy off\nwhile True: pass")
    assert not only_autopsy_commands("")


def test_record_level_blames_only_the_cell_itself():
    settings = Settings()
    # Memory is nearly full, but this cell was quick and added nothing: still green.
    assert record_level(make_record(1, rss=int(0.95 * GB)), settings) == 0
    assert record_level(make_record(1, elapsed=2), settings) == 1
    assert record_level(make_record(1, elapsed=20), settings) == 2
    assert record_level(make_record(1, delta=int(0.10 * GB)), settings) == 1
    assert record_level(make_record(1, delta=int(0.30 * GB)), settings) == 2
    assert record_level(make_record(1, delta=-int(0.50 * GB)), settings) == 0  # freed memory
    assert record_level(make_record(1, delta=None, limit=None), settings) == 0


def test_summary_ranks_worst_cells_first():
    records = [make_record(1), make_record(2, elapsed=3), make_record(3, elapsed=30, stopped=1, loops=1),
               make_record(4, delta=int(0.3 * GB), outcome="error", muted=True), make_record(5, elapsed=0.5)]
    html, text = build_summary(records, Settings(), rss=512 * MB, limit=GB)
    assert "5 cells run" in text and "2 healthy, 1 watch out, 2 heavy" in text
    assert "memory now 512.0 MB / 1.0 GB (50%)" in text
    order = [line.split()[0] for line in text.splitlines() if line.startswith("[")]
    assert order == ["[3]", "[4]", "[2]", "[5]", "[1]"]  # by colour, then by time
    assert "1 stopped" in text and "error, output muted" in text
    assert html.count("display:inline-block") == 5 and "cell_3()" in html


def test_summary_top_ten_and_all():
    records = [make_record(n, elapsed=n / 100) for n in range(1, 16)]
    _, text = build_summary(records, Settings())
    rows = [line for line in text.splitlines() if line.startswith("[")]
    assert len(rows) == 10 and rows[0].startswith("[15]") and "10 of 15 shown" in text
    html, text = build_summary(records, Settings(), show_all=True)
    rows = [line.split()[0] for line in text.splitlines() if line.startswith("[")]
    assert rows == [f"[{n}]" for n in range(1, 16)]
    assert html.count("display:inline-block") == 15  # the strip always shows every cell


def test_summary_escapes_cell_source():
    html, _ = build_summary([make_record(1, source="print('<b>hi</b>')")], Settings())
    assert "<b>hi</b>" not in html and "&lt;b&gt;" in html


def test_cells_are_remembered():
    settings = Settings(max_output_lines=0)
    stats = {"loops": 0, "stopped": 0}
    health = CellHealth(None, settings, stats)
    run_fake_cell(health, 1, "a = 1")
    run_fake_cell(health, 2, "%autopsy summary")  # our own command: skipped
    run_fake_cell(health, 2, "hidden()", store_history=False)  # no [number]: skipped
    run_fake_cell(health, 3, "1 / 0", error=ZeroDivisionError())
    run_fake_cell(health, 4, "while True: pass", error=KeyboardInterrupt())
    assert [(r.number, r.source, r.outcome) for r in health.records] == [
        (1, "a = 1", "ok"), (3, "1 / 0", "error"), (4, "while True: pass", "interrupted")]


def test_history_limit_and_off(capsys):
    settings = Settings(max_output_lines=0, history_cells=3)
    health = CellHealth(None, settings, {"loops": 0, "stopped": 0})
    for n in range(1, 6):
        run_fake_cell(health, n)
    assert [r.number for r in health.records] == [3, 4, 5]
    settings.history_cells = 2  # changed while running: the newest cells are kept
    run_fake_cell(health, 6)
    assert [r.number for r in health.records] == [5, 6]
    health.summary("clear")
    assert not health.records
    settings.history_cells = 0
    run_fake_cell(health, 7)
    assert not health.records
    health.summary()
    assert "Cell history is off" in capsys.readouterr().out
