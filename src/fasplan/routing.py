"""Converged-state routing simulation.

Given a topology, a plan and a set of failed links, compute every router's
forwarding table as OSPF or EIGRP would after convergence, then forward
packets hop by hop with longest-prefix match.  This is how the planner checks
whether a summary is safe: a summarizing router keeps advertising the
summary (and keeps a discard route for it) while *any* component is reachable,
so traffic for an unreachable component is dropped there.

OSPF model (RFC 2328, single process, no virtual links)
  * intra-area routes per area from SPF over that area's alive links;
  * every ABR originates one type-3 LSA per area range that is *active*
    (some component reachable intra-area), with cost = max component cost
    (RFC 2328 12.4.3), plus one LSA per unsummarized subnet; active ranges get
    a discard route;
  * an ABR ignores type-3 LSAs equal to one of its own active ranges (16.2);
  * ABRs use only backbone summaries and re-advertise inter-area routes into
    their non-backbone areas, which is what repairs a partitioned area when no
    summarization hides the specifics.

EIGRP model (converged distance vector, additive metric)
  * a subnet propagates everywhere except out of interfaces where a summary
    containing it is configured;
  * a summarizer with at least one component installs a discard route and
    advertises the summary on its summary interfaces with metric = minimum
    component metric; it ignores the same summary learned from neighbors.
"""

from __future__ import annotations

import heapq
import math
from collections import defaultdict
from dataclasses import dataclass

from .graph import INF, adjacency, dijkstra
from .model import Topology
from .plan import Plan


@dataclass(frozen=True)
class Route:
    prefix: object
    kind: str  # connected | intra | inter | eigrp | discard
    cost: float
    nexthop: str | None
    via: str | None = None


@dataclass(frozen=True)
class Walk:
    status: str  # delivered | blackhole | unreachable | loop
    cost: float
    path: tuple[str, ...]
    drop_at: str | None = None


class RoutingState:
    def __init__(self, topo: Topology, plan: Plan, failed: frozenset[str],
                 fibs: dict[str, dict], info: dict) -> None:
        self.topo = topo
        self.plan = plan
        self.failed = failed
        self.fibs = fibs
        self.info = info
        self._tables = {
            r: sorted(((p.prefixlen, int(p.network_address), int(p.netmask), rt) for p, rt in fib.items()),
                      key=lambda t: -t[0])
            for r, fib in fibs.items()
        }
        self._walks: dict[tuple[str, str], Walk] = {}

    def lookup(self, r: str, addr: int) -> Route | None:
        for _, net, mask, rt in self._tables[r]:
            if addr & mask == net:
                return rt
        return None

    def route_count(self, r: str) -> int:
        return len(self.fibs[r])

    def walk(self, src: str, subnet: str) -> Walk:
        key = (src, subnet)
        if key in self._walks:
            return self._walks[key]
        s = self.topo.subnet(subnet)
        addr = int(self.plan.allocation.subnets[subnet].network_address) + 1
        path = [src]
        cost = 0.0
        cur = src
        seen = {src}
        result = None
        while result is None:
            if cur == s.router:
                result = Walk("delivered", cost, tuple(path))
                break
            rt = self.lookup(cur, addr)
            if rt is None:
                result = Walk("unreachable", cost, tuple(path), cur)
            elif rt.kind == "discard":
                result = Walk("blackhole", cost, tuple(path), cur)
            else:
                link = self.topo.link_between(cur, rt.nexthop) if rt.nexthop else None
                if link is None or link.id in self.failed:
                    result = Walk("unreachable", cost, tuple(path), cur)
                else:
                    cost += link.cost
                    cur = rt.nexthop
                    if cur in seen:
                        path.append(cur)
                        result = Walk("loop", cost, tuple(path), cur)
                    else:
                        seen.add(cur)
                        path.append(cur)
        self._walks[key] = result
        return result


def simulate(topo: Topology, plan: Plan, failed: frozenset[str] = frozenset(),
             protocol: str | None = None) -> RoutingState:
    protocol = (protocol or plan.protocol).lower()
    if protocol == "ospf":
        fibs, info = _ospf(topo, plan, failed)
    elif protocol == "eigrp":
        fibs, info = _eigrp(topo, plan, failed)
    else:
        raise ValueError(f"unknown protocol {protocol}")
    return RoutingState(topo, plan, failed, fibs, info)


# --------------------------------------------------------------------------- OSPF
def _ospf(topo: Topology, plan: Plan, failed: frozenset[str]):
    pfx = plan.allocation.subnets
    routers = sorted(topo.routers)
    areas_of = {r: topo.router_areas(r, failed) for r in routers}
    in_area: dict[int, set[str]] = defaultdict(set)
    for r, ars in areas_of.items():
        for a in ars:
            in_area[a].add(r)
    adjs: dict[int, dict] = {}
    spcache: dict[tuple[int, str], tuple] = {}

    def spf(a: int, r: str):
        if (a, r) not in spcache:
            if a not in adjs:
                adjs[a] = adjacency(topo, failed, area=a, nodes=in_area[a])
            spcache[(a, r)] = dijkstra(adjs[a], r)
        return spcache[(a, r)]

    by_area: dict[int, list] = defaultdict(list)
    for s in topo.subnets:
        by_area[s.area].append(s)

    fib: dict[str, dict] = {r: {} for r in routers}

    # 1. intra-area routes
    for r in routers:
        for a in sorted(areas_of[r]):
            dist, fh = spf(a, r)
            for s in by_area[a]:
                p = pfx[s.name]
                if s.router == r:
                    fib[r][p] = Route(p, "connected", 0, None)
                elif s.router in dist:
                    fib[r][p] = Route(p, "intra", dist[s.router], fh[s.router])

    # 2. ABR type-3 originations into the backbone
    abrs = [r for r in routers if 0 in areas_of[r] and len(areas_of[r]) > 1]
    t3: list[tuple[str, int, object, float]] = []
    active: dict[str, set] = defaultdict(set)
    for b in abrs:
        for x in sorted(areas_of[b] - {0}):
            for ad in plan.adverts.get(x, []):
                costs = [fib[b][pfx[m]].cost for m in ad.members if pfx[m] in fib[b]]
                if not costs:
                    continue
                if ad.kind == "summary":
                    t3.append((b, x, ad.prefix, max(costs)))
                    active[b].add(ad.prefix)
                else:
                    t3.append((b, x, ad.prefix, costs[0]))
    for b, ranges in active.items():
        for p in ranges:
            fib[b][p] = Route(p, "discard", 0, None)

    # 3. backbone routers (ABRs included) compute inter-area routes
    for r in routers:
        if 0 not in areas_of[r]:
            continue
        dist, fh = spf(0, r)
        best: dict = {}
        for b, _x, p, c in t3:
            if b == r or b not in dist or p in fib[r]:
                continue
            cand = (dist[b] + c, b)
            if p not in best or cand < best[p][0]:
                best[p] = (cand, fh[b])
        for p, ((cost, b), nh) in best.items():
            fib[r][p] = Route(p, "inter", cost, nh, b)

    # 4. ABRs advertise into their non-backbone areas
    into: dict[tuple[str, int], dict] = {}
    for b in abrs:
        for y in sorted(areas_of[b] - {0}):
            ads: dict = {}
            for o, x, p, c in t3:
                if o == b and x != y:
                    ads[p] = c
            for s in by_area[0]:
                rt = fib[b].get(pfx[s.name])
                if rt and rt.kind in ("connected", "intra"):
                    ads[pfx[s.name]] = rt.cost
            for p, rt in fib[b].items():
                if rt.kind == "inter" and p not in ads:
                    ads[p] = rt.cost
            into[(b, y)] = ads

    # 5. internal (non-backbone) routers
    for r in routers:
        if 0 in areas_of[r]:
            continue
        best = {}
        for y in sorted(areas_of[r]):
            dist, fh = spf(y, r)
            for b in abrs:
                if b == r or y not in areas_of[b] or b not in dist:
                    continue
                for p, c in into.get((b, y), {}).items():
                    if p in fib[r]:
                        continue
                    cand = (dist[b] + c, b)
                    if p not in best or cand < best[p][0]:
                        best[p] = (cand, fh[b])
        for p, ((cost, b), nh) in best.items():
            fib[r][p] = Route(p, "inter", cost, nh, b)

    info = {
        "abrs": abrs,
        "lsa_backbone": len(t3),
        "lsa_total": len(t3) + sum(len(v) for v in into.values()),
        "ctrl_prefixes": len(t3) + sum(len(v) for v in into.values()),
        "active_ranges": {b: sorted(map(str, v)) for b, v in active.items()},
    }
    return fib, info


# -------------------------------------------------------------------------- EIGRP
def _eigrp(topo: Topology, plan: Plan, failed: frozenset[str]):
    pfx = plan.allocation.subnets
    routers = sorted(topo.routers)
    nbrs = {r: sorted(((l.other(r), l) for l in topo.incident(r) if l.id not in failed),
                      key=lambda t: t[0]) for r in routers}

    sum_on: dict[tuple[str, str], list] = defaultdict(list)  # (router, link id) -> summaries
    suppressed: dict[tuple[str, str], set[str]] = defaultdict(set)
    configured_at: dict[object, set[str]] = defaultdict(set)  # summary prefix -> routers
    summaries = {}
    for x, ads in plan.adverts.items():
        if x == 0:
            continue
        sums = [ad for ad in ads if ad.kind == "summary"]
        if not sums:
            continue
        for b, l in topo.border_links(x):
            for ad in sums:
                sum_on[(b, l.id)].append(ad)
                suppressed[(b, l.id)].update(ad.members)
                configured_at[ad.prefix].add(b)
                summaries[ad.prefix] = ad

    fib: dict[str, dict] = {r: {} for r in routers}

    def spread(seeds: list[tuple[float, str, str | None]], allow, blocked: set[str] = frozenset()):
        dist: dict[str, float] = {}
        nh: dict[str, str | None] = {}
        heap = []
        for d, v, h in seeds:
            if d < dist.get(v, INF) or (d == dist.get(v) and h is not None and (nh[v] or "") > h):
                dist[v], nh[v] = d, h
                heapq.heappush(heap, (d, v))
        done = set()
        while heap:
            d, u = heapq.heappop(heap)
            if u in done or d > dist[u]:
                continue
            done.add(u)
            for v, l in nbrs[u]:
                if v in blocked or not allow(u, l):
                    continue
                nd = d + l.cost
                if nd < dist.get(v, INF) or (nd == dist[v] and v not in done and u < (nh[v] or "~")):
                    dist[v], nh[v] = nd, u
                    heapq.heappush(heap, (nd, v))
        return dist, nh

    # specific subnets
    for s in topo.subnets:
        p = pfx[s.name]
        dist, nh = spread([(0, s.router, None)],
                          lambda u, l, name=s.name: name not in suppressed.get((u, l.id), ()))
        for r, d in dist.items():
            fib[r][p] = Route(p, "connected" if r == s.router else "eigrp", d, nh[r])

    # summaries
    active_ranges: dict[str, list[str]] = defaultdict(list)
    for P, ad in sorted(summaries.items()):
        comps = [pfx[m] for m in ad.members]
        origin = {}
        for b in sorted(configured_at[P]):
            ms = [fib[b][c].cost for c in comps if c in fib[b]]
            if ms:
                origin[b] = min(ms)
        if not origin:
            continue
        seeds = []
        for b, m in origin.items():
            fib[b][P] = Route(P, "discard", 0, None)
            active_ranges[b].append(str(P))
            for v, l in nbrs[b]:
                if ad in sum_on.get((b, l.id), ()) and v not in origin:
                    seeds.append((m + l.cost, v, b))
        dist, nh = spread(seeds, lambda u, l: True, blocked=set(origin))
        for r, d in dist.items():
            if r not in origin:
                fib[r][P] = Route(P, "eigrp", d, nh[r])

    # control-plane size: prefixes carried on every adjacency (split horizon applied)
    name_of = {v: k for k, v in pfx.items()}
    ctrl = 0
    for r in routers:
        for v, l in nbrs[r]:
            sup = suppressed.get((r, l.id), set())
            for p, rt in fib[r].items():
                if rt.kind == "discard":
                    if any(ad.prefix == p for ad in sum_on.get((r, l.id), ())):
                        ctrl += 1
                    continue
                if rt.nexthop == v:
                    continue
                if rt.kind in ("connected", "eigrp") and name_of.get(p) in sup:
                    continue
                ctrl += 1
    info = {"ctrl_prefixes": ctrl, "active_ranges": dict(active_ranges)}
    return fib, info

