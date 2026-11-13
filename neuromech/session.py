"""Rig sessions: plan one before the bench, score it after.

    python -m neuromech.session plan  --name S1 [--sids A-P0,A-P1p | --groups A,M] [--reps 3]
                                      [--repeat A-P1p:10] [--seed 7] [--chirp] [--no-blind]
    python -m neuromech.session score --name S1 --encoder-dir DIR [--phone-manifest FILE.json]
                                      [--accept-session-lag-ms MS] [--tracking-limit-ms MS]
    python -m neuromech.session check-log --bin LOG.bin --player FILE.csv

CHECK-LOG answers the one bench question the program cannot answer itself: does StretchExperiment's log
record while the Pendulum Player drives the setpoint? Play any player file, then point this at the log.
It finds the file in the log's position_setpoint channel and reports coverage, match, how often the
setpoint changed (the Player writes every ~10 ms), and tracking. Exit status 1 if the log is unusable.

PLAN writes neuromech/sessions/<name>/:
  runsheet.csv          what the operator sees: run id, order, duration, blank columns to fill.
                        Scenario ids are NOT on it (R3: assessors and operators grade blind).
  key.csv               run id -> scenario id. Keep it away from anyone grading.
  commands/<run>.csv    1 kHz command (t_s, setpoint_deg, vel_dps, acc_dps2, phase)
  player/<run>.csv      what the Pendulum Player VI reads: two columns (t_s, setpoint_deg), NO header
                        (a header row reads as zeros, makes dt = 0 and ends playback at once)
  commands/<run>.dbl    the same five columns, big-endian float64, interleaved, no header --
                        LabVIEW "Read from Binary File" (DBL, big-endian is its default), reshape to N x 5.
  session.json          seed, scorer constants, and the library's provenance (formula ID + model hash)
                        PLAN refuses a library whose provenance differs from the code (rebuild it first).

SCORE reads each run's LabVIEW log from <encoder-dir>, by the name written in the run sheet's
encoder_file column (StretchExperiment names its own logs; <run>.bin if left blank), and, optionally, the
phone session manifest (one phone recording per run, in run order). For every run it reports:
  delivered   the 7 parameters + PT7 + band scored from the ENCODER (the truth, plan D4)
  intended    the same from the scenario manifest
  phone       the same from the phone gyro, aligned on the three sync pulses
  tracking    encoder vs command RMSE over the swing
  release     where each trace is scored from. The scorer is handed the release of the DELIVERED motion, not
              the command's: the arm leaves the hold later than the command does (Player hold + plant), and
              a release that is early by ~15 ms makes the scorer count the start of the record as an extra
              half-oscillation. The delivered release is measured per run and per trace (_delivered_release)
              and written out: cmd_release_s, enc_release_s, enc_release_lag_ms, enc_release_resid_deg, and
              the same for the phone plus ph_minus_enc_lag_ms. A trace is FLAGGED and not scored (there is
              no fallback to the commanded release; the reason is in the report; `score` exits 1) when
                - the onset does not look like the command's (enc_release_resid_deg), or
                - the release is uncertain: the hold is not still before it (enc_hold_rms_deg), or another
                  release 10 ms or more away fits nearly as well (enc_rival_ratio), or
                - a phone trace is not where a phone of its sample period is expected against the encoder
                  (ph_minus_enc_lag_ms against ph_expected_offset_ms; the gate is in sample periods).
              The lag itself is judged ACROSS THE SESSION, because it is a property of the rig: a run more
              than SESSION_LAG_TOL_S from the session's median is flagged as that run's fault; a session
              whose runs agree with each other but sit outside RELEASE_LAG_PLAUSIBLE_S is ONE rig finding,
              printed once, and nothing is scored until the rig is looked at or the finding is accepted
              with --accept-session-lag-ms MS (MS must be the session's median to within the tolerance;
              it is written to session_lag_override_ms on every row).
  tracking    also as a time: the lag-aligned RMSE over the swing divided by the command's rms speed
              (tracking_time_err_ms). Over TRACKING_TIME_ERR_MAX_MS (--tracking-limit-ms) the run is
              "tracking flagged": its scores stay in the report but it does not count as a delivery of
              its scenario, and `score` exits 1. PROVISIONAL limit, from the simulated plant.
The hold, uniqueness, session and tracking limits are all provisional: set on dryrun's plant and on
constructed faults, with no rig log behind them.
Output: sessions/<name>/report.csv and a printed summary.
SCORE refuses when the session's provenance differs from the current code or library: the intended values
would come from a different scorer or model than the session was planned against (plan D2/B14).
--allow-provenance-mismatch scores anyway and records the mismatch in report.csv.
"""
from __future__ import annotations

import argparse
import csv
import glob
import json
import math
import os
import shutil
import sys
from datetime import datetime

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")

import motor_bench as mb  # noqa: E402
import motor_reference as mr  # noqa: E402
import pendulastic_pt_score as P  # noqa: E402
from labview_bin_reader import read_labview_bin  # noqa: E402
from neuromech import ilc  # noqa: E402
from neuromech.build import OUT, score_angle, PARAMS7, FS_SCORE, provenance, provenance_mismatch, library_provenance  # noqa: E402

SESS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sessions")
FS = 1000.0


# ── plan ────────────────────────────────────────────────────────────────────
def chirp_command(f0=0.2, f1=10.0, dur=20.0, amp=8.0):
    """Identification run: sync preamble (no lift), then a tapered log chirp about zero."""
    tp, pre = mr.preamble(a0_deg=0.0, fs_hz=FS)
    t = np.arange(0, dur, 1 / FS)
    k = math.log(f1 / f0) / dur
    x = amp * np.sin(2 * math.pi * f0 * (np.exp(k * t) - 1) / k) * np.clip(np.minimum(t, dur - t) / 1.0, 0, 1)
    sp = np.concatenate([pre, x, np.zeros(int(2 * FS))])
    phase = np.concatenate([np.zeros(len(pre)), 3 * np.ones(len(x)), 3 * np.ones(int(2 * FS))]).astype(int)
    ts = np.arange(len(sp)) / FS
    vel = np.gradient(sp, 1 / FS); acc = np.gradient(vel, 1 / FS)
    return ts, sp, vel, acc, phase


_write_player = ilc.write_player


def _write_command(dst_base, t, sp, vel, acc, phase):
    with open(dst_base + ".csv", "w", newline="") as fh:
        w = csv.writer(fh); w.writerow(["t_s", "setpoint_deg", "vel_dps", "acc_dps2", "phase"])
        for row in zip(np.round(t, 4), np.round(sp, 4), np.round(vel, 3), np.round(acc, 2), phase): w.writerow(row)
    np.column_stack([t, sp, vel, acc, phase]).astype(">f8").tofile(dst_base + ".dbl")
    _write_player(os.path.join(os.path.dirname(os.path.dirname(dst_base)), "player", os.path.basename(dst_base) + ".csv"), t, sp)


def plan(name, sids=None, groups=None, reps=1, repeat=None, seed=7, chirp=False, blind=True):
    prov = provenance()
    stale = provenance_mismatch(library_provenance(), prov)
    if stale:
        raise SystemExit(f"the scenario library was built under different {', '.join(stale)} than this code "
                         f"({prov['score_formula_id']}, model {prov['model_hash']}); "
                         f"run python -m neuromech.build --recalibrate first")
    man = pd.read_csv(os.path.join(OUT, "scenario_manifest.csv"))
    pick = man
    if sids: pick = man[man.sid.isin(sids)]
    elif groups: pick = man[man.group.isin(groups)]
    missing = set(sids or []) - set(pick.sid)
    if repeat and repeat.split(":")[0] not in set(man.sid):   # before anything is written
        missing.add(repeat.split(":")[0])
    if missing: raise SystemExit(f"unknown scenario ids: {sorted(missing)}")
    order = [sid for sid in pick.sid for _ in range(reps)]
    if repeat:
        sid, n = repeat.split(":"); order = [sid] * int(n) + order          # repeatability block first
    rng = np.random.default_rng(seed)
    body = order[int(repeat.split(":")[1]):] if repeat else order
    rng.shuffle(body)
    order = (order[: int(repeat.split(":")[1])] if repeat else []) + list(body)
    d = os.path.join(SESS, name); cmd_dir = os.path.join(d, "commands")
    if os.path.exists(d): raise SystemExit(f"session {name} exists; pick another name")
    os.makedirs(cmd_dir)
    runs = []
    if chirp:
        t, sp, vel, acc, ph = chirp_command(); _write_command(os.path.join(cmd_dir, "R000"), t, sp, vel, acc, ph)
        runs.append(dict(run_id="R000", sid="R-chirp", duration_s=round(float(t[-1]), 1), peak_deg=round(float(np.max(np.abs(sp))), 1)))
    for i, sid in enumerate(order, start=1):
        rid = f"R{i:03d}"
        src = os.path.join(OUT, "setpoints", f"{sid}.csv")
        shutil.copy(src, os.path.join(cmd_dir, rid + ".csv"))
        a = np.genfromtxt(src, delimiter=",", names=True)
        np.column_stack([a["t_s"], a["setpoint_deg"], a["vel_dps"], a["acc_dps2"], a["phase"]]).astype(">f8").tofile(os.path.join(cmd_dir, rid + ".dbl"))
        _write_player(os.path.join(d, "player", rid + ".csv"), a["t_s"], a["setpoint_deg"])
        runs.append(dict(run_id=rid, sid=sid, duration_s=round(float(a["t_s"][-1]), 1),
                         peak_deg=round(float(np.max(np.abs(a["setpoint_deg"]))), 1)))
    with open(os.path.join(d, "runsheet.csv"), "w", newline="") as fh:
        w = csv.writer(fh)
        # peak_deg: the furthest the run commands the arm from zero, so the operator can check it
        # against the arm's range before pressing run (the Angle Limit fields do not bound the Player).
        # encoder_file: StretchExperiment names its own logs; write the real name here at the bench.
        w.writerow(["order", "run_id", "duration_s", "peak_deg", "encoder_file", "phone_started", "app_auto_stopped", "notes"]
                   + ([] if blind else ["scenario"]))
        for k, r in enumerate(runs):
            w.writerow([k, r["run_id"], r["duration_s"], r["peak_deg"], f"{r['run_id']}.bin", "", "", ""] + ([] if blind else [r["sid"]]))
    pd.DataFrame(runs)[["run_id", "sid", "duration_s"]].to_csv(os.path.join(d, "key.csv"), index=False)
    json.dump(dict(name=name, created=datetime.now().isoformat(timespec="seconds"), seed=seed, reps=reps, repeat=repeat,
                   chirp=chirp, blind=blind, n_runs=len(runs), provenance=prov, scorer=dict(HEALTHY_REF=P.HEALTHY_REF, F_PLAUSIBLE_MAX_HZ=getattr(P, "F_PLAUSIBLE_MAX_HZ", None))),
              open(os.path.join(d, "session.json"), "w"), indent=1, default=float)
    print(f"planned {len(runs)} runs -> {d}  (runsheet.csv for the bench, key.csv kept separately)")
    return d


# ── score ───────────────────────────────────────────────────────────────────
def _phone_angle(path):
    """(t, angle_deg, rate_deg_s) from raw gyro-y. Bias is NOT removed here; _align_phone removes it from
    the still lead-in once it knows where that is -- the step the app's stillness calibration performs."""
    recs = [json.loads(l) for l in open(path, encoding="utf-8") if l.strip()]
    g = sorted((r["t"], r["v"][1]) for r in recs if r.get("sensor") == "gyro")
    t = np.array([x[0] for x in g], float); w = np.degrees(np.array([x[1] for x in g], float))
    if np.median(np.diff(t)) > 0.5: t = t / 1000.0
    t = t - t[0]
    return t, _integrate(t, w), w


def _integrate(t, w):
    return np.concatenate([[0.0], np.cumsum((w[1:] + w[:-1]) / 2 * np.diff(t))])


def _coarse_lag(tp, ap, te, pe, dt=0.01):
    """NORMALISED cross-correlation of an encoder template (te, pe) against the whole phone record:
    (lag, sign) with phone_t + lag = encoder_t. Normalising by the phone's local energy stops a large swing
    from outscoring the true, smaller-amplitude match of the preamble."""
    gp = np.arange(tp[0], tp[-1], dt); ge = np.arange(te[0], te[-1], dt)
    xp = np.interp(gp, tp, ap); xe = np.interp(ge, te, pe); xe = xe - xe.mean()
    m = len(xe)
    if len(xp) < m: raise ValueError("phone record shorter than the preamble")
    num = np.correlate(xp, xe, "valid")                            # phone window starting at j vs template
    cs, cs2 = np.cumsum(np.r_[0, xp]), np.cumsum(np.r_[0, xp ** 2])
    wsum = cs[m:] - cs[:-m]; wsq = cs2[m:] - cs2[:-m]
    var = np.maximum(wsq - wsum ** 2 / m, 1e-12)
    r = num / np.sqrt(var * np.sum(xe ** 2))
    j = int(np.argmax(np.abs(r)))
    return (ge[0] - gp[j]), (1.0 if r[j] >= 0 else -1.0)


def _align_phone(te, pos, t_cmd0, tp, wp):
    """Phone samples onto the encoder clock, bias removed. Returns (tq, angle_deg, sign, lag_s, enc_edges, bias_dps).

    Coarse: cross-correlate the phone angle against the encoder's PREAMBLE only (sync pulses + min-jerk lift +
    hold, identical in every run and unlike any swing), both signs. Bias: median gyro rate over the still
    lead-in before the first pulse, on the coarse clock. Fine: the three sync-pulse edges, searched from the
    command's start on both clocks (find_pulse_edges assumes a trace that starts at the command; neither a
    LabVIEW log nor a phone recording does)."""
    T_REL = mr.LEAD_S + mr.release_offset_s()          # command start -> release (release_offset is from pulse 1)
    t_pre_end = t_cmd0 + T_REL + 0.5
    k = (te >= t_cmd0 - 0.2) & (te <= t_pre_end)
    ap = _integrate(tp, wp)
    lag, sg = _coarse_lag(tp, ap, te[k], pos[k])
    tq = tp + lag
    # Bias from every interval the arm is commanded still: the lead-in before the pulses and the 3 s hold
    # at the release angle (the app's own zero capture also uses a still window). ~5 s of stillness puts the
    # median within ~0.01 deg/s; 2 s gave +-0.04, enough to move PT7 by a band through area_ratio.
    t_hold0 = t_cmd0 + T_REL - mr.PRE_ROLL_S
    still = ((tq > t_cmd0 - 1.5) & (tq < t_cmd0 + mr.LEAD_S - 0.02)) |             ((tq > t_hold0 + 0.6) & (tq < t_cmd0 + T_REL - 0.1))
    bias = float(np.median(wp[still])) if still.sum() >= 10 else 0.0
    ang = sg * _integrate(tp, wp - bias)
    w0 = t_cmd0 - 0.1
    ke = te >= w0; e_edges = np.asarray(mb.find_pulse_edges(te[ke] - w0, pos[ke])) + w0
    kp = tq >= w0; i_edges = np.asarray(mb.find_pulse_edges(tq[kp] - w0, ang[kp] - ang[kp][0])) + w0
    off = i_edges - e_edges
    if np.ptp(off) > mb.MAX_OFFSET_SPREAD_S:
        raise mb.SyncError(f"pulse offsets disagree by {np.ptp(off):.3f} s")
    tq = tq - float(np.median(off))
    pre = tq < e_edges[0] - 0.05
    ang = ang - (np.median(ang[pre]) if pre.any() else 0.0)
    return tq, ang, sg, float(lag - np.median(off)), list(map(float, e_edges)), bias


# The delivered release. All of these are gates or window sizes, none is the lag itself: the lag is measured
# from each recording.
RELEASE_ONSET_DEG = 2.0                    # the onset is the command's first 2 deg of motion after its release
RELEASE_ONSET_PRE_S = 0.05                 # ... plus this much of the hold before it, which anchors the corner
RELEASE_ONSET_MAX_S = 1.0                  # a command that has not moved 2 deg by then has no release to find
RELEASE_LAG_SEARCH_S = (-0.060, 0.130)     # searched; wider than plausible so an edge optimum is recognisable
RELEASE_LAG_PLAUSIBLE_S = (-0.010, 0.080)  # delivered release - commanded release; outside it the run is flagged
RELEASE_RESID_MAX_DEG = 0.3                # rms mismatch of the matched onsets. The fit removes the mean, and the
#                                            window (50 ms of hold + the first 2 deg) has a standard deviation of 0.56 deg
#                                            (median of the 67 commands; 0.49 to 1.11), which is what an arm that never
#                                            leaves the hold scores: the limit is 54 % of the window's signal, NOT "15 % of
#                                            the onset". A clean fit is at 0.03. It catches a delivery that is not the
#                                            command (kick-back, slack); it does not tell a right release from a wrong one.
# An ambiguous onset. The residual cannot tell a right release from a wrong one when the hold moves (review
# 2026-11-11: wrong ones at 0.07-0.29 deg, right ones up to 0.24), so two things are checked beside it.
# PROVISIONAL, both: the limits sit between what the simulated plant delivers and the constructed faults; no
# rig log has been through them (docs/reports/2026-11-11-neuromech-release-alignment.md, "After review").
RELEASE_HOLD_STILL_S = 0.30                # the hold is judged over this long before the delivered release ...
RELEASE_HOLD_STILL_MAX_DEG = 0.20          # ... where the recording's rms about its own mean must stay below this.
#                                            Simulated: 0.02-0.05 on the identified plant (67 scenarios x 3 seeds, D-wobble
#                                            aside), at most 0.06 with bandwidth or damping +-30 %; every constructed hold
#                                            fault that moved a release by more than 10 ms is at 0.36 or above.
RELEASE_UNIQUE_APART_S = 0.010             # a rival release is any shift at least this far from the best one ...
RELEASE_UNIQUE_MIN_RATIO = 1.5             # ... and the best rival must fit at least this many times worse (rms ratio).
#                                            Simulated: 2.7 or more on the identified plant, 1.87 or more at +-30 %;
#                                            D-wobble, kick-back and slack are at 1.0 to 1.3.
# The phone against the encoder: both sensed one arm, so their releases must agree -- once the phone's known
# offset is allowed for. _align_phone puts the phone on the encoder clock from the three sync-pulse edges,
# and mb.find_pulse_edges takes an edge at the FIRST SAMPLE past its threshold: on the encoder's 1 ms grid
# that is the crossing, on a phone grid of period dt it is anywhere from 0 to dt after it. So the alignment
# moves the phone trace early by about half a sample, and integrating and interpolating the release corner on
# that grid adds a little more. Measured on the simulated phone (67 scenarios per rate): median -0.39 dt at
# 200 and 100 Hz, -0.54 to -0.58 dt at 59 Hz with jitter (-0.82 dt at exactly 59 Hz with none, where all three
# pulses land on one sampling phase), -0.64 dt at 30 and 25 Hz; of which the clock is -0.39 to -0.50 dt.
# The gate is therefore centred on -PHONE_EARLY_SAMPLES sample periods, and its width is in sample periods
# too, taken from the recording's own median period. PROVISIONAL: a real phone's timing has not been measured.
PHONE_EARLY_SAMPLES = 0.6                  # expected (phone lag - encoder lag), in phone sample periods, early
PHONE_SYNC_TOL_SAMPLES = 1.5               # allowed either side of that; simulated spread is within 0.64 at 59-200 Hz
SCORE_LEAD_S = 0.05                        # hold scored before the hold's own start (as before this change)


def _delivered_release(t_cmd, sp, t, x, t_rel, enc_lag_s=None):
    """Where the DELIVERED motion x(t) is released, given the command sp(t_cmd) released at t_rel (one clock).

    Onset matching: take the command from RELEASE_ONSET_PRE_S before its release until it has moved
    RELEASE_ONSET_DEG, and find the time shift at which the delivered trace lies on it (minimum rms
    difference after removing the mean difference, so an angle offset between the two costs nothing).
    delivered release = commanded release + that shift.

    It is the ONSET that is matched, not the whole first drop, because the two give different answers and
    the scorer is sensitive to the first: a closed loop rounds the corner at the release, so the arm trails
    the command by more while it leaves the hold than it does over the drop as a whole (the
    cross-correlation lag). Scored from the cross-correlation point the rounded corner is still inside
    the record (docs/reports/2026-11-11-neuromech-release-alignment.md).
    The evidence for that is the trace, not the score: measured as the time the delivered trace takes to
    leave the hold by x deg, less the same for the command, the arm is 30.5 ms behind at 1 deg, 29.7 at 2,
    25.1 at 5 and 20.6 at 10 (simulated plant). The N and band counts do NOT single out the 2 deg onset: on
    the identified plant a constant +30 to +50 ms gives the same counts. What separates a measured lag from
    a constant is a different plant (20 ms of delay leaves a constant +30 ms wrong in 40 of 67 scenarios and
    the measured lag in 10; review 2026-11-11).

    The fit alone can be confidently wrong, so two properties of it are checked and returned:
      hold_rms_deg   rms of the recording about its own mean over the RELEASE_HOLD_STILL_S before the
                     delivered release. The release is "the last instant the arm is still"; if the arm is
                     not still before it (sag, ringing, a commanded wobble) the fit has motion to lock onto
                     that is not the release, and A0 is read off a moving hold.
      rival_ratio    rms mismatch of the best shift at least RELEASE_UNIQUE_APART_S away from the chosen one,
                     over the mismatch of the chosen one. A clean onset has one sharp minimum and the ratio
                     is large; a second minimum, or a shallow one, brings it towards 1: some other release
                     fits nearly as well, and nothing in the data says which is right.

    -> dict(release_s, lag_s, resid_deg, hold_rms_deg, rival_ratio, flag, kind). flag is "" when the release
    is usable, otherwise the reason it is not; a flagged release must not be scored from. kind names the
    check that failed ("lag" for the plausible-range check, which session.score judges across the session).
    enc_lag_s: given for a PHONE trace, the encoder's lag for the same run. A phone trace is not held to the
    plausible lag range at all (the encoder is, across the session, and a phone reads early by an amount that
    depends on its sample period, so an absolute limit flags it exactly when the arm tracks well). It is held
    to the encoder: phone lag - encoder lag must be within PHONE_SYNC_TOL_SAMPLES sample periods of the
    expected -PHONE_EARLY_SAMPLES sample periods; the expected value is returned as expected_offset_s."""
    out = dict(release_s=np.nan, lag_s=np.nan, resid_deg=np.nan, hold_rms_deg=np.nan, rival_ratio=np.nan,
               expected_offset_s=np.nan, flag="", kind="")

    def flagged(kind, why):
        out.update(flag=why, kind=kind); return out

    k = int(np.searchsorted(t_cmd, t_rel))
    moved = np.flatnonzero(np.abs(sp[k:] - sp[k]) >= RELEASE_ONSET_DEG)
    if not len(moved) or t_cmd[k + moved[0]] - t_rel > RELEASE_ONSET_MAX_S:
        return flagged("command", f"the command does not move {RELEASE_ONSET_DEG:g} deg within {RELEASE_ONSET_MAX_S:g} s of its release")
    tw = np.arange(t_rel - RELEASE_ONSET_PRE_S, t_cmd[k + moved[0]] + 1e-9, 1.0 / FS)
    lo, hi = RELEASE_LAG_SEARCH_S
    if t[0] > tw[0] + lo or t[-1] < tw[-1] + hi:
        return flagged("coverage", "the recording does not cover the release")
    ref = np.interp(tw, t_cmd, sp)
    lags = np.arange(lo, hi + 1e-9, 0.0005)
    err = np.std(np.interp(tw[None, :] + lags[:, None], t, x) - ref[None, :], axis=1)
    i = int(np.argmin(err)); lag = float(lags[i])
    if 0 < i < len(lags) - 1:                      # parabola through the three points about the minimum
        den = err[i - 1] - 2 * err[i] + err[i + 1]
        if den > 0: lag += 0.5 * (err[i - 1] - err[i + 1]) / den * (lags[1] - lags[0])
    rival = np.abs(lags - lags[i]) >= RELEASE_UNIQUE_APART_S - 1e-9
    held = (t >= t_rel + lag - RELEASE_HOLD_STILL_S) & (t <= t_rel + lag)
    out.update(release_s=t_rel + lag, lag_s=lag, resid_deg=float(err[i]),
               hold_rms_deg=float(np.std(x[held])) if held.sum() >= 3 else np.nan,
               rival_ratio=float(err[rival].min() / max(err[i], 1e-9)))
    p0, p1 = RELEASE_LAG_PLAUSIBLE_S
    if i in (0, len(lags) - 1):
        return flagged("edge", f"lag {lag * 1e3:+.0f} ms is the edge of the search ({lo * 1e3:+.0f} to {hi * 1e3:+.0f} ms): "
                               "no release was found, only where the search stopped")
    if err[i] > RELEASE_RESID_MAX_DEG:
        return flagged("resid", f"the onset does not match the command's (rms {err[i]:.2f} deg over its first "
                                f"{RELEASE_ONSET_DEG:g} deg; limit {RELEASE_RESID_MAX_DEG:g})")
    if not out["hold_rms_deg"] <= RELEASE_HOLD_STILL_MAX_DEG:
        return flagged("hold", f"release uncertain: the hold is not still (rms {out['hold_rms_deg']:.2f} deg over the "
                               f"{RELEASE_HOLD_STILL_S:g} s before the release; limit {RELEASE_HOLD_STILL_MAX_DEG:g})")
    if out["rival_ratio"] < RELEASE_UNIQUE_MIN_RATIO:
        return flagged("ambiguous", f"release uncertain: a release {RELEASE_UNIQUE_APART_S * 1e3:.0f} ms or more away fits "
                                    f"only {out['rival_ratio']:.1f} times worse (limit {RELEASE_UNIQUE_MIN_RATIO:g})")
    if enc_lag_s is None:
        if not p0 <= lag <= p1:
            return flagged("lag", f"lag {lag * 1e3:+.1f} ms is outside the plausible {p0 * 1e3:+.0f} to {p1 * 1e3:+.0f} ms")
        return out
    dt = float(np.median(np.diff(t)))
    out["expected_offset_s"] = -PHONE_EARLY_SAMPLES * dt
    off = lag - enc_lag_s - out["expected_offset_s"]
    if abs(off) > PHONE_SYNC_TOL_SAMPLES * dt:
        return flagged("phone", f"phone lag {lag * 1e3:+.1f} ms and encoder lag {enc_lag_s * 1e3:+.1f} ms differ by {(lag - enc_lag_s) * 1e3:+.1f} ms; "
                                f"at this phone's {dt * 1e3:.1f} ms sample period {out['expected_offset_s'] * 1e3:+.1f} ms is expected, within "
                                f"{PHONE_SYNC_TOL_SAMPLES * dt * 1e3:.0f} ms: the sync is off")
    return out


# The lag is a property of the rig, so it is judged across the session: the plausible range applies to the
# session's MEDIAN lag, and a run is judged against that median. PROVISIONAL, like the range itself.
SESSION_LAG_TOL_S = 0.012                  # a run this far from the session median is the suspect one. Simulated good
#                                            runs sit within -8.3 to +4.2 ms of their session median at +-30 % bandwidth
#                                            and damping (-4.8 to +3.0 on the identified plant); a slow start is +21,
#                                            40 ms of stiction +26, a forward lurch -22.
SESSION_LAG_MIN_RUNS = 3                   # fewer usable runs than this is not a session to take a median of
SESSION_LAG_CONSISTENT = 2.0 / 3.0         # share of runs within the tolerance for the lag to count as one rig lag


def _session_lag(lags_s, accept_s=None):
    """Judge the release lags of one session's usable fits. -> dict:
      n, median_s    how many lags, and their median
      reference_s    what each run is compared with (within SESSION_LAG_TOL_S), or None: each run is then held
                     to RELEASE_LAG_PLAUSIBLE_S on its own, as before there was a session check
      scorable       False when the session as a whole is outside the plausible range and not accepted
      finding        the session-level message, "" if there is nothing to say
      override_s     the accepted lag when --accept-session-lag-ms was USED to score, else NaN

    A median outside the plausible range with the runs agreeing is one finding about the rig, not one fault
    per run. accept_s is the operator saying "this rig's lag is about this, score it": it is honoured only
    when the session's median is within the tolerance of it, and it is recorded."""
    lags = np.asarray([v for v in lags_s if np.isfinite(v)], float)
    S = dict(n=len(lags), median_s=np.nan, reference_s=None, scorable=True, finding="", override_s=np.nan)
    if not len(lags): return S
    med = float(np.median(lags)); S["median_s"] = med
    p0, p1 = RELEASE_LAG_PLAUSIBLE_S; tol = SESSION_LAG_TOL_S
    near = int(np.sum(np.abs(lags - med) <= tol))
    spread = f"{len(lags)} runs, {lags.min() * 1e3:+.1f} to {lags.max() * 1e3:+.1f} ms, {near} within {tol * 1e3:.0f} ms of the median"
    rng = f"{p0 * 1e3:+.0f} to {p1 * 1e3:+.0f} ms"
    if p0 <= med <= p1:
        if len(lags) >= SESSION_LAG_MIN_RUNS: S["reference_s"] = med
        if accept_s is not None:
            S["finding"] = (f"--accept-session-lag-ms {accept_s * 1e3:g} was given and not needed: the session's median lag "
                            f"{med * 1e3:+.1f} ms is inside the plausible {rng}. It was ignored.")
        return S
    accepted = accept_s is not None and abs(med - accept_s) <= tol
    if accepted:
        S.update(reference_s=med, override_s=float(accept_s),
                 finding=(f"SESSION LAG OVERRIDE USED: the session's median release lag is {med * 1e3:+.1f} ms ({spread}), outside the "
                          f"plausible {rng}. It was scored because --accept-session-lag-ms {accept_s * 1e3:g} accepts it; each run "
                          f"is held to within {tol * 1e3:.0f} ms of the session median."))
        return S
    if len(lags) < SESSION_LAG_MIN_RUNS or near < SESSION_LAG_CONSISTENT * len(lags):
        S["finding"] = (f"the session's release lags are outside the plausible {rng} without agreeing with each other "
                        f"(median {med * 1e3:+.1f} ms; {spread}): there is no session lag to accept, and each run is judged on its own."
                        if len(lags) >= SESSION_LAG_MIN_RUNS else "")
        return S
    S["scorable"] = False
    S["finding"] = (
        f"SESSION LAG FINDING: on this session the delivered release is {med * 1e3:+.1f} ms from the commanded one "
        f"({spread}), outside the plausible {rng}. The lag is consistent across the session, so it points at the RIG "
        f"(latency or bandwidth the model does not have), not at one run. No run with this lag is scored. "
        f"What to do: (1) run check-log on one run and see that the setpoint changes every ~10 ms; (2) identify the plant "
        f"from the chirp run (R000) and compare it with the model's 6.35 Hz, zeta 0.23, no delay; (3) look for latency "
        f"added between the Player and the drive. If the lag is understood and is this rig's, score again with "
        f"--accept-session-lag-ms {med * 1e3:.0f}: the override and its value are written into report.csv. "
        f"The plausible range comes from a simulated plant and is provisional."
        + (f" (--accept-session-lag-ms {accept_s * 1e3:g} was given, but it is more than {tol * 1e3:.0f} ms from this "
           f"session's median, so it was not used.)" if accept_s is not None else ""))
    return S


def _lag_verdict(lag_s, S):
    """"" if this run's lag is acceptable in its session S (_session_lag), else the reason it is flagged."""
    p0, p1 = RELEASE_LAG_PLAUSIBLE_S
    if S["reference_s"] is None:
        if S["scorable"] and p0 <= lag_s <= p1: return ""
        if not S["scorable"]:
            return (f"session lag finding: lag {lag_s * 1e3:+.1f} ms, with the session's median at {S['median_s'] * 1e3:+.1f} ms, is "
                    f"outside the plausible {p0 * 1e3:+.0f} to {p1 * 1e3:+.0f} ms; see session_lag_finding")
        return f"lag {lag_s * 1e3:+.1f} ms is outside the plausible {p0 * 1e3:+.0f} to {p1 * 1e3:+.0f} ms"
    d = lag_s - S["reference_s"]
    if abs(d) > SESSION_LAG_TOL_S:
        return (f"lag {lag_s * 1e3:+.1f} ms is {d * 1e3:+.1f} ms from the session median {S['reference_s'] * 1e3:+.1f} ms "
                f"(limit {SESSION_LAG_TOL_S * 1e3:.0f} ms): this run, not the rig")
    return ""


# Tracking. Nothing above looks at whether the arm followed the command once it had left the hold, and the
# scorer is insensitive to some ways of not following it: a start at 30 % gain recovering over 0.6 s scores
# in band on 8 of 8 scenarios (review 2026-11-11). The release is not the place to catch that.
# The gated quantity is not the rms error in degrees: on the identified plant that runs from 0.18 to 2.79 deg
# across the 67 scenarios with nothing wrong (it scales with how fast the scenario moves), and a rig with
# 40 ms of latency shows up to 11 deg on runs that score correctly, because the lag is scored out. So the
# recording is first shifted by the measured release lag, and the remaining rms error is divided by the
# command's rms speed over the swing: the DELAY that would produce the same error on this command, in ms.
TRACKING_TIME_ERR_MAX_MS = 25.0            # PROVISIONAL until real rig data exists. Simulated, identified plant, 67
#                                            scenarios x 3 seeds: median 14.1, 95 % 15.2, maximum 19.2 (B-clonus; D-wobble
#                                            21.5, which is release-flagged anyway). The review's slow start: 28.3 to 65.4
#                                            on all 67. With bandwidth -30 % two good runs exceed it (R-reflex-sat 27.5,
#                                            S-lam3 25.6): a slower rig than the model will need this limit re-set.


def _tracking(t, sp, swing, t_rec, x, lag_s):
    """Tracking error over the swing with the release lag taken out. -> (rms error in deg, the same as the
    delay in ms that would produce it on this command: rms error / the command's rms speed)."""
    tt = t[swing]; err = np.interp(tt + lag_s, t_rec, x) - sp[swing]
    rms = float(np.sqrt(np.mean(err ** 2))); speed = float(np.sqrt(np.mean(np.gradient(sp[swing], tt) ** 2)))
    return rms, (rms / speed * 1e3 if speed > 0 else np.nan)


def _score_trace(t, rig_deg, rest_model_deg, t_release, lead_s, tail_s=None):
    """Rig angle (0 = passive rest) -> Pendulastic angle, scored from lead_s before t_release.

    The scorer's 100 Hz grid is laid out FROM the release, so the release is a grid sample whatever its
    sub-sample value, and a trace that is merely late is scored on the same samples as one that is not.
    (score_angle rounds a release between samples up to the next one; with the grid started at the hold,
    a 1 ms change of release could move it by 10.) tail_s: how much of the trace after the release to
    score; None = all of it."""
    dt = 1.0 / FS_SCORE
    n0 = int(round(lead_s / dt))
    end = t[-1] if tail_s is None else min(t[-1], t_release + tail_s)
    n1 = int(np.floor((end - t_release) / dt + 1e-6))
    tg = t_release + np.arange(-n0, n1 + 1) * dt
    if tg[0] < t[0] - 1e-9: raise ValueError("the trace starts after the scored hold")
    return score_angle(tg, 180.0 + rest_model_deg + np.interp(tg, t, rig_deg), t_release - 0.1 * dt)


def _summ(prm, est):
    out = {k: (float(prm[k]) if prm and prm.get(k) is not None and np.isfinite(prm[k]) else np.nan) for k in PARAMS7 + ["A0_deg"]}
    out["pt7"] = est["pt7"] if est and est["pt7"] is not None else np.nan
    out["band"] = est["mas"] if est and est["interpretable"] else "refused"
    return out


def score(name, encoder_dir, phone_manifest=None, allow_provenance_mismatch=False, accept_session_lag_ms=None,
          tracking_limit_ms=None):
    d = os.path.join(SESS, name)
    planned = json.load(open(os.path.join(d, "session.json"), encoding="utf-8")).get("provenance")
    prov = provenance()
    stale = sorted(set(provenance_mismatch(planned, prov)) | set(provenance_mismatch(library_provenance(), prov)))
    if stale:
        why = (f"session {name} was planned under {planned or 'no recorded provenance'}, the library is "
               f"{library_provenance() or 'unrecorded'}, and the code is {prov}")
        if not allow_provenance_mismatch:
            raise SystemExit(f"refusing to score: {why}. Score it from the commit it was planned on, or pass "
                             f"--allow-provenance-mismatch to score anyway with the mismatch recorded.")
        print(f"WARNING: provenance mismatch ({', '.join(stale)}): {why}")
    key = pd.read_csv(os.path.join(d, "key.csv"))
    man = pd.read_csv(os.path.join(OUT, "scenario_manifest.csv")).set_index("sid")
    phones = []
    if phone_manifest:
        m = json.load(open(phone_manifest, encoding="utf-8")); base = os.path.dirname(phone_manifest)
        phones = [os.path.join(base, x["file"]) for x in sorted(m["trials"], key=lambda x: x["timestamp"])]
        if len(phones) != len(key):
            print(f"WARNING: {len(phones)} phone recordings for {len(key)} runs -- pairing by order is unsafe; phone columns skipped")
            phones = []
    sheet = pd.read_csv(os.path.join(d, "runsheet.csv"), dtype=str).set_index("run_id")
    # Pass 1: find every run and measure its release. Nothing is scored yet, because whether a lag is
    # acceptable is a question about the session (_session_lag), not about one run.
    rows, work = [], []
    for i, r in key.iterrows():
        rid, sid = r.run_id, r.sid
        row = dict(run_id=rid, sid=sid)
        # the log's real name, as the operator wrote it on the run sheet (StretchExperiment names its own)
        fname = sheet.loc[rid, "encoder_file"] if rid in sheet.index and isinstance(sheet.loc[rid, "encoder_file"], str) else f"{rid}.bin"
        encf = os.path.join(encoder_dir, fname.strip())
        if not os.path.exists(encf):
            row["status"] = "no encoder log"; rows.append(row); continue
        a = np.genfromtxt(os.path.join(d, "commands", rid + ".csv"), delimiter=",", names=True)
        e = read_labview_bin(encf)
        k0 = ilc.locate(a["setpoint_deg"], e["position_setpoint"], FS)
        n = min(len(a["t_s"]), len(e["position"]) - k0)
        pos = e["position"][k0:k0 + n]; t = a["t_s"][:n]; ph = a["phase"][:n].astype(int); sp = a["setpoint_deg"][:n]
        # the log from the command's start to its own end, on the command's time base: a late arm is still
        # moving after the command has finished, and that tail belongs to the delivered motion
        pos_all = e["position"][k0:]; t_all = t[0] + np.arange(len(pos_all)) / FS
        if sid == "R-chirp":
            fit = ilc.identify(sp, pos, FS); row.update(status="identified", **{f"plant_{k}": v for k, v in fit.items()}); rows.append(row); continue
        sw = ph == 2
        row["tracking_rmse_deg"] = float(np.sqrt(np.mean((pos[sw] - sp[sw]) ** 2)))
        t_rel = float(t[np.argmax(sw)]); t_hold = float(t[np.argmax(ph == 1)])
        lead = t_rel - t_hold + SCORE_LEAD_S; tail = float(t[-1]) - t_rel
        rest = float(man.loc[sid, "rest_model_deg"])
        row.update({f"int_{k}": man.loc[sid, k] if k in man.columns else np.nan for k in PARAMS7 + ["A0_deg", "pt7"]})
        row["int_band"] = man.loc[sid, "mas"] if isinstance(man.loc[sid, "mas"], str) else "refused"
        rel = _delivered_release(t, sp, t_all, pos_all, t_rel)
        row.update(cmd_release_s=t_rel, enc_release_s=rel["release_s"], enc_release_lag_ms=rel["lag_s"] * 1e3,
                   enc_release_resid_deg=rel["resid_deg"], enc_hold_rms_deg=rel["hold_rms_deg"], enc_rival_ratio=rel["rival_ratio"])
        rows.append(row)
        work.append(dict(i=i, row=row, rel=rel, e=e, k0=k0, t=t, sp=sp, sw=sw, t_all=t_all, pos_all=pos_all, t_rel=t_rel,
                         lead=lead, tail=tail, rest=rest))
    # The session's lag: over every run whose fit is usable. A fit flagged for its hold, its uniqueness or its
    # residual has no lag to contribute; one flagged only for its lag is exactly what is being judged here.
    S = _session_lag([w["rel"]["lag_s"] for w in work if w["rel"]["kind"] in ("", "lag")],
                     None if accept_session_lag_ms is None else accept_session_lag_ms / 1e3)
    track_max = TRACKING_TIME_ERR_MAX_MS if tracking_limit_ms is None else float(tracking_limit_ms)
    # Pass 2: score.
    for w in work:
        i, row, rel, e, k0, t, sp, sw, t_all, pos_all, t_rel, lead, tail, rest = (w[k] for k in (
            "i", "row", "rel", "e", "k0", "t", "sp", "sw", "t_all", "pos_all", "t_rel", "lead", "tail", "rest"))
        flag = _lag_verdict(rel["lag_s"], S) if rel["kind"] in ("", "lag") else rel["flag"]
        if flag:                                         # no fallback: an unscored run, said out loud
            row.update(status=f"release flagged: {flag}", delivered_band_ok=False)
            if phones: row["phone_status"] = "not scored: the encoder release is flagged"
            continue
        try:
            dlv = _summ(*_score_trace(t_all, pos_all, rest, rel["release_s"], lead, tail))
        except Exception as ex:                          # noqa: BLE001 -- one run's scoring error must not stop the session
            row.update(status=f"scoring error: {str(ex)[:90]}", delivered_band_ok=False)
            if phones: row["phone_status"] = "not scored: the encoder trace could not be scored"
            continue
        row.update({f"enc_{k}": v for k, v in dlv.items()})
        row["delivered_band_ok"] = row["enc_band"] == row["int_band"]
        row["tracking_aligned_rmse_deg"], row["tracking_time_err_ms"] = _tracking(t, sp, sw, t_all, pos_all, rel["lag_s"])
        status = "scored"
        if not row["tracking_time_err_ms"] <= track_max:
            # The score above is kept: it is an honest score of what the arm did. What the arm did was not the
            # command, so the run does not count as a delivery of its scenario, whatever band it landed in.
            status = (f"tracking flagged: tracking error {row['tracking_time_err_ms']:.1f} ms (rms {row['tracking_aligned_rmse_deg']:.2f} deg "
                      f"with the release lag removed) is over the limit of {track_max:g} ms: the arm did not follow this command")
            row["delivered_band_ok"] = False
        if phones:
            tp, _ap, wp = _phone_angle(phones[i]); te = np.arange(len(e["position"])) / FS
            # Three things can go wrong here and each is called what it is: the sync (pulse alignment), the
            # release (flagged, or it could not be computed), and the scoring.
            try:
                tq, ang, sg, lag, e_edges, bias = _align_phone(te, e["position"], k0 / FS, tp, wp)
                tq = tq - k0 / FS                        # onto the command's time base
            except Exception as ex:                      # noqa: BLE001 -- a refused sync is reported, not fatal
                row["phone_status"] = f"sync refused: {str(ex)[:90]}"; row["status"] = status; continue
            try:
                # The phone's release is measured on the phone's own trace. Its clock sits a fraction of a
                # sample off the encoder's after the pulse alignment, so the encoder's release is not the
                # phone's; measured here, the offset moves the release with the trace and is reported.
                prl = _delivered_release(t, sp, tq, ang, t_rel, enc_lag_s=rel["lag_s"])
            except Exception as ex:                      # noqa: BLE001
                row["phone_status"] = f"release error: {str(ex)[:90]}"; row["status"] = status; continue
            row.update(ph_release_s=prl["release_s"], ph_release_lag_ms=prl["lag_s"] * 1e3,
                       ph_release_resid_deg=prl["resid_deg"], ph_hold_rms_deg=prl["hold_rms_deg"], ph_rival_ratio=prl["rival_ratio"],
                       ph_minus_enc_lag_ms=(prl["lag_s"] - rel["lag_s"]) * 1e3, ph_expected_offset_ms=prl["expected_offset_s"] * 1e3)
            if prl["flag"]:
                row["phone_status"] = f"release flagged: {prl['flag']}"
            else:
                try:
                    # over the command's span, as the encoder is: scored to the end of its own recording the
                    # phone saw 0.9 s more of D-short than the encoder did and gave 0.142 against 0.446
                    ph_s = _summ(*_score_trace(tq, ang, rest, prl["release_s"], lead, tail))
                except Exception as ex:                  # noqa: BLE001
                    row["phone_status"] = f"scoring error: {str(ex)[:90]}"
                else:
                    row.update({f"ph_{k}": v for k, v in ph_s.items()})
                    row["phone_status"] = f"ok (sign {sg:+.0f}, lag {lag:.3f} s, bias {bias:+.3f} deg/s removed)"
        row["status"] = status
    rep = pd.DataFrame(rows)
    rep["provenance_mismatch"] = ",".join(stale)
    # the session's lag, what was concluded from it, and the override if one was used to score: on every row,
    # so that no copy of the report can lose them
    rep["session_lag_median_ms"] = S["median_s"] * 1e3
    rep["session_lag_runs"] = S["n"]
    rep["session_lag_finding"] = S["finding"]
    rep["session_lag_override_ms"] = S["override_s"] * 1e3
    rep["tracking_limit_ms"] = track_max                 # the limit this report was scored under (provisional)
    rep.to_csv(os.path.join(d, "report.csv"), index=False)
    _print(rep); return rep


# ── check-log ───────────────────────────────────────────────────────────────
def check_log(bin_path, player_csv, verbose=True):
    """Is this log usable for scoring the run that played player_csv? -> dict(ok, reason, ...)."""
    with open(player_csv) as fh:
        header = not fh.readline()[:1].lstrip("-").isdigit()       # a commands/<run>.csv works too
    sp = (np.genfromtxt(player_csv, delimiter=",", names=True)["setpoint_deg"] if header
          else np.loadtxt(player_csv, delimiter=",")[:, 1])
    e = read_labview_bin(bin_path); log_sp = np.asarray(e["position_setpoint"], float)
    fs = 1.0 / float(np.median(np.diff(e["time"]))) if "time" in e and len(e["time"]) > 1 else FS
    out = dict(ok=False, log_s=len(log_sp) / fs, file_s=len(sp) / FS, fs_hz=fs)
    span_f, span_l = float(np.ptp(sp)), float(np.ptp(log_sp))
    if span_l < 0.1 * span_f:
        out["reason"] = (f"position_setpoint barely moved ({span_l:.2f} of the file's {span_f:.1f} deg): the log ran "
                         "but did not see the Player's writes. Check the variable in DSM, or play inside a Stretch-mode hold")
    else:
        # a log that stopped early is shorter than the file, and locate would then truncate the file to fit
        # and misplace it; pad with the last logged value so the part that WAS recorded is found
        padded = np.concatenate([log_sp, np.full(len(sp), log_sp[-1])])
        k0 = ilc.locate(sp, padded, fs); n = min(len(sp), len(log_sp) - k0)
        seg = log_sp[k0:k0 + n]; ref = sp[:n]
        r = float(np.corrcoef(seg, ref)[0, 1]) if n > 10 and np.std(seg) > 0 else 0.0
        moving = np.flatnonzero(np.abs(np.diff(seg)) > 1e-9)
        period_ms = float(np.median(np.diff(moving)) * 1000.0 / fs) if len(moving) > 2 else float("nan")
        pos = np.asarray(e["position"], float)[k0:k0 + n]
        out.update(start_s=k0 / fs, coverage=n / len(sp), r=r, max_err_deg=float(np.max(np.abs(seg - ref))),
                   update_ms=period_ms, tracking_rmse_deg=float(np.sqrt(np.mean((pos - ref) ** 2))))
        if r < 0.99:
            out["reason"] = (f"position_setpoint moved but does not follow this file (r = {r:.3f}): StretchExperiment "
                             "logged its own profile, or a different file was played")
        elif out["coverage"] < 0.98:
            out["reason"] = f"the log stopped {(1 - out['coverage']) * out['file_s']:.1f} s before the file ended"
        else:
            out["ok"] = True
            out["reason"] = "usable: the log recorded the Player's setpoint for the whole file"
            if period_ms > 25:
                out["reason"] += f" (but it changed only every {period_ms:.0f} ms; the Player should write every ~10)"
    if verbose:
        print(f"log {os.path.basename(bin_path)}: {out['log_s']:.1f} s at {fs:.0f} Hz; file {os.path.basename(player_csv)}: {out['file_s']:.1f} s")
        if "r" in out:
            print(f"  found at {out['start_s']:.2f} s, coverage {out['coverage'] * 100:.0f}%, r {out['r']:.4f}, "
                  f"max |log - file| {out['max_err_deg']:.2f} deg, setpoint changes every {out['update_ms']:.1f} ms, "
                  f"tracking RMSE {out['tracking_rmse_deg']:.2f} deg")
        print(("PASS  " if out["ok"] else "FAIL  ") + out["reason"])
    return out


def release_flags(rep):
    """[(run_id, trace, reason)] for every trace whose delivered release was flagged and so was not scored."""
    out = []
    for _, r in rep.iterrows():
        for trace, col in (("encoder", "status"), ("phone", "phone_status")):
            v = r.get(col)
            if isinstance(v, str) and v.startswith("release flagged: "):
                out.append((r.run_id, trace, v[len("release flagged: "):]))
    return out


def tracking_flags(rep):
    """[(run_id, reason)] for every run whose tracking error was over the limit: scored, but not a delivery."""
    out = []
    for _, r in rep.iterrows():
        v = r.get("status")
        if isinstance(v, str) and v.startswith("tracking flagged: "): out.append((r.run_id, v[len("tracking flagged: "):]))
    return out


def _print(rep):
    st = rep.status.fillna("") if "status" in rep else pd.Series("", index=rep.index)
    sc = rep[st == "scored"]
    runs = rep[st.str.startswith(("scored", "release flagged", "tracking flagged", "scoring error"))]     # every run with a log
    print(f"\n{'run':5s} {'scenario':24s} {'track':>6s}  {'PT7 int':>7s} {'enc':>6s} {'phone':>6s}   band int/enc/phone")
    for _, r in sc.iterrows():
        print(f"{r.run_id:5s} {r.sid[:24]:24s} {r.get('tracking_rmse_deg', np.nan):6.2f}  {r.get('int_pt7', np.nan):7.3f} {r.get('enc_pt7', np.nan):6.3f} "
              f"{r.get('ph_pt7', np.nan):6.3f}   {r.get('int_band', '')}/{r.get('enc_band', '')}/{r.get('ph_band', '-')}")
    if len(runs):
        # Of ALL the runs, flagged ones included: "100 % of 1 run" above a list of eight flags is not a result.
        n = len(runs); ok = int(sc.delivered_band_ok.astype(bool).sum()) if len(sc) else 0
        n_rel = int(st.str.startswith("release flagged").sum()); n_trk = int(st.str.startswith("tracking flagged").sum())
        n_err = int(st.str.startswith("scoring error").sum())
        print(f"\ndelivered band == intended band: {ok} of {n} runs ({100.0 * ok / n:.0f}%); scored {len(sc)}, "
              f"release flagged {n_rel}, tracking flagged {n_trk}" + (f", scoring error {n_err}" if n_err else "")
              + (f";  median tracking RMSE {sc.tracking_rmse_deg.median():.2f} deg" if len(sc) else ""))
    if len(sc):
        if "ph_pt7" in sc:
            had = sc[sc.phone_status.notna()] if "phone_status" in sc else sc
            got = had[had.ph_pt7.notna()]; dd = (got.ph_pt7 - got.enc_pt7).abs()
            print(f"phone vs encoder |dPT7| median {dd.median():.3f}, max {dd.max():.3f}; band agreement "
                  f"{int((got.ph_band == got.enc_band).sum())} of {len(had)} phone traces ({100.0 * (got.ph_band == got.enc_band).sum() / max(len(had), 1):.0f}%), "
                  f"{len(had) - len(got)} of them not scored")
        elif "phone_status" in sc and sc.phone_status.notna().any():
            print(f"phone: none of {int(sc.phone_status.notna().sum())} traces scored")
        lg = sc.enc_release_lag_ms
        msg = f"delivered release - commanded release: encoder median {lg.median():.1f} ms ({lg.min():.1f} to {lg.max():.1f})"
        if "ph_minus_enc_lag_ms" in sc and sc.ph_minus_enc_lag_ms.notna().any():
            pd_ = sc.ph_minus_enc_lag_ms.dropna()
            msg += f"; phone - encoder median {pd_.median():+.1f} ms ({pd_.min():+.1f} to {pd_.max():+.1f})"
        print(msg)
    flagged = release_flags(rep)
    finding = rep.session_lag_finding.iloc[0] if "session_lag_finding" in rep and len(rep) else ""
    if isinstance(finding, str) and finding:             # said ONCE: it is about the session, not about a run
        print("\n" + finding)
    session_wide = [f for f in flagged if f[2].startswith("session lag finding")]
    if session_wide:
        print(f"  not scored for this reason: {len(session_wide)} run(s) ({', '.join(f[0] for f in session_wide)})")
    flagged = [f for f in flagged if f not in session_wide]
    if flagged:
        print(f"\nRELEASE FLAGGED on {len(flagged)} trace(s); these were NOT scored (flagged runs are counted in the totals above):")
        for rid, trace, why in flagged: print(f"  {rid} {trace}: {why}")
    tracked = tracking_flags(rep)
    if tracked:
        print(f"\nTRACKING FLAGGED on {len(tracked)} run(s); the arm did not follow the command, so these are NOT deliveries of "
              f"their scenarios (counted in the totals above; their scores are in report.csv):")
        for rid, why in tracked: print(f"  {rid}: {why}")
    idn = rep[rep.get("status", "") == "identified"] if "status" in rep else rep.iloc[0:0]
    for _, r in idn.iterrows():
        print(f"plant identified from {r.run_id}: wn {r.plant_wn_hz:.2f} Hz, zeta {r.plant_zeta:.3f}, delay {r.plant_delay_ms:.1f} ms (fit R2 {r.plant_r2:.3f})")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("plan"); a.add_argument("--name", required=True); a.add_argument("--sids"); a.add_argument("--groups")
    a.add_argument("--reps", type=int, default=1); a.add_argument("--repeat"); a.add_argument("--seed", type=int, default=7)
    a.add_argument("--chirp", action="store_true"); a.add_argument("--no-blind", action="store_true")
    b = sub.add_parser("score"); b.add_argument("--name", required=True); b.add_argument("--encoder-dir", required=True); b.add_argument("--phone-manifest")
    b.add_argument("--allow-provenance-mismatch", action="store_true")
    b.add_argument("--accept-session-lag-ms", type=float, default=None, metavar="MS",
                   help="score a session whose median release lag is outside the plausible range, when that median is within "
                        f"{SESSION_LAG_TOL_S * 1e3:.0f} ms of MS (the value the session lag finding printed). Recorded in report.csv.")
    b.add_argument("--tracking-limit-ms", type=float, default=None, metavar="MS",
                   help=f"flag a run whose tracking error is over MS (default {TRACKING_TIME_ERR_MAX_MS:g}, provisional). "
                        "The limit used is written to report.csv.")
    c = sub.add_parser("check-log"); c.add_argument("--bin", required=True); c.add_argument("--player", required=True)
    x = ap.parse_args(argv)
    if x.cmd == "check-log":
        sys.exit(0 if check_log(x.bin, x.player)["ok"] else 1)
    if x.cmd == "plan":
        plan(x.name, sids=x.sids.split(",") if x.sids else None, groups=x.groups.split(",") if x.groups else None,
             reps=x.reps, repeat=x.repeat, seed=x.seed, chirp=x.chirp, blind=not x.no_blind)
    else:
        rep = score(x.name, x.encoder_dir, x.phone_manifest, x.allow_provenance_mismatch,
                    accept_session_lag_ms=x.accept_session_lag_ms, tracking_limit_ms=x.tracking_limit_ms)
        if release_flags(rep) or tracking_flags(rep):
            sys.exit(1)


if __name__ == "__main__":
    main()
