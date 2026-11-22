"""Run-time glue: receives begin/due/check calls from instrumented loops,
runs the detectors and shows reports.
Owner: NATHANAEL George Steindhart.
"""

from __future__ import annotations

import ast
import builtins
import functools
import getpass
import inspect
import os
import re
import signal
import sys
import threading
import time
import types
import weakref
from collections import deque
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any, Callable, Dict, List, Optional, Tuple

from . import detectors, growth, loop_notes, recursion, report
from .config import Finding, LoopInfo, LoopState, LoopStopped, Settings
from .transformer import RUNTIME_NAME

_TIMEIT = re.compile(r"^\s*%%?timeit\b", re.MULTILINE)
SLOW_GAP = 0.25  # seconds between checks before the stride is reduced
INTERRUPT_WINDOW = 2.0  # seconds: a loop checked this recently is "the running loop"
OUR_EXCEPTIONS = (LoopStopped, KeyboardInterrupt, RecursionError)
# Functions that wait for the person at the keyboard; time spent in them is not loop time.
_WAITS = ((builtins, "input"), (getpass, "getpass"))
# Built-ins whose argument feed() may watch (transformer.visit_Call); by identity, so a
# function of the user's that happens to be called "list" is left alone.
_CONSUMER_IDS = frozenset(id(f) for f in (builtins.list, builtins.tuple, builtins.set, builtins.frozenset,
                                          builtins.sorted, builtins.sum, builtins.min, builtins.max,
                                          builtins.dict, builtins.any, builtins.all, deque))
WATCHDOG_EVERY = 0.25  # seconds between the watchdog's looks at the running loop
WATCHDOG_QUIET = 2.0  # seconds without a new round before a loop past its time limit is stopped
# Above this, a runaway recursion can crash the kernel before Python raises RecursionError.
# Python 3.12+ keeps Python calls off the C stack, so the limit there is mostly about memory.
SAFE_RECURSION = 10_000 if sys.version_info < (3, 12) else None
_MISSING = object()
# Built-in values: fingerprinted whole, never followed attribute by attribute.
_PLAIN_TYPES = frozenset({type(None), bool, int, float, complex, str, bytes, bytearray, list, tuple, dict, set,
                          frozenset, deque, range})


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
_SIZED: Tuple[type, ...] = (range, list, tuple, str, bytes, bytearray, dict, set, frozenset, deque,
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


def _plain(value: Any) -> Any:
    """A NumPy number as the plain Python number it holds (``np.int64(3)`` -> ``3``),
    so the rules can read it. Anything else is returned as it is."""
    t = type(value)
    if t.__module__ == "numpy" and t.__name__ != "ndarray" and getattr(value, "ndim", None) == 0:
        try:
            return value.item()
        except Exception:
            return value
    return value


def _read_attr(obj: Any, attr: str) -> Any:
    """``obj.attr`` without running any of the object's code: a value stored on the
    instance or the class, or in ``__slots__``. A property (or anything else computed
    when it is read) is not read, and gives ``_MISSING``."""
    try:
        found = inspect.getattr_static(obj, attr, _MISSING)
        if found is _MISSING:
            return _MISSING
        try:
            own = object.__getattribute__(obj, "__dict__")
        except Exception:
            own = None
        if isinstance(own, (dict, types.MappingProxyType)) and attr in own and own[attr] is found:
            if not isinstance(obj, type) or not hasattr(type(found), "__get__"):
                return found  # stored on the object itself (or a plain class attribute)
        if type(found) is types.MemberDescriptorType:  # __slots__
            return found.__get__(obj, type(obj))
        if hasattr(type(found), "__get__"):
            return _MISSING  # a property, method or other descriptor: reading it runs code
        return found  # a plain class attribute
    except Exception:
        return _MISSING


def _print_note(text: str) -> None:
    """Notes go straight to the real output, past the output guard. A console that
    cannot show ■ (a Windows terminal) gets "the stop button" instead of an error."""
    stream = getattr(sys.stdout, "raw_stream", sys.stdout)
    try:
        print(text, file=stream)
    except UnicodeEncodeError:
        print(text.replace("the stop button ■", "the stop button").replace("■", "the stop button"), file=stream)


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
        self.last: Optional[Tuple[Finding, LoopState, Optional[float]]] = None  # elapsed: None for recursion
        self.active: Optional[LoopState] = None
        self.active_time = 0.0
        self._old_exc: Optional[Tuple[tuple, Any]] = None
        self._noted: set = set()  # loop ids whose notes from the code were shown
        self._noted_endless: set = set()  # loop ids whose "never runs out" note was shown
        self._local_names: Dict[int, frozenset] = {}  # loop id -> names local to the code it sits in
        # loop id -> the runs of that loop in progress, outermost first. More than one when the
        # loop runs inside itself (a loop in a recursive function) or in several threads.
        # Instrumented code drops a run from here when it ends (transformer._END).
        self.stacks: Dict[int, List[LoopState]] = {}
        self._begun: set = set()  # loop ids started since the last cell began
        self.waited = 0.0  # seconds spent in input() / getpass() since loading
        self._waiting = 0  # input() / getpass() calls in progress
        self._old_thread_hook: Optional[Callable[..., Any]] = None
        self.loops_running = 0  # watched loop runs in progress (transformer._END counts them down)
        self._pending_stop: Optional[Tuple[Finding, LoopState, float]] = None  # set by the watchdog
        self._watchdog: Optional[Tuple[threading.Thread, threading.Event]] = None
        self._noted_limit = False

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
        self._pending_stop = None  # a watchdog stop that never arrived belongs to an older cell
        self.restore_runtime()
        self.time_waits()
        self.guard_recursion_limit()
        self._trim_finished()

    def _trim_finished(self) -> None:
        """Drop the stored fingerprints of loop runs that have ended: the last run of
        each loop is kept for its report, but its fingerprints are no longer needed and
        can hold up to a few million elements."""
        for loop_id in self._begun:
            state = self._state(loop_id)
            if state is not None and not self.stacks.get(loop_id):
                for key in ("rep_seen", "rep_full", "rep_stored"):
                    state.scratch.pop(key, None)
        self._begun.clear()

    # ------------------------------------------------------------------ waiting for the user
    def time_waits(self) -> None:
        """Wrap ``input()`` and ``getpass()`` so the time a loop spends waiting for the
        person at the keyboard does not count towards its time limit, also when it
        calls them through a helper function. ipykernel installs its own ``input`` for
        every cell, so this runs before each cell."""
        for owner, name in _WAITS:
            func = getattr(owner, name, None)
            if func is not None and not getattr(func, "__loop_autopsy_wait__", False):
                setattr(owner, name, self._timed(func))

    def untime_waits(self) -> None:
        for owner, name in _WAITS:
            func = getattr(owner, name, None)
            if getattr(func, "__loop_autopsy_wait__", False) and getattr(func, "__self_monitor__", None) is self:
                setattr(owner, name, getattr(func, "__wrapped__"))

    def _timed(self, func):
        monitor = self

        @functools.wraps(func)
        def waiting(*args, **kwargs):
            start = time.perf_counter()
            monitor._waiting += 1
            try:
                return func(*args, **kwargs)
            finally:
                monitor._waiting -= 1
                monitor.waited += time.perf_counter() - start

        waiting.__loop_autopsy_wait__ = True  # type: ignore[attr-defined]
        waiting.__self_monitor__ = self  # type: ignore[attr-defined]
        return waiting

    # ------------------------------------------------------------------ recursion limit
    def guard_recursion_limit(self) -> None:
        """Wrap ``sys.setrecursionlimit`` to warn, once, about a limit so high that a
        runaway recursion crashes the kernel (no report is possible then)."""
        func = sys.setrecursionlimit
        if SAFE_RECURSION is None:
            return
        if getattr(func, "__loop_autopsy_limit__", False):
            # Already wrapped, maybe by an older Monitor that was never unloaded: take it
            # over, so this Monitor's notes and unguard_recursion_limit() apply to it.
            func.__self_monitor__ = self  # type: ignore[attr-defined]
            return

        @functools.wraps(func)
        def setrecursionlimit(limit):
            func(limit)
            monitor = setrecursionlimit.__self_monitor__  # type: ignore[attr-defined]
            safe = SAFE_RECURSION
            if (monitor.enabled and safe is not None and isinstance(limit, int) and limit > safe
                    and not monitor._noted_limit):
                monitor._noted_limit = True
                _print_note(
                    f"[loop-autopsy] Note: with a recursion limit of {limit:,}, a recursion that never stops "
                    f"can crash the kernel (Python runs out of C stack) before it raises `RecursionError`, and "
                    f"then loop-autopsy cannot show a report. Up to about {safe:,} is usually safe; for deeper "
                    f"work, a loop with a list as a stack is safer than recursion.")

        setrecursionlimit.__loop_autopsy_limit__ = True  # type: ignore[attr-defined]
        setrecursionlimit.__self_monitor__ = self  # type: ignore[attr-defined]
        sys.setrecursionlimit = setrecursionlimit

    def unguard_recursion_limit(self) -> None:
        func = sys.setrecursionlimit
        if getattr(func, "__self_monitor__", None) is self:
            sys.setrecursionlimit = getattr(func, "__wrapped__")

    # ------------------------------------------------------------------ %run scripts
    def watch_scripts(self, transformer) -> None:
        """``%run script.py`` compiles the file with ``compile()``, past IPython's AST
        transformers, so its loops would not be watched. For the one ``compile()``
        call that reads the script, the loops are rewritten first."""
        shell = self.shell
        if shell is None or "safe_execfile" in vars(shell):
            return
        original = shell.safe_execfile
        monitor = self

        @functools.wraps(original)
        def safe_execfile(fname, *where, **kwargs):
            if monitor.is_paused() or kwargs.get("shell_futures"):
                return original(fname, *where, **kwargs)
            if where and isinstance(where[0], dict):
                where[0][RUNTIME_NAME] = monitor  # the script runs in a namespace of its own
            real_compile = builtins.compile
            target = os.path.abspath(str(fname))

            def compile_script(source, filename, mode, *args, **kw):
                builtins.compile = real_compile  # once only
                if mode != "exec" or args or kw or os.path.abspath(str(filename)) != target:
                    return real_compile(source, filename, mode, *args, **kw)
                try:
                    tree = real_compile(source, filename, "exec", ast.PyCF_ONLY_AST, True)
                except SyntaxError:
                    return real_compile(source, filename, mode)  # raise the error the normal way
                return real_compile(transformer.visit(tree), filename, "exec", 0, True)

            builtins.compile = compile_script
            caller_raises = kwargs.get("raise_exceptions", False)
            try:
                # IPython would catch a stop itself and print a plain traceback, so errors
                # come back here: ours get a report, the rest are shown as IPython would.
                return original(fname, *where, **{**kwargs, "raise_exceptions": True})
            except SystemExit:
                if caller_raises:
                    raise
                if not kwargs.get("exit_ignore", False):
                    shell.showtraceback(exception_only=True)
            except BaseException:
                if caller_raises:
                    raise
                etype, value, tb = sys.exc_info()
                if etype is not None and issubclass(etype, OUR_EXCEPTIONS):
                    monitor._handle(shell, etype, value, tb)
                else:
                    shell.showtraceback(tb_offset=3)  # hide this wrapper and IPython's own frames
            finally:
                if builtins.compile is compile_script:
                    builtins.compile = real_compile

        safe_execfile.__self_monitor__ = self  # type: ignore[attr-defined]
        shell.safe_execfile = safe_execfile

    def unwatch_scripts(self) -> None:
        shell = self.shell
        ours = vars(shell).get("safe_execfile") if shell is not None else None
        if getattr(ours, "__self_monitor__", None) is self:
            del shell.safe_execfile

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
        state = LoopState(info, started=time.perf_counter(), replay_rows=self.settings.replay_rows)
        thread = threading.get_ident()
        state.scratch["thread"], state.scratch["waited0"] = thread, self.waited
        stack = self.stacks.setdefault(loop_id, [])
        if stack and stack[-1].scratch.get("thread") != thread:
            # The same loop running in two threads at once: its checks could mix both
            # runs' values, so only the time and memory rules are used for them.
            state.scratch["shared"] = stack[-1].scratch["shared"] = True
        stack.append(state)
        self.loops_running += 1
        self.states[loop_id] = state
        self._begun.add(loop_id)
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

    # ------------------------------------------------------------------ comprehensions, list(it), sum(it)
    def watch(self, loop_id: int, iterable: Any) -> Any:
        """Called with each ``for`` clause's iterable in a comprehension. Returns an
        iterator over the same items that checks the time limit and memory as it goes.
        A list/set/dict comprehension over an iterator that never runs out is stopped
        at once: it cannot stop early, so it could only end by running out of memory."""
        if not self.enabled or not 0 <= loop_id < len(self.infos):
            return iterable
        info = self.infos[loop_id]
        if not info.lazy:
            kind = loop_notes.endless_iterable(iterable)
            if kind:
                self._stop_now(info, detectors.endless_finding(info, kind))
        return self._items(loop_id, info, iter(iterable), _length(iterable))

    def feed(self, loop_id: int, func: Any, iterable: Any) -> Any:
        """Called with the argument of ``list(it)``, ``sum(it)``, ``", ".join(it)`` and
        the like, which loop over it in C. A list, range or other sized value is handed
        back untouched; an iterator is stopped at once if it never runs out (unless the
        function may stop early, like ``any``), otherwise watched for time and memory."""
        known = id(func) in _CONSUMER_IDS or (type(func) is str and func == "join")
        if (not self.enabled or not known or not 0 <= loop_id < len(self.infos)
                or not isinstance(iterable, Iterator)):
            return iterable
        info = self.infos[loop_id]
        if not info.lazy:
            kind = loop_notes.endless_iterable(iterable)
            if kind:
                self._stop_now(info, detectors.endless_finding(info, kind))
        return self._items(loop_id, info, iterable, None)

    def _stop_now(self, info: LoopInfo, finding: Finding) -> None:
        state = LoopState(info, started=time.perf_counter(), replay_rows=self.settings.replay_rows)
        state.stopped = finding
        self.stats["stopped"] += 1
        self.last = (finding, state, 0.0)
        stopped = LoopStopped(finding)
        stopped.report = self.last  # type: ignore[attr-defined]
        raise stopped

    def _items(self, loop_id: int, info: LoopInfo, it: Any, total: Optional[int]):
        """The items of ``it``, one by one, with a check every few items."""
        state = LoopState(info, started=time.perf_counter(), replay_rows=self.settings.replay_rows)
        state.total = total
        state.scratch["waited0"] = self.waited
        self.states[loop_id] = state
        rounds, next_check = 0, 1
        try:
            for item in it:
                rounds += 1
                if rounds >= next_check:
                    state.rounds = rounds
                    next_check = rounds + self._check_items(state)
                yield item
        finally:
            state.rounds = rounds

    def _check_items(self, state: LoopState) -> int:
        """The time and memory rules for a comprehension; returns the items until the next check."""
        info, settings = state.info, self.settings
        now = time.perf_counter()
        state.checks += 1
        self._update_stride(state, now)
        finding = None
        if state.checks % 16 == 0:
            finding = _safely(state, "memory", growth.check_memory, info, state, {}, settings)
        # A generator expression used by a watched loop is timed by that loop: the time
        # between its items is mostly the loop's own work.
        if finding is None and not (info.lazy and self.loops_running > 0):
            loop_now = now - (self.waited - state.scratch.get("waited0", self.waited))
            finding = (_safely(state, "estimate", detectors.check_estimate, info, state, settings, loop_now)
                       or _safely(state, "time", detectors.check_time, info, state, settings, loop_now))
        if finding is not None:
            state.stopped = finding
            self.stats["stopped"] += 1
            self.last = (finding, state, now - state.started)
            stopped = LoopStopped(finding)
            stopped.report = self.last  # type: ignore[attr-defined]
            raise stopped
        return state.stride

    @staticmethod
    def _update_stride(state: LoopState, now: float) -> None:
        """Check every round at first, then less often, but often again when rounds are slow."""
        gap, state.last_check = now - state.last_check, now
        if not state.scratch.get("rep_refine"):
            base = 256 if state.rounds >= 100_000 else 16 if state.rounds >= 1_000 else 1
            state.stride = base if gap <= SLOW_GAP else max(1, min(base, state.stride) // 4)

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
        self._update_stride(state, now)

        settings = self.settings
        finding = None
        # In two threads at once, or with an await (other tasks run meanwhile), the values
        # can change without the loop changing them: only the time and memory rules apply.
        if not state.scratch.get("shared") and not info.suspends:
            finding = (_safely(state, "repeat", detectors.check_repeat, info, state, values)
                       or _safely(state, "direction", detectors.check_direction, info, state, values, settings)
                       or _safely(state, "growing", growth.check_growing_iterable, info, state, values, settings))
        if finding is None and state.checks % 16 == 0:
            finding = _safely(state, "memory", growth.check_memory, info, state, values, settings)
        if finding is None:
            loop_now = now - (self.waited - state.scratch.get("waited0", self.waited))  # minus input() time
            finding = (_safely(state, "estimate", detectors.check_estimate, info, state, settings, loop_now)
                       or _safely(state, "time", detectors.check_time, info, state, settings, loop_now))
        state.next_check = state.rounds + state.stride
        if finding is not None:
            state.stopped = finding
            self.stats["stopped"] += 1
            self.last = (finding, state, now - state.started)
            stopped = LoopStopped(finding)
            stopped.report = self.last  # type: ignore[attr-defined]  # for a loop stopped in a thread
            raise stopped

    def _values(self, loop_id: int, info: LoopInfo, state: LoopState, namespace: Dict[str, Any],
                global_ns: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        """The watched variables right now. Inside a function ``locals()`` misses the
        globals the loop uses (``while state['i'] < 5``, ``global n``), so those are read
        from ``globals()``. A condition variable found nowhere switches the stuck and
        cycle rules off: an unchanged state proves nothing if part of it is unseen."""
        values: Dict[str, Any] = {}
        local_names = None
        chains = []
        for name in info.watched:
            if "." in name:
                chains.append(name)
                continue
            if name in info.attr_only:
                continue  # followed through its attributes below
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
                values[name] = _plain(value)
        for chain in chains:  # self.n, c.count: read without running the object's code
            base_name, attr = chain.split(".", 1)
            if base_name in values:
                continue
            base = namespace.get(base_name, _MISSING)
            if base is _MISSING and global_ns is not None:
                base = global_ns.get(base_name, _MISSING)
            if base is _MISSING or isinstance(base, types.ModuleType):
                continue
            if type(base) in _PLAIN_TYPES:  # y.real on an int: the int itself says it all
                values[base_name] = base
                continue
            value = _read_attr(base, attr)
            if value is _MISSING:
                if chain in info.cond_names or base_name in info.attr_only:
                    state.scratch.setdefault("rep_off", "unseen")  # part of the state is hidden
                continue
            if not _skip(value):
                values[chain] = _plain(value)
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

    # ------------------------------------------------------------------ watchdog
    def start_watchdog(self) -> None:
        """A background thread that stops a loop whose current round never ends (a
        ``time.sleep``, a network call without a timeout, a blocking queue): the time
        limit is otherwise only checked when the next round starts."""
        if self._watchdog is not None:
            return
        stop = threading.Event()
        thread = threading.Thread(target=self._watchdog_loop, args=(stop,), name="loop-autopsy watchdog",
                                  daemon=True)
        self._watchdog = (thread, stop)
        thread.start()

    def stop_watchdog(self) -> None:
        if self._watchdog is not None:
            thread, stop = self._watchdog
            stop.set()
            thread.join(2)
            self._watchdog = None

    def _watchdog_loop(self, stop: threading.Event) -> None:
        while not stop.wait(WATCHDOG_EVERY):
            try:
                self.watchdog_tick()
            except Exception:  # never let the watchdog die or disturb the kernel
                pass

    def watchdog_tick(self, now: Optional[float] = None) -> Optional[Finding]:
        """One look at the innermost loop running on the main thread. When it is past
        its time limit and its round counter has not moved for ``WATCHDOG_QUIET``
        seconds, the main thread is interrupted, and the report says why."""
        settings = self.settings
        if not self.enabled or settings.time_limit <= 0 or self._pending_stop is not None or self._waiting:
            return None
        main = threading.main_thread().ident
        running = [state for stack in list(self.stacks.values()) for state in list(stack)
                   if state.scratch.get("thread") == main and state.stopped is None]
        if not running:
            return None
        state = max(running, key=lambda s: s.started)
        if state.info.waits_for_user:
            return None
        now = time.perf_counter() if now is None else now
        seen = state.scratch.get("wd_seen")
        if seen is None or seen[0] != state.rounds:  # a new round started since the last look
            state.scratch["wd_seen"] = (state.rounds, now)
            return None
        quiet = now - seen[1]
        elapsed = now - state.started - (self.waited - state.scratch.get("waited0", self.waited))
        if elapsed < settings.time_limit or quiet < WATCHDOG_QUIET:
            return None
        if signal.getsignal(signal.SIGINT) is not signal.default_int_handler:
            return None  # someone else handles ■ here: interrupting could do anything
        finding = detectors.blocked_finding(state.info, elapsed, quiet, settings)
        state.stopped = finding
        self._pending_stop = (finding, state, now - state.started)
        if hasattr(signal, "pthread_kill"):  # wakes a blocking call in the main thread too
            signal.pthread_kill(main, signal.SIGINT)  # type: ignore[arg-type]
        else:  # Windows: raised here, it still wakes time.sleep() in the main thread
            signal.raise_signal(signal.SIGINT)
        return finding

    # ------------------------------------------------------------------ reports
    def show_last(self) -> bool:
        if not self.last:
            return False
        finding, state, elapsed = self.last
        report.display_report(report.render_text(finding, state, elapsed),
                              report.render_html(finding, state, elapsed))
        return True

    def install_exception_handler(self) -> None:
        """Show reports for our exceptions. A handler another extension installed
        before us keeps working: its exception types are still sent to it."""
        old_types = tuple(self.shell.custom_exceptions or ())
        self._old_exc = (old_types, getattr(self.shell, "CustomTB", None))
        self.shell.set_custom_exc(OUR_EXCEPTIONS + tuple(t for t in old_types if t not in OUR_EXCEPTIONS),
                                  self._handle)
        self._old_thread_hook = threading.excepthook
        threading.excepthook = self._thread_hook

    def remove_exception_handler(self) -> None:
        if threading.excepthook == self._thread_hook and self._old_thread_hook is not None:
            threading.excepthook = self._old_thread_hook
        self._old_thread_hook = None
        if self._old_exc is None:
            return
        old_tuple, old_tb = self._old_exc
        self.shell.custom_exceptions = old_tuple
        if old_tb is not None:
            self.shell.CustomTB = old_tb
        self._old_exc = None

    def _thread_hook(self, args) -> None:
        """A loop stopped inside a thread: show its report instead of a raw
        "Exception in thread" traceback through loop-autopsy's code."""
        stopped = args.exc_value
        saved = getattr(stopped, "report", None)
        if isinstance(stopped, LoopStopped) and saved:
            finding, state, elapsed = saved
            report.display_report(report.render_text(finding, state, elapsed),
                                  report.render_html(finding, state, elapsed))
            return
        (self._old_thread_hook or threading.__excepthook__)(args)

    def _delegate(self, etype) -> Optional[Any]:
        """The handler that was installed before us, if it handles ``etype``."""
        if self._old_exc is None:
            return None
        old_types, old_tb = self._old_exc
        return old_tb if old_tb is not None and etype in old_types else None

    def _handle(self, shell, etype, value, tb, tb_offset=None):
        if not issubclass(etype, OUR_EXCEPTIONS):
            old = self._delegate(etype)
            return old(etype, value, tb, tb_offset=tb_offset) if old is not None else None
        if issubclass(etype, LoopStopped):
            self.show_last()
            return []
        if issubclass(etype, RecursionError):
            return self._handle_recursion(shell, etype, value, tb, tb_offset)
        # KeyboardInterrupt: keep the normal message (or the earlier handler's), then add the replay.
        old = self._delegate(etype)
        stb: Any = []
        if old is not None:
            stb = old(etype, value, tb, tb_offset=tb_offset)
        else:
            shell.showtraceback((etype, value, _without_our_frames(tb)), tb_offset=tb_offset)
        pending, self._pending_stop = self._pending_stop, None
        if pending is not None:  # the watchdog stopped a loop stuck inside one round
            self.stats["stopped"] += 1
            self.last = pending
            self.show_last()
            return stb
        state = self.active
        if (state is not None and state.stopped is None and state.history
                and time.perf_counter() - self.active_time < INTERRUPT_WINDOW):
            finding = report.interrupt_finding(state)
            elapsed = time.perf_counter() - state.started
            self.last = (finding, state, elapsed)
            report.display_report(report.render_text(finding, state, elapsed),
                                  report.render_html(finding, state, elapsed))
        return stb

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
            old = self._delegate(etype)
            if old is not None:
                return old(etype, value, tb, tb_offset=tb_offset)
            shell.showtraceback((etype, value, _without_our_frames(tb)), tb_offset=tb_offset)
            return []
        finding, state = explained
        self.last = (finding, state, None)
        self.show_last()
        return []
