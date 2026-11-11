"""Whole-trial synthetic phone recordings, and what the app makes of them.

    python -m neuromech.phone_trial [--sids A-P0,A-P2,...] [--defects all|none|name,name] [--ideal]
                                    [--out DIR] [--seed N]

A recording is the whole trial the capture screen sees, not just the swing:

    rest -> lift -> STILL HOLD -> release -> swing -> quiet tail

because the app finds the release itself, takes its zero pose and its gyro bias from the hold, and ends
the trial itself after five still seconds. Hand it a bare swing and none of that runs.

The swing is any model trace (neuromech/out/traces/<sid>.npz, built by `python -m neuromech.build`) or the
built-in damped pendulum. The phone is a rigid body on the shank: gyro = the body's angular rate, accel =
gravity in the sensor frame plus the lever-arm acceleration of a phone strapped below the knee, sampled at
~60 Hz with jitter, bias and noise, in a mounting that is not axis-aligned. It is written in the app's own
raw export format (.jsonl), next to a .truth.json holding what was planted: where the release is, what
the production scorer says about the true motion, and what the app is expected to report.

`run_app` replays a recording through the app's worker and the wasm that ships
(webapp/scripts/replay-trial.mjs); `check` compares the result with the truth.

Good trials ask: is the hold accepted, is the release found where it was planted, does the trial end
itself, are the scored parameters right. Each DEFECT is a good trial with one thing wrong, and asks
whether the app says so:

    fast_lift        lifted briskly (86 deg/s) after resting still: the lift itself looks like a release
    early_release    released before the hold had lasted long enough to be trusted
    moved_in_hold    handled during the hold, released before a fresh still second
    hold_drift       the leg sags 9 deg through the hold (the app's limit is 5)
    lateral_shake    the shank rolls and swings sideways during the swing
    short_tail       recording stopped 2 s into the quiet tail
    no_tail          recording stopped while the leg was still swinging

What it cannot test: a real sensor's errors, soft-tissue wobble, and the browser's event delivery.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from neuromech import build as B  # noqa: E402

FS_SIM = 1000.0
G = 9.81
REPLAY_JS = os.path.join(ROOT, "webapp", "scripts", "replay-trial.mjs")

# The app's own gates, mirrored so a recording can be built to sit on the right side of each. Every one is
# pinned to the Rust constant by neuromech/tests/test_phone_trial.py.
GUARD_RAD_S = 0.3          # stillness.rs ZERO_CAPTURE_GUARD_RAD_S: below this a sample is calm
CAPTURE_RAD_S = 1.0        # calibration.rs FLEX_CAPTURE_THRESHOLD: a trusted release crosses this
CALM_WINDOW_S = 0.95       # 0.95 * stillness.rs GYRO_BIAS_WINDOW_S: calm needed before a release
MAX_HOLD_DRIFT_DEG = 5.0   # session.rs MAX_HOLD_DRIFT_DEG
SETTLE_TARGET_S = 5.0      # session.rs SETTLE_TARGET_S
LATERAL_THRESHOLD = 1.0    # flex_axis.rs DEFAULT_THRESHOLD

LIFT_PEAK_RAD_S = 0.6      # a good trial's lift: above the guard (it is handling), well below a release
DEFECTS = ("fast_lift", "early_release", "moved_in_hold", "hold_drift", "lateral_shake", "short_tail", "no_tail")

# How far the app may sit from the truth on a good trial. The parameter units are the designer's
# (Designer.cs `Unit`): one unit is the size of error that program treats as a miss.
TOL = {"R2n": 0.1, "N": 0.5, "f": 0.05, "area_ratio": 0.02, "phi_max_ratio": 0.05,
       "omega_max_n": 0.5, "omega_min_n": 0.5, "A0_deg": 2.0, "pt": 0.02}
RELEASE_TOL_S = 0.15
APP_KEY = {"R2n": "r2n", "N": "n", "f": "f", "area_ratio": "area_ratio", "phi_max_ratio": "phi_max_ratio",
           "omega_max_n": "omega_max_n", "omega_min_n": "omega_min_n", "A0_deg": "a0_deg"}


@dataclass
class Phone:
    """The sensor and how it is strapped on."""
    fs_hz: float = 60.0                       # iOS Safari DeviceMotion rate
    jitter_ms: float = 1.5
    mount_deg: tuple = (6.0, -4.0, 9.0)       # sensor tilt away from "x = knee axis, z = up at the hold"
    lever_m: float = 0.25                     # knee to phone, along the shank
    gyro_bias: tuple = (0.0015, -0.0010, 0.0020)
    gyro_noise: float = 0.003                 # rad/s per sample
    accel_offset: tuple = (0.010, 0.005, -0.008)
    accel_noise: float = 0.02                 # m/s^2 per sample

    @staticmethod
    def ideal() -> "Phone":
        """No noise, bias, jitter or lever arm, axis-aligned: isolates the pipeline from the sensor."""
        return Phone(jitter_ms=0.0, mount_deg=(0.0, 0.0, 0.0), lever_m=0.0, gyro_bias=(0, 0, 0),
                     gyro_noise=0.0, accel_offset=(0, 0, 0), accel_noise=0.0)


@dataclass
class Trial:
    name: str
    t_s: np.ndarray            # sample times, seconds from the first sample
    accel: np.ndarray          # (n, 3) m/s^2, sensor frame
    gyro: np.ndarray           # (n, 3) rad/s, sensor frame
    truth: dict = field(default_factory=dict)


# ───────────────────────────────────────────── swings ─────────────────────────────────────────────

def pendulum(freq_hz=0.9, zeta=0.08, depth_deg=55.0, duration=14.0):
    """A released lower leg as a damped pendulum: (t, theta_deg), theta 0 at release, negative = flexed."""
    t = np.arange(0, duration, 1 / FS_SIM)
    wn = 2 * math.pi * freq_hz; wd = wn * math.sqrt(1 - zeta ** 2)
    env = np.exp(-zeta * wn * t)
    return t, -depth_deg * (1 - env * (np.cos(wd * t) + zeta * wn / wd * np.sin(wd * t)))


def scenario(sid: str):
    """(t, theta_deg) of a built scenario, from release. Needs `python -m neuromech.build` to have run."""
    p = os.path.join(B.OUT, "traces", f"{sid}.npz")
    if not os.path.exists(p):
        raise SystemExit(f"no trace for {sid} at {p}: run `python -m neuromech.build` first")
    d = np.load(p)
    return d["t"], d["theta"]


# ─────────────────────────────────────────── the recording ───────────────────────────────────────────

def _minjerk(a, b, secs):
    u = np.arange(int(round(secs * FS_SIM))) / max(int(round(secs * FS_SIM)) - 1, 1)
    return a + (b - a) * (10 * u ** 3 - 15 * u ** 4 + 6 * u ** 5)


def _rot(rotvec):
    """Rotation matrix of a rotation vector (Rodrigues)."""
    v = np.asarray(rotvec, float); a = np.linalg.norm(v)
    if a < 1e-15:
        return np.eye(3)
    k = v / a; K = np.array([[0, -k[2], k[1]], [k[2], 0, -k[0]], [-k[1], k[0], 0]])
    return np.eye(3) + math.sin(a) * K + (1 - math.cos(a)) * K @ K


def _mount(deg):
    rx, ry, rz = np.radians(deg)
    return _rot([0, 0, rz]) @ _rot([0, ry, 0]) @ _rot([rx, 0, 0])


def _burst(t, t0, secs, peak, hz):
    """A zero-net-rotation wobble: a Hann-windowed sine, `peak` rad/s, over [t0, t0 + secs]."""
    u = (t - t0) / secs
    on = (u >= 0) & (u <= 1)
    return np.where(on, peak * np.sin(2 * math.pi * hz * (t - t0)) * np.sin(math.pi * np.clip(u, 0, 1)) ** 2, 0.0)


def synthesize(t_swing, theta_swing, *, name="trial", phone: Phone | None = None, defects=(), seed=0,
               rest_s=1.5, lift=True, hold_s=3.0, tail_s=7.0) -> Trial:
    """The whole trial around one swing. `theta_swing` is degrees from the release pose (negative = flexed),
    starting at 0 with the leg at rest in the examiner's hand. `lift=False` starts the recording already in
    the hold."""
    phone = phone or Phone()
    defects = tuple(defects)
    unknown = set(defects) - set(DEFECTS)
    if unknown:
        raise ValueError(f"unknown defect(s) {sorted(unknown)}; known: {', '.join(DEFECTS)}")
    rng = np.random.default_rng(seed)

    if "early_release" in defects:
        hold_s = 0.3                                   # with a brisker lift below: under 0.95 s of calm in all
    drift0 = 9.0 if "hold_drift" in defects else 0.0   # the hold starts this far above the release pose

    sw = np.interp(np.arange(0, t_swing[-1], 1 / FS_SIM), t_swing, theta_swing)
    rest = float(sw[-1])
    n_hold = int(round(hold_s * FS_SIM))
    # A min-jerk lift peaks at 1.875 * distance / duration; its duration sets how brisk it is.
    peak = 1.5 if "fast_lift" in defects else 0.9 if "early_release" in defects else LIFT_PEAK_RAD_S
    lift_s = 1.875 * math.radians(abs(drift0 - rest)) / peak
    parts = [np.full(int(round(rest_s * FS_SIM)), rest), _minjerk(rest, drift0, lift_s),
             np.linspace(drift0, 0.0, n_hold, endpoint=False), sw, np.full(int(round(tail_s * FS_SIM)), rest)]
    if not lift:
        parts[0], parts[1] = parts[0][:0], parts[1][:0]
    theta = np.concatenate(parts)
    t = np.arange(len(theta)) / FS_SIM
    t_hold = (len(parts[0]) + len(parts[1])) / FS_SIM
    t_rel = t_hold + hold_s
    t_swing_end = t_rel + len(sw) / FS_SIM

    # Angular rate in a frame where x is the knee axis, y runs down the shank and z is up at the hold.
    w = np.zeros((len(t), 3))
    w[:, 0] = np.radians(np.gradient(theta, 1 / FS_SIM))
    if "moved_in_hold" in defects:
        # Handling that ends 0.5 s before the release: too big and too long to be sensor noise
        # (calibration.rs NOISE_EXCURSION_*), too close to the release for a fresh calm window.
        t0 = t_rel - 0.5 - 0.6
        w[:, 0] += _burst(t, t0, 0.6, 0.8, 3.0)
        w[:, 1] += _burst(t, t0, 0.6, 0.5, 4.0)
    if "lateral_shake" in defects:
        # The shank rolls about its own long axis and swings sideways, fading over the first swings.
        env = np.where(t >= t_rel, np.exp(-(t - t_rel) / 2.0), 0.0)
        w[:, 1] += 2.0 * env * np.sin(2 * math.pi * 2.3 * (t - t_rel))
        w[:, 2] += 0.8 * env * np.sin(2 * math.pi * 1.7 * (t - t_rel))

    if "early_release" in defects or "moved_in_hold" in defects:
        # These two are only defects if the app's OWN rule is broken: less than its calm window below its
        # guard before the release. A slow lift's last moments are below the guard and count as hold.
        busy = np.nonzero((np.linalg.norm(w, axis=1) >= GUARD_RAD_S) & (t < t_rel))[0]
        calm_s = t_rel - (t[busy[-1]] if len(busy) else 0.0)
        if calm_s > CALM_WINDOW_S - 0.2:
            raise ValueError(f"{defects}: {calm_s:.2f} s of calm before the release is a hold the app accepts")

    # "Up" as the body sees it. A world-fixed vector seen from a rotating body obeys u' = -w x u; stepped
    # with the exact rotation over each 1 ms at the midpoint rate.
    th0 = math.radians(theta[0])
    up = np.empty((len(t), 3)); up[0] = [0.0, math.sin(th0), math.cos(th0)]
    wm = 0.5 * (w[1:] + w[:-1]) / FS_SIM
    for i in range(len(t) - 1):
        up[i + 1] = _rot(-wm[i]) @ up[i]
    alpha = np.gradient(w, 1 / FS_SIM, axis=0)
    p = np.array([0.0, phone.lever_m, 0.0])
    f = G * up + np.cross(alpha, p) + np.cross(w, np.cross(w, p))    # specific force at the phone

    # When the recording stops. A real one stops itself; these two are stopped by hand, too soon.
    calm_from = t[np.nonzero(np.linalg.norm(w, axis=1) >= GUARD_RAD_S)[0][-1]]
    t_end = t[-1]
    if "short_tail" in defects:
        t_end = calm_from + 2.0
    if "no_tail" in defects:
        t_end = calm_from - 0.5

    ts, tk = [], 0.0
    while tk <= t_end:
        ts.append(tk); tk += 1 / phone.fs_hz
    ts = np.array(ts)
    if phone.jitter_ms:
        ts[1:] += np.clip(rng.normal(0, phone.jitter_ms / 1000.0, len(ts) - 1), -0.4 / phone.fs_hz, 0.4 / phone.fs_hz)
    ts = np.clip(ts, 0, t[-1])

    M = _mount(phone.mount_deg)
    at = lambda a: np.column_stack([np.interp(ts, t, a[:, k]) for k in range(3)])  # noqa: E731
    w_true = at(w)
    gyro = w_true @ M.T + np.asarray(phone.gyro_bias, float) + rng.normal(0, 1, (len(ts), 3)) * phone.gyro_noise
    accel = at(f) @ M.T + np.asarray(phone.accel_offset, float) + rng.normal(0, 1, (len(ts), 3)) * phone.accel_noise

    # What the production scorer says about the motion that was planted, release given -- on two footings.
    # `expected`: the true angle sampled, ticked and smoothed exactly as the app does it, then scored. This
    # is what a perfect sensor fusion would hand the app's scorer, so it is what the app is judged against.
    # `kinematic`: the same motion on neuromech's 100 Hz grid with no smoothing (build.score_angle), which is
    # the footing the scenario manifest's numbers are on. The gap between the two is the app's smoothing.
    knee = 180.0 + theta
    keep = t <= ts[-1]
    prm_k, est_k = B.score_angle(t[keep], knee[keep], t_rel) if keep.sum() > 400 else (None, {})
    tick_t, tick_a = app_ticks(ts, 180.0 + np.interp(ts, t, theta))
    prm, est = None, {}
    if len(tick_t) > 41:
        prm = B.P.compute_pt_params(tick_t[1:], tick_a[1:], release_idx=int(np.searchsorted(tick_t[1:], t_rel)), detrend=False)
        est = B.P.mas_estimate(prm) if prm else {}
    # The app takes its zero pose on the first sample at or past the capture rate, not at the release, so
    # the leg has already moved by then. How far: between the previous sample and this one.
    k_fire = next((k for k in range(len(ts)) if ts[k] >= t_rel and np.linalg.norm(w_true[k]) >= CAPTURE_RAD_S), None)
    zero_lag = None if k_fire is None else [abs(float(np.interp(ts[k_fire - 1], t, theta))), abs(float(np.interp(ts[k_fire], t, theta)))]
    fast = np.linalg.norm(w_true, axis=1) >= LATERAL_THRESHOLD
    perp = np.linalg.norm(w_true[fast][:, 1:], axis=1)
    lateral = float(perp.sum() / np.linalg.norm(w_true[fast], axis=1).sum()) if fast.any() else None

    settles = bool(ts[-1] - calm_from >= SETTLE_TARGET_S + 0.2)
    truth = {
        "name": name, "defects": list(defects), "seed": seed, "phone": phone.__dict__,
        "samples": int(len(ts)), "duration_s": float(ts[-1]),
        "hold_start_s": float(t_hold), "hold_s": float(hold_s),
        "release_t_s": float(t_rel), "release_sample": int(np.searchsorted(ts, t_rel)),
        "swing_end_s": float(t_swing_end), "calm_from_s": float(calm_from),
        "hold_drift_deg": drift0, "lateral_fraction": lateral, "zero_pose_lag_deg": zero_lag,
        "expected_params": None if not prm else {k: float(prm[k]) for k in APP_KEY},
        "expected_pt": None if not est else est.get("pt7"),
        "expected_interpretable": bool(est.get("interpretable", False)),
        "kinematic_params": None if not prm_k else {k: float(prm_k[k]) for k in APP_KEY},
        "kinematic_pt": None if not est_k else est_k.get("pt7"),
        "expect": _expect(defects, settles),
    }
    if not prm:
        truth["expect"]["scored"] = False          # the scorer refuses this motion itself; so must the app
    return Trial(name=name, t_s=ts, accel=accel, gyro=gyro, truth=truth)


def app_ticks(t_s, angle, tick_s=0.05, alpha=0.3):
    """The app's fixed-cadence stage (resample.rs): a 50 ms zero-order hold carrying the state as of just
    before each tick, so tick 0 is NaN, then an EMA seeded by the first finite value."""
    n = int((t_s[-1] - t_s[0]) / tick_s) + 1
    ticks = t_s[0] + np.arange(n) * tick_s
    k = np.searchsorted(t_s, ticks, side="left") - 1          # last sample strictly before the tick
    held = np.where(k >= 0, angle[np.clip(k, 0, None)], np.nan)
    out = np.full(n, np.nan); ema = None
    for i, v in enumerate(held):
        if np.isnan(v):
            ema = None; continue
        ema = v if ema is None else alpha * v + (1 - alpha) * ema
        out[i] = ema
    return ticks - t_s[0], out


def _expect(defects, settles):
    """What the app should do with this recording, by its own documented rules -- written from the rules in
    session.rs / calibration.rs / capture-feedback.js, never from what a run happened to print."""
    e = {"ready_at_release": True, "scored": True, "release_found": True, "hold_drift_flagged": False,
         "lateral_reported": False, "ends_itself": settles,
         "capture_quality": "clean" if settles else None}
    if "early_release" in defects or "moved_in_hold" in defects:
        # No trusted hold behind the release, so no zero pose: the trial must not come back with a score.
        e.update(ready_at_release=False, scored=False, release_found=False, ends_itself=False, capture_quality=None)
    if "hold_drift" in defects:
        e["hold_drift_flagged"] = True
    if "lateral_shake" in defects:
        e["lateral_reported"] = True
    if "short_tail" in defects:
        e.update(ends_itself=False, capture_quality="short")
    if "no_tail" in defects:
        e.update(ends_itself=False, capture_quality="unsettled")
    return e


def write(trial: Trial, out_dir: str) -> str:
    """<name>.jsonl in the app's raw export format (export_jsonl.rs) and <name>.truth.json beside it."""
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, trial.name + ".jsonl")
    t0 = 1.0                                           # event.timeStamp: seconds since the page loaded
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        for ti, a, g in zip(trial.t_s, trial.accel, trial.gyro):
            tt = t0 + float(ti); ms = int(round(tt * 1000.0))
            for sensor, v in (("accel", a), ("gyro", g)):          # accel first: the fusion reads it
                fh.write(json.dumps({"t": tt, "role": "distal", "sensor": sensor,
                                     "v": [float(x) for x in v], "phone_ts_ms": ms}, separators=(",", ":")) + "\n")
    with open(os.path.join(out_dir, trial.name + ".truth.json"), "w", encoding="utf-8") as fh:
        json.dump(trial.truth, fh, indent=1)
    return path


# ───────────────────────────────────────────── the app ─────────────────────────────────────────────

def node_available() -> bool:
    return shutil.which("node") is not None


def run_app(jsonl_path: str, trajectory: bool = False, wasm: str | None = None) -> dict:
    """Replay a recording through the app's worker and its wasm core; what the app would show and store.
    `wasm` swaps in another build of the same core (a candidate fix)."""
    cmd = ["node", REPLAY_JS, jsonl_path] + (["--trajectory"] if trajectory else []) + (["--wasm", wasm] if wasm else [])
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
    if r.returncode != 0:
        raise RuntimeError(f"replay-trial failed on {jsonl_path}:\n{r.stdout}\n{r.stderr}")
    return json.loads(r.stdout.strip().splitlines()[-1])


def _state_at(app: dict, t: float) -> str:
    s = "moving"
    for tr in app["transitions"]:
        if tr["t"] <= t:
            s = tr["state"]
    return s


def check(truth: dict, app: dict) -> list[dict]:
    """One row per question asked of this recording: {check, ok, got, want}."""
    e = truth["expect"]; rows = []
    add = lambda name, ok, got, want: rows.append({"check": name, "ok": bool(ok), "got": got, "want": want})  # noqa: E731
    t_rel = truth["release_t_s"]

    # the hold, as the screen showed it on the last sample before the release
    before = _state_at(app, t_rel - 1e-6)
    add("hold accepted at release", (before == "ready") == e["ready_at_release"], before,
        "ready" if e["ready_at_release"] else "not ready")

    live = next((tr["t"] for tr in app["transitions"] if tr["state"] == "released"), None)
    if e["release_found"]:
        add("live release", live is not None and -0.02 <= live - t_rel <= RELEASE_TOL_S,
            None if live is None else round(live - t_rel, 3), f"0..{RELEASE_TOL_S} s after planted")
    else:
        add("no release trusted", live is None, "none" if live is None else f"released at {live - t_rel:+.2f} s", "none")

    add("scored" if e["scored"] else "refused", app["scored"] == e["scored"],
        "scored" if app["scored"] else f"refused ({app['error']})", "scored" if e["scored"] else "refused")

    if e["scored"] and app["scored"]:
        sr = app["scored_release_t"]
        add("scored release", sr is not None and abs(sr - t_rel) <= RELEASE_TOL_S, round(sr - t_rel, 3), f"within {RELEASE_TOL_S} s")
        exp = truth["expected_params"] or {}
        clean = not truth["defects"]
        for k, ak in APP_KEY.items():
            got = app["params"].get(ak)
            if got is None or k not in exp:
                continue
            d = got - exp[k]
            if k == "A0_deg" and truth.get("zero_pose_lag_deg"):
                lo, hi = truth["zero_pose_lag_deg"]
                rows.append({"check": "leg fallen at zero pose", "ok": True, "info": True, "got": f"{lo:.1f}..{hi:.1f} deg",
                             "want": f"reported (the hold then reads low and rest high: A0 low by {2 * lo:.1f}..{2 * hi:.1f})"})
            # only a good trial owes the truth its parameters; on a defective one they are reported, not judged
            add(f"{k}", abs(d) <= TOL[k] or not clean, round(d, 4), f"|d| <= {TOL[k]}" if clean else "reported")
        if truth["expected_pt"] is not None and app.get("pt_score") and app["pt_score"].get("score") is not None:
            d = app["pt_score"]["score"] - truth["expected_pt"]
            add("PT score", abs(d) <= TOL["pt"] or not clean, round(d, 4), f"|d| <= {TOL['pt']}" if clean else "reported")
            if truth.get("kinematic_pt") is not None:
                rows.append({"check": "PT vs 100 Hz unsmoothed", "ok": True, "info": True,
                             "got": round(app["pt_score"]["score"] - truth["kinematic_pt"], 4),
                             "want": f"reported (app {app['pt_score']['score']:.3f}, motion {truth['kinematic_pt']:.3f})"})

    if e["hold_drift_flagged"]:
        # The 5 deg limit only means something if the hold was NOT accepted at the release.
        add("hold drift caught", before != "ready", f"{before}, max drift shown {app['max_hold_drift_deg']:.1f} deg",
            f"hold refused (drift {truth['hold_drift_deg']:.0f} > {MAX_HOLD_DRIFT_DEG:.0f} deg)")

    if app["scored"]:
        lm = app.get("lateral_motion"); got = None if not lm else lm.get("lateral_fraction")
        want = truth["lateral_fraction"]
        if e["lateral_reported"]:
            add("lateral motion", got is not None and want is not None and abs(got - want) <= 0.05,
                None if got is None else round(got, 3), f"{want:.3f} +/- 0.05")
        else:
            add("no lateral motion", got is not None and got <= 0.05, None if got is None else round(got, 3), "<= 0.05")

    add("ends itself" if e["ends_itself"] else "does not end itself", (app["ended"] == "settled") == e["ends_itself"],
        app["ended"], "settled" if e["ends_itself"] else "end_of_file")
    if e["capture_quality"]:
        add("capture quality", app["capture_quality"] == e["capture_quality"], app["capture_quality"], e["capture_quality"])
    return rows


def generate_and_check(name, t_swing, theta_swing, out_dir, wasm=None, **kw):
    tr = synthesize(t_swing, theta_swing, name=name, **kw)
    path = write(tr, out_dir)
    app = run_app(path, wasm=wasm)
    with open(os.path.join(out_dir, name + ".app.json"), "w", encoding="utf-8") as fh:
        json.dump(app, fh, indent=1)
    return tr, app, check(tr.truth, app)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sids", default="pendulum", help="comma-separated scenario ids, or 'pendulum' (built in)")
    ap.add_argument("--defects", default="none", help="none | all | comma-separated names; each makes its own trial")
    ap.add_argument("--ideal", action="store_true", help="a perfect sensor: no noise, bias, jitter or lever arm")
    ap.add_argument("--out", default=os.path.join(B.OUT, "phone_trials"))
    ap.add_argument("--seed", type=int, default=11)
    ap.add_argument("--quiet", action="store_true", help="print failures only")
    ap.add_argument("--wasm", help="replay through this build of the core instead of webapp/src/wasm")
    x = ap.parse_args(argv)
    if not node_available():
        raise SystemExit("node is not on PATH: the recordings can be written but not replayed through the app")
    defects = [] if x.defects == "none" else list(DEFECTS) if x.defects == "all" else x.defects.split(",")
    phone = Phone.ideal() if x.ideal else Phone()
    n_bad = 0
    for sid in x.sids.split(","):
        t, th = pendulum() if sid == "pendulum" else scenario(sid)
        for d in [None] + defects:
            name = sid + ("" if d is None else "__" + d) + ("__ideal" if x.ideal else "")
            tr, app, rows = generate_and_check(name, t, th, x.out, wasm=x.wasm, phone=phone,
                                                defects=() if d is None else (d,), seed=x.seed)
            judged = [r for r in rows if not r.get("info")]
            bad = [r for r in judged if not r["ok"]]; n_bad += len(bad)
            print(f"{'FAIL' if bad else 'ok  '} {name:<40} {len(judged) - len(bad)}/{len(judged)} checks")
            for r in rows if not x.quiet else bad:
                mark = "i" if r.get("info") else " " if r["ok"] else "x"
                print(f"       {mark} {r['check']:<26} got {r['got']!s:<34} want {r['want']}")
    print(f"\nrecordings, truth and app output in {x.out}")
    return 1 if n_bad else 0


if __name__ == "__main__":
    sys.exit(main())
