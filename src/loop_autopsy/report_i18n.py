"""Small, dependency-free translation layer for report UI text.

Only fixed UI labels live here. Finding explanations and hints remain supplied
by the detector so existing reports are not unexpectedly translated.
"""

from __future__ import annotations

from typing import Mapping

SUPPORTED_LANGUAGES = ("en", "id", "zh")

MESSAGES: Mapping[str, Mapping[str, str]] = {
    "en": {
        "title": "Loop autopsy",
        "last_rounds": "Last rounds checked:",
        "first_calls": "First and last calls:",
        "hint": "Hint:",
        "no_variables": "(no variables to show)",
        "same": "(same)",
        "round": "round",
        "call": "call",
        "stopped_rounds": "Stopped after {rounds:,} rounds{elapsed}",
        "stopped_calls": "Stopped by Python after {rounds:,} nested calls",
    },
    "id": {
        "title": "Autopsi loop",
        "last_rounds": "Putaran terakhir yang diperiksa:",
        "first_calls": "Pemanggilan pertama dan terakhir:",
        "hint": "Petunjuk:",
        "no_variables": "(tidak ada variabel untuk ditampilkan)",
        "same": "(sama)",
        "round": "putaran",
        "call": "pemanggilan",
        "stopped_rounds": "Berhenti setelah {rounds:,} putaran{elapsed}",
        "stopped_calls": "Dihentikan Python setelah {rounds:,} pemanggilan bertingkat",
    },
    "zh": {
        "title": "循环分析",
        "last_rounds": "最近检查的循环轮次：",
        "first_calls": "第一次和最后一次调用：",
        "hint": "提示：",
        "no_variables": "（没有可显示的变量）",
        "same": "（相同）",
        "round": "轮",
        "call": "调用",
        "stopped_rounds": "运行 {rounds:,} 轮后停止{elapsed}",
        "stopped_calls": "Python 在 {rounds:,} 次嵌套调用后停止",
    },
}


def normalize_language(language: str | None) -> str:
    """Normalize a language code and reject unsupported languages."""
    code = (language or "en").strip().lower().replace("_", "-")
    code = code.split("-", 1)[0]
    if code not in SUPPORTED_LANGUAGES:
        raise ValueError(
            f"Unsupported report language: {language!r}. "
            f"Choose from {', '.join(SUPPORTED_LANGUAGES)}."
        )
    return code


def message(key: str, language: str | None = "en", **values: object) -> str:
    """Return one translated report label."""
    lang = normalize_language(language)
    try:
        template = MESSAGES[lang][key]
    except KeyError as exc:
        raise KeyError(f"Unknown report message: {key!r}") from exc
    return template.format(**values)


def available_languages() -> tuple[str, ...]:
    """Return the language codes supported by the report UI."""
    return SUPPORTED_LANGUAGES
