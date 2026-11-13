import pytest
from IPython import get_ipython
from IPython.testing.globalipapp import start_ipython

from loop_autopsy.config import LoopInfo, LoopState


@pytest.fixture
def shell():
    """A real IPython shell with loop-autopsy loaded; unloaded afterwards."""
    ip = start_ipython() or get_ipython()  # start_ipython only returns the shell the first time
    ip.run_cell("%load_ext loop_autopsy")
    yield ip
    ip.run_cell("%unload_ext loop_autopsy")


def make_state(**info_fields) -> LoopState:
    defaults = dict(loop_id=0, kind="while", lineno=1, header="while x < 5:", test_src="x < 5",
                    cond_names=("x",), watched=("x",), compare=(("name", "x"), "<", ("const", 5)))
    defaults.update(info_fields)
    return LoopState(LoopInfo(**defaults), started=0.0)


def step(state, check, values):
    """Simulate one checked round and run ``check``."""
    state.rounds += 1
    state.checks += 1
    return check(state.info, state, values)
