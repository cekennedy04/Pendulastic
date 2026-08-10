# Per-Trace Release-Start Alignment — Design Spec
**Date:** 2026-08-10
**Status:** Approved

---

## 1. Goal

There is a delay in the release mark between the OptiTrack and the phone IMU knee-angle CSV
traces loaded into `pendulastic_workbench.py`: each source's release moment lands at a different
point on its own clock, and today the workbench has no way to align them at that shared physical
event. Goal: let the user select each trace's own release moment and use it to align that trace
against the chosen reference trace.

## 2. Relationship to Existing Work

**`detect_release_t0()` / `align_to_release()` already exist** in `pendulastic_pt_score.py`
(commit `6776a73`, "pure adaptive-threshold t0 release detection + trial time-axis alignment"),
with their own tests in `tests/test_pt_score.py`. They are not wired into `pendulastic_workbench.py`,
`workbench_engine.py`, or `pendulastic_app.py` today — dead code outside their own tests.
`detect_release_t0(t, signal, baseline_sec=0.6)` Savitzky-Golay filters the signal and returns the
absolute time of the first sample that crosses `0.08 * signal_range` past a `baseline_sec` baseline.

That per-trace threshold crossing is very likely the source of the delay this spec exists to fix:
IMU and OptiTrack signals have different noise floors and different shapes right at the release
event, so each trace's independently-detected threshold crossing lands at a systematically
different point relative to the true physical release — auto-detecting each trace's t0 in
isolation does not make them agree with each other. This spec does not change
`detect_release_t0`/`_detect_release`'s algorithm. It reuses `detect_release_t0` as a
**starting suggestion only**, and adds the user-driven correction/override loop that was missing.

The workbench already has two related, unconnected mechanisms this spec bridges:
- A global "Release Start" milestone (one timestamp per session, tied to the video scrubber,
  drawn as a vertical dashed line, exported via `annotations_to_csv_rows`/`export_session`).
- A per-trace manual `lag(s)` override `Entry` next to each trace's visibility checkbox, which
  shifts that trace's time array before `engine.compare_pair` computes RMSE/MAE/timing-offset
  against the reference trace selected in the "Reference" dropdown.

## 3. Design

### 3.1 "Release Start" becomes per-trace

Remove `"Release Start"` from `MILESTONE_LABELS` (`First Peak Extension`, `Maximum Flexion`,
`Rest/Settled` remain as today — global, session-level, marked via the existing "Mark Here"
dropdown+button). Each trace's existing chip row in `set_traces()` (checkbox + `lag(s):` field)
gains a **"Mark Release"** button.

A per-trace release mark is stored under the key `"Release Start (<label>)"` in the *existing*
`self._annotations` dict, using the *existing* `(frame_index, t_sec)` shape every other milestone
already uses. This is a deliberate reuse, not a new store: `_draw_milestone_artist`,
`_redraw_annotations` (already called at the end of `set_traces()`, so marks survive the async
HPE-results re-load), `get_annotations()`, `annotations_to_csv_rows`, and `export_session` all
pick up per-trace release marks automatically, with zero new export code.

### 3.2 Auto-suggested seed via `detect_release_t0`

When `set_traces()` sees a label with no existing release mark (a genuinely new trace, not one
being preserved across a re-load), it calls
`pendulastic_pt_score.detect_release_t0(t, angle)` for that trace. On success, the result seeds
`self._annotations["Release Start (<label>)"] = (fi, t0)` immediately (`fi` derived from `t0` via
the loaded video's fps when available, else `0`) and draws the marker — no scrubbing required for
traces where the auto-guess is good enough. `detect_release_t0` raises `ValueError` on fewer than
4 finite samples; that (or any other exception) is caught and the trace is simply left unmarked,
falling back fully to manual marking.

This does not change `detect_release_t0` itself — it only gives its existing output a place to be
displayed, corrected, and used, closing the gap between "detector exists" and "detector affects
anything."

### 3.3 Manual marking / correction

The user drags the shared scrubber to the frame where a specific trace visibly shows its release,
then clicks that trace's "Mark Release" button — overwriting any auto-seeded (or previous manual)
mark for that trace with `(current_frame_index(), current_time_sec())`, exactly like
`_on_mark_milestone` does today for the global milestones. The button's label reflects the current
state: `"Mark Release"` when unmarked, `"Release: 1.23s"` once marked (auto-seeded or manual) —
clicking again re-marks at the current scrubber position.

### 3.4 Auto-align: release marks drive the existing lag override

New `_recompute_release_lags()`: reads the reference trace's own release mark (if any); for every
other trace that also has a release mark, computes `lag = engine.release_lag_sec(ref_t, test_t)`
(new pure function in `workbench_engine.py`, `ref_t - test_t`) and writes it as text into that
trace's *existing* `lag(s)` `Entry` — which immediately feeds the unchanged
`compare_pair(..., lag_override_sec=...)` RMSE/MAE/PT-score pipeline. The `lag(s)` field stays a
normal editable `Entry`; a researcher can hand-tune after auto-fill, and doing so is not
overwritten unless a release mark changes again.

Triggered by: any "Mark Release" click (on any trace, including the reference trace itself), any
auto-seed in `set_traces()`, and the Reference dropdown changing (replaces that `trace_add`
callback's direct call to `_recompute_metrics()` — `_recompute_release_lags()` always calls
`_recompute_metrics()` itself at the end, so this is a drop-in replacement, not an addition).

**Known limitation, not engineered around:** switching Reference to a trace with no release mark
leaves other traces' already-computed lag values in place rather than clearing them, to avoid
guessing whether a value currently in a `lag(s)` field is release-derived or hand-typed.

## 4. Out of Scope

- Changing `_detect_release`'s threshold algorithm or `align_to_release`'s behavior.
- Shifting the underlying trace time arrays, on-disk CSVs, or PT-score windowing — this is
  strictly a faster, better-seeded way to populate the existing `lag(s)` override, which already
  feeds every downstream comparison computation unchanged.
- Auto-clearing lag values when the reference or its release mark changes (Section 3.4).

## 5. Testing Plan

1. `engine.release_lag_sec(ref_t, test_t)` — pure function unit test.
2. `_recompute_release_lags()` — reference and one trace both marked → other trace's `lag(s)`
   field is set to the expected value; only reference marked → no field changes; neither marked →
   no-op (still calls `_recompute_metrics()`).
3. `set_traces()` auto-seed — a trace with a clean synthetic release signal gets an
   auto-populated mark; a trace with degenerate/insufficient data is left unmarked, no exception
   propagates.
4. "Mark Release" button — click sets `self._annotations["Release Start (<label>)"]` from the
   current scrubber position, overwriting any prior value (auto-seeded or manual), and triggers
   `_recompute_release_lags()`.
5. Existing annotation/export tests continue to pass unchanged (`annotations_to_csv_rows`,
   `export_session` are generic over whatever's in the dict, verified by inspection in Section 2).
6. Full regression: existing test suite stays green.
