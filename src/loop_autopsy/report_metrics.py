"""Small analysis helpers for Loop Autopsy reports.

These helpers keep report-specific calculations separate from the rendering
code. They work with the existing LoopState shape and do not change the
detector or monitor behaviour.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List


def history_summary(state: Any) -> Dict[str, int]:
    """Return compact statistics about the recorded execution history."""
    history = getattr(state, "history", [])
    variables = set()

    for _, snapshot in history:
        variables.update(snapshot.keys())

    return {
        "snapshots": len(history),
        "variables": len(variables),
        "rounds": int(getattr(state, "rounds", 0)),
    }


def changed_variables(state: Any) -> List[str]:
    """Return variable names whose recorded values changed."""
    history = getattr(state, "history", [])
    if len(history) < 2:
        return []

    names = []
    seen = set()

    for _, snapshot in history:
        for name in snapshot:
            if name not in seen:
                seen.add(name)
                names.append(name)

    changed = []
    for name in names:
        values = [snapshot.get(name, _MISSING) for _, snapshot in history]
        first = values[0]
        if any(_different(first, value) for value in values[1:]):
            changed.append(name)

    return changed


class _Missing:
    pass


_MISSING = _Missing()


def _different(left: Any, right: Any) -> bool:
    if left is _MISSING or right is _MISSING:
        return left is not right

    try:
        return type(left) is not type(right) or not bool(left == right)
    except Exception:
        return True


def format_summary(state: Any) -> str:
    """Format history statistics for a human-readable report footer."""
    stats = history_summary(state)
    return (
        f"{stats['snapshots']} snapshots, "
        f"{stats['variables']} variables, "
        f"{stats['rounds']:,} rounds"
    )
