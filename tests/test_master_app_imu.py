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


def test_start_imu_csv_exception_does_not_block_raw_log(tmp_path, monkeypatch):
    r = _root()
    app = None
    try:
        app = _app(r)
        app.var_record_imu.set(True)
        monkeypatch.setattr(master_app.messagebox, "showwarning",
                             lambda title, message: None)

        def fake_start_recording(path, meta):
            raise RuntimeError("disk full")
        calls = {}
        monkeypatch.setattr(master_app.imu_server, "start_recording", fake_start_recording)
        monkeypatch.setattr(master_app.imu_server, "start_raw_log",
                             lambda path: calls.setdefault("raw_path", path))

        app._start_imu(str(tmp_path), "PYTESTIMU2", 1)

        assert app._imu_recording is False
        assert app._imu_csv_path == ""
        assert app._imu_raw_recording is True
        assert calls["raw_path"] == os.path.join(str(tmp_path), "Trial_1_imu_raw.jsonl")
    finally:
        _teardown(app, r)


def test_start_imu_raw_log_exception_does_not_block_csv(tmp_path, monkeypatch):
    r = _root()
    app = None
    try:
        app = _app(r)
        app.var_record_imu.set(True)
        warnings = []
        monkeypatch.setattr(master_app.messagebox, "showwarning",
                             lambda title, message: warnings.append(message))

        monkeypatch.setattr(master_app.imu_server, "start_recording",
                             lambda path, meta: True)

        # A non-OSError on purpose: proves the new raw-log catch is
        # `except Exception`, broader than pendulastic_app.py's `except
        # OSError`-only sibling implementation (Global Constraints).
        def fake_start_raw_log(path):
            raise RuntimeError("unexpected server error")
        monkeypatch.setattr(master_app.imu_server, "start_raw_log", fake_start_raw_log)

        app._start_imu(str(tmp_path), "PYTESTIMU3", 1)

        assert app._imu_recording is True
        assert app._imu_raw_recording is False
        assert app._imu_raw_jsonl_path == ""
        assert any("raw JSONL" in w for w in warnings)
    finally:
        _teardown(app, r)


def test_start_imu_csv_returns_false_raw_log_still_succeeds(tmp_path, monkeypatch):
    r = _root()
    app = None
    try:
        app = _app(r)
        app.var_record_imu.set(True)
        monkeypatch.setattr(master_app.imu_server, "start_recording",
                             lambda path, meta: False)
        monkeypatch.setattr(master_app.imu_server, "start_raw_log",
                             lambda path: None)

        app._start_imu(str(tmp_path), "PYTESTIMU4", 1)

        assert app._imu_recording is False
        assert app._imu_raw_recording is True
    finally:
        _teardown(app, r)


def test_stop_imu_stops_raw_log_even_if_csv_stop_raises():
    r = _root()
    app = None
    try:
        app = _app(r)
        app._imu_recording = True
        app._imu_raw_recording = True
        app._imu_raw_jsonl_path = "fake_raw_path"

        import master_app as _m
        orig_stop_recording = _m.imu_server.stop_recording
        orig_stop_raw_log = _m.imu_server.stop_raw_log
        raw_stopped = []
        _m.imu_server.stop_recording = lambda: (_ for _ in ()).throw(RuntimeError("boom"))
        _m.imu_server.stop_raw_log = lambda: raw_stopped.append(True)
        try:
            app._stop_imu()
        finally:
            _m.imu_server.stop_recording = orig_stop_recording
            _m.imu_server.stop_raw_log = orig_stop_raw_log

        assert raw_stopped == [True]
        assert app._imu_recording is False
        assert app._imu_raw_recording is False
    finally:
        _teardown(app, r)


def test_stop_imu_stops_csv_even_if_raw_log_stop_raises():
    r = _root()
    app = None
    try:
        app = _app(r)
        app._imu_recording = True
        app._imu_raw_recording = True
        app._imu_raw_jsonl_path = "fake_raw_path"

        import master_app as _m
        orig_stop_recording = _m.imu_server.stop_recording
        orig_stop_raw_log = _m.imu_server.stop_raw_log
        csv_stopped = []
        _m.imu_server.stop_recording = lambda: csv_stopped.append(True)
        _m.imu_server.stop_raw_log = lambda: (_ for _ in ()).throw(RuntimeError("boom"))
        try:
            app._stop_imu()
        finally:
            _m.imu_server.stop_recording = orig_stop_recording
            _m.imu_server.stop_raw_log = orig_stop_raw_log

        assert csv_stopped == [True]
        assert app._imu_recording is False
        assert app._imu_raw_recording is False
        assert app._imu_raw_jsonl_path == ""
    finally:
        _teardown(app, r)


def test_repeated_start_imu_calls_leave_no_stale_state(tmp_path, monkeypatch):
    r = _root()
    app = None
    try:
        app = _app(r)
        app.var_record_imu.set(True)
        monkeypatch.setattr(master_app.messagebox, "showwarning",
                             lambda title, message: None)

        # First trial: raw log fails.
        monkeypatch.setattr(master_app.imu_server, "start_recording",
                             lambda path, meta: True)
        monkeypatch.setattr(master_app.imu_server, "start_raw_log",
                             lambda path: (_ for _ in ()).throw(OSError("nope")))
        app._start_imu(str(tmp_path), "PYTESTIMU5", 1)
        assert app._imu_raw_recording is False

        # Second trial: raw log succeeds. Must not inherit trial 1's failure.
        monkeypatch.setattr(master_app.imu_server, "start_raw_log",
                             lambda path: None)
        app._start_imu(str(tmp_path), "PYTESTIMU5", 2)
        assert app._imu_raw_recording is True
        assert app._imu_raw_jsonl_path == os.path.join(str(tmp_path), "Trial_2_imu_raw.jsonl")
    finally:
        _teardown(app, r)


def test_overwrite_confirmation_names_raw_jsonl_log(monkeypatch):
    import shutil

    r = _root()
    app = None
    pid = "PYTESTIMUOVW1"
    try:
        app = _app(r)
        app.var_record_imu.set(False)  # skip the unrelated IMU-readiness prompt
        app.entry_id.delete(0, tk.END)
        app.entry_id.insert(0, pid)
        app.var_leg.set("Right")
        app.entry_characterization.delete(0, tk.END)
        app.entry_characterization.insert(0, "pre")
        app.var_trial.set("1")

        _, video_path, _ = app._build_paths(pid)
        with open(video_path, "wb") as f:
            f.write(b"placeholder")

        asked = {}
        def fake_askyesno(title, message):
            asked["message"] = message
            return False
        monkeypatch.setattr(master_app.messagebox, "askyesno", fake_askyesno)

        app.start_recording()

        assert "raw IMU JSONL" in asked["message"]
    finally:
        if app is not None:
            if app.writing_flag.is_set():
                app.stop_recording()
            app._close_camera()
        r.destroy()
        p = os.path.join(master_app.ROOT_DIR, f"Participant_{pid}")
        if os.path.isdir(p):
            shutil.rmtree(p)
