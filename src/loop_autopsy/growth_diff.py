"""Compare estimated notebook variable sizes before and after running a cell.

Load with %load_ext loop_autopsy.growth_diff, then run %growthdiff mark,
execute the code of interest, and run %growthdiff show.
"""

from __future__ import annotations

from IPython.core.error import UsageError

from .growth import format_bytes, largest_variables


def _snapshot(namespace):
    # Keep only names and byte counts so a checkpoint cannot keep objects alive.
    return dict(largest_variables(namespace, top=len(namespace)))


def _report(before, after, limit):
    changes = []
    for name in before.keys() | after.keys():
        delta = after.get(name, 0) - before.get(name, 0)
        if name not in before:
            status = "new"
        elif name not in after:
            status = "removed"
        elif delta:
            status = "grew" if delta > 0 else "shrank"
        else:
            continue
        changes.append((name, delta, status))

    if not changes:
        return "No estimated variable sizes changed since the checkpoint."

    changes.sort(key=lambda change: (-abs(change[1]), change[0]))
    lines = ["Estimated size change by variable (largest absolute change first):"]
    for name, delta, status in changes[:limit]:
        sign = "+" if delta > 0 else "-" if delta < 0 else ""
        lines.append(f"  {name}  {sign}{format_bytes(abs(delta))}  ({status})")
    if len(changes) > limit:
        lines.append(f"Showing {limit} of {len(changes)} changed names.")
    return "\n".join(lines)


def load_ipython_extension(ip):
    """Register a line magic with one checkpoint per interactive shell."""
    if ip.find_line_magic("growthdiff") is not None:
        raise UsageError("A %growthdiff magic is already registered.")

    checkpoint = None

    def growthdiff(line):
        nonlocal checkpoint
        args = line.split()
        if args == ["mark"]:
            checkpoint = _snapshot(ip.user_ns)
            print(f"Checkpoint saved for {len(checkpoint)} variables.")
        elif args == ["clear"]:
            checkpoint = None
            print("Checkpoint cleared.")
        elif not args or (args[0] == "show" and len(args) <= 2):
            limit = 10
            if len(args) == 2:
                try:
                    limit = int(args[1])
                except ValueError:
                    raise UsageError("Use %growthdiff show with a positive integer.") from None
                if limit <= 0:
                    raise UsageError("Use %growthdiff show with a positive integer.")
            if checkpoint is None:
                raise UsageError("Run %growthdiff mark to save a checkpoint first.")
            print(_report(checkpoint, _snapshot(ip.user_ns), limit))
        else:
            raise UsageError("Use %growthdiff mark, show [N], or clear.")

    ip.register_magic_function(growthdiff, magic_kind="line", magic_name="growthdiff")
    ip._loop_autopsy_growthdiff = growthdiff


def unload_ipython_extension(ip):
    magic = getattr(ip, "_loop_autopsy_growthdiff", None)
    if magic is not None:
        if ip.find_line_magic("growthdiff") is magic:
            del ip.magics_manager.magics["line"]["growthdiff"]
        del ip._loop_autopsy_growthdiff
