"""
prototype_opensim_ik.py
========================
Prototype: does running MediaPipe's own landmarks through OpenSim's
biomechanically-constrained inverse kinematics (via Pose2Sim's BlazePose
setup files) produce a lower-RMSE knee angle than the pipeline's current
raw 3-point geometric angle (mediapipe_preprocessing.knee_angle_from_points)?

Reuses: Pose2Sim.kinematics.perform_scaling/perform_IK (calls opensim's
ScaleTool/InverseKinematicsTool with Pose2Sim's BlazePose .osim model,
Markers_BlazePose.xml, Scaling/IK setup XMLs -- all already installed in
.venv, zero new dependencies). Ground truth + RMSE scoring reuse
pendulastic_pt_score.load_optitrack / workbench_engine.compare_pair, same
as every other prototype script this session.

Run:
    .venv\\Scripts\\python.exe prototype_opensim_ik.py
"""
from __future__ import annotations

import math
import os
import shutil
import sys
import traceback
from pathlib import Path

import cv2
import mediapipe as mp
import numpy as np
import pandas as pd

import mediapipe_preprocessing as mp_pre
import pendulastic_pt_score as pt
import rmse_pipeline_common as rpc
import workbench_engine as engine

N_TRIALS = 4
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(BASE_DIR, "Model_Analysis_Outputs", "OpenSim_IK_Prototype")
MODEL_PATH = os.path.join(BASE_DIR, "models", "mediapipe", "pose_landmarker_heavy.task")
VIS_THRESH = 0.30

# Real per-trial anthropometry: NOT usable. Every Recordings/Participant_*/
# metadata.json's weight_kg either equals or shadows the age field
# (P10/11/12: age=20,weight=20; P13: age=60,weight=20; P14: age=40,weight=40)
# -- clearly placeholder/test data entered at intake, not real patient
# weights (no adult participant weighs 20-40kg). Using it would make the
# scaled model *more* wrong than the generic estimate, not less. Height
# isn't collected anywhere in Pendulastic's intake at all, and deriving it
# from OptiTrack would need the raw per-marker CSV (pt.load_optitrack only
# returns a scalar knee-angle series, not marker positions) -- real
# engineering effort disproportionate to this retry. So scaling still uses
# the same generic subject_height/subject_mass as pass 1; only the IK
# marker-weighting fix (lower-body-only) is new in this pass.
GENERIC_HEIGHT_M = 1.75
GENERIC_MASS_KG = 70

# BlazePose IK setup has no marker/weight subset for "trust the legs, not
# the arms" the way Pose2Sim's HALPE_26_LOWER does -- that variant is just
# a same-XML-file lookup plus this exact downweighting rule (kinematics.py
# perform_IK(): `if 'LOWER' in pose_model.upper(): weight shoulders 0.1`),
# but get_markers_path/get_scaling_setup/get_IK_Setup resolve pose_model to
# a *file name* match, and no Markers_blazeposelower.xml /
# Scaling_Setup_Pose2Sim_blazeposelower.xml / IK_Setup_Pose2Sim_blazeposelower.xml
# exist -- passing "BLAZEPOSE_LOWER" straight to perform_IK() raises
# "Pose model not supported yet." So this reimplements the same
# weight-editing technique directly against the full BlazePose IK XML
# (via get_IK_Setup("BLAZEPOSE", ...) + opensim.InverseKinematicsTool,
# bypassing perform_IK()'s pose_model dispatch) rather than perform_IK()
# itself. BlazePose has far more upper-body markers than Halpe26's two
# shoulders, so every non-leg marker gets downweighted, not just shoulders.
# Names here match the IK XML's IKMarkerTask name="..." attributes exactly
# (confirmed via grep on IK_Setup_Pose2Sim_Blazepose.xml: both knees are
# "LKnee"/"RKnee", capital K -- NOT BLAZEPOSE_MARKERS' "Lknee" TRC-writing
# spelling above, which is an unrelated pre-existing casing inconsistency
# in pass 1's marker list that happens not to matter for TRC writing).
LOWER_BODY_MARKERS = {"LHip", "RHip", "LKnee", "RKnee", "LAnkle", "RAnkle",
                      "LHeel", "RHeel", "LBigToe", "RBigToe"}
UPPER_BODY_WEIGHT = 0.1

BaseOptions = mp.tasks.BaseOptions
PoseLandmarker = mp.tasks.vision.PoseLandmarker
PoseLandmarkerOpts = mp.tasks.vision.PoseLandmarkerOptions
VisionRunningMode = mp.tasks.vision.RunningMode

# BLAZEPOSE marker tree (name -> MediaPipe landmark index), from
# Pose2Sim/skeletons.py's BLAZEPOSE node tree. "Hip" (pelvis) has no direct
# MediaPipe landmark and is computed as the RHip/LHip midpoint below.
BLAZEPOSE_MARKERS = [
    ("RHip", 24), ("RKnee", 26), ("RAnkle", 28), ("RHeel", 30), ("RBigToe", 32),
    ("LHip", 23), ("Lknee", 25), ("LAnkle", 27), ("LHeel", 29), ("LBigToe", 31),
    ("Nose", 0), ("REye", 5), ("LEye", 2),
    ("RShoulder", 12), ("RElbow", 14), ("RWrist", 16), ("RPinky", 18), ("RIndex", 20), ("RThumb", 22),
    ("LShoulder", 11), ("LElbow", 13), ("LWrist", 15), ("LPinky", 17), ("LIndex", 19), ("LThumb", 21),
]
MARKER_NAMES = ["Hip"] + [m[0] for m in BLAZEPOSE_MARKERS]


def _make_landmarker(model_path):
    opts = PoseLandmarkerOpts(
        base_options=BaseOptions(model_asset_path=str(model_path)),
        running_mode=VisionRunningMode.IMAGE,
        num_poses=2,
    )
    return PoseLandmarker.create_from_options(opts)


def extract_trial(video_path, landmarker, leg):
    """Runs MediaPipe once per trial, returns:
    - world_rows: list of dict(marker_name -> (x,y,z) in meters, hip-centered) or None per frame
    - t_list: per-frame timestamps
    - baseline_ang: existing raw 2D pixel-space knee angle per frame (for comparison)
    """
    import batch_mediapipe as bm
    h_idx, k_idx, a_idx = bm.MP_LEG_IDX[leg]

    cap = cv2.VideoCapture(str(video_path))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    t_list, world_rows, baseline_ang = [], [], []
    i = 0
    while True:
        ok, frame_bgr = cap.read()
        if not ok:
            break
        t_list.append(i / fps)
        rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
        result = landmarker.detect(mp_image)
        poses = result.pose_landmarks or []
        world_poses = result.pose_world_landmarks or []
        pose = bm._select_patient_pose(poses)
        ang = float("nan")
        row = None
        if pose is not None and world_poses:
            # world_poses is index-aligned with poses (same pose ordering)
            pose_idx = poses.index(pose)
            wpose = world_poses[pose_idx]
            hl, kl, al = pose[h_idx], pose[k_idx], pose[a_idx]
            if hl.visibility > VIS_THRESH and kl.visibility > VIS_THRESH and al.visibility > VIS_THRESH:
                fh, fw = frame_bgr.shape[:2]
                ang = mp_pre.knee_angle_from_points(
                    (hl.x * fw, hl.y * fh), (kl.x * fw, kl.y * fh), (al.x * fw, al.y * fh))
                row = {}
                for name, idx in BLAZEPOSE_MARKERS:
                    lm = wpose[idx]
                    row[name] = (lm.x, lm.y, lm.z)
                rhip, lhip = row["RHip"], row["LHip"]
                row["Hip"] = tuple((a + b) / 2.0 for a, b in zip(rhip, lhip))
        world_rows.append(row)
        baseline_ang.append(ang)
        i += 1
    cap.release()
    return t_list, world_rows, baseline_ang


def write_trc(trc_path, t_list, world_rows, fps):
    """Pose2Sim-format TRC (see Pose2Sim/triangulation.py header block).
    MediaPipe world landmarks: X right, Y down, Z toward camera (from hip).
    OpenSim/Pose2Sim TRC convention here is Y-up -- flip Y and Z to get a
    conventional Y-up, Z-forward right-handed frame.

    OpenSim's TRCFileAdapter reads every cell as a numeric token (stream
    extraction, not a strict tab-delimited parser) -- a blank cell for a
    missing-frame doesn't parse as "N/A", it desyncs the whole row's column
    count. So frames with no usable pose are dropped entirely rather than
    written as blank rows; frame numbers/timestamps are renumbered to stay
    contiguous, which is fine for IK (it only needs a valid time series, not
    original frame indices)."""
    n_markers = len(MARKER_NAMES)
    good = [(t, row) for t, row in zip(t_list, world_rows) if row is not None]
    n_frames = len(good)
    header = [
        f"PathFileType\t4\t(X/Y/Z)\t{os.path.basename(trc_path)}\n",
        "DataRate\tCameraRate\tNumFrames\tNumMarkers\tUnits\tOrigDataRate\tOrigDataStartFrame\tOrigNumFrames\n",
        f"{fps}\t{fps}\t{n_frames}\t{n_markers}\tm\t{fps}\t0\t{n_frames}\n",
        "Frame#\tTime\t" + "\t\t\t".join(MARKER_NAMES) + "\t\t\t\n",
        "\t\t" + "\t".join(f"X{i+1}\tY{i+1}\tZ{i+1}" for i in range(n_markers)) + "\t\n",
    ]
    lines = []
    for fi, (t, row) in enumerate(good):
        vals = [str(fi + 1), f"{t:.6f}"]
        for name in MARKER_NAMES:
            x, y, z = row[name]
            # flip Y (down->up) and Z (toward-camera -> forward)
            vals += [f"{x:.6f}", f"{-y:.6f}", f"{-z:.6f}"]
        lines.append("\t".join(vals))
    with open(trc_path, "w") as f:
        f.writelines(header)
        f.write("\n".join(lines) + "\n")


def perform_IK_lower_weighted(trc_path, kinematics_dir, osim_setup_dir):
    """Same as Pose2Sim.kinematics.perform_IK(trc_path, ..., "BLAZEPOSE"),
    but downweights every upper-body IKMarkerTask to UPPER_BODY_WEIGHT
    instead of running unmodified -- see LOWER_BODY_MARKERS comment above
    for why this can't just be perform_IK(..., "BLAZEPOSE_LOWER")."""
    from lxml import etree
    import opensim
    from Pose2Sim.kinematics import get_IK_Setup, read_trc

    ik_path = get_IK_Setup("BLAZEPOSE", osim_setup_dir)
    ik_path_temp = str(kinematics_dir / (trc_path.stem + "_ik_setup.xml"))
    scaled_model_path = (kinematics_dir / (trc_path.stem + ".osim")).resolve()
    output_motion_file = Path(kinematics_dir, trc_path.stem + ".mot").resolve()
    _, _, time_col, _, _ = read_trc(trc_path)
    start_time, end_time = time_col.iloc[0], time_col.iloc[-1]

    ik_tree = etree.parse(ik_path)
    ik_root = ik_tree.getroot()
    ik_root.find(".//model_file").text = str(scaled_model_path)
    ik_root.find(".//time_range").text = f"{start_time} {end_time}"
    ik_root.find(".//output_motion_file").text = str(output_motion_file)
    ik_root.find(".//marker_file").text = str(trc_path.resolve())

    n_downweighted = 0
    for task in ik_root.findall(".//IKMarkerTask"):
        name = task.get("name")
        if name not in LOWER_BODY_MARKERS:
            task.find("weight").text = str(UPPER_BODY_WEIGHT)
            n_downweighted += 1

    ik_tree.write(ik_path_temp, pretty_print=True, xml_declaration=True, encoding="utf-8")
    opensim.InverseKinematicsTool(str(ik_path_temp)).run()
    Path(ik_path_temp).unlink()
    return n_downweighted


def read_mot_knee_angle(mot_path, leg):
    with open(mot_path) as f:
        lines = f.readlines()
    hdr_end = next(i for i, l in enumerate(lines) if l.strip().lower() == "endheader")
    col_names = lines[hdr_end + 1].strip().split("\t")
    data = pd.read_csv(mot_path, sep="\t", skiprows=hdr_end + 2, header=None, names=col_names)
    col = "knee_angle_r" if leg == "right" else "knee_angle_l"
    return data["time"].to_numpy(), data[col].to_numpy()


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    from Pose2Sim.kinematics import perform_scaling, get_opensim_setup_dir

    trials = rpc.discover_video_trials()[:N_TRIALS]
    print(f"Running on {len(trials)} trial(s).")

    osim_setup_dir = get_opensim_setup_dir()
    kinematics_dir = Path(OUT_DIR)

    rows = []
    with _make_landmarker(MODEL_PATH) as landmarker:
        for trial in trials:
            trial_key = trial["trial_key"]
            leg = trial["leg"]
            print(f"\n=== {trial_key} ({leg}) ===")
            try:
                opti_t, opti_ang = pt.load_optitrack(trial["optitrack_path"])
            except Exception as e:
                print(f"  [skip] OptiTrack load failed: {e}")
                continue

            cap = cv2.VideoCapture(str(trial["video_path"]))
            fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
            cap.release()

            t_list, world_rows, baseline_ang = extract_trial(trial["video_path"], landmarker, leg)
            n_valid = sum(1 for r in world_rows if r is not None)
            print(f"  {n_valid}/{len(world_rows)} frames with usable landmarks")
            if n_valid < 10:
                print("  [skip] too few valid frames")
                continue

            # Baseline (current production approach): raw 2D angle RMSE
            baseline_result = engine.compare_pair(
                opti_t, opti_ang, np.array(t_list), np.array(baseline_ang))
            baseline_rmse = baseline_result.get("rmse_deg") if baseline_result.get("status") == "ok" else None

            # OpenSim IK path
            trc_path = kinematics_dir / f"{trial_key}.trc"
            write_trc(trc_path, t_list, world_rows, fps)

            ik_rmse = None
            ik_error = None
            n_downweighted = None
            try:
                perform_scaling(trc_path, "BLAZEPOSE", kinematics_dir, osim_setup_dir,
                                use_simple_model=True, subject_height=GENERIC_HEIGHT_M,
                                subject_mass=GENERIC_MASS_KG, remove_scaling_setup=True)
                n_downweighted = perform_IK_lower_weighted(trc_path, kinematics_dir, osim_setup_dir)
                mot_path = kinematics_dir / f"{trial_key}.mot"
                mot_t, mot_ang = read_mot_knee_angle(mot_path, leg)
                # OpenSim's knee_angle_r/l is a flexion angle (0=full
                # extension, increases with flexion) -- the OPPOSITE
                # convention from knee_angle_from_points()'s interior
                # joint angle (180=straight, decreases with flexion, which
                # is what ground truth / baseline both use). Convert to the
                # same convention before comparing.
                ik_result = engine.compare_pair(opti_t, opti_ang, mot_t, 180.0 - mot_ang)
                if ik_result.get("status") == "ok":
                    ik_rmse = ik_result["rmse_deg"]
                else:
                    ik_error = f"compare_pair status: {ik_result.get('status')!r}"
            except Exception as e:
                ik_error = f"{type(e).__name__}: {e}"
                traceback.print_exc()

            print(f"  baseline (raw 2D angle) RMSE: {baseline_rmse}")
            print(f"  OpenSim IK (lower-body-weighted) RMSE: {ik_rmse}"
                 f"  (n_downweighted={n_downweighted}, error: {ik_error})")
            rows.append({
                "trial_key": trial_key, "leg": leg,
                "baseline_rmse_deg": baseline_rmse,
                "opensim_ik_lower_rmse_deg": ik_rmse,
                "n_upper_body_markers_downweighted": n_downweighted,
                "opensim_ik_error": ik_error,
            })

    df = pd.DataFrame(rows)
    out_csv = os.path.join(OUT_DIR, "prototype_results_pass2_lower_weighted.csv")
    df.to_csv(out_csv, index=False)
    print(f"\n-> {out_csv}")
    print(df.to_string(index=False))

    valid_baseline = df["baseline_rmse_deg"].dropna()
    valid_ik = df["opensim_ik_lower_rmse_deg"].dropna()
    print(f"\nbaseline mean RMSE (n={len(valid_baseline)}): "
         f"{valid_baseline.mean() if len(valid_baseline) else float('nan'):.2f} deg")
    print(f"OpenSim IK lower-weighted mean RMSE (n={len(valid_ik)}): "
         f"{valid_ik.mean() if len(valid_ik) else float('nan'):.2f} deg")
    print("(pass 1, full-body/unweighted IK, same 2 trials: 20.10 deg, 32.46 deg)")


if __name__ == "__main__":
    main()
