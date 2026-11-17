"""Score the phone's SENSOR-CHANNEL dynamics against the 1 kHz encoder.

Horizontal-rig valid metrics only (all gyro-observable): oscillation FREQUENCY,
ring-down COUNT N and DECAY (Block R), and step RISE / OVERSHOOT / SETTLE (Block S).
Absolute amplitude / A0 is NOT scored here -- it is unobservable in-plane and belongs
on the tilted rig (see docs/bench/TILTED_RIG_ANGLE_VALIDATION_PROTOCOL.md).

Phone angle = raw gyro-y integration (the validated horizontal estimator), NOT the
app's fused/projected output. The SAME estimator (`oscillation_metrics` /
`step_metrics`) runs on the encoder and the phone, and ring-down events are detected
INDEPENDENTLY on each trace, so no sample-level time alignment is needed and both
sides are scored identically. Steps are detected on the encoder (clean truth) and
evaluated on the phone at the same instants via the cross-correlation lag.

Ported from the September bench lineage (commit a09bbfc, preserved on the local
archive/bench/* tags) with three fixes: the trial number is read from "Trial<NN>"
rather than the first digit run (``ck_9_14_pt...`` used to parse as 9 for every
file), --min-num / --max-num apply independently, and phone<->encoder pairing is a
one-to-one monotone alignment (an encoder file can no longer be claimed by two phone
trials, and identical staircases can no longer swap partners).

Usage:
  python bench_dynamics.py --mode ringdown \
      --manifest <session>-manifest.json --encoder-dir <folder> \
      --out ringdown.csv [--plot ringdown.png] [--glob "*Fast.bin"]
  python bench_dynamics.py --mode step \
      --manifest <session>-manifest.json --encoder-dir <folder> --out step.csv

The phone trials (manifest, sorted by capture timestamp) are paired with the encoder
files (sorted by trial number) in capture order, refined within a +/-window by best
angle cross-correlation. Verify the pairing in the CSV `enc_file` column.
"""
from __future__ import annotations

import argparse
import csv
import glob
import json
import os
import re

import numpy as np
from scipy.signal import find_peaks

from labview_bin_reader import read_labview_bin

DEG = 180.0 / np.pi


# ── loaders ───────────────────────────────────────────────────────────────────
def load_jsonl(path):
    with open(path, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def gyro_from_records(recs, axis=1):
    """(t[s], angle[deg], rate[deg/s]) from gyro integration of one axis.

    r['v'][1] is the swing axis with the phone flat on the horizontal arm; the export
    stores rad/s (iOS rotationRate). Returns None when there are too few samples.
    """
    g = sorted((r["t"], r["v"][axis]) for r in recs if r.get("sensor") == "gyro")
    if len(g) < 30:
        return None
    t = np.array([x[0] for x in g], float)
    w = np.array([x[1] for x in g], float) * DEG          # rad/s -> deg/s
    if np.median(np.diff(t)) > 0.5:                       # ms -> s
        t = t / 1000.0
    t = t - t[0]
    ang = np.concatenate([[0.0], np.cumsum((w[1:] + w[:-1]) / 2 * np.diff(t))])
    return t, ang - np.median(ang[:40]), w


def phone_gyro(path, axis=1):
    return gyro_from_records(load_jsonl(path), axis)


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
    t = np.asarray(t, float)
    x = np.asarray(x, float)
    fin = np.isfinite(x)
    t, x = t[fin], x[fin]
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
    mag = np.abs(x[idx])
    start = int(np.argmax(mag))                            # the release / first big extremum
    ring = idx[start + 1:]                                 # oscillation after release
    if len(ring) < 3:
        return nan
    tr = _refine_extrema_times(t, x, ring)
    mr = np.abs(x[ring])
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


def _refine_extrema_times(t, x, idx):
    """Sub-sample extremum times by a 3-point parabola through each peak.

    The phone gyro arrives at ~60 Hz, so a 4-5 Hz ring has only 6-7 samples per
    half-period and raw extremum times quantise f by several percent. The encoder
    (1 kHz) is barely affected; both sides still go through the same code.
    """
    out = t[idx].astype(float)
    for k, i in enumerate(idx):
        if 0 < i < len(x) - 1:
            y0, y1, y2 = x[i - 1], x[i], x[i + 1]
            den = y0 - 2 * y1 + y2
            if den != 0:
                d = 0.5 * (y0 - y2) / den
                if abs(d) <= 1:
                    out[k] = t[i] + d * (t[i + 1] - t[i - 1]) / 2
    return out


def find_steps(t, pos, vel, min_step_deg=3.0, vel_frac=0.3, min_vel=20.0):
    """Segment a staircase into steps from the velocity pulses (works on either trace)."""
    t = np.asarray(t, float)
    pos = np.asarray(pos, float)
    av = np.abs(np.asarray(vel, float))
    dt = float(np.median(np.diff(t)))
    thr = max(min_vel, vel_frac * av.max())
    pk, _ = find_peaks(av, height=thr, distance=max(1, int(0.5 / dt)))
    steps = []
    for p in pk:
        h = 0.2 * av[p]
        lo = p
        while lo > 0 and av[lo] > h:
            lo -= 1
        hi = p
        while hi < len(av) - 1 and av[hi] > h:
            hi += 1
        b0, b1 = max(0, lo - int(0.6 / dt)), max(0, lo - int(0.1 / dt))
        a0, a1 = min(len(pos) - 1, hi + int(0.1 / dt)), min(len(pos) - 1, hi + int(0.6 / dt))
        if b1 <= b0 or a1 <= a0:
            continue
        frm, to = float(np.median(pos[b0:b1])), float(np.median(pos[a0:a1]))
        if abs(to - frm) < min_step_deg:
            continue
        if steps and t[lo] - steps[-1]["t_start"] < 0.4:  # dedupe a split ramp
            continue
        steps.append(dict(t_start=float(t[lo]), t_ramp=float(t[p]), frm=frm, to=to))
    return steps


def step_metrics(t, x, frm, to, t0, twin=3.0):
    """Rise (10-90%), overshoot (%), settle (+/-2% band) for one step, from t0."""
    t = np.asarray(t, float)
    x = np.asarray(x, float)
    m = (t >= t0) & (t <= t0 + twin)
    tt, xx = t[m] - t0, x[m]
    step = to - frm
    s = np.sign(step)
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


# ── phone <-> encoder pairing (capture order + angle cross-correlation) ─────────
def trial_num(p):
    """Trial number from an encoder filename: the digits after 'Trial', else the last
    digit run. ``ck_9_14_ptStretchTrial07Slow.bin`` -> 7 (not 9)."""
    b = os.path.basename(p)
    m = re.search(r"Trial(\d+)", b, flags=re.IGNORECASE)
    if m:
        return int(m.group(1))
    runs = re.findall(r"\d+", b)
    return int(runs[-1]) if runs else 0


def xcorr_align(tp, ap, te, pe, dt=0.01):
    """Align phone angle vs encoder position (both polarities).

    Returns (quality, lag, sign) where phone_time = encoder_time + lag and
    sign * phone ~ encoder. To draw the phone in the encoder frame use
    (tp - lag, sign * ap).
    """
    gp = np.arange(0, min(tp[-1], te[-1]), dt)
    A = np.interp(gp, tp, ap)
    B0 = np.interp(gp, te, pe)
    if len(A) < 10:
        return -1.0, 0.0, 1
    A = A - A.mean()
    best = None
    for s in (1, -1):
        B = s * B0 - (s * B0).mean()
        xc = np.correlate(A, B, mode="full")
        i = int(np.argmax(xc))
        q = float(xc[i] / (np.linalg.norm(A) * np.linalg.norm(B) + 1e-9))
        if best is None or q > best[0]:
            best = (q, (i - (len(B) - 1)) * dt, s)
    return best


def list_encoders(enc_dir, glob_pat="*.bin", min_num=None, max_num=None):
    encs = [e for e in glob.glob(os.path.join(enc_dir, glob_pat)) if os.path.getsize(e) > 0]
    encs = sorted(encs, key=lambda e: (trial_num(e), os.path.basename(e)))
    if min_num is not None:
        encs = [e for e in encs if trial_num(e) >= min_num]
    if max_num is not None:
        encs = [e for e in encs if trial_num(e) <= max_num]
    return encs


def pair_by_order(phones, encs, window=3, min_q=0.2, gap=0.15):
    """One-to-one pairing of capture-ordered phone traces with trial-ordered encoders.

    phones: list of (key, (t, angle, rate) or None) in capture order.
    encs:   list of encoder paths in trial order.
    Both lists are chronological, so the matching is a MONOTONE alignment (dynamic
    programming, like sequence alignment): it maximises the summed cross-correlation
    quality, may skip a trial on either side (a capture with no encoder log, an
    encoder log with no phone export), never claims an encoder file twice, and
    never crosses -- identical staircases cannot swap partners on a noisy score.
    Candidates are encoders within +/-window of the same rank; pairs with quality
    below `min_q` are not matched.
    Returns {phone_key: (enc_path, quality, lag, sign)}.
    """
    cache = {}

    def _enc(j):
        if j not in cache:
            try:
                cache[j] = encoder(encs[j])
            except Exception:                              # unreadable / truncated file
                cache[j] = None
        return cache[j]

    n_p, n_e = len(phones), len(encs)
    # a count mismatch (extra captures, a filtered encoder range) shifts the diagonal;
    # widen the band by it -- the monotone constraint keeps the wider band safe
    window = window + abs(n_p - n_e)
    cand = {}
    for i, (key, pg) in enumerate(phones):
        if pg is None:
            continue
        tp, ap, _ = pg
        for j in range(max(0, i - window), min(n_e, i + window + 1)):
            e = _enc(j)
            if e is None:
                continue
            q, lag, sign = xcorr_align(tp, ap, e[0], e[1])
            if q >= min_q:
                cand[(i, j)] = (q, lag, sign)
    # Semi-global alignment: S[i][j] = best score using phones[:i] and encs[:j].
    # Leading/trailing skips are free (an encoder range that covers only part of the
    # session); each interior skip costs `gap`, so a contiguous run of identical
    # trials is preferred over an interleaved one.
    S = np.zeros((n_p + 1, n_e + 1))
    move = np.zeros((n_p + 1, n_e + 1), dtype=np.int8)     # 0 skip phone, 1 skip enc, 2 match
    for i in range(1, n_p + 1):
        for j in range(1, n_e + 1):
            best, mv = S[i - 1, j] - gap, 0
            if S[i, j - 1] - gap > best:
                best, mv = S[i, j - 1] - gap, 1
            c = cand.get((i - 1, j - 1))
            if c is not None and S[i - 1, j - 1] + c[0] > best:
                best, mv = S[i - 1, j - 1] + c[0], 2
            S[i, j], move[i, j] = best, mv
    # free trailing skips: start the traceback from the best cell on the last row/col
    ends = [(S[n_p, j], n_p, j) for j in range(n_e + 1)] + [(S[i, n_e], i, n_e) for i in range(n_p + 1)]
    _, i, j = max(ends, key=lambda e: (e[0], -abs(e[1] - e[2])))
    out = {}
    while i > 0 and j > 0:
        mv = move[i, j]
        if mv == 2:
            q, lag, sign = cand[(i - 1, j - 1)]
            out[phones[i - 1][0]] = (encs[j - 1], q, lag, sign)
            i, j = i - 1, j - 1
        elif mv == 1:
            j -= 1
        else:
            i -= 1
    return out


def pair_trials(manifest, enc_dir, glob_pat="*.bin", window=3, num_range=None):
    """Back-compatible entry point: [(phone_path, enc_path, quality, lag), ...]."""
    base = os.path.dirname(manifest)
    with open(manifest, encoding="utf-8") as fh:
        man = json.load(fh)
    phone_files = [os.path.join(base, x["file"])
                   for x in sorted(man["trials"], key=lambda t: t["timestamp"])]
    lo, hi = num_range if num_range else (None, None)
    encs = list_encoders(enc_dir, glob_pat, lo, hi)
    if not encs:
        raise SystemExit(f"no encoder files matching {glob_pat!r} in {enc_dir}")
    phones = [(pf, phone_gyro(pf) if os.path.exists(pf) else None) for pf in phone_files]
    m = pair_by_order(phones, encs, window)
    return [(pf, m[pf][0], m[pf][1], m[pf][2]) for pf, _ in phones if pf in m]


# ── modes ───────────────────────────────────────────────────────────────────
def level(t, x, lo, hi):
    m = (t >= lo) & (t <= hi)
    return float(np.median(x[m])) if m.any() else np.nan


def score_ringdown(pg, enc):
    """Phone/encoder ring-down metrics for one paired trial."""
    ph = oscillation_metrics(pg[0], pg[1])
    en = oscillation_metrics(enc[0], enc[1])
    return dict(phone_f=ph["f"], enc_f=en["f"], df_pct=pct(ph["f"], en["f"]),
                phone_N=ph["N"], enc_N=en["N"],
                phone_tau=ph["tau"], enc_tau=en["tau"],
                phone_zeta=ph["zeta"], enc_zeta=en["zeta"])


def score_steps(pg, enc, lag):
    """Per-step rows. Steps are detected on the ENCODER (clean truth); the phone
    transient is evaluated at those same instants (phone t0 = enc t_start + lag), with
    the phone's own settled levels. This avoids false steps from integrated-gyro drift."""
    tpp, app, _ = pg
    te, pe, ev = enc
    rows = []
    for k, es in enumerate(find_steps(te, pe, ev), 1):
        em = step_metrics(te, pe, es["frm"], es["to"], es["t_start"])
        p0 = es["t_start"] + lag                          # encoder instant -> phone frame
        p_frm = level(tpp, app, p0 - 0.5, p0 - 0.1)
        p_to = level(tpp, app, p0 + 1.5, p0 + 2.8)        # settled part of the 3 s dwell
        pm = step_metrics(tpp, app, p_frm, p_to, p0)
        # guard: if the phone step doesn't match the encoder's, the alignment failed
        # for this step -- don't report a bogus transient (a sync tap prevents this).
        if not (np.isfinite(pm["step"]) and abs(pm["step"]) > 0.5 * abs(em["step"])):
            pm = dict(rise=np.nan, overshoot=np.nan, settle=np.nan, step=pm["step"])
        rows.append(dict(step=k, enc_step_deg=round(em["step"], 1),
                         phone_step_deg=round(pm["step"], 1) if np.isfinite(pm["step"]) else np.nan,
                         phone_rise=pm["rise"], enc_rise=em["rise"],
                         phone_over=pm["overshoot"], enc_over=em["overshoot"],
                         phone_settle=pm["settle"], enc_settle=em["settle"]))
    return rows


def run_ringdown(pairs, out, plot):
    rows = []
    for rank, (pf, ef, q, lag) in enumerate(pairs, 1):
        r = dict(rank=rank, enc_file=os.path.basename(ef), match_q=round(q, 3))
        r.update(score_ringdown(phone_gyro(pf), encoder(ef)))
        rows.append(r)
    write_csv(out, rows)
    pf_, ef_ = paired(rows, "phone_f", "enc_f")
    print(f"\nRing-down: {len(rows)} trials")
    if len(pf_) >= 2:
        g, b = np.polyfit(ef_, pf_, 1)
        r2 = np.corrcoef(ef_, pf_)[0, 1] ** 2
        print(f"  frequency  phone vs encoder:  gain {g:.3f}  intercept {b:+.3f} Hz  R2 {r2:.4f}")
        print(f"             median |df|        {np.nanmedian(np.abs(col(rows, 'df_pct'))):.1f} %")
    print(f"  swing N    median |dN|        {med_abs_diff(rows, 'phone_N', 'enc_N'):.2f}")
    print(f"  decay tau  median |dtau|      {med_abs_diff(rows, 'phone_tau', 'enc_tau'):.2f} s")
    if plot:
        _plot_grid(pairs, plot, "Ring-down: phone gyro vs encoder", ringdown=True)


def run_step(pairs, out, plot):
    rows = []
    for rank, (pf, ef, q, lag) in enumerate(pairs, 1):
        for r in score_steps(phone_gyro(pf), encoder(ef), lag):
            rows.append(dict(rank=rank, enc_file=os.path.basename(ef), **r))
    write_csv(out, rows)
    print(f"\nStep dynamics: {len(rows)} steps over {len(pairs)} trials")
    for a, b, lab, unit in [("phone_rise", "enc_rise", "rise", "s"),
                            ("phone_over", "enc_over", "overshoot", "%"),
                            ("phone_settle", "enc_settle", "settle", "s")]:
        print(f"  {lab:9s} median |phone-enc|  {med_abs_diff(rows, a, b):.3f} {unit}")
    if plot:
        _plot_grid(pairs, plot, "Step staircase: phone gyro vs encoder", ringdown=False)


# ── small helpers ─────────────────────────────────────────────────────────────
def pct(a, b):
    return 100.0 * (a - b) / b if (np.isfinite(a) and np.isfinite(b) and b) else np.nan


def col(rows, k):
    return np.array([r.get(k, np.nan) if r.get(k) is not None else np.nan for r in rows], float)


def paired(rows, a, b):
    A, B = col(rows, a), col(rows, b)
    m = np.isfinite(A) & np.isfinite(B)
    return A[m], B[m]


def med_abs_diff(rows, a, b):
    A, B = paired(rows, a, b)
    return float(np.nanmedian(np.abs(A - B))) if len(A) else np.nan


def write_csv(path, rows):
    if not rows:
        print("no rows to write")
        return
    keys = []
    for r in rows:
        keys += [k for k in r if k not in keys]
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=keys)
        w.writeheader()
        for r in rows:
            w.writerow({k: (f"{v:.4g}" if isinstance(v, float) else v) for k, v in r.items()})
    print(f"wrote {len(rows)} rows -> {path}")


def _plot_grid(pairs, out, title, ringdown, n=6):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    pick = pairs[:: max(1, len(pairs) // n)][:n]
    fig, axes = plt.subplots(2, 3, figsize=(17, 8))
    for ax, (pf, ef, q, lag) in zip(axes.ravel(), pick):
        pg = phone_gyro(pf)
        te, pe, _ = encoder(ef)
        _, _, sign = xcorr_align(pg[0], pg[1], te, pe)
        ax.plot(te, pe, color="#14212B", lw=1.4, label="encoder (truth)")
        ax.plot(pg[0] - lag, sign * pg[1], color="#00819C", lw=1.0, label="phone gyro (aligned)")
        label = os.path.basename(ef)
        if ringdown:
            en, ph = oscillation_metrics(te, pe), oscillation_metrics(pg[0], pg[1])
            label += (f"\nf {ph['f']:.2f}/{en['f']:.2f}Hz  N {ph['N']:.1f}/{en['N']:.1f}  "
                      f"tau {ph['tau']:.1f}/{en['tau']:.1f}s")
        ax.set_title(label, fontsize=8)
        ax.grid(alpha=.3)
        ax.legend(fontsize=7)
        ax.set_xlabel("s")
        ax.set_ylabel("deg")
    fig.suptitle(title + (" (phone/encoder metrics in titles)" if ringdown else ""), y=1.0)
    fig.tight_layout()
    fig.savefig(out, dpi=120, bbox_inches="tight")
    plt.close(fig)
    print(f"saved {out}")


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
    pairs = pair_trials(a.manifest, a.encoder_dir, a.glob, a.window, (a.min_num, a.max_num))
    lowq = sum(1 for p in pairs if p[2] < 0.5)
    print(f"paired {len(pairs)} phone<->encoder trials (verify enc_file in the CSV)"
          + (f"; {lowq} with low match confidence (<0.5) -- check those rows / use the sync tap"
             if lowq else ""))
    (run_ringdown if a.mode == "ringdown" else run_step)(pairs, a.out, a.plot)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
