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
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Callable, Deque, Dict, Iterable, List, Optional, Tuple

from .config import Settings
from .growth import current_rss, format_bytes, largest_variables, memory_limit

COLORS = ("#2b8a3e", "#e67700", "#c92a2a")  # green, yellow, red
LABELS = ("healthy", "watch out", "heavy")
BIG_GROWTH = 100 * 1024 * 1024  # 100 MB
SOURCE_WIDTH = 40  # characters of a cell's first line kept for the summary
TOP_CELLS = 10  # rows in the "worst cells" table
RERUNS_NEEDED = 2  # re-runs in a row that must each add memory before a cell is flagged
MB = 1024 * 1024
_ONLY_AUTOPSY = re.compile(r"\s*%autopsy\b")  # a line that is just our own line magic


# --------------------------------------------------------------------------- output guard
class OutputGuard:
    """Wraps sys.stdout or sys.stderr and mutes it after ``max_lines`` printed lines.

    The last ``tail_lines`` muted lines are kept, because the end of a flood is
    usually what matters (the final result, or the values just before an error).
    ``flush_tail`` shows them when the cell ends or an error is about to be shown.
    """

    NOTICE = ("\n[loop-autopsy] Output muted after {n:,} lines to keep your browser responsive. "
              "The cell is still running; a stuck loop will still be stopped.\n")
    MAX_PARTIAL = 2000  # characters kept of a muted line that has not ended yet
    LINE_CHARS = 1000  # a long line counts once per this many characters (print(x, end=" ") in a loop)

    def __init__(self, stream, max_lines: int, tail_lines: int = 0):
        self._stream = stream
        self.raw_stream = stream  # reports write here so they are never muted
        self.max_lines = max_lines
        self.lines = 0
        self.muted = False
        self.tail_lines = tail_lines
        self.hidden = 0  # complete lines muted since the last flush
        self._tail: Deque[str] = deque(maxlen=tail_lines)
        self._partial = ""  # a muted line still being written (no newline yet)
        self._run = 0  # characters written since the last counted line

    def write(self, text: str) -> int:
        if self.muted:
            self._hide(text)
            return len(text)
        if not self.max_lines:
            return self._stream.write(text)
        cut, lines, run = self._scan(text, self.max_lines - self.lines)
        if cut is not None:
            self._stream.write(text[:cut])
            self._stream.write(self.NOTICE.format(n=self.max_lines))
            self.lines = self.max_lines
            self.muted = True
            self._hide(text[cut:])
            return len(text)
        self.lines += lines
        self._run = run
        return self._stream.write(text)

    def _scan(self, text: str, budget: int):
        """Count the lines in ``text``: each newline, and each ``LINE_CHARS``
        characters without one. Returns ``(cut, lines, run)``: ``cut`` is where
        the ``budget``-th line ends (``None`` if it is not reached)."""
        width, run, lines, pos, n = self.LINE_CHARS, self._run, 0, 0, len(text)
        if "\n" not in text and run + n < width:
            return None, 0, run + n  # the common case: a short piece of a line
        while pos < n:
            newline = text.find("\n", pos)
            end = n if newline < 0 else newline
            while run + (end - pos) >= width:  # a long stretch without a newline
                pos += width - run
                run, lines = 0, lines + 1
                if lines >= budget:
                    return pos, lines, run
            run += end - pos
            pos = end
            if newline >= 0:
                pos, run, lines = newline + 1, 0, lines + 1
                if lines >= budget:
                    return pos, lines, run
        return None, lines, run

    def _hide(self, text: str) -> None:
        """Remember the end of muted output for ``flush_tail``."""
        if not self.tail_lines or not text:
            return
        parts = (self._partial + text).split("\n")
        self._partial = parts.pop()[-self.MAX_PARTIAL:]
        self.hidden += len(parts)
        self._tail.extend(parts)

    def flush_tail(self) -> None:
        """Show the last muted lines, once, under a short notice."""
        shown = (list(self._tail) + ([self._partial] if self._partial else []))[-self.tail_lines:]
        hidden = self.hidden + (1 if self._partial else 0)
        self._tail.clear()
        self._partial, self.hidden = "", 0
        if not shown:
            return
        if hidden > len(shown):
            notice = f"[loop-autopsy] {hidden:,} lines were muted. The last {len(shown)}:"
        else:
            notice = f"[loop-autopsy] The muted line{'s' if len(shown) != 1 else ''}:"
        self._stream.write("\n" + notice + "\n" + "\n".join(shown) + "\n")

    def reset(self) -> None:
        """Start counting again (the output area was cleared, so the muted
        lines from before are no longer wanted either)."""
        self.lines, self.muted, self._run = 0, False, 0
        self._tail.clear()
        self._partial, self.hidden = "", 0

    def release(self) -> None:
        """The cell is over: pass everything through from now on. Code that kept
        a reference to this guard (a logging handler, for example) keeps working."""
        self.max_lines, self.muted = 0, False

    def __getattr__(self, name: str) -> Any:
        return getattr(self._stream, name)


class DisplayGuard:
    """Stands in for ``shell.display_pub.publish`` and mutes it after ``max_items``
    calls, so a loop that calls ``display()`` every round cannot freeze the browser.

    Updates to an existing output (``update=True``) replace it instead of adding
    to the page, so they are never counted or muted.
    """

    NOTICE = ("[loop-autopsy] Display output muted after {n:,} items to keep your browser responsive. "
              "The cell is still running; a stuck loop will still be stopped.")

    def __init__(self, publish: Callable[..., Any], max_items: int):
        self._publish = publish
        self.max_items = max_items
        self.items = 0
        self.muted = False
        self._unguarded = 0

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        if self._unguarded or not self.max_items or kwargs.get("update"):
            return self._publish(*args, **kwargs)
        if self.muted:
            return None
        self.items += 1
        result = self._publish(*args, **kwargs)
        if self.items >= self.max_items:
            self.muted = True
            self._publish(data={"text/plain": self.NOTICE.format(n=self.max_items)})
        return result

    @contextmanager
    def unguarded(self):
        """Reports are shown inside this, so they are never counted or muted."""
        self._unguarded += 1
        try:
            yield
        finally:
            self._unguarded -= 1

    def reset(self) -> None:
        self.items, self.muted = 0, False

    def release(self) -> None:
        self.max_items, self.muted = 0, False


# --------------------------------------------------------------------------- bar
def level_of(value: float, yellow: float, red: float) -> int:
    return 2 if value >= red else 1 if value >= yellow else 0


def build_bar(elapsed: float, rss: Optional[int], limit: Optional[int], delta: Optional[int],
              loops: int, stopped: int, settings: Settings,
              biggest: Optional[List[Tuple[str, int]]] = None,
              rerun: Optional[Tuple[int, int]] = None) -> Tuple[int, str, str]:
    """Return (level, html, text) for one cell. Pure function: easy to test.

    ``rerun`` is ``(re-runs, average bytes added)`` from ``rerun_growth``, when
    this cell keeps adding memory every time it is run again.
    """
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
    if rerun is not None:
        times, added = rerun
        warn_text += (f"\nNote: the last {times} times this cell ran again, it added about "
                      f"{format_bytes(added)} more each time. Running it again adds to what is "
                      f"already there (for example df = pd.concat([df, new]) or items.append(...)). "
                      f"Re-run the cell that creates the variable first, or restart the kernel.")
        warn_html += (f"<div style='color:{COLORS[1]};margin-top:3px'><b>Note:</b> the last {times} times "
                      f"this cell ran again, it added about {format_bytes(added)} more each time. "
                      f"Running it again adds to what is already there (for example "
                      f"<code>df = pd.concat([df, new])</code> or <code>items.append(...)</code>). "
                      f"Re-run the cell that creates the variable first, or restart the kernel.</div>")

    fill = min(100, int((frac or 0) * 100))
    html_text = (
        f"<div style='font-size:80%;opacity:.9;margin:2px 0;line-height:1.3'>"
        f"<div style='height:4px;border-radius:2px;background:rgba(128,128,128,.25);margin-bottom:2px'>"
        f"<div style='height:4px;width:{max(fill, 2)}%;border-radius:2px;background:{color}'></div></div>"
        f"<span style='color:{color};font-weight:bold'>{LABELS[level]}</span> &middot; "
        + " &middot; ".join(bits) + f"{warn_html}</div>"
    )
    return level, html_text, text + warn_text


def should_show(mode: str, level: int, delta: Optional[int], rerun: bool = False) -> bool:
    """``auto`` shows the bar only when the cell was slow, memory is high or grew a
    lot, or the cell keeps adding memory each time it is re-run.
    (A stopped loop already shows its own report, so it does not trigger the bar.)"""
    if mode == "on":
        return True
    if mode == "off":
        return False
    return level > 0 or rerun or (delta is not None and delta >= BIG_GROWTH)


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
    key: Optional[int] = None  # same code = same key, so re-runs of a cell can be found


def cell_key(raw_cell: str) -> int:
    """Identifies a cell's code within this session (re-running it gives the same key)."""
    return hash(raw_cell.strip())


def rerun_growth(records: Iterable[CellRecord], key: Optional[int],
                 min_bytes: int) -> Optional[Tuple[int, int]]:
    """Does the cell with ``key`` keep adding memory each time it is run again?

    The first run may add memory (loading data is normal). But when each of the
    latest ``RERUNS_NEEDED`` or more re-runs added at least ``min_bytes`` too,
    the cell is piling data on top of itself, as ``df = pd.concat([df, new])``
    does. Returns ``(re-runs in that streak, average bytes they added)`` or None.
    """
    if key is None or min_bytes <= 0:
        return None
    reruns = [r.delta for r in records if r.key == key][1:]
    streak: List[int] = []
    for delta in reversed(reruns):
        if delta is None or delta < min_bytes:
            break
        streak.append(delta)
    if len(streak) < RERUNS_NEEDED:
        return None
    return len(streak), sum(streak) // len(streak)


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


def _notes(record: CellRecord, rerun: Optional[Tuple[int, int]] = None) -> str:
    notes = []
    if record.stopped:
        notes.append(f"{record.stopped} stopped")
    if record.outcome != "ok":
        notes.append(record.outcome)
    if record.muted:
        notes.append("output muted")
    if rerun is not None:
        notes.append(f"+{format_bytes(rerun[1])} each re-run")
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
    # A cell that grows on every re-run gets a note on its latest run only.
    latest = {r.key: i for i, r in enumerate(records) if r.key is not None}
    reruns = {i: rerun_growth(records, key, settings.rerun_growth_mb * MB) for key, i in latest.items()}

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
             format_bytes(r.rss), str(r.loops), _notes(r, reruns.get(i)), r.source]
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
        self._guard: Optional[OutputGuard] = None  # sys.stdout
        self._err_guard: Optional[OutputGuard] = None  # sys.stderr
        self._display_guard: Optional[DisplayGuard] = None  # display()
        self._clear_hook: Optional[Callable[..., Any]] = None
        self._traceback_hook: Optional[Callable[..., Any]] = None
        self._was_muted = False  # a guard muted output earlier in this cell
        self._saved_stdout: Any = None
        self._saved_stderr: Any = None

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
            self._saved_stdout, self._saved_stderr = sys.stdout, sys.stderr
            limit, tail = self.settings.max_output_lines, self.settings.muted_tail
            self._guard = OutputGuard(sys.stdout, limit, tail)
            self._err_guard = OutputGuard(sys.stderr, limit, tail)
            sys.stdout, sys.stderr = self._guard, self._err_guard
        self._was_muted = False
        self._guard_display()
        self._guard_traceback()

    def post_run_cell(self, result=None) -> None:
        if self.depth == 0:
            return  # hooks were registered during this cell: nothing was measured
        self.depth -= 1
        if self.depth:
            return
        muted = self._was_muted or self._any_muted()
        self._restore_stdout()
        elapsed = time.perf_counter() - self._t0
        rss, limit = current_rss(), memory_limit()
        delta = (rss - self._rss0) if (rss is not None and self._rss0 is not None) else None
        record = self._remember(result, elapsed, delta, rss, limit, muted)
        rerun = None
        if record is not None:
            rerun = rerun_growth(self.records, record.key, self.settings.rerun_growth_mb * MB)
        frac = (rss / limit) if rss and limit else 0
        biggest = largest_variables(self.shell.user_ns) if frac >= self.settings.memory_warn else None
        level, html_text, text = build_bar(elapsed, rss, limit, delta, self.stats.get("loops", 0),
                                           self.stats.get("stopped", 0), self.settings, biggest, rerun)
        if should_show(self.settings.heatmap, level, delta, rerun is not None) and self.show:
            self.show(text, html_text)

    # ------------------------------------------------------------------ session summary
    def _remember(self, result, elapsed: float, delta: Optional[int], rss: Optional[int],
                  limit: Optional[int], muted: bool) -> Optional[CellRecord]:
        """Add the cell that just finished to the session history and return it."""
        keep = self.settings.history_cells
        # Only cells with a visible [number] count: frontends also run hidden helper code.
        if keep <= 0 or not self._numbered or only_autopsy_commands(self._raw_cell):
            return None
        if self.records.maxlen != keep:  # the setting was changed: keep the newest cells
            self.records = deque(self.records, maxlen=keep)
        error = getattr(result, "error_in_exec", None) or getattr(result, "error_before_exec", None)
        outcome = "ok"
        if isinstance(error, KeyboardInterrupt):
            outcome = "interrupted"
        elif error is not None and not self.stats.get("stopped", 0):
            outcome = "error"  # a stopped loop is already counted in ``stopped``
        record = CellRecord(
            number=getattr(result, "execution_count", None), source=first_line(self._raw_cell),
            elapsed=elapsed, delta=delta, rss=rss, limit=limit, loops=self.stats.get("loops", 0),
            stopped=self.stats.get("stopped", 0), outcome=outcome, muted=muted,
            key=cell_key(self._raw_cell),
        )
        self.records.append(record)
        return record

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

    # ------------------------------------------------------------------ output guards
    def _guard_display(self) -> None:
        """Put a DisplayGuard in front of ``display()`` for this cell, and reset
        every guard when the output area is cleared (animations clear and redraw)."""
        pub = getattr(self.shell, "display_pub", None)
        if pub is None or not (self.settings.max_displays or self.settings.max_output_lines):
            return
        self._display_guard = DisplayGuard(pub.publish, self.settings.max_displays)
        pub.publish = self._display_guard  # instance attribute: hides the class's method
        original_clear = getattr(pub, "clear_output", None)
        if original_clear is not None:
            def clear_output(*args: Any, **kwargs: Any) -> Any:
                for guard in (self._guard, self._err_guard, self._display_guard):
                    if guard is not None:
                        guard.reset()
                return original_clear(*args, **kwargs)

            self._clear_hook = pub.clear_output = clear_output

    def _any_muted(self) -> bool:
        return any(g is not None and g.muted for g in (self._guard, self._err_guard, self._display_guard))

    def _guard_traceback(self) -> None:
        """An error message must never be muted. In terminal IPython tracebacks
        are printed to sys.stdout, so after a muted flood of ``print`` the error
        that ended the cell would vanish. Unmute just before one is shown, after
        the last muted lines, which show the values just before the error."""
        original = getattr(self.shell, "showtraceback", None)
        if original is None:
            return

        def showtraceback(*args: Any, **kwargs: Any) -> Any:
            self._was_muted = self._was_muted or self._any_muted()
            self._flush_tails()
            for guard in (self._guard, self._err_guard, self._display_guard):
                if guard is not None:
                    guard.reset()
            return original(*args, **kwargs)

        self._traceback_hook = self.shell.showtraceback = showtraceback

    def _flush_tails(self) -> None:
        for guard in (self._guard, self._err_guard):
            if guard is not None:
                guard.flush_tail()

    def _restore_stdout(self) -> None:
        """Take every guard away again. Only undo what is still ours: something
        else may have replaced a stream in the meantime."""
        self._flush_tails()
        if self._guard is not None and sys.stdout is self._guard:
            sys.stdout = self._saved_stdout
        if self._err_guard is not None and sys.stderr is self._err_guard:
            sys.stderr = self._saved_stderr
        pub = getattr(self.shell, "display_pub", None)
        if pub is not None:
            for name, ours in (("publish", self._display_guard), ("clear_output", self._clear_hook)):
                if ours is not None and vars(pub).get(name) is ours:
                    delattr(pub, name)
        if self._traceback_hook is not None and vars(self.shell).get("showtraceback") is self._traceback_hook:
            del self.shell.showtraceback
        self._traceback_hook = None
        for guard in (self._guard, self._err_guard, self._display_guard):
            if guard is not None:
                guard.release()
        self._guard = self._err_guard = self._display_guard = self._clear_hook = None
