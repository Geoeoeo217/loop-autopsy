"""Tests for heatmap.py"""
import io

from loop_autopsy.config import Settings
from loop_autopsy.heatmap import OutputGuard, build_bar, level_of, should_show

GB = 1024 ** 3


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
