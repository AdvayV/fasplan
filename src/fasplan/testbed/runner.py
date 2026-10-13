"""Drive a generated containerlab lab: deploy, wait for convergence, inject
link failures, and measure packet loss, outage, recovery and routing-control
traffic.

    sudo -E .venv/bin/python -m fasplan.testbed.runner --lab labs/fasp-ospf deploy
    sudo -E .venv/bin/python -m fasplan.testbed.runner --lab labs/fasp-ospf converge
    sudo -E .venv/bin/python -m fasplan.testbed.runner --lab labs/fasp-ospf fail-test --link c-d1--c-d2

Failure injection works on the containers' network namespaces, which needs
root: `carrier` sets both interface ends down (the peer sees loss of carrier at
once), `silent` drops every packet with netem so the protocol only notices when
its dead/hold timer expires.  Use --dry-run to print the commands instead.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import shlex
import subprocess
import sys
import time
from pathlib import Path

from .analysis import (analyze_ping, count_frr_routes, count_full_neighbors, count_ios_neighbors,
                       count_ios_routes, count_summary_lsas, pcap_stats)
from .configgen import NETSHOOT_IMAGE, TARGETS, build_lab

CSV_FIELDS = ["strategy", "protocol", "scenario", "run", "target", "loss_pct", "outage_ms",
              "ctrl_pkts", "ctrl_bytes", "routes_total", "recovery_ms", "blackholed",
              "ctrl_update_pkts", "ctrl_update_bytes", "mode", "target_ip", "lab"]


# ------------------------------------------------------------------- shell
class Shell:
    def __init__(self, dry_run: bool = False, sudo: str = "auto", verbose: bool = False) -> None:
        self.dry_run = dry_run
        self.verbose = verbose or dry_run
        self.use_sudo = sudo == "always" or (sudo == "auto" and hasattr(os, "geteuid") and os.geteuid() != 0)
        self.log: list[str] = []

    def _cmd(self, cmd: list[str]) -> list[str]:
        return (["sudo", "-n"] + cmd) if self.use_sudo else cmd

    def run(self, cmd: list[str], check: bool = False, timeout: float | None = None) -> subprocess.CompletedProcess:
        full = self._cmd(cmd)
        line = shlex.join(full)
        self.log.append(line)
        if self.verbose:
            print("+", line, file=sys.stderr)
        if self.dry_run:
            return subprocess.CompletedProcess(full, 0, "", "")
        return subprocess.run(full, capture_output=True, text=True, check=check, timeout=timeout)

    def popen(self, cmd: list[str]):
        full = self._cmd(cmd)
        line = shlex.join(full)
        self.log.append(line)
        if self.verbose:
            print("+", line, file=sys.stderr)
        if self.dry_run:
            return None
        return subprocess.Popen(full, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)

    def sleep(self, s: float) -> None:
        if not self.dry_run and s > 0:
            time.sleep(s)


# --------------------------------------------------------------------- lab
class Lab:
    def __init__(self, path: str | Path) -> None:
        self.dir = Path(path).resolve()
        self.m = json.loads((self.dir / "lab.json").read_text())

    @property
    def clab_file(self) -> Path:
        return self.dir / self.m["clab_file"]

    def container(self, node: str) -> str:
        return self.m["nodes"][node]["container"] if node in self.m["nodes"] else self.m["probe"]["container"]

    def resolve_link(self, ref: str) -> str:
        links = self.m["links"]
        if ref in links:
            return ref
        for sep in ("--", ":", ","):
            if sep in ref:
                a, b = (x.strip() for x in ref.split(sep, 1))
                for lid, l in links.items():
                    if {l["a"], l["b"]} == {a, b}:
                        return lid
        raise SystemExit(f"unknown link {ref!r}; known: {', '.join(links)}")

    def targets(self, names: str | None) -> list[dict]:
        ts = self.m["targets"]
        if names:
            want = {x.strip() for x in names.split(",")}
            ts = [t for t in ts if t["subnet"] in want or t["ip"] in want]
            if not ts:
                raise SystemExit(f"no targets match {names!r}")
        return ts


def _netns_prefix(sh: Shell, ns: str, container: str) -> list[str]:
    if sh.dry_run or Path(f"/var/run/netns/{ns}").exists():
        return ["ip", "netns", "exec", ns]
    pid = sh.run(["docker", "inspect", "-f", "{{.State.Pid}}", container]).stdout.strip()
    return ["nsenter", "-t", pid, "-n"]


def set_link(sh: Shell, lab: Lab, lid: str, mode: str, down: bool) -> None:
    l = lab.m["links"][lid]
    for node, ifname in ((l["a"], l["a_if"]), (l["b"], l["b_if"])):
        info = lab.m["nodes"][node]
        pre = _netns_prefix(sh, info["netns"], info["container"])
        if mode == "carrier":
            sh.run(pre + ["ip", "link", "set", ifname, "down" if down else "up"])
        elif mode == "silent":
            if down:
                sh.run(pre + ["tc", "qdisc", "add", "dev", ifname, "root", "netem", "loss", "100%"])
            else:
                sh.run(pre + ["tc", "qdisc", "del", "dev", ifname, "root"])
        else:
            raise SystemExit(f"unknown failure mode {mode}")


# ------------------------------------------------------------- operations
def deploy(sh: Shell, lab: Lab) -> int:
    return sh.run(["containerlab", "deploy", "-t", str(lab.clab_file), "--reconfigure"]).returncode


def destroy(sh: Shell, lab: Lab) -> int:
    return sh.run(["containerlab", "destroy", "-t", str(lab.clab_file), "--cleanup"]).returncode


def converge(sh: Shell, lab: Lab, timeout: float = 180, quiet: bool = False) -> bool:
    t0 = time.time()
    probe = lab.m["probe"]["container"]
    while True:
        pending = []
        if lab.m["target"] == "frr":
            for r, info in lab.m["nodes"].items():
                out = sh.run(["docker", "exec", info["container"], "vtysh", "-c",
                              "show ip ospf neighbor json"]).stdout
                if not sh.dry_run and count_full_neighbors(out) < info["neighbors"]:
                    pending.append(r)
        if not pending:
            for t in lab.m["targets"]:
                rc = sh.run(["docker", "exec", probe, "ping", "-c", "1", "-W", "1", t["ip"]]).returncode
                if rc != 0:
                    pending.append(t["subnet"])
        if not pending:
            if not quiet:
                print(f"converged after {time.time() - t0:.1f}s")
            return True
        if time.time() - t0 > timeout:
            print(f"not converged after {timeout}s; waiting on: {', '.join(pending)}", file=sys.stderr)
            return False
        sh.sleep(2)


def _iol_session(sh: Shell, container: str):
    try:
        from netmiko import ConnectHandler
    except ImportError:
        raise SystemExit("IOL snapshots need netmiko: uv pip install -e '.[lab]'")
    ip = sh.run(["docker", "inspect", "-f", "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}",
                 container]).stdout.strip()
    return ConnectHandler(device_type="cisco_ios", host=ip, username=os.environ.get("FASPLAN_IOL_USER", "admin"),
                          password=os.environ.get("FASPLAN_IOL_PASS", "admin"))


def snapshot(sh: Shell, lab: Lab) -> dict:
    per = {}
    for r, info in lab.m["nodes"].items():
        if lab.m["target"] == "frr":
            routes = count_frr_routes(sh.run(["docker", "exec", info["container"], "vtysh", "-c",
                                              "show ip route json"]).stdout)
            lsas = count_summary_lsas(sh.run(["docker", "exec", info["container"], "vtysh", "-c",
                                              "show ip ospf database"]).stdout)
            nbrs = count_full_neighbors(sh.run(["docker", "exec", info["container"], "vtysh", "-c",
                                                "show ip ospf neighbor json"]).stdout)
        elif sh.dry_run:
            routes = lsas = nbrs = 0
        else:
            with _iol_session(sh, info["container"]) as s:
                routes = count_ios_routes(s.send_command("show ip route summary"))
                if lab.m["protocol"] == "ospf":
                    lsas = count_summary_lsas(s.send_command("show ip ospf database"))
                    nbrs = count_ios_neighbors(s.send_command("show ip ospf neighbor"))
                else:
                    lsas = 0
                    nbrs = count_ios_neighbors(s.send_command("show ip eigrp neighbors"))
        per[r] = {"routes": routes, "summary_lsas": lsas, "neighbors": nbrs}
    snap = {"routers": per, "routes_total": sum(v["routes"] for v in per.values()),
            "routes_max": max((v["routes"] for v in per.values()), default=0),
            "summary_lsas_total": sum(v["summary_lsas"] for v in per.values()), "time": time.time()}
    if not sh.dry_run:
        (lab.dir / "snapshot.json").write_text(json.dumps(snap, indent=2))
    return snap


def fail_test(sh: Shell, lab: Lab, links: list[str], mode: str = "carrier", pre: float = 3, hold: float = 10,
              post: float = 5, interval: float = 0.01, targets: str | None = None, run: int = 0,
              scenario: str | None = None, routes_total: int | str = "") -> list[dict]:
    lids = [lab.resolve_link(x) for x in links]
    scenario = scenario or "fail " + " + ".join(lids)
    tgts = lab.targets(targets)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    capdir = lab.dir / "captures" / f"{stamp}-run{run}-{'_'.join(lids)}"
    if not sh.dry_run:
        capdir.mkdir(parents=True, exist_ok=True)

    caps = []
    for r, info in lab.m["nodes"].items():
        name = f"fasplan-cap-{lab.m['lab']}-{r}"[:63]
        sh.run(["docker", "rm", "-f", name])
        sh.run(["docker", "run", "-d", "--rm", "--name", name, "--net", f"container:{info['container']}",
                "-v", f"{capdir}:/cap", NETSHOOT_IMAGE, "tcpdump", "-i", "any", "-U", "-w", f"/cap/{r}.pcap",
                "ip proto 89 or ip proto 88"])
        caps.append(name)
    sh.sleep(2)

    total = pre + hold + post
    probe = lab.m["probe"]["container"]
    pings = {}
    for t in tgts:
        pings[t["subnet"]] = sh.popen(["docker", "exec", probe, "ping", "-D", "-n", "-i", str(interval),
                                       "-W", "1", "-w", str(int(round(total))), t["ip"]])
    sh.sleep(pre)
    t_fail = time.time()
    for lid in lids:
        set_link(sh, lab, lid, mode, down=True)
    sh.sleep(hold)
    t_restore = time.time()
    for lid in lids:
        set_link(sh, lab, lid, mode, down=False)
    outputs = {}
    for name, proc in pings.items():
        outputs[name] = proc.communicate(timeout=total + 30)[0] if proc else ""
    sh.sleep(1)
    for name in caps:
        sh.run(["docker", "stop", "-t", "2", name])

    ctrl = {"pkts": 0, "bytes": 0, "updates": 0, "update_bytes": 0}
    for r in lab.m["nodes"]:
        f = capdir / f"{r}.pcap"
        if f.exists():
            st = pcap_stats(f.read_bytes())
            for k in ctrl:
                ctrl[k] += st[k]
    rows = []
    for t in tgts:
        a = analyze_ping(outputs[t["subnet"]], interval, t_fail, t_restore)
        rows.append({
            "strategy": lab.m["strategy"], "protocol": lab.m["protocol"], "scenario": scenario, "run": run,
            "target": t["subnet"], "loss_pct": a["loss_pct"], "outage_ms": a["outage_ms"],
            "ctrl_pkts": ctrl["pkts"], "ctrl_bytes": ctrl["bytes"], "routes_total": routes_total,
            "recovery_ms": a["recovery_ms"], "blackholed": a["blackholed"],
            "ctrl_update_pkts": ctrl["updates"], "ctrl_update_bytes": ctrl["update_bytes"],
            "mode": mode, "target_ip": t["ip"], "lab": lab.m["lab"],
        })
    if not sh.dry_run:
        (capdir / "pings.json").write_text(json.dumps({"t_fail": t_fail, "t_restore": t_restore,
                                                       "outputs": outputs}, indent=1))
    return rows


def append_csv(path: Path, rows: list[dict]) -> None:
    new = not path.exists()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=CSV_FIELDS)
        if new:
            w.writeheader()
        w.writerows(rows)


def print_rows(rows: list[dict]) -> None:
    print(f"{'target':16} {'loss%':>7} {'outage ms':>10} {'recovery ms':>12} {'blackholed':>10}")
    for r in rows:
        rec = "-" if r["recovery_ms"] is None else r["recovery_ms"]
        print(f"{r['target']:16} {r['loss_pct']:>7} {r['outage_ms']:>10} {rec!s:>12} {str(r['blackholed']):>10}")
    if rows:
        print(f"control packets: {rows[0]['ctrl_pkts']} ({rows[0]['ctrl_update_pkts']} non-hello), "
              f"{rows[0]['ctrl_bytes']} bytes")


# --------------------------------------------------------------------- cli
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="fasplan.testbed.runner", description=__doc__.split("\n\n")[0])
    ap.add_argument("--lab", help="lab directory (contains lab.json)")
    ap.add_argument("--dry-run", action="store_true", help="print commands instead of running them")
    ap.add_argument("--sudo", choices=["auto", "always", "never"], default="auto")
    ap.add_argument("-v", "--verbose", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("build", help="plan a topology and write a lab directory")
    b.add_argument("--topology", required=True)
    b.add_argument("--strategy", default="fasp")
    b.add_argument("--protocol", default="ospf", choices=["ospf", "eigrp"])
    b.add_argument("--target", default="frr", choices=list(TARGETS))
    b.add_argument("--out", required=True)
    b.add_argument("--probe")
    b.add_argument("--lab-name")

    sub.add_parser("deploy")
    sub.add_parser("destroy")
    c = sub.add_parser("converge")
    c.add_argument("--timeout", type=float, default=180)
    sub.add_parser("snapshot")

    f = sub.add_parser("fail-test")
    f.add_argument("--link", action="append", required=True, help="a--b; repeat for multi-link failures")
    f.add_argument("--mode", choices=["carrier", "silent"], default="carrier")
    f.add_argument("--pre", type=float, default=3)
    f.add_argument("--hold", "--duration", dest="hold", type=float, default=10)
    f.add_argument("--post", type=float, default=5)
    f.add_argument("--interval", type=float, default=0.01)
    f.add_argument("--repeat", type=int, default=1)
    f.add_argument("--targets", help="comma-separated subnet names or IPs (default: all)")
    f.add_argument("--out", help="CSV to append to (default: <lab>/results.csv)")

    g = sub.add_parser("campaign")
    g.add_argument("--plans", "--labs", dest="plans", nargs="+", required=True, help="lab directories")
    g.add_argument("--scenarios", default="all", help="'all' or comma-separated scenario names / link ids")
    g.add_argument("--repeat", type=int, default=5)
    g.add_argument("--mode", choices=["carrier", "silent"], default="carrier")
    g.add_argument("--hold", type=float, default=10)
    g.add_argument("--interval", type=float, default=0.01)
    g.add_argument("--targets")
    g.add_argument("--deploy", action=argparse.BooleanOptionalAction, default=True)
    g.add_argument("--destroy", action=argparse.BooleanOptionalAction, default=True)
    g.add_argument("--out", default="results/testbed.csv")

    args = ap.parse_args(argv)
    sh = Shell(args.dry_run, args.sudo, args.verbose)

    if args.cmd == "build":
        from ..model import load_topology
        from ..strategies import Planner
        topo = load_topology(args.topology)
        plan = Planner(topo, args.protocol).plan(args.strategy)
        out = build_lab(topo, plan, args.out, args.target, args.protocol, args.lab_name, args.probe)
        print(f"lab written to {out} ({Lab(out).m['clab_file']})")
        return 0

    if args.cmd == "campaign":
        out = Path(args.out)
        for d in args.plans:
            lab = Lab(d)
            if args.deploy and deploy(sh, lab) != 0:
                print(f"deploy failed for {d}", file=sys.stderr)
                return 1
            try:
                if not converge(sh, lab):
                    return 1
                routes_total = snapshot(sh, lab)["routes_total"]
                scen = lab.m["scenarios"]
                if args.scenarios != "all":
                    want = {x.strip() for x in args.scenarios.split(",")}
                    scen = [s for s in scen if s["name"] in want or set(s["failed"]) & want]
                for s in scen:
                    for i in range(args.repeat):
                        rows = fail_test(sh, lab, s["failed"], args.mode, hold=args.hold, interval=args.interval,
                                         targets=args.targets, run=i, scenario=s["name"],
                                         routes_total=routes_total)
                        if not sh.dry_run:
                            append_csv(out, rows)
                        converge(sh, lab, quiet=True)
                print(f"{lab.m['lab']}: {len(scen)} scenarios x {args.repeat} runs -> {out}")
            finally:
                if args.destroy:
                    destroy(sh, lab)
        return 0

    if not args.lab:
        ap.error("--lab is required for this command")
    lab = Lab(args.lab)
    if args.cmd == "deploy":
        return deploy(sh, lab)
    if args.cmd == "destroy":
        return destroy(sh, lab)
    if args.cmd == "converge":
        return 0 if converge(sh, lab, args.timeout) else 1
    if args.cmd == "snapshot":
        print(json.dumps(snapshot(sh, lab), indent=2))
        return 0
    if args.cmd == "fail-test":
        routes_total = ""
        snap = lab.dir / "snapshot.json"
        if snap.exists():
            routes_total = json.loads(snap.read_text())["routes_total"]
        for i in range(args.repeat):
            rows = fail_test(sh, lab, args.link, args.mode, args.pre, args.hold, args.post, args.interval,
                             args.targets, run=i, routes_total=routes_total)
            if not sh.dry_run:
                append_csv(Path(args.out) if args.out else lab.dir / "results.csv", rows)
                print_rows(rows)
            if i + 1 < args.repeat:
                converge(sh, lab, quiet=True)
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
