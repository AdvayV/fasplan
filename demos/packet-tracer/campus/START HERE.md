# Fasplan campus in Cisco Packet Tracer

Start with **Campus-Proposed.pkt** in Packet Tracer 9.0.1. These native files were
generated from `fasplan/examples/campus.yaml` and its OSPF address/summary plans.
The structure has been checked, and the proposed-plan diagram was confirmed
visible in Packet Tracer 9.0.1. Alternate-file loading, OSPF convergence, and
live ping results still need checking; expected paths below are predictions
from Fasplan, not observed Packet Tracer measurements.

## What is included

- 10 routers preserving all 14 original router links and their OSPF costs/areas.
- 15 LANs, each with a 2960 switch and one sample PC (40 devices, 44 cables).
- Individual VLSM masks sized for the original host counts, not 15 /24 LANs.
- Multi-area OSPF and ABR `area range` commands from each Fasplan plan.
- Proposed and Maximum Aggregation versions, plus a link-down copy of each.
- Per-router CLI text, PC addressing tables, and a router port/wiring table.

Router-PT generic routers have five copper FastEthernet modules so the topology
does not need extra serial WAN links. Router links use /30 subnets instead of
Fasplan's /31 allocation for compatibility; LAN prefixes and summary ranges come
from the actual plans. This change does not change link costs or connectivity.
One PC represents a subnet sized for the documented number of hosts.

## First packet: Engineering to Hostels

1. Open `Campus-Proposed.pkt`. Zoom out to see the whole campus.
2. Stay in **Realtime** while OSPF settles. Wait about a minute or use the
   **Fast Forward Time** control. Router links should become green. A first ping
   may time out while ARP resolves; repeat it.
3. Click PC **eng-servers**, then **Desktop > Command Prompt**.
4. Run `ping 10.20.16.2` (PC **hostel-1**).
5. Run `tracert 10.20.16.2` to inspect the router path.
6. To watch packets, select **Simulation**. The saved event filters select
   ARP and ICMP. Use **Add Simple PDU** (closed envelope), click `eng-servers`,
   then `hostel-1`. Use **Capture/Forward** to step through each event.

Source eng-servers: `10.20.2.2`. Destination hostel-1:
`10.20.16.2`. Check `PC Addresses-fasp.csv` for all other PCs.

## Failure comparison

Open **Campus-Proposed-Hostel-Link-Down.pkt**, wait for OSPF, and repeat the ping.
Fasplan predicts the router path `a-d2 -> core2 -> core1 -> c-d1`, then hostel-1.
The two hostel interfaces are administratively shut, preserving the cable so
the failed link remains visible.

Open **Campus-Maximum-Aggregation-Hostel-Link-Down.pkt**. Use `eng-servers`
to ping its `hostel-1` PC at `10.20.0.2`. This file has the
maximum-aggregation address allocation, so its destination may differ from the
proposed file. Fasplan predicts `a-d2 -> core2 -> c-d2`, then a discard caused
by a broad summary. Confirm the behavior in Packet Tracer rather than assuming
the simplified simulator and Packet Tracer implement every IOS detail equally.

To compare normal operation, open **Campus-Maximum-Aggregation.pkt**.

## Fail or restore the link yourself

In a normal file, click router `c-d1`, open **CLI**, press Enter, then enter:

```text
enable
configure terminal
interface FastEthernet1/0
shutdown
end
```

This disables the link to `c-d2`. After convergence, repeat the ping and trace.
Restore with `no shutdown` in the same interface configuration.
In the prebuilt link-down files, both ends are shut: apply `no shutdown` to
`c-d1 FastEthernet1/0` AND `c-d2 FastEthernet1/0` to restore it.

Useful router commands:

```text
enable
show ip interface brief
show ip ospf neighbor
show ip route
show running-config
```

## Scope

This is the campus implementation of the exported plans. Fasplan remains the
address planner and strategy evaluator. Its browser controls and automatic
comparison table are not embedded in Packet Tracer. Packet Tracer supplies
the simulated devices, IOS routing and packet animation. OSPF is configured;
EIGRP is not configured in this first lab. Wi-Fi-named LANs are represented by
wired sample hosts; access points and hundreds of individual hosts are not
required to demonstrate routing between those subnets.

## Generator provenance

Native encoding and device templates: [packet-tracer-pkt-format](https://github.com/aacanadaa/packet-tracer-pkt-format),
GPL-3.0-or-later, downloaded in the separate `FasplanReportWork` work folder.
No third-party generator source was copied into Fasplan. This is an independent
file-format generator, not a Cisco-supported YAML import command.


## Simulation buffer full

Choose **Clear Event List** at the warning. Stop Auto Capture/Play. In
**Options > Preferences > Miscellaneous**, enable **Buffer Filtered Events Only**.
The saved campus files now enable this setting. In **Edit Filters**, keep ARP
and ICMP selected. Use **Capture/Forward** and stop after the ping finishes.
**Reset Simulation** clears the captured-event list while retaining device
routing/ARP tables; it keeps user-created PDUs, so delete unwanted or periodic
PDUs from the User Created PDU List before creating one Simple PDU.
