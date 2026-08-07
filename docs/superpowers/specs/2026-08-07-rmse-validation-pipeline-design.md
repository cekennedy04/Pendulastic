# Auto-Triggered RMSE Validation Pipeline — Design Spec

**Revision note (2026-08-07):** this spec went through two rounds of Codex review
(`/codex consult`, session `019fdd23-8d0c-7230-adae-b40c2652e2b6`). Round 1 found two
factual errors and several correctness gaps in the initial draft. Round 2, run after
applying round 1's fixes, caught that two of those fixes were themselves incomplete or
introduced new problems (the `trial_key` design and §7.2's coverage rule) and flagged
gaps round 1's fixes hadn't closed (reconciliation's fingerprint blind spot, the
promotion incumbent-rescoring gap, MediaPipe cache granularity, the stability-check
mechanism, and the condition-variable race). All incorporated below. Superseded points
from earlier revisions (e.g. the size/mtime cache key from the Grok-round revision, and
the source-path-hash `trial_key` from round 1) are called out explicitly where replaced.

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
  (`MODEL_VARIANTS` × `VIS_THRESH_CANDIDATES`), scored the same way, but scoped to P14 only.
- Decided during design: build a new shared module on top of this existing, already-correct
  scoring code, reusing it rather than reimplementing it, and never modifying the standalone
  scripts themselves (they stay runnable independently, same pattern as `pt_cohort_common.py`
  being built on `pt_report_common.py` without touching `ms_vs_healthy_analysis.py`). **Correction
  from the Codex review:** this reuse is not a "thin wrapper" in every case. The IMU side reuses
  `sweep_imu_config.py`'s grid, discovery, and scoring pipeline close to as-is (including its
  dependency on `reconstruct_imu_raw_logs.reconstruct_trial()`, omitted from the original draft).
  The MediaPipe side requires real generalization work — `sweep_mediapipe_config.py`'s discovery
  is hardcoded to Participant 14's exact folder layout, so running it over every participant is a
  genuine refactor of that discovery logic, not a parameter swap. Also, `pt_cohort_common.py` is
  cited only for the module-organization pattern (build new logic on existing primitives without
  touching them) — it is deterministic reporting over already-scored data, with no filesystem
  events, long-running process, expensive inference, or caching, so it is not an architectural
  precedent for §6/§7 below.

## 3. Scope

- New: `rmse_pipeline_common.py` (shared discovery/scoring/sweep orchestration), `rmse_watcher.py`
  (the long-running watcher/scheduler), `rmse_best_config.json` (tracked best-known config per
  methodology + promotion history), `sweep_cache/` (per-trial/candidate RMSE cache, content-hash
  keyed — see §7).
- Small addition: none to existing scripts — `batch_imu_vs_optitrack_rmse.py`,
  `sweep_imu_config.py`, `sweep_mediapipe_config.py`, `model_vs_optitrack_eval.py`,
  `reconstruct_imu_raw_logs.py`, `imu_calibration_tuner.py` are read and reused, not modified.
- Out of scope: any change to the AHRS fusion algorithm or MediaPipe fitting logic itself; any
  change to `imu_calibration_config.json` or the live self-supervised tuner
  (`imu_calibration_tuner.py`'s own `score_waveform`-based loop, which stays the production config
  source — this pipeline tracks a separate, OptiTrack-RMSE-validated "best known" config and never
  auto-applies it); auto-promotion of a sweep result into any live config (report-only, human
  approves); mixed-effects/hierarchical statistical modeling (median-of-medians aggregation is the
  chosen approach here, same as the MS-vs-Control cohort work's pseudoreplication fix).

## 4. Canonical Trial Identity (new — closes the discovery-unification gap)

The three source scripts each discover and key trials differently: `pt_report_common.py` keys
loosely on participant/leg/condition, `batch_imu_vs_optitrack_rmse.py` anchors on
`Trial_{n}_imu.csv`, and `sweep_mediapipe_config.py` anchors on one exact
`Recordings/Participant_14/{Left,Right}/pre/Trial_{n}.avi` layout. A `(participant, position,
trial)` tuple — the original draft's proposed key — is not sufficient to identify a trial: it
omits leg, condition/session, and height, all of which vary independently in the real folder
structure (e.g. `Participant_13_left_post` vs `Participant_13_right_post` are different capture
sessions, not different `position` values of the same trial).

`rmse_pipeline_common.py` introduces one canonical discovery layer that does **not** reuse any
source script's discovery output as the identity model — it builds a `TrialRecord` per discovered
capture:

```python
TrialRecord = {
    "trial_key": "<sha256 of the canonical structural capture tuple>",
    # the fields the key is actually built from — canonicalized (lowercased,
    # normalized) participant/leg/condition/height/trial_number, resolved
    # unambiguously by the discovery layer, NOT by which files happen to exist:
    "participant": "14", "leg": "left", "condition": "pre",
    "session": "pre", "position": "1", "height": "Joint-Level", "trial_number": "3",
    # resolved, repo-root-relative, normalized, case-folded paths (nullable) —
    # mutable inputs, NOT part of trial_key (see below):
    "optitrack_path": "...", "imu_anchor_path": "...", "imu_component_paths": {...},
    "video_path": "...",
    # capability flags — "eligible" is methodology-specific, not a single bool:
    "has_imu_rmse": True, "has_mediapipe_rmse": False,
    "exclusion_reasons": [],
}
```

**`trial_key` fix (second Codex round):** the first revision hashed the resolved *source paths*
(optitrack + imu_anchor + video), which broke identity stability — adding a video later for a
capture that previously had only IMU data, or relocating a file, changes the key for the same
physical capture, silently fragmenting its cache/history into what looks like two different
trials. `trial_key` is instead a hash of the canonical **structural** capture tuple (participant,
leg, condition/session, height, trial_number) resolved by the discovery layer — this is stable
across capability changes. The actual file identities (which files currently exist, their content)
live in `input_fingerprints` (§7.1) and are free to change without changing `trial_key`. If the
structural resolution itself is ambiguous — multiple candidate file sets could satisfy the same
tuple, or `find_optitrack_match()`'s shallow-match fallback can't disambiguate — the record is
marked ambiguous and **excluded**, never heuristically resolved. A silent wrong pairing is worse
than a skipped trial, especially for a project whose whole point is measurement accuracy.

## 5. `rmse_pipeline_common.py` (new shared module)

- **`discover_scorable_trials()`** — builds the `TrialRecord` set from §4, reusing
  `evaluate_all_participants.py`'s generic path discovery and `batch_imu_vs_optitrack_rmse.py`'s
  `find_optitrack_match()` for OptiTrack pairing (explicitly not treated as a generic identity
  system — see §4's ambiguity handling).
- **`score_imu_candidate(trial, params)`** — reuses `sweep_imu_config.py`'s pipeline close to
  as-is: `reconstruct_imu_raw_logs.reconstruct_trial()` to build the raw sample stream,
  `imu_calibration_tuner.replay_trial()` to run the AHRS/fusion candidate, and
  `workbench_engine.compare_pair()` to score against OptiTrack. Grid: `sweep_imu_config.WIDE_GRID`
  as-is (currently 288 combos: `beta`×6, `ema_alpha`×4, `flex_axis_capture`×2, `gravity_seed`×2,
  `method`×3 — this file is actively hand-tuned; the shared module always imports the grid live,
  never copies it — see §7 for how the cache stays correct when the grid changes under it).
- **`score_mediapipe_candidate(trial, model_variant, vis_threshold)`** — generalizes
  `sweep_mediapipe_config.py`'s scoring (`batch_mediapipe.py`'s `_select_patient_pose`/
  `MP_LEG_IDX`/`_leg_from_name`, `workbench_engine.compare_pair`) to run over every discovered
  trial with `has_mediapipe_rmse` rather than only P14's — this is real discovery-logic work, not
  a parameter change (see §2's correction).
- **`run_full_sweep(priority_trial_keys=None)`** — discover trials → for each trial, score every
  candidate its capability flags support, in both grids (reading/writing `sweep_cache/` — see §7
  for the cache-key design) → rank candidates within each methodology's **frozen ranking cohort**
  (see §7.2 — fixed in the second Codex round) → return ranked `{mediapipe: [...], imu: [...]}`
  plus coverage metadata (trial/participant counts actually contributing, not just discovered).
  `priority_trial_keys` is an optional ordering/caching hint only (score these first so a
  triggering trial's own result is available sooner) — it never filters which trials are scored or
  ranked; the no-argument call and the hinted call always produce the same ranking for the same
  underlying data.
- **`load_best_config()` / `record_sweep_result()`** — atomic read/write of `rmse_best_config.json`
  (same atomic-write pattern as `imu_calibration_config.py`). Every sweep **re-scores the currently
  recorded best candidate's exact config against the current frozen cohort**, independent of
  whether that exact config is still present in the live grid (grids are hand-tuned and can change
  — see §5's IMU-grid note) — this is what makes the promotion comparison apples-to-apples; without
  it, a challenger scored on today's cohort could be compared against an incumbent RMSE computed on
  a stale, smaller cohort from whenever it was first promoted. A candidate is only promoted to
  "current best" when it beats the freshly-rescored incumbent by more than a defined epsilon
  (units: RMSE degrees, absolute — not relative — since cross-run relative comparisons are already
  unstable as dataset size changes; exact value TBD at implementation time, default proposal 0.1°)
  **and** meets the minimum-coverage floor in §7.2.

This module has no side effects beyond reading trial data and writing its own output files — it
never touches `imu_calibration_config.json`, `participant_groups.json`, or any file outside
`Model_Analysis_Outputs/RMSE_Tracking/`, `rmse_best_config.json`, and `sweep_cache/`.

## 6. `rmse_watcher.py` (the long-running service)

- **Design for testability:** the `watchdog.Observer`'s event handler is a thin adapter with no
  logic of its own — it only calls `watcher.on_file_event(path)`. All debounce/stability/trigger
  logic lives in plain, directly-callable methods, driven by an injected `clock: Callable[[],
  float]` instead of `time.time()`. **This includes the stability check's polling, not just the
  debounce timer** — the original draft left the stability check's "poll twice ~1.5s apart" as an
  implicit real-time wait, which would still block real wall-clock time in tests despite the
  clock-injected debounce. Both go through the same injected clock/scheduling abstraction so tests
  call these methods directly with a fake clock and monkeypatched `run_full_sweep` — no real
  sleeps, no real `Observer`, matching this repo's existing `monkeypatch`/`tmp_path`/plain-function
  test convention (no test classes).
- **Debounce:** `on_file_event` resolves the touched path to a `trial_key` (via §4's discovery
  layer, not ad hoc path parsing) and sets/refreshes that key's due-time to `clock() + 8`. This
  collapses a trial's several sibling files (IMU split-CSVs, video, OptiTrack export) landing
  within seconds of each other into one trigger.
- **`tick()`** — called on a short interval; for every key whose due-time has passed: run the
  file-stability check. **Concrete mechanism (fixed in the second Codex round — "opens in
  shared-read mode" was not concretely implementable as stated):** after two size-stable polls
  ~1.5s apart (via the injected clock, not a real sleep), attempt an actual parse with the file's
  real reader — `csv.reader` for CSV inputs, an OpenCV `VideoCapture` open-plus-one-frame-read for
  video. A successful parse is the stability signal; a parse exception is treated identically to a
  failed size-stability poll (re-arm and retry), since it directly proves the file wasn't safely
  readable yet rather than inferring that from open-mode semantics. If unstable, re-arm and retry
  (bounded ~60s). **If a trial exceeds the 60s bound repeatedly across multiple reconciliation
  cycles**, log a persistent warning distinct from the routine per-attempt log line, so a genuinely
  stuck/corrupt file is visible rather than silently retried forever — accepted as logging, not
  alerting: this is a single-user local research tool, not a monitored service, so a paging/alert
  channel is out of scope. If stable, mark the key dirty (see the scheduling model below).
- **`reconciliation_pass()`** — every 600s, two checks, not one (the first revision only did the
  first, which a second Codex round caught as insufficient — a changed file keeps the same
  `trial_key`, so diffing trial-key existence alone never notices an edit):
  1. Re-derive `discover_scorable_trials()` for the whole tree and diff against `sweep_cache/`'s
     known trial keys — catches genuinely new/removed trials, same as the first revision.
  2. For every already-known trial, re-check its `input_fingerprints` (§7.1) via the same
     stat-pre-filter/hash-on-change path the cache uses, **and** recompute the current
     `implementation_fingerprint` and compare it against the one recorded on the last completed
     sweep. A changed input fingerprint marks that trial dirty; a changed implementation
     fingerprint (source/grid/model files, which live outside the watched
     `Recordings/`/`OptiTrack_Recordings/` roots and so generate no `watchdog` events at all) marks
     **every** known trial dirty, since a code or grid change invalidates every existing cache
     entry at once.

  This is the safety net for any `watchdog` OS-level events dropped under Windows file-system event
  coalescing, for same-path content edits that don't change `trial_key`, and for the entire class
  of dependency changes the file watcher structurally cannot see (anything outside its watched
  roots). In-memory dirty-set state lost to a crash is recovered here too — a crash between marking
  a trial dirty and committing its cache/output write means it's simply not yet reflected in
  `sweep_cache/`'s fingerprints, so the next reconciliation pass (worst case, 600s later) notices
  the mismatch and re-marks it dirty. No separate persistent dirty-journal is needed as long as
  reconciliation is fingerprint-based rather than existence-based.
- **Coalesced scheduling (revised — replaces the plain `queue.Queue` design).** The original draft's
  FIFO queue did not actually guarantee "one sweep per burst of triggers" — Codex's review caught
  that a plain single-consumer queue runs one sweep *per queued token*, contradicting the test
  claim in §10. Replaced with a dirty-set + request/running flag pattern under one lock:

  ```python
  dirty_trial_keys: set[str]      # accumulated by tick()/reconciliation_pass()
  sweep_requested: bool
  sweep_running: bool

  # on a stable/dirty finding (tick() or reconciliation_pass()):
  with lock:
      dirty_trial_keys.add(trial_key)
      if not sweep_running and not sweep_requested:
          sweep_requested = True
          wake_consumer()

  # consumer loop:
  while running:
      wait_until_requested()
      with lock:
          sweep_requested = False
          sweep_running = True
          dirty_snapshot = dirty_trial_keys.copy()
          dirty_trial_keys.clear()
      try:
          run_full_sweep(dirty_snapshot)   # snapshot is a cache-priority hint, not a filter —
                                            # run_full_sweep() still reaggregates every valid
                                            # cached result, see §5's coverage rule
      finally:
          with lock:
              sweep_running = False
              if dirty_trial_keys:          # more dirt arrived mid-sweep -> exactly one follow-up
                  sweep_requested = True
                  wake_consumer()
  ```

  This gives the property the original test claim wanted: any number of triggers before the
  consumer starts collapses to one sweep, and any number of triggers during a sweep collapses to
  exactly one follow-up sweep — no backlog of redundant full sweeps. `wait_until_requested()` is a
  condition-variable predicate loop (`cv.wait_for(lambda: sweep_requested)`), not a bare sleep/poll
  — a bare wait can miss a wakeup that arrives between checking the flag and re-entering the wait,
  stranding `sweep_requested=True` unconsumed (flagged in the second Codex round).
- **Failure isolation:** a single trial's scoring failure (corrupt CSV, unreadable frame) is caught
  and logged per-trial; the sweep continues over the remaining trials/candidates, matching
  `run_pt_analysis.py`'s existing pattern of wrapping cohort comparison in try/except so one bad
  input can't take down a whole run.
- **Logging:** `logging.handlers.RotatingFileHandler` to `docs/rmse_pipeline/watcher-runtime.log`,
  **gitignored** (same convention as the nightly hygiene job's `docs/hygiene/last-run.log` — add
  the path explicitly to `.gitignore` during implementation) — handled Python-side rather than
  relying on Task Scheduler's stdout/stderr capture, which is exactly the kind of thing that goes
  silently missing under a non-interactive session.

## 7. Caching, Coverage, and Outputs

### 7.1 Cache keys (revised — content-hash, not size/mtime)

The prior revision of this spec (after an earlier Grok-sourced review round) used `(trial key,
file size, mtime, config)` as the cache key, reasoning that full content-hashing was too
expensive. Codex's review correctly identified this as unsound, not just imprecise: RMSE for a
given candidate depends on every split IMU file, the OptiTrack CSV, the video, the MediaPipe model
file, the scoring/alignment code, and the grid definition itself (which — per §5 — is imported
live from a file under active hand-tuning) — none of which are captured by a trial file's size and
mtime. Concretely, fixing a bug in scoring code, or `sweep_imu_config.py`'s grid changing under an
uncommitted edit (already observed happening during this design's own investigation), would leave
stale cached RMSE served indefinitely under the size/mtime scheme.

Replaced with a content-addressed cache key, using stat (size/mtime) only as a cheap pre-filter to
decide whether a file is *worth re-hashing* — not as the correctness gate itself, so the common
case (an untouched multi-GB video across repeated sweeps) still costs one stat call, not one full
re-hash:

```python
cache_key = sha256(canonical_json({
    "schema": 2,
    "methodology": "imu" | "mediapipe",
    "trial_key": trial_key,                    # from §4
    "candidate": canonical_candidate_config,
    "input_fingerprints": {
        "optitrack": sha256_file(opti_path),
        # IMU: every split CSV actually used
        "imu": {"imu": sha256_file(...), "accel": sha256_file(...),
                "gyro": sha256_file(...), "mag": sha256_file(...)},
        # or MediaPipe: the video plus the selected .task model file
        "video": sha256_file(video_path), "model_file": sha256_file(task_path),
    },
    "implementation_fingerprint": sha256_of(
        # canonical serialized grid definition (both grids)
        # + relevant source files: rmse_pipeline_common.py, workbench_engine.py,
        #   imu_calibration_tuner.py, reconstruct_imu_raw_logs.py, the MediaPipe
        #   extraction/scoring code, and anything they import for scoring/alignment
        # + Python version + numpy/scipy/opencv-python/mediapipe package versions
    ),
}))
```

`sha256_file()` itself is gated by the size/mtime pre-filter: unchanged stat → reuse the last
computed file hash from a small stat→hash side-table, no re-read. A file whose stat changed (or
that's never been hashed) gets actually re-hashed. This keeps the common case cheap while making
the cache key genuinely correctness-bearing rather than a heuristic.

**MediaPipe cache granularity (added — second Codex round):** a per-`(trial, full-config)` RMSE
cache entry alone does not prevent repeating pose inference when only `vis_threshold` changes —
inference is by far the expensive part; thresholding already-extracted landmarks is cheap. Split
the MediaPipe cache into two layers: a **landmark-extraction cache** keyed on
`(trial_key, model_variant, video fingerprint, model-file fingerprint, extraction-code
fingerprint)` storing raw per-frame landmarks, and the existing per-candidate RMSE cache keyed as
above, which for MediaPipe candidates reads from the landmark cache rather than re-running
inference when only `vis_threshold` differs.

### 7.2 Ranking cohort and coverage rule (revised — second Codex round fixed a self-contradiction here)

The first revision said "candidates must be ranked on the same scored subset" but then described
each candidate independently aggregating over whatever it happened to score, gated only by an 80%
floor — that still lets two candidates be compared on different subsets. Fixed:

- **One frozen ranking cohort per methodology per sweep.** Before scoring, `run_full_sweep()` fixes
  the cohort as every trial with the relevant capability flag (`has_imu_rmse` or
  `has_mediapipe_rmse`) that isn't excluded (§4). Every candidate in that methodology's grid is
  scored against every trial in that same cohort — not "whatever it happened to succeed on."
- **A candidate that fails to score on a required cohort trial does not get a smaller, easier
  denominator.** It is marked `low_coverage` for that sweep and excluded from ranking and
  promotion eligibility entirely, rather than aggregated over a partial subset. This makes "ranked
  on the same scored subset" literally true, not just aspirational.
- **Minimum-coverage floor still applies to the cohort itself**, not per-candidate: if the frozen
  cohort has fewer than 3 distinct participants, ranking/promotion for that methodology is skipped
  for this sweep entirely (reported, not silently dropped) rather than running a low-confidence
  ranking over a too-small cohort.
- **The IMU-vs-MediaPipe comparison figure uses its own frozen intersection, computed from the
  selected current-best candidates' actual results** — not a vague "same day" or independently-
  computed overlap. Concretely: after each methodology's ranking (above) selects its current-best
  candidate, `imu_vs_mediapipe_rmse.png` is computed over the intersection of the two methodologies'
  frozen cohorts (trials where both `has_imu_rmse` and `has_mediapipe_rmse` are true and neither
  was excluded), scored with each side's selected best candidate and cache/dependency fingerprints
  recorded alongside the figure — not recomputed candidate-by-candidate. The trial/participant
  count of that intersection is labeled directly on the figure, not the full dataset size for
  either side.

### 7.3 Outputs

- `rmse_best_config.json` (repo root) — `{"mediapipe": {config, rmse, updated_at, n_trials,
  n_participants}, "imu": {same}, "history": [...]}`. Each history entry additionally records a
  **dataset fingerprint** (hash of the contributing `trial_key` set), the `implementation_fingerprint`
  from §7.1, and coverage — so history entries are comparable and reproducible even as the dataset
  and grids change over time, not just a bare RMSE number. Report-only: read by a human deciding
  whether to hand-apply a change to the live MediaPipe fitting defaults or
  `imu_calibration_config.json`; never written to those files automatically.
- `sweep_cache/` — content-hash keyed per §7.1. Output files (CSVs/PNGs) are written via
  write-to-temp-then-rename so a crash mid-write never leaves a partially-written output file in
  place — a gap the original draft's atomic-JSON-write coverage didn't extend to.
- `Model_Analysis_Outputs/RMSE_Tracking/` — `rmse_sweep_results.csv` (full grid × trial results,
  including `low_coverage` flags), `rmse_trend.png` (best RMSE over time, annotated with dataset
  size at each point so growth isn't mistaken for a regression), `sweep_heatmap.png` (MediaPipe
  grid heatmap, same style as the existing `rmse_heatmap.png`), and `imu_vs_mediapipe_rmse.png`
  per §7.2's frozen-intersection rule.

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
- `watchdog` is a new runtime dependency — add to the repo's dependency manifest during
  implementation (this repo currently installs via `.venv`, no `requirements.txt` entry exists
  yet for it). MediaPipe/OpenCV are known to behave differently under a non-interactive Scheduled
  Task session (headless/display-context issues) versus an interactive terminal — verify the full
  sweep runs successfully under the actual Scheduled Task context early in implementation, not
  just from an interactive shell, before relying on it.

## 9. Known Limitations (new)

- **`workbench_engine.compare_pair()`'s automatic lag correction** optimizes alignment before
  computing RMSE. This is appropriate if the intended metric is "waveform-shape error after
  optimal alignment," but it is not a raw recording-method timing-accuracy metric — a candidate
  with genuine timing/latency defects can still score well because the lag correction absorbs
  them. This pipeline inherits that property from the existing scoring engine (out of scope to
  change here); anyone reading the RMSE figures for a timing-sensitive claim should know this.
- **Epsilon's exact value** (§5) is left as an implementation-time decision within the stated
  units/direction (absolute RMSE degrees) rather than fixed here.

## 10. Error Handling

- Per-trial scoring failure: caught, logged, sweep continues (§6).
- Unstable/locked file at debounce expiry: re-armed, bounded retry, persistent warning after
  repeated failures rather than silent forever-retry (§6).
- Malformed `rmse_best_config.json` or `sweep_cache/` entries: treated as empty/missing rather than
  raising (same defensive pattern as `pt_cohort_common.load_registry()`), since these are files a
  human could plausibly hand-edit.
- Watcher process crash: caught by Task Scheduler's restart-on-failure (§8); the content-hash
  `sweep_cache/` (§7.1) makes a restart cheap — no full-dataset recompute, only whatever wasn't
  cached yet, and write-to-temp-then-rename output writes mean a crash mid-write never leaves a
  corrupt CSV/PNG in place.

## 11. Testing

- `rmse_pipeline_common.py`: plain-function unit tests for `TrialRecord` discovery/identity —
  including that `trial_key` is stable across a capability change (adding a video to a
  previously-IMU-only structural tuple must NOT change `trial_key`, the specific bug round 2
  caught) and the ambiguous-match-excluded case from §4; scoring wrappers (mocked
  `compare_pair`/`replay_trial`/`reconstruct_trial`); the frozen-ranking-cohort rule (§7.2) —
  including that a candidate failing one required cohort trial is excluded from ranking rather
  than aggregated over a smaller subset; cache key computation (stat-pre-filter/hash-on-change
  behavior, and the separate landmark-extraction cache for MediaPipe); best-config promotion
  (incumbent re-scored against the current cohort before comparison, epsilon, coverage floor).
  `tmp_path`/`monkeypatch`, no test classes — matching `tests/test_pt_cohort_common.py`'s existing
  convention.
- `rmse_watcher.py`: clock-injected methods tested directly — `on_file_event`/`tick` collapsing
  rapid-fire same-key events into one dirty-mark after the fake clock advances past the debounce
  window (stability-check polling, including the parse-based stability signal, also driven by the
  injected clock, not real sleeps); `reconciliation_pass` tested for both its existence-diff path
  and its fingerprint-revalidation path (an unchanged trial-key set with a changed input file, and
  a changed `implementation_fingerprint` marking every known trial dirty); the coalesced-scheduling
  invariant from §6 tested directly via the condition-variable primitive: N dirty-marks before the
  consumer starts produce exactly one `run_full_sweep()` call, and N more dirty-marks arriving
  *during* a (monkeypatched, artificially slow) sweep produce exactly one follow-up call, not N. No
  real filesystem watching or real sleeps in any test.
- Full regression pass: run `tests/test_pt_cohort_common.py` and the rest of the existing suite
  after this work lands, to confirm no cross-module regressions (matching the verification step
  used for the MS-vs-Control cohort work).
