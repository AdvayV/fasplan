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
