# Pendulastic status

**This is the single source of truth for what is being worked on.** Read it before
starting; update it in the same PR that changes a workstream's state. Dated notes in
`docs/reports/` are history, not status. Last full review: 2026-11-11.

## Priorities (owner, 2026-11-10)

1. **Neuromech emulator / rig** is the top priority.
2. The PT scorer must be settled before any rig result quotes a PT score.
3. Legacy video / desktop code is to be archived, not developed.
4. Webapp deployment and paper/thesis results follow, in that order.

## Quality bar (owner, 2026-11-11)

This is clinical-measurement software. A change to the scorer, the AHRS filter or rig
scoring lands alone, with: corpus before/after and every changed trial explained; the
Rust core run on real trials, not only read; parity checks across Python, Rust and C#;
an independent review by someone who did not write it; and a version bump when reported
scores move. "Not verified" is stated, never implied.

## Decisions waiting on the owner

1. **Merge the AHRS seed fix (#92)?** It is physically correct and confirmed in Rust,
   and it changes every scored phone trial (workstream 4).
2. **App versus reference scoring footing.** The app (20 Hz ticks, EMA 0.3, no flex
   projection) and the Python reference (100 Hz, EMA 0.5, flex projection) disagree on
   56 of 179 on-screen verdicts. Either the app moves to the reference footing or the
   thresholds are re-derived for the app's (workstream 7).
3. **Re-derive `HEALTHY_REF` with the drift bound?** The bound is correct but moves four
   reference parameters by 2 to 5 percent (workstream 3).
4. **Count oscillations from the first trough?** Recommended remedy for the scorer's
   release-timing sensitivity; needs a score-formula version bump (workstream 3).
5. **Release detector design.** September's detector was rejected as a port; four
   design options are on file (workstream 3).
6. **Rewrite history to remove clinical data already pushed on PR #90's branch?**
   (workstream 1).
7. `omega_max_n`: drop it (#83, which contains #76) or keep it.
8. Rig decisions O1–O5: arm range ±65° or 0.75× amplitude render; actuator of at least
   20 N·m; when to build the vertical rig; two blind clinicians per profile; clasp-knife
   adaptation handling.
9. Nightly hygiene agent: retire it or re-register it.

## Workstreams

### 1. Neuromech knee-spasticity emulator and rig — ACTIVE, top priority
- **Goal:** a motor rig that plays back knee swings of known severity so the phone app
  can be checked against known truth and clinicians can rate the same swing.
- **Canonical branch:** `main`. `feat/neuromech-emulator` and PR #69 are superseded by
  #70. The two late commits on `feat/neuromech-player` are already on main via #80.
- **Entry points:** `python -m neuromech.build`; `neuromech/session.py` (plan, score,
  check-log); `neuromech/dryrun.py`; Pendulum Player in `neuromech/labview/player/`;
  plan in `docs/plans/2026-10-26-001-plan-neuromech-emulator-unified.md`.
- **Merged 2026-11-11:** #87 (provenance, B14), #86 (plan review). #82 closed as
  contained in #90.
- **Open PRs:** #90 (fall-speed check, B13). Its reference file now holds only person,
  A0 and peak. Earlier commits on that branch and on `neuromech/fall-speed-check` still
  contain per-leg clinical fields and are public; see decision 6.
- **Current numbers (main, 2026-11-10):** `python -m neuromech.build` writes 67
  scenarios. The A-P2 and A-P3 presets are unreachable on the current scorer. The dry
  run delivers 7 of 9 runs in their intended band; the two misses are one scenario
  (`B-claspknife` and `M-catch_release_high` share parameters) and are pinned by a test
  from #89.
- **Why the two runs miss** (`docs/reports/2026-11-10-neuromech-band-edge-misses.md` on
  `neuromech/band-edge-misses`): `session.score` gives the scorer the commanded release
  while the delivered motion starts about 18 ms later; the scorer then counts a
  start-of-swing shoulder as half an oscillation. 40 of 67 scenarios are affected.
- **In progress:** `neuromech/session-release-alignment` makes rig sessions score from
  the delivered release. Not reviewed, not merged.
- **Rig path versus app path** (simulation only;
  `docs/reports/2026-11-10-rig-phone-angle-vs-app.md` on `eval/rig-phone-angle-vs-app`):
  `session.py`'s gyro integration tracks truth within about 1 degree when the swing is
  on gyro-y, but silently accepts an off-axis mount; the app's score is 0.06 to 0.15
  PT7 higher than the 100 Hz score. Rig scores must not be described as what the app
  would report. The generator simulates a vertical swing; a horizontal rig with the
  phone flat is untested.
- **Next actions:** review and land the release alignment; land #90; then rig sessions.

### 2. Synthetic phone-trial generator — ACTIVE (part of neuromech)
- **Goal:** check the app end to end without a phone, against known truth.
- **Canonical branch:** `feat/synthetic-trial-generator`, PR #92 (draft; see
  workstream 4 for why).
- **Entry points:** `neuromech/phone_trial.py`, `webapp/scripts/replay-trial.mjs`,
  `neuromech/README.md` §7.
- **Known app defects it exposes** (strict-xfail tests in
  `neuromech/tests/test_phone_trial.py`): a brisk lift fires the release detector, and a
  steady 9 degree sag through the hold is never caught. On rig-format recordings the
  app's offline scorer places the release inside the lift on some scenarios and takes
  the first sync pulse as the release when given a whole recording.

### 3. PT scorer — ACTIVE, gates workstream 1
- **Goal:** one validated scorer, identical in every language.
- **Implementations:** Python `pendulastic_pt_score.py` (reference),
  Rust `mobile-imu-core/src/` (`scoring.rs`, `pt_score.rs`; ships in the webapp),
  C# `neuromech/designer/PtScore.cs` (given-release, `detrend=False` path only; it has
  no release detector and no drift correction). `run_statistical_engine.py` holds a
  fourth `HEALTHY_REF` copy.
- **Conventions:** on screen the app scores with `detrend=False`; the record the app
  STORES uses `detrend=True` (`finish_detrended` in `wasm.rs`, `persistTrial` in
  `webapp/src/app.js`). `neuromech.build.score_angle` is 100 Hz, release given,
  `detrend=False`. `pt_report_common.score_trial` uses the `compute_pt_params` defaults
  (`detrend=True`, auto release). These are not interchangeable.
- **Lineage comparison** (`docs/scorer-lineage-comparison`): test-retest under
  `detrend=False` is a tie between `main` and the September lineage. `9a555de` is
  already on main. `61d5723` (signed swing angle) must stay out.
- **Drift-correction bound (`4bd13a2`):** ported to Python on
  `fix/port-drift-bound-and-compare-orientation`; `detrend=False` output is bit-identical
  on the corpus. Independent review: code correct. Open items: the matching Rust change
  (in progress on the same branch), handling of the new `AmplitudeExceedsCurveError` in
  callers, and decision 3. Not merged.
- **`compare_pair` orientation (`fb46178`):** ported on the same branch; independent
  review found the rule wrong when the optical hold is occluded or the recording opens
  at rest (up to 17 of 145 decisions). Must be reworked and split into its own PR.
  Until then main's reported IMU-versus-OptiTrack RMSE is inflated (about 22 degrees
  median where correctly oriented curves give about 15).
- **Onset release detector (`d2e2cee`, `35d3192`, `05b5a4e`):** REJECTED as a port
  (`docs/release-detector-port-findings`). It fixes 12 corpus trials and breaks 2, fails
  main's lift-and-hold tests, and cannot handle curves whose hold is the minimum, which
  includes most optical control curves. See decision 5.
- **Release-timing sensitivity** (`docs/oscillation-count-sensitivity`): 40 of 162
  scorable corpus trials count a start-of-swing shoulder as half an oscillation; a 10 ms
  release shift changes N on 34 trials. Not what limits test-retest reliability.
  `area_ratio` has a separate, larger release sensitivity. See decision 4.
- **Held until a bench session can back them:** frequency cap and `f_out_of_range`
  (`c2dfe67`), Rust flex-projection tier (`f429356`).
- **Open PRs:** #76 (drop `omega_max_n`) and #83 (desktop live view; contains #76).
  Both conflict with `main`.
- **Do not:** remove `abs()` from clinical swing-angle extraction.

### 4. Rust AHRS / phone replay — CHANGE PENDING OWNER DECISION
- **Canonical branch:** `main` (#78 merged; #91, a negative result, merged 2026-11-11).
- **Gravity-seed fix (PR #92, draft):** the seed quaternion had the tilt sign mirrored in
  Python and Rust. On real trials the phone starts about 100 degrees from flat, so the
  old filter started about 145 degrees wrong about gravity and did not converge within a
  trial. The fix changes every scored trial (A0 by a median 4.4 degrees; 24 verdicts at
  the reference settings, 36 on screen at the app's settings), improves resting-tail
  drift and A0 test-retest, and leaves IMU-versus-OptiTrack RMSE unchanged. Reports:
  `docs/seed-fix-corpus-check`, `docs/seed-fix-rust-confirmation`.
- **Rust port fidelity:** against Python at the app's settings on the same stream,
  traces are identical and verdicts agree on 179 of 179.
- **Consequence:** every phone-IMU number measured before #92 merges used the mirrored
  seed. The live web app has the bug until #92 merges and the app is redeployed.
  `ALGORITHM_VERSION` must be bumped when it lands.

### 5. OptiTrack / RMSE validation — STABLE
- **Canonical branch:** `main` (#81, #88 merged).
- **Entry points:** `batch_imu_vs_optitrack_rmse.py`, `rmse_pipeline_common.py`.
- **Gaps:** the result "AHRS floor has zero effect on 144 paired corpus trials" is
  recorded only in agent memory. The RMSE gate compares only the active swing window, so
  it could not see the seed bug; do not treat it as a sufficient gate for filter changes.

### 6. Bench / test-retest (September lineage) — FROZEN, to be harvested
- **Branches:** `bench/retest-abs`, `bench/retest-diagnostics`,
  `fix/swing-angle-signed-abs-fold`, `feat/rust-abs-flex-projection`,
  `fix/optitrack-trial-quality` (local only, not on origin). About 110 to 130 commits
  that never reached `main`. #68 closed as moot for main.
- **Holds, not on main:** the 2026-09-04 instrument-validation plan, tilted-rig protocol
  and run sheets, dynamic-characterization run sheet, `regenerating-figures.md`,
  September reports.
- **Next action:** rescue those docs, tag each branch `archive/<name>`, then retire. Do
  not start new work on these branches.
- **Canonical bench number:** phone static accuracy 0.58° (the 0.92° in the Notion SOP
  is stale). Measured before the seed fix.

### 7. Webapp (phone IMU app) — LIVE, NOT VALIDATED
- **Canonical branch:** `main`. `integrate-webapp` (local only, not on origin) and
  `feat/webapp-workbench-restyle` are stale; do not build or deploy from them.
- **Entry points:** `webapp/` (vanilla ES modules, wasm from `mobile-imu-core`);
  deploy is a manual Vercel upload of `webapp/dist` to project `pendulastic-app`.
- **Known gap to the reference:** see decision 2. The app's PT score is a median 0.058
  higher than the reference configuration and reads borderline where the reference
  reads healthy on 32 of 179 corpus trials. Zone thresholds and `HEALTHY_REF` were
  derived on the reference footing; whether they suit the app's has not been tested.
- **Backlog:** scripted deploy; eviction soak test; zone recalibration (blocked: only 2
  legs with MAS ≥ 1); app bugs in `TODOS.md`.

### 8. Video / pose estimation, desktop apps, `web/`, `mobile/` — TO ARCHIVE
- Idle since August. Owner decision: archive.
- **First pass:** move `web/`, `mobile/`, `Evaluate Models Py/` and root scripts with no
  importers and no CI test into `archive/`. Note #66 currently deletes
  `Evaluate Models Py/` instead; reconcile before either lands.
- **Frozen in place for now** (still imported by tests or other code):
  `workbench_engine.py`, `pendulastic_app.py`, `analysis_pipeline.py`,
  `batch_mediapipe.py`, `pendulastic_viewer.py`. Do not develop them, except
  `workbench_engine.compare_pair`, which the RMSE pipeline uses.

### 9. Hygiene and reporting — STALLED
- **Open PRs:** #66 (repo cleanup; archives the daily and Monday reports). Its
  `.gitignore` would ignore the `bench_figures/` sources #92 tracks; fix before merge.
  43 draft `claude/daily-report-*` PRs are open and should be closed once #66 lands.
- The nightly hygiene agent last ran 2026-09-23 and failed; no scheduled task exists.
- Run `git worktree list` for the current worktrees rather than trusting a list here.
- Disk: drive C: was full on 2026-11-11 and is at about 4 GB free after removing
  finished worktrees and regenerable build output. Builds and tests fail with "No space
  left on device" when it fills; `mobile-imu-core/target` and `neuromech/out` in any
  worktree are safe to delete.
- No `.gitattributes`: this Windows checkout can write CRLF into files that are LF on
  main. Compare `git diff --stat` with `git diff --stat --ignore-cr-at-eol` before
  committing.

## Paper / thesis backlog
- Collect MAS for P17–P24; confirm P15 left; verify the P4 left/right transposition.
- Re-derive IMU results after the seed fix lands.
- Name one canonical figure pipeline.
- Gate the Python test files that do not need Tk in CI (61 of 74 are ungated).
