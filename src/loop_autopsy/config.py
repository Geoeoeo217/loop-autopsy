from __future__ import annotations

from collections import deque
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


@dataclass
class Finding:
    """What a detection rule proved about a loop."""

    kind: str  # stuck | cycle | wrong_direction | jumped_over | growing_iterable | memory | time_limit
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
    compare: Optional[tuple] = None  # (left, op, right); side = ("name", n) | ("const", v)
    iter_name: Optional[str] = None
    receivers: Tuple[str, ...] = ()  # names used as ``name.method(...)``; checked at run time
    repeat_safe: bool = True
    waits_for_user: bool = False


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

    def __post_init__(self):
        self.history = deque(maxlen=max(1, self.replay_rows))
        self.last_check = self.started


@dataclass
class Settings:
    """User-adjustable settings (``%autopsy config key=value``)."""

    time_limit: float = 60.0
    direction_after: int = 500
    jump_after: int = 100
    growth_after: int = 2000
    memory_stop: float = 0.85
    memory_warn: float = 0.75
    max_output_lines: int = 1000
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

    def update(self, key: str, raw: str) -> Any:
        """Set ``key`` from a string such as ``"30"``; returns the new value."""
        names = {f.name: f for f in fields(self)}
        if key not in names:
            raise KeyError(f"unknown setting {key!r}; choose from: {', '.join(names)}")
        current = getattr(self, key)
        if isinstance(current, str):
            value: Any = raw.strip().strip("'\"").lower()
            if key == "heatmap" and value not in ("on", "off", "auto"):
                raise ValueError("heatmap must be on, off or auto")
        elif isinstance(current, int) and not isinstance(current, bool):
            value = int(float(raw))
        else:
            value = float(raw)
        if isinstance(value, (int, float)) and value < 0:
            raise ValueError(f"{key} cannot be negative")
        setattr(self, key, value)
        return value

    def as_dict(self) -> dict:
        return {f.name: getattr(self, f.name) for f in fields(self)}
