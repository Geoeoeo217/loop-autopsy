"""Export already-rendered Loop Autopsy reports to files.

The helpers accept the text/HTML produced by the existing report renderer.
They intentionally do not modify the existing report.py API.
"""

from __future__ import annotations

from pathlib import Path
from typing import Union

PathLike = Union[str, Path]


def export_text(report: str, path: PathLike) -> Path:
    """Write a plain-text report and return the resulting Path."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(report, encoding="utf-8")
    return target


def export_html(html_report: str, path: PathLike, title: str = "Loop Autopsy Report") -> Path:
    """Write a self-contained HTML document and return the resulting Path."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)

    document = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{_escape(title)}</title>
</head>
<body>
{html_report}
</body>
</html>
"""
    target.write_text(document, encoding="utf-8")
    return target


def _escape(value: str) -> str:
    """Escape a value used inside an HTML title."""
    return (
        str(value)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )
