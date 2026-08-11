# Trial Quality Triage — Design Spec
**Date:** 2026-08-11
**Status:** Approved

---

## 1. Goal

The IMU-vs-OptiTrack RMSE work this session (accel-bias direction fix, AHRS correction-gate fix,
release-anchored cross-correlation sync, `find_optitrack_match` de-wrapping fallback, magnetometer
sample-rate exemption — see git history for `pendulastic_imu_server.py`, `imu_calibration_tuner.py`,
`workbench_engine.py`, `analysis_pipeline.py`, `batch_imu_vs_optitrack_rmse.py`) cut corpus mean RMSE
from 17.24° to 14.76° and lifted trials-under-10° from 3/24 (12.5%) to 7/40 (18%), but fell far short
of a 90%-under-10° target. Two further investigations this session found no exploitable systematic
cause:
- **Duration-vs-RMSE correlation is flat (r=0.11)** — rules out a filter-architecture rewrite (e.g.
  replacing Madgwick's heuristic gate with an EKF) as a high-leverage fix, since a better continuous
  filter would mainly help long-duration drift, and RMSE doesn't grow with duration.
- **`bias_deg` per participant is high-variance, not high-mean** (corpus: mean 2.75°, std 9.58°; every
  participant's own trials span both positive and negative bias) — rules out a constant coordinate-
  frame/mounting-axis offset as the dominant driver, since a fixed mounting misalignment would produce
  a stable sign and magnitude within a participant, not one that flips trial to trial.

The remaining error looks like genuine per-trial noise: compromised calibration holds, OptiTrack
marker occlusion, mounting slip, examiner contamination at release. Goal: build a way to (a) surface
the quality signals this session's investigation already showed are computable, (b) let a researcher
tag a trial's quality issue while reviewing it in the Workbench, and (c) stratify the RMSE report so
"true algorithm capability" (clean trials only) is visible separately from "everything, including
known-compromised hardware/capture instances."

This is explicitly a **measurement and triage** tool, not a further attempt to close the RMSE gap
algorithmically — the two investigations above indicate there isn't a single remaining algorithmic
lever left to pull.

## 2. Relationship to Existing Work

- **`excluded_trials.json`** (read by `pt_report_common.load_excluded_trials()`, keyed by
  `trial_key()` = `f"{participant}_{leg}_{condition}_T{trial}"`) already exists as a hard exclusion
  list, currently populated by hand for physically-invalid trials (e.g. active muscle intervention
  during the swing). `batch_imu_vs_optitrack_rmse.py`'s own `discover_trials()` does **not** currently
  read it — trials excluded from the PT-score pipeline can still appear in the RMSE batch report.
- **`mas_validation.append_mas_score()`** and **`imu_calibration_config.save_config()`** both already
  use a temp-file-then-`os.replace()` atomic write. This spec's new registries follow the same
  pattern rather than introducing a new one.
- **`pendulastic_workbench.py`'s `_on_save_trial_clicked`** already opens a `tk.Toplevel` dialog with
  its own validation/feedback pattern (`_reference_trace_pt_score()` gating whether save is allowed).
  The new "Flag Trial Quality" dialog follows the same UI idiom.
- **`workbench_engine.compute_raw_sensor_diagnostics()`** already reads raw split-CSV component files
  directly, independent of the fused/scored pipeline, specifically for cross-check diagnostics ("Never
  touches load_imu_trial's fused-angle PT-score path" — its own docstring). The new calibration-hold
  signals in this spec follow that exact precedent.
- **`pendulastic_pt_score.py`'s `AREA_RATIO_WARN` / `quality_warn`** already exists inside
  `compute_pt_params()`'s output but isn't surfaced anywhere in the Workbench UI today.

Considered and rejected: storing quality tags inside the per-trial JSON `pendulastic_storage.save_trial()`
already writes. Rejected because that storage only covers trials a researcher has explicitly clicked
"Save Trial to Dashboard" for — the stratification goal needs coverage of every trial in the corpus,
including ones nobody has opened in the Workbench yet. Quality tags need to live at the same
corpus-wide discovery layer `excluded_trials.json` already operates at.

## 3. Data Model

Two separate registries, both under `BASE_DIR` alongside `excluded_trials.json`, both read via a
`load_*`/write via an `append_*`/`clear_*` function pair mirroring `mas_validation.py`'s existing
functions:

**`trial_quality_tags.json`** (new) — `{trial_key: {"category": str, "details": str, "timestamp":
iso8601}}`. Drives **stratified reporting only** — presence in this file never removes a trial from
`discover_trials()` or `discover_all_trials()`. `category` is validated against a fixed enum at write
time (`calibration_hold`, `marker_occlusion`, `mounting_slip`, `release_contamination`, `other`) —
invalid values raise `ValueError`, mirroring `mas_validation._valid_grade()`'s existing gate on
`mas_grade`.

**`excluded_trials.json`** (existing, unchanged schema) — stays the hard "never score this trial,
anywhere" list. A trial can be tagged without being excluded, and (separately, deliberately) excluded
without a category tag if a researcher just wants it gone. The Workbench UI (Section 5) can write to
both in one action, but they remain two distinct files/concerns.

Both files use the existing atomic-write idiom (temp file + `os.replace()`). Multi-writer conflict
resolution (e.g. file locking against a second concurrent editor) is explicitly out of scope — this is
a single-researcher desktop Tkinter app, not a multi-user service; if that assumption changes, this
needs revisiting.

`batch_imu_vs_optitrack_rmse.py`'s `discover_trials()` gains a check against `load_excluded_trials()`
(new — it doesn't do this today), so a hard-excluded trial disappears from the RMSE batch report the
same way it already disappears from `pt_report_common`'s PT-score pipeline.

## 4. Signal Computation

New function in `workbench_engine.py`:

```python
def compute_quality_signals(imu_paths: dict, ref_angle: np.ndarray) -> dict:
```

Returns a dict of independently-computed, documented signals — **not** a single opaque score:

- **`hold_gravity_z_frac`** and **`hold_stillness_ok`** — read the raw accel/gyro component CSVs
  directly via `imu_paths` (mirroring `compute_raw_sensor_diagnostics()`'s existing pattern exactly).
  These describe the pre-release calibration hold, a window that exists only in the raw log before
  fusion, release-detection, or cross-correlation alignment — there is no "synchronized" version of
  this window to diverge from, so reading raw files here is correct by construction, not a shortcut.
- **`optitrack_dropout_frac`** — computed from `ref_angle`, which callers must pass as the *exact same
  array* already loaded via `load_optitrack_trial()` and already fed into `compare_pair()` for
  scoring. No separate reload — this guarantees the diagnostic can't drift from what the scorer sees.
- **`optitrack_area_ratio_warn`** — surfaces the `quality_warn` field `compute_pt_params()` already
  computes for the OptiTrack trace, previously unused outside the PT-score formula itself.

The docstring on `compute_quality_signals` states explicitly, per-field, which pipeline stage each
signal reflects (raw pre-fusion vs. already-loaded/already-scored), so a future reader doesn't have to
re-derive this the way this spec's own review process did.

**Suggestion logic** (a small, named, threshold-based rule set — matching this codebase's existing
style of hand-tuned constants like `AREA_RATIO_WARN`, `GYRO_STATIONARY_MAX_RAD_S`): tilted/unstable
hold suggests `calibration_hold`; high dropout or the area-ratio warning suggests `marker_occlusion`;
otherwise **no suggestion** — the dialog shows an explicit "No automated suggestion — select
category…" placeholder, never a silently-preselected category, so a lack of signal is never
mistaken for a clean trial. `mounting_slip` and `release_contamination` have no computable signal at
all and are always manual-only. **Threshold values are seed values, not validated ones** — they need
tuning against the real corpus during implementation, the same way the AHRS gate's 0.3 rad/s and the
release-anchor's 0.6s margin were tuned this session, not asserted up front.

## 5. Workbench UI

One new "Flag Trial Quality" button next to the existing "Save Trial to Dashboard," opening a single
`tk.Toplevel` dialog (same idiom as `_on_save_trial_clicked`):

- Computed signals shown read-only for context.
- Category dropdown, pre-filled with the auto-suggestion when one exists, otherwise the neutral
  placeholder.
- Details text field, pre-filled with an auto-generated sentence when a suggestion exists (e.g. "Hold-
  window gravity only 57% on Z axis"), which the researcher edits rather than writing from scratch.
- One checkbox, unchecked by default: "Also exclude from all analysis." Checking it and saving writes
  to both `trial_quality_tags.json` and `excluded_trials.json` in the same click — tagging and
  exclusion stay conceptually separate (Section 3) without ever requiring a second dialog.
- A "Clear" action in the same dialog removes whichever of the tag entry and exclusion entry
  currently exist for this trial (both, if both are set) in one click, for correcting mistakes —
  matching the "no duplicate administrative hoops" principle the save path already follows.

Separately, the existing raw-diagnostics label (`_raw_diag_label`, currently showing peak gyro
velocity and accel release time) gets `optitrack_dropout_frac` and `hold_gravity_z_frac` appended, so
a researcher can notice a problem passively while reviewing a trial, before ever opening the dialog.

## 6. Stratified Reporting

`batch_imu_vs_optitrack_rmse.py`'s `main()` summary gains a stratified breakdown alongside the
existing overall mean/median/under-goal-count: the same stats recomputed with each tag category's
trials excluded (`trial_quality_tags.json`-driven, not `excluded_trials.json` — a tagged-but-not-
excluded trial still appears in the raw pipeline output, only the *stratified* summary block treats
it as excluded for that specific breakdown), so "algorithm capability on clean trials" vs. "everything,
including known hardware/capture issues" is visible directly in the existing report output rather than
requiring a separate analysis pass.

## 7. Testing

- Unit tests for `compute_quality_signals()` against synthetic raw CSVs with known tilt/dropout
  values (mirroring how `compute_raw_sensor_diagnostics()` is already tested).
- Unit tests for the new `trial_quality_tags.json` read/write/clear functions, including the
  category-enum rejection case.
- Unit test confirming `batch_imu_vs_optitrack_rmse.discover_trials()` now drops entries present in
  `excluded_trials.json` (it currently doesn't — this is a real behavior change, not just new code).
- Unit test for the stratified-report computation (given a fixed set of rows + tags, does the
  per-category breakdown match hand-computed expected stats).
- Tkinter dialog interaction itself is not unit-tested (matching this codebase's existing practice for
  `_on_save_trial_clicked`'s dialog) — smoke-tested manually against a real trial during
  implementation.

## 8. Out of Scope

- Tuning the exact suggestion thresholds to the real corpus — flagged in Section 4 as a required
  implementation step, not pre-decided here.
- Multi-writer concurrency / file locking (Section 3).
- Any further algorithmic RMSE-reduction work — this spec is measurement/triage only, per Section 1's
  finding that no further systematic algorithmic lever is evident.
- A separate batch/triage-list screen for reviewing many trials at once (considered during
  brainstorming, decided against in favor of in-Workbench tagging while a trial is already loaded).
