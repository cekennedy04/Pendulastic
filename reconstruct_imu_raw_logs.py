"""
reconstruct_imu_raw_logs.py
============================
Reconstructs the raw IMU sample-stream format imu_calibration_tuner.py's
replay_trial() expects ({"t", "role", "sensor", "v", "phone_ts_ms"} per
sample, chronologically sorted) from the split Trial_{n}_{accel,gyro,mag}.csv
files already saved for real recorded trials.

No real participant ever had a genuine *_imu_raw.jsonl log saved (that
format only exists for dev/test recordings under data/) -- but the split
CSVs were written from the exact same live event stream
(pendulastic_imu_server._log_raw_csv), just reshaped by sensor type with an
extra timestamp_ms = t*1000 column, so converting back is a lossless format
change, not an approximation.

Run:
    .venv\\Scripts\\python.exe reconstruct_imu_raw_logs.py
"""
from __future__ import annotations

import csv
import glob
import json
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
REC_ROOT = os.path.join(BASE_DIR, "Recordings")
OUT_DIR = os.path.join(BASE_DIR, "Model_Analysis_Outputs", "Reconstructed_IMU_Raw_Logs")

_SENSOR_NAME_MAP = {"accelerometer": "accel", "gyroscope": "gyro", "magnetometer": "mag"}


def _read_split_csv(path):
    """One split CSV (accel/gyro/mag) -> list of raw-sample dicts."""
    samples = []
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            sensor = _SENSOR_NAME_MAP.get((row.get("sensor_name") or "").strip().lower())
            if sensor is None:
                continue
            try:
                samples.append({
                    "t": float(row["timestamp_ms"]) / 1000.0,
                    "role": row["role"].strip(),
                    "sensor": sensor,
                    "v": [float(row["x"]), float(row["y"]), float(row["z"])],
                    "phone_ts_ms": int(float(row["phone_ts_ms"])),
                })
            except (ValueError, KeyError):
                continue
    return samples


def reconstruct_trial(accel_csv, gyro_csv, mag_csv):
    """Merge the three split CSVs for one trial into one chronologically-
    sorted raw sample list, matching *_imu_raw.jsonl's exact schema."""
    samples = []
    for path in (accel_csv, gyro_csv, mag_csv):
        if path and os.path.isfile(path):
            samples.extend(_read_split_csv(path))
    samples.sort(key=lambda s: s["t"])
    return samples


def discover_split_imu_trials():
    """Every Trial_{n}_accel.csv under Recordings/ that has matching
    gyro/mag siblings in the same directory. Returns a list of
    (accel_path, gyro_path, mag_path, label) tuples."""
    out = []
    for accel_csv in sorted(glob.glob(os.path.join(REC_ROOT, "**", "Trial_*_accel.csv"), recursive=True)):
        base = accel_csv[:-len("_accel.csv")]
        gyro_csv = base + "_gyro.csv"
        mag_csv = base + "_mag.csv"
        if os.path.isfile(gyro_csv) and os.path.isfile(mag_csv):
            label = os.path.relpath(base, REC_ROOT).replace(os.sep, "_")
            out.append((accel_csv, gyro_csv, mag_csv, label))
    return out


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    trials = discover_split_imu_trials()
    print(f"{len(trials)} trial(s) with accel+gyro+mag CSVs found.")
    written = []
    for accel_csv, gyro_csv, mag_csv, label in trials:
        samples = reconstruct_trial(accel_csv, gyro_csv, mag_csv)
        if not samples:
            print(f"  [skip] {label} -- no valid samples reconstructed")
            continue
        out_path = os.path.join(OUT_DIR, f"{label}_imu_raw.jsonl")
        with open(out_path, "w", encoding="utf-8") as f:
            for s in samples:
                f.write(json.dumps(s) + "\n")
        written.append(out_path)
        print(f"  -> {out_path}  ({len(samples)} samples)")
    return written


if __name__ == "__main__":
    main()
