# MediaPipe HPE Preprocessing — Design Spec

**Status:** Draft — pending user review

**Date:** 2026-08-11

---

## 1. Goal

Reduce MediaPipe-vs-OptiTrack knee-angle RMSE across the Pendulastic pendulum-test dataset to
**under 10° for at least 90% of trials** (both legs, every participant with video + OptiTrack
ground truth). This is an existing, already-tracked target (`sweep_mediapipe_config.RMSE_GOAL_DEG
= 10.0`), currently unmet by a wide margin: the last real sweep (`sweep_mediapipe_config.py`,
scoped to P14 only) found a best median RMSE of **27.65°** (heavy model, vis_thresh=0.3) — 4
trials, all from one participant.

Approach: since MediaPipe Pose Landmarker has no text-prompt interface (it is a fixed landmark
regressor, not an LLM/VLM), "tell the model what it should be looking for" becomes three concrete,
independently-testable frame-preprocessing mechanisms, applied before inference, that encode the
two facts a clinician already knows about every trial: **the patient is reclined** (torso roughly
horizontal, not the upright/standing posture BlazePose is mostly trained on) and **only one leg is
moving** (the tested leg, released and swinging; the other leg and any assessor in frame are
noise). Each mechanism is measured in isolation against the current baseline, across the full
dataset, before any combination is attempted.

## 2. Background

- `sweep_mediapipe_config.py` already exists as the (P14-only) empirical sweep harness: iterates
  MediaPipe model variant (lite/full/heavy) × visibility threshold, scores each candidate via
  `workbench_engine.compare_pair` (the same lag-corrected, active-window RMSE comparison the live
  Workbench UI uses), and reports median RMSE against `RMSE_GOAL_DEG`. It is explicitly a **local,
  human-read diagnostic script, not a CI-gating test** — this design keeps that convention for the
  new preprocessing sweep.
- `rmse_pipeline_common.py` already has `discover_video_trials()` / `discover_scorable_trials()` —
  a full-dataset (not P14-only) discovery layer that pairs every trial's video with its OptiTrack
  ground truth, plus a content-addressed caching layer (`extract_landmarks_cached`,
  `compute_cache_key`) keyed on trial + candidate config + implementation fingerprint. This design
  reuses both rather than re-implementing dataset discovery or cache invalidation.
- `batch_mediapipe.py`'s `_select_patient_pose()` (docstring: *"the pendulum test always has the
  patient reclining with a roughly horizontal torso, while a standing assessor's torso is roughly
  vertical"*) and `patient_identity_tracker.PatientIdentityTracker` already encode the
  "reclined-patient" fact today, but only as a **person-selection** heuristic (which of ≤2 detected
  poses is the patient), not as an **image-orientation** correction. The tracker is stateful,
  hysteresis-gated, and already unit-tested (`tests/test_patient_identity_tracker.py`) — but it is
  only wired into `batch_mediapipe.py`'s production path. `sweep_mediapipe_config.py`'s evaluation
  path still calls the older, stateless `_select_patient_pose()`. The sweep has therefore never
  actually measured the RMSE of the person-selection logic production uses today — that gap is
  itself one of this design's three candidates.
- `MP_LEG_IDX[leg]` already restricts landmark extraction to one leg's hip/knee/ankle indices, but
  `leg` comes from trial folder-name metadata (which side was tested), not from detecting motion in
  the frame. This design's "crop to moving leg" mechanism is a genuinely new capability — spatial
  localization via motion, not label lookup.
- The knee-flexion angle metric (`arccos` of the angle between the hip→knee and ankle→knee vectors)
  is **rotation-invariant**: rotating the whole frame rotates hip/knee/ankle together, so the angle
  between two vectors sharing the knee as a vertex is unchanged. This means the rotation mechanism
  needs no inverse-transform back to original-frame coordinates for RMSE scoring — only a
  visualization/annotation use (out of scope here) would need one.

## 3. Scope

Three new, independently-measured frame-preprocessing mechanisms, plus a full-dataset diagnostic
sweep script and synthetic-input unit tests. **First pass only — mechanisms measured in isolation
against the current baseline, never stacked.** Stacking the strongest performers is an explicit,
separate second iteration (§10), so the RMSE contribution of each mechanism is never confounded by
another (e.g. rotate + crop both improving RMSE together would not by itself say whether crop was
doing anything once rotation already normalized orientation).

No change to `sweep_mediapipe_config.py`, `batch_mediapipe.py`, `patient_identity_tracker.py`, or
`rmse_pipeline_common.py` itself — this design adds new files that reuse their proven primitives
(discovery, caching, `compare_pair` scoring) rather than modifying them.

## 4. File Impact Matrix

| File | Nature of change |
|---|---|
| `mediapipe_preprocessing.py` (new) | `rotate_to_upright()`, `crop_to_moving_leg()` + its motion-based bounding-box estimator. Pure frame/array transforms — no MediaPipe or video I/O. |
| `sweep_mediapipe_preprocessing.py` (new) | Full-dataset diagnostic sweep: discovery via `rmse_pipeline_common.discover_video_trials()`, 5-candidate grid (baseline, rotate+90, rotate−90, crop, identity-tracker), caching, report + CSV. Non-gating — no assertions, no pytest integration. |
| `tests/test_mediapipe_preprocessing.py` (new) | Synthetic-input unit tests per §8. Gating (runs in normal `pytest`), fast, no video/model files required. |

## 5. The Three Candidates

### 5.1 Rotate to upright

`rotate_to_upright(frame, angle_deg)` rotates a frame by a fixed angle (90° or −90°) before
inference. Two candidates, not an adaptive per-frame detector: the torso-horizontal fact is
already established dataset-wide (§2), but which way the patient's head points relative to the
camera is not knowable a priori without a first detection pass, so both directions are swept as
independent candidates rather than auto-detected. (An adaptive version — detect orientation once
per trial from an initial low-confidence pass, then commit to a rotation for the rest of that
trial — is deferable future work if neither fixed direction wins clearly; not built in this pass.)

No inverse-transform step: per §2, the knee-angle metric computed from rotated-frame landmarks
equals the metric computed from unrotated-frame landmarks, so `angles_from_raw()`-equivalent logic
runs directly on the rotated-frame detection output.

### 5.2 Crop to moving leg

`crop_to_moving_leg(frames, fps)` takes the trial's full frame sequence and computes a motion-energy
map via frame-differencing, **skipping a fixed leading window** (`CROP_BASELINE_SEC = 3.0`,
matching the `BASELINE_SEC` constant convention already used elsewhere in this codebase's HPE
scripts, e.g. `Evaluate Models Py/evaluate_models_participant_1.py`). This is a fixed time-based
skip, not a call into `pt._detect_release()` — that function needs an already-extracted scalar
angle signal to find the release moment, which doesn't exist yet at this stage (cropping runs
*before* MediaPipe inference, on raw pixels only), so reusing it here would be circular. It then
finds the bounding box of the highest cumulative-motion connected region across the remaining
frames, pads it by **20% of the box's width/height on each side** (clamped to the frame bounds),
and uses that padded box to crop every frame before inference, so MediaPipe is not distracted by
the static leg, an assessor, or background clutter. If no clear motion region is found
(degenerate/all-still input, or a trial shorter than `CROP_BASELINE_SEC`), the function returns the
full, uncropped frame set rather than raising or guessing — same "never abort, degrade to
unavailable" convention `_score_grid` and `release_aligned_hpe_curve` already use elsewhere in this
codebase.

### 5.3 Stateful identity-tracker reuse

No new logic. `sweep_mediapipe_preprocessing.py`'s "identity-tracker" candidate calls
`patient_identity_tracker.PatientIdentityTracker.select()` (one instance per trial, matching
`batch_mediapipe.py`'s own usage) in place of the sweep's current baseline call to the stateless
`batch_mediapipe._select_patient_pose()`. This measures, for the first time, whether the
production person-selection logic actually performs differently from what the sweep has been
implicitly benchmarking against.

## 6. `sweep_mediapipe_preprocessing.py`

- **Discovery:** `rmse_pipeline_common.discover_video_trials()` — every participant with a video
  and OptiTrack ground truth, not P14-only. This directly answers the "90% of trials" framing,
  which requires cross-participant coverage; a single-participant sweep cannot show whether a
  mechanism generalizes or just overfits one recording setup.
- **Candidate grid (isolated, first pass):** `baseline` (today's stateless
  `_select_patient_pose()`, unrotated, uncropped — the actual current-production comparison point),
  `rotate_+90`, `rotate_-90`, `crop`, `identity_tracker`. Five candidates total, each independent —
  no combinations.
- **Scoring:** landmark extraction cached per (trial, candidate) via
  `rmse_pipeline_common`-style content-addressed caching (trial content hash + candidate config +
  implementation fingerprint), reusing that module's existing `sha256_file`/cache-manifest
  machinery rather than re-implementing it, so a small code tweak to one mechanism doesn't force
  re-running inference on trials/candidates it didn't touch. Each (trial, candidate) pair is scored
  via `workbench_engine.compare_pair` against that trial's OptiTrack curve, same as every other
  RMSE comparison in this codebase.
- **Reporting:** per candidate — n_trials scored, median RMSE, mean RMSE, **and % of trials with
  RMSE < 10°** (the 90%-of-trials target is a distribution statement, not a central-tendency one,
  so median alone would hide a candidate that helps the typical trial while leaving a long tail
  above goal). Printed to console and written to
  `Model_Analysis_Outputs/MediaPipe_Sweep/preprocessing_sweep_results.csv`.
- **Non-gating:** no pytest integration, no assertion on the 10°/90% target. A human reads the
  report and decides what (if anything) to promote into `batch_mediapipe.py` / production. Matches
  `sweep_mediapipe_config.py`'s existing convention exactly, for the same reason: real video
  inference is slow, depends on local data files not guaranteed present on every machine, and
  asserting a hard RMSE threshold in CI would make the test suite flap with lighting/codec/hardware
  variance rather than with actual code correctness.
- **Failure isolation:** one trial/candidate scoring failure is logged and skipped, never aborts
  the whole sweep — matches `rmse_pipeline_common._score_grid`'s existing convention.

## 7. Error Handling

- No video or no OptiTrack for a trial → excluded by `discover_video_trials()` before scoring ever
  starts (existing behavior, unchanged).
- `crop_to_moving_leg()` finds no clear motion region → falls back to the full, uncropped frame
  set; never raises.
- A rotation candidate that pushes the patient fully out of frame on some trial → that
  (trial, candidate) simply scores worse or fails `compare_pair`'s `< 4 finite samples` guard and
  is dropped for that trial/candidate combination, same path as any other underperforming
  candidate — not a special-cased error.
- Any per-trial/candidate exception during extraction or scoring → logged with trial key and
  candidate name, skipped, sweep continues (matches `_score_grid`).
- Fewer than `min_participants` (existing `rank_candidates` convention, reused if this design later
  adopts full ranking) — not gating here since this script only reports, but the report explicitly
  states n_trials/n_participants per candidate so a reader can judge coverage themselves.

## 8. Testing — `tests/test_mediapipe_preprocessing.py`

Synthetic-input only, matching `tests/test_patient_identity_tracker.py`'s existing convention
(plain arrays / a minimal landmark stand-in, no real video file or MediaPipe model load — these
run in normal `pytest`, fast and deterministic):

- `test_rotate_to_upright_pixel_mapping` — a synthetic frame with a known marker pixel, rotated by
  +90°/−90°/0°, lands at the geometrically-predicted output location and the output frame has the
  swapped (for ±90°) width/height dimensions.
- `test_rotate_to_upright_angle_invariance` — synthetic hip/knee/ankle points, knee-angle computed
  before and after applying the same rotation to all three points, are equal within float
  tolerance. Direct regression test for §2's rotation-invariance claim, the property the whole
  "skip the inverse-transform" design decision rests on.
- `test_crop_to_moving_leg_finds_high_motion_region` — synthetic frame-diff input with a known
  high-motion region in one corner and a static region elsewhere → returned bounding box covers the
  high-motion corner, not the static one.
- `test_crop_to_moving_leg_degenerate_input_falls_back_to_full_frame` — all-zero motion input, and
  separately a frame sequence shorter than `CROP_BASELINE_SEC` at the given fps → both return the
  original, uncropped frames rather than raising or returning an empty/degenerate box.
- `test_sweep_identity_tracker_candidate_invokes_tracker` — a monkeypatched/stubbed
  `PatientIdentityTracker.select()` is actually called once per frame when the `identity_tracker`
  candidate runs (smoke test for correct wiring; the tracker's own selection behavior is already
  covered by `test_patient_identity_tracker.py` and is not re-tested here).

No test in this file loads a real video, calls into `mediapipe`, or asserts an RMSE number — that
measurement lives entirely in the non-gating `sweep_mediapipe_preprocessing.py` diagnostic script
(§6), consistent with the user's explicit direction to keep RMSE-vs-target evaluation out of CI.

## 9. Deliverable / Definition of Done for This Pass

Running `sweep_mediapipe_preprocessing.py` once, end to end, against the real dataset, produces
`preprocessing_sweep_results.csv` with 5 rows (baseline + 4 mechanisms), each showing n_trials,
median RMSE, mean RMSE, and % < 10°, across every participant with video + OptiTrack — not just
P14. `tests/test_mediapipe_preprocessing.py` passes under plain `pytest` with no video/model
dependency. Whether the 90%-of-trials goal is actually met by any single candidate is a reported
outcome, not an assertion — this pass's job is to produce that number honestly, not to force it.

## 10. Out of Scope / Future

- **Stacking mechanisms.** Once this pass's report shows which individual lever(s) provide the best
  RMSE-per-compute gain, stacking the strongest performers (e.g. rotate + identity-tracker) is an
  explicit second, smaller iteration — a new small grid over combinations of only the candidates
  that individually helped, not a re-run of the full isolated grid. Not built now, to avoid
  confounding which mechanism did the work (user's explicit direction).
- **Adaptive per-trial rotation direction detection** (rather than sweeping both fixed ±90°
  candidates) — deferred unless neither fixed direction wins clearly enough to justify picking one
  for production.
- **Promoting a winning candidate into `batch_mediapipe.py` production code** — this design produces
  the measurement; wiring a chosen mechanism into the production extraction path is a follow-up
  change made after a human reads the sweep report, not part of this pass.
- **CI-gated RMSE assertions** — explicitly rejected per §6; real-video-dependent RMSE thresholds
  stay in the local diagnostic script, matching `sweep_mediapipe_config.py`'s existing convention.
- **Visualization/annotated-video output** for rotated or cropped candidates — the rotation
  invariance property (§2) means this design never needs an inverse-transform for scoring, but a
  human-readable annotated overlay would need one; not needed for this pass's goal.
