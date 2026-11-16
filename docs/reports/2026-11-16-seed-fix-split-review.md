# Independent review: PR #98 (gravity-seed fix) and PR #99 (synthetic phone trials)

Reviewed 2026-11-16 by someone who wrote neither PR. #98 at `8965212`, #99 at
`5bc9332`, base `origin/main` at `a955ecb`. Everything below was re-run in detached
worktrees unless it says otherwise. Neither PR was changed, commented on or merged.

## Verdicts

| PR | Verdict | Blocking? |
|---|---|---|
| #98 `fix/ahrs-gravity-seed` | **Ready for the owner to consider merging**, with one merge-method condition (merge commit, not squash, or restamp after the squash; see R1) | Owner decision 1 still applies: it changes every scored phone trial |
| #99 `feat/synthetic-phone-trials` | **Ready for the owner to consider merging after small fixes** (R2, R3); none affects behaviour | No |

The seed fix is physically correct: derived independently below and confirmed
numerically in Python and Rust. Every claim I re-checked in the #98 body held.

## PR #98: the gravity-seed fix

### 1. Physics, derived independently

Convention, read from `mobile-imu-core/src/ahrs.rs` and `pendulastic_imu_server.py`:
quaternions are `[w, x, y, z]`, and the prediction step is
`q_dot = 0.5 * q ⊗ [0, ω_body]` (I expanded the Hamilton product against the code). So
`q` rotates sensor-frame vectors into the earth frame (`v_e = q v_s q*`, matrix `R(q)`).
The filter's gravity residual is `f = [2(q2q4 − q1q3), 2(q1q2 + q3q4), 1 − 2(q2² + q3²)] − â`.
That vector is the third row of `R(q)`, i.e. `R(q)ᵀ ẑ`: the earth "up" axis seen from the
sensor. A seed is at rest exactly when `R(q)ᵀ ẑ = ĝ`, i.e. `R(q) ĝ = ẑ`: the rotation that
takes the measured gravity direction **onto** the reference axis.

The shortest arc from unit `a` to unit `b` is `[1 + a·b, a × b]`, normalised. With
`a = ĝ`, `b = ẑ`: `ĝ × ẑ = (gy, −gx, 0)`, so the seed is `[1 + gz, gy, −gx, 0]`. That is
#98's seed. The old seed `[1 + gz, −gy, gx, 0]` is the arc from `ẑ` to `ĝ`, its
conjugate, so it predicts gravity rotated by twice the tilt about the same axis. That
explains "twice the tilt, folded at 180 deg".

Numeric check (independent of the PR's own test: scipy `Rotation` builds the true
sensor-to-earth pose and predicts the reading; the PR's formula is not used):

| tilt (roll, pitch deg) | 0,0 | 10,0 | 0,10 | 45,30 | 100,0 | 0,100 | 100,40 | 135,−60 | 170,20 |
|---|---|---|---|---|---|---|---|---|---|
| seed error, #98 (deg) | 0 | 0 | 0 | 0 | 0 | 0 | 1e-6 | 0 | 1e-6 |
| seed error, main (deg) | 0 | 20.0 | 20.0 | 104.5 | 160.0 | 160.0 | 164.7 | 138.6 | 44.5 |
| still 5 s at 60 Hz, rotation, #98 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| still 5 s at 60 Hz, rotation, main | 0 | 20.0 | 20.0 | 16.4 | 4.4 | 4.4 | 3.4 | 8.8 | 21.8 |

Main's error is 2 × tilt, folded at 180 deg (tilt 52.2 gives 104.5; tilt 100 gives 160).

End-to-end, I rotated a seeded sensor about a body axis at (0, 0.2, 0.1) rad/s, below the
correction gate, and fed it the true gyro and the true accelerometer reading. With #98 the
filter's gravity error stays at 0.05 to 0.12 deg for start rolls of 0, 30 and 100 deg.
With main it is 0.1 deg flat, 60 deg falling to 19 deg after 10 s at 30 deg roll, and
160 deg falling to 152 deg at 100 deg roll. This checks the seed together with the
`q_dot` convention, not the seed formula alone.

**Python versus Rust:** the nine seeds above, computed by `_gravity_seed` and by Rust
`gravity_seed` (a throwaway example binary, since deleted), agree to the last bit (max
difference 0.0) on #98 and on main.

### 2. `golden.rs`

- Committed #98 against committed main: the e2e series move by
  `E2E_OCK_ANG` 1.479 deg, `TRIAL_E2E_ANG` 0.807 deg and `TRIAL_E2E_PRE_RELEASE_DEG`
  5.3e-4 deg; the other `TRIAL_E2E_*` move by at most 1.1e-6. The rest move by at most
  7.3e-12, plus the `GENERATED_WITH` string.
- I regenerated both sides with `gen_fixtures.py` in the project `.venv` (numpy 2.4.6,
  scipy 1.17.1, as in `requirements-lock.txt`). Regenerated main against regenerated
  #98: **only the 20 e2e constants differ**, and every other constant is textually
  identical. Committed main against regenerated main: at most 7.3e-12 (library drift).
- Committed #98 `golden.rs` is **byte-identical** to my regeneration from #98.

The author's account is confirmed.

### 3. Version bump, `SCORE_FORMULA_ID`, caches

- **Nothing compares `ALGORITHM_VERSION`.** In code (not docs) it is read in
  `webapp/src/app.js` (stored on the record and shown in the build-info line),
  `export.js` (written to the manifest), `session-store.js` (stored) and
  `views/session.js` (displayed). The two build scripts only write it. No Python or
  Rust code reads it. Tests check only its format. Records stored before the fix keep
  `0.1.0+...`, are not re-scored, and are not flagged as stale. That matches the PR's
  claim.
- **`SCORE_FORMULA_ID` is rightly unchanged.** The formula did not change. The values
  derived under it (`PT_HEALTHY_MAX`/`PT_BORDERLINE_MAX` via `recalibrate_pt_zones.py`,
  and `HEALTHY_REF` via `derive_healthy_ref.py`) are computed from OptiTrack curves
  (`pts.load_optitrack`), not phone traces, so the seed does not touch them. The C#
  designer has no AHRS, so there is nothing to bring into parity with the seed there.
- **Cache keys:** `rmse_pipeline_common.compute_implementation_fingerprint` hashes only
  modules already in `sys.modules`. I confirmed that `pendulastic_imu_server` is
  loaded when it runs, through `sweep_imu_config` → `imu_calibration_tuner`, so the new
  entry is actually hashed rather than silently skipped. The PR's test only checks
  that the name is in the tuple. `corpus_rest._fingerprint` opens `sys.modules[m]`
  directly, so a missing module would raise rather than be skipped.
- **Other caches:** `neuromech/fall_speed.py` (keyed on `pendulastic_pt_score`,
  `pt_report_common`) and `derive_healthy_ref.py` (keyed on the scorer source) cache
  **OptiTrack** angles only, so they are unaffected. The `extract_landmarks_cached`
  callers are video only. I found no other persisted IMU-replay cache. The service
  worker re-caches because `BUILD_ID` moves.
- `BUILD_ID` `9a32a8122408` and the wasm hash `5875d6a6173c` are what my local
  `npm run build:wasm` produced. Only the revision part differs, as designed.

**R1 (required, merge method).** The `ALGORITHM_VERSION` revision component names
`96dd946` (the bump commit). The four most recent PRs (#66, #90, #95, #96) were
squash-merged. If #98 is squash-merged, `96dd946` will not be in `main`'s history, and
once the branch is deleted, a stored `0.2.0+96dd946168a5...` record will not resolve to a
source revision. That defeats the point of the component. Main's current stamp
(`39c50ac`) is in main's history because those PRs were merge commits. Either merge #98
with a merge commit, or restamp `build-id.js` on main right after the squash.

### 4. Corpus sample, re-run

Trial list: `batch_imu_vs_optitrack_rmse.discover_trials()`, which resolves folders through
`participant_paths` (179 trials, as reported). Sample: every 18th trial from index 4
(10 trials). Each worktree imported only its own modules (asserted).

**Python reference** (corpus config: beta 0.041, EMA 0.5, flex capture on, 100 Hz
analysis tick; `compute_pt_params(detrend=False)`): 9 of 10 score on both sides (one
has no parameters on either side). A0 changes by −12.5, −5.4, +4.9, +2.9, −2.5, −2.5,
−2.3, −1.5 and −0.5 deg: median absolute 2.5 deg, max 12.5. All lie inside the
reported distribution (median 4.37, p90 12.2, max 52.2). All 18 values (before and
after, 9 trials) **equal the author's per-trial outputs exactly**, on today's main.
That supports the PR's statement that main has not changed the replay path since the
reports.

**Rust core** (native `replay::replay` + `scoring::compute_pt_params`, app defaults: EMA
0.3, 50 ms ticks, no magnetometer, relative): same 9 trials. A0 (`detrend=false`)
changes by −12.0, −5.9, −4.3, +3.7, +3.0, +1.9, −1.9, −1.5 and −0.1 deg: median absolute
3.0, max 12.0. The sign agrees with Python on 8 of 9. The settings differ, so the
values differ. **Parity:** Python `replay_trial` run at the same app settings agrees
with Rust on all 18 A0 values (both conventions) to 3.1e-13 deg on #98 and 2.0e-13 on
main.

What this sample does not do: re-run the full 179-trial before/after, the verdict
tables, ICC or RMSE. I sampled them and they reproduced. I did not re-derive the
aggregates. The wasm replay of real trials was not repeated. I used native Rust.

### 5. Gates (#98)

| gate | result |
|---|---|
| `cargo test` (1.99.0) | all suites pass (ahrs_test 13, including the new resting-point test) |
| `cargo clippy --all-targets -- -D warnings` (1.99.0) | clean |
| CI Python list + `neuromech/tests` (library not built, as CI) | 641 passed, 87 skipped, 3 xfailed |
| `node neuromech/labview/verify_formula_node.js` | PASS |
| `tests/test_imu_server.py tests/test_rmse_pipeline_common.py` | 180 passed |
| `npm run build:wasm && npm test` | 426 of 426 |
| line endings | `diff --stat` equals `--ignore-cr-at-eol`; changed files are LF |
| CI on the PR | rust, python, webapp green |

## PR #99: synthetic phone trials

### 1. The seed-conditional strict xfail

`test_the_hold_reads_steady_after_a_lift` is xfail-strict only while the regex
`-gy\s*/\s*denom\s*,\s*gx\s*/\s*denom` matches `ahrs.rs`. I ran each case:

| source | wasm under test | result |
|---|---|---|
| old seed | old seed (main) | xfail (expected) |
| fixed (#98's `ahrs.rs`) | **stale** old-seed wasm | **FAIL**: "a still leg read 175.1..178.5 deg" |
| fixed | rebuilt fixed wasm | pass; whole file 11 passed, 2 xfailed |
| old seed | fixed wasm | **FAIL**: XPASS(strict) |

The regex matches the current line and survives whitespace and line-break changes. It
does not match a rewrite (`* inv`, `-(gy / denom)`), and then the test runs
unconditionally. Every mismatch fails loudly, and no combination passes silently with
the bug present. That is the property that matters. The weakness is diagnosis: with a
stale wasm the failure reads as though the bug were back.

I would not stack #99 on #98: the generator is useful whatever the owner decides about
the seed. **R3 (suggested, not blocking):** key the condition on the wasm actually
replayed, for example replay a still, tilted recording and test whether the angle moves,
or compare the replayed build's `ALGORITHM_VERSION` with the source. Otherwise, add
"rebuild the wasm" to the failure message. This only affects local runs: CI skips every
replay test (no wasm in the python job), so CI cannot see the seed fix land or a strict
xfail flip. Whoever merges #98 second must rebuild locally and run this file.

### 2. Public-repo content

- **CSVs** (`bench_figures/*.csv`, `inputs/ssr_encoder_qa.csv`): motor-bench data only:
  encoder/phone frequency, gain, step and ring metrics, PID settings, dates, LabVIEW file
  names. There are no participant columns, device identifiers or personal paths, and
  the notes columns hold only "no steps found".
- **`usb_export_data.ps1`**: paths come from `$PSScriptRoot` and a `-Drive` argument, with
  no hardcoded user paths. It copies study data to removable media and verifies it. It
  holds no data itself, and its file list names only data files that are already
  gitignored.
- **R2 (required, small):**
  - `bench_figures/bench_figs.py:439,449` hardcodes one participant's ID, leg and
    condition, and builds a figure title from the MS study's folder prefix. This
    breaks the CLAUDE.md rule against spelling participant folder names in code, and it
    ties an ID to a study group in a newly added file. The same pairing already appears
    in comments on main (`batch_imu_vs_optitrack_rmse.py`), so this is not a new
    disclosure, but the new code should take the ID as an argument and resolve any label
    through `participant_paths`, or leave the study prefix out of the title.
  - `bench_figures/make_ssr_tables.py:16-17` defaults to a personal `C:\Users\...\Downloads`
    path, and `bench_figures/README.md:22` has a `cd` to a personal path. Make the two
    arguments required, or default them to a repo-relative path. The PR body discloses
    the first. Main already has one such default (`BASE_DIR` in
    `batch_imu_vs_optitrack_rmse.py`).
- `sys.path`: `bench_figs.py`, `deck_figs.py`, `dynamic_extra.py` and `static_extra.py`
  derive it from `__file__`, with no absolute paths. `bench_figs.py` reads
  `Model_Analysis_Outputs/` CSVs (untracked derived clinical outputs), but only at run
  time, and they are not committed. The `mk_anchor` and `mk_icc` figures hardcode
  aggregate results as literals, which are not identifying.

### 3. `phone_trial.py` correctness

- **Export format:** it matches `export_jsonl.rs` and the wasm entry (`wasm.rs`): `t` in
  seconds, `role` `"distal"`, accel line then gyro line at the same `t`, and
  `phone_ts_ms = round(t·1000)` (the core uses `round(c[0])` of the same millisecond
  value). Gyro is in rad/s, which `capture.js` converts from deg/s. Accel is in m/s².
- **Kinematics:** body frame is x = knee axis, y along the shank, z up at the hold, with
  `ω_x = dθ/dt`. World up seen from the body obeys `u' = −ω × u`, stepped as exact
  rotations, and starts at `[0, sin θ0, cos θ0] = R_x(θ0)ᵀ ẑ` (consistent). Specific
  force is `g·up + α × p + ω × (ω × p)` for a point at a lever arm from a fixed knee,
  which is correct in body coordinates. The mounting rotation is applied to both
  sensors.
- **Truth:** expected parameters come from the true angle run through a re-implementation
  of the app's 50 ms zero-order hold and EMA 0.3. I checked it against
  `resample.rs::tick_resample`/`ema_smooth`: same "last sample strictly before the tick"
  rule and NaN reset. It is then scored by the production scorer with `detrend=False`
  (the on-screen convention). One difference: the truth passes `release_idx` explicitly
  while the app passes `None`. The release tolerance absorbs this.
- **The two documented app defects are real.** `session.rs::advance_hold` resets drift and
  the calm clock when net drift exceeds 5 deg, then becomes Ready again 0.95 s later. A
  steady 9 deg sag over 3 s therefore resets once and is Ready at release, as the xfail
  says. A lift above `FLEX_CAPTURE_THRESHOLD` (1 rad/s) after a calm second fires the
  release detector, which has no direction test. Both xfails reproduce on main's core
  and on #98's core. The second is arguably a detector design limit rather than a
  bug: a direction check could separate them.
- **Not verifiable without a phone:** the accelerometer sign convention. The generator
  assumes `accelerationIncludingGravity` reads +g along the up axis (W3C). Whether iOS
  Safari delivers that sign, and the gyro handedness relative to it, can only be settled
  by a real `devicemotion` capture. Every replay result depends on it.
- `replay-trial.mjs` re-declares the capture config (`beta 0.041`, `emaAlpha 0.3`)
  because `capture.js` keeps it inline. A future change in one place would not reach
  the other. Minor.

### 4. Gates (#99)

| gate | result |
|---|---|
| `python -m neuromech.build` | 67 scenarios |
| `python -m pytest neuromech/tests` (library built, own wasm) | 197 passed, 3 xfailed |
| `test_phone_trial.py`, main's core | 10 passed, 3 xfailed |
| `test_phone_trial.py`, #98's core rebuilt | 11 passed, 2 xfailed |
| `npm run build:wasm && npm test` | 426 of 426; `BUILD_ID` `f2b92312b3b9`, same as main |
| line endings | `diff --stat` equals `--ignore-cr-at-eol`; new files LF; the two `.gitignore` lines are CRLF, matching their CRLF neighbours in a mixed file |
| CI on the PR | rust, python, webapp green |

I did not re-run the CI `tests/` list on #99: it changes none of those modules, and CI is
green.

## STATUS.md and merge order

`git merge-tree --write-tree` is clean for every pair: #98/#99, #98/#97, #99/#97, and
each against main. Two three-way sequences are also clean: (#98+#99) then #97, and
(#97+#98) then #99. The three PRs edit different hunks: #97 rewrites the header,
decisions and workstreams 1, 3 and 9; #99 rewrites workstream 2; #98 rewrites decision 1
and workstream 4.

Suggested order: **#97, then #99, then #98** (when the owner decides).

- #97 is docs only and records merges that have already happened.
- #99 adds no behaviour change and stays correct on either core.
- #98 goes last: its merge is the one that needs follow-up: R1 (merge commit or
  restamp), a local `npm run build:wasm` and run of `test_phone_trial.py` (the strict
  xfail must flip, and CI will not show it), a STATUS edit marking the decision taken,
  and the redeploy.

Any order is mechanically safe. After #98 lands, STATUS.md still calls it "draft"
until someone edits it.

## Not verified

- The full corpus before/after (179 trials), verdict transitions, ICC and RMSE tables:
  sampled on 9 trials (exact match to the author's per-trial values), not re-derived.
- The shipped wasm on real trials (I used native Rust), the app clock feed, and the
  `detrend=true` "stored" convention beyond A0 on the sample.
- Any phone, real `devicemotion` stream, the capture screen, iOS/Android harnesses, or
  the deployed app.
- Whether the new verdicts are clinically right. There is no per-trial ground truth.
- `bench_figures/` scripts and `usb_export_data.ps1` were read, not run.
- The hold-drift and brisk-lift defects were confirmed against the source and by the
  xfails reproducing. Each was observed only through the synthetic generator.
- Two-phone (relative-quaternion) trials: none in the corpus.
