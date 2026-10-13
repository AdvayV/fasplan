# Emulation testbed (containerlab)

The planner's results come from a converged-state simulator. The testbed runs the
same plans on real routing software and measures what a simulator cannot:
packet loss while the protocol converges, outage and recovery time, and
routing-control traffic.

> **Status:** the generated labs, configs and runner commands have been
> validated statically (unit tests parse every file and check addresses,
> interfaces and summaries; the runner is tested in `--dry-run`). **None of it
> has been deployed live yet**, because Docker needs root on the development
> machine. Run the steps below on a machine with root access and check the
> results before you rely on them.

## What gets generated

```
labs/fasp-ospf/
  campus-3b-fasp-ospf.clab.yml   containerlab topology (routers + probe host)
  configs/<router>/frr.conf       FRR: OSPF config  (+ daemons, vtysh.conf)
  configs/<router>/startup.cfg    IOL: IOS config
  lab.json                        manifest: containers, links/interfaces, probe, ping targets, scenarios
  plan.json                       the plan (allocation, summaries, decisions)
```

| Target | Protocols | Router image (override) |
|---|---|---|
| `frr` | OSPF | `quay.io/frrouting/frr:9.1.0` (`FASPLAN_FRR_IMAGE` at build time) |
| `iol` | OSPF, EIGRP | `${FASPLAN_IOL_IMAGE:=vrnetlab/cisco_iol:17.12.01}`, which containerlab expands at deploy time |

Probe and capture image: `nicolaka/netshoot:v0.13` (`FASPLAN_NETSHOOT_IMAGE`).

How the plan becomes router configuration:

* **Addresses:** every link is a /31 from the `p2p` space (`a` side gets the
  even address). Every router has a /32 loopback, which is also its router ID. Each
  LAN's gateway is its first host address. On FRR a LAN is a Linux `dummy`
  interface (`lan0`, `lan1`, …) created by containerlab `exec`. On IOL it is a
  `LoopbackN` interface with `ip ospf network point-to-point`, so OSPF advertises
  the whole prefix.
* **OSPF:** interfaces join the area of their link, LANs join the subnet's area,
  and LANs and the probe link are passive. Each summary becomes `area X range P`
  on every border router (ABR) of area X. Timers default to hello 1 s / dead 4 s.
  Link costs come from the topology (`ip ospf cost`).
* **EIGRP (IOL only):** AS 100. Each summary becomes
  `ip summary-address eigrp 100 P M` on every backbone interface of the
  building's border routers (`Topology.border_links`). Hello is 1 s and hold time 4 s.
  **Metric mapping:** every interface gets `bandwidth 1000000` and
  `delay 10*cost`. With classic metrics and the default K values the composite
  metric is `256*(10 + Σdelay)`, a linear function of total link cost, so
  route choice matches the planner. A summary's metric is the lowest of its
  components' metrics, which is also what the simulator assumes.
* **FRR and EIGRP:** this combination is refused on purpose. FRR's `eigrpd` is incomplete
  and has no interface summary-address, so use `--target iol`.
* **Probe:** a netshoot container on a /29 taken from the `p2p` space after the link
  /31s. It is attached to an access router outside the building with the most
  LANs (`b-acc` in the example), or to the router you pass with `--probe`. It routes the LAN,
  loopback and p2p spaces through that router. The ping targets are all LAN gateways
  except those on the probe's own router.

Interface names: in the topology file each router port uses the Linux name
(`eth1`, `eth2`, … in the order of `topo.links`). IOS sees port `ethN` as
`Ethernet{N//4}/{N%4}` (`eth1` is `Ethernet0/1`, `eth4` is `Ethernet1/0`), and
`Ethernet0/0` is management. That is the documented containerlab `cisco_iol`
convention as I understand it. **Check it against the containerlab version you
install.** If it differs, change `iol_ifname()` in
`src/fasplan/testbed/configgen.py`.

## Prerequisites

1. Linux with Docker. The runner calls `docker`, `ip netns`/`nsenter` and `tc`,
   so run it as root (`sudo -E`).
2. containerlab:
   `bash -c "$(curl -sL https://get.containerlab.dev)"` (see containerlab.dev/install).
3. Pull the images: `docker pull quay.io/frrouting/frr:9.1.0 nicolaka/netshoot:v0.13`.
   If that FRR tag is missing from quay.io, use another 9.x/10.x tag and set
   `FASPLAN_FRR_IMAGE` before `build`.
4. EIGRP only: a Cisco IOL image. IOL is Cisco software that is not freely
   redistributable. It comes with Cisco Modeling Labs (CML, including the
   Free/Personal tiers) as the `iol-xe`/`iol` node definition. Build a container from it with
   [vrnetlab](https://github.com/hellt/vrnetlab) (`cisco/iol`) as described in
   containerlab's `cisco_iol` docs, then `export FASPLAN_IOL_IMAGE=<your tag>`.
   containerlab's default IOL login is admin/admin. Override it with
   `FASPLAN_IOL_USER`/`FASPLAN_IOL_PASS` (used by `snapshot` through netmiko:
   `uv pip install -e '.[lab]'`).
5. Resources: the 10-router campus with FRR needs about 1–1.5 GB RAM. IOL needs about
   300–500 MB per router.

## Demo: max aggregation versus the proposed plan

```bash
cd ~/Projects/fasplan
R="sudo -E .venv/bin/python -m fasplan.testbed.runner"

# 1. build two labs from the example campus
$R build --topology examples/campus.yaml --strategy maxagg --protocol ospf --target frr --out labs/maxagg-ospf
$R build --topology examples/campus.yaml --strategy fasp   --protocol ospf --target frr --out labs/fasp-ospf

# 2. deploy max aggregation and wait for OSPF to converge
$R --lab labs/maxagg-ospf deploy
$R --lab labs/maxagg-ospf converge
$R --lab labs/maxagg-ospf snapshot        # routes per router, type-3 LSAs

# 3. cut the only link inside the Hostels building
$R --lab labs/maxagg-ospf fail-test --link c-d1--c-d2 --targets hostel-1,hostel-wifi,hostel-2,sports
#   expected (simulator prediction): hostel-2 and sports show blackholed=True for the whole
#   failure window. b-acc's traffic for 10.20.0.0/21 exits via c-d1, which keeps advertising
#   the summary and drops packets for c-d2's LANs at its discard route.
$R --lab labs/maxagg-ospf destroy

# 4. same failure with the proposed plan (two /22 summaries, one per side)
$R --lab labs/fasp-ospf deploy && $R --lab labs/fasp-ospf converge && $R --lab labs/fasp-ospf snapshot
$R --lab labs/fasp-ospf fail-test --link c-d1--c-d2 --targets hostel-1,hostel-wifi,hostel-2,sports
#   expected: short or no loss and no blackhole
$R --lab labs/fasp-ospf destroy
```

The **maxagg** addresses differ from the **fasp** ones (conventional and
failure-aware VLSM), so `--targets` takes subnet names, not IPs. During a live demo,
`docker exec -it clab-campus-3b-maxagg-ospf-c-d1 vtysh -c "show ip route 10.20.0.0/21"`
shows the `blackhole` discard route installed by the area range.

### Measuring a failure

`fail-test` does the following:

1. Starts one `tcpdump` container per router that shares the router's network namespace
   (`--net container:<router>`) and captures `ip proto 89 or ip proto 88`.
2. Starts `ping -D -i 0.01 -W 1` from the probe to every target.
3. After `--pre` seconds, takes the link(s) down on both ends.
   * `--mode carrier`: `ip link set down` (the peer sees loss of carrier at once).
   * `--mode silent`: `tc netem loss 100%` (detected only by the dead/hold timer).
4. Restores the link after `--hold` seconds and stops after `--post` seconds.

For each target it reports:

| Field | Meaning |
|---|---|
| `loss_pct` | percentage of probes lost over the whole window |
| `outage_ms` | longest run of missing replies × interval |
| `recovery_ms` | from the failure to the first reply after the first loss |
| `blackholed` | no reply returned before the link was restored |
| `ctrl_pkts` / `ctrl_bytes` | OSPF/EIGRP packets the routers *sent* during the window, each packet counted once (Linux-cooked capture, outgoing only) |
| `ctrl_update_pkts` | same, excluding hellos |

Rows are appended to `<lab>/results.csv`. The pcaps and raw ping output stay in
`<lab>/captures/`.

### Full campaign

```bash
for s in flat maxagg shaikh fasp; do
  $R build --topology examples/campus.yaml --strategy $s --protocol ospf --target frr --out labs/$s-ospf
done
$R campaign --plans labs/*-ospf --repeat 10 --mode carrier --out results/testbed-ospf.csv
```

`campaign` handles each lab in turn: deploy, converge, snapshot, then every
scenario in `lab.json` (all single-link failures plus the extra double failures
in the topology file) `--repeat` times, then destroy. It writes these columns:
`strategy, protocol, scenario, run, target, loss_pct, outage_ms, ctrl_pkts,
ctrl_bytes, routes_total, recovery_ms, blackholed, ctrl_update_pkts,
ctrl_update_bytes, mode, target_ip, lab`.

For EIGRP, repeat with `--protocol eigrp --target iol`.

`--dry-run` prints every command without running it (no root or Docker needed):

```bash
.venv/bin/python -m fasplan.testbed.runner --lab labs/maxagg-ospf --dry-run --sudo never fail-test --link c-d1--c-d2
```

## Caveats to check on first live run

* **Image tags.** `quay.io/frrouting/frr:9.1.0` and `nicolaka/netshoot:v0.13` are
  pinned from memory. Pull them before the demo.
* **IOL interface naming and startup config.** As far as I know, containerlab merges
  `startup-config` into the base config it generates for management and SSH.
  Check that the `Ethernet` names inside IOS match the endpoints.
* **IOL failure detection.** Taking down the Linux `ethN` inside an IOL container may
  not bring the IOS interface down. If so, IOS detects the failure only through the
  hello/hold timers (4 s), the same as `--mode silent`.
* **Network namespaces.** containerlab normally registers `/var/run/netns/clab-<lab>-<node>`.
  If it doesn't, the runner falls back to `nsenter -t <pid> -n`.
* **Route counts include infrastructure.** The emulated routing tables include the /31
  link prefixes, the loopbacks and the probe /29, so absolute route counts are higher
  than the simulator's LAN-only counts. The differences between strategies are
  unaffected.
* **Summary cost.** FRR and IOS compute an area range's cost as the largest component cost
  (RFC 2328), which is what the simulator assumes. The LAN interface cost (FRR 10,
  IOS 1) adds the same constant to every component, so it does not change which exit
  is chosen.
