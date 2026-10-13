"""Fast analytical check of one candidate summary.

For a building (area) X with border routers B1..Bn and a candidate group G of
X's subnets, each scenario f gives:

  d_f(B, s)  intra-building distance from border B to subnet s (inf if cut off)
  D_f(R, B)  distance from a remote router R to border B outside the building

Summarizing G makes every border B with at least one reachable member
advertise one route whose cost is max_s d_f(B, s) (OSPF) or min_s d_f(B, s)
(EIGRP).  Remote router R then sends *all* of G to the border minimising
D_f(R, B) + cost(B).  If that border cannot reach some member, packets for
that member are dropped at its discard route: a blackhole.  Otherwise the
extra cost against per-subnet routing is the path stretch.

The full simulator (routing.py) verifies the final plan; this proxy only
needs to be cheap enough to score every node of the address trie.
"""

from __future__ import annotations

from dataclasses import dataclass

from .graph import INF, adjacency, dijkstra
from .model import Scenario, Topology


@dataclass
class Assessment:
    bh_normal: int = 0
    bh_failure: int = 0
    normal_max_rel: float = 1.0
    fail_max_rel: float = 1.0
    normal_error: float = 0.0
    fail_error: float = 0.0
    bh_reason: str = ""
    normal_reason: str = ""
    fail_reason: str = ""

    def verdict(self, tau_normal: float, tau_failure: float, use_failures: bool = True) -> tuple[bool, str]:
        if self.bh_normal:
            return False, self.bh_reason
        if self.normal_max_rel > tau_normal + 1e-9:
            return False, self.normal_reason
        if use_failures:
            if self.bh_failure:
                return False, self.bh_reason
            if self.fail_max_rel > tau_failure + 1e-9:
                return False, self.fail_reason
        return True, ""


class DomainContext:
    def __init__(self, topo: Topology, area: int, scenarios: list[Scenario], protocol: str) -> None:
        self.topo = topo
        self.area = area
        self.protocol = protocol
        self.agg = max if protocol == "ospf" else min
        self.borders = topo.border_routers(area)
        self.subnets = [s.name for s in topo.subnets_in(area)]
        owner = {s.name: s.router for s in topo.subnets_in(area)}
        self.remotes = [r for r in sorted(topo.routers) if area not in topo.router_areas(r)]
        self.data = []
        for f in scenarios:
            in_area = {r for r in topo.routers if area in topo.router_areas(r, f.failed)}
            adj_in = adjacency(topo, f.failed, area=area, nodes=in_area)
            adj_out = adjacency(topo, f.failed, exclude_area=area)
            d, D = {}, {}
            for b in self.borders:
                if b in in_area:
                    dist, _ = dijkstra(adj_in, b)
                    d[b] = {s: dist[o] for s, o in owner.items() if o in dist}
                else:
                    d[b] = {}
                D[b], _ = dijkstra(adj_out, b)
            ref = {}
            for r in self.remotes:
                ref[r] = {s: min((D[b].get(r, INF) + d[b].get(s, INF) for b in self.borders), default=INF)
                          for s in self.subnets}
            self.data.append((f, d, D, ref))
        self._memo: dict[frozenset, Assessment] = {}

    # ---------------------------------------------------------------- signatures
    def signature(self, s: str) -> tuple:
        """Which borders can reach `s` in each scenario."""
        return tuple(frozenset(b for b in self.borders if s in d[b]) for _f, d, _D, _r in self.data)

    def cost_vector(self, s: str) -> tuple:
        return tuple(d[b].get(s, INF) for _f, d, _D, _r in self.data for b in self.borders)

    # ---------------------------------------------------------------- assessment
    def assess(self, members) -> Assessment:
        key = frozenset(members)
        if key in self._memo:
            return self._memo[key]
        a = Assessment()
        members = sorted(members)
        for f, d, D, ref in self.data:
            cost = {}
            for b in self.borders:
                vals = [d[b][s] for s in members if s in d[b]]
                if vals:
                    cost[b] = self.agg(vals)
            if not cost:
                continue
            for r in self.remotes:
                best = None
                for b, cb in cost.items():
                    dr = D[b].get(r, INF)
                    if dr < INF and (best is None or (dr + cb, b) < best):
                        best = (dr + cb, b)
                if best is None:
                    continue
                exit_b = best[1]
                for s in members:
                    rf = ref[r][s]
                    if rf == INF:
                        continue
                    ds = d[exit_b].get(s, INF)
                    if ds == INF:
                        if f.is_normal:
                            a.bh_normal += 1
                        else:
                            a.bh_failure += 1
                        if not a.bh_reason:
                            a.bh_reason = (f"{f.name}: {exit_b} still advertises the summary but cannot reach "
                                           f"{s}, so traffic from {r} is dropped")
                        continue
                    got = D[exit_b][r] + ds
                    rel = got / rf if rf > 0 else 1.0
                    if f.is_normal:
                        a.normal_error += got - rf
                        if rel > a.normal_max_rel:
                            a.normal_max_rel = rel
                            a.normal_reason = (f"normal: traffic from {r} to {s} exits via {exit_b}, "
                                               f"cost {got:g} vs {rf:g} (x{rel:.2f})")
                    else:
                        a.fail_error += got - rf
                        if rel > a.fail_max_rel:
                            a.fail_max_rel = rel
                            a.fail_reason = (f"{f.name}: traffic from {r} to {s} exits via {exit_b}, "
                                             f"cost {got:g} vs {rf:g} (x{rel:.2f})")
        self._memo[key] = a
        return a
