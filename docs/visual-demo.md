# Visual network demos

Open the dashboard and choose Campus network, Branch office or Data centre.
Each example selects a source router and destination subnet automatically.

Three graphs show the same flow and failures:

- Selected strategy: the method chosen in the top dropdown.
- Maximum aggregation: the baseline that combines each building into one route.
- Proposed plan: the failure-aware design.

Blue arrows show forwarding direction. Numbered hops and the route strip show the router order.
The packet marker moves when you press Replay path; Next hop advances one step.
A red stop means the flow is discarded or otherwise cannot continue. A green endpoint means delivery.
Playback is an illustration of computed forwarding, not a measurement of routing convergence time.

## Example failures

| Example | Default flow | Failure |
| --- | --- | --- |
| Campus network | a-d2 to hostel-1 | c-d1--c-d2 |
| Branch office | client to checkout | branch-left--branch-right |
| Data centre | client to database | dc-left--dc-middle and dc-left--dc-right |

Press Apply example failure, then Replay path. Use Restore all links to compare normal operation.
These failures are included in each example's planning scenarios. Their selected flows are delivered
by both strategies normally; under the example failure, maximum aggregation blackholes the flow
and the proposed plan delivers it. These checks run under OSPF and EIGRP in tests/test_web.py.

The graph library is bundled locally at its existing pinned version, so the dashboard no longer
requires a CDN connection. Its embedded MIT licence and attribution are retained.
