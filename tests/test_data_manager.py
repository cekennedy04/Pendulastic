# tests/test_data_manager.py
import csv, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from pendulastic_app import DataManager


def test_build_filename_basic():
    assert DataManager.build_filename("P1", "right", "MS", 1) == \
        "PID_P1_LEG_Right_MS_TRIAL_1.csv"


def test_build_filename_spaces_become_underscores():
    assert DataManager.build_filename("P2", "left", "Unaffected Control", 3) == \
        "PID_P2_LEG_Left_Unaffected_Control_TRIAL_3.csv"


def test_build_filename_leg_capitalised():
    assert DataManager.build_filename("P3", "LEFT", "Stroke", 2) == \
        "PID_P3_LEG_Left_Stroke_TRIAL_2.csv"


def test_save_trial_creates_file(tmp_path, monkeypatch):
    monkeypatch.setattr(DataManager, "DATA_DIR", str(tmp_path))
    meta = {"pid": "P1", "leg": "Right", "ms_status": "MS",
            "trial": 1, "methodology": "imu"}
    path = DataManager.save_trial("test.csv", [170.0, 165.0], meta, fps=30.0)
    assert os.path.isfile(path)


def test_save_trial_csv_headers(tmp_path, monkeypatch):
    monkeypatch.setattr(DataManager, "DATA_DIR", str(tmp_path))
    meta = {"pid": "P1", "leg": "Right", "ms_status": "MS",
            "trial": 1, "methodology": "rgb"}
    DataManager.save_trial("test.csv", [170.0, 165.0], meta, fps=30.0)
    with open(tmp_path / "test.csv") as f:
        header = next(csv.reader(f))
    assert header == ["frame", "time_s", "knee_angle_deg",
                      "pid", "leg", "ms_status", "trial", "methodology"]


def test_save_trial_fps_timestamps(tmp_path, monkeypatch):
    monkeypatch.setattr(DataManager, "DATA_DIR", str(tmp_path))
    meta = {"pid": "P1", "leg": "Right", "ms_status": "MS",
            "trial": 1, "methodology": "rgb"}
    DataManager.save_trial("test.csv", [170.0, 165.0], meta, fps=10.0)
    with open(tmp_path / "test.csv") as f:
        rows = list(csv.reader(f))
    assert rows[1][1] == "0.0000"   # frame 0: 0/10
    assert rows[2][1] == "0.1000"   # frame 1: 1/10


def test_save_trial_explicit_timestamps(tmp_path, monkeypatch):
    monkeypatch.setattr(DataManager, "DATA_DIR", str(tmp_path))
    meta = {"pid": "P1", "leg": "Right", "ms_status": "MS",
            "trial": 1, "methodology": "imu"}
    DataManager.save_trial("test.csv", [170.0, 165.0], meta,
                           timestamps=[1000.0, 1000.5])
    with open(tmp_path / "test.csv") as f:
        rows = list(csv.reader(f))
    assert rows[1][1] == "0.0000"   # t[0] - t[0] = 0
    assert rows[2][1] == "0.5000"   # t[1] - t[0] = 0.5


def test_build_filename_with_imu_source():
    assert DataManager.build_filename("P1", "right", "MS", 1, source="imu") == \
        "PID_P1_LEG_Right_MS_TRIAL_1_imu.csv"


def test_build_filename_with_rgb_source():
    assert DataManager.build_filename("P2", "left", "MS", 3, source="rgb") == \
        "PID_P2_LEG_Left_MS_TRIAL_3_rgb.csv"


def test_build_filename_source_none_backward_compat():
    """source=None must produce the same output as the old 4-arg call."""
    assert DataManager.build_filename("P1", "right", "MS", 1, source=None) == \
        "PID_P1_LEG_Right_MS_TRIAL_1.csv"


def test_save_trial_source_param_written_to_csv(tmp_path, monkeypatch):
    monkeypatch.setattr(DataManager, "DATA_DIR", str(tmp_path))
    meta = {"pid": "P1", "leg": "Right", "ms_status": "MS", "trial": 1,
            "sources": ["imu"]}  # new-style metadata (no "methodology" key)
    DataManager.save_trial("test_imu.csv", [170.0], meta, source="imu")
    with open(tmp_path / "test_imu.csv") as f:
        rows = list(csv.reader(f))
    # methodology column (index 7) should contain the source name
    assert rows[1][7] == "imu"


def test_save_trial_backward_compat_methodology_key(tmp_path, monkeypatch):
    """Old callers that pass metadata["methodology"] still work when source=None."""
    monkeypatch.setattr(DataManager, "DATA_DIR", str(tmp_path))
    meta = {"pid": "P1", "leg": "Right", "ms_status": "MS", "trial": 1,
            "methodology": "rgb"}
    DataManager.save_trial("test.csv", [170.0], meta)
    with open(tmp_path / "test.csv") as f:
        rows = list(csv.reader(f))
    assert rows[1][7] == "rgb"
