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
