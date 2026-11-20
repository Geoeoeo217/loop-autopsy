"""Optional notebook commands for finding aliases and inspecting container values."""

from itertools import islice

from IPython.core.error import UsageError

from .growth import format_bytes, size_of

_SCAN_LIMIT = 64


def _aliases(shell, name, value):
    namespace = shell.user_ns
    hidden = shell.user_ns_hidden
    names = sorted(key for key, obj in namespace.items()
                   if obj is value and (key == name or
                   (not key.startswith("_") and key not in hidden)))
    outputs = namespace.get("Out")
    history = []
    if type(outputs) is dict:
        history = [f"Out[{i}]" for i, obj in outputs.items()
                   if type(i) is int and obj is value]
    lines = ["Notebook names: " + ", ".join(names)]
    if history:
        lines.append("Cached outputs: " + ", ".join(history))
    return "\n".join(lines)


def _key_label(key, position):
    if type(key) is str and len(key) <= 60:
        return repr(key)
    if type(key) is int and key.bit_length() <= 64:
        return str(key)
    # Arbitrary keys may have expensive or side-effecting repr methods.
    return f"entry #{position + 1}"


def _parts(name, value, top):
    if type(value) not in (dict, list, tuple):
        raise UsageError("%memparts supports built-in dict, list and tuple variables.")
    if not value:
        return f"{name} is empty."

    if type(value) is dict:
        entries = ((_key_label(key, i), child)
                   for i, (key, child) in enumerate(value.items()))
    else:
        entries = ((str(i), child) for i, child in enumerate(value))

    rows = [(size_of(child), label) for label, child in islice(entries, _SCAN_LIMIT)]
    rows.sort(key=lambda row: row[0], reverse=True)
    lines = [f"Inspected {len(rows)} of {len(value)} entries in {name} (estimated sizes):"]
    for size, label in rows[:top]:
        lines.append(f"  {name}[{label}]  {format_bytes(size)}")
    return "\n".join(lines)


def load_ipython_extension(ip):
    def variable(name):
        if not name.isidentifier() or name not in ip.user_ns:
            raise UsageError("Give the name of an existing notebook variable.")
        return ip.user_ns[name]

    def memaliases(line):
        name = line.strip()
        print(_aliases(ip, name, variable(name)))

    def memparts(line):
        args = line.split()
        if len(args) not in (1, 2):
            raise UsageError("Use %memparts NAME [N].")
        top = 5
        if len(args) == 2:
            try:
                top = int(args[1])
            except ValueError:
                raise UsageError("N must be a positive integer.") from None
            if top <= 0:
                raise UsageError("N must be a positive integer.")
        print(_parts(args[0], variable(args[0]), top))

    commands = {"memaliases": memaliases, "memparts": memparts}
    for name in commands:
        if ip.find_line_magic(name) is not None:
            raise UsageError(f"A %{name} magic is already registered.")
    for name, function in commands.items():
        ip.register_magic_function(function, magic_kind="line", magic_name=name)
    ip._loop_autopsy_memory_inspect = commands


def unload_ipython_extension(ip):
    commands = getattr(ip, "_loop_autopsy_memory_inspect", None)
    if commands is None:
        return
    for name, function in commands.items():
        if ip.find_line_magic(name) is function:
            del ip.magics_manager.magics["line"][name]
    del ip._loop_autopsy_memory_inspect
