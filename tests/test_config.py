import pytest

from loop_autopsy.config import Finding, LoopInfo, LoopState, LoopStopped, Settings


def test_defaults():
    s = Settings()
    assert s.time_limit == 60.0 and s.replay_rows == 6 and s.heatmap == "auto"
    assert set(s.as_dict()) >= {"time_limit", "max_watched", "heatmap", "history_cells"}


def test_update_converts_to_the_setting_type():
    s = Settings()
    assert s.update("time_limit", "120") == 120.0 and isinstance(s.time_limit, float)
    assert s.update("replay_rows", "10") == 10 and isinstance(s.replay_rows, int)
    assert s.update("max_watched", "2.7") == 2  # whole numbers are rounded down
    assert s.update("time_limit", "1e2") == 100.0


def test_update_zero_is_allowed():
    s = Settings()
    assert s.update("time_limit", "0") == 0.0  # 0 = never stop on time
    assert s.update("max_output_lines", "0") == 0


def test_heatmap_modes():
    s = Settings()
    assert s.update("heatmap", " ON ") == "on"
    assert s.update("heatmap", "'off'") == "off"
    with pytest.raises(ValueError, match="on, off or auto"):
        s.update("heatmap", "sometimes")


def test_unknown_setting_lists_the_choices():
    with pytest.raises(KeyError, match="time_limit"):
        Settings().update("speed", "3")


@pytest.mark.parametrize("key, raw", [("time_limit", "-1"), ("replay_rows", "-3"), ("time_limit", "abc")])
def test_bad_values_are_rejected_and_nothing_changes(key, raw):
    s = Settings()
    before = s.as_dict()
    with pytest.raises(ValueError):
        s.update(key, raw)
    assert s.as_dict() == before


@pytest.mark.parametrize("raw", ["inf", "-inf", "nan", "1e999"])
def test_infinite_and_nan_values_are_rejected(raw):
    s = Settings()
    for key in ("time_limit", "replay_rows"):
        with pytest.raises(ValueError, match="finite number"):
            s.update(key, raw)
    assert s.time_limit == 60.0 and s.replay_rows == 6


def test_missing_value_says_how_to_write_it():
    with pytest.raises(ValueError, match="time_limit=NUMBER"):
        Settings().update("time_limit", "")


def test_magic_reports_bad_values_without_a_traceback(shell, capsys):
    shell.run_cell("%autopsy config replay_rows=inf time_limit")
    out = capsys.readouterr().out
    assert "Could not set 'replay_rows'" in out and "finite number" in out
    assert "time_limit=NUMBER" in out and "Traceback" not in out


def test_settings_are_independent():
    a, b = Settings(), Settings()
    a.update("time_limit", "5")
    assert b.time_limit == 60.0


def test_loop_state_history_follows_replay_rows():
    info = LoopInfo(loop_id=0, kind="while", lineno=1, header="while x:")
    assert LoopState(info, started=1.0, replay_rows=3).history.maxlen == 3
    assert LoopState(info, started=1.0, replay_rows=0).history.maxlen == 1
    assert LoopState(info, started=1.0).last_check == 1.0


def test_loop_stopped_is_not_an_exception():
    finding = Finding("stuck", "stuck loop", "nothing changed", "change x")
    stop = LoopStopped(finding)
    assert not isinstance(stop, Exception)  # `except Exception:` in a student loop cannot swallow it
    assert stop.finding is finding and str(stop) == "stuck loop"


def test_reset_restores_defaults_in_place_and_reports_changes():
    s = Settings()
    same = s
    s.update("time_limit", "0")
    s.update("heatmap", "off")
    assert s.reset() == {"time_limit": (0.0, 60.0), "heatmap": ("off", "auto")}
    assert same is s and s.as_dict() == Settings().as_dict()
    assert s.reset() == {}  # nothing left to change


def test_reset_magic(shell, capsys):
    capsys.readouterr()
    shell.run_cell("%autopsy config time_limit=0 replay_rows=3")
    shell.run_cell("%autopsy reset")
    out = capsys.readouterr().out
    assert "back to their defaults" in out
    assert "time_limit       0.0 -> 60.0" in out and "replay_rows      3 -> 6" in out
    shell.run_cell("%autopsy reset")
    assert "already have their default values" in capsys.readouterr().out
    shell.run_cell("%autopsy help")
    assert "%autopsy reset" in capsys.readouterr().out


@pytest.mark.parametrize("typed, suggestion", [("stauts", "status"), ("rest", "reset"), ("memroy", "memory"),
                                               ("sumary", "summary")])
def test_mistyped_command_suggests_the_closest_one(typed, suggestion):
    from loop_autopsy.magics import unknown_command
    assert unknown_command(typed) == f"Unknown command '{typed}'. Did you mean '{suggestion}'?"


def test_unknown_command_without_a_close_match():
    from loop_autopsy.magics import unknown_command
    assert unknown_command("dance") == "Unknown command 'dance'."


def test_mistyped_command_in_the_magic_still_shows_help(shell, capsys):
    from loop_autopsy.magics import HELP
    capsys.readouterr()
    shell.run_cell("%autopsy stauts")
    out = capsys.readouterr().out
    assert out.startswith("Unknown command 'stauts'. Did you mean 'status'?") and HELP.strip() in out
    shell.run_cell("%autopsy help")
    assert "Unknown command" not in capsys.readouterr().out


@pytest.mark.parametrize("typed, suggestion", [("time_limt", "time_limit"), ("replayrows", "replay_rows"),
                                               ("heatmapp", "heatmap")])
def test_mistyped_setting_suggests_the_closest_one(typed, suggestion):
    with pytest.raises(KeyError) as err:
        Settings().update(typed, "5")
    assert str(err.value) == f"unknown setting; did you mean '{suggestion}'?"


def test_unknown_setting_message_has_no_extra_quotes(shell, capsys):
    capsys.readouterr()
    shell.run_cell("%autopsy config time_limt=5")
    out = capsys.readouterr().out
    assert out.strip() == "Could not set 'time_limt': unknown setting; did you mean 'time_limit'?"
