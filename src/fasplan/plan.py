"""Plan = address allocation + the prefixes each building advertises."""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass, field

from .addressing import Allocation
from .model import Topology

IPv4Network = ipaddress.IPv4Network


@dataclass
class Advert:
    prefix: IPv4Network
    members: tuple[str, ...]
    kind: str  # "summary" | "subnet"
    note: str = ""


@dataclass
class Decision:
    """One step of the summary search, kept so the UI and report can explain it."""
    prefix: str
    members: list[str]
    verdict: str  # "summarize" | "split" | "single"
    reason: str = ""


@dataclass
class Plan:
    strategy: str
    protocol: str
    allocation: Allocation
    adverts: dict[int, list[Advert]]
    decisions: list[Decision] = field(default_factory=list)
    meta: dict = field(default_factory=dict)

    def summaries(self, area: int | None = None) -> list[tuple[int, Advert]]:
        return [(a, ad) for a, ads in sorted(self.adverts.items()) for ad in ads
                if ad.kind == "summary" and (area is None or a == area)]

    def advert_count(self) -> int:
        return sum(len(v) for v in self.adverts.values())

    def subnet_index(self) -> dict[str, tuple[int, Advert]]:
        return {m: (a, ad) for a, ads in self.adverts.items() for ad in ads for m in ad.members}

    def to_dict(self, topo: Topology) -> dict:
        alloc = self.allocation
        return {
            "strategy": self.strategy,
            "protocol": self.protocol,
            "allocation_method": alloc.method,
            "address_utilization": round(alloc.utilization(topo), 4),
            "domain_blocks": {str(a): str(n) for a, n in sorted(alloc.domain_blocks.items())},
            "subnets": alloc.to_rows(topo),
            "adverts": {
                str(a): [{"prefix": str(ad.prefix), "kind": ad.kind, "members": list(ad.members),
                          "note": ad.note} for ad in ads]
                for a, ads in sorted(self.adverts.items())
            },
            "advert_count": self.advert_count(),
            "summary_count": len(self.summaries()),
            "decisions": [d.__dict__ for d in self.decisions],
            "p2p": {k: str(v) for k, v in alloc.p2p.items()},
            "loopbacks": {k: str(v) for k, v in alloc.loopbacks.items()},
            "meta": self.meta,
        }


def flat_adverts(topo: Topology, alloc: Allocation) -> dict[int, list[Advert]]:
    out: dict[int, list[Advert]] = {}
    for s in topo.subnets:
        out.setdefault(s.area, []).append(Advert(alloc.subnets[s.name], (s.name,), "subnet"))
    for ads in out.values():
        ads.sort(key=lambda ad: ad.prefix)
    return out


def tight_prefix(nets: list[IPv4Network]) -> IPv4Network:
    """Smallest single prefix covering all `nets`."""
    lo = min(int(n.network_address) for n in nets)
    hi = max(int(n.broadcast_address) for n in nets)
    plen = 32
    while plen > 0:
        mask = (0xFFFFFFFF << (32 - plen)) & 0xFFFFFFFF
        if lo & mask == hi & mask:
            break
        plen -= 1
    return ipaddress.ip_network((lo & ((0xFFFFFFFF << (32 - plen)) & 0xFFFFFFFF), plen))
