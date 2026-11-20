# Changelog

All notable changes to loop-autopsy are listed here, newest first.
Version numbers follow [Semantic Versioning](https://semver.org/).

## Unreleased

### Added

- New rule "would take too long": a `for` loop over something with a length
  (a `range`, list, dict, string, NumPy array, ...) that at its current speed
  needs at least twice the time limit is stopped after 5 s with an estimate,
  instead of after the full time limit. Loops that can `break`, `return` or
  `raise` are left to the time limit. New setting `estimate_after` (0 = off).
- The time-limit report for a `for` loop says how many of its rounds were
  finished and how long all of them would take.
- A `while True:` loop with no `break`, `return`, `raise` or `exit()` gets a
  one-line note when it starts, since it can only end with an error.
- `%autopsy reset` puts every setting back to its default and lists the ones
  it changed.
- `%autopsy memory [N]`: memory in use and the biggest variables.
- `%autopsy memory` shows each variable's share of the memory limit in an
  "of limit" column (left out when the limit is unknown).
- `%autopsy memory` also lists big results that IPython's output history
  (`Out`, `_`, `__`, `___`) still keeps alive. A variable that a cell displayed
  stays in memory after `del`; the report names it, says whether a variable
  still uses it, and points to `%reset -f out`, which frees it.
- Output guard: the last 20 muted lines are shown when the cell ends, or just
  before its error message. New setting `muted_tail` (0 = off).
- Health bar and session summary: a cell that adds memory every time it is
  re-run (two re-runs in a row, 10 MB or more each) gets a note explaining
  why. New setting `rerun_growth_mb` (0 = off).
- Report helper modules: themes, translated labels, accessibility checks,
  export to files, report metrics and sparkline charts (`report_*.py`,
  `sparkline.py`).

### Changed

- Output guard also mutes `stderr` and `display()` output after the line limit.
- `MEM_LIMIT` may now be written with a unit, such as `2G` or `512M` (powers of
  1024, like JupyterHub's `mem_limit`). Before, only a plain number of bytes
  worked and `2G` was silently ignored.

### Fixed

- The sizes in `%autopsy memory` and in the health bar's "biggest variables"
  now include what a variable holds. A list of lists was reported at about a
  fifth of its real size, and an object that holds a big list as a few dozen
  bytes, so the variable using the most memory could be missing from the table.
- For a condition that compares two variables, such as `while lo < hi`, the
  "wrong direction" and "jumped over" reports now name the variable that
  actually moved (or both). They used to blame the left one even when it never
  changed. The hint's example (`+=` or `-=`) now also points the right way.
- `%autopsy config` rejects `inf`, `nan` and a missing value with a short
  message. `replay_rows=inf` used to end in a traceback, and `time_limit=nan`
  was accepted.
- An outer loop no longer watches the names inside a function defined in its
  body, or the check inserted into an inner loop (`locals`).

### Development

- Continuous integration on GitHub Actions: Python 3.9–3.13 on Linux, plus
  Windows and macOS; the package build is checked too.
- `pytest` now runs straight from a fresh clone, without `pip install -e` first.
- Tests that run cells in a real Jupyter kernel (`tests/test_real_kernel.py`),
  including the whole demo notebook.
- The demo notebook is saved with its outputs; `python examples/refresh_demo.py`
  runs it again and saves them.
- Tests for the monitor, the `%autopsy` magics and the settings, a check that
  the README matches the code, and an overhead benchmark
  (`benchmarks/overhead.py`).
- Many more tests for `growth.py` (now fully covered), `detectors.py` and the
  loop transformer.
- License checks for every package loop-autopsy uses (`THIRD_PARTY_LICENSES.md`).
- `CONTRIBUTING.md` with who owns which files and how to make a change.

## 0.3.0 (2026-11-14)

- Runaway recursion: a `RecursionError` from a function typed in a cell is
  replaced by a short report (which function, its first and last arguments,
  and why the base case was never reached).
- Demo notebook section for recursion.

## 0.2.0 (2026-11-14)

- Session summary: `%autopsy summary` shows every cell run as a strip of
  coloured squares and a table of the heaviest cells.
- New settings `history_cells`, `grow_yellow` and `grow_red`.

## 0.1.0 (2026-11-13)

First release.

- Loop rewriting: an IPython AST transformer adds a check to every `while`
  and `for` loop typed in a cell.
- Detection rules: stuck, cycle, wrong direction, jumped over a `!=` stop
  value, growing list, memory about to run out, and a time limit fallback.
- Replay table report showing the last rounds of a stopped loop.
- Cell health bar for time, memory and loops, and an output guard.
- `%autopsy` line magic and `%%autopsy off` cell magic.
- Demo notebook in `examples/demo.ipynb`.
