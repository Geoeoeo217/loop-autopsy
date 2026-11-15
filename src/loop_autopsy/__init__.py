"""loop-autopsy: stops runaway loops in Jupyter and shows the evidence for why
they never ended.

Usage in a notebook::

    %load_ext loop_autopsy
"""

from __future__ import annotations

__version__ = "0.2.0"

from .config import Finding, LoopStopped, Settings  # noqa: F401

_LOADED: dict = {}


def load_ipython_extension(ip) -> None:
    from .heatmap import CellHealth
    from .magics import AutopsyMagics
    from .monitor import Monitor
    from .report import display_report
    from .transformer import RUNTIME_NAME, LoopTransformer

    if id(ip) in _LOADED:
        print("loop-autopsy is already loaded. Use %autopsy help for options.")
        return
    settings = Settings()
    monitor = Monitor(ip, settings)
    transformer = LoopTransformer(monitor.register, lambda: settings.max_watched, monitor.is_paused)
    health = CellHealth(ip, settings, monitor.stats, show=display_report)

    ip.user_ns[RUNTIME_NAME] = monitor
    ip.user_ns_hidden[RUNTIME_NAME] = monitor  # keep it out of %who / %whos
    ip.ast_transformers.append(transformer)
    ip.events.register("pre_run_cell", monitor.pre_run_cell)
    health.register()
    monitor.install_exception_handler()
    ip.register_magics(AutopsyMagics(ip, monitor, health))
    _LOADED[id(ip)] = (monitor, transformer, health)
    print(f"loop-autopsy {__version__} is watching your loops. Type %autopsy help for options.")


def unload_ipython_extension(ip) -> None:
    entry = _LOADED.pop(id(ip), None)
    if entry is None:
        return
    monitor, transformer, health = entry
    monitor.enabled = False  # functions instrumented earlier keep working, unwatched
    if transformer in ip.ast_transformers:
        ip.ast_transformers.remove(transformer)
    try:
        ip.events.unregister("pre_run_cell", monitor.pre_run_cell)
    except ValueError:
        pass
    health.unregister()
    monitor.remove_exception_handler()
