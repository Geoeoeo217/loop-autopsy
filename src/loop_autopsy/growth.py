"""Memory helpers and rules R5 (growing iterable) and R6 (memory).

See SPEC.md sections 5 and 10. Owner: Teammate 3.
No third-party dependency is required; psutil is used only if installed.
"""

from __future__ import annotations

import os
import sys
from typing import Any, Dict, List, Optional, Tuple

from .config import Finding, LoopInfo, LoopState, Settings

_UNLIMITED = 1 << 60  # cgroup files use huge numbers for "no limit"
_limit_cache: List[Optional[int]] = []


# --------------------------------------------------------------------------- memory readings
def format_bytes(n: Optional[float]) -> str:
    if n is None:
        return "?"
    n = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit in ("B", "KB") else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"  # pragma: no cover


def _read_int(path: str) -> Optional[int]:
    try:
        with open(path) as f:
            text = f.read().strip()
        return None if text in ("", "max") else int(text)
    except (OSError, ValueError):
        return None


def memory_limit(refresh: bool = False) -> Optional[int]:
    """Bytes this kernel may use: MEM_LIMIT -> cgroup v2 -> cgroup v1 -> physical RAM."""
    if _limit_cache and not refresh:
        return _limit_cache[0]
    limit: Optional[int] = None
    env = os.environ.get("MEM_LIMIT")
    if env:
        try:
            limit = int(float(env))
        except ValueError:
            limit = None
    if limit is None:
        for path in ("/sys/fs/cgroup/memory.max", "/sys/fs/cgroup/memory/memory.limit_in_bytes"):
            value = _read_int(path)
            if value and value < _UNLIMITED:
                limit = value
                break
    if limit is None:
        try:
            import psutil  # type: ignore

            limit = int(psutil.virtual_memory().total)
        except Exception:
            try:
                limit = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
            except (ValueError, OSError, AttributeError):
                limit = None
    _limit_cache[:] = [limit]
    return limit


def current_rss() -> Optional[int]:
    """Resident memory of this process in bytes (best available method)."""
    try:
        with open("/proc/self/statm") as f:
            pages = int(f.read().split()[1])
        return pages * os.sysconf("SC_PAGE_SIZE")
    except (OSError, ValueError, IndexError, AttributeError):
        pass
    try:
        import psutil  # type: ignore

        return int(psutil.Process().memory_info().rss)
    except Exception:
        pass
    try:
        import resource

        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return peak if sys.platform == "darwin" else peak * 1024
    except Exception:  # pragma: no cover
        return None


def memory_fraction() -> Optional[float]:
    rss, limit = current_rss(), memory_limit()
    if not rss or not limit:
        return None
    return rss / limit


# --------------------------------------------------------------------------- sizes
def size_of(value: Any) -> int:
    """Approximate bytes used by ``value`` (pandas / NumPy aware)."""
    try:
        if hasattr(value, "memory_usage") and callable(value.memory_usage):  # pandas
            usage = value.memory_usage(deep=True)
            return int(usage.sum() if hasattr(usage, "sum") else usage)
        if hasattr(value, "nbytes") and isinstance(getattr(value, "nbytes"), int):  # NumPy
            return int(value.nbytes)
        base = sys.getsizeof(value)
        if isinstance(value, (list, tuple, set, frozenset)) and value:
            sample = list(value)[:1000] if not isinstance(value, (list, tuple)) else value[:1000]
            per_item = sum(sys.getsizeof(x) for x in sample) / len(sample)
            return int(base + per_item * len(value))
        if isinstance(value, dict) and value:
            items = list(value.items())[:1000]
            per_item = sum(sys.getsizeof(k) + sys.getsizeof(v) for k, v in items) / len(items)
            return int(base + per_item * len(value))
        return base
    except Exception:
        return 0


def largest_variables(namespace: Dict[str, Any], top: int = 3) -> List[Tuple[str, int]]:
    """The ``top`` biggest user variables as ``(name, bytes)``."""
    import types

    sizes = []
    for name, value in list(namespace.items()):
        if name.startswith("_") or name in ("In", "Out", "exit", "quit", "get_ipython"):
            continue
        if isinstance(value, types.ModuleType) or callable(value):
            continue
        sizes.append((name, size_of(value)))
    sizes.sort(key=lambda pair: pair[1], reverse=True)
    return sizes[:top]


def _lengths(values: Dict[str, Any]) -> Dict[str, int]:
    out = {}
    for name, value in values.items():
        if isinstance(value, (list, dict, set, bytearray, str, bytes, tuple)) or hasattr(value, "__len__"):
            try:
                out[name] = len(value)
            except Exception:
                pass
    return out


def remember_start(state: LoopState, values: Dict[str, Any]) -> None:
    """Called on the first check of a loop run: remember container sizes."""
    state.scratch["len0"] = _lengths(values)


# --------------------------------------------------------------------------- R5
def check_growing_iterable(info: LoopInfo, state: LoopState, values: Dict[str, Any],
                           settings: Settings) -> Optional[Finding]:
    """R5: ``for x in items`` while ``items`` keeps growing."""
    name = info.iter_name
    if info.kind != "for" or not name or name not in values:
        return None
    try:
        now = len(values[name])
    except TypeError:
        return None
    start = state.scratch.get("len0", {}).get(name)
    if start is None or state.rounds < settings.growth_after:
        return None
    grew = now - start
    if grew >= 0.9 * state.rounds:
        return Finding(
            "growing_iterable", "loop over a list that keeps growing",
            f"You are looping over `{name}` while adding to it. It started with {start:,} items and now has "
            f"{now:,}: it grows as fast as the loop reads it, so the loop never reaches the end.",
            f"Loop over a copy (`for ... in {name}[:]:`) or put new items in a separate list.",
            (name,),
        )
    return None


# --------------------------------------------------------------------------- R6
def check_memory(info: LoopInfo, state: LoopState, values: Dict[str, Any],
                 settings: Settings, fraction: Optional[float] = None) -> Optional[Finding]:
    """R6: stop before the kernel runs out of memory."""
    if fraction is None:
        fraction = memory_fraction()
    if fraction is None or fraction < settings.memory_stop:
        return None
    start = state.scratch.get("len0", {})
    now = _lengths(values)
    growth = sorted(((now[n] - start.get(n, 0), n) for n in now), reverse=True)
    culprit = growth[0] if growth and growth[0][0] > 0 else None
    limit = memory_limit()
    detail = ""
    if culprit:
        grew, name = culprit
        detail = f" `{name}` grew from {start.get(name, 0):,} to {now[name]:,} items during this loop."
    return Finding(
        "memory", "loop is running out of memory",
        f"Memory is at {fraction:.0%} of your {format_bytes(limit)} limit.{detail} "
        f"The kernel would crash soon, so the loop was stopped first.",
        "If the loop should end, check its stop condition. If you really need all that data, "
        "process it in smaller chunks instead of keeping everything in one list.",
        (culprit[1],) if culprit else (),
    )
