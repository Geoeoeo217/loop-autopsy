"""
Each ``check_*`` function receives the loop's static ``LoopInfo``, its run-time
``LoopState`` and ``values`` (the watched variables right now) and returns a
``Finding`` when it can prove the loop will not end, otherwise ``None``.
Private bookkeeping lives in ``state.scratch``.
"""

from __future__ import annotations

import math
import numbers
from collections import deque
from dataclasses import replace
from decimal import Decimal
from fractions import Fraction
from typing import Any, Dict, Optional

from .config import Finding, LoopInfo, LoopState, Settings
from .loop_notes import branch_hint, indent_hint

OPAQUE = object()  # "cannot see inside this value"
SCALARS = (bool, int, float, complex, Fraction, Decimal)
SMALL = 50  # containers up to this size are fingerprinted by content
MAX_STORED = 50_000  # fingerprints kept per loop run
FULL_BUDGET = 1_000_000  # elements visited by one full fingerprint
GUARD_BUDGET = 10_000  # elements visited per check for the other parts of an `and` condition
LONG_TEXT = 1000  # longer str/bytes are fingerprinted by length and hash
STORED_ELEMENTS = 2_000_000  # elements kept across all stored full fingerprints of one loop run
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
    if t in (str, bytes) and len(value) > LONG_TEXT:
        # Stored fingerprints must not keep a big string alive. String hashes are
        # randomised (SipHash), so unlike small ints, unequal strings do not collide.
        return (t.__name__, len(value), hash(value))
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
    return isinstance(x, (numbers.Real, Decimal)) and not isinstance(x, bool)


# --------------------------------------------------------------------------- R1 / R2
def check_repeat(info: LoopInfo, state: LoopState, values: Dict[str, Any]) -> Optional[Finding]:
    """R1 stuck / R2 cycle: the loop returned to an exact earlier state."""
    s = state.scratch
    if info.kind != "while" or not info.repeat_safe or s.get("rep_off"):
        return None
    if info.unwatched_changes:  # a changing variable is not watched: "nothing changed" is no proof
        s["rep_off"] = "partial"
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

    parts, size = [], 0
    for n, v in values.items():
        budget = [FULL_BUDGET]
        fp = full_fp(v, budget)
        if fp is OPAQUE:
            s["rep_off"] = True
            return None
        parts.append((n, fp))
        size += FULL_BUDGET - budget[0] + 1
    # The full fingerprint itself is the key, so a match means the states are equal:
    # a hash alone is not proof (in CPython hash(-1) == hash(-2)).
    full = tuple(parts)
    full_seen = s.setdefault("rep_full", {})
    try:
        if full not in full_seen:
            stored = s.get("rep_stored", 0)
            if len(full_seen) < MAX_STORED and stored + size <= STORED_ELEMENTS:
                full_seen[full] = state.rounds
                s["rep_stored"] = stored + size
            return None
    except TypeError:  # e.g. a signalling Decimal NaN cannot be hashed
        s["rep_off"] = True
        return None

    if state.stride > 1:
        # Sampled checks cannot tell the exact cycle length: re-measure every round.
        s["rep_refine"] = True
        state.stride = 1
        state.next_check = state.rounds + 1
        s["rep_seen"], s["rep_full"], s["rep_stored"] = {}, {}, 0
        return None
    distance = state.rounds - full_seen[full]
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
        if info.outside_update is not None:
            hint = indent_hint(info.outside_update)  # the line exists, but after the loop
        elif info.branch_updates:
            hint = branch_hint(info.branch_updates, values) or hint  # the line exists, in an if that never runs
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
# Built-in containers whose len() is cheap and cannot run any of the user's code.
LEN_TYPES = (list, tuple, str, bytes, bytearray, dict, set, frozenset, deque, range)


def _side_value(side, values):
    kind, data = side
    if kind == "const":
        return data
    value = values.get(data)
    if kind == "len":
        return len(value) if type(value) in LEN_TYPES else None
    return value


def _label(side) -> str:
    """How one side of the comparison is named in a report: ``i`` or ``len(nums)``."""
    return f"len({side[1]})" if side[0] == "len" else str(side[1])


def _comparable(a: Any, b: Any):
    """``a`` and ``b`` in types that can be subtracted: Python compares a Decimal with
    a float or a Fraction, but cannot subtract them, so those pairs become floats."""
    if isinstance(a, Decimal) != isinstance(b, Decimal):
        other = b if isinstance(a, Decimal) else a
        if isinstance(other, (float, Fraction)):
            return float(a), float(b)
    return a, b


def _shrank(step: Any, before: Any) -> bool:
    """Did the distance grow by less this time than the time before? Then the loop
    is slowing down and may still turn round (a ball thrown up comes back down).
    Floats get a tiny tolerance for rounding."""
    if isinstance(step, float) or isinstance(before, float):
        return step < before - 1e-9 * abs(before)
    return step < before


def _moving_away(s: dict, prefix: str, size: Any, rounds: int) -> bool:
    """One reading of the distance to the stop value (``size``): True when it grew
    by at least as much as the last time (per round), which a loop that will
    turn round cannot keep doing."""
    prev, prev_round = s.get(prefix + "prev_size"), s.get(prefix + "prev_round")
    s[prefix + "prev_size"], s[prefix + "prev_round"] = size, rounds
    if prev is None or not size > prev:
        s.pop(prefix + "step", None)
        return False
    step, span = size - prev, rounds - (prev_round or 0)
    before = s.get(prefix + "step")
    s[prefix + "step"] = (step, span)
    if before is not None and before[1] == span and _shrank(step, before[0]):
        return False
    return True


def check_direction(info: LoopInfo, state: LoopState, values: Dict[str, Any],
                    settings: Settings) -> Optional[Finding]:
    """R3 wrong direction and R4 jumped over, for simple comparisons.

    Moving away from the stop value only counts while it does not slow down:
    a loop whose steps shrink (a thrown ball, a decaying simulation) may still
    turn round and end.

    In ``a < 10 or b > 0`` each part is followed on its own: the loop goes on
    while any part is True, so one part moving the wrong way keeps it going.
    In ``i < 10 and not done`` the comparison counts only while the variables
    of the other parts stay the same (see ``transformer.and_spec``).
    """
    if info.kind != "while":
        return None
    if info.compare:
        return _check_spec(state, values, settings, info.compare, info.test_src, "")
    for k, (spec, src) in enumerate(info.compare_or):
        finding = _check_spec(state, values, settings, spec, src, f"or{k}_")
        if finding is not None:
            return _part_of(finding, f" The loop goes on while any part of `{info.test_src}` is True, "
                                     f"so this part alone keeps it going.")
    if info.compare_and is not None:
        # Run on every check, so keep it cheap: a big container in the other parts (e.g. the
        # `data` of `while i < n and data:`) is not followed, and without it there is no proof.
        guard = tuple(full_fp(values[n], [GUARD_BUDGET]) if n in values else OPAQUE for n in info.and_guard)
        s = state.scratch
        if any(fp is OPAQUE for fp in guard):
            return None  # the other parts cannot be followed: no proof
        if s.get("and_guard") != guard:  # the other parts changed: start counting again
            s["and_guard"], s["and_dir_streak"], s["and_jump_streak"] = guard, 0, 0
        spec, src = info.compare_and
        finding = _check_spec(state, values, settings, spec, src, "and_")
        if finding is not None:
            return _part_of(finding, f" The other parts of `{info.test_src}` did not change in that time, "
                                     f"so they cannot end the loop either.")
    return None


def _part_of(finding: Finding, sentence: str) -> Finding:
    return replace(finding, explanation=finding.explanation + sentence)


def _check_spec(state: LoopState, values: Dict[str, Any], settings: Settings,
                spec: tuple, src: str, p: str) -> Optional[Finding]:
    """R3 and R4 for one comparison ``spec`` (source text ``src``). ``p`` prefixes
    this comparison's keys in ``state.scratch``, so the parts of an ``or`` are
    followed separately."""
    left, op, right = spec
    a, b = _side_value(left, values), _side_value(right, values)
    if not (_numeric(a) and _numeric(b)):
        return None
    s = state.scratch
    sides = (left, right)
    now = (a, b)  # both sides are remembered: with two variables, either one may be the one that moves
    ga, gb = _comparable(a, b)

    if op == "!=":
        gap = gb - ga
        prev = s.get(p + "jump_prev")
        away = _moving_away(s, p + "jump_", abs(gap), state.rounds)
        if prev is not None and (prev > 0) != (gap > 0):
            s[p + "jump_crossed"] = (s.get(p + "jump_prev_val"), now)
            s[p + "jump_streak"] = 0
        elif s.get(p + "jump_crossed") and prev is not None and away:
            s[p + "jump_streak"] = s.get(p + "jump_streak", 0) + 1
        elif s.get(p + "jump_crossed"):
            s[p + "jump_streak"] = 0
        s[p + "jump_prev"], s[p + "jump_prev_val"] = gap, now
        if s.get(p + "jump_crossed") and s.get(p + "jump_streak", 0) >= max(1, settings.jump_after):
            before, after = s[p + "jump_crossed"]
            moved = _moved(sides, before, after)
            if _length_moved(sides, moved):
                return None
            if len(moved) == 2:
                first, second = left[1], right[1]
                return Finding(
                    "jumped_over", "loop jumped over its stop value",
                    f"`{src}` only becomes False when `{first}` and `{second}` are exactly equal. "
                    f"`{first}` went from {_show(before[0])} to {_show(after[0])} and `{second}` from "
                    f"{_show(before[1])} to {_show(after[1])}, passing each other without ever being equal, "
                    f"and they have moved further apart ever since (now {_show(a)} and {_show(b)}).",
                    f"Use `<` or `>` instead of `!=` in the condition, or change the steps so `{first}` "
                    f"and `{second}` land on the same value.",
                    (first, second),
                )
            i = moved[0]
            var, target = _label(sides[i]), _other_side(sides[1 - i], now[1 - i])
            return Finding(
                "jumped_over", "loop jumped over its stop value",
                f"`{src}` only becomes False when `{var}` is exactly {target}. "
                f"`{var}` went from {_show(before[i])} to {_show(after[i])}, jumping over {target}, "
                f"and has moved further away ever since (now {_show(now[i])}).",
                f"Use `<` or `>` instead of `!=` in the condition, or change the step so `{var}` "
                f"lands exactly on {target}.",
                (var,),
            )
        return None

    gap = (gb - ga) if op in ("<", "<=") else (ga - gb)
    if _moving_away(s, p + "dir_", gap, state.rounds):
        if s.get(p + "dir_streak", 0) == 0:
            s[p + "dir_start"] = s.get(p + "dir_prev_val")
        s[p + "dir_streak"] = s.get(p + "dir_streak", 0) + 1
    else:
        s[p + "dir_streak"] = 0
    s[p + "dir_prev_val"] = now
    if s.get(p + "dir_streak", 0) >= max(1, settings.direction_after):
        start = s[p + "dir_start"]
        moved = _moved(sides, start, now)
        if _length_moved(sides, moved):
            return None
        if len(moved) == 2:
            first, second = left[1], right[1]
            return Finding(
                "wrong_direction", "loop moving the wrong way",
                f"The loop stops when `{src}` becomes False, which needs `{first}` and `{second}` "
                f"to meet. But over the last {s[p + 'dir_streak']:,} checks they moved further apart every time: "
                f"`{first}` went from {_show(start[0])} to {_show(a)} and `{second}` from {_show(start[1])} "
                f"to {_show(b)}.",
                f"Check the lines that update `{first}` and `{second}`: at least one of them should go "
                f"the other way.",
                (first, second),
            )
        i = moved[0]
        var, target = _label(sides[i]), _other_side(sides[1 - i], now[1 - i])
        fix = "`-=` instead of `+=`" if now[i] > start[i] else "`+=` instead of `-=`"
        return Finding(
            "wrong_direction", "loop moving the wrong way",
            f"The loop stops when `{src}` becomes False, which needs `{var}` to reach {target}. "
            f"But over the last {s[p + 'dir_streak']:,} checks `{var}` went from {_show(start[i])} "
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
    names = [i for i, side in enumerate(sides) if side[0] != "const"]
    changed = [i for i in names if before[i] != after[i]]
    return changed or names[:1]


def _length_moved(sides, moved) -> bool:
    """A ``len(...)`` side that changed is no proof: a work list (a queue, a
    stack) often grows for a long time before it empties. Only a list whose
    length stays put is a fixed target."""
    return any(sides[i][0] == "len" for i in moved)


def _other_side(side, value) -> str:
    """How the side a variable is compared with is shown: its value, with its
    name in front when it is a variable too."""
    return _show(value) if side[0] == "const" else f"`{_label(side)}` ({_show(value)})"


# --------------------------------------------------------------------------- R8
ESTIMATE_MARGIN = 2.0  # stop early only when the estimate is at least this many times the time limit
ESTIMATE_WINDOW = 1.0  # seconds: the speed is measured over the last one to two of these
ESTIMATE_MIN_ROUNDS = 100  # rounds finished before the average since the start is trusted on its own


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


def _recent_speed(s: dict, now: float, done: int) -> Optional[float]:
    """Seconds per round over the last second or two, measured from rounds that
    finished after the first one: a slow first round (a model loading, a file
    downloading) must not make a fast loop look slow."""
    older, newer = s.get("est_a"), s.get("est_b")
    if newer is None:
        s["est_b"] = (now, done)
    elif now - newer[0] >= ESTIMATE_WINDOW:
        s["est_a"], s["est_b"] = newer, (now, done)
        older = newer
    ref = older or newer
    if ref is None or done <= ref[1] or now <= ref[0]:
        return None
    return (now - ref[0]) / (done - ref[1])


def check_estimate(info: LoopInfo, state: LoopState, settings: Settings, now: float) -> Optional[Finding]:
    """R8: a for loop that, at its current speed, needs far longer than the time limit.

    The estimate is a guess (a loop may speed up: a cache warms, slow items
    come first), so it is shown as a note and the loop keeps running; only the
    time limit stops it. Loops that can end early (a ``break``, ``return`` or
    ``raise``) get no estimate: a search may finish long before its last item.
    """
    if (info.kind not in ("for", "comprehension") or info.exits or info.waits_for_user
            or settings.time_limit <= 0 or settings.estimate_after <= 0):
        return None
    progress = _progress(state)
    if progress is None:
        return None
    done, total, share = progress
    per_round = _recent_speed(state.scratch, now, done)
    elapsed = now - state.started
    if elapsed < settings.estimate_after or elapsed >= settings.time_limit:
        return None
    if per_round is not None:
        needed = elapsed + (total - done) * per_round
    elif done >= ESTIMATE_MIN_ROUNDS:
        needed = elapsed * total / done
    else:
        return None  # a few slow first rounds may just be warming up (loading, downloading)
    if needed < ESTIMATE_MARGIN * settings.time_limit:
        return None
    allow = int(math.ceil(needed * 1.25 / 60) * 60)
    return Finding(
        "too_slow", "loop would take too long",
        f"After {elapsed:.0f} s this loop has finished {done:,} of its {total:,} rounds ({share}). "
        f"At this speed it needs about {duration(needed)} in total, far more than the "
        f"{settings.time_limit:.0f} s time limit. It keeps running in case it speeds up, and is "
        f"stopped at the time limit if it does not. Nothing is wrong with the loop itself: it is just slow.",
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
    elif info.kind == "while" and off == "partial":
        reason = (f" (it changes more variables than the {settings.max_watched} loop-autopsy watches; "
                  f"see `max_watched`)")
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
    hint = ("Use the replay table to see whether the values move towards the stop condition. "
            f"To allow longer loops: `%autopsy config time_limit={int(settings.time_limit * 5)}`.")
    if info.outside_update is not None:
        hint = f"{indent_hint(info.outside_update)} {hint}"
    if info.kind == "comprehension":
        extra = extra.replace(" rounds", " items")
        return Finding(
            "time_limit", "loop ran too long",
            f"`{info.header}` has been going through items for {elapsed:.0f} s ({state.rounds:,} so far). "
            f"loop-autopsy cannot see inside it, so it cannot tell whether it is just slow or never ends.{extra}",
            "Try it on a few items first (for example `data[:100]`). If it goes through an iterator that never "
            "runs out, take only what you need with `itertools.islice(...)`. To allow longer: "
            f"`%autopsy config time_limit={int(settings.time_limit * 5)}`.",
        )
    return Finding(
        "time_limit", "loop ran too long",
        f"This loop has been running for {elapsed:.0f} s ({state.rounds:,} rounds). loop-autopsy could not prove "
        f"that it is stuck{reason}. It might just be slow, or stuck in a way the rules cannot see.{extra}",
        hint,
    )


def endless_finding(info: LoopInfo, kind: str) -> Finding:
    """A list/set/dict comprehension, or list(), sum() and the like, over an iterator
    that never runs out: it can only end with an error, usually out of memory."""
    return Finding(
        "endless", "never ends: its iterator never runs out",
        f"`{info.header}` (line {info.lineno}) takes every item of `{kind}`, which never runs out, and it "
        f"cannot stop early, so it would run until the computer runs out of memory. loop-autopsy stopped "
        f"it before it started.",
        "Take only the items you need, for example `list(itertools.islice(it, 100))` or "
        "`[x for _, x in zip(range(100), it)]`, or use a `for` loop with a `break`.",
    )


def blocked_finding(info: LoopInfo, elapsed: float, quiet: float, settings: Settings) -> Finding:
    """The watchdog's report: the time limit passed while one round never ended."""
    return Finding(
        "time_limit", "loop stuck inside one round",
        f"This loop has been running for {elapsed:.0f} s, and for the last {quiet:.0f} s it has not got to "
        f"the end of a round: it is waiting or working inside one step, for example `time.sleep(...)`, "
        f"a network request with no timeout, waiting on a queue, or a long calculation in a function. "
        f"loop-autopsy stopped it at the {settings.time_limit:.0f} s time limit instead of waiting for that "
        f"step to finish.",
        "The traceback above ends in the slow step; it says KeyboardInterrupt because loop-autopsy stopped "
        "the loop the way the stop button does. Give network calls a timeout, for example "
        "`requests.get(url, timeout=10)`, and queues one too (`q.get(timeout=5)`). To allow longer: "
        f"`%autopsy config time_limit={int(settings.time_limit * 5)}`.",
    )
