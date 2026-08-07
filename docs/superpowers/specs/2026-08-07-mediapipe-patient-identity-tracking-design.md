# Stateful Patient-Identity Tracking for batch_mediapipe.py — Design Spec

**Revision note (2026-08-07):** this spec incorporates a `/codex consult` second opinion
(session run inline, not resumed) on the choice between a trained person-ID classifier and
extending the existing geometric heuristic. Codex confirmed the geometric approach as the
right first move given dataset size, but flagged that naively porting `mediapipe_worker.py`'s
`_KneeTracker` and shank/thigh ratio gate as separate bolt-on filters would carry its own
wrong-lock failure modes. The design below reflects Codex's corrected approach (a single
stateful scorer with hysteresis, not two ported filters) rather than the original port plan.
One point from Codex's suggested init-score factors — "expected patient-side region" — is
explicitly dropped; see §3.

## 1. Goal

Fix the dominant driver of high MediaPipe-vs-OptiTrack RMSE in Pendulastic's production
pose-extraction script (`batch_mediapipe.py`): MediaPipe sometimes locks onto the assessor
(present in frame, holding/releasing the patient's leg) instead of the reclining patient.
Confirmed via annotated-video review as the dominant RMSE driver, not merely suspected.

## 2. Background / Why

- `batch_mediapipe.py`'s `_select_patient_pose()` selects the patient from up to 2 detected
  poses per frame using trunk-horizontality (reclining patient ≈ horizontal shoulder-to-hip
  vector; standing/sitting assessor ≈ vertical). It decides independently every frame — no
  memory of the previous frame's choice, no anatomical plausibility check on the resulting
  ankle. A single ambiguous frame (assessor bending over the patient, momentary occlusion)
  can flip the selection for that frame with nothing to prevent or even detect it.
- `_select_patient_pose()`'s own docstring documents that an earlier position-based heuristic
  ("patient is furthest-left knee") was confirmed wrong for Participant_14, where the assessor
  stands on the left. This project has already paid the cost of one positional-prior heuristic
  proving unreliable — the design below deliberately avoids reintroducing that class of prior.
- A separate, non-production research pipeline (`mediapipe_worker.py`, invoked via
  `pendulastic_pipeline.py` / `pendulastic_workbench.py`) already has two relevant techniques
  `batch_mediapipe.py` lacks: `_KneeTracker` (nearest-prior-position identity tracking in duo
  mode) and a biomechanical shank/thigh pixel-ratio gate (rejects anatomically implausible
  ankle detections). Both were considered for direct porting; Codex's review found real failure
  modes in each as originally implemented (see §3) that make direct porting the wrong move.
- Dataset size: 48 trial videos across ~10 participants total. The only pre-existing labeled
  tracking data (`tracking_selections.json`, 39 entries) is from the pre-migration folder
  layout and covers none of the current participants. A trained person-ID classifier was
  considered and rejected for the first pass — insufficient data diversity, no current-layout
  labels, real risk of overfitting to this specific room/camera/participant set. See §7.

## 3. Design

Replace the `_select_patient_pose()` call inside `batch_mediapipe.py`'s `process_trial()` with
a new `PatientIdentityTracker` class (new file `patient_identity_tracker.py`), instantiated
once per trial and fed each frame's detected poses in order.

**Per-frame candidate scoring** — for each of up to 2 detected poses:

- `horizontal_score` — existing trunk-horizontality calculation, unchanged.
- `visibility_score` — mean visibility of hip/knee/ankle landmarks.
- `anatomical_score` — shank/thigh pixel-length ratio plausibility (human range 0.4–2.5x),
  applied as a **soft down-weight**, not a hard reject: multiply the combined score by a
  penalty factor when outside range, rather than excluding the candidate outright. Per Codex:
  2D foreshortening, knee flexion, and camera angle can produce implausible ratios even for
  a correctly-identified person, so this must inform scoring, not gate it categorically.
- `continuity_score` — only once an identity is locked (see below): inverse distance from the
  last confirmed knee position, **normalized by that frame's thigh length** (hip-to-knee
  distance), not raw pixels. This avoids the fixed-150px-jump-threshold fragility Codex
  identified in `_KneeTracker` — a threshold in raw pixels is resolution- and
  camera-distance-dependent, and this project's participants are not filmed at a fixed
  distance/zoom.

The four component scores combine into one `combined_score` via a weighted sum (weights are
module-level constants, tunable at implementation time — proposed starting point: equal weight
on `horizontal_score`/`visibility_score`/`continuity_score`, with `anatomical_score` applied as
a multiplicative penalty rather than an additive term, since it's meant to down-weight
implausible candidates rather than reward plausible ones). Exact weights are an implementation-
time calibration decision, same as `N` and the confidence floor below — not fixed here.

Explicitly **not** included: any positional prior ("patient is on the left/right of frame").
Codex's suggested four-factor init score included "expected patient-side region," but this
codebase's own history (§2) shows that exact class of heuristic already failed for
Participant_14. Init score uses horizontal + visibility + anatomical only.

**Selection state machine:**

1. **Init** (no lock yet): pick the candidate with the best combined
   horizontal + visibility + anatomical score.
2. **Locked:** the currently-tracked candidate is re-scored each frame using all four factors
   (continuity now included) and stays selected by default. A competing candidate can only
   take over after it outscores the tracked candidate for **N consecutive frames**
   (hysteresis; default `N = 5`, a module-level constant). This is the core fix: today's bug is
   that a single ambiguous frame can flip the whole trial, because there is no memory and no
   requirement for sustained disagreement before switching.
3. **Ambiguous:** if the best candidate's combined score falls below a minimum confidence floor
   (module-level constant, default proposal `0.35` on the same 0–1 scale as the component
   scores — exact value to be calibrated during implementation against the real 48-trial set,
   same as `N=5` above), or no poses are detected, the frame is marked ambiguous and written
   as a NaN row — matching existing NaN-row behavior for undetected frames. Critically, an
   ambiguous frame does **not** update the locked identity's continuity state, so one bad frame
   can't corrupt the track the way a naive "always follow the winner" scorer would.
4. The existing per-frame ankle NaN gate (score below `VIS_THRESH`) is unchanged — it already
   matches Codex's "preserve a missing observation rather than substitute a dubious ankle."

## 4. Logging

Two additions to `batch_mediapipe.py`'s existing per-trial CSV output:

- `identity_score` — the winning candidate's combined score for that frame.
- `identity_ambiguous` — boolean, true for frames marked ambiguous per §3.3.

Plus a stdout summary at the end of each trial's processing, in the same style as the existing
`MediaPipe: {mp_hits}/{len(rows)} frames ({pct}%)` line:
`Identity: {n_switches} switches, {n_ambiguous}/{len(rows)} ambiguous frames`.

This is groundwork for future manual review or a future labeled dataset (per Codex, useful
regardless of whether a classifier is ever built) — not itself a training pipeline.

## 5. Rollout

Add a `--force` flag to `batch_mediapipe.py`'s CLI (alongside existing `--leg`/`--dry-run`), so
trials already known to have wrong-person tracking can be explicitly reprocessed without manual
CSV/video deletion. `discover_new_trials()`'s existing skip-if-CSV-and-video-exist logic is
unchanged for the default (non-`--force`) path.

## 6. Testing

- Unit tests for `PatientIdentityTracker`, independent of real video/MediaPipe I/O — feed
  synthetic landmark sequences (fake objects exposing `.x`/`.y`/`.visibility`):
  - Correct initial lock when one candidate is clearly horizontal+visible+anatomically valid
    and the other is not.
  - A single contradictory frame does not flip the locked identity.
  - N consecutive contradictory frames do flip it (and `N-1` do not).
  - Both candidates scoring below the confidence floor marks the frame ambiguous and leaves
    the lock state unchanged.
  - Continuity distance normalization: identical pixel displacement is scored differently at
    different thigh lengths (i.e. it's relative, not absolute).
- Before/after RMSE comparison on the real 48-trial dataset via the existing
  `batch_imu_vs_optitrack_rmse.py` (or `model_vs_optitrack_eval.py`), on trials with OptiTrack
  ground truth, to confirm the change actually reduces RMSE and not just visually-inspected
  identity switches.
- Manual spot-check of a few trials' annotated video output (`_draw_pose`) for trials
  previously known to have wrong-person tracking, reprocessed via `--force`.

## 7. Deferred: trained classifier

Not built in this pass. Per Codex's recommendation, revisit only if post-rollout error
analysis on real trials shows a residual failure class the geometric/continuity scorer
cannot resolve (e.g. two people both reclining and anatomically plausible simultaneously —
not currently a known scenario in this dataset). If that need materializes: a classifier
would apply only to frames the deterministic tracker itself marks ambiguous (§3.3), with the
deterministic tracker remaining authoritative for everything else, and ambiguous frames queued
for manual labeling rather than assumed correct — explicitly to avoid a system that
"auto-corrects" using its own unverified predictions as training signal.

## 8. Out of scope

- Any change to `mediapipe_worker.py` or its `--guided` mode (research pipeline, untouched).
- Any change to the OptiTrack ground-truth pipeline or RMSE scoring scripts themselves — this
  work changes what feeds into them, not how they score.
- Reprocessing every historical trial automatically — `--force` reprocessing of specific known-
  bad trials is a manual, reviewed action per §5, not an automatic bulk rerun.
