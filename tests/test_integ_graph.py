"""Tests for pyverilog_auto.integ.graph."""

from __future__ import annotations

from pyverilog_auto.integ.graph import compute_order, tarjan_scc


def test_chain_is_leaf_first():
    g = {"top": ["wrap"], "wrap": ["leaf"], "leaf": []}
    o = compute_order(g, ["top", "wrap", "leaf"])
    assert o.files == ["leaf", "wrap", "top"]
    assert o.levels == [["leaf"], ["wrap"], ["top"]]
    assert o.cycles == []
    assert o.level_of == {"leaf": 0, "wrap": 1, "top": 2}


def test_diamond_and_tie_break_by_filelist_order():
    g = {"top": ["a", "b"], "a": ["leaf"], "b": ["leaf"], "leaf": []}
    o = compute_order(g, ["top", "b", "a", "leaf"])
    assert o.levels == [["leaf"], ["b", "a"], ["top"]]
    assert o.files == ["leaf", "b", "a", "top"]


def test_cycle_is_reported_and_levelled_together():
    g = {"top": ["x"], "x": ["y"], "y": ["x", "leaf"], "leaf": []}
    o = compute_order(g, ["top", "x", "y", "leaf"])
    assert o.cycles == [["x", "y"]]
    assert o.level_of["x"] == o.level_of["y"] == 1
    assert o.level_of["top"] == 2
    assert o.files == ["leaf", "x", "y", "top"]


def test_library_leaves_and_isolated_files():
    g = {"top": ["lib"], "lib": [], "alone": []}
    o = compute_order(g, ["alone", "top", "lib"])
    assert o.levels[0] == ["alone", "lib"]
    assert o.levels[1] == ["top"]


def test_self_reference_is_ignored():
    g = {"a": ["a"], "b": ["a"]}
    o = compute_order(g, ["a", "b"])
    assert o.files == ["a", "b"]
    assert o.cycles == []


def test_tarjan_dependencies_first():
    g = {"top": ["mid"], "mid": ["leaf"], "leaf": [], "p": ["q"], "q": ["p"]}
    sccs = tarjan_scc(g, ["top", "mid", "leaf", "p", "q"])
    pos = {n: i for i, comp in enumerate(sccs) for n in comp}
    assert pos["leaf"] < pos["mid"] < pos["top"]
    assert pos["p"] == pos["q"]


def test_order_stable_under_reordering():
    g = {"top": ["a", "b"], "a": [], "b": []}
    o1 = compute_order(g, ["top", "a", "b"])
    o2 = compute_order(g, ["b", "top", "a"])
    assert o1.levels[0] == ["a", "b"]
    assert o2.levels[0] == ["b", "a"]
    assert o1.levels[1] == o2.levels[1] == ["top"]
