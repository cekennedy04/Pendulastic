"""
evaluate_flex_axis_methods.py
=============================
Compare flex-axis estimation methods against the OptiTrack A0 reference, with
every mode run inside the SAME working tree so the surrounding code is identical
and only the axis differs.

    estimator : the shipped online FlexAxisEstimator (commits after 25 samples)
    batch     : principal axis over the WHOLE trial -- the covariance branch's
                method. The best offline reference; it cannot run live, because
                the server sees one sample at a time and cannot look ahead.
    off       : no axis projection at all (the control)

Result on 100 paired trials, 2026-08-28 (93 before P17 was recovered in
328c6e6; the earlier 93-trial run gave 56.4 / 53.8 / 75.4 and the same ranking):

    mode        median err   ratio IQR    median ratio   beyond 2x
    estimator      54.9%     1.32-1.76       1.536       13/100
    batch          53.7%     1.30-1.76       1.524       13/100
    off            75.1%     1.45-2.00       1.743       26/100

Batch wins by 1.2 points with an effectively identical IQR and an identical
beyond-2x count, and it cannot run live, so the online estimator is the shipped
choice. Both halve the beyond-2x failures against no axis at all.

NOTE this compares AXIS METHODS only. None of them addresses the residual
~1.55x amplitude over-read, which is single-sensor geometry rather than axis
error -- see docs/reports/2026-08-28-imu-over-read-root-cause.md.

A bug worth not repeating
-------------------------
The first version of this harness pulled gyro vectors with `s.get("gyro")`.
Raw samples are keyed {"t", "role", "v", "sensor"}; there is no "gyro" key.
Every lookup returned None, the batch axis came back None, and "batch" silently
became "off" -- producing a table where the two agreed to three decimal places,
which reads as a finding rather than a defect. Extraction now goes through
imu_absolute_vs_knee.gyro_vectors(), which RAISES when it extracts nothing.
"""
from __future__ import annotations

import collections
import os
import statistics as st
import warnings

import numpy as np

warnings.filterwarnings("ignore")

import batch_imu_vs_optitrack_rmse as B
import imu_calibration_config as cfg
import imu_calibration_tuner as tuner
import pendulastic_pt_score as pt
import workbench_engine as engine
from imu_absolute_vs_knee import gyro_vectors
from imu_flex_axis import FlexAxisEstimator, principal_axis

MODES = ("estimator", "batch", "off")

_REAL_ESTIMATOR = FlexAxisEstimator
_forced = {"axis": None}


class _FixedAxis:
    """Stands in for FlexAxisEstimator, committed from the first sample to a
    pre-computed whole-trial axis. This is how the batch method is expressed
    without duplicating replay_trial."""

    def __init__(self, *_a, **_k):
        pass

    @property
    def axis(self):
        return _forced["axis"]

    @property
    def committed(self):
        return True

    @property
    def n_samples(self):
        return 1

    def update(self, v, gravity=None):
        pass

    def reset(self):
        pass


_real_replay = tuner.replay_trial


def _batch_replay(raw_samples, params):
    """replay_trial with the axis pinned to the whole trial's principal axis."""
    # gyro_vectors() raises rather than returning empty, so a batch mode that
    # cannot compute an axis fails loudly instead of impersonating "off".
    _forced["axis"] = principal_axis(gyro_vectors(raw_samples))
    if _forced["axis"] is None:
        raise ValueError("batch axis unavailable -- would silently equal 'off'")
    tuner.FlexAxisEstimator = _FixedAxis
    try:
        return _real_replay(raw_samples, params)
    finally:
        tuner.FlexAxisEstimator = _REAL_ESTIMATOR


def score_trial(val, mode, base_config):
    """A0_deg for one validated trial under one axis mode, or None."""
    config = dict(base_config)
    config["flex_axis_capture"] = (mode != "off")
    tuner.replay_trial = _batch_replay if mode == "batch" else _real_replay
    try:
        t, angle, _ref = engine.load_imu_trial_from_components(val, config=config)
        params = pt.compute_pt_params(np.asarray(t), np.asarray(angle))
        return params.get("A0_deg") if params else None
    except Exception:
        return None
    finally:
        tuner.replay_trial = _real_replay


def collect(limit=None):
    base = cfg.load_config()
    anchors = []
    for root, _dirs, files in os.walk(B.REC_ROOT):
        for fn in files:
            if B._TRIAL_ANCHOR_RE.match(fn):
                anchors.append(os.path.join(root, fn))
    anchors.sort()
    if limit:
        anchors = anchors[:limit]

    rows = []
    for path in anchors:
        try:
            opti = B.find_optitrack_match(path, B.REC_ROOT, B.OPTI_ROOT)
        except Exception:
            opti = None
        if not opti:
            continue
        try:
            comp = B.derive_component_paths(path)
            val = {k: engine.validate_component_csv(comp[k], k)
                   for k in ("accel", "gyro", "mag", "imu")}
            if any(not v["ok"] for v in val.values()):
                continue
            t_o, a_o, _m = engine.load_optitrack_trial(opti)
            ref = pt.compute_pt_params(np.asarray(t_o), np.asarray(a_o))
        except Exception:
            continue
        if not ref or ref.get("A0_deg") is None:
            continue
        rec = {"path": path, "opti": ref["A0_deg"]}
        for mode in MODES:
            rec[mode] = score_trial(val, mode, base)
        if all(rec.get(m) is not None for m in MODES):
            rows.append(rec)
    return rows


def report(rows):
    print(f"\npaired trials scored with ALL modes: {len(rows)}\n")
    print(f"{'mode':<12}{'median err':>12}{'ratio IQR':>18}{'median ratio':>14}{'beyond 2x':>12}")
    for mode in MODES:
        err = [abs(r[mode] - r["opti"]) / r["opti"] * 100 for r in rows]
        ratios = [r[mode] / r["opti"] for r in rows]
        bad = sum(1 for x in ratios if x < 0.5 or x > 2.0)
        print(f"{mode:<12}{st.median(err):11.1f}%"
              f"{np.percentile(ratios, 25):9.2f}-{np.percentile(ratios, 75):<8.2f}"
              f"{st.median(ratios):>14.3f}{bad:>9}/{len(rows)}")

    by = collections.defaultdict(lambda: collections.defaultdict(list))
    for r in rows:
        parts = r["path"].replace("\\", "/").split("/")
        pid = next((x for x in parts if x.startswith("Participant")), "?")
        for mode in MODES:
            by[pid][mode].append(r[mode] / r["opti"])
    print()
    print(f"{'participant':<16}{'n':>4}" + "".join(f"{m + ' spread':>20}" for m in MODES))
    for pid in sorted(by):
        spread = lambda v: f"{max(v) / min(v):.1f}x" if v and min(v) > 0 else "n/a"
        print(f"{pid:<16}{len(by[pid]['estimator']):>4}"
              + "".join(f"{spread(by[pid][m]):>20}" for m in MODES))


if __name__ == "__main__":
    report(collect())
