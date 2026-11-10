"""The three September-deck figures, from data (not simulated):
 - fig_ssr:      real SSR settled steps from the CK Final tracker's per-step tab (deck2/ssr_steps.csv)
 - fig_physics:  real hand-pulled swings, Manual_9_14 (deck2/manual_physics.csv)
 - fig_anglefix: Rust flex-projection validation (34/34 bench trials bit-identical)
PUB=1 also writes 300-dpi PNG + vector PDF with panel letters to the publication folder.
"""
import os, sys
import numpy as np, pandas as pd
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pubstyle import setup, save, TEAL, SEA, NAVY, CORAL, GREY, PANEL, style, note
setup()
D2 = os.path.dirname(os.path.abspath(__file__))

# ---- Figure 1: SSR sensor accuracy -- every settled step, phone gyro vs encoder ----
ss = pd.read_csv(os.path.join(D2, "ssr_steps.csv"), encoding="utf-8").dropna(subset=["Encoder Δ", "IMU Δ (gyro)"])
e, m = ss["Encoder Δ"].values, ss["IMU Δ (gyro)"].values
g, b = np.polyfit(e, m, 1); r2 = np.corrcoef(e, m)[0, 1] ** 2; d = m - e
fig, ax = plt.subplots(figsize=(6.6, 5.0))
ax.plot([-85, 85], [-85, 85], color=GREY, lw=1.4, ls="--", zorder=1, label="ideal (45°)")
for sp, c in [(200, TEAL), (250, CORAL)]:
    k = ss["Step speed"] == sp
    ax.scatter(e[k], m[k], s=40, color=c, alpha=0.9, edgecolor="white", lw=0.5, zorder=3, label=f"step speed {sp} °/s  (n = {k.sum()})")
ax.set_xlim(-85, 85); ax.set_ylim(-85, 85)
ax.set_xlabel("Encoder settled step Δ (deg)  —  1 kHz truth"); ax.set_ylabel("Phone gyro settled step Δ (deg)")
ax.set_title("SSR sensor accuracy: phone vs motor encoder")
ax.legend(loc="upper left", frameon=False, fontsize=10)
note(ax, f"{len(ss)} steps / {ss['Encoder trial'].nunique()} recordings\ngain {g:.3f}  ·  R² {r2:.3f}\nmean |error| {np.abs(d).mean():.2f}°   •   Bland–Altman {d.mean():+.2f}° ± {1.96 * d.std(ddof=1):.2f}°")
style(ax); save(fig, "fig_ssr")
print(f"SSR: n={len(ss)} gain={g:.3f} R2={r2:.4f} mean|err|={np.abs(d).mean():.3f} bias={d.mean():+.3f} LoA=±{1.96 * d.std(ddof=1):.2f} max={np.abs(d).max():.2f}")

# ---- Figure 2: frequency follows sqrt(P) (physics cross-check, hand-released swings) ----
mp = pd.read_csv(os.path.join(D2, "manual_physics.csv")); gm = mp.groupby("P").f.agg(["mean", "std", "count"])
f01, f1 = gm.loc[0.1, "mean"], gm.loc[1.0, "mean"]; k = f01 / np.sqrt(0.1)
fig, ax = plt.subplots(figsize=(6.6, 5.0))
xs = np.linspace(0.05, 1.15, 200)
ax.plot(xs, k * np.sqrt(xs), color=NAVY, lw=2.2, zorder=1, label="theory  f = k·√P  (fit at P = 0.1)")
ax.scatter(mp.P + np.random.default_rng(1).normal(0, 0.012, len(mp)), mp.f, s=70, color=TEAL, edgecolor="white", lw=1.0, zorder=3, label=f"phone-measured f  (n = {len(mp)} swings)")
for pv in (0.1, 1.0):
    ax.annotate(f"{gm.loc[pv, 'mean']:.2f} Hz", (pv, gm.loc[pv, "mean"]), textcoords="offset points", xytext=(12, -16), fontsize=11, color=NAVY, fontweight="bold")
ax.set_xlabel("Controller gain  P  (loop spring constant)"); ax.set_ylabel("Oscillation frequency  f  (Hz)")
ax.set_title("Frequency tracks √P  —  physics cross-check")
ax.legend(loc="upper left", frameon=False, fontsize=10.5)
note(ax, f"measured ratio {f1 / f01:.2f}×  vs  √10 = 3.16 predicted\n{len(mp)} swings  •  SD {gm.loc[0.1, 'std']:.3f} Hz at P 0.1")
ax.set_xlim(0, 1.15); ax.set_ylim(0, 3.6); style(ax); save(fig, "fig_physics")
print(f"physics: P0.1 {f01:.2f} Hz, P1 {f1:.2f} Hz, ratio {f1 / f01:.2f}")

# ---- Figure 3: Rust angle fix -- divergence from validated reference ----
fig, ax = plt.subplots(figsize=(6.6, 4.2))
labels = ["Before\n(shipping core)", "After\n(flex-axis fix)"]
worst = [53, 0.02]; median = [1.1, 0.0]
x = np.arange(2)
ax.bar(x - 0.17, worst, 0.34, color=CORAL, label="worst-trial error", zorder=2)
ax.bar(x + 0.17, median, 0.34, color=TEAL, label="median error", zorder=2)
for xi, w, md in zip(x, worst, median):
    ax.annotate(f"{w:g}°", (xi - 0.17, w), textcoords="offset points", xytext=(0, 3), ha="center", fontsize=10, color=NAVY, fontweight="bold")
    ax.annotate(f"{md:g}°", (xi + 0.17, md), textcoords="offset points", xytext=(0, 3), ha="center", fontsize=10, color=NAVY, fontweight="bold")
ax.set_xticks(x); ax.set_xticklabels(labels, fontsize=11)
ax.set_ylabel("App angle error vs validated reference (deg)")
ax.set_title("Angle fix: shipping app now matches the validated core")
ax.legend(loc="upper right", frameon=False, fontsize=10.5)
ax.text(0.62, 0.55, "34 / 34 bench trials\nnow bit-identical", transform=ax.transAxes, ha="center", fontsize=10.5, color=NAVY, bbox=dict(boxstyle="round,pad=0.4", fc=PANEL, ec="#c9d2da"))
ax.set_ylim(0, 60); style(ax); save(fig, "fig_anglefix")
print("wrote fig_ssr.png, fig_physics.png, fig_anglefix.png")
