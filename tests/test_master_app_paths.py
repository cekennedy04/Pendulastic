# tests/test_master_app_paths.py
"""Regression coverage for master_app.py's recording save-path convention:
Recordings/Participant_<ID>/<Right|Left>/<Characterization>/Trial_<N>.avi.

No computer-use/GUI-automation tool exists in this project's environment for
driving a local Windows desktop Tkinter app, so these tests exercise the real
MasterApp object and its real widgets directly (no mainloop, no mocks) rather
than simulating clicks -- the same approach test_acquisition_panel.py already
uses for pendulastic_app.py's AcquisitionPanel.
"""
import json
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import tkinter as tk

import master_app


def _root():
    r = tk.Tk()
    r.withdraw()
    return r


def _set(entry, text):
    entry.delete(0, tk.END)
    entry.insert(0, text)


def _app(root):
    os.makedirs(master_app.ROOT_DIR, exist_ok=True)
    return master_app.MasterApp(root)


def _teardown(app, root):
    """Release any real camera/recording state before destroying root, so a
    background streaming thread doesn't outlive it (and so a locked video
    file doesn't block _cleanup_participant's rmtree)."""
    if app is not None:
        if app.writing_flag.is_set():
            app.stop_recording()
        app._close_camera()
    root.destroy()


def _cleanup_participant(pid):
    p = os.path.join(master_app.ROOT_DIR, f"Participant_{pid}")
    if os.path.isdir(p):
        shutil.rmtree(p)


# ---------------------------------------------------------------------------
# GUI field shape: Position/Height removed, Leg + Characterization present
# ---------------------------------------------------------------------------

def test_position_height_session_fields_removed():
    r = _root()
    app = None
    try:
        app = _app(r)
        for name in ("var_pos", "var_height", "var_session",
                     "drop_pos", "drop_height", "drop_session"):
            assert not hasattr(app, name), f"{name} should have been removed"
    finally:
        _teardown(app, r)


def test_leg_field_defaults():
    r = _root()
    app = None
    try:
        app = _app(r)
        assert app.var_leg.get() == "Right"
        assert list(app.drop_leg["values"]) == ["Right", "Left"]
        assert str(app.drop_leg["state"]) == "readonly"
    finally:
        _teardown(app, r)


def test_characterization_field_defaults():
    r = _root()
    app = None
    try:
        app = _app(r)
        assert app.entry_characterization.get() == ""
    finally:
        _teardown(app, r)


# ---------------------------------------------------------------------------
# _validate_inputs: Characterization validation
# ---------------------------------------------------------------------------

def test_validate_inputs_rejects_blank_characterization():
    r = _root()
    app = None
    try:
        app = _app(r)
        _set(app.entry_id, "PYTESTVAL1")
        _set(app.entry_characterization, "")
        try:
            app._validate_inputs()
            assert False, "expected ValueError"
        except ValueError as e:
            assert str(e) == "Characterization cannot be empty."
    finally:
        _teardown(app, r)


def test_validate_inputs_rejects_whitespace_only_characterization():
    r = _root()
    app = None
    try:
        app = _app(r)
        _set(app.entry_id, "PYTESTVAL1")
        _set(app.entry_characterization, "   ")
        try:
            app._validate_inputs()
            assert False, "expected ValueError"
        except ValueError as e:
            assert str(e) == "Characterization cannot be empty."
    finally:
        _teardown(app, r)


def test_validate_inputs_rejects_illegal_char_characterization():
    r = _root()
    app = None
    try:
        app = _app(r)
        _set(app.entry_id, "PYTESTVAL1")
        _set(app.entry_characterization, "pre/post")
        try:
            app._validate_inputs()
            assert False, "expected ValueError"
        except ValueError as e:
            assert str(e) == 'Characterization contains illegal characters: < > : " / \\ | ? *'
    finally:
        _teardown(app, r)


def test_validate_inputs_rejects_dot_and_dotdot_characterization():
    r = _root()
    app = None
    try:
        app = _app(r)
        for bad in (".", ".."):
            _set(app.entry_id, "PYTESTVAL1")
            _set(app.entry_characterization, bad)
            try:
                app._validate_inputs()
                assert False, f"expected ValueError for {bad!r}"
            except ValueError as e:
                assert str(e) == 'Characterization cannot be "." or "..".'
    finally:
        _teardown(app, r)


def test_validate_inputs_accepts_valid_characterization():
    r = _root()
    app = None
    try:
        app = _app(r)
        _set(app.entry_id, "PYTESTVAL1")
        _set(app.entry_characterization, "pre")
        assert app._validate_inputs() == "PYTESTVAL1"
    finally:
        _teardown(app, r)


# ---------------------------------------------------------------------------
# _build_paths / _write_metadata: new two-level structure
# ---------------------------------------------------------------------------

def test_build_paths_creates_two_level_structure():
    r = _root()
    app = None
    pid = "PYTESTPATH1"
    try:
        app = _app(r)
        _set(app.entry_id, pid)
        app.var_leg.set("Left")
        _set(app.entry_characterization, "post")
        app.var_trial.set("2")

        participant_dir, video_path, rel_path = app._build_paths(pid)

        assert video_path == os.path.join(master_app.ROOT_DIR,
                                           f"Participant_{pid}", "Left", "post", "Trial_2.avi")
        assert rel_path == os.path.join(f"Participant_{pid}", "Left", "post")
        assert os.path.isdir(os.path.dirname(video_path))
    finally:
        _teardown(app, r)
        _cleanup_participant(pid)


def test_write_metadata_has_leg_and_characterization_keys():
    r = _root()
    app = None
    pid = "PYTESTMETA1"
    try:
        app = _app(r)
        _set(app.entry_id, pid)
        app.var_leg.set("Left")
        _set(app.entry_characterization, "post")
        app.var_trial.set("2")

        participant_dir, video_path, rel_path = app._build_paths(pid)
        app._write_metadata(participant_dir, pid)

        with open(os.path.join(participant_dir, "metadata.json")) as f:
            meta = json.load(f)
        last_trial = meta["last_trial"]

        assert last_trial["leg"] == "Left"
        assert last_trial["characterization"] == "post"
        assert not ({"camera_position", "camera_height", "session"} & set(last_trial.keys()))
    finally:
        _teardown(app, r)
        _cleanup_participant(pid)


# ---------------------------------------------------------------------------
# start_recording: overwrite confirmation for a repeated Leg/Characterization/Trial
# ---------------------------------------------------------------------------

def test_start_recording_prompts_before_overwriting_existing_trial(monkeypatch):
    r = _root()
    app = None
    pid = "PYTESTOVW1"
    try:
        app = _app(r)
        # Skip the (unrelated) IMU-readiness prompt so this test is deterministic
        # regardless of whether a phone happens to be connected in this run.
        app.var_record_imu.set(False)
        _set(app.entry_id, pid)
        app.var_leg.set("Right")
        _set(app.entry_characterization, "pre")
        app.var_trial.set("1")

        # Pre-create the trial's video file, simulating an already-recorded take.
        _, video_path, _ = app._build_paths(pid)
        with open(video_path, "wb") as f:
            f.write(b"placeholder")

        asked = {}
        def fake_askyesno(title, message):
            asked["title"] = title
            asked["message"] = message
            return False  # operator declines the overwrite
        monkeypatch.setattr(master_app.messagebox, "askyesno", fake_askyesno)

        app.start_recording()

        assert asked.get("title") == "Trial Already Recorded"
        assert "Trial_1.avi" in asked.get("message", "")
        assert not app.writing_flag.is_set()
        # The placeholder file must be untouched -- declining must not overwrite it.
        with open(video_path, "rb") as f:
            assert f.read() == b"placeholder"
    finally:
        _teardown(app, r)
        _cleanup_participant(pid)


def test_start_recording_does_not_prompt_for_a_new_trial(monkeypatch):
    r = _root()
    app = None
    pid = "PYTESTOVW2"
    try:
        app = _app(r)
        # Skip the IMU-readiness prompt (unrelated to this test) and force the
        # camera-open path to fail deterministically -- this test must not
        # depend on whether real camera hardware happens to be attached to
        # whatever machine runs the suite. __init__'s rescan_cameras() may
        # already have opened a real camera and set streaming_flag, in which
        # case start_recording()'s "is it already live" guard would skip
        # _open_camera() entirely and actually start recording -- clear that
        # state first so the mocked _open_camera() is what actually gets hit.
        app.var_record_imu.set(False)
        app._close_camera()
        monkeypatch.setattr(app, "_open_camera", lambda: False)
        _set(app.entry_id, pid)
        app.var_leg.set("Right")
        _set(app.entry_characterization, "pre")
        app.var_trial.set("1")

        calls = []
        monkeypatch.setattr(master_app.messagebox, "askyesno",
                             lambda title, message: calls.append(title) or False)

        # No pre-existing video file for this trial -- the overwrite prompt
        # must never fire, and start_recording() proceeds to (forced) camera
        # failure instead.
        app.start_recording()

        assert calls == [], f"askyesno should not have been called, was: {calls}"
        assert not app.writing_flag.is_set()
        assert "already exists" not in app.var_status.get()
    finally:
        _teardown(app, r)
        _cleanup_participant(pid)
