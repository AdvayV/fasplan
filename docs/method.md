# Method: failure-aware subnet design and route summarization

This file describes what `fasplan` computes, in the form you would put in the
report's methodology section.

## 1. Problem

A campus has buildings `X1..Xn`, each an OSPF area (or EIGRP summarization
domain) behind its *border routers*: the ABRs, or the distribution routers whose
backbone interfaces carry `ip summary-address eigrp`. Each building `X` has
departmental subnets `S_X`, each with a host count and an attached router.
`F` is a set of failure scenarios: no failure, every single-link failure, and
chosen or sampled double failures.

The output is:

1. a VLSM allocation `addr(s)` for every subnet, and
2. a partition of each `S_X` into *advertised groups*. A group with one subnet
   is advertised as is. A larger group is advertised as the smallest prefix
   covering it (an OSPF `area X range` / EIGRP summary on every border).

The goals are to minimise the number of advertised prefixes and to satisfy, in
every scenario `f in F`:

- **(C1) no summarization-induced loss**: if per-subnet routing would deliver
  traffic from router `R` to subnet `s`, the plan must deliver it too;
- **(C2) bounded stretch**: path cost is at most `tau_n` (normal) or `tau_f`
  (failure) times the per-subnet routing cost.

## 2. Why summaries fail

A border router keeps advertising a summary, and keeps a discard (Null0) route
for it, as long as **any** component is reachable inside its building. In OSPF
the range stays *active* (RFC 2328 §12.4.3), and the ABR ignores identical
summaries from other ABRs (§16.2). In EIGRP the discard route (AD 5) overrides
any learned copy. If a failure cuts border `B` off from some members of a
group but not all, routers that pick `B` as their exit for the summary send
traffic for the cut-off members into `B`'s discard route. Per-subnet routing
would not lose this traffic: the specific route disappears from `B`, and the
backbone carries traffic to the other border (inter-area routes repair a
partitioned area). The base paper's trade-off, fewer routes against less
accurate path selection, therefore has a second, failure-time dimension that
the paper does not consider.

Summary cost also differs by protocol. OSPF uses the **maximum** component
cost and EIGRP the **minimum** component metric, so the same group can be safe
under one protocol and stretch paths under the other.

## 3. Scoring a candidate group

For building `X`, border set `B_X`, and scenario `f`:

- `d_f(b, s)`: shortest intra-building distance from border `b` to subnet `s`
  (infinity if cut off);
- `D_f(R, b)`: distance from a remote router `R` to border `b` without using
  building `X`'s links.

Summarizing group `G` makes each border with a reachable member advertise cost
`c_f(b) = agg_{s in G, d_f(b,s) < inf} d_f(b,s)`, where `agg` is `max` (OSPF)
or `min` (EIGRP). Remote router `R` exits via
`b* = argmin_b D_f(R,b) + c_f(b)`. For every member `s` that per-subnet
routing reaches (`ref = min_b D_f(R,b) + d_f(b,s) < inf`):

- `d_f(b*, s) = inf` is a **blackhole** and violates C1;
- otherwise `stretch = (D_f(R,b*) + d_f(b*,s)) / ref` must satisfy C2.

`proxy.DomainContext.assess` computes this in O(|F|·|R|·(|B|+|G|)) and caches
the result per group.

## 4. Failure-aware VLSM (`fasp` allocation)

Summary selection can only choose prefixes that the address plan makes
contiguous, so the method first decides where subnets go:

1. **Equivalence groups.** Subnets with identical vectors
   `(d_f(b,s))_{f,b}` behave identically (normally: same attached router).
   Each group gets one aligned block.
2. **Signature classes.** The *reachability signature*
   `sig(s) = ( {b : d_f(b,s) < inf} )_{f in F}` lists which borders can reach
   `s` in each scenario. Subnets with equal signatures can never blackhole each
   other, so each class gets one aligned block.
3. **Class tree.** Classes are merged bottom-up (complete linkage) by Hamming
   distance between signatures, breaking ties by padding waste. The resulting
   binary tree is the layout: siblings sit under a common parent prefix, so
   compatible classes can still be summarized together when the check allows
   it.

Blocks are placed largest first. Every block is a power of two, so every group
is aligned and can become one prefix. The cost is address space: utilisation
falls (reported as `addr_util`). This trade-off should be stated in the paper.

## 5. Summary selection

**Top-down selection (proposed).** Recursion over the address trie of each
building block: if the group under the tightest covering prefix passes C1 and
C2 for all `f`, advertise it as one summary. Otherwise split the prefix into
its two halves and recurse. Because validity is checked per node and the
subtrees are independent, this gives the minimum number of prefixes that
satisfies the per-node constraints for the given allocation.

**Verification and split.** The proxy approximates path selection: it ignores
transit-area restrictions and EIGRP relaying through inactive summarizers. The
plan is therefore re-checked with the full converged-state simulator
(`routing.py`) in every scenario. Any summary whose members lose traffic that
per-subnet routing delivers is forbidden and re-split, until no loss remains.

**Budgeted DP (Shaikh-style baseline).** For a budget `k`, a dynamic programme
over the same trie minimises total extra path cost in normal operation. Each
node gets a table `k -> min error`, built by combining summarize-here against
the min-plus product of its children; a knapsack then combines buildings. With
`k` set to the proposed plan's prefix count, this compares the two methods at
**equal routing-table size**: same table size, failure-aware or not.

## 6. Simulator semantics (summary)

| | OSPF | EIGRP |
|---|---|---|
| summarization point | ABR (`area X range`) | border interfaces (`ip summary-address`) |
| summary cost | max component cost | min component metric |
| summary kept while | any component reachable intra-area | any component in table |
| discard route | yes, on active ranges | yes (AD 5) |
| partition repair | inter-area routes via backbone, unless a range hides the specifics | specifics via core, unless suppressed |
| path choice | intra > inter; lowest cost; ABRs use backbone summaries only | lowest additive metric (stand-in for delay) |

Simplifications: single next hop (no ECMP), additive EIGRP metric, no virtual
links, no NSSA/stub areas, LAN cost 0. The containerlab testbed checks the
model against real FRR/IOS behaviour.

## 7. Metrics

| Metric | Meaning |
|---|---|
| `adverts` | prefixes advertised by the plan (summaries + separate subnets) |
| `routes_mean/max` | LAN prefixes in each router's table, normal operation (discard routes counted) |
| `ctrl_prefixes` | OSPF: type-3 LSAs across areas. EIGRP: prefixes carried on all adjacencies |
| `fail_lost_pairs`, `fail_loss_rate` | (router, subnet) pairs delivered by per-subnet routing but dropped by the plan, summed over failure scenarios |
| `scenarios_with_loss` | scenarios in which the plan loses any pair |
| `stretch_*` | path cost relative to per-subnet routing under the same failures |
| `churn_mean` | routing-table entries that change when a failure happens (a proxy for update traffic) |
| `addr_util` | allocated subnet space / building blocks |
| reduction retained | `(flat − plan) / (flat − maxagg)` on routes per router |

The testbed adds measured packet loss, outage duration (recovery time), and
control-packet counts and bytes during the failure.

## 8. Threats to validity (write these up honestly)

- The same scenario set `F` is used for planning and evaluation. Evaluate
  sampled double failures that were *not* in the planning set before claiming
  generalisation (`failures.double_link_sample` plus a separate seed).
- Generated topologies follow one campus template, so vary the profiles and
  report results per profile.
- The OSPF/EIGRP model is converged-state. Transient loss during convergence is
  measured only in the testbed.
- Novelty must be checked against FIB aggregation (e.g. ORTC), later
  OSPF/IS-IS summarization work, and work on summarization-induced blackholes.
