"""Additional dynamic-characterization evidence (phone gyro vs 1 kHz encoder):
  dy_bland      - Bland-Altman for rise / overshoot / settle (bias, limits of agreement)
  dy_velocity   - angular velocity: peak rate per step, and rate residual vs speed (omega_max is a scored parameter)
  dy_bode       - empirical frequency response pooled over all paired recordings: gain, phase, coherence
  dy_ring_steps - ring frequency and decay after every fast step, phone vs encoder (84 more rings than the 7 motor swings)
"""
import os, sys
os.environ.setdefault("ONLY", "none")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np, pandas as pd
import matplotlib.pyplot as plt
from scipy.signal import csd, welch
import bench_figs as B
from bench_figs import (ss, SPD, ETRIAL, TEAL, SEA, NAVY, CORAL, GREY, INK, PANEL, style, note, save, safe,
                        ssr_pairs, motor_pairs, phone_angle, enc_load, align, extrema, OUT)

FS = 100.0  # common analysis rate (Hz)

def _pairs_ssr(min_r=0.98):
    cfg = ss.groupby(ETRIAL)[SPD].first(); out = []
    for k, tr, pf, ef in ssr_pairs():
        if tr not in cfg.index: continue
        tp, ap, wp = phone_angle(pf); te, pe, ve, sp = enc_load(ef); lag, sg, r = align(tp, ap, te, pe)
        if r > min_r: out.append(dict(trial=tr, speed=cfg[tr], tp=tp + lag, ap=sg * ap, wp=sg * wp, te=te, pe=pe, ve=ve, sp=sp, r=r))
    return out

def _steps(p):
    """Step starts from the encoder setpoint ramp, one per step (1 s refractory)."""
    te, sp = p["te"], p["sp"]; moving = np.abs(np.gradient(sp, te)) > 1.0
    raw = np.where(np.diff(moving.astype(int)) == 1)[0] + 1; starts = []
    for s0 in raw:
        if not starts or te[s0] - te[starts[-1]] > 1.0: starts.append(s0)
    ends = [te[s] for s in starts[1:]] + [te[-1]]
    return [(te[s], e) for s, e in zip(starts, ends) if e - te[s] > 1.5]

def _metrics(t, x, t0, t1):
    frm = np.median(x[(t >= t0 - 0.3) & (t < t0)]); to = np.median(x[(t >= t1 - 1.0) & (t <= t1)]); step = to - frm
    if abs(step) < 5: return None
    w = (t >= t0) & (t <= t1); tw, xw = t[w], (x[w] - frm) / step
    i10 = np.argmax(xw >= 0.1); i90 = np.argmax(xw >= 0.9); out = np.where(np.abs(xw - 1.0) > 0.02)[0]
    return dict(rise=tw[i90] - tw[i10], over=max(0.0, (xw.max() - 1.0) * 100), settle=(tw[out[-1]] - t0) if len(out) else 0.0, step=step, frm=frm, to=to)

def _ba(ax, a, b, unit, title, c=TEAL):
    d = b - a; mu = (a + b) / 2; bias, sd = d.mean(), d.std(ddof=1)
    ax.scatter(mu, d, s=22, color=c, alpha=0.75, edgecolor="white", lw=0.4)
    ax.axhline(bias, color=NAVY, lw=1.5); ax.axhline(bias + 1.96 * sd, color=CORAL, ls="--", lw=1); ax.axhline(bias - 1.96 * sd, color=CORAL, ls="--", lw=1)
    ax.set_xlabel(f"mean of phone & encoder ({unit})"); ax.set_ylabel(f"phone − encoder ({unit})"); ax.set_title(title); style(ax)
    note(ax, f"bias {bias:+.3g} {unit}\nLoA ±{1.96 * sd:.3g} {unit}\nn = {len(d)}", "upper left")
    return bias, sd

def dy_bland():
    rows = []
    for p in _pairs_ssr():
        for t0, t1 in _steps(p):
            me = _metrics(p["te"], p["pe"], t0, t1); mq = p["tp"] >= p["te"][0]; mp = _metrics(p["tp"][mq], p["ap"][mq], t0, t1)
            if me and mp: rows.append(dict(trial=p["trial"], speed=p["speed"], **{f"e_{k}": me[k] for k in ("rise", "over", "settle")}, **{f"p_{k}": mp[k] for k in ("rise", "over", "settle")}))
    d = pd.DataFrame(rows); d.to_csv(os.path.join(OUT, "step_dynamics_paired.csv"), index=False)
    fig, axs = plt.subplots(1, 3, figsize=(13.5, 4.2))
    _ba(axs[0], d.e_rise, d.p_rise, "s", "Rise time 10–90 %"); _ba(axs[1], d.e_over, d.p_over, "%", "Overshoot"); _ba(axs[2], d.e_settle, d.p_settle, "s", "Settle time ±2 %")
    fig.suptitle("Step-transient metrics, Bland–Altman: phone vs encoder over every step", fontweight="bold", color=NAVY)
    save(fig, "dy_bland.png"); print(f"   n={len(d)}  rise bias {(d.p_rise - d.e_rise).mean():+.4f} s  over bias {(d.p_over - d.e_over).mean():+.2f} %  settle bias {(d.p_settle - d.e_settle).mean():+.3f} s")

def dy_velocity():
    peaks, res_v, res_e = [], [], []
    for p in _pairs_ssr():
        te, ve, tp, wp = p["te"], p["ve"], p["tp"], p["wp"]
        for t0, t1 in _steps(p):
            we = (te >= t0) & (te <= t0 + 0.6); wq = (tp >= t0) & (tp <= t0 + 0.6)
            if we.sum() < 10 or wq.sum() < 10: continue
            ie = np.argmax(np.abs(ve[we])); iq = np.argmax(np.abs(wp[wq]))
            peaks.append(dict(trial=p["trial"], speed=p["speed"], enc=ve[we][ie], phone=wp[wq][iq]))
        m = (tp >= te[0]) & (tp <= te[-1]); vi = np.interp(tp[m], te, ve); res_v.append(vi); res_e.append(wp[m] - vi)
    d = pd.DataFrame(peaks); vi = np.concatenate(res_v); er = np.concatenate(res_e); d.to_csv(os.path.join(OUT, "peak_velocity_paired.csv"), index=False)
    fig, axs = plt.subplots(1, 3, figsize=(13.5, 4.2))
    a = axs[0]; lim = max(np.abs(d.enc).max(), np.abs(d.phone).max()) * 1.08
    a.plot([-lim, lim], [-lim, lim], "--", color=GREY, lw=1.2)
    for sp, c in [(200, TEAL), (250, CORAL)]:
        k = d.speed == sp; a.scatter(d.enc[k], d.phone[k], s=24, color=c, alpha=0.8, edgecolor="white", lw=0.4, label=f"{sp} °/s")
    g = np.polyfit(d.enc, d.phone, 1)[0]; r2 = np.corrcoef(d.enc, d.phone)[0, 1] ** 2
    a.set_xlabel("encoder peak angular velocity (°/s)"); a.set_ylabel("phone gyro peak angular velocity (°/s)"); a.set_title("Peak velocity of every step"); a.legend(frameon=False, loc="upper left"); style(a)
    note(a, f"gain {g:.3f}  ·  R² {r2:.3f}\nn = {len(d)} steps")
    _ba(axs[1], d.enc.abs(), d.phone.abs(), "°/s", "Peak velocity, Bland–Altman")
    b = axs[2]; bins = np.arange(0, 320, 20); idx = np.digitize(np.abs(vi), bins); mu = []; sd = []; ctr = []
    for i in range(1, len(bins)):
        k = idx == i
        if k.sum() > 50: ctr.append((bins[i - 1] + bins[i]) / 2); mu.append(er[k].mean()); sd.append(er[k].std())
    ctr, mu, sd = map(np.array, (ctr, mu, sd))
    b.fill_between(ctr, mu - sd, mu + sd, color=SEA, alpha=0.2, label="±1 SD"); b.plot(ctr, mu, color=SEA, lw=2, marker="o", ms=4, label="mean residual")
    b.axhline(0, color=GREY, lw=0.8); b.set_xlabel("|encoder angular velocity| (°/s)"); b.set_ylabel("phone rate − encoder velocity (°/s)"); b.set_title("Rate residual vs speed, all samples"); b.legend(frameon=False, loc="upper left"); style(b)
    note(b, f"{len(er):,} samples\nslope of mean residual {np.polyfit(ctr, mu, 1)[0] * 100:+.2f} °/s per 100 °/s", "lower left")
    fig.suptitle("Angular velocity: the phone's rate channel against the encoder's derivative", fontweight="bold", color=NAVY)
    save(fig, "dy_velocity.png"); print(f"   peaks n={len(d)} gain {g:.3f} R2 {r2:.4f}; sample residual mean {er.mean():+.2f} sd {er.std():.2f}")

def dy_bode():
    Pxx = Pyy = Pxy = None; n = 0; recs = _pairs_ssr()
    mp, encd = motor_pairs()
    for rank, pf, ef, lag, sg, r in mp:
        if r < 0.999: continue
        tp, ap, wp = phone_angle(pf); te, pe, ve, _ = encd[ef]; recs.append(dict(trial=f"M{rank}", speed=0, tp=tp + lag, ap=sg * ap, wp=sg * wp, te=te, pe=pe, ve=ve, sp=None, r=r))
    for p in recs:
        t0, t1 = max(p["tp"][0], p["te"][0]), min(p["tp"][-1], p["te"][-1]); g = np.arange(t0, t1, 1 / FS)
        if len(g) < 600: continue
        x = np.interp(g, p["te"], p["pe"]); y = np.interp(g, p["tp"], p["ap"]); x -= x.mean(); y -= y.mean()
        f, pxx = welch(x, FS, nperseg=512); _, pyy = welch(y, FS, nperseg=512); _, pxy = csd(x, y, FS, nperseg=512)
        Pxx = pxx if Pxx is None else Pxx + pxx; Pyy = pyy if Pyy is None else Pyy + pyy; Pxy = pxy if Pxy is None else Pxy + pxy; n += 1
    H = Pxy / Pxx; coh = np.abs(Pxy) ** 2 / (Pxx * Pyy); gain = np.abs(H); ph = np.degrees(np.angle(H))
    band = (f >= 0.4) & (f <= 12); good = band & (coh > 0.9)
    tau_ms = -np.polyfit(f[good], np.radians(ph[good]), 1)[0] / (2 * np.pi) * 1000
    pd.DataFrame(dict(f=f, gain=gain, phase_deg=ph, coherence=coh)).to_csv(os.path.join(OUT, "bode_pooled.csv"), index=False)
    fig, axs = plt.subplots(3, 1, figsize=(7.5, 8.2), sharex=True)
    axs[0].semilogx(f[band], gain[band], color=TEAL, lw=2); axs[0].axhline(1, color=GREY, ls="--", lw=1); axs[0].set_ylim(0.8, 1.2); axs[0].set_ylabel("gain |phone / encoder|"); axs[0].set_title(f"Empirical frequency response, pooled over {n} paired recordings")
    axs[0].axvspan(0.5, 1.5, color=SEA, alpha=0.1); axs[0].text(0.55, 1.16, "knee pendulum band 0.5–1.5 Hz", color=SEA, fontsize=9)
    axs[1].semilogx(f[band], ph[band], color=TEAL, lw=2); axs[1].axhline(0, color=GREY, ls="--", lw=1); axs[1].set_ylim(-30, 30); axs[1].set_ylabel("phase (deg)")
    axs[2].semilogx(f[band], coh[band], color=NAVY, lw=2); axs[2].axhline(0.9, color=CORAL, ls="--", lw=1); axs[2].set_ylim(0, 1.05); axs[2].set_ylabel("coherence"); axs[2].set_xlabel("frequency (Hz)")
    for ax in axs: style(ax)
    gb = gain[(f >= 0.5) & (f <= 1.5)]; g3 = gain[(f >= 0.5) & (f <= 5)]
    note(axs[0], f"0.5–1.5 Hz: gain {gb.mean():.3f} ± {gb.std():.3f}\n0.5–5 Hz: gain {g3.mean():.3f} ± {g3.std():.3f}", "lower left")
    note(axs[1], f"phase slope → {tau_ms:+.1f} ms residual delay\n(after cross-correlation alignment)", "lower left")
    note(axs[2], f"coherence > 0.9 up to {f[good].max():.1f} Hz", "lower left")
    save(fig, "dy_bode.png"); print(f"   n={n}; gain 0.5-1.5 Hz {gb.mean():.4f}; 0.5-5 Hz {g3.mean():.4f}; coh>0.9 to {f[good].max():.1f} Hz; tau {tau_ms:+.1f} ms")

def dy_ring_steps():
    rows = []
    for p in _pairs_ssr():
        for t0, t1 in _steps(p):
            out = {}
            for nm, t, x in (("e", p["te"], p["pe"]), ("p", p["tp"], p["ap"])):
                w = (t >= t0 + 0.2) & (t <= min(t0 + 1.3, t1 - 0.2)); tt, xx = t[w], x[w]
                if len(tt) < 20: break
                lvl = np.median(x[(t >= t1 - 1.0) & (t <= t1)]); y = xx - lvl
                et, ev = extrema(tt, y, 0.5)
                if len(et) < 4: break
                fr = (len(et) - 1) / (2 * (et[-1] - et[0])); sl = np.polyfit(et - et[0], np.log(np.abs(ev)), 1)[0]
                out[nm] = (fr, (-1 / sl) if sl < 0 else np.nan, len(et) / 2)
            if len(out) == 2: rows.append(dict(trial=p["trial"], speed=p["speed"], e_f=out["e"][0], p_f=out["p"][0], e_tau=out["e"][1], p_tau=out["p"][1], e_N=out["e"][2], p_N=out["p"][2]))
    d = pd.DataFrame(rows); d.to_csv(os.path.join(OUT, "ring_steps_paired.csv"), index=False)
    fig, axs = plt.subplots(1, 3, figsize=(13.5, 4.2))
    for ax, (a, b, lab, unit) in zip(axs, [("e_f", "p_f", "Ring frequency", "Hz"), ("e_tau", "p_tau", "Decay time constant τ", "s"), ("e_N", "p_N", "Ring cycles counted", "cycles")]):
        dd = d.dropna(subset=[a, b]); lo, hi = min(dd[a].min(), dd[b].min()) * 0.9, max(dd[a].max(), dd[b].max()) * 1.08
        ax.plot([lo, hi], [lo, hi], "--", color=GREY, lw=1.2)
        for sp, c in [(200, TEAL), (250, CORAL)]:
            k = dd.speed == sp; ax.scatter(dd[a][k], dd[b][k], s=26, color=c, alpha=0.8, edgecolor="white", lw=0.4, label=f"{sp} °/s")
        rel = ((dd[b] - dd[a]).abs() / dd[a]).median() * 100
        ax.set_xlabel(f"encoder ({unit})"); ax.set_ylabel(f"phone gyro ({unit})"); ax.set_title(lab); style(ax)
        note(ax, f"median |Δ| {(dd[b] - dd[a]).abs().median():.3g} {unit} ({rel:.1f} %)\nn = {len(dd)} rings", "upper left")
    axs[0].legend(frameon=False, loc="lower right")
    fig.suptitle("The ring after every fast step: frequency, decay and cycle count, phone vs encoder", fontweight="bold", color=NAVY)
    save(fig, "dy_ring_steps.png"); print(f"   rings n={len(d)}; f median |dF| {((d.p_f - d.e_f).abs() / d.e_f).median() * 100:.1f} %; f range {d.e_f.min():.1f}-{d.e_f.max():.1f} Hz")

if __name__ == "__main__":
    only = [s for s in os.environ.get("ONLY", "").split(",") if s and s != "none"]
    for nm, fn in [("dy_bland", dy_bland), ("dy_velocity", dy_velocity), ("dy_bode", dy_bode), ("dy_ring_steps", dy_ring_steps)]:
        if not only or any(nm.startswith(o) for o in only): safe(nm, fn)
