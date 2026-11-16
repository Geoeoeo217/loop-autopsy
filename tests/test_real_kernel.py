"""Tests through a real Jupyter kernel.

The other end-to-end tests use IPython's terminal-style test shell. A notebook
is different: output travels as messages, reports are HTML and tracebacks do
not pass through ``sys.stdout``. These tests start a real kernel (the same one
JupyterLab uses) and run cells in it, so notebook-only behaviour is checked too.

They need ``nbclient`` and ``ipykernel`` (both in the ``[test]`` extra) and are
skipped when those are not installed.
"""
import os
import re
from pathlib import Path

import pytest

nbformat = pytest.importorskip("nbformat")
nbclient = pytest.importorskip("nbclient")
pytest.importorskip("ipykernel")

ROOT = Path(__file__).resolve().parent.parent
DEMO = ROOT / "examples" / "demo.ipynb"


def run_notebook(nb):
    """Execute ``nb`` in a fresh kernel that can import this checkout of loop-autopsy."""
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(filter(None, [str(ROOT / "src"), env.get("PYTHONPATH", "")]))
    env.pop("MEM_LIMIT", None)
    client = nbclient.NotebookClient(nb, timeout=120, kernel_name="python3", allow_errors=True,
                                     record_timing=False)
    client.execute(env=env)
    return nb


def text_of(cell) -> str:
    """Everything a student would read under a cell, as plain text."""
    parts = []
    for out in cell.outputs:
        if out.output_type == "stream":
            parts.append(out.text)
        elif out.output_type == "error":
            parts.append(f"{out.ename}: {out.evalue}")
        else:
            html = out.data.get("text/html")
            parts.append(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)) if html
                         else out.data.get("text/plain", ""))
    return "\n".join(parts)


def stream(cell, name: str) -> str:
    return "".join(o.text for o in cell.outputs if o.output_type == "stream" and o.name == name)


def displays(cell):
    return [o for o in cell.outputs if o.output_type == "display_data"]


def errors(cell):
    return [o.ename for o in cell.outputs if o.output_type == "error"]


CELLS = {
    "load": "%load_ext loop_autopsy",
    "config": "%autopsy config max_displays=5 max_output_lines=20",
    "stuck": "count = 1\nwhile count <= 5:\n    print(count)",
    "display_flood_then_stuck": ("from IPython.display import display\nfor i in range(50):\n    display(i)\n"
                                 "k = 1\nwhile k <= 5:\n    pass"),
    "stderr_flood": "import sys\nfor i in range(300):\n    print('warn', i, file=sys.stderr)",
    "print_flood_then_error": "for i in range(300):\n    print(i)\n1 / 0",
    "animation": ("from IPython.display import clear_output\nfor i in range(100):\n"
                  "    clear_output(wait=True)\n    display(f'frame {i}')"),
    "clean_again": "print('fresh'); display('shown'); print('err ok', file=sys.stderr)",
    "display_flood_then_recursion": "for i in range(50):\n    display(i)\ndef f(n):\n    return f(n)\nf(1)",
    "interrupt": ("t = 0\nidle = 0\nwhile t < 50:\n    t += 1\n    idle += 0\n"
                  "    if t == 40:\n        raise KeyboardInterrupt"),
    "bar": "%autopsy heatmap on\nvalue = sum(range(1000))",
    "bar_off": "%autopsy heatmap auto",
    "last": "%autopsy last",
    "summary": "%autopsy summary all",
}


@pytest.fixture(scope="module")
def session():
    """One kernel for the whole module: ``session["name"]`` is that cell after it ran."""
    nb = nbformat.v4.new_notebook(cells=[nbformat.v4.new_code_cell(src) for src in CELLS.values()])
    run_notebook(nb)
    return dict(zip(CELLS, nb.cells))


def test_extension_loads_in_a_kernel(session):
    assert "is watching your loops" in stream(session["load"], "stdout")
    assert not errors(session["load"])


def test_stuck_loop_report_is_html(session):
    cell = session["stuck"]
    assert not errors(cell)  # a stopped loop is not shown as a Python error
    report = displays(cell)[-1].data
    assert "text/html" in report and "border-left:4px solid" in report["text/html"]
    assert "Loop autopsy — stuck loop (line 2)" in text_of(cell)


def test_report_still_shows_after_display_is_muted(session):
    cell = session["display_flood_then_stuck"]
    shown = [o.data.get("text/plain") for o in displays(cell)]
    assert shown[:5] == ["0", "1", "2", "3", "4"]
    assert sum("Display output muted after 5 items" in (s or "") for s in shown) == 1
    assert len(shown) == 7  # five items, the notice, the report
    assert "Loop autopsy — stuck loop" in text_of(cell)


def test_stderr_flood_is_muted(session):
    err = stream(session["stderr_flood"], "stderr")
    assert "warn 19" in err and "warn 20" not in err and err.count("Output muted after 20 lines") == 1


def test_error_is_reported_after_a_muted_flood(session):
    cell = session["print_flood_then_error"]
    assert stream(cell, "stdout").count("Output muted") == 1
    assert errors(cell) == ["ZeroDivisionError"]


def test_animation_is_not_muted(session):
    cell = session["animation"]
    assert [o.data["text/plain"] for o in displays(cell)] == ["'frame 99'"]
    assert "muted" not in text_of(cell)


def test_next_cell_starts_clean(session):
    cell = session["clean_again"]
    assert stream(cell, "stdout") == "fresh\n" and stream(cell, "stderr") == "err ok\n"
    assert [o.data["text/plain"] for o in displays(cell)] == ["'shown'"]


def test_recursion_report_in_a_kernel(session):
    cell = session["display_flood_then_recursion"]
    assert not errors(cell)  # the report replaces the long traceback
    text = text_of(cell)
    assert "recursion stuck on the same arguments" in text and "First and last calls:" in text
    assert "Display output muted" in text


def test_stop_button_report_in_a_kernel(session):
    cell = session["interrupt"]
    assert errors(cell) == ["KeyboardInterrupt"]  # Jupyter's own message is kept
    assert "you stopped this loop" in text_of(cell) and "idle" in text_of(cell)


def test_health_bar_in_a_kernel(session):
    bar = displays(session["bar"])[-1].data["text/html"]
    assert "healthy" in bar and "time" in bar and "memory" in bar
    assert not displays(session["bar_off"])  # auto mode: a quick cell shows no bar


def test_last_and_summary_in_a_kernel(session):
    assert "you stopped this loop" in text_of(session["last"])
    summary = displays(session["summary"])[-1].data["text/html"]
    assert "Session summary" in summary and summary.count("display:inline-block") >= 8
    text = text_of(session["summary"])
    assert "output muted" in text and "1 stopped" in text and "interrupted" in text
    assert "%autopsy config" not in text  # cells that are only our own commands are left out


# --------------------------------------------------------------------------- the demo notebook
@pytest.fixture(scope="module")
def demo():
    nb = nbformat.read(DEMO, as_version=4)
    for cell in nb.cells:
        if cell.cell_type == "code":
            cell.outputs = []
            if cell.source.startswith("%pip"):
                cell.source = "pass  # the test uses this checkout, not the copy on PyPI"
    run_notebook(nb)
    return [c for c in nb.cells if c.cell_type == "code"]


def test_demo_notebook_runs_without_errors(demo):
    failed = [(c.source.splitlines()[0], errors(c)) for c in demo if errors(c)]
    assert not failed, f"demo cells raised: {failed}"


def test_demo_notebook_shows_every_feature(demo):
    everything = "\n".join(text_of(c) for c in demo)
    for expected in ("stuck loop", "loop moving the wrong way", "loop jumped over its stop value",
                     "loop stuck in a 3-round cycle", "loop over a list that keeps growing",
                     "recursion went past its base case", "Collatz steps: 111", "Session summary",
                     "Memory:", "ran without watching: 100000", "loop-autopsy is ON"):
        assert expected in everything, f"the demo no longer shows: {expected}"


def test_demo_broken_loops_each_get_a_report(demo):
    for cell in demo:
        if re.match(r"(count|i|x|a|nums) = ", cell.source) or cell.source.startswith("def countdown"):
            assert "Loop autopsy" in text_of(cell), cell.source
