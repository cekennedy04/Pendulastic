import os, sys, json
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import numpy as np
import tune_imu


def _write_jsonl(path, samples):
    with open(path, "w", encoding="utf-8") as f:
        for s in samples:
            f.write(json.dumps(s) + "\n")


def test_load_raw_log_parses_jsonl(tmp_path):
    path = tmp_path / "a.jsonl"
    _write_jsonl(path, [
        {"t": 0.0, "role": "distal", "sensor": "gyro", "v": [0, 0, 0], "phone_ts_ms": 0},
        {"t": 0.01, "role": "distal", "sensor": "gyro", "v": [0, 1, 0], "phone_ts_ms": 10},
    ])
    samples = tune_imu.load_raw_log(str(path))
    assert len(samples) == 2
    assert samples[1]["v"] == [0, 1, 0]


def test_load_raw_log_skips_malformed_lines(tmp_path):
    path = tmp_path / "b.jsonl"
    path.write_text(
        '{"t": 0.0, "role": "distal", "sensor": "gyro", "v": [0,0,0], "phone_ts_ms": 0}\n'
        'not valid json\n'
        '{"t": 0.02, "role": "distal", "sensor": "gyro", "v": [0,0,1], "phone_ts_ms": 20}\n',
        encoding="utf-8")
    samples = tune_imu.load_raw_log(str(path))
    assert len(samples) == 2


def test_main_averages_penalty_across_multiple_logs(tmp_path, monkeypatch, capsys):
    path1 = tmp_path / "a.jsonl"
    path2 = tmp_path / "b.jsonl"
    _write_jsonl(path1, [{"t": 0.0, "role": "distal", "sensor": "gyro",
                         "v": [0, 0, 0], "phone_ts_ms": 0}])
    _write_jsonl(path2, [{"t": 0.0, "role": "distal", "sensor": "gyro",
                         "v": [0, 0, 0], "phone_ts_ms": 0}])

    monkeypatch.setattr(tune_imu, "TUNING_GRID", [
        {"beta": 0.041, "ema_alpha": 0.3, "flex_axis_capture": True, "gravity_seed": True},
    ])
    monkeypatch.setattr(tune_imu, "replay_trial",
                        lambda raw, p: (np.array([0.0, 0.05]), np.array([180.0, 175.0])))
    monkeypatch.setattr(tune_imu, "score_waveform",
                        lambda t, a: {"passes": True, "penalty": 1.0, "params": None})
    monkeypatch.setattr(tune_imu, "save_config", lambda cfg: None)
    monkeypatch.setattr(tune_imu, "load_config",
                        lambda: {"beta": 0.041, "ema_alpha": 0.3,
                                "flex_axis_capture": True, "gravity_seed": True,
                                "penalty": None, "passes": False,
                                "tuned_at": None, "source_trial": None})

    tune_imu.main([str(path1), str(path2)])
    out = capsys.readouterr().out
    assert "beta" in out.lower() or "0.041" in out
