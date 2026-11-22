"""``%autopsy`` line magic and ``%%autopsy off`` cell magic. Owner: George."""

from __future__ import annotations

from difflib import get_close_matches
from typing import Optional

from IPython.core.error import UsageError
from IPython.core.magic import Magics, line_cell_magic, magics_class

from . import growth

HELP = """loop-autopsy commands
  %autopsy                  show status and settings
  %autopsy on | off         start / stop watching loops
  %autopsy last             show the last report again
  %autopsy heatmap on|off|auto   cell health bar (auto = only when worth seeing)
  %autopsy summary [all|clear]   every cell run this session, heaviest first
  %autopsy memory [N]       memory in use and the N biggest variables (default 5)
  %autopsy config key=value ...  change a setting, e.g. %autopsy config time_limit=120
  %autopsy reset            put every setting back to its default
  %%autopsy off             (first line of a cell) run that cell without watching
"""

COMMANDS = ("status", "on", "off", "last", "heatmap", "summary", "memory", "config", "reset", "help")


class CellFailed(Exception):
    """A cell run with ``%%autopsy off`` raised an error. Its traceback was already
    shown, so this one is shown as a single line instead of a second traceback."""

    def __init__(self, error: Optional[BaseException] = None):
        super().__init__(f"the cell raised {type(error).__name__}" if error is not None else "the cell failed")
        self.error = error

    def _render_traceback_(self):  # IPython shows these lines instead of a traceback
        return [f"(%%autopsy off: {self.args[0]}, see above)"]


def unknown_command(command: str) -> str:
    """A one-line note for a mistyped command, with the closest real one if there is one."""
    close = get_close_matches(command, COMMANDS, n=1, cutoff=0.6)
    hint = f" Did you mean '{close[0]}'?" if close else ""
    return f"Unknown command '{command}'.{hint}"


@magics_class
class AutopsyMagics(Magics):
    def __init__(self, shell, monitor, health=None):
        super().__init__(shell)
        self.monitor = monitor
        self.health = health  # CellHealth, for %autopsy summary

    @line_cell_magic
    def autopsy(self, line: str, cell: Optional[str] = None):
        args = line.split()
        if cell is not None:
            if args[:1] == ["off"]:
                assert self.shell is not None
                with self.monitor.paused():
                    result = self.shell.run_cell(cell, store_history=False)
                if result is not None and not result.success:
                    # The inner cell already showed its error. Fail this cell too, so that
                    # Jupyter marks it as failed and "Run All" stops here.
                    raise CellFailed(result.error_in_exec or result.error_before_exec)
                return None
            # An error, not just a message: a typo such as "%%autopsy of" must not quietly
            # skip the cell while "Run All" carries on as if it had run.
            given = f" (it says '{line.strip()}')" if line.strip() else ""
            raise UsageError("The cell was not run. Use %%autopsy off on the first line of a cell "
                             f"to run it without loop watching{given}.")

        command = args[0].lower() if args else "status"
        settings = self.monitor.settings
        if command == "status":
            state = "ON" if self.monitor.enabled else "OFF"
            print(f"loop-autopsy is {state}. Settings:")
            for key, value in settings.as_dict().items():
                print(f"  {key:<17}{value}")
        elif command in ("on", "off"):
            self.monitor.enabled = command == "on"
            print(f"loop-autopsy is now {command.upper()}.")
        elif command == "last":
            if not self.monitor.show_last():
                print("No loop has been stopped yet.")
        elif command == "heatmap":
            mode = args[1].lower() if len(args) > 1 else ""
            try:
                settings.update("heatmap", mode)
                print(f"Cell health bar: {mode}")
            except ValueError as err:
                print(err)
        elif command == "summary" and self.health is not None:
            self.health.summary(args[1].lower() if len(args) > 1 else "")
        elif command == "memory":
            try:
                top = max(1, int(args[1])) if len(args) > 1 else 5
            except ValueError:
                print("Usage: %autopsy memory [number of variables to show]")
                return None
            assert self.shell is not None
            print(growth.memory_overview(self.shell.user_ns, top))
        elif command == "config":
            if len(args) == 1:
                print("Usage: %autopsy config key=value [key=value ...]")
            for pair in args[1:]:
                key, _, raw = pair.partition("=")
                try:
                    print(f"{key} = {settings.update(key, raw)}")
                except (KeyError, ValueError) as err:
                    print(f"Could not set {key!r}: {err}")
        elif command == "reset":
            changed = settings.reset()
            if not changed:
                print("All settings already have their default values.")
            else:
                print("Settings are back to their defaults:")
                for key, (old, new) in changed.items():
                    print(f"  {key:<17}{old} -> {new}")
        else:
            if command not in COMMANDS:
                print(unknown_command(command) + "\n")
            print(HELP)
        return None
