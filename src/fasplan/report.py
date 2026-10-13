"""Markdown/CSV/JSON outputs for one topology."""

from __future__ import annotations

import csv
import json
from pathlib import Path

from .model import Topology
from .plan import Plan

COMPARE_COLS = [
    ("label", "Strategy"), ("adverts", "Prefixes advertised"), ("routes_mean", "Routes/router"),
    ("ctrl_prefixes", "Ctrl prefixes"), ("fail_lost_pairs", "Lost pairs (failures)"),
    ("scenarios_with_loss", "Scenarios with loss"), ("stretch_normal_max", "Max stretch (normal)"),
    ("stretch_fail_max", "Max stretch (failures)"), ("churn_mean", "FIB churn/failure"),
    ("addr_util", "Addr utilisation"),
]


def table(rows: list[dict], cols=COMPARE_COLS) -> str:
    out = ["| " + " | ".join(c[1] for c in cols) + " |", "|" + "---|" * len(cols)]
    for r in rows:
        out.append("| " + " | ".join(str(r[c[0]]) for c in cols) + " |")
    return "\n".join(out)


def text_table(rows: list[dict], cols=COMPARE_COLS) -> str:
    heads = [c[1] for c in cols]
    data = [[str(r[c[0]]) for c in cols] for r in rows]
    widths = [max(len(h), *(len(d[i]) for d in data)) for i, h in enumerate(heads)]
    line = lambda xs: "  ".join(x.ljust(w) for x, w in zip(xs, widths))
    return "\n".join([line(heads), line(["-" * w for w in widths])] + [line(d) for d in data])


def plan_markdown(topo: Topology, plan: Plan) -> str:
    d = plan.to_dict(topo)
    lines = [f"# Address and summary plan: {topo.name}", "",
             f"Strategy **{plan.meta.get('label', plan.strategy)}**, protocol **{plan.protocol.upper()}**, "
             f"allocation **{d['allocation_method']}**, address utilisation {d['address_utilization']:.0%}.", "",
             "## Building blocks", ""]
    for a, blk in d["domain_blocks"].items():
        lines.append(f"- area {a}: `{blk}`")
    lines += ["", "## Subnet allocation (VLSM)", "",
              "| Subnet | Area | Router | Hosts | Prefix | Mask | Gateway | Usable range | Broadcast |",
              "|---|---|---|---|---|---|---|---|---|"]
    for r in d["subnets"]:
        lines.append(f"| {r['subnet']} | {r['area']} | {r['router']} | {r['hosts_needed']} | `{r['prefix']}` | "
                     f"{r['mask']} | {r['gateway']} | {r['first_host']} - {r['last_host']} | {r['broadcast']} |")
    lines += ["", "## Advertised prefixes", ""]
    for a, ads in d["adverts"].items():
        if a == "0":
            continue
        border = ", ".join(topo.border_routers(int(a)))
        lines.append(f"### Area {a} (border routers: {border})")
        lines.append("")
        for ad in ads:
            mem = ", ".join(ad["members"])
            if ad["kind"] == "summary":
                lines.append(f"- **summary** `{ad['prefix']}` covers {mem}")
            else:
                lines.append(f"- subnet `{ad['prefix']}` ({mem}) advertised separately")
        lines.append("")
    if plan.decisions:
        lines += ["## Why: summary decisions", ""]
        for dec in plan.decisions:
            mark = {"summarize": "kept", "split": "split", "single": "single"}[dec.verdict]
            reason = f" — {dec.reason}" if dec.reason else ""
            lines.append(f"- `{dec.prefix}` [{', '.join(dec.members)}]: **{mark}**{reason}")
        lines.append("")
    return "\n".join(lines)


def write_plan(topo: Topology, plan: Plan, outdir: str | Path) -> list[Path]:
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    d = plan.to_dict(topo)
    paths = [outdir / "plan.json", outdir / "allocation.csv", outdir / "plan.md"]
    paths[0].write_text(json.dumps(d, indent=2))
    with open(paths[1], "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(d["subnets"][0]))
        w.writeheader()
        w.writerows(d["subnets"])
    paths[2].write_text(plan_markdown(topo, plan))
    return paths
