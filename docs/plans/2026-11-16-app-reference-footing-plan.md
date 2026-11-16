# Plan: move the app to the reference footing, count N from the first trough, drop omega_max_n

Date: 2026-11-16. Branch: `feat/app-reference-footing` (cut from `origin/main` at `8aa1055`).
Supersedes: none. Owner decisions taken 2026-11-16: STATUS decision 2 (move the app to the
reference footing), decision 4 (count oscillations from the first trough) and decision 7 (drop
`omega_max_n`). The three land together under one score-formula version bump.

Status of this document: plan only, revised after an independent review (2026-11-16). Nothing
below is implemented. The numbers in section 2 come
from a Python-only feasibility spike (section 2.1); the Rust core has not been run on any of it.

## 1. What changes, in one paragraph

The phone app today replays a trial on a 50 ms grid with EMA 0.3 per tick and no flex-axis
projection, then scores it. The Python reference replays on a 10 ms grid with EMA 0.5 (per 50 ms,
rescaled to the 10 ms step) and projects the swing onto the captured flex axis. After this change
the Rust core does what the reference does, so on the same sample stream the app and the reference
produce the same trace and the same parameters. In the scorer (Python, Rust, C#) a peak found
before the first counted trough is no longer counted as half an oscillation, and `omega_max_n`
leaves the canonical score (it stays computed, stored and selectable in the parameter panel).

## 2. Expected corpus impact (measured in Python, not yet in Rust)

### 2.1 How this was measured

`imu_calibration_tuner.replay_trial` replayed all 179 corpus phone trials (the
`batch_imu_vs_optitrack_rmse.discover_trials()` list, exclusions applied) at:

- **app footing**: tick 0.05 s, `ema_alpha` 0.3, `flex_axis_capture` off (beta 0.041, gravity
  seed on, method relative). The seed-fix Rust confirmation (`docs/seed-fix-rust-confirmation`)
  showed Python at these settings reproduces the Rust core exactly on all 179 trials, so this
  stands in for the app;
- **reference footing**: tick 0.01 s (`ANALYSIS_TICK_S`), `ema_alpha` 0.5, flex capture on.

Each curve was scored with `origin/main`'s scorer (S0), with option B of the oscillation-count
report emulated by a source patch (S9), with `omega_max_n` removed from `_PARAM_KEYS` (S11), and
with both (S911). "Verdict" is `mas_estimate`'s zone, or refused, or unscored; the zone edges are
stale for every formula here and the app does not display zones, so verdict counts are a proxy
for how far the reported score moves, not a clinical finding.

Check that the harness is the one the reports used: app against reference under S0 differs on
**56 of 179** on-screen verdicts and **63** stored, exactly STATUS decision 2's figures.

Spike scripts are in the session scratchpad, not in the repo; they are rewritten as a tracked
corpus script in step 5 of section 7.

### 2.2 Results

Verdicts that differ between the app and the reference, of 179:

| scorer | on screen (`detrend=False`) | stored (`detrend=True`) | PT median app minus reference |
|---|---|---|---|
| main (S0) | 56 | 63 | +0.058 |
| + first trough (S9) | 56 | 62 | +0.057 |
| + drop `omega_max_n` (S11) | 38 | 45 | +0.028 |
| both (S911) | 38 | 43 | +0.028 |
| both, after the footing move | 0 by construction (to be shown in Rust: section 7, step 4) | 0 | 0 |

Dropping `omega_max_n` closes a third of the gap on its own, because `omega_max_n` is the
parameter the tick and EMA move most (median 1.23 lower in the app). The footing move closes the
rest. With the new scorer, the remaining gap splits as tick plus EMA 34 verdicts and flex
projection 21 (they overlap).

What changes for **the app** (app footing with main's scorer, to reference footing with the new
scorer):

| | on screen | stored |
|---|---|---|
| verdicts that change | 59 | 65 |
| PT score median change | -0.071 | -0.074 |
| largest movements | borderline to healthy 37, impaired to borderline 12 | borderline to healthy 43, impaired to borderline 10 |
| refused to scored / scored to refused | 5 / 2 | 7 / 4 |
| unscored to scored | 2 | 1 |

What changes for **the reference** (reference footing, main's scorer to the new scorer):

| | on screen | stored |
|---|---|---|
| first trough alone: verdicts | 1 | 2 |
| drop `omega_max_n` alone: verdicts | 17 | 16 |
| both: verdicts | 17 | 17 |
| both: PT median change, median abs | -0.010, 0.032 | -0.007, 0.032 |
| first trough: N changes | 59 trials (58 by -0.5, 1 up) | 65 (64 by -0.5, 1 up) |

**Discrepancy to resolve before implementing (9):** the oscillation-count report found option B
changes N on 43 trials; this spike finds 59 on screen. The report ran at `e9a3a01`, before the
seed fix (#98) moved every trace, so the seed fix is the likely cause, but that is not shown. Step 2
of section 7 explains every one of the 59 from its trace, as the report did for its 40.

At the app's current 20 Hz footing option B would change N on only 12 trials: the shoulder is
mostly a property of the 100 Hz grid, so (9) matters to the app because of (8).

### 2.3 Interaction with the `area_ratio` release sensitivity (not fixed here)

- (9) does not touch `area_ratio`. The integral still starts at the release, so its PT
  contribution still moves by 0.005 or more on about 22 of 162 trials per 10 ms of release shift
  (oscillation-count report, section 3).
- (8) shrinks the app's exposure: the app's release index moves from a 50 ms grid to a 10 ms one.
  Today a one-tick release difference in the app is 50 ms, beyond the 30 ms at which the report
  already saw N change on 92 trials and the PT move by 0.005 or more on 100 (measured at 100 Hz;
  the 20 Hz sensitivity was not measured). After the move the quantisation step is 10 ms.
- With (11), the PT score loses one release-sensitive term (`omega_max_n`'s divisor `A0` is read
  at the release), so per-term weights go from 1/6 to 1/5. `area_ratio`'s share of any
  release-shift PT movement therefore rises by a fifth. Measured after implementation by
  re-running the report's shift sweep (section 7, step 5).

## 3. Exactly what changes

### 3.1 (8) Reference footing in the Rust core

Constants (pinned in tracked code; today the reference's EMA 0.5 lives only in the untracked,
gitignored `imu_calibration_config.json`, and the tracked `DEFAULT_CONFIG` says 0.3):

| constant | app today | after | where |
|---|---|---|---|
| analysis tick | `TICK_S` 0.05 s | `ANALYSIS_TICK_S` 0.01 s | `resample.rs` (new const), used by `replay.rs` |
| EMA alpha | 0.3 per tick, from `capture.js` | 0.5 per 50 ms, rescaled to the tick: `1 - (1 - 0.5)^(0.01/0.05)` = 0.1294 | `resample.rs::rescale_ema_alpha` (port of `_rescale_ema_alpha`), `replay.rs` |
| swing extraction | across-gravity, else total rotation | flex-projected, else across-gravity, else total | `replay.rs::swing_deg` |
| beta | 0.041 | unchanged | `ahrs.rs` |

Rust:

- `resample.rs`: add `ANALYSIS_TICK_S = 0.01` and `rescale_ema_alpha`. `TICK_S` stays for the
  rescale's reference step.
- `replay.rs`: `ReplayConfig` gains `tick_s` and `flex_axis_capture`; a new
  `ReplayConfig::reference()` returns the reference footing and becomes `Default`. `replay`
  feeds main's existing `flex_axis::FlexAxisEstimator` exactly as `replay_trial` does: starting
  with the sample that captures the zero pose, raw gyro, bias-corrected accel as gravity, only
  while not yet committed (the estimator already ignores sub-threshold samples). Every sample is
  projected onto the final axis, as Python projects every tick.
- Flex-feed start under a release override. Rust has `ReplayConfig::release_override` (the
  clinician's scrub; `replay.rs`, the `capture` match in the gyro branch); Python's
  `replay_trial` has no override. Rule chosen: the estimator is fed from the sample that captures
  the zero pose, whichever path captured it (auto-detected or overridden), so an override moves
  the feed start with the zero pose. Without an override this is exactly Python's rule. With one
  there is no Python reference; a Rust test pins the behaviour (override later than the auto
  release: the axis is learnt from the override onward).
- `wasm.rs`: `WasmSession::new(beta, ema_alpha)` keeps its signature (a cached old JS must still
  load a new wasm and the other way round) but builds from `ReplayConfig::reference()`. The
  footing is then fixed inside the wasm. `ALGORITHM_VERSION` is crate version, git revision and
  wasm hash (`webapp/scripts/build-wasm.mjs`, the `algorithmVersion` line); none of the three
  sees a value passed in from `capture.js`, which is where `emaAlpha` lives today, so a JS change
  could alter scores without changing `ALGORITHM_VERSION`. Moving the footing into the core
  closes that.
- `wasm.rs` exports: today only `healthy_reference`, `pt_score_from_params` and
  `pt_score_from_params_selected` are exported free functions. Add `score_formula_id()` and
  `scoring_footing()` so the webapp reads both from the wasm that scored the trial, not from a
  JS copy. `pt_score_from_params` and `_selected` keep their `omega_max_n` argument (wire
  compatibility, and the panel can still select it); the canonical score just stops reading it.
- `session.rs`: no scoring change; `finish_with` calls `replay` as now.
- `Cargo.toml`: crate version 0.2.0 to **0.3.0**.

**Why reimplement rather than port `f429356`.** Read against main, `f429356` is the wrong base:

- It adds its own `FlexAxisEstimator` (260 lines). Main already has one (`7ef108b`), pinned
  against Python by `tests/flex_axis_test.rs` and used for `lateral_motion`. Two estimators in
  one crate is the drift this repo keeps paying for.
- It snapshots gravity at release from the single latest accelerometer sample. Main already
  takes it from the hold buffer's mean (`gravity_dir_from_hold`), which is what
  `_gravity_snapshot` does; `f429356` would regress that.
- Its gyro-bias SNR gate is already on main.
- It predates main's `MAX_GYRO_DT_S`, the seed fix and the across-gravity tier, so it does not
  apply; what is left of value is about 30 lines of `swing_deg` and the feeding rule, which are
  easier to write against main and Python side by side than to merge.
- Its "known residual" (7 of 34 horizontal-bench trials diverging 6 to 32 degrees, localised to
  the AHRS) was measured before the AHRS was brought to parity; main's AHRS now reproduces Python
  exactly on 179 of 179 corpus trials. Whether the residual survives on bench recordings is
  checked in section 7, step 4, not assumed.

What is taken from `f429356`: the three-tier order and its doc comment, and the `abs()`
magnitude. **`abs()` stays in every tier; the signed variant (`61d5723`) stays out.**

Webapp:

- `capture.js`: pass `emaAlpha: 0.5` (documentation only once the core ignores it).
- `webapp/scripts/replay-trial.mjs`: same; it drives the shipped wasm, so nothing else changes.
- `session-store.js`: new records carry `score_formula_id` and `scoring_footing`
  (`"ref-100hz-ema0.5-flex"`), both read from the wasm. Absent means a record from before 0.3.0.

neuromech (`neuromech/phone_trial.py`): it passes no `emaAlpha` to anything; its expected app
trace is a Python copy of the app footing, `app_ticks(t_s, angle, tick_s=0.05, alpha=0.3)` (50 ms
hold, EMA 0.3, no rescale, no flex projection), called where the trial is built, with a
`len(tick_t) > 41` gate before scoring. That copy moves to the reference footing: 10 ms ticks,
the rescaled alpha (`_rescale_ema_alpha(0.5, 0.01)`, imported, not copied) and the flex
projection. The simplest faithful form is to build the expected trace by running
`imu_calibration_tuner.replay_trial` at the reference footing on the generated samples instead
of a hand copy; decided in the step that does it, with the hand copy kept only if the replay is
too slow for the test suite. The gate is derived from the scorer's own constant, not kept as a
separate number: `MIN_SCORABLE_S` if Q7 is accepted, otherwise the scorer's 40-sample count (the
old "41 ticks at 50 ms", about 2.05 s, is dropped). Then the strict-xfail defect tests in
`neuromech/tests/test_phone_trial.py` (a brisk lift fires the release detector; a steady 9 degree
sag through the hold is never caught) are re-run: each must still xfail for its stated reason,
or, if the footing move changes the outcome, the xfail is removed with the reason in the
commit.
- Trends and trials views: see section 4.
- `webapp/src/build-id.js`: restamped from an LF checkout (CLAUDE.md).

Python:

- `imu_calibration_config.py`: decision for the owner (Q1). The reference footing must be named
  in tracked code. Proposed: add `REFERENCE_REPLAY_CONFIG` (beta 0.041, EMA 0.5, flex on, seed
  on, relative) and use it in the corpus and parity scripts this plan adds. Changing
  `DEFAULT_CONFIG`'s 0.3, which every fresh checkout and `_replay_raw_imu_fallback` use, is the
  alternative; it moves desktop paths outside this plan's scope, so it is not proposed here.
- No change to `replay_trial`: it is the reference.

C#: no replay exists in the designer (it scores given curves), so (8) does not touch C#.

### 3.2 (9) Count N from the first trough

Rule (option B of `docs/oscillation-count-sensitivity`): after the second, swing-centred pass and
before the active-oscillation window bound, drop every peak whose index is below the first trough
that clears the count gate (`phi - phi_centre < -min_amp`). If no trough clears the gate, drop
nothing. Trough detection, the centred frame, `A0` and the symmetry integral stay exactly as
`b44dc7d` left them.

Insertion points, by code (line numbers will move with #94):

- Python `pendulastic_pt_score.compute_pt_params`: about 5 lines after the second-pass block
  (`if len(_ext0) >= 3 and _dt_c > 0:` ... the two `find_peaks` calls on `_phi_sc`) and before
  `window_end_t = _active_oscillation_window_end(...)`.
- Rust `scoring.rs::compute_pt_params`: about 6 lines after the second pass (the `find_peaks`
  calls on `phi_sc` / `neg_sc` inside the `swing_centre` branch) and before
  `let window_end_t = active_oscillation_window_end(...)`.
- C# `PtScore.cs`: about 8 lines after the second pass (`FindPeaks(sc, ...)` /
  `FindPeaks(Negate(sc), ...)` inside the `SwingCentre` branch) and before
  `double window_end_t = ActiveOscillationWindowEnd(...)`.

Consequences that follow from the rule, not separate changes: `f` changes where the dropped peak
changed the extremum spacing (the report: 42 trials); `_active_oscillation_window_end` sees one
peak fewer (two report trials move through it; see section 8).

Open before coding (Q3): the report kept the peak when no trough is ever counted, which leaves
heavily damped rebounds alone. This plan keeps that.

### 3.3 (11) Drop `omega_max_n` from the canonical score

Taken from #76 (`fix/drop-omega-max-n-real-cohort`), re-applied by hand onto main because the
branch conflicts and main has since re-derived `HEALTHY_REF` (#79) and renamed the formula
(`pt6rescaled-ref-2026-10-27`):

- `285c49b`: Python `_PARAM_KEYS` and `_DIAGNOSTIC_KEYS`; Rust `N_PARAMS` 6 to 5,
  `CANONICAL_PT_KEYS`, the `PtScoreBreakdown` struct and the `n_scored` list; webapp
  `param-select.js` `CANONICAL_KEYS` and its caveat text; C# `ScoredKeys`; parity tests,
  including the one that re-adding `omega_max_n` widens the divisor; golden fixtures
  regenerated; the `recalibrate_pt_zones.py` and `healthy_ref_frequency_audit.py` messages.
- `9d6872f`: the legacy `pt7` key docstring.
- `955334a`: the designer's four-knob fallback that reaches the MAS 3 preset on the five-term
  scorer (`Designer.cs`).

When this PR merges, #76 and #83 are closed as superseded (#83 contains #76; its desktop
live-view part is not carried forward).

Not taken: #83's desktop live-view change (`pendulastic_app.py` is to be archived, and the app
under change here is the webapp), the `docs/reports/2026-10-27-where-this-stands.md` rewrite
(reports are history), and the merge and `BUILD_ID` commits. `_SIMPLE_KEYS` keeps
`omega_max_n`, as #76 deliberately did.

One more fix found while reading: `app.js::unmeasuredNotice` divides by 7 ("up to N/7 of the
scale"); the canonical set will have five terms. It should read the divisor from the score's
`n_scored`.

### 3.4 Version bump

- `SCORE_FORMULA_ID` (Python, Rust, C#): `pt6rescaled-ref-2026-10-27` to
  `pt5trough-ref-2026-11-16` (five terms, N from the first trough, same `HEALTHY_REF`). If #94
  lands first with its own id, this takes the next one.
- `PT_ZONES_CALIBRATED_FOR` is untouched; it already differs from the formula id
  (`pt7-2026-08-24`), zones stay off (`ZONE_CLASSIFICATION_CALIBRATED = false`), and
  `test_pt_zone_parity` keeps passing for that reason.
- Crate 0.2.0 to 0.3.0, so `ALGORITHM_VERSION` reads `0.3.0+...`.
- neuromech `provenance.json` changes with the formula id; `python -m neuromech.build` rebuilds
  the 67 scenarios and the provenance check flags old libraries.
- `neuromech/fall_speed_reference.json` is pinned to `pt6rescaled-ref-2026-10-27` and a scorer
  fingerprint (`fall_speed.py`, `_fingerprint` and the `score_formula_id` comparison in the
  check). It is re-pinned in the step that bumps the id (section 7, step 6), with the reason.

### 3.5 Minimum-length gate (40 samples)

The scorer refuses a curve with fewer than 40 finite samples, in all three ports:
Python `compute_pt_params` (`mask.sum() < 40` on `np.isfinite(angle_raw)`), Rust
`scoring.rs::compute_pt_params` (`finite_indices.len() < 40`) and C# `PtScore.cs`
(`finiteIdx.Count < 40`, reason "fewer than 40 finite samples"). At 20 Hz that is 2 s of signal;
at 100 Hz it is 0.4 s, about five times looser, so a short recording the app refuses today would
be scored after the move.

Proposed (pending, Q7): replace the count with a duration, defined identically in all three
ports as a new constant `MIN_SCORABLE_S = 2.0`:

- span = `t[last finite sample] - t[first finite sample]`, over the input as given (before the
  release cut), in seconds;
- refuse when span < `MIN_SCORABLE_S`;
- gaps (NaN runs) inside the span count as span: the gate measures how long the curve covers, not
  how many samples it has. Gap handling stays where it is today (the scorer's own
  finite-filtering and the replay's EMA reset); the gate does not add a second gap rule;
- keep a sample floor as well (at least 2 finite samples, so the span is defined); the existing
  SG-window and `find_peaks` minimums are unchanged.

Exported from each port (Python constant, Rust `pub const`, C# `const`) and pinned by
`tests/test_pt_zone_parity.py` like the other shared constants.

Out of scope: the `score_waveform` quality gate (Rust `scoring.rs::score_waveform`,
`t.len() < 40 || finite count < 40`; Python `imu_calibration_tuner.score_waveform`, the same
test). It is the tuner's config-search heuristic, not part of the app's scoring path, and it keeps
its sample count in this change. Named here so nobody reads it as missed.

Effect beyond the phone: the 120 Hz optical curves currently need 40 samples, 0.33 s; under the
duration rule they need 2.0 s. Section 7, step 5 counts the optical curves that would newly be
refused, and checks the `HEALTHY_REF` derivation population (baseline control optical trials)
for control curves under 2.0 s that would drop out of it. If any control curve drops, Q7 goes
back to the owner with that count before the gate changes.

## 4. Stored records

- **Nothing recomputes stored params.** A stored trial keeps its `params` (always
  `detrend=True` when the wasm had `finish_detrended`), its trajectory, its raw JSONL and its
  `algorithm_version`. This change does not rewrite or migrate any record.
- **The composite is recomputed at read time.** The trends and trials views score stored params
  through `pt_score_from_params` with whatever wasm is loaded. So the day 0.3.0 ships, every
  stored trial's displayed score moves through (11) (five terms instead of six), while its
  params stay on the 0.2.x footing (20 Hz, EMA 0.3, no flex, N with the shoulder). A trend that
  spans the upgrade mixes footings even though both points show a five-term score.
- **Proposed (Q2):** the trends view breaks the series at the footing change: a trial without
  `scoring_footing` (or with `algorithm_version` below 0.3.0) is plotted as "earlier scoring"
  and not pooled into the same median as 0.3.0 trials. Its score is still shown, labelled.
- **Possible later, not in this plan:** re-score an old trial from its stored raw JSONL with the
  current core, shown as a derived value beside the stored one, never written over it. The raw log
  is on every scored record, so this is feasible; it is a feature with its own review.
- Exported bundles already carry `algorithm_version` per trial; they will also carry
  `score_formula_id` and `scoring_footing`.
- Trajectory size: the stored trajectory grows about fivefold (100 Hz instead of 20 Hz), about
  2 000 points for a 20 s trial, smaller than the raw JSONL stored beside it.

## 5. `detrend`: unify the stored and on-screen conventions? Not in this change.

Evidence:

- On the reference footing with the new scorer, on-screen and stored verdicts differ on 7 of 179
  trials (app footing today: 21). PT median absolute difference 0.0023. The move itself shrinks
  the disagreement by two thirds.
- The two conventions differ most in `A0`: stored minus on-screen is a median 2.1 degrees in
  absolute value, but up to 148.5 degrees. Differences of that size are what the lineage
  comparison attributes to the unbounded drift-correction fallback that PR #94 bounds. Whether
  the 7 differing verdicts are those trials was not checked. Unifying before #94 would decide the
  convention on data #94 is about to change.
- Test-retest favours `detrend=False` on main today (PT ICC 0.657 against 0.473,
  `docs/scorer-lineage-comparison`), but the gap is attributed there to the same unbounded
  fallback.
- `HEALTHY_REF` is derived with `detrend=True` on the optical controls. Re-run here with
  `detrend=False` (same 63 baseline control trials, 7 unloadable): `phi_max_ratio` 0.6407 to
  0.6229 (-2.8%), `area_ratio` 0.1766 to 0.1677 (-5.0%), `f` and `N` unchanged, 39 to 37
  interpretable trials. Unifying on `detrend=False` therefore changes what two `HEALTHY_REF`
  entries mean and needs a re-derivation, which is #94's territory.

Decision proposed: keep the two conventions as they are in this change. After #94 merges,
re-measure test-retest under both conventions with the bound in place; if `detrend=False` is
still better, unify on it in its own change together with re-deriving `phi_max_ratio` and
`area_ratio` under `detrend=False`.

## 6. Does this change what `HEALTHY_REF` means?

- **(8): no.** `HEALTHY_REF` and the zone edges are derived from OptiTrack curves at 120 Hz, not
  from phone curves. The move puts the app on the phone footing the reference uses; it does not
  change how any reference value was obtained. Note the phone reference footing and the optical
  derivation footing are not the same thing; the app is compared against optical-derived values
  before and after this change.
- **(9): marginally.** Re-derived on the 39 interpretable optical controls with option B: `f`
  trial median 0.9511 to 0.9501; `N` median 6.0 unchanged (the `N` reference is the literature
  anchor 6.5 anyway); every other entry unchanged. Not re-derived here. If #94 re-derives
  `HEALTHY_REF` before this lands, its `f` is computed under the old count and will be 0.001 high
  for the new formula; the fix is to re-run `derive_healthy_ref.py` once on the final combined
  scorer, after the rebase, and update `f` only if it moves at the fourth decimal.
- **(11): yes for one entry.** `HEALTHY_REF["omega_max_n"]` stays, for the panel, but no longer
  enters the score. The zone edges and the Popovic MAS table are on the score's scale, which
  changes from a six-term to a five-term mean. Both are already marked stale and the zone edges
  are not displayed; the neuromech bands (which use the MAS table) are re-measured in section 7, step 6.

## 7. Implementation order and test strategy

Each step is one commit and each commit is green on its own: Python tests, `cargo test`,
`cargo clippy --all-targets -- -D warnings`, the webapp suite (including `sw-shell.test.js`) and
the neuromech tests. Two rules make that hold:

- **`SCORE_FORMULA_ID` is bumped in step 1**, to the final id `pt5trough-ref-2026-11-16`, in
  Python, Rust and C# together. The id names the end state; the intermediate commits are not
  shipped. Everything pinned to the old id moves in that same commit: neuromech's
  `provenance.json` (rebuilt; `neuromech/tests/test_provenance.py` asserts the built id equals
  `P.SCORE_FORMULA_ID`), `neuromech/fall_speed_reference.json` (id and scorer fingerprint), and
  any neuromech expected value step 1 moves. Steps 2 and 3 re-pin what they move again.
- **`BUILD_ID` is restamped in every commit that touches `mobile-imu-core/src/`** (it hashes the
  Rust source; `webapp/tests/sw-shell.test.js` fails otherwise), from an LF checkout or with the
  value CI's failure message expects (CLAUDE.md). A commit that touches neither Rust nor the shell
  restores `build-id.js` after `npm run build:wasm`.

The three parity checks (`tests/test_pt_zone_parity.py`, `neuromech/designer/parity/run_parity.py`,
`mobile-imu-core/tests/fixtures/gen_fixtures.py` then `cargo test`) run on every step that touches
the scorer (1, 2) and again at the end.

1. **(11) and the id bump** in Python, Rust, C#, the webapp panel and `unmeasuredNotice`; golden
   fixtures regenerated; neuromech library rebuilt and its pins (provenance, fall-speed
   reference, dry-run expectations, designer presets with `955334a`) re-pinned, each moved value
   with its cause in the commit; `BUILD_ID` restamped.
2. **(9)** in all three, inserted where section 3.2 names. New tests: a synthetic swing scored at
   release shifts of -30 to +30 ms in 10 ms steps must give one N (Python, Rust, C#); the
   `b44dc7d` guard (`test_a_limb_that_sags_after_the_swing_still_scores_as_it_swung`) must still
   pass; the two window-anchor side effects from the report are looked at trace by trace; the
   59-against-43 discrepancy (section 2.2) is explained. neuromech re-pinned; `BUILD_ID`
   restamped.
3. **(8)** in Rust: `ANALYSIS_TICK_S`, `rescale_ema_alpha`, `ReplayConfig::reference()`, the flex
   tier and its feed rule (including the release-override rule in 3.1), the wasm exports, crate
   0.3.0; webapp records and the trends break (Q2); `neuromech/phone_trial.py`'s expected trace
   moved to the reference footing and its strict-xfail tests re-checked (section 3.1). New golden
   fixtures for `replay` at the reference footing, generated by running `replay_trial` on
   synthetic raw logs (in-plane swing, swing with a yaw component, a log where the axis never
   commits), compared at 1e-9. **The existing replay fixtures stay on the old footing
   deliberately**, by passing an explicit old-footing config, because they pin the AHRS, the hold
   and the tick stage, which this change does not alter: `tests/replay_test.rs` (the tests that
   use `ReplayConfig::default()`), `gen_fixtures.py`'s replay block, and `pipeline_test.rs` and
   `params_json_test.rs`, which build `ReplayConfig` field by field (they gain the two new fields
   set to the old values). The `Default` change is covered by the new fixtures, not by moving the
   old ones. `BUILD_ID` restamped.
4. **Rust core run on real trials.** A native example (as in the seed-fix Rust confirmation,
   kept out of the repo) feeds each of the 179 corpus streams through `TrialSession` with the
   reference footing; Python `replay_trial` at the reference footing on the same streams.
   Required: trace difference at most 1e-9 degrees on every tick, every parameter within 1e-9,
   verdict identical on 179 of 179, both conventions. Repeat through the wasm with
   `replay-trial.mjs` (app-clock feed). Also run the horizontal-bench single-phone recordings
   `f429356` used, to see whether its residual exists on main's AHRS.
5. **Corpus before and after**, with every change explained:
   - app before (0.2.0, main scorer) against app after: every changed verdict (about 59 on
     screen) and every refused or unscored transition, by trace;
   - reference before against after: every changed verdict (about 17) and every N change (about
     59), by trace;
   - the 40-sample minimum (section 3.5): how many corpus trials and optical curves it admits or
     refuses under the sample count and under the 2.0 s rule (phone at 100 Hz, optical at 120 Hz),
     including any baseline control curve the `HEALTHY_REF` derivation would lose, and the
     decision (Q7);
   - the oscillation-count report's release-shift sweep re-run on the new scorer, for N and for
     `area_ratio`'s share of the PT movement;
   - test-retest ICC(1,1) of PT and N before and after, both conventions.
   The report is written without per-participant grades or groups.
6. **neuromech, end state**: rebuild the 67 scenarios and re-run the dry run on the final scorer.
   The dry-run test is now
   `test_default_session_delivers_every_band_and_the_edge_scenarios_stay_on_their_edge`
   (`neuromech/tests/test_session_dryrun.py`); its edge-scenario pins move under (9) and (11) and
   are re-pinned with the cause of each. Check the A-P2 and A-P3 presets (already unreachable)
   and the MAS 3 preset with `955334a`. Re-pin `neuromech/fall_speed_reference.json` (formula id
   and scorer fingerprint). The fingerprint hashes scorer source, so it changes again at step 2
   and at the section 3.5 gate change, not only at step 1; the neuromech tests check only the
   reference file's key set, so a stale fingerprint would not fail a test. Re-pinning here, on the
   end-state scorer, is what keeps it true.
7. **Webapp**: `npm run build:wasm`, the full webapp test suite, `BUILD_ID` check. Time `finish()`
   for a 60 s trial under node at both footings (each result calls replay five times: `finish`,
   trajectory, score, detrended params and detrended score).
8. **Independent review** by someone who did not write it, then STATUS.md updated (workstreams
   3 and 7, decisions 2, 4 and 7 closed) in the same PR. #100 also edits STATUS.md; whichever
   lands second merges by hand. #76 and #83 are closed as superseded.
9. **Rebase onto #94** (branch `fix/port-drift-bound-and-compare-orientation`) when it merges.
   #94 touches `pendulastic_pt_score.py`, `scoring.rs`, `golden.rs`, `gen_fixtures.py`,
   `webapp/src/build-id.js`, `pt_report_common.py` (which feeds the fall-speed fingerprint) and
   `web/api/services/pt_score_bridge.py`; it does not touch `PtScore.cs`. After the rebase:
   regenerate fixtures, restamp `BUILD_ID`, re-run all three parity checks, step 4, step 5 and
   step 6 (the fall-speed fingerprint will have moved).

## 8. Risks

- **Reference footing defined by an untracked file.** The corpus reports' "reference" depends on a
  gitignored `imu_calibration_config.json` holding EMA 0.5; a fresh checkout replays at 0.3.
  Pinning the footing in tracked code (Q1) is part of this change.
- **Flex tier parity is unproven in Rust.** The estimator is pinned against Python, but the feeding
  rule and the per-sample projection are new; section 7, step 4 is the gate. `f429356`'s bench residual
  may reappear on horizontal-plane recordings.
- **The N change count does not match the report** (59 against 43). Unexplained until section 7, step 2.
- **The neuromech library moves.** Every scenario's expected values, the dry-run band delivery and
  the designer's presets are on the scorer; workstream 1 is the top priority, so its re-pinning is
  part of this PR, not a follow-up.
- **Mixed footings in trends** until Q2 is decided.
- **Conflicts with #94** (`fix/port-drift-bound-and-compare-orientation`) in
  `pendulastic_pt_score.py`, `scoring.rs`, `golden.rs`, `gen_fixtures.py` and `build-id.js`; it
  also moves the fall-speed fingerprint through `pt_report_common.py`. Kept separable: (9) and (11) touch the count and the key lists, #94 the drift
  correction; neither changes the other's lines. Whichever lands second regenerates the fixtures.
- **`lateral_motion` uses a different axis from the angle.** It runs its own estimator over the
  whole log from the first sample; after this change the angle uses the estimator fed from the
  release. The stored capture-quality field would then describe a different axis from the one
  scored. Proposed: leave it in this change (it moves a stored field) and decide separately (Q4).
- **Phone performance.** Five replays per result at five times the tick count. Expected to be
  small (a 60 s trial is about 6 000 samples) but not measured on a phone.
- **Bench and rig comparisons go stale.** STATUS's "the app's score is 0.06 to 0.15 PT7 higher
  than the 100 Hz score" and every app-versus-reference figure describe the old footing.

## 9. Open questions for the owner

- **Q1.** Pin the reference footing as a new tracked `REFERENCE_REPLAY_CONFIG`, or change
  `DEFAULT_CONFIG`'s `ema_alpha` from 0.3 to 0.5 (which also moves the desktop paths)?
- **Q2.** Trends across the upgrade: break the series at 0.3.0 (proposed), or pool with a label,
  or re-score old trials from their raw logs (a later feature)?
- **Q3.** When no trough is ever counted, keep a lone early peak (proposed, as emulated) or drop
  it?
- **Q4.** `lateral_motion`: move it onto the scoring axis now (a stored field changes) or later?
- **Q5.** `detrend`: agree to defer unification until after #94 (section 5)?
- **Q6.** If #94 merges first, may this PR update `HEALTHY_REF["f"]` by 0.001 after re-running
  `derive_healthy_ref.py` on the combined scorer, or should that go back to #94's owner?
- **Q7.** Convert the 40-sample minimum to a 2.0 s span in all three languages (proposed; it also
  raises the optical minimum from 0.33 s to 2.0 s), or keep the
  sample count and accept that the app scores shorter recordings at 100 Hz?

None of Q1 to Q7 has been answered. Every "proposed" choice in this plan is a default pending
the owner's answer; no step that depends on a question is implemented until it is answered.

## Not verified

- Every number in sections 2, 5 and 6 is from a Python spike. The Rust core was not
  built or run for this plan.
- Option B was emulated by patching the Python source in memory; no Rust or C# was written.
- Python at the app's settings is taken as the app on the strength of the seed-fix Rust
  confirmation (179 of 179 at `76dca49`); it was not re-confirmed on `8aa1055`.
- The optical re-derivations used a separate driver reproducing `derive_healthy_ref`'s selection;
  `derive_healthy_ref.py` itself was not run. Its `detrend=True` medians reproduce the shipped
  `HEALTHY_REF` values to four places.
- Zone and MAS-band counts are crossings of edges that are stale for every formula compared.
