import itertools
from pathlib import Path

import pytest

from fasplan.addressing import conventional, prefixlen_for_hosts
from fasplan.evaluate import compare
from fasplan.generator import GenParams, generate
from fasplan.model import load_topology, topology_from_dict
from fasplan.plan import Advert, Plan, flat_adverts, tight_prefix
from fasplan.routing import simulate
from fasplan.strategies import Planner

ROOT = Path(__file__).resolve().parents[1]
CAMPUS = ROOT / "examples" / "campus.yaml"


@pytest.fixture(scope="module")
def campus():
    return load_topology(CAMPUS)


def partition_topo():
    """Area 1 has two ABRs joined by one link; each ABR hosts one LAN."""
    return topology_from_dict({
        "name": "partition",
        "spaces": {"lan": "10.1.0.0/16"},
        "routers": {"core": {"area": 0}, "x1": {"area": 1}, "x2": {"area": 1}},
        "links": [{"a": "core", "b": "x1", "cost": 10}, {"a": "core", "b": "x2", "cost": 10},
                  {"a": "x1", "b": "x2", "cost": 3}],
        "subnets": [{"name": "l1", "router": "x1", "hosts": 100}, {"name": "l2", "router": "x2", "hosts": 100}],
    })


def summarize_all(topo, plan):
    names = [s.name for s in topo.subnets_in(1)]
    p = tight_prefix([plan.allocation.subnets[n] for n in names])
    adverts = dict(plan.adverts)
    adverts[1] = [Advert(p, tuple(names), "summary")]
    return Plan("sum", plan.protocol, plan.allocation, adverts)


def test_prefixlen_for_hosts():
    assert prefixlen_for_hosts(2) == 30
    assert prefixlen_for_hosts(62) == 26
    assert prefixlen_for_hosts(63) == 25
    assert prefixlen_for_hosts(500) == 23


def test_vlsm_aligned_and_disjoint(campus):
    for alloc in (conventional(campus), Planner(campus)._fa_alloc()):
        nets = list(alloc.subnets.values())
        for a, b in itertools.combinations(nets, 2):
            assert not a.overlaps(b)
        for s in campus.subnets:
            net = alloc.subnets[s.name]
            assert net.num_addresses - 2 >= s.hosts
            assert net.subnet_of(alloc.domain_blocks[s.area])
        for a, b in itertools.combinations(alloc.domain_blocks.values(), 2):
            assert not a.overlaps(b)


@pytest.mark.parametrize("protocol", ["ospf", "eigrp"])
def test_partition_blackhole_only_with_summary(protocol):
    topo = partition_topo()
    flat = Planner(topo, protocol).plan("flat")
    summ = summarize_all(topo, flat)
    cut = frozenset({"x1--x2"})
    # per-subnet routing repairs the partitioned area through the backbone
    assert simulate(topo, flat, cut, protocol).walk("core", "l2").status == "delivered"
    assert simulate(topo, flat, cut, protocol).walk("x1", "l2").status == "delivered"
    # the summary hides it: x1 keeps advertising and discards traffic for l2
    w = simulate(topo, summ, cut, protocol).walk("x1", "l2")
    assert w.status == "blackhole" and w.drop_at == "x1"
    # without the failure the summary is fine
    assert simulate(topo, summ, frozenset(), protocol).walk("core", "l2").status == "delivered"


def test_summary_cost_semantics():
    topo = partition_topo()
    flat = Planner(topo, "ospf").plan("flat")
    summ = summarize_all(topo, flat)
    p = summ.adverts[1][0].prefix
    ospf = simulate(topo, summ, frozenset(), "ospf").fibs["core"][p]
    eigrp = simulate(topo, summ, frozenset(), "eigrp").fibs["core"][p]
    assert ospf.cost == 10 + 3  # OSPF: max component cost (RFC 2328)
    assert eigrp.cost == 10 + 0  # EIGRP: min component metric


@pytest.mark.parametrize("protocol", ["ospf", "eigrp"])
def test_campus_proposed_is_lossless_and_smaller(campus, protocol):
    rows, plans, _ = compare(campus, protocol)
    by = {r["strategy"]: r for r in rows}
    assert by["fasp"]["fail_lost_pairs"] == 0 and by["fasp"]["normal_lost"] == 0
    assert by["maxagg"]["fail_lost_pairs"] > 0
    assert by["fasp"]["adverts"] < by["flat"]["adverts"]
    assert by["shaikh"]["adverts"] <= by["fasp"]["adverts"]  # equal-budget baseline
    assert by["fasp"]["stretch_normal_max"] <= 1.25 + 1e-9


def test_partition_planner_splits():
    topo = partition_topo()
    plan = Planner(topo, "ospf").plan("fasp")
    assert not plan.summaries()
    assert any("x1.30" in d.reason for d in plan.decisions)  # stretch 13/10 > 1.25 already rejects it
    lax = Planner(topo, "ospf", tau_normal=2.0, tau_failure=2.0).plan("fasp")
    assert not lax.summaries()
    assert any("cannot reach" in d.reason for d in lax.decisions)  # the failure check still does


def test_plans_cover_every_subnet_once(campus):
    pl = Planner(campus, "ospf")
    for s in ["flat", "maxagg", "shaikh", "fasp-sel", "fasp"]:
        plan = pl.plan(s)
        members = [m for ads in plan.adverts.values() for ad in ads for m in ad.members]
        assert sorted(members) == sorted(x.name for x in campus.subnets)
        for _, ad in plan.summaries():
            for m in ad.members:
                assert plan.allocation.subnets[m].subnet_of(ad.prefix)


def test_shaikh_respects_budget(campus):
    pl = Planner(campus, "ospf")
    for k in (4, 6, 10):
        assert pl.plan("shaikh", budget=k).advert_count() <= k


def test_generator_deterministic():
    a, b = generate(7), generate(7)
    assert a.to_dict() == b.to_dict()
    assert len(generate(3, GenParams(buildings=5)).domains()) == 5


@pytest.mark.parametrize("seed", range(6))
@pytest.mark.parametrize("protocol", ["ospf", "eigrp"])
def test_generated_proposed_lossless(seed, protocol):
    topo = generate(seed, GenParams(buildings=3, double_failures=5))
    rows, _, _ = compare(topo, protocol, strategies=["fasp"])
    assert rows[0]["fail_lost_pairs"] == 0 and rows[0]["normal_lost"] == 0


def test_cli_smoke(tmp_path, capsys):
    from fasplan.cli import main
    assert main(["plan", str(CAMPUS), "-o", str(tmp_path / "p")]) == 0
    assert (tmp_path / "p" / "plan.json").exists()
    assert main(["scenario", str(CAMPUS), "--strategy", "maxagg", "--fail", "c-d1--c-d2"]) == 0
    assert "blackhole" in capsys.readouterr().out
