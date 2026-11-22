"""Memory readings on the real operating system, without fakes.

tests/test_growth.py checks each way of reading memory with made-up values on any
computer. These tests read the real values, each on its own system, so CI checks
the Linux, macOS and Windows code paths on the machines they were written for.
"""
import sys

import pytest

from loop_autopsy import growth

MB = 1024 ** 2


@pytest.fixture
def fresh_limit(monkeypatch):
    monkeypatch.delenv("MEM_LIMIT", raising=False)
    growth._limit_cache[:] = []
    yield
    growth._limit_cache[:] = []


def no_psutil(monkeypatch):
    monkeypatch.setitem(sys.modules, "psutil", None)  # makes ``import psutil`` fail


def peak_rss():
    """The most memory this process has ever used, in bytes (ru_maxrss is in KB on
    Linux and in bytes on macOS)."""
    import resource
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return peak if sys.platform == "darwin" else peak * 1024


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux memory files")
def test_linux_memory_without_psutil(monkeypatch, fresh_limit):
    """/proc/self/statm gives the memory in use now; the limit comes from the cgroup
    (in a container) or from the total RAM."""
    no_psutil(monkeypatch)
    rss, limit = growth.current_rss(), growth.memory_limit(refresh=True)
    assert rss and limit
    assert 1 * MB < rss <= peak_rss() * 1.1 + MB  # never more than the peak (small slack for timing)
    assert rss < limit
    blob = b"x" * (200 * MB)  # memory in use goes up when a big value is filled in
    assert growth.current_rss() > rss + 150 * MB
    del blob


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS memory functions")
def test_macos_memory_without_psutil(monkeypatch, fresh_limit):
    """macOS has no /proc and only reports the peak memory, which never goes down, so
    without psutil the memory in use is "unknown" (the memory features are off) rather
    than wrong. The total RAM is still known."""
    no_psutil(monkeypatch)
    assert growth.current_rss() is None
    limit = growth.memory_limit(refresh=True)
    assert limit and limit > 512 * MB


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS memory functions")
def test_macos_memory_with_psutil(fresh_limit):
    """With psutil (pip install "loop-autopsy[memory]") macOS gets real readings."""
    pytest.importorskip("psutil")
    rss, limit = growth.current_rss(), growth.memory_limit(refresh=True)
    assert rss and limit and 1 * MB < rss < limit
    blob = b"x" * (200 * MB)  # filled in: an all-zero buffer may not take real memory yet
    assert growth.current_rss() > rss + 150 * MB
    del blob


def test_memory_fraction_is_a_real_share_where_memory_is_known(fresh_limit):
    """On every system: either "unknown" or a share between 0 and 1."""
    fraction = growth.memory_fraction()
    assert fraction is None or 0 < fraction < 1
