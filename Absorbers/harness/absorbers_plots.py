"""Figures for the Absorbers paper, from the generated JSON only.

    python -m harness.absorbers_plots      # writes paper/absorbers/figures/*.{pdf,png}

Three figures, one claim each. Colours are the validated categorical slots in
fixed order plus one status colour for "leaked"; every figure carries direct
labels, so identity never rests on colour alone. PDF metadata is stripped so the
files are byte-stable across runs.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
FIG = ROOT / "paper" / "absorbers" / "figures"

SWEEPS = [("results", "deepseek-chat\nT=0"), ("results-reasoner", "DeepSeek thinking\n(T ignored)"),
          ("results-local", "gemma3:4b\nT=0"), ("results-phi4-mini", "phi4-mini\nT=0")]
SHORT = {"results": "deepseek-chat", "results-reasoner": "DeepSeek thinking",
         "results-local": "gemma3:4b", "results-phi4-mini": "phi4-mini"}
CHAINS = [("results-chain-deepseek", "deepseek-chat"), ("results-chain-gemma3", "gemma3:4b"),
          ("results-chain-phi4", "phi4-mini")]

# Validated 2026-09-03 with the dataviz palette validator (categorical, light surface):
# ["#2a78d6","#eb6834","#1baf7a"] passes lightness, chroma, CVD and normal-vision
# separation; the green carries a contrast WARN, relieved by direct labels on every mark.
SLOT1, SLOT2, SLOT3 = "#2a78d6", "#eb6834", "#1baf7a"
INK, INK2, GRID, SURFACE = "#0b0b0b", "#52514e", "#e6e5e1", "#fcfcfb"
LEAK = "#d03b3b"   # status critical, reserved; always paired with a written count

plt.rcParams.update({
    "font.size": 8, "axes.titlesize": 8.5, "axes.labelsize": 8, "xtick.labelsize": 7.5,
    "ytick.labelsize": 7.5, "legend.fontsize": 7.5, "axes.edgecolor": INK2, "axes.linewidth": 0.6,
    "xtick.color": INK2, "ytick.color": INK2, "text.color": INK, "axes.labelcolor": INK,
    "figure.facecolor": SURFACE, "axes.facecolor": SURFACE, "pdf.fonttype": 42, "savefig.dpi": 200,
})


def _load():
    out = []
    for d, label in SWEEPS:
        p = ROOT / d / "absorbers.json"
        if p.exists():
            out.append((d, label, json.loads(p.read_text())))
    return out


def _load_chains():
    out = []
    for d, label in CHAINS:
        p = ROOT / d / "absorbers_chain.json"
        if p.exists():
            out.append((d, label, json.loads(p.read_text())))
    return out


def _style(ax, axis="x"):
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    ax.grid(True, axis=axis, color=GRID, linewidth=0.6, zorder=0)
    ax.set_axisbelow(True)


def _save(fig, name):
    FIG.mkdir(parents=True, exist_ok=True)
    fig.savefig(FIG / f"{name}.pdf", bbox_inches="tight",
                metadata={"CreationDate": None, "Producer": None, "Creator": None})
    fig.savefig(FIG / f"{name}.png", bbox_inches="tight")
    plt.close(fig)


def fig_e2(data):
    """Identical input, identical output? Text level against the record the core consumes."""
    rows = [(label, a["E2_functionality_T0"]["A3"]) for _, label, a in data
            if "A3" in a["E2_functionality_T0"]]
    fig, ax = plt.subplots(figsize=(3.4, 0.62 * len(rows) + 1.25))
    y = list(range(len(rows)))[::-1]
    h = 0.32
    for i, (label, e2) in zip(y, rows):
        t = 100 * (e2["text_identical_frac"] or 0)
        r = 100 * (e2["record_identical_frac"] or 0)
        ax.barh(i + h / 2 + 0.02, t, height=h, color=SLOT1, edgecolor=SURFACE, linewidth=1, zorder=3)
        ax.barh(i - h / 2 - 0.02, r, height=h, color=SLOT2, edgecolor=SURFACE, linewidth=1, zorder=3)
        ax.text(t + 1.5, i + h / 2 + 0.02, f"{t:.1f}%", va="center", fontsize=7, color=INK)
        ax.text(r + 1.5, i - h / 2 - 0.02, f"{r:.1f}%", va="center", fontsize=7, color=INK)
    ax.set_yticks(y)
    ax.set_yticklabels([lbl for lbl, _ in rows])
    ax.set_xlim(0, 118)
    ax.set_xticks([0, 25, 50, 75, 100])
    ax.set_xlabel("identical-input pairs with identical output (%)")
    ax.axvline(100, color=INK2, linewidth=0.6, linestyle=":", zorder=2)
    n = rows[0][1]["identical_input_pairs"]
    ax.set_title(f"A3 parse, configured T=0, {n} identical-prompt pairs per model", loc="left")
    ax.legend(handles=[plt.Rectangle((0, 0), 1, 1, color=SLOT1), plt.Rectangle((0, 0), 1, 1, color=SLOT2)],
              labels=["raw text identical", "validated record identical"],
              loc="upper center", bbox_to_anchor=(0.5, -0.30), ncol=1, frameon=False)
    _style(ax)
    _save(fig, "fig_e2_functionality")


def fig_e1(data):
    """Where parse variance is absorbed and where it leaks, by what the oracle says."""
    rows = []
    for d, label, a in data:
        e1 = a["E1_absorption"].get("A3")
        if not e1:
            continue
        for oc in ("approve", "escalate", "reject"):
            s = e1["by_oracle_decision"].get(oc, {})
            if s.get("parse_record_varies", 0):
                rows.append((f"{SHORT[d]} / oracle {oc}", s["absorbed"], s["leaked"]))
    fig, ax = plt.subplots(figsize=(3.4, 0.30 * len(rows) + 1.35))
    y = list(range(len(rows)))[::-1]
    xmax = max(ab + lk for _, ab, lk in rows)
    for i, (label, ab, lk) in zip(y, rows):
        ax.barh(i, ab, height=0.58, color=INK2, edgecolor=SURFACE, linewidth=1, zorder=3)
        if lk:
            ax.barh(i, lk, left=ab, height=0.58, color=LEAK, edgecolor=SURFACE, linewidth=1, zorder=3)
        txt = f"{ab} absorbed" + (f", {lk} leaked" if lk else "")
        ax.text(ab + lk + 0.35, i, txt, va="center", fontsize=6.8, color=INK)
    ax.set_yticks(y)
    ax.set_yticklabels([r[0] for r in rows])
    ax.set_xlabel("requests whose ten A3 parse records differ")
    ax.set_xlim(0, xmax * 1.75)
    ax.set_title("Absorption tracks the oracle class, not the core", loc="left")
    ax.legend(handles=[plt.Rectangle((0, 0), 1, 1, color=INK2), plt.Rectangle((0, 0), 1, 1, color=LEAK)],
              labels=["decision unanimous (absorbed)", "decision varied (leaked)"],
              loc="upper center", bbox_to_anchor=(0.5, -0.22), ncol=1, frameon=False)
    _style(ax)
    _save(fig, "fig_e1_absorption")


def fig_e3(data):
    """Calls saved by each reuse policy, with divergence written on every bar.

    P1 and P2 coincide for A3 at this depth: the parse never reads a supplier
    field, so the only perturbations outside its output set are exactly the ones
    that leave its prompt byte-identical. They are drawn as one bar and the
    caption says so.
    """
    rows = []
    for d, label, a in data:
        e3 = a["E3_reuse"].get("A3")
        if not e3:
            continue
        p1, p2, p3 = (e3["policies"][k] for k in ("P1_input_hash", "P2_dependency_cone", "P3_oracle_irrelevance"))
        coincide = p1["reused"] == p2["reused"]
        rows.append((SHORT[d], p2, p3, coincide))
    fig, ax = plt.subplots(figsize=(3.4, 0.78 * len(rows) + 1.35))
    y = list(range(len(rows)))[::-1]
    h = 0.32
    for i, (label, p2, p3, coincide) in zip(y, rows):
        for off, pol, color in ((h / 2 + 0.02, p2, SLOT2), (-h / 2 - 0.02, p3, SLOT3)):
            x = 100 * (pol["calls_saved_frac"] or 0)
            ax.barh(i + off, x, height=h, color=color, edgecolor=SURFACE, linewidth=1, zorder=3)
            ax.text(x + 1.5, i + off, f"{x:.0f}%  div {100 * (pol['divergence_frac'] or 0):.2f}%",
                    va="center", fontsize=6.8, color=INK)
    ax.set_yticks(y)
    ax.set_yticklabels([r[0] for r in rows])
    ax.set_xlim(0, 118)
    ax.set_xticks([0, 25, 50, 75, 100])
    ax.set_xlabel("A3 parse calls saved (%), of 964 perturbation rows")
    ax.set_title("Reuse saves calls; divergence stays under one percent", loc="left")
    ax.legend(handles=[plt.Rectangle((0, 0), 1, 1, color=SLOT2), plt.Rectangle((0, 0), 1, 1, color=SLOT3)],
              labels=["P1 input hash = P2 dependency cone", "P3 oracle irrelevance (upper bound)"],
              loc="upper center", bbox_to_anchor=(0.5, -0.26), ncol=1, frameon=False)
    _style(ax)
    _save(fig, "fig_e3_reuse")
    return all(r[3] for r in rows)


def fig_e3_depth(chains):
    """The headline: cone reuse triggers on the same share on every model; safety does not follow.

    Bars are the TRIGGERED share (a property of the graph and the grid), not the
    scored one; the P2 bar turns the reserved status colour where the policy
    introduced an unsafe approval, and the count is written on it. The right margin
    carries each feeding step's record-identical share, so the reader sees that the
    configuration that broke is the one whose steps were both functional.
    """
    rows = []
    for d, label, a in chains:
        pol = a["E3_reuse"]["policies"]
        e2 = a["E2_functionality_T0"]
        rows.append((label, pol["P1_input_hash"], pol["P2_dependency_cone"], pol["P3_oracle_irrelevance"],
                     e2["c_parse"]["record_identical_frac"], e2["c_justify"]["record_identical_frac"]))
    fig, ax = plt.subplots(figsize=(3.4, 0.78 * len(rows) + 1.45))
    y = list(range(len(rows)))[::-1]
    h = 0.32
    for i, (label, p1, p2, p3, rp, rj) in zip(y, rows):
        t1 = 100 * (p1["calls_triggered_frac"] or 0)
        t2 = 100 * (p2["calls_triggered_frac"] or 0)
        t3 = 100 * (p3["calls_triggered_frac"] or 0)
        unsafe = p2["unsafe_introduced"]; req = p2.get("unsafe_requests", 0)
        ax.barh(i + h / 2 + 0.02, t1, height=h, color=SLOT1, edgecolor=SURFACE, linewidth=1, zorder=3)
        ax.barh(i - h / 2 - 0.02, t2, height=h, color=(LEAK if unsafe else SLOT2), edgecolor=SURFACE, linewidth=1, zorder=3)
        ax.plot([t3, t3], [i - h - 0.06, i - 0.06], color=INK2, linewidth=1.2, zorder=4)
        ax.text(t1 + 1.5, i + h / 2 + 0.02, f"{t1:.1f}% triggered", va="center", fontsize=6.8, color=INK)
        lab = f"{t2:.1f}% triggered, {unsafe} unsafe" + (f" ({req} request)" if unsafe else "")
        ax.text(t2 + 1.5, i - h / 2 - 0.02, lab, va="center", fontsize=6.8, color=INK)
        if i == y[0]:
            ax.text(t3, i - h - 0.12, "P3 oracle bound", ha="center", va="top", fontsize=6.2, color=INK2)
    # Each step's record-identical share lives in the category label, so identity
    # never collides with the direct labels on the bars.
    ax.set_yticks(y)
    ax.set_yticklabels([f"{label}\nparse {100*rp:.1f}% / justify {100*rj:.1f}%"
                        + ("\nboth steps functional" if (rp == 1.0 and rj == 1.0) else "")
                        for label, _, _, _, rp, rj in rows], fontsize=6.8)
    ax.set_xlim(0, 150)
    ax.set_xticks([0, 25, 50, 75, 100])
    ax.set_xlabel("feeding-step calls the policy triggers on (%)")
    ax.set_title("Cone reuse triggers on the same share on every model;\nsafety does not follow", loc="left")
    ax.legend(handles=[plt.Rectangle((0, 0), 1, 1, color=SLOT1), plt.Rectangle((0, 0), 1, 1, color=SLOT2),
                       plt.Rectangle((0, 0), 1, 1, color=LEAK)],
              labels=["P1 input hash", "P2 dependency cone", "P2 introduced unsafe approvals"],
              loc="upper center", bbox_to_anchor=(0.5, -0.30), ncol=1, frameon=False)
    _style(ax)
    _save(fig, "fig_e3_depth")


def publication_figures(root: Path, output: Path, reports: Path) -> None:
    from harness.make_absorbers_numbers import require_publication_inputs
    from harness.absorbers import _detection_curve, _profile_to_qs
    require_publication_inputs(root)
    chains = [(directory,label,json.loads((root/directory/"absorbers_chain.json").read_text())) for directory,label in CHAINS]
    schemes=[("P2_dependency_cone","Cone",SLOT2),("cert_eq","Equality",SLOT1),("cert_safe_valid","Safe valid",SLOT3),("cert_safe_all","Safe all",INK2)]
    fig,axes=plt.subplots(1,3,figsize=(7.0,2.9),sharex=True)
    tradeoff=[]
    for ax,(directory,label,data) in zip(axes,chains):
        cert=data["E8_decomposition"]["certificates"]
        rows=[]
        for name,short,color in schemes:
            value=data["E3_reuse"]["policies"][name] if name.startswith("P2") else cert["schemes"][name]
            changed=value["diverged"] if name.startswith("P2") else value["decision_changes"]
            rows.append((short,100*value["calls_saved_frac"],changed,value["unsafe_introduced"],color))
            tradeoff.append({"sweep":directory,"model":data["model"],"scheme":name,"rows":cert["rows"],"unscoreable_rows":value["unscoreable_rows"] if name.startswith("P2") else cert["unscoreable_rows"],"calls_saved_frac":value["calls_saved_frac"],"decision_changes":changed,"unsafe_introduced":value["unsafe_introduced"]})
        y=list(range(len(rows)))[::-1]
        for i,(short,saved,changed,unsafe,color) in zip(y,rows):
            ax.barh(i,saved,height=.6,color=color,zorder=3)
            ax.text(saved+1,i,f"{saved:.1f}%",va="center",fontsize=7)
            if unsafe:
                ax.text(1,i,f"{unsafe} unsafe",va="center",fontsize=7,color="white",weight="bold")
        ax.set_yticks(y,[r[0] for r in rows]);ax.set_xlim(0,80);ax.set_xticks([0,25,50,75]);ax.set_xlabel("Nominal feeding-step slots\nsaved (%)")
        ax.set_title(f"{label}\n{cert['rows']:,} rows; {cert['unscoreable_rows']} unscoreable")
        _style(ax)
    fig.tight_layout();_save(fig,"certificate-tradeoff")
    reports.mkdir(parents=True,exist_ok=True)
    (reports/"certificate-tradeoff.json").write_text(json.dumps({"schema_version":1,"saved_denominator":"2*all perturbation rows; nominal slots include unscoreable rows and fail-fast missing calls","results":tradeoff},indent=2,sort_keys=True)+"\n")
    # Only configurations with a measured mismatch have a nonzero detection curve.
    # The others are explicitly listed as zero-observed, not declared functional.
    scenarios=[];zero=[]
    for directory,label in SWEEPS[:3]:
        data=json.loads((root/directory/"absorbers.json").read_text())
        canary=data["E5_canary"]["A3"]
        if canary["mismatching_requests"]:
            scenarios.append((directory,"A3",label.replace("\n"," "),canary))
        else:zero.append({"sweep":directory,"step":"A3","requests":canary["requests"],"mismatching_requests":0})
    for directory,label,data in chains:
        for step,canary in data["E5_canary"].items():
            if canary["mismatching_requests"]:
                scenarios.append((directory,step,label+" / "+step.removeprefix("c_"),canary))
            else:zero.append({"sweep":directory,"step":step,"requests":canary["requests"],"mismatching_requests":0})
    fig,axes=plt.subplots(1,len(scenarios),figsize=(7.0,2.75),sharey=True)
    all_curves=[]
    for ax,(directory,step,label,canary) in zip(axes,scenarios):
        population=canary["requests"];ks=list(range(1,population+1))
        curves={}
        for name,indicator,color,style in [("per_recompute",False,SLOT1,"-"),("upper_bound",True,INK2,"--")]:
            curve,k95,at95=_detection_curve(_profile_to_qs(canary["pair_mismatch_profile"],indicator=indicator),ks)
            curves[name]={"probability_by_k":curve,"k_for_95":k95,"probability_at_k95":at95}
            ax.plot(ks,[100*curve[str(k)] for k in ks],color=color,linestyle=style,label="One recompute" if not indicator else "Observed-bad-set scenario")
        ax.axhline(95,color=GRID,linestyle=":",linewidth=1);ax.set_ylim(0,102);ax.set_xlim(0,population)
        ax.set_title(label+f"\n{canary['mismatching_requests']}/{population} requests ever mismatched")
        ax.set_xlabel("Requests sampled (k)");_style(ax,axis="y")
        all_curves.append({"sweep":directory,"step":step,"requests":population,"mismatching_requests":canary["mismatching_requests"],"pair_mismatch_profile":canary["pair_mismatch_profile"],"curves":curves})
    axes[0].set_ylabel("Detection probability (%)")
    fig.legend(*axes[0].get_legend_handles_labels(),loc="lower center",bbox_to_anchor=(.5,-.02),ncol=2,frameon=False)
    fig.tight_layout(rect=(0,.08,1,1));_save(fig,"canary-detection")
    (reports/"canary-detection.json").write_text(json.dumps({"schema_version":1,"assumptions":"Uniformly sampled requests without replacement; conditional independent one-call mismatch probabilities estimated from retained per-request pairs; stationary configuration and the enumerated request population. The observed-bad-set scenario assigns p=1 to requests that ever mismatched. It bounds the fitted empirical one-call model, not true future mismatch rates and not a statistical confidence bound. No future deployment or cross-population guarantee.","nonzero_scenarios":all_curves,"zero_observed_mismatch_scenarios":zero},indent=2,sort_keys=True)+"\n")
    fig_e3_depth(chains)


def main(argv=None):
    global ROOT,FIG
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--publication",action="store_true",help="Require six complete sweeps and render E8/canary figures")
    parser.add_argument("--root",type=Path,default=ROOT)
    parser.add_argument("--output-dir",type=Path)
    parser.add_argument("--reports-dir",type=Path)
    args=parser.parse_args(argv)
    ROOT=args.root.resolve()
    FIG=args.output_dir or (ROOT/"publication/figures" if args.publication else ROOT/"paper/absorbers/figures")
    if args.publication:
        publication_figures(ROOT,FIG,args.reports_dir or FIG.parent/"reports")
        print(f"wrote publication certificate, canary and depth figures to {FIG}")
        return 0
    data = _load()
    chains = _load_chains()
    if chains:
        fig_e3_depth(chains)
    if not data:
        print("no absorbers.json found", file=sys.stderr)
        return 1
    fig_e2(data)
    fig_e1(data)
    coincide = fig_e3(data)
    print(f"wrote {3 + (1 if chains else 0)} figures to {FIG} from {[d for d, _, _ in data] + [d for d, _, _ in chains]}")
    print(f"  P1 and P2 coincide for A3 in every sweep drawn: {coincide}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
