"""Run-time glue: receives begin/due/check calls from instrumented loops,
runs the detectors and shows reports.
Owner: NATHANAEL George Steindhart.
"""

from __future__ import annotations

import os
import re
import time
import types
from contextlib import contextmanager
from typing import Any, Dict, List, Optional, Tuple

from . import detectors, growth, recursion, report
from .config import Finding, LoopInfo, LoopState, LoopStopped, Settings

_TIMEIT = re.compile(r"^\s*%%?timeit\b", re.MULTILINE)
SLOW_GAP = 0.25  # seconds between checks before the stride is reduced
INTERRUPT_WINDOW = 2.0  # seconds: a loop checked this recently is "the running loop"


_PACKAGE_DIR = os.path.dirname(os.path.abspath(__file__))


def _without_our_frames(tb):
    """Cut the traceback where it enters loop-autopsy, so students only see their code."""
    first = tb
    while tb is not None and tb.tb_next is not None:
        nxt = tb.tb_next
        if os.path.dirname(os.path.abspath(nxt.tb_frame.f_code.co_filename)) == _PACKAGE_DIR:
            tb.tb_next = None
            break
        tb = nxt
    return first


def _skip(value: Any) -> bool:
    return callable(value) or isinstance(value, types.ModuleType)


class Monitor:
    """One per IPython shell. Instrumented code calls it as ``__loop_autopsy__``."""

    def __init__(self, shell, settings: Settings):
        self.shell = shell
        self.settings = settings
        self.infos: List[LoopInfo] = []
        self.states: List[Optional[LoopState]] = []
        self.enabled = True
        self.paused_cell = False
        self._pause_depth = 0
        self.stats: Dict[str, int] = {"loops": 0, "stopped": 0}
        self.last: Optional[Tuple[Finding, LoopState, float]] = None
        self.active: Optional[LoopState] = None
        self.active_time = 0.0
        self._old_exc = None

    # ------------------------------------------------------------------ transformer side
    def register(self, info: LoopInfo) -> int:
        info.loop_id = len(self.infos)
        self.infos.append(info)
        self.states.append(None)
        return info.loop_id

    def is_paused(self) -> bool:
        return not self.enabled or self.paused_cell or self._pause_depth > 0

    @contextmanager
    def paused(self):
        self._pause_depth += 1
        try:
            yield
        finally:
            self._pause_depth -= 1

    def pre_run_cell(self, info=None) -> None:
        raw = getattr(info, "raw_cell", "") or ""
        self.paused_cell = bool(_TIMEIT.search(raw))
        self.active = None

    # ------------------------------------------------------------------ loop side (hot path)
    def begin(self, loop_id: int) -> None:
        self.states[loop_id] = LoopState(self.infos[loop_id], started=time.perf_counter(),
                                         replay_rows=self.settings.replay_rows)
        self.stats["loops"] += 1

    def due(self, loop_id: int) -> bool:
        state = self.states[loop_id]
        if state is None or not self.enabled:
            return False
        state.rounds += 1
        return state.rounds >= state.next_check

    def check(self, loop_id: int, namespace: Dict[str, Any]) -> None:
        state = self.states[loop_id]
        info = state.info
        now = time.perf_counter()
        state.checks += 1
        values = {}
        for name in info.watched:
            if name in namespace:
                value = namespace[name]
                if not _skip(value):
                    values[name] = value
        if state.checks == 1:
            growth.remember_start(state, values)
        report.record(state, values)
        self.active, self.active_time = state, now

        gap, state.last_check = now - state.last_check, now
        if not state.scratch.get("rep_refine"):
            base = 256 if state.rounds >= 100_000 else 16 if state.rounds >= 1_000 else 1
            state.stride = base if gap <= SLOW_GAP else max(1, min(base, state.stride) // 4)

        finding = (detectors.check_repeat(info, state, values)
                   or detectors.check_direction(info, state, values, self.settings)
                   or growth.check_growing_iterable(info, state, values, self.settings))
        if finding is None and state.checks % 16 == 0:
            finding = growth.check_memory(info, state, values, self.settings)
        if finding is None:
            finding = detectors.check_time(info, state, self.settings, now)
        state.next_check = state.rounds + state.stride
        if finding is not None:
            state.stopped = finding
            self.stats["stopped"] += 1
            self.last = (finding, state, now - state.started)
            raise LoopStopped(finding)

    # ------------------------------------------------------------------ reports
    def show_last(self) -> bool:
        if not self.last:
            return False
        finding, state, elapsed = self.last
        report.display_report(report.render_text(finding, state, elapsed),
                              report.render_html(finding, state, elapsed))
        return True

    def install_exception_handler(self) -> None:
        self._old_exc = (self.shell.custom_exceptions, getattr(self.shell, "CustomTB", None))
        self.shell.set_custom_exc((LoopStopped, KeyboardInterrupt, RecursionError), self._handle)

    def remove_exception_handler(self) -> None:
        if self._old_exc is None:
            return
        old_tuple, old_tb = self._old_exc
        self.shell.custom_exceptions = old_tuple
        if old_tb is not None:
            self.shell.CustomTB = old_tb
        self._old_exc = None

    def _handle(self, shell, etype, value, tb, tb_offset=None):
        if issubclass(etype, LoopStopped):
            self.show_last()
            return []
        if issubclass(etype, RecursionError):
            return self._handle_recursion(shell, etype, value, tb, tb_offset)
        # KeyboardInterrupt: keep IPython's normal message, then add the replay.
        shell.showtraceback((etype, value, _without_our_frames(tb)), tb_offset=tb_offset)
        state = self.active
        if (state is not None and state.stopped is None and state.history
                and time.perf_counter() - self.active_time < INTERRUPT_WINDOW):
            finding = report.interrupt_finding(state)
            elapsed = time.perf_counter() - state.started
            self.last = (finding, state, elapsed)
            report.display_report(report.render_text(finding, state, elapsed),
                                  report.render_html(finding, state, elapsed))
        return []

    def _handle_recursion(self, shell, etype, value, tb, tb_offset=None):
        """Replace the long RecursionError traceback with a report, when the
        recursion happened in a function typed in a notebook cell."""
        explained = None
        if self.enabled:
            try:
                explained = recursion.explain(shell, tb, self.settings.replay_rows, self.settings.max_watched)
            except Exception:  # never let the explanation hide the real error
                explained = None
        if explained is None:
            shell.showtraceback((etype, value, _without_our_frames(tb)), tb_offset=tb_offset)
            return []
        finding, state = explained
        self.last = (finding, state, None)
        self.show_last()
        return []
