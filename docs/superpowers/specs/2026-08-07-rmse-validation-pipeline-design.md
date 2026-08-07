# Auto-Triggered RMSE Validation Pipeline — Design Spec

## 1. Goal

Give Pendulastic a pipeline that reacts automatically as new trial data lands, so that
IMU-vs-OptiTrack and MediaPipe-vs-OptiTrack accuracy figures and parameter-sweep results are
always current — never a manual, one-off script run against a stale snapshot. This directly
supports iterating the phone IMU fusion algorithm and the MediaPipe pose-fitting config to
reduce RMSE against OptiTrack ground truth.

## 2. Background / Why

- `batch_imu_vs_optitrack_rmse.py`, `model_vs_optitrack_eval.py`, `sweep_mediapipe_config.py`,
  and `sweep_imu_config.py` already compute real RMSE-vs-OptiTrack numbers, but each is an
  explicitly one-off/diagnostic script: hardcoded to specific participants (P2/P4), scoped to a
  single participant for iteration speed (P14, or "every trial" but only when run by hand), or
  simply never wired into anything that runs automatically.
- Investigating "which recording methodology is more accurate" during this design surfaced the
  concrete cost of that gap: the only existing IMU-vs-OptiTrack numbers
  (`Model_Analysis_Outputs/imu_vs_optitrack_rmse.csv`, Aug 5) were being informally compared
  against MediaPipe/pose-model numbers (`pendulastic model analysis/accuracy_ranking.csv`,
  `global_model_leaderboard.csv`) that were seven weeks stale (Jun 19/22) — an apples-to-oranges
  comparison that nobody had a way to know was stale without manually checking file dates.
- `sweep_imu_config.py` already exists with a hand-tuned 288-combo AHRS/fusion parameter grid
  (`WIDE_GRID`), scored against OptiTrack via `workbench_engine.compare_pair` — confirmed via its
  uncommitted local diff (grid trimmed from 576 to 288 combos) that this is actively being
  hand-iterated today. `sweep_mediapipe_config.py` similarly already has a 9-combo MediaPipe grid
  (model variant × visibility threshold), scored the same way, but scoped to P14 only.
- Decided during design: build a new shared module and a watcher process on top of this existing,
  already-correct scoring code — wrap and generalize it, never duplicate or rewrite it, and never
  modify the standalone scripts themselves (they stay runnable independently, same pattern as
  `pt_cohort_common.py` being built on `pt_report_common.py` without touching `ms_vs_healthy_analysis.py`).

## 3. Scope

- New: `rmse_pipeline_common.py` (shared discovery/scoring/sweep orchestration), `rmse_watcher.py`
  (the long-running watcher/queue/consumer), `rmse_best_config.json` (tracked best-known config per
  methodology + promotion history), `sweep_cache/` (per-`(trial, config)` RMSE cache).
- Small addition: none to existing scripts — `batch_imu_vs_optitrack_rmse.py`,
  `sweep_imu_config.py`, `sweep_mediapipe_config.py`, `model_vs_optitrack_eval.py` are read and
  wrapped, not modified.
- Out of scope: any change to the AHRS fusion algorithm or MediaPipe fitting logic itself; any
  change to `imu_calibration_config.json` or the live self-supervised tuner
  (`imu_calibration_tuner.py`'s own `score_waveform`-based loop, which stays the production config
  source — this pipeline tracks a separate, OptiTrack-RMSE-validated "best known" config and never
  auto-applies it); auto-promotion of a sweep result into any live config (report-only, human
  approves).

## 4. Architecture Overview

```
Recordings/ , OptiTrack_Recordings/          (watched; Model_Analysis_Outputs/ excluded)
        |  file created/modified
        v
  watchdog Observer (thin adapter) --> watcher.on_file_event(path)
        |
        v
  per-trial-key debounce (8s, clock-injected) --> tick()
        |  expired
        v
  file-stability check (size stable ~1.5s apart, opens in shared-read mode)
        |  stable + complete (source data AND OptiTrack counterpart present)
        |  unstable --> re-arm debounce (bounded ~60s, then defer to reconciliation)
        v
  push token onto shared queue.Queue  <---- reconciliation_pass() (every 600s,
        |                                    cheap stat-only diff against sweep_cache
        v                                    keys, same queue, same token shape)
  single consumer loop (only one; serializes sweeps, no separate lock needed)
        |
        v
  run_full_sweep()  [rmse_pipeline_common.py]
    for every eligible trial (whole dataset, via discovery):
      x MediaPipe grid (sweep_mediapipe_config.WIDE-equivalent grid, generalized off P14)
      x IMU grid (sweep_imu_config.WIDE_GRID, as-is)
    sweep_cache/ keyed on (trial key, file size, mtime) skips recompute for unchanged trials
        |
        v
  compare best-per-methodology candidate against rmse_best_config.json's current best
    beats it (> epsilon) --> append to history, update "current best", log promotion
                              (never writes to imu_calibration_config.json or any live config)
        |
        v
  regenerate Model_Analysis_Outputs/RMSE_Tracking/:
    rmse_sweep_results.csv, rmse_trend.png, sweep_heatmap.png, imu_vs_mediapipe_rmse.png
```

## 5. `rmse_pipeline_common.py` (new shared module)

- **`discover_scorable_trials()`** — every (participant, position, trial) with both source data and
  an OptiTrack counterpart. Reuses `evaluate_all_participants.py`'s generic discovery and
  `batch_imu_vs_optitrack_rmse.py`'s `find_optitrack_match()` path-matching (already handles the
  non-mirrored directory-depth cases confirmed by direct inspection).
- **`score_imu_candidate(trial, params)`** — thin wrapper around `sweep_imu_config.py`'s existing
  `discover_scoreable_trials()` + `imu_calibration_tuner.replay_trial()` +
  `workbench_engine.compare_pair()` pipeline. Grid: `sweep_imu_config.WIDE_GRID` as-is (currently
  288 combos: `beta`×6, `ema_alpha`×4, `flex_axis_capture`×2, `gravity_seed`×2, `method`×3 — this
  file is actively hand-tuned; the shared module always imports the grid live, never copies it).
- **`score_mediapipe_candidate(trial, model_variant, vis_threshold)`** — thin wrapper around
  `sweep_mediapipe_config.py`'s scoring (`batch_mediapipe.py`'s `_select_patient_pose`/
  `MP_LEG_IDX`/`_leg_from_name`, `workbench_engine.compare_pair`), generalized to run over every
  discovered trial rather than only P14's.
- **`run_full_sweep()`** — discover trials -> score every trial x every candidate in both grids
  (reading/writing `sweep_cache/` keyed on `(trial key, file size, mtime)`) -> aggregate median
  RMSE per candidate -> return ranked `{mediapipe: [...], imu: [...]}`.
- **`load_best_config()` / `record_sweep_result()`** — atomic read/write of `rmse_best_config.json`
  (same atomic-write pattern as `imu_calibration_config.py`). A candidate is only promoted to
  "current best" when it beats the recorded best by more than a small epsilon, to avoid promoting
  on noise.

This module has no side effects beyond reading trial data and writing its own output files — it
never touches `imu_calibration_config.json`, `participant_groups.json`, or any file outside
`Model_Analysis_Outputs/RMSE_Tracking/`, `rmse_best_config.json`, and `sweep_cache/`.

## 6. `rmse_watcher.py` (the long-running service)

- **Design for testability:** the `watchdog.Observer`'s event handler is a thin adapter with no
  logic of its own — it only calls `watcher.on_file_event(path)`. All debounce/stability/trigger
  logic lives in plain, directly-callable methods (`on_file_event`, `tick`,
  `reconciliation_pass`, the queue consumer), driven by an injected `clock: Callable[[], float]`
  instead of `time.time()`. Tests call these methods directly with a fake clock and monkeypatched
  `run_full_sweep` — no real sleeps, no real `Observer`, matching this repo's existing
  `monkeypatch`/`tmp_path`/plain-function test convention (no test classes).
- **Debounce:** `on_file_event` resolves the touched path to a trial key (participant/position/
  trial, via the same path-parsing already used by `batch_imu_vs_optitrack_rmse.py` and
  `evaluate_all_participants.py`) and sets/refreshes that key's due-time to `clock() + 8`. This
  collapses a trial's several sibling files (IMU split-CSVs, video, OptiTrack export) landing
  within seconds of each other into one trigger.
- **`tick()`** — called on a short interval; for every key whose due-time has passed: run the
  file-stability check (size stable across two ~1.5s-apart polls, opens in shared-read mode
  without a lock error). If unstable, re-arm the debounce and retry (bounded ~60s of retries, then
  log and defer to reconciliation rather than retry forever). If stable, check
  `discover_scorable_trials()` for completeness (source + OptiTrack both present); if complete,
  push a token onto the shared `queue.Queue`.
- **`reconciliation_pass()`** — every 600s, a cheap stat-only diff of the whole tree against
  `sweep_cache/`'s existing `(trial key, size, mtime)` keys (no separate manifest file — the sweep
  cache doubles as the reconciliation baseline). Anything unprocessed pushes the same token onto
  the same queue. This is the safety net for any `watchdog` OS-level events dropped under Windows
  file-system event coalescing — the event-driven path already handles a same-key edit (re-export,
  patch) via a fresh `modified` event, so reconciliation only needs to catch what the OS-level
  watch genuinely missed.
- **Serialization:** exactly one consumer loop pulls from the queue and calls `run_full_sweep()` —
  serialization is a property of having a single consumer, not a separate lock to reason about
  independently.
- **Failure isolation:** a single trial's scoring failure (corrupt CSV, unreadable frame) is caught
  and logged per-trial; the sweep continues over the remaining trials/candidates, matching
  `run_pt_analysis.py`'s existing pattern of wrapping cohort comparison in try/except so one bad
  input can't take down a whole run.
- **Logging:** `logging.handlers.RotatingFileHandler` to `docs/rmse_pipeline/watcher-runtime.log` —
  handled Python-side rather than relying on Task Scheduler's stdout/stderr capture, which is
  exactly the kind of thing that goes silently missing under a non-interactive session.

## 7. State and Outputs

- `rmse_best_config.json` (repo root) — `{"mediapipe": {config..., rmse, updated_at, n_trials},
  "imu": {config..., rmse, updated_at, n_trials}, "history": [...]}`. Report-only: read by a human
  deciding whether to hand-apply a change to the live MediaPipe fitting defaults or
  `imu_calibration_config.json`; never written to those files automatically.
- `sweep_cache/` — per-`(trial key, file size, mtime, config)` cached RMSE result, so a "full sweep
  on every new trial" (the explicit, deliberate choice for this pipeline) recomputes only the
  genuinely new/changed `(trial, config)` pairs rather than repeating expensive MediaPipe inference
  on unchanged data every time. Acknowledged residual risk: an edit that preserves both file size
  and mtime would be missed by the cache/reconciliation baseline — but such an edit would already
  have fired a live `watchdog` "modified" event at the moment it happened, so this only matters for
  events genuinely dropped at the OS level.
- `Model_Analysis_Outputs/RMSE_Tracking/` (existing output-directory convention) —
  `rmse_sweep_results.csv` (full grid x trial results), `rmse_trend.png` (best RMSE over time),
  `sweep_heatmap.png` (MediaPipe grid heatmap, same style as the existing `rmse_heatmap.png`), and
  `imu_vs_mediapipe_rmse.png` — both methodologies' current-best RMSE computed from the identical,
  same-day trial set on every run, specifically closing the stale-comparison gap found in §2.

## 8. Deployment

- Windows Scheduled Task, action = `.venv\Scripts\python.exe` (absolute path) with
  `rmse_watcher.py`'s absolute path as the argument and the repo root as "Start in" — no shell
  wrapper, no stdin piping (that pattern belongs to the nightly hygiene task's `claude -p` prompt
  feed and doesn't apply to a plain long-running Python process).
- Trigger: "run whether user is logged on or not" (chosen over "at logon" so the watcher survives
  logout/reboot). This requires storing the account password in Task Scheduler's credential
  store — a new kind of secret-at-rest on this machine, accepted as a deliberate tradeoff for
  always-on coverage. **Registering the task requires an interactive password entry by the user**;
  it will not be scripted or have the password passed via a command Claude runs (would land in
  shell history/logs).
- Restart-on-failure: Task Scheduler's native retry setting (restart after N minutes, up to a cap),
  since per-trial failure isolation doesn't cover a genuinely unhandled crash in the watcher's own
  loop.

## 9. Error Handling

- Per-trial scoring failure: caught, logged, sweep continues (§6).
- Unstable/locked file at debounce expiry: re-armed, bounded retry, deferred to reconciliation
  (§6).
- Malformed `rmse_best_config.json` or `sweep_cache/` entries: treated as empty/missing rather than
  raising (same defensive pattern as `pt_cohort_common.load_registry()`), since these are files a
  human could plausibly hand-edit.
- Watcher process crash: caught by Task Scheduler's restart-on-failure (§8); the `sweep_cache/`
  makes a restart cheap — no full-dataset recompute, only whatever wasn't cached yet.

## 10. Testing

- `rmse_pipeline_common.py`: plain-function unit tests for discovery, scoring wrappers (mocked
  `compare_pair`/`replay_trial`), sweep aggregation, cache key computation, and best-config
  promotion (including the epsilon threshold), `tmp_path`/`monkeypatch`, no test classes — matching
  `tests/test_pt_cohort_common.py`'s existing convention.
- `rmse_watcher.py`: clock-injected, queue-decoupled methods tested directly —
  `on_file_event`/`tick` collapsing rapid-fire same-key events into one trigger after the fake
  clock advances past the debounce window; `reconciliation_pass` pushing unprocessed items onto
  the same queue; the single-consumer loop processing exactly one `run_full_sweep()` call even when
  multiple triggers arrive before it finishes (verified via a monkeypatched, artificially slow
  stub). No real filesystem watching or real sleeps in any test.
- Full regression pass: run `tests/test_pt_cohort_common.py` and the rest of the existing suite
  after this work lands, to confirm no cross-module regressions (matching the verification step
  used for the MS-vs-Control cohort work).
