"""Figures for the report/paper from results/experiments.csv (matplotlib, PNG + SVG)."""

from __future__ import annotations

import statistics
from pathlib import Path

from .experiments import _ci, load, retained_reduction
from .strategies import LABELS, STRATEGIES

# Fixed categorical order (validated reference palette, light mode); colour follows the strategy.
COLORS = {"fasp": "#2a78d6", "maxagg": "#eb6834", "shaikh": "#1baf7a", "fasp-sel": "#eda100", "flat": "#e87ba4"}
TEXT, MUTED, GRID, SURFACE = "#0b0b0b", "#52514e", "#e4e3df", "#fcfcfb"
ORDER = ["flat", "maxagg", "shaikh", "fasp-sel", "fasp"]


def _style(plt):
    plt.rcParams.update({
        "figure.facecolor": SURFACE, "axes.facecolor": SURFACE, "savefig.facecolor": SURFACE,
        "axes.edgecolor": GRID, "axes.labelcolor": MUTED, "xtick.color": MUTED, "ytick.color": MUTED,
        "text.color": TEXT, "axes.grid": True, "axes.grid.axis": "y", "grid.color": GRID, "grid.linewidth": 0.8,
        "axes.axisbelow": True, "axes.spines.top": False, "axes.spines.right": False,
        "font.size": 10, "axes.titlesize": 11, "axes.titleweight": "bold", "legend.frameon": False,
    })


def _bars(ax, rows, metric, protocols, fmt="{:.2g}", pct=False):
    width = 0.8 / len(ORDER)
    for i, s in enumerate(ORDER):
        means, errs = [], []
        for p in protocols:
            m, ci = _ci([float(r[metric]) for r in rows if r["protocol"] == p and r["strategy"] == s])
            means.append(m * (100 if pct else 1))
            errs.append(ci * (100 if pct else 1))
        xs = [j + (i - (len(ORDER) - 1) / 2) * width for j in range(len(protocols))]
        bars = ax.bar(xs, means, width * 0.92, yerr=errs, color=COLORS[s], label=LABELS[s],
                      error_kw={"elinewidth": 1, "ecolor": MUTED, "capsize": 2})
        if s in ("fasp", "maxagg"):
            for b, m in zip(bars, means):
                ax.annotate(fmt.format(m) + ("%" if pct else ""), (b.get_x() + b.get_width() / 2, b.get_height()),
                            xytext=(0, 3), textcoords="offset points", ha="center", fontsize=8, color=TEXT)
    ax.set_xticks(range(len(protocols)), [p.upper() for p in protocols])


def make_all(csv_path: str | Path, outdir: str | Path | None = None) -> list[Path]:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    _style(plt)
    rows = load(csv_path)
    outdir = Path(outdir or Path(csv_path).parent / "figures")
    outdir.mkdir(parents=True, exist_ok=True)
    protocols = sorted({r["protocol"] for r in rows})
    written = []

    def save(fig, name):
        for ext in ("png", "svg"):
            p = outdir / f"{name}.{ext}"
            fig.savefig(p, dpi=180, bbox_inches="tight")
            written.append(p)
        plt.close(fig)

    # 1. Routing table size vs failure loss: the core trade-off
    fig, axes = plt.subplots(1, 2, figsize=(10, 3.8))
    _bars(axes[0], rows, "routes_mean", protocols, "{:.1f}")
    axes[0].set_title("Routes per router (normal operation)")
    axes[0].set_ylabel("LAN prefixes in FIB")
    _bars(axes[1], rows, "fail_loss_rate", protocols, "{:.2f}", pct=True)
    axes[1].set_title("Traffic lost during single/double link failures")
    axes[1].set_ylabel("% of reachable src-dst pairs dropped")
    handles, labels = axes[0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="lower center", ncol=5, bbox_to_anchor=(0.5, -0.06))
    fig.tight_layout(rect=(0, 0.06, 1, 1))
    save(fig, "tradeoff_routes_vs_loss")

    # 1b. Generalisation: double failures the planner never saw
    if "holdout_loss_rate" in rows[0]:
        fig, ax = plt.subplots(figsize=(7, 4.2))
        _bars(ax, rows, "holdout_loss_rate", protocols, "{:.2f}", pct=True)
        ax.set_title("Loss on held-out double-link failures")
        ax.set_ylabel("% of reachable src-dst pairs dropped")
        fig.legend(*ax.get_legend_handles_labels(), loc="lower center", ncol=3, fontsize=8,
                   bbox_to_anchor=(0.5, -0.08))
        fig.tight_layout(rect=(0, 0.1, 1, 1))
        save(fig, "holdout_loss")

    # 2. Reduction retained by profile (fasp vs shaikh vs fasp-sel)
    keep = retained_reduction(rows)
    profiles = [p for p in ("low", "mixed", "high") if any(r["profile"] == p for r in rows)]
    fig, axes = plt.subplots(1, len(protocols), figsize=(5 * len(protocols), 3.6), sharey=True)
    axes = axes if len(protocols) > 1 else [axes]
    strategies = ["shaikh", "fasp-sel", "fasp"]
    width = 0.8 / len(strategies)
    for ax, proto in zip(axes, protocols):
        for i, s in enumerate(strategies):
            ms, es = [], []
            for prof in profiles:
                vals = [v for k, v in keep.items() if k[0] == proto and k[2] == prof and k[4] == s]
                m, ci = _ci(vals) if vals else (0, 0)
                ms.append(m * 100)
                es.append(ci * 100)
            xs = [j + (i - 1) * width for j in range(len(profiles))]
            ax.bar(xs, ms, width * 0.92, yerr=es, color=COLORS[s], label=LABELS[s],
                   error_kw={"elinewidth": 1, "ecolor": MUTED, "capsize": 2})
        ax.set_xticks(range(len(profiles)), [f"{p} redundancy" for p in profiles])
        ax.set_title(proto.upper())
        ax.axhline(100, color=MUTED, lw=1, ls="--")
        ax.annotate("max-aggregation level", (len(profiles) - 0.5, 100), xytext=(0, 3),
                    textcoords="offset points", ha="right", fontsize=8, color=MUTED)
    axes[0].set_ylabel("% of max-aggregation route reduction kept")
    handles, labels = axes[0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="lower center", ncol=3, bbox_to_anchor=(0.5, -0.06))
    fig.tight_layout(rect=(0, 0.06, 1, 1))
    save(fig, "reduction_retained")

    # 3. Path stretch
    fig, axes = plt.subplots(1, 2, figsize=(10, 3.6))
    _bars(axes[0], rows, "stretch_normal_max", protocols, "{:.2f}")
    axes[0].set_title("Worst path stretch, normal operation")
    axes[0].set_ylim(bottom=1)
    _bars(axes[1], rows, "stretch_fail_p95", protocols, "{:.2f}")
    axes[1].set_title("95th-percentile stretch under failures")
    axes[1].set_ylim(bottom=1)
    for ax in axes:
        ax.set_ylabel("cost / per-subnet routing cost")
    handles, labels = axes[0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="lower center", ncol=5, bbox_to_anchor=(0.5, -0.06))
    fig.tight_layout(rect=(0, 0.06, 1, 1))
    save(fig, "stretch")

    # 4. Control plane: prefixes carried and churn per failure
    fig, axes = plt.subplots(1, 2, figsize=(10, 3.6))
    _bars(axes[0], rows, "ctrl_prefixes", protocols, "{:.0f}")
    axes[0].set_title("Control-plane prefixes (LSAs / EIGRP adverts)")
    _bars(axes[1], rows, "churn_mean", protocols, "{:.1f}")
    axes[1].set_title("Routing-table entries changed per failure")
    handles, labels = axes[0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="lower center", ncol=5, bbox_to_anchor=(0.5, -0.06))
    fig.tight_layout(rect=(0, 0.06, 1, 1))
    save(fig, "control_plane")

    # 5. Cost of the method: address utilisation
    fig, ax = plt.subplots(figsize=(5, 3.4))
    _bars(ax, [r for r in rows if r["protocol"] == protocols[0]], "addr_util", [protocols[0]], "{:.0f}", pct=True)
    ax.set_xticks([0], ["all topologies"])
    ax.set_title("Address-block utilisation (the price of safe grouping)")
    ax.set_ylabel("% of allocated LAN space in use")
    fig.legend(*ax.get_legend_handles_labels(), loc="lower center", ncol=2, fontsize=8,
               bbox_to_anchor=(0.5, -0.12))
    fig.tight_layout(rect=(0, 0.14, 1, 1))
    save(fig, "address_utilisation")

    # 6. Loss by building count (scaling)
    bcounts = sorted({r["buildings"] for r in rows})
    fig, axes = plt.subplots(1, len(protocols), figsize=(5 * len(protocols), 3.4), sharey=True)
    axes = axes if len(protocols) > 1 else [axes]
    for ax, proto in zip(axes, protocols):
        for s in ["maxagg", "shaikh", "fasp"]:
            ys = []
            for b in bcounts:
                vals = [r["fail_loss_rate"] * 100 for r in rows
                        if r["protocol"] == proto and r["strategy"] == s and r["buildings"] == b]
                ys.append(statistics.fmean(vals) if vals else 0)
            ax.plot(bcounts, ys, color=COLORS[s], lw=2, marker="o", ms=6, label=LABELS[s])
        ax.set_xticks(bcounts)
        ax.set_xlabel("buildings")
        ax.set_title(proto.upper())
    axes[0].set_ylabel("% pairs lost under failures")
    axes[0].legend()
    fig.tight_layout()
    save(fig, "loss_by_size")
    return written
