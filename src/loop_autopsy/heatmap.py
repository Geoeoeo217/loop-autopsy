"""Cell health bar and output guard. See SPEC.md section 10. Owner: Teammate 4.

After each cell a thin coloured bar shows time, memory and loops watched.
In ``auto`` mode it only appears when something is worth seeing.
"""

from __future__ import annotations

import sys
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from .config import Settings
from .growth import current_rss, format_bytes, largest_variables, memory_limit

COLORS = ("#2b8a3e", "#e67700", "#c92a2a")  # green, yellow, red
LABELS = ("healthy", "watch out", "heavy")
BIG_GROWTH = 100 * 1024 * 1024  # 100 MB


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


# --------------------------------------------------------------------------- IPython hooks
class CellHealth:
    """Registers pre/post cell hooks. ``stats`` is shared with the monitor."""

    def __init__(self, shell, settings: Settings, stats: Dict[str, int],
                 show: Optional[Callable[[str, str], None]] = None):
        self.shell, self.settings, self.stats = shell, settings, stats
        self.show = show
        self.depth = 0
        self._t0 = 0.0
        self._rss0: Optional[int] = None
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
        self._restore_stdout()
        elapsed = time.perf_counter() - self._t0
        rss, limit = current_rss(), memory_limit()
        delta = (rss - self._rss0) if (rss is not None and self._rss0 is not None) else None
        frac = (rss / limit) if rss and limit else 0
        biggest = largest_variables(self.shell.user_ns) if frac >= self.settings.memory_warn else None
        level, html_text, text = build_bar(elapsed, rss, limit, delta, self.stats.get("loops", 0),
                                           self.stats.get("stopped", 0), self.settings, biggest)
        if should_show(self.settings.heatmap, level, delta) and self.show:
            self.show(text, html_text)

    def _restore_stdout(self) -> None:
        if self._guard is not None and sys.stdout is self._guard:
            sys.stdout = self._saved_stdout
        self._guard = None
