"""Small accessibility checks for rendered Loop Autopsy HTML."""

from __future__ import annotations

import re
from typing import List

_IMAGE_RE = re.compile(r"<img\b[^>]*>", re.IGNORECASE)
_ALT_RE = re.compile(r"\balt\s*=\s*(['\"]).*?\1", re.IGNORECASE | re.DOTALL)


def accessibility_issues(html: str) -> List[str]:
    """Return simple, actionable accessibility warnings for report HTML."""
    issues: List[str] = []

    if not re.search(r"<html\b[^>]*\blang\s*=", html, re.IGNORECASE):
        issues.append("HTML document should declare a language with <html lang=...>.")

    for image in _IMAGE_RE.findall(html):
        if not _ALT_RE.search(image):
            issues.append("Each <img> element should have an alt attribute.")

    if not re.search(r"<title\b[^>]*>.*?</title>", html, re.IGNORECASE | re.DOTALL):
        issues.append("HTML document should include a <title> element.")

    return issues


def is_accessible_report(html: str) -> bool:
    """Return True when the basic report accessibility checks pass."""
    return not accessibility_issues(html)
