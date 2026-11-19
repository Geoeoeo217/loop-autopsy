"""Tests for heatmap.py"""
import io
from types import SimpleNamespace

from loop_autopsy.config import Settings
from loop_autopsy.heatmap import (CellHealth, CellRecord, DisplayGuard, OutputGuard, build_bar,
                                  build_summary, first_line, level_of, only_autopsy_commands,
                                  record_level, rerun_growth, should_show)

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


def test_output_guard_shows_the_last_muted_lines():
    out = io.StringIO()
    guard = OutputGuard(out, max_lines=3, tail_lines=4)
    guard.write("line 0\nline 1\nline 2\nline 3\nline 4\n")  # crosses the limit mid-write
    for i in range(5, 50):
        guard.write(f"line {i}\n")
    guard.write("no newline yet")
    assert "line 49" not in out.getvalue()  # nothing extra while the cell runs
    guard.flush_tail()
    text = out.getvalue()
    assert "48 lines were muted. The last 4:" in text
    assert text.endswith("line 47\nline 48\nline 49\nno newline yet\n")
    assert "line 46" not in text and "line 3\n" not in text
    guard.flush_tail()  # shown once only
    assert out.getvalue() == text


def test_output_guard_tail_when_few_lines_were_muted():
    out = io.StringIO()
    guard = OutputGuard(out, max_lines=2, tail_lines=20)
    for i in range(3):
        guard.write(f"x{i}\n")
    guard.flush_tail()
    assert "The muted line:\nx2\n" in out.getvalue()


def test_output_guard_tail_off_and_cleared_by_reset():
    out = io.StringIO()
    guard = OutputGuard(out, max_lines=1, tail_lines=0)
    for i in range(10):
        guard.write(f"y{i}\n")
    guard.flush_tail()
    assert "muted. The last" not in out.getvalue() and "y9" not in out.getvalue()
    out = io.StringIO()
    guard = OutputGuard(out, max_lines=1, tail_lines=5)
    for i in range(10):
        guard.write(f"old{i}\n")
    guard.reset()  # the output area was cleared: old muted lines are not wanted
    guard.flush_tail()
    assert "old9" not in out.getvalue()


def test_output_guard_caps_an_endless_line():
    guard = OutputGuard(io.StringIO(), max_lines=1, tail_lines=5)
    guard.write("first\n")
    for _ in range(10_000):
        guard.write("." * 10)  # print(end="") forever: no newline ever comes
    assert len(guard._partial) <= OutputGuard.MAX_PARTIAL


def test_tail_is_shown_when_the_cell_ends(monkeypatch):
    out = io.StringIO()
    monkeypatch.setattr("sys.stdout", out)
    health = CellHealth(None, Settings(max_output_lines=5, muted_tail=3, heatmap="off"),
                        {"loops": 0, "stopped": 0})
    health.pre_run_cell(SimpleNamespace(raw_cell="flood()", store_history=True))
    for i in range(100):
        print(f"value {i}")
    health.post_run_cell(SimpleNamespace(execution_count=1, error_in_exec=None, error_before_exec=None))
    text = out.getvalue()
    assert "95 lines were muted. The last 3:\nvalue 97\nvalue 98\nvalue 99\n" in text
    assert "value 96" not in text


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


def test_rerun_growth_needs_two_growing_reruns_in_a_row():
    def runs(*deltas, key=7):
        return [make_record(n, delta=d, key=key) for n, d in enumerate(deltas, 1)]

    assert rerun_growth(runs(500 * MB, 0, 0), 7, 10 * MB) is None  # loading data once is normal
    assert rerun_growth(runs(500 * MB, 50 * MB), 7, 10 * MB) is None  # one re-run is not enough
    assert rerun_growth(runs(0, 40 * MB, 60 * MB), 7, 10 * MB) == (2, 50 * MB)
    assert rerun_growth(runs(0, 40 * MB, 1 * MB, 20 * MB, 20 * MB, 20 * MB), 7, 10 * MB) == (3, 20 * MB)
    assert rerun_growth(runs(0, 40 * MB, 60 * MB, None), 7, 10 * MB) is None  # memory unknown
    assert rerun_growth(runs(0, 40 * MB, 60 * MB), 8, 10 * MB) is None  # another cell
    assert rerun_growth(runs(0, 40 * MB, 60 * MB), None, 10 * MB) is None
    assert rerun_growth(runs(0, 40 * MB, 60 * MB), 7, 0) is None  # setting is 0: off


def test_rerun_growth_in_bar_and_summary():
    settings = Settings()
    level, html, text = build_bar(0.1, 200 * MB, GB, 30 * MB, 0, 0, settings, rerun=(2, 30 * MB))
    assert level == 0 and "the last 2 times this cell ran again" in text and "30.0 MB" in text
    assert "pd.concat" in html and "restart the kernel" in html
    assert should_show("auto", 0, 30 * MB, rerun=True) and not should_show("off", 0, 0, rerun=True)

    records = [make_record(1, delta=0, key=1, source="df = pd.concat([df, new])"),
               make_record(2, delta=0, key=2),
               make_record(3, delta=30 * MB, key=1, source="df = pd.concat([df, new])"),
               make_record(4, delta=30 * MB, key=1, source="df = pd.concat([df, new])")]
    _, text = build_summary(records, settings, show_all=True)
    noted = [line for line in text.splitlines() if "each re-run" in line]
    assert len(noted) == 1 and noted[0].startswith("[4]") and "+30.0 MB each re-run" in noted[0]


def test_rerun_growth_is_warned_after_the_cell(monkeypatch):
    shown = []
    health = CellHealth(None, Settings(max_output_lines=0), {"loops": 0, "stopped": 0},
                        show=lambda text, html: shown.append(text))
    rss = iter([100 * MB, 100 * MB, 100 * MB, 150 * MB, 150 * MB, 200 * MB])
    monkeypatch.setattr("loop_autopsy.heatmap.current_rss", lambda: next(rss))
    monkeypatch.setattr("loop_autopsy.heatmap.memory_limit", lambda: 8 * GB)
    code = "data = data + [0] * 10_000_000"
    for n in (1, 2, 3):
        run_fake_cell(health, n, code)
    assert len(shown) == 1 and "the last 2 times this cell ran again" in shown[0]


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


# --------------------------------------------------------------------------- wider output guard
class FakePublisher:
    """Stands in for ``shell.display_pub``."""

    def __init__(self):
        self.shown, self.cleared = [], 0

    def publish(self, data=None, **kwargs):
        self.shown.append(data)

    def clear_output(self, wait=False):
        self.cleared += 1


def notices(pub):
    return [d for d in pub.shown if "Display output muted" in str(d)]


def test_output_guard_reset_and_release():
    out = io.StringIO()
    guard = OutputGuard(out, max_lines=2)
    for i in range(5):
        guard.write(f"a{i}\n")
    assert guard.muted
    guard.reset()  # the output area was cleared: count from zero again
    guard.write("b0\n")
    assert "b0" in out.getvalue() and not guard.muted
    guard.release()  # the cell is over: a kept reference must never mute again
    for i in range(10):
        guard.write(f"c{i}\n")
    assert "c9" in out.getvalue()


def test_display_guard_mutes_after_limit():
    pub = FakePublisher()
    guard = DisplayGuard(pub.publish, max_items=3)
    for i in range(10):
        guard(data={"text/plain": f"item {i}"})
    assert [d["text/plain"] for d in pub.shown[:3]] == ["item 0", "item 1", "item 2"]
    assert len(pub.shown) == 4 and len(notices(pub)) == 1 and guard.muted


def test_display_guard_lets_updates_and_reports_through():
    pub = FakePublisher()
    guard = DisplayGuard(pub.publish, max_items=2)
    for i in range(5):
        guard(data={"text/plain": "progress"}, update=True)  # replaces an output: never counted
    assert len(pub.shown) == 5 and not guard.muted
    for i in range(5):
        guard(data={"text/plain": "flood"})
    assert guard.muted
    with guard.unguarded():
        guard(data={"text/plain": "report"})
    assert pub.shown[-1] == {"text/plain": "report"}
    unlimited = DisplayGuard(pub.publish, max_items=0)
    for i in range(500):
        unlimited(data={"text/plain": "x"})
    assert not unlimited.muted


def test_stderr_is_guarded_and_restored(monkeypatch):
    err = io.StringIO()
    monkeypatch.setattr("sys.stderr", err)
    health = CellHealth(None, Settings(max_output_lines=3), {"loops": 0, "stopped": 0})
    health.pre_run_cell(SimpleNamespace(raw_cell="warn()", store_history=True))
    import sys
    kept = sys.stderr  # like a logging handler that stores the stream
    for i in range(10):
        print(f"warning {i}", file=sys.stderr)
    health.post_run_cell(SimpleNamespace(execution_count=1, error_in_exec=None, error_before_exec=None))
    assert sys.stderr is err
    text = err.getvalue()
    before, tail = text.split("[loop-autopsy] The muted lines:")  # the tail comes at the end
    assert "warning 2" in before and "warning 3" not in before and before.count("Output muted") == 1
    assert tail.strip().splitlines() == [f"warning {i}" for i in range(3, 10)]
    assert health.records[-1].muted
    kept.write("later\n")  # the old guard passes everything through after the cell
    assert "later" in err.getvalue()


def test_display_is_guarded_and_restored():
    pub = FakePublisher()
    shell = SimpleNamespace(display_pub=pub)
    health = CellHealth(shell, Settings(max_output_lines=0, max_displays=3), {"loops": 0, "stopped": 0})
    health.pre_run_cell(SimpleNamespace(raw_cell="show()", store_history=True))
    for i in range(10):
        pub.publish(data={"text/plain": f"item {i}"})
    assert len(pub.shown) == 4 and len(notices(pub)) == 1
    pub.clear_output(wait=True)  # an animation clears and redraws: the count starts again
    pub.publish(data={"text/plain": "after clear"})
    assert pub.cleared == 1 and pub.shown[-1] == {"text/plain": "after clear"}
    for i in range(10):
        pub.publish(data={"text/plain": "more"})
    health.post_run_cell(SimpleNamespace(execution_count=1, error_in_exec=None, error_before_exec=None))
    assert "publish" not in vars(pub) and "clear_output" not in vars(pub)  # the real methods are back
    assert health.records[-1].muted
    before = len(pub.shown)
    for i in range(10):
        pub.publish(data={"text/plain": "next cell"})
    assert len(pub.shown) == before + 10
