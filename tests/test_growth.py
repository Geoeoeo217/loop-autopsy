import builtins
import io
import os
import sys
import types

import pytest
from conftest import make_state

from loop_autopsy import growth
from loop_autopsy.config import Settings

GB = 1024 ** 3


def test_format_bytes():
    assert growth.format_bytes(512) == "512 B"
    assert growth.format_bytes(2 * 1024 ** 3) == "2.0 GB"
    assert growth.format_bytes(None) == "?"


def test_memory_limit_prefers_jupyterhub_env(monkeypatch):
    monkeypatch.setenv("MEM_LIMIT", str(2 * 1024 ** 3))
    assert growth.memory_limit(refresh=True) == 2 * 1024 ** 3
    monkeypatch.delenv("MEM_LIMIT")
    assert growth.memory_limit(refresh=True) is None or growth.memory_limit() > 0


def test_current_rss_is_positive():
    rss = growth.current_rss()
    assert rss is None or rss > 0


def test_size_of_and_largest_variables():
    small, big = [1], list(range(100_000))
    assert growth.size_of(big) > growth.size_of(small)
    ns = {"small": small, "big": big, "_hidden": list(range(10 ** 6)), "f": print}
    names = [n for n, _ in growth.largest_variables(ns, top=2)]
    assert names == ["big", "small"]


def test_growing_iterable():
    settings = Settings(growth_after=100)
    state = make_state(kind="for", iter_name="nums", watched=("nums",), compare=None)
    nums = [1, 2, 3]
    growth.remember_start(state, {"nums": nums})
    finding = None
    for _ in range(150):
        nums.append(0)
        state.rounds += 1
        finding = finding or growth.check_growing_iterable(state.info, state, {"nums": nums}, settings)
    assert finding.kind == "growing_iterable" and "started with 3 items" in finding.explanation


def test_normal_for_loop_is_fine():
    settings = Settings(growth_after=10)
    state = make_state(kind="for", iter_name="nums", watched=("nums",), compare=None)
    nums = list(range(50))
    growth.remember_start(state, {"nums": nums})
    state.rounds = 50
    assert growth.check_growing_iterable(state.info, state, {"nums": nums}, settings) is None


def test_memory_rule_names_the_growing_list():
    settings = Settings(memory_stop=0.85)
    state = make_state(watched=("results", "x"))
    results = []
    growth.remember_start(state, {"results": results, "x": 1})
    results.extend(range(5000))
    assert growth.check_memory(state.info, state, {"results": results}, settings, fraction=0.5) is None
    finding = growth.check_memory(state.info, state, {"results": results}, settings, fraction=0.9)
    assert finding.kind == "memory" and "`results` grew from 0 to 5,000" in finding.explanation


# --------------------------------------------------------------------------- edge cases
@pytest.fixture
def fresh_limit(monkeypatch):
    """Forget the cached memory limit before and after, so tests cannot leak into each other."""
    monkeypatch.delenv("MEM_LIMIT", raising=False)
    growth._limit_cache[:] = []
    yield
    growth._limit_cache[:] = []


def no_psutil(monkeypatch):
    monkeypatch.setitem(sys.modules, "psutil", None)  # makes ``import psutil`` fail


def fake_proc_statm(monkeypatch, content):
    """Make ``/proc/self/statm`` readable with ``content`` (or missing if None), on any system."""
    real_open = builtins.open

    def fake_open(path, *args, **kwargs):
        if path == "/proc/self/statm":
            if content is None:
                raise FileNotFoundError(path)
            return io.StringIO(content)
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", fake_open)


def test_format_bytes_every_unit():
    assert growth.format_bytes(0) == "0 B"
    assert growth.format_bytes(1023) == "1023 B"
    assert growth.format_bytes(1024) == "1 KB"
    assert growth.format_bytes(1536 * 1024) == "1.5 MB"
    assert growth.format_bytes(5 * 1024 ** 4) == "5.0 TB"
    assert growth.format_bytes(3000 * 1024 ** 4) == "3000.0 TB"  # TB is the largest unit
    assert growth.format_bytes(-2048) == "-2 KB"


def test_read_int(tmp_path):
    good, unlimited, empty, words = (tmp_path / n for n in ("good", "max", "empty", "words"))
    good.write_text("4096\n")
    unlimited.write_text("max\n")
    empty.write_text("")
    words.write_text("lots")
    assert growth._read_int(str(good)) == 4096
    assert growth._read_int(str(unlimited)) is None  # cgroup v2 writes "max" for no limit
    assert growth._read_int(str(empty)) is None
    assert growth._read_int(str(words)) is None
    assert growth._read_int(str(tmp_path / "missing")) is None


def test_memory_limit_is_cached_until_refreshed(monkeypatch, fresh_limit):
    monkeypatch.setenv("MEM_LIMIT", str(GB))
    assert growth.memory_limit() == GB
    monkeypatch.setenv("MEM_LIMIT", str(2 * GB))
    assert growth.memory_limit() == GB  # still the cached answer
    assert growth.memory_limit(refresh=True) == 2 * GB


def test_memory_limit_accepts_float_text_and_ignores_nonsense(monkeypatch, fresh_limit):
    monkeypatch.setattr(growth, "_read_int", lambda path: 3 * GB)
    monkeypatch.setenv("MEM_LIMIT", "1.5e9")
    assert growth.memory_limit(refresh=True) == 1_500_000_000
    monkeypatch.setenv("MEM_LIMIT", "two gigabytes")
    assert growth.memory_limit(refresh=True) == 3 * GB  # falls through to the container limit


def test_memory_limit_reads_the_container_limit(monkeypatch, fresh_limit):
    v2, v1 = "/sys/fs/cgroup/memory.max", "/sys/fs/cgroup/memory/memory.limit_in_bytes"
    monkeypatch.setattr(growth, "_read_int", {v2: 2 * GB, v1: 4 * GB}.get)
    assert growth.memory_limit(refresh=True) == 2 * GB  # cgroup v2 is asked first
    monkeypatch.setattr(growth, "_read_int", {v2: None, v1: 4 * GB}.get)
    assert growth.memory_limit(refresh=True) == 4 * GB


def test_memory_limit_ignores_the_no_limit_number(monkeypatch, fresh_limit):
    monkeypatch.setattr(growth, "_read_int", lambda path: growth._UNLIMITED)  # cgroup v1's "no limit"
    monkeypatch.setitem(sys.modules, "psutil", types.SimpleNamespace(
        virtual_memory=lambda: types.SimpleNamespace(total=16 * GB)))
    assert growth.memory_limit(refresh=True) == 16 * GB


def test_memory_limit_without_psutil(monkeypatch, fresh_limit):
    monkeypatch.setattr(growth, "_read_int", lambda path: None)
    no_psutil(monkeypatch)
    monkeypatch.setattr(os, "sysconf", {"SC_PAGE_SIZE": 4096, "SC_PHYS_PAGES": 1000}.get, raising=False)
    assert growth.memory_limit(refresh=True) == 4096 * 1000

    def unsupported(name):
        raise ValueError(name)

    monkeypatch.setattr(os, "sysconf", unsupported, raising=False)
    assert growth.memory_limit(refresh=True) is None  # nothing works: say "unknown", do not crash


def test_current_rss_reads_proc_on_linux(monkeypatch):
    fake_proc_statm(monkeypatch, "5000 1234 300 1 0 800 0\n")
    monkeypatch.setattr(os, "sysconf", lambda name: 4096, raising=False)
    assert growth.current_rss() == 1234 * 4096  # second number = resident pages


def test_current_rss_falls_back_to_psutil(monkeypatch):
    fake_proc_statm(monkeypatch, None)
    process = types.SimpleNamespace(memory_info=lambda: types.SimpleNamespace(rss=77_000_000))
    monkeypatch.setitem(sys.modules, "psutil", types.SimpleNamespace(Process=lambda: process))
    assert growth.current_rss() == 77_000_000


@pytest.mark.parametrize("platform, expected", [("darwin", 5000), ("linux", 5000 * 1024)])
def test_current_rss_falls_back_to_resource(monkeypatch, platform, expected):
    fake_proc_statm(monkeypatch, "garbage")  # unreadable numbers are skipped too
    no_psutil(monkeypatch)
    usage = types.SimpleNamespace(ru_maxrss=5000)  # bytes on macOS, kilobytes on Linux
    monkeypatch.setitem(sys.modules, "resource", types.SimpleNamespace(
        RUSAGE_SELF=0, getrusage=lambda who: usage))
    monkeypatch.setattr(sys, "platform", platform)
    assert growth.current_rss() == expected


def test_memory_fraction(monkeypatch):
    monkeypatch.setattr(growth, "current_rss", lambda: GB)
    monkeypatch.setattr(growth, "memory_limit", lambda refresh=False: 4 * GB)
    assert growth.memory_fraction() == 0.25
    monkeypatch.setattr(growth, "memory_limit", lambda refresh=False: None)
    assert growth.memory_fraction() is None
    monkeypatch.setattr(growth, "current_rss", lambda: None)
    assert growth.memory_fraction() is None


def test_size_of_pandas_and_numpy_like_objects():
    class Frame:  # like a DataFrame: memory_usage() returns one number per column
        def memory_usage(self, deep=False):
            assert deep, "deep=True is needed to count the strings inside"
            return types.SimpleNamespace(sum=lambda: 4096)

    class Series:  # like a Series: memory_usage() returns a plain number
        def memory_usage(self, deep=False):
            return 512

    class Array:  # like a NumPy array
        nbytes = 8000

    assert growth.size_of(Frame()) == 4096
    assert growth.size_of(Series()) == 512
    assert growth.size_of(Array()) == 8000


def test_size_of_containers_counts_their_items():
    numbers = list(range(1000, 3000))
    assert growth.size_of(numbers) > sys.getsizeof(numbers)
    assert growth.size_of(set(numbers)) > sys.getsizeof(set(numbers))
    assert growth.size_of(tuple(numbers)) > sys.getsizeof(tuple(numbers))
    table = {n: str(n) for n in numbers}
    assert growth.size_of(table) > sys.getsizeof(table)
    assert growth.size_of([]) == sys.getsizeof([]) and growth.size_of({}) == sys.getsizeof({})
    assert growth.size_of(42) == sys.getsizeof(42)
    # A huge list is sampled (first 1000 items), so the size is an estimate, not a full walk.
    big = [0] * 1_000_000
    assert growth.size_of(big) == sys.getsizeof(big) + sys.getsizeof(0) * 1_000_000


def test_size_of_never_raises():
    class Broken:
        def memory_usage(self, deep=False):
            raise RuntimeError("no size for you")

    assert growth.size_of(Broken()) == 0


def test_largest_variables_skips_ipython_names_and_respects_top():
    ns = {"In": ["x"] * 9999, "Out": {}, "exit": "e", "quit": "q", "get_ipython": 1,
          "_": [0] * 9999, "__name__": "m", "os": os, "f": len, "Cls": int,
          "a": [0] * 300, "b": [0] * 200, "c": [0] * 100, "d": 1}
    assert [n for n, _ in growth.largest_variables(ns)] == ["a", "b", "c"]  # top defaults to 3
    assert [n for n, _ in growth.largest_variables(ns, top=10)] == ["a", "b", "c", "d"]
    assert growth.largest_variables({}) == []


def test_memory_overview_without_measurements(monkeypatch):
    monkeypatch.setattr(growth, "memory_limit", lambda refresh=False: None)
    monkeypatch.setattr(growth, "current_rss", lambda: 512 * 1024 ** 2)
    assert growth.memory_overview({}) == "Memory: 512.0 MB (limit unknown)\n\nNo variables to show yet."
    monkeypatch.setattr(growth, "current_rss", lambda: None)
    text = growth.memory_overview({"n": 5, "items": [1, 2, 3]})
    assert text.splitlines()[0] == "Memory: cannot be measured on this system"
    assert "list (len 3)" in text
    assert any(line.split()[0] == "n" and line.split()[-1] == "int" for line in text.splitlines()[2:])


def test_lengths_only_counts_things_with_a_length():
    class Lazy:
        def __len__(self):
            raise RuntimeError("not ready")

    lengths = growth._lengths({"a": [1, 2], "s": "abc", "d": {"k": 1}, "n": 5, "lazy": Lazy(),
                               "r": range(10)})
    assert lengths == {"a": 2, "s": 3, "d": 1, "r": 10}


def test_growing_iterable_needs_a_for_loop_over_a_named_list():
    settings = Settings(growth_after=10)
    nums = list(range(100))

    def check(state, values, rounds=50, remember=True):
        if remember:
            growth.remember_start(state, {"nums": [1, 2, 3]})
        state.rounds = rounds
        return growth.check_growing_iterable(state.info, state, values, settings)

    grows = make_state(kind="for", iter_name="nums", watched=("nums",), compare=None)
    assert check(grows, {"nums": nums}).kind == "growing_iterable"
    assert check(make_state(iter_name="nums"), {"nums": nums}) is None  # a while loop
    assert check(make_state(kind="for", iter_name=None), {"nums": nums}) is None  # for x in range(...)
    assert check(make_state(kind="for", iter_name="nums"), {}) is None  # the name is not visible
    assert check(make_state(kind="for", iter_name="nums"), {"nums": iter(nums)}) is None  # no len()
    assert check(make_state(kind="for", iter_name="nums"), {"nums": nums}, remember=False) is None
    assert check(make_state(kind="for", iter_name="nums"), {"nums": nums}, rounds=5) is None  # too early


def test_growing_iterable_ignores_slow_growth():
    settings = Settings(growth_after=10)
    state = make_state(kind="for", iter_name="nums", watched=("nums",), compare=None)
    growth.remember_start(state, {"nums": [0] * 1000})
    state.rounds = 500
    # 100 new items in 500 rounds: the loop reads faster than the list grows, so it will end.
    assert growth.check_growing_iterable(state.info, state, {"nums": [0] * 1100}, settings) is None
    # 450 new items in 500 rounds is right at the limit (90%).
    assert growth.check_growing_iterable(state.info, state, {"nums": [0] * 1450}, settings) is not None


def test_memory_rule_without_a_growing_container(monkeypatch):
    monkeypatch.setattr(growth, "memory_limit", lambda refresh=False: 2 * GB)
    settings = Settings(memory_stop=0.85)
    state = make_state(watched=("x", "kept"))
    growth.remember_start(state, {"x": 1, "kept": [1, 2, 3]})
    finding = growth.check_memory(state.info, state, {"x": 2, "kept": [1, 2, 3]}, settings, fraction=0.92)
    assert finding.kind == "memory" and finding.culprits == ()
    assert "92% of your 2.0 GB limit" in finding.explanation and "grew from" not in finding.explanation


def test_memory_rule_names_the_fastest_growing_container(monkeypatch):
    monkeypatch.setattr(growth, "memory_limit", lambda refresh=False: 2 * GB)
    state = make_state(watched=("small", "big", "late"))
    growth.remember_start(state, {"small": [0] * 10, "big": [0] * 10})
    values = {"small": [0] * 60, "big": [0] * 9000, "late": [0] * 500}  # ``late`` appeared mid-loop
    finding = growth.check_memory(state.info, state, values, Settings(), fraction=0.99)
    assert finding.culprits == ("big",) and "`big` grew from 10 to 9,000 items" in finding.explanation


def test_memory_rule_measures_memory_itself_when_not_told(monkeypatch):
    state = make_state()
    monkeypatch.setattr(growth, "memory_fraction", lambda: None)  # cannot be measured
    assert growth.check_memory(state.info, state, {}, Settings()) is None
    monkeypatch.setattr(growth, "memory_fraction", lambda: 0.5)
    assert growth.check_memory(state.info, state, {}, Settings()) is None
    monkeypatch.setattr(growth, "memory_fraction", lambda: 0.9)
    assert growth.check_memory(state.info, state, {}, Settings()).kind == "memory"
