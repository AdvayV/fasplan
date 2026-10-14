/* Visual forwarding playback. Paths always come from /api/simulate. */
"use strict";
class FasplanPaths {
  constructor() {
    this.views = []; this.mini = {}; this.graphKey = ""; this.timer = null; this.step = 0;
    this.resizeObserver = new ResizeObserver(() => {
      this.views.forEach((view) => {view.cy.resize(); view.cy.fit(undefined, 30);});
    });
    ["cy", "cy-maxagg", "cy-fasp"].forEach((id) => this.resizeObserver.observe($("#" + id)));
  }
  stop() {
    clearInterval(this.timer); this.timer = null;
    this.views.forEach((view) => view.cy?.getElementById("visual-packet").stop());
    $("#playPath").textContent = "Replay path";
  }
  update() {
    this.stop();
    if (!S.sim || !S.cy) return;
    const key = JSON.stringify([S.analysis.graph, S.analysis.protocol, css("--panel")]);
    if (key !== this.graphKey) {
      Object.values(this.mini).forEach((cy) => cy.destroy());
      this.mini = {};
      const elements = S.cy.elements().filter((e) => !e.hasClass("packet")).map((e) => ({
        group: e.group(), data: {...e.data(), label: e.data("baseLabel") || e.data("label")}, position: {...e.position()},
      }));
      for (const strategy of ["maxagg", "fasp"]) {
        this.mini[strategy] = cytoscape({container: $("#cy-" + strategy), elements: JSON.parse(JSON.stringify(elements)),
          style: cyStyle(), layout: {name: "preset", padding: 30}, autoungrabify: true,
          wheelSensitivity: 0.2, minZoom: 0.2, maxZoom: 3});
        this.mini[strategy].fit(undefined, 30);
      }
      this.graphKey = key;
    }
    this.views = [
      {cy: S.cy, trace: S.sim.selected.trace, result: S.sim.selected, walk: "routeWalk", label: S.sim.selected.label},
      {cy: this.mini.maxagg, trace: S.sim.contrast.maxagg.trace, result: S.sim.contrast.maxagg, walk: "maxaggWalk", label: "Maximum aggregation", outcome: "maxaggOutcome"},
      {cy: this.mini.fasp, trace: S.sim.contrast.fasp.trace, result: S.sim.contrast.fasp, walk: "faspWalk", label: "Proposed plan", outcome: "faspOutcome"},
    ].filter((v) => v.trace);
    const owner = S.analysis.graph.nodes.find((n) => n.subnets.some((s) => s.name === S.flow.subnet))?.id;
    for (const view of this.views) {
      this.paint(view, owner);
      const t = view.trace;
      const delivered = t.status === "delivered";
      const outcome = delivered ? "Delivered" : t.status === "blackhole" ? "Discarded at " + t.drop_at : t.status + " at " + t.drop_at;
      const route = t.path.map((router, i) => `<li class="hop" data-hop="${i}"><span>${i === 0 ? "Source" : "Hop " + (i + 1)}</span><b>${esc(router)}</b></li>`).join("");
      $("#" + view.walk).innerHTML = `<div class="route-title"><b>${esc(S.flow.src)} → ${esc(S.flow.subnet)}</b><span class="route-outcome ${delivered ? "good" : "bad"}">${esc(outcome)} · cost ${fmt(t.cost)}</span></div><ol class="hop-list">${route}</ol>`;
      if (view.outcome) {
        $("#" + view.outcome).textContent = outcome + " · " + view.result.lost + " lost pairs";
        $("#" + view.outcome).className = "route-outcome " + (delivered ? "good" : "bad");
      }
      const container = view.cy.container();
      container.setAttribute("aria-label", view.label + ": " + t.path.join(" to ") + ". " + outcome);
    }
    this.step = 0;
    this.move(false);
    $("#playPath").disabled = !this.views.length;
    $("#stepPath").disabled = !this.views.length;
    $("#playbackStatus").textContent = "Arrows show the computed route. Replay or step through the hops; playback is illustrative, not convergence timing.";
  }
  paint(view, owner) {
    const cy = view.cy;
    const path = view.trace.path;
    cy.batch(() => {
      cy.getElementById("visual-packet").remove();
      cy.elements().removeClass("onpath flatpath route-forward route-reverse route-source route-destination route-stop failed");
      cy.nodes().forEach((n) => {if (n.data("baseLabel")) n.data("label", n.data("baseLabel"));});
      S.failed.forEach((id) => cy.getElementById(id).addClass("failed"));
      path.forEach((router, i) => {
        const node = cy.getElementById(router);
        node.addClass("onpath");
        const at = path.map((r, index) => r === router ? index + 1 : null).filter(Boolean);
        node.data("label", "Hop " + at.join("/") + " · " + router);
      });
      for (let i = 0; i + 1 < path.length; i++) {
        const edge = cy.edges().filter((e) => [path[i], path[i + 1]].includes(e.data("source")) && [path[i], path[i + 1]].includes(e.data("target")));
        edge.addClass("onpath");
        edge.addClass(edge.data("source") === path[i] ? "route-forward" : "route-reverse");
      }
      cy.getElementById(path[0]).addClass("route-source");
      if (owner) cy.getElementById(owner).addClass("route-destination");
      if (view.trace.drop_at) cy.getElementById(view.trace.drop_at).addClass("route-stop");
      if (path.length) cy.add({group: "nodes", data: {id: "visual-packet"}, classes: "packet", position: {...cy.getElementById(path[0]).position()}, selectable: false, grabbable: false});
    });
  }
  move(animate) {
    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    for (const view of this.views) {
      const index = Math.min(this.step, view.trace.path.length - 1);
      const target = view.cy.getElementById(view.trace.path[index]);
      const packet = view.cy.getElementById("visual-packet");
      const end = index === view.trace.path.length - 1;
      packet.removeClass("delivered dropped");
      if (end) packet.addClass(view.trace.status === "delivered" ? "delivered" : "dropped");
      packet.stop();
      if (animate && !reduced) packet.animate({position: {...target.position()}}, {duration: 550, easing: "linear"});
      else packet.position({...target.position()});
      $$("#" + view.walk + " .hop").forEach((hop) => hop.classList.toggle("active", Number(hop.dataset.hop) === index));
    }
  }
  maxStep() { return Math.max(0, ...this.views.map((v) => v.trace.path.length - 1)); }
  next() {
    this.stop();
    this.step = this.step >= this.maxStep() ? 0 : this.step + 1;
    this.move(false);
    this.describe();
  }
  describe() {
    $("#playbackStatus").textContent = this.step >= this.maxStep()
      ? "Playback complete. A discarded flow stops at its drop router; a delivered flow reaches the destination router."
      : "Playback hop " + (this.step + 1) + ". The packet marker follows each strategy's computed route.";
  }
  play() {
    if (this.timer) {this.stop(); return;}
    this.step = 0; this.move(false); this.describe();
    if (!this.maxStep()) return;
    $("#playPath").textContent = "Pause replay";
    this.timer = setInterval(() => {
      this.step++; this.move(true); this.describe();
      if (this.step >= this.maxStep()) {
        clearInterval(this.timer); this.timer = null;
        $("#playPath").textContent = "Replay path";
      }
    }, 850);
  }
}
