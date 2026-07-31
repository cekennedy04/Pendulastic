# Pendulastic Workbench — Design Spec

**Status:** Approved (brainstorming complete, pending user review of this document)
**Date:** 2026-07-31

## 1. Problem & Existing Landscape

The ask was for a multi-modal comparison tool: ingest phone-IMU raw logs, video
(MediaPipe HPE), and OptiTrack gold-standard CSVs for one trial; time-align
them; compute RMSE/MAE/timing-jitter metrics; and present an interactive
synced video+signal viewer with click-to-annotate clinical milestones,
exportable to JSON.

Before designing, the codebase was audited for overlap, since this repo
already has substantial infrastructure in this exact space:

| Capability | Already exists in |
|---|---|
| 6-model HPE video pipeline (MediaPipe, RTMPose, MMPose, FreMoCap, OpenPose, PosePipe) | `analysis_pipeline.py` (`MODEL_FUNCTIONS`) |
| OptiTrack ingestion — rigid-body rotation quaternions (bone-axis) | `analysis_pipeline._optitrack_knee_angle_series` |
| OptiTrack ingestion — marker-triplet PCA (tracking-reset fallback) | `pendulastic_pt_score._angle_from_labeled_markers_pca` |
| Cross-correlation time alignment + resampling | `analysis_pipeline.synchronize_signals` |
| RMSE / bias / limits-of-agreement | `analysis_pipeline.compute_rmse`, `compute_bias_and_loa`, `score_model_against_reference` |
| Phone IMU raw log → Madgwick AHRS → knee angle | `imu_calibration_tuner.replay_trial` |
| Video-scrub → vertical time-cursor pattern | `pendulastic_viewer.py`'s `self._vline.set_xdata(...)` in `_update_plot` |
| Single-purpose click-to-mark-release | `pendulastic_viewer.py`'s `_on_graph_click` (release-frame only, not general) |
| Independent lag-validation dataset | `diagnose_lag.py` + `training_data/annotations/coco_keypoints.json` (two known trials) |

**Conclusion:** this is primarily an *integration and UI* project, not a new
ingestion/metrics engine. A separate, stale CustomTkinter prototype
(`pendulastic_workspace.py`, last touched 2026-06-19) covers overlapping
ground but is out of scope — the new workbench is built fresh in the
`pendulastic_app.py` plain-Tkinter + matplotlib style, and does not depend on
`pendulastic_workspace.py`.

## 2. Architecture

Two new files, mirroring this repo's established config/engine/UI separation
(as in `imu_calibration_config.py` + `imu_calibration_tuner.py` +
`pendulastic_app.py`):

- **`workbench_engine.py`** — pure functions, no Tkinter dependency. Fully
  unit-testable in isolation.
- **`pendulastic_workbench.py`** — Tkinter UI (panels, matplotlib canvas,
  video player), following `pendulastic_app.py`'s `App(tk.Tk)` panel-swap
  container pattern.
- **`tests/test_workbench_engine.py`** — synthetic-fixture unit tests for
  the engine module.

```
pendulastic_workbench.py
  TrialLoadPanel(tk.Frame)  - 3 independent file pickers (IMU log / video /
                              OptiTrack CSV) + HPE model checkboxes (of the
                              6 in analysis_pipeline.MODEL_FUNCTIONS)
  WorkbenchView(tk.Frame)   - ttk.PanedWindow: video canvas + ttk.Scale
                              scrubber | matplotlib multi-trace Figure +
                              annotation toolbar + metrics readout
  App(tk.Tk)                - panel-swap container (matches pendulastic_app.py)

workbench_engine.py
  load_imu_trial(jsonl_path, config=None) -> (t, angle)
  load_optitrack_trial(csv_path) -> (t, angle, method: "rigid_body"|"marker_pca")
  load_video_trial(video_path, models: list[str], progress_cb=None)
      -> dict[str, (t, angle) | {"error": str}]
  compare_pair(ref_t, ref_y, test_t, test_y) -> metrics dict
  extrema_jitter(t, angle) -> {"pk_i", "tr_i", "cycle_times"}
```

A trial is whatever subset of the three modalities the researcher supplies —
2 of 3 is valid, matching `analysis_pipeline.process_trial`'s existing
`has_reference`-optional philosophy.

## 3. Ingestion

- **`load_imu_trial`**: parses the raw accel/gyro/mag JSONL and calls
  `imu_calibration_tuner.replay_trial(samples, params)`, where `params`
  defaults to the currently-persisted `imu_calibration_config.load_config()`
  (overridable). Returns the finite-filtered `(t, angle)` series.
- **`load_optitrack_trial`**: tries `analysis_pipeline._optitrack_knee_angle_series`
  first (rigid-body rotation quaternions — deterministic, bone-axis-grounded:
  the angle between the Thigh and Shank rigid bodies' actual local-X axes).
  Only if that raises (missing rigid-body rotation data, or a Motive
  tracking-reset) does it fall back to `pendulastic_pt_score`'s
  marker-triplet-PCA reconstruction. The returned `method` tag drives a
  visible UI badge ("OptiTrack: rigid-body axis" vs "OptiTrack: marker-PCA
  fallback") so a researcher is never shown a heuristic reconstruction
  without knowing it's not the deterministic ground truth. If neither path
  succeeds, the trial has no OptiTrack reference at all (same as
  `has_reference=False` elsewhere in this codebase) — never a silent
  substitution.
- **`load_video_trial`**: runs each checked model from
  `analysis_pipeline.MODEL_FUNCTIONS` via `model_func(video_path)`. This is
  the slow step (full-video pose inference × N models) and always runs on a
  background thread with a progress callback, matching `pendulastic_app.py`'s
  existing `run_offline_track` progress-queue pattern. One model erroring
  (missing ONNX weights, decode failure) does not abort the trial — that
  trace is omitted with a visible status note; other models still load.

## 4. Time Alignment & Metrics

- **Pairwise, not global**: `compare_pair(ref_t, ref_y, test_t, test_y)`
  wraps `analysis_pipeline.synchronize_signals` (cross-correlation lag +
  resampling) + `compute_rmse` + `compute_bias_and_loa`, adding one line for
  MAE (not currently returned by `synchronize_signals`). A dedicated
  accel-transient release detector was considered and rejected — the
  existing cross-correlation primitive already solves general alignment and
  is reused rather than duplicated.
- **NaN safety**: OptiTrack marker occlusion can produce NaN samples, and
  `np.interp` does not handle NaN gracefully (it propagates and corrupts
  neighboring interpolated points across a gap). `compare_pair` filters
  non-finite `(t, y)` pairs out of each raw series *before* calling
  `synchronize_signals`, and masks any residual non-finite values again
  before the RMSE/MAE/bias reduction.
- **Manual override**: the auto-detected `lag_sec` is applied by default but
  exposed as an editable numeric field per trace in the UI; editing it
  re-runs the resample/score. This satisfies "or manual sync alignment"
  without a separate detector.
- **`extrema_jitter`**: reuses `pendulastic_pt_score`'s existing peak/trough
  detection (the same machinery behind `compute_pt_params`) applied
  independently to each modality's own curve. Comparing extrema *timing*
  pairwise across modalities gives the "timing jitter across oscillation
  cycles" metric from the original ask.

## 5. UI: Synchronized Video + Signal Viewer

- **Layout**: `ttk.PanedWindow` — video canvas (OpenCV `VideoCapture` frame
  display) + a new `ttk.Scale` frame scrubber on one side; one matplotlib
  `Figure` with all loaded traces overlaid (IMU, OptiTrack, each checked HPE
  model), color-coded with a legend, plus the current pairwise metrics
  readout, on the other. (No draggable scrubber existed to port from
  `pendulastic_viewer.py` — only the axvline-cursor pattern does; the
  `ttk.Scale` scrubber itself is new, standard `cv2.CAP_PROP_POS_FRAMES`
  seeking.)
- **Cursor coupling, both directions**: one shared `axvline`, moved via
  `.set_xdata([t, t])` + `canvas.draw_idle()` — the exact pattern already
  proven in `pendulastic_viewer.py._update_plot`. Dragging the scrubber
  moves the axvline to that frame's time; clicking the plot seeks the video
  to the nearest frame (generalizing the legacy viewer's single-purpose
  release-frame click handler into an arbitrary seek).
- **Per-trace visibility checkboxes**, since up to 8 traces (IMU + OptiTrack
  + 6 HPE models) would otherwise overlap heavily.

## 6. Annotations

- **Fixed milestone set**: `Release Start`, `First Peak Extension`,
  `Maximum Flexion`, `Rest/Settled` — chosen via a button group in the
  annotation toolbar before each click. Extends the legacy viewer's
  single-purpose click-to-mark-release into a general
  `{label: (frame_index, t_sec)}` store, rendered as labeled vertical
  markers (reusing the existing `_ax.annotate` pattern already present in
  `pendulastic_viewer.py`).
- Annotations are per-trial (one shared timeline), not per-trace.
- **Export**: a standalone JSON — not merged into `analysis_pipeline`'s
  batch report schema, since this tool is for interactive single-trial
  inspection, not aggregate leaderboard generation. Bundles trial metadata,
  the annotation set, and the pairwise metrics from Section 4, written next
  to the loaded trial files.

## 7. Error Handling

- Per-model video failures don't abort a trial (Section 3).
- Unlike `_run_imu_tuning`'s broad exception-swallowing (appropriate for a
  live clinical flow that must never block on a failure), this is a research
  tool: a bad/missing file surfaces a clear, visible error in its panel — it
  is not silently swallowed into an empty trace.
- Missing OptiTrack grounding (Section 3) — no reference for that trial,
  never a silent fallback substitution the UI doesn't disclose.

## 8. Testing

- **`tests/test_workbench_engine.py`**: pure-function tests with synthetic
  `(t, angle)` fixtures — `compare_pair`'s NaN-masking and lag-override
  paths, `extrema_jitter`'s cycle-timing math, and an OptiTrack fixture with
  deliberately-missing rigid-body rotation columns to exercise the
  rigid-body → marker-PCA fallback-with-badge path.
- **Real-data validation (manual)**: cross-check `compare_pair`'s
  auto-detected lag against `diagnose_lag.py`'s independent brute-force
  frame-shift correlation result, for the same two trials it already covers
  (`Participant_4_left_T2`, `Participant_8_right_control_T2`) — should agree
  within a frame or two, since both approaches maximize correlation, just on
  different time bases (frame-index vs. seconds).
- **UI**: manual smoke test only (load a real trial, scrub, click-annotate,
  export, reopen the JSON) — matching this repo's existing precedent of not
  unit-testing its Tkinter panels (`pendulastic_app.py`'s panels aren't
  either).

## 9. Explicitly Out of Scope

- `pendulastic_workspace.py` is not touched, extended, or depended upon.
- No changes to `analysis_pipeline.py`'s batch report/leaderboard writers.
- No new accel-transient release-detection algorithm (superseded by reusing
  `synchronize_signals`).
- No live/streaming OptiTrack support — this workbench is for post-hoc
  offline trial review only.
- User-definable annotation labels (fixed set only, per Section 6).
