"""Repeated analytical experiments over generated campuses.

Grid: building count x redundancy profile x seeds x protocol x strategy.
Each topology is planned and evaluated with every strategy; one CSV row per
(topology, protocol, strategy).  Runs in parallel across processes.
"""

from __future__ import annotations

import csv
import math
import os
import statistics
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import asdict, replace
from pathlib import Path

from .evaluate import compare, evaluate_plan
from .model import NORMAL
from .generator import GenParams, generate

PROFILES = {
    "low": GenParams(dual_dist_prob=0.6, dual_home_prob=0.3, dist_link_prob=0.2, dual_uplink_prob=0.1),
    "mixed": GenParams(),
    "high": GenParams(dual_dist_prob=1.0, dual_home_prob=1.0, dist_link_prob=1.0, dual_uplink_prob=0.5),
}


def _job(seed: int, buildings: int, profile: str, protocols: tuple[str, ...], double_failures: int,
         tau_normal: float, tau_failure: float, holdout: int) -> list[dict]:
    params = replace(PROFILES[profile], buildings=buildings, double_failures=double_failures)
    topo = generate(seed, params)
    # double failures the planner never sees, drawn with a different seed
    held = topo.sample_double_failures(holdout, seed=10_000 + seed, exclude=topo.scenarios())
    rows = []
    for proto in protocols:
        res, plans, _details = compare(topo, proto, tau_normal=tau_normal, tau_failure=tau_failure)
        flat_states: dict = {}
        for r in res:
            row = {"seed": seed, "buildings": buildings, "profile": profile,
                   "routers": len(topo.routers), "links": len(topo.links),
                   "subnets": len(topo.subnets), **r}
            if held:
                h, _ = evaluate_plan(topo, plans[r["strategy"]], [NORMAL] + held, proto, flat_states)
                row.update(holdout_scenarios=len(held), holdout_lost_pairs=h["fail_lost_pairs"],
                           holdout_loss_rate=h["fail_loss_rate"],
                           holdout_scenarios_with_loss=h["scenarios_with_loss"],
                           holdout_stretch_p95=h["stretch_fail_p95"])
            rows.append(row)
    return rows


def run(outdir: str | Path, seeds: int = 10, buildings=(3, 4, 6), profiles=("low", "mixed", "high"),
        protocols=("ospf", "eigrp"), double_failures: int = 15, tau_normal: float = 1.25,
        tau_failure: float = 1.5, workers: int | None = None, holdout: int = 30, progress=print) -> Path:
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    jobs = [(s, b, p) for b in buildings for p in profiles for s in range(seeds)]
    rows: list[dict] = []
    workers = workers or max(1, (os.cpu_count() or 2) - 1)
    with ProcessPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(_job, s, b, p, tuple(protocols), double_failures, tau_normal, tau_failure, holdout): (s, b, p)
                for s, b, p in jobs}
        for i, fut in enumerate(as_completed(futs), 1):
            rows.extend(fut.result())
            if progress and (i % max(1, len(jobs) // 10) == 0 or i == len(jobs)):
                progress(f"  {i}/{len(jobs)} topologies done")
    rows.sort(key=lambda r: (r["protocol"], r["buildings"], r["profile"], r["seed"], r["strategy"]))
    path = outdir / "experiments.csv"
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    write_summary(rows, outdir / "summary.md")
    return path


def load(path: str | Path) -> list[dict]:
    with open(path) as fh:
        rows = list(csv.DictReader(fh))
    for r in rows:
        for k, v in r.items():
            try:
                r[k] = float(v) if any(c in v for c in ".e") else int(v)
            except (ValueError, TypeError):
                pass
    return rows


def _ci(xs: list[float]) -> tuple[float, float]:
    m = statistics.fmean(xs)
    if len(xs) < 2:
        return m, 0.0
    return m, 1.96 * statistics.stdev(xs) / math.sqrt(len(xs))


def retained_reduction(rows: list[dict]) -> dict[tuple, float]:
    """Share of max-aggregation's route reduction a plan keeps: (flat - plan) / (flat - maxagg)."""
    idx = {(r["protocol"], r["buildings"], r["profile"], r["seed"], r["strategy"]): r for r in rows}
    out = {}
    for (proto, b, p, s, strat), r in idx.items():
        flat = idx.get((proto, b, p, s, "flat"))
        mx = idx.get((proto, b, p, s, "maxagg"))
        if flat and mx and flat["routes_mean"] > mx["routes_mean"]:
            out[(proto, b, p, s, strat)] = (flat["routes_mean"] - r["routes_mean"]) / (flat["routes_mean"] - mx["routes_mean"])
    return out


def write_summary(rows: list[dict], path: Path) -> None:
    from .strategies import LABELS, STRATEGIES
    keep = retained_reduction(rows)
    metrics = [("adverts", "Advertised prefixes"), ("routes_mean", "Routes / router"),
               ("ctrl_prefixes", "Control-plane prefixes"), ("fail_loss_rate", "Failure loss rate"),
               ("scenarios_with_loss", "Scenarios with loss"), ("stretch_normal_max", "Max stretch (normal)"),
               ("stretch_fail_p95", "p95 stretch (failures)"), ("churn_mean", "FIB churn / failure"),
               ("addr_util", "Address utilisation")]
    if rows and "holdout_loss_rate" in rows[0]:
        metrics.insert(5, ("holdout_loss_rate", "Held-out double-failure loss rate"))
    lines = ["# Experiment summary", "",
             f"{len({(r['buildings'], r['profile'], r['seed']) for r in rows})} generated topologies; "
             "mean ± 95% CI across topologies.", ""]
    for proto in sorted({r["protocol"] for r in rows}):
        lines += [f"## {proto.upper()}", "",
                  "| Strategy | " + " | ".join(m[1] for m in metrics) + " | Reduction retained |",
                  "|---" * (len(metrics) + 2) + "|"]
        for s in STRATEGIES:
            sub = [r for r in rows if r["protocol"] == proto and r["strategy"] == s]
            if not sub:
                continue
            cells = []
            for key, _ in metrics:
                m, ci = _ci([float(r[key]) for r in sub])
                cells.append(f"{m:.3g} ± {ci:.2g}")
            kr = [v for k, v in keep.items() if k[0] == proto and k[4] == s]
            m, ci = _ci(kr) if kr else (float("nan"), 0)
            lines.append(f"| {LABELS[s]} | " + " | ".join(cells) + f" | {m:.0%} ± {ci:.0%} |")
        lines += ["", "Topologies where the strategy lost traffic in at least one failure scenario:", ""]
        for s in STRATEGIES:
            sub = [r for r in rows if r["protocol"] == proto and r["strategy"] == s]
            if sub:
                bad = sum(1 for r in sub if r["fail_lost_pairs"] > 0 or r["normal_lost"] > 0)
                line = f"- {LABELS[s]}: {bad}/{len(sub)} on planned scenarios"
                if "holdout_lost_pairs" in sub[0]:
                    hb = sum(1 for r in sub if r["holdout_lost_pairs"] > 0)
                    line += f", {hb}/{len(sub)} on held-out double failures"
                lines.append(line)
        lines.append("")
    path.write_text("\n".join(lines))
