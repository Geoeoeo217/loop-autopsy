"""Explain runaway recursion.

A function that keeps calling itself is the recursive cousin of an infinite
loop. Python already stops it with ``RecursionError``; this module reads the
traceback, finds the function from a notebook cell that called itself, and
shows how its arguments changed from the first call to the last, in the same
report format as a stopped loop.

``analyse`` is a pure function on plain argument dictionaries, so it is easy to
test; ``explain`` does the traceback reading.
"""

from __future__ import annotations

import sys
from collections import Counter
from decimal import Decimal
from fractions import Fraction
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from . import report
from .config import Finding, LoopInfo, LoopState
from .detectors import OPAQUE, full_fp

MIN_CALLS = 50  # fewer nested calls than this is not "runaway"
COMPANION_SHARE = 0.25  # other functions with this share of calls take part (mutual recursion)
CO_VARARGS, CO_VARKEYWORDS = 0x04, 0x08


# --------------------------------------------------------------------------- traceback side
def is_cell_file(shell, filename: str) -> bool:
    """True when ``filename`` is code typed in a notebook cell."""
    if filename.startswith("<ipython-input-"):
        return True
    return filename in getattr(getattr(shell, "compile", None), "_filename_map", {})


def argument_names(code) -> Tuple[str, ...]:
    """Parameter names of a function, in order (including ``*args`` and ``**kwargs``)."""
    count = code.co_argcount + code.co_kwonlyargcount
    count += bool(code.co_flags & CO_VARARGS) + bool(code.co_flags & CO_VARKEYWORDS)
    return tuple(code.co_varnames[:count])


def find_recursion(tb, is_cell: Callable[[str], bool]):
    """Return ``(code, frames, companions)`` for the cell function that recursed
    most in this traceback, or ``None``. ``frames`` run from the first call to
    the last; ``companions`` are other cell functions in the cycle."""
    frames: Dict[Any, list] = {}
    while tb is not None:
        code = tb.tb_frame.f_code
        if code.co_name != "<module>" and is_cell(code.co_filename):
            frames.setdefault(code, []).append(tb.tb_frame)
        tb = tb.tb_next
    if not frames:
        return None
    counts = Counter({code: len(fs) for code, fs in frames.items()})
    code, top = counts.most_common(1)[0]
    if top < MIN_CALLS:
        return None
    companions = tuple(c.co_name for c, n in counts.most_common()
                       if c is not code and n >= top * COMPANION_SHARE)
    return code, frames[code], companions


# --------------------------------------------------------------------------- analysis
def _measure(value: Any) -> Optional[Tuple[float, str]]:
    """A number to follow for one argument: the value itself, or a container's length."""
    if isinstance(value, (int, float, Fraction, Decimal)) and not isinstance(value, bool):
        return value, ""
    if isinstance(value, (str, bytes, list, tuple, dict, set, frozenset)):
        return len(value), "length of "
    return None


def _show(value: Any) -> str:
    return f"{value:,}" if isinstance(value, int) and not isinstance(value, bool) else report.short_repr(value, 30)


def _args_text(args: Dict[str, Any]) -> str:
    return ", ".join(f"`{n} = {report.short_repr(v, 30)}`" for n, v in args.items())


def _cycle_length(fps: Sequence[Any]) -> int:
    """Smallest k > 1 such that the last calls repeat every k calls, else 0."""
    tail = fps[-60:]
    for k in range(2, len(tail) // 3 + 1):
        if all(tail[i] == tail[i - k] for i in range(k, len(tail))):
            return k
    return 0


def analyse(name: str, calls: List[Dict[str, Any]], companions: Tuple[str, ...] = (),
            limit: Optional[int] = None) -> Finding:
    """Explain why ``name`` never stopped calling itself.

    ``calls`` holds the arguments of every nested call, first call first.
    """
    limit = limit or sys.getrecursionlimit()
    n = len(calls)
    who = f"`{name}`"
    if companions:
        others = " and ".join(f"`{c}`" for c in companions)
        who_calls = f"{who} and {others} kept calling each other"
    else:
        who_calls = f"{who} kept calling itself"
    opener = f"{who_calls} ({n:,} nested calls) until Python stopped it with `RecursionError`."
    base_hint = (f"Every recursive function needs a base case: an `if` that returns without calling "
                 f"{who} again. Check that it exists and that each call moves the arguments closer to it.")
    names = tuple(calls[0]) if calls else ()

    fps = [tuple((k, full_fp(v)) for k, v in c.items()) for c in calls]
    readable = all(fp is not OPAQUE for call in fps for _, fp in call)
    if readable and n >= 2 and all(fp == fps[0] for fp in fps):
        if not names:
            return Finding(
                "recursion_stuck", "runaway recursion", opener + f" {who} takes no arguments, so every call is "
                f"identical and nothing can ever reach a base case.",
                f"Give {who} a base case, or an argument that changes with each call.", ())
        return Finding(
            "recursion_stuck", "recursion stuck on the same arguments",
            opener + f" Every call received exactly the same arguments ({_args_text(calls[0])}), "
            f"so no call is any closer to the base case than the first one.",
            f"The recursive call must pass a changed value, for example a smaller number or a shorter list, "
            f"so that the base case is eventually reached.", names)
    if readable:
        k = _cycle_length(fps)
        if k:
            return Finding(
                "recursion_cycle", f"recursion stuck in a {k}-call cycle",
                opener + f" The arguments come back to exactly the same values every {k} calls, "
                f"so the calls go round in a circle and never reach the base case.",
                "Look at the table: the arguments repeat instead of moving towards the base case.", names)

    for arg in names:
        measured = [_measure(c.get(arg)) for c in calls]
        if any(m is None for m in measured) or n < 3:
            continue
        values = [m[0] for m in measured]
        label = f"{measured[0][1]}`{arg}`"
        first, last = values[0], values[-1]
        steps = [b - a for a, b in zip(values, values[1:])]
        if all(s == 0 for s in steps):
            continue
        if not (all(s <= 0 for s in steps) or all(s >= 0 for s in steps)):
            continue  # goes up and down: no single direction to talk about
        if (first > 0 > last) or (first < 0 < last):
            return Finding(
                "recursion_past_base", "recursion went past its base case",
                opener + f" {label[0].upper() + label[1:]} went from {_show(first)} to {_show(last)}, "
                f"passing 0 without stopping.",
                f"Check the base case: does it catch every value {label} can take? A test like `{arg} == 0` "
                f"is skipped when {label} jumps over 0; `{arg} <= 0` is safer.", (arg,))
        if abs(last) > abs(first):
            if measured[0][1]:  # a length: the container got longer
                hint = (f"If the base case needs `{arg}` to get shorter, each call should pass a shorter "
                        f"value (for example `{arg}[1:]`) instead of a longer one.")
            else:
                hint = (f"If the base case needs {label} to get smaller, the recursive call goes the wrong way "
                        f"(for example `{arg} + 1` instead of `{arg} - 1`).")
            return Finding(
                "recursion_wrong_way", "recursion going the wrong way",
                opener + f" {label[0].upper() + label[1:]} went from {_show(first)} to {_show(last)}, "
                f"moving further from 0 with every call.", hint, (arg,))
        return Finding(
            "recursion_too_deep", "recursion too deep for Python",
            opener + f" {label[0].upper() + label[1:]} was still moving towards 0 (from {_show(first)} to "
            f"{_show(last)}), so the function may be correct but need more than Python's limit of "
            f"about {limit:,} nested calls.",
            f"Start with a smaller value, or rewrite {who} as a loop, which has no depth limit.", (arg,))

    return Finding("recursion", "runaway recursion",
                   opener + " Here are its arguments in the first and last calls.", base_hint, ())


# --------------------------------------------------------------------------- report
def build_state(code, calls: List[Dict[str, Any]], replay_rows: int, max_watched: int) -> LoopState:
    """A LoopState that the normal report renderer can show: one row per call,
    the first calls then the last calls."""
    names = argument_names(code)[: max(1, max_watched)]
    info = LoopInfo(loop_id=-1, kind="recursion", lineno=code.co_firstlineno,
                    header=f"def {code.co_name}({', '.join(argument_names(code))}):",
                    cond_names=names, watched=names)
    rows = max(2, replay_rows)
    state = LoopState(info, started=0.0, replay_rows=rows)
    state.rounds = len(calls)
    head = rows // 2
    picked = list(range(min(head, len(calls))))
    picked += [i for i in range(max(head, len(calls) - (rows - head)), len(calls))]
    for i in picked:
        state.history.append((i + 1, {k: report.replay_value(v) for k, v in calls[i].items() if k in names}))
    return state


def explain(shell, tb, replay_rows: int = 6, max_watched: int = 25):
    """Return ``(finding, state)`` for a RecursionError traceback, or ``None``
    when it did not come from a function in a notebook cell."""
    found = find_recursion(tb, lambda f: is_cell_file(shell, f))
    if found is None:
        return None
    code, frames, companions = found
    names = argument_names(code)
    calls = []
    for frame in frames:
        local = frame.f_locals
        calls.append({k: local[k] for k in names if k in local})
    finding = analyse(code.co_name, calls, companions)
    return finding, build_state(code, calls, replay_rows, max_watched)
