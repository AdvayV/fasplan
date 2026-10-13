"""Planning strategies.

flat      conventional VLSM, every subnet advertised separately (no summarization)
maxagg    conventional VLSM, one summary per building (maximum aggregation)
shaikh    conventional VLSM, Shaikh et al. (ICNP 2003) style budgeted dynamic
          programme over the address trie: for a budget of k advertised
          prefixes, minimise total path-cost error in normal operation.
          By default k equals the number of prefixes fasp advertises, so the
          two are compared at equal routing-table size.
fasp-sel  conventional VLSM + failure-aware summary selection (ablation)
fasp      failure-aware VLSM + failure-aware selection + simulator
          verification that splits any summary still causing loss
"""

from __future__ import annotations

import math
from itertools import combinations

from .addressing import Allocation, Group, Leaf, allocate, conventional, leaf_for
from .model import Scenario, Topology
from .plan import Advert, Decision, Plan, flat_adverts, tight_prefix
from .proxy import DomainContext
from .routing import simulate

STRATEGIES = ["flat", "maxagg", "shaikh", "fasp-sel", "fasp"]
LABELS = {
    "flat": "No summarization",
    "maxagg": "Maximum aggregation",
    "shaikh": "Shaikh DP (failure-unaware)",
    "fasp-sel": "Failure-aware selection only",
    "fasp": "Proposed (failure-aware design)",
}

BIG = 1e9


class Planner:
    def __init__(self, topo: Topology, protocol: str = "ospf", scenarios: list[Scenario] | None = None,
                 tau_normal: float = 1.25, tau_failure: float = 1.5, headroom: float = 0.0,
                 verify: bool = True) -> None:
        self.topo = topo
        self.protocol = protocol.lower()
        self.scenarios = scenarios or topo.scenarios()
        self.tau_normal = tau_normal
        self.tau_failure = tau_failure
        self.headroom = headroom
        self.verify = verify
        self._ctx: dict[int, DomainContext] = {}
        self._cache: dict[str, Plan] = {}
        self._conv_alloc: Allocation | None = None
        self._fa_alloc_cache: Allocation | None = None

    def ctx(self, area: int) -> DomainContext:
        if area not in self._ctx:
            self._ctx[area] = DomainContext(self.topo, area, self.scenarios, self.protocol)
        return self._ctx[area]

    # ------------------------------------------------------------------ public
    def plan(self, strategy: str, budget: int | None = None) -> Plan:
        key = f"{strategy}:{budget}"
        if key in self._cache:
            return self._cache[key]
        fn = {
            "flat": self._flat, "maxagg": self._maxagg, "shaikh": lambda: self._shaikh(budget),
            "fasp-sel": lambda: self._fasp(failure_aware_alloc=False),
            "fasp": lambda: self._fasp(failure_aware_alloc=True),
        }.get(strategy)
        if fn is None:
            raise ValueError(f"unknown strategy {strategy}; choose from {STRATEGIES}")
        plan = fn()
        plan.meta.setdefault("label", LABELS[strategy])
        plan.meta.update(tau_normal=self.tau_normal, tau_failure=self.tau_failure,
                         scenarios=len(self.scenarios))
        self._cache[key] = plan
        return plan

    def plan_all(self) -> dict[str, Plan]:
        return {s: self.plan(s) for s in STRATEGIES}

    # -------------------------------------------------------------- strategies
    def _conv(self) -> Allocation:
        if self._conv_alloc is None:
            self._conv_alloc = conventional(self.topo, self.headroom)
        return self._conv_alloc

    def _flat(self) -> Plan:
        alloc = self._conv()
        return Plan("flat", self.protocol, alloc, flat_adverts(self.topo, alloc))

    def _maxagg(self) -> Plan:
        alloc = self._conv()
        adverts = flat_adverts(self.topo, alloc)
        for area in self.topo.domains():
            names = [s.name for s in self.topo.subnets_in(area)]
            if len(names) > 1:
                p = tight_prefix([alloc.subnets[n] for n in names])
                adverts[area] = [Advert(p, tuple(sorted(names)), "summary", "one summary per building")]
        return Plan("maxagg", self.protocol, alloc, adverts)

    def _shaikh(self, budget: int | None) -> Plan:
        alloc = self._conv()
        if budget is None:
            budget = self.plan("fasp").advert_count()
        adverts = flat_adverts(self.topo, alloc)
        domains = self.topo.domains()
        fixed = sum(len(v) for a, v in adverts.items() if a not in domains)
        tables = {a: self._dp(alloc, a, [s.name for s in self.topo.subnets_in(a)], {}) for a in domains}
        # knapsack over buildings: total budget for building prefixes
        k_left = max(budget - fixed, len(domains))
        combo = {0: (0.0, {})}
        for a in domains:
            nxt = {}
            for k0, (e0, pick) in combo.items():
                for k1, (e1, _) in tables[a][1].items():
                    k = k0 + k1
                    if k > k_left:
                        continue
                    e = e0 + e1
                    if k not in nxt or e < nxt[k][0]:
                        nxt[k] = (e, {**pick, a: k1})
            combo = nxt
        if not combo:  # budget below one prefix per building: fall back to the cheapest
            best_pick = {a: min(tables[a][1]) for a in domains}
        else:
            best_pick = min(combo.values(), key=lambda t: (t[0], -sum(t[1].values())))[1]
        for a in domains:
            adverts[a] = self._dp_build(alloc, a, tables[a], best_pick[a])
        plan = Plan("shaikh", self.protocol, alloc, adverts)
        plan.meta["budget"] = budget
        return plan

    def _fasp(self, failure_aware_alloc: bool) -> Plan:
        alloc = self._fa_alloc() if failure_aware_alloc else self._conv()
        name = "fasp" if failure_aware_alloc else "fasp-sel"
        decisions: list[Decision] = []
        adverts = flat_adverts(self.topo, alloc)
        forbid: set = set()
        for a in self.topo.domains():
            adverts[a] = self._topdown(alloc, a, [s.name for s in self.topo.subnets_in(a)],
                                       decisions, forbid, use_failures=True)
        plan = Plan(name, self.protocol, alloc, adverts, decisions)
        if self.verify:
            self._verify_and_split(plan, forbid)
        return plan

    # ------------------------------------------------------------- selection
    def _topdown(self, alloc: Allocation, area: int, members: list[str], decisions: list[Decision],
                 forbid: set, use_failures: bool) -> list[Advert]:
        nets = [alloc.subnets[m] for m in members]
        if len(members) == 1:
            return [Advert(nets[0], (members[0],), "subnet")]
        p = tight_prefix(nets)
        ok, reason = self.ctx(area).assess(members).verdict(self.tau_normal, self.tau_failure, use_failures)
        if ok and p in forbid:
            ok, reason = False, "simulation found traffic loss with this summary"
        if ok:
            decisions.append(Decision(str(p), sorted(members), "summarize"))
            return [Advert(p, tuple(sorted(members)), "summary", "safe in all checked scenarios")]
        decisions.append(Decision(str(p), sorted(members), "split", reason))
        lo, hi = p.subnets(prefixlen_diff=1)
        left = [m for m in members if alloc.subnets[m].subnet_of(lo)]
        right = [m for m in members if alloc.subnets[m].subnet_of(hi)]
        out = []
        for half in (left, right):
            if half:
                out += self._topdown(alloc, area, half, decisions, forbid, use_failures)
        return out

    def _dp(self, alloc: Allocation, area: int, members: list[str], memo: dict):
        """Budgeted DP: returns (members, {k: (error, choice)})."""
        key = frozenset(members)
        if key in memo:
            return memo[key]
        if len(members) == 1:
            res = (members, {1: (0.0, ("leaf",))})
            memo[key] = res
            return res
        p = tight_prefix([alloc.subnets[m] for m in members])
        a = self.ctx(area).assess(members)
        table = {1: (a.normal_error + BIG * a.bh_normal, ("sum",))}
        lo, hi = p.subnets(prefixlen_diff=1)
        left = [m for m in members if alloc.subnets[m].subnet_of(lo)]
        right = [m for m in members if alloc.subnets[m].subnet_of(hi)]
        tl = self._dp(alloc, area, left, memo)
        tr = self._dp(alloc, area, right, memo)
        for kl, (el, _) in tl[1].items():
            for kr, (er, _) in tr[1].items():
                k, e = kl + kr, el + er
                if k not in table or e < table[k][0]:
                    table[k] = (e, ("split", kl, kr, tl, tr))
        res = (members, table)
        memo[key] = res
        return res

    def _dp_build(self, alloc: Allocation, area: int, node, k: int) -> list[Advert]:
        members, table = node
        _, choice = table[k]
        if choice[0] == "leaf":
            return [Advert(alloc.subnets[members[0]], (members[0],), "subnet")]
        if choice[0] == "sum":
            p = tight_prefix([alloc.subnets[m] for m in members])
            return [Advert(p, tuple(sorted(members)), "summary", "chosen by budgeted DP")]
        _, kl, kr, tl, tr = choice
        return self._dp_build(alloc, area, tl, kl) + self._dp_build(alloc, area, tr, kr)

    # ------------------------------------------------------------ verification
    def _verify_and_split(self, plan: Plan, forbid: set, rounds: int = 8) -> None:
        topo = self.topo
        flat = Plan("flat", self.protocol, plan.allocation, flat_adverts(topo, plan.allocation))
        routers = sorted(topo.routers)
        for it in range(rounds):
            bad: dict = {}
            for f in self.scenarios:
                sp = simulate(topo, plan, f.failed, self.protocol)
                sf = simulate(topo, flat, f.failed, self.protocol)
                for area, ad in plan.summaries():
                    if (area, ad.prefix) in bad:
                        continue
                    for s in ad.members:
                        hit = next((r for r in routers if sf.walk(r, s).status == "delivered"
                                    and sp.walk(r, s).status != "delivered"), None)
                        if hit:
                            w = sp.walk(hit, s)
                            bad[(area, ad.prefix)] = (f"simulation, {f.name}: {s} from {hit} "
                                                      f"{w.status} at {w.drop_at}")
                            break
            plan.meta["verify_rounds"] = it + 1
            if not bad:
                return
            for (area, prefix), why in bad.items():
                forbid.add(prefix)
                ads = plan.adverts[area]
                idx = next(i for i, ad in enumerate(ads) if ad.prefix == prefix)
                ad = ads[idx]
                plan.decisions.append(Decision(str(prefix), list(ad.members), "split", why))
                repl = self._topdown(plan.allocation, area, list(ad.members), plan.decisions, forbid, True)
                ads[idx:idx + 1] = repl
        plan.meta["verify_unresolved"] = True

    # ------------------------------------------------------ failure-aware VLSM
    def _fa_alloc(self) -> Allocation:
        if self._fa_alloc_cache is not None:
            return self._fa_alloc_cache
        topo = self.topo
        trees = {}
        for area in sorted({s.area for s in topo.subnets}):
            names = [s.name for s in topo.subnets_in(area)]
            if area == 0:
                trees[area] = Group([leaf_for(topo, n, self.headroom) for n in names], "area0")
                continue
            ctx = self.ctx(area)
            # level 1: subnets with identical behaviour (normally: same router)
            eq: dict[tuple, list[str]] = {}
            for n in names:
                eq.setdefault((ctx.signature(n), ctx.cost_vector(n)), []).append(n)
            # level 2: same reachability signature -> one class block
            classes: dict[tuple, list] = {}
            for (sig, _cv), ns in sorted(eq.items(), key=lambda kv: sorted(kv[1])):
                node = self._node([leaf_for(topo, n, self.headroom) for n in ns], "+".join(sorted(ns)))
                classes.setdefault(sig, []).append((node, ns))
            items = []
            for sig, parts in classes.items():
                node = self._node([p for p, _ in parts], "class:" + "+".join(sorted(x for _, ns in parts for x in ns)))
                items.append((node, [sig]))
            # level 3: complete-linkage clustering of classes by signature distance
            while len(items) > 1:
                best = None
                for (i, (ni, si)), (j, (nj, sj)) in combinations(enumerate(items), 2):
                    dist = max(_hamming(x, y) for x in si for y in sj)
                    merged = Group([ni, nj]).size
                    waste = merged - ni.size - nj.size
                    cand = (dist, waste, i, j)
                    if best is None or cand < best:
                        best = cand
                _, _, i, j = best
                ni, si = items[i]
                nj, sj = items[j]
                merged = (Group([ni, nj], f"({_label(ni)}|{_label(nj)})"), si + sj)
                items = [x for k, x in enumerate(items) if k not in (i, j)] + [merged]
            root = items[0][0]
            trees[area] = root if isinstance(root, Group) else Group([root], f"area{area}")
        alloc = allocate(topo, trees, "failure-aware", self.headroom)
        self._fa_alloc_cache = alloc
        return alloc

    @staticmethod
    def _node(children: list, label: str):
        return children[0] if len(children) == 1 else Group(children, label)


def _hamming(a: tuple, b: tuple) -> int:
    return sum(len(x ^ y) for x, y in zip(a, b))


def _label(node) -> str:
    return node.subnet if isinstance(node, Leaf) else node.label


def build_plan(topo: Topology, strategy: str, protocol: str = "ospf", **kw) -> Plan:
    budget = kw.pop("budget", None)
    return Planner(topo, protocol, **kw).plan(strategy, budget)
