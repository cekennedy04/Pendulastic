# Gravity-seed fix (PR #92): confirmation in the Rust core on corpus trials

Run 2026-11-11. Old seed = `origin/main` at `e9a3a01`. New seed =
`origin/feat/synthetic-trial-generator` at `76dca49`. Between the two, the only
change to `mobile-imu-core/src` is the one line in `ahrs::gravity_seed`
(`[1+gz, -gy, gx, 0]` to `[1+gz, gy, -gx, 0]`). Companion to
`docs/reports/2026-11-10-seed-fix-corpus-check.md` (branch
`docs/seed-fix-corpus-check`), which measured the same change in the Python
reference only. No product code was changed for this report.

## Verdict

**Does the Rust core show the same seed-fix effect as Python? Partly.**

- **Same, measured.** The fix moves every scored trial in Rust, as it does in
  Python: 159 of 159 under the on-screen convention, 160 of 160 under the stored
  one. A0 moves by the same amount and in the same direction: median absolute
  change 4.42 deg (Python 4.37), median change -1.50 deg (Python -1.71),
  per-trial rank correlation with the Python change +0.85, same sign on 126 of
  the 137 trials where both moved by more than 0.5 deg. The settled-tail slope
  improves the same way: median 0.73 to 0.19 deg/s, smaller on 126 of 159
  trials (Python 0.98 to 0.27, 131 of 161).
- **Not the same, measured.** The verdict changes are more numerous and fall on
  different trials. On-screen: 36 of 179 verdicts change in Rust against 24 in
  the Python reference; 13 trials change in both, 8 with the same transition.
  Stored: 39 against 20; 12 in both, 5 with the same transition. Python's "23
  verdicts" is therefore the right order of magnitude for the app but is not
  the list of trials the app will change.
- **Why they differ, measured.** Not the port. Run with the app's settings, the
  Python reference reproduces the Rust core exactly on all 179 trials and both
  seeds, including all 36 and 39 verdict changes. The difference is that the
  app does not run the configuration the Python corpus check ran (see below),
  so its scores sit elsewhere relative to the zone boundaries.

**Is Python/Rust parity on real trials acceptable?** Two different answers.

- *The port:* yes. Same sample stream, same settings: angle traces identical
  (maximum difference 0 deg on 167 of 167 traces), every parameter within
  1.3e-12, verdict identical on 179 of 179, for both seeds and both conventions.
- *The app as configured against the Python reference configuration:* no, and
  this predates PR #92. With the new seed the on-screen verdict differs on 56
  of 179 trials (stored: 63). The app's PT score is a median 0.058 higher and
  its `omega_max_n` a median 1.23 lower. Most of that comes from the 20 Hz tick
  with EMA 0.3 against the reference's 100 Hz tick with EMA 0.5; the missing
  flex-projection tier adds a smaller, separate difference. Numbers below.

**Is any trial made clearly worse in Rust?** No trial's trace is shown to be
wrong, but the fix does not help every trial and these are the ones to look at:

- Three trials lose their on-screen verdict to a refusal:
  `13_right_month_post_T4`, `13_right_week_1_post_T1`, `14_right_pre_T3`
  (stored convention: `21_right_pre_T3`, `21_right_pre_T4`, `21_right_pre_T5`,
  `13_left_week_1_post_T1`, `13_right_month_post_T4`). Thirteen go the other
  way on screen (refused to a zone), eight stored.
- Three trials go from refused to unscored on screen (`14_left_pre_T2`,
  `15_right_post_1week_T3`, `4_right_june_post_T1`). None had a usable verdict
  before.
- The settled tail drifts more with the fix, by over 0.5 deg/s, on 17 trials
  (Python: 12; 10 are the same trials). The largest is `4_right_pre_T2`,
  0.09 to 3.59 deg/s.

There is no per-trial ground truth for the score, so "worse" here means a less
usable result or a less flat resting trace, not a demonstrated error.

## Method

### How corpus trials reached the Rust core

The corpus stores each phone trial as three CSVs written by the older desktop
recorder. Each sample carries two clocks: the arrival time at the laptop
(`timestamp_ms`) and the phone's own sensor time (`phone_ts_ms`).
`reconstruct_imu_raw_logs.reconstruct_trial` merges them into the stream
`imu_calibration_tuner.replay_trial` walks, ordered by arrival time.

That stream is not what the app sees on a phone. Samples reach the laptop in
100 ms batches of ten magnetometer, ten gyro, then ten accelerometer samples
(median arrival gap 0.5 ms, 99th percentile 101 ms; phone clock gap 10 ms).
The app has one clock and gets accelerometer and gyro together. So two feeds
were used, and they answer different questions.

**Feed 1, exact stream (primary; used for parity and for the headline
numbers).** A converter (`py_run.py --dump`) writes each trial's reconstructed
stream as a neutral text dump, one line per sample (`sensor t ts_ms x y z`,
floats at full precision), in the reference's order. A 130-line Rust example
(`corpus_replay.rs`, kept out of the repo and built against each core version)
reads the dump and pushes every line as one `RawSample` into
`session::TrialSession`, the type `WasmSession` wraps, with the app's
configuration (`beta` 0.041, `ema_alpha` 0.3 from `capture.js`; method relative;
no magnetometer). It then calls `finish_with(None, false)` (what `finish` shows
on screen) and `finish_with(None, true)` (what `finish_detrended` stores), and
serialises with the core's own `params_to_json`, `pt_score_to_json` and
`trajectory_to_json`. Native x86-64 build, whole recording.

- What it represents: exactly the samples, order and both clocks the Python
  reference replays, including magnetometer lines (stored, unused) and the
  batched arrival order.
- What it cannot represent: the wasm binary (it is a native build of the same
  source); the app's pairing of one accelerometer sample per gyro sample; the
  app ending a trial at SETTLED; a phone's evenly timed delivery.

**Feed 2, app clock, through the shipped wasm (secondary).** A second converter
(`make_app.py`) writes each trial in the app's export format
(`export_jsonl.rs`): accelerometer and gyro lines only, re-ordered by the phone
clock with accelerometer before gyro on a tie, `t` set to `phone_ts_ms / 1000`.
`webapp/scripts/replay-trial.mjs` replays that through `worker.js` and the wasm,
pairing each gyro line with the accelerometer line before it and ending the
trial when the core reports SETTLED, as the capture screen does. The old-seed
binary was passed with `--wasm`.

- What it represents: the binary that ships, the worker protocol, the app's
  single clock, paired samples, and its termination rule.
- What it cannot represent: magnetometer lines (dropped; the core does not use
  them); gyro lines before the first accelerometer line (dropped: one line on
  110 trials, three to nine on 5 trials, none on 60); a true simultaneous
  accelerometer sample (the paired one is up to 10 ms older); the four trials
  with no accelerometer stream at all, which have no app-format form. The
  motion is the recorded one, so nothing here reproduces a phone's own sensor
  timing or the operator ending a trial early.

To tie feed 2 back to feed 1's build, the native example was also run in a
"paired" mode that mimics `replay-trial.mjs` and `push_batch` line for line.

### Builds

Two throwaway worktrees, one per commit. Native: `cargo build --release
--example corpus_replay`. Wasm: `cargo build --release --target
wasm32-unknown-unknown` then `wasm-bindgen 0.2.127 --target web` (the version
`Cargo.lock` pins), called directly so `webapp/src/build-id.js` was never
rewritten. The seed line was confirmed to differ between the two trees.

### Python side

- *Python reference configuration:* the per-trial outputs of the 2026-11-10
  corpus check were reused as they stand (corpus `imu_calibration_config.json`:
  flex-axis capture on, EMA 0.5, 100 Hz analysis tick; before at `75109c5`,
  after at `76dca49`). `75109c5` and `e9a3a01` do not differ in the Rust core,
  the web app or the Python replay and scoring modules.
- *Python at the app's settings:* re-run here from each worktree's own modules
  on the same trials: `replay_trial` with flex-axis capture off, EMA 0.3, the
  50 ms tick, then `compute_pt_params` under both conventions,
  `compute_pt_score` and `mas_estimate`. A second variant turned flex-axis
  capture on to isolate that tier.

### Conventions and terms

- *On-screen* = `detrend=false` (`TrialSession::finish`). *Stored* =
  `detrend=true` (`finish_detrended`).
- *Verdict* = the zone from `pt_score_to_json` (healthy, borderline, impaired);
  *refused* = the zone is withheld (`uninterpretable` in Rust, not
  interpretable in `mas_estimate`); *unscored* = no parameters returned.
- *Changed* means an absolute difference above 1e-9. Release time is the
  scorer's release (`t_r[0]`), in seconds from the first sample.
- Tail slope: straight-line slope of the trace over the last 2 s of the
  recording; 0 is correct for a settled leg.

Recorded per trial, for both seeds, both feeds and both conventions: release
time, pre-release angle, A0, A1, N, f, R2n, phi_max_ratio, omega_max_n,
area_ratio, neutral, first trough depth, PT score, verdict, and the full angle
trace. The appendix prints the exact-stream values.

## Trial counts

| | count |
|---|---|
| Corpus phone trials (the 2026-11-10 list; one is an unkeyed test recording) | 179 |
| Fed to the Rust core, exact stream | 179 |
| release never detected, both seeds (the same 12 the Python reference returns no trace for) | 12 |
| no accelerometer stream, so never seeded; trace identical for both seeds, unscored | 4 |
| seeded trace on both seeds | 163 |
| scored on screen: old / new / both | 162 / 159 / 159 |
| scored stored: old / new / both | 162 / 161 / 160 |
| Fed to the wasm, app clock (179 less the 4 with no accelerometer stream) | 175 |
| ended at SETTLED / ran to end of file | 64 / 111 |
| scored by the worker: old / new / both | 161 / 159 / 159 |

No trial was excluded from the exact-stream feed. The worker reports a trial as
unscorable whenever the on-screen scoring returns nothing, so it reports no
stored-convention result for a trial the core could score that way.

## Results

### 1. Port fidelity: Rust core against Python at the app's settings

Same stream (feed 1), same settings, per seed.

| | new seed | old seed |
|---|---|---|
| traces on both sides / same tick grid and NaN mask | 167 / 167 | 167 / 167 |
| largest trace difference, any tick, any trial (deg) | 0 | 0 |
| scored by both, on screen / stored | 159 / 161 | 162 / 162 |
| scored by one side only | 0 | 0 |
| largest difference in any angle parameter (deg) | 1.0e-12 | 1.3e-12 |
| largest difference in PT score | 5.3e-14 | 3.0e-14 |
| release time, N, f | identical | identical |
| verdict agreement over 179, on screen / stored | 179 / 179 | 179 / 179 |

The wasm binary agrees with the native build on the same paired input to
within 7.1e-14 deg on every parameter and 2.8e-14 deg on every trace (318
scored results on the new seed, 322 on the old), with identical verdicts.

### 2. The app's configuration against the Python reference configuration

New seed, feed 1. A = Rust core (20 Hz tick, EMA 0.3, no flex projection).
B = Python reference (100 Hz tick, EMA 0.5, flex projection). On-screen
convention, 159 trials scored by both.

| parameter | median A | median B | median A-B | median abs | p90 abs | max abs |
|---|---|---|---|---|---|---|
| release time (s) | 2.65 | 2.59 | +0.03 | 0.04 | 0.09 | 6.40 |
| pre-release angle (deg) | 177.62 | 179.33 | -1.55 | 1.56 | 3.67 | 24.5 |
| A0 (deg) | 65.12 | 62.46 | +0.75 | 3.11 | 11.6 | 32.4 |
| A1 (deg) | 87.89 | 87.51 | -3.20 | 6.70 | 20.4 | 95.1 |
| N | 5 | 5.5 | -0.5 | 0.5 | 1.1 | 4 |
| f (Hz) | 0.935 | 0.945 | -0.006 | 0.009 | 0.077 | 1.89 |
| R2n | 0.848 | 0.879 | -0.035 | 0.051 | 0.115 | 0.90 |
| phi_max_ratio | 0.373 | 0.451 | -0.054 | 0.054 | 0.110 | 0.45 |
| omega_max_n | 3.86 | 4.95 | -1.23 | 1.23 | 2.53 | 4.73 |
| area_ratio | 0.0761 | 0.0637 | +0.013 | 0.017 | 0.112 | 0.90 |
| neutral (deg) | 111.4 | 117.0 | -3.38 | 4.39 | 13.2 | 31.2 |
| PT score | 0.211 | 0.145 | +0.058 | 0.060 | 0.117 | 1.20 |

Verdict agreement over 179: on screen 123 agree, 56 differ; stored 116 agree,
63 differ. On screen, the app reads borderline where the reference reads
healthy on 32 trials, and impaired where it reads borderline on 13. Verdict
counts with the new seed, on screen:

| | healthy | borderline | impaired | refused | unscored |
|---|---|---|---|---|---|
| Rust core, exact stream | 58 | 62 | 28 | 11 | 20 |
| wasm, app clock | 59 | 61 | 28 | 11 | 20 |
| Python reference configuration | 89 | 46 | 18 | 8 | 18 |

Where the difference comes from, measured in Python by changing one thing at a
time (new seed, on screen, 159 trials):

| step | A0 median abs (deg) | neutral median abs (deg) | omega_max_n median change | PT median change | verdicts that differ, of 179 |
|---|---|---|---|---|---|
| add the flex-projection tier (20 Hz, EMA 0.3 both sides) | 3.20 | 4.37 | -0.04 | +0.008 | 26 |
| then 20 Hz and EMA 0.3 to 100 Hz and EMA 0.5 | 0.83 | 0.02 | +1.39 | -0.069 | 64 |

So the flex-projection tier accounts for most of the angle-level gap (A0,
neutral, pre-release angle: the median pre-release angle is 177.6 deg without
the projection and 179.2 deg with it), and the tick and
smoothing account for most of the score-level gap (velocity and ratio
parameters, hence PT score and verdict). The trace with and without flex
projection differs by a median maximum of 7.4 deg per trial (p90 26.9).

The gap is not produced by the batched arrival order: the wasm on the
app-clock feed is as far from the Python reference as the exact-stream run is
(PT score median +0.054 higher, 54 on-screen verdicts differ), and agrees with
the exact-stream Rust run on 171 of 179 on-screen verdicts.

Two recordings (`15_right_post_1week_T3`, `4_right_june_post_T1`) contain a
drop-and-hold before the real swing. The reference at 100 Hz scores the later
swing (release at 5.6 s and 8.0 s, A0 63 and 86 deg); at 20 Hz the same scorer,
in Rust and in Python alike, takes the first movement (release at 2.1 s and
2.25 s) and returns a refusal or nothing.

### 3. Seed effect in the Rust core (old against new)

Feed 1, whole recording. Python reference columns are from the 2026-11-10
check, recomputed here over the same 179 trials.

On-screen convention:

| parameter | Rust n | Rust median change | Rust median abs | Rust p90 abs | Rust max abs | Rust changed | Python median change | Python median abs | Python p90 abs |
|---|---|---|---|---|---|---|---|---|---|
| release time (s) | 159 | 0 | 0 | 0.30 | 3.45 | 60 | 0 | 0.01 | 0.04 |
| pre-release angle (deg) | 159 | -0.44 | 0.66 | 1.71 | 14.4 | 158 | -0.03 | 0.08 | 0.39 |
| A0 (deg) | 159 | -1.50 | 4.42 | 12.0 | 37.9 | 159 | -1.71 | 4.37 | 12.2 |
| A1 (deg) | 159 | -0.05 | 0.15 | 35.3 | 80.5 | 151 | -0.01 | 0.22 | 14.8 |
| N | 159 | 0 | 0 | 1 | 7 | 58 | 0 | 0 | 1 |
| f (Hz) | 159 | 0 | 0 | 0.056 | 1.45 | 69 | +0.003 | 0.004 | 0.078 |
| R2n | 159 | +0.007 | 0.041 | 0.635 | 1.16 | 152 | +0.015 | 0.043 | 0.184 |
| phi_max_ratio | 159 | 0 | 0.030 | 0.133 | 0.49 | 141 | -0.006 | 0.021 | 0.121 |
| omega_max_n | 159 | +0.08 | 0.22 | 0.74 | 6.39 | 159 | +0.13 | 0.27 | 1.09 |
| area_ratio | 159 | 0.000 | 0.017 | 0.212 | 0.89 | 159 | -0.002 | 0.005 | 0.185 |
| neutral (deg) | 159 | +1.21 | 3.67 | 11.7 | 36.9 | 159 | +2.03 | 4.20 | 11.7 |
| PT score | 159 | -0.004 | 0.011 | 0.249 | 1.00 | 159 | -0.005 | 0.009 | 0.186 |

Counts above a practical size:

| | Rust on screen | Python on screen | Rust stored | Python stored |
|---|---|---|---|---|
| trials scored on both seeds | 159 | 161 | 160 | 161 |
| any scored parameter or PT score changed | 159 | 161 | 160 | 161 |
| A0 by more than 1 / 5 / 10 deg | 137 / 65 / 25 | 135 / 69 / 28 | 141 / 77 / 42 | 144 / 72 / 30 |
| A0 down / up | 97 / 62 | 107 / 54 | 59 / 101 | 64 / 97 |
| A0 median change, median abs (deg) | -1.50, 4.42 | -1.71, 4.37 | +2.09, 4.78 | +1.68, 4.52 |
| PT score by more than 0.01 / 0.05 / 0.1 | 85 / 41 / 33 | 78 / 31 / 23 | 104 / 35 / 27 | 86 / 30 / 19 |
| PT score down / up | 103 / 56 | 110 / 51 | 70 / 89 | 66 / 95 |
| PT score median change, median abs | -0.004, 0.011 | -0.005, 0.009 | +0.002, 0.015 | +0.002, 0.011 |
| release time by more than 0.5 s | 14 | 8 | 14 | 8 |
| N by more than 1 | 8 | 8 | 11 | 10 |
| verdict changes over 179 | 36 | 24 | 39 | 20 |
| verdict changes among trials scored on both seeds | 33 | 23 | 36 | 18 |

Per-trial agreement between the Rust change and the Python reference change
(159 trials scored on both seeds in both):

| parameter | on screen: rank correlation | same sign, where both moved | stored: rank correlation | same sign |
|---|---|---|---|---|
| A0 | +0.85 | 126 / 137 | +0.64 | 113 / 143 |
| neutral | +0.87 | 128 / 136 | +0.69 | 123 / 154 |
| omega_max_n | +0.82 | 118 / 129 | +0.59 | 105 / 131 |
| R2n | +0.76 | 110 / 119 | +0.59 | 94 / 117 |
| phi_max_ratio | +0.58 | 84 / 100 | +0.58 | 77 / 90 |
| area_ratio | +0.46 | 58 / 73 | +0.27 | 42 / 63 |
| PT score | +0.46 | 67 / 82 | +0.39 | 69 / 96 |
| N | +0.36 | 23 / 25 | +0.32 | 29 / 38 |
| f | +0.28 | 22 / 27 | +0.29 | 28 / 35 |

The angle-level parameters track Python closely. The count and ratio
parameters, and so the PT score, track it loosely: they turn on which peaks the
scorer accepts, and that is decided on a different tick grid.

Trace level, Rust old against new (absolute difference, deg):

| | n | median | p90 | max | Python median |
|---|---|---|---|---|---|
| pre-release, max | 162 | 2.17 | 5.66 | 9.5 | 1.47 |
| first 1 s of swing, max | 162 | 0.08 | 0.30 | 4.9 | 0.02 |
| swing, max | 162 | 4.83 | 15.1 | 48.9 | 5.41 |
| swing, RMS | 162 | 2.02 | 6.55 | 21.6 | 2.29 |

As in Python, the two traces are pinned together at release and separate
afterwards; the release transient is nearly unchanged and the tail carries the
difference.

Tail slope, last 2 s (absolute, deg/s):

| | n | median old | median new | new smaller / larger | signed median old to new |
|---|---|---|---|---|---|
| Rust, exact stream | 159 | 0.73 | 0.19 | 126 / 33 | -0.46 to -0.05 |
| Rust, quiet tails only (under 2 deg peak-to-peak once detrended) | 121 | 0.73 | 0.10 | 107 / 14 | |
| Python reference | 161 | 0.98 | 0.27 | 131 / 30 | -0.77 to -0.07 |
| wasm, app clock | 159 | 0.57 | 0.12 | 123 / 36 | -0.27 to -0.05 |

On-screen verdict transitions in Rust, over all 179 (36 changes): refused to
impaired 9, impaired to borderline 8, refused to borderline 3, refused to
healthy 1, borderline to healthy 3, healthy to borderline 3, borderline to
impaired 3, impaired to refused 3, refused to unscored 3. Python reference (24
changes): impaired to borderline 9, refused to impaired 5, refused to
borderline 4, borderline to healthy 3, refused to healthy 1, healthy to
borderline 1, refused to unscored 1.

Verdict totals, Rust on screen, old to new: healthy 57 to 58, borderline 54 to
62, impaired 27 to 28, refused 24 to 11, unscored 17 to 20. Stored: healthy 56
to 52, borderline 61 to 67, impaired 27 to 27, refused 18 to 15, unscored 17 to
18.

Direction of the verdict changes:

| | toward less impaired | toward more impaired | refused or unscored to a zone | zone to refused or unscored |
|---|---|---|---|---|
| Rust, on screen | 11 | 6 | 13 | 3 |
| Python reference, on screen | 12 | 1 | 10 | 0 |
| Rust, stored | 8 | 15 | 8 | 6 |
| Python reference, stored | 2 | 3 | 10 | 3 |

Changes between refused and unscored are not counted in this table (Rust 3 on
screen and 2 stored; Python 1 and 2).

The 36 on-screen and 39 stored changes are listed per trial in the appendix
(any row whose two verdicts differ).

### 4. Seed effect in the shipped wasm on the app-clock feed

175 trials, ended at SETTLED where reached. 159 scored on both seeds.

| | on screen | stored |
|---|---|---|
| any scored parameter or PT score changed | 159 of 159 | 159 of 159 |
| A0 median change, median abs, p90 abs (deg) | -0.72, 2.82, 9.10 | +1.18, 3.18, 17.8 |
| A0 by more than 1 / 5 / 10 deg | 130 / 44 / 13 | 128 / 60 / 34 |
| neutral median abs (deg) | 2.26 | 4.16 |
| PT score median change, median abs | -0.0003, 0.008 | +0.003, 0.013 |
| PT score by more than 0.01 / 0.05 / 0.1 | 69 / 33 / 26 | 86 / 35 / 24 |
| release time by more than 0.5 s | 14 | not reported by the worker |
| verdict changes over 179 | 28 | 34 |

The fix still moves every trial, and the tail still flattens (table above), but
the A0 change is about two thirds the size seen on the exact stream, and the
per-trial change correlates only loosely with the exact-stream Rust change
(rank correlation +0.45 for A0) and with the Python reference change (+0.38).
Of the 28 on-screen verdict changes, 17 are also changes on the exact stream
and 10 are also changes in the Python reference. Over the three runs, 56
distinct trials change on-screen verdict in at least one and 8 change in all
three.

### 5. Trials the fix does not help in Rust

Exact stream. Measured facts only; none of these has a ground-truth score.

Lost an on-screen verdict to a refusal (3):

| trial | old | new | what changed | Python reference |
|---|---|---|---|---|
| `13_right_month_post_T4` | impaired, PT 0.560, A0 63.5 | refused, PT 0.684, A0 58.9, A1 0 | scorer's release moves 3.35 s to 2.00 s; pre-release angle 177.1 to 169.3; trace differs by up to 10.1 deg | refused on both seeds on screen; release moves 3.32 s to 1.65 s; stored verdict borderline to refused |
| `13_right_week_1_post_T1` | impaired, PT 0.628, A0 55.1 | refused, PT 1.525, A1 0 | trace differs by up to 4.4 deg; tail slope -1.25 to -0.04 deg/s; first swing no longer accepted | impaired to borderline |
| `14_right_pre_T3` | impaired, PT 0.542, N 1.5 | refused, PT 1.520, N 1, A1 0 | trace differs by at most 1.6 deg; tail slope 0.24 to -0.03 deg/s; first swing no longer accepted | impaired on both seeds |

On the last two the trace changed little and became flatter at rest; the
verdict turned on whether the scorer accepted a first swing. Python at the
app's settings gives the same three results, so this is the scorer at the app's
tick and smoothing reacting to a small trace change, not a Rust defect.

Stored convention, zone to refused (5): `21_right_pre_T3`, `21_right_pre_T4`,
`21_right_pre_T5`, `13_left_week_1_post_T1` (all impaired to refused),
`13_right_month_post_T4` (borderline to refused). The three `21_right` trials
go refused to impaired on screen with the same fix.

Refused to unscored on screen (3): `14_left_pre_T2` (also unscored after the
fix in Python), and the two drop-and-hold recordings named in section 2, which
read A0 3.6 and 3.8 deg before the fix and nothing after it.

Scorer's release time moved by more than 0.5 s (14): `22_right_pre_T4`,
`23_right_pre_T2`, `13_left_post_T3`, `13_right_month_post_T2`,
`13_right_month_post_T4`, `13_right_week_1_post_T5`, `14_left_pre_T3`,
`14_left_pre_T4`, `17_right_post_T3`, `18_left_pre_T2`, `4_left_pre_T2`,
`5_left_post_1month_T2`, `5_left_post_1month_T3`, `5_left_post_1month_T5`.
Python reference: 8 trials.

Tail slope larger with the fix by more than 0.5 deg/s (17 of 159; old to new,
deg/s): `4_right_pre_T2` 0.09 to 3.59, `5_left_post_1month_T1` 11.33 to 13.99
(recording ends mid-movement), `15_left_post_T4` 1.15 to 3.76,
`18_right_pre_T3` 0.50 to 3.01, `15_right_post_T4` 0.16 to 2.49,
`24_right_pre_T3` 0.08 to 2.07, `17_right_post_1_week_T2` 0.97 to 2.85,
`20_right_pre_T2` 0.42 to 1.90, `23_right_pre_T2` 0.31 to 1.65,
`15_right_post_1week_T2` 0.15 to 1.39, `15_right_post_1week_T4` 0.67 to 1.74,
`18_left_pre_T4` 0.34 to 1.40, `5_right_post_1month_T2` 0.32 to 1.07,
`5_right_post_1month_T1` 0.14 to 0.84, `17_right_post_T4` 0.00 to 0.68,
`23_right_pre_T4` 0.24 to 0.89, `18_right_pre_T5` 1.77 to 2.39. The Python
reference shows the same on 12 trials, 10 of them in this list, with the same
shape: on `4_right_pre_T2`, `15_right_post_T4`, `18_right_pre_T3` and
`24_right_pre_T3` the old trace settles flat and the new one keeps moving over
the last 2 s in both implementations. Across trials the change
in tail slope correlates +0.74 between Rust and Python. Why the fixed filter
drifts on these trials was not investigated.

## What is measurement and what is inference

Measured: everything in the tables; that the Rust core and Python agree to
rounding when given the same settings; that the app's settings and the
reference's give different verdicts on about a third of trials; that the fix
moves every scored trial in Rust; the per-trial lists.

Inferred, not tested: that the larger number of verdict changes in the app
comes from its scores sitting closer to the zone boundaries (supported by the
verdict counts in section 2, not isolated by an experiment); that the weaker
agreement on the app-clock feed comes from re-timing and pairing rather than
from ending at SETTLED (the two were not separated).

## Not verified

- No phone was used. The motion is the corpus recording, captured by the older
  recorder; a phone's own sensor timing, a real `devicemotion` stream and the
  app's capture screen were not exercised.
- The wasm binaries are local builds of the two commits, not the artifact a
  release would deploy, and `npm run build:wasm` itself was not run. Only the
  wasm was swapped for the old seed; the worker and bindings were the new
  tree's.
- The native iOS and Android harnesses were not built or run.
- No ground truth for the score: this report cannot say which of two verdicts
  is right. IMU-against-OptiTrack RMSE was not recomputed for the Rust traces.
- Test-retest ICC was not recomputed for the Rust outputs.
- `cargo test` and the web app's test suite were not run on either tree.
- The Python reference configuration results were reused from the 2026-11-10
  run, not regenerated.
- Hold-state behaviour on the app-clock feed (READY, hold drift, quality
  labels) was recorded but not analysed.
- Why some trials' tails drift more with the fix, and whether the three lost
  on-screen verdicts are right or wrong, were not investigated.
- The zone thresholds and `HEALTHY_REF` were taken as they are. Whether they
  suit the app's tick and smoothing was not examined; section 2 only shows
  that the app and the reference configuration disagree.

## Appendix: per-trial values, Rust core, exact stream

Each cell is old seed / new seed. Columns up to "on-screen verdict" are the
on-screen convention; the last three are the stored convention. A dash means
the scorer returned nothing.

| trial | release s (old/new) | pre-release deg | A0 deg | N | f Hz | PT | on-screen verdict | stored A0 deg | stored PT | stored verdict |
|---|---|---|---|---|---|---|---|---|---|---|
| 19_left_pre_T1 | 3.40 / 3.40 | 179.0 / 178.3 | 68.3 / 63.1 | 3.5 / 3.5 | 1.00 / 1.00 | 0.241 / 0.234 | borderline / borderline | 60.6 / 63.3 | 0.227 / 0.234 | borderline / borderline |
| 19_left_pre_T2 | 3.25 / 3.20 | 178.7 / 178.2 | 60.5 / 62.5 | 4.5 / 4.5 | 0.99 / 0.99 | 0.200 / 0.210 | borderline / borderline | 71.2 / 64.6 | 0.216 / 0.214 | borderline / borderline |
| 19_left_pre_T3 | 3.65 / 3.65 | 178.9 / 177.7 | 71.0 / 67.5 | 5 / 5 | 1.00 / 1.00 | 0.217 / 0.213 | borderline / borderline | 63.2 / 69.6 | 0.205 / 0.216 | borderline / borderline |
| 19_left_pre_T4 | 2.35 / 2.35 | 178.6 / 177.6 | 69.9 / 65.1 | 4.5 / 4.5 | 1.00 / 1.00 | 0.235 / 0.229 | borderline / borderline | 62.1 / 70.8 | 0.223 / 0.237 | borderline / borderline |
| 19_right_pre_T1 | 3.30 / 3.35 | 176.8 / 176.2 | 60.0 / 63.0 | 4.5 / 4.5 | 1.67 / 1.67 | 0.394 / 0.363 | impaired / impaired | 67.5 / 62.7 | 0.349 / 0.404 | borderline / impaired |
| 19_right_pre_T2 | 2.55 / 2.55 | 177.2 / 176.5 | 67.3 / 65.1 | 5 / 5.5 | 1.34 / 1.49 | 0.319 / 0.302 | borderline / borderline | 65.2 / 65.6 | 0.322 / 0.301 | borderline / borderline |
| 19_right_pre_T3 | 2.95 / 2.95 | 177.2 / 177.1 | 57.9 / 61.7 | 5.5 / 4.5 | 1.47 / 1.33 | 0.283 / 0.384 | borderline / impaired | 62.8 / 61.5 | 0.390 / 0.384 | impaired / impaired |
| 19_right_pre_T4 | 3.05 / 3.05 | 179.5 / 178.7 | 70.8 / 64.5 | 3 / 4 | 1.25 / 1.46 | 0.445 / 0.397 | impaired / impaired | 59.4 / 63.9 | 0.382 / 0.398 | impaired / impaired |
| 21_left_pre_T1 | 2.20 / 2.20 | 178.3 / 178.3 | 55.5 / 45.3 | 1 / 1.5 | 0.00 / 0.00 | 1.467 / 0.469 | refused / impaired | 42.4 / 43.1 | 0.479 / 0.634 | impaired / impaired |
| 21_left_pre_T2 | 2.25 / 2.30 | 177.6 / 176.4 | 55.3 / 45.3 | 1 / 1 | 0.00 / 0.00 | 1.490 / 0.652 | refused / impaired | 42.4 / 44.6 | 0.479 / 0.494 | impaired / impaired |
| 21_left_pre_T3 | 2.85 / 2.90 | 178.8 / 177.4 | 61.7 / 49.3 | 0.5 / 1 | 0.00 / 0.00 | 1.506 / 0.504 | refused / impaired | 46.9 / 48.2 | 0.545 / 0.510 | impaired / impaired |
| 21_left_pre_T4 | 2.55 / 2.55 | 178.2 / 178.2 | 46.4 / 46.3 | 1.5 / 1.5 | 0.00 / 0.00 | 0.448 / 0.449 | impaired / impaired | 45.5 / 44.9 | 0.448 / 0.448 | impaired / impaired |
| 21_left_pre_T5 | 2.55 / 2.55 | 177.9 / 177.6 | 52.6 / 46.5 | 1 / 1 | 0.00 / 0.00 | 0.700 / 0.493 | impaired / impaired | 44.9 / 46.8 | 0.497 / 0.494 | impaired / impaired |
| 21_right_pre_T1 | 2.65 / 2.65 | 177.7 / 177.4 | 70.9 / 67.9 | 0 / 0.5 | 0.00 / 0.00 | 1.645 / 1.431 | refused / impaired | 69.2 / 69.2 | 1.652 / 1.612 | refused / refused |
| 21_right_pre_T2 | 2.75 / 2.80 | 178.3 / 176.9 | 42.3 / 28.3 | 0 / 0.5 | 0.00 / 0.00 | 1.523 / 0.608 | refused / refused | 29.7 / 30.6 | 0.555 / 0.556 | refused / impaired |
| 21_right_pre_T3 | 2.40 / 2.40 | 177.9 / 177.3 | 37.1 / 32.5 | 0 / 0.5 | 0.00 / 0.00 | 1.438 / 0.548 | refused / impaired | 31.8 / 36.1 | 0.544 / 1.344 | impaired / refused |
| 21_right_pre_T4 | 3.05 / 3.05 | 178.1 / 177.9 | 65.8 / 62.1 | 0 / 0.5 | 0.00 / 0.00 | 1.652 / 1.310 | refused / impaired | 61.4 / 64.8 | 1.353 / 1.599 | impaired / refused |
| 21_right_pre_T5 | 2.30 / 2.35 | 177.8 / 177.6 | 45.8 / 33.6 | 0 / 0.5 | 0.00 / 0.00 | 1.549 / 0.607 | refused / impaired | 32.8 / 38.3 | 0.564 / 1.441 | impaired / refused |
| 22_right_pre_T1 | 2.35 / 2.35 | 176.8 / 176.9 | 59.3 / 63.0 | 2.5 / 2.5 | 1.25 / 1.25 | 0.413 / 0.395 | impaired / impaired | 67.6 / 60.9 | 0.595 / 0.394 | impaired / impaired |
| 22_right_pre_T2 | 2.15 / 2.35 | 176.3 / 177.7 | 60.7 / 60.8 | 3 / 2.5 | 1.25 / 1.25 | 0.352 / 0.363 | borderline / impaired | 61.6 / 60.2 | 0.353 / 0.362 | impaired / impaired |
| 22_right_pre_T3 | 2.40 / 2.40 | 177.8 / 177.9 | 57.8 / 61.5 | 2.5 / 2.5 | 1.21 / 1.21 | 0.346 / 0.348 | borderline / borderline | 64.5 / 60.8 | 0.352 / 0.347 | borderline / borderline |
| 22_right_pre_T4 | 1.75 / 2.85 | 172.9 / 177.8 | 63.9 / 61.6 | 0 / 2.5 | 0.00 / 1.25 | 0.690 / 0.382 | refused / impaired | 65.3 / 61.8 | 0.692 / 0.383 | refused / impaired |
| 24_left_pre_T1 | 1.95 / 2.00 | 179.2 / 178.4 | 68.6 / 62.8 | 4 / 3.5 | 0.93 / 0.95 | 0.327 / 0.332 | borderline / borderline | 62.0 / 70.6 | 0.329 / 0.523 | borderline / impaired |
| 24_left_pre_T2 | 2.00 / 2.00 | 178.3 / 177.6 | 67.6 / 64.7 | 3 / 4 | 0.79 / 0.95 | 0.545 / 0.325 | impaired / borderline | 59.6 / 64.8 | 0.341 / 0.325 | borderline / borderline |
| 24_left_pre_T3 | 2.10 / 2.15 | 178.6 / 178.0 | 78.6 / 65.1 | 3 / 4 | 0.78 / 0.95 | 0.521 / 0.335 | impaired / borderline | 62.2 / 64.9 | 0.343 / 0.336 | borderline / borderline |
| 24_left_pre_T4 | 2.35 / 2.35 | 178.3 / 177.6 | 62.9 / 59.4 | 2.5 / 3.5 | 0.77 / 0.97 | 0.636 / 0.363 | impaired / impaired | 54.8 / 58.6 | 0.375 / 0.365 | impaired / impaired |
| 24_right_pre_T1 | release never detected | | | | | | unscored / unscored | | | unscored / unscored |
| 24_right_pre_T2 | 2.30 / 2.30 | 177.7 / 176.7 | 71.3 / 66.2 | 5 / 5 | 0.91 / 0.91 | 0.214 / 0.208 | borderline / borderline | 56.0 / 74.7 | 0.201 / 0.220 | borderline / borderline |
| 24_right_pre_T3 | 2.20 / 2.20 | 177.1 / 178.2 | 62.6 / 68.5 | 6 / 5.5 | 0.91 / 0.90 | 0.181 / 0.202 | borderline / borderline | 61.8 / 48.1 | 0.179 / 0.188 | borderline / borderline |
| 24_right_pre_T4 | release never detected | | | | | | unscored / unscored | | | unscored / unscored |
| 23_left_pre_T1 | - / - | - / - | - / - | - / - | - / - | - / - | unscored / unscored | 16.5 / 20.1 | 0.385 / 0.385 | refused / refused |
| 23_left_pre_T2 | 1.70 / 1.70 | 178.3 / 178.1 | 73.7 / 72.0 | 6 / 6 | 0.88 / 0.88 | 0.104 / 0.101 | healthy / healthy | 70.0 / 70.5 | 0.099 / 0.100 | healthy / healthy |
| 23_left_pre_T3 | 2.45 / 2.45 | 178.3 / 177.5 | 68.6 / 68.0 | 5.5 / 6 | 0.88 / 0.89 | 0.114 / 0.099 | healthy / healthy | 68.1 / 68.5 | 0.114 / 0.100 | healthy / healthy |
| 23_left_pre_T4 | 2.10 / 2.10 | 179.5 / 178.6 | 76.4 / 69.2 | 6.5 / 6.5 | 0.88 / 0.88 | 0.108 / 0.093 | healthy / healthy | 80.2 / 91.3 | 0.117 / 0.143 | healthy / healthy |
| 23_right_pre_T1 | 2.35 / 2.40 | 177.7 / 176.4 | 77.4 / 73.8 | 5.5 / 5.5 | 0.87 / 0.87 | 0.168 / 0.155 | healthy / healthy | 90.9 / 94.6 | 0.203 / 0.200 | borderline / borderline |
| 23_right_pre_T2 | 2.50 / 1.95 | 177.1 / 173.6 | 64.9 / 74.2 | 6 / 6.5 | 0.88 / 0.88 | 0.113 / 0.125 | healthy / healthy | 89.4 / 108.6 | 0.168 / 0.207 | healthy / borderline |
| 23_right_pre_T3 | 1.95 / 1.95 | 178.8 / 178.5 | 69.3 / 69.5 | 7 / 7 | 0.87 / 0.87 | 0.082 / 0.082 | healthy / healthy | 80.1 / 86.2 | 0.100 / 0.116 | healthy / healthy |
| 23_right_pre_T4 | 2.55 / 2.55 | 177.9 / 178.1 | 62.5 / 61.6 | 7.5 / 7.5 | 0.87 / 0.87 | 0.073 / 0.073 | healthy / healthy | 64.7 / 65.3 | 0.075 / 0.076 | healthy / healthy |
| 13_left_month_post_T1 | 2.90 / 2.95 | 178.7 / 178.1 | 65.0 / 62.3 | 2.5 / 2.5 | 0.91 / 0.93 | 0.344 / 0.333 | borderline / borderline | 65.6 / 62.0 | 0.345 / 0.333 | borderline / borderline |
| 13_left_month_post_T2 | 2.75 / 2.70 | 178.5 / 178.2 | 67.7 / 61.6 | 4.5 / 4.5 | 0.98 / 0.98 | 0.167 / 0.162 | healthy / healthy | 54.7 / 62.7 | 0.131 / 0.165 | healthy / healthy |
| 13_left_month_post_T3 | 3.40 / 3.35 | 178.3 / 177.9 | 56.7 / 58.7 | 4.5 / 4.5 | 0.98 / 0.99 | 0.146 / 0.160 | healthy / healthy | 54.6 / 58.6 | 0.139 / 0.160 | healthy / healthy |
| 13_left_month_post_T4 | 3.25 / 3.25 | 179.1 / 177.7 | 60.5 / 60.5 | 4.5 / 4.5 | 0.96 / 0.98 | 0.161 / 0.163 | healthy / healthy | 56.5 / 64.3 | 0.150 / 0.172 | healthy / borderline |
| 13_left_post_T1 | 3.60 / 3.65 | 176.3 / 175.2 | 65.7 / 47.4 | 2.5 / 2.5 | 1.14 / 1.18 | 0.359 / 0.275 | impaired / borderline | 64.7 / 45.9 | 0.360 / 0.265 | impaired / borderline |
| 13_left_post_T2 | 2.95 / 2.90 | 177.2 / 176.0 | 64.9 / 52.3 | 2.5 / 2.5 | 1.11 / 1.11 | 0.473 / 0.331 | impaired / borderline | 37.0 / 51.7 | 0.274 / 0.330 | borderline / borderline |
| 13_left_post_T3 | 0.55 / 2.65 | 162.0 / 176.4 | 52.9 / 48.3 | 0 / 3 | 0.00 / 1.16 | 0.672 / 0.262 | refused / borderline | 50.1 / 47.5 | 0.666 / 0.259 | refused / borderline |
| 13_left_post_T4 | 3.45 / 3.50 | 177.6 / 176.1 | 46.6 / 46.9 | 3 / 3 | 1.25 / 1.25 | 0.340 / 0.333 | borderline / borderline | 45.0 / 54.5 | 0.338 / 0.480 | borderline / impaired |
| 13_left_week_1_post_T1 | 2.85 / 2.85 | 176.7 / 176.3 | 76.2 / 64.2 | 0 / 0 | 0.00 / 0.00 | 1.539 / 1.632 | refused / refused | 55.9 / 62.9 | 0.772 / 1.637 | impaired / refused |
| 13_left_week_1_post_T2 | 2.05 / 2.05 | 176.8 / 176.7 | 60.3 / 56.1 | 4.5 / 4.5 | 1.00 / 1.00 | 0.204 / 0.197 | borderline / borderline | 45.5 / 59.5 | 0.155 / 0.204 | healthy / borderline |
| 13_left_week_1_post_T3 | 3.15 / 3.15 | 176.9 / 176.6 | 64.0 / 57.2 | 4 / 4.5 | 1.00 / 1.01 | 0.234 / 0.214 | borderline / borderline | 43.1 / 61.0 | 0.185 / 0.221 | borderline / borderline |
| 13_left_week_1_post_T4 | 2.00 / 2.00 | 176.7 / 177.3 | 60.0 / 56.5 | 4 / 4 | 1.01 / 1.01 | 0.220 / 0.215 | borderline / borderline | 44.5 / 56.2 | 0.185 / 0.214 | borderline / borderline |
| 13_right_month_post_T1 | 0.65 / 0.60 | 160.0 / 155.0 | 63.1 / 63.1 | 0 / 0 | 0.00 / 0.00 | 0.700 / 0.700 | refused / refused | 61.0 / 62.6 | 0.697 / 0.699 | refused / refused |
| 13_right_month_post_T2 | 3.50 / 2.45 | 177.9 / 172.1 | 55.0 / 57.6 | 3 / 3 | 1.16 / 1.16 | 0.304 / 0.315 | borderline / borderline | 53.0 / 57.5 | 0.300 / 0.315 | borderline / borderline |
| 13_right_month_post_T3 | 2.55 / 2.55 | 175.3 / 175.5 | 84.1 / 57.1 | 2.5 / 3 | 1.14 / 1.16 | 0.417 / 0.320 | impaired / borderline | 52.4 / 71.5 | 0.304 / 0.427 | borderline / impaired |
| 13_right_month_post_T4 | 3.35 / 2.00 | 177.1 / 169.3 | 63.5 / 58.9 | 2 / 0 | 1.11 / 0.00 | 0.560 / 0.684 | impaired / refused | 51.1 / 72.2 | 0.334 / 0.704 | borderline / refused |
| 13_right_month_post_T5 | 2.55 / 2.60 | 176.9 / 176.3 | 61.0 / 56.4 | 2.5 / 2.5 | 1.14 / 1.14 | 0.343 / 0.334 | borderline / borderline | 54.9 / 56.2 | 0.332 / 0.334 | borderline / borderline |
| 13_right_post_T1 | 2.40 / 2.45 | 175.3 / 174.7 | 56.2 / 51.0 | 3.5 / 3.5 | 0.98 / 0.98 | 0.233 / 0.218 | borderline / borderline | 53.0 / 51.2 | 0.226 / 0.218 | borderline / borderline |
| 13_right_post_T2 | 1.15 / 0.85 | 168.5 / 169.2 | 36.7 / 41.6 | 0 / 0 | 0.00 / 0.00 | 0.672 / 0.687 | refused / refused | 12.0 / 41.6 | 0.600 / 0.687 | refused / refused |
| 13_right_post_T3 | 3.15 / 3.15 | 177.3 / 177.0 | 53.8 / 52.5 | 4 / 4 | 0.99 / 0.99 | 0.176 / 0.172 | borderline / borderline | 48.1 / 52.6 | 0.143 / 0.173 | healthy / borderline |
| 13_right_post_T4 | 2.55 / 2.50 | 177.0 / 177.4 | 71.8 / 59.8 | 4 / 4 | 0.99 / 1.00 | 0.260 / 0.196 | borderline / borderline | 44.5 / 79.6 | 0.133 / 0.289 | healthy / borderline |
| 13_right_post_T5 | 2.40 / 2.40 | 176.9 / 176.3 | 58.6 / 56.0 | 4 / 4 | 1.00 / 1.00 | 0.210 / 0.205 | borderline / borderline | 52.7 / 56.8 | 0.196 / 0.207 | borderline / borderline |
| 13_right_week_1_post_T1 | 2.45 / 2.25 | 177.1 / 175.9 | 55.1 / 54.1 | 2 / 1 | 1.03 / 0.00 | 0.628 / 1.525 | impaired / refused | 48.9 / 52.9 | 1.469 / 1.457 | refused / refused |
| 13_right_week_1_post_T2 | 3.15 / 3.15 | 179.0 / 178.5 | 95.2 / 89.9 | 0.5 / 0 | 0.00 / 0.00 | 1.588 / 1.620 | refused / refused | 97.4 / 96.3 | 1.586 / 1.646 | refused / refused |
| 13_right_week_1_post_T3 | 3.45 / 3.45 | 178.2 / 178.1 | 51.0 / 50.2 | 3 / 3 | 1.25 / 1.25 | 0.354 / 0.353 | impaired / impaired | 48.1 / 50.7 | 0.350 / 0.354 | borderline / impaired |
| 13_right_week_1_post_T4 | 2.95 / 2.95 | 177.3 / 177.1 | 59.3 / 49.1 | 2 / 2.5 | 1.20 / 1.25 | 0.611 / 0.398 | impaired / impaired | 66.3 / 49.3 | 0.599 / 0.398 | impaired / impaired |
| 13_right_week_1_post_T5 | 1.70 / 3.10 | 175.3 / 177.4 | 57.8 / 51.6 | 0 / 3 | 0.00 / 1.22 | 0.701 / 0.350 | refused / borderline | 45.6 / 48.9 | 0.676 / 0.346 | refused / borderline |
| 14_left_post_T1 | 2.90 / 2.90 | 177.7 / 177.7 | 75.4 / 70.0 | 3 / 3 | 1.00 / 1.00 | 0.311 / 0.309 | borderline / borderline | 67.4 / 68.5 | 0.306 / 0.308 | borderline / borderline |
| 14_left_post_T2 | 2.35 / 2.35 | 177.1 / 176.7 | 71.3 / 66.6 | 4 / 4 | 0.97 / 0.97 | 0.279 / 0.277 | borderline / borderline | 65.2 / 66.6 | 0.274 / 0.277 | borderline / borderline |
| 14_left_post_T3 | 2.40 / 2.40 | 178.8 / 178.1 | 60.9 / 68.1 | 3 / 3 | 0.98 / 1.00 | 0.490 / 0.385 | impaired / impaired | 68.3 / 67.7 | 0.360 / 0.384 | impaired / impaired |
| 14_left_post_T4 | 2.55 / 2.55 | 178.3 / 177.6 | 68.8 / 69.2 | 5 / 5 | 0.96 / 0.96 | 0.243 / 0.243 | borderline / borderline | 70.4 / 72.3 | 0.257 / 0.258 | borderline / borderline |
| 14_left_pre_T1 | 2.35 / 2.65 | 173.1 / 174.5 | 65.7 / 58.7 | 0.5 / 0 | 0.00 / 0.00 | 1.611 / 1.638 | refused / refused | 54.7 / 58.7 | 1.599 / 1.636 | refused / refused |
| 14_left_pre_T2 | 5.15 / - | 163.8 / - | 19.5 / - | 1 / - | 0.00 / - | 0.369 / - | refused / unscored | 59.0 / - | 0.351 / - | borderline / unscored |
| 14_left_pre_T3 | 2.40 / 3.40 | 173.9 / 176.9 | 68.9 / 68.0 | 1.5 / 1 | 0.00 / 0.00 | 0.544 / 1.384 | impaired / impaired | 68.1 / 66.9 | 0.541 / 1.325 | impaired / impaired |
| 14_left_pre_T4 | 2.05 / 3.15 | 173.6 / 175.4 | 70.7 / 63.4 | 0 / 0 | 0.00 / 0.00 | 1.658 / 1.416 | refused / refused | 71.0 / 63.2 | 1.659 / 1.375 | refused / refused |
| 14_right_post_T1 | 2.65 / 2.65 | 175.8 / 174.7 | 56.5 / 60.9 | 2.5 / 2 | 0.98 / 0.97 | 0.485 / 0.598 | impaired / impaired | 57.8 / 60.2 | 0.425 / 0.411 | impaired / impaired |
| 14_right_post_T2 | 2.35 / 2.35 | 177.5 / 176.7 | 64.2 / 59.4 | 4.5 / 5 | 1.01 / 1.02 | 0.281 / 0.267 | borderline / borderline | 55.9 / 59.2 | 0.264 / 0.267 | borderline / borderline |
| 14_right_post_T3 | 3.30 / 3.30 | 177.9 / 177.1 | 56.6 / 60.5 | 5.5 / 5 | 1.01 / 1.00 | 0.224 / 0.238 | borderline / borderline | 60.2 / 60.5 | 0.239 / 0.238 | borderline / borderline |
| 14_right_post_T4 | 2.25 / 2.25 | 178.1 / 177.7 | 64.1 / 67.7 | 5 / 5 | 1.00 / 1.00 | 0.225 / 0.228 | borderline / borderline | 68.4 / 65.1 | 0.230 / 0.225 | borderline / borderline |
| 14_right_pre_T1 | 2.85 / 2.85 | 178.1 / 177.9 | 62.1 / 62.9 | 3.5 / 3.5 | 1.02 / 1.02 | 0.344 / 0.344 | borderline / borderline | 62.2 / 61.1 | 0.344 / 0.343 | borderline / borderline |
| 14_right_pre_T2 | 3.15 / 3.15 | 177.3 / 177.2 | 59.8 / 60.5 | 2.5 / 2.5 | 1.05 / 1.05 | 0.415 / 0.414 | impaired / impaired | 60.8 / 60.2 | 0.414 / 0.414 | impaired / impaired |
| 14_right_pre_T3 | 4.15 / 4.15 | 177.5 / 177.6 | 58.0 / 59.3 | 1.5 / 1 | 0.00 / 0.00 | 0.542 / 1.520 | impaired / refused | 59.5 / 59.1 | 0.540 / 0.505 | impaired / impaired |
| 14_right_pre_T4 | 2.15 / 2.15 | 178.1 / 178.0 | 102.1 / 111.7 | 0.5 / 0.5 | 0.00 / 0.00 | 0.565 / 0.563 | impaired / impaired | 108.0 / 119.7 | 0.563 / 0.561 | impaired / impaired |
| 15_left_post_T1 | 2.45 / 2.45 | 178.0 / 178.2 | 65.9 / 64.7 | 7 / 7 | 0.94 / 0.95 | 0.174 / 0.171 | borderline / borderline | 78.1 / 69.8 | 0.190 / 0.178 | borderline / borderline |
| 15_left_post_T3 | 3.00 / 3.00 | 178.2 / 177.3 | 67.2 / 66.9 | 9 / 9 | 0.92 / 0.93 | 0.148 / 0.144 | healthy / healthy | 68.5 / 71.1 | 0.150 / 0.151 | healthy / healthy |
| 15_left_post_T4 | 3.25 / 3.25 | 177.8 / 177.7 | 81.0 / 72.7 | 9 / 9.5 | 0.92 / 0.92 | 0.167 / 0.158 | healthy / healthy | 83.5 / 78.6 | 0.169 / 0.165 | healthy / healthy |
| 15_left_post_T5 | 3.00 / 3.00 | 177.8 / 177.3 | 62.8 / 65.2 | 10 / 10 | 0.92 / 0.92 | 0.125 / 0.129 | healthy / healthy | 66.6 / 57.0 | 0.133 / 0.110 | healthy / healthy |
| 15_left_post_1month_T1 | release never detected | | | | | | unscored / unscored | | | unscored / unscored |
| 15_left_post_1month_T2 | release never detected | | | | | | unscored / unscored | | | unscored / unscored |
| 15_left_post_1month_T3 | 2.40 / 2.40 | 177.7 / 177.6 | 63.3 / 65.8 | 7 / 7 | 0.96 / 0.96 | 0.151 / 0.154 | healthy / healthy | 66.7 / 66.1 | 0.155 / 0.154 | healthy / healthy |
| 15_left_post_1month_T4 | 2.25 / 2.25 | 179.1 / 178.2 | 84.6 / 74.9 | 6.5 / 6.5 | 0.94 / 0.95 | 0.199 / 0.193 | borderline / borderline | 64.7 / 73.7 | 0.182 / 0.192 | borderline / borderline |
| 15_left_post_1month_T5 | 2.70 / 2.70 | 177.7 / 177.3 | 69.6 / 72.8 | 6.5 / 6.5 | 0.95 / 0.95 | 0.180 / 0.183 | borderline / borderline | 73.6 / 73.0 | 0.198 / 0.183 | borderline / borderline |
| 15_left_post_1week_T1 | 2.65 / 2.70 | 179.2 / 178.7 | 83.8 / 70.3 | 9 / 9 | 0.89 / 0.89 | 0.160 / 0.139 | healthy / healthy | 66.0 / 80.8 | 0.131 / 0.155 | healthy / healthy |
| 15_left_post_1week_T2 | 2.25 / 2.25 | 179.6 / 178.7 | 68.4 / 67.9 | 9 / 9 | 0.89 / 0.89 | 0.151 / 0.150 | healthy / healthy | 69.6 / 77.0 | 0.153 / 0.164 | healthy / healthy |
| 15_left_post_1week_T3 | 2.85 / 2.85 | 178.1 / 177.6 | 71.9 / 68.1 | 8 / 8.5 | 0.89 / 0.89 | 0.191 / 0.187 | borderline / borderline | 63.7 / 67.4 | 0.180 / 0.186 | borderline / borderline |
| 15_left_post_1week_T4 | 3.70 / 3.40 | 179.8 / 178.1 | 73.3 / 67.2 | 9.5 / 10 | 0.88 / 0.89 | 0.179 / 0.174 | borderline / borderline | 49.3 / 68.4 | 0.162 / 0.176 | healthy / borderline |
| 15_right_post_T1 | 3.35 / 3.35 | 177.8 / 178.6 | 70.2 / 68.3 | 7 / 7 | 0.94 / 0.94 | 0.131 / 0.128 | healthy / healthy | 66.8 / 73.7 | 0.125 / 0.138 | healthy / healthy |
| 15_right_post_T2 | 4.55 / 4.60 | 178.4 / 177.8 | 70.2 / 63.0 | 6.5 / 7 | 0.93 / 0.94 | 0.111 / 0.089 | healthy / healthy | 63.7 / 62.8 | 0.095 / 0.088 | healthy / healthy |
| 15_right_post_T3 | 3.00 / 3.00 | 178.1 / 178.1 | 69.0 / 66.2 | 7 / 7 | 0.93 / 0.94 | 0.095 / 0.088 | healthy / healthy | 65.3 / 66.1 | 0.086 / 0.087 | healthy / healthy |
| 15_right_post_T4 | 2.80 / 2.80 | 178.0 / 177.3 | 64.8 / 67.2 | 7 / 7 | 0.93 / 0.93 | 0.086 / 0.091 | healthy / healthy | 70.2 / 76.1 | 0.099 / 0.111 | healthy / healthy |
| 15_right_post_1month_T1 | 3.35 / 3.35 | 177.2 / 177.8 | 25.1 / 60.6 | 5 / 4 | 0.95 / 0.95 | 0.296 / 0.202 | refused / borderline | 63.4 / 60.5 | 0.214 / 0.202 | borderline / borderline |
| 15_right_post_1month_T2 | 2.80 / 2.80 | 173.9 / 174.0 | 63.1 / 70.9 | 4 / 4 | 0.96 / 0.96 | 0.235 / 0.243 | borderline / borderline | 69.1 / 63.4 | 0.242 / 0.234 | borderline / borderline |
| 15_right_post_1month_T3 | 2.45 / 2.00 | 178.1 / 177.2 | 50.7 / 61.5 | 4 / 4 | 0.95 / 0.99 | 0.201 / 0.232 | borderline / borderline | 58.3 / 60.6 | 0.216 / 0.231 | borderline / borderline |
| 15_right_post_1month_T4 | 3.30 / 3.30 | 175.9 / 176.0 | 61.5 / 66.1 | 4.5 / 4.5 | 0.95 / 0.95 | 0.213 / 0.218 | borderline / borderline | 62.4 / 64.8 | 0.214 / 0.217 | borderline / borderline |
| 15_right_post_1month_T5 | 3.50 / 3.50 | 176.5 / 176.0 | 31.5 / 62.7 | 5.5 / 4.5 | 0.95 / 0.95 | 0.259 / 0.214 | borderline / borderline | 71.1 / 62.5 | 0.230 / 0.213 | borderline / borderline |
| 15_right_post_1week_T1 | 3.20 / 3.20 | 177.2 / 178.0 | 62.7 / 65.7 | 3 / 3 | 0.94 / 0.94 | 0.278 / 0.281 | borderline / borderline | 60.5 / 63.0 | 0.275 / 0.278 | borderline / borderline |
| 15_right_post_1week_T2 | 2.85 / 2.80 | 177.8 / 178.6 | 70.1 / 70.5 | 6.5 / 6.5 | 0.88 / 0.88 | 0.147 / 0.155 | healthy / healthy | 83.3 / 67.9 | 0.210 / 0.151 | borderline / healthy |
| 15_right_post_1week_T3 | 2.10 / - | 177.3 / - | 3.6 / - | 0.5 / - | 0.00 / - | 0.385 / - | refused / unscored | - / 19.2 | - / 0.385 | unscored / refused |
| 15_right_post_1week_T4 | 2.75 / 2.75 | 176.8 / 176.6 | 58.2 / 63.5 | 8 / 7.5 | 0.87 / 0.86 | 0.106 / 0.107 | healthy / healthy | 74.1 / 45.8 | 0.133 / 0.101 | healthy / healthy |
| 17_left_post_T1 | 2.55 / 2.55 | 179.3 / 179.1 | 70.1 / 71.1 | 5.5 / 5.5 | 0.93 / 0.93 | 0.155 / 0.155 | healthy / healthy | 71.3 / 69.6 | 0.158 / 0.153 | healthy / healthy |
| 17_left_post_T2 | 2.45 / 2.35 | 179.2 / 178.7 | 59.6 / 67.2 | 6 / 6.5 | 0.93 / 0.94 | 0.103 / 0.120 | healthy / healthy | 59.6 / 63.4 | 0.103 / 0.112 | healthy / healthy |
| 17_left_post_T3 | 2.75 / 2.75 | 179.1 / 178.2 | 57.0 / 63.8 | 6.5 / 6.5 | 0.94 / 0.94 | 0.084 / 0.092 | healthy / healthy | 59.4 / 65.3 | 0.084 / 0.111 | healthy / healthy |
| 17_left_post_T4 | 2.35 / 2.35 | 178.3 / 178.9 | 72.1 / 73.0 | 6 / 6 | 0.92 / 0.92 | 0.132 / 0.134 | healthy / healthy | 76.0 / 72.3 | 0.140 / 0.132 | healthy / healthy |
| 17_left_post_1_month_T1 | 2.60 / 2.60 | 178.8 / 178.1 | 70.7 / 72.6 | 6 / 6 | 0.94 / 0.95 | 0.137 / 0.139 | healthy / healthy | 73.1 / 72.6 | 0.142 / 0.138 | healthy / healthy |
| 17_left_post_1_month_T2 | 5.70 / 5.70 | 177.7 / 177.1 | 65.7 / 65.6 | 6 / 6 | 0.95 / 0.96 | 0.127 / 0.127 | healthy / healthy | 68.0 / 59.9 | 0.132 / 0.104 | healthy / healthy |
| 17_left_post_1_month_T3 | 2.55 / 2.55 | 178.2 / 177.3 | 68.8 / 66.8 | 6 / 6.5 | 0.95 / 0.95 | 0.144 / 0.127 | healthy / healthy | 63.7 / 67.1 | 0.134 / 0.141 | healthy / healthy |
| 17_left_post_1_month_T4 | 2.45 / 2.50 | 178.6 / 177.9 | 65.5 / 63.8 | 6.5 / 6.5 | 0.95 / 0.95 | 0.099 / 0.089 | healthy / healthy | 65.8 / 63.9 | 0.100 / 0.089 | healthy / healthy |
| 17_left_post_1_month_T5 | 1.95 / 1.95 | 178.5 / 177.7 | 78.3 / 76.9 | 6 / 6.5 | 0.95 / 0.95 | 0.136 / 0.120 | healthy / healthy | 75.8 / 76.7 | 0.118 / 0.120 | healthy / healthy |
| 17_left_post_1_week_T1 | 2.65 / 2.65 | 179.3 / 178.9 | 80.3 / 78.3 | 6 / 6 | 0.92 / 0.92 | 0.143 / 0.140 | healthy / healthy | 76.4 / 78.3 | 0.137 / 0.140 | healthy / healthy |
| 17_left_post_1_week_T2 | 2.85 / 2.85 | 178.6 / 177.5 | 75.1 / 73.1 | 6.5 / 6.5 | 0.92 / 0.92 | 0.133 / 0.130 | healthy / healthy | 62.4 / 70.5 | 0.114 / 0.125 | healthy / healthy |
| 17_left_post_1_week_T3 | 2.10 / 2.10 | 178.9 / 178.2 | 84.4 / 64.4 | 6 / 6.5 | 0.92 / 0.92 | 0.156 / 0.110 | healthy / healthy | 50.8 / 78.6 | 0.091 / 0.137 | healthy / healthy |
| 17_left_post_1_week_T4 | 1.95 / 1.95 | 178.7 / 178.4 | 89.6 / 84.9 | 6.5 / 6.5 | 0.92 / 0.92 | 0.141 / 0.134 | healthy / healthy | 76.5 / 89.5 | 0.119 / 0.141 | healthy / healthy |
| 17_right_post_T1 | 2.65 / 2.65 | 171.0 / 171.1 | 66.7 / 65.6 | 5.5 / 5.5 | 0.93 / 0.93 | 0.139 / 0.137 | healthy / healthy | 63.9 / 64.9 | 0.132 / 0.135 | healthy / healthy |
| 17_right_post_T2 | 2.30 / 2.30 | 174.6 / 174.0 | 66.8 / 65.5 | 5.5 / 5.5 | 0.93 / 0.93 | 0.146 / 0.143 | healthy / healthy | 60.4 / 92.5 | 0.123 / 0.248 | healthy / borderline |
| 17_right_post_T3 | 1.40 / 2.70 | 174.9 / 178.5 | 74.5 / 68.8 | 6 / 6 | 0.93 / 0.93 | 0.148 / 0.129 | healthy / healthy | 124.9 / 82.3 | 0.244 / 0.169 | refused / healthy |
| 17_right_post_T4 | 2.30 / 2.30 | 178.5 / 178.3 | 70.1 / 68.1 | 5.5 / 5.5 | 0.92 / 0.92 | 0.146 / 0.142 | healthy / healthy | 75.1 / 71.0 | 0.156 / 0.149 | healthy / healthy |
| 17_right_post_1_month_T1 | 3.10 / 3.10 | 179.1 / 177.9 | 76.1 / 74.8 | 6.5 / 6.5 | 0.94 / 0.94 | 0.107 / 0.104 | healthy / healthy | 63.7 / 72.8 | 0.088 / 0.100 | healthy / healthy |
| 17_right_post_1_month_T2 | 2.10 / 2.10 | 179.2 / 178.4 | 79.1 / 80.8 | 6.5 / 6.5 | 0.94 / 0.94 | 0.121 / 0.124 | healthy / healthy | 81.1 / 80.6 | 0.125 / 0.123 | healthy / healthy |
| 17_right_post_1_month_T3 | 2.30 / 2.30 | 177.5 / 176.5 | 72.9 / 83.0 | 6.5 / 6.5 | 0.94 / 0.94 | 0.135 / 0.148 | healthy / healthy | 67.4 / 79.4 | 0.132 / 0.143 | healthy / healthy |
| 17_right_post_1_month_T4 | 2.45 / 2.45 | 176.6 / 178.0 | 86.1 / 81.1 | 6.5 / 6.5 | 0.93 / 0.93 | 0.129 / 0.122 | healthy / healthy | 62.8 / 78.5 | 0.101 / 0.117 | healthy / healthy |
| 17_right_post_1_week_T1 | 3.75 / 3.75 | 178.7 / 178.7 | 76.6 / 74.2 | 6.5 / 6.5 | 0.94 / 0.94 | 0.110 / 0.106 | healthy / healthy | 74.3 / 78.0 | 0.106 / 0.114 | healthy / healthy |
| 17_right_post_1_week_T2 | 2.45 / 2.45 | 177.1 / 177.0 | 63.1 / 69.1 | 7 / 6.5 | 0.94 / 0.94 | 0.086 / 0.089 | healthy / healthy | 75.8 / 48.7 | 0.106 / 0.075 | healthy / healthy |
| 17_right_post_1_week_T3 | 1.85 / 1.90 | 178.9 / 178.0 | 78.4 / 74.2 | 6.5 / 7 | 0.93 / 0.94 | 0.126 / 0.112 | healthy / healthy | 75.8 / 80.8 | 0.121 / 0.124 | healthy / healthy |
| 17_right_post_1_week_T4 | 2.55 / 2.60 | 170.5 / 170.0 | 76.1 / 68.8 | 6.5 / 6.5 | 0.93 / 0.93 | 0.117 / 0.095 | healthy / healthy | 60.3 / 69.6 | 0.090 / 0.097 | healthy / healthy |
| 18_left_pre_T1 | release never detected | | | | | | unscored / unscored | | | unscored / unscored |
| 18_left_pre_T2 | 5.10 / 5.80 | 176.3 / 178.8 | 92.4 / 75.0 | 7.5 / 7.5 | 0.92 / 0.92 | 0.189 / 0.165 | borderline / healthy | 64.7 / 107.9 | 0.163 / 0.199 | healthy / borderline |
| 18_left_pre_T3 | release never detected | | | | | | unscored / unscored | | | unscored / unscored |
| 18_left_pre_T4 | 3.50 / 3.55 | 178.9 / 178.7 | 78.5 / 72.1 | 7 / 7.5 | 0.92 / 0.92 | 0.172 / 0.158 | borderline / healthy | 71.7 / 83.1 | 0.163 / 0.173 | healthy / borderline |
| 18_right_pre_T1 | release never detected | | | | | | unscored / unscored | | | unscored / unscored |
| 18_right_pre_T2 | 3.20 / 3.20 | 178.1 / 178.2 | 80.7 / 73.5 | 4 / 4 | 0.92 / 0.92 | 0.293 / 0.291 | borderline / borderline | 70.6 / 72.6 | 0.274 / 0.291 | borderline / borderline |
| 18_right_pre_T3 | 2.40 / 2.40 | 178.7 / 178.7 | 73.8 / 78.3 | 6.5 / 6 | 0.91 / 0.91 | 0.171 / 0.188 | healthy / borderline | 80.1 / 78.1 | 0.193 / 0.175 | borderline / borderline |
| 18_right_pre_T4 | release never detected | | | | | | unscored / unscored | | | unscored / unscored |
| 18_right_pre_T5 | 1.75 / 1.75 | 177.6 / 176.4 | 65.5 / 76.1 | 6.5 / 6.5 | 0.91 / 0.91 | 0.159 / 0.171 | healthy / borderline | 85.4 / 54.2 | 0.197 / 0.160 | borderline / healthy |
| 20_left_pre_T1 | - / - | - / - | - / - | - / - | - / - | - / - | unscored / unscored | - / - | - / - | unscored / unscored |
| 20_left_pre_T2 | - / - | - / - | - / - | - / - | - / - | - / - | unscored / unscored | - / - | - / - | unscored / unscored |
| 20_left_pre_T3 | - / - | - / - | - / - | - / - | - / - | - / - | unscored / unscored | - / - | - / - | unscored / unscored |
| 20_left_pre_T4 | - / - | - / - | - / - | - / - | - / - | - / - | unscored / unscored | - / - | - / - | unscored / unscored |
| 20_right_pre_T1 | 2.00 / 2.00 | 178.4 / 178.0 | 46.3 / 58.1 | 7.5 / 5.5 | 1.43 / 1.30 | 0.273 / 0.286 | borderline / borderline | 62.0 / 40.3 | 0.293 / 0.257 | borderline / borderline |
| 20_right_pre_T2 | 3.05 / 3.05 | 178.0 / 176.8 | 52.8 / 55.6 | 5 / 1 | 1.45 / 0.00 | 0.346 / 0.520 | borderline / impaired | 56.1 / 44.4 | 0.497 / 0.309 | impaired / borderline |
| 20_right_pre_T3 | 3.10 / 2.90 | 177.1 / 175.7 | 39.3 / 54.1 | 6 / 5 | 1.53 / 1.48 | 0.391 / 0.359 | impaired / impaired | 59.3 / 39.8 | 0.430 / 0.315 | impaired / borderline |
| 20_right_pre_T4 | release never detected | | | | | | unscored / unscored | | | unscored / unscored |
| 4_left_june_post_T1 | 2.80 / 2.80 | 179.0 / 178.6 | 73.8 / 68.6 | 5 / 5 | 0.88 / 0.88 | 0.301 / 0.236 | borderline / borderline | 63.0 / 68.5 | 0.216 / 0.236 | borderline / borderline |
| 4_left_june_post_T2 | 2.05 / 2.05 | 179.3 / 178.3 | 51.5 / 62.6 | 4.5 / 4.5 | 0.88 / 0.88 | 0.285 / 0.211 | borderline / borderline | 55.1 / 62.6 | 0.193 / 0.211 | borderline / borderline |
| 4_left_june_post_T3 | 1.35 / 1.35 | 166.0 / 171.1 | 108.3 / 70.4 | 0 / 0 | 0.00 / 0.00 | 0.718 / 0.674 | refused / refused | 290.2 / 67.5 | 1.630 / 0.668 | refused / refused |
| 4_left_june_post_T4 | 2.55 / 2.45 | 178.8 / 178.8 | 110.0 / 80.7 | 2.5 / 2.5 | 0.91 / 0.91 | 0.434 / 0.320 | impaired / borderline | 195.9 / 76.1 | 1.580 / 0.317 | refused / borderline |
| 4_left_june_post_T5 | 3.00 / 2.95 | 179.0 / 178.4 | 83.1 / 74.3 | 2.5 / 3 | 0.91 / 0.98 | 0.402 / 0.296 | impaired / borderline | 57.5 / 72.5 | 0.418 / 0.295 | impaired / borderline |
| 4_left_pre_T1 | 2.60 / 2.65 | 178.9 / 177.8 | 73.1 / 65.1 | 2 / 2 | 0.91 / 0.91 | 0.551 / 0.383 | impaired / impaired | 59.2 / 64.0 | 0.510 / 0.383 | impaired / impaired |
| 4_left_pre_T2 | 3.00 / 6.45 | 172.0 / 178.0 | 68.4 / 62.6 | 0 / 3 | 0.00 / 0.89 | 0.695 / 0.361 | refused / impaired | 59.1 / 63.9 | 0.679 / 0.363 | refused / impaired |
| 4_left_pre_T3 | 3.55 / 3.50 | 179.2 / 178.1 | 65.2 / 66.1 | 6 / 6 | 0.89 / 0.89 | 0.163 / 0.174 | healthy / borderline | 51.3 / 65.1 | 0.138 / 0.172 | healthy / borderline |
| 4_left_pre_T4 | 1.45 / 1.45 | 178.8 / 178.3 | 58.6 / 67.4 | 6 / 6 | 0.89 / 0.89 | 0.173 / 0.182 | borderline / borderline | 49.8 / 67.1 | 0.212 / 0.182 | borderline / borderline |
| 4_left_pre_T5 | 2.25 / 2.25 | 178.7 / 177.6 | 62.7 / 67.2 | 6 / 6 | 0.89 / 0.89 | 0.174 / 0.179 | borderline / borderline | 66.2 / 67.6 | 0.179 / 0.180 | borderline / borderline |
| 4_right_june_post_T1 | 2.25 / - | 178.9 / - | 3.8 / - | 0.5 / - | 0.00 / - | 0.385 / - | refused / unscored | 93.3 / - | 0.697 / - | refused / unscored |
| 4_right_june_post_T2 | 3.10 / 3.10 | 177.0 / 177.0 | 87.7 / 85.8 | 5.5 / 5.5 | 0.91 / 0.91 | 0.209 / 0.208 | borderline / borderline | 66.6 / 80.6 | 0.183 / 0.203 | borderline / borderline |
| 4_right_june_post_T3 | release never detected | | | | | | unscored / unscored | | | unscored / unscored |
| 4_right_june_post_T4 | 2.75 / 2.80 | 177.4 / 176.4 | 50.8 / 65.8 | 6.5 / 6.5 | 0.92 / 0.92 | 0.246 / 0.155 | borderline / healthy | 77.9 / 54.7 | 0.192 / 0.200 | borderline / borderline |
| 4_right_june_post_T5 | release never detected | | | | | | unscored / unscored | | | unscored / unscored |
| 4_right_pre_T1 | 3.20 / 3.00 | 170.9 / 169.5 | 60.9 / 65.2 | 7 / 6.5 | 0.94 / 0.94 | 0.175 / 0.183 | borderline / borderline | 73.2 / 67.5 | 0.203 / 0.185 | borderline / borderline |
| 4_right_pre_T2 | 4.90 / 4.85 | 177.8 / 177.1 | 60.1 / 68.3 | 5.5 / 5.5 | 0.93 / 0.93 | 0.180 / 0.186 | borderline / borderline | 67.1 / 43.0 | 0.178 / 0.240 | borderline / borderline |
| 4_right_pre_T3 | 2.00 / 2.05 | 175.5 / 174.4 | 73.4 / 71.9 | 5.5 / 5 | 0.93 / 0.92 | 0.206 / 0.215 | borderline / borderline | 66.2 / 68.4 | 0.188 / 0.211 | borderline / borderline |
| 4_right_pre_T4 | 1.75 / 1.80 | 179.1 / 177.9 | 69.4 / 70.8 | 4.5 / 4 | 0.95 / 0.93 | 0.252 / 0.266 | borderline / borderline | 64.9 / 69.3 | 0.238 / 0.249 | borderline / borderline |
| 4_right_pre_T5 | 2.30 / 2.25 | 178.3 / 179.6 | 63.9 / 73.2 | 4 / 4 | 0.92 / 0.92 | 0.443 / 0.315 | impaired / borderline | 72.2 / 73.4 | 0.315 / 0.315 | borderline / borderline |
| 5_left_post_1month_T1 | 2.35 / 2.30 | 177.1 / 177.1 | 72.2 / 67.8 | 0.5 / 0.5 | 0.00 / 0.00 | 0.856 / 0.654 | impaired / impaired | 78.1 / 68.2 | 1.050 / 0.666 | impaired / impaired |
| 5_left_post_1month_T2 | 2.50 / 3.60 | 174.3 / 177.8 | 61.7 / 56.8 | 6.5 / 6.5 | 0.91 / 0.91 | 0.156 / 0.147 | healthy / healthy | 41.6 / 48.3 | 0.137 / 0.137 | healthy / healthy |
| 5_left_post_1month_T3 | 2.45 / 3.35 | 175.0 / 177.4 | 53.5 / 52.1 | 7 / 6.5 | 0.90 / 0.91 | 0.153 / 0.146 | healthy / healthy | 69.8 / 54.8 | 0.183 / 0.153 | borderline / healthy |
| 5_left_post_1month_T4 | 2.10 / 2.05 | 176.6 / 177.1 | 56.0 / 56.9 | 6 / 6 | 0.91 / 0.91 | 0.145 / 0.157 | healthy / healthy | 74.1 / 57.4 | 0.194 / 0.158 | borderline / healthy |
| 5_left_post_1month_T5 | 2.55 / 4.00 | 172.8 / 177.6 | 59.4 / 54.1 | 0 / 7 | 0.00 / 0.91 | 0.702 / 0.140 | refused / healthy | 30.2 / 39.4 | 0.611 / 0.135 | refused / healthy |
| 5_right_post_1month_T1 | 2.45 / 2.45 | 177.3 / 177.8 | 56.5 / 58.8 | 6 / 6 | 0.92 / 0.92 | 0.088 / 0.095 | healthy / healthy | 60.1 / 52.0 | 0.100 / 0.083 | healthy / healthy |
| 5_right_post_1month_T2 | 4.05 / 4.05 | 177.3 / 177.5 | 56.4 / 58.3 | 6 / 6 | 0.92 / 0.92 | 0.087 / 0.088 | healthy / healthy | 68.3 / 61.8 | 0.133 / 0.100 | healthy / healthy |
| 5_right_post_1month_T3 | 3.90 / 3.90 | 176.9 / 177.0 | 57.8 / 57.7 | 6.5 / 6.5 | 0.92 / 0.92 | 0.091 / 0.090 | healthy / healthy | 65.0 / 62.0 | 0.111 / 0.103 | healthy / healthy |
| 5_right_post_1month_T4 | 2.95 / 2.95 | 178.1 / 178.1 | 54.0 / 56.4 | 6 / 6 | 0.93 / 0.94 | 0.090 / 0.090 | healthy / healthy | 57.1 / 61.8 | 0.108 / 0.121 | healthy / healthy |
| (unkeyed #178) | release never detected | | | | | | unscored / unscored | | | unscored / unscored |
