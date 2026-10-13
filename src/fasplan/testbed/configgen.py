"""Render a containerlab lab (topology file, router configs, manifest) from a plan.

Targets
  frr   FRRouting containers, OSPF only (multi-area, `area X range` on ABRs).
  iol   Cisco IOL (containerlab kind `cisco_iol`), OSPF or EIGRP.

Every router interface keeps the Linux name containerlab gives it (eth1, eth2,
...) in the topology file.  On IOL the same port appears inside IOS as
Ethernet<slot>/<port> with four ports per slot and Ethernet0/0 reserved for
management, so eth1 -> Ethernet0/1, eth4 -> Ethernet1/0.

EIGRP metric mapping (IOL): every interface gets `bandwidth 1000000` and
`delay 10*cost` (delay is in tens of microseconds).  With classic metrics and
default K values the composite metric is 256*(10 + sum(delay)), a linear
function of the summed link cost, so path choice matches the planner's
additive cost model.
"""

from __future__ import annotations

import ipaddress
import json
import math
import os
import re
from pathlib import Path

import yaml
from jinja2 import Environment, FileSystemLoader, StrictUndefined

from ..model import Topology
from ..plan import Plan

FRR_IMAGE = os.environ.get("FASPLAN_FRR_IMAGE", "quay.io/frrouting/frr:9.1.0")
NETSHOOT_IMAGE = os.environ.get("FASPLAN_NETSHOOT_IMAGE", "nicolaka/netshoot:v0.13")
# containerlab expands ${VAR:=default} in topology files, so the user can point
# at their own IOL image without regenerating the lab.
IOL_IMAGE = "${FASPLAN_IOL_IMAGE:=vrnetlab/cisco_iol:17.12.01}"

EIGRP_AS = 100
PROBE = "probe"
TARGETS = ("frr", "iol")

_env = Environment(loader=FileSystemLoader(Path(__file__).parent / "templates"),
                   undefined=StrictUndefined, trim_blocks=True, lstrip_blocks=True,
                   keep_trailing_newline=True)


# ------------------------------------------------------------------ naming
def iol_ifname(n: int) -> str:
    """Linux port number (eth<n>, n >= 1) -> IOS interface name."""
    return f"Ethernet{n // 4}/{n % 4}"


def linux_ifnames(topo: Topology, probe_router: str | None = None) -> dict[str, dict[str, str]]:
    """router -> {link_id | 'probe': ethN}; numbering follows topo.links order."""
    out: dict[str, dict[str, str]] = {}
    for r in sorted(topo.routers):
        m = {l.id: f"eth{i}" for i, l in enumerate(topo.incident(r), start=1)}
        if r == probe_router:
            m["probe"] = f"eth{len(topo.incident(r)) + 1}"
        out[r] = m
    return out


def interface_map(topo: Topology, target: str, probe_router: str | None = None) -> dict[str, dict[str, str]]:
    """router -> {link_id: ifname, 'lan:<subnet>': ifname, ['probe': ifname]}
    using the names the router OS sees (FRR: ethN / lanN, IOS: EthernetX/Y / LoopbackN)."""
    if target not in TARGETS:
        raise ValueError(f"unknown target {target!r}; choose from {TARGETS}")
    lin = linux_ifnames(topo, probe_router)
    out: dict[str, dict[str, str]] = {}
    for r in sorted(topo.routers):
        m = {}
        for key, eth in lin[r].items():
            m[key] = eth if target == "frr" else iol_ifname(int(eth[3:]))
        lans = [s for s in topo.subnets if s.router == r]
        for i, s in enumerate(lans):
            m[f"lan:{s.name}"] = f"lan{i}" if target == "frr" else f"Loopback{i + 1}"
        out[r] = m
    return out


def default_probe_router(topo: Topology) -> str:
    """An access router outside the building with the most LANs (fewest local LANs wins)."""
    per_area: dict[int, int] = {}
    for s in topo.subnets:
        per_area[s.area] = per_area.get(s.area, 0) + 1
    busiest = max(sorted(per_area), key=lambda a: per_area[a]) if per_area else None
    local = {r: sum(1 for s in topo.subnets if s.router == r) for r in topo.routers}

    def pick(cands):
        return min(cands, key=lambda r: (local[r], r)) if cands else None

    rs = topo.routers.values()
    return (pick([r.name for r in rs if r.role == "access" and r.area != busiest])
            or pick([r.name for r in rs if r.role == "access"])
            or pick([r.name for r in rs if r.area not in (0, busiest)])
            or pick(list(topo.routers)))


def probe_subnet(topo: Topology) -> ipaddress.IPv4Network:
    """First /29 in the p2p space after the /31s used by links."""
    used = len(topo.links) * 2
    start = int(topo.p2p_space.network_address) + math.ceil(used / 8) * 8
    net = ipaddress.ip_network((start, 29))
    if not net.subnet_of(topo.p2p_space):
        raise ValueError(f"p2p space {topo.p2p_space} has no room for the probe /29")
    return net


def lab_name_for(topo: Topology, plan: Plan, protocol: str) -> str:
    raw = f"{topo.name}-{plan.strategy}-{protocol}".lower()
    return re.sub(r"[^a-z0-9-]+", "-", raw).strip("-")


# ------------------------------------------------------------------ model
def _router_context(topo: Topology, plan: Plan, r: str, target: str, protocol: str,
                    names: dict[str, str], lin: dict[str, str], probe: dict | None,
                    hello: int, dead: int) -> dict:
    alloc = plan.allocation
    router = topo.routers[r]
    areas = topo.router_areas(r)
    lo_area = 0 if 0 in areas else router.area
    ifaces = []
    for l in topo.incident(r):
        net = alloc.p2p[l.id]
        ip = net.network_address if l.a == r else net.network_address + 1
        ifaces.append({
            "name": names[l.id], "linux": lin[l.id], "kind": "p2p", "ip": str(ip),
            "prefixlen": 31, "mask": "255.255.255.254", "network": str(net.network_address),
            "cost": l.cost, "delay": 10 * l.cost, "area": l.area, "peer": l.other(r),
            "desc": f"to {l.other(r)} ({l.id})", "passive": False, "summaries": [],
        })
    for s in topo.subnets:
        if s.router != r:
            continue
        net = alloc.subnets[s.name]
        ifaces.append({
            "name": names[f"lan:{s.name}"], "linux": names[f"lan:{s.name}"], "kind": "lan",
            "ip": str(net.network_address + 1), "prefixlen": net.prefixlen, "mask": str(net.netmask),
            "network": str(net.network_address), "cost": 1, "delay": 1, "area": s.area, "peer": None,
            "desc": f"LAN {s.name} ({s.hosts} hosts)", "passive": True, "summaries": [],
        })
    if probe and probe["router"] == r:
        net = ipaddress.ip_network(probe["subnet"])
        ifaces.append({
            "name": names["probe"], "linux": lin["probe"], "kind": "probe", "ip": probe["router_ip"],
            "prefixlen": net.prefixlen, "mask": str(net.netmask), "network": str(net.network_address),
            "cost": 1, "delay": 1, "area": router.area, "peer": PROBE,
            "desc": "measurement probe", "passive": True, "summaries": [],
        })

    ospf_ranges = []
    if protocol == "ospf":
        for area, ad in plan.summaries():
            if r in topo.border_routers(area):
                ospf_ranges.append({"area": area, "prefix": str(ad.prefix),
                                    "network": str(ad.prefix.network_address), "mask": str(ad.prefix.netmask)})
    else:
        by_link = {}
        for area, ad in plan.summaries():
            for b, l in topo.border_links(area):
                if b == r:
                    by_link.setdefault(l.id, []).append({"prefix": str(ad.prefix),
                                                         "network": str(ad.prefix.network_address),
                                                         "mask": str(ad.prefix.netmask)})
        for i in ifaces:
            if i["kind"] == "p2p":
                lid = next(l.id for l in topo.incident(r) if names[l.id] == i["name"])
                i["summaries"] = by_link.get(lid, [])

    return {
        "hostname": r, "router_id": str(alloc.loopbacks[r]), "loopback_area": lo_area,
        "protocol": protocol, "target": target, "interfaces": ifaces, "ospf_ranges": ospf_ranges,
        "hello": hello, "dead": dead, "eigrp_as": EIGRP_AS, "strategy": plan.strategy,
        "building": router.building or "", "role": router.role,
    }


def render_configs(topo: Topology, plan: Plan, target: str = "frr", protocol: str = "ospf",
                   lab_name: str | None = None, probe: str | None = None,
                   hello: int = 1, dead: int = 4) -> dict[str, str]:
    """Return {relative path: file content} for a complete containerlab lab."""
    protocol = protocol.lower()
    if target not in TARGETS:
        raise ValueError(f"unknown target {target!r}; choose from {TARGETS}")
    if protocol not in ("ospf", "eigrp"):
        raise ValueError(f"unknown protocol {protocol!r}")
    if target == "frr" and protocol == "eigrp":
        raise ValueError("EIGRP is not supported on the FRR target: FRR's eigrpd is incomplete and has no "
                         "interface summary-address support. Use target='iol' (Cisco IOL) for EIGRP.")
    lab = lab_name or lab_name_for(topo, plan, protocol)
    probe_router = probe or default_probe_router(topo)
    if probe_router not in topo.routers:
        raise ValueError(f"probe router {probe_router!r} not in topology")
    names = interface_map(topo, target, probe_router)
    lin = linux_ifnames(topo, probe_router)
    pnet = probe_subnet(topo)
    hosts = list(pnet.hosts())
    probe_info = {"router": probe_router, "subnet": str(pnet), "router_ip": str(hosts[0]),
                  "ip": str(hosts[1]), "router_if": lin[probe_router]["probe"], "if": "eth1"}

    files: dict[str, str] = {}
    nodes: dict[str, dict] = {}
    for r in sorted(topo.routers):
        ctx = _router_context(topo, plan, r, target, protocol, names[r], lin[r], probe_info, hello, dead)
        if target == "frr":
            files[f"configs/{r}/frr.conf"] = _env.get_template("frr.conf.j2").render(**ctx)
            files[f"configs/{r}/daemons"] = _env.get_template("daemons").render()
            files[f"configs/{r}/vtysh.conf"] = _env.get_template("vtysh.conf").render(hostname=r)
            exec_cmds = ["sysctl -w net.ipv4.ip_forward=1", "sysctl -w net.ipv4.conf.all.rp_filter=0"]
            for i in ctx["interfaces"]:
                if i["kind"] == "lan":
                    exec_cmds += [f"ip link add {i['name']} type dummy", f"ip link set {i['name']} up"]
                else:
                    exec_cmds.append(f"sysctl -w net.ipv4.conf.{i['linux']}.rp_filter=0")
            nodes[r] = {"kind": "linux", "image": FRR_IMAGE,
                        "binds": [f"configs/{r}/frr.conf:/etc/frr/frr.conf",
                                  f"configs/{r}/daemons:/etc/frr/daemons",
                                  f"configs/{r}/vtysh.conf:/etc/frr/vtysh.conf"],
                        "exec": exec_cmds}
        else:
            files[f"configs/{r}/startup.cfg"] = _env.get_template("iol.cfg.j2").render(**ctx)
            nodes[r] = {"kind": "cisco_iol", "image": IOL_IMAGE,
                        "startup-config": f"configs/{r}/startup.cfg"}
        nodes[r]["labels"] = {"fasplan-role": topo.routers[r].role,
                              "fasplan-area": str(topo.routers[r].area)}

    nodes[PROBE] = {"kind": "linux", "image": NETSHOOT_IMAGE, "exec": [
        f"ip addr add {probe_info['ip']}/{pnet.prefixlen} dev eth1",
        "ip link set eth1 up",
        *[f"ip route replace {sp} via {probe_info['router_ip']}"
          for sp in (topo.lan_space, topo.loopback_space, topo.p2p_space)],
    ]}

    links = [{"endpoints": [f"{l.a}:{lin[l.a][l.id]}", f"{l.b}:{lin[l.b][l.id]}"]} for l in topo.links]
    links.append({"endpoints": [f"{probe_router}:{probe_info['router_if']}", f"{PROBE}:eth1"]})
    clab = {"name": lab, "topology": {"nodes": nodes, "links": links}}
    files[f"{lab}.clab.yml"] = (f"# generated by fasplan: {topo.name}, strategy={plan.strategy}, "
                                f"protocol={protocol}, target={target}\n"
                                + yaml.safe_dump(clab, sort_keys=False, width=120))

    manifest = _manifest(topo, plan, target, protocol, lab, lin, names, probe_info, hello, dead)
    files["lab.json"] = json.dumps(manifest, indent=2) + "\n"
    files["plan.json"] = json.dumps(plan.to_dict(topo), indent=2, default=str) + "\n"
    return files


def _manifest(topo, plan, target, protocol, lab, lin, names, probe_info, hello, dead) -> dict:
    alloc = plan.allocation
    container = lambda n: f"clab-{lab}-{n}"  # noqa: E731
    return {
        "lab": lab, "topology": topo.name, "strategy": plan.strategy, "protocol": protocol,
        "target": target, "clab_file": f"{lab}.clab.yml", "hello": hello, "dead": dead,
        "lan_space": str(topo.lan_space),
        "nodes": {r: {"container": container(r), "netns": container(r), "kind": "linux" if target == "frr" else "cisco_iol",
                      "area": topo.routers[r].area, "role": topo.routers[r].role,
                      "router_id": str(alloc.loopbacks[r]), "neighbors": len(topo.incident(r))}
                  for r in sorted(topo.routers)},
        "links": {l.id: {"a": l.a, "b": l.b, "cost": l.cost, "area": l.area,
                         "a_if": lin[l.a][l.id], "b_if": lin[l.b][l.id],
                         "a_os_if": names[l.a][l.id], "b_os_if": names[l.b][l.id],
                         "subnet": str(alloc.p2p[l.id])}
                  for l in topo.links},
        "probe": {**probe_info, "container": container(PROBE)},
        "targets": [{"subnet": s.name, "ip": str(alloc.subnets[s.name].network_address + 1),
                     "prefix": str(alloc.subnets[s.name]), "router": s.router, "area": s.area}
                    for s in topo.subnets if s.router != probe_info["router"]],
        "summaries": [{"area": a, "prefix": str(ad.prefix), "members": list(ad.members)}
                      for a, ad in plan.summaries()],
        "scenarios": [{"name": f.name, "failed": sorted(f.failed)} for f in topo.scenarios() if f.failed],
    }


def build_lab(topo: Topology, plan: Plan, outdir, target: str = "frr", protocol: str = "ospf",
              lab_name: str | None = None, probe: str | None = None) -> Path:
    out = Path(outdir)
    for rel, content in render_configs(topo, plan, target, protocol, lab_name, probe).items():
        p = out / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
    return out
