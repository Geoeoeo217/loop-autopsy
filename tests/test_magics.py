"""Tests for magics.py: the ``%autopsy`` line magic and ``%%autopsy off`` cell magic."""
import loop_autopsy
from loop_autopsy.config import LoopStopped, Settings
from loop_autopsy.magics import HELP, AutopsyMagics
from loop_autopsy.monitor import Monitor


def out_of(shell, capsys, code):
    capsys.readouterr()
    shell.run_cell(code)
    return capsys.readouterr().out


def monitor_of(shell):
    return loop_autopsy._LOADED[id(shell)][0]


def test_status_lists_every_setting(shell, capsys):
    for command in ("%autopsy", "%autopsy status"):
        out = out_of(shell, capsys, command)
        assert "loop-autopsy is ON. Settings:" in out
        for key in Settings().as_dict():
            assert key in out
    shell.run_cell("%autopsy off")
    assert "loop-autopsy is OFF" in out_of(shell, capsys, "%autopsy")


def test_on_and_off_switch_watching(shell, capsys):
    stuck = "z = 1\nwhile z <= 5:\n    pass\n    break\n"
    assert "now OFF" in out_of(shell, capsys, "%autopsy OFF")  # commands ignore case
    assert monitor_of(shell).enabled is False
    assert shell.run_cell(stuck).error_in_exec is None
    assert "now ON" in out_of(shell, capsys, "%autopsy on")
    assert monitor_of(shell).enabled is True


def test_last_before_any_report(shell, capsys):
    assert "No loop has been stopped yet." in out_of(shell, capsys, "%autopsy last")


def test_heatmap_modes(shell, capsys):
    settings = monitor_of(shell).settings
    for mode in ("on", "off", "auto"):
        assert f"Cell health bar: {mode}" in out_of(shell, capsys, f"%autopsy heatmap {mode}")
        assert settings.heatmap == mode
    assert "heatmap must be on, off or auto" in out_of(shell, capsys, "%autopsy heatmap purple")
    assert "heatmap must be on, off or auto" in out_of(shell, capsys, "%autopsy heatmap")
    assert settings.heatmap == "auto"  # a bad value changes nothing


def test_config_changes_settings(shell, capsys):
    settings = monitor_of(shell).settings
    out = out_of(shell, capsys, "%autopsy config time_limit=5 replay_rows=3 heatmap=ON")
    assert "time_limit = 5.0" in out and "replay_rows = 3" in out and "heatmap = on" in out
    assert (settings.time_limit, settings.replay_rows, settings.heatmap) == (5.0, 3, "on")


def test_config_rejects_bad_input(shell, capsys):
    settings = monitor_of(shell).settings
    assert "Usage: %autopsy config key=value" in out_of(shell, capsys, "%autopsy config")
    out = out_of(shell, capsys, "%autopsy config nope=1 time_limit=-3 replay_rows=many time_limit")
    assert "Could not set 'nope'" in out and "unknown setting" in out
    assert "cannot be negative" in out
    assert out.count("Could not set") == 4
    assert settings.time_limit == 60.0 and settings.replay_rows == 6  # nothing changed


def test_help_and_unknown_commands(shell, capsys):
    assert HELP.strip() in out_of(shell, capsys, "%autopsy help")
    assert HELP.strip() in out_of(shell, capsys, "%autopsy dance")
    for command in ("on | off", "last", "heatmap", "summary", "config", "%%autopsy off"):
        assert command in HELP


def test_cell_magic_off_runs_the_cell_unwatched(shell):
    monitor = monitor_of(shell)
    before = len(monitor.infos)
    result = shell.run_cell("%%autopsy off\ncounter = 0\nwhile counter < 300:\n    counter += 1\n")
    assert result.error_in_exec is None and shell.user_ns["counter"] == 300
    assert len(monitor.infos) == before  # the loop was never instrumented
    assert not monitor.is_paused()  # watching is back on for the next cell
    assert isinstance(shell.run_cell("y = 1\nwhile y <= 5:\n    pass\n").error_in_exec, LoopStopped)


def test_cell_magic_with_another_word_does_not_run_the_cell(shell):
    for first_line in ("%%autopsy on", "%%autopsy of", "%%autopsy"):  # "of": a typo for "off"
        result = shell.run_cell(first_line + "\nran_anyway = True\n")
        assert type(result.error_in_exec).__name__ == "UsageError"  # an error, so Run All stops here
        assert "The cell was not run" in str(result.error_in_exec)
        assert "ran_anyway" not in shell.user_ns


def test_summary_without_cell_health_shows_help(shell, capsys):
    magics = AutopsyMagics(shell, Monitor(shell, Settings()))  # no CellHealth passed
    magics.autopsy("summary")
    assert "loop-autopsy commands" in capsys.readouterr().out


def test_loading_twice_and_unloading_twice(shell, capsys):
    assert "already loaded" in out_of(shell, capsys, "%load_ext loop_autopsy")
    loop_autopsy.load_ipython_extension(shell)
    assert "already loaded" in capsys.readouterr().out
    loop_autopsy.unload_ipython_extension(shell)
    loop_autopsy.unload_ipython_extension(shell)  # nothing to unload: must not fail
    assert id(shell) not in loop_autopsy._LOADED
    loop_autopsy.load_ipython_extension(shell)  # leave it loaded for the fixture's teardown
