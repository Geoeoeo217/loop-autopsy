"""Tiny charts for the loop-autopsy report: a variable's path against its stop value.

No dependencies. Two renderers share the same cleaning and scaling:

* ``sparkline_line`` / ``sparkline_text`` - Unicode blocks, for plain-text reports
* ``sparkline_svg``                       - inline SVG, for the HTML report

Labels come from ``report_i18n`` and colours from ``report_theme``, so the
chart follows the report's language and light/dark theme.
"""

from __future__ import annotations

import math
from html import escape

from .report_i18n import message
from .report_theme import ReportTheme

BLOCKS = "\u2581\u2582\u2583\u2584\u2585\u2586\u2587\u2588"  # ▁▂▃▄▅▆▇█
LINE_COLOR = "#d9534f"


def _is_number(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def _clean(values):
    """Keep only real, finite numbers (bool, NaN, inf, strings are dropped)."""
    try:
        return [float(v) for v in values if _is_number(v)]
    except TypeError:
        return []


def _downsample(vals, width):
    """Pick at most ``width`` evenly spaced points, always keeping first and last."""
    n = len(vals)
    if width < 2 or n <= width:
        return list(vals)
    step = (n - 1) / (width - 1)
    return [vals[round(i * step)] for i in range(width)]


def _fmt(x):
    x = float(x)
    if x.is_integer():
        return str(int(x))
    return f"{x:.6g}"


def _span(vals, target):
    pool = list(vals)
    if _is_number(target):
        pool.append(float(target))
    return min(pool), max(pool)


def _block(v, lo, hi):
    if hi == lo:
        return BLOCKS[len(BLOCKS) // 2]
    idx = int((v - lo) / (hi - lo) * (len(BLOCKS) - 1) + 0.5)
    return BLOCKS[max(0, min(len(BLOCKS) - 1, idx))]


def sparkline_text(values, target=None, width=24):
    """Return e.g. ``'▇▆▅▄▃▂▁ │ █'``: the path, then the level of the stop value.

    The stop value is part of the scale, so the gap between the last block
    and the block after the bar shows how far the variable is from finishing.
    Returns ``''`` when there is nothing numeric to draw.
    """
    vals = _clean(values)
    if not vals:
        return ""
    vals = _downsample(vals, width)
    lo, hi = _span(vals, target)
    line = "".join(_block(v, lo, hi) for v in vals)
    if _is_number(target):
        line += " \u2502 " + _block(float(target), lo, hi)
    return line


def sparkline_line(name, values, target=None, width=24, language="en"):
    """One report line: ``i  ▇▆▅▄▃▂▁ │ █  (stop value 10)``."""
    spark = sparkline_text(values, target, width)
    if not spark:
        return ""
    out = f"{name}  {spark}"
    if _is_number(target):
        out += f"  ({message('stop_value', language)} {_fmt(target)})"
    return out


def sparkline_svg(values, target=None, width=240, height=40, name="",
                  language="en", theme: ReportTheme | None = None):
    """Return an inline ``<svg>``: the path as a line, the stop value dashed.

    ``theme`` (a ``ReportTheme``) sets the dashed stop-value colour; without
    it ``currentColor`` is used, which also works in light and dark notebooks.
    Returns ``''`` when there is nothing numeric to draw.
    """
    vals = _clean(values)
    if not vals:
        return ""
    vals = _downsample(vals, max(2, width // 4))
    lo, hi = _span(vals, target)
    pad = 4
    w, h = width, height

    def y_of(v):
        if hi == lo:
            return h / 2
        return pad + (hi - v) / (hi - lo) * (h - 2 * pad)

    n = len(vals)
    xs = [pad + (w - 2 * pad) * (i / (n - 1) if n > 1 else 0.5) for i in range(n)]
    pts = " ".join(f"{x:.1f},{y_of(v):.1f}" for x, v in zip(xs, vals))

    has_target = _is_number(target)
    key = "sparkline_label_target" if has_target else "sparkline_label"
    label = message(
        key, language, name=name, start=_fmt(vals[0]), end=_fmt(vals[-1]),
        target=_fmt(target) if has_target else "",
    )
    target_color = escape(theme.muted, quote=True) if theme else "currentColor"

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" '
        f'viewBox="0 0 {w} {h}" role="img" aria-label="{escape(label, quote=True)}" '
        'style="vertical-align:middle">'
    ]
    if has_target:
        ty = y_of(float(target))
        parts.append(
            f'<line x1="{pad}" x2="{w - pad}" y1="{ty:.1f}" y2="{ty:.1f}" '
            f'stroke="{target_color}" stroke-opacity="0.8" stroke-dasharray="4 3"/>'
        )
    if n > 1:
        parts.append(
            f'<polyline points="{pts}" fill="none" stroke="{LINE_COLOR}" '
            'stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>'
        )
    parts.append(f'<circle cx="{xs[-1]:.1f}" cy="{y_of(vals[-1]):.1f}" r="3" fill="{LINE_COLOR}"/>')
    parts.append("</svg>")
    return "".join(parts)
