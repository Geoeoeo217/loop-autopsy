"""Run-time glue: receives begin/due/check calls from instrumented loops,
runs the detectors and shows reports.
Owner: NATHANAEL George Steindhart.
"""

from __future__ import annotations

import builtins
import os
import re
import sys
import time
import types
import weakref
from collections import deque
from contextlib import contextmanager
from typing import Any, Dict, List, Optional, Tuple

from . import detectors, growth, loop_notes, recursion, report
from .config import Finding, LoopInfo, LoopState, LoopStopped, Settings
from .transformer import RUNTIME_NAME

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


# Iterables whose len() is cheap and equals the number of rounds a for loop over them runs.
_SIZED = (range, list, tuple, str, bytes, bytearray, dict, set, frozenset, deque,
          type({}.keys()), type({}.values()), type({}.items()))
# NumPy arrays and pandas Series loop over their rows; a DataFrame loops over its column names, so it is left out.
_SIZED_BY_NAME = {("numpy", "ndarray"), ("pandas", "Series")}


def _length(iterable: Any) -> Optional[int]:
    t = type(iterable)
    if t not in _SIZED and (t.__module__.split(".")[0], t.__name__) not in _SIZED_BY_NAME:
        return None
    try:
        return len(iterable)
    except Exception:  # e.g. a range too big for len(), or a 0-d array
        return None


def endless_note(info: LoopInfo) -> str:
    """Shown when a ``while True:`` loop with no way out starts."""
    return (f"[loop-autopsy] Note: `{info.header}` (line {info.lineno}) has no `break`, `return` or `raise` "
            f"inside, so it can only end with an error. Add a `break` for when the work is done; "
            f"until then loop-autopsy stops it if it gets stuck, or press ■.")


# The loops instrumented in each shell, kept across %unload_ext / %reload_ext: a function
# defined before a reload still carries its old loop ids, which must keep pointing at the
# right LoopInfo (and never at a loop instrumented later in a different cell).
_REGISTRIES: "weakref.WeakKeyDictionary" = weakref.WeakKeyDictionary()


def registry_for(shell) -> List[LoopInfo]:
    """The list of loops instrumented in ``shell`` so far, shared by every Monitor it gets."""
    try:
        return _REGISTRIES.setdefault(shell, [])
    except TypeError:  # a shell that cannot be weakly referenced: no sharing
        return []


def _print_note(text: str) -> None:
    """Notes go straight to the real output, past the output guard."""
    print(text, file=getattr(sys.stdout, "raw_stream", sys.stdout))


def _safely(state: LoopState, rule: str, check, *args):
    """Run one detection rule. A rule that fails on unusual values (for example a
    Decimal compared with a float) is switched off for the rest of this loop run
    instead of crashing the student's loop."""
    broken = state.scratch.get("broken_rules")
    if broken and rule in broken:
        return None
    try:
        return check(*args)
    except Exception:
        state.scratch.setdefault("broken_rules", set()).add(rule)
        return None


class Monitor:
    """One per IPython shell. Instrumented code calls it as ``__loop_autopsy__``."""

    def __init__(self, shell, settings: Settings, infos: Optional[List[LoopInfo]] = None):
        self.shell = shell
        self.settings = settings
        self.infos: List[LoopInfo] = [] if infos is None else infos
        self.states: List[Optional[LoopState]] = [None] * len(self.infos)
        self.enabled = True
        self.paused_cell = False
        self._pause_depth = 0
        self.stats: Dict[str, int] = {"loops": 0, "stopped": 0}
        self.last: Optional[Tuple[Finding, LoopState, float]] = None
        self.active: Optional[LoopState] = None
        self.active_time = 0.0
        self._old_exc = None
        self._noted: set = set()  # loop ids whose notes from the code were shown
        self._noted_endless: set = set()  # loop ids whose "never runs out" note was shown
        self._local_names: Dict[int, frozenset] = {}  # loop id -> names local to the code it sits in

    # ------------------------------------------------------------------ transformer side
    def register(self, info: LoopInfo) -> int:
        info.loop_id = len(self.infos)
        self.infos.append(info)
        self._sync_states()
        return info.loop_id

    def _sync_states(self) -> None:
        """Give every registered loop a slot in ``states`` (another Monitor of the same
        shell may have registered loops into the shared list)."""
        missing = len(self.infos) - len(self.states)
        if missing > 0:
            self.states.extend([None] * missing)

    def _state(self, loop_id: int) -> Optional[LoopState]:
        return self.states[loop_id] if 0 <= loop_id < len(self.states) else None

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
        self.restore_runtime()

    def restore_runtime(self) -> None:
        """Put ``__loop_autopsy__`` back if ``%reset`` (or ``del``) removed it: every
        instrumented loop, including those in functions defined earlier, looks it up."""
        ns = getattr(self.shell, "user_ns", None)
        if ns is None or ns.get(RUNTIME_NAME) is self:
            return
        ns[RUNTIME_NAME] = self
        hidden = getattr(self.shell, "user_ns_hidden", None)
        if hidden is not None:
            hidden[RUNTIME_NAME] = self  # keep it out of %who / %whos

    # ------------------------------------------------------------------ loop side (hot path)
    def begin(self, loop_id: int) -> None:
        if not 0 <= loop_id < len(self.infos):
            return  # a loop this monitor never registered: it runs unwatched
        self._sync_states()
        info = self.infos[loop_id]
        self.states[loop_id] = LoopState(info, started=time.perf_counter(),
                                         replay_rows=self.settings.replay_rows)
        self.stats["loops"] += 1
        endless = info.always_true and not info.exits
        if (endless or info.notes) and self.enabled and loop_id not in self._noted:
            self._noted.add(loop_id)  # once per loop, even if a function runs it many times
            for note in ((endless_note(info),) if endless else ()) + info.notes:
                _print_note(note)

    def sized(self, loop_id: int, iterable: Any) -> Any:
        """Called with a for loop's iterable just before it starts: remember how
        many rounds it will run, and hand the iterable back untouched."""
        state = self._state(loop_id)
        if state is not None and self.enabled:
            state.total = _length(iterable)
            info = state.info
            if state.total is None and not info.exits and loop_id not in self._noted_endless:
                kind = loop_notes.endless_iterable(iterable)
                if kind:  # shown on the first round, so an empty cycle() stays quiet
                    state.scratch["endless_note"] = loop_notes.endless_iterator_note(
                        info.header, info.lineno, kind, self.settings.time_limit)
        return iterable

    def due(self, loop_id: int) -> bool:
        try:  # runs every round: no extra call, and a try costs nothing until it fails
            state = self.states[loop_id]
        except IndexError:  # a loop this monitor never registered
            return False
        if state is None or not self.enabled:
            return False
        state.rounds += 1
        return state.rounds >= state.next_check

    def check(self, loop_id: int, namespace: Dict[str, Any],
              global_ns: Optional[Dict[str, Any]] = None) -> None:
        state = self._state(loop_id)
        if state is None:
            return
        info = state.info
        now = time.perf_counter()
        state.checks += 1
        values = self._values(loop_id, info, state, namespace, global_ns)
        if state.checks == 1:
            _safely(state, "start", growth.remember_start, state, values)
            note = state.scratch.pop("endless_note", None)
            if note is not None:
                self._noted_endless.add(loop_id)
                _print_note(note)
        report.record(state, values)
        self.active, self.active_time = state, now

        gap, state.last_check = now - state.last_check, now
        if not state.scratch.get("rep_refine"):
            base = 256 if state.rounds >= 100_000 else 16 if state.rounds >= 1_000 else 1
            state.stride = base if gap <= SLOW_GAP else max(1, min(base, state.stride) // 4)

        settings = self.settings
        finding = (_safely(state, "repeat", detectors.check_repeat, info, state, values)
                   or _safely(state, "direction", detectors.check_direction, info, state, values, settings)
                   or _safely(state, "growing", growth.check_growing_iterable, info, state, values, settings))
        if finding is None and state.checks % 16 == 0:
            finding = _safely(state, "memory", growth.check_memory, info, state, values, settings)
        if finding is None:
            finding = (_safely(state, "estimate", detectors.check_estimate, info, state, settings, now)
                       or _safely(state, "time", detectors.check_time, info, state, settings, now))
        state.next_check = state.rounds + state.stride
        if finding is not None:
            state.stopped = finding
            self.stats["stopped"] += 1
            self.last = (finding, state, now - state.started)
            raise LoopStopped(finding)

    def _values(self, loop_id: int, info: LoopInfo, state: LoopState, namespace: Dict[str, Any],
                global_ns: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        """The watched variables right now. Inside a function ``locals()`` misses the
        globals the loop uses (``while state['i'] < 5``, ``global n``), so those are read
        from ``globals()``. A condition variable found nowhere switches the stuck and
        cycle rules off: an unchanged state proves nothing if part of it is unseen."""
        values = {}
        local_names = None
        for name in info.watched:
            if name in namespace:
                value = namespace[name]
            elif global_ns is None or global_ns is namespace or name not in global_ns:
                if name in info.cond_names and not hasattr(builtins, name):
                    state.scratch.setdefault("rep_off", "unseen")
                continue
            else:
                if local_names is None:
                    local_names = self._locals_of(loop_id)
                if name in local_names:
                    continue  # a local variable that is not assigned yet
                value = global_ns[name]
            if not _skip(value):
                values[name] = value
        return values

    def _locals_of(self, loop_id: int) -> frozenset:
        """Names local to the function the loop sits in, read from the loop's own frame."""
        names = self._local_names.get(loop_id)
        if names is None:
            try:
                code = sys._getframe(3).f_code  # _locals_of <- _values <- check <- the loop
                names = frozenset(code.co_varnames + code.co_cellvars + code.co_freevars)
            except (AttributeError, ValueError):  # pragma: no cover - no frame access
                names = frozenset()
            self._local_names[loop_id] = names
        return names

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
