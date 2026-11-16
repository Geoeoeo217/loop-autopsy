# Changelog

All notable changes to loop-autopsy are listed here, newest first.
Version numbers follow [Semantic Versioning](https://semver.org/).

## Unreleased

- Tests that run cells in a real Jupyter kernel (`tests/test_real_kernel.py`),
  including the whole demo notebook.
- The demo notebook is saved with its outputs; `python examples/refresh_demo.py`
  runs it again and saves them.
- Many more tests for `growth.py` (now fully covered) and `detectors.py`.

- `pytest` now runs straight from a fresh clone, without `pip install -e` first.
- Output guard also mutes `stderr` and `display()` output after the line limit.
- Continuous integration on GitHub Actions: Python 3.9–3.13 on Linux, plus
  Windows and macOS; the package build is checked too.
- Tests for the monitor and the `%autopsy` magics, a check that the README
  matches the code, and an overhead benchmark (`benchmarks/overhead.py`).
- `%autopsy memory [N]`: memory in use and the biggest variables.
- License checks for every package loop-autopsy uses (`THIRD_PARTY_LICENSES.md`).

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
