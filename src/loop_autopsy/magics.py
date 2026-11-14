"""``%autopsy`` line magic and ``%%autopsy off`` cell magic. Owner: George."""

from __future__ import annotations

from IPython.core.magic import Magics, line_cell_magic, magics_class

HELP = """loop-autopsy commands
  %autopsy                  show status and settings
  %autopsy on | off         start / stop watching loops
  %autopsy last             show the last report again
  %autopsy heatmap on|off|auto   cell health bar (auto = only when worth seeing)
  %autopsy summary [all|clear]   every cell run this session, heaviest first
  %autopsy config key=value ...  change a setting, e.g. %autopsy config time_limit=120
  %%autopsy off             (first line of a cell) run that cell without watching
"""


@magics_class
class AutopsyMagics(Magics):
    def __init__(self, shell, monitor, health=None):
        super().__init__(shell)
        self.monitor = monitor
        self.health = health  # CellHealth, for %autopsy summary

    @line_cell_magic
    def autopsy(self, line: str, cell: str = None):
        args = line.split()
        if cell is not None:
            if args[:1] == ["off"]:
                with self.monitor.paused():
                    self.shell.run_cell(cell, store_history=False)
                return None
            print("Use %%autopsy off on the first line of a cell to run it without loop watching.")
            return None

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
        elif command == "config":
            if len(args) == 1:
                print("Usage: %autopsy config key=value [key=value ...]")
            for pair in args[1:]:
                key, _, raw = pair.partition("=")
                try:
                    print(f"{key} = {settings.update(key, raw)}")
                except (KeyError, ValueError) as err:
                    print(f"Could not set {key!r}: {err}")
        else:
            print(HELP)
        return None
