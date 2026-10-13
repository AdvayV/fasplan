"""Topology model: routers, links, departmental subnets and failure scenarios.

A topology is described in YAML (see examples/campus.yaml).  Areas follow OSPF
conventions: area 0 is the backbone and every other area is one building
(a *summarization domain*).  A link takes the area of its endpoints when both
share a home area, otherwise it belongs to the backbone, which makes the
building's distribution routers its border routers (ABRs).
"""

from __future__ import annotations

import ipaddress
import itertools
from dataclasses import dataclass, field
from pathlib import Path

import yaml

IPv4Network = ipaddress.IPv4Network


@dataclass(frozen=True)
class Router:
    name: str
    area: int
    building: str | None = None
    role: str = "router"


@dataclass(frozen=True)
class Link:
    id: str
    a: str
    b: str
    cost: int
    area: int

    def other(self, r: str) -> str:
        return self.b if r == self.a else self.a

    def touches(self, r: str) -> bool:
        return r == self.a or r == self.b


@dataclass(frozen=True)
class Subnet:
    name: str
    router: str
    hosts: int
    area: int


@dataclass(frozen=True)
class Scenario:
    name: str
    failed: frozenset[str]

    @property
    def is_normal(self) -> bool:
        return not self.failed


NORMAL = Scenario("normal", frozenset())


def link_id(a: str, b: str) -> str:
    return f"{a}--{b}"


@dataclass
class Topology:
    name: str
    routers: dict[str, Router]
    links: list[Link]
    subnets: list[Subnet]
    lan_space: IPv4Network
    p2p_space: IPv4Network
    loopback_space: IPv4Network
    failure_spec: dict = field(default_factory=dict)
    description: str = ""

    def __post_init__(self) -> None:
        self._links_by_id = {l.id: l for l in self.links}
        self._pair = {}
        self._incident: dict[str, list[Link]] = {r: [] for r in self.routers}
        for l in self.links:
            key = frozenset((l.a, l.b))
            if key in self._pair:
                raise ValueError(f"parallel links between {l.a} and {l.b} are not supported")
            self._pair[key] = l
            self._incident[l.a].append(l)
            self._incident[l.b].append(l)
        self._subnets_by_name = {s.name: s for s in self.subnets}
        self._validate()

    # ------------------------------------------------------------------ queries
    def link(self, lid: str) -> Link:
        return self._links_by_id[lid]

    def link_between(self, a: str, b: str) -> Link | None:
        return self._pair.get(frozenset((a, b)))

    def incident(self, r: str) -> list[Link]:
        return self._incident[r]

    def subnet(self, name: str) -> Subnet:
        return self._subnets_by_name[name]

    def areas(self) -> list[int]:
        return sorted({r.area for r in self.routers.values()} | {l.area for l in self.links})

    def domains(self) -> list[int]:
        """Non-backbone areas that own at least one subnet."""
        return sorted({s.area for s in self.subnets if s.area != 0})

    def subnets_in(self, area: int) -> list[Subnet]:
        return [s for s in self.subnets if s.area == area]

    def router_areas(self, r: str, failed: frozenset[str] = frozenset()) -> set[int]:
        """Areas a router participates in, given failed links.

        The home area always counts (LAN interfaces live there)."""
        areas = {self.routers[r].area}
        for l in self._incident[r]:
            if l.id not in failed:
                areas.add(l.area)
        return areas

    def border_routers(self, area: int) -> list[str]:
        """Routers attached to both `area` and the backbone (static view)."""
        return sorted(r for r in self.routers
                      if area in self.router_areas(r) and 0 in self.router_areas(r))

    def border_links(self, area: int) -> list[tuple[str, Link]]:
        """(border router, backbone link) pairs where EIGRP summaries of `area` are configured."""
        out = []
        for r in self.border_routers(area):
            for l in self._incident[r]:
                if l.area == 0:
                    out.append((r, l))
        return out

    # ---------------------------------------------------------------- scenarios
    def scenarios(self) -> list[Scenario]:
        spec = self.failure_spec or {}
        out = [NORMAL]
        seen = {frozenset()}
        if spec.get("single_link", True):
            for l in self.links:
                s = Scenario(f"fail {l.id}", frozenset([l.id]))
                out.append(s)
                seen.add(s.failed)
        for group in spec.get("extra", []) or []:
            ids = frozenset(self.resolve_link(x).id for x in group)
            if ids not in seen:
                out.append(Scenario("fail " + " + ".join(sorted(ids)), ids))
                seen.add(ids)
        if spec.get("double_link_sample"):
            import random
            rng = random.Random(spec.get("seed", 0))
            pairs = list(itertools.combinations([l.id for l in self.links], 2))
            rng.shuffle(pairs)
            for p in pairs[: int(spec["double_link_sample"])]:
                ids = frozenset(p)
                if ids not in seen:
                    out.append(Scenario("fail " + " + ".join(sorted(ids)), ids))
                    seen.add(ids)
        return out

    def sample_double_failures(self, n: int, seed: int, exclude: list[Scenario] | None = None) -> list[Scenario]:
        """Random double-link failures not already in `exclude` (for held-out evaluation)."""
        import random
        seen = {s.failed for s in exclude or []}
        pairs = [frozenset(p) for p in itertools.combinations(sorted(l.id for l in self.links), 2)]
        random.Random(seed).shuffle(pairs)
        out = []
        for p in pairs:
            if p not in seen:
                out.append(Scenario("fail " + " + ".join(sorted(p)), p))
                if len(out) == n:
                    break
        return out

    def resolve_link(self, ref: str) -> Link:
        """Accept 'a--b', 'b--a' or 'a:b'."""
        if ref in self._links_by_id:
            return self._links_by_id[ref]
        for sep in ("--", ":", ","):
            if sep in ref:
                a, b = (x.strip() for x in ref.split(sep, 1))
                l = self.link_between(a, b)
                if l:
                    return l
        raise KeyError(f"unknown link {ref!r}")

    # --------------------------------------------------------------- validation
    def _validate(self) -> None:
        for l in self.links:
            for r in (l.a, l.b):
                if r not in self.routers:
                    raise ValueError(f"link {l.id} references unknown router {r}")
        names = set()
        for s in self.subnets:
            if s.router not in self.routers:
                raise ValueError(f"subnet {s.name} attached to unknown router {s.router}")
            if s.name in names:
                raise ValueError(f"duplicate subnet name {s.name}")
            names.add(s.name)
        for r in self.routers:
            nonzero = self.router_areas(r) - {0}
            if len(nonzero) > 1:
                raise ValueError(f"router {r} joins several non-backbone areas {sorted(nonzero)}; "
                                 "only backbone ABRs are supported")
        for a in self.domains():
            if not self.border_routers(a):
                raise ValueError(f"area {a} has no border router to the backbone")

    # ---------------------------------------------------------------- export
    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "spaces": {"lan": str(self.lan_space), "p2p": str(self.p2p_space),
                       "loopback": str(self.loopback_space)},
            "routers": {r.name: {"area": r.area, "building": r.building, "role": r.role}
                        for r in self.routers.values()},
            "links": [{"a": l.a, "b": l.b, "cost": l.cost, "area": l.area} for l in self.links],
            "subnets": [{"name": s.name, "router": s.router, "hosts": s.hosts} for s in self.subnets],
            "failures": self.failure_spec,
        }


def topology_from_dict(d: dict) -> Topology:
    spaces = d.get("spaces", {})
    routers = {}
    for name, rd in d["routers"].items():
        rd = rd or {}
        routers[name] = Router(name, int(rd.get("area", 0)), rd.get("building"), rd.get("role", "router"))
    links = []
    for ld in d["links"]:
        if isinstance(ld, (list, tuple)):
            a, b, *rest = ld
            ld = {"a": a, "b": b, "cost": rest[0] if rest else 1}
        a, b = ld["a"], ld["b"]
        if "area" in ld and ld["area"] is not None:
            area = int(ld["area"])
        else:
            ra, rb = routers[a].area, routers[b].area
            area = ra if ra == rb else 0
        links.append(Link(link_id(a, b), a, b, int(ld.get("cost", 1)), area))
    subnets = []
    for sd in d["subnets"]:
        r = sd["router"]
        subnets.append(Subnet(sd["name"], r, int(sd["hosts"]), int(sd.get("area", routers[r].area))))
    return Topology(
        name=d.get("name", "topology"),
        description=d.get("description", ""),
        routers=routers,
        links=links,
        subnets=subnets,
        lan_space=ipaddress.ip_network(spaces.get("lan", "10.0.0.0/8")),
        p2p_space=ipaddress.ip_network(spaces.get("p2p", "172.16.0.0/16")),
        loopback_space=ipaddress.ip_network(spaces.get("loopback", "192.168.255.0/24")),
        failure_spec=d.get("failures", {"single_link": True}) or {},
    )


def load_topology(path: str | Path) -> Topology:
    with open(path) as fh:
        return topology_from_dict(yaml.safe_load(fh))


def loads_topology(text: str) -> Topology:
    return topology_from_dict(yaml.safe_load(text))
