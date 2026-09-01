"""
Tests for capture_coverage.py.

The failure this guards against is only reproducible with real hardware in the
room, so the decision logic is deliberately pure and every case here is driven
by a synthetic frame stream. Several cases replay measured shapes from the real
corpus so the thresholds are checked against what actually happens, not against
what would be convenient.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import capture_coverage as cc

FPS = 120.0


def _feed(monitor, pattern, fps=FPS, start=0.0):
    """pattern: iterable of (thigh_ok, shank_ok) per frame."""
    for i, (t_ok, s_ok) in enumerate(pattern):
        monitor.feed(start + i / fps, t_ok, s_ok)
    return monitor


def _steady(n, thigh=True, shank=True):
    return [(thigh, shank)] * n


# ── statistics ───────────────────────────────────────────────────────────────

def test_perfect_tracking_reports_full_coverage_and_one_unbroken_run():
    m = _feed(cc.CoverageMonitor(), _steady(600))          # 5 s at 120 Hz
    s = m.stats()
    assert s.coverage == 1.0
    assert s.longest_continuous_s == pytest.approx(599 / FPS, abs=0.01)
    assert s.thigh_coverage == 1.0 and s.shank_coverage == 1.0


def test_longest_run_is_measured_in_time_not_frames():
    """So the threshold means the same thing if the rig is ever run at another
    rate."""
    at120 = _feed(cc.CoverageMonitor(), _steady(240), fps=120.0).stats()
    at60 = _feed(cc.CoverageMonitor(), _steady(120), fps=60.0).stats()
    assert at120.longest_continuous_s == pytest.approx(at60.longest_continuous_s, abs=0.02)


def test_coverage_can_be_high_while_the_longest_run_is_useless():
    """The corpus case that motivates reporting both: 74% of frames tracked, but
    arriving as a mist of isolated frames. Every reconstruction step that
    carries continuity needs consecutive frames."""
    pattern = []
    for _ in range(200):
        pattern += [(True, True)] * 3 + [(False, False)]     # 75%, runs of 3
    s = _feed(cc.CoverageMonitor(), pattern).stats()
    assert s.coverage == pytest.approx(0.75, abs=0.01)
    assert s.longest_continuous_s < 0.05


def test_per_cluster_coverage_is_reported_separately():
    """P22 left lost the shank while the thigh stayed visible -- 34.8% against
    51.7%. A single number would have hidden which cluster to look at."""
    pattern = _steady(300, thigh=True, shank=True) + _steady(300, thigh=True, shank=False)
    s = _feed(cc.CoverageMonitor(), pattern).stats()
    assert s.thigh_coverage == 1.0
    assert s.shank_coverage == pytest.approx(0.5, abs=0.01)
    assert s.coverage == pytest.approx(0.5, abs=0.01)


def test_stats_needs_at_least_two_frames():
    m = cc.CoverageMonitor()
    assert m.stats() is None
    m.feed(0.0, True, True)
    assert m.stats() is None


# ── the rolling window, for the live indicator ───────────────────────────────

def test_rolling_window_forgets_frames_outside_it():
    m = cc.CoverageMonitor(window_s=1.0)
    _feed(m, _steady(240, thigh=True, shank=False))         # 2 s all bad
    _feed(m, _steady(120), start=2.0)                       # then 1 s all good
    s = m.stats()
    assert s.coverage > 0.9, "the old bad frames should have aged out"
    assert s.duration_s <= 1.05


def test_unbounded_monitor_keeps_everything():
    m = cc.CoverageMonitor()
    _feed(m, _steady(240, shank=False))
    _feed(m, _steady(240), start=2.0)
    assert m.stats().coverage == pytest.approx(0.5, abs=0.01)


def test_reset_clears_the_window():
    m = _feed(cc.CoverageMonitor(), _steady(120))
    m.reset()
    assert m.stats() is None


# ── the verdict ──────────────────────────────────────────────────────────────

def test_a_good_setup_passes():
    v = cc.verdict(_feed(cc.CoverageMonitor(), _steady(600)).stats())
    assert v.status == cc.PASS
    assert "Safe to record" in v.detail


def test_fragmented_tracking_fails_and_names_the_real_cause():
    """The corpus median is a 0.3 s longest run. This must fail, and the message
    has to point at the assessor's position, because that is what the evidence
    says is happening -- not the volume edge and not the markers."""
    pattern = []
    for _ in range(150):
        pattern += [(True, True)] * 30 + [(False, False)] * 10   # runs of 0.25 s
    v = cc.verdict(_feed(cc.CoverageMonitor(), pattern).stats())
    assert v.status == cc.FAIL
    assert "breaking up" in v.headline
    assert "assessor" in v.detail
    assert "cannot be scored" in v.detail


def test_the_p22_left_shape_fails_and_names_the_shank():
    """P22 left: the shank was gone for the whole hold while the thigh stayed
    visible. The operator needs to be told WHICH cluster."""
    v = cc.verdict(_feed(cc.CoverageMonitor(),
                         _steady(600, thigh=True, shank=False)).stats())
    assert v.status == cc.FAIL
    assert "shank" in v.detail


def test_a_lost_thigh_is_named_as_the_thigh():
    v = cc.verdict(_feed(cc.CoverageMonitor(),
                         _steady(600, thigh=False, shank=True)).stats())
    assert v.status == cc.FAIL
    assert "thigh" in v.detail


def test_long_runs_but_repeated_dropouts_fail_on_coverage():
    """Passes the continuity bar but not the coverage one -- a distinct failure
    with a distinct message."""
    pattern = []
    for _ in range(6):
        pattern += _steady(150) + _steady(60, thigh=False, shank=False)
    s = _feed(cc.CoverageMonitor(), pattern).stats()
    assert s.longest_continuous_s >= cc.PREFLIGHT_MIN_CONTINUOUS_S
    v = cc.verdict(s)
    assert v.status == cc.FAIL
    assert "coverage" in v.headline.lower()


def test_no_frames_at_all_is_distinguished_from_bad_tracking():
    """'Motive is not streaming' and 'the assessor is in the way' need different
    actions, so they must not share a message."""
    v = cc.verdict(None)
    assert v.status == cc.NO_DATA
    assert "Data Streaming" in v.detail
    assert "assessor" not in v.detail


def test_the_threshold_is_a_low_bar_by_construction():
    """1 s is well under a real hold. Documenting the intent: this catches a
    broken setup, it does not grade technique."""
    assert cc.PREFLIGHT_MIN_CONTINUOUS_S <= 1.0
    v = cc.verdict(_feed(cc.CoverageMonitor(), _steady(int(1.2 * FPS))).stats())
    assert v.status == cc.PASS


# ── adapting a NatNet frame ──────────────────────────────────────────────────

class _RB:
    def __init__(self, valid):
        self.tracking_valid = valid


class _Frame:
    def __init__(self, bodies):
        self.rigid_bodies = bodies


def test_rigid_body_flags_are_read_from_a_frame():
    f = _Frame({1: _RB(True), 2: _RB(False)})
    assert cc.rigid_bodies_tracked(f, 1, 2) == (True, False)


def test_a_missing_rigid_body_counts_as_untracked():
    """Motive omits a body it has lost; absent and invalid mean the same thing
    to us."""
    assert cc.rigid_bodies_tracked(_Frame({1: _RB(True)}), 1, 2) == (True, False)
    assert cc.rigid_bodies_tracked(_Frame({}), 1, 2) == (False, False)


def test_a_frame_without_rigid_bodies_does_not_raise():
    assert cc.rigid_bodies_tracked(_Frame(None), 1, 2) == (False, False)


def test_the_adapter_feeds_the_monitor_end_to_end():
    m = cc.CoverageMonitor()
    for i in range(300):
        f = _Frame({1: _RB(True), 2: _RB(i < 150)})
        m.feed(i / FPS, *cc.rigid_bodies_tracked(f, 1, 2))
    s = m.stats()
    assert s.thigh_coverage == 1.0
    assert s.shank_coverage == pytest.approx(0.5, abs=0.01)


# ── the live session ─────────────────────────────────────────────────────────
#
# The NatNet client is injected so these run without a Motive server. That is
# the point of the split: the socket work is one thin class, the decisions are
# everything else.

import capture_coverage_session as ccs


class _FakeClient:
    def __init__(self):
        self.on_frame = None
        self.started = False
        self.stopped = False

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True


def _session(client=None):
    client = client or _FakeClient()
    s = ccs.CoverageSession(client_factory=lambda: client)
    return s, client


def test_session_starts_and_hooks_the_frame_callback():
    s, client = _session()
    assert s.start() is True
    assert client.started and client.on_frame is not None
    assert s.running


def test_a_stream_that_will_not_open_is_reported_not_raised():
    """An operator with Motive closed should get a status line, not a
    traceback."""
    def boom():
        raise OSError("no route to host")
    s = ccs.CoverageSession(client_factory=boom)
    assert s.start() is False
    assert not s.running
    assert s.live_verdict().status == cc.NO_DATA


def test_stop_is_safe_when_never_started():
    s, _ = _session()
    s.stop()          # must not raise
    assert not s.running


def test_frames_reach_the_live_window():
    s, _ = _session()
    s.start()
    for i in range(360):
        s.feed_frame(i, i / FPS, _RB(True), _RB(True))
    assert s.live_verdict().status == cc.PASS


def test_timestamps_are_rebased_so_a_nonzero_stream_clock_is_harmless():
    """NatNet timestamps have no meaningful origin; only differences matter."""
    s, _ = _session()
    s.start()
    for i in range(360):
        s.feed_frame(i, 91234.5 + i / FPS, _RB(True), _RB(True))
    st = s.live.stats()
    assert st.duration_s == pytest.approx(359 / FPS, abs=0.02)


def test_a_missing_rigid_body_is_untracked_not_a_crash():
    s, _ = _session()
    s.start()
    for i in range(240):
        s.feed_frame(i, i / FPS, _RB(True), None)
    assert s.live.stats().shank_coverage == 0.0


def test_preflight_accumulates_independently_of_the_live_window():
    """The live view is a short rolling window; the check is a fixed watch. One
    must not truncate the other."""
    s, _ = _session()
    s.start()
    s.begin_preflight(duration_s=60.0)
    for i in range(1200):                      # 10 s, far longer than the window
        s.feed_frame(i, i / FPS, _RB(True), _RB(True))
    v = s.finish_preflight()
    assert v.stats.duration_s > cc.LIVE_WINDOW_S
    assert v.status == cc.PASS


def test_preflight_reports_the_shape_that_failed():
    s, _ = _session()
    s.start()
    s.begin_preflight(duration_s=60.0)
    for i in range(600):
        s.feed_frame(i, i / FPS, _RB(True), _RB(False))
    v = s.finish_preflight()
    assert v.status == cc.FAIL
    assert "shank" in v.detail


def test_preflight_with_no_frames_says_no_data():
    s, _ = _session()
    s.start()
    s.begin_preflight(duration_s=0.0)
    assert s.finish_preflight().status == cc.NO_DATA


def test_finishing_clears_the_preflight_so_a_second_check_is_independent():
    s, _ = _session()
    s.start()
    s.begin_preflight(duration_s=60.0)
    for i in range(600):
        s.feed_frame(i, i / FPS, _RB(True), _RB(False))
    assert s.finish_preflight().status == cc.FAIL
    s.begin_preflight(duration_s=60.0)
    for i in range(600):
        s.feed_frame(i, 10.0 + i / FPS, _RB(True), _RB(True))
    assert s.finish_preflight().status == cc.PASS


def test_preflight_countdown_reaches_done():
    s, _ = _session()
    s.start()
    assert s.preflight_seconds_left() == 0.0
    s.begin_preflight(duration_s=0.0)
    assert s.preflight_done()


# ── timestamps that cannot be real ───────────────────────────────────────────
#
# natnet_client falls back to time.monotonic() when a packet's suffix is
# missing, so one short read on the wire hands us a timestamp from a different
# clock. Believing it would stretch the window and the longest unbroken stretch
# with it -- reporting a setup that cannot record as fine, which is the worst
# direction to fail in.

def test_a_forward_clock_jump_does_not_inflate_the_window():
    s, _ = _session()
    s.start()
    for i in range(120):
        s.feed_frame(i, i / FPS, _RB(True), _RB(True))
    s.feed_frame(120, 987654.0, _RB(True), _RB(True))       # monotonic fallback
    for i in range(121, 240):
        s.feed_frame(i, 987654.0 + (i - 120) / FPS, _RB(True), _RB(True))
    st = s.live.stats()
    assert st.duration_s < 5.0, f"window inflated to {st.duration_s:.1f}s"
    assert s.clock_breaks == 1


def test_a_backward_clock_jump_is_refused_too():
    s, _ = _session()
    s.start()
    for i in range(120):
        s.feed_frame(i, 500.0 + i / FPS, _RB(True), _RB(True))
    s.feed_frame(120, 1.0, _RB(True), _RB(True))
    st = s.live.stats()
    assert st.duration_s > 0, "time must keep moving forward"
    assert s.clock_breaks == 1


def test_the_jump_cannot_manufacture_a_passing_verdict():
    """The failure mode that matters: fragmented tracking plus a clock jump
    must still read as fragmented, not as one long clean stretch."""
    s, _ = _session()
    s.start()
    for i in range(60):                                     # 0.5 s tracked
        s.feed_frame(i, i / FPS, _RB(True), _RB(True))
    s.feed_frame(60, 987654.0, _RB(True), _RB(True))        # clock break
    for i in range(61, 120):                                # 0.5 s more
        s.feed_frame(i, 987654.0 + (i - 60) / FPS, _RB(True), _RB(True))
    assert s.live.stats().longest_continuous_s < 1.5


def test_ordinary_frame_intervals_are_untouched():
    """The guard must not fire on anything legitimate."""
    s, _ = _session()
    s.start()
    for i in range(600):
        s.feed_frame(i, i / FPS, _RB(True), _RB(True))
    assert s.clock_breaks == 0
    # `live` is a rolling window, so it holds LIVE_WINDOW_S, not the whole run.
    assert s.live.stats().duration_s == pytest.approx(cc.LIVE_WINDOW_S, abs=0.05)


def test_clock_break_state_resets_on_restart():
    s, client = _session()
    s.start()
    s.feed_frame(0, 0.0, _RB(True), _RB(True))
    s.feed_frame(1, 987654.0, _RB(True), _RB(True))
    assert s.clock_breaks == 1
    s.stop()
    s._client = None
    s.start()
    assert s.clock_breaks == 0


# ── the command-line smoke test ──────────────────────────────────────────────
#
# Its exit code is the part worth pinning: a wrong mapping would let a broken
# setup look fine to anything scripting it.

def _run_cli(monkeypatch, pattern, opened=True):
    class _Fake:
        def __init__(self, *a, **k):
            self.live = cc.CoverageMonitor()
            self._done = False
            self.clock_breaks = 0
            self._pattern = pattern

        def start(self):
            return opened

        def begin_preflight(self, duration_s=0.0):
            for i, (t_ok, s_ok) in enumerate(self._pattern):
                self.live.feed(i / FPS, t_ok, s_ok)

        def preflight_done(self):
            return True

        def finish_preflight(self):
            return cc.verdict(self.live.stats())

        def stop(self):
            pass

    monkeypatch.setattr(ccs, "CoverageSession", _Fake)
    return ccs._main([])


def test_cli_exits_zero_when_the_setup_would_pass(monkeypatch, capsys):
    assert _run_cli(monkeypatch, _steady(600)) == 0
    assert "Safe to record" in capsys.readouterr().out


def test_cli_exits_one_when_the_setup_would_fail(monkeypatch, capsys):
    assert _run_cli(monkeypatch, _steady(600, shank=False)) == 1
    assert "shank" in capsys.readouterr().out


def test_cli_exits_two_when_nothing_arrives(monkeypatch, capsys):
    assert _run_cli(monkeypatch, [], opened=True) == 2


def test_cli_exits_two_when_the_stream_will_not_open(monkeypatch, capsys):
    assert _run_cli(monkeypatch, _steady(600), opened=False) == 2
    assert "Data Streaming" in capsys.readouterr().out


# -- the words match the sensor actually in the room ------------------------

def test_the_no_data_message_names_the_source_it_was_waiting_on():
    """An operator with no mocap connected must not be told to check Motive."""
    assert "Motive" in cc.verdict(None, modality=cc.MOCAP).detail
    assert "Motive" not in cc.verdict(None, modality=cc.POSE).detail
    assert "camera" in cc.verdict(None, modality=cc.POSE).detail


def test_a_pose_failure_does_not_blame_markers():
    """The pose check ran on video with no markers in it at all; calling the
    result 'marker coverage' names a sensor that is not there."""
    monitor = cc.CoverageMonitor()
    for i in range(600):
        ok = i % 3 != 0
        monitor.feed(i / 120.0, ok, ok)
    v = cc.verdict(monitor.stats(), modality=cc.POSE)
    assert v.status == cc.FAIL
    assert "marker" not in (v.headline + v.detail).lower()
    assert "pose landmarks" in v.detail


def test_the_marker_failure_still_says_markers():
    monitor = cc.CoverageMonitor()
    for i in range(600):
        ok = i % 3 != 0
        monitor.feed(i / 120.0, ok, ok)
    assert "markers" in cc.verdict(monitor.stats(), modality=cc.MOCAP).detail


def test_the_default_modality_is_the_marker_check():
    """Every existing caller passes no modality and means Motive."""
    assert cc.verdict(None).detail == cc.verdict(None, modality=cc.MOCAP).detail
