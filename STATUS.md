# Pendulastic status

**This is the single source of truth for what is being worked on.** Read it before
starting; update it in the same PR that changes a workstream's state. Dated notes in
`docs/reports/` are history, not status. Last full review: 2026-11-10.

## Priorities (owner, 2026-11-10)

1. **Neuromech emulator / rig** is the top priority.
2. The PT scorer must be settled before any rig result quotes a PT score (verify the
   September lineage against main, then port only what main is missing).
3. Legacy video / desktop code is to be archived, not developed.
4. Webapp deployment and paper/thesis results follow, in that order.

## Workstreams

### 1. Neuromech knee-spasticity emulator and rig — ACTIVE, top priority
- **Goal:** a motor rig that plays back knee swings of known severity (MAS 0 / 1+ / 2 / 3)
  so the phone app can be checked against known truth and clinicians can rate the same swing.
- **Canonical branch:** `main`. `feat/neuromech-emulator` and PR #69 are superseded by #70.
- **Entry points:** `python -m neuromech.build`; `neuromech/session.py` (plan, score,
  check-log); `neuromech/dryrun.py`; Pendulum Player in `neuromech/labview/player/`;
  plan in `docs/plans/2026-10-26-001-plan-neuromech-emulator-unified.md`.
- **Open PRs:** #86 (plan review), #87 (provenance, B14), #90 (fall-speed check, B13;
  contains #82, which should be closed).
- **Not yet on main:** 2 commits on `feat/neuromech-player` made after #71 merged
  (C# port of the #75 excursion gate).
- **Current numbers (re-run on main, 2026-11-10):** `python -m neuromech.build` writes
  67 scenarios. `python -m neuromech.dryrun` delivers 7 of 9 runs in their intended band
  (median tracking 0.73 deg; phone vs encoder |dPT7| max 0.015). The misses are
  `B-claspknife` and `M-catch_release_high`: both intend PT7 0.139 and deliver about
  0.115, across the 0 / 1 band edge. Older docs quoting 9/9 or other counts are stale.
- **Next actions:** land #86, #87, #90; port the two player commits; decide what to do
  about the two band-edge misses (move the targets off the edge, or accept); quantify `neuromech/session._phone_angle` (raw gyro integration) against the
  shipped app pipeline using `neuromech/phone_trial.py`; then rig sessions.
- **Owner decisions pending (O1–O5):** arm range ±65° or 0.75× amplitude render;
  actuator of at least 20 N·m; when to build the vertical rig; two blind clinicians per
  profile; clasp-knife adaptation handling.

### 2. Synthetic phone-trial generator — ACTIVE (part of neuromech)
- **Goal:** check the app end to end without a phone, against known truth.
- **Canonical branch:** `feat/synthetic-trial-generator` (pushed 2026-11-10, no PR yet).
- **Entry points:** `neuromech/phone_trial.py`, `webapp/scripts/replay-trial.mjs`,
  `neuromech/README.md` §7.
- **Also on that branch:** the AHRS gravity-seed sign fix it found
  (`mobile-imu-core/src/ahrs.rs`, `pendulastic_imu_server.py`, regenerated `golden.rs`),
  and `bench_figures/` scripts.
- **Next action:** open the PR and land it.

### 3. PT scorer — ACTIVE, gates workstream 1
- **Goal:** one validated scorer, identical in every language.
- **Implementations:** Python `pendulastic_pt_score.py` (reference),
  Rust `mobile-imu-core/src/pt_score.rs` (ships in the webapp),
  C# `neuromech/designer/PtScore.cs`. `run_statistical_engine.py` holds a fourth
  `HEALTHY_REF` copy.
- **Calling convention for rig and app work:** `neuromech.build.score_angle`
  (100 Hz grid, release index given, `detrend=False`). `pt_report_common.score_trial`
  uses the `compute_pt_params` defaults (`detrend=True`, auto release) and is not
  interchangeable.
- **Open question:** whether `main` carries the corpus-validated September fixes
  (`d2e2cee` release-at-onset, `f429356`, `9a555de` settle, `61d5723`, `f_out_of_range`).
  None is an ancestor of `main`. Not yet verified either way.
- **Open PRs:** #76 (drop `omega_max_n`) and #83 (desktop live view; contains #76).
- **Next action:** run the matched corpus test-retest and IMU-vs-OptiTrack RMSE on
  `main` and on `feat/rust-abs-flex-projection`; write the comparison report; port only
  what main lacks.
- **Do not:** remove `abs()` from clinical swing-angle extraction (regresses corpus
  test-retest; see PR #68).
- **Owner decisions pending:** drop `omega_max_n` (#83) or keep it; unify the two
  `score_waveform` windows.

### 4. Rust AHRS / phone replay — STABLE
- **Canonical branch:** `main` (#78 merged).
- **Open PRs:** #91 (settled-tail gyro-bias re-estimate measured, does not help, not
  shipped; doc-only, safe to merge).
- **Pending:** gravity-seed fix on `feat/synthetic-trial-generator` (workstream 2).

### 5. OptiTrack / RMSE validation — STABLE
- **Canonical branch:** `main` (#81, #88 merged).
- **Entry points:** `batch_imu_vs_optitrack_rmse.py`, `rmse_pipeline_common.py`.
- **Gap:** the result "AHRS floor has zero effect on 144 paired corpus trials" is
  recorded only in agent memory. Write it into a repo report.

### 6. Bench / test-retest (September lineage) — FROZEN, to be harvested
- **Branches:** `bench/retest-abs`, `bench/retest-diagnostics`,
  `fix/swing-angle-signed-abs-fold`, `feat/rust-abs-flex-projection`,
  `fix/optitrack-trial-quality`. About 110 to 130 commits that never reached `main`.
- **Open PRs:** #68 (based on a non-main branch).
- **Holds, not on main:** the 2026-09-04 instrument-validation plan, tilted-rig protocol
  and run sheets, dynamic-characterization run sheet, `regenerating-figures.md`,
  September reports.
- **Next action:** after workstream 3's comparison, rescue those docs, decide #68, tag
  each branch `archive/<name>`, then retire. Do not start new work on these branches.
- **Canonical bench number:** phone static accuracy 0.58° (the 0.92° in the Notion SOP
  is stale).

### 7. Webapp (phone IMU app) — LIVE, NOT VALIDATED, paused behind 1 and 3
- **Canonical branch:** `main`. `integrate-webapp` and `feat/webapp-workbench-restyle`
  are stale; do not build or deploy from them.
- **Entry points:** `webapp/` (vanilla ES modules, wasm from `mobile-imu-core`);
  deploy is a manual Vercel upload of `webapp/dist` to project `pendulastic-app`.
- **Backlog:** scripted deploy; stop tracking `webapp/src/build-id.js`; eviction soak
  test; zone recalibration (blocked: only 2 legs with MAS ≥ 1); app bugs in `TODOS.md`.

### 8. Video / pose estimation, desktop apps, `web/`, `mobile/` — TO ARCHIVE
- Idle since August. Owner decision: archive.
- **First pass:** move `web/`, `mobile/`, `Evaluate Models Py/` and root scripts with no
  importers and no CI test into `archive/`.
- **Frozen in place for now** (still imported by tests or other code):
  `workbench_engine.py`, `pendulastic_app.py`, `analysis_pipeline.py`,
  `batch_mediapipe.py`, `pendulastic_viewer.py`. Do not develop them.

### 9. Hygiene and reporting — STALLED
- **Open PRs:** #66 (repo cleanup; archives the daily and Monday reports). 43 draft
  `claude/daily-report-*` PRs are open and should be closed once #66 lands.
- The nightly hygiene agent last ran 2026-09-23 and failed; no scheduled task exists.
  Owner decision pending: retire it or re-register it.
- Stale worktrees to remove (with owner confirmation): `wt-lf`, `wt-prod`, `wt-gate`,
  `wt-rmse-offset`, `wt-replay-parity`, `wt-neuromech`, `wt-fall`.

## Paper / thesis backlog
- Collect MAS for P17–P24; confirm P15 left; verify the P4 left/right transposition.
- Name one canonical figure pipeline.
- Gate the Python test files that do not need Tk in CI (61 of 74 are ungated).
