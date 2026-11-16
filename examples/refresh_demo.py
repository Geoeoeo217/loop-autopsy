"""Run ``demo.ipynb`` in a real Jupyter kernel and save its outputs.

GitHub shows a notebook's saved outputs, so the demo should always be committed
with them. After you change the demo, or anything it shows, run from the project
folder::

    python examples/refresh_demo.py

It needs ``nbclient`` and ``ipykernel`` (``pip install -e ".[test]"``). The
``%pip install`` cell is not run: the outputs come from this checkout, not from
the copy on PyPI.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import nbformat
from nbclient import NotebookClient

ROOT = Path(__file__).resolve().parent.parent
DEMO = ROOT / "examples" / "demo.ipynb"
SKIPPED = "pass"


def main() -> int:
    raw = DEMO.read_bytes()
    newline = "\r\n" if b"\r\n" in raw else "\n"  # keep the file's line endings
    nb = nbformat.reads(raw.decode("utf-8"), as_version=4)

    pip_cells = {}
    for index, cell in enumerate(nb.cells):
        if cell.cell_type != "code":
            continue
        cell.outputs, cell.execution_count = [], None
        cell.metadata.pop("execution", None)  # run times: they would change on every refresh
        if cell.source.startswith("%pip"):
            pip_cells[index], cell.source = cell.source, SKIPPED

    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(filter(None, [str(ROOT / "src"), env.get("PYTHONPATH", "")]))
    env.pop("MEM_LIMIT", None)
    client = NotebookClient(nb, timeout=180, kernel_name="python3", allow_errors=True, record_timing=False)
    client.execute(env=env)

    for index, source in pip_cells.items():  # put the install line back, without output
        cell = nb.cells[index]
        cell.source, cell.outputs, cell.execution_count = source, [], None

    failed = [cell.source.splitlines()[0] for cell in nb.cells if cell.cell_type == "code"
              and any(out.output_type == "error" for out in cell.outputs)]
    if failed:
        print("Not saved: these cells raised an error:", *failed, sep="\n  ")
        return 1

    text = nbformat.writes(nb)
    if not text.endswith("\n"):
        text += "\n"
    DEMO.write_bytes(text.replace("\r\n", "\n").replace("\n", newline).encode("utf-8"))
    shown = sum(1 for cell in nb.cells if cell.cell_type == "code" and cell.outputs)
    print(f"Saved {DEMO.relative_to(ROOT)}: {shown} cells with output.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
