"""Score the phone's SENSOR-CHANNEL dynamics against the 1 kHz encoder.

Horizontal-rig valid metrics only (all gyro-observable): oscillation FREQUENCY,
ring-down COUNT N and DECAY (Block R), and step RISE / OVERSHOOT / SETTLE (Block S).
Absolute amplitude / A0 is NOT scored here -- it is unobservable in-plane and belongs
on the tilted rig (see TILTED_RIG_ANGLE_VALIDATION_PROTOCOL.md).

Phone angle = raw gyro-y integration (the validated horizontal estimator), NOT the
app's fused/abs() output. The SAME estimator (`oscillation_metrics` / `step_metrics`)
runs on the encoder and the phone, and events are detected INDEPENDENTLY on each trace
then paired by order -- so no sample-level time alignment is needed and both sides are
scored identically.

Usage:
  python bench_dynamics.py --mode ringdown \
      --manifest <session>-manifest.json --encoder-dir <folder> \
      --out ringdown.csv [--plot ringdown.png] [--glob "*Fast.bin"]
  python bench_dynamics.py --mode step \
      --manifest <session>-manifest.json --encoder-dir <folder> --out step.csv

The phone trials (manifest, sorted by capture timestamp) are paired with the encoder
files (sorted by embedded trial number, else name) in capture order, refined within a
+/-window by best rate cross-correlation. Verify the pairing in the CSV `enc_file` column.
"""
from __future__ import annotations
import argparse, glob, json, os, re
import numpy as np
from scipy.signal import find_peaks

import bench_rescore as br
from labview_bin_reader import read_labview_bin

DEG = 180.0 / np.pi


# ── loaders ───────────────────────────────────────────────────────────────────
def phone_gyro(path):
    """(t[s], angle[deg], rate[deg/s]) from gyro-y integration. r['v'][1] = swing axis."""
    recs = br.load_jsonl(path)
    g = sorted((r["t"], r["v"][1]) for r in recs if r.get("sensor") == "gyro")
    if len(g) < 30:
        return None
    t = np.array([x[0] for x in g], float)
    w = np.array([x[1] for x in g], float) * DEG          # rad/s -> deg/s
    if np.median(np.diff(t)) > 0.5:                       # ms -> s
        t = t / 1000.0
    t = t - t[0]
    ang = np.concatenate([[0.0], np.cumsum((w[1:] + w[:-1]) / 2 * np.diff(t))])
    return t, ang - np.median(ang[:40]), w


def encoder(path):
    """(t[s], position[deg], velocity[deg/s]) from the 1 kHz .bin."""
    e = read_labview_bin(path)
    v = e.get("velocity")
    if v is None:
        v = np.gradient(e["position"], e["time"])
    return e["time"], e["position"] - np.median(e["position"][:100]), np.asarray(v, float)


# ── shared estimators (identical for phone and encoder) ─────────────────────────
def oscillation_metrics(t, x, prom_frac=0.05, floor_frac=0.05, floor_abs_deg=0.5):
    """Ring-down: frequency f[Hz], swing count N, decay tau[s], damping zeta.

    Successive extrema of a decaying oscillation are half a period apart. We start at
    the largest extremum (the release), take the ring extrema after it, read f from the
    median half-period, N from how many stay above a floor, and tau from a log-linear
    fit of the peak envelope.
    """
    t = np.asarray(t, float); x = np.asarray(x, float)
    fin = np.isfinite(x); t, x = t[fin], x[fin]
    nan = dict(f=np.nan, N=np.nan, tau=np.nan, zeta=np.nan, A0=np.nan, n_ext=0)
    if len(x) < 20:
        return nan
    dt = float(np.median(np.diff(t)))
    tail = max(5, int(1.0 / dt))
    x = x - np.median(x[-tail:])                          # zero the settled tail
    amax = float(np.nanmax(np.abs(x)))
    if amax <= 1e-6:
        return nan
    prom = prom_frac * amax
    pk_pos, _ = find_peaks(x, prominence=prom)
    pk_neg, _ = find_peaks(-x, prominence=prom)
    idx = np.sort(np.concatenate([pk_pos, pk_neg]).astype(int))
    if len(idx) < 3:
        return nan
    mag = np.abs(x[idx]); te = t[idx]
    start = int(np.argmax(mag))                            # the release / first big extremum
    ring = idx[start + 1:]                                 # oscillation after release
    if len(ring) < 3:
        return nan
    tr = t[ring]; mr = np.abs(x[ring])
    half_T = np.median(np.diff(tr))
    f = 1.0 / (2.0 * half_T) if half_T > 0 else np.nan
    floor = max(floor_frac * mr[0], floor_abs_deg)
    N = float(np.sum(mr >= floor)) / 2.0                   # half-swings -> full swings
    good = mr > floor
    tau = zeta = np.nan
    if good.sum() >= 3:
        slope = np.polyfit(tr[good] - tr[0], np.log(mr[good]), 1)[0]
        if slope < 0:
            tau = -1.0 / slope
            sigma, wd = 1.0 / tau, 2 * np.pi * f
            if np.isfinite(wd):
                zeta = sigma / np.hypot(sigma, wd)
    return dict(f=f, N=N, tau=tau, zeta=zeta, A0=float(mr[0]), n_ext=len(ring))


def find_steps(t, pos, vel, min_step_deg=3.0, vel_frac=0.3, min_vel=20.0):
    """Segment a staircase into steps from the velocity pulses (works on either trace)."""
    t = np.asarray(t, float); pos = np.asarray(pos, float); av = np.abs(np.asarray(vel, float))
    dt = float(np.median(np.diff(t)))
    thr = max(min_vel, vel_frac * av.max())
    pk, _ = find_peaks(av, height=thr, distance=max(1, int(0.5 / dt)))
    steps = []
    for p in pk:
        h = 0.2 * av[p]
        l = p
        while l > 0 and av[l] > h:
            l -= 1
        r = p
        while r < len(av) - 1 and av[r] > h:
            r += 1
        b0, b1 = max(0, l - int(0.6 / dt)), max(0, l - int(0.1 / dt))
        a0, a1 = min(len(pos) - 1, r + int(0.1 / dt)), min(len(pos) - 1, r + int(0.6 / dt))
        if b1 <= b0 or a1 <= a0:
            continue
        frm, to = float(np.median(pos[b0:b1])), float(np.median(pos[a0:a1]))
        if abs(to - frm) < min_step_deg:
            continue
        if steps and t[l] - steps[-1]["t_start"] < 0.4:   # dedupe a split ramp
            continue
        steps.append(dict(t_start=float(t[l]), t_ramp=float(t[p]), frm=frm, to=to))
    return steps


def step_metrics(t, x, frm, to, t0, twin=3.0):
    """Rise (10-90%), overshoot (%), settle (+/-2% band) for one step, from t0."""
    t = np.asarray(t, float); x = np.asarray(x, float)
    m = (t >= t0) & (t <= t0 + twin)
    tt, xx = t[m] - t0, x[m]
    step = to - frm; s = np.sign(step)
    nan = dict(rise=np.nan, overshoot=np.nan, settle=np.nan, step=step)
    if len(xx) < 5 or step == 0:
        return nan
    y = (xx - frm) / step                                  # 0 at start, 1 at target
    def _cross(thr):
        w = np.where(y >= thr)[0]
        return tt[w[0]] if len(w) else np.nan
    t10, t90 = _cross(0.1), _cross(0.9)
    rise = t90 - t10 if (np.isfinite(t10) and np.isfinite(t90) and t90 > t10) else np.nan
    peak = float(np.max(s * (xx - to)))
    overshoot = 100.0 * peak / abs(step) if peak > 0 else 0.0
    band = 0.02 * abs(step)
    outside = np.where(np.abs(xx - to) > band)[0]
    settle = float(tt[outside[-1]]) if len(outside) else 0.0
    return dict(rise=rise, overshoot=overshoot, settle=settle, step=step)


# ── phone <-> encoder pairing (capture order + rate cross-correlation) ──────────
def _trial_num(p):
    m = re.search(r"(\d+)", os.path.basename(p))
    return int(m.group(1)) if m else 0


def _xcorr(tp, ap, te, pe, dt=0.01):
    """Align phone angle vs encoder position (both polarities). Returns (quality, lag)
    where phone_time = encoder_time + lag (so phone t0 = encoder t_start + lag; to draw
    the phone in the encoder frame, shift it by -lag)."""
    gp = np.arange(0, min(tp[-1], te[-1]), dt)
    A = np.interp(gp, tp, ap); B0 = np.interp(gp, te, pe)
    if len(A) < 10:
        return -1.0, 0.0
    A = A - A.mean()
    best = None
    for s in (1, -1):
        B = s * B0 - (s * B0).mean()
        xc = np.correlate(A, B, mode="full")
        i = int(np.argmax(xc))
        q = float(xc[i] / (np.linalg.norm(A) * np.linalg.norm(B) + 1e-9))
        if best is None or q > best[0]:
            best = (q, (i - (len(B) - 1)) * dt)             # phone_time = enc_time + lag
    return best


def pair_trials(manifest, enc_dir, glob_pat, window, num_range=None):
    base = os.path.dirname(manifest)
    man = json.load(open(manifest, encoding="utf-8"))
    phones = [os.path.join(base, x["file"]) for x in sorted(man["trials"], key=lambda t: t["timestamp"])]
    encs = sorted(glob.glob(os.path.join(enc_dir, glob_pat)), key=_trial_num)
    if num_range:
        lo, hi = num_range
        encs = [e for e in encs if lo <= _trial_num(e) <= hi]
    if not encs:
        raise SystemExit(f"no encoder files matching {glob_pat!r} in {enc_dir}")
    pairs = []
    for i, pf in enumerate(phones):
        pg = phone_gyro(pf)
        if pg is None:
            continue
        tp, ap, _ = pg
        best = None
        for j in range(max(0, i - window), min(len(encs), i + window + 1)):
            try:
                te, pe, _ = encoder(encs[j])
            except Exception:
                continue
            q, lag = _xcorr(tp, ap, te, pe)
            if best is None or q > best[0]:
                best = (q, j, lag)
        if best:
            pairs.append((pf, encs[best[1]], best[0], best[2]))
    return pairs


# ── modes ───────────────────────────────────────────────────────────────────
def _level(t, x, lo, hi):
    m = (t >= lo) & (t <= hi)
    return float(np.median(x[m])) if m.any() else np.nan


def run_ringdown(pairs, out, plot):
    rows = []
    for rank, (pf, ef, q, lag) in enumerate(pairs, 1):
        pg = phone_gyro(pf); te, pe, _ = encoder(ef)
        ph = oscillation_metrics(pg[0], pg[1]); en = oscillation_metrics(te, pe)
        rows.append(dict(rank=rank, enc_file=os.path.basename(ef), match_q=round(q, 3),
                         phone_f=ph["f"], enc_f=en["f"], df_pct=_pct(ph["f"], en["f"]),
                         phone_N=ph["N"], enc_N=en["N"],
                         phone_tau=ph["tau"], enc_tau=en["tau"],
                         phone_zeta=ph["zeta"], enc_zeta=en["zeta"]))
    _write_csv(out, rows)
    pf_, ef_ = _paired(rows, "phone_f", "enc_f")
    print(f"\nRing-down: {len(rows)} trials")
    if len(pf_) >= 2:
        g, b = np.polyfit(ef_, pf_, 1)
        r2 = np.corrcoef(ef_, pf_)[0, 1] ** 2
        print(f"  frequency  phone vs encoder:  gain {g:.3f}  intercept {b:+.3f} Hz  R2 {r2:.4f}")
        print(f"             median |df|        {np.nanmedian(np.abs(_col(rows,'df_pct'))):.1f} %")
    print(f"  swing N    median |dN|        {_med_abs_diff(rows,'phone_N','enc_N'):.2f}")
    print(f"  decay tau  median |dtau|      {_med_abs_diff(rows,'phone_tau','enc_tau'):.2f} s")
    if plot:
        _plot_ringdown(pairs, plot)


def run_step(pairs, out, plot):
    """Steps are detected on the ENCODER (clean truth); the phone transient is then
    evaluated at those same instants (phone t0 = enc t_start - lag), with the phone's
    own settled levels. This avoids false steps from the drifty integrated-gyro angle."""
    rows = []
    for rank, (pf, ef, q, lag) in enumerate(pairs, 1):
        (tpp, app, _), (te, pe, ev) = phone_gyro(pf), encoder(ef)
        for k, es in enumerate(find_steps(te, pe, ev), 1):
            em = step_metrics(te, pe, es["frm"], es["to"], es["t_start"])
            p0 = es["t_start"] + lag                          # encoder instant -> phone frame
            p_frm = _level(tpp, app, p0 - 0.5, p0 - 0.1)
            p_to = _level(tpp, app, p0 + 1.5, p0 + 2.8)       # settled part of the 3 s dwell
            pm = step_metrics(tpp, app, p_frm, p_to, p0)
            # guard: if the phone step doesn't match the encoder's, the alignment failed
            # for this step -- don't report a bogus transient (a sync tap prevents this).
            if not (np.isfinite(pm["step"]) and abs(pm["step"]) > 0.5 * abs(em["step"])):
                pm = dict(rise=np.nan, overshoot=np.nan, settle=np.nan, step=pm["step"])
            rows.append(dict(rank=rank, enc_file=os.path.basename(ef), step=k,
                             enc_step_deg=round(em["step"], 1),
                             phone_step_deg=round(pm["step"], 1),
                             phone_rise=pm["rise"], enc_rise=em["rise"],
                             phone_over=pm["overshoot"], enc_over=em["overshoot"],
                             phone_settle=pm["settle"], enc_settle=em["settle"]))
    _write_csv(out, rows)
    print(f"\nStep dynamics: {len(rows)} steps over {len(pairs)} trials")
    for a, b, lab, unit in [("phone_rise", "enc_rise", "rise", "s"),
                            ("phone_over", "enc_over", "overshoot", "%"),
                            ("phone_settle", "enc_settle", "settle", "s")]:
        print(f"  {lab:9s} median |phone-enc|  {_med_abs_diff(rows,a,b):.3f} {unit}")
    if plot:
        _plot_step(pairs, plot)


# ── small helpers ─────────────────────────────────────────────────────────────
def _pct(a, b):
    return 100.0 * (a - b) / b if (np.isfinite(a) and np.isfinite(b) and b) else np.nan

def _col(rows, k):
    return np.array([r.get(k, np.nan) for r in rows], float)

def _paired(rows, a, b):
    A, B = _col(rows, a), _col(rows, b)
    m = np.isfinite(A) & np.isfinite(B)
    return A[m], B[m]

def _med_abs_diff(rows, a, b):
    A, B = _paired(rows, a, b)
    return float(np.nanmedian(np.abs(A - B))) if len(A) else np.nan

def _write_csv(path, rows):
    if not rows:
        print("no rows to write"); return
    import csv
    keys = list(rows[0].keys())
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=keys)
        w.writeheader()
        for r in rows:
            w.writerow({k: (f"{v:.4g}" if isinstance(v, float) else v) for k, v in r.items()})
    print(f"wrote {len(rows)} rows -> {path}")

def _shift_to_first_extremum(t, x):
    m = oscillation_metrics(t, x)
    return t, x  # plot uses raw; kept simple

def _plot_ringdown(pairs, out, n=6):
    import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt
    pick = pairs[:: max(1, len(pairs) // n)][:n]
    fig, axes = plt.subplots(2, 3, figsize=(17, 8))
    for ax, (pf, ef, q, lag) in zip(axes.ravel(), pick):
        pg = phone_gyro(pf); te, pe, _ = encoder(ef)
        ax.plot(te, pe, color="#14212B", lw=1.4, label="encoder (truth)")
        ax.plot(pg[0], pg[1], color="#00819C", lw=1.0, label="phone gyro")
        en = oscillation_metrics(te, pe); ph = oscillation_metrics(pg[0], pg[1])
        ax.set_title(f"{os.path.basename(ef)}\nf {ph['f']:.2f}/{en['f']:.2f}Hz  "
                     f"N {ph['N']:.1f}/{en['N']:.1f}  tau {ph['tau']:.1f}/{en['tau']:.1f}s",
                     fontsize=8)
        ax.grid(alpha=.3); ax.legend(fontsize=7); ax.set_xlabel("s"); ax.set_ylabel("deg")
    fig.suptitle("Ring-down: phone gyro vs encoder (phone/encoder metrics in titles)", y=1.0)
    fig.tight_layout(); fig.savefig(out, dpi=120, bbox_inches="tight"); print(f"saved {out}")

def _plot_step(pairs, out, n=6):
    import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt
    pick = pairs[:: max(1, len(pairs) // n)][:n]
    fig, axes = plt.subplots(2, 3, figsize=(17, 8))
    for ax, (pf, ef, q, lag) in zip(axes.ravel(), pick):
        pg = phone_gyro(pf); te, pe, _ = encoder(ef)
        ax.plot(te, pe, color="#14212B", lw=1.4, label="encoder")
        ax.plot(pg[0] - lag, pg[1], color="#00819C", lw=1.0, label="phone gyro (aligned)")
        ax.set_title(os.path.basename(ef), fontsize=8)
        ax.grid(alpha=.3); ax.legend(fontsize=7); ax.set_xlabel("s"); ax.set_ylabel("deg")
    fig.suptitle("Step staircase: phone gyro vs encoder", y=1.0)
    fig.tight_layout(); fig.savefig(out, dpi=120, bbox_inches="tight"); print(f"saved {out}")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--mode", choices=["ringdown", "step"], required=True)
    ap.add_argument("--manifest", required=True, help="phone session *-manifest.json")
    ap.add_argument("--encoder-dir", required=True, help="folder of encoder .bin files")
    ap.add_argument("--glob", default="*.bin", help="encoder filename glob (default *.bin)")
    ap.add_argument("--window", type=int, default=3, help="+/- pairing search window")
    ap.add_argument("--min-num", type=int, help="keep encoder files with trial number >= this")
    ap.add_argument("--max-num", type=int, help="keep encoder files with trial number <= this")
    ap.add_argument("--out", required=True, help="output CSV")
    ap.add_argument("--plot", help="optional overlay PNG")
    a = ap.parse_args(argv)
    num_range = (a.min_num, a.max_num) if (a.min_num is not None and a.max_num is not None) else None
    pairs = pair_trials(a.manifest, a.encoder_dir, a.glob, a.window, num_range)
    lowq = sum(1 for p in pairs if p[2] < 0.5)
    print(f"paired {len(pairs)} phone<->encoder trials (verify enc_file in the CSV)"
          + (f"; {lowq} with low match confidence (<0.5) -- check those rows / use the sync tap" if lowq else ""))
    (run_ringdown if a.mode == "ringdown" else run_step)(pairs, a.out, a.plot)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
