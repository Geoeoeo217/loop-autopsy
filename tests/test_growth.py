"""Tests for growth.py (owner: Teammate 3)."""
from conftest import make_state

from loop_autopsy import growth
from loop_autopsy.config import Settings


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
