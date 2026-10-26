"""Figures for the neuromechanical emulator report (deck PNGs; PUB=1 also writes publication PNG+PDF).

    python neuromech/nm_figs.py          (after python -m neuromech.build)
"""
from __future__ import annotations

import os
import sys

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.colors import TwoSlopeNorm
from scipy.signal import lsim, filtfilt, butter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
from neuromech.pubstyle import setup, style, note, save as _save, TEAL, SEA, NAVY, CORAL, GREY, INK, PANEL, GOLD  # noqa: E402
import pendulastic_pt_score as P  # noqa: E402
from neuromech import scenarios as S  # noqa: E402
from neuromech.build import render, score, OUT  # noqa: E402

setup()
FIG = os.path.join(OUT, "figures"); os.makedirs(FIG, exist_ok=True)
def save(fig, name): _save(fig, name, FIG)
M = pd.read_csv(os.path.join(OUT, "scenario_manifest.csv"))
def trace(sid):
    z = np.load(os.path.join(OUT, "traces", f"{sid}.npz")); return z["t"], 180.0 + z["theta"]
MAS_COL = {"0": TEAL, "1": SEA, "1+": GOLD, "2": "#F08A3C", "3": CORAL, "4": "#9B2226"}
BANDS = [(0, .12, "0"), (.12, .28, "1"), (.28, .44, "1+"), (.44, .60, "2"), (.60, .78, "3"), (.78, 1.8, "4")]


def fig_ladder():
    fig, (a, b) = plt.subplots(1, 2, figsize=(13, 4.6), gridspec_kw=dict(width_ratios=[1.15, 1]))
    for g, sid in zip(("0", "1", "1+", "2", "3"), ("A-P0", "A-P1", "A-P1p", "A-P2", "A-P3")):
        t, th = trace(sid); r = M[M.sid == sid].iloc[0]
        a.plot(t, th, color=MAS_COL[g], lw=1.7, label=f"band {g}  (PT7 {r.pt7:.2f})")
    a.set_xlim(0, 7); a.set_xlabel("time from release (s)"); a.set_ylabel("knee angle (deg, 180 = full extension)")
    a.set_title("PT7-band presets: one model, five severities"); a.legend(frameon=False, loc="lower right"); style(a)
    lams = np.r_[np.linspace(0, 1.0, 21), [1.25, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0, 6.0]]
    pts = []
    for lam in lams:
        sc = S.Scenario("sweep", "S", "", S.severity(lam)); t, th, _, _ = render(sc); prm, est = score(t, th, sc)
        pts.append((lam, est["pt7"] if est["pt7"] is not None else np.nan, est["interpretable"], prm["A0_deg"] if prm else np.nan))
    pts = np.array(pts, dtype=float)
    for lo, hi, g in BANDS:
        b.axhspan(lo, hi, color=MAS_COL[g], alpha=0.10); b.text(6.15, min((lo + hi) / 2, 1.55), g, va="center", fontsize=9, color=MAS_COL[g])
    ok = pts[:, 2] == 1
    b.plot(pts[:, 0], pts[:, 1], color=GREY, lw=1, zorder=1)
    b.scatter(pts[ok, 0], pts[ok, 1], s=30, color=NAVY, zorder=3, label="graded")
    b.scatter(pts[~ok, 0], pts[~ok, 1], s=46, facecolor="white", edgecolor=CORAL, lw=1.5, zorder=3, label="grade refused (A0 < 25 deg)")
    b.axvline(1.0, color=INK, ls=":", lw=1); b.text(1.03, 1.62, "De Groote 2018\nnominal CP", fontsize=8.5, color=INK, va="top")
    b.set_xlabel("severity  λ  (tone 2λ N·m, k_f 0.75λ, k_df 0.025λ)"); b.set_ylabel("PT7 (production scorer)")
    b.set_ylim(0, 1.7); b.set_xlim(-0.1, 6.4); b.set_title("PT7 is non-monotonic in severity"); b.legend(frameon=False, loc="upper right"); style(b)
    fig.suptitle("Severity ladder and the composite's inversion on the published model", fontweight="bold", color=NAVY)
    save(fig, "nm_ladder")
    pd.DataFrame(pts, columns=["lambda", "pt7", "interpretable", "A0_deg"]).to_csv(os.path.join(OUT, "severity_sweep.csv"), index=False)


SHORT = {"B-extensor": "Extensor-dominant", "B-flexor": "Flexor-dominant", "B-mixed": "Co-spastic (ext + flex)", "B-claspknife": "Clasp-knife catch", "B-clonus": "Clonus, 6 Hz", "B-leadpipe": "Lead-pipe rigidity", "B-cogwheel": "Cogwheel rigidity", "B-contracture": "Contracture", "B-hypotonic": "Hypotonic / flaccid", "B-guarding": "Non-relaxation (guarding)", "B-spasm": "Mid-swing spasm", "C-area-high": "Healthy leg, rest creeps 3 deg/s"}


def fig_behaviours():
    sids = ["B-extensor", "B-flexor", "B-mixed", "B-claspknife", "B-clonus", "B-leadpipe", "B-cogwheel", "B-contracture",
            "B-hypotonic", "B-guarding", "B-spasm", "C-area-high"]
    t0, h0 = trace("A-P0")
    fig, axs = plt.subplots(3, 4, figsize=(14, 8.2), sharex=True, sharey=True); axs = axs.ravel()
    for ax, sid in zip(axs, sids):
        t, th = trace(sid); r = M[M.sid == sid].iloc[0]; g = r.mas if isinstance(r.mas, str) else "refused"
        ax.plot(t0, h0, color=GREY, lw=1, alpha=0.7); ax.plot(t, th, color=MAS_COL.get(g, CORAL), lw=1.6)
        ax.set_title(f"{r.title.split(':')[0].split('(')[0].strip()[:30]}\nscored MAS {g}  ·  PT7 {r.pt7:.2f}", fontsize=9.5); style(ax)
        ax.set_xlim(0, 9)
    for ax in axs[8:]: ax.set_xlabel("time (s)")
    for ax in axs[::4]: ax.set_ylabel("knee angle (deg)")
    fig.suptitle("Programmable behaviours vs a healthy swing (grey) — and what the production scorer calls them", fontweight="bold", color=NAVY)
    save(fig, "nm_behaviours")


def fig_matrix():
    keys = ["R2n", "N", "phi_max_ratio", "omega_max_n", "omega_min_n", "f", "area_ratio"]
    ref = dict(P.HEALTHY_REF)
    lab = {"R2n": "R2n", "N": "N", "phi_max_ratio": "φmax", "omega_max_n": "ωmax", "omega_min_n": "ωmin", "f": "f", "area_ratio": "area"}
    d = M[M.group.isin(["A", "S", "B", "C", "D", "M"])].copy()
    Z = np.zeros((len(d), len(keys)))
    for j, k in enumerate(keys):
        floor = 0.002 if k == "omega_min_n" else 0.02
        Z[:, j] = np.log2(np.maximum(d[k].fillna(0).values, floor) / max(ref[k], floor))
    fig, ax = plt.subplots(figsize=(10.5, 15.5))
    im = ax.imshow(np.clip(Z, -4, 4), cmap="RdBu_r", norm=TwoSlopeNorm(0, -4, 4), aspect="auto")
    for i in range(len(d)):
        for j, k in enumerate(keys):
            v = d[k].iloc[i]; ax.text(j, i, "—" if pd.isna(v) else f"{v:.2g}", ha="center", va="center", fontsize=7.5, color="white" if abs(Z[i, j]) > 2.6 else INK)
        g = d.mas.iloc[i]; tag = (g if isinstance(g, str) else "refused")
        fl = ("  f>2Hz" if d.flag_f_out_of_range.iloc[i] else "") + ("  area>.55" if d.flag_area_warn.iloc[i] else "")
        ax.text(len(keys) - 0.4, i, f"MAS {tag}{fl}", va="center", fontsize=8, color=MAS_COL.get(tag, CORAL), fontweight="bold")
    ax.set_yticks(range(len(d))); ax.set_yticklabels([f"{s}  {t[:42]}" for s, t in zip(d.sid, d.title)], fontsize=7.8)
    ax.set_xticks(range(len(keys))); ax.set_xticklabels([lab[k] for k in keys]); ax.xaxis.tick_top()
    ax.set_xlim(-0.5, len(keys) + 1.6)
    for y, gname in zip(np.flatnonzero(d.group.ne(d.group.shift()).values), d.group.unique()):
        ax.axhline(y - 0.5, color=INK, lw=0.8)
    cb = fig.colorbar(im, ax=ax, fraction=0.025, pad=0.01, location="bottom"); cb.set_label("log₂(value / healthy reference)")
    ax.set_title("Expected scorer output for every scenario, relative to HEALTHY_REF", pad=34)
    save(fig, "nm_matrix")


def fig_rig_ilc():
    z = np.load(os.path.join(ROOT, "neuromech", "data", "rig_frf_2026-10-26.npz"))
    f, g, ph, coh = z["f_200"], z["g_200"], z["ph_200"], z["coh_200"]
    wn, zeta = 2 * np.pi * 6.35, 1 / (2 * 2.17)                      # identified from the resonance
    num, den = [wn ** 2], [1, 2 * zeta * wn, wn ** 2]
    t, th = trace("A-P2"); dt = 1e-3
    tg = np.arange(0, 8, dt); y_des = np.interp(tg, t, th - th[-1])
    from scipy.signal import tf2ss, lsim as _lsim
    A, Bm, C, D = tf2ss(num, den)
    x0 = -np.linalg.solve(A, Bm[:, 0] * y_des[0])          # plant at rest, held at the release angle
    bq, aq = butter(2, 15 / (0.5 / dt))                      # ILC robustness filter Q, 15 Hz, zero-phase
    def inv_model(e):                                        # G^-1 e = e + (2 zeta/wn) e' + e''/wn^2
        ef = filtfilt(bq, aq, e); d1 = np.gradient(ef, dt); d2 = np.gradient(d1, dt)
        return ef + (2 * zeta / wn) * d1 + d2 / wn ** 2
    u = y_des.copy(); rms = []; outs = []
    for it in range(6):
        _, y, _ = _lsim((A, Bm, C, D), u, tg, X0=x0); e = y_des - y; rms.append(np.sqrt(np.mean(e ** 2))); outs.append(y)
        u = u + 0.8 * inv_model(e)                           # plant-inversion ILC, learning gain 0.8
    fig, (a, b, c) = plt.subplots(1, 3, figsize=(14, 4.3), gridspec_kw=dict(width_ratios=[1, 1.4, 0.8]))
    m = (f > 0.2) & (f < 12)
    a.semilogx(f[m], g[m], color=TEAL, lw=2, label="measured (23 SSR recordings)")
    w = 2 * np.pi * f[m]; Hm = wn ** 2 / np.sqrt((wn ** 2 - w ** 2) ** 2 + (2 * zeta * wn * w) ** 2)
    a.semilogx(f[m], Hm, color=NAVY, ls="--", lw=1.4, label="2nd-order fit, 6.35 Hz, ζ 0.23")
    a.axhline(1, color=GREY, lw=0.8); a.axvspan(0.5, 1.5, color=SEA, alpha=0.1); a.axvspan(5, 8, color=CORAL, alpha=0.08)
    a.text(0.55, 2.1, "pendulum", color=SEA, fontsize=8.5); a.text(5.1, 2.1, "clonus", color=CORAL, fontsize=8.5)
    a.set_xlabel("frequency (Hz)"); a.set_ylabel("|encoder / setpoint|"); a.set_title("Rig tracking, current tune (P4 I0.3 D0.03)"); a.legend(frameon=False, fontsize=8.5, loc="lower left"); style(a)
    b.plot(tg, y_des, color=INK, lw=2.4, label="intended (band-2 preset)")
    b.plot(tg, outs[0], color=CORAL, lw=1.3, label=f"commanded as-is: RMSE {rms[0]:.2f} deg")
    b.plot(tg, outs[-1], color=TEAL, lw=1.3, label=f"after 5 ILC passes: RMSE {rms[-1]:.3f} deg")
    b.set_xlim(0, 4); b.set_xlabel("time from release (s)"); b.set_ylabel("rig angle (deg)"); b.set_title("Rendering a spastic swing on that plant"); b.legend(frameon=False, fontsize=8.5); style(b)
    c.semilogy(range(len(rms)), rms, color=NAVY, marker="o"); c.set_xlabel("ILC iteration (one rig run each)"); c.set_ylabel("tracking RMSE (deg)")
    c.set_title(f"{rms[0] / rms[-1]:.0f}× smaller in {len(rms) - 1} runs"); style(c)
    fig.suptitle("Why the rig needs trajectory correction — and what iterative learning control buys (simulated on the identified plant)", fontweight="bold", color=NAVY)
    save(fig, "nm_rig_ilc"); print("   ILC RMSE per iteration:", [round(x, 3) for x in rms])


def fig_range():
    d = M.copy()
    fig, ax = plt.subplots(figsize=(12, 4.6))
    x = np.arange(len(d)); col = [CORAL if e else TEAL for e in d.exceeds_rig_limit]
    ax.vlines(x, d.rig_min_deg, d.rig_max_deg, color=col, lw=4)
    ax.axhspan(-45, 45, color=SEA, alpha=0.08); ax.axhline(45, color=SEA, ls="--", lw=1); ax.axhline(-45, color=SEA, ls="--", lw=1)
    ax.text(len(d) - 0.5, 47, "current ±45 deg software limits", ha="right", color=SEA, fontsize=9)
    ax.set_xticks(x); ax.set_xticklabels(d.sid, rotation=90, fontsize=7.5); ax.set_ylabel("rig angle needed (deg, 0 = passive rest)")
    ax2 = ax.twinx(); ax2.plot(x, d.rig_peak_dps, "o", color=NAVY, ms=4); ax2.axhline(440, color=NAVY, ls=":", lw=1); ax2.set_ylabel("peak speed (deg/s)", color=NAVY)
    ax2.text(1, 455, "highest speed the rig has shown: 440 deg/s", color=NAVY, fontsize=8.5)
    for s in ("top",): ax2.spines[s].set_visible(False)
    ax.set_title(f"Range and speed each scenario needs — {int(d.exceeds_rig_limit.sum())} of {len(d)} exceed the current limits"); style(ax)
    save(fig, "nm_range")


def fig_dryrun():
    """The session program end to end on synthetic hardware: intended vs encoder-delivered vs phone PT7,
    and ILC convergence through the real tool code."""
    import glob
    sess = sorted(glob.glob(os.path.join(os.path.dirname(OUT), "sessions", "DRYRUN-*")))
    sess = [d for d in sess if os.path.exists(os.path.join(d, "ilc_demo.csv"))]
    if not sess:
        print("   no dry run with ilc_demo.csv; run python -m neuromech.dryrun first"); return
    d = sess[-1]; rep = pd.read_csv(os.path.join(d, "report.csv")); il = pd.read_csv(os.path.join(d, "ilc_demo.csv"))
    rep = rep[rep.status == "scored"].reset_index(drop=True)
    fig, (a, b) = plt.subplots(1, 2, figsize=(13.5, 4.6), gridspec_kw=dict(width_ratios=[1.4, 1]))
    x = np.arange(len(rep)); w = 0.27
    a.bar(x - w, rep.int_pt7, w, color=GREY, label="intended (model)")
    a.bar(x, rep.enc_pt7, w, color=NAVY, label="delivered (encoder)")
    a.bar(x + w, rep.ph_pt7, w, color=TEAL, label="phone (gyro, bias from stillness)")
    for lo, hi, g in BANDS[:-1]:
        a.axhline(hi, color=MAS_COL[g], lw=0.8, ls=":")
    a.set_xticks(x); a.set_xticklabels(rep.sid.str.replace("M-", "").str[:16], rotation=35, ha="right", fontsize=8.5)
    a.set_ylabel("PT7"); a.set_title(f"Delivered band = intended on {rep.delivered_band_ok.mean() * 100:.0f}% of runs; "
                                     f"phone |ΔPT7| median {(rep.ph_pt7 - rep.enc_pt7).abs().median():.3f}")
    a.legend(frameon=False, fontsize=9); style(a)
    for (sid, g), c in zip(il.groupby("sid"), (NAVY, TEAL, CORAL, GOLD, SEA, GREY)):
        b.semilogy(g.iteration, g.tracking_rmse_deg, marker="o", lw=1.6, color=c, label=sid.replace("M-", ""))
    b.set_xlabel("ILC iteration (one rig run each)"); b.set_ylabel("swing tracking RMSE (deg)")
    b.set_title("Correction loop through the real tools"); b.legend(frameon=False, fontsize=9); style(b)
    fig.suptitle("Dry run of a full session: planned files, synthetic encoder and phone, scored like a real session", fontweight="bold", color=NAVY)
    save(fig, "nm_dryrun")


if __name__ == "__main__":
    only = [x for x in os.environ.get("ONLY", "").split(",") if x]
    for fn in (fig_ladder, fig_behaviours, fig_matrix, fig_rig_ilc, fig_range, fig_dryrun):
        if only and fn.__name__ not in only: continue
        print("==", fn.__name__); fn()
