import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import reconstruct_imu_raw_logs as rc


def _write_csv(path, rows):
    header = "timestamp_ms,phone_ts_ms,role,sensor_name,x,y,z\n"
    with open(path, "w", encoding="utf-8") as f:
        f.write(header)
        for r in rows:
            f.write(",".join(str(v) for v in r) + "\n")


def test_read_split_csv_converts_ms_to_seconds_and_maps_sensor_name(tmp_path):
    csv_path = tmp_path / "Trial_1_accel.csv"
    _write_csv(csv_path, [[1785948932150.979, 1785948931731, "proximal", "Accelerometer",
                          0.874252, -0.087570, 0.383102]])
    samples = rc._read_split_csv(str(csv_path))
    assert len(samples) == 1
    s = samples[0]
    assert s["t"] == 1785948932150.979 / 1000.0
    assert s["sensor"] == "accel"
    assert s["role"] == "proximal"
    assert s["v"] == [0.874252, -0.087570, 0.383102]
    assert s["phone_ts_ms"] == 1785948931731


def test_read_split_csv_skips_unrecognized_sensor_name(tmp_path):
    csv_path = tmp_path / "Trial_1_weird.csv"
    _write_csv(csv_path, [[1000.0, 1000, "proximal", "Barometer", 1, 2, 3]])
    assert rc._read_split_csv(str(csv_path)) == []


def test_reconstruct_trial_merges_and_sorts_chronologically(tmp_path):
    accel = tmp_path / "Trial_1_accel.csv"
    gyro = tmp_path / "Trial_1_gyro.csv"
    mag = tmp_path / "Trial_1_mag.csv"
    _write_csv(accel, [[2000.0, 2000, "proximal", "Accelerometer", 1, 1, 1]])
    _write_csv(gyro, [[1000.0, 1000, "proximal", "Gyroscope", 2, 2, 2]])
    _write_csv(mag, [[3000.0, 3000, "proximal", "Magnetometer", 3, 3, 3]])

    samples = rc.reconstruct_trial(str(accel), str(gyro), str(mag))
    assert [s["sensor"] for s in samples] == ["gyro", "accel", "mag"]   # sorted by t
    assert [s["t"] for s in samples] == [1.0, 2.0, 3.0]


def test_reconstruct_trial_handles_missing_sibling_gracefully(tmp_path):
    accel = tmp_path / "Trial_1_accel.csv"
    _write_csv(accel, [[1000.0, 1000, "proximal", "Accelerometer", 1, 1, 1]])
    samples = rc.reconstruct_trial(str(accel), str(tmp_path / "no_such_gyro.csv"), None)
    assert len(samples) == 1
