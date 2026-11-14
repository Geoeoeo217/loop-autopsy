"""Cell health bar, session summary and output guard.

After each cell a thin coloured bar shows time, memory and loops watched.
In ``auto`` mode it only appears when something is worth seeing.

Every cell is also remembered, so ``%autopsy summary`` can show the whole
session at once: a strip of coloured squares (the heatmap) and a table of the
heaviest cells.
"""

from __future__ import annotations

import html
import re
import sys
import time
from collections import deque
from dataclasses import dataclass
from typing import Any, Callable, Deque, Dict, Iterable, List, Optional, Tuple

from .config import Settings
from .growth import current_rss, format_bytes, largest_variables, memory_limit

COLORS = ("#2b8a3e", "#e67700", "#c92a2a")  # green, yellow, red
LABELS = ("healthy", "watch out", "heavy")
BIG_GROWTH = 100 * 1024 * 1024  # 100 MB
SOURCE_WIDTH = 40  # characters of a cell's first line kept for the summary
TOP_CELLS = 10  # rows in the "worst cells" table
_ONLY_AUTOPSY = re.compile(r"\s*%autopsy\b")  # a line that is just our own line magic


# --------------------------------------------------------------------------- output guard
class OutputGuard:
    """Wraps sys.stdout and mutes it after ``max_lines`` printed lines."""

    NOTICE = ("\n[loop-autopsy] Output muted after {n:,} lines to keep your browser responsive. "
              "The cell is still running; a stuck loop will still be stopped.\n")

    def __init__(self, stream, max_lines: int):
        self._stream = stream
        self.raw_stream = stream  # reports write here so they are never muted
        self.max_lines = max_lines
        self.lines = 0
        self.muted = False

    def write(self, text: str) -> int:
        if self.muted:
            return len(text)
        newlines = text.count("\n")
        if self.max_lines and self.lines + newlines >= self.max_lines:
            keep = self.max_lines - self.lines
            cut = 0
            for _ in range(keep):
                cut = text.index("\n", cut) + 1
            self._stream.write(text[:cut])
            self._stream.write(self.NOTICE.format(n=self.max_lines))
            self.lines = self.max_lines
            self.muted = True
            return len(text)
        self.lines += newlines
        return self._stream.write(text)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._stream, name)


# --------------------------------------------------------------------------- bar
def level_of(value: float, yellow: float, red: float) -> int:
    return 2 if value >= red else 1 if value >= yellow else 0


def build_bar(elapsed: float, rss: Optional[int], limit: Optional[int], delta: Optional[int],
              loops: int, stopped: int, settings: Settings,
              biggest: Optional[List[Tuple[str, int]]] = None) -> Tuple[int, str, str]:
    """Return (level, html, text) for one cell. Pure function: easy to test."""
    frac = (rss / limit) if rss and limit else None
    level = level_of(elapsed, settings.time_yellow, settings.time_red)
    if frac is not None:
        level = max(level, level_of(frac, settings.mem_yellow, settings.mem_red))
    color = COLORS[level]

    bits = [f"time {elapsed:.2f} s"]
    if frac is not None:
        bits.append(f"memory {format_bytes(rss)} / {format_bytes(limit)} ({frac:.0%})")
    if delta is not None:
        sign = "+" if delta >= 0 else "-"
        bits.append(f"{sign}{format_bytes(abs(delta))} this cell")
    if loops:
        bits.append(f"{loops} loop{'s' if loops != 1 else ''} watched")
    if stopped:
        bits.append(f"{stopped} stopped")
    text = f"[{LABELS[level]}] " + " | ".join(bits)

    warn_text, warn_html = "", ""
    if frac is not None and frac >= settings.memory_warn:
        names = ", ".join(f"{n} ({format_bytes(b)})" for n, b in (biggest or []))
        warn_text = f"\nWarning: memory is at {frac:.0%} of your limit." + (f" Biggest variables: {names}." if names else "")
        listed = ", ".join(f"<code>{n}</code> ({format_bytes(b)})" for n, b in (biggest or []))
        warn_html = (f"<div style='color:{COLORS[2]};margin-top:3px'><b>Warning:</b> memory is at {frac:.0%} of "
                     f"your limit.{' Biggest variables: ' + listed + '.' if listed else ''} "
                     f"Delete what you no longer need with <code>del name</code>.</div>")

    fill = min(100, int((frac or 0) * 100))
    html_text = (
        f"<div style='font-size:80%;opacity:.9;margin:2px 0;line-height:1.3'>"
        f"<div style='height:4px;border-radius:2px;background:rgba(128,128,128,.25);margin-bottom:2px'>"
        f"<div style='height:4px;width:{max(fill, 2)}%;border-radius:2px;background:{color}'></div></div>"
        f"<span style='color:{color};font-weight:bold'>{LABELS[level]}</span> &middot; "
        + " &middot; ".join(bits) + f"{warn_html}</div>"
    )
    return level, html_text, text + warn_text


def should_show(mode: str, level: int, delta: Optional[int]) -> bool:
    """``auto`` shows the bar only when the cell was slow, memory is high or grew a lot.
    (A stopped loop already shows its own report, so it does not trigger the bar.)"""
    if mode == "on":
        return True
    if mode == "off":
        return False
    return level > 0 or (delta is not None and delta >= BIG_GROWTH)


# --------------------------------------------------------------------------- session summary
@dataclass
class CellRecord:
    """What one execution of a cell cost. Stored for ``%autopsy summary``."""

    number: Optional[int]  # the [12] shown beside the cell
    source: str  # first line of the cell, shortened
    elapsed: float
    delta: Optional[int]  # memory this cell added (bytes); negative if it freed memory
    rss: Optional[int]  # total memory after the cell
    limit: Optional[int]
    loops: int = 0
    stopped: int = 0
    outcome: str = "ok"  # ok | error | interrupted
    muted: bool = False  # the output guard cut this cell's output


def first_line(raw_cell: str, width: int = SOURCE_WIDTH) -> str:
    """First non-blank line of a cell, shortened to ``width`` characters."""
    for line in raw_cell.splitlines():
        line = line.strip()
        if line:
            return line if len(line) <= width else line[: width - 3] + "..."
    return ""


def only_autopsy_commands(raw_cell: str) -> bool:
    """True for a cell that holds nothing but ``%autopsy ...`` lines. Such cells
    are about the tool, not the student's work, so they are not remembered."""
    lines = [line for line in raw_cell.splitlines() if line.strip()]
    return bool(lines) and all(_ONLY_AUTOPSY.match(line) for line in lines)


def record_level(record: CellRecord, settings: Settings) -> int:
    """Colour of one cell in the summary: 0 green, 1 yellow, 2 red.

    Unlike the health bar, this only blames a cell for what it did itself: how
    long it ran and how much memory it added (as a fraction of the limit).
    """
    level = level_of(record.elapsed, settings.time_yellow, settings.time_red)
    if record.delta is not None and record.limit and record.delta > 0:
        level = max(level, level_of(record.delta / record.limit, settings.grow_yellow, settings.grow_red))
    return level


def _delta_text(delta: Optional[int]) -> str:
    if delta is None:
        return "?"
    return ("+" if delta >= 0 else "-") + format_bytes(abs(delta))


def _notes(record: CellRecord) -> str:
    notes = []
    if record.stopped:
        notes.append(f"{record.stopped} stopped")
    if record.outcome != "ok":
        notes.append(record.outcome)
    if record.muted:
        notes.append("output muted")
    return ", ".join(notes)


def _cell_label(record: CellRecord) -> str:
    return f"[{record.number}]" if record.number is not None else "[?]"


def build_summary(records: Iterable[CellRecord], settings: Settings, show_all: bool = False,
                  rss: Optional[int] = None, limit: Optional[int] = None) -> Tuple[str, str]:
    """Return (html, text) for the whole session. Pure function: easy to test.

    ``records`` are in run order. The table shows the ``TOP_CELLS`` worst cells
    (by colour, then time) or, with ``show_all``, every cell in run order.
    ``rss`` and ``limit`` describe memory right now, for the totals line.
    """
    records = list(records)
    levels = [record_level(r, settings) for r in records]
    counts = [levels.count(i) for i in range(3)]

    totals = [f"{len(records)} cell{'s' if len(records) != 1 else ''} run",
              ", ".join(f"{counts[i]} {LABELS[i]}" for i in range(3)),
              f"total time {sum(r.elapsed for r in records):.2f} s"]
    if rss and limit:
        totals.append(f"memory now {format_bytes(rss)} / {format_bytes(limit)} ({rss / limit:.0%})")

    order = list(range(len(records)))
    if show_all:
        heading = "Every cell, in the order it ran:"
    else:
        order.sort(key=lambda i: (-levels[i], -records[i].elapsed))
        order = order[:TOP_CELLS]
        heading = "Worst cells first:"
        if len(records) > TOP_CELLS:
            heading = (f"Worst cells first ({TOP_CELLS} of {len(records)} shown; "
                       f"%autopsy summary all = every cell):")

    head = ["cell", "health", "time", "memory", "total after", "loops", "notes", "first line"]
    rows = [[_cell_label(r), LABELS[levels[i]], f"{r.elapsed:.2f} s", _delta_text(r.delta),
             format_bytes(r.rss), str(r.loops), _notes(r), r.source]
            for i, r in ((i, records[i]) for i in order)]

    # -- text
    widths = [max([len(head[c])] + [len(row[c]) for row in rows]) for c in range(len(head))]
    line = lambda cells: "  ".join(c.ljust(w) for c, w in zip(cells, widths)).rstrip()  # noqa: E731
    text = "\n".join(["Session summary: " + " | ".join(totals), "", heading,
                      line(head), line(["-" * w for w in widths])] + [line(row) for row in rows])

    # -- html
    squares = "".join(
        f"<span title='{html.escape(_cell_label(r) + ' ' + LABELS[lv] + ': ' + r.source, quote=True)}' "
        f"style='display:inline-block;min-width:22px;height:22px;line-height:22px;margin:1px;padding:0 2px;"
        f"border-radius:3px;background:{COLORS[lv]};color:#fff;font-size:75%;text-align:center;"
        f"font-family:monospace'>{r.number if r.number is not None else '?'}</span>"
        for r, lv in zip(records, levels)
    )
    th = "padding:2px 10px;text-align:left;border-bottom:1px solid rgba(128,128,128,.4)"
    td = "padding:2px 10px;font-family:monospace;text-align:left"
    body = ""
    for i, row in zip(order, rows):
        cells = "".join(
            f"<td style='{td}{';font-weight:bold;color:' + COLORS[levels[i]] if c == 1 else ''}'>"
            f"{html.escape(value)}</td>" for c, value in enumerate(row))
        body += f"<tr>{cells}</tr>"
    html_text = (
        f"<div style='border-left:4px solid {COLORS[max(levels, default=0)]};padding:8px 12px;margin:6px 0;"
        f"background:rgba(128,128,128,.07);border-radius:4px;line-height:1.45'>"
        f"<div style='font-weight:bold'>Session summary</div>"
        f"<div style='opacity:.8;font-size:90%;margin-bottom:4px'>{html.escape(' · '.join(totals))}</div>"
        f"<div style='margin:4px 0'>{squares}</div>"
        f"<div style='opacity:.8;font-size:90%;margin-top:6px'>{html.escape(heading)}</div>"
        f"<table style='border-collapse:collapse;margin:6px 0'><tr>"
        + "".join(f"<th style='{th}'>{h}</th>" for h in head) + f"</tr>{body}</table>"
        f"<div style='opacity:.65;font-size:85%'>Colours show what each cell did itself: its run time "
        f"and the memory it added.  ·  %autopsy summary clear = start again</div></div>"
    )
    return html_text, text


# --------------------------------------------------------------------------- IPython hooks
class CellHealth:
    """Registers pre/post cell hooks. ``stats`` is shared with the monitor."""

    def __init__(self, shell, settings: Settings, stats: Dict[str, int],
                 show: Optional[Callable[[str, str], None]] = None):
        self.shell, self.settings, self.stats = shell, settings, stats
        self.show = show
        self.depth = 0
        self.records: Deque[CellRecord] = deque(maxlen=settings.history_cells or None)
        self._t0 = 0.0
        self._rss0: Optional[int] = None
        self._raw_cell = ""
        self._numbered = False
        self._guard: Optional[OutputGuard] = None
        self._saved_stdout = None

    def register(self) -> None:
        self.shell.events.register("pre_run_cell", self.pre_run_cell)
        self.shell.events.register("post_run_cell", self.post_run_cell)

    def unregister(self) -> None:
        for name, cb in (("pre_run_cell", self.pre_run_cell), ("post_run_cell", self.post_run_cell)):
            try:
                self.shell.events.unregister(name, cb)
            except ValueError:
                pass
        self._restore_stdout()

    def pre_run_cell(self, info=None) -> None:
        self.depth += 1
        if self.depth > 1:
            return  # nested run_cell (e.g. %%autopsy off): the outer cell is measured
        self.stats.update(loops=0, stopped=0)
        self._raw_cell = getattr(info, "raw_cell", "") or ""
        self._numbered = bool(getattr(info, "store_history", False))
        self._t0 = time.perf_counter()
        self._rss0 = current_rss()
        if self.settings.max_output_lines:
            self._saved_stdout = sys.stdout
            self._guard = OutputGuard(sys.stdout, self.settings.max_output_lines)
            sys.stdout = self._guard

    def post_run_cell(self, result=None) -> None:
        if self.depth == 0:
            return  # hooks were registered during this cell: nothing was measured
        self.depth -= 1
        if self.depth:
            return
        muted = bool(self._guard is not None and self._guard.muted)
        self._restore_stdout()
        elapsed = time.perf_counter() - self._t0
        rss, limit = current_rss(), memory_limit()
        delta = (rss - self._rss0) if (rss is not None and self._rss0 is not None) else None
        self._remember(result, elapsed, delta, rss, limit, muted)
        frac = (rss / limit) if rss and limit else 0
        biggest = largest_variables(self.shell.user_ns) if frac >= self.settings.memory_warn else None
        level, html_text, text = build_bar(elapsed, rss, limit, delta, self.stats.get("loops", 0),
                                           self.stats.get("stopped", 0), self.settings, biggest)
        if should_show(self.settings.heatmap, level, delta) and self.show:
            self.show(text, html_text)

    # ------------------------------------------------------------------ session summary
    def _remember(self, result, elapsed: float, delta: Optional[int], rss: Optional[int],
                  limit: Optional[int], muted: bool) -> None:
        """Add the cell that just finished to the session history."""
        keep = self.settings.history_cells
        # Only cells with a visible [number] count: frontends also run hidden helper code.
        if keep <= 0 or not self._numbered or only_autopsy_commands(self._raw_cell):
            return
        if self.records.maxlen != keep:  # the setting was changed: keep the newest cells
            self.records = deque(self.records, maxlen=keep)
        error = getattr(result, "error_in_exec", None) or getattr(result, "error_before_exec", None)
        outcome = "ok"
        if isinstance(error, KeyboardInterrupt):
            outcome = "interrupted"
        elif error is not None and not self.stats.get("stopped", 0):
            outcome = "error"  # a stopped loop is already counted in ``stopped``
        self.records.append(CellRecord(
            number=getattr(result, "execution_count", None), source=first_line(self._raw_cell),
            elapsed=elapsed, delta=delta, rss=rss, limit=limit, loops=self.stats.get("loops", 0),
            stopped=self.stats.get("stopped", 0), outcome=outcome, muted=muted,
        ))

    def summary(self, mode: str = "") -> None:
        """``%autopsy summary [all|clear]``."""
        if mode == "clear":
            self.records.clear()
            print("Session summary cleared.")
        elif mode not in ("", "all"):
            print("Usage: %autopsy summary | %autopsy summary all | %autopsy summary clear")
        elif self.settings.history_cells <= 0:
            print("Cell history is off. Turn it on with: %autopsy config history_cells=200")
        elif not self.records:
            print("No cells recorded yet. Run some cells, then try %autopsy summary again.")
        else:
            html_text, text = build_summary(self.records, self.settings, mode == "all",
                                            current_rss(), memory_limit())
            if self.show:
                self.show(text, html_text)
            else:
                print(text)

    def _restore_stdout(self) -> None:
        if self._guard is not None and sys.stdout is self._guard:
            sys.stdout = self._saved_stdout
        self._guard = None
