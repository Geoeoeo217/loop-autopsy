import pytest

from loop_autopsy.report_i18n import (
    available_languages,
    message,
    normalize_language,
)


def test_supported_languages_are_available():
    assert available_languages() == ("en", "id", "zh")


def test_default_language_is_english():
    assert message("hint") == "Hint:"


def test_indonesian_messages():
    assert message("hint", "id") == "Petunjuk:"
    assert message("last_rounds", "id").startswith("Putaran")


def test_mandarin_messages():
    assert message("hint", "zh") == "提示："


def test_language_codes_accept_locale_suffixes():
    assert normalize_language("ID-id") == "id"
    assert normalize_language("zh_CN") == "zh"


def test_unknown_language_is_rejected():
    with pytest.raises(ValueError):
        normalize_language("fr")
