"""VLSM address allocation.

Allocation is driven by a *layout tree*: leaves are subnets, internal nodes are
groups that must occupy one aligned power-of-two block (so each group can later
be advertised as a single summary).  Children of a group are packed largest
first, which keeps every child aligned without gaps.

* conventional VLSM: one flat group per building, subnets sorted by size.
* failure-aware VLSM: the planner builds a deeper tree whose groups are subnets
  with the same reachability behaviour under failures (see strategies.py).
"""

from __future__ import annotations

import ipaddress
import math
from dataclasses import dataclass, field

from .model import Topology

IPv4Network = ipaddress.IPv4Network


def prefixlen_for_hosts(hosts: int, headroom: float = 0.0) -> int:
    need = math.ceil(hosts * (1 + headroom)) + 2  # network + broadcast
    return min(30, 32 - math.ceil(math.log2(max(need, 4))))


def pow2_ceil(n: int) -> int:
    return 1 << max(0, (n - 1).bit_length())


@dataclass
class Leaf:
    subnet: str
    size: int


@dataclass
class Group:
    children: list
    label: str = ""

    @property
    def size(self) -> int:
        return pow2_ceil(sum(c.size for c in self.children))


def _sort_key(node):
    name = node.subnet if isinstance(node, Leaf) else node.label
    return (-node.size, name)


def _assign(node, start: int, out: dict[str, IPv4Network], groups: list) -> None:
    if isinstance(node, Leaf):
        out[node.subnet] = ipaddress.ip_network((start, 32 - int(math.log2(node.size))))
        return
    groups.append((node, ipaddress.ip_network((start, 32 - int(math.log2(node.size))))))
    pos = start
    for child in sorted(node.children, key=_sort_key):
        _assign(child, pos, out, groups)
        pos += child.size


@dataclass
class Allocation:
    subnets: dict[str, IPv4Network]
    domain_blocks: dict[int, IPv4Network]
    groups: dict[int, list[tuple[str, IPv4Network, list[str]]]] = field(default_factory=dict)
    p2p: dict[str, IPv4Network] = field(default_factory=dict)
    loopbacks: dict[str, ipaddress.IPv4Address] = field(default_factory=dict)
    method: str = "conventional"

    def utilization(self, topo: Topology, area: int | None = None) -> float:
        areas = [area] if area is not None else list(self.domain_blocks)
        used = sum(self.subnets[s.name].num_addresses for s in topo.subnets if s.area in areas)
        total = sum(self.domain_blocks[a].num_addresses for a in areas if a in self.domain_blocks)
        return used / total if total else 1.0

    def host_utilization(self, topo: Topology) -> float:
        hosts = sum(s.hosts for s in topo.subnets)
        total = sum(b.num_addresses for b in self.domain_blocks.values())
        return hosts / total if total else 1.0

    def to_rows(self, topo: Topology) -> list[dict]:
        rows = []
        for s in sorted(topo.subnets, key=lambda s: self.subnets[s.name]):
            net = self.subnets[s.name]
            hosts = list(net.hosts())
            rows.append({
                "subnet": s.name, "area": s.area, "router": s.router, "hosts_needed": s.hosts,
                "prefix": str(net), "mask": str(net.netmask), "usable": net.num_addresses - 2,
                "gateway": str(hosts[0]), "first_host": str(hosts[1]) if len(hosts) > 1 else "",
                "last_host": str(hosts[-1]), "broadcast": str(net.broadcast_address),
            })
        return rows


def allocate(topo: Topology, trees: dict[int, Group], method: str, headroom: float = 0.0) -> Allocation:
    """Place each area's layout tree inside the LAN space, largest area first."""
    base = int(topo.lan_space.network_address)
    end = base + topo.lan_space.num_addresses
    subnets: dict[str, IPv4Network] = {}
    blocks: dict[int, IPv4Network] = {}
    groups: dict[int, list] = {}
    pos = base
    for area, tree in sorted(trees.items(), key=lambda kv: (-kv[1].size, kv[0])):
        size = tree.size
        pos = (pos + size - 1) // size * size  # align
        if pos + size > end:
            raise ValueError(f"LAN space {topo.lan_space} too small for the requested subnets")
        found: list = []
        _assign(tree, pos, subnets, found)
        blocks[area] = ipaddress.ip_network((pos, 32 - int(math.log2(size))))
        groups[area] = [(g.label, net, sorted(_leaves(g))) for g, net in found if g is not tree]
        pos += size
    alloc = Allocation(subnets, blocks, groups, method=method)
    _infra(topo, alloc)
    return alloc


def _leaves(node) -> list[str]:
    if isinstance(node, Leaf):
        return [node.subnet]
    return [x for c in node.children for x in _leaves(c)]


def _infra(topo: Topology, alloc: Allocation) -> None:
    p2p = list(topo.p2p_space.subnets(new_prefix=31))
    if len(p2p) < len(topo.links):
        raise ValueError("p2p space too small")
    for l, net in zip(topo.links, p2p):
        alloc.p2p[l.id] = net
    lo = list(topo.loopback_space.hosts()) or list(topo.loopback_space)
    for r, ip in zip(sorted(topo.routers), lo):
        alloc.loopbacks[r] = ip


def leaf_for(topo: Topology, name: str, headroom: float = 0.0) -> Leaf:
    s = topo.subnet(name)
    return Leaf(name, 1 << (32 - prefixlen_for_hosts(s.hosts, headroom)))


def conventional(topo: Topology, headroom: float = 0.0) -> Allocation:
    """Classic VLSM: per area, biggest subnet first, no regard for topology."""
    trees = {}
    for area in sorted({s.area for s in topo.subnets}):
        trees[area] = Group([leaf_for(topo, s.name, headroom) for s in topo.subnets_in(area)],
                            label=f"area{area}")
    return allocate(topo, trees, "conventional", headroom)
