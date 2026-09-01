# tests/test_master_app_coverage.py
"""master_app.py's marker-coverage panel: the pre-flight check and the live
readout.

Follows tests/test_master_app_imu.py's convention -- real MasterApp, real
tk.Tk(), no GUI automation, collaborator monkeypatched. The mocap stream is
faked throughout; nothing here needs Motive.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import tkinter as tk

import capture_coverage as cc
import capture_coverage_session as ccs
import master_app


def _root():
    r = tk.Tk()
    r.withdraw()
    return r


def _app(root):
    os.makedirs(master_app.ROOT_DIR, exist_ok=True)
    return master_app.MasterApp(root)


def _teardown(app, root):
    """Matches tests/test_master_app_imu.py's teardown, plus the mocap stream.

    Closing the camera matters: the capture thread posts back with
    root.after(0, ...), and destroying the root while it still runs raises
    "main thread is not in main loop" from that thread.
    """
    if app is not None:
        if app.writing_flag.is_set():
            app.stop_recording()
        try:
            app._coverage_stop()
        except Exception:
            pass
        app._close_camera()
    root.destroy()


class _RB:
    def __init__(self, valid=True):
        self.tracking_valid = valid


class _FakeClient:
    def __init__(self):
        self.on_frame = None
        self.started = False
        self.stopped = False

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True


def _install_fake(app, client=None):
    """Give the app a session backed by a fake stream."""
    client = client or _FakeClient()
    app._cov_session = ccs.CoverageSession(client_factory=lambda: client)
    return client


def _drive(app, n, thigh=True, shank=True, fps=120.0, start=0.0):
    for i in range(n):
        app._cov_session.feed_frame(i, start + i / fps,
                                    _RB(thigh), _RB(shank))


# ── the panel exists and degrades gracefully ─────────────────────────────────

def test_the_panel_is_built():
    root = _root(); app = None
    try:
        app = _app(root)
        assert app.btn_coverage is not None
        assert app.lbl_coverage is not None
        assert "CHECK COVERAGE" in app.btn_coverage.cget("text")
    finally:
        _teardown(app, root)


def test_the_app_starts_with_no_session_open():
    """The socket must not be opened until asked -- an operator who never runs
    a check should not have a listener running."""
    root = _root(); app = None
    try:
        app = _app(root)
        assert app._cov_session is None
    finally:
        _teardown(app, root)


def test_a_stream_that_will_not_open_shows_a_message_and_does_not_raise():
    """Motive closed is the normal case when someone opens the app at a desk."""
    root = _root(); app = None
    try:
        app = _app(root)

        def boom():
            raise OSError("no route to host")

        app._cov_session = ccs.CoverageSession(client_factory=boom)
        app._on_check_coverage()          # must not raise
        assert "Motive" in app.lbl_coverage.cget("text")
        assert app.btn_coverage.cget("state") == "normal"
    finally:
        _teardown(app, root)


# ── the pre-flight check ─────────────────────────────────────────────────────

def test_a_good_setup_passes_and_re_enables_the_button(monkeypatch):
    root = _root(); app = None
    try:
        app = _app(root)
        _install_fake(app)
        shown = []
        monkeypatch.setattr(master_app.messagebox, "showinfo",
                            lambda *a, **k: shown.append(("info", a)))
        monkeypatch.setattr(master_app.messagebox, "showwarning",
                            lambda *a, **k: shown.append(("warn", a)))
        app._cov_session.start()
        app._cov_session.begin_preflight(duration_s=0.0)
        _drive(app, 600)
        app._poll_preflight()
        assert shown and shown[0][0] == "info"
        assert app.btn_coverage.cget("state") == "normal"
        assert "OK" in app.lbl_coverage.cget("text")
    finally:
        _teardown(app, root)


def test_the_p22_shape_warns_and_names_the_shank(monkeypatch):
    """A leg whose shank is never seen -- the failure that cost P22 its left
    leg. The operator must be told which cluster, in a warning not an info."""
    root = _root(); app = None
    try:
        app = _app(root)
        _install_fake(app)
        shown = []
        monkeypatch.setattr(master_app.messagebox, "showinfo",
                            lambda *a, **k: shown.append(("info", a)))
        monkeypatch.setattr(master_app.messagebox, "showwarning",
                            lambda *a, **k: shown.append(("warn", a)))
        app._cov_session.start()
        app._cov_session.begin_preflight(duration_s=0.0)
        _drive(app, 600, shank=False)
        app._poll_preflight()
        assert shown and shown[0][0] == "warn"
        assert "shank" in shown[0][1][1]
    finally:
        _teardown(app, root)


def test_the_button_is_disabled_while_a_check_runs():
    root = _root(); app = None
    try:
        app = _app(root)
        _install_fake(app)
        app._on_check_coverage()
        assert app.btn_coverage.cget("state") == "disabled"
        assert "Watching" in app.lbl_coverage.cget("text")
    finally:
        _teardown(app, root)


# ── the live readout ─────────────────────────────────────────────────────────

def test_the_live_readout_reflects_current_tracking():
    root = _root(); app = None
    try:
        app = _app(root)
        _install_fake(app)
        app._cov_session.start()
        _drive(app, 600)
        app._poll_coverage_live()
        assert "OK" in app.lbl_coverage.cget("text")

        _drive(app, 600, shank=False, start=10.0)
        app._poll_coverage_live()
        assert "OK" not in app.lbl_coverage.cget("text")
    finally:
        _teardown(app, root)


def test_the_live_poll_does_not_fight_a_running_preflight():
    """Both write the same label; the check owns it while it runs."""
    root = _root(); app = None
    try:
        app = _app(root)
        _install_fake(app)
        app._cov_session.start()
        _drive(app, 600)
        app._cov_session.begin_preflight(duration_s=60.0)
        app.lbl_coverage.config(text="Watching... 5s")
        app._poll_coverage_live()
        assert "Watching" in app.lbl_coverage.cget("text")
    finally:
        _teardown(app, root)


def test_the_live_poll_is_a_noop_without_a_session():
    root = _root(); app = None
    try:
        app = _app(root)
        app._poll_coverage_live()          # must not raise
    finally:
        _teardown(app, root)


# ── teardown ─────────────────────────────────────────────────────────────────

def test_closing_stops_the_stream():
    """A listening socket must not outlive the window."""
    root = _root(); app = None
    try:
        app = _app(root)
        client = _install_fake(app)
        app._cov_session.start()
        app._coverage_stop()
        assert client.stopped
    finally:
        _teardown(app, root)


def test_stopping_without_a_session_is_safe():
    root = _root(); app = None
    try:
        app = _app(root)
        app._coverage_stop()               # must not raise
    finally:
        _teardown(app, root)
