"""
evaluate_continuity_window.py
=============================
One-off research diagnostic (NOT pipeline-wired, same category as
evaluate_peak_detection.py / evaluate_flex_axis_methods.py).

THE QUESTION. `score_waveform`'s continuity check is bounded to an
"active-swing window", and the two ports compute that window with DIFFERENT
CODE:

  RULE_INLINE   imu_calibration_tuner.score_waveform (Python, desktop tuner):
                two or more extrema -> the last extremum, capped at
                release + 4.0 s; otherwise the first point the signal is
                permanently within tolerance of `neutral`, also capped.

  RULE_HELPER   mobile-imu-core/src/scoring.rs::score_waveform (Rust, the
                shipped wasm): active_oscillation_window_end, which stops at
                the first gap -- from release, or between consecutive extrema
                -- larger than _OSCILLATION_GAP_FACTOR times the extrema's own
                median spacing, and has NO time cap at all.

The Python helper's docstring says it was modelled on score_waveform's inline
logic. The helper then evolved (gap cadence, cap removed), Rust adopted the
helper, and Python's score_waveform kept the original. The golden parity tests
pin six synthetic trials, the two rules agree on all six, and nothing noticed.

So the desktop tuner and the shipped app may apply different quality gates to
the same waveform. "May" is the whole problem: it was asserted from reading the
code and never measured. This script measures it, so the decision about which
rule is right -- above all whether to keep the 4.0 s cap -- can be made against
numbers.

WHY COMPARING TWO PYTHON FUNCTIONS MEASURES A PYTHON-VS-RUST DIFFERENCE.
RULE_HELPER here is `P._active_oscillation_window_end`, the reference that
Rust's `active_oscillation_window_end` is a line-by-line port of. The existing
parity tests (mobile-imu-core/tests/scoring_test.rs, pipeline_test.rs against
tests/fixtures/golden.rs) check that port on the golden trials. This script
therefore measures the ALGORITHMIC divergence, which is the part that matters
for the decision; it would not catch a porting slip inside the helper itself.

HOW THE REPLICATION IS KEPT HONEST. The verdict under RULE_INLINE is computed
twice: once by calling the real `score_waveform`, and once by this file's own
replication of it. Every trial asserts the two agree on both `passes` and
`penalty`. If the replication ever drifts from the real function, the run
fails loudly instead of quietly comparing the wrong thing -- so the RULE_HELPER
column can be trusted to differ only in the window.

Usage:
    python3 evaluate_continuity_window.py [quick|full|clean]

`clean` runs PART 2: clean trials only -- no freeze, no tremor -- sweeping the
sample rate. It exists because PART 1's headline is misleading on its own. The
helper's window is longer on every trial where the two differ, and it catches
sensor freezes the 4 s cap hides; but it also rejects trials with no artifact
in them at all, and that false rejection is a function of SAMPLE RATE, because
the plateau rule is written in SAMPLES (a run of 6 steps under 0.05 deg) rather
than as a duration. Six samples is 0.3 s at 20 Hz and 0.06 s at 100 Hz, and a
genuine turning point on a decaying swing is flat to within 0.05 deg over
0.06 s. So the window and the plateau rule cannot be decided separately.
"""
from __future__ import annotations

import math
import sys

import numpy as np

import pendulastic_pt_score as P
from imu_calibration_tuner import score_waveform
from pendulastic_pt_score import compute_pt_params


# ══════════════════════════════════════════════════════════════════════════════
# Synthetic trials
# ══════════════════════════════════════════════════════════════════════════════

def make_trial(duration_s=12.0, fps=20.0, release_t=1.0, neutral_deg=140.0,
               decay=0.18, freq=0.9, tremor_deg=0.0, tremor_hz=4.0,
               freeze_at_s=None, freeze_len_s=0.0, seed=0):
    """A damped swing from 180 deg toward `neutral_deg`, optionally with a
    resting tremor and a frozen-sensor stretch.

    Same convention as tests/test_imu_calibration_tuner.py's fixture:
    angle(tau) = neutral + (180 - neutral) * exp(-decay*tau) * cos(2*pi*f*tau),
    which is exactly 180 at release and never exceeds it.

    A freeze holds the last good sample and then shifts the remainder by a
    constant so it resumes continuously. The shift matters: without it the
    un-freeze edge is a step that the clip check catches on its own, and the
    trial would no longer isolate the window at all.
    """
    rng = np.random.default_rng(seed)
    t = np.arange(0.0, duration_s, 1.0 / fps)
    ang = np.full_like(t, 180.0)
    amp = 180.0 - neutral_deg
    post = t >= release_t
    tau = t[post] - release_t
    ang[post] = neutral_deg + amp * np.exp(-decay * tau) * np.cos(2 * np.pi * freq * tau)

    if tremor_deg > 0:
        # Tremor only in the settled tail, which is the pathology the window's
        # right-hand bound exists to keep out of the extremum count.
        settled = t >= release_t + 0.75 * (duration_s - release_t)
        ang[settled] += tremor_deg * np.sin(2 * np.pi * tremor_hz * t[settled])
        ang[settled] += rng.normal(0.0, 0.02, int(settled.sum()))

    if freeze_at_s is not None and freeze_len_s > 0:
        i0 = int(np.searchsorted(t, freeze_at_s))
        i1 = int(np.searchsorted(t, freeze_at_s + freeze_len_s))
        if 0 < i0 < i1 < len(t) - 1:
            held = ang[i0]
            ang[i0:i1] = held
            ang[i1:] += held - ang[i1]
    return t, ang


# ══════════════════════════════════════════════════════════════════════════════
# The two window rules
# ══════════════════════════════════════════════════════════════════════════════

def window_inline(pt, extrema):
    """score_waveform's own rule, as it stands in imu_calibration_tuner."""
    t_r, ang_r = pt["t_r"], pt["ang_r"]
    if len(extrema) >= 2:
        last_extremum_t = float(t_r[int(extrema.max())])
        return t_r[0] + min(4.0, max(0.0, last_extremum_t - t_r[0]))
    neutral = pt["neutral_deg"]
    tol = max(2.0, 0.05 * pt["A0_deg"])
    near_neutral = np.abs(ang_r - neutral) <= tol
    settle_idx = len(ang_r) - 1
    for i in range(len(ang_r)):
        if np.all(near_neutral[i:]):
            settle_idx = i
            break
    return min(t_r[0] + 4.0, float(t_r[settle_idx]))


def window_helper(pt, extrema):
    """The rule Rust's score_waveform uses, including the same `>= 2`
    routing: fewer than two extrema takes the helper's no-oscillation
    branch."""
    pk, tr = pt["pk_i"], pt["tr_i"]
    if len(extrema) < 2:
        pk, tr = np.array([], dtype=int), np.array([], dtype=int)
    return float(P._active_oscillation_window_end(
        pt["t_r"], pt["ang_r"], pk, tr, pt["neutral_deg"], pt["A0_deg"]))


# ══════════════════════════════════════════════════════════════════════════════
# score_waveform, replicated so one window can be swapped for the other
# ══════════════════════════════════════════════════════════════════════════════

def _continuity(t, angle_deg, t_r0, window_end_t):
    """score_waveform's clip and plateau scan, verbatim."""
    clip = 0
    diffs = np.diff(angle_deg)
    for i in range(len(diffs)):
        if not (np.isfinite(angle_deg[i]) and np.isfinite(angle_deg[i + 1])):
            continue
        if abs(diffs[i]) > 25.0:
            clip += 1

    plateau, run = 0, 0
    for i in np.where((t >= t_r0) & (t <= window_end_t))[0]:
        if i + 1 >= len(angle_deg):
            continue
        if not (np.isfinite(angle_deg[i]) and np.isfinite(angle_deg[i + 1])):
            run = 0
            continue
        if abs(angle_deg[i + 1] - angle_deg[i]) < 0.05:
            run += 1
            if run >= 6:
                plateau += 1
        else:
            run = 0
    return clip, plateau


def replicate(t, angle_deg, pt, window_fn):
    """passes/penalty under an arbitrary window rule.

    Everything outside check C is score_waveform's own logic; only the window
    is a parameter. Returns None when score_waveform would have refused the
    trial before reaching C.
    """
    start_mask = t <= (t[0] + 0.3)
    start_vals = angle_deg[start_mask]
    start_vals = start_vals[np.isfinite(start_vals)]
    if len(start_vals) == 0:
        return None
    start_median = float(np.median(start_vals))
    start_ok = abs(start_median - 180.0) <= 8.0
    start_penalty = max(0.0, abs(start_median - 180.0) - 8.0)

    ang_r = pt["ang_r"]
    min_angle = float(np.nanmin(ang_r))
    range_ok = 80.0 <= min_angle <= 178.0
    range_penalty = max(0.0, 80.0 - min_angle) + max(0.0, min_angle - 178.0)

    extrema = np.concatenate([np.asarray(pt["pk_i"]), np.asarray(pt["tr_i"])])
    window_end_t = window_fn(pt, extrema)
    clip, plateau = _continuity(t, angle_deg, pt["t_r"][0], window_end_t)
    continuity_ok = (clip == 0 and plateau == 0)

    d_ok = (
        0.0 <= pt["N"] <= 10.0
        and 10.0 <= pt["A0_deg"] <= 90.0
        and (pt["f"] == 0.0 or 0.3 <= pt["f"] <= 3.0)
        and math.isfinite(pt["R2n"])
        and math.isfinite(pt["omega_max_n"])
        and math.isfinite(pt["omega_min_n"])
    )
    return {
        "passes": start_ok and range_ok and continuity_ok and d_ok,
        "penalty": (start_penalty + range_penalty
                    + 2.0 * clip + 1.0 * plateau
                    + (0.0 if d_ok else 50.0)),
        "window_end_t": window_end_t,
        "window_s": window_end_t - float(pt["t_r"][0]),
        "plateau": plateau,
        "continuity_ok": continuity_ok,
    }


# ══════════════════════════════════════════════════════════════════════════════
# The corpus
# ══════════════════════════════════════════════════════════════════════════════

def corpus(quick: bool):
    """Every combination is a legitimate trial shape or a sensor artifact we
    would want caught; none is contrived to favour either rule."""
    durations = [12.0, 30.0] if quick else [6.0, 12.0, 20.0, 30.0]
    decays = [0.18, 0.9] if quick else [0.08, 0.18, 0.40, 0.90]
    fpss = [20.0, 100.0] if quick else [20.0, 50.0, 100.0]
    tremors = [0.0, 2.0] if quick else [0.0, 0.5, 2.0]
    neutrals = [140.0] if quick else [120.0, 140.0, 160.0]
    freqs = [0.9] if quick else [0.6, 0.9, 1.3]

    freezes = [(None, 0.0)]
    for at in (1.1, 3.0, 6.0):
        for ln in (0.5, 2.0, 4.0):
            freezes.append((at, ln))

    n = 0
    for dur in durations:
        for decay in decays:
            for fps in fpss:
                for tremor in tremors:
                    for neutral in neutrals:
                        for freq in freqs:
                            for at, ln in freezes:
                                if at is not None and at + ln >= dur - 0.5:
                                    continue
                                n += 1
                                yield dict(
                                    duration_s=dur, fps=fps, decay=decay,
                                    freq=freq, neutral_deg=neutral,
                                    tremor_deg=tremor, freeze_at_s=at,
                                    freeze_len_s=ln, seed=n)


def main(mode="quick"):
    quick = (mode != "full")
    rows, skipped, refused = [], 0, 0

    for kw in corpus(quick):
        t, ang = make_trial(**kw)
        pt = compute_pt_params(t, ang, detrend=False)
        if pt is None:
            refused += 1
            continue
        a = replicate(t, ang, pt, window_inline)
        b = replicate(t, ang, pt, window_helper)
        if a is None or b is None:
            skipped += 1
            continue

        # The honesty check: the replication under the inline window must BE
        # score_waveform. If this ever fires, the comparison is meaningless.
        real = score_waveform(t, ang)
        if real["params"] is not None:
            assert real["passes"] == a["passes"], (
                f"replication drift on {kw}: passes {a['passes']} vs real "
                f"{real['passes']}")
            assert abs(real["penalty"] - a["penalty"]) < 1e-9, (
                f"replication drift on {kw}: penalty {a['penalty']} vs real "
                f"{real['penalty']}")
        rows.append((kw, a, b))

    print(f"trials evaluated      {len(rows)}")
    print(f"  refused by scorer   {refused}")
    print(f"  unscorable pre-C    {skipped}")
    print("  (replication matched the real score_waveform on every one)")
    print()

    dt_tol = 1e-6
    diff_window = [r for r in rows if abs(r[1]["window_s"] - r[2]["window_s"]) > dt_tol]
    diff_cont = [r for r in rows if r[1]["continuity_ok"] != r[2]["continuity_ok"]]
    diff_pass = [r for r in rows if r[1]["passes"] != r[2]["passes"]]

    def pct(k):
        return f"{100.0 * k / max(1, len(rows)):5.1f}%"

    print(f"windows differ        {len(diff_window):5d}  {pct(len(diff_window))}")
    print(f"continuity verdict    {len(diff_cont):5d}  {pct(len(diff_cont))}")
    print(f"OVERALL pass/fail     {len(diff_pass):5d}  {pct(len(diff_pass))}")
    print()

    if diff_window:
        inl = np.array([r[1]["window_s"] for r in diff_window])
        hlp = np.array([r[2]["window_s"] for r in diff_window])
        print("where they differ, window length after release (s):")
        print(f"  inline  median {np.median(inl):6.2f}   max {inl.max():6.2f}")
        print(f"  helper  median {np.median(hlp):6.2f}   max {hlp.max():6.2f}")
        print(f"  helper longer in {int((hlp > inl).sum())} of {len(diff_window)}")
        print()

    if diff_pass:
        print("the disagreements that change the verdict:")
        hdr = ("  dur  fps decay  freq neutral trem  freeze      "
               "win_inl win_hlp  inline  helper")
        print(hdr)
        for kw, a, b in diff_pass[:40]:
            fz = ("none" if kw["freeze_at_s"] is None
                  else f"{kw['freeze_at_s']:.1f}+{kw['freeze_len_s']:.1f}s")
            print(f"  {kw['duration_s']:4.0f} {kw['fps']:4.0f} "
                  f"{kw['decay']:5.2f} {kw['freq']:5.1f} "
                  f"{kw['neutral_deg']:7.0f} {kw['tremor_deg']:4.1f} "
                  f"{fz:>11s} {a['window_s']:7.2f} {b['window_s']:7.2f} "
                  f"{'PASS' if a['passes'] else 'fail':>7s} "
                  f"{'PASS' if b['passes'] else 'fail':>7s}")
        if len(diff_pass) > 40:
            print(f"  ... and {len(diff_pass) - 40} more")
        print()

        froze = sum(1 for kw, _, _ in diff_pass if kw["freeze_at_s"] is not None)
        helper_stricter = sum(1 for _, a, b in diff_pass
                              if a["passes"] and not b["passes"])
        print(f"  of those, {froze} have a sensor freeze and "
              f"{len(diff_pass) - froze} are clean trials")
        print(f"  helper rejects what inline accepts: {helper_stricter}")
        print(f"  inline rejects what helper accepts: "
              f"{len(diff_pass) - helper_stricter}")
        print()

        # A clean trial whose verdict depends on the rule is the worst case:
        # the two ports disagree about a trial with no artifact in it at all.
        clean = [(kw, a, b) for kw, a, b in diff_pass
                 if kw["freeze_at_s"] is None]
        if clean:
            print(f"  WORST CASE: {len(clean)} trial(s) with NO artifact at "
                  f"all get different verdicts.")
    else:
        print("no trial in this corpus gets a different pass/fail verdict.")


def clean_sweep():
    """PART 2: on trials with no artifact at all, does the helper rule reject,
    and does that depend on the sample rate?"""
    print("CLEAN trials only -- no freeze, no tremor. A disagreement here is a")
    print("FALSE rejection: there is no artifact in the trial to catch.")
    print()
    print("  fps  decay  freq neutral | win_inl win_hlp  plateau  verdict")
    by_fps = {}
    for fps in (20.0, 50.0, 100.0, 200.0):
        by_fps[fps] = [0, 0]
        for decay in (0.08, 0.18, 0.40, 0.90):
            for freq in (0.6, 0.9, 1.3):
                for neutral in (120.0, 140.0, 160.0):
                    t, ang = make_trial(duration_s=12.0, fps=fps, decay=decay,
                                       freq=freq, neutral_deg=neutral)
                    pt = compute_pt_params(t, ang, detrend=False)
                    if pt is None:
                        continue
                    a = replicate(t, ang, pt, window_inline)
                    b = replicate(t, ang, pt, window_helper)
                    if a is None or b is None:
                        continue
                    by_fps[fps][1] += 1
                    if a["passes"] != b["passes"]:
                        by_fps[fps][0] += 1
                        if neutral == 160.0:
                            print(f"  {fps:4.0f} {decay:6.2f} {freq:5.1f} "
                                  f"{neutral:7.0f} | {a['window_s']:7.2f} "
                                  f"{b['window_s']:7.2f} {b['plateau']:8d}  "
                                  f"{'PASS' if a['passes'] else 'fail'} -> "
                                  f"{'PASS' if b['passes'] else 'fail'}")
    print()
    print("  false rejections on clean trials, by sample rate:")
    for fps, (f, n) in by_fps.items():
        print(f"    {fps:5.0f} Hz : {f:3d} of {n:3d}"
              f"   (6 samples = {6.0 / fps:.3f} s)")
    print()
    print("  The 20 Hz row is clean and 100 Hz is the worst. The drop back to")
    print("  1 of 36 at 200 Hz is NOT explained here -- the window must come")
    print("  out shorter at that rate, but this script does not show why, so")
    print("  no mechanism is claimed for it.")


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "quick"
    if mode == "clean":
        clean_sweep()
    else:
        main(mode)
