import ipaddress
import json
import re
import struct
from pathlib import Path

import pytest
import yaml

from fasplan.model import load_topology
from fasplan.strategies import Planner
from fasplan.testbed import build_lab, interface_map, render_configs
from fasplan.testbed.analysis import (analyze_ping, count_full_neighbors, count_ios_routes, count_summary_lsas,
                                      parse_ping, pcap_stats)
from fasplan.testbed.configgen import default_probe_router, iol_ifname, linux_ifnames
from fasplan.testbed.runner import main as runner_main

ROOT = Path(__file__).resolve().parents[1]
TOPO = load_topology(ROOT / "examples" / "campus.yaml")
COMBOS = [("frr", "ospf"), ("iol", "ospf"), ("iol", "eigrp")]


@pytest.fixture(scope="module")
def plans():
    out = {}
    for proto in ("ospf", "eigrp"):
        pl = Planner(TOPO, proto)
        out[proto] = {s: pl.plan(s) for s in ("fasp", "maxagg")}
    return out


def _config(files, target, r):
    return files[f"configs/{r}/frr.conf" if target == "frr" else f"configs/{r}/startup.cfg"]


def _iface_block(cfg: str, name: str) -> str:
    m = re.search(rf"^interface {re.escape(name)}\n(.*?)^(?:!|exit)", cfg, re.M | re.S)
    assert m, f"interface {name} missing"
    return m.group(1)


@pytest.mark.parametrize("strategy", ["fasp", "maxagg"])
@pytest.mark.parametrize("target,protocol", COMBOS)
def test_render(plans, strategy, target, protocol):
    plan = plans[protocol][strategy]
    files = render_configs(TOPO, plan, target, protocol)
    manifest = json.loads(files["lab.json"])
    clab = yaml.safe_load(files[manifest["clab_file"]])
    nodes = clab["topology"]["nodes"]
    assert set(nodes) == set(TOPO.routers) | {"probe"}
    probe_router = manifest["probe"]["router"]
    names = interface_map(TOPO, target, probe_router)
    lin = linux_ifnames(TOPO, probe_router)

    # every clab endpoint is a known interface of that node
    endpoints = [ep for l in clab["topology"]["links"] for ep in l["endpoints"]]
    for ep in endpoints:
        node, ifname = ep.split(":")
        assert node == "probe" or ifname in lin[node].values()
    assert len(set(endpoints)) == len(endpoints)

    alloc = plan.allocation
    for l in TOPO.links:
        assert l.id in names[l.a] and l.id in names[l.b]
        assert f"{l.a}:{lin[l.a][l.id]}" in endpoints and f"{l.b}:{lin[l.b][l.id]}" in endpoints
        net = alloc.p2p[l.id]
        for r, ip in ((l.a, net.network_address), (l.b, net.network_address + 1)):
            block = _iface_block(_config(files, target, r), names[r][l.id])
            assert str(ip) in block

    for s in TOPO.subnets:
        cfg = _config(files, target, s.router)
        gw = alloc.subnets[s.name].network_address + 1
        block = _iface_block(cfg, names[s.router][f"lan:{s.name}"])
        assert str(gw) in block

    summaries = plan.summaries()
    assert summaries, "test plans should contain summaries"
    for area, ad in summaries:
        p = ad.prefix
        if protocol == "ospf":
            line = (f"area {area} range {p}" if target == "frr"
                    else f"area {area} range {p.network_address} {p.netmask}")
            for r in TOPO.routers:
                present = line in _config(files, target, r)
                assert present == (r in TOPO.border_routers(area)), (r, line)
        else:
            line = f"ip summary-address eigrp 100 {p.network_address} {p.netmask}"
            border = {(b, l.id) for b, l in TOPO.border_links(area)}
            for b, lid in border:
                assert line in _iface_block(_config(files, target, b), names[b][lid])
            for l in TOPO.links:  # nowhere else
                for r in (l.a, l.b):
                    if (r, l.id) not in border:
                        assert line not in _iface_block(_config(files, target, r), names[r][l.id])

    expected = {s.name for s in TOPO.subnets if s.router != probe_router}
    assert {t["subnet"] for t in manifest["targets"]} == expected
    for t in manifest["targets"]:
        assert ipaddress.ip_address(t["ip"]) == alloc.subnets[t["subnet"]].network_address + 1
    assert manifest["scenarios"] and all(s["failed"] for s in manifest["scenarios"])
    assert set(manifest["links"]) == {l.id for l in TOPO.links}


def test_frr_eigrp_rejected(plans):
    with pytest.raises(ValueError, match="iol"):
        render_configs(TOPO, plans["eigrp"]["fasp"], "frr", "eigrp")


def test_iol_naming():
    assert [iol_ifname(n) for n in (1, 2, 3, 4, 5, 8)] == [
        "Ethernet0/1", "Ethernet0/2", "Ethernet0/3", "Ethernet1/0", "Ethernet1/1", "Ethernet2/0"]


def test_probe_default_and_build(tmp_path, plans):
    assert TOPO.routers[default_probe_router(TOPO)].role == "access"
    d = build_lab(TOPO, plans["ospf"]["fasp"], tmp_path / "lab", "frr", "ospf")
    m = json.loads((d / "lab.json").read_text())
    assert (d / m["clab_file"]).exists()
    assert (d / "configs" / "core1" / "frr.conf").exists()
    probe = ipaddress.ip_network(m["probe"]["subnet"])
    for net in plans["ospf"]["fasp"].allocation.p2p.values():
        assert not net.overlaps(probe)


def test_runner_dry_run(tmp_path, plans, capsys):
    d = build_lab(TOPO, plans["ospf"]["maxagg"], tmp_path / "lab", "frr", "ospf")
    rc = runner_main(["--lab", str(d), "--dry-run", "--sudo", "never", "fail-test", "--link", "c-d2--c-d1",
                      "--mode", "carrier"])
    assert rc == 0
    err = capsys.readouterr().err
    m = json.loads((d / "lab.json").read_text())
    l = m["links"]["c-d1--c-d2"]
    assert f"ip netns exec {m['nodes']['c-d1']['netns']} ip link set {l['a_if']} down" in err
    assert f"ip netns exec {m['nodes']['c-d2']['netns']} ip link set {l['b_if']} up" in err
    assert "tcpdump -i any -U -w" in err and "ping -D -n -i 0.01" in err
    rc = runner_main(["--lab", str(d), "--dry-run", "--sudo", "never", "fail-test", "--link", "c-d1--c-d2",
                      "--mode", "silent"])
    assert rc == 0
    assert "netem loss 100%" in capsys.readouterr().err


# ------------------------------------------------------------------ parsers
def _ping_text(received, tx=100, t0=1000.0, interval=0.01, unreachable=()):
    lines = ["PING 10.20.16.1 (10.20.16.1) 56(84) bytes of data."]
    for seq in range(1, tx + 1):
        ts = t0 + seq * interval
        if seq in received:
            lines.append(f"[{ts:.6f}] 64 bytes from 10.20.16.1: icmp_seq={seq} ttl=62 time=0.120 ms")
        elif seq in unreachable:
            lines.append(f"[{ts:.6f}] From 172.16.0.33 icmp_seq={seq} Destination Net Unreachable")
    lines += ["", "--- 10.20.16.1 ping statistics ---",
              f"{tx} packets transmitted, {len(received)} received, "
              f"{round(100 * (tx - len(received)) / tx)}% packet loss, time 990ms"]
    return "\n".join(lines)


def test_ping_recovery():
    rx = set(range(1, 31)) | set(range(61, 101))
    text = _ping_text(rx, unreachable={40})
    p = parse_ping(text)
    assert p["transmitted"] == 100 and p["received"] == 70
    a = analyze_ping(text, 0.01, t_fail=1000.305, t_restore=1000.9)
    assert a["loss_pct"] == 30.0
    assert a["outage_ms"] == 300.0
    assert a["recovery_ms"] == pytest.approx(305.0, abs=0.5)
    assert a["blackholed"] is False


def test_ping_blackhole_and_clean():
    text = _ping_text(set(range(1, 31)))
    a = analyze_ping(text, 0.01, t_fail=1000.305, t_restore=1000.9)
    assert a["blackholed"] is True and a["recovery_ms"] is None
    clean = analyze_ping(_ping_text(set(range(1, 101))), 0.01, t_fail=1000.5)
    assert clean["loss_pct"] == 0 and clean["recovery_ms"] == 0.0 and clean["outage_ms"] == 0


def _pcap(linktype, packets):
    out = struct.pack("<IHHiIII", 0xA1B2C3D4, 2, 4, 0, 0, 65535, linktype)
    for i, (ptype, proto, msg) in enumerate(packets):
        ip = bytes([0x45, 0]) + struct.pack(">H", 44) + b"\0" * 5 + bytes([proto]) + b"\0" * 10
        body = ip + bytes([2, msg]) + b"\0" * 22
        if linktype == 276:
            hdr = b"\x08\x00\0\0" + b"\0\0\0\1" + b"\0\1" + bytes([ptype]) + b"\x06" + b"\0" * 8
        else:
            hdr = struct.pack(">H", ptype) + b"\0" * 12 + b"\x08\x00"
        pkt = hdr + body
        out += struct.pack("<IIII", i, 0, len(pkt), len(pkt)) + pkt
    return out


@pytest.mark.parametrize("linktype", [113, 276])
def test_pcap_stats(linktype):
    data = _pcap(linktype, [(4, 89, 1), (4, 89, 4), (0, 89, 4), (4, 88, 1), (4, 88, 5), (4, 6, 0)])
    st = pcap_stats(data)
    assert st["pkts"] == 4  # incoming LSU and TCP ignored
    assert st["updates"] == 2  # OSPF LSU + EIGRP update
    assert st["types"] == {"ospf-hello": 1, "ospf-lsu": 1, "eigrp-update": 1, "eigrp-hello": 1}
    assert pcap_stats(data, outgoing_only=False)["pkts"] == 5


FRR_DB = """
       OSPF Router with ID (192.168.255.1)

                Router Link States (Area 0.0.0.0)

Link ID         ADV Router      Age  Seq#       CkSum  Link count
192.168.255.1   192.168.255.1     50 0x80000005 0x1234 3

                Summary Link States (Area 0.0.0.0)

Link ID         ADV Router      Age  Seq#       CkSum  Route
10.20.0.0       192.168.255.3     40 0x80000001 0xabcd 10.20.0.0/22
10.20.0.0       192.168.255.4     40 0x80000001 0xabcd 10.20.0.0/22
10.20.16.0      192.168.255.7     40 0x80000001 0xabcd 10.20.16.0/22
"""

IOS_DB = """
                Summary Net Link States (Area 0)

Link ID         ADV Router      Age         Seq#       Checksum
10.20.0.0       192.168.255.3   45          0x80000001 0x00A1B2
10.20.8.0       192.168.255.5   45          0x80000001 0x00A1B2

                Summary ASB Link States (Area 1)

Link ID         ADV Router      Age         Seq#       Checksum
192.168.255.9   192.168.255.3   45          0x80000001 0x00A1B2
"""


def test_router_output_parsers():
    assert count_summary_lsas(FRR_DB) == 3
    assert count_summary_lsas(IOS_DB) == 2
    nbr = {"neighbors": {"192.168.255.2": [{"nbrState": "Full/-"}], "192.168.255.3": [{"nbrState": "Init/-"}]}}
    assert count_full_neighbors(json.dumps(nbr)) == 1
    summary = "IP routing table name is default (0x0)\nconnected       0           12\nTotal           31          9\n"
    assert count_ios_routes(summary) == 31
