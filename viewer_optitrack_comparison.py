"""
viewer_optitrack_comparison.py
==============================
Three-way comparison: OptiTrack (ground truth) vs Pendulastic Viewer vs
best HPE model (rtmpose_m, from hpe_mas_comparison.csv).

Matching strategy:
  viewer session  participant_id (strip "P" prefix, e.g. "P004" → "4")
                + leg ("right" / "left")
                + trial number
  → OptiTrack     pid = "{num}_{leg}"  trial = {trial}

Outputs (Model_Analysis_Outputs/Viewer_Comparison/):
  viewer_vs_optitrack.csv        — matched trial table
  viewer_vs_optitrack_scatter.png — PT score scatter (viewer vs optitrack)
  viewer_vs_optitrack_mas.png    — MAS agreement heatmap (3-way)
  viewer_vs_optitrack_params.png — per-parameter comparison box plots

Run:
    .venv\\Scripts\\python.exe viewer_optitrack_comparison.py
"""
from __future__ import annotations

import json
import math
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import matplotlib.colors as mcolors
import numpy as np
import pandas as pd

# ── paths ─────────────────────────────────────────────────────────────────────
BASE_DIR   = os.path.dirname(os.path.abspath(__file__))
SESSIONS   = os.path.join(BASE_DIR, "sessions.json")
OPTI_CSV   = os.path.join(BASE_DIR, "Model_Analysis_Outputs", "PT_Scores",
                          "pendulum_test_scores.csv")
HPE_CSV    = os.path.join(BASE_DIR, "Model_Analysis_Outputs", "HPE_Comparison",
                          "hpe_mas_comparison.csv")
OUT_DIR    = os.path.join(BASE_DIR, "Model_Analysis_Outputs", "Viewer_Comparison")
os.makedirs(OUT_DIR, exist_ok=True)

_BG     = "#12172a"
_PANEL  = "#1c2340"
_MAS_COLOR = {
    "0": "#00E87A", "1": "#A0E030", "1+": "#FFE000",
    "2": "#FF9000", "3": "#FF4500", "4":  "#FF1A1A", "?": "#444466",
}
_MAS_NUMS = {"0": 0, "1": 1, "1+": 1.5, "2": 2, "3": 3, "4": 4, "?": -1}

BEST_HPE = "rtmpose_m"   # confirmed most accurate from hpe_mas_evaluation.py


# ══════════════════════════════════════════════════════════════════════════════
# Load sessions.json viewer data
# ══════════════════════════════════════════════════════════════════════════════

def _norm_pid(raw: str) -> str:
    """'P004' → '4',  '4' → '4',  'P001' → '1'."""
    s = str(raw).strip()
    if s.upper().startswith("P"):
        s = s[1:]
    try:
        return str(int(s))
    except ValueError:
        return s


def load_viewer_sessions() -> pd.DataFrame:
    """Load sessions from sessions.json and return a normalised DataFrame."""
    if not os.path.exists(SESSIONS):
        print(f"sessions.json not found: {SESSIONS}")
        return pd.DataFrame()
    with open(SESSIONS, encoding="utf-8") as f:
        db = json.load(f)

    # Build participant-id → diagnosis from the participants list
    part_diag = {}
    for p in db.get("participants", []):
        pid  = _norm_pid(p.get("id", ""))
        diag = str(p.get("diagnosis", "")).lower()
        if "ms" in diag or "stroke" in diag:
            part_diag[pid] = "MS"
        elif "control" in diag or "unaffected" in diag or "healthy" in diag:
            part_diag[pid] = "Healthy"
        else:
            part_diag[pid] = "Unknown"

    rows = []
    for s in db.get("sessions", []):
        raw_pid = str(s.get("participant", ""))
        num_pid = _norm_pid(raw_pid)
        leg     = str(s.get("leg", "right")).lower()
        trial   = str(s.get("trial", "")).strip()
        pt4     = s.get("pt_4")
        mas     = str(s.get("mas", "?"))
        rows.append({
            "viewer_pid":   num_pid,
            "viewer_leg":   leg,
            "viewer_trial": trial,
            "viewer_pt4":   float(pt4) if pt4 is not None else float("nan"),
            "viewer_mas":   mas,
            "viewer_R2n":   s.get("R2n"),
            "viewer_N":     s.get("N"),
            "viewer_phi":   s.get("phi_max_ratio"),
            "viewer_omega": s.get("omega_max_n"),
            "viewer_A0":    s.get("A0_deg"),
            "diagnosis":    part_diag.get(num_pid, "Unknown"),
            "session_id":   s.get("id", ""),
        })
    df = pd.DataFrame(rows)
    df["viewer_trial"] = df["viewer_trial"].astype(str)
    return df


# ══════════════════════════════════════════════════════════════════════════════
# Load OptiTrack reference
# ══════════════════════════════════════════════════════════════════════════════

def load_optitrack() -> pd.DataFrame:
    if not os.path.exists(OPTI_CSV):
        print(f"OptiTrack CSV not found: {OPTI_CSV}")
        return pd.DataFrame()
    df = pd.read_csv(OPTI_CSV)
    df["quality_warn"] = df["quality_warn"].fillna("")
    df["pid"]   = df["pid"].astype(str)
    df["trial"] = df["trial"].astype(str)
    df = df.dropna(subset=["pt_score_4param"])
    # Extract numeric prefix and leg from pid (e.g. "4_right" → num=4, leg=right)
    def _split(pid):
        parts = str(pid).split("_", 1)
        num   = parts[0]
        leg   = "right" if "right" in pid else ("left" if "left" in pid else "")
        return num, leg
    df[["opti_num", "opti_leg"]] = pd.DataFrame(
        df["pid"].map(_split).tolist(), index=df.index)
    return df


# ══════════════════════════════════════════════════════════════════════════════
# Load best HPE model scores
# ══════════════════════════════════════════════════════════════════════════════

def load_hpe() -> pd.DataFrame:
    if not os.path.exists(HPE_CSV):
        return pd.DataFrame()
    df = pd.read_csv(HPE_CSV)
    df["pid"]   = df["pid"].astype(str)
    df["trial"] = df["trial"].astype(str)
    # Keep only the best model family
    best = df[df["family"] == BEST_HPE].copy()
    # Extract numeric prefix
    best["hpe_num"] = best["pid"].apply(lambda p: p.split("_")[0])
    best["hpe_leg"] = best["pid"].apply(
        lambda p: "right" if "right" in p else ("left" if "left" in p else ""))
    return best


# ══════════════════════════════════════════════════════════════════════════════
# Match viewer → optitrack → hpe
# ══════════════════════════════════════════════════════════════════════════════

def build_matched(viewer: pd.DataFrame, opti: pd.DataFrame,
                  hpe: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for _, v in viewer.iterrows():
        # Match optitrack row: same participant number + leg + trial
        mask = (
            (opti["opti_num"] == v["viewer_pid"]) &
            (opti["opti_leg"] == v["viewer_leg"]) &
            (opti["trial"]    == v["viewer_trial"])
        )
        o_rows = opti[mask]
        if o_rows.empty:
            # Try without leg constraint (participant with no side suffix)
            mask2 = (
                (opti["opti_num"] == v["viewer_pid"]) &
                (opti["opti_leg"] == "") &
                (opti["trial"]    == v["viewer_trial"])
            )
            o_rows = opti[mask2]
        if o_rows.empty:
            continue
        o = o_rows.iloc[0]

        # Match HPE row
        h_mask = (
            (hpe["hpe_num"] == v["viewer_pid"]) &
            (hpe["hpe_leg"] == v["viewer_leg"]) &
            (hpe["trial"]   == v["viewer_trial"])
        )
        h_rows = hpe[h_mask]
        hpe_pt4  = float(h_rows.iloc[0]["hpe_pt"])  if not h_rows.empty else float("nan")
        hpe_mas  = str(h_rows.iloc[0]["hpe_mas"])   if not h_rows.empty else "?"

        opti_pt4 = float(o["pt_score_4param"])
        opti_mas = str(o["est_MAS_4param"])

        rows.append({
            "participant":  v["viewer_pid"],
            "leg":          v["viewer_leg"],
            "trial":        v["viewer_trial"],
            "diagnosis":    v["diagnosis"],
            "quality_warn": str(o["quality_warn"]),
            # OptiTrack
            "opti_pt4":    opti_pt4,
            "opti_mas":    opti_mas,
            "opti_R2n":    o.get("R2n"),
            "opti_N":      o.get("N"),
            "opti_phi":    o.get("phi_max_ratio"),
            "opti_omega":  o.get("omega_max_n"),
            "opti_A0":     o.get("A0_deg"),
            # Viewer
            "viewer_pt4":  v["viewer_pt4"],
            "viewer_mas":  v["viewer_mas"],
            "viewer_R2n":  v["viewer_R2n"],
            "viewer_N":    v["viewer_N"],
            "viewer_phi":  v["viewer_phi"],
            "viewer_omega":v["viewer_omega"],
            "viewer_A0":   v["viewer_A0"],
            # Best HPE
            "hpe_pt4":     hpe_pt4,
            "hpe_mas":     hpe_mas,
            # Agreement flags
            "viewer_mas_match": v["viewer_mas"] == opti_mas,
            "hpe_mas_match":    hpe_mas == opti_mas,
            "viewer_pt_diff":   abs(v["viewer_pt4"] - opti_pt4)
                                if math.isfinite(v["viewer_pt4"]) else float("nan"),
            "hpe_pt_diff":      abs(hpe_pt4 - opti_pt4)
                                if math.isfinite(hpe_pt4) else float("nan"),
        })

    return pd.DataFrame(rows)


# ══════════════════════════════════════════════════════════════════════════════
# Main
# ══════════════════════════════════════════════════════════════════════════════

def main() -> None:
    sep = "=" * 72
    print(f"\n{sep}\n  Three-Way Comparison: OptiTrack vs Viewer vs HPE ({BEST_HPE})\n{sep}\n")

    viewer = load_viewer_sessions()
    opti   = load_optitrack()
    hpe    = load_hpe()

    if viewer.empty:
        print("No viewer sessions found in sessions.json"); return
    if opti.empty:
        print("No OptiTrack data found"); return

    print(f"Viewer sessions : {len(viewer)}")
    print(f"OptiTrack trials: {len(opti)}")
    print(f"HPE ({BEST_HPE}) trials: {len(hpe)}")

    matched = build_matched(viewer, opti, hpe)
    if matched.empty:
        print("\nNo matching trials found between viewer and OptiTrack.")
        print("Check that trial numbers and participant IDs match.")
        print("\nViewer participants:", sorted(viewer["viewer_pid"].unique()))
        print("OptiTrack nums:     ", sorted(opti["opti_num"].unique()))
        return

    # Exclude artifact trials for accuracy metrics
    clean = matched[matched["quality_warn"] == ""].copy()

    print(f"\nMatched trials: {len(matched)}  ({len(clean)} without artifact warning)")

    # ── Accuracy summary ──────────────────────────────────────────────────────
    print(f"\n{sep}")
    print(f"{'Metric':<36} {'Viewer':>10} {'HPE ('+BEST_HPE+')':>16}")
    print("-" * 65)

    def _pct(series, denom):
        return f"{series.mean()*100:.1f}%" if denom > 0 else "n/a"

    n_c = len(clean)
    metrics = [
        ("Exact MAS agreement",
         clean["viewer_mas_match"].sum() / n_c * 100 if n_c else float("nan"),
         clean["hpe_mas_match"].sum()    / n_c * 100 if n_c else float("nan")),
        ("Adjacent MAS agreement",
         clean.apply(lambda r: abs(_MAS_NUMS.get(r["viewer_mas"], -1) -
                                   _MAS_NUMS.get(r["opti_mas"], -1)) <= 1, axis=1
                     ).mean() * 100 if n_c else float("nan"),
         clean.apply(lambda r: abs(_MAS_NUMS.get(r["hpe_mas"], -1) -
                                   _MAS_NUMS.get(r["opti_mas"], -1)) <= 1, axis=1
                     ).mean() * 100 if n_c else float("nan")),
        ("Mean absolute PT error",
         clean["viewer_pt_diff"].mean(),
         clean["hpe_pt_diff"].mean()),
    ]
    for label, v_val, h_val in metrics:
        vs = f"{v_val:.1f}" if not math.isnan(v_val) else "n/a"
        hs = f"{h_val:.1f}" if not math.isnan(h_val) else "n/a"
        suf = "%" if "agree" in label else ""
        print(f"{label:<36} {vs+suf:>10} {hs+suf:>16}")
    print(sep)

    # ── Per-trial table ───────────────────────────────────────────────────────
    print("\nPer-trial breakdown (clean trials):")
    print(f"{'P':>4} {'Leg':>6} {'T':>3}  {'OptiPT':>8} {'Opti':>5}  "
          f"{'ViewPT':>8} {'View':>5}  {'HPEPT':>7} {'HPE':>5}")
    print("-" * 60)
    for _, r in clean.sort_values(["participant","leg","trial"]).iterrows():
        vp = f"{r['viewer_pt4']:.3f}" if math.isfinite(r["viewer_pt4"]) else " n/a "
        hp = f"{r['hpe_pt4']:.3f}"    if math.isfinite(r["hpe_pt4"])    else " n/a "
        print(f"{r['participant']:>4} {r['leg']:>6} {r['trial']:>3}  "
              f"{r['opti_pt4']:>8.3f} {r['opti_mas']:>5}  "
              f"{vp:>8} {r['viewer_mas']:>5}  "
              f"{hp:>7} {r['hpe_mas']:>5}")
    print(sep)

    # ── Save CSV ──────────────────────────────────────────────────────────────
    out_csv = os.path.join(OUT_DIR, "viewer_vs_optitrack.csv")
    matched.to_csv(out_csv, index=False)
    print(f"\nDetail CSV -> {out_csv}")

    # ── Plots ─────────────────────────────────────────────────────────────────
    if len(clean) >= 2:
        _plot_scatter(clean)
        _plot_mas_heatmap(clean)
    if len(clean) >= 3:
        _plot_param_comparison(clean)
    print(f"\nOutputs -> {OUT_DIR}")


# ══════════════════════════════════════════════════════════════════════════════
# Plots
# ══════════════════════════════════════════════════════════════════════════════

def _plot_scatter(df: pd.DataFrame) -> None:
    """PT score scatter: viewer vs optitrack vs HPE, colour by diagnosis."""
    fig, axes = plt.subplots(1, 2, figsize=(12, 5), facecolor=_BG)
    fig.text(0.5, 0.99,
             "4-param PT Score: Viewer and HPE vs OptiTrack Ground Truth",
             ha="center", va="top", color="#D0DEFF", fontsize=13, fontweight="bold")

    _diag_c = {"MS": "#FF5533", "Healthy": "#00E87A", "Unknown": "#88AACC"}

    for ax, (col_pt, col_mas, src_lbl) in zip(
        axes,
        [("viewer_pt4", "viewer_mas", "Pendulastic Viewer"),
         ("hpe_pt4",    "hpe_mas",    f"HPE ({BEST_HPE})")],
    ):
        ax.set_facecolor(_PANEL)
        for sp in ax.spines.values():
            sp.set_edgecolor("#3A4A6A")

        for _, r in df.iterrows():
            x  = r["opti_pt4"]
            y  = r[col_pt]
            if not math.isfinite(y): continue
            c  = _diag_c.get(r["diagnosis"], "#AABBCC")
            ax.scatter(x, y, color=c, s=60, zorder=4,
                       edgecolors="black", linewidths=0.5)

        lo = min(df["opti_pt4"].min(), df[col_pt].dropna().min()) - 0.02
        hi = max(df["opti_pt4"].max(), df[col_pt].dropna().max()) + 0.02
        lo = max(0, lo)
        ax.plot([lo, hi], [lo, hi], color="#FFFFFF", lw=1, ls="--", alpha=0.5, label="y = x")
        ax.set_xlim(lo, hi); ax.set_ylim(lo, hi)
        ax.set_xlabel("OptiTrack PT Score", color="#90A8CC", fontsize=11)
        ax.set_ylabel(f"{src_lbl} PT Score", color="#90A8CC", fontsize=11)
        ax.set_title(src_lbl, color="#D0DEFF", fontsize=12, fontweight="bold")
        ax.tick_params(colors="#A0B0C8", labelsize=9)

        # Correlation
        valid = df[[col_pt, "opti_pt4"]].dropna()
        if len(valid) >= 2:
            r_val = valid.corr().iloc[0, 1]
            mae   = (valid[col_pt] - valid["opti_pt4"]).abs().mean()
            ax.text(0.05, 0.93, f"r = {r_val:.3f}  MAE = {mae:.3f}",
                    transform=ax.transAxes, color="#A0C8E0", fontsize=9)

    leg_handles = [mpatches.Patch(facecolor=c, label=g)
                   for g, c in _diag_c.items() if g != "Unknown"]
    fig.legend(handles=leg_handles, loc="lower center", ncol=3,
               facecolor=_PANEL, edgecolor="#3A4A6A", labelcolor="#C0D0F0", fontsize=10)
    plt.tight_layout(rect=[0, 0.05, 1, 0.97])
    out = os.path.join(OUT_DIR, "viewer_vs_optitrack_scatter.png")
    plt.savefig(out, dpi=130, facecolor=fig.get_facecolor(), bbox_inches="tight")
    plt.close()
    print(f"  -> {os.path.basename(out)}")


def _plot_mas_heatmap(df: pd.DataFrame) -> None:
    """MAS grade heatmap: trial × source (OptiTrack | Viewer | HPE)."""
    df = df.copy().sort_values(["participant", "leg", "trial"])
    labels = [f"P{r['participant']} {r['leg']} T{r['trial']}" for _, r in df.iterrows()]
    n_rows = len(labels)
    sources = ["OptiTrack", "Viewer", f"HPE\n({BEST_HPE})"]
    mas_cols = ["opti_mas", "viewer_mas", "hpe_mas"]

    cmap_vals = ["#00E87A", "#A0E030", "#FFE000", "#FF9000", "#FF4500", "#FF1A1A"]
    cmap  = mcolors.ListedColormap(["#2A2A3A"] + cmap_vals)
    bounds = [-1.5, -0.5, 0.5, 1.25, 1.75, 2.5, 3.5, 4.5]
    norm  = mcolors.BoundaryNorm(bounds, cmap.N)
    _nums = {"0": 0, "1": 1, "1+": 1.5, "2": 2, "3": 3, "4": 4, "?": -1}

    mat = np.full((n_rows, 3), -1.0)
    for ri, (_, r) in enumerate(df.iterrows()):
        for ci, col in enumerate(mas_cols):
            mat[ri, ci] = _nums.get(str(r[col]), -1)

    fig_h = max(4, n_rows * 0.55 + 1.5)
    fig, ax = plt.subplots(figsize=(7, fig_h), facecolor=_BG)
    ax.set_facecolor(_BG)

    im = ax.imshow(mat, aspect="auto", cmap=cmap, norm=norm, interpolation="none")
    grade_str = {0: "0", 1: "1", 1.5: "1+", 2: "2", 3: "3", 4: "4"}
    for ri in range(n_rows):
        for ci in range(3):
            v = mat[ri, ci]
            txt = grade_str.get(v, "")
            if txt:
                ax.text(ci, ri, txt, ha="center", va="center",
                        fontsize=11, color="black", fontweight="bold")

    ax.set_xticks([0, 1, 2])
    ax.set_xticklabels(sources, color="#C0D0F0", fontsize=11)
    ax.set_yticks(range(n_rows))
    ax.set_yticklabels(labels, color="#C0D0F0", fontsize=9)
    for sp in ax.spines.values(): sp.set_edgecolor("#3A4A6A")

    cbar = plt.colorbar(im, ax=ax, pad=0.02, shrink=0.6)
    cbar.set_ticks([-1, 0, 1, 1.5, 2, 3, 4])
    cbar.set_ticklabels(["n/a", "0", "1", "1+", "2", "3", "4"])
    cbar.ax.tick_params(colors="#C0D0F0", labelsize=9)

    fig.text(0.5, 0.99, "MAS Grade Comparison  (OptiTrack vs Viewer vs HPE)",
             ha="center", va="top", color="#D0DEFF", fontsize=12, fontweight="bold")
    plt.tight_layout(rect=[0, 0, 1, 0.97])
    out = os.path.join(OUT_DIR, "viewer_vs_optitrack_mas.png")
    plt.savefig(out, dpi=130, facecolor=fig.get_facecolor(), bbox_inches="tight")
    plt.close()
    print(f"  -> {os.path.basename(out)}")


def _plot_param_comparison(df: pd.DataFrame) -> None:
    """Side-by-side box plots of each PT parameter: optitrack vs viewer."""
    PCOLS = [
        ("R2n",           "opti_R2n", "viewer_R2n"),
        ("N",             "opti_N",   "viewer_N"),
        ("phi_max_ratio", "opti_phi", "viewer_phi"),
        ("omega_max_n",   "opti_omega","viewer_omega"),
        ("A0 (deg)",      "opti_A0",  "viewer_A0"),
    ]
    valid_cols = [(lbl, oc, vc) for lbl, oc, vc in PCOLS
                  if oc in df.columns and vc in df.columns]
    n = len(valid_cols)
    fig, axes = plt.subplots(1, n, figsize=(3.2 * n, 5), facecolor=_BG)
    if n == 1:
        axes = [axes]

    _C_O = "#00C8FF"   # optitrack = cyan
    _C_V = "#FF9000"   # viewer = amber

    for ax, (lbl, oc, vc) in zip(axes, valid_cols):
        ax.set_facecolor(_PANEL)
        for sp in ax.spines.values(): sp.set_edgecolor("#3A4A6A")

        o_vals = df[oc].dropna().values.astype(float)
        v_vals = df[vc].dropna().values.astype(float)

        bp = ax.boxplot([o_vals, v_vals], positions=[0, 1], patch_artist=True,
                        widths=0.4,
                        medianprops=dict(color="#FFFFFF", lw=2),
                        whiskerprops=dict(color="#6080A0", lw=1.2),
                        capprops=dict(color="#6080A0", lw=1.2),
                        flierprops=dict(marker="o", markerfacecolor="#FF6060", markersize=4))
        bp["boxes"][0].set_facecolor(_C_O + "44"); bp["boxes"][0].set_edgecolor(_C_O)
        bp["boxes"][1].set_facecolor(_C_V + "44"); bp["boxes"][1].set_edgecolor(_C_V)

        np.random.seed(42)
        jit = lambda n: (np.random.rand(n) - 0.5) * 0.2
        ax.scatter(jit(len(o_vals)), o_vals, color=_C_O, s=35, zorder=4, alpha=0.85)
        ax.scatter(1 + jit(len(v_vals)), v_vals, color=_C_V, s=35, zorder=4, alpha=0.85)

        ax.set_title(lbl, color="#D0DEFF", fontsize=10, fontweight="bold")
        ax.set_xticks([0, 1])
        ax.set_xticklabels(["OptiTrack", "Viewer"], color="#C0D0F0", fontsize=9)
        ax.tick_params(axis="y", colors="#A0B0C8", labelsize=8)

    fig.text(0.5, 0.99, "PT Parameters: OptiTrack vs Pendulastic Viewer",
             ha="center", va="top", color="#D0DEFF", fontsize=12, fontweight="bold")
    plt.tight_layout(rect=[0, 0, 1, 0.95])
    out = os.path.join(OUT_DIR, "viewer_vs_optitrack_params.png")
    plt.savefig(out, dpi=130, facecolor=fig.get_facecolor(), bbox_inches="tight")
    plt.close()
    print(f"  -> {os.path.basename(out)}")


if __name__ == "__main__":
    main()
