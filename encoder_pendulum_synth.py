"""Synthetic free-pendulum fixture trials with a matching encoder log.

For testing `encoder_pendulum_validation.py` with known truth. Nothing here is a
measurement. The fixture of
docs/plans/2026-11-18-001-plan-free-pendulum-encoder-weight-validation.md is modelled as

    rest (hanging) -> slow lift to the peg -> STILL HOLD -> release -> ring-down -> rest

with a nonlinear gravity pendulum of equivalent length `l_eq_m` and viscous damping,

    phi'' = -(g / L_eq) sin(phi) - 2 zeta w_n phi',      w_n = sqrt(g / L_eq),

so that the accelerometer of a phone clamped at radius r reads what a real one would:

    along the arm      -g cos(phi) - r phi'^2             (gravity + centripetal)
    across the arm      g sin(phi) + r phi''              (gravity + tangential)
                      = g sin(phi) (1 - r / L_eq) on the undamped swing

The gyro reads phi' whatever r is. The phone model (rate, jitter, mount, bias, noise)
and the raw-export writer are `neuromech.phone_trial`'s; only the geometry is new,
because that synthesiser holds the shank horizontal and this fixture hangs vertical.

The encoder is written in the motor rig's LabVIEW layout (big-endian float64,
interleaved, position in degrees first; `labview_bin_reader.read_labview_bin`), sampled
at 1 kHz, quantised to the encoder's counts, with the errors a test wants to plant:

    scale        encoder reads scale * true angle (a counts-per-revolution mistake)
    offset_deg   a zero error
    noise_deg    white noise per sample, before quantisation
    start_s      the encoder log starts this long AFTER the phone recording
                 (phone_time = encoder_time + start_s: the clock offset to recover)

and on the phone: `gyro_scale` (a gyro gain error, which the angle inherits).
`end_tap` adds the run sheet's raw-mode knuckle tap near the end of the record.
"""
from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass, field

import numpy as np

G = 9.81
FS_SIM = 1000.0
LIFT_PEAK_RAD_S = 0.6     # as neuromech.phone_trial: handling, well under the app's release rate


@dataclass
class EncoderErrors:
    scale: float = 1.0
    offset_deg: float = 0.0
    noise_deg: float = 0.0
    start_s: float = 0.0
    counts_per_rev: int = 8000        # 0.045 deg per count, the plan's minimum resolution
    n_channels: int = 7               # the motor rig's layout


@dataclass
class FixtureTrial:
    name: str
    t_s: np.ndarray                   # phone sample times from the first phone sample
    accel: np.ndarray                 # (n, 3) m/s^2
    gyro: np.ndarray                  # (n, 3) rad/s
    t_true: np.ndarray                # 1 kHz truth, phone clock
    phi_true_deg: np.ndarray          # angle from vertical
    enc_t: np.ndarray                 # encoder clock (starts at 0)
    enc_deg: np.ndarray               # what the encoder logged
    truth: dict = field(default_factory=dict)


def _minjerk(a, b, secs):
    n = max(int(round(secs * FS_SIM)), 2)
    u = np.arange(n) / (n - 1)
    return a + (b - a) * (10 * u ** 3 - 15 * u ** 4 + 6 * u ** 5)


def swing(release_deg, l_eq_m=0.37, zeta=0.03, duration_s=60.0, settle_deg=0.05):
    """Angle from vertical (deg) at 1 kHz from a release at rest; RK4. Ends when the
    envelope is under `settle_deg`, or at `duration_s`."""
    wn2 = G / l_eq_m
    c = 2.0 * zeta * math.sqrt(wn2)

    def acc(p, v):
        return -wn2 * math.sin(p) - c * v

    h = 1.0 / FS_SIM
    n = int(round(duration_s * FS_SIM))
    out = np.empty(n)
    p, v = math.radians(release_deg), 0.0
    stop = n
    e_stop = wn2 * (1.0 - math.cos(math.radians(settle_deg)))
    for i in range(n):
        out[i] = p
        k1p, k1v = v, acc(p, v)
        k2p, k2v = v + 0.5 * h * k1v, acc(p + 0.5 * h * k1p, v + 0.5 * h * k1v)
        k3p, k3v = v + 0.5 * h * k2v, acc(p + 0.5 * h * k2p, v + 0.5 * h * k2v)
        k4p, k4v = v + h * k3v, acc(p + h * k3p, v + h * k3v)
        p += h / 6.0 * (k1p + 2 * k2p + 2 * k3p + k4p)
        v += h / 6.0 * (k1v + 2 * k2v + 2 * k3v + k4v)
        if 0.5 * v * v + wn2 * (1.0 - math.cos(p)) < e_stop:
            stop = i + 1
            break
    return np.degrees(out[:stop])


def synthesize(release_deg=40.0, *, name="trial", phone=None, radius_m=0.30, l_eq_m=0.37,
               zeta=0.03, gyro_scale=1.0, encoder: EncoderErrors | None = None, seed=0,
               rest_s=1.5, hold_s=5.0, tail_s=7.0, max_swing_s=60.0, end_tap=False,
               knock_deg=0.0, knock_s=0.025, knock_back=True, knock_after_s=2.5,
               gyro_blip_rad_s=0.0, gyro_blip_before_s=3.0, gyro_blip_n=1) -> FixtureTrial:
    """One whole fixture trial and its encoder log. `phone` is a
    `neuromech.phone_trial.Phone` (its `lever_m` is ignored: `radius_m` is the lever).

    `knock_deg` plants a knock or a bounce off the peg in the hold: after `knock_after_s`
    of still hold the arm drops `knock_deg` in `knock_s` and (with `knock_back`) returns as
    fast; the `hold_s` hold and the release follow from wherever that leaves it.

    `gyro_blip_rad_s` plants motion ONLY THE PHONE saw: `gyro_blip_n` consecutive gyro
    samples, `gyro_blip_before_s` before the release, read that much more about the swing
    axis while the arm (and so the encoder) stays still. A phone shifting in its clamp, or
    a glitch of the sensor."""
    from neuromech import phone_trial as PT          # slow import; only when synthesising

    phone = phone or PT.Phone()
    enc = encoder or EncoderErrors()
    rng = np.random.default_rng(seed)

    lift_s = 1.875 * math.radians(abs(release_deg)) / LIFT_PEAK_RAD_S
    level, knock = float(release_deg), []
    if knock_deg:
        knock = [np.full(int(round(knock_after_s * FS_SIM)), level), _minjerk(level, level - knock_deg, knock_s)]
        if knock_back:
            knock.append(_minjerk(level - knock_deg, level, knock_s))
        else:
            level -= knock_deg
    sw = swing(level, l_eq_m=l_eq_m, zeta=zeta, duration_s=max_swing_s)
    parts = [np.zeros(int(round(rest_s * FS_SIM))), _minjerk(0.0, release_deg, lift_s), *knock,
             np.full(int(round(hold_s * FS_SIM)), level), sw,
             np.full(int(round(tail_s * FS_SIM)), float(sw[-1]))]
    phi = np.concatenate(parts)
    t = np.arange(len(phi)) / FS_SIM
    t_rel = sum(len(p) for p in parts[:-2]) / FS_SIM
    t_knock = (len(parts[0]) + len(parts[1]) + len(knock[0])) / FS_SIM if knock else None
    if end_tap:
        # The run sheet's end tap (raw mode): a knuckle on the still arm, 2 s before the
        # stop. A 20 Hz ring of 0.1 deg that both sensors see.
        u = t - (t[-1] - 2.0)
        phi = phi + np.where(u > 0, 0.1 * np.exp(-np.clip(u, 0, None) / 0.05) * np.sin(2 * math.pi * 20.0 * u), 0.0)

    p = np.radians(phi)
    w = np.gradient(p, 1.0 / FS_SIM)
    a = np.gradient(w, 1.0 / FS_SIM)
    # Frame: x the pivot axis, y along the arm away from the pivot, z across the arm.
    # World up seen from the arm is [0, -cos(phi), sin(phi)]; the phone is at [0, r, 0].
    f = np.column_stack([np.zeros_like(p),
                         -G * np.cos(p) - radius_m * w ** 2,
                         G * np.sin(p) + radius_m * a])
    wv = np.column_stack([w, np.zeros_like(w), np.zeros_like(w)])

    ts = np.arange(0.0, t[-1], 1.0 / phone.fs_hz)
    if phone.jitter_ms:
        ts[1:] += np.clip(rng.normal(0, phone.jitter_ms / 1000.0, len(ts) - 1),
                          -0.4 / phone.fs_hz, 0.4 / phone.fs_hz)
    ts = np.clip(ts, 0, t[-1])
    M = PT._mount(phone.mount_deg)

    def at(arr):
        return np.column_stack([np.interp(ts, t, arr[:, k]) for k in range(3)])

    gyro = (gyro_scale * at(wv)) @ M.T + np.asarray(phone.gyro_bias, float) \
        + rng.normal(0, 1, (len(ts), 3)) * phone.gyro_noise
    accel = at(f) @ M.T + np.asarray(phone.accel_offset, float) \
        + rng.normal(0, 1, (len(ts), 3)) * phone.accel_noise
    t_blip = None
    if gyro_blip_rad_s:
        i = int(np.searchsorted(ts, t_rel - gyro_blip_before_s))
        gyro[i:i + int(gyro_blip_n)] += np.array([gyro_blip_rad_s, 0.0, 0.0]) @ M.T
        t_blip = float(ts[i])

    # The encoder: its own clock, starting `start_s` after the phone's first sample.
    te = np.arange(0.0, t[-1] - enc.start_s, 1.0 / FS_SIM)
    te = te[te + enc.start_s >= 0.0]
    true_at_enc = np.interp(te + enc.start_s, t, phi)
    logged = enc.scale * true_at_enc + enc.offset_deg + rng.normal(0, 1, len(te)) * enc.noise_deg
    step = 360.0 / enc.counts_per_rev
    logged = np.round(logged / step) * step

    wn = math.sqrt(G / l_eq_m)
    truth = {
        "name": name, "release_deg": float(level), "release_t_phone_s": float(t_rel),
        "knock_t_phone_s": t_knock, "knock_deg": float(knock_deg),
        "gyro_blip_t_phone_s": t_blip, "gyro_blip_rad_s": float(gyro_blip_rad_s),
        "release_t_encoder_s": float(t_rel - enc.start_s), "clock_offset_s": float(enc.start_s),
        "radius_m": float(radius_m), "l_eq_m": float(l_eq_m), "zeta": float(zeta),
        "f_small_angle_hz": wn * math.sqrt(1 - zeta ** 2) / (2 * math.pi),
        "gyro_scale": float(gyro_scale), "encoder": dict(enc.__dict__),
        "phone": {k: (list(v) if isinstance(v, tuple) else v) for k, v in phone.__dict__.items()},
        "seed": seed, "synthetic": True,
    }
    return FixtureTrial(name=name, t_s=ts, accel=accel, gyro=gyro, t_true=t, phi_true_deg=phi,
                        enc_t=te, enc_deg=logged, truth=truth)


def write_encoder_bin(path, enc_deg, n_channels=7, fs_hz=FS_SIM):
    """The motor rig's LabVIEW layout: big-endian float64, interleaved by sample,
    position (deg) then velocity (deg/s), the remaining channels zero."""
    pos = np.asarray(enc_deg, float)
    data = np.zeros((len(pos), n_channels))
    data[:, 0] = pos
    if n_channels > 1:
        data[:, 1] = np.gradient(pos, 1.0 / fs_hz)
    data.astype(">f8").tofile(path)
    return path


def write(trial: FixtureTrial, phone_dir: str, encoder_path: str) -> str:
    """<phone_dir>/<name>.jsonl in the app's raw export format (and <name>.truth.json),
    and the encoder log at `encoder_path`. Returns the .jsonl path."""
    from neuromech import phone_trial as PT

    os.makedirs(os.path.dirname(os.path.abspath(encoder_path)), exist_ok=True)
    as_pt = PT.Trial(name=trial.name, t_s=trial.t_s, accel=trial.accel, gyro=trial.gyro,
                     truth=trial.truth)
    path = PT.write(as_pt, phone_dir)
    write_encoder_bin(encoder_path, trial.enc_deg, trial.truth["encoder"]["n_channels"])
    return path


# ── a whole synthetic session in the bench folder layout ───────────────────────
WEIGHTS = {"W0": 0.0, "W1": 1.0, "W2": 3.0, "W3": 4.5, "W4": 6.0}
# Small-angle L_eq and damping per weight: a heavier arm rings longer (plan 3.4).
# Invented for the synthetic session; the real values come from the pilot.
WEIGHT_MODEL = {"W0": (0.32, 0.045), "W1": (0.36, 0.035), "W2": (0.37, 0.030),
                "W3": (0.37, 0.027), "W4": (0.38, 0.025)}


def write_session(out_dir, session="S1", platform="iphone", conditions=("W0", "W2", "W4"),
                  angles=(20, 40, 60), radius_m=0.30, seed=20261118, encoder_errors=None,
                  gyro_scale=1.0, mmdd="1126"):
    """A synthetic session folder as the run sheet's "Save" section lays it out:
    phone/ (manifest + .jsonl), encoder/ (pf<MMDD>_NNN.bin), rig_log.csv, session.json.
    One Quick-Test-style swing per condition x angle. Returns the truths by trial id."""
    from neuromech import phone_trial as PT

    phone_dir = os.path.join(out_dir, "phone")
    enc_dir = os.path.join(out_dir, "encoder")
    os.makedirs(phone_dir, exist_ok=True)
    os.makedirs(enc_dir, exist_ok=True)
    fs = 60.0 if platform == "iphone" else 100.0
    rng = np.random.default_rng(seed)
    rows, trials, truths = [], [], {}
    seq = 0
    for cond in conditions:
        l_eq, zeta = WEIGHT_MODEL[cond]
        for ang in angles:
            seq += 1
            tid = f"PF-{session}-{cond}-A{ang}-Q-{seq:02d}"
            enc = encoder_errors or EncoderErrors(noise_deg=0.02)
            enc = EncoderErrors(**{**enc.__dict__, "start_s": float(rng.uniform(0.3, 1.2))})
            tr = synthesize(ang, name=f"synthetic-{session}-trial{seq}", phone=PT.Phone(fs_hz=fs),
                            radius_m=radius_m, l_eq_m=l_eq, zeta=zeta, gyro_scale=gyro_scale,
                            encoder=enc, seed=int(rng.integers(1 << 30)))
            write(tr, phone_dir, os.path.join(enc_dir, f"pf{mmdd}_{seq:03d}.bin"))
            trials.append({"file": tr.name + ".jsonl", "timestamp": 1795000000000 + seq * 90000,
                           "quick_test": True, "research": False})
            rows.append(dict(seq=seq, trial_id=tid, encoder_trial=seq, condition=cond,
                             release_angle_deg=ang, mode="quicktest", app_result="scored",
                             stopped_by="auto", notes="SYNTHETIC"))
            truths[tid] = tr.truth
    with open(os.path.join(phone_dir, f"synthetic-{session}-manifest.json"), "w", encoding="utf-8") as fh:
        json.dump({"schema": "pendulastic/session-export/v6", "synthetic": True,
                   "algorithm_version": "synthetic", "trials": trials}, fh, indent=1)
    with open(os.path.join(out_dir, "rig_log.csv"), "w", encoding="utf-8", newline="") as fh:
        cols = ["seq", "trial_id", "encoder_trial", "condition", "release_angle_deg", "mode",
                "app_result", "stopped_by", "notes"]
        fh.write(",".join(cols) + "\n")
        for r in rows:
            fh.write(",".join(str(r[c]) for c in cols) + "\n")
    with open(os.path.join(out_dir, "session.json"), "w", encoding="utf-8") as fh:
        json.dump({
            "synthetic": True, "date": "2026-11-26", "operator": "synthetic", "session": session,
            "phone": {"platform": platform, "model": f"synthetic {platform}", "os": "n/a", "browser": "n/a"},
            "app_build": "synthetic", "app_algorithm": "synthetic",
            "phone_radius_m": radius_m, "phone_radius_measured": True,
            "phone_orientation": "synthetic mount (6, -4, 9) deg",
            "encoder": {"model": "synthetic", "counts_per_rev": 8000, "format": "labview_bin",
                        "n_channels": 7, "sample_rate_hz": 1000.0, "position_units": "deg",
                        "zero_deg": 0.0, "file_prefix": f"pf{mmdd}"},
            "pivot_tilt_deg": 0.0,
            "weights": {k: {"mass_kg": v, "radius_m": 0.38} for k, v in WEIGHTS.items()},
        }, fh, indent=1)
    return truths
