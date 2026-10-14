/* fasplan dashboard — vanilla JS + Cytoscape */
"use strict";

const S = {
  protocol: "ospf",
  strategy: "fasp",
  analysis: null,
  failed: new Set(),
  sim: null,
  router: null,       // router whose FIB is shown
  focus: null,        // highlighted lost pair / trace
  cy: null,
  busy: 0,
  examples: [], demo: null, flow: null, paths: null,
  simSeq: 0, analysisSeq: 0,
};

const $ = (sel, el = document) => el.querySelector(sel);
const $$ = (sel, el = document) => [...el.querySelectorAll(sel)];
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
const fmt = (x, d = 2) => (typeof x === "number" ? (Number.isInteger(x) ? String(x) : x.toFixed(d)) : esc(x));

function status(text, kind = "") {
  const el = $("#status");
  el.textContent = text;
  el.className = "status " + kind;
}

async function api(path, body) {
  S.busy++;
  status("working…", "busy");
  try {
    const res = await fetch(path, body === undefined ? {} : {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    });
    if (!res.ok) {
      let msg = res.statusText;
      try { msg = (await res.json()).detail || msg; } catch (_) { /* not json */ }
      throw new Error(typeof msg === "string" ? msg : JSON.stringify(msg));
    }
    return res;
  } finally {
    S.busy--;
    if (!S.busy) status("ready");
  }
}

function baseReq() {
  return {
    yaml: $("#yaml").value,
    protocol: S.protocol,
    tau_normal: parseFloat($("#tauN").value),
    tau_failure: parseFloat($("#tauF").value),
  };
}

/* ------------------------------------------------------------ examples */
async function loadExamples() {
  try {
    const data = await (await api("/api/examples")).json();
    S.examples = data.examples;
    const sel = $("#examples");
    sel.innerHTML = `<option value="">Examples…</option>` +
      data.examples.map((e, i) => `<option value="${i}">${esc(e.title || e.name)}</option>`).join("");
    sel.onchange = () => {if (sel.value !== "") chooseExample(Number(sel.value));};
    $("#demoExamples").innerHTML = data.examples.map((e, i) =>
      `<button class="demo-choice ghost" data-example="${i}" aria-pressed="false">${esc(e.title || e.name)}</button>`).join("");
    $$("[data-example]").forEach((button) => button.onclick = () => chooseExample(Number(button.dataset.example)));
    const first = data.examples.find((e) => e.name === "campus") || data.examples[0];
    if (first) chooseExample(data.examples.indexOf(first));
  } catch (e) { status(e.message, "err"); }
}

function chooseExample(index) {
  const example = S.examples[index];
  if (!example) return;
  S.demo = example.demo || null;
  S.flow = S.demo ? { src: S.demo.source, subnet: S.demo.subnet } : null;
  S.failed.clear();
  $("#examples").value = index;
  $("#yaml").value = example.yaml;
  $("#demoStory").textContent = S.demo?.description || "Select a source and destination, then fail a link to explore forwarding.";
  $$("[data-example]").forEach((button) => button.setAttribute("aria-pressed", String(Number(button.dataset.example) === index)));
  analyze();
}

function fillFlows() {
  const routers = S.analysis.graph.nodes.map((n) => n.id);
  const subnets = S.analysis.graph.nodes.flatMap((n) => n.subnets.map((s) => s.name)).sort();
  if (!S.flow || !routers.includes(S.flow.src) || !subnets.includes(S.flow.subnet)) {
    const destination = subnets[0];
    const owner = S.analysis.graph.nodes.find((n) => n.subnets.some((s) => s.name === destination));
    S.flow = {src: routers.find((r) => r !== owner?.id) || routers[0], subnet: destination};
  }
  $("#flowSource").innerHTML = routers.map((r) => `<option>${esc(r)}</option>`).join("");
  $("#flowDestination").innerHTML = subnets.map((s) => `<option>${esc(s)}</option>`).join("");
  $("#flowSource").value = S.flow.src;
  $("#flowDestination").value = S.flow.subnet;
  const known = new Set(S.analysis.graph.edges.map((e) => e.id));
  $("#demoFailure").disabled = !S.demo?.failed?.length || !S.demo.failed.every((id) => known.has(id));
}

async function generate() {
  try {
    const seed = $("#genSeed").value, b = $("#genB").value;
    const data = await (await api(`/api/generate?seed=${encodeURIComponent(seed)}&buildings=${encodeURIComponent(b)}`)).json();
    S.demo = null; S.flow = null; S.failed.clear();
    $("#demoStory").textContent = "Generated network. Choose a flow and click links to explore failures.";
    $$("[data-example]").forEach((button) => button.setAttribute("aria-pressed", "false"));
    $("#yaml").value = data.yaml;
    $("#examples").value = "";
    analyze();
  } catch (e) { status(e.message, "err"); }
}

/* ------------------------------------------------------------ analyze */
async function analyze() {
  $("#analyze").disabled = true;
  const sequence = ++S.analysisSeq;
  ++S.simSeq;
  S.paths.stop();
  try {
    const analysis = await (await api("/api/analyze", baseReq())).json();
    if (sequence !== S.analysisSeq) return;
    S.analysis = analysis;
    const known = new Set(S.analysis.graph.edges.map((e) => e.id));
    S.failed = new Set([...S.failed].filter((x) => known.has(x)));
    S.router = null; S.focus = null;
    $("#topoName").textContent = `${S.analysis.name} · ${S.analysis.graph.nodes.length} routers · ` +
      `${S.analysis.graph.edges.length} links · ${S.analysis.scenarios.length} scenarios · ${S.analysis.protocol.toUpperCase()}`;
    fillStrategies();
    fillFlows();
    buildGraph();
    renderComparison(); renderAddr(); renderWhy(); renderScenarios(); renderExport();
    await runSim();
  } catch (e) {
    status(e.message, "err");
  } finally {
    if (sequence === S.analysisSeq) $("#analyze").disabled = false;
  }
}

function fillStrategies() {
  const sel = $("#strategy");
  const a = S.analysis;
  if (!a.strategies.includes(S.strategy)) S.strategy = "fasp";
  sel.innerHTML = a.strategies.map((s) => `<option value="${s}" ${s === S.strategy ? "selected" : ""}>${esc(a.labels[s])}</option>`).join("");
}

/* ------------------------------------------------------------ graph */
function areaColor(area) { return css(`--a${((area % 10) + 10) % 10}`) || "#888"; }

function layoutPositions(graph) {
  const adj = {};
  graph.nodes.forEach((n) => (adj[n.id] = []));
  graph.edges.forEach((e) => { adj[e.source].push(e.target); adj[e.target].push(e.source); });
  const depth = {};
  const q = [];
  graph.nodes.filter((n) => n.area === 0).forEach((n) => { depth[n.id] = 0; q.push(n.id); });
  if (!q.length && graph.nodes.length) { depth[graph.nodes[0].id] = 0; q.push(graph.nodes[0].id); }
  while (q.length) {
    const u = q.shift();
    for (const v of adj[u]) if (depth[v] === undefined) { depth[v] = depth[u] + 1; q.push(v); }
  }
  graph.nodes.forEach((n) => { if (depth[n.id] === undefined) depth[n.id] = 1; });

  const groups = {};
  graph.nodes.forEach((n) => {
    const key = n.area === 0 ? "__bb" : `${n.area}|${n.building}`;
    (groups[key] = groups[key] || []).push(n);
  });
  const pos = {};
  const DX = 150, DY = 210, GAP = 85;
  let x = 0;
  const keys = Object.keys(groups).filter((k) => k !== "__bb")
    .sort((a, b) => parseInt(a) - parseInt(b) || a.localeCompare(b));
  for (const k of keys) {
    const rows = {};
    groups[k].forEach((n) => (rows[depth[n.id]] = rows[depth[n.id]] || []).push(n));
    const width = Math.max(...Object.values(rows).map((r) => r.length));
    for (const [d, r] of Object.entries(rows)) {
      r.sort((a, b) => a.id.localeCompare(b.id));
      const off = (width - r.length) * DX / 2;
      r.forEach((n, i) => (pos[n.id] = { x: x + off + i * DX, y: Number(d) * DY + (r.length === 3 && n.role === "distribution" && i === 1 ? 95 : 0) }));
    }
    x += width * DX + GAP;
  }
  const total = Math.max(x - GAP, DX);
  const bb = (groups.__bb || []).sort((a, b) => a.id.localeCompare(b.id));
  const bbRows = {};
  bb.forEach((n) => {const row = n.role === "core" ? 0 : 1; (bbRows[row] = bbRows[row] || []).push(n);});
  for (const [d, r] of Object.entries(bbRows)) {
    const span = (r.length - 1) * DX * 1.6;
    r.forEach((n, i) => (pos[n.id] = { x: total / 2 - DX / 2 - span / 2 + i * DX * 1.6, y: Number(d) * 110 - 60 }));
  }
  return pos;
}

function graphRenderingOptions() {
  return {pixelRatio: Math.min(3, Math.max(2, window.devicePixelRatio || 1)),
    textureOnViewport: false, motionBlur: false, hideEdgesOnViewport: false};
}

function fitGraph(cy) {
  if (!cy || cy.destroyed()) return;
  cy.resize();
  cy.fit(cy.elements().not(".packet"), cy.width() < 500 ? 22 : 44);
}

function fitExpandedGraph(cy) {
  fitGraph(cy);
  if (cy.width() < 500) {
    cy.zoom(Math.max(0.7, cy.zoom()));
    const view = S.paths.views.find((v) => v.cy === cy);
    if (view?.trace.path.length) cy.center(cy.getElementById(view.trace.path[0]));
  }
}

function setPlaybackLabel(label) {
  $$("#playPath, [data-playback='play']").forEach((button) => {button.textContent = label;});
}

function graphFor(id) {
  return id === "cy" ? S.cy : S.paths?.mini[id.replace("cy-", "")];
}

let expandedGraph = null;
function updateExpandedRoute() {
  if (!expandedGraph) return;
  const walk = {cy: "routeWalk", "cy-maxagg": "maxaggWalk", "cy-fasp": "faspWalk"}[expandedGraph.id];
  const focused = document.activeElement.closest?.("#expandedRoute .hop");
  const focusIndex = focused?.dataset.hop;
  $("#expandedRoute").innerHTML = $("#" + walk).innerHTML;
  $$("#expandedRoute .hop").forEach((hop) => {
    hop.tabIndex = 0;
    hop.setAttribute("role", "button");
    hop.setAttribute("aria-label", "Center on " + hop.querySelector("b").textContent);
    hop.title = "Center this router in the graph";
  });
  if (focusIndex !== undefined) $(`#expandedRoute [data-hop="${focusIndex}"]`)?.focus({preventScroll: true});
}

function expandGraph(id, opener) {
  if (!graphFor(id)) return;
  const cy = graphFor(id), canvas = $("#" + id), placeholder = document.createComment("graph position");
  cy.unmount();
  canvas.before(placeholder);
  expandedGraph = {id, opener, placeholder, canvas};
  $("#expandedGraphTitle").textContent = {cy: "Selected strategy", "cy-maxagg": "Maximum aggregation", "cy-fasp": "Proposed plan"}[id];
  $("#expandedGraphSlot").append(canvas);
  document.body.classList.add("graph-expanded");
  updateExpandedRoute();
  $("#graphDialog").showModal();
  // Remount after moving the container: Cytoscape observes DOM removal and
  // stops its renderer, so resizing alone leaves a frozen canvas behind.
  cy.mount(canvas);
  requestAnimationFrame(() => fitExpandedGraph(cy));
}

function changeGraphView(id, action, button) {
  const cy = graphFor(id);
  if (!cy) return;
  if (action === "expand") {expandGraph(id, button); return;}
  if (action === "fit") {fitGraph(cy); return;}
  const factor = action === "in" ? 1.25 : 0.8;
  cy.zoom({level: Math.max(cy.minZoom(), Math.min(cy.maxZoom(), cy.zoom() * factor)),
    renderedPosition: {x: cy.width() / 2, y: cy.height() / 2}});
}

function watchGraphZoom(cy, id) {
  const update = () => {
    const output = $(`[data-graph-zoom="${id}"]`);
    if (output) output.value = Math.round(cy.zoom() * 100) + "%";
  };
  cy.on("zoom", update); update();
}

function cyStyle() {
  const ink = css("--ink"), muted = css("--muted"), line = css("--line"), bad = css("--bad"),
    warn = css("--warn"), accent = css("--accent"), panel = css("--panel"), panel2 = css("--panel-2");
  return [
    { selector: "node", style: {
      "background-color": "data(color)", label: "data(label)", color: ink, "font-size": 20,
      "font-family": "Segoe UI, system-ui, sans-serif", "font-weight": 600, "min-zoomed-font-size": 0,
      "text-valign": "bottom", "text-margin-y": 8, "text-wrap": "wrap", width: 46, height: 46,
      "border-width": 3, "border-color": panel, "text-background-color": panel,
      "text-background-opacity": 1, "text-background-padding": 4, "text-background-shape": "roundrectangle",
    } },
    { selector: "node[role = 'core']", style: { shape: "round-rectangle", width: 52, height: 42 } },
    { selector: "node[role = 'distribution']", style: { shape: "round-diamond", width: 46, height: 46 } },
    { selector: ":parent", style: {
      "background-color": "data(color)", "background-opacity": 0.045, "border-color": "data(color)",
      "border-width": 1.5, "border-style": "solid", label: "data(label)", "text-valign": "top",
      "text-halign": "center", "font-size": 18, "font-weight": 600, color: ink, padding: 28,
      shape: "round-rectangle", "text-background-opacity": 1, "text-background-color": panel, "text-margin-y": -8,
    } },
    { selector: "edge", style: {
      width: 2.5, "line-color": css("--graph-line"), label: "data(cost)", "font-size": 14, color: ink,
      "font-weight": 600, "font-family": "Segoe UI, system-ui, sans-serif",
      "text-background-color": panel, "text-background-opacity": 1, "text-background-padding": 4, "text-background-shape": "roundrectangle",
      "curve-style": "bezier", opacity: 0.9,
    } },
    { selector: "edge[area = 0]", style: { width: 3.5 } },
    { selector: "edge.failed", style: { "line-color": bad, "line-style": "dashed", opacity: 1, color: bad, width: 4 } },
    { selector: "edge.onpath", style: { "line-color": accent, width: 6, opacity: 1, "z-index": 10 } },
    { selector: "edge.flatpath", style: { "line-color": css("--good"), width: 5, "line-style": "dotted", opacity: 1 } },
    { selector: "node.lostsrc", style: { "border-color": warn, "border-width": 4 } },
    { selector: "node.drop", style: { "background-color": bad, "border-color": bad, "border-width": 5 } },
    { selector: "node.sel", style: { "border-color": accent, "border-width": 5 } },
    { selector: "node.onpath", style: { "border-color": accent, "border-width": 4 } },
    { selector: "edge:selected", style: { "overlay-opacity": 0.08 } },
    { selector: "node.dim, edge.dim", style: { opacity: 0.35 } },
    { selector: "edge.route-forward", style: {"target-arrow-shape": "triangle", "target-arrow-color": accent, "arrow-scale": 1.3} },
    { selector: "edge.route-reverse", style: {"source-arrow-shape": "triangle", "source-arrow-color": accent, "arrow-scale": 1.3} },
    { selector: "node.route-source", style: {"border-color": accent, "border-width": 6} },
    { selector: "node.route-destination", style: {"border-color": css("--good"), "border-width": 6} },
    { selector: "node.route-stop", style: {"background-color": bad, "border-color": bad, "border-width": 6} },
    { selector: "node.packet", style: {label: "", width: 15, height: 15, shape: "ellipse", "background-color": warn, "border-color": panel, "border-width": 2, "z-index": 100, events: "no"} },
    { selector: "node.packet.delivered", style: {"background-color": css("--good")} },
    { selector: "node.packet.dropped", style: {"background-color": bad} },
    { selector: "core", style: { "selection-box-color": line } },
  ];
}

function buildGraph() {
  const g = S.analysis.graph;
  const pos = layoutPositions(g);
  const els = [];
  const parents = new Map();
  g.nodes.forEach((n) => {
    const pid = n.area === 0 ? "grp:backbone" : `grp:${n.area}:${n.building}`;
    if (!parents.has(pid)) {
      parents.set(pid, true);
      els.push({ group: "nodes", data: { id: pid, label: n.area === 0 ? "Backbone · area 0" : `${n.building} · area ${n.area}`, color: areaColor(n.area) } });
    }
    const lans = n.subnets.length ? `\n${n.subnets.length} LAN${n.subnets.length > 1 ? "s" : ""}` : "";
    els.push({ group: "nodes", data: { id: n.id, parent: pid, label: n.id + lans, baseLabel: n.id + lans, color: areaColor(n.area), role: n.role, area: n.area }, position: pos[n.id] });
  });
  g.edges.forEach((e) => {
    els.push({ group: "edges", data: { id: e.id, source: e.source, target: e.target, cost: e.cost, area: e.area, color: e.area === 0 ? css("--a0") : areaColor(e.area) } });
  });
  if (S.cy) S.cy.destroy();
  S.cy = cytoscape({
    ...graphRenderingOptions(), container: $("#cy"), elements: els, style: cyStyle(), layout: { name: "preset", padding: 30 },
    wheelSensitivity: 0.25, minZoom: 0.2, maxZoom: 3, autoungrabify: false,
  });
  S.cy.on("tap", "edge", (ev) => {
    const id = ev.target.id();
    S.failed.has(id) ? S.failed.delete(id) : S.failed.add(id);
    S.focus = null;
    runSim();
  });
  S.cy.on("tap", "node", (ev) => {
    const n = ev.target;
    if (n.isParent()) return;
    S.router = S.router === n.id() ? null : n.id();
    renderSide(); highlight();
  });
  S.cy.on("tap", (ev) => { if (ev.target === S.cy) { S.router = null; renderSide(); highlight(); } });
  fitGraph(S.cy);
  watchGraphZoom(S.cy, "cy");
  renderLegend();
}

function renderLegend() {
  const g = S.analysis.graph;
  const items = [...new Map(g.nodes.map((n) => [n.area, n.area === 0 ? "Backbone" : n.building])).entries()]
    .sort((a, b) => a[0] - b[0])
    .map(([a, b]) => `<span><i style="background:${areaColor(a)}"></i>${esc(b)} (area ${a})</span>`);
  items.push(`<span><i class="dash"></i>failed link</span>`);
  items.push(`<span><i style="background:${css("--accent")}"></i>arrows show forwarding direction</span>`);
  items.push(`<span><i style="background:${css("--warn")}"></i>packet position during replay</span>`);
  items.push(`<span><i style="background:${css("--bad")}"></i>drops traffic (discard route)</span>`);
  items.push(`<span><i style="background:transparent;border:3px solid ${css("--warn")}"></i>source losing traffic</span>`);
  $("#legend").innerHTML = items.join("");
}

function edgeBetween(a, b) {
  return S.cy.edges().filter((e) => (e.data("source") === a && e.data("target") === b) || (e.data("source") === b && e.data("target") === a));
}

function highlight() {
  const cy = S.cy;
  if (!cy) return;
  cy.batch(() => {
    cy.elements().removeClass("failed onpath flatpath lostsrc drop sel dim");
    S.failed.forEach((id) => cy.getElementById(id).addClass("failed"));
    const sim = S.sim && S.sim.selected;
    if (sim) {
      sim.lost_pairs.forEach((p) => {
        cy.getElementById(p.src).addClass("lostsrc");
        if (p.drop_at) cy.getElementById(p.drop_at).addClass("drop");
      });
    }
    if (S.focus) {
      const path = S.focus.path || [];
      for (let i = 0; i + 1 < path.length; i++) edgeBetween(path[i], path[i + 1]).addClass("onpath");
      path.forEach((r) => cy.getElementById(r).addClass("onpath"));
      if (S.focus.flatPath) {
        const fp = S.focus.flatPath;
        for (let i = 0; i + 1 < fp.length; i++) {
          const e = edgeBetween(fp[i], fp[i + 1]);
          if (!e.hasClass("onpath")) e.addClass("flatpath");
        }
      }
    }
    if (S.router) cy.getElementById(S.router).addClass("sel");
  });
}

/* ------------------------------------------------------------ simulate */
async function runSim(trace) {
  if (!S.analysis) return;
  const sequence = ++S.simSeq;
  S.paths.stop();
  if (trace) S.flow = {src: trace.src, subnet: trace.subnet};
  try {
    const body = { ...baseReq(), strategy: S.strategy, failed: [...S.failed], ...S.flow };
    const simulation = await (await api("/api/simulate", body)).json();
    if (sequence !== S.simSeq) return;
    S.sim = simulation;
    if (S.sim.selected.trace) {
      const t = S.sim.selected.trace;
      S.focus = { path: t.path, flatPath: t.flat.path, trace: t };
    }
    $("#flowSource").value = S.flow.src;
    $("#flowDestination").value = S.flow.subnet;
    renderContrast(); renderSide(); highlight();
    S.paths.update();
  } catch (e) { status(e.message, "err"); }
}

function renderContrast() {
  const c = S.sim.contrast;
  const order = [...new Set([S.strategy, "maxagg", "fasp"])];
  $("#contrast").innerHTML = order.map((s) => {
    const r = c[s];
    const lostCls = r.lost ? "bad" : "good";
    return `<div class="card ${s === S.strategy ? "sel" : ""}">
      <div class="t">${esc(r.label)}</div>
      <div class="v ${lostCls}">${r.lost} <span class="m">lost pairs</span></div>
      <div class="m">${r.delivered}/${r.pairs} delivered · stretch max ×${fmt(r.stretch_max)}</div>
      <div class="m">${r.ctrl_prefixes} control-plane prefixes</div></div>`;
  }).join("");
}

function renderSide() {
  const side = $("#side");
  if (!S.sim) return;
  const sel = S.sim.selected;
  if (S.router && sel.fib) {
    $("#sideTitle").textContent = `Routing table · ${S.router}`;
    const fib = sel.fib[S.router] || [];
    const ranges = (sel.active_ranges[S.router] || []);
    side.innerHTML = `<p><button class="ghost" id="backBtn">← failure impact</button></p>
      <div class="kv"><b>Strategy</b><span>${esc(sel.label)}</span><b>Routes</b><span>${fib.length}</span>
      <b>Active summaries</b><span class="mono">${ranges.length ? ranges.map(esc).join(", ") : "—"}</span></div>
      <table><thead><tr><th>Prefix</th><th>Type</th><th class="num">Cost</th><th>Next hop</th></tr></thead><tbody>
      ${fib.map((r) => `<tr><td class="mono">${esc(r.prefix)}</td><td><span class="badge ${r.kind === "discard" ? "split" : r.kind === "connected" ? "summary" : ""}">${esc(r.kind)}</span></td>
        <td class="num">${fmt(r.cost, 1)}</td><td class="mono">${r.kind === "discard" ? "Null0" : esc(r.nexthop || "—")}</td></tr>`).join("")}
      </tbody></table>`;
    $("#backBtn").onclick = () => { S.router = null; renderSide(); highlight(); };
    return;
  }
  $("#sideTitle").textContent = "Failure impact";
  const failed = S.sim.failed.length ? S.sim.failed.map((x) => `<span class="chip">${esc(x)}</span>`).join(" ") : `<span class="muted">none — normal operation</span>`;
  const routers = S.analysis.graph.nodes.map((n) => n.id);
  const subnets = S.analysis.graph.nodes.flatMap((n) => n.subnets.map((s) => s.name)).sort();
  const t = S.focus && S.focus.trace;
  side.innerHTML = `
    <div class="kv"><b>Strategy</b><span>${esc(sel.label)}</span><b>Failed</b><span class="chips">${failed}</span>
    <b>Lost pairs</b><span style="color:${sel.lost ? "var(--bad)" : "var(--good)"}">${sel.lost} of ${sel.delivered_flat} reachable</span>
    <b>Stretch</b><span>mean ×${fmt(sel.stretch_mean, 3)} · max ×${fmt(sel.stretch_max)}</span></div>
    <h3>Trace a flow</h3>
    <div class="gen"><select id="trSrc">${routers.map((r) => `<option>${esc(r)}</option>`).join("")}</select>
    <select id="trDst">${subnets.map((s) => `<option>${esc(s)}</option>`).join("")}</select>
    <button class="ghost" id="trBtn">Trace</button></div>
    ${t ? `<p class="note">${esc(t.src)} → ${esc(t.subnet)}: <b style="color:${t.status === "delivered" ? "var(--good)" : "var(--bad)"}">${esc(t.status)}</b>
      ${t.drop_at ? `at ${esc(t.drop_at)}` : ""} · cost ${fmt(t.cost, 1)} (per-subnet routing: ${esc(t.flat.status)}, cost ${fmt(t.flat.cost, 1)})<br>
      <span class="mono">${t.path.map(esc).join(" → ")}</span><br><span class="muted">blue = this plan, green dotted = per-subnet routing</span></p>` : ""}
    <h3>Dropped flows ${sel.lost ? `(${sel.lost})` : ""}</h3>
    ${sel.lost ? `<ul class="lost">${sel.lost_pairs.map((p, i) => `<li data-i="${i}" class="${S.focus && S.focus.i === i ? "on" : ""}">
        <b>${esc(p.src)}</b> → <b>${esc(p.subnet)}</b> <span class="badge split">${esc(p.status)}${p.drop_at ? " @ " + esc(p.drop_at) : ""}</span>
        <div class="path">${p.path.map(esc).join(" → ")}</div></li>`).join("")}</ul>`
      : `<p class="muted">No flow loses connectivity with this plan${S.sim.failed.length ? " under this failure" : ""}.</p>`}`;
  if (t) { $("#trSrc").value = t.src; $("#trDst").value = t.subnet; }
  $("#trBtn").onclick = () => runSim({ src: $("#trSrc").value, subnet: $("#trDst").value });
  $$(".lost li", side).forEach((li) => (li.onclick = () => {
    const i = Number(li.dataset.i);
    const p = sel.lost_pairs[i];
    S.router = null;
    runSim({src: p.src, subnet: p.subnet});
  }));
}

/* ------------------------------------------------------------ tabs */
function bar(v, max, bad = false) {
  const w = max > 0 ? Math.max(2, Math.round((v / max) * 60)) : 0;
  return `<span class="bar ${bad ? "bad" : ""}"><span class="b" style="width:${v ? w : 0}px"></span>${fmt(v)}</span>`;
}

function renderComparison() {
  const rows = S.analysis.comparison;
  const by = Object.fromEntries(rows.map((r) => [r.strategy, r]));
  const max = (k) => Math.max(...rows.map((r) => r[k]));
  const cols = [
    ["adverts", "Adverts", true], ["routes_mean", "Routes / router", true], ["ctrl_prefixes", "Control prefixes", true],
    ["normal_lost", "Normal loss", false, true], ["fail_lost_pairs", "Failure loss (pairs)", true, true],
    ["scenarios_with_loss", "Scenarios w/ loss", false, true], ["stretch_normal_max", "Stretch normal max"],
    ["stretch_fail_max", "Stretch failure max"], ["churn_mean", "Churn / failure", true], ["addr_util", "Address util."],
  ];
  let headline = "";
  if (by.flat && by.maxagg && by.fasp) {
    const span = by.flat.routes_mean - by.maxagg.routes_mean;
    const kept = span > 0 ? (by.flat.routes_mean - by.fasp.routes_mean) / span : 0;
    headline = `<p class="note">The proposed plan keeps <b>${Math.round(kept * 100)}%</b> of maximum aggregation's routing-table reduction
      (${fmt(by.flat.routes_mean, 1)} → ${fmt(by.fasp.routes_mean, 1)} routes per router vs ${fmt(by.maxagg.routes_mean, 1)})
      while failure loss goes from <b>${by.maxagg.fail_lost_pairs}</b> lost pairs to <b>${by.fasp.fail_lost_pairs}</b>
      across ${by.fasp.scenarios} scenarios. Click a row to inspect that strategy.</p>`;
  }
  $("#tab-cmp").innerHTML = headline + `<table><thead><tr><th>Strategy</th>${cols.map((c) => `<th class="num">${c[1]}</th>`).join("")}</tr></thead><tbody>
    ${rows.map((r) => `<tr class="clickable ${r.strategy === "fasp" ? "hi" : ""}" data-s="${r.strategy}"><td><b>${esc(r.label)}</b><div class="muted small">${esc(r.strategy)}</div></td>
      ${cols.map(([k, , showBar, isBad]) => `<td class="num">${showBar ? bar(r[k], max(k), isBad && r[k] > 0) : k === "addr_util" ? Math.round(r[k] * 100) + "%" : (isBad && r[k] > 0 ? `<span style="color:var(--bad)">${fmt(r[k])}</span>` : fmt(r[k], k.startsWith("stretch") ? 3 : 2))}</td>`).join("")}</tr>`).join("")}
    </tbody></table>`;
  $$("#tab-cmp tr.clickable").forEach((tr) => (tr.onclick = () => setStrategy(tr.dataset.s)));
}

function renderAddr() {
  const p = S.analysis.plans[S.strategy];
  const areaName = Object.fromEntries(S.analysis.graph.nodes.map((n) => [n.area, n.area === 0 ? "Backbone" : n.building]));
  const blocks = Object.entries(p.domain_blocks).map(([a, n]) => `<span class="chip">area ${esc(a)} · ${esc(areaName[a] || "")}: ${esc(n)}</span>`).join("");
  const adverts = Object.entries(p.adverts).map(([a, ads]) => `<h3>Area ${esc(a)} · ${esc(areaName[a] || "")} — ${ads.length} advertised</h3>
    <table><tbody>${ads.map((ad) => `<tr><td class="mono">${esc(ad.prefix)}</td><td><span class="badge ${ad.kind}">${ad.kind}</span></td>
      <td>${ad.members.map(esc).join(", ")}</td><td class="muted">${esc(ad.note)}</td></tr>`).join("")}</tbody></table>`).join("");
  $("#tab-addr").innerHTML = `<p class="note"><b>${esc(S.analysis.labels[S.strategy])}</b> · ${esc(p.allocation_method)} VLSM ·
      ${p.advert_count} prefixes advertised (${p.summary_count} summaries) · address utilization ${Math.round(p.address_utilization * 100)}%</p>
    <div class="chips">${blocks}</div>
    <div class="grid2"><div><h3>Allocation</h3><table><thead><tr><th>Subnet</th><th>Area</th><th>Router</th><th class="num">Hosts</th>
      <th>Prefix</th><th>Mask</th><th>Gateway</th><th>Range</th></tr></thead><tbody>
      ${p.subnets.map((r) => `<tr><td>${esc(r.subnet)}</td><td>${r.area}</td><td>${esc(r.router)}</td><td class="num">${r.hosts_needed}/${r.usable}</td>
        <td class="mono">${esc(r.prefix)}</td><td class="mono">${esc(r.mask)}</td><td class="mono">${esc(r.gateway)}</td>
        <td class="mono">${esc(r.first_host)} – ${esc(r.last_host)}</td></tr>`).join("")}</tbody></table></div>
    <div><h3>Advertised prefixes</h3>${adverts}</div></div>`;
}

function renderWhy() {
  const p = S.analysis.plans[S.strategy];
  const ds = p.decisions || [];
  const intro = {
    flat: "Every subnet is advertised on its own, so no summary can hide a failure (largest tables).",
    maxagg: "One summary per building, chosen without looking at failures or path costs.",
    shaikh: `Budgeted dynamic programme over the address trie (Shaikh et al.): for a budget of ${esc(p.meta.budget ?? "?")} prefixes it minimises path-cost error in normal operation only.`,
    "fasp-sel": "Failure-aware selection on the conventional allocation: a summary is kept only if no border router would blackhole any member in any scenario and stretch stays within τ.",
    fasp: "Failure-aware allocation groups subnets that behave alike under failures into aligned blocks, then the failure-aware selection and a full simulation check decide each summary.",
  }[S.strategy] || "";
  $("#tab-why").innerHTML = `<p class="note">${intro}</p>` + (ds.length ? `<table><thead><tr><th>Decision</th><th>Prefix</th><th>Members</th><th>Reason</th></tr></thead><tbody>
    ${ds.map((d) => `<tr><td><span class="badge ${d.verdict}">${esc(d.verdict)}</span></td><td class="mono">${esc(d.prefix)}</td>
      <td>${d.members.map(esc).join(", ")}</td><td>${esc(d.reason || (d.verdict === "summarize" ? "safe in all checked scenarios" : ""))}</td></tr>`).join("")}
    </tbody></table>` : `<p class="muted">This strategy records no per-summary decisions.</p>`);
}

function renderScenarios() {
  const a = S.analysis;
  const strategies = a.strategies;
  const maxLost = Math.max(1, ...strategies.flatMap((s) => a.scenario_loss[s].map((r) => r.lost)));
  const bad = css("--bad");
  $("#tab-scen").innerHTML = `<p class="note">Flow pairs (router → subnet) that are reachable with per-subnet routing but lost with each plan. Click a row to apply that failure in the graph.</p>
    <table><thead><tr><th>Scenario</th>${strategies.map((s) => `<th class="num">${esc(a.labels[s])}</th>`).join("")}</tr></thead><tbody>
    ${a.scenarios.map((sc, i) => `<tr class="clickable" data-i="${i}"><td class="mono">${esc(sc.name)}</td>
      ${strategies.map((s) => {
        const r = a.scenario_loss[s][i];
        const alpha = r.lost ? 0.15 + 0.75 * (r.lost / maxLost) : 0;
        return `<td class="heat" style="background:${r.lost ? hexA(bad, alpha) : "transparent"}">${r.lost || "·"}</td>`;
      }).join("")}</tr>`).join("")}
    </tbody></table>`;
  $$("#tab-scen tr.clickable").forEach((tr) => (tr.onclick = () => {
    const sc = a.scenarios[Number(tr.dataset.i)];
    S.failed = new Set(sc.failed);
    S.focus = null;
    runSim();
    $("#cy").scrollIntoView({ behavior: "smooth", block: "center" });
  }));
}

function hexA(color, alpha) {
  const m = color.match(/^#([0-9a-f]{6})$/i);
  if (!m) return color;
  const n = parseInt(m[1], 16);
  return `rgba(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}, ${alpha.toFixed(2)})`;
}

function renderExport() {
  $("#tab-exp").innerHTML = `<p class="note">Downloads use the strategy selected at the top (<b>${esc(S.analysis.labels[S.strategy])}</b>) and the current protocol (${S.protocol.toUpperCase()}).</p>
    <div class="exp">
      <button class="primary" data-target="frr">FRR configs + containerlab (.zip)</button>
      <button class="ghost" data-target="iol">Cisco IOL configs (.zip)</button>
      <button class="ghost" id="dlPlan">Plan JSON</button>
      <button class="ghost" id="dlCsv">Comparison CSV</button>
    </div><p class="note" id="expMsg"></p>`;
  $$("#tab-exp [data-target]").forEach((b) => (b.onclick = () => downloadConfigs(b.dataset.target)));
  $("#dlPlan").onclick = () => saveBlob(new Blob([JSON.stringify(S.analysis.plans[S.strategy], null, 2)], { type: "application/json" }),
    `${S.analysis.name}-${S.strategy}-${S.protocol}-plan.json`);
  $("#dlCsv").onclick = () => {
    const rows = S.analysis.comparison;
    const keys = Object.keys(rows[0]);
    const csv = [keys.join(","), ...rows.map((r) => keys.map((k) => JSON.stringify(r[k] ?? "")).join(","))].join("\n");
    saveBlob(new Blob([csv], { type: "text/csv" }), `${S.analysis.name}-${S.protocol}-comparison.csv`);
  };
}

async function downloadConfigs(target) {
  const msg = $("#expMsg");
  msg.textContent = "";
  try {
    const res = await api("/api/configs", { ...baseReq(), strategy: S.strategy, target });
    const cd = res.headers.get("Content-Disposition") || "";
    const name = (cd.match(/filename="([^"]+)"/) || [])[1] || `${target}.zip`;
    saveBlob(await res.blob(), name);
    msg.textContent = `Downloaded ${name}`;
  } catch (e) {
    msg.textContent = e.message;
    msg.style.color = "var(--bad)";
  }
}

function saveBlob(blob, name) {
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = name;
  document.body.appendChild(a);
  a.click();
  setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 500);
}

function setStrategy(s) {
  S.router = null;
  S.strategy = s;
  $("#strategy").value = s;
  S.focus = null;
  renderAddr(); renderWhy(); renderExport();
  runSim();
}

/* ------------------------------------------------------------ wiring */
function init() {
  S.paths = new FasplanPaths();
  const traceSelectedFlow = () => {S.router = null; runSim({src: $("#flowSource").value, subnet: $("#flowDestination").value});};
  $("#traceFlow").onclick = traceSelectedFlow;
  $("#flowSource").onchange = traceSelectedFlow;
  $("#flowDestination").onchange = traceSelectedFlow;
  $("#demoFailure").onclick = () => {S.failed = new Set(S.demo.failed); S.router = null; runSim();};
  $("#playPath").onclick = () => S.paths.play();
  $("#stepPath").onclick = () => S.paths.next();
  $("[data-playback='play']").onclick = () => S.paths.play();
  $("[data-playback='next']").onclick = () => S.paths.next();
  $$(".seg button").forEach((b) => (b.onclick = () => {
    if (S.protocol === b.dataset.proto) return;
    S.protocol = b.dataset.proto;
    $$(".seg button").forEach((x) => x.classList.toggle("on", x === b));
    analyze();
  }));
  $("#strategy").onchange = (e) => setStrategy(e.target.value);
  $("#analyze").onclick = analyze;
  $("#genBtn").onclick = generate;
  $("#clearFail").onclick = () => { S.failed.clear(); S.router = null; S.focus = null; runSim(); };
  $$('[data-graph-action]').forEach((button) => button.onclick = () => changeGraphView(button.dataset.graph, button.dataset.graphAction, button));
  $$('[data-expanded-action]').forEach((button) => button.onclick = () => expandedGraph && changeGraphView(expandedGraph.id, button.dataset.expandedAction, button));
  $("#closeGraphDialog").onclick = () => $("#graphDialog").close();
  $("#graphDialog").addEventListener("close", () => {
    if (!expandedGraph) return;
    const {id, canvas, placeholder, opener} = expandedGraph;
    const cy = graphFor(id);
    cy?.unmount();
    placeholder.replaceWith(canvas);
    cy?.mount(canvas);
    expandedGraph = null;
    document.body.classList.remove("graph-expanded");
    requestAnimationFrame(() => fitGraph(graphFor(id)));
    opener?.focus();
  });
  const centerExpandedHop = (event) => {
    if (!expandedGraph || (event.type === "keydown" && !["Enter", " "].includes(event.key))) return;
    const hop = event.target.closest(".hop");
    if (!hop) return;
    event.preventDefault();
    const cy = graphFor(expandedGraph.id), view = S.paths.views.find((v) => v.cy === cy);
    const router = view?.trace.path[Number(hop.dataset.hop)];
    if (router) {cy.zoom(Math.max(0.75, cy.zoom())); cy.center(cy.getElementById(router));}
  };
  $("#expandedRoute").onclick = centerExpandedHop;
  $("#expandedRoute").onkeydown = centerExpandedHop;
  const sync = () => { $("#tnOut").textContent = Number($("#tauN").value).toFixed(2); $("#tfOut").textContent = Number($("#tauF").value).toFixed(2); };
  $("#tauN").oninput = sync; $("#tauF").oninput = sync;
  $("#tauN").onchange = analyze; $("#tauF").onchange = analyze;
  $("#yaml").addEventListener("keydown", (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key === "Enter") { e.preventDefault(); analyze(); }
    if (e.key === "Tab") {
      e.preventDefault();
      const t = e.target, s = t.selectionStart;
      t.value = t.value.slice(0, s) + "  " + t.value.slice(t.selectionEnd);
      t.selectionStart = t.selectionEnd = s + 2;
    }
  });
  $$(".tabbar button").forEach((b) => (b.onclick = () => {
    $$(".tabbar button").forEach((x) => x.classList.toggle("on", x === b));
    $$(".tab").forEach((t) => t.classList.toggle("on", t.id === "tab-" + b.dataset.tab));
  }));
  window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => {
    if (S.analysis) { buildGraph(); highlight(); S.paths.update(); renderScenarios(); }
  });
  if (typeof cytoscape === "undefined") status("Cytoscape failed to load (offline?)", "err");
  loadExamples();
}

init();
