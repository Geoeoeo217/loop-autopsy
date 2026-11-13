"""
Each ``check_*`` function receives the loop's static ``LoopInfo``, its run-time
``LoopState`` and ``values`` (the watched variables right now) and returns a
``Finding`` when it can prove the loop will not end, otherwise ``None``.
Private bookkeeping lives in ``state.scratch``.
"""

from __future__ import annotations

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
    moving = left if left[0] == "name" else right  # the variable side we talk about
    var = moving[1]
    target = b if moving is left else a
    now_val = values.get(var)

    if op == "!=":
        gap = b - a
        prev = s.get("jump_prev")
        if prev is not None and (prev > 0) != (gap > 0):
            s["jump_crossed"] = (s.get("jump_prev_val"), now_val)
            s["jump_streak"] = 0
        elif s.get("jump_crossed") and prev is not None and abs(gap) > abs(prev):
            s["jump_streak"] = s.get("jump_streak", 0) + 1
        elif s.get("jump_crossed"):
            s["jump_streak"] = 0
        s["jump_prev"], s["jump_prev_val"] = gap, now_val
        if s.get("jump_crossed") and s.get("jump_streak", 0) >= settings.jump_after:
            before, after = s["jump_crossed"]
            return Finding(
                "jumped_over", "loop jumped over its stop value",
                f"`{info.test_src}` only becomes False when `{var}` is exactly {_show(target)}. "
                f"`{var}` went from {_show(before)} to {_show(after)}, jumping over {_show(target)}, "
                f"and has moved further away ever since (now {_show(now_val)}).",
                f"Use `<` or `>` instead of `!=` in the condition, or change the step so `{var}` "
                f"lands exactly on {_show(target)}.",
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
    s["dir_prev"], s["dir_prev_val"] = gap, now_val
    if s.get("dir_streak", 0) >= settings.direction_after:
        return Finding(
            "wrong_direction", "loop moving the wrong way",
            f"The loop stops when `{info.test_src}` becomes False, which needs `{var}` to reach {_show(target)}. "
            f"But over the last {s['dir_streak']:,} checks `{var}` went from {_show(s.get('dir_start'))} "
            f"to {_show(now_val)}, moving further away from {_show(target)} every time.",
            f"Check the line that updates `{var}`: should it go the other way (for example `+=` instead of `-=`)?",
            (var,),
        )
    return None


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
    return Finding(
        "time_limit", "loop ran too long",
        f"This loop has been running for {elapsed:.0f} s ({state.rounds:,} rounds). loop-autopsy could not prove "
        f"that it is stuck{reason}. It might just be slow, or stuck in a way the rules cannot see.",
        "Use the replay table to see whether the values move towards the stop condition. "
        f"To allow longer loops: `%autopsy config time_limit={int(settings.time_limit * 5)}`.",
    )
