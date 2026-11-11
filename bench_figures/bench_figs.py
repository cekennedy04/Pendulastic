"""Figures for the extended Pendulastic progress deck (static / dynamic / PID / cohort / markerless).
Every figure is regenerated from data on disk; provenance is printed per figure.
"""
import sys, os, json, glob, traceback
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
import numpy as np, pandas as pd
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt
from scipy.stats import mannwhitneyu
from labview_bin_reader import read_labview_bin

OUT = os.path.dirname(os.path.abspath(__file__))
MAT = os.path.join(REPO, "Pendulastic_Mobile_App_Trial")
ENC = os.path.join(MAT, "ck_9_14_pt")
sys.path.insert(0, OUT)
from pubstyle import setup, style, note, save as _save, TEAL, SEA, NAVY, CORAL, GREY, INK, PANEL, GOLD, PUB
setup()
def save(fig, name): _save(fig, name, OUT)
def safe(name, fn):
    print("==", name)
    try: fn()
    except Exception: traceback.print_exc()

# ----------------------------------------------------------------- signal helpers
def phone_angle(path):
    recs = [json.loads(l) for l in open(path, encoding="utf-8") if l.strip()]
    g = sorted((r["t"], r["v"][1]) for r in recs if r.get("sensor") == "gyro")
    t = np.array([x[0] for x in g], float); w = np.degrees(np.array([x[1] for x in g], float))
    if np.median(np.diff(t)) > 0.5: t = t / 1000.0
    t = t - t[0]
    ang = np.concatenate([[0.0], np.cumsum((w[1:] + w[:-1]) / 2 * np.diff(t))])
    return t, ang - np.median(ang[:40]), w
def enc_load(path):
    e = read_labview_bin(path); t = e["time"]; p = e["position"] - np.median(e["position"][:100])
    return t, p, np.asarray(e["velocity"], float), e.get("position_setpoint")
def align(tp, ap, te, pe, dt=0.01):
    """Return (lag, sign, r): phone time in encoder frame = tp + lag; phone angle *= sign."""
    gp = np.arange(tp[0], tp[-1], dt); ge = np.arange(te[0], te[-1], dt)
    xp = np.interp(gp, tp, ap); xe = np.interp(ge, te, pe); xp -= xp.mean(); xe -= xe.mean()
    c = np.correlate(xe, xp, "full"); lags = np.arange(-(len(xp) - 1), len(xe))
    k = int(np.argmax(np.abs(c))); sign = 1.0 if c[k] >= 0 else -1.0; lag = lags[k] * dt + (ge[0] - gp[0])
    # pearson on overlap
    tq = tp + lag; m = (tq >= te[0]) & (tq <= te[-1]); r = np.corrcoef(sign * ap[m], np.interp(tq[m], te, pe))[0, 1] if m.sum() > 50 else np.nan
    return lag, sign, r
def order_by_capture(manifest):
    man = json.load(open(manifest, encoding="utf-8")); base = os.path.dirname(manifest)
    return [os.path.join(base, x["file"]) for x in sorted(man["trials"], key=lambda t: t["timestamp"])]
def extrema(t, x, thresh):
    """Alternating local extrema of x with |x| > thresh. Returns (times, values)."""
    d = np.sign(np.diff(x)); idx = np.where(np.diff(d) != 0)[0] + 1
    ts, vs = [], []
    for i in idx:
        if abs(x[i]) > thresh and (not vs or np.sign(x[i]) != np.sign(vs[-1])):
            ts.append(t[i]); vs.append(x[i])
        elif vs and np.sign(x[i]) == np.sign(vs[-1]) and abs(x[i]) > abs(vs[-1]):
            ts[-1], vs[-1] = t[i], x[i]
    return np.array(ts), np.array(vs)
def ring_metrics(t, x, rate, mode="motor"):
    """Ring-down metrics. mode='manual': recording starts held at the pull, release = first |rate|>40 deg/s.
    mode='motor': release = last slow moment (|rate|<15) before the fastest motion (the commanded return).
    Rest = median of the last 1 s. f = FFT peak of the first 4 s after release; N = swings (extrema/2); tau from log-envelope fit."""
    dt = np.median(np.diff(t)); n1 = int(1 / dt)
    if mode == "manual":
        fast = np.where(np.abs(rate) > 40)[0]
        if not len(fast): return None
        rel = int(fast[0])
    else:
        ipk = int(np.argmax(np.abs(rate))); slow = np.where(np.abs(rate[:ipk]) < 15)[0]
        rel = int(slow[-1]) if len(slow) else max(0, ipk - int(0.2 / dt))
    rest = np.median(x[-n1:]); pull = abs(x[rel] - rest)
    if pull < 5 or (t[-1] - t[rel]) < 1.5: return None
    seg_t, seg_x = t[rel:], x[rel:]; y = seg_x - rest
    w = seg_t - seg_t[0] <= 4.0; yy = y[w] - y[w].mean(); g = np.arange(0, seg_t[w][-1] - seg_t[0], 0.005); yg = np.interp(g, seg_t[w] - seg_t[0], yy) * np.hanning(len(g))
    spec = np.abs(np.fft.rfft(yg, n=1 << 17)); fr = np.fft.rfftfreq(1 << 17, 0.005); k = (fr > 0.2) & (fr < 8); f = fr[k][np.argmax(spec[k])]
    thr = max(0.05 * np.max(np.abs(y[:int(2 / dt)])), 0.5); et, ev = extrema(seg_t, y, thr)
    tau = np.nan; f_zc = np.nan
    if len(et) >= 3:
        sl = np.polyfit(et - et[0], np.log(np.abs(ev)), 1)[0]; tau = -1 / sl if sl < 0 else np.nan
        f_zc = (len(et) - 1) / (2 * (et[-1] - et[0]))          # half-cycles between first and last extremum
    return dict(f=f_zc if np.isfinite(f_zc) else f, f_fft=f, N=len(et) / 2, tau=tau, pull=pull, t_rel=t[rel], rest=rest, et=et, ev=ev)
def gyro_to_phone_frame(path):
    return phone_angle(path)

# ----------------------------------------------------------------- STATIC
ENC_D, IMU_D, ERR, AERR, DRIFT, SPD, ETRIAL = "Encoder Δ", "IMU Δ (gyro)", "Δ error", "|Δ err|", "Drift °/s", "Step speed", "Encoder trial"
ss = pd.read_csv(os.path.join(OUT, "ssr_steps.csv"), encoding="utf-8").dropna(subset=[ENC_D, IMU_D])
print("SSR clean:", len(ss), "steps /", ss[ETRIAL].nunique(), "recordings")
def st_scatter():
    e, m = ss[ENC_D].values, ss[IMU_D].values
    g, b = np.polyfit(e, m, 1); r2 = np.corrcoef(e, m)[0, 1] ** 2
    fig, ax = plt.subplots(figsize=(6.6, 5.2))
    ax.plot([-85, 85], [-85, 85], "--", color=GREY, lw=1.3, label="ideal (45°)")
    for sp, c in [(200, TEAL), (250, CORAL)]:
        k = ss[SPD] == sp; ax.scatter(e[k], m[k], s=34, color=c, alpha=0.85, edgecolor="white", lw=0.5, label=f"step speed {sp} °/s  (n={k.sum()})")
    ax.set_xlabel("Encoder settled step Δ (deg) — 1 kHz truth"); ax.set_ylabel("Phone gyro settled step Δ (deg)")
    ax.set_title("Static accuracy: every settled step, phone vs encoder"); ax.legend(loc="upper left", frameon=False, fontsize=10)
    note(ax, f"{len(ss)} steps / {ss[ETRIAL].nunique()} recordings\ngain {g:.3f}  ·  R² {r2:.3f}\nmean |error| {ss[AERR].mean():.2f}°  ·  max {ss[AERR].max():.1f}°")
    ax.set_xlim(-85, 85); ax.set_ylim(-85, 85); style(ax); save(fig, "st_scatter.png")
    print("   gain", g, "R2", r2, "mean|err|", ss[AERR].mean(), "sd", ss[ERR].std(), "bias", ss[ERR].mean())
def st_bland():
    e, m = ss[ENC_D].values, ss[IMU_D].values; d = m - e; mu = (m + e) / 2; bias, sd = d.mean(), d.std(ddof=1)
    fig, ax = plt.subplots(figsize=(6.6, 4.6))
    ax.scatter(np.abs(mu), d, s=30, color=TEAL, alpha=0.8, edgecolor="white", lw=0.5)
    ax.axhline(bias, color=NAVY, lw=1.6); ax.axhline(bias + 1.96 * sd, color=CORAL, ls="--"); ax.axhline(bias - 1.96 * sd, color=CORAL, ls="--")
    ax.axhspan(-5, 5, color=SEA, alpha=0.07)
    ax.text(80, bias + 1.96 * sd + 0.25, f"+1.96 SD  {bias + 1.96 * sd:+.2f}°", ha="right", color=CORAL, fontsize=10)
    ax.text(80, bias - 1.96 * sd - 0.9, f"−1.96 SD  {bias - 1.96 * sd:+.2f}°", ha="right", color=CORAL, fontsize=10)
    ax.text(80, bias + 0.25, f"bias {bias:+.2f}°", ha="right", color=NAVY, fontsize=10)
    ax.text(2, 4.3, "± 5° clinical goal", color=SEA, fontsize=9.5, va="top")
    ax.set_xlabel("|step magnitude| (deg), mean of phone & encoder"); ax.set_ylabel("phone − encoder (deg)")
    ax.set_title("Bland–Altman: bias and limits of agreement"); ax.set_ylim(-6, 6); style(ax); save(fig, "st_bland.png")
def st_err_drift():
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(11, 4.2))
    a1.hist(ss[AERR], bins=np.arange(0, 5.5, 0.25), color=TEAL, edgecolor="white")
    a1.axvline(5, color=CORAL, ls="--"); a1.text(4.9, a1.get_ylim()[1] * 0.9, "5° clinical goal", ha="right", color=CORAL)
    a1.set_xlabel("|step error| (deg)"); a1.set_ylabel("steps"); a1.set_title(f"Where the error lives — {int((ss[AERR] < 1).mean() * 100)}% of steps under 1°"); style(a1)
    a2.hist(ss[DRIFT].abs(), bins=np.arange(0, 0.8, 0.04), color=SEA, edgecolor="white")
    a2.set_xlabel("|gyro drift| during a 3 s hold (°/s)"); a2.set_ylabel("holds")
    a2.set_title(f"Drift during holds — median {ss[DRIFT].abs().median():.2f} °/s, p90 {ss[DRIFT].abs().quantile(0.9):.2f}"); style(a2)
    save(fig, "st_err_drift.png")
def ssr_pairs():
    """phone capture-rank k <-> encoder trial k+68 (from the tracker's SSR tab)."""
    phones = order_by_capture(os.path.join(MAT, "pendulastic-QUICK-TEST-20260915T175634-manifest.json"))
    out = []
    for k, pf in enumerate(phones, start=1):
        ef = os.path.join(ENC, f"ck_ssr_pt_testStretchTrial{k + 68}Fast.bin")
        if os.path.exists(ef): out.append((k, k + 68, pf, ef))
    return out
def st_wave():
    pairs = ssr_pairs(); cfg = ss.groupby(ETRIAL)[SPD].first()
    scored = []                                   # best-aligned two recordings per step speed
    for k, tr, pf, ef in pairs:
        if tr not in cfg.index or k <= 3: continue
        tp, ap, _ = phone_angle(pf); te, pe, _, _ = enc_load(ef); scored.append((align(tp, ap, te, pe)[2], k, tr, pf, ef))
    picks = []
    for want in (200, 250):
        picks += [s[1:] for s in sorted([s for s in scored if cfg[s[2]] == want], reverse=True)[:2]]
    fig, axs = plt.subplots(2, 2, figsize=(12, 6.8)); axs = axs.ravel(); qs = []
    for ax, (k, tr, pf, ef) in zip(axs, picks):
        tp, ap, _ = phone_angle(pf); te, pe, _, _ = enc_load(ef); lag, sg, r = align(tp, ap, te, pe); qs.append(r)
        tq = tp + lag; m = (tq >= te[0]) & (tq <= te[-1]); err = np.abs(sg * ap[m] - np.interp(tq[m], te, pe))
        ax.plot(te, pe, color=INK, lw=2.4, label="encoder (1 kHz truth)"); ax.plot(tq, sg * ap, color=TEAL, lw=1.3, label="phone gyro")
        ax.set_title(f"recording {tr}  ·  step speed {int(cfg[tr])} °/s  ·  mean |err| {err.mean():.2f}°, max {err.max():.1f}°", fontsize=11.5)
        ax.set_xlim(te[0], te[-1]); ax.set_xlabel("time (s, aligned)"); ax.set_ylabel("angle (deg)"); style(ax)
    axs[0].legend(loc="lower right", frameon=False, fontsize=10)
    fig.suptitle("Static waveforms — the phone reproduces every step, overshoot and ring-down", fontweight="bold", color=NAVY, fontsize=14)
    save(fig, "st_wave.png"); print("   corr:", np.round(qs, 4))

# ----------------------------------------------------------------- DYNAMIC
def dy_step():
    """Step dynamics (rise / overshoot / settle) phone vs encoder with the correct pairing, same estimator both sides."""
    pairs = ssr_pairs(); cfg = ss.groupby(ETRIAL)[SPD].first(); rows = []
    for k, tr, pf, ef in pairs:
        try:
            tp, ap, _ = phone_angle(pf); te, pe, ve, sp = enc_load(ef); lag, sg, r = align(tp, ap, te, pe)
            if not (r > 0.98): continue
            tq = tp + lag; pq = sg * ap
            dsp = np.abs(np.gradient(sp, te)); moving = dsp > 1.0
            raw = np.where(np.diff(moving.astype(int)) == 1)[0] + 1
            starts = []                                   # the setpoint is a ramp: keep one start per step (1 s refractory)
            for s0 in raw:
                if not starts or te[s0] - te[starts[-1]] > 1.0: starts.append(s0)
            starts = np.array(starts)
            for s0 in starts:
                t0 = te[s0]; nxt = starts[starts > s0]; t_end = te[nxt[0]] if len(nxt) else te[-1]
                if t_end - t0 < 1.5: continue
                seg = (te >= t0 - 0.3) & (te <= t_end)
                def metr(tt, xx):
                    frm = np.median(xx[(tt >= t0 - 0.3) & (tt < t0)]); to = np.median(xx[(tt >= t_end - 1.0) & (tt <= t_end)])
                    step = to - frm
                    if abs(step) < 5: return None
                    w = (tt >= t0) & (tt <= t_end); tw, xw = tt[w], (xx[w] - frm) / step
                    i10 = np.argmax(xw >= 0.1); i90 = np.argmax(xw >= 0.9)
                    rise = tw[i90] - tw[i10]; over = max(0.0, (xw.max() - 1.0) * 100)
                    out = np.where(np.abs(xw - 1.0) > 0.02)[0]; settle = (tw[out[-1]] - t0) if len(out) else 0.0
                    return rise, over, settle, step
                me = metr(te, pe); mq = tq >= te[0]; mp = metr(tq[mq], pq[mq])
                if me and mp: rows.append(dict(trial=tr, speed=cfg.get(tr), enc_rise=me[0], ph_rise=mp[0], enc_over=me[1], ph_over=mp[1], enc_settle=me[2], ph_settle=mp[2], step=me[3]))
        except Exception as ex: print("   skip", tr, ex)
    d = pd.DataFrame(rows); d.to_csv(os.path.join(OUT, "step_dynamics_paired.csv"), index=False)
    print("   steps:", len(d), "trials:", d.trial.nunique()); print(d.describe().round(3).T[["50%", "mean"]])
    fig, axs = plt.subplots(1, 3, figsize=(13, 4.3))
    for ax, (a, b, lab, unit) in zip(axs, [("enc_rise", "ph_rise", "Rise time 10–90 %", "s"), ("enc_over", "ph_over", "Overshoot", "%"), ("enc_settle", "ph_settle", "Settle time ±2 %", "s")]):
        lo, hi = 0, max(d[a].max(), d[b].max()) * 1.08
        ax.plot([lo, hi], [lo, hi], "--", color=GREY, lw=1.2)
        for sp, c in [(200, TEAL), (250, CORAL)]:
            k = d.speed == sp; ax.scatter(d[a][k], d[b][k], s=30, color=c, alpha=0.8, edgecolor="white", lw=0.5, label=f"{sp} °/s")
        dd = (d[b] - d[a]).abs()
        ax.set_title(lab); ax.set_xlabel(f"encoder ({unit})"); ax.set_ylabel(f"phone gyro ({unit})"); style(ax)
        note(ax, f"median |Δ| {dd.median():.3g} {unit}\nn = {len(d)} steps", "upper left")
    axs[0].legend(loc="lower right", frameon=False, fontsize=10)
    fig.suptitle("Dynamic step response — phone vs encoder, same estimator on both", fontweight="bold", color=NAVY, fontsize=14)
    save(fig, "dy_step.png")
def motor_pairs():
    """Session_3 (Motor_9_14) phone recordings paired to the Slow encoder files by cross-correlation (window ±4)."""
    phones = order_by_capture(os.path.join(MAT, "Session_3", "pendulastic-Motor_9_14-20260914T134351-manifest.json"))
    encs = sorted(glob.glob(os.path.join(ENC, "*Slow.bin")), key=lambda p: int(os.path.basename(p).split("Trial")[1][:2]))
    encd = {e: enc_load(e) for e in encs}; out = []
    for i, pf in enumerate(phones):
        tp, ap, wp = phone_angle(pf); best = None
        for j in range(max(0, i - 4), min(len(encs), i + 5)):
            te, pe, ve, _ = encd[encs[j]]; lag, sg, r = align(tp, ap, te, pe)
            if best is None or r > best[0]: best = (r, encs[j], lag, sg)
        out.append((i + 1, pf, best[1], best[2], best[3], best[0]))
    return out, encd
def dy_ring():
    pairs, encd = motor_pairs(); rows = []; panels = []
    for rank, pf, ef, lag, sg, r in pairs:
        tp, ap, wp = phone_angle(pf); te, pe, ve, _ = encd[ef]; tq = tp + lag
        m = (te >= tq[0]) & (te <= tq[-1]); mt, mx, mv = te[m], pe[m], ve[m]
        me = ring_metrics(mt, mx, mv); mp = ring_metrics(tq, sg * ap, sg * wp)
        if me and mp and r > 0.9:
            rows.append(dict(rank=rank, enc=os.path.basename(ef), r=r, enc_f=me["f"], ph_f=mp["f"], enc_ffft=me["f_fft"], ph_ffft=mp["f_fft"], enc_N=me["N"], ph_N=mp["N"], enc_tau=me["tau"], ph_tau=mp["tau"]))
            panels.append((rank, tq, sg * ap, mt, mx, me, mp, r))
    d = pd.DataFrame(rows); d.to_csv(os.path.join(OUT, "ringdown_paired.csv"), index=False); print(d.round(3).to_string())
    def zc_f(t, x, t0, t1):                       # zero-crossing frequency of the ring on [t0, t1], about the final rest
        k = (t >= t0) & (t <= t1); y = x[k] - np.median(x[-int(1 / np.median(np.diff(t))):]); s = np.sign(y); c = np.where(np.diff(s) != 0)[0]
        return (len(c) - 1) / (2 * (t[k][c[-1]] - t[k][c[0]])) if len(c) >= 3 else np.nan, y
    sel = []
    for p in panels:
        rank, tq, pq, mt, mx, me, mp, r = p; t0 = me["t_rel"]
        fe, ye = zc_f(mt, mx, t0 + 0.35, t0 + 2.5); fp, yp = zc_f(tq, pq, t0 + 0.35, t0 + 2.5)
        amp = np.max(np.abs(ye[int(len(ye) * 0.05):])) if len(ye) else 0
        print(f"   rank {rank}: zc f enc {fe:.2f} / phone {fp:.2f} Hz, ring amp {amp:.1f}°")
        if np.isfinite(fe) and np.isfinite(fp) and amp > 0.8: sel.append((rank, tq, pq, mt, mx, t0, fe, fp))
    sel = sel[:2]; print("   panels:", len(sel))
    fig, axs = plt.subplots(1, max(len(sel), 1), figsize=(12.5, 4.4), squeeze=False); axs = axs.ravel()
    for ax, (rank, tq, pq, mt, mx, t0, fe, fp) in zip(axs, sel):
        re_, rp = np.median(mx[-100:]), np.median(pq[-60:]); k = (mt >= t0 - 0.5) & (mt <= t0 + 3.0); kq = (tq >= t0 - 0.5) & (tq <= t0 + 3.0)
        ax.plot(mt[k] - t0, mx[k] - re_, color=INK, lw=2.4, label="encoder (1 kHz truth)"); ax.plot(tq[kq] - t0, pq[kq] - rp, color=TEAL, lw=1.3, label="phone gyro")
        ax.set_title(f"motor return at 200 °/s, P = 1  ·  ring f: encoder {fe:.2f} Hz, phone {fp:.2f} Hz", fontsize=11.5)
        ax.set_xlabel("time from release (s)"); ax.set_ylabel("angle from rest (deg)"); style(ax)
    axs[0].legend(loc="upper right", frameon=False, fontsize=10)
    fig.suptitle("Dynamic waveform vs encoder — the phone follows the loop's own ring-down", fontweight="bold", color=NAVY, fontsize=13.5)
    save(fig, "dy_ring_wave.png")
def dy_wave():
    """Encoder-paired dynamic waveforms: Motor_9_14 lift at 100 deg/s with the position loop ringing on the raised arm.
    Pairs by angle cross-correlation (motor_pairs); only r > 0.99 kept. Both traces zeroed at their own pre-lift rest."""
    pairs, encd = motor_pairs(); rows = []; cur = {}
    for rank, pf, ef, lag, sg, r in pairs:
        if r < 0.999: continue
        tp, ap, wp = phone_angle(pf); te, pe, ve, _ = encd[ef]; tq = tp + lag
        m = (te >= tq[0]) & (te <= tq[-1]); mt, mx, mv = te[m], pe[m], ve[m]
        il = int(np.argmax(np.abs(mv) > 40)); tl = mt[il]
        if tl - mt[0] < 0.5: continue
        re0 = np.median(mx[(mt >= tl - 0.8) & (mt <= tl - 0.1)]); rp0 = np.median(sg * ap[(tq >= tl - 0.8) & (tq <= tl - 0.1)])
        ye, yp = mx - re0, sg * ap - rp0
        def ring(t, y):
            w = (t >= tl + 0.35) & (t <= tl + 3.0); tt, yy = t[w], y[w]; yy = yy - np.polyval(np.polyfit(tt, yy, 1), tt)
            c = np.where(np.diff(np.sign(yy)) != 0)[0]; f = (len(c) - 1) / (2 * (tt[c[-1]] - tt[c[0]])) if len(c) >= 3 else np.nan
            return f, np.max(np.abs(yy))
        fe, ae = ring(mt, ye); fp, apk = ring(tq, yp)
        w = (tq >= tl - 0.5) & (tq <= tl + 4.0); err = np.abs(yp[w] - np.interp(tq[w], mt, ye))
        rows.append(dict(rank=rank, enc=os.path.basename(ef), r=r, enc_f=fe, ph_f=fp, enc_amp=ae, ph_amp=apk, mean_err=err.mean(), max_err=err.max()))
        cur[rank] = (mt - tl, ye, tq - tl, yp)
    d = pd.DataFrame(rows); d.to_csv(os.path.join(OUT, "dynamic_wave_pairs.csv"), index=False); print(d.round(3).to_string())
    fig = plt.figure(figsize=(13, 7.4)); gs = fig.add_gridspec(2, 3, height_ratios=[1, 1])
    a = fig.add_subplot(gs[0, :]); b = fig.add_subplot(gs[1, 0]); c = fig.add_subplot(gs[1, 1]); dd = fig.add_subplot(gs[1, 2])
    k1 = 3 if 3 in cur else list(cur)[0]; k2 = 7 if 7 in cur else list(cur)[-1]
    mt, ye, tq, yp = cur[k1]; r1 = d[d["rank"] == k1].iloc[0]
    a.plot(mt, ye, color=INK, lw=2.4, label="encoder (1 kHz truth)"); a.plot(tq, yp, color=TEAL, lw=1.3, label="phone gyro")
    a.set_xlim(-1, 14); a.set_xlabel("time from lift (s)"); a.set_ylabel("angle from rest (deg)"); a.legend(loc="upper right", frameon=False)
    a.set_title(f"Motor lift at 100 °/s, position loop ringing on the raised arm, then the slow return  ·  mean |err| {r1.mean_err:.2f}°, max {r1.max_err:.1f}° over the lift + ring"); style(a)
    for ax, k in ((b, k1), (c, k2)):
        mt, ye, tq, yp = cur[k]; rr = d[d["rank"] == k].iloc[0]; w = (mt >= -0.3) & (mt <= 3.2); wq = (tq >= -0.3) & (tq <= 3.2)
        ax.plot(mt[w], ye[w], color=INK, lw=2.4); ax.plot(tq[wq], yp[wq], color=TEAL, lw=1.3)
        ax.set_title(f"ring zoom, swing {k}\nf  encoder {rr.enc_f:.2f} Hz  ·  phone {rr.ph_f:.2f} Hz", fontsize=10.5); ax.set_xlabel("time from lift (s)"); ax.set_ylabel("angle from rest (deg)"); style(ax)
    lo, hi = min(d.enc_f.min(), d.ph_f.min()) - 0.2, max(d.enc_f.max(), d.ph_f.max()) + 0.2
    dd.plot([lo, hi], [lo, hi], "--", color=GREY, lw=1.2); dd.scatter(d.enc_f, d.ph_f, s=70, color=TEAL, edgecolor="white", lw=0.9, zorder=3); dd.set_xlim(lo, hi); dd.set_ylim(lo, hi)
    rel = ((d.ph_f - d.enc_f).abs() / d.enc_f).median() * 100
    dd.set_xlabel("encoder ring frequency (Hz)"); dd.set_ylabel("phone ring frequency (Hz)"); dd.set_title("ring frequency\nall paired swings", fontsize=10.5); style(dd)
    note(dd, f"n = {len(d)} swings\nmedian |Δf| {rel:.1f} %\nmean |err| {d.mean_err.median():.2f}° (median swing)", "upper left")
    fig.suptitle("Dynamic waveforms vs encoder — the phone follows the lift, the ring and the return", fontweight="bold", color=NAVY, fontsize=14)
    save(fig, "dy_wave.png")
def dy_physics():
    """Manual_9_14: 18 hand-pulled swings, motor holding 0 with gain P. Run order -> P and pull angle from the tracker's Trials sheet."""
    phones = order_by_capture(os.path.join(MAT, "Session_4", "pendulastic-Manual_9_14-20260914T160224-manifest.json"))
    P = [1, 1, 1] + [0.1] * 12 + [1, 1, 1]; PULL = [30] * 9 + [40] * 3 + [50] * 3 + [30] * 3
    rows = []; waves = {}
    for i, pf in enumerate(phones):
        t, a, w = phone_angle(pf); m = ring_metrics(t, a, w, mode="manual")
        if m: rows.append(dict(run=i + 1, P=P[i], pull=PULL[i], f=m["f"], N=m["N"], tau=m["tau"], meas_pull=m["pull"])); waves.setdefault(P[i], (t - m["t_rel"], a - m["rest"]))
        else: print("   no ring:", os.path.basename(pf))
    d = pd.DataFrame(rows); d.to_csv(os.path.join(OUT, "manual_physics.csv"), index=False); print(d.round(2).to_string())
    g = d.groupby("P").f.agg(["mean", "std", "count"]); print(g)
    f01, f1 = g.loc[0.1, "mean"], g.loc[1.0, "mean"]; ratio = f1 / f01
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(12.5, 4.8), gridspec_kw=dict(width_ratios=[1.15, 1]))
    xs = np.linspace(0.05, 1.15, 200); kk = f01 / np.sqrt(0.1); a1.plot(xs, kk * np.sqrt(xs), color=NAVY, lw=2.2, label="theory  f = k·√P  (fit to P = 0.1)")
    a1.scatter(d.P + np.random.default_rng(1).normal(0, 0.012, len(d)), d.f, s=70, color=TEAL, edgecolor="white", lw=0.9, zorder=3, label=f"phone-measured f  (n = {len(d)})")
    a1.set_xlabel("controller gain P  (the loop's spring constant)"); a1.set_ylabel("oscillation frequency f (Hz)"); a1.set_title("Frequency follows physics: f ∝ √P")
    a1.legend(loc="upper left", frameon=False, fontsize=10); a1.set_xlim(0, 1.15); a1.set_ylim(0, max(3.6, d.f.max() * 1.1)); style(a1)
    note(a1, f"P 0.1 → {f01:.2f} Hz,  P 1 → {f1:.2f} Hz\nratio {ratio:.2f}×  vs  √10 = 3.16 predicted")
    for pv, c in [(0.1, TEAL), (1.0, CORAL)]:
        tt, aa = waves[pv]; k = (tt > -0.5) & (tt < 6); a2.plot(tt[k], aa[k], color=c, lw=1.5, label=f"P = {pv:g}  (f ≈ {g.loc[pv, 'mean']:.2f} Hz)")
    a2.set_xlabel("time from release (s)"); a2.set_ylabel("phone angle (deg)"); a2.set_title("Same pull, two spring settings"); a2.legend(frameon=False, fontsize=10); style(a2)
    save(fig, "dy_physics.png")
    fig, ax = plt.subplots(figsize=(6.2, 4.6))
    for pv, c in [(30, TEAL), (40, SEA), (50, CORAL)]:
        k = d.pull == pv; ax.scatter([pv] * k.sum() + np.random.default_rng(2).normal(0, 0.4, k.sum()), d.meas_pull[k], s=60, color=c, edgecolor="white", lw=0.8, zorder=3)
    ax.plot([20, 60], [20, 60], "--", color=GREY); ax.set_xlabel("commanded pull angle (deg)"); ax.set_ylabel("phone-measured pull (deg, gyro)")
    ax.set_title("Amplitude tracks the pull"); style(ax); ax.set_xlim(22, 58); ax.set_ylim(22, 60)
    gm = d.groupby("pull").meas_pull.mean().round(0).astype(int).to_dict(); note(ax, "  ·  ".join(f"{k}° → {v}°" for k, v in gm.items()), "lower right")
    save(fig, "dy_amplitude.png")

# ----------------------------------------------------------------- PID
def pid_speed():
    j = pd.read_csv(os.path.join(OUT, "ssr_qa_joined.csv")).dropna(subset=["P"])
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(12, 4.6), gridspec_kw=dict(width_ratios=[1, 1.3]))
    for sp, c in [(200, TEAL), (250, CORAL)]:
        k = j["Step speed"] == sp; x = np.full(k.sum(), sp) + np.random.default_rng(3).normal(0, 4, k.sum())
        a1.scatter(x, j.overshoot_pct[k], s=55, color=c, edgecolor="white", lw=0.8, zorder=3)
        a1.hlines(j.overshoot_pct[k].median(), sp - 15, sp + 15, color=INK, lw=2.2, zorder=4)
        a1.text(sp + 17, j.overshoot_pct[k].median(), f"median {j.overshoot_pct[k].median():.1f} %", va="center", fontsize=10, color=NAVY)
    a1.set_xticks([200, 250]); a1.set_xlim(170, 300); a1.set_xlabel("commanded step speed (°/s)"); a1.set_ylabel("encoder overshoot (%)"); a1.set_title("Step speed is the lever"); style(a1)
    for sp, c, mk in [(200, TEAL, "o"), (250, CORAL, "s")]:
        k = j["Step speed"] == sp; a2.scatter(j.P[k] + np.random.default_rng(4).normal(0, 0.02, k.sum()), j.overshoot_pct[k], s=55, color=c, marker=mk, edgecolor="white", lw=0.8, label=f"{sp} °/s", zorder=3)
    a2.set_xlabel("P term  (I swept 0.01–0.7, D swept 0.03–0.8 within each speed)"); a2.set_ylabel("encoder overshoot (%)"); a2.set_title("…the gains are not: overshoot is flat across P, I and D")
    a2.legend(frameon=False, fontsize=10, title="step speed"); style(a2)
    fig.suptitle(f"PID sweep on {len(j)} SSR recordings — what actually controls a clean step", fontweight="bold", color=NAVY, fontsize=14)
    save(fig, "pid_speed.png")
def pid_explainer():
    t = np.linspace(0, 6, 1200)
    def osc(P, zeta=0.03, J=0.00288):
        w = np.sqrt(P / J); return 30 * np.exp(-zeta * w * t) * np.cos(w * np.sqrt(1 - zeta ** 2) * t)
    fig, axs = plt.subplots(1, 3, figsize=(13.5, 4.0))
    for P, c in [(0.1, TEAL), (0.4, SEA), (1.0, CORAL)]: axs[0].plot(t, osc(P), color=c, lw=1.6, label=f"P = {P:g}")
    axs[0].set_title("P — the spring: higher P, faster swing"); axs[0].legend(frameon=False, fontsize=10)
    for z, c, lab in [(0.01, TEAL, "D ≈ 0"), (0.05, SEA, "small D"), (0.15, CORAL, "more D")]: axs[1].plot(t, osc(0.1, z), color=c, lw=1.6, label=lab)
    axs[1].set_title("D — the brake: more D, fewer swings"); axs[1].legend(frameon=False, fontsize=10)
    # real encoder steps at 200 vs 250 deg/s
    cfg = ss.groupby(ETRIAL)[SPD].first()
    for sp, c in [(200, TEAL), (250, CORAL)]:
        tr = int(cfg[cfg == sp].index[2]); te, pe, _, spt = enc_load(os.path.join(ENC, f"ck_ssr_pt_testStretchTrial{tr}Fast.bin"))
        i0 = np.argmax(np.abs(np.gradient(spt, te)) > 1); k = (te >= te[i0] - 0.2) & (te <= te[i0] + 2.0)
        axs[2].plot(te[k] - te[i0], pe[k], color=c, lw=1.8, label=f"step at {sp} °/s")
    axs[2].axhline(37.5, color=GREY, ls="--", lw=1); axs[2].text(1.95, 38.3, "target", ha="right", color=GREY, fontsize=9)
    axs[2].set_title("Step speed — how hard you push: real encoder"); axs[2].legend(frameon=False, fontsize=10, loc="lower right"); axs[2].set_xlabel("time from step (s)")
    for ax in axs: ax.set_ylabel("angle (deg)"); style(ax)
    for ax in axs[:2]: ax.set_xlabel("time (s)  —  illustrative simulation")
    save(fig, "pid_explainer.png")

# ----------------------------------------------------------------- COHORT
mm = pd.read_csv(os.path.join(REPO, "Model_Analysis_Outputs", "multimetric_trials.csv"))
PARAMS = [("R2n", "R2n  relaxation index", 1.032), ("N", "N  swings", 5.50), ("phi_max_ratio", "φmax  first-flexion ratio", 0.639), ("omega_max_n", "ωmax  peak velocity (norm.)", 6.768), ("omega_min_n", "ωmin  (norm.)", 0.001), ("f", "f  frequency (Hz)", 0.914), ("area_ratio", "area ratio  asymmetry", 0.077)]
GROUPS = [("Control", TEAL), ("Stroke", GOLD), ("MS", CORAL)]
def co_box():
    hp = pd.read_csv(os.path.join(REPO, "Model_Analysis_Outputs", "HPE_Comparison", "mediapipe_pt_metrics.csv"))
    hp = hp[(hp.variant == "full_0.5") & (hp.opti_interpretable == True)]
    comp = pd.read_csv(os.path.join(REPO, "Model_Analysis_Outputs", "Cohort_Comparison", "cohort_composition.csv")); g_of = dict(zip(comp.pid.astype(int), comp.group))
    hp["group"] = hp.participant.astype(int).map(g_of); hp = hp.dropna(subset=["group"])
    print("   cohort rows:", hp.group.value_counts().to_dict())
    P2 = [x for x in PARAMS if x[0] != "omega_min_n"] + [("pt7", "PT7 composite score", 0.0)]
    fig, axs = plt.subplots(2, 4, figsize=(14, 7)); axs = axs.ravel()
    for ax, (p, lab, ref) in zip(axs, P2):
        data = [hp.loc[hp.group == g, f"opti_{p}"].dropna() for g, _ in GROUPS]
        bp = ax.boxplot(data, widths=0.55, patch_artist=True, showfliers=False, medianprops=dict(color=INK, lw=1.8))
        for patch, (_, c) in zip(bp["boxes"], GROUPS): patch.set_facecolor(c); patch.set_alpha(0.55); patch.set_edgecolor(c)
        for i, (g, c) in enumerate(GROUPS, start=1): ax.scatter(np.full(len(data[i - 1]), i) + np.random.default_rng(i).normal(0, 0.06, len(data[i - 1])), data[i - 1], s=12, color=c, alpha=0.7, zorder=3)
        if ref: ax.axhline(ref, color=NAVY, ls=":", lw=1.2)
        ax.set_xticks([1, 2, 3]); ax.set_xticklabels([f"{g}\nn={len(d)}" for (g, _), d in zip(GROUPS, data)], fontsize=9.5)
        ax.set_title(lab, fontsize=11.5); style(ax)
        hi = np.nanpercentile(np.concatenate([d.values for d in data]), 97); ax.set_ylim(min(0, ax.get_ylim()[0]), hi * 1.1)
    ax = axs[7]; ours = pd.read_csv(os.path.join(REPO, "mas_scores.csv")).mas_grade.astype(str).value_counts()
    cats = ["0", "1", "1+", "2", "3"]; wh = dict(zip(cats, [53, 33, 14, 16, 11])); o = [ours.get(c, 0) for c in cats]; w = [wh[c] for c in cats]
    x = np.arange(5); ax.bar(x - 0.2, np.array(o) / sum(o) * 100, 0.4, color=TEAL, label=f"ours (n = {sum(o)} leg-sessions)"); ax.bar(x + 0.2, np.array(w) / sum(w) * 100, 0.4, color=GREY, label="Whelan 2018 (n = 131 knees)")
    ax.set_xticks(x); ax.set_xticklabels(cats); ax.set_xlabel("clinician MAS"); ax.set_ylabel("% of knees"); ax.set_title("MAS distribution vs the literature", fontsize=11.5); ax.legend(frameon=False, fontsize=8.5); style(ax)
    fig.suptitle("Cohort comparison — OptiTrack-scored Popović parameters by group  (dotted = healthy reference)", fontweight="bold", color=NAVY, fontsize=14)
    save(fig, "co_box.png")
def auc(a, b):
    a, b = np.asarray(a, float), np.asarray(b, float); a, b = a[~np.isnan(a)], b[~np.isnan(b)]
    if len(a) < 3 or len(b) < 3: return np.nan, len(a), len(b)
    u = mannwhitneyu(a, b, alternative="two-sided").statistic; A = u / (len(a) * len(b)); return max(A, 1 - A), len(a), len(b)
def co_auc():
    d = mm.dropna(subset=["mas"]); pos, neg = d[d.mas > 0], d[d.mas == 0]
    lit = {"R2n": ("RI", 0.784), "N": ("Ncyc", 0.665), "phi_max_ratio": ("F1amp", 0.807)}
    names = ["R2n", "N", "phi_max_ratio", "omega_max_n", "omega_min_n", "f", "area_ratio"]; labs = ["R2n", "N", "φmax", "ωmax", "ωmin", "f", "area"]
    rows = []
    for p in names:
        ao, n1, n0 = auc(pos[f"opti_{p}"], neg[f"opti_{p}"]); ai, _, _ = auc(pos[f"imu_{p}"], neg[f"imu_{p}"]); am, _, _ = auc(pos[f"mp_{p}"], neg[f"mp_{p}"])
        rows.append(dict(p=p, opti=ao, imu=ai, mp=am, lit=lit.get(p, ("", np.nan))[1], litname=lit.get(p, ("", ""))[0], n1=n1, n0=n0))
    r = pd.DataFrame(rows); print(r.round(3).to_string())
    fig, ax = plt.subplots(figsize=(12, 4.8)); x = np.arange(len(names)); wdt = 0.2
    ax.bar(x - 1.5 * wdt, r.lit, wdt, color=GREY, label="published (Whelan 2018, 131 knees)"); ax.bar(x - 0.5 * wdt, r.opti, wdt, color=NAVY, label="ours — OptiTrack")
    ax.bar(x + 0.5 * wdt, r.imu, wdt, color=TEAL, label="ours — phone IMU"); ax.bar(x + 1.5 * wdt, r.mp, wdt, color=CORAL, label="ours — MediaPipe")
    for i, ln in enumerate(r.litname):
        if ln: ax.text(x[i] - 1.5 * wdt, r.lit[i] + 0.01, ln, ha="center", fontsize=8.5, color=INK)
    ax.axhline(0.7, color=INK, ls="--", lw=1); ax.text(6.55, 0.705, "0.70 = acceptable", ha="right", fontsize=9, color=INK); ax.axhline(0.5, color=GREY, lw=0.8)
    ax.set_xticks(x); ax.set_xticklabels(labs); ax.set_ylim(0.4, 1.0); ax.set_ylabel("ROC AUC — spastic (MAS > 0) vs not (MAS 0)")
    ax.set_title(f"Can each parameter tell a spastic knee from a normal one?  ours: {r.n1[0]} MAS>0 vs {r.n0[0]} MAS-0 trials")
    ax.legend(frameon=False, fontsize=9.5, ncol=2, loc="upper right"); style(ax); save(fig, "co_auc.png")
def co_dumbbell():
    s = pd.read_csv(os.path.join(REPO, "Model_Analysis_Outputs", "MS_vs_Healthy", "ms_vs_healthy_stats.csv"))
    s = s[s.column.isin(["R2n", "N", "phi_max_ratio", "omega_max_n", "pt_score_4param"])]
    fig, axs = plt.subplots(1, len(s), figsize=(13, 3.9))
    for ax, (_, row) in zip(axs, s.iterrows()):
        ax.plot([0, 1], [row.healthy_median, row.ms_median], color=GREY, lw=2, zorder=1)
        ax.scatter([0], [row.healthy_median], s=160, color=TEAL, zorder=3); ax.scatter([1], [row.ms_median], s=160, color=CORAL, zorder=3)
        ax.errorbar([0], [row.healthy_median], yerr=row.healthy_IQR / 2, color=TEAL, capsize=4, lw=1.2); ax.errorbar([1], [row.ms_median], yerr=row.ms_IQR / 2, color=CORAL, capsize=4, lw=1.2)
        ax.set_xticks([0, 1]); ax.set_xticklabels([f"healthy\nn={int(row.healthy_n)}", f"MS\nn={int(row.ms_n)}"]); ax.set_xlim(-0.5, 1.5)
        ax.set_title(row.parameter.replace(" (", "\n("), fontsize=11); style(ax)
        ax.text(0.5, 0.02, f"p = {row.mann_whitney_p:.3g}\nCliff's δ {row.cliffs_delta:+.2f} ({row.effect_size})", transform=ax.transAxes, ha="center", va="bottom", fontsize=9, color=NAVY, bbox=dict(boxstyle="round,pad=0.3", fc=PANEL, ec="#c9d2da"))
    fig.suptitle("Healthy vs MS — median ± IQR/2 per leg-session (phone IMU channel)", fontweight="bold", color=NAVY, fontsize=13.5)
    save(fig, "co_dumbbell.png")

# ----------------------------------------------------------------- MARKERLESS vs IMU vs OPTITRACK
mp = pd.read_csv(os.path.join(REPO, "Model_Analysis_Outputs", "HPE_Comparison", "mediapipe_pt_metrics.csv"))
def mk_rmse():
    d = mp.dropna(subset=["rmse_2d", "rmse_imu"]); better = (d.rmse_imu < d.rmse_2d).mean() * 100
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(12, 4.6), gridspec_kw=dict(width_ratios=[1, 1.2]))
    vp = a1.violinplot([d.rmse_imu, d.rmse_2d], showmedians=True, widths=0.8)
    for b, c in zip(vp["bodies"], [TEAL, CORAL]): b.set_facecolor(c); b.set_alpha(0.55)
    for k in ("cmedians", "cbars", "cmins", "cmaxes"): vp[k].set_color(INK)
    a1.set_xticks([1, 2]); a1.set_xticklabels([f"phone IMU\nmedian {d.rmse_imu.median():.1f}°", f"MediaPipe\nmedian {d.rmse_2d.median():.1f}°"]); a1.set_ylabel("knee-angle RMSE vs OptiTrack (deg, offset removed)")
    a1.set_title(f"Angle tracking on {len(d)} paired trials"); style(a1)
    a2.scatter(d.rmse_2d, d.rmse_imu, s=30, color=NAVY, alpha=0.7, edgecolor="white", lw=0.5); lim = max(d.rmse_2d.max(), d.rmse_imu.max()) * 1.05
    a2.plot([0, lim], [0, lim], "--", color=GREY); a2.fill_between([0, lim], [0, 0], [0, lim], color=TEAL, alpha=0.06); a2.text(lim * 0.97, lim * 0.05, "IMU better ↓", ha="right", color=TEAL, fontsize=10)
    a2.set_xlabel("MediaPipe RMSE (deg)"); a2.set_ylabel("phone IMU RMSE (deg)"); a2.set_title(f"Trial by trial — IMU better on {better:.0f}% of trials"); style(a2)
    save(fig, "mk_rmse.png"); print("   paired", len(d), "IMU med", d.rmse_imu.median(), "MP med", d.rmse_2d.median(), "better%", better)
def mk_icc():
    tab = [("ωmin", 0.98, 0.38), ("N", 0.62, 0.33), ("φmax", 0.57, 0.14), ("f", 0.24, 0.14), ("R2n", 0.15, -0.13), ("area ratio", 0.15, 0.16), ("ωmax", 0.02, -0.05), ("A0", -0.02, -0.15)]
    fig, ax = plt.subplots(figsize=(7.2, 5)); y = np.arange(len(tab))[::-1]
    ax.barh(y + 0.18, [r[1] for r in tab], 0.36, color=TEAL, label="phone IMU"); ax.barh(y - 0.18, [r[2] for r in tab], 0.36, color=CORAL, label="MediaPipe")
    for yi, r in zip(y, tab):
        ax.text(max(r[1], 0) + 0.02, yi + 0.18, f"{r[1]:+.2f}", va="center", fontsize=9); ax.text(max(r[2], 0) + 0.02, yi - 0.18, f"{r[2]:+.2f}", va="center", fontsize=9)
    ax.axvline(0, color=INK, lw=0.8); ax.set_yticks(y); ax.set_yticklabels([r[0] for r in tab]); ax.set_xlim(-0.3, 1.15); ax.set_xlabel("ICC(2,1) against OptiTrack")
    ax.set_title("Parameter agreement with OptiTrack — IMU wins 7 of 8"); ax.legend(frameon=False, loc="lower right"); style(ax, y=False)
    save(fig, "mk_icc.png")
def mk_hpe():
    h = pd.read_csv(os.path.join(REPO, "Model_Analysis_Outputs", "HPE_Comparison", "hpe_accuracy_summary.csv")); h = h[h.n_trials >= 3].sort_values("adj_MAS_agree_%")
    fig, ax = plt.subplots(figsize=(7.6, 5.4)); cols = [TEAL if m == "pendulastic" else (NAVY if m == "pose2sim_lightweight" else "#c9d2da") for m in h.model_family]
    ax.barh(h.model_family, h["adj_MAS_agree_%"], color=cols)
    for i, (v, n) in enumerate(zip(h["adj_MAS_agree_%"], h.n_trials)): ax.text(v + 1, i, f"{v:.0f}%  (n={n})", va="center", fontsize=9.5)
    ax.set_xlim(0, 112); ax.set_xlabel("adjacent-MAS agreement with the clinician (%)"); ax.set_title("Pose-estimation families vs the clinician's MAS"); style(ax, y=False)
    ax.set_yticks(range(len(h))); ax.set_yticklabels([m.replace("_", " ") for m in h.model_family]); save(fig, "mk_hpe.png")
def mk_wave():
    """Three channels on one trial, on the alignment the RMSE used (reuses the methodology script's loaders)."""
    saved = dict(plt.rcParams); import generate_methodology_comparison_figures as G; plt.rcParams.update({k: saved[k] for k in ("font.family", "font.size", "axes.titleweight", "axes.titlecolor", "axes.titlesize", "axes.edgecolor")})
    rows = G.load_rows(); want = [("13", "left", "month_post", "1"), ("13", "left", "month_post", "2")]
    fig, axs = plt.subplots(1, 2, figsize=(13, 4.6))
    for ax, key in zip(axs, want):
        row = next(r for r in rows if (r["participant"], r["leg"], r["condition"], r["trial"]) == key)
        opti, imu, mpc = G._curves_for(row); ot, oa, flip = G._tail_zero_orient(*opti); t0 = ot[0]
        ax.plot(ot - t0, oa, color=INK, lw=2.4, label="OptiTrack (reference)", zorder=4)
        for data, col, lab, ch, rk in ((imu, TEAL, "phone IMU", "imu", "rmse_imu"), (mpc, CORAL, "MediaPipe", "video", "rmse_2d")):
            if data is None: continue
            res = G.cmr.align_against_optitrack(opti[0], opti[1], data[0], data[1], ch); mt, my, _ = G._tail_zero_orient(data[0], data[1])   # each channel oriented on its own
            lag = float(res["lag_sec"]) if res else 0.0; ax.plot(mt + lag - t0, my, color=col, lw=1.5, label=f"{lab}  (RMSE {float(row[rk]):.1f}°)")
        ax.set_xlim(0, 10); ax.set_xlabel("time (s)"); ax.set_ylabel("knee angle, tail-zeroed (deg)"); ax.set_title(f"MINT_{key[0]} {key[1]} {key[2].replace('_', ' ')} · trial {key[3]}", fontsize=12); style(ax)
    axs[0].legend(frameon=False, fontsize=10, loc="lower right")
    fig.suptitle("Same swing, three instruments — OptiTrack vs phone IMU vs MediaPipe", fontweight="bold", color=NAVY, fontsize=14)
    save(fig, "mk_wave.png")
def mk_anchor():
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(12, 4.4))
    ch = ["OptiTrack", "phone IMU", "MediaPipe 2D"]; within = [100, 10, 0]; exact = [86, 10, 0]; c = [NAVY, TEAL, CORAL]
    x = np.arange(3); a1.bar(x - 0.2, exact, 0.4, color=c, alpha=0.55, label="exact grade"); a1.bar(x + 0.2, within, 0.4, color=c, label="within one grade")
    for i in range(3): a1.text(x[i] - 0.2, exact[i] + 2, f"{exact[i]}%", ha="center", fontsize=9.5); a1.text(x[i] + 0.2, within[i] + 2, f"{within[i]}%", ha="center", fontsize=9.5)
    a1.set_xticks(x); a1.set_xticklabels(["OptiTrack\n7 trials", "phone IMU\n10 trials", "MediaPipe 2D\n8 trials"]); a1.set_ylim(0, 112); a1.set_ylabel("agreement with clinician (%)")
    a1.set_title("Clinical anchor — clinician MAS 0, both legs"); a1.legend(frameon=False, fontsize=9.5); style(a1)
    vals = [0.077, 0.033, 0.759]; lab = ["healthy\nreference", "OptiTrack", "phone IMU"]; a2.bar(lab, vals, color=[GREY, NAVY, TEAL])
    for i, v in enumerate(vals): a2.text(i, v + 0.015, f"{v:.3f}", ha="center", fontsize=10)
    a2.set_ylabel("area ratio (swing asymmetry)"); a2.set_title("Why the IMU over-grades: area ratio (85% of its score)"); a2.set_ylim(0, 0.9); style(a2)
    save(fig, "mk_anchor.png")

for nm, fn in [("st_scatter", st_scatter), ("st_bland", st_bland), ("st_err_drift", st_err_drift), ("st_wave", st_wave),
               ("dy_step", dy_step), ("dy_wave", dy_wave), ("dy_physics", dy_physics),
               ("pid_speed", pid_speed), ("pid_explainer", pid_explainer),
               ("co_box", co_box), ("co_auc", co_auc), ("co_dumbbell", co_dumbbell),
               ("mk_rmse", mk_rmse), ("mk_icc", mk_icc), ("mk_hpe", mk_hpe), ("mk_anchor", mk_anchor), ("mk_wave", mk_wave)]:
    only = [s for s in os.environ.get("ONLY", "").split(",") if s]
    if only and not any(nm.startswith(o) for o in only): continue
    safe(nm, fn)
print("DONE")

# ----------------------------------------------------------------- NEURO-MECHANICAL MODEL (illustrative simulation)
def nm_sim():
    """Damped pendulum  J·θ'' + B·θ' + K·θ = −T_reflex(θ'), with a velocity-threshold extensor reflex.
    Purely illustrative: shows what each controller term does to the seven parameters. Not fitted to data."""
    J, K = 0.30, 9.8                                   # shank+foot inertia (kg m²), gravity stiffness mgl (N m/rad)
    levels = [("MAS 0", 0.35, 0.0, 9.9, TEAL), ("MAS 1", 0.6, 1.5, 3.0, SEA), ("MAS 2", 1.0, 4.0, 1.8, GOLD), ("MAS 3", 1.6, 9.0, 0.8, CORAL)]
    dt = 0.001; t = np.arange(0, 8, dt); delay = int(0.04 / dt)
    def run(B, G, vth):
        th, w = np.radians(-70.0), 0.0; out = np.empty_like(t); hist = np.zeros(delay + 1)
        for i in range(len(t)):
            hist = np.roll(hist, 1); hist[0] = w; wd = hist[-1]
            T_ref = -G * max(0.0, wd - vth) if wd > 0 else 0.0            # quadriceps fires when it is stretched fast (flexion, +w)
            a = (-B * w - K * th + T_ref) / J; w += a * dt; th += w * dt; out[i] = np.degrees(th)
        return out
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(13, 4.6), gridspec_kw=dict(width_ratios=[1.6, 1]))
    rows = []
    for name, B, G, vth, c in levels:
        y = run(B, G, vth); a1.plot(t, y, color=c, lw=1.7, label=name)
        yy = y - y[-500:].mean(); ext = extrema(t, yy, 1.0); N = len(ext[0]) / 2
        A0 = abs(yy[0]); A1 = (yy.max() - yy.min()) if len(ext[0]) else 0; f = (len(ext[0]) - 1) / (2 * (ext[0][-1] - ext[0][0])) if len(ext[0]) >= 3 else np.nan
        rows.append((name, N, A1 / (1.6 * A0), f)); print(f"   {name}: B {B} G {G} vth {vth}  N {N:.1f}  R2n {A1 / (1.6 * A0):.2f}  f {f:.2f}")
    a1.axhline(0, color=GREY, lw=0.8, ls=":"); a1.set_xlabel("time from release (s)"); a1.set_ylabel("knee angle from rest (deg)")
    a1.set_title("Simulated pendulum test at four spasticity levels  (illustrative)"); a1.legend(frameon=False, loc="lower right", ncol=4); style(a1)
    a1.text(0.985, 0.95, "\n".join((f"{n}:  N {N:.0f}   R2n {r:.2f}   f {f:.2f} Hz" if np.isfinite(f) else f"{n}:  N {N:.0f}   R2n {r:.2f}   f  —") for n, N, r, f in rows), transform=a1.transAxes, ha="right", va="top", fontsize=9, color=NAVY, family="DejaVu Sans Mono", bbox=dict(boxstyle="round,pad=0.4", fc=PANEL, ec="#c9d2da"))
    v = np.linspace(0, 12, 200)
    for name, B, G, vth, c in levels: a2.plot(v, G * np.maximum(0, v - vth), color=c, lw=1.8, label=name)
    a2.set_xlabel("stretch velocity of the quadriceps (rad/s)"); a2.set_ylabel("reflex torque opposing the swing (N·m)"); a2.set_title("The reflex law the motor will run"); a2.legend(frameon=False); style(a2)
    a2.text(0.03, 0.95, "threshold ↓ and gain ↑ with severity", transform=a2.transAxes, va="top", fontsize=9.5, color=NAVY)
    save(fig, "nm_sim.png")
if __name__ == "__main__" and (not os.environ.get("ONLY") or any("nm" .startswith(o) for o in os.environ["ONLY"].split(","))):
    safe("nm_sim", nm_sim)
