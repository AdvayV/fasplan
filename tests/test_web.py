from pathlib import Path

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

from fasplan.web.app import create_app  # noqa: E402

CAMPUS = (Path(__file__).resolve().parents[1] / "examples" / "campus.yaml").read_text()


@pytest.fixture(scope="module")
def client():
    return TestClient(create_app())


def test_index_and_static(client):
    r = client.get("/")
    assert r.status_code == 200 and "cytoscape" in r.text
    assert client.get("/static/app.js").status_code == 200
    assert client.get("/static/style.css").status_code == 200


def test_examples(client):
    ex = client.get("/api/examples").json()["examples"]
    assert any(e["name"] == "campus" for e in ex)


def test_analyze(client):
    r = client.post("/api/analyze", json={"yaml": CAMPUS, "protocol": "ospf"})
    assert r.status_code == 200, r.text
    d = r.json()
    assert len(d["graph"]["nodes"]) == 10
    assert {row["strategy"] for row in d["comparison"]} == set(d["strategies"])
    rows = {row["strategy"]: row for row in d["comparison"]}
    assert rows["fasp"]["fail_lost_pairs"] == 0
    assert rows["maxagg"]["fail_lost_pairs"] > 0
    assert set(d["plans"]) == set(d["strategies"])
    assert len(d["scenario_loss"]["fasp"]) == len(d["scenarios"])


@pytest.mark.parametrize("protocol", ["ospf", "eigrp"])
def test_simulate_partition(client, protocol):
    r = client.post("/api/simulate", json={"yaml": CAMPUS, "protocol": protocol, "strategy": "maxagg",
                                           "failed": ["c-d1--c-d2"], "src": "a-acc", "subnet": "hostel-2"})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["selected"]["lost"] > 0
    assert d["contrast"]["maxagg"]["lost"] > 0
    assert d["contrast"]["fasp"]["lost"] == 0
    assert "fib" in d["selected"] and "c-d1" in d["selected"]["fib"]
    assert d["selected"]["trace"]["path"][0] == "a-acc"


def test_bad_input(client):
    assert client.post("/api/analyze", json={"yaml": "routers: [", "protocol": "ospf"}).status_code == 400
    assert client.post("/api/simulate", json={"yaml": CAMPUS, "failed": ["nope--x"]}).status_code == 400
    assert client.post("/api/simulate", json={"yaml": CAMPUS, "strategy": "bogus"}).status_code == 400


def test_configs_endpoint(client):
    r = client.post("/api/configs", json={"yaml": CAMPUS, "strategy": "fasp", "target": "frr"})
    assert r.status_code in (200, 501)
    if r.status_code == 200:
        assert r.headers["content-type"] == "application/zip"


@pytest.mark.parametrize("example", ["campus", "branch-office", "data-centre"])
@pytest.mark.parametrize("protocol", ["ospf", "eigrp"])
def test_guided_example_paths(client, example, protocol):
    examples = client.get("/api/examples").json()["examples"]
    selected = next(item for item in examples if item["name"] == example)
    demo = selected["demo"]
    assert selected["title"] and demo["description"]
    payload = {"yaml": selected["yaml"], "protocol": protocol, "strategy": "maxagg",
               "src": demo["source"], "subnet": demo["subnet"], "failed": demo["failed"]}
    response = client.post("/api/simulate", json=payload)
    assert response.status_code == 200, response.text
    result = response.json()["contrast"]
    assert result["maxagg"]["trace"]["status"] == "blackhole"
    assert result["maxagg"]["lost"] > 0
    assert result["fasp"]["trace"]["status"] == "delivered"
    assert result["fasp"]["lost"] == 0
    from fasplan.model import loads_topology
    topology = loads_topology(selected["yaml"])
    failed = {topology.resolve_link(link).id for link in demo["failed"]}
    assert frozenset(failed) in {scenario.failed for scenario in topology.scenarios()}
    for strategy in ("maxagg", "fasp"):
        trace = result[strategy]["trace"]
        assert trace["path"][0] == demo["source"]
        for a, b in zip(trace["path"], trace["path"][1:]):
            link = topology.link_between(a, b)
            assert link is not None and link.id not in failed
    assert result["fasp"]["trace"]["path"][-1] == topology.subnet(demo["subnet"]).router
    payload["failed"] = []
    normal = client.post("/api/simulate", json=payload).json()["contrast"]
    assert all(normal[strategy]["trace"]["status"] == "delivered" for strategy in ("maxagg", "fasp"))
