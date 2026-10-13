"""Small deterministic shortest-path helpers (no external graph library)."""

from __future__ import annotations

import heapq
import math

from .model import Topology

INF = math.inf

Adj = dict[str, list[tuple[str, int]]]


def adjacency(topo: Topology, failed: frozenset[str], area: int | None = None,
              exclude_area: int | None = None, nodes: set[str] | None = None) -> Adj:
    """Adjacency over alive links, optionally restricted to (or excluding) one area."""
    adj: Adj = {r: [] for r in (nodes if nodes is not None else topo.routers)}
    for l in topo.links:
        if l.id in failed:
            continue
        if area is not None and l.area != area:
            continue
        if exclude_area is not None and l.area == exclude_area:
            continue
        adj.setdefault(l.a, []).append((l.b, l.cost))
        adj.setdefault(l.b, []).append((l.a, l.cost))
    for r in adj:
        adj[r].sort()
    return adj


def dijkstra(adj: Adj, src: str) -> tuple[dict[str, float], dict[str, str | None]]:
    """Distances and first hops from `src`.  Ties resolve to the lexicographically
    smallest first hop so results are reproducible."""
    dist: dict[str, float] = {src: 0}
    first: dict[str, str | None] = {src: None}
    heap = [(0, src, "")]
    done = set()
    while heap:
        d, u, fh = heapq.heappop(heap)
        if u in done:
            continue
        done.add(u)
        for v, c in adj.get(u, ()):
            nd = d + c
            vfh = v if u == src else first[u]
            if nd < dist.get(v, INF) or (nd == dist.get(v) and v not in done and vfh < (first[v] or "")):
                dist[v] = nd
                first[v] = vfh
                heapq.heappush(heap, (nd, v, vfh))
    return dist, first


class SPFCache:
    """Memoises Dijkstra runs per (adjacency key, source)."""

    def __init__(self) -> None:
        self._adj: dict[tuple, Adj] = {}
        self._sp: dict[tuple, tuple[dict, dict]] = {}

    def adj(self, key: tuple, build) -> Adj:
        if key not in self._adj:
            self._adj[key] = build()
        return self._adj[key]

    def spf(self, key: tuple, adj: Adj, src: str):
        k = (key, src)
        if k not in self._sp:
            self._sp[k] = dijkstra(adj, src)
        return self._sp[k]
