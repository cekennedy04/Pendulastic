#!/usr/bin/env python3
"""
mediapipe_pt_metrics.py
=======================
One row per MediaPipe HPE CSV in the corpus: extraction provenance, tracking
coverage, all seven Popovic PT parameters, the PT7 composite, and the gated MAS
estimate -- computed from BOTH angle columns the extractor writes
(``knee_angle_deg``, the projected 2D angle the pipeline has always used, and
``knee_angle_world_deg``, BlazePose's view-independent metric-world angle) --
alongside the OptiTrack reference for the same trial wherever one exists.

Why one script rather than reading hpe_mas_evaluation.py's table: that one
scores a single angle column, calls pt_to_mas() directly (so it prints a grade
for trials the 2026-08-30 excursion gate refuses), and only sees trials that
reached its own discovery. This walks the CSVs on disk, so a trial that has an
HPE CSV but no OptiTrack export -- 28 of them, invisible to every
OptiTrack-gated discovery in the repo -- still gets scored and reported.

Scoring path matches production for optical data: compute_pt_params() with its
default detrend=True (the phone path deliberately passes detrend=False; the two
conventions disagree on roughly a third of MAS grades, so they must not be
mixed), then mas_estimate(), which refuses a grade the excursion gate cannot
support instead of printing one anyway.

Usage:
    python mediapipe_pt_metrics.py
    python mediapipe_pt_metrics.py --out somewhere/else.csv
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import os
import pickle
import re
import warnings

import numpy as np

import cross_modality_rmse as cmr
import participant_paths
import pendulastic_pt_score as pts

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
REC_ROOT = os.path.join(BASE_DIR, "Recordings")
OPTI_ROOT = os.path.join(BASE_DIR, "OptiTrack_Recordings")
DEFAULT_OUT = os.path.join(BASE_DIR, "Model_Analysis_Outputs", "HPE_Comparison",
                           "mediapipe_pt_metrics.csv")

#: The seven scored Popovic parameters, plus A0 (the excursion the gate tests,
#: not itself scored). Names must match compute_pt_params' own keys exactly --
#: this said "f_hz" until 2026-09-08, which is not a key it returns, so the
#: frequency column was silently empty in every row of every earlier run.
PT_KEYS = ("R2n", "N", "phi_max_ratio", "omega_max_n", "omega_min_n",
           "f", "area_ratio", "A0_deg")

_CSV_RE = re.compile(r"^(?P<prefix>.+?)_T_(?P<trial>\d+)_mediapipe_(?P<variant>.+?)\.csv$",
                     re.IGNORECASE)


#: Where a superseded duplicate CSV is parked. A trial that carried two CSVs for
#: the same variant (from an earlier folder layout) was being scored twice, once
#: off each -- load_hpe_model_curves globs *_T_{n}_*.csv non-recursively, so
#: moving the older copy one level down takes it out of discovery without
#: deleting it. Skipped here for the same reason.
SUPERSEDED_DIR = "_superseded_2026-09-04"


def iter_hpe_csvs():
    """Yield (path, root_label, trial_n, variant) for every MediaPipe HPE CSV."""
    for root, label in ((REC_ROOT, "Recordings"), (OPTI_ROOT, "OptiTrack_Recordings")):
        if not os.path.isdir(root):
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d != SUPERSEDED_DIR]
            for name in sorted(filenames):
                m = _CSV_RE.match(name)
                if m:
                    yield (os.path.join(dirpath, name), label,
                           int(m.group("trial")), m.group("variant"))


def _pid_and_context(path):
    """('19', 'left', 'pre') from a recording path, best effort."""
    rel = os.path.relpath(os.path.dirname(path), BASE_DIR)
    parts = rel.split(os.sep)
    pid = None
    for part in parts:
        got = participant_paths.pid_from_dir_name(part)
        if got is not None:
            pid = got
            break
    lower = rel.lower()
    leg = "left" if "left" in lower else ("right" if "right" in lower else "")
    # Everything below the leg folder is the timepoint/condition label.
    cond = ""
    for i, part in enumerate(parts):
        if part.lower() in ("left", "right") and i + 1 < len(parts):
            cond = "/".join(parts[i + 1:])
            break
    return pid or "", leg, cond


def _optitrack_path(hpe_path, trial_n):
    """The OptiTrack export for the same trial, or None."""
    rel = os.path.relpath(os.path.dirname(hpe_path), BASE_DIR)
    parts = rel.split(os.sep)
    # Strip whichever root the HPE CSV lives under and rebuild under OptiTrack's.
    tail = os.sep.join(parts[1:])
    for name in (f"Trial_{trial_n}_optitrack.csv", f"trial_{trial_n}_optitrack.csv"):
        cand = os.path.join(OPTI_ROOT, tail, name)
        if os.path.isfile(cand):
            return cand
    return None


_OPTI_CACHE_DIR = os.path.join(BASE_DIR, "Model_Analysis_Outputs", "_cache",
                               "mediapipe_pt_metrics")


def load_optitrack_cached(path):
    """(t, angle) for an OptiTrack export, memoised on disk.

    Parsing one Motive export takes ~3.4 s and the corpus has ~285 of them, so
    an uncached run spends a quarter of an hour re-deriving numbers that only
    change when the export does. The key carries size and mtime, so a
    re-exported take misses cache rather than silently serving the old angles.
    """
    st = os.stat(path)
    # hashlib, not the builtin hash(): PYTHONHASHSEED randomises str hashing per
    # process, so a hash()-derived filename is a different name on every run and
    # the cache never hits -- it just silently rewrites 285 entries and re-parses
    # every Motive export, which is the quarter-hour this exists to avoid.
    ident = f"{os.path.abspath(path)}|{st.st_size}|{int(st.st_mtime)}"
    key = hashlib.sha256(ident.encode("utf-8")).hexdigest()[:32] + ".pkl"
    cache_file = os.path.join(_OPTI_CACHE_DIR, key)
    if os.path.isfile(cache_file):
        try:
            with open(cache_file, "rb") as f:
                return pickle.load(f)
        except (OSError, pickle.UnpicklingError):
            pass                      # a truncated cache entry is not fatal
    value = pts.load_optitrack(path)
    os.makedirs(_OPTI_CACHE_DIR, exist_ok=True)
    tmp = cache_file + ".tmp"
    with open(tmp, "wb") as f:
        pickle.dump(value, f)
    os.replace(tmp, cache_file)
    return value


def _read_columns(path):
    """(time, angle_2d, angle_world, ambiguous_frac, n_rows) as float arrays.

    Blank cells -- every frame where the tracked joints fell below the
    visibility threshold -- become NaN rather than being dropped, so the time
    base stays uniform and the detection rate stays measurable.
    """
    t, a2, aw, amb, n = [], [], [], 0, 0
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        header = reader.fieldnames or []
        for row in reader:
            n += 1
            def _num(key):
                v = (row.get(key) or "").strip()
                try:
                    return float(v)
                except ValueError:
                    return float("nan")
            t.append(_num("time_sec"))
            a2.append(_num("knee_angle_deg"))
            aw.append(_num("knee_angle_world_deg"))
            if str(row.get("identity_ambiguous", "")).strip().lower() == "true":
                amb += 1
    return (np.asarray(t, float), np.asarray(a2, float), np.asarray(aw, float),
            (amb / n if n else float("nan")), n,
            "knee_angle_world_deg" in header)


def score_curve(t, angle, detrend=True):
    """PT params + PT7 + gated MAS for one angle series. Never raises.

    `detrend` is per-channel and must follow each channel's own production
    convention, not a house default. Optical curves are scored detrended
    (hpe_mas_evaluation.hpe_pt_score, and every OptiTrack call site); the phone
    IMU is scored WITHOUT detrending (imu_calibration_tuner:552,
    pendulastic_app:1795). The two conventions are not interchangeable — on
    CHAT_19 left T1 the same IMU trace scores PT7 0.608 undetrended and 0.127
    detrended, and the choice flips roughly a third of MAS grades across the
    corpus. Mixing them in one table would manufacture a disagreement between
    channels that is an artefact of this flag alone.
    """
    blank = {k: float("nan") for k in PT_KEYS}
    blank.update(pt7=float("nan"), mas="", interpretable=False, reason="")
    finite = np.isfinite(t) & np.isfinite(angle)
    if finite.sum() < 40:
        blank["reason"] = f"only {int(finite.sum())} tracked frames (need 40)"
        return blank
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            params = pts.compute_pt_params(t[finite], angle[finite],
                                           detrend=detrend)
        est = pts.mas_estimate(params)
    except Exception as exc:                      # noqa: BLE001 - one bad trial
        blank["reason"] = f"{type(exc).__name__}: {exc}"   # must not end the run
        return blank
    if params is None:
        blank["reason"] = "compute_pt_params returned None"
        return blank
    out = {k: float(params.get(k, float("nan"))) for k in PT_KEYS}
    out.update(pt7=(est["pt7"] if est["pt7"] is not None else float("nan")),
               mas=(est["mas"] or ""), interpretable=est["interpretable"],
               reason=est["reason"])
    return out


def _round(v, n=4):
    return round(v, n) if isinstance(v, float) and np.isfinite(v) else v


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=DEFAULT_OUT, help="output CSV path")
    args = ap.parse_args()

    rows = []
    csvs = list(iter_hpe_csvs())
    print(f"Scoring {len(csvs)} MediaPipe HPE CSV(s) ...\n")
    for i, (path, root_label, trial_n, variant) in enumerate(csvs, 1):
        pid, leg, cond = _pid_and_context(path)
        t, a2, aw, amb_frac, n_rows, has_world = _read_columns(path)
        det2 = float(np.isfinite(a2).sum()) / n_rows if n_rows else float("nan")
        detw = float(np.isfinite(aw).sum()) / n_rows if n_rows else float("nan")

        # schema_current is a property of the HEADER, not of the data: a trial
        # where MediaPipe found the patient in zero frames writes the column
        # and leaves it blank, and calling that "stale" would confuse a
        # tracking failure with an out-of-date extractor.
        s2 = score_curve(t, a2)
        sw = score_curve(t, aw) if np.isfinite(aw).any() else None

        opti = _optitrack_path(path, trial_n)
        so = None
        if opti:
            try:
                ot, oa = load_optitrack_cached(opti)
                so = score_curve(np.asarray(ot, float), np.asarray(oa, float))
            except Exception as exc:              # noqa: BLE001
                so = None
                print(f"  [{i}/{len(csvs)}] OptiTrack load failed for T{trial_n}: "
                      f"{type(exc).__name__}: {exc}")

        # The phone IMU for the same trial, replayed through the live fusion
        # pipeline at ANALYSIS_TICK_S -- the 100 Hz analysis grid, so this is
        # the post-2026-09-04 scoring path rather than the 2026-08-28
        # imu_vs_optitrack_rmse.csv snapshot, which predates it. Scored
        # undetrended, per the phone convention (see score_curve).
        si = None
        replay = pts._replay_raw_imu_fallback(os.path.dirname(path), str(trial_n))
        if replay is not None:
            it, ia = replay
            si = score_curve(np.asarray(it, float), np.asarray(ia, float),
                             detrend=False)

        rec = {
            "participant": pid, "leg": leg, "condition": cond, "trial": trial_n,
            "variant": variant, "root": root_label,
            "csv": os.path.relpath(path, BASE_DIR),
            "n_frames": n_rows,
            "schema_current": has_world,
            "det_rate_2d": _round(det2, 4),
            "det_rate_world": _round(detw, 4),
            "identity_ambiguous_frac": _round(amb_frac, 4),
            "has_optitrack": bool(opti),
            "has_imu": si is not None,
        }
        for tag, s in (("2d", s2), ("world", sw), ("opti", so), ("imu", si)):
            if s is None:
                for k in PT_KEYS:
                    rec[f"{tag}_{k}"] = ""
                rec[f"{tag}_pt7"] = ""
                rec[f"{tag}_mas"] = ""
                rec[f"{tag}_interpretable"] = ""
                rec[f"{tag}_reason"] = ""
                continue
            for k in PT_KEYS:
                rec[f"{tag}_{k}"] = _round(s[k])
            rec[f"{tag}_pt7"] = _round(s["pt7"])
            rec[f"{tag}_mas"] = s["mas"]
            rec[f"{tag}_interpretable"] = s["interpretable"]
            rec[f"{tag}_reason"] = s["reason"]

        # Angle-level agreement, separate from PT-level. Both read
        # rmse_deg_offset_removed and pick their own capture-skew rule; see
        # cross_modality_rmse for why either choice is easy to get wrong.
        rec["rmse_2d"] = rec["rmse_2d_gapfilled"] = rec["rmse_imu"] = ""
        rec["opti_span_deg"] = rec["opti_max_jump_deg"] = ""
        if opti and so is not None:
            try:
                oa_arr = np.asarray(oa, float)
                fin = oa_arr[np.isfinite(oa_arr)]
                if fin.size > 2:
                    rec["opti_span_deg"] = _round(float(fin.max() - fin.min()), 2)
                    rec["opti_max_jump_deg"] = _round(
                        float(np.max(np.abs(np.diff(fin)))), 2)
                rec["rmse_2d"] = _round(
                    cmr.score_against_optitrack(ot, oa, t, a2, "video") or float("nan"), 3)
                filled = cmr.fill_detection_gaps(t, a2)
                if filled is not None:
                    rec["rmse_2d_gapfilled"] = _round(
                        cmr.score_against_optitrack(ot, oa, filled[0], filled[1],
                                                    "video") or float("nan"), 3)
                if replay is not None:
                    rec["rmse_imu"] = _round(
                        cmr.score_against_optitrack(ot, oa, replay[0], replay[1],
                                                    "imu") or float("nan"), 3)
            except Exception as exc:              # noqa: BLE001
                print(f"  [{i}/{len(csvs)}] RMSE failed for T{trial_n}: "
                      f"{type(exc).__name__}: {exc}")

        for tag in ("2d", "world", "imu"):
            a, b = rec.get(f"{tag}_pt7"), rec.get("opti_pt7")
            rec[f"{tag}_pt7_abs_err"] = (_round(abs(a - b))
                                         if isinstance(a, float) and isinstance(b, float)
                                         else "")
        rows.append(rec)
        if i % 25 == 0 or i == len(csvs):
            print(f"  {i}/{len(csvs)}")

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    fields = list(rows[0].keys()) if rows else []
    with open(args.out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    print(f"\nWrote {len(rows)} row(s) -> {os.path.relpath(args.out, BASE_DIR)}")

    # ── Summary ───────────────────────────────────────────────────────────────
    def _col(name):
        return np.array([r[name] for r in rows if isinstance(r[name], float)
                         and np.isfinite(r[name])], dtype=float)

    stale = sum(1 for r in rows if not r["schema_current"])
    print(f"\nSchema: {len(rows) - stale} current (has knee_angle_world_deg), {stale} stale")
    det = _col("det_rate_2d")
    if det.size:
        print(f"Detection rate (2D): median {np.median(det):.1%}, "
              f"{int((det < 0.5).sum())}/{det.size} trials under 50%")
    for tag, label in (("2d", "2D angle"), ("world", "world angle"),
                       ("imu", "phone IMU"), ("opti", "OptiTrack")):
        n_ok = sum(1 for r in rows if r[f"{tag}_interpretable"] is True)
        pt7 = _col(f"{tag}_pt7")
        print(f"{label:12s}: {n_ok} trials pass the excursion gate; "
              f"PT7 median {np.median(pt7):.3f} (n={pt7.size})" if pt7.size
              else f"{label:12s}: {n_ok} pass the gate; no PT7 computed")
    for tag, label in (("2d", "2D vs OptiTrack"), ("world", "world vs OptiTrack"),
                       ("imu", "IMU vs OptiTrack")):
        err = _col(f"{tag}_pt7_abs_err")
        if err.size:
            print(f"{label:20s}: |dPT7| median {np.median(err):.3f}, "
                  f"mean {err.mean():.3f} (n={err.size})")


if __name__ == "__main__":
    main()
