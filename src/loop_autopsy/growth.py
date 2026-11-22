"""Memory helpers and rules R5 (growing iterable) and R6 (memory).

No third-party dependency is required; psutil is used only if installed.
"""

from __future__ import annotations

import os
import re
import sys
from collections import deque
from itertools import islice
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


_SIZE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:([KMGT])(?:i?B)?|B)?", re.IGNORECASE)
_SIZE_UNITS = {"K": 1024, "M": 1024 ** 2, "G": 1024 ** 3, "T": 1024 ** 4}


def parse_memory_size(text: Any) -> Optional[int]:
    """Bytes in a size such as ``"2147483648"``, ``"1.5e9"``, ``"512M"`` or ``"2 GiB"``.

    The suffixes K, M, G and T are powers of 1024, the same as JupyterHub's own
    ``mem_limit`` setting. Returns ``None`` when the text is not a size, or is negative.
    """
    if isinstance(text, bool):
        return None
    try:
        number = int(float(text))
        return number if number >= 0 else None
    except (TypeError, ValueError, OverflowError):
        pass
    match = _SIZE.fullmatch(str(text).strip())
    if not match:
        return None
    return int(float(match.group(1)) * _SIZE_UNITS.get((match.group(2) or "").upper(), 1))


def memory_limit(refresh: bool = False) -> Optional[int]:
    """Bytes this kernel may use: MEM_LIMIT -> cgroup v2 -> cgroup v1 -> physical RAM."""
    if _limit_cache and not refresh:
        return _limit_cache[0]
    limit: Optional[int] = None
    env = os.environ.get("MEM_LIMIT")
    if env:
        limit = parse_memory_size(env)
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
                limit = _windows_total_memory() if sys.platform == "win32" else None
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
    if sys.platform == "win32":
        return _windows_rss()
    # resource.getrusage() only gives the *peak* memory, which never goes down: once a
    # cell had used a lot, every later loop would look about to run out. Unknown is
    # better than wrong (install psutil: pip install "loop-autopsy[memory]").
    return None


def _windows_rss() -> Optional[int]:
    """This process's working set (what psutil calls rss on Windows), without psutil."""
    try:
        import ctypes
        from ctypes import wintypes

        class Counters(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD)] + [
                (name, ctypes.c_size_t) for name in (
                    "PeakWorkingSetSize", "WorkingSetSize", "QuotaPeakPagedPoolUsage", "QuotaPagedPoolUsage",
                    "QuotaPeakNonPagedPoolUsage", "QuotaNonPagedPoolUsage", "PagefileUsage", "PeakPagefileUsage")]

        kernel32 = ctypes.WinDLL("kernel32")  # type: ignore[attr-defined]
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        get_info = kernel32.K32GetProcessMemoryInfo
        get_info.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
        get_info.restype = wintypes.BOOL
        counters = Counters()
        counters.cb = ctypes.sizeof(Counters)
        if not get_info(kernel32.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
            return None
        return int(counters.WorkingSetSize)
    except Exception:
        return None


def _windows_total_memory() -> Optional[int]:
    """Physical RAM on Windows, without psutil."""
    try:
        import ctypes
        from ctypes import wintypes

        class Status(ctypes.Structure):
            _fields_ = [("dwLength", wintypes.DWORD), ("dwMemoryLoad", wintypes.DWORD)] + [
                (name, ctypes.c_ulonglong) for name in (
                    "ullTotalPhys", "ullAvailPhys", "ullTotalPageFile", "ullAvailPageFile",
                    "ullTotalVirtual", "ullAvailVirtual", "ullAvailExtendedVirtual")]

        status = Status()
        status.dwLength = ctypes.sizeof(Status)
        if not ctypes.WinDLL("kernel32").GlobalMemoryStatusEx(ctypes.byref(status)):  # type: ignore[attr-defined]
            return None
        return int(status.ullTotalPhys)
    except Exception:
        return None


def memory_fraction() -> Optional[float]:
    rss, limit = current_rss(), memory_limit()
    if not rss or not limit:
        return None
    return rss / limit


# --------------------------------------------------------------------------- sizes
# How many items of a container are measured, by nesting depth, and how deep to look.
# The mean size of the measured items is multiplied by the container's length, so a
# huge nested structure is estimated in milliseconds instead of being walked in full.
_SAMPLE_BY_DEPTH = (1000, 50, 10, 3)
_VISIT_BUDGET = 60_000  # most items looked at by one size_of call
_LEAF = (bool, int, float, complex, str, bytes, range, type(None))  # nothing inside to add up


def size_of(value: Any) -> int:
    """Approximate bytes used by ``value`` (pandas / NumPy aware).

    Lists, tuples, sets, dicts and deques are measured including what they hold, also
    when nested (a list of lists). The attributes of an ordinary object (``__dict__``
    and ``__slots__``) count too, so a class instance that holds a big list is not
    reported as a few dozen bytes. It is an estimate: only the first few items of a
    big container are measured, and an object shared by several items is counted
    once per item.
    """
    try:
        return int(_deep_size(value, 0, [_VISIT_BUDGET]))
    except Exception:
        return 0


def _slot_values(value: Any) -> List[Any]:
    """Values stored in the ``__slots__`` of ``value``'s class (and its parents)."""
    found: List[Any] = []
    for cls in type(value).__mro__:
        names = cls.__dict__.get("__slots__", ())
        for name in (names,) if isinstance(names, str) else names:
            if name in ("__dict__", "__weakref__"):
                continue
            try:
                found.append(object.__getattribute__(value, name))
            except AttributeError:  # slot never assigned
                pass
    return found


def _deep_size(value: Any, depth: int, budget: List[int]) -> float:
    import types

    # pandas only: memory_usage() on another library's object can run real work
    # (a Dask DataFrame would compute itself just to be measured).
    if type(value).__module__.split(".")[0] == "pandas" and callable(getattr(value, "memory_usage", None)):
        usage = value.memory_usage(deep=True)
        return usage.sum() if hasattr(usage, "sum") else usage
    if hasattr(value, "nbytes") and isinstance(getattr(value, "nbytes"), int):  # NumPy
        return value.nbytes
    base: float = sys.getsizeof(value)
    budget[0] -= 1
    if budget[0] < 0 or depth >= len(_SAMPLE_BY_DEPTH):
        return base  # nested too deep, or already looked at enough: count this one shallowly
    if isinstance(value, _LEAF) or isinstance(
            value, (types.ModuleType, type, types.FunctionType, types.BuiltinFunctionType, types.MethodType)):
        return base

    if isinstance(value, (list, tuple, set, frozenset, deque)) and value:
        sample = islice(value, _SAMPLE_BY_DEPTH[depth])
        return base + _mean_size(sample, depth, budget) * len(value)
    if isinstance(value, dict) and value:
        pairs = islice(value.items(), _SAMPLE_BY_DEPTH[depth])
        return base + _mean_size(pairs, depth, budget, pairs=True) * len(value)

    inner = getattr(value, "__dict__", None)
    if isinstance(inner, dict) and inner:
        base += _child_size(inner, depth + 1, budget)
    for item in _slot_values(value):
        base += _child_size(item, depth + 1, budget)
    return base


def _child_size(item: Any, depth: int, budget: List[int]) -> float:
    try:
        return _deep_size(item, depth, budget)
    except Exception:  # one odd item must not hide the size of everything around it
        return 0.0


def _mean_size(items, depth: int, budget: List[int], pairs: bool = False) -> float:
    """Average size of the sampled ``items`` (or of ``(key, value)`` pairs)."""
    total, count = 0.0, 0
    for item in items:
        if pairs:
            total += _child_size(item[0], depth + 1, budget) + _child_size(item[1], depth + 1, budget)
        else:
            total += _child_size(item, depth + 1, budget)
        count += 1
        if budget[0] <= 0:
            break
    return total / count if count else 0.0


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


_HISTORY_MIN = 1 << 20  # outputs smaller than 1 MB are not worth mentioning


def output_history_holders(namespace: Dict[str, Any], top: int = 3,
                           min_bytes: int = _HISTORY_MIN) -> List[Tuple[List[str], int, Any, List[str]]]:
    """Big objects that IPython's output history keeps alive, biggest first.

    ``Out[3]``, ``_3`` and ``_`` / ``__`` / ``___`` all refer to what a cell displayed, so
    ``del big`` does not free ``big`` if a cell showed it as its result. Each item is
    ``(history names, size in bytes, the object, variables that still use it)``.
    """
    held: Dict[int, Tuple[List[str], Any]] = {}

    def add(label: str, value: Any) -> None:
        if value is not None:
            held.setdefault(id(value), ([], value))[0].append(label)

    out = namespace.get("Out")
    if isinstance(out, dict):
        for number, value in list(out.items()):
            add(f"Out[{number}]", value)
    for name, value in list(namespace.items()):
        if name in ("_", "__", "___") or (name.startswith("_") and name[1:].isdigit()):
            add(name, value)

    found = []
    for labels, value in held.values():
        size = size_of(value)
        if size >= min_bytes:
            users = [n for n, v in list(namespace.items())
                     if v is value and not n.startswith("_") and n not in ("In", "Out")]
            found.append((labels, size, value, users))
    found.sort(key=lambda item: item[1], reverse=True)
    return found[:top]


def _describe(value: Any) -> str:
    """Type name, with the length when the value has one: ``list (len 3)``."""
    kind = type(value).__name__
    try:
        kind += f" (len {len(value):,})"
    except TypeError:
        pass
    return kind


def _share(size: float, limit: float) -> str:
    """``size`` as a share of the memory ``limit``: ``25.0%``, or ``<0.1%`` for a sliver."""
    ratio = size / limit
    return "<0.1%" if 0 < ratio < 0.001 else f"{ratio:.1%}"


def _table(headers: List[str], rows: List[List[str]], right: Tuple[int, ...] = ()) -> List[str]:
    """Plain-text table lines; the columns listed in ``right`` are right-aligned."""
    widths = [max([len(head)] + [len(row[i]) for row in rows]) for i, head in enumerate(headers)]

    def line(cells: List[str]) -> str:
        parts = [cell.rjust(w) if i in right else cell.ljust(w) for i, (cell, w) in enumerate(zip(cells, widths))]
        return "  ".join(parts).rstrip()

    return [line(headers), line(["-" * w for w in widths])] + [line(row) for row in rows]


def _history_lines(holders: List[Tuple[List[str], int, Any, List[str]]],
                   limit: Optional[int] = None) -> List[str]:
    headers = ["held by", "size"] + (["of limit"] if limit else []) + ["type", "used by"]
    rows = []
    for labels, size, value, users in holders:
        row = [" = ".join(labels), format_bytes(size)]
        if limit:
            row.append(_share(size, limit))
        row += [_describe(value), ", ".join(f"`{u}`" for u in users) if users else "no variable"]
        rows.append(row)
    lines = ["Kept alive by the output history (Out, _, __, ___):", ""]
    lines += _table(headers, rows, right=(1, 2) if limit else (1,))
    lines += ["", "`del name` does not free an object the history still holds.",
              "`%reset -f out` clears the whole output history (Out, _, __, ___, _1, _2, ...);",
              "an object is freed once no variable uses it either."]
    return lines


def memory_overview(namespace: Dict[str, Any], top: int = 5) -> str:
    """Plain-text answer for ``%autopsy memory``: total memory use and the biggest variables."""
    rss, limit = current_rss(), memory_limit()
    if rss and limit:
        head = f"Memory: {format_bytes(rss)} of {format_bytes(limit)} ({rss / limit:.0%})"
    elif rss:
        head = f"Memory: {format_bytes(rss)} (limit unknown)"
    else:
        head = "Memory: cannot be measured on this system"

    biggest = [(name, size) for name, size in largest_variables(namespace, top) if size > 0]
    holders = output_history_holders(namespace)
    if not biggest and not holders:
        return head + "\n\nNo variables to show yet."

    lines = [head]
    if biggest:
        headers = ["variable", "size"] + (["of limit"] if limit else []) + ["type"]
        rows = []
        for name, size in biggest:
            row = [name, format_bytes(size)]
            if limit:
                row.append(_share(size, limit))
            rows.append(row + [_describe(namespace[name])])
        lines += [""] + _table(headers, rows, right=(1, 2) if limit else (1,))
    if holders:
        lines += [""] + _history_lines(holders, limit)
    return "\n".join(lines)


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
