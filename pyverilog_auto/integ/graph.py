"""File dependency graph, strongly connected components and leaf-first order.

Pure functions over plain dicts so they are trivially unit-testable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Mapping


@dataclass
class Order:
    """Leaf-first processing order of files."""

    files: list[str] = field(default_factory=list)          # leaf-first, stable
    levels: list[list[str]] = field(default_factory=list)   # levels[0] = leaves
    cycles: list[list[str]] = field(default_factory=list)   # SCCs with more than one file
    level_of: dict[str, int] = field(default_factory=dict)

    def level(self, key: str) -> int:
        return self.level_of.get(key, 0)


def tarjan_scc(graph: Mapping[str, Iterable[str]], nodes: Iterable[str]) -> list[list[str]]:
    """Return SCCs of *graph* (node -> successors) in reverse topological
    order: every SCC appears after all SCCs it depends on.

    Iterative implementation (no recursion limit issues on deep designs).
    Members inside one SCC keep the order in which they were first visited.
    """
    index: dict[str, int] = {}
    lowlink: dict[str, int] = {}
    on_stack: set[str] = set()
    stack: list[str] = []
    result: list[list[str]] = []
    counter = 0

    for root in nodes:
        if root in index:
            continue
        work: list[tuple[str, "list[str]"]] = [(root, list(graph.get(root, ())))]
        index[root] = lowlink[root] = counter
        counter += 1
        stack.append(root)
        on_stack.add(root)
        while work:
            node, succs = work[-1]
            if succs:
                nxt = succs.pop(0)
                if nxt not in index:
                    index[nxt] = lowlink[nxt] = counter
                    counter += 1
                    stack.append(nxt)
                    on_stack.add(nxt)
                    work.append((nxt, list(graph.get(nxt, ()))))
                elif nxt in on_stack:
                    lowlink[node] = min(lowlink[node], index[nxt])
                continue
            work.pop()
            if work:
                parent = work[-1][0]
                lowlink[parent] = min(lowlink[parent], lowlink[node])
            if lowlink[node] == index[node]:
                comp: list[str] = []
                while True:
                    w = stack.pop()
                    on_stack.discard(w)
                    comp.append(w)
                    if w == node:
                        break
                comp.reverse()
                result.append(comp)
    return result


def compute_order(graph: Mapping[str, Iterable[str]], file_order: list[str]) -> Order:
    """Compute the leaf-first :class:`Order` for *graph* (file -> files it
    depends on).  *file_order* gives the tie-break order (filelist order)
    and must contain every node.
    """
    rank = {f: i for i, f in enumerate(file_order)}
    sccs = tarjan_scc(graph, file_order)
    scc_of: dict[str, int] = {}
    for i, comp in enumerate(sccs):
        for f in comp:
            scc_of[f] = i

    # Level of an SCC = 1 + max level of the SCCs it depends on (0 for leaves).
    scc_level: list[int] = [0] * len(sccs)
    for i, comp in enumerate(sccs):   # dependencies come first, so a single pass works
        lvl = 0
        for f in comp:
            for dep in graph.get(f, ()):
                j = scc_of.get(dep)
                if j is None or j == i:
                    continue
                lvl = max(lvl, scc_level[j] + 1)
        scc_level[i] = lvl

    level_of: dict[str, int] = {}
    for i, comp in enumerate(sccs):
        for f in comp:
            level_of[f] = scc_level[i]

    max_level = max(scc_level) if scc_level else -1
    levels: list[list[str]] = [[] for _ in range(max_level + 1)]
    for f in sorted(file_order, key=lambda f: (level_of[f], rank[f])):
        levels[level_of[f]].append(f)

    order = Order(
        files=[f for lvl in levels for f in lvl],
        levels=levels,
        cycles=[sorted(comp, key=lambda f: rank[f]) for comp in sccs if len(comp) > 1],
        level_of=level_of,
    )
    return order
