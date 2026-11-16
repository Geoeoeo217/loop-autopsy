from loop_autopsy.report_theme import DARK_THEME, LIGHT_THEME, get_theme, theme_from_settings


def test_default_theme_is_light():
    assert get_theme().name == "light"
    assert get_theme().background == LIGHT_THEME.background


def test_dark_theme_has_dark_background():
    theme = get_theme("dark")
    assert theme is DARK_THEME
    assert theme.background == "#1e1e1e"
    assert theme.foreground != LIGHT_THEME.foreground


def test_theme_names_are_case_insensitive():
    assert get_theme(" DARK ").name == "dark"


def test_unknown_theme_is_rejected():
    try:
        get_theme("sepia")
    except ValueError as exc:
        assert "light" in str(exc) and "dark" in str(exc)
    else:
        raise AssertionError("unknown themes should raise ValueError")


def test_theme_from_settings_keeps_light_default():
    class Settings:
        pass

    assert theme_from_settings(Settings()).name == "light"


def test_theme_from_settings_reads_report_theme():
    class Settings:
        report_theme = "dark"

    assert theme_from_settings(Settings()) is DARK_THEME
