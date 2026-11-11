"""Additional static-characterization evidence: does the 0.58 deg hide structure?
  st_structure  - error by step type, across the session, and vs drift measured in the hold
  st_wave_rmse  - sample-by-sample agreement over whole recordings (not just settled endpoints)
  st_hold       - zoom inside three holds: noise floor and drift at the scale that matters
Run:  ONLY=none is set internally so bench_figs' own figure loop does not run on import.
"""
import os, sys
os.environ.setdefault("ONLY", "none")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np, pandas as pd
import matplotlib.pyplot as plt
import bench_figs as B
from bench_figs import ss, ERR, DRIFT, SPD, ETRIAL, TEAL, SEA, NAVY, CORAL, GREY, INK, PANEL, style, save, safe, ssr_pairs, phone_angle, enc_load, align, OUT

def _step_label(r):
    a, b = r["From°"], r["To°"]
    return f"{a:+.0f}→{b:+.0f}"

def st_structure():
    d = ss.copy(); d["type"] = d.apply(_step_label, axis=1)
    order = [t for t in ["+0→+38", "+38→-38", "-38→+38", "+38→+0"] if t in set(d.type)] or sorted(d.type.unique())
    fig, (a, b, c) = plt.subplots(1, 3, figsize=(14, 4.4), gridspec_kw=dict(width_ratios=[1.1, 1.3, 1]))
    groups = [d.loc[d.type == t, ERR] for t in order]
    bp = a.boxplot(groups, widths=0.55, patch_artist=True, showfliers=False, medianprops=dict(color=INK, lw=1.8))
    for patch in bp["boxes"]: patch.set_facecolor(TEAL); patch.set_alpha(0.45); patch.set_edgecolor(TEAL)
    for i, g in enumerate(groups, start=1):
        a.scatter(np.full(len(g), i) + np.random.default_rng(i).normal(0, 0.06, len(g)), g, s=12, color=TEAL, alpha=0.7, zorder=3)
    a.axhline(0, color=GREY, lw=0.8); a.set_xticks(range(1, len(order) + 1)); a.set_xticklabels([f"{t}°\nn={len(g)}" for t, g in zip(order, groups)], fontsize=9)
    a.set_ylabel("phone − encoder (deg)"); a.set_title(f"By step type: medians within {max(abs(g.median()) for g in groups):.1f}° of zero"); style(a)
    sd = d.groupby("type")[ERR].std().mean()
    a.text(0.03, 0.04, f"per-type SD {sd:.2f}°  →  MDC95 {1.96 * np.sqrt(2) * sd:.2f}°", transform=a.transAxes, fontsize=9.5, color=NAVY, bbox=dict(boxstyle="round,pad=0.3", fc=PANEL, ec="#c9d2da"))
    for sp, col in [(200, TEAL), (250, CORAL)]:
        k = d[SPD] == sp; b.scatter(d.loc[k, "Rec #"], d.loc[k, ERR], s=18, color=col, alpha=0.75, label=f"{sp} °/s")
    rec = d.groupby("Rec #")[ERR].mean(); b.plot(rec.index, rec.values, color=INK, lw=1.6, label="recording mean")
    sl = np.polyfit(rec.index, rec.values, 1)[0]
    b.axhline(0, color=GREY, lw=0.8); b.set_xlabel("recording, in capture order"); b.set_ylabel("phone − encoder (deg)"); b.set_title("Across the session: no warm-up or thermal trend"); b.legend(frameon=False, fontsize=9); style(b)
    b.text(0.03, 0.04, f"trend {sl * len(rec):+.2f}° over {len(rec)} recordings", transform=b.transAxes, fontsize=9.5, color=NAVY, bbox=dict(boxstyle="round,pad=0.3", fc=PANEL, ec="#c9d2da"))
    r = np.corrcoef(d[DRIFT], d[ERR])[0, 1]
    c.scatter(d[DRIFT] * 3.0, d[ERR], s=18, color=SEA, alpha=0.75); c.axhline(0, color=GREY, lw=0.8); c.axvline(0, color=GREY, lw=0.8)
    c.set_xlabel("gyro drift over the 3 s hold (deg)"); c.set_ylabel("phone − encoder (deg)"); c.set_title(f"vs drift in the hold: r = {r:.2f}"); style(c)
    fig.suptitle("Static residuals carry no session trend and no drift dependence — the 0.58° is scatter, not a bias", fontweight="bold", color=NAVY, fontsize=13.5)
    save(fig, "st_structure.png"); print(f"   per-type SD {sd:.3f}, session trend {sl * len(rec):+.3f} deg, drift r {r:.3f}")

def st_wave_rmse():
    pairs = ssr_pairs(); cfg = ss.groupby(ETRIAL)[SPD].first(); rows = []; resid = []
    for k, tr, pf, ef in pairs:
        if tr not in cfg.index: continue
        tp, ap, _ = phone_angle(pf); te, pe, _, _ = enc_load(ef); lag, sg, r = align(tp, ap, te, pe)
        if not r > 0.98: continue
        tq = tp + lag; m = (tq >= te[0]) & (tq <= te[-1]); e = sg * ap[m] - np.interp(tq[m], te, pe)
        rows.append(dict(trial=tr, speed=cfg[tr], r=r, rmse=np.sqrt(np.mean(e ** 2)), mae=np.abs(e).mean(), p95=np.percentile(np.abs(e), 95), n=int(m.sum()))); resid.append(e)
    d = pd.DataFrame(rows); allr = np.concatenate(resid); d.to_csv(os.path.join(OUT, "waveform_rmse.csv"), index=False)
    p95 = np.percentile(np.abs(allr), 95)
    print(f"   {len(d)} recordings, {len(allr)} samples; RMSE median {d.rmse.median():.2f} (200: {d.rmse[d.speed == 200].median():.2f}, 250: {d.rmse[d.speed == 250].median():.2f}); pooled 95% |resid| {p95:.2f}")
    fig, (a, b) = plt.subplots(1, 2, figsize=(12.5, 4.4), gridspec_kw=dict(width_ratios=[1, 1.2]))
    for sp, col in [(200, TEAL), (250, CORAL)]:
        k = d.speed == sp; x = np.full(k.sum(), sp) + np.random.default_rng(5).normal(0, 4, k.sum())
        a.scatter(x, d.rmse[k], s=45, color=col, edgecolor="white", lw=0.7, zorder=3); a.hlines(d.rmse[k].median(), sp - 15, sp + 15, color=INK, lw=2.2, zorder=4)
        a.text(sp + 17, d.rmse[k].median(), f"median {d.rmse[k].median():.2f}°", va="center", fontsize=10, color=NAVY)
    a.set_xticks([200, 250]); a.set_xlim(170, 300); a.set_xlabel("commanded step speed (°/s)"); a.set_ylabel("whole-recording RMSE, phone vs encoder (deg)")
    a.set_title(f"Every sample of every recording  (n = {len(d)})"); a.set_ylim(0, max(3, d.rmse.max() * 1.15)); style(a)
    b.hist(allr, bins=np.arange(-5, 5.01, 0.1), color=TEAL, edgecolor="white"); b.axvline(0, color=INK, lw=1)
    b.axvline(-p95, color=CORAL, ls="--"); b.axvline(p95, color=CORAL, ls="--")
    b.text(p95 + 0.15, b.get_ylim()[1] * 0.88, f"95 % of samples\nwithin ±{p95:.1f}°", color=CORAL, fontsize=10)
    b.set_xlabel("phone − encoder, every 10 ms sample (deg)"); b.set_ylabel("samples"); b.set_title(f"{len(allr):,} aligned samples across {len(d)} recordings"); style(b)
    fig.suptitle("Beyond the endpoints: the phone matches the encoder throughout the motion", fontweight="bold", color=NAVY, fontsize=13.5)
    save(fig, "st_wave_rmse.png")

def st_hold():
    pairs = ssr_pairs(); cfg = ss.groupby(ETRIAL)[SPD].first()
    k, tr, pf, ef = next(p for p in pairs if p[1] in cfg.index and cfg[p[1]] == 200 and p[0] > 3)
    tp, ap, _ = phone_angle(pf); te, pe, _, sp = enc_load(ef); lag, sg, r = align(tp, ap, te, pe); tq = tp + lag
    dsp = np.abs(np.gradient(sp, te)) > 1; raw = np.where(np.diff(dsp.astype(int)) == 1)[0] + 1
    starts = []
    for s0 in raw:
        if not starts or te[s0] - te[starts[-1]] > 1.0: starts.append(s0)
    fig, axs = plt.subplots(1, 3, figsize=(13.5, 4.0)); noise = []
    for j, (ax, s0) in enumerate(zip(axs, starts[:3])):
        t0 = te[s0]; t1 = te[starts[j + 1]] if j + 1 < len(starts) else te[-1]
        w = (te >= t0 + 0.9) & (te <= t1 - 0.05); wq = (tq >= t0 + 0.9) & (tq <= t1 - 0.05)
        lvl = np.median(pe[w]); ph = sg * ap[wq]
        ax.plot(te[w] - t0, pe[w] - lvl, color=INK, lw=2.2, label="encoder"); ax.plot(tq[wq] - t0, ph - lvl, color=TEAL, lw=1.4, label="phone gyro")
        ax.axhspan(-0.5, 0.5, color=SEA, alpha=0.08); ax.set_ylim(-1.5, 1.5); ax.set_xlabel("time from step (s)"); ax.set_ylabel("angle − hold level (deg)")
        e = ph - np.interp(tq[wq], te, pe); fit = np.polyfit(tq[wq], ph, 1); noise.append(np.std(ph - np.polyval(fit, tq[wq])))
        ax.set_title(f"hold at {lvl:+.1f}°  ·  mean |err| {np.abs(e).mean():.2f}°, drift {fit[0]:+.2f} °/s", fontsize=10.5); style(ax)
    axs[0].legend(frameon=False, fontsize=9.5, loc="upper left"); axs[0].text(0.03, 0.05, "±0.5° band", transform=axs[0].transAxes, color=SEA, fontsize=9)
    fig.suptitle(f"Inside the holds (recording {tr}): once settled the phone is flat to {np.mean(noise):.2f}° SD — the error is a constant offset picked up in the step, not drift", fontweight="bold", color=NAVY, fontsize=13)
    save(fig, "st_hold.png")

if __name__ == "__main__":
    only = [s for s in os.environ.get("ONLY", "").split(",") if s and s != "none"]
    for nm, fn in [("st_structure", st_structure), ("st_wave_rmse", st_wave_rmse), ("st_hold", st_hold)]:
        if not only or any(nm.startswith(o) for o in only): safe(nm, fn)
