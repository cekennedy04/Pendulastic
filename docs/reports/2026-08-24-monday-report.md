# Weekly Progress Report — Pendulastic

**To:** Dr. Perez
**From:** Claire Kennedy
**Date:** Monday, 2026-08-24 (covering 2026-08-17 through 2026-08-22)

---

## 1. Greeting & Overview

Hi Dr. Perez — here's a summary of last week's work on Pendulastic. The
week split across three threads: (1) closing out the trial-exclusion UI and
shipping a new MAS flexion/extension scoring path, (2) a deep instrument-
validation and clinical-validity pass comparing our IMU and MediaPipe
pipelines against OptiTrack and MAS, written up as two internal analysis
documents, and (3) starting exploratory work on a phone-native (Rust) IMU
core as a prototype for an eventual standalone mobile app. Fifty commits
landed on `main` this week (2026-08-17 to 2026-08-22, none on 08-23/08-24),
plus one nightly hygiene report on 08-18.

The headline finding from the validation pass, stated plainly: our IMU
pipeline is the only modality currently worth pursuing (RMSE 14.84°
mean vs. OptiTrack; MediaPipe RMSE 36.0° mean, effectively no agreement),
but neither has yet cleared the bar of "generalizes to an unseen
participant" — full detail in `docs/reports/2026-08-19-full-project-analysis-vs-mas.md`.

## 2. Key Work & Development Done

**Trial exclusion UI (merged 08-18):**
- Added a trial table view with background-loaded per-trial PT scores, a
  "Toggle Excluded" button with busy-flag gating and duplicate-key
  confirmation, and empty-selection/fully-excluded-participant guards on
  Generate.
- Fixed a registry-write race on Windows (transient sharing violation,
  now retried) and an idle table-load polling chain that never terminated.

**MAS flexion/extension scoring (08-18 to 08-19):**
- Added `mas_flexion`/`mas_extension` optional columns to `mas_scores.csv`,
  a direction filter for flexion/extension pooling in the PT lookup, and
  `pair_pt_and_mas_by_direction` for canonical pair-record keys.
- Added a `PENDING_MAS_GRADE` sentinel so a not-yet-assessed overall grade
  is distinguishable from a real 0, and added the corresponding fields to
  `MasEntryPanel`.

**Instrument validation & clinical-validity analysis (08-17 to 08-19):**
- Ran IMU-vs-OptiTrack and MediaPipe-vs-OptiTrack RMSE/ICC(2,1)/Bland-Altman
  comparisons across 61 trials (5 participants) and 37 trials respectively,
  plus a Spearman correlation between IMU-derived R2n and MAS grade, and an
  MS-vs-control mixed-effects group comparison. Written up in
  `docs/reports/2026-08-19-results-data-analysis-draft.md` and synthesized
  against the "beats MAS" question in
  `docs/reports/2026-08-19-full-project-analysis-vs-mas.md`.
- Added a "PT cohort analysis" tooling pass — methodology-comparison
  scripts, figure/paper generation scripts, and sweep run logs — checkpointed
  ahead of a planned laptop switch (commit `39a0baa`).

**Pin + interpolation trial-correction feature (08-19 to 08-20):**
- Full design-spec-first build: click-to-place ankle pin, arc-based
  interpolation, corrections persisted as fingerprint-validated per-leg
  sidecars (schema bumped to v2 with a `tracker_version` provenance field),
  collision-safe backups before overwrite, and event replay for
  reconstructable pin state.
- Four follow-on hardening fixes once real use surfaced edge cases: a
  race in `_on_image_click` during an in-progress retrack, an anchor cache
  that needs eviction on retrack (not just hip/knee equality), idempotency
  for repeated "Interpolate Pins" clicks, and defense against malformed or
  stale sidecar files.

**Analysis Panel scoring fix (08-21, `05127ec`):** see Roadblocks below.

**mobile-imu-core (08-21 to 08-22):** a throwaway Rust core (AHRS,
calibration, stillness detection) with Android/Kotlin and iOS/Swift capture
harnesses, prototyping on-device sensor fusion ahead of a possible
standalone phone-IMU app — spec at
`docs/plans/2026-08-21-001-feat-phone-imu-pendulum-app-plan.md`. Also moved
the MAS validation graphs to their own page in the desktop app, and landed
video-review-dialog/viewer correction-handling fixes with matching test
coverage.

## 3. Visuals, Figures & UI State

Straight answer: **no new figure images or UI screenshots were generated
this week** that I can point you to as files. The four `.png` files in the
repo root (`lateral_impact_presentation*.png`, `oneoff_lateral_check*.png`)
predate this reporting window (last touched 08-14) and aren't new work, so
I'm not linking them here as if they were.

What *is* new is quantitative, not visual: the RMSE/ICC/Bland-Altman tables
in `docs/reports/2026-08-19-results-data-analysis-draft.md` (Results
section) and the summary table in
`docs/reports/2026-08-19-full-project-analysis-vs-mas.md` (Section 1). I'd
recommend we treat generating the actual figures (Bland-Altman plots, the
paper-figure scripts added this week like
`generate_all_figures_comprehensive.py` and
`generate_group_condition_figures.py`) as this week's task rather than
something to report on retroactively.

On the UI: the MAS validation graphs move to their own page (`94c09fb`) and
the new trial table view are both in the desktop Tkinter app
(`pendulastic_app.py`), not the `web/` frontend. I don't have a display
available in this reporting pass to capture a live screenshot — happy to
grab one live in our next sync if useful.

## 4. Roadblocks, Errors, & Solutions

- **MAS/RMSE contradiction on a healthy control (resolved, `05127ec`).**
  A control trial (Participant 16, Left/Control/Trial 1) scored RMSE 6.95
  and a predicted MAS of 4, while OptiTrack ground truth said MAS 0 on the
  same data. Root cause: `attach_rmse` wasn't cross-correlation-aligning
  the IMU/MediaPipe curve to OptiTrack's clock before scoring — the
  existing alignment was a Y-baseline shift only, which assumes
  synchronized clocks that this trial didn't have (needed a -0.717s
  shift). Fixed by routing through the same `compare_pair` lag-alignment
  used for the RMSE CSVs. Also added a top-contributor flag to the
  multi-trial report (e.g. "area_ratio 0.95 (ref 0.05) 73% of score") so
  an implausible score reads as explainable rather than arbitrary.

- **Marker/anchor tracking drift under repeated correction (resolved,
  four fixes 08-20).** The new pin-interpolation feature's anchor cache
  could go stale across retrack, and hip/knee-equality wasn't a reliable
  eviction signal — fixed with a per-frame anchor cache that's evicted
  correctly on retrack and by interpolation's own write loop, plus
  idempotency for repeated "Interpolate Pins" clicks and hardening
  against malformed/stale correction sidecars.

- **Oscillation-count bound (resolved, `a1ca2b5`).** `compute_pt_params`'s
  oscillation counting wasn't bounded to the active swing window — fixed.

- **Sample-size limit on generalization claims (open, not a bug).** The
  clinical-validity analysis is explicit that we don't yet have enough
  independent participants for any accuracy claim to survive a proper
  generalization test — this is the current ceiling on "beats MAS," not
  an engineering gap. See Section 6 of the 08-19 full-project-analysis doc.

## 5. Questions & Request for Feedback

1. Given IMU clearly outperforms MediaPipe on agreement (14.84° vs. 36.0°
   mean RMSE) but neither has cleared the generalization bar, do you want
   us to prioritize recruiting more independent participants before
   further modeling work, or continue tuning method (e.g. the lag-alignment
   fix class of bug) on the current cohort?
2. Is now the right time to invest further in `mobile-imu-core` (Rust +
   native harnesses) given it's explicitly scoped as a throwaway
   prototype, or should that stay paused until the core accuracy question
   is resolved on the desktop pipeline?
3. For MAS flexion/extension pooling — does splitting by direction change
   how you want inter-rater reliability reported, or should we keep
   reporting an overall MAS alongside the split scores?
4. The pin+interpolation correction feature changes trial data after the
   fact (with sidecar provenance/versioning). Are you comfortable with
   clinician-applied corrections being used in scored analysis, or should
   corrected trials be flagged/excluded from the validation dataset by
   default?

## 6. Next Week's Action Plan

- Generate the actual figures (Bland-Altman, longitudinal, group-condition)
  from this week's analysis scripts so the next report has real images to
  link, not just tables.
- Continue eng-debt items flagged in the 08-21 `/plan-eng-review`: strengthen
  `mobile-imu-core`'s cross-platform parity test beyond same-Rust-code
  equivalence (currently doesn't test real device sensor capture or
  timestamp handling), and specify a hardware-capability fallback policy
  for devices with degraded/missing gyroscope or magnetometer.
- Decide and implement a trial-history/browse/recovery model for the phone
  app plan (currently no way to reopen, re-export, or recover a trial after
  the app is killed mid-capture).
- Pending your answer to Question 1 above: either begin participant
  recruitment/scheduling, or continue hardening the scoring pipeline on the
  current cohort.
- Run `/design-consultation` for the phone IMU pendulum app before its
  visual system (color, typography, iconography) locks in by default.

---

*Prepared from repository commit history (2026-08-17 through 2026-08-22),
the nightly hygiene report of 2026-08-18, and the two internal analysis
documents dated 2026-08-19. No figures were fabricated for this report —
Section 3 reflects an honest gap rather than placeholder images.*
