# loop-autopsy

[![tests](https://github.com/Geoeoeo217/loop-autopsy/actions/workflows/tests.yml/badge.svg)](https://github.com/Geoeoeo217/loop-autopsy/actions/workflows/tests.yml)

**Stops runaway loops in Jupyter and shows the evidence for why they never ended.**

```python
%load_ext loop_autopsy

count = 1
while count <= 5:
    print(count)
```

Instead of freezing the notebook, the loop is stopped after 3 rounds and you get:

![A loop-autopsy report for a stuck loop](https://raw.githubusercontent.com/Geoeoeo217/loop-autopsy/main/docs/report.png)

```
Loop autopsy — stuck loop (line 2)
    while count <= 5:

Nothing changed between rounds: `count = 1` stayed exactly the same, so the
condition `count <= 5` is True every time and the loop can never end.

Last rounds checked:
round  count (same)
-----  ------------
1      1
2      1
3      1

Hint: Add a line inside the loop that changes `count` so that `count <= 5`
eventually becomes False (for example `count += 1`).
```

---

## Motivation

Every beginner writes an infinite loop. In a notebook on a shared JupyterHub this
is worse than it sounds:

- the cell hangs with no explanation, and the only remedy is the stop button ■;
- a `print` inside the loop floods the browser with output until the tab freezes;
- the loop keeps using CPU and memory on a server shared by the whole class.

Pressing ■ stops the loop but teaches nothing. The student still does not know
*which* variable failed to change or *why* the condition never became False.

## Innovation

loop-autopsy watches loops **while they run** and stops them only when it can
**prove** they will never end. Then it shows the evidence.

| | Stops a stuck loop? | Explains *why*? | When? |
|---|---|---|---|
| Stop button ■ | yes (you press it) | no | whenever you notice |
| Timeout decorators (e.g. `wrapt_timeout_decorator`) | yes, after a fixed time | no | after the time limit |
| Error explainers (e.g. `friendly-traceback`) | no | explains *errors*, but an infinite loop raises none | after a crash |
| Memory monitors (e.g. `jupyter-resource-usage`) | no | no | shows totals only |
| **loop-autopsy** | **yes, automatically** | **yes, with a replay table** | **during the loop, often after 3 rounds** |

How it works, in short:

1. An IPython **AST transformer** (it edits the code's syntax tree before it runs)
   adds a tiny check to every `while` and `for` loop you type in a cell.
2. Each check takes a **fingerprint** of the loop's variables. If a deterministic
   loop comes back to an *exactly identical* state, it must repeat forever: that
   is a proof, not a guess.
3. Further rules catch a counter moving *away* from its target, a `!=` condition
   that jumps over its stop value (`0.1 + 0.1 + ...` never equals `1.0`), a list
   that grows while you loop over it, and memory running out.
4. Anything it cannot prove falls back to an honest time limit. No AI model, no
   internet, and the same result every time.

Every rule and its numbers are listed below, and every number can be changed (see Settings).

## What it detects

| Pattern | Example | Stopped after |
|---|---|---|
| **Stuck**: nothing changes | `while count <= 5: print(count)` | 3 rounds |
| **Cycle**: values go round in a circle | `while a < 5: a = (a + 1) % 3` | about 2 cycles |
| **Wrong direction** | `while i < 10: i -= 1` | 500 checks |
| **Jumped over** the stop value | `while x != 1.0: x += 0.1` | about 100 checks |
| **Growing list** in a `for` loop | `for n in nums: nums.append(n)` | 2,000 rounds |
| **Memory** about to run out | a loop filling a huge list | at 85% of the limit |
| **Would take too long**: a `for` loop over something with a length, too slow to finish in time | `for row in range(10**9): slow(row)` | 5 s, if it needs at least twice the time limit |
| **Never ends**: a comprehension, or `list()`, `sum()`, `max()`... over an iterator that never runs out | `[x for x in itertools.count()]`, `list(naturals())` | before it starts |
| **Stuck inside one round**: a round that never finishes | `for url in urls: time.sleep(600)` | the time limit, without waiting for the round to end |
| **Too long** (fallback) | anything else, including slow comprehensions and `async` loops | 60 s |
| **Runaway recursion** | `def f(n): return f(n - 2)` | when Python raises `RecursionError` |

What is watched: `while` and `for` loops, `async for` and loops with `await`,
comprehensions and generator expressions, the iterator given to `list()`,
`tuple()`, `set()`, `dict()`, `sorted()`, `sum()`, `min()`, `max()`, `any()`,
`all()`, `deque()` and `", ".join()`, and the loops of a script run with
`%run`. A loop with `await` (or the same loop in two threads at once) only gets
the time and memory rules, since other tasks can change its values while it
waits. A generator expression that a watched loop is using is timed by that loop.

Conditions are followed through plain attributes (`while self.n < 10`,
`while len(self.items) < 3`), NumPy numbers, `and` and `or`: in
`a < 10 or b > 0`, one part moving the wrong way keeps the loop going; in
`i < 10 and not done`, the comparison counts while the other parts stay the
same. In `0 <= i < n`, moving away from one end is moving towards the other, so
no direction is reported. An attribute is read without running any code: a
property is never read, and the loop then gets the time limit.

A `for` loop that "would take too long" is stopped with an estimate ("finished
1,234 of 10,000,000 rounds; at this speed it needs about 11 hours") instead of a
minute of waiting, because the time limit would stop it anyway. This only
happens when the loop has no `break`, `return` or `raise`: a search that may end
early is given the full time limit. A loop stopped by the time limit also shows
how far it got, when its length is known.

"Wrong direction" and "jumped over" also work when the condition compares with
the length of a list, string, tuple, dict or set, as in `while i < len(nums):`.
They are reported only when the length stays the same. A list that keeps
growing is no proof, because a queue often grows for a long time before it
empties; such a loop gets the time limit instead.

Moving away from the stop value only counts while the loop does not slow
down. A ball thrown upwards (`while h > 0: h += v; v -= 0.01`) rises for a
long time, but each step is smaller than the last, so it is left to run and
comes back down.

A `while True:` loop with no `break`, `return`, `raise` or `exit()` inside gets
a one-line note as soon as it starts, because it can only end with an error.

Four more mistakes are read straight from the code:

- **Update line after the loop.** When the line right after a `while` loop
  changes a variable of its condition and nothing inside the loop does, the
  hint names that line and asks whether it should be indented:
  `Line 4 (count += 1) changes count, but it comes after the loop`. The hint
  is left out when the loop has a `break`, `return` or `raise`, or calls a
  function of your own that could change the variable.
- **Update line inside an `if` that never runs.** When a stuck loop's only
  update sits inside an `if` whose condition is False every round, the hint
  names both: `Line 4 (i += 1) changes i, but it never runs: it is inside
  if y > 3: (line 3), and y > 3 is False every round (y = 2 never changes)`.
  The same goes for an update in an `else` or `elif` part that is always
  skipped. The condition is only worked out when it uses plain variables,
  numbers, comparisons and a few built-ins such as `len()`, and only reads
  values nothing earlier in the round changes. Otherwise the usual hint is
  kept.
- **Endless iterator.** A `for` loop over `itertools.count()`,
  `itertools.cycle(...)`, `itertools.repeat(x)` or a generator of yours that
  ends in a `while True:` loop with no way out, with no `break`, `return` or
  `raise` of its own, gets a note on its first round, since it can only end
  with an error.
- **Catch-all `except`.** A bare `except:` or `except BaseException:` also
  catches the stop button ■ and loop-autopsy's stop signal. Inside a loop, the
  note says ■ will only jump to the `except` block. Around a loop, it says a
  stopped loop's report will be hidden (`%autopsy last` still shows it). There
  is no note when the `except` block re-raises, or, inside a loop, when it
  leaves the loop.

Runaway recursion is the recursive cousin of an infinite loop. Python already
stops it, but with a traceback hundreds of lines long. loop-autopsy replaces that
with a report: which function kept calling itself (or which functions kept
calling each other), its arguments in the first and last calls, and why the base
case was never reached. It tells apart arguments that never change, arguments
that go round in a circle, a value that jumped past 0, a value moving the wrong
way, and a function that may be correct but needs more depth than Python allows.

Plus four extras:

- **Press ■ yourself** and you still get the replay table, showing which
  variables never changed.
- **Cell health bar**: time, memory and loops for a cell, coloured green / yellow /
  red. It warns when memory passes 75% of your limit and names the biggest
  variables (pandas and NumPy aware); `%autopsy memory` shows the same list
  whenever you ask. It also notices a cell that adds memory **every time you
  re-run it** (such as `df = pd.concat([df, new])`): after two such re-runs in a
  row, each adding 10 MB or more, it explains why and what to do.
- **Output guard**: a cell's output is muted after 1,000 printed lines, so the
  browser stays responsive. This covers `print`, error output (`sys.stderr`) and,
  after 200 items, `display()` (tables, plots). The last 20 muted lines are shown
  when the cell ends (or just before its error message), because the end of the
  output is usually the part you need. Animations that clear the output and
  redraw are not muted, because `clear_output()` starts the count again.
- **Session summary**: `%autopsy summary` shows every cell you have run as a strip
  of coloured squares, then a table of the heaviest cells with their time, memory
  and stopped loops. Here a cell is coloured only by what it did itself (its run
  time and the memory it added), so one big dataframe does not turn every later
  cell red. The health bar is different: it colours by total memory in use, to
  warn you when you are close to the limit.

## Example use cases

- **Students** in an intro course: load it once at the top of every notebook and
  never lose a session to a frozen loop again.
- **Instructors** on JupyterHub: load it for everyone (see below) to protect the
  shared server and turn every infinite loop into a mini-lesson.
- **Teaching assistants**: the replay table shows at a glance what went wrong in
  a student's loop.
- **Data notebooks**: the health bar shows which cell is slow or memory-hungry.

## Setup

```bash
pip install loop-autopsy
```

The only requirement is IPython 8.15 or newer (already present in Jupyter). Python 3.9+.

For exact memory readings on macOS and some servers, also install psutil:
`pip install "loop-autopsy[memory]"`. Without it, memory is read from Linux's
`/proc` or Windows itself; on macOS the memory features are then off.

In a notebook:

```python
%load_ext loop_autopsy
```

**For instructors**: to turn it on for every notebook, add this to the IPython
profile (`~/.ipython/profile_default/ipython_config.py`):

```python
c.InteractiveShellApp.extensions = ["loop_autopsy"]
```

## Usage

| Command | Effect |
|---|---|
| `%autopsy` | status and settings |
| `%autopsy on` / `off` | start / stop watching loops |
| `%autopsy last` | show the last report again |
| `%autopsy heatmap on` / `off` / `auto` | cell health bar (`auto` = only when a cell is slow or memory-heavy) |
| `%autopsy summary` | the 10 heaviest cells of this session, worst first |
| `%autopsy summary all` / `clear` | every cell in run order / forget the history |
| `%autopsy memory` / `%autopsy memory 10` | memory in use, the 5 (or N) biggest variables with their sizes and share of your limit, and big results that the output history (`Out`, `_`) still keeps alive after you `del` the variable |
| `%autopsy config time_limit=120` | change a setting (see Settings below) |
| `%autopsy reset` | put every setting back to its default, and list the ones that changed |
| `%%autopsy off` | first line of a cell: run that cell without watching |
| `%autopsy help` | help |

Cells that use `%timeit` / `%%timeit` are never watched, so timings stay accurate.
Loops inside generators (`yield`) are left alone, because infinite generators are
normal; whatever uses the generator is watched instead.

Try [examples/demo.ipynb](examples/demo.ipynb) for every feature in one notebook.

### Compare variable sizes across cells

To see which variables grew while a cell ran, load the optional extension and
save a checkpoint before running the code you want to measure:

```python
%load_ext loop_autopsy.growth_diff
samples = bytearray(1024)
%growthdiff mark
```

In the next cell, grow the buffer and display the changes:

```python
samples.extend(bytes(4096))
%growthdiff show
```

The report lists `samples` as `grew` and shows its estimated increase in bytes.
New, removed and shrinking variables also appear, ordered by absolute size
change. Estimates are per variable name, so two names for the same object can
both appear. An object whose name was removed may still be held by another name or
by the notebook's output history.

| Command | Effect |
|---|---|
| `%growthdiff mark` | Save the current names and estimated sizes as the new checkpoint. |
| `%growthdiff` or `%growthdiff show` | Show up to 10 changes since the checkpoint. Repeated calls use the same checkpoint. |
| `%growthdiff show 3` | Show the three largest changes. Use any positive integer. |
| `%growthdiff clear` | Discard the checkpoint. |

### Inspect a variable's memory use

Load the optional memory inspection commands once per notebook:

```python
%load_ext loop_autopsy.memory_inspect
batch = {"metadata": "train", "pixels": bytearray(8192)}
preview = batch
%memaliases batch
%memparts batch
```

**`%memaliases NAME`** lists visible notebook names pointing to that exact object.
In the example, both `batch` and `preview` appear. If an output in IPython's
`Out` cache holds the object directly, its cell number appears too. References
inside containers and shared buffers between separate objects are outside the scan.

**`%memparts NAME [N]`** ranks the immediate values in a built-in dict, list or
tuple by estimated size. In the example, `batch['pixels']` ranks first.
It inspects the first 64 entries in iteration order and shows the largest five
among them by default. Use `%memparts batch 2` to show two. Each row measures
one value using the same estimator as `%autopsy memory`. Shared values can
appear more than once, and container overhead is excluded from the rows.

### Preview a growing-list loop

Use [the optional `%%growthcheck` command](docs/growth-check.md) to locate direct list updates inside a `for` loop before running the cell.

## Settings

Change any of these with `%autopsy config key=value` (for example `%autopsy config time_limit=120`).

| Setting | Default | Meaning |
|---|---|---|
| `time_limit` | 60 | seconds before a loop that cannot be proven stuck is stopped (0 = never) |
| `estimate_after` | 5 | seconds before a `for` loop's total time is estimated, to stop it early if it needs at least twice `time_limit` (0 = never) |
| `direction_after` | 500 | checks in a row moving away from the stop value before "wrong direction" |
| `jump_after` | 100 | checks in a row after jumping over a `!=` stop value |
| `growth_after` | 2000 | rounds before a growing list in a `for` loop is reported |
| `memory_stop` | 0.85 | stop a loop when memory reaches this fraction of the limit |
| `memory_warn` | 0.75 | health bar warning at this fraction of the limit |
| `max_output_lines` | 1000 | printed lines per cell before output is muted, counted separately for normal and error output; a line with no newline counts once per 1,000 characters (0 = off) |
| `max_displays` | 200 | `display()` calls per cell before they are muted (0 = off) |
| `muted_tail` | 20 | last muted lines shown when the cell ends (0 = none) |
| `replay_rows` | 6 | rows in the replay table |
| `max_watched` | 25 | variables watched per loop; the condition's and the ones the loop changes come first |
| `heatmap` | `auto` | `on`, `off` or `auto` |
| `time_yellow` / `time_red` | 1.0 / 10.0 | seconds for a yellow / red health bar |
| `mem_yellow` / `mem_red` | 0.50 / 0.80 | memory fraction for a yellow / red health bar |
| `history_cells` | 200 | cells remembered for `%autopsy summary` (0 = off) |
| `grow_yellow` / `grow_red` | 0.05 / 0.20 | summary: memory one cell added, as a fraction of the limit, for a yellow / red square |
| `rerun_growth_mb` | 10 | MB a cell must add on each of two re-runs in a row to be flagged as growing on every re-run (0 = off) |

Counts such as `direction_after` and `replay_rows` must be at least 1, and the
memory fractions (`memory_stop`, `memory_warn`, `mem_yellow`, `mem_red`,
`grow_yellow`, `grow_red`) must be above 0 and at most 1; other values are refused.

The summary also uses `time_yellow` / `time_red` for a cell's run time. Its history
lives in memory only and is gone when the kernel restarts. Spotting a cell that
grows on every re-run uses this history too, so it is off when `history_cells=0`.

The memory limit is read from JupyterHub's `MEM_LIMIT` setting (a number of bytes, or a
size such as `2G` or `512M`), then the container's memory limit, then the computer's total RAM.

## Limitations 

- No tool can detect *every* infinite loop (the halting problem). loop-autopsy
  only reports a stuck, cycling, wrong-direction or jumped-over loop when it can
  prove it; everything else gets the time limit.
- Loops that call your own functions or modules such as `random` cannot be proven
  stuck (those calls might change hidden state), so they rely on the time limit.
- Only code typed in notebook cells and scripts run with `%run` is watched, not
  code inside imported modules, code run with `exec()`, or functions defined
  before `%load_ext loop_autopsy`.
- Work done inside C code cannot be interrupted: a regular expression that
  backtracks for hours, `2 ** 10 ** 10`, or a NumPy call. A round stuck in
  `time.sleep()`, `input()` or a network call is stopped by a watchdog when the
  time limit passes (except while waiting for `input()`).
- A very high `sys.setrecursionlimit(...)` lets a runaway recursion crash the
  kernel before Python can raise `RecursionError`; on Python 3.11 and older,
  loop-autopsy warns when the limit is set above 10,000.
- Watching adds a small cost to each loop round: roughly 0.1–0.3 µs, measured with
  `benchmarks/overhead.py` on Python 3.12, and about 0.07 µs per item of a
  comprehension. That is invisible when a round does real work, but a loop that
  does almost nothing per round (such as `i += 1`) runs about 3 to 10 times
  slower. Use `%%autopsy off` for heavy number crunching.
- A bare `except:` around a loop can catch the stop signal, just as it catches ■,
  and so can `with suppress(BaseException):` or a `break`, `continue` or
  `return` inside a `finally:` block. loop-autopsy points this out with a note
  when the loop starts, but it does not change how your code behaves.
- Runaway recursion is explained only for functions typed in notebook cells, and
  only when the `RecursionError` reaches the top of the cell. A `RecursionError`
  your own code catches is left alone.

## Development

```bash
git clone https://github.com/Geoeoeo217/loop-autopsy
cd loop-autopsy
pip install -e ".[test]"
pytest
```

The tests also run on GitHub for every push and pull request, on Python 3.9 to
3.13 (Linux) and on Windows and macOS, with the oldest supported IPython, and
against the built wheel instead of the source folder; `mypy` checks the type
hints. See `.github/workflows/tests.yml`.

`tests/test_false_positives.py` runs correct loops that look suspicious (a binary
search, Collatz, a queue that grows before it empties) with the rules made
stricter than usual, and checks that none of them is stopped. When you change a
rule, add the correct loops it could get wrong.

Some tests start a real Jupyter kernel and run notebook cells in it, including
the whole demo notebook, because a notebook shows output differently from a
terminal. They are skipped if `nbclient` and `ipykernel` are not installed; the
`[test]` extra installs both.

After changing the demo notebook, save it with fresh outputs, since GitHub only
shows saved outputs:

```bash
python examples/refresh_demo.py
```

To measure what watching costs per loop round on your own computer:

```bash
python benchmarks/overhead.py
```
## Extras (optional)

The modules in `loop_autopsy/extras/` are optional helpers you import and call
yourself; the reports loop-autopsy shows on its own do not use them:

- [`growth_trace`](docs/growth-trace.md): locate notebook lines with growing live allocations after a loop
- `report_theme`: light/dark colours for an HTML report you build
- `report_i18n`: report labels in English, Indonesian and Chinese
- `report_export`: export reports to text or HTML files
- `sparkline`: mini chart of a variable's path against its stop value

The core feature of this project: **stopping the loop and explaining why.**

## Team

Built by a team of five for CS1302 (Introduction to Computer Programming).

| Member | Part |
| --- | --- |
| NATHANAEL George Steindhart | project lead: loop rewriting (`transformer.py`), run-time glue (`monitor.py`), settings (`config.py`), magics, packaging and docs |
| SETIAWAN Matthew Henson | detection rules: stuck, cycle, wrong direction, jumped over, time limit (`detectors.py`), runaway recursion (`recursion.py`), notes read from the code (`loop_notes.py`) |
| LATHIF Muhammad Akmal | memory and growing-list rules, `%autopsy memory` (`growth.py`); `%%growthcheck` (`growth_check.py`), `%growthdiff` (`growth_diff.py`), `%memaliases` / `%memparts` (`memory_inspect.py`), allocation tracing (`extras/growth_trace.py`); license checks and third-party licenses (`tests/test_license.py`, `THIRD_PARTY_LICENSES.md`) |
| KWAN Kendrick Matthew | replay table and reports (`report.py`), accessibility checks and report metrics (`report_accessibility.py`, `report_metrics.py`); optional extras for themes, translations, export and sparkline charts (`extras/`) |
| HANDAYA Clement Steward | cell health bar, session summary and output guard (`heatmap.py`); CI, benchmark, real-kernel and documentation tests |


## License

MIT. The licenses of the packages loop-autopsy uses are listed in
[THIRD_PARTY_LICENSES.md](THIRD_PARTY_LICENSES.md).
