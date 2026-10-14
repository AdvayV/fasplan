"""FastAPI dashboard: edit a topology, compare strategies, fail links interactively.

Run with:  python -m fasplan.web.app  [--host 127.0.0.1] [--port 8000]
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import statistics
import threading
import zipfile
from collections import OrderedDict
from pathlib import Path

import yaml
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from ..evaluate import compare
from ..model import Topology, loads_topology
from ..plan import Plan, flat_adverts
from ..routing import simulate as run_simulation
from ..strategies import LABELS, STRATEGIES, Planner

STATIC = Path(__file__).parent / "static"


def _examples_dir() -> Path | None:
    candidates = [os.environ.get("FASPLAN_EXAMPLES"),
                  Path(__file__).resolve().parents[3] / "examples",
                  Path.cwd() / "examples"]
    for c in candidates:
        if c and Path(c).is_dir():
            return Path(c)
    return None


# ------------------------------------------------------------------ request models
class AnalyzeReq(BaseModel):
    yaml: str
    protocol: str = "ospf"
    tau_normal: float = Field(1.25, ge=1.0, le=10.0)
    tau_failure: float = Field(1.5, ge=1.0, le=10.0)


class SimulateReq(AnalyzeReq):
    strategy: str = "fasp"
    failed: list[str] = []
    src: str | None = None
    subnet: str | None = None


class ConfigReq(AnalyzeReq):
    strategy: str = "fasp"
    target: str = "frr"


# ------------------------------------------------------------------ planner cache
class _Entry:
    def __init__(self, topo: Topology, planner: Planner) -> None:
        self.topo = topo
        self.planner = planner
        self.analysis: dict | None = None
        self.lock = threading.Lock()


class PlannerCache:
    def __init__(self, size: int = 16) -> None:
        self.size = size
        self._d: OrderedDict[tuple, _Entry] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, req: AnalyzeReq) -> _Entry:
        protocol = req.protocol.lower()
        if protocol not in ("ospf", "eigrp"):
            raise HTTPException(400, f"unknown protocol {req.protocol!r}")
        key = (hashlib.sha1(req.yaml.encode()).hexdigest(), protocol,
               round(req.tau_normal, 4), round(req.tau_failure, 4))
        with self._lock:
            if key in self._d:
                self._d.move_to_end(key)
                return self._d[key]
        try:
            topo = loads_topology(req.yaml)
        except (yaml.YAMLError, KeyError, ValueError, TypeError) as e:
            raise HTTPException(400, f"invalid topology: {e}")
        entry = _Entry(topo, Planner(topo, protocol, tau_normal=req.tau_normal, tau_failure=req.tau_failure))
        with self._lock:
            self._d[key] = entry
            while len(self._d) > self.size:
                self._d.popitem(last=False)
        return entry


# ------------------------------------------------------------------ helpers
def _graph(topo: Topology) -> dict:
    by_router: dict[str, list[dict]] = {}
    for s in topo.subnets:
        by_router.setdefault(s.router, []).append({"name": s.name, "hosts": s.hosts})
    return {
        "nodes": [{"id": r.name, "area": r.area, "building": r.building or f"Area {r.area}",
                   "role": r.role, "subnets": by_router.get(r.name, [])}
                  for r in sorted(topo.routers.values(), key=lambda r: r.name)],
        "edges": [{"id": l.id, "source": l.a, "target": l.b, "cost": l.cost, "area": l.area}
                  for l in topo.links],
        "areas": topo.areas(),
        "domains": topo.domains(),
    }


def _analyze(entry: _Entry) -> dict:
    with entry.lock:
        if entry.analysis is not None:
            return entry.analysis
        topo, planner = entry.topo, entry.planner
        try:
            rows, plans, details = compare(topo, planner.protocol, planner=planner)
        except ValueError as e:
            raise HTTPException(400, str(e))
        scen = [{"name": r.scenario, "failed": r.failed} for r in details[STRATEGIES[0]]]
        entry.analysis = {
            "name": topo.name,
            "description": topo.description,
            "protocol": planner.protocol,
            "graph": _graph(topo),
            "scenarios": scen,
            "strategies": STRATEGIES,
            "labels": LABELS,
            "comparison": rows,
            "plans": {s: p.to_dict(topo) for s, p in plans.items()},
            "scenario_loss": {
                s: [{"scenario": r.scenario, "failed": r.failed, "lost": r.lost,
                     "delivered_flat": r.delivered_flat, "churn": r.churn,
                     "stretch_max": round(max(r.stretch, default=1.0), 4)} for r in res]
                for s, res in details.items()
            },
        }
        return entry.analysis


def _flat_for(topo: Topology, plan: Plan) -> Plan:
    return Plan("flat", plan.protocol, plan.allocation, flat_adverts(topo, plan.allocation))


def _sim_one(topo: Topology, plan: Plan, failed: frozenset[str], with_fib: bool,
             src: str | None, subnet: str | None) -> dict:
    sp = run_simulation(topo, plan, failed, plan.protocol)
    sf = run_simulation(topo, _flat_for(topo, plan), failed, plan.protocol)
    lost, stretch = [], []
    pairs = delivered = delivered_flat = 0
    for s in topo.subnets:
        for r in sorted(topo.routers):
            if r == s.router:
                continue
            pairs += 1
            wp, wf = sp.walk(r, s.name), sf.walk(r, s.name)
            delivered += wp.status == "delivered"
            if wf.status != "delivered":
                continue
            delivered_flat += 1
            if wp.status != "delivered":
                lost.append({"src": r, "subnet": s.name, "status": wp.status,
                             "drop_at": wp.drop_at, "path": list(wp.path)})
            elif wf.cost > 0:
                stretch.append(wp.cost / wf.cost)
    out = {
        "strategy": plan.strategy,
        "label": plan.meta.get("label", plan.strategy),
        "pairs": pairs,
        "delivered": delivered,
        "delivered_flat": delivered_flat,
        "lost": len(lost),
        "lost_pairs": lost,
        "stretch_mean": round(statistics.fmean(stretch), 4) if stretch else 1.0,
        "stretch_max": round(max(stretch), 4) if stretch else 1.0,
        "ctrl_prefixes": sp.info.get("ctrl_prefixes", 0),
        "active_ranges": sp.info.get("active_ranges", {}),
        "routes": {r: sp.route_count(r) for r in sorted(topo.routers)},
    }
    if with_fib:
        out["fib"] = {
            r: [{"prefix": str(p), "kind": rt.kind, "cost": rt.cost, "nexthop": rt.nexthop, "via": rt.via}
                for p, rt in sorted(fib.items(), key=lambda kv: (kv[0].network_address, kv[0].prefixlen))]
            for r, fib in sp.fibs.items()
        }
    if src and subnet:
        if src not in topo.routers:
            raise HTTPException(400, f"unknown router {src}")
        try:
            topo.subnet(subnet)
        except KeyError:
            raise HTTPException(400, f"unknown subnet {subnet}")
        w, wf = sp.walk(src, subnet), sf.walk(src, subnet)
        out["trace"] = {"src": src, "subnet": subnet, "status": w.status, "cost": w.cost,
                        "path": list(w.path), "drop_at": w.drop_at,
                        "flat": {"status": wf.status, "cost": wf.cost, "path": list(wf.path)}}
    return out


# ------------------------------------------------------------------ app
def create_app() -> FastAPI:
    app = FastAPI(title="fasplan dashboard", version="0.1.0")
    cache = PlannerCache()
    app.state.cache = cache

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(STATIC / "index.html")

    @app.get("/api/examples")
    def examples():
        d = _examples_dir()
        out = []
        if d:
            for p in sorted(d.glob("*.y*ml")):
                text = p.read_text()
                metadata = yaml.safe_load(text) or {}
                demo = metadata.get("demo", {})
                out.append({"name": p.stem, "yaml": text, "demo": demo,
                            "title": demo.get("title", p.stem.replace("-", " ").title())})
        out.sort(key=lambda item: (item["name"] != "campus", item["name"]))
        return {"examples": out}

    @app.get("/api/generate")
    def generate(seed: int = 1, buildings: int = 3):
        try:
            from .. import generator  # noqa: PLC0415
        except ImportError:
            raise HTTPException(501, "topology generator not available")
        if not 2 <= buildings <= 12:
            raise HTTPException(400, "buildings must be between 2 and 12")
        params = generator.GenParams(buildings=buildings) if hasattr(generator, "GenParams") else None
        topo = generator.generate(seed, params)
        d = topo.to_dict() if hasattr(topo, "to_dict") else topo
        return {"name": f"generated-s{seed}-b{buildings}", "yaml": yaml.safe_dump(d, sort_keys=False)}

    @app.post("/api/analyze")
    def analyze(req: AnalyzeReq):
        return _analyze(cache.get(req))

    @app.post("/api/simulate")
    def simulate(req: SimulateReq):
        entry = cache.get(req)
        topo, planner = entry.topo, entry.planner
        if req.strategy not in STRATEGIES:
            raise HTTPException(400, f"unknown strategy {req.strategy}")
        try:
            failed = frozenset(topo.resolve_link(x).id for x in req.failed)
        except KeyError as e:
            raise HTTPException(400, str(e))
        with entry.lock:
            results = {}
            for s in dict.fromkeys([req.strategy, "maxagg", "fasp"]):
                results[s] = _sim_one(topo, planner.plan(s), failed, s == req.strategy, req.src, req.subnet)
        return {"failed": sorted(failed), "strategy": req.strategy,
                "selected": results[req.strategy],
                "contrast": {s: {k: v for k, v in r.items() if k != "fib"} for s, r in results.items()}}

    @app.post("/api/configs")
    def configs(req: ConfigReq):
        entry = cache.get(req)
        if req.strategy not in STRATEGIES:
            raise HTTPException(400, f"unknown strategy {req.strategy}")
        try:
            from ..testbed import render_configs  # noqa: PLC0415
        except ImportError as e:
            raise HTTPException(501, f"config generation not available: {e}")
        with entry.lock:
            plan = entry.planner.plan(req.strategy)
            try:
                files = render_configs(entry.topo, plan, target=req.target, protocol=entry.planner.protocol)
            except ValueError as e:
                raise HTTPException(400, str(e))
            plan_json = json.dumps(plan.to_dict(entry.topo), indent=2)
        buf = io.BytesIO()
        root = f"{entry.topo.name}-{req.strategy}-{entry.planner.protocol}-{req.target}"
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            for rel, content in sorted(files.items()):
                z.writestr(f"{root}/{rel}", content)
            if "plan.json" not in files:
                z.writestr(f"{root}/plan.json", plan_json)
        buf.seek(0)
        return StreamingResponse(buf, media_type="application/zip",
                                 headers={"Content-Disposition": f'attachment; filename="{root}.zip"'})

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app


def serve(host: str = "127.0.0.1", port: int = 8000) -> None:
    import uvicorn  # noqa: PLC0415

    uvicorn.run(create_app(), host=host, port=port, log_level="info")


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="fasplan web dashboard")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    a = ap.parse_args(argv)
    serve(a.host, a.port)


if __name__ == "__main__":
    main()
