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
