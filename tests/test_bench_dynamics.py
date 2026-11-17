"""bench_dynamics / bench_session_report on synthetic rig data with known answers.

A damped oscillation of known frequency and decay is written out exactly as the rig
and the phone app would write it -- a 1 kHz big-endian float64 LabVIEW .bin and a
~60 Hz phone JSONL whose gyro-y is the angle's derivative in rad/s -- and the
estimators must recover f and tau from both sides.
"""
import json
import os

import numpy as np
import pytest

import bench_dynamics as bd
import bench_session_report as bsr

F_HZ, TAU_S, A_DEG = 1.20, 2.50, 30.0


def ringdown(t, t_rel=3.0, f=F_HZ, tau=TAU_S, a=A_DEG, lift_s=1.0):
    """Fast Trial shape: rest, slow lift to +a, hold, release at t_rel, ring about 0."""
    x = np.zeros_like(t)
    up = (t >= t_rel - 2.0) & (t < t_rel - 2.0 + lift_s)
    x[up] = a * (t[up] - (t_rel - 2.0)) / lift_s
    x[(t >= t_rel - 2.0 + lift_s) & (t < t_rel)] = a
    r = t >= t_rel
    tr = t[r] - t_rel
    x[r] = a * np.exp(-tr / tau) * np.cos(2 * np.pi * f * tr)
    return x


def staircase(t, a=37.5, dwell=3.0, ramp=0.25):
    """0 -> +a -> -a -> +a -> 0 with linear ramps and a small damped overshoot."""
    levels = [0.0, a, -a, a, 0.0]
    x = np.zeros_like(t)
    for k in range(1, len(levels)):
        t0 = 1.0 + (k - 1) * dwell
        frm, to = levels[k - 1], levels[k]
        m = t >= t0
        prog = np.clip((t[m] - t0) / ramp, 0, 1)
        settle = t[m] - t0 - ramp
        ring = np.where(settle > 0, 0.05 * np.exp(-settle / 0.15) * np.sin(2 * np.pi * 4 * settle), 0)
        x[m] = frm + (to - frm) * (prog + ring)
    return x


def write_bin(path, t, pos):
    vel = np.gradient(pos, t)
    data = np.zeros((len(t), 7))
    data[:, 0], data[:, 1], data[:, 2] = pos, vel, pos
    data.astype(">f8").tofile(path)


def write_phone(path, t, ang_deg, sign=1.0):
    w = sign * np.gradient(np.radians(ang_deg), t)
    with open(path, "w", encoding="utf-8") as fh:
        for ti, wi in zip(t, w):
            fh.write(json.dumps({"t": 100.0 + ti, "role": "distal", "sensor": "accel",
                                 "v": [0.0, 0.0, -9.81]}) + "\n")
            fh.write(json.dumps({"t": 100.0 + ti, "role": "distal", "sensor": "gyro",
                                 "v": [0.0, float(wi), 0.0]}) + "\n")


def test_ringdown_recovers_known_f_and_tau_on_encoder_rate():
    t = np.arange(0, 12, 0.001)
    m = bd.oscillation_metrics(t, ringdown(t))
    assert m["f"] == pytest.approx(F_HZ, rel=0.01)
    assert m["tau"] == pytest.approx(TAU_S, rel=0.05)
    assert m["N"] > 3


def test_ringdown_recovers_known_f_and_tau_at_phone_rate():
    # ~60 Hz with jitter: sub-sample extremum refinement keeps f within 1 %
    rng = np.random.default_rng(0)
    t = np.cumsum(rng.uniform(0.014, 0.019, 700))
    t -= t[0]
    m = bd.oscillation_metrics(t, ringdown(t))
    assert m["f"] == pytest.approx(F_HZ, rel=0.01)
    assert m["tau"] == pytest.approx(TAU_S, rel=0.05)


def test_phone_gyro_integration_reconstructs_angle(tmp_path):
    t = np.arange(0, 10, 1 / 60)
    ang = ringdown(t)
    p = tmp_path / "trial1.jsonl"
    write_phone(p, t, ang)
    tp, ap, _ = bd.phone_gyro(str(p))
    assert len(tp) == len(t)
    assert np.max(np.abs(ap - ang)) < 1.0


def test_step_metrics_on_staircase():
    t = np.arange(0, 14, 0.001)
    x = staircase(t)
    steps = bd.find_steps(t, x, np.gradient(x, t))
    assert len(steps) == 4
    s = steps[1]
    m = bd.step_metrics(t, x, s["frm"], s["to"], s["t_start"])
    assert m["step"] == pytest.approx(-75.0, abs=1.0)
    assert m["overshoot"] == pytest.approx(5.0 * 0.85, abs=1.5)    # 5 % ring, first lobe


def test_trial_num_reads_the_trial_field_not_the_prefix():
    assert bd.trial_num("ck_9_14_ptStretchTrial07Slow.bin") == 7
    assert bd.trial_num("bench1118StretchTrial115Fast.bin") == 115


def test_pairing_is_monotone_and_skips_an_extra_encoder(tmp_path):
    """Encoder trial 1 has no phone capture; the three phone trials must pair 2,3,4."""
    te = np.arange(0, 12, 0.001)
    freqs = [0.6, 0.9, 1.3, 1.8]
    encs = []
    for k, f in enumerate(freqs, 1):
        p = tmp_path / f"rigStretchTrial{k:02d}Fast.bin"
        write_bin(p, te, ringdown(te, f=f))
        encs.append(str(p))
    tp = np.arange(0, 14, 1 / 60)
    phones = []
    for f in freqs[1:]:
        ang = ringdown(tp, t_rel=4.0, f=f)               # phone started ~1 s earlier
        phones.append((f, (tp, ang, None)))
    out = bd.pair_by_order(phones, encs, window=2)
    assert [bd.trial_num(out[f][0]) for f in freqs[1:]] == [2, 3, 4]
    assert all(out[f][1] > 0.9 for f in freqs[1:])
    assert out[freqs[1]][2] == pytest.approx(1.0, abs=0.05)   # lag: phone = enc + 1 s


def test_session_report_end_to_end(tmp_path):
    enc_dir, ph_dir, out = tmp_path / "enc", tmp_path / "phone", tmp_path / "out"
    enc_dir.mkdir()
    ph_dir.mkdir()
    te = np.arange(0, 14, 0.001)
    tp = np.arange(0, 15, 1 / 60)
    specs = [("ringdown", 0.7), ("ringdown", 1.2), ("ringdown", 1.7), ("step", None)]
    trials = []
    for k, (blk, f) in enumerate(specs, 1):
        enc = ringdown(te, f=f) if blk == "ringdown" else staircase(te)
        ph = ringdown(tp, t_rel=4.0, f=f) if blk == "ringdown" else staircase(tp - 1.0)
        write_bin(enc_dir / f"rigStretchTrial{k:02d}Fast.bin", te, enc)
        name = f"pendulastic-BENCH-20261118T090000-trial{5 - k}.jsonl"   # scrambled numbers
        write_phone(ph_dir / name, tp, ph, sign=-1.0)                    # flipped polarity
        trials.append({"file": name, "timestamp": 1_800_000_000_000 + 60_000 * k,
                       "algorithm_version": "0.3.0+test", "capture_quality": "clean",
                       "scorable": blk == "ringdown",
                       "params": {"f": 1.0, "n": 2} if blk == "ringdown" else None,
                       "research": blk == "step"})
    (ph_dir / "pendulastic-BENCH-20261118T090000-manifest.json").write_text(json.dumps(
        {"schema": "pendulastic/session-export/v6", "algorithm_version": "0.3.0+test",
         "trials": trials}), encoding="utf-8")
    log = tmp_path / "rig_log.csv"
    log.write_text("encoder_trial,block,P,step_speed\n1,ringdown,0.25,\n2,ringdown,1,\n"
                   "3,ringdown,2,\n4,step,,30\n", encoding="utf-8")

    rows, steps = bsr.analyze(str(ph_dir), str(enc_dir), str(out), rig_log=str(log))
    ring = [r for r in rows if r["block"] == "ringdown"]
    assert [r["enc_trial"] for r in rows] == [1, 2, 3, 4]
    assert [r["block"] for r in rows] == ["ringdown"] * 3 + ["step"]
    for r, (_, f) in zip(ring, specs):
        assert r["enc_f"] == pytest.approx(f, rel=0.01)
        assert r["phone_f"] == pytest.approx(f, rel=0.02)
        assert r["phone_tau"] == pytest.approx(TAU_S, rel=0.1)
    assert len(steps) == 4
    md = (out / "summary.md").read_text(encoding="utf-8")
    assert "0.3.0+test" in md and "| 4/4 | 3 | 0 |" in md
    for fig in ("fig_ringdown_overlay.png", "fig_f_scatter.png", "fig_decay_scatter.png",
                "fig_step_overlay.png"):
        assert os.path.getsize(out / fig) > 10_000
    # auto-classification agrees with the log
    assert [bsr.infer_block(bd.encoder(str(p))) for p in sorted(enc_dir.iterdir())] == \
        ["ringdown"] * 3 + ["step"]


# ── review fixes: pairing with a missing trial, session polarity, rig-log parsing ──
SWEEP_P = [0.25] * 3 + [0.5] * 3 + [1] * 3 + [2] * 3
SWEEP_F = {0.25: 0.5, 0.5: 0.7, 1: 1.0, 2: 1.4}
SWEEP_TAU = {0.25: 3.0, 0.5: 2.5, 1: 2.0, 2: 1.5}


def fast_trial(t, f, tau, a=30.0, t_lift=3.0, lift_s=0.6, hold=5.0):
    """Fast Trial: the lift/hold plateau is identical at every P; only the ring differs."""
    x = np.zeros_like(t)
    up = (t >= t_lift) & (t < t_lift + lift_s)
    x[up] = a * (t[up] - t_lift) / lift_s
    x[(t >= t_lift + lift_s) & (t < t_lift + lift_s + hold)] = a
    r = t >= t_lift + lift_s + hold
    tr = t[r] - (t_lift + lift_s + hold)
    x[r] = a * np.exp(-tr / tau) * np.cos(2 * np.pi * f * tr)
    return x


def _sweep(tmp_path, missing):
    """Write the 12 encoder files and the phone export with trial `missing` absent
    (an app ERROR: nothing saved). Returns (enc_dir, phone_dir, log_path)."""
    rng = np.random.default_rng(1)
    enc_dir, ph_dir = tmp_path / "enc", tmp_path / "phone"
    enc_dir.mkdir()
    ph_dir.mkdir()
    te = np.arange(0, 21, 0.001)
    trials, log = [], ["encoder_trial,block,P,app_result"]
    for k, P in enumerate(SWEEP_P, 1):
        f, tau = SWEEP_F[P], SWEEP_TAU[P]
        write_bin(enc_dir / f"bench1118StretchTrial{k:02d}Fast.bin", te, fast_trial(te, f, tau))
        log.append(f"{k},R,{P},{'ERROR: worker fault' if k == missing else 'scored'}")
        if k == missing:
            continue
        lag = rng.uniform(1.5, 4.0)
        tp = np.cumsum(rng.uniform(0.014, 0.019, 1500))
        tp -= tp[0]
        name = f"pendulastic-BENCH-20261118T090000-trial{k}.jsonl"
        write_phone(ph_dir / name, tp, fast_trial(tp - lag, f, tau), sign=-1.0)
        trials.append({"file": name, "timestamp": 1_800_000_000_000 + 60_000 * k,
                       "algorithm_version": "0.3.0+test", "params": {"f": f, "n": 2}})
    (ph_dir / "pendulastic-BENCH-20261118T090000-manifest.json").write_text(
        json.dumps({"schema": "pendulastic/session-export/v6", "trials": trials}), encoding="utf-8")
    logp = tmp_path / "rig_log.csv"
    logp.write_text("\n".join(log) + "\n", encoding="utf-8")
    return enc_dir, ph_dir, logp


@pytest.mark.parametrize("missing", [3, 6, 9])
def test_sweep_with_missing_trial_pairs_exactly_from_the_rig_log(tmp_path, missing):
    enc_dir, ph_dir, logp = _sweep(tmp_path, missing)
    rows, _ = bsr.analyze(str(ph_dir), str(enc_dir), str(tmp_path / "out"), rig_log=str(logp))
    got = [r["enc_trial"] for r in rows]
    assert got == [k for k in range(1, 13) if k != missing]
    assert all(r["polarity"] == -1 for r in rows)
    assert not any(r["flag"] for r in rows)
    md = (tmp_path / "out" / "summary.md").read_text(encoding="utf-8")
    assert "COUNT MISMATCH" not in md
    assert f"| ring-down P={SWEEP_P[missing - 1]:g} | 3 | 1 |" in md


@pytest.mark.parametrize("missing", [3, 6, 9])
def test_sweep_with_missing_trial_without_log_keeps_every_pair_on_its_P_and_warns(tmp_path, missing):
    enc_dir, ph_dir, _ = _sweep(tmp_path, missing)
    rows, _ = bsr.analyze(str(ph_dir), str(enc_dir), str(tmp_path / "out"))
    for r in rows:
        k = int(r["file"].rsplit("trial", 1)[1].split(".")[0])     # phone k recorded trial k
        assert SWEEP_P[r["enc_trial"] - 1] == SWEEP_P[k - 1]          # never shifted across P
        assert not r["flag"]
    md = (tmp_path / "out" / "summary.md").read_text(encoding="utf-8")
    assert "pass --rig-log" in md


def test_a_log_that_omits_an_error_row_is_reported_not_silently_shifted(tmp_path):
    enc_dir, ph_dir, logp = _sweep(tmp_path, 6)
    lines = logp.read_text(encoding="utf-8").splitlines()
    logp.write_text("\n".join(lines[:6] + [lines[6].replace("ERROR: worker fault", "scored")]
                              + lines[7:]) + "\n", encoding="utf-8")
    rows, _ = bsr.analyze(str(ph_dir), str(enc_dir), str(tmp_path / "out"), rig_log=str(logp))
    md = (tmp_path / "out" / "summary.md").read_text(encoding="utf-8")
    assert "COUNT MISMATCH" in md
    assert any("mis-pair" in r["flag"] for r in rows)            # wrong-P pairs are caught


def test_staircases_use_one_polarity_and_every_step_has_the_right_sign_and_size(tmp_path):
    enc_dir, ph_dir = tmp_path / "enc", tmp_path / "phone"
    enc_dir.mkdir()
    ph_dir.mkdir()
    te = np.arange(0, 14, 0.001)
    tp = np.arange(0, 16, 1 / 60)
    trials = []
    for k in range(1, 6):
        write_bin(enc_dir / f"rigStretchTrial{k:02d}Fast.bin", te, staircase(te))
        name = f"pendulastic-BENCH-20261118T090000-trial{k}.jsonl"
        # phone flipped AND started 1-2 s earlier: a flipped staircase shifted by one
        # step is nearly the original, which is what a free per-trial sign latched onto
        write_phone(ph_dir / name, tp, staircase(tp - 1.0 - 0.2 * k), sign=-1.0)
        trials.append({"file": name, "timestamp": 1_800_000_000_000 + k, "params": None,
                       "research": True})
    (ph_dir / "m-manifest.json").write_text(json.dumps({"trials": trials}), encoding="utf-8")
    rows, steps = bsr.analyze(str(ph_dir), str(enc_dir), str(tmp_path / "out"), block="S")
    assert {r["polarity"] for r in rows} == {-1}
    assert len(steps) == 20
    for s in steps:
        assert np.sign(s["phone_step_deg"]) == np.sign(s["enc_step_deg"])
        assert s["phone_step_deg"] / s["enc_step_deg"] == pytest.approx(1.0, abs=0.05)
        assert np.isfinite(s["phone_over"])


def test_step_guard_rejects_a_wrong_sign_step():
    t = np.arange(0, 14, 0.001)
    x = staircase(t)
    pg = (t, -x, None)                                   # wrong polarity on purpose
    rows = bd.score_steps(pg, (t, x, np.gradient(x, t)), lag=0.0, sign=1)
    assert rows and all(not np.isfinite(r["phone_over"]) for r in rows)


def test_block_aliases_and_rig_log_encoding(tmp_path):
    assert bsr.normalize_block("R") == "ringdown"
    assert bsr.normalize_block(" Ring-Down ") == "ringdown"
    assert bsr.normalize_block("s") == "step"
    assert bsr.normalize_block("") is None
    with pytest.raises(SystemExit, match="unknown block"):
        bsr.normalize_block("sweep")
    p = tmp_path / "log.csv"
    p.write_bytes("encoder_trial,block,P,notes\n7,S,1.0,arm \xb0 ok\n3,r,0.50,\n".encode("cp1252"))
    rows = bsr.load_rig_log(str(p))
    assert [r["_trial"] for r in rows] == [3, 7]
    assert [r["_block"] for r in rows] == ["ringdown", "step"]
    assert [r["P"] for r in rows] == ["0.5", "1"]
    assert rows[1]["notes"] == "arm ° ok"
