# tests/test_master_app_imu.py
"""Coverage for master_app.py's IMU recording lifecycle: the fused/split-CSV
path (pendulastic_imu_server.start_recording/stop_recording) and the raw
JSONL path (start_raw_log/stop_raw_log) this feature adds alongside it.

Follows tests/test_master_app_paths.py's convention: real MasterApp + real
tk.Tk(), no GUI automation, monkeypatched imu_server collaborator.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import tkinter as tk

import master_app


def _root():
    r = tk.Tk()
    r.withdraw()
    return r


def _app(root):
    os.makedirs(master_app.ROOT_DIR, exist_ok=True)
    return master_app.MasterApp(root)


def _teardown(app, root):
    if app is not None:
        if app.writing_flag.is_set():
            app.stop_recording()
        app._close_camera()
    root.destroy()


def test_start_imu_opens_raw_jsonl_alongside_csv(tmp_path, monkeypatch):
    r = _root()
    app = None
    try:
        app = _app(r)
        app.var_record_imu.set(True)

        calls = {}
        monkeypatch.setattr(master_app.imu_server, "start_recording",
                             lambda path, meta: calls.setdefault("csv_path", path) or True)
        monkeypatch.setattr(master_app.imu_server, "start_raw_log",
                             lambda path: calls.setdefault("raw_path", path))

        app._start_imu(str(tmp_path), "PYTESTIMU1", 3)

        assert calls["csv_path"] == os.path.join(str(tmp_path), "Trial_3_imu.csv")
        assert calls["raw_path"] == os.path.join(str(tmp_path), "Trial_3_imu_raw.jsonl")
        assert app._imu_recording is True
        assert app._imu_csv_path == calls["csv_path"]
        assert app._imu_raw_recording is True
        assert app._imu_raw_jsonl_path == calls["raw_path"]
    finally:
        _teardown(app, r)


def test_stop_imu_closes_both_independently():
    r = _root()
    app = None
    try:
        app = _app(r)
        stopped = []
        app._imu_recording = True
        app._imu_csv_path = "fake_csv_path"
        app._imu_raw_recording = True
        app._imu_raw_jsonl_path = "fake_raw_path"

        import master_app as _m
        orig_stop_recording = _m.imu_server.stop_recording
        orig_stop_raw_log = _m.imu_server.stop_raw_log
        _m.imu_server.stop_recording = lambda: stopped.append("csv")
        _m.imu_server.stop_raw_log = lambda: stopped.append("raw")
        try:
            app._stop_imu()
        finally:
            _m.imu_server.stop_recording = orig_stop_recording
            _m.imu_server.stop_raw_log = orig_stop_raw_log

        assert stopped == ["csv", "raw"]
        assert app._imu_recording is False
        assert app._imu_raw_recording is False
        assert app._imu_raw_jsonl_path == ""
    finally:
        _teardown(app, r)
