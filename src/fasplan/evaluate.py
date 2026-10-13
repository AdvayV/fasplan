"""Analytical evaluation: simulate every scenario and score a plan against
per-subnet routing (the flat plan) under the same failures."""

from __future__ import annotations

import statistics
from dataclasses import asdict, dataclass, field

from .model import Scenario, Topology
from .plan import Plan
from .routing import RoutingState, simulate
from .strategies import STRATEGIES, Planner


@dataclass
class ScenarioResult:
    scenario: str
    failed: list[str]
    pairs: int = 0
    delivered_flat: int = 0
    delivered: int = 0
    lost: int = 0  # delivered with per-subnet routes, dropped with this plan
    lost_pairs: list[dict] = field(default_factory=list)
    stretch: list[float] = field(default_factory=list)
    routes_mean: float = 0.0
    routes_max: int = 0
    ctrl_prefixes: int = 0
    churn: int = 0


def _fib_signature(state: RoutingState) -> dict[str, dict]:
    return {r: {p: (rt.kind, rt.cost, rt.nexthop) for p, rt in fib.items()} for r, fib in state.fibs.items()}


def _churn(a: dict, b: dict) -> int:
    n = 0
    for r in a:
        fa, fb = a[r], b.get(r, {})
        n += sum(1 for p in fa.keys() | fb.keys() if fa.get(p) != fb.get(p))
    return n


def evaluate_plan(topo: Topology, plan: Plan, scenarios: list[Scenario], protocol: str,
                  flat_states: dict[frozenset, RoutingState] | None = None,
                  keep_pairs: int = 50) -> tuple[dict, list[ScenarioResult]]:
    routers = sorted(topo.routers)
    flat_states = flat_states if flat_states is not None else {}
    results = []
    normal_sig = None
    for f in scenarios:
        sp = simulate(topo, plan, f.failed, protocol)
        if f.failed not in flat_states:
            from .plan import flat_adverts
            flat = Plan("flat", protocol, plan.allocation, flat_adverts(topo, plan.allocation))
            flat_states[f.failed] = simulate(topo, flat, f.failed, protocol)
        sf = flat_states[f.failed]
        res = ScenarioResult(f.name, sorted(f.failed))
        for s in topo.subnets:
            for r in routers:
                if r == s.router:
                    continue
                res.pairs += 1
                wf = sf.walk(r, s.name)
                wp = sp.walk(r, s.name)
                if wp.status == "delivered":
                    res.delivered += 1
                if wf.status == "delivered":
                    res.delivered_flat += 1
                    if wp.status != "delivered":
                        res.lost += 1
                        if len(res.lost_pairs) < keep_pairs:
                            res.lost_pairs.append({"src": r, "subnet": s.name, "status": wp.status,
                                                   "drop_at": wp.drop_at, "path": list(wp.path)})
                    elif wf.cost > 0:
                        res.stretch.append(wp.cost / wf.cost)
        sizes = [sp.route_count(r) for r in routers]
        res.routes_mean = statistics.fmean(sizes)
        res.routes_max = max(sizes)
        res.ctrl_prefixes = sp.info.get("ctrl_prefixes", 0)
        sig = _fib_signature(sp)
        if f.is_normal:
            normal_sig = sig
        elif normal_sig is not None:
            res.churn = _churn(normal_sig, sig)
        results.append(res)
    return summarize(topo, plan, results), results


def summarize(topo: Topology, plan: Plan, results: list[ScenarioResult]) -> dict:
    normal = next(r for r in results if not r.failed)
    fails = [r for r in results if r.failed]
    fail_stretch = sorted(x for r in fails for x in r.stretch)
    fail_flat = sum(r.delivered_flat for r in fails)
    fail_lost = sum(r.lost for r in fails)

    def pct(xs, q):
        return xs[min(len(xs) - 1, int(q * len(xs)))] if xs else 1.0

    return {
        "strategy": plan.strategy,
        "label": plan.meta.get("label", plan.strategy),
        "protocol": plan.protocol,
        "adverts": plan.advert_count(),
        "summaries": len(plan.summaries()),
        "routes_mean": round(normal.routes_mean, 3),
        "routes_max": normal.routes_max,
        "ctrl_prefixes": normal.ctrl_prefixes,
        "normal_lost": normal.lost,
        "fail_lost_pairs": fail_lost,
        "fail_loss_rate": round(fail_lost / fail_flat, 5) if fail_flat else 0.0,
        "scenarios_with_loss": sum(1 for r in results if r.lost),
        "scenarios": len(results),
        "stretch_normal_mean": round(statistics.fmean(normal.stretch), 4) if normal.stretch else 1.0,
        "stretch_normal_max": round(max(normal.stretch), 4) if normal.stretch else 1.0,
        "stretch_fail_mean": round(statistics.fmean(fail_stretch), 4) if fail_stretch else 1.0,
        "stretch_fail_p95": round(pct(fail_stretch, 0.95), 4),
        "stretch_fail_max": round(fail_stretch[-1], 4) if fail_stretch else 1.0,
        "churn_mean": round(statistics.fmean(r.churn for r in fails), 3) if fails else 0.0,
        "addr_util": round(plan.allocation.utilization(topo), 4),
        "host_util": round(plan.allocation.host_utilization(topo), 4),
    }


def compare(topo: Topology, protocol: str = "ospf", strategies: list[str] | None = None,
            planner: Planner | None = None, eval_scenarios: list[Scenario] | None = None,
            **planner_kw) -> tuple[list[dict], dict[str, Plan], dict[str, list[ScenarioResult]]]:
    planner = planner or Planner(topo, protocol, **planner_kw)
    scenarios = eval_scenarios or planner.scenarios
    rows, plans, details = [], {}, {}
    flat_states: dict = {}
    for s in strategies or STRATEGIES:
        plan = planner.plan(s)
        summary, res = evaluate_plan(topo, plan, scenarios, protocol, flat_states)
        rows.append(summary)
        plans[s] = plan
        details[s] = res
    return rows, plans, details


def scenario_details(results: list[ScenarioResult]) -> list[dict]:
    return [asdict(r) | {"stretch": None, "stretch_max": max(r.stretch, default=1.0)} for r in results]
