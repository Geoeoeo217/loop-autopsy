"""Theme helpers for loop-autopsy reports.

The module is intentionally independent from report.py so applications can
choose a visual theme without changing report generation logic.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

THEMES = ("light", "dark")


@dataclass(frozen=True)
class ReportTheme:
    """Colors used by the HTML report container."""

    name: str
    background: str
    foreground: str
    muted: str
    border: str
    code_background: str


LIGHT_THEME = ReportTheme(
    name="light",
    background="#ffffff",
    foreground="#212529",
    muted="#6c757d",
    border="rgba(128,128,128,.35)",
    code_background="rgba(128,128,128,.08)",
)

DARK_THEME = ReportTheme(
    name="dark",
    background="#1e1e1e",
    foreground="#f1f3f5",
    muted="#adb5bd",
    border="rgba(255,255,255,.22)",
    code_background="rgba(255,255,255,.08)",
)

_THEME_MAP: Mapping[str, ReportTheme] = {
    "light": LIGHT_THEME,
    "dark": DARK_THEME,
}


def get_theme(name: str = "light") -> ReportTheme:
    """Return a report theme by name.

    Unknown names fail loudly instead of silently changing the user's
    requested report appearance.
    """
    key = name.strip().lower()
    if key not in _THEME_MAP:
        raise ValueError(f"Unknown report theme: {name!r}. Choose from {THEMES}.")
    return _THEME_MAP[key]


def theme_from_settings(settings: object | None) -> ReportTheme:
    """Read ``report_theme`` from a settings-like object.

    A missing setting keeps the existing light appearance.
    """
    name = getattr(settings, "report_theme", "light")
    return get_theme(str(name))
