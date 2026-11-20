"""Locate notebook lines whose live traced allocations grew after a checkpoint."""

from linecache import getline
import tracemalloc

from IPython.core.error import UsageError

from ..growth import format_bytes


def _snapshot():
    # Match filenames produced by terminal IPython and by ipykernel.
    return tracemalloc.take_snapshot().filter_traces((
        tracemalloc.Filter(True, "<ipython-input-*>"),
        tracemalloc.Filter(True, "*/ipykernel_*/*.py"),
    ))


def load_ipython_extension(ip):
    if ip.find_line_magic("growthtrace") is not None:
        raise UsageError("A %growthtrace magic is already registered.")

    baseline = None
    owns_trace = False

    def stop():
        nonlocal baseline, owns_trace
        baseline = None
        if owns_trace:
            tracemalloc.stop()
        owns_trace = False

    def growthtrace(line):
        nonlocal baseline, owns_trace
        args = line.split()
        if args == ["mark"]:
            if not tracemalloc.is_tracing():
                tracemalloc.start()
                owns_trace = True
            try:
                baseline = _snapshot()
            except BaseException:
                stop()
                raise
            print("Allocation checkpoint saved. Run your loop, then %growthtrace show.")
        elif args == ["stop"]:
            stop()
            print("Allocation checkpoint cleared.")
        elif not args or (args[0] == "show" and len(args) <= 2):
            count = 5
            if len(args) == 2:
                try:
                    count = int(args[1])
                except ValueError:
                    raise UsageError("N must be a positive integer.") from None
                if count < 1:
                    raise UsageError("N must be a positive integer.")
            if baseline is None or not tracemalloc.is_tracing():
                raise UsageError("Run %growthtrace mark first.")
            gains = [d for d in _snapshot().compare_to(baseline, "lineno") if d.size_diff > 0]
            if not gains:
                print("No net increase attributed to notebook source lines.")
            for delta in gains[:count]:
                frame = delta.traceback[0]
                print(f"+{format_bytes(delta.size_diff)}  {frame.filename}:{frame.lineno}")
                source = getline(frame.filename, frame.lineno).strip()
                if source:
                    print(f"    {source}")
        else:
            raise UsageError("Use %growthtrace mark, show [N], or stop.")

    ip.register_magic_function(growthtrace, magic_kind="line", magic_name="growthtrace")
    ip._loop_autopsy_growthtrace = growthtrace, stop


def unload_ipython_extension(ip):
    registration = getattr(ip, "_loop_autopsy_growthtrace", None)
    if registration is not None:
        magic, stop = registration
        stop()
        if ip.find_line_magic("growthtrace") is magic:
            del ip.magics_manager.magics["line"]["growthtrace"]
        del ip._loop_autopsy_growthtrace
