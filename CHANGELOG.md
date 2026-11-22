# Changelog

All notable changes to loop-autopsy are listed here, newest first.
Version numbers follow [Semantic Versioning](https://semver.org/).

## Unreleased

Nothing yet.

## 0.4.0 (2026-11-22)

### Added

- Update line after the loop: when the line right after a `while` loop
  changes a variable of its condition and nothing inside the loop does, the
  stuck and time-limit reports name that line and ask whether it should be
  indented.
- Update line inside an `if` that never runs: when a stuck loop's only
  update sits in an `if` (or `else`/`elif` part) whose condition never lets
  it run, the hint names that line, the condition and the values that keep
  it False.
- A `for` loop over `itertools.count()`, `itertools.cycle(...)` or
  `itertools.repeat(x)` with no `break`, `return` or `raise` gets a note on
  its first round.
- A bare `except:` or `except BaseException:` inside or around a loop gets a
  note when the loop starts: inside, it swallows ■; around, it hides a
  stopped loop's report.
- New module `loop_notes.py` holds these checks.
- "Wrong direction" and "jumped over" also work for a condition with
  `len(name)`, such as `while i < len(nums):`, as long as the length stays
  the same. A length that changes is left alone, since a queue can grow for
  a long time before it empties.
- Optional memory tools, each loaded on its own:
  `%%growthcheck` previews a `for` loop that changes the list it loops over
  (`loop_autopsy.growth_check`), `%growthdiff mark` / `show` compares
  variable sizes before and after a cell (`loop_autopsy.growth_diff`), and
  `%memaliases` / `%memparts` find names sharing one object and show what a
  container holds (`loop_autopsy.memory_inspect`).
- `loop_autopsy.extras.growth_trace` finds the notebook lines whose live
  allocations keep growing after a loop.

### Changed

- The optional report helpers moved into `loop_autopsy.extras`: import
  `loop_autopsy.extras.report_theme`, `report_i18n`, `report_export` and
  `sparkline` instead of `loop_autopsy.<name>`. The core package does not
  use them.

### Development

- `tests/test_false_positives.py`: 27 correct but suspicious-looking loops
  that must never be stopped, run with stricter thresholds than the
  defaults, plus broken versions that must still be stopped.
- The license is declared as the SPDX expression `license = "MIT"`
  (setuptools 77 or newer), which removes the deprecation warnings on build.

## 0.3.0 (2026-11-20)

The first release on PyPI since 0.2.0. It contains everything below.

### Added

- Runaway recursion: a `RecursionError` from a function typed in a cell is
  replaced by a short report (which function, its first and last arguments,
  and why the base case was never reached).
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
- A mistyped command or setting name gets a suggestion, such as
  `Unknown command 'stauts'. Did you mean 'status'?` or
  `unknown setting; did you mean 'time_limit'?`.
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
- The demo notebook has a section for every feature, including recursion.
- PyPI page lists the supported Python versions (3.9–3.13).

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
