"""
generate_paper_results_analysis.py
=====================================
Computes the real statistics for the Results/Data-Analysis section draft
requested 2026-08-19 (results section for a paper to present to Monica
Perez): IMU-vs-OptiTrack and MediaPipe-vs-OptiTrack agreement (ICC(2,1) +
Bland-Altman) on the Popovic 7-parameter PT metrics, MAS correlation
(Spearman) for the MS participants who have MAS scores, and an MS-vs-
Control group comparison via a linear mixed-effects model (participant as
random effect, since each participant contributes multiple trials).

Uses the current live IMU config (imu_calibration_config.json, the
2026-08-18 re-tuned beta=0.041/ema_alpha=0.5 config) and reuses
rmse_pipeline_common's cached MediaPipe landmark extraction from the
2026-08-18 run (sweep_cache/landmarks/) so it does not re-run pose
inference.

Usage:
    .venv\\Scripts\\python.exe generate_paper_results_analysis.py
"""
from __future__ import annotations

import csv
import json
import os
import statistics

import numpy as np
from scipy import stats
import statsmodels.formula.api as smf
import pandas as pd

import batch_imu_vs_optitrack_rmse as batch
import workbench_engine as engine
import rmse_pipeline_common as rpc

PARAMS = ["R2n", "N", "phi_max_ratio", "omega_max_n", "f", "area_ratio", "omega_min_n"]
MODEL_VARIANT = "full"
VIS_THRESH = 0.5


def icc_2_1(subject_a: np.ndarray, subject_b: np.ndarray):
    """ICC(2,1): two-way random effects, single measurement, absolute
    agreement (McGraw & Wong 1996), for two raters/methods across n
    subjects/trials. Returns None if fewer than 3 valid paired trials."""
    mask = np.isfinite(subject_a) & np.isfinite(subject_b)
    a, b = subject_a[mask], subject_b[mask]
    n = len(a)
    if n < 3:
        return None
    data = np.stack([a, b], axis=1)  # (n, 2)
    k = 2
    grand_mean = data.mean()
    row_means = data.mean(axis=1)
    col_means = data.mean(axis=0)
    ss_total = ((data - grand_mean) ** 2).sum()
    ss_rows = k * ((row_means - grand_mean) ** 2).sum()
    ss_cols = n * ((col_means - grand_mean) ** 2).sum()
    ss_error = ss_total - ss_rows - ss_cols
    ms_rows = ss_rows / (n - 1)
    ms_cols = ss_cols / (k - 1)
    ms_error = ss_error / ((n - 1) * (k - 1)) if (n - 1) * (k - 1) > 0 else float("nan")
    denom = ms_rows + (k - 1) * ms_error + k * (ms_cols - ms_error) / n
    if denom == 0 or not np.isfinite(denom):
        return None
    icc = (ms_rows - ms_error) / denom
    return float(icc)


def bland_altman(subject_a: np.ndarray, subject_b: np.ndarray):
    """subject_a = reference (OptiTrack), subject_b = test method."""
    mask = np.isfinite(subject_a) & np.isfinite(subject_b)
    a, b = subject_a[mask], subject_b[mask]
    if len(a) < 3:
        return None
    diff = b - a
    bias = float(np.mean(diff))
    sd = float(np.std(diff, ddof=1))
    return {"bias": bias, "sd": sd, "loa_lower": bias - 1.96 * sd, "loa_upper": bias + 1.96 * sd,
            "n": len(a)}


# Folder names under Recordings/ that are not real participants.
_NON_PARTICIPANT_IDS = {"test", "0", "demo"}


def load_group_and_demo():
    """participant -> {"group", "spasticity", "spasticity_source", "age", "sex",
    "diagnosis"} from Recordings/Participant_N/metadata.json.

    Keyed by the FOLDER name (matches discover_trials()'s t["participant"]),
    not the JSON's own "participant_id" field -- that field is unreliable
    (e.g. Recordings/Participant_5/metadata.json has participant_id="6_jk",
    a data-entry bug in the source file, confirmed 2026-08-19).

    "group" is the CANONICAL arm from pt_cohort_common.classify_participant.
    This function used to classify inline as:

        group = "MS" if "multiple sclerosis" in diagnosis else ("Control" if diagnosis else None)

    which swept every non-MS diagnosis into Control. Measured against the real
    metadata on 2026-08-27 that put all four post-stroke participants (19, 21,
    22, 24) into the CONTROL arm of the mixed-effects model, the AUC and the
    boxplots. Control now means control.

    "spasticity" is the per-participant characterisation from
    spasticity_grouping -- non-spastic / spastic / unknown -- rolled up from
    per-leg labels. It exists because diagnosis is not the variable the
    pendulum test measures, and because grouping on it keeps every participant
    in the analysis: the arm-based split has no home for a post-stroke
    participant, so correcting the arm alone would have dropped those four
    rather than mislabelling them, which is not an improvement.
    """
    import pt_cohort_common as _pcc
    import spasticity_grouping as _sg

    registry, registry_exists = _pcc.load_registry()
    mas_by_leg = _sg.load_mas_by_leg()
    mas_components = _sg.load_mas_components_by_leg()
    a0 = a0_by_leg_from_optitrack()

    out = {}
    base = "Recordings"
    for name in os.listdir(base):
        if not name.startswith("Participant_"):
            continue
        # Participant_test is capture scaffolding (participant_id "test",
        # weight 20 kg) and was being classified as an MS participant.
        if name.replace("Participant_", "").strip().lower() in _NON_PARTICIPANT_IDS:
            continue
        meta_path = os.path.join(base, name, "metadata.json")
        if not os.path.isfile(meta_path):
            continue
        try:
            with open(meta_path, encoding="utf-8") as f:
                meta = json.load(f)
        except (OSError, ValueError):
            continue
        pid = name.replace("Participant_", "")
        arm, _source = _pcc.classify_participant(
            pid, _pcc.load_metadata_diagnosis(pid), registry, registry_exists)
        legs = _sg.classify_participant_legs(
            pid, arm=arm, mas_by_leg=mas_by_leg,
            mas_components=mas_components,
            a0_by_leg={leg: a0.get((pid, leg)) for leg in _sg.LEGS})
        rolled = _sg.participant_level(legs)
        out[pid] = {"group": arm,
                    "spasticity": rolled.level,
                    "spasticity_source": rolled.source,
                    "age": meta.get("age"), "sex": meta.get("sex"),
                    "diagnosis": meta.get("diagnosis")}
    return out


def a0_by_leg_from_optitrack():
    """{(participant, leg): median A0_deg} over each leg's pre-condition trials.

    The median, not the mean: OptiTrack coverage collapses mid-swing on most
    trials, so a single badly-tracked trial can drag an average a long way.
    Trials that cannot be reconstructed at all are simply absent, which leaves
    the leg UNKNOWN rather than guessed.
    """
    import glob
    import statistics
    import pendulastic_pt_score as _pts

    per_leg = {}
    pattern = os.path.join("OptiTrack_Recordings", "Participant_*", "*", "pre",
                           "**", "trial_*_optitrack.csv")
    for path in glob.glob(pattern, recursive=True):
        parts = path.replace("\\", "/").split("/")
        pid = parts[1].replace("Participant_", "")
        leg = parts[2].lower()
        try:
            t, angle, _quality = _pts.load_optitrack_detailed(path)
            val = _pts.compute_pt_params(t, angle).get("A0_deg")
        except Exception:
            continue
        if val is not None and float(val) == float(val):
            per_leg.setdefault((pid, leg), []).append(float(val))
    return {k: statistics.median(v) for k, v in per_leg.items() if v}


def load_mas_scores():
    """participant -> list of mas_grade (as float, '1+' -> 1.5)."""
    out = {}
    def parse_grade(g):
        g = g.strip()
        if g.endswith("+"):
            return float(g[:-1]) + 0.5
        try:
            return float(g)
        except ValueError:
            return None
    with open("mas_scores.csv", newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            pid = row["participant"].strip()
            grade = parse_grade(row["mas_grade"])
            if grade is None:
                continue
            out.setdefault(pid, []).append(grade)
    return {pid: statistics.mean(vals) for pid, vals in out.items()}


def imu_pt_params_for_trial(t_dict):
    validations = {
        "accel": engine.validate_component_csv(t_dict["accel"], "accel"),
        "gyro": engine.validate_component_csv(t_dict["gyro"], "gyro"),
        "mag": engine.validate_component_csv(t_dict["mag"], "mag"),
        "imu": engine.validate_component_csv(t_dict["imu"], "imu"),
    }
    if any(not v["ok"] for v in validations.values()):
        return None
    try:
        t, angle, _ref = engine.load_imu_trial_from_components(validations, method="relative")
        ref_t, ref_angle, _m = engine.load_optitrack_trial(t_dict["optitrack_path"])
    except Exception:
        return None
    if len(t) < 10 or len(ref_t) < 10:
        return None
    imu_pt = engine.windowed_pt_params(t, angle)
    opti_pt = engine.windowed_pt_params(ref_t, ref_angle)
    return imu_pt, opti_pt


def main():
    trials = batch.discover_trials()
    matched = [t for t in trials if t["optitrack_path"] is not None]
    groups = load_group_and_demo()
    mas = load_mas_scores()

    rows = []  # one row per trial: pid, group, imu_pt..., opti_pt...
    for t in matched:
        result = imu_pt_params_for_trial(t)
        if result is None:
            continue
        imu_pt, opti_pt = result
        pid = t["participant"].replace("Participant_", "")
        row = {"pid": pid, "trial_key": t["trial_key"], "group": groups.get(pid, {}).get("group"),
              "age": groups.get(pid, {}).get("age"), "sex": groups.get(pid, {}).get("sex")}
        for p in PARAMS:
            row[f"imu_{p}"] = imu_pt[p]
            row[f"opti_{p}"] = opti_pt[p]
        rows.append(row)

    print(f"{len(rows)} trials with computable PT parameters (of {len(matched)} matched)\n")

    # --- ICC(2,1) + Bland-Altman per parameter, IMU vs OptiTrack ---
    print("=== IMU vs OptiTrack: ICC(2,1) + Bland-Altman, per PT parameter ===")
    print(f"{'param':16s} {'ICC(2,1)':>10s} {'bias':>8s} {'LoA_lo':>8s} {'LoA_hi':>8s} {'n':>4s}")
    for p in PARAMS:
        a = np.array([r[f"opti_{p}"] for r in rows], dtype=float)
        b = np.array([r[f"imu_{p}"] for r in rows], dtype=float)
        icc = icc_2_1(a, b)
        ba = bland_altman(a, b)
        icc_s = f"{icc:.3f}" if icc is not None else "n/a"
        if ba:
            print(f"{p:16s} {icc_s:>10s} {ba['bias']:8.3f} {ba['loa_lower']:8.3f} "
                  f"{ba['loa_upper']:8.3f} {ba['n']:4d}")
        else:
            print(f"{p:16s} {icc_s:>10s} {'n/a':>8s}")

    # --- MAS correlation (R2n vs MAS, per-trial where MAS available) ---
    print("\n=== Spearman correlation: IMU R2n (relaxation index) vs MAS grade ===")
    mas_pairs = [(mas[r["pid"]], r["imu_R2n"]) for r in rows if r["pid"] in mas]
    if len(mas_pairs) >= 3:
        mas_vals, r2n_vals = zip(*mas_pairs)
        rho, pval = stats.spearmanr(mas_vals, r2n_vals)
        print(f"n={len(mas_pairs)} trials (participants with MAS scores: "
              f"{sorted(set(r['pid'] for r in rows if r['pid'] in mas))})")
        print(f"Spearman rho={rho:.3f}, p={pval:.4f}")
    else:
        print(f"Only {len(mas_pairs)} trials have both MAS and computed R2n -- too few to correlate.")

    # --- MS vs Control mixed-effects model on R2n ---
    print("\n=== MS vs Control: linear mixed-effects model (R2n ~ group, random=participant) ===")
    df = pd.DataFrame(rows)
    df_grouped = df[df["group"].isin(["MS", "Control"])].copy()
    print(f"n={len(df_grouped)} trials, participants: "
          f"{df_grouped.groupby('group')['pid'].nunique().to_dict()}")
    if df_grouped["group"].nunique() == 2 and len(df_grouped) >= 6:
        try:
            model = smf.mixedlm("imu_R2n ~ group", df_grouped, groups=df_grouped["pid"])
            fit = model.fit()
            print(fit.summary())
        except Exception as e:
            print(f"Mixed model failed: {type(e).__name__}: {e}")
    else:
        print("Not enough data for a mixed model yet.")

    # Save the per-trial table for inspection / for the paper's supplementary table
    out_path = "Model_Analysis_Outputs/paper_results_analysis_trials.csv"
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        fieldnames = list(rows[0].keys()) if rows else []
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    print(f"\nSaved per-trial table: {out_path}")


if __name__ == "__main__":
    main()
