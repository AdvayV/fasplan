# Strategy comparison: campus-3b

## OSPF

| Strategy | Prefixes advertised | Routes/router | Ctrl prefixes | Lost pairs (failures) | Scenarios with loss | Max stretch (normal) | Max stretch (failures) | FIB churn/failure | Addr utilisation |
|---|---|---|---|---|---|---|---|---|---|
| No summarization | 15 | 15.0 | 90 | 0 | 0 | 1.0 | 1.0 | 22.625 | 0.8154 |
| Maximum aggregation | 4 | 7.6 | 24 | 132 | 5 | 1.3636 | 1.3636 | 9.688 | 0.8154 |
| Shaikh DP (failure-unaware) | 8 | 10.5 | 48 | 87 | 3 | 1.3 | 1.3 | 14.125 | 0.8154 |
| Failure-aware selection only | 12 | 13.1 | 72 | 0 | 0 | 1.0 | 1.25 | 19.125 | 0.8154 |
| Proposed (failure-aware design) | 8 | 10.7 | 48 | 0 | 0 | 1.0 | 1.25 | 13.812 | 0.5464 |

## EIGRP

| Strategy | Prefixes advertised | Routes/router | Ctrl prefixes | Lost pairs (failures) | Scenarios with loss | Max stretch (normal) | Max stretch (failures) | FIB churn/failure | Addr utilisation |
|---|---|---|---|---|---|---|---|---|---|
| No summarization | 15 | 15.0 | 285 | 0 | 0 | 1.0 | 1.0 | 22.812 | 0.8154 |
| Maximum aggregation | 4 | 7.6 | 103 | 111 | 5 | 1.3636 | 1.3636 | 6.562 | 0.8154 |
| Shaikh DP (failure-unaware) | 8 | 10.5 | 172 | 72 | 3 | 1.3 | 1.3 | 13.125 | 0.8154 |
| Failure-aware selection only | 12 | 13.1 | 240 | 0 | 0 | 1.0 | 1.2273 | 19.375 | 0.8154 |
| Proposed (failure-aware design) | 8 | 10.7 | 170 | 0 | 0 | 1.0 | 1.2273 | 13.5 | 0.5464 |
