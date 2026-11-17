from loop_autopsy.report_i18n import MESSAGES, SUPPORTED_LANGUAGES
from loop_autopsy.report_theme import DARK_THEME
from loop_autopsy.sparkline import BLOCKS, sparkline_line, sparkline_svg, sparkline_text


def test_empty_and_non_numeric_give_nothing():
    assert sparkline_text([]) == ""
    assert sparkline_text(["a", None, True, float("nan")]) == ""
    assert sparkline_svg([]) == ""
    assert sparkline_line("x", []) == ""


def test_flat_values_draw_a_flat_line():
    assert len(set(sparkline_text([1, 1, 1]))) == 1


def test_wrong_direction_falls_while_target_is_high():
    path, target_block = sparkline_text([5, 4, 3, 2, 1], target=10).split(" \u2502 ")
    assert path[0] > path[-1]
    assert target_block == BLOCKS[-1]


def test_long_input_is_downsampled_and_keeps_ends():
    s = sparkline_text(list(range(1000)), width=20)
    assert len(s) == 20 and s[0] == BLOCKS[0] and s[-1] == BLOCKS[-1]


def test_line_label_follows_language():
    assert "(stop value 10)" in sparkline_line("i", [1, 2, 3], target=10)
    assert "(nilai berhenti 10)" in sparkline_line("i", [1, 2, 3], target=10, language="id")
    assert "stop value" not in sparkline_line("i", [1, 2, 3])


def test_svg_escaped_labelled_and_themed():
    svg = sparkline_svg([1, 2, 3], target=5, name='<b>"x"</b>', theme=DARK_THEME)
    assert svg.startswith("<svg") and svg.endswith("</svg>")
    assert "<b>" not in svg
    assert 'role="img"' in svg and "aria-label=" in svg
    assert DARK_THEME.muted in svg and "stroke-dasharray" in svg


def test_svg_single_point_has_no_polyline():
    svg = sparkline_svg([3], target=3)
    assert "polyline" not in svg and "<circle" in svg


def test_new_messages_exist_in_every_language():
    for lang in SUPPORTED_LANGUAGES:
        for key in ("stop_value", "sparkline_label", "sparkline_label_target"):
            assert key in MESSAGES[lang]
