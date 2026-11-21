from __future__ import annotations

import math
from collections import deque
from difflib import get_close_matches
from dataclasses import dataclass, field, fields
from typing import Any, Optional, Tuple


class LoopStopped(BaseException):
    """Raised inside a watched loop to stop it.

    It derives from BaseException (like KeyboardInterrupt) so that a normal
    ``except Exception:`` block inside the student's loop cannot swallow it.
    """

    def __init__(self, finding: "Finding"):
        super().__init__(finding.title)
        self.finding = finding


class UnknownSetting(KeyError):
    """A setting name that does not exist. Printed without the extra quotes KeyError adds."""

    def __str__(self) -> str:
        return str(self.args[0]) if self.args else ""


@dataclass
class Finding:
    """What a detection rule proved about a loop."""

    kind: str  # stuck | cycle | wrong_direction | jumped_over | growing_iterable | memory | too_slow | time_limit
    title: str
    explanation: str
    hint: str
    culprits: Tuple[str, ...] = ()


@dataclass
class LoopInfo:
    """Static facts about one loop, collected when the cell is transformed."""

    loop_id: int
    kind: str  # "while" | "for"
    lineno: int
    header: str
    test_src: str = ""
    cond_names: Tuple[str, ...] = ()
    watched: Tuple[str, ...] = ()
    compare: Optional[tuple] = None  # (left, op, right); side = ("name", n) | ("const", v) | ("len", n)
    iter_name: Optional[str] = None
    receivers: Tuple[str, ...] = ()  # names used as ``name.method(...)``; checked at run time
    repeat_safe: bool = True
    waits_for_user: bool = False
    always_true: bool = False  # ``while True:`` (a condition that is a true constant)
    exits: bool = True  # the body has a break, return, raise, assert or exit() that can end the loop
    notes: Tuple[str, ...] = ()  # read from the code (loop_notes.py); shown once when the loop starts
    outside_update: Optional[Tuple[str, int, str]] = None  # (name, line, code): its update sits after the loop
    branch_updates: Tuple[Any, ...] = ()  # loop_notes.BranchUpdate: its updates sit inside an if


@dataclass
class LoopState:
    """Run-time record of one execution of a loop."""

    info: LoopInfo
    started: float
    replay_rows: int = 6
    rounds: int = 0
    checks: int = 0
    stride: int = 1
    next_check: int = 1
    last_check: float = 0.0
    history: deque = field(default_factory=deque)
    scratch: dict = field(default_factory=dict)
    stopped: Optional[Finding] = None
    total: Optional[int] = None  # rounds a ``for`` loop will run, when its iterable has a length

    def __post_init__(self):
        self.history = deque(maxlen=max(1, self.replay_rows))
        self.last_check = self.started


@dataclass
class Settings:
    """User-adjustable settings (``%autopsy config key=value``)."""

    time_limit: float = 60.0
    estimate_after: float = 5.0  # seconds before a slow for loop's total time is estimated (0 = never)
    direction_after: int = 500
    jump_after: int = 100
    growth_after: int = 2000
    memory_stop: float = 0.85
    memory_warn: float = 0.75
    max_output_lines: int = 1000
    max_displays: int = 200  # display() calls per cell before they are muted (0 = off)
    muted_tail: int = 20  # last muted lines shown when the cell ends (0 = none)
    replay_rows: int = 6
    max_watched: int = 25
    heatmap: str = "auto"  # on | off | auto
    time_yellow: float = 1.0
    time_red: float = 10.0
    mem_yellow: float = 0.50
    mem_red: float = 0.80
    history_cells: int = 200  # cells remembered for %autopsy summary (0 = off)
    grow_yellow: float = 0.05  # summary colours: memory one cell added, as a fraction of the limit
    grow_red: float = 0.20
    rerun_growth_mb: int = 10  # MB a cell must add on each re-run to be flagged (0 = off)

    def update(self, key: str, raw: str) -> Any:
        """Set ``key`` from a string such as ``"30"``; returns the new value."""
        names = {f.name: f for f in fields(self)}
        if key not in names:
            close = get_close_matches(key, names, n=1, cutoff=0.6)
            if close:
                raise UnknownSetting(f"unknown setting; did you mean '{close[0]}'?")
            raise UnknownSetting(f"unknown setting; choose from: {', '.join(names)}")
        current = getattr(self, key)
        if isinstance(current, str):
            value: Any = raw.strip().strip("'\"").lower()
            if key == "heatmap" and value not in ("on", "off", "auto"):
                raise ValueError("heatmap must be on, off or auto")
        else:
            number = _number(key, raw)
            is_int = isinstance(current, int) and not isinstance(current, bool)
            value = int(number) if is_int else number
        if isinstance(value, (int, float)) and value < 0:
            raise ValueError(f"{key} cannot be negative")
        setattr(self, key, value)
        return value

    def as_dict(self) -> dict:
        return {f.name: getattr(self, f.name) for f in fields(self)}

    def reset(self) -> dict:
        """Put every setting back to its default, in place (other parts of the package keep
        a reference to this object). Returns ``{key: (old, new)}`` for the ones that changed."""
        changed = {}
        for name, default in Settings().as_dict().items():
            old = getattr(self, name)
            if old != default:
                changed[name] = (old, default)
                setattr(self, name, default)
        return changed


def _number(key: str, raw: str) -> float:
    """Read a setting's number, with a message a beginner can act on."""
    text = raw.strip()
    if not text:
        raise ValueError(f"no value given; write it as {key}=NUMBER")
    try:
        number = float(text)
    except ValueError:
        raise ValueError(f"{key} must be a number, not {text!r}") from None
    if not math.isfinite(number):
        raise ValueError(f"{key} must be a finite number, not {text!r}")
    return number
