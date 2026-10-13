"""Command-line interface: `fasplan <command> ...`."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

from .model import load_topology


def _planner(args, topo):
    from .strategies import Planner
    return Planner(topo, args.protocol, tau_normal=args.tau_normal, tau_failure=args.tau_failure,
                   headroom=args.headroom)


def cmd_plan(args) -> int:
    from .report import write_plan
    topo = load_topology(args.topology)
    plan = _planner(args, topo).plan(args.strategy)
    paths = write_plan(topo, plan, args.out)
    print(f"{plan.meta['label']} ({args.protocol.upper()}): {plan.advert_count()} prefixes advertised, "
          f"{len(plan.summaries())} summaries")
    for area, ad in plan.summaries():
        print(f"  area {area}: {ad.prefix}  <- {', '.join(ad.members)}")
    for p in paths:
        print(f"  wrote {p}")
    return 0


def cmd_compare(args) -> int:
    from .evaluate import compare
    from .report import table, text_table
    topo = load_topology(args.topology)
    protocols = ["ospf", "eigrp"] if args.protocol == "both" else [args.protocol]
    md = [f"# Strategy comparison: {topo.name}", ""]
    for proto in protocols:
        rows, _plans, _ = compare(topo, proto, tau_normal=args.tau_normal, tau_failure=args.tau_failure,
                                  headroom=args.headroom)
        print(f"\n{proto.upper()} — {len(topo.scenarios())} scenarios "
              f"({len(topo.scenarios()) - 1} with failures)\n")
        print(text_table(rows))
        md += [f"## {proto.upper()}", "", table(rows), ""]
    if args.out:
        Path(args.out).write_text("\n".join(md))
        print(f"\nwrote {args.out}")
    return 0


def cmd_scenario(args) -> int:
    from .plan import Plan, flat_adverts
    from .routing import simulate
    topo = load_topology(args.topology)
    plan = _planner(args, topo).plan(args.strategy)
    failed = frozenset(topo.resolve_link(x).id for x in args.fail)
    flat = Plan("flat", args.protocol, plan.allocation, flat_adverts(topo, plan.allocation))
    sp, sf = simulate(topo, plan, failed, args.protocol), simulate(topo, flat, failed, args.protocol)
    lost = []
    for s in topo.subnets:
        for r in sorted(topo.routers):
            if r != s.router and sf.walk(r, s.name).status == "delivered" and sp.walk(r, s.name).status != "delivered":
                w = sp.walk(r, s.name)
                lost.append(f"  {r:>8} -> {s.name:<14} {w.status} at {w.drop_at}  path {' > '.join(w.path)}")
    print(f"{plan.meta['label']} / {args.protocol.upper()} / failed: {', '.join(sorted(failed)) or 'none'}")
    print(f"{len(lost)} source-subnet pairs lose traffic that per-subnet routing would deliver")
    print("\n".join(lost[: args.limit]))
    if args.router:
        print(f"\nFIB of {args.router}:")
        for p, rt in sorted(sp.fibs[args.router].items(), key=lambda kv: kv[0]):
            print(f"  {str(p):<18} {rt.kind:<9} cost {rt.cost:<5g} via {rt.nexthop or '-'}")
    return 0


def cmd_generate(args) -> int:
    from .generator import GenParams, generate
    from .experiments import PROFILES
    from dataclasses import replace
    params = replace(PROFILES[args.profile], buildings=args.buildings, double_failures=args.double_failures)
    topo = generate(args.seed, params)
    text = yaml.safe_dump(topo.to_dict(), sort_keys=False)
    if args.out:
        Path(args.out).write_text(text)
        print(f"wrote {args.out}: {len(topo.routers)} routers, {len(topo.links)} links, {len(topo.subnets)} subnets")
    else:
        sys.stdout.write(text)
    return 0


def cmd_experiment(args) -> int:
    from .experiments import run
    path = run(args.out, seeds=args.seeds, buildings=tuple(args.buildings), profiles=tuple(args.profiles),
               protocols=tuple(args.protocols), double_failures=args.double_failures,
               tau_normal=args.tau_normal, tau_failure=args.tau_failure, workers=args.workers,
               holdout=args.holdout)
    print(f"wrote {path} and {path.parent / 'summary.md'}")
    if not args.no_plots:
        from .plots import make_all
        figs = make_all(path)
        print(f"wrote {len(figs)} figure files to {figs[0].parent}")
    return 0


def cmd_plots(args) -> int:
    from .plots import make_all
    figs = make_all(args.csv, args.out)
    print(f"wrote {len(figs)} figure files to {figs[0].parent}")
    return 0


def cmd_lab(args) -> int:
    from .testbed.configgen import build_lab
    topo = load_topology(args.topology)
    plan = _planner(args, topo).plan(args.strategy)
    out = build_lab(topo, plan, args.out, target=args.target, protocol=args.protocol,
                    lab_name=args.name, probe=args.probe)
    print(f"lab files for {plan.meta['label']} ({args.target}/{args.protocol}) in {out}")
    return 0


def cmd_serve(args) -> int:
    from .web.app import serve
    serve(args.host, args.port)
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="fasplan", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p, protocol_choices=("ospf", "eigrp")):
        p.add_argument("--protocol", default="ospf", choices=protocol_choices)
        p.add_argument("--tau-normal", type=float, default=1.25,
                       help="max path stretch allowed for a summary in normal operation (default 1.25)")
        p.add_argument("--tau-failure", type=float, default=1.5,
                       help="max path stretch allowed under failures (default 1.5)")
        p.add_argument("--headroom", type=float, default=0.0, help="host growth headroom, e.g. 0.2 = +20%%")

    strategies = ["flat", "maxagg", "shaikh", "fasp-sel", "fasp"]

    p = sub.add_parser("plan", help="VLSM allocation + summary recommendation for a topology")
    p.add_argument("topology")
    p.add_argument("--strategy", default="fasp", choices=strategies)
    p.add_argument("-o", "--out", default="out/plan")
    common(p)
    p.set_defaults(fn=cmd_plan)

    p = sub.add_parser("compare", help="evaluate all strategies under every failure scenario")
    p.add_argument("topology")
    p.add_argument("-o", "--out", help="write markdown table here")
    common(p, ("ospf", "eigrp", "both"))
    p.set_defaults(fn=cmd_compare)

    p = sub.add_parser("scenario", help="show what one failure does to one plan")
    p.add_argument("topology")
    p.add_argument("--strategy", default="maxagg", choices=strategies)
    p.add_argument("--fail", nargs="*", default=[], help="links like a-d1--a-d2")
    p.add_argument("--router", help="also print this router's FIB")
    p.add_argument("--limit", type=int, default=30)
    common(p)
    p.set_defaults(fn=cmd_scenario)

    p = sub.add_parser("generate", help="random campus topology (YAML)")
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--buildings", type=int, default=3)
    p.add_argument("--profile", default="mixed", choices=["low", "mixed", "high"])
    p.add_argument("--double-failures", type=int, default=0)
    p.add_argument("-o", "--out")
    p.set_defaults(fn=cmd_generate)

    p = sub.add_parser("experiment", help="repeated experiments over generated campuses")
    p.add_argument("-o", "--out", default="results")
    p.add_argument("--seeds", type=int, default=10)
    p.add_argument("--buildings", type=int, nargs="+", default=[3, 4, 6])
    p.add_argument("--profiles", nargs="+", default=["low", "mixed", "high"])
    p.add_argument("--protocols", nargs="+", default=["ospf", "eigrp"])
    p.add_argument("--double-failures", type=int, default=15)
    p.add_argument("--tau-normal", type=float, default=1.25)
    p.add_argument("--tau-failure", type=float, default=1.5)
    p.add_argument("--workers", type=int)
    p.add_argument("--holdout", type=int, default=30,
                   help="held-out double-link failures per topology, never shown to the planner")
    p.add_argument("--no-plots", action="store_true")
    p.set_defaults(fn=cmd_experiment)

    p = sub.add_parser("plots", help="regenerate figures from an experiments CSV")
    p.add_argument("csv")
    p.add_argument("-o", "--out")
    p.set_defaults(fn=cmd_plots)

    p = sub.add_parser("lab", help="write containerlab topology + router configs for a plan")
    p.add_argument("topology")
    p.add_argument("--strategy", default="fasp", choices=strategies)
    p.add_argument("--target", default="frr", choices=["frr", "iol"])
    p.add_argument("--name", help="lab name (default: <topology>-<strategy>-<protocol>)")
    p.add_argument("--probe", help="router the probe host attaches to")
    p.add_argument("-o", "--out", default="lab")
    common(p)
    p.set_defaults(fn=cmd_lab)

    p = sub.add_parser("serve", help="start the web dashboard")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    p.set_defaults(fn=cmd_serve)

    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
