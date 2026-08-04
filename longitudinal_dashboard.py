"""
longitudinal_dashboard.py
==========================
Renders the 3-panel longitudinal comparison figure (waveform overlay,
parameter bar chart, PT score trend) for one participant/leg from a
pendulastic_storage.load_history() history dict. Zero Tkinter dependency
-- callers embed the returned Figure however they like.

See docs/superpowers/specs/2026-08-04-longitudinal-dashboard-design.md.
"""
from __future__ import annotations

from datetime import datetime

from matplotlib.figure import Figure

from pendulastic_pt_score import HEALTHY_REF, PT_HEALTHY_MAX, PT_BORDERLINE_MAX

PARAM_KEYS = ["R2n", "N", "phi_max_ratio", "omega_max_n", "omega_min_n", "f", "area_ratio"]


def _sorted_sessions_with_trace(history: dict, leg: str, trace_label: str) -> list:
    """Sessions for `leg` that have `trace_label`, sorted by date -- never
    by JSON insertion/append order, so a backfilled earlier-dated session
    saved after a later one still renders in the correct chronological
    position (design spec Section 6). A session whose date can't be
    parsed is excluded; load_history() already flagged that via
    "_skipped", this is not a second place to raise about it."""
    sessions = history.get("legs", {}).get(leg, {}).get("sessions", [])
    dated = []
    for session in sessions:
        if trace_label not in session.get("traces", {}):
            continue
        try:
            d = datetime.fromisoformat(session["date"])
        except (ValueError, TypeError, KeyError):
            continue
        dated.append((d, session))
    dated.sort(key=lambda pair: pair[0])
    return [session for _d, session in dated]


def render_dashboard(history: dict, leg: str, trace_label: str) -> Figure:
    sessions = _sorted_sessions_with_trace(history, leg, trace_label)

    fig = Figure(figsize=(9, 11), dpi=100)
    ax_wave = fig.add_subplot(3, 1, 1)
    ax_bar = fig.add_subplot(3, 1, 2)
    ax_trend = fig.add_subplot(3, 1, 3)

    _render_waveform_overlay(ax_wave, sessions, trace_label)
    _render_parameter_bars(ax_bar, sessions, trace_label)
    _render_pt_trend(ax_trend, sessions, trace_label)

    fig.tight_layout()
    return fig


def _render_waveform_overlay(ax, sessions: list, trace_label: str) -> None:
    ax.set_xlabel("Time since release (s)")
    ax.set_ylabel("Knee angle (deg)")
    ax.set_title("Waveform overlay")
    for session in sessions:
        trace = session["traces"][trace_label]
        t = trace["t"]
        angle = trace["angle"]
        t0 = t[0] if t else 0.0
        t_aligned = [ti - t0 for ti in t]
        pt_score = trace["metrics"]["pt_score"]
        pt_str = f"{pt_score:.3f}" if pt_score is not None else "n/a"
        ax.plot(t_aligned, angle, label=f"{session['label']} (PT={pt_str})")
    if sessions:
        ax.legend(fontsize=8)


def _render_parameter_bars(ax, sessions: list, trace_label: str) -> None:
    ax.set_ylabel("Parameter value")
    ax.set_title("Parameter comparison vs healthy reference")

    # Strict single-trace filtering: every bar in a session's group comes
    # from traces[trace_label]["metrics"] only -- never from another trace
    # present in the same session, even as a fallback for a missing param.
    # A session missing any of the 7 params is dropped whole, not
    # partially filled, so a grouped bar never mixes readings from two
    # different sensors (design spec Section 6).
    usable = [s for s in sessions
             if all(key in s["traces"][trace_label]["metrics"] for key in PARAM_KEYS)]
    if not usable:
        return

    n_sessions = len(usable)
    n_params = len(PARAM_KEYS)
    width = 0.8 / n_sessions
    x = list(range(n_params))

    for i, session in enumerate(usable):
        metrics = session["traces"][trace_label]["metrics"]
        values = [metrics[key] for key in PARAM_KEYS]
        offsets = [xi + i * width for xi in x]
        ax.bar(offsets, values, width=width, label=session["label"])

    for i, key in enumerate(PARAM_KEYS):
        ax.hlines(HEALTHY_REF[key], i - width / 2, i + (n_sessions - 0.5) * width, colors="black",
                  linestyles="dashed", linewidth=1)

    ax.set_xticks([xi + width * (n_sessions - 1) / 2 for xi in x])
    ax.set_xticklabels(PARAM_KEYS, rotation=30, ha="right")
    ax.legend(fontsize=8)


def _render_pt_trend(ax, sessions: list, trace_label: str) -> None:
    """Placeholder body -- filled in by Task 7."""
    ax.set_xlabel("Session")
    ax.set_ylabel("PT score")
    ax.set_title("Longitudinal PT score trend")
