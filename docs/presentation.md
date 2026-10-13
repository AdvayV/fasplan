# Presenting the project

## What you are presenting (three pieces of software)

| Piece | Role in the talk | Command |
|---|---|---|
| `fasplan` planner + CLI | the contribution: VLSM + failure-aware summaries | `fasplan plan examples/campus.yaml` |
| Web dashboard | live demo: click a link to fail it, watch blackholes | `fasplan serve` → http://127.0.0.1:8000 |
| Analytical experiments | evidence across many topologies | `fasplan experiment -o results` |
| containerlab testbed | evidence on real routing software (FRR / Cisco IOL) | `fasplan lab ...` + `python -m fasplan.testbed.runner` (see testbed.md) |

## Suggested slide flow (12–15 min)

1. **Problem** (1 slide): routing tables grow with subnets, and summarization
   shrinks them. Show the campus figure from the dashboard.
2. **The hidden failure** (2 slides): Hostel area, `c-d1--c-d2` fails. Max
   aggregation keeps advertising `10.20.0.0/21` from both ABRs, so `c-d2`
   discards traffic for hostel-1. Show the output of
   `fasplan scenario examples/campus.yaml --strategy maxagg --fail c-d1--c-d2 --router c-d2`
   (the FIB with its `discard` line).
3. **Base paper** (1 slide): Shaikh et al. 2003 trade routes for path accuracy
   with a DP over the address trie. It considers normal operation only.
4. **Method** (3 slides): reachability signatures, then failure-aware VLSM,
   then top-down selection, then simulator verification. Use the "Why" tab of
   the dashboard, whose reasons are written in plain English.
5. **Results, worked example** (1 slide): table from
   `results/campus-comparison.md`: 15 → 8 prefixes, zero loss vs 132 lost pairs
   for max aggregation and 87 for the Shaikh DP at the same 8-prefix budget.
6. **Results, many topologies** (2 slides): `results/figures/tradeoff_routes_vs_loss.png`,
   `reduction_retained.png`, `holdout_loss.png`.
7. **Testbed validation** (1 slide): measured ping loss / outage for `maxagg`
   vs `fasp` on FRR when `c-d1--c-d2` fails.
8. **Costs and limits** (1 slide): address utilisation drops
   (`address_utilisation.png`); planning is offline; single next hop model;
   further novelty checks needed.
9. **Conclusion + future work**: online re-planning, IS-IS, IPv6, ECMP.

## Live demo script (dashboard)

1. `fasplan serve`, open the page, pick `campus.yaml`, and press Analyze.
2. Comparison tab: point at the routes column, then the loss column.
3. Select strategy *Maximum aggregation*, click the `c-d1 — c-d2` edge. Red
   sources appear, and the drop router `c-d2` is highlighted. Click `c-d2` to
   show its FIB with the discard route.
4. Switch to *Proposed*. Same failure, zero loss.
5. "Why" tab: read one split reason aloud.
6. Export tab: download FRR configs. These are what the testbed runs.

Keep a screen recording of the demo as a fallback.

## Viva questions to prepare for

- *Why not just never summarize?* Table size, LSA flooding scope and churn:
  show the `ctrl_prefixes` and `churn_mean` columns.
- *Isn't this solved by a link between the ABRs?* Often yes, and the method
  then summarizes the building fully (Engineering in the example). The
  contribution is knowing *when* and doing it automatically.
- *How does OSPF choose among ABR summaries?* Max component cost (RFC 2328
  §12.4.3). EIGRP uses the min component metric, which is why results differ.
- *Is the simulator trustworthy?* Unit tests encode the RFC behaviour, and
  the testbed checks it on FRR/IOS.
- *Do failures you didn't plan for break it?* The held-out double-failure
  results answer this.
