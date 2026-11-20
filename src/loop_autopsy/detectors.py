"""
Each ``check_*`` function receives the loop's static ``LoopInfo``, its run-time
``LoopState`` and ``values`` (the watched variables right now) and returns a
``Finding`` when it can prove the loop will not end, otherwise ``None``.
Private bookkeeping lives in ``state.scratch``.
"""

from __future__ import annotations

import math
from collections import deque
from decimal import Decimal
from fractions import Fraction
from typing import Any, Dict, Optional

from .config import Finding, LoopInfo, LoopState, Settings

OPAQUE = object()  # "cannot see inside this value"
SCALARS = (bool, int, float, complex, Fraction, Decimal)
SMALL = 50  # containers up to this size are fingerprinted by content
MAX_STORED = 50_000  # fingerprints kept per loop run
FULL_BUDGET = 1_000_000  # elements visited by one full fingerprint
# Methods on these types only change the object itself, which is fingerprinted.
BUILTIN_RECEIVERS = (str, bytes, list, dict, set, frozenset, tuple, deque, bytearray, int, float, complex)


# --------------------------------------------------------------------------- fingerprints
def cheap_fp(value: Any, depth: int = 0) -> Any:
    """Fast fingerprint. Equal states always give equal results; different
    states *may* collide, which the full fingerprint then rules out."""
    t = type(value)
    if value is None or t in SCALARS:
        return value
    if t in (str, bytes):
        return value if len(value) <= 64 else (t.__name__, len(value), hash(value))
    if t is range:
        return ("range", value.start, value.stop, value.step)
    if depth >= 3:
        return (t.__name__, id(value)) if t in (list, dict, set, deque, bytearray, tuple, frozenset) else OPAQUE
    if t in (tuple, frozenset, list, set, deque, bytearray) and len(value) <= SMALL:
        parts = tuple(cheap_fp(x, depth + 1) for x in value)
        if any(p is OPAQUE for p in parts):
            return OPAQUE
        return (t.__name__, id(value) if t not in (tuple, frozenset) else 0) + parts
    if t is dict and len(value) <= SMALL:
        parts = tuple((cheap_fp(k, depth + 1), cheap_fp(v, depth + 1)) for k, v in value.items())
        if any(p is OPAQUE for pair in parts for p in pair):
            return OPAQUE
        return ("dict", id(value)) + parts
    if t in (tuple, frozenset, list, set, deque, bytearray, dict):
        return (t.__name__, id(value), len(value))
    return OPAQUE


def full_fp(value: Any, budget: Optional[list] = None, depth: int = 0) -> Any:
    """Complete, content-based fingerprint (hashable), or OPAQUE."""
    if budget is None:
        budget = [FULL_BUDGET]
    t = type(value)
    if value is None or t in SCALARS or t in (str, bytes):
        return value
    if t is range:
        return ("range", value.start, value.stop, value.step)
    if depth > 20:
        return OPAQUE  # e.g. a list that contains itself
    if t in (list, tuple, deque, bytearray, set, frozenset, dict):
        budget[0] -= len(value)
        if budget[0] < 0:
            return OPAQUE
        if t is dict:
            parts = []
            for k, v in value.items():
                fk, fv = full_fp(k, budget, depth + 1), full_fp(v, budget, depth + 1)
                if fk is OPAQUE or fv is OPAQUE:
                    return OPAQUE
                parts.append((fk, fv))
            return ("dict", tuple(parts))
        parts = []
        for x in value:
            fx = full_fp(x, budget, depth + 1)
            if fx is OPAQUE:
                return OPAQUE
            parts.append(fx)
        if t in (set, frozenset):
            return (t.__name__, frozenset(parts))
        return (t.__name__, tuple(parts))
    return OPAQUE


def _show(value: Any, width: int = 40) -> str:
    text = repr(value)
    return text if len(text) <= width else text[: width - 3] + "..."


def _numeric(x: Any) -> bool:
    return isinstance(x, (int, float, Fraction, Decimal)) and not isinstance(x, bool)


# --------------------------------------------------------------------------- R1 / R2
def check_repeat(info: LoopInfo, state: LoopState, values: Dict[str, Any]) -> Optional[Finding]:
    """R1 stuck / R2 cycle: the loop returned to an exact earlier state."""
    s = state.scratch
    if info.kind != "while" or not info.repeat_safe or s.get("rep_off"):
        return None
    for name in info.receivers:  # e.g. random.random(): hidden state, cannot prove anything
        if type(values.get(name, OPAQUE)) not in BUILTIN_RECEIVERS:
            s["rep_off"] = "calls"
            return None
    cheap = []
    for name, value in values.items():
        fp = cheap_fp(value)
        if fp is OPAQUE:
            s["rep_off"] = True
            return None
        cheap.append((name, fp))
    try:
        key = hash(tuple(cheap))
    except TypeError:
        s["rep_off"] = True
        return None
    seen = s.setdefault("rep_seen", {})
    if key not in seen:
        if len(seen) < MAX_STORED:
            seen[key] = state.rounds
        return None

    full = tuple((n, full_fp(v)) for n, v in values.items())
    if any(fp is OPAQUE for _, fp in full):
        s["rep_off"] = True
        return None
    try:
        fkey = hash(full)
    except TypeError:
        s["rep_off"] = True
        return None
    full_seen = s.setdefault("rep_full", {})
    if fkey not in full_seen:
        if len(full_seen) < MAX_STORED:
            full_seen[fkey] = state.rounds
        return None

    if state.stride > 1:
        # Sampled checks cannot tell the exact cycle length: re-measure every round.
        s["rep_refine"] = True
        state.stride = 1
        state.next_check = state.rounds + 1
        s["rep_seen"], s["rep_full"] = {}, {}
        return None
    distance = state.rounds - full_seen[fkey]
    return _stuck_finding(info, values) if distance <= 1 else _cycle_finding(info, values, distance)


def _cond_values(info: LoopInfo, values: Dict[str, Any]) -> str:
    return ", ".join(f"`{n} = {_show(values[n])}`" for n in info.cond_names if n in values)


def _stuck_finding(info: LoopInfo, values: Dict[str, Any]) -> Finding:
    cond = info.test_src or "True"
    if info.cond_names:
        names = ", ".join(f"`{n}`" for n in info.cond_names)
        explanation = (
            f"Nothing changed between rounds: {_cond_values(info, values)} stayed exactly the same, "
            f"so the condition `{cond}` is True every time and the loop can never end."
        )
        example = ""
        if info.compare and info.compare[0][0] == "name":
            var, op = info.compare[0][1], info.compare[1]
            if op in ("<", "<="):
                example = f" (for example `{var} += 1`)"
            elif op in (">", ">="):
                example = f" (for example `{var} -= 1`)"
        hint = f"Add a line inside the loop that changes {names} so that `{cond}` eventually becomes False{example}."
    else:
        explanation = (
            f"The condition `{cond}` never changes and nothing inside the loop changes either, "
            f"so every round is identical and no `break` is ever reached."
        )
        hint = "Add a `break` that runs when the work is done, or use a condition that can become False."
    return Finding("stuck", "stuck loop", explanation, hint, tuple(info.cond_names))


def _cycle_finding(info: LoopInfo, values: Dict[str, Any], k: int) -> Finding:
    cond = info.test_src or "True"
    shown = _cond_values(info, values)
    explanation = (
        f"Every {k} rounds the loop comes back to exactly the same values"
        f"{' (' + shown + ')' if shown else ''}. "
        f"Nothing else can change them, so it will repeat this {k}-round cycle forever."
    )
    hint = f"Look at the replay table: the values go round in a circle instead of moving towards making `{cond}` False."
    return Finding("cycle", f"loop stuck in a {k}-round cycle", explanation, hint, tuple(info.cond_names))


# --------------------------------------------------------------------------- R3 / R4
def _side_value(side, values):
    kind, data = side
    return data if kind == "const" else values.get(data)


def check_direction(info: LoopInfo, state: LoopState, values: Dict[str, Any],
                    settings: Settings) -> Optional[Finding]:
    """R3 wrong direction and R4 jumped over, for simple comparisons."""
    if info.kind != "while" or not info.compare:
        return None
    left, op, right = info.compare
    a, b = _side_value(left, values), _side_value(right, values)
    if not (_numeric(a) and _numeric(b)):
        return None
    s = state.scratch
    sides = (left, right)
    now = (a, b)  # both sides are remembered: with two variables, either one may be the one that moves

    if op == "!=":
        gap = b - a
        prev = s.get("jump_prev")
        if prev is not None and (prev > 0) != (gap > 0):
            s["jump_crossed"] = (s.get("jump_prev_val"), now)
            s["jump_streak"] = 0
        elif s.get("jump_crossed") and prev is not None and abs(gap) > abs(prev):
            s["jump_streak"] = s.get("jump_streak", 0) + 1
        elif s.get("jump_crossed"):
            s["jump_streak"] = 0
        s["jump_prev"], s["jump_prev_val"] = gap, now
        if s.get("jump_crossed") and s.get("jump_streak", 0) >= settings.jump_after:
            before, after = s["jump_crossed"]
            moved = _moved(sides, before, after)
            if len(moved) == 2:
                first, second = left[1], right[1]
                return Finding(
                    "jumped_over", "loop jumped over its stop value",
                    f"`{info.test_src}` only becomes False when `{first}` and `{second}` are exactly equal. "
                    f"`{first}` went from {_show(before[0])} to {_show(after[0])} and `{second}` from "
                    f"{_show(before[1])} to {_show(after[1])}, passing each other without ever being equal, "
                    f"and they have moved further apart ever since (now {_show(a)} and {_show(b)}).",
                    f"Use `<` or `>` instead of `!=` in the condition, or change the steps so `{first}` "
                    f"and `{second}` land on the same value.",
                    (first, second),
                )
            i = moved[0]
            var, target = sides[i][1], _other_side(sides[1 - i], now[1 - i])
            return Finding(
                "jumped_over", "loop jumped over its stop value",
                f"`{info.test_src}` only becomes False when `{var}` is exactly {target}. "
                f"`{var}` went from {_show(before[i])} to {_show(after[i])}, jumping over {target}, "
                f"and has moved further away ever since (now {_show(now[i])}).",
                f"Use `<` or `>` instead of `!=` in the condition, or change the step so `{var}` "
                f"lands exactly on {target}.",
                (var,),
            )
        return None

    gap = (b - a) if op in ("<", "<=") else (a - b)
    prev = s.get("dir_prev")
    if prev is not None and gap > prev:
        if s.get("dir_streak", 0) == 0:
            s["dir_start"] = s.get("dir_prev_val")
        s["dir_streak"] = s.get("dir_streak", 0) + 1
    else:
        s["dir_streak"] = 0
    s["dir_prev"], s["dir_prev_val"] = gap, now
    if s.get("dir_streak", 0) >= settings.direction_after:
        start = s["dir_start"]
        moved = _moved(sides, start, now)
        if len(moved) == 2:
            first, second = left[1], right[1]
            return Finding(
                "wrong_direction", "loop moving the wrong way",
                f"The loop stops when `{info.test_src}` becomes False, which needs `{first}` and `{second}` "
                f"to meet. But over the last {s['dir_streak']:,} checks they moved further apart every time: "
                f"`{first}` went from {_show(start[0])} to {_show(a)} and `{second}` from {_show(start[1])} "
                f"to {_show(b)}.",
                f"Check the lines that update `{first}` and `{second}`: at least one of them should go "
                f"the other way.",
                (first, second),
            )
        i = moved[0]
        var, target = sides[i][1], _other_side(sides[1 - i], now[1 - i])
        fix = "`-=` instead of `+=`" if now[i] > start[i] else "`+=` instead of `-=`"
        return Finding(
            "wrong_direction", "loop moving the wrong way",
            f"The loop stops when `{info.test_src}` becomes False, which needs `{var}` to reach {target}. "
            f"But over the last {s['dir_streak']:,} checks `{var}` went from {_show(start[i])} "
            f"to {_show(now[i])}, moving further away from {target} every time.",
            f"Check the line that updates `{var}`: should it go the other way (for example {fix})?",
            (var,),
        )
    return None


def _moved(sides, before, after):
    """Which sides of the comparison changed between two readings: ``[0]`` for
    the left one, ``[1]`` for the right one, ``[0, 1]`` for both.

    Only variables count. ``while lo < hi`` can go wrong because ``lo`` moves,
    because ``hi`` moves, or both, and the report must name the right one.
    """
    names = [i for i, side in enumerate(sides) if side[0] == "name"]
    changed = [i for i in names if before[i] != after[i]]
    return changed or names[:1]


def _other_side(side, value) -> str:
    """How the side a variable is compared with is shown: its value, with its
    name in front when it is a variable too."""
    return _show(value) if side[0] == "const" else f"`{side[1]}` ({_show(value)})"


# --------------------------------------------------------------------------- R8
ESTIMATE_MARGIN = 2.0  # stop early only when the estimate is at least this many times the time limit


def duration(seconds: float) -> str:
    """``45 s``, ``12 min``, ``3.5 hours`` or ``4 days``: how long a wait feels."""
    if seconds < 90:
        return f"{seconds:.0f} s"
    if seconds < 90 * 60:
        return f"{seconds / 60:.0f} min"
    if seconds < 48 * 3600:
        return f"{seconds / 3600:.1f} hours"
    return f"{seconds / 86400:,.0f} days"


def _progress(state: LoopState) -> Optional[tuple]:
    """``(rounds finished, total rounds, "12.5%")`` for a for loop of known length."""
    total, done = state.total, state.rounds - 1  # the current round has only just started
    if not total or done < 1 or state.rounds > total:  # more rounds than items: the iterable grew
        return None
    share = done / total
    return done, total, (f"{share:.1%}" if share >= 0.001 else "less than 0.1%")


def check_estimate(info: LoopInfo, state: LoopState, settings: Settings, now: float) -> Optional[Finding]:
    """R8: a for loop that, at its current speed, needs far longer than the time limit.

    It would be stopped by the time limit anyway, so it is stopped as soon as
    that is clear, with an estimate instead of a minute of waiting. Loops that
    can end early (a ``break``, ``return`` or ``raise``) are left to the time
    limit: a search loop may well finish long before its last item.
    """
    if (info.kind != "for" or info.exits or info.waits_for_user
            or settings.time_limit <= 0 or settings.estimate_after <= 0):
        return None
    elapsed = now - state.started
    if elapsed < settings.estimate_after or elapsed >= settings.time_limit:
        return None
    progress = _progress(state)
    if progress is None:
        return None
    done, total, share = progress
    needed = elapsed * total / done
    if needed < ESTIMATE_MARGIN * settings.time_limit:
        return None
    allow = int(math.ceil(needed * 1.25 / 60) * 60)
    return Finding(
        "too_slow", "loop would take too long",
        f"After {elapsed:.0f} s this loop has finished {done:,} of its {total:,} rounds ({share}). "
        f"At this speed it needs about {duration(needed)} in total, far more than the "
        f"{settings.time_limit:.0f} s time limit, so it was stopped now instead of after "
        f"{settings.time_limit:.0f} s. Nothing is wrong with the loop itself: it is just slow.",
        "Try it on a small part of the data first (for example the first 1,000 items) and check the result, "
        "or make each round do less work. To let it run to the end: "
        f"`%autopsy config time_limit={allow}`, or start the cell with `%%autopsy off`.",
    )


# --------------------------------------------------------------------------- R7
def check_time(info: LoopInfo, state: LoopState, settings: Settings, now: float) -> Optional[Finding]:
    """R7: honest fallback when nothing could be proven."""
    if settings.time_limit <= 0 or info.waits_for_user:
        return None
    elapsed = now - state.started
    if elapsed < settings.time_limit:
        return None
    reason = ""
    off = state.scratch.get("rep_off")
    if info.kind == "while" and (not info.repeat_safe or off == "calls"):
        reason = " (it calls functions whose effects loop-autopsy cannot check)"
    elif info.kind == "while" and off:
        reason = " (it uses values loop-autopsy cannot look inside)"
    extra = ""
    progress = _progress(state)
    if progress is not None:
        done, total, share = progress
        extra = (f" It had finished {done:,} of its {total:,} rounds ({share}); at this speed all of them "
                 f"would take about {duration(elapsed * total / done)}.")
    elif info.always_true and not info.exits:
        extra = " It also has no `break`, `return` or `raise`, so it could not end on its own."
    return Finding(
        "time_limit", "loop ran too long",
        f"This loop has been running for {elapsed:.0f} s ({state.rounds:,} rounds). loop-autopsy could not prove "
        f"that it is stuck{reason}. It might just be slow, or stuck in a way the rules cannot see.{extra}",
        "Use the replay table to see whether the values move towards the stop condition. "
        f"To allow longer loops: `%autopsy config time_limit={int(settings.time_limit * 5)}`.",
    )
