"""Random campus topologies for repeated experiments.

Each building is one OSPF area with 1-2 distribution routers (the border
routers) and a few access routers; two core routers form the backbone.  The
knobs control how much redundancy a building has, which is what decides
whether conventional summarization is safe.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from .model import Topology, topology_from_dict

HOST_CHOICES = [20, 30, 50, 60, 100, 120, 150, 200, 250, 300, 400, 500]


@dataclass
class GenParams:
    buildings: int = 3
    access_per_building: tuple[int, int] = (1, 3)
    subnets_per_router: tuple[int, int] = (1, 3)
    dual_dist_prob: float = 0.85      # building has two distribution routers
    dual_home_prob: float = 0.6       # access router connects to both dists
    dist_link_prob: float = 0.5       # direct link between the two dists
    dual_uplink_prob: float = 0.3     # a dist connects to both cores
    lan_on_dist_prob: float = 0.5     # a dist also hosts LANs
    interbuilding_prob: float = 0.3   # extra backbone fibre between buildings
    core_routers: int = 2
    double_failures: int = 0          # sampled double-link failures added to scenarios


def generate(seed: int, p: GenParams | None = None) -> Topology:
    p = p or GenParams()
    rng = random.Random(seed)
    routers: dict[str, dict] = {}
    links: list[dict] = []
    subnets: list[dict] = []
    cores = [f"core{i + 1}" for i in range(p.core_routers)]
    for c in cores:
        routers[c] = {"area": 0, "building": "Core", "role": "core"}
    for a, b in zip(cores, cores[1:]):
        links.append({"a": a, "b": b, "cost": 5})
    if len(cores) > 2:
        links.append({"a": cores[-1], "b": cores[0], "cost": 5})
    subnets.append({"name": "datacenter", "router": cores[0], "hosts": rng.choice([60, 120, 250])})

    dists_by_building: list[list[str]] = []
    for bi in range(p.buildings):
        area = bi + 1
        tag = chr(ord("a") + bi) if bi < 26 else f"b{bi}"
        bname = f"Building-{tag.upper()}"
        nd = 2 if rng.random() < p.dual_dist_prob else 1
        dists = [f"{tag}-d{i + 1}" for i in range(nd)]
        for i, d in enumerate(dists):
            routers[d] = {"area": area, "building": bname, "role": "distribution"}
            links.append({"a": cores[i % len(cores)], "b": d, "cost": rng.choice([10, 10, 15])})
            if len(cores) > 1 and rng.random() < p.dual_uplink_prob:
                links.append({"a": cores[(i + 1) % len(cores)], "b": d, "cost": rng.choice([10, 15, 20])})
        if nd == 2 and rng.random() < p.dist_link_prob:
            links.append({"a": dists[0], "b": dists[1], "cost": rng.choice([2, 5, 8])})
        na = rng.randint(*p.access_per_building)
        hosts_on = []
        for j in range(na):
            acc = f"{tag}-acc{j + 1}"
            routers[acc] = {"area": area, "building": bname, "role": "access"}
            if nd == 2 and rng.random() < p.dual_home_prob:
                for d in dists:
                    links.append({"a": d, "b": acc, "cost": rng.choice([1, 2, 4])})
            else:
                links.append({"a": rng.choice(dists), "b": acc, "cost": rng.choice([1, 2, 4])})
            hosts_on.append(acc)
        for d in dists:
            if rng.random() < p.lan_on_dist_prob:
                hosts_on.append(d)
        for r in hosts_on:
            for k in range(rng.randint(*p.subnets_per_router)):
                subnets.append({"name": f"{r}-lan{k + 1}", "router": r, "hosts": rng.choice(HOST_CHOICES)})
        dists_by_building.append(dists)

    for i in range(len(dists_by_building)):
        for j in range(i + 1, len(dists_by_building)):
            if rng.random() < p.interbuilding_prob / max(1, len(dists_by_building) - 2):
                links.append({"a": rng.choice(dists_by_building[i]), "b": rng.choice(dists_by_building[j]),
                              "cost": rng.choice([15, 20, 30])})

    failures = {"single_link": True}
    if p.double_failures:
        failures.update(double_link_sample=p.double_failures, seed=seed)
    d = {
        "name": f"gen-s{seed}-b{p.buildings}",
        "description": f"generated campus seed={seed} params={p}",
        "spaces": {"lan": "10.0.0.0/8", "p2p": "172.16.0.0/16", "loopback": "192.168.0.0/16"},
        "routers": routers, "links": links, "subnets": subnets, "failures": failures,
    }
    return topology_from_dict(d)
