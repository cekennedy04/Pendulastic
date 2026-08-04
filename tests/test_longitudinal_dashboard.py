import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest
import longitudinal_dashboard as dash


def _session(label, date, pt_score=0.1, trace_label="imu", extra_metrics=None):
    metrics = {"R2n": 0.9, "N": 6.0, "phi_max_ratio": 0.8, "omega_max_n": 7.0,
              "omega_min_n": 0.01, "f": 1.0, "area_ratio": 0.1, "pt_score": pt_score,
              "mas": "0"}
    if extra_metrics is not None:
        metrics = extra_metrics
    return {
        "label": label, "date": date, "reference_trace": trace_label,
        "traces": {trace_label: {"t": [0.0, 0.1, 0.2], "angle": [140.0, 138.0, 135.0],
                                 "metrics": metrics}},
    }


def _history(sessions, leg="left"):
    return {"participant_id": "P5",
           "legs": {"left": {"sessions": []}, "right": {"sessions": []},
                    **{leg: {"sessions": sessions}}}}


def test_sorted_sessions_with_trace_filters_and_sorts_by_date():
    s_later = _session("Follow-up", "2026-08-01")
    s_earlier = _session("Initial", "2026-07-07")
    s_missing_trace = _session("Other", "2026-07-20", trace_label="optitrack")
    history = _history([s_later, s_earlier, s_missing_trace])

    result = dash._sorted_sessions_with_trace(history, "left", "imu")

    assert [s["label"] for s in result] == ["Initial", "Follow-up"]


def test_render_dashboard_returns_figure_with_three_axes():
    history = _history([_session("Initial", "2026-07-07")])
    fig = dash.render_dashboard(history, "left", "imu")
    assert len(fig.axes) == 3


def test_render_dashboard_waveform_legend_has_pt_scores():
    history = _history([_session("Initial", "2026-07-07", pt_score=0.115)])
    fig = dash.render_dashboard(history, "left", "imu")
    legend = fig.axes[0].get_legend()
    assert legend is not None
    assert "PT=0.115" in legend.get_texts()[0].get_text()


def test_render_dashboard_waveform_legend_shows_na_for_none_pt_score():
    """pt_score/mas are None together when compute_pt_params reports
    insufficient signal (design spec Section 2) -- the legend must render
    "PT=n/a", not crash trying to format None with :.3f."""
    history = _history([_session("Initial", "2026-07-07", pt_score=None)])
    fig = dash.render_dashboard(history, "left", "imu")
    legend = fig.axes[0].get_legend()
    assert "PT=n/a" in legend.get_texts()[0].get_text()


def test_render_dashboard_empty_history_does_not_raise():
    history = _history([])
    fig = dash.render_dashboard(history, "left", "imu")
    assert len(fig.axes) == 3


def test_sorted_sessions_with_trace_excludes_unparseable_dates():
    """Sessions with unparseable or missing dates are excluded, not errored."""
    s_valid = _session("Initial", "2026-07-07")
    s_unparseable = _session("Broken", "not-a-date")
    s_missing_date = {
        "label": "NoDate", "reference_trace": "imu",
        "traces": {"imu": {"t": [0.0, 0.1], "angle": [140.0, 138.0],
                           "metrics": {"R2n": 0.9, "N": 6.0, "phi_max_ratio": 0.8,
                                      "omega_max_n": 7.0, "omega_min_n": 0.01, "f": 1.0,
                                      "area_ratio": 0.1, "pt_score": 0.1, "mas": "0"}}},
    }
    history = _history([s_unparseable, s_valid, s_missing_date])

    result = dash._sorted_sessions_with_trace(history, "left", "imu")

    assert len(result) == 1
    assert result[0]["label"] == "Initial"


def test_render_dashboard_bar_chart_covers_all_params():
    history = _history([_session("Initial", "2026-07-07")])
    fig = dash.render_dashboard(history, "left", "imu")
    ax_bar = fig.axes[1]
    xtick_labels = [t.get_text() for t in ax_bar.get_xticklabels()]
    assert xtick_labels == dash.PARAM_KEYS


def test_render_dashboard_bar_chart_drops_session_missing_a_param():
    """Strict single-trace filtering (design spec Section 6): a session
    whose selected-trace metrics are missing one of the 7 params must be
    dropped from the bar chart entirely, never partially rendered or
    backfilled from another trace in that same session."""
    complete = _session("Initial", "2026-07-07")
    incomplete_metrics = {"R2n": 0.9, "N": 6.0, "pt_score": 0.1, "mas": "0"}
    incomplete = _session("Post-Training", "2026-07-17", extra_metrics=incomplete_metrics)
    history = _history([complete, incomplete])

    fig = dash.render_dashboard(history, "left", "imu")
    ax_bar = fig.axes[1]
    legend = ax_bar.get_legend()
    labels = [t.get_text() for t in legend.get_texts()]
    assert labels == ["Initial"]


def test_render_dashboard_bar_chart_empty_sessions_does_not_raise():
    history = _history([])
    fig = dash.render_dashboard(history, "left", "imu")
    assert fig.axes[1].get_legend() is None


def test_render_dashboard_bar_chart_xtick_position_with_two_sessions():
    """Verify that xtick positions are centered on the bar group.
    With 2 sessions, width = 0.8 / 2 = 0.4.
    For parameter 0 (xi=0), the group center should be at:
    0 + 0.4 * (2 - 1) / 2 = 0.2"""
    s1 = _session("Initial", "2026-07-07")
    s2 = _session("Follow-up", "2026-07-17")
    history = _history([s1, s2])

    fig = dash.render_dashboard(history, "left", "imu")
    ax_bar = fig.axes[1]
    xticks = ax_bar.get_xticks()

    # With 2 sessions and 7 parameters, xticks should be at positions
    # for each parameter. The first xtick (param 0) should be at 0.2.
    assert xticks[0] == pytest.approx(0.2, abs=1e-6)
