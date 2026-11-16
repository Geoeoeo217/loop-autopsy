"""Measure what loop-autopsy costs per loop round.

Each loop below is run twice: as plain Python, and after the same rewriting a
notebook cell gets (``LoopTransformer`` plus a real ``Monitor``). The difference,
divided by the number of rounds, is the cost of watching one round.

Run it from the project folder::

    python benchmarks/overhead.py
    python benchmarks/overhead.py --rounds 5000000 --repeats 7

Timings depend on the computer and the Python version, so compare the two
columns with each other rather than with someone else's numbers.
"""

from __future__ import annotations

import argparse
import ast
import platform
import sys
import time

from loop_autopsy.config import Settings
from loop_autopsy.monitor import Monitor
from loop_autopsy.transformer import RUNTIME_NAME, LoopTransformer

# name, source. ``N`` is the number of rounds; every loop is a healthy one that ends.
LOOPS = [
    ("while, counter only",
     "i = 0\nwhile i < N:\n    i += 1\n"),
    ("for over range",
     "total = 0\nfor i in range(N):\n    total += i\n"),
    ("while, five variables",
     "i = 0\na = b = c = 0\nwhile i < N:\n    a += i\n    b = a % 7\n    c = b * 2\n    i += 1\n"),
    ("while, small list",
     "i = 0\nitems = [0] * 8\nwhile i < N:\n    items[i % 8] = i\n    i += 1\n"),
    ("inside a function",
     "def run(n):\n    i = 0\n    while i < n:\n        i += 1\n    return i\nrun(N)\n"),
]


def compile_plain(source: str):
    return compile(source, "<plain>", "exec")


def compile_watched(source: str, monitor: Monitor):
    """Rewrite ``source`` exactly as a notebook cell is rewritten."""
    transformer = LoopTransformer(monitor.register, lambda: monitor.settings.max_watched, monitor.is_paused)
    tree = transformer.visit(ast.parse(source))
    return compile(tree, "<watched>", "exec")


def timed(code, namespace: dict) -> float:
    start = time.perf_counter()
    exec(code, namespace)
    return time.perf_counter() - start


def measure(source: str, rounds: int, repeats: int):
    """Return (plain seconds, watched seconds, checks in one run) for one loop.

    The plain and watched versions take turns, so a busy moment on the computer
    hits both, and the fastest run of each counts: the one least disturbed.
    """
    monitor = Monitor(None, Settings(time_limit=0))  # no time limit: nothing stops the loop
    plain = compile_plain(source)
    watched = compile_watched(source, monitor)
    plain_s = watched_s = float("inf")
    for _ in range(repeats):
        plain_s = min(plain_s, timed(plain, {"N": rounds}))
        watched_s = min(watched_s, timed(watched, {"N": rounds, RUNTIME_NAME: monitor}))
    checks = sum(state.checks for state in monitor.states if state is not None)
    return plain_s, watched_s, checks


def reference_speed() -> float:
    """Seconds for a fixed piece of work. Taken before and after the benchmark:
    if the two differ a lot, the computer changed speed part-way (a laptop
    saving power, another program starting) and the numbers cannot be trusted."""
    code = compile("i = 0\nwhile i < 200000:\n    i += 1\n", "<reference>", "exec")
    return min(timed(code, {}) for _ in range(5))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--rounds", type=int, default=500_000, help="rounds per loop (default 500,000)")
    parser.add_argument("--repeats", type=int, default=7, help="runs per loop; the fastest counts (default 7)")
    args = parser.parse_args(argv)
    speed_before = reference_speed()

    print(f"Python {platform.python_version()} on {platform.system()} {platform.machine()}, "
          f"{args.rounds:,} rounds, best of {args.repeats}")
    print()
    head = ("loop", "plain", "watched", "extra per round", "slowdown", "checks")
    rows, extras = [], []
    for name, source in LOOPS:
        plain_s, watched_s, checks = measure(source, args.rounds, args.repeats)
        extra_us = (watched_s - plain_s) / args.rounds * 1e6
        extras.append(extra_us)
        rows.append((name, f"{plain_s * 1000:.0f} ms", f"{watched_s * 1000:.0f} ms",
                     f"{extra_us:.3f} µs", f"{watched_s / plain_s:.2f}x", f"{checks:,}"))
    widths = [max(len(head[c]), *(len(r[c]) for r in rows)) for c in range(len(head))]
    for line in (head, tuple("-" * w for w in widths), *rows):
        print("  ".join(cell.ljust(w) for cell, w in zip(line, widths)).rstrip())
    print()
    print(f"Extra cost per round: {min(extras):.3f} to {max(extras):.3f} µs")
    speed_after = reference_speed()
    change = max(speed_before, speed_after) / min(speed_before, speed_after)
    if change > 1.5:
        print()
        print(f"Warning: the computer's speed changed {change:.1f}x during this run, so these numbers are "
              f"not reliable. Close other programs, plug in a laptop, and run it again.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
