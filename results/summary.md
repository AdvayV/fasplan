# Experiment summary

180 generated topologies; mean ± 95% CI across topologies.

## EIGRP

| Strategy | Advertised prefixes | Routes / router | Control-plane prefixes | Failure loss rate | Scenarios with loss | Held-out double-failure loss rate | Max stretch (normal) | p95 stretch (failures) | FIB churn / failure | Address utilisation | Reduction retained |
|---|---|---|---|---|---|---|---|---|---|---|---|
| No summarization | 25.7 ± 1.3 | 25.7 ± 1.3 | 1.1e+03 ± 1.1e+02 | 0 ± 0 | 0 ± 0 | 0 ± 0 | 1 ± 0 | 1 ± 0 | 63.9 ± 4.4 | 0.691 ± 0.011 | 0% ± 0% |
| Maximum aggregation | 5.33 ± 0.18 | 9.93 ± 0.27 | 324 ± 29 | 0.0451 ± 0.011 | 10.4 ± 2.3 | 0.0471 ± 0.011 | 1.21 ± 0.023 | 1.06 ± 0.01 | 15.3 ± 0.82 | 0.691 ± 0.011 | 100% ± 0% |
| Shaikh DP (failure-unaware) | 7.46 ± 0.4 | 11.7 ± 0.42 | 395 ± 33 | 0.0245 ± 0.0073 | 6.62 ± 1.8 | 0.0262 ± 0.0072 | 1.17 ± 0.02 | 1.04 ± 0.0079 | 20.9 ± 1.5 | 0.691 ± 0.011 | 88% ± 2% |
| Failure-aware selection only | 10.8 ± 0.81 | 14.4 ± 0.72 | 502 ± 44 | 0 ± 0 | 0 ± 0 | 0.000702 ± 0.00038 | 1.09 ± 0.012 | 1.02 ± 0.0049 | 29.8 ± 2.7 | 0.691 ± 0.011 | 71% ± 4% |
| Proposed (failure-aware design) | 7.46 ± 0.4 | 11.8 ± 0.42 | 394 ± 33 | 0 ± 0 | 0 ± 0 | 0.000816 ± 0.0004 | 1.09 ± 0.012 | 1.02 ± 0.0051 | 21.4 ± 1.5 | 0.451 ± 0.017 | 88% ± 2% |

Topologies where the strategy lost traffic in at least one failure scenario:

- No summarization: 0/180 on planned scenarios, 0/180 on held-out double failures
- Maximum aggregation: 106/180 on planned scenarios, 110/180 on held-out double failures
- Shaikh DP (failure-unaware): 100/180 on planned scenarios, 103/180 on held-out double failures
- Failure-aware selection only: 0/180 on planned scenarios, 20/180 on held-out double failures
- Proposed (failure-aware design): 0/180 on planned scenarios, 23/180 on held-out double failures

## OSPF

| Strategy | Advertised prefixes | Routes / router | Control-plane prefixes | Failure loss rate | Scenarios with loss | Held-out double-failure loss rate | Max stretch (normal) | p95 stretch (failures) | FIB churn / failure | Address utilisation | Reduction retained |
|---|---|---|---|---|---|---|---|---|---|---|---|
| No summarization | 25.7 ± 1.3 | 25.7 ± 1.3 | 223 ± 20 | 0 ± 0 | 0 ± 0 | 0 ± 0 | 1 ± 0 | 1 ± 0 | 62.1 ± 4.3 | 0.691 ± 0.011 | 0% ± 0% |
| Maximum aggregation | 5.33 ± 0.18 | 9.93 ± 0.27 | 45.6 ± 3.5 | 0.0456 ± 0.011 | 10.4 ± 2.3 | 0.0477 ± 0.011 | 1.23 ± 0.023 | 1.06 ± 0.01 | 18.6 ± 0.9 | 0.691 ± 0.011 | 100% ± 0% |
| Shaikh DP (failure-unaware) | 7.76 ± 0.4 | 12 ± 0.42 | 66.4 ± 5.8 | 0.0219 ± 0.0067 | 5.96 ± 1.7 | 0.0237 ± 0.0067 | 1.18 ± 0.021 | 1.04 ± 0.0077 | 24.6 ± 1.5 | 0.691 ± 0.011 | 86% ± 2% |
| Failure-aware selection only | 11.4 ± 0.81 | 14.9 ± 0.73 | 97.5 ± 9.9 | 0 ± 0 | 0 ± 0 | 0.000671 ± 0.00039 | 1.09 ± 0.012 | 1.02 ± 0.0045 | 33 ± 2.6 | 0.691 ± 0.011 | 68% ± 4% |
| Proposed (failure-aware design) | 7.76 ± 0.4 | 12 ± 0.42 | 66.4 ± 5.8 | 0 ± 0 | 0 ± 0 | 0.000761 ± 0.0004 | 1.09 ± 0.012 | 1.02 ± 0.0046 | 24.3 ± 1.5 | 0.451 ± 0.017 | 86% ± 2% |

Topologies where the strategy lost traffic in at least one failure scenario:

- No summarization: 0/180 on planned scenarios, 0/180 on held-out double failures
- Maximum aggregation: 106/180 on planned scenarios, 110/180 on held-out double failures
- Shaikh DP (failure-unaware): 96/180 on planned scenarios, 99/180 on held-out double failures
- Failure-aware selection only: 0/180 on planned scenarios, 17/180 on held-out double failures
- Proposed (failure-aware design): 0/180 on planned scenarios, 21/180 on held-out double failures
