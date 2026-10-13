# fasplan: failure-aware subnet design and route summarization

Implementation for the project **"Failure-Aware Subnet Design and Route
Summarization for Resilient Campus Networks"** (base paper: A. Shaikh et al.,
*An Efficient Algorithm for OSPF Subnet Aggregation*, IEEE ICNP 2003).

Route summarization shrinks routing tables, but a border router keeps
advertising a summary while **any** of its subnets is reachable. After some
link failures it therefore attracts, and discards, traffic for subnets it can
no longer reach, even though another path exists. `fasplan` plans IPv4
addressing (VLSM) and summaries so that this cannot happen in the failure
scenarios you specify, while keeping as much of the table reduction as
possible.

## What's in the box

| Component | Path | What it does |
|---|---|---|
| Planner | `src/fasplan/strategies.py`, `addressing.py`, `proxy.py` | VLSM allocation, failure-aware grouping, summary selection, 4 baselines |
| Routing simulator | `src/fasplan/routing.py` | converged OSPF (multi-area, area ranges) and EIGRP (interface summaries) forwarding tables + hop-by-hop forwarding |
| Evaluation | `src/fasplan/evaluate.py`, `experiments.py`, `plots.py` | per-scenario loss / stretch / table size / churn, multi-topology campaigns, figures |
| Topology generator | `src/fasplan/generator.py` | random campuses with low / mixed / high redundancy |
| Web dashboard | `src/fasplan/web/` | interactive demo: fail links by clicking, see blackholes, export configs |
| Emulation testbed | `src/fasplan/testbed/` | containerlab + FRR / Cisco IOL configs, failure injection, loss/outage/control-traffic measurement |
| Docs | `docs/method.md`, `docs/testbed.md`, `docs/presentation.md` | method write-up, testbed guide, slide + demo plan |

## Quick start

```bash
cd ~/Projects/fasplan
uv venv && uv pip install -e '.[web,analysis,dev]'
source .venv/bin/activate

fasplan plan examples/campus.yaml -o out/plan          # allocation.csv, plan.md, plan.json
fasplan compare examples/campus.yaml --protocol both    # all strategies, all failure scenarios
fasplan scenario examples/campus.yaml --strategy maxagg --fail c-d1--c-d2 --router c-d2
fasplan serve                                           # dashboard on http://127.0.0.1:8000
fasplan experiment -o results --seeds 20                # campaign + results/figures/
fasplan lab examples/campus.yaml --strategy fasp -o lab/fasp   # containerlab files (see docs/testbed.md)
pytest
```

## Strategies compared

| Name | Allocation | Summaries |
|---|---|---|
| `flat` | conventional VLSM | none (every subnet advertised) |
| `maxagg` | conventional VLSM | one per building (maximum aggregation) |
| `shaikh` | conventional VLSM | Shaikh-style budgeted DP minimising normal-state path error, budget = proposed plan's prefix count |
| `fasp-sel` | conventional VLSM | failure-aware selection (ablation) |
| `fasp` | **failure-aware VLSM** | **failure-aware selection + simulator verification** (proposed) |

## Topology format

See `examples/campus.yaml`. Each router has a home area, and each building is
one area. A link takes its endpoints' area when they share one, otherwise it
is a backbone link. Subnets give host counts. `failures` lists the scenarios
the plan must survive: every single-link failure by default, plus `extra`
explicit sets and/or `double_link_sample: N`.

## Results (analytical, converged-state simulator)

**Worked example** (`examples/campus.yaml`, 10 routers, 15 LANs, 16 failure
scenarios; `results/campus-comparison.md`):

| OSPF | Prefixes | Routes/router | Lost pairs (failures) | Max stretch (normal) |
|---|---|---|---|---|
| No summarization | 15 | 15.0 | 0 | 1.00 |
| Maximum aggregation | 4 | 7.6 | 132 (5 scenarios) | 1.36 |
| Shaikh DP, same budget | 8 | 10.5 | 87 (3 scenarios) | 1.30 |
| **Proposed** | **8** | **10.7** | **0** | **1.00** |

**Campaign**: 180 generated campuses (3/4/6 buildings × low/mixed/high
redundancy × 20 seeds), planned against all single-link failures + 15 sampled
double failures and also tested on 30 *held-out* double failures. See
`results/summary.md` and `results/figures/`.

- On planned scenarios the proposed plan lost no traffic on any of the 180
  topologies, under both OSPF and EIGRP. Maximum aggregation lost traffic on
  106/180 and the Shaikh DP at the same table size on 96–100/180.
- It keeps 86% (OSPF) / 88% (EIGRP) of maximum aggregation's routing-table
  reduction (about 12 vs 26 routes per router).
- Normal-operation worst-case stretch is 1.09, against 1.23 for maximum
  aggregation.
- **Held-out double failures:** the proposed plan's loss rate is 0.08%,
  against 4.8% for maximum aggregation, but it is **not zero**. 21–23/180
  topologies lose some traffic in failures the planner was not given. The
  guarantee holds only for the scenarios you plan for.
- **Cost:** address-space utilisation falls from 69% to 45%, because
  failure-aware grouping pads blocks to powers of two.
- The emulation testbed (FRR / Cisco IOL) is generated and statically
  validated but **has not yet been run live**. See `docs/testbed.md`.
