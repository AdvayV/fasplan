"""Pure parsers for testbed measurements (no Docker needed, unit-tested)."""

from __future__ import annotations

import json
import re
import struct

_REPLY = re.compile(r"^(?:\[(?P<ts>\d+\.\d+)\]\s+)?\d+ bytes from [^:]+: icmp_seq=(?P<seq>\d+)")
_SUMMARY = re.compile(r"(?P<tx>\d+) packets transmitted, (?P<rx>\d+) (?:packets )?received")


def parse_ping(text: str) -> dict:
    """Parse iputils `ping -D` output into replies [(seq, ts)] and counters."""
    replies = []
    tx = rx = None
    for line in text.splitlines():
        m = _REPLY.match(line.strip())
        if m:
            ts = float(m["ts"]) if m["ts"] else None
            replies.append((int(m["seq"]), ts))
            continue
        m = _SUMMARY.search(line)
        if m:
            tx, rx = int(m["tx"]), int(m["rx"])
    seen = {}
    for seq, ts in replies:  # duplicates (DUP!) keep the first
        seen.setdefault(seq, ts)
    replies = sorted(seen.items())
    if tx is None:
        tx = max((s for s, _ in replies), default=0)
    if rx is None:
        rx = len(replies)
    return {"transmitted": tx, "received": rx, "replies": replies}


def longest_gap(received: set[int], transmitted: int) -> tuple[int, int]:
    """Longest run of consecutive missing sequence numbers in 1..transmitted -> (length, first seq)."""
    best = (0, 0)
    run, start = 0, 0
    for seq in range(1, transmitted + 1):
        if seq in received:
            run = 0
        else:
            if run == 0:
                start = seq
            run += 1
            if run > best[0]:
                best = (run, start)
    return best


def analyze_ping(text: str, interval: float, t_fail: float | None = None,
                 t_restore: float | None = None) -> dict:
    """Loss, outage and recovery for one ping stream.

    outage_ms    longest run of missing replies x interval
    recovery_ms  from failure injection to the first reply after the first loss
                 that follows the failure (0 if nothing was lost)
    blackholed   no reply came back between that loss and the link restore
    """
    p = parse_ping(text)
    tx, replies = p["transmitted"], p["replies"]
    received = {s for s, _ in replies}
    gap, gap_start = longest_gap(received, tx)
    out = {
        "transmitted": tx,
        "received": len(received),
        "loss_pct": round(100.0 * (tx - len(received)) / tx, 3) if tx else 0.0,
        "outage_ms": round(gap * interval * 1000, 1),
        "gap_start_seq": gap_start,
        "recovery_ms": None,
        "blackholed": False,
    }
    if t_fail is None or not replies or replies[0][1] is None:
        return out
    before = [s for s, ts in replies if ts <= t_fail]
    last_ok = max(before) if before else 0
    first_missing = next((s for s in range(last_ok + 1, tx + 1) if s not in received), None)
    if first_missing is None:
        out["recovery_ms"] = 0.0
        return out
    after = [(s, ts) for s, ts in replies if s > first_missing]
    if not after or (t_restore is not None and after[0][1] >= t_restore):
        out["blackholed"] = True
        return out
    out["recovery_ms"] = round(max(0.0, after[0][1] - t_fail) * 1000, 1)
    return out


# ---------------------------------------------------------------- pcap
LINKTYPE_ETHERNET, LINKTYPE_SLL, LINKTYPE_SLL2 = 1, 113, 276
PACKET_OUTGOING = 4
OSPF_TYPES = {1: "hello", 2: "dbd", 3: "lsr", 4: "lsu", 5: "lsack"}
EIGRP_OPCODES = {1: "update", 3: "query", 4: "reply", 5: "hello", 10: "siaquery", 11: "siareply"}


def pcap_stats(data: bytes, outgoing_only: bool = True) -> dict:
    """Count routing-protocol packets in a classic pcap file.

    With a Linux cooked capture (tcpdump -i any) only packets the router sent
    are counted, so each packet on a link is counted once.  Returns totals and
    a per-message-type breakdown; `updates` excludes hellos."""
    stats = {"pkts": 0, "bytes": 0, "updates": 0, "update_bytes": 0, "types": {}}
    if len(data) < 24:
        return stats
    magic = data[:4]
    if magic in (b"\xd4\xc3\xb2\xa1", b"\x4d\x3c\xb2\xa1"):
        endian = "<"
    elif magic in (b"\xa1\xb2\xc3\xd4", b"\xa1\xb2\x3c\x4d"):
        endian = ">"
    else:
        raise ValueError("not a classic pcap file (pcapng is not supported; use tcpdump -w)")
    linktype = struct.unpack(endian + "I", data[20:24])[0] & 0x0FFFFFFF
    off = 24
    while off + 16 <= len(data):
        _s, _us, incl, orig = struct.unpack(endian + "IIII", data[off:off + 16])
        pkt = data[off + 16: off + 16 + incl]
        off += 16 + incl
        if linktype == LINKTYPE_SLL:
            ptype = struct.unpack(">H", pkt[0:2])[0] if len(pkt) >= 2 else None
            l3 = 16
        elif linktype == LINKTYPE_SLL2:
            ptype = pkt[10] if len(pkt) > 10 else None
            l3 = 20
        elif linktype == LINKTYPE_ETHERNET:
            ptype, l3 = None, 14
        else:
            ptype, l3 = None, 0
        if outgoing_only and ptype is not None and ptype != PACKET_OUTGOING:
            continue
        ip = pkt[l3:]
        if len(ip) < 20 or ip[0] >> 4 != 4:
            continue
        ihl = (ip[0] & 0x0F) * 4
        proto = ip[9]
        body = ip[ihl:]
        if proto == 89 and len(body) >= 2:
            kind = "ospf-" + OSPF_TYPES.get(body[1], str(body[1]))
            hello = body[1] == 1
        elif proto == 88 and len(body) >= 2:
            kind = "eigrp-" + EIGRP_OPCODES.get(body[1], str(body[1]))
            hello = body[1] == 5
        else:
            continue
        stats["pkts"] += 1
        stats["bytes"] += orig
        stats["types"][kind] = stats["types"].get(kind, 0) + 1
        if not hello:
            stats["updates"] += 1
            stats["update_bytes"] += orig
    return stats


# ------------------------------------------------------------ router output
_IPV4_ROW = re.compile(r"^\s*\d+\.\d+\.\d+\.\d+\s+\d+\.\d+\.\d+\.\d+\s+\d+")


def count_summary_lsas(text: str) -> int:
    """Count type-3 (summary) LSAs in `show ip ospf database` output (FRR or IOS).

    Sections titled 'Summary Link States' (FRR) or 'Summary Net Link States'
    (IOS) are counted row by row; ASBR summaries are skipped."""
    n = 0
    in_section = False
    for line in text.splitlines():
        s = line.strip()
        if "Link States" in s:
            in_section = "Summary" in s and "ASB" not in s
            continue
        if in_section and _IPV4_ROW.match(line):
            n += 1
    return n


def count_full_neighbors(text: str) -> int:
    """Number of OSPF neighbours in Full state from FRR `show ip ospf neighbor json`."""
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return sum(1 for line in text.splitlines() if "Full" in line)
    n = 0

    def walk(x):
        nonlocal n
        if isinstance(x, dict):
            state = x.get("nbrState") or x.get("state")
            if isinstance(state, str) and state.startswith("Full"):
                n += 1
                return
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)

    walk(data)
    return n


def count_frr_routes(text: str) -> int:
    """Prefixes in FRR `show ip route json`."""
    try:
        return len(json.loads(text))
    except json.JSONDecodeError:
        return 0


def count_ios_routes(text: str) -> int:
    """Total prefixes from IOS `show ip route summary` (the 'Total' row)."""
    for line in text.splitlines():
        if line.strip().startswith("Total"):
            nums = re.findall(r"\d+", line)
            if nums:
                return int(nums[0])
    return 0


def count_ios_neighbors(text: str) -> int:
    """Full OSPF neighbours or EIGRP peers from IOS show output."""
    full = sum(1 for line in text.splitlines() if "FULL" in line)
    if full:
        return full
    return sum(1 for line in text.splitlines() if re.match(r"^\s*\d+\s+\d+\.\d+\.\d+\.\d+\s+\S+", line))
