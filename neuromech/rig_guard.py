"""The motor playback safety guard, on the analysis PC: the Python twin of labview/player/RigGuard.cs.

    python -m neuromech.rig_guard FILE_OR_DIR [...] [--limit 40] [--index OUT.csv]
    python -m neuromech.rig_guard --library [--index OUT.csv]       # neuromech/out/setpoints/*.csv

Both players on the rig PC (play_setpoints.ps1 via PLAY.bat, and the Pendulum Designer) run RigGuard.cs;
this module applies the same rules to the same 10 ms stream with the same plant, so a bundle or a session
plan can say before the stick leaves which files the rig will refuse and why. test_rig_guard.py feeds the
same fixture files to all three and requires the same verdict and the same predicted peak.

Rules (owner decisions 2026-11-17; HARD_LIMIT_DEG and MAX_STEP_DEG are compiled into the players and an
operator can only lower the limit):
  * refuse: fewer than two rows, a header, a non-number, NaN/Inf, time not starting at 0, first angle not 0,
    time not strictly increasing, any |angle| > limit, any change > 6 deg between consecutive 10 ms writes
    (600 deg/s) on the stream the player sends, or a predicted delivered peak > limit;
  * never clip: a file over the limit is refused whole (a live Designer design is instead offered a smaller
    rig amplitude scale, played only on an explicit Yes).

Predicted delivered peak: the player's 10 ms staircase (the file read at k * 10 ms, then 2 s held at the end)
through the identified closed-loop rig response, y'' = wn^2 (u - y) - 2 zeta wn y', 6.35 Hz / zeta 0.23
(2026-10-26, 23 SSR logs: gain 1.26 at 3 Hz, ~2.0 at 5 Hz, 2.2 at resonance), and through detuned copies:
wn x0.9 and x1.1 (the drift the LABVIEW spec tolerates before re-identifying) and zeta 0.18. The largest
|y| over the six is the prediction. It is a model, not a guarantee: a rig that moved further than that from
the identified plant is outside it, and the E-stop remains the protection.
"""
from __future__ import annotations

import argparse
import csv
import glob
import math
import os
import re
import sys

import numpy as np

HARD_LIMIT_DEG = 40.0
PERIOD_MS = 10
MAX_STEP_DEG = 6.0
MAX_SPEED_DPS = 600.0
SCALE_MARGIN_DEG = 1.0
PLANT_WN_HZ, PLANT_ZETA = 6.35, 0.23
VARIANT_WN_HZ = (5.715, 6.35, 6.985)
VARIANT_ZETA = (0.23, 0.18)
SIM_STEP_S = 0.001
TAIL_S = 2.0
START_TOL, START_ANGLE_TOL, EPS = 1e-6, 0.01, 1e-9
SCALABLE = ("peak", "speed", "predicted")

_NUM = re.compile(r"^[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?$")
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out")


def _nonfinite_word(s):
    return s.strip().lower().lstrip("+-") in ("nan", "inf", "infinity")


def _f(v, dec):
    return f"{v:.{dec}f}"


def _clip(s):
    s = s.strip()
    return s[:40] + "..." if len(s) > 40 else s


def _fail(kind, reason):
    return dict(ok=False, kind=kind, reason=reason, reasons=[reason], limit_deg=float("nan"), scale_needed=None,
                raw_peak_deg=float("nan"), raw_peak_t=float("nan"), max_step_deg=float("nan"), max_step_t=float("nan"),
                predicted_peak_deg=float("nan"), predicted_peak_t=float("nan"), scale_to_fit=None)


def check_limit(limit_deg):
    """None if the operator limit is usable, else why not (it may only lower HARD_LIMIT_DEG)."""
    if not isinstance(limit_deg, (int, float)) or not math.isfinite(limit_deg) or limit_deg <= 0:
        return f"the angle limit must be a positive number of degrees (got {limit_deg})"
    if limit_deg > HARD_LIMIT_DEG + EPS:
        return (f"the angle limit {limit_deg:.1f} deg is above the hard limit of {HARD_LIMIT_DEG:.0f} deg built into the "
                "players; it can only be lowered")
    return None


def parse_lines(lines):
    """(t, sp, None) or (None, None, refusal dict): the players' parser (RigGuard.Parse)."""
    t, sp, row = [], [], 0
    for line in lines:
        s = line.strip()
        if not s:
            continue
        row += 1
        p = s.split(",")
        a_s = p[0].strip()
        ok_a = bool(_NUM.match(a_s))
        if row == 1 and not ok_a and not _nonfinite_word(a_s):
            return None, None, _fail("header", f"the file starts with a header row ('{_clip(s)}'); player files have none."
                                     " Use the session's player\\ file, not commands\\")
        if len(p) < 2:
            return None, None, _fail("parse", f"row {row} does not have two columns: '{_clip(s)}'")
        b_s = p[1].strip(); ok_b = bool(_NUM.match(b_s))
        if ok_a: a = float(a_s)
        elif _nonfinite_word(a_s): a = float("nan")
        else: return None, None, _fail("parse", f"row {row}: the time '{_clip(a_s)}' is not a number")
        if ok_b: b = float(b_s)
        elif _nonfinite_word(b_s): b = float("nan")
        else: return None, None, _fail("parse", f"row {row}: the angle '{_clip(b_s)}' is not a number")
        t.append(a); sp.append(b)
    return np.array(t, float), np.array(sp, float), None


def at(t, sp, sec):
    """The file's angle at elapsed time(s) sec, exactly as RigGuard.At computes it."""
    t = np.asarray(t, float); sp = np.asarray(sp, float); sec = np.atleast_1d(np.asarray(sec, float))
    n = len(t)
    j = np.searchsorted(t, sec, side="left")
    jc = np.clip(j, 0, n - 1)
    exact = (j < n) & (t[jc] == sec)
    jl = np.clip(j - 1, 0, n - 2)
    f = (sec - t[jl]) / (t[jl + 1] - t[jl])
    out = sp[jl] + f * (sp[jl + 1] - sp[jl])
    out = np.where(exact, sp[jc], out)
    out = np.where(sec <= 0, sp[0], out)
    out = np.where(sec >= t[-1], sp[-1], out)
    return out


def stream(t, sp):
    """The writes an on-time player sends: the file at k * 10 ms until the clock reaches the end, then the last angle."""
    dur = float(t[-1])
    k = np.arange(int(math.floor(dur * 1000.0 / PERIOD_MS)) + 2)
    e = k * PERIOD_MS / 1000.0
    e = e[e < dur]
    return np.concatenate([at(t, sp, e), [float(sp[-1])]])


def _discrete(wn_hz, zeta, dt=SIM_STEP_S):
    w = 2 * math.pi * wn_hz; sg = zeta * w; wd = w * math.sqrt(1 - zeta * zeta)
    ex, c, s = math.exp(-sg * dt), math.cos(wd * dt), math.sin(wd * dt)
    a11 = ex * (c + sg / wd * s); a12 = ex * s / wd
    a21 = -ex * w * w / wd * s; a22 = ex * (c - sg / wd * s)
    return np.array([[a11, a12], [a21, a22]]), np.array([1 - a11, -a21])


def sim_response(st, wn_hz=PLANT_WN_HZ, zeta=PLANT_ZETA):
    """Arm angle (1 kHz, starting 1 ms after the first write) for a 10 ms write stream held 2 s at the end."""
    from scipy.signal import lfilter, ss2tf
    A, B = _discrete(wn_hz, zeta)
    sub = int(round(PERIOD_MS / 1000.0 / SIM_STEP_S))
    u = np.concatenate([np.repeat(np.asarray(st, float), sub), np.full(int(round(TAIL_S / SIM_STEP_S)), float(st[-1]))])
    b, a = ss2tf(A, B.reshape(2, 1), np.array([[1.0, 0.0]]), np.array([[0.0]]))
    y = lfilter(b[0], a, np.concatenate([u, [0.0]]))       # y[n] = position after n steps
    return y[1:]


def predicted_peak(st):
    """(peak_deg, at_s): the largest |arm angle| over the nominal plant and its detuned copies."""
    best, best_t = 0.0, 0.0
    for wn in VARIANT_WN_HZ:
        for z in VARIANT_ZETA:
            y = np.abs(sim_response(st, wn, z)); i = int(np.argmax(y))
            if y[i] > best:
                best, best_t = float(y[i]), (i + 1) * SIM_STEP_S
    return best, best_t


def scale_to_fit(raw_peak, max_step, pred, limit_deg):
    s = 1.0
    if raw_peak > 0: s = min(s, limit_deg / raw_peak)
    if max_step > 0: s = min(s, MAX_STEP_DEG / max_step)
    if pred > 0: s = min(s, (limit_deg - SCALE_MARGIN_DEG) / pred)
    return math.floor(s * 100.0 + 1e-9) / 100.0


def check_trajectory(t, sp, limit_deg=HARD_LIMIT_DEG):
    """Every load-time rule the players apply, on a player-format stream (t seconds, sp setpoint deg).

    Returns a dict: ok, kind (first failing rule; '' when ok), reason (the players' message for it),
    reasons (every failing amplitude/speed/prediction rule, or the one structural refusal),
    predicted_peak_deg / predicted_peak_t, raw_peak_deg / raw_peak_t, max_step_deg / max_step_t,
    limit_deg, and scale_needed (the same value is also under scale_to_fit): the largest 0.01-step factor for the WHOLE stream that passes with the
    predicted peak <= limit - 1 deg (None when the stream passes, or when no scale can fix it). Scaling a
    whole file scales its 5 deg sync pulses too.
    """
    lim = check_limit(limit_deg)
    if lim:
        return _fail("operator_limit", lim)
    t = np.asarray(t, float); sp = np.asarray(sp, float)
    n = len(t)
    if n < 2 or len(sp) != n:
        return _fail("rows", "the file has fewer than two rows")
    for i in range(n):
        if not math.isfinite(t[i]):
            return _fail("nonfinite", f"row {i + 1}: the time is {t[i]} (not a number)")
        if not math.isfinite(sp[i]):
            return _fail("nonfinite", f"at t = {t[i]:.3f} s (row {i + 1}) the angle is {sp[i]}")
    if abs(t[0]) > START_TOL:
        return _fail("start_time", f"the file's time must start at 0 (it starts at {t[0]} s)")
    if abs(sp[0]) > START_ANGLE_TOL:
        return _fail("start_angle", f"the file's first angle must be 0 (it is {sp[0]:.2f} deg); every player file starts at 0")
    bad = np.flatnonzero(np.diff(t) <= 0)
    if len(bad):
        i = int(bad[0]) + 1
        return _fail("time_order", f"time does not increase at row {i + 1} (t = {t[i]} s after {t[i - 1]} s)")
    a = np.abs(sp); ip = int(np.argmax(a))
    raw_peak, raw_peak_t = float(a[ip]), float(t[ip])
    over = np.flatnonzero(a > limit_deg + EPS)
    st = stream(t, sp)
    d = np.abs(np.diff(np.concatenate([[0.0], st])))
    k = int(np.argmax(d))
    max_step, max_step_t = float(d[k]), min(k * PERIOD_MS / 1000.0, float(t[-1]))
    pred, pred_t = predicted_peak(st)
    reasons = []
    if len(over):
        i = int(over[0])
        reasons.append(("peak", f"at t = {t[i]:.3f} s the angle is {sp[i]:.2f} deg (peak {raw_peak:.2f} deg at {raw_peak_t:.3f} s); "
                                f"the limit is {limit_deg:.1f} deg"))
    if max_step > MAX_STEP_DEG + EPS:
        reasons.append(("speed", f"at t = {max_step_t:.2f} s the setpoint changes by {max_step:.2f} deg in one 10 ms write "
                                 f"({max_step * 1000.0 / PERIOD_MS:.0f} deg/s); the limit is {MAX_STEP_DEG:.0f} deg per write "
                                 f"({MAX_SPEED_DPS:.0f} deg/s)"))
    if pred > limit_deg + EPS:
        reasons.append(("predicted", f"the arm is predicted to reach {pred:.2f} deg near t = {pred_t:.2f} s (the commanded peak is "
                                     f"{raw_peak:.2f} deg; the rig overshoots fast content, up to 2x at 5 Hz); the limit is {limit_deg:.1f} deg"))
    ok = not reasons
    scale = None if ok else scale_to_fit(raw_peak, max_step, pred, limit_deg)
    return dict(scale_needed=scale,ok=ok, kind="" if ok else reasons[0][0], reason="" if ok else reasons[0][1], reasons=[r for _, r in reasons],
                limit_deg=float(limit_deg), raw_peak_deg=raw_peak, raw_peak_t=raw_peak_t, max_step_deg=max_step,
                max_step_t=max_step_t, predicted_peak_deg=pred, predicted_peak_t=pred_t,
                scale_to_fit=scale)


def check_lines(lines, limit_deg=HARD_LIMIT_DEG):
    lim = check_limit(limit_deg)
    if lim:
        return _fail("operator_limit", lim)
    t, sp, refusal = parse_lines(lines)
    return refusal if refusal else check_trajectory(t, sp, limit_deg)


def read_any(path):
    """(t, sp) from a player file (no header) or a commands/setpoints file (header with t_s, setpoint_deg)."""
    with open(path, newline="") as fh:
        lines = fh.read().splitlines()
    first = next((l for l in lines if l.strip()), "")
    if first and not _NUM.match(first.split(",")[0].strip()) and not _nonfinite_word(first.split(",")[0]):
        rows = list(csv.DictReader(lines))
        return (np.array([float(r["t_s"]) for r in rows]), np.array([float(r["setpoint_deg"]) for r in rows]))
    t, sp, refusal = parse_lines(lines)
    if refusal:
        raise ValueError(refusal["reason"])
    return t, sp


def check_file(path, limit_deg=HARD_LIMIT_DEG):
    """The player's verdict on a player file, read exactly as the player reads it."""
    with open(path, newline="") as fh:
        return check_lines(fh.read().splitlines(), limit_deg)


INDEX_COLS = ["file", "ok", "kind", "raw_peak_deg", "max_step_deg", "predicted_peak_deg", "scale_to_fit", "reason"]


def index_rows(paths, limit_deg=HARD_LIMIT_DEG, library=False):
    rows = []
    for p in paths:
        if library:
            t, sp = read_any(p); r = check_trajectory(t, sp, limit_deg)
        else:
            r = check_file(p, limit_deg)
        rows.append(dict(file=os.path.basename(p), ok=r["ok"], kind=r["kind"],
                         raw_peak_deg=round(r["raw_peak_deg"], 3), max_step_deg=round(r["max_step_deg"], 3),
                         predicted_peak_deg=round(r["predicted_peak_deg"], 3),
                         scale_to_fit="" if r["scale_to_fit"] is None else r["scale_to_fit"],
                         reason=" | ".join(r["reasons"])))
    return rows


def write_index(rows, path):
    """A sidecar listing each file's verdict and predicted peak (LF line endings)."""
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=INDEX_COLS, lineterminator="\n"); w.writeheader(); w.writerows(rows)
    return path


def library_paths():
    return sorted(glob.glob(os.path.join(OUT, "setpoints", "*.csv")))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("paths", nargs="*"); ap.add_argument("--limit", type=float, default=HARD_LIMIT_DEG)
    ap.add_argument("--library", action="store_true"); ap.add_argument("--index")
    x = ap.parse_args(argv)
    paths = []
    for p in x.paths:
        paths += sorted(glob.glob(os.path.join(p, "*.csv"))) if os.path.isdir(p) else [p]
    if x.library:
        paths += library_paths()
    if not paths:
        ap.error("no files")
    rows = index_rows(paths, x.limit, library=x.library and not x.paths)
    for r in rows:
        print(f"{'PLAYABLE' if r['ok'] else 'REFUSED '}  {r['file']:32s} peak {r['raw_peak_deg']:7.2f}  step {r['max_step_deg']:5.2f}  "
              f"predicted {r['predicted_peak_deg']:7.2f}  {r['reason']}")
    n_ok = sum(r["ok"] for r in rows)
    print(f"{n_ok} of {len(rows)} playable at +/-{x.limit:g} deg")
    if x.index:
        write_index(rows, x.index); print(f"index -> {x.index}")
    return 0 if n_ok == len(rows) else 1


if __name__ == "__main__":
    sys.exit(main())
