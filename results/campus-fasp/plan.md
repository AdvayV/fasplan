# Address and summary plan: campus-3b

Strategy **Proposed (failure-aware design)**, protocol **OSPF**, allocation **failure-aware**, address utilisation 55%.

## Building blocks

- area 0: `10.20.24.0/26`
- area 1: `10.20.0.0/21`
- area 2: `10.20.8.0/21`
- area 3: `10.20.16.0/21`

## Subnet allocation (VLSM)

| Subnet | Area | Router | Hosts | Prefix | Mask | Gateway | Usable range | Broadcast |
|---|---|---|---|---|---|---|---|---|
| cse-lab | 1 | a-acc | 200 | `10.20.0.0/24` | 255.255.255.0 | 10.20.0.1 | 10.20.0.2 - 10.20.0.254 | 10.20.0.255 |
| ece-lab | 1 | a-acc | 120 | `10.20.1.0/25` | 255.255.255.128 | 10.20.1.1 | 10.20.1.2 - 10.20.1.126 | 10.20.1.127 |
| faculty-eng | 1 | a-acc | 60 | `10.20.1.128/26` | 255.255.255.192 | 10.20.1.129 | 10.20.1.130 - 10.20.1.190 | 10.20.1.191 |
| eng-servers | 1 | a-d2 | 25 | `10.20.2.0/27` | 255.255.255.224 | 10.20.2.1 | 10.20.2.2 - 10.20.2.30 | 10.20.2.31 |
| eng-wifi | 1 | a-d1 | 400 | `10.20.4.0/23` | 255.255.254.0 | 10.20.4.1 | 10.20.4.2 - 10.20.5.254 | 10.20.5.255 |
| library | 2 | b-acc | 150 | `10.20.8.0/24` | 255.255.255.0 | 10.20.8.1 | 10.20.8.2 - 10.20.8.254 | 10.20.8.255 |
| admin | 2 | b-acc | 80 | `10.20.9.0/25` | 255.255.255.128 | 10.20.9.1 | 10.20.9.2 - 10.20.9.126 | 10.20.9.127 |
| accounts | 2 | b-acc | 40 | `10.20.9.128/26` | 255.255.255.192 | 10.20.9.129 | 10.20.9.130 - 10.20.9.190 | 10.20.9.191 |
| admin-wifi | 2 | b-d1 | 250 | `10.20.10.0/24` | 255.255.255.0 | 10.20.10.1 | 10.20.10.2 - 10.20.10.254 | 10.20.10.255 |
| cctv | 2 | b-d2 | 30 | `10.20.12.0/27` | 255.255.255.224 | 10.20.12.1 | 10.20.12.2 - 10.20.12.30 | 10.20.12.31 |
| hostel-1 | 3 | c-d1 | 300 | `10.20.16.0/23` | 255.255.254.0 | 10.20.16.1 | 10.20.16.2 - 10.20.17.254 | 10.20.17.255 |
| hostel-wifi | 3 | c-d1 | 500 | `10.20.18.0/23` | 255.255.254.0 | 10.20.18.1 | 10.20.18.2 - 10.20.19.254 | 10.20.19.255 |
| hostel-2 | 3 | c-d2 | 300 | `10.20.20.0/23` | 255.255.254.0 | 10.20.20.1 | 10.20.20.2 - 10.20.21.254 | 10.20.21.255 |
| sports | 3 | c-d2 | 50 | `10.20.22.0/26` | 255.255.255.192 | 10.20.22.1 | 10.20.22.2 - 10.20.22.62 | 10.20.22.63 |
| datacenter | 0 | core1 | 60 | `10.20.24.0/26` | 255.255.255.192 | 10.20.24.1 | 10.20.24.2 - 10.20.24.62 | 10.20.24.63 |

## Advertised prefixes

### Area 1 (border routers: a-d1, a-d2)

- **summary** `10.20.0.0/22` covers cse-lab, ece-lab, eng-servers, faculty-eng
- subnet `10.20.4.0/23` (eng-wifi) advertised separately

### Area 2 (border routers: b-d1, b-d2)

- **summary** `10.20.8.0/23` covers accounts, admin, library
- subnet `10.20.10.0/24` (admin-wifi) advertised separately
- subnet `10.20.12.0/27` (cctv) advertised separately

### Area 3 (border routers: c-d1, c-d2)

- **summary** `10.20.16.0/22` covers hostel-1, hostel-wifi
- **summary** `10.20.20.0/22` covers hostel-2, sports

## Why: summary decisions

- `10.20.0.0/21` [cse-lab, ece-lab, eng-servers, eng-wifi, faculty-eng]: **split** — fail a-d1--a-acc + a-d1--a-d2: a-d1 still advertises the summary but cannot reach cse-lab, so traffic from b-acc is dropped
- `10.20.0.0/22` [cse-lab, ece-lab, eng-servers, faculty-eng]: **kept**
- `10.20.8.0/21` [accounts, admin, admin-wifi, cctv, library]: **split** — normal: traffic from a-d2 to cctv exits via b-d1, cost 26 vs 20 (x1.30)
- `10.20.8.0/22` [accounts, admin, admin-wifi, library]: **split** — fail b-d1--b-acc: b-d1 still advertises the summary but cannot reach accounts, so traffic from a-acc is dropped
- `10.20.8.0/23` [accounts, admin, library]: **kept**
- `10.20.16.0/21` [hostel-1, hostel-2, hostel-wifi, sports]: **split** — normal: traffic from a-acc to hostel-2 exits via c-d1, cost 30 vs 22 (x1.36)
- `10.20.16.0/22` [hostel-1, hostel-wifi]: **kept**
- `10.20.20.0/22` [hostel-2, sports]: **kept**
