"""Turn a Finding and a loop's history into a readable report.

``record`` is called on every check, so it must stay fast: it stores values,
not text, and converts containers to a small size marker.
"""

from __future__ import annotations

import html
from decimal import Decimal
from fractions import Fraction
from typing import Any, Dict, List, Optional, Tuple

from .config import Finding, LoopState

_IMMUTABLE = (type(None), bool, int, float, complex, str, bytes, Fraction, Decimal, range)
ACCENT = {"stuck": "#d9480f", "cycle": "#d9480f", "wrong_direction": "#d9480f", "jumped_over": "#d9480f",
          "growing_iterable": "#c2255c", "memory": "#c2255c", "time_limit": "#e67700", "interrupt": "#1971c2",
          "recursion": "#7048e8", "recursion_stuck": "#7048e8", "recursion_cycle": "#7048e8",
          "recursion_past_base": "#7048e8", "recursion_wrong_way": "#7048e8", "recursion_too_deep": "#7048e8"}
_MISSING = object()


def _is_recursion(state: LoopState) -> bool:
    return state.info.kind == "recursion"


def _step_word(state: LoopState) -> str:
    """Column heading for the replay table: rounds for loops, calls for recursion."""
    return "call" if _is_recursion(state) else "round"


def _table_title(state: LoopState) -> str:
    return "First and last calls:" if _is_recursion(state) else "Last rounds checked:"


class Text(str):
    """repr of a small container, frozen at the time of the check."""

    def __repr__(self):
        return str(self)


class Size:
    """Marker stored in the history instead of a (mutable) container."""

    __slots__ = ("kind", "length")

    def __init__(self, kind: str, length: int):
        self.kind, self.length = kind, length

    def __eq__(self, other):
        return isinstance(other, Size) and (self.kind, self.length) == (other.kind, other.length)

    def __hash__(self):
        return hash((self.kind, self.length))

    def __repr__(self):
        return f"{self.kind}(len={self.length})" if self.length >= 0 else f"<{self.kind}>"


def replay_value(value: Any) -> Any:
    """Cheap snapshot of one value for the replay table."""
    if isinstance(value, _IMMUTABLE):
        return value
    if isinstance(value, tuple) and len(value) <= 10 and all(isinstance(x, _IMMUTABLE) for x in value):
        return value
    try:
        if isinstance(value, (list, dict, set, tuple)) and len(value) <= 8:
            text = repr(value)
            if len(text) <= 40:
                return Text(text)
        return Size(type(value).__name__, len(value))
    except Exception:
        return Size(type(value).__name__, -1)


def record(state: LoopState, values: Dict[str, Any]) -> None:
    """Append this check to the loop's replay history."""
    state.history.append((state.rounds, {n: replay_value(v) for n, v in values.items()}))


def short_repr(value: Any, width: int = 40) -> str:
    text = repr(value)
    return text if len(text) <= width else text[: width - 3] + "..."


# --------------------------------------------------------------------------- table
def _same(a: Any, b: Any) -> bool:
    try:
        return type(a) is type(b) and bool(a == b)
    except Exception:
        return False


def replay_table(state: LoopState) -> Tuple[List[str], List[List[str]], List[str]]:
    """Return (columns, rows, unchanged_columns). Rows start with the round number."""
    order = list(state.info.watched)
    present: List[str] = []
    for _, snap in state.history:
        for name in snap:
            if name not in present:
                present.append(name)
    columns = [n for n in order if n in present] + [n for n in present if n not in order]
    rows = [[f"{rnd:,}"] + [short_repr(snap[c]) if c in snap else "-" for c in columns]
            for rnd, snap in state.history]
    unchanged = []
    if len(state.history) >= 2:
        for c in columns:
            seen = [snap.get(c, _MISSING) for _, snap in state.history]
            if seen[0] is not _MISSING and all(_same(seen[0], v) for v in seen[1:]):
                unchanged.append(c)
    return columns, rows, unchanged


def _footer(state: LoopState, elapsed: Optional[float]) -> str:
    if _is_recursion(state):
        return f"Stopped by Python after {state.rounds:,} nested calls  ·  %autopsy last = show again"
    t = f" in {elapsed:.2f} s" if elapsed is not None else ""
    return f"Stopped after {state.rounds:,} rounds{t}  ·  %autopsy last = show again  ·  %%autopsy off = skip a cell"


# --------------------------------------------------------------------------- text
def _text_table(columns: List[str], rows: List[List[str]], unchanged: List[str], step: str = "round") -> str:
    head = [step] +[c + (" (same)" if c in unchanged else "") for c in columns]
    widths = [max([len(head[i])] + [len(r[i]) for r in rows]) for i in range(len(head))]
    line = lambda cells: "  ".join(c.ljust(w) for c, w in zip(cells, widths)).rstrip()  # noqa: E731
    return "\n".join([line(head), line(["-" * w for w in widths])] + [line(r) for r in rows])


def render_text(finding: Finding, state: LoopState, elapsed: Optional[float] = None) -> str:
    columns, rows, unchanged = replay_table(state)
    return "\n".join([
        f"Loop autopsy — {finding.title} (line {state.info.lineno})",
        f"    {state.info.header}",
        "",
        finding.explanation,
        "",
        _table_title(state),
        _text_table(columns, rows, unchanged, _step_word(state)) if columns else "(no variables to show)",
        "",
        "Hint: " + finding.hint,
        _footer(state, elapsed),
    ])


# --------------------------------------------------------------------------- html
def _md_code(text: str) -> str:
    """Escape, then turn `backticks` into <code>."""
    out, inside = [], False
    for piece in html.escape(text).split("`"):
        out.append(f"<code>{piece}</code>" if inside else piece)
        inside = not inside
    return "".join(out)


def _html_table(columns, rows, unchanged, culprits, step: str = "round") -> str:
    if not columns:
        return "<p><i>(no variables to show)</i></p>"
    th = "padding:2px 10px;text-align:left;border-bottom:1px solid rgba(128,128,128,.4)"
    head = f"<th style='{th}'>{step}</th>"
    for c in columns:
        mark = " <span style='font-weight:normal;opacity:.7'>(same)</span>" if c in unchanged else ""
        style = th + (";color:#d9480f" if c in culprits else "")
        head += f"<th style='{style}'><code>{html.escape(c)}</code>{mark}</th>"
    body = "".join(
        "<tr>" + "".join(f"<td style='padding:2px 10px;font-family:monospace'>{html.escape(x)}</td>" for x in r) + "</tr>"
        for r in rows
    )
    return f"<table style='border-collapse:collapse;margin:6px 0'><tr>{head}</tr>{body}</table>"


def render_html(finding: Finding, state: LoopState, elapsed: Optional[float] = None) -> str:
    columns, rows, unchanged = replay_table(state)
    color = ACCENT.get(finding.kind, "#d9480f")
    return (
        f"<div style='border-left:4px solid {color};padding:8px 12px;margin:6px 0;"
        f"background:rgba(128,128,128,.07);border-radius:4px;line-height:1.45'>"
        f"<div style='font-weight:bold;color:{color}'>Loop autopsy — {html.escape(finding.title)} "
        f"(line {state.info.lineno})</div>"
        f"<div style='margin:4px 0'><code>{html.escape(state.info.header)}</code></div>"
        f"<p style='margin:6px 0'>{_md_code(finding.explanation)}</p>"
        f"<div style='opacity:.8;font-size:90%'>{_table_title(state)}</div>"
        f"{_html_table(columns, rows, unchanged, set(finding.culprits), _step_word(state))}"
        f"<p style='margin:6px 0'><b>Hint:</b> {_md_code(finding.hint)}</p>"
        f"<div style='opacity:.65;font-size:85%'>{html.escape(_footer(state, elapsed))}</div></div>"
    )


# --------------------------------------------------------------------------- interrupt (stop button)
def interrupt_finding(state: LoopState) -> Finding:
    """What we can say when the student pressed stop themselves."""
    _, _, unchanged = replay_table(state)
    cond = [n for n in state.info.cond_names if n in unchanged]
    hint = "Compare the replay table with the loop's stop condition: are the values moving towards it?"
    if cond:
        names = ", ".join(f"`{n}`" for n in cond)
        text = (f"You stopped this loop. In the last rounds checked, {names} never changed, "
                f"so `{state.info.test_src or 'the condition'}` could not become False.")
        hint = f"Find the line inside the loop that should change {names}."
    elif unchanged:
        names = ", ".join(f"`{n}`" for n in unchanged)
        text = f"You stopped this loop. In the last rounds checked, {names} did not change."
    else:
        text = "You stopped this loop. Here is what its variables were doing just before."
    return Finding("interrupt", "you stopped this loop", text, hint, tuple(cond))


def display_report(text: str, html_text: str) -> None:
    """HTML inside Jupyter, plain text in terminal IPython or plain Python."""
    try:
        from IPython import get_ipython
        from IPython.display import HTML, display

        shell = get_ipython()
        if shell is not None and shell.__class__.__name__ == "ZMQInteractiveShell":
            display(HTML(html_text))
            return
    except Exception:
        pass
    import sys

    stream = getattr(sys.stdout, "raw_stream", sys.stdout)  # skip the output guard
    print(text, file=stream)
