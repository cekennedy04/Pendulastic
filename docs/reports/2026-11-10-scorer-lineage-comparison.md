# Scorer lineage comparison: `main` vs the September branches

**2026-11-10.** Verification only. No scorer code was changed and nothing was ported.

Question: about 110-130 September commits never reached `main`. Older notes say
that lineage holds the corpus-validated scorer and that `main` regressed A0, N
and area_ratio; a later note says `main` contains everything. Which is true, and
what is `main` actually missing?

Short answer, measured on the phone corpus:

- **Test-retest: a tie.** Under the production convention (`detrend=False`) no
  Popovic parameter separates the two lineages (every bootstrap interval on the
  ICC difference spans zero, apart from `omega_min_n`, which the two lineages
  define differently). The "main regressed A0, N and area_ratio" claim does not
  reproduce there. It reproduces for **A0 only, and only with `detrend=True`**
  (ICC 0.040 on main vs 0.265, SEM 23.8 vs 12.6 deg), and that traces to one
  missing fix (`4bd13a2`).
- **IMU-vs-OptiTrack RMSE: a tie on the IMU side.** As run, main reports a
  median offset-removed RMSE of 22.0 deg and September 14.7 deg, but the whole
  gap is the comparison harness (`compare_pair`), not the scorer or the replay:
  with either lineage's harness applied to both lineages' IMU curves the medians
  are equal (14.68 vs 14.68 deg, or 22.04 vs 22.63 deg).
- **Coverage: main is better.** Main scores 162 of 179 trials, September 152.
- **"Main contains everything" is false.** Five behaviours are missing (section 5).

---

## 1. What was compared

| | main lineage | September lineage |
|---|---|---|
| ref | `origin/main` @ `75109c5` | `feat/rust-abs-flex-projection` @ `9a555de` |
| merge base | `bccb44c` (2026-09-01) | same |
| commits not on main | — | 114 (`git cherry`: 110 have no patch-equivalent on main) |

`feat/rust-abs-flex-projection` contains `d2e2cee`, `f429356` and `9a555de`. It
does **not** contain `61d5723` (the signed swing angle); that commit lives only
on `bench/retest-abs`, `bench/retest-diagnostics` and
`fix/swing-angle-signed-abs-fold`. Both lineages compared here use the `abs()`
fold in the Python extraction (`imu_calibration_tuner._swing_from_quats` is the
same three-tier code on both).

Each lineage's code ran from its own throwaway worktree against the same data in
the main checkout. `participant_paths.py` exists on both lineages and the
September copy resolves the renamed `Control_/CHAT_/MINT_` folders, so no
workaround was needed: trial discovery on the two lineages returned byte-identical
lists.

## 2. Method

**Trials.** `batch_imu_vs_optitrack_rmse.discover_trials()` with the main
checkout's `excluded_trials.json` (38 entries) applied on both sides: **179
phone trials, 14 participants, 159 with an OptiTrack match**. One list, used for
both lineages. No trial was dropped by hand.

**Calling convention**, identical on both sides, each lineage running its own
modules:

```
samples = rmse_pipeline_common.reconstruct_trial(accel, gyro, mag)
t, ang  = imu_calibration_tuner.replay_trial(samples, cfg, tick_s=ANALYSIS_TICK_S)   # 0.01 s, 100 Hz grid
params  = pendulastic_pt_score.compute_pt_params(t, ang, detrend=<True|False>)       # release_idx=None
score   = pendulastic_pt_score.compute_pt_score(params)
```

- `cfg` is the main checkout's `imu_calibration_config.json` on both sides
  (beta 0.041, ema_alpha 0.5, flex_axis_capture on, gravity_seed on).
- Release is auto-detected by each lineage's own `_detect_release`.
- Both conventions are reported: `detrend=False` (what `neuromech/corpus_rest.py`
  calls the production convention) and `detrend=True` (the function default,
  which the earlier test-retest work used).
- `PT (own)` is each lineage's own `compute_pt_score` and `HEALTHY_REF`. These are
  different formulas (main: `pt6rescaled-ref-2026-10-27`; September: the 7-term
  sum), so `PT (common)` re-scores both lineages' parameters with main's formula.

**Test-retest.** As `test_retest_reliability.py` on `bench/retest-abs`: group =
participant + leg + condition (trial key minus `_Tn`; keys come from the
participant ID), groups with at least 3 repeats, ICC(1,1) one-way random, SEM =
within-group SD. Restricted to trials scored on **both** lineages so the two
columns describe the same trials. Interval on the ICC difference: 2000 bootstrap
resamples over participants.

**RMSE.** Each lineage's own `batch_imu_vs_optitrack_rmse.evaluate_trial`. All
matched trials load through the marker path (`marker_pca`), which returns a
relative curve, so absolute `rmse_deg` (about 140-160 deg) carries no
information; `rmse_deg_offset_removed` is the comparable number. To separate the
IMU pipeline from the comparison harness, each lineage's `compare_pair` was also
applied to both lineages' IMU curves against the optical curve.

**Not committed.** The driver scripts lived in a scratch directory; only this
report is committed.

## 3. Results

### 3.1 Coverage

| | main | September |
|---|---|---|
| trials scored (of 179) | 162 | 152 |
| scored on both | 151 (`detrend=True`), 152 (`detrend=False`) | |
| replay returned no curve | 12 | 26 |

The 14 extra empty replays on September are its release gate never capturing a
zero pose (10 trials, `no_zero_capture`) and a log with no accelerometer samples
(4 trials, which main replays but then does not score either). Main's
`849430e` (a noise blip during the hold no longer loses the trial) is the
difference. Replay curves are identical on 127 of the 153 trials both lineages
replay; they differ on 26 (largest single-sample difference 50 deg), which is
main's later AHRS and release-gate fixes.

### 3.2 Per-parameter deltas (main minus September, trials scored on both)

`detrend=False`, n = 152:

| parameter | median main | median Sept | median delta | median abs delta | p90 abs delta |
|---|---|---|---|---|---|
| A0 (deg) | 63.80 | 63.89 | -0.15 | 0.27 | 3.58 |
| A1 (deg) | 87.36 | 86.39 | -0.56 | 0.81 | 71.2 |
| N | 5.5 | 4.25 | +0.5 | 0.5 | 2.0 |
| R2n | 0.839 | 0.841 | -0.001 | 0.005 | 0.690 |
| phi_max_ratio | 0.448 | 0.375 | +0.001 | 0.002 | 0.380 |
| omega_max_n | 4.73 | 4.13 | +0.68 | 0.74 | 1.88 |
| omega_min_n | -4.71 | 0.0008 | -4.72 | 4.72 | 6.52 |
| omega peak (deg/s) | 313.6 | 262.9 | +44.6 | 44.6 | 107 |
| f (Hz) | 0.939 | 0.803 | +0.107 | 0.127 | 0.534 |
| area_ratio | 0.067 | 0.544 | -0.425 | 0.425 | 0.742 |
| PT (own) | 0.167 | 0.872 | -0.560 | 0.560 | 1.24 |
| PT (common) | 0.167 | 0.567 | -0.303 | 0.303 | 0.948 |

`detrend=True`, n = 151:

| parameter | median main | median Sept | median delta | median abs delta | p90 abs delta |
|---|---|---|---|---|---|
| A0 (deg) | 61.53 | 61.30 | -0.14 | 0.26 | 3.74 |
| A1 (deg) | 87.41 | 86.98 | -0.73 | 0.79 | 29.5 |
| N | 5.5 | 4.5 | +0.5 | 0.5 | 2.5 |
| R2n | 0.880 | 0.902 | -0.003 | 0.005 | 0.676 |
| phi_max_ratio | 0.412 | 0.377 | +0.000 | 0.002 | 0.068 |
| omega_max_n | 5.10 | 4.49 | +0.68 | 0.70 | 1.54 |
| omega_min_n | -5.10 | 0.0018 | -5.10 | 5.10 | 6.92 |
| omega peak (deg/s) | 312.6 | 266.7 | +39.9 | 39.9 | 82.6 |
| f (Hz) | 0.941 | 0.911 | +0.006 | 0.011 | 0.475 |
| area_ratio | 0.067 | 0.288 | -0.157 | 0.178 | 0.697 |
| PT (own) | 0.153 | 0.464 | -0.233 | 0.245 | 1.08 |
| PT (common) | 0.153 | 0.293 | -0.083 | 0.099 | 0.716 |

Reading it:

- **A0 agrees** to a quarter of a degree on the typical trial. The tail (p90 3.6
  deg) is the release detector and the drift correction, below.
- **N, omega and area_ratio differ by definition, not by regression.** Main
  removed the oscillation-window cap (`2255eeb`), signs `omega_min_n` (`a23606a`;
  on September it is min|omega|, about zero, which is why its row is not
  comparable), and measures area about the swing centre (`b44dc7d`). September
  flags `quality_warn` (area_ratio > 0.55) on 75 of 152 trials under
  `detrend=False`; main on 9 of 162.
- **PT** therefore differs mostly through area_ratio and the formula.

### 3.3 Test-retest, ICC(1,1)

36 participant-leg-condition groups, 12 participants; 146 trials
(`detrend=False`), 145 (`detrend=True`). "Better" is called only when the
bootstrap interval on the difference excludes zero.

`detrend=False`:

| parameter | ICC main | ICC Sept | difference [95%] | SEM main | SEM Sept | better |
|---|---|---|---|---|---|---|
| A0 (deg) | 0.291 | 0.243 | +0.048 [-0.016, +0.115] | 12.3 | 14.6 | tie |
| A1 (deg) | 0.518 | 0.677 | -0.159 [-0.499, +0.126] | 22.4 | 23.9 | tie |
| N | 0.712 | 0.722 | -0.010 [-0.153, +0.106] | 1.36 | 1.12 | tie |
| R2n | 0.496 | 0.211 | +0.285 [-0.236, +0.481] | 0.246 | 0.854 | tie |
| phi_max_ratio | 0.596 | 0.622 | -0.027 [-0.241, +0.162] | 0.135 | 0.151 | tie |
| omega_max_n | 0.354 | 0.090 | +0.264 [-0.121, +0.491] | 1.49 | 4.81 | tie |
| omega_min_n | 0.501 | -0.072 | +0.573 [+0.196, +0.772] | 1.17 | 0.005 | main (definition differs) |
| omega peak (deg/s) | 0.670 | 0.659 | +0.011 [-0.095, +0.080] | 38.7 | 37.8 | tie |
| f (Hz) | 0.320 | 0.488 | -0.168 [-0.539, +0.138] | 0.317 | 0.248 | tie |
| area_ratio | 0.533 | 0.551 | -0.018 [-0.557, +0.289] | 0.143 | 0.211 | tie |
| pre-release angle (deg) | 0.062 | 0.503 | -0.442 [-0.620, +0.031] | 8.69 | 0.55 | tie (see 4.2) |
| PT (own) | 0.657 | 0.610 | +0.048 [-0.451, +0.306] | 0.201 | 0.362 | tie |
| PT (common) | 0.657 | 0.652 | +0.005 [-0.464, +0.234] | 0.201 | 0.293 | tie |

`detrend=True`:

| parameter | ICC main | ICC Sept | difference [95%] | SEM main | SEM Sept | better |
|---|---|---|---|---|---|---|
| A0 (deg) | 0.040 | 0.265 | -0.225 [-0.430, +0.025] | 23.8 | 12.6 | tie (see 4.1) |
| A1 (deg) | 0.393 | 0.671 | -0.278 [-0.597, +0.132] | 28.3 | 19.7 | tie |
| N | 0.700 | 0.753 | -0.053 [-0.156, +0.027] | 1.37 | 1.08 | tie |
| R2n | 0.318 | 0.462 | -0.143 [-0.455, +0.195] | 0.271 | 0.252 | tie |
| phi_max_ratio | 0.571 | 0.672 | -0.101 [-0.277, +0.037] | 0.135 | 0.121 | tie |
| omega_max_n | 0.246 | 0.324 | -0.079 [-0.196, +0.064] | 1.43 | 0.922 | tie |
| omega_min_n | 0.252 | 0.022 | +0.230 [+0.038, +0.537] | 1.44 | 0.010 | main (definition differs) |
| omega peak (deg/s) | 0.667 | 0.624 | +0.043 [-0.051, +0.121] | 39.5 | 37.6 | tie |
| f (Hz) | 0.413 | 0.287 | +0.126 [-0.035, +0.375] | 0.304 | 0.269 | tie |
| area_ratio | 0.278 | 0.533 | -0.255 [-0.524, +0.030] | 0.170 | 0.212 | tie |
| neutral (deg) | 0.148 | 0.271 | -0.122 [-0.225, -0.027] | 32.2 | 16.1 | September |
| PT (own) | 0.473 | 0.561 | -0.088 [-0.439, +0.153] | 0.248 | 0.365 | tie |
| PT (common) | 0.473 | 0.592 | -0.118 [-0.414, +0.074] | 0.248 | 0.285 | tie |

Twelve participants give wide intervals. "Tie" means this corpus cannot separate
the lineages, not that they are shown to be equal. The point estimates lean
September under `detrend=True` (A0, A1, area_ratio, neutral) and are mixed under
`detrend=False`.

### 3.4 IMU vs OptiTrack RMSE

159 trials have an OptiTrack match. Optical curves are identical between
lineages on all 155 trials where both loaded one.

As run, each lineage's own pipeline, 134 trials with status ok on both:

| metric | main median | main mean | Sept median | Sept mean | main lower / Sept lower / equal | Wilcoxon p |
|---|---|---|---|---|---|---|
| RMSE, offset removed (deg) | 22.04 | 24.99 | 14.68 | 14.82 | 16 / 59 / 59 | 4.5e-10 |
| RMSE, absolute (deg) | 143.3 | 141.5 | 159.3 | 155.7 | 65 / 10 / 59 | 1.4e-10 |

Status ok: main 145, September 134 (10 of the 11 are the empty replays of 3.1).

One harness, both lineages' IMU curves, same 134 trials (offset removed, deg):

| harness (`compare_pair` from) | main IMU median | Sept IMU median | main lower / Sept lower / equal | Wilcoxon p |
|---|---|---|---|---|
| September | 14.68 | 14.68 | 13 / 8 / 113 | 0.15 |
| main | 22.04 | 22.63 | 16 / 5 / 113 | 0.025 |

So the 22.0-vs-14.7 gap is the harness. September's `compare_pair` orients both
curves release-positive before aligning them (`fb46178`); main's does not. The
September harness reports the optical curve arriving inverted on 65 of 134
trials and the IMU curve on none, which is the arbitrary optical polarity
introduced by `87d7645`. On the IMU side the two lineages are equally accurate;
main is marginally ahead under its own harness.

## 4. Where the differences come from

### 4.1 Drift-correction fallback (`4bd13a2`), `detrend=True` only

When the settled-tail slope is unusable, both lineages fall back to a line
fitted through the pre-release hold. September bounds that fallback
(`_credible_drift_slope`); main does not. On main the fallback was used on 39 of
162 scored trials, and on four of them it removed 0.7 to 6.3 times the curve's
whole range, giving A0 of 139.8, 155.3, 186.2 and 234.8 deg (participants 4 and
13) where the same trials read 27.6 to 115.5 deg without detrending. September
reports no A0 above 120 deg in either convention; its bound rejected the
fallback on 3 trials. This is the source of main's A0 SEM of 23.8 deg under
`detrend=True` against 12.3 deg under `detrend=False`.

### 4.2 Release detector (`d2e2cee`, `35d3192`, `05b5a4e`)

Main keeps the 8%-of-range displacement threshold, with a 0.10 s back-off
(`ef508b9`) and a lift-and-hold rule (`f1584ae`). September replaced it with a
sustained-rate onset detector. On 131 of 152 trials the two agree within 0.05 s
(median difference +0.010 s) and A0 then agrees to a median 0.22 deg, so main's
back-off is a behavioural equivalent for the typical trial.

On 12 of 152 trials (participants 4, 13, 14, 15, 18) they disagree by more than
0.25 s:

- September's release starts within 5 deg of full extension on 12 of 12; main's
  on 4 of 12.
- Main fires 0.8 to 6.9 s early on 10 of them and returns f = 0 on 9 of those 10
  (N = 0 on 7), where September measures an oscillation on 6 of the 10. This is
  the failure `d2e2cee` describes.
- Main fires late, mid-swing, on 2 (pre-release angle 95 and 114 deg). On those
  two September's own A0 is 5.0 and 7.3 deg, which is not credible either.

Pre-release angle SEM is 8.7 deg on main against 0.55 deg on September.

### 4.3 Frequency cap and `f_out_of_range` (`c2dfe67`)

Main still spaces extrema by one period at 3.5 Hz (`min_dist = fps/3.5`) in
Python, Rust (`scoring.rs`) and C# (`PtScore.cs`), and has no
`F_PLAUSIBLE_MAX_HZ` or `f_out_of_range` anywhere. `neuromech/build.py` reads the
constant with `getattr(..., None)`, so its `flag_f_out_of_range` is always False
on main. The corpus cannot test this: no trial on either lineage has f above
2 Hz. The commit's own evidence is the bench (3.2-4 Hz swings scoring at half
frequency or not at all), which was not re-run here.

### 4.4 Rust flex-axis projection (`f429356`) — emulated, not run

Main's Rust `replay::swing_deg` has the across-gravity tier (from `68ce627`) and
the per-axis gyro-bias gate, but not the flex-axis-projected tier; main's
`FlexAxisEstimator` port is used only for the lateral-motion report. The Python
reference on main does use the flex projection, so on main the phone core and
the desktop reference extract the angle differently.

The Rust was not run. As a stand-in, main's **Python** replay was run with
`flex_axis_capture=False`, which selects the same across-gravity tier:

- A0 moves between the two tiers by a median 2.1 deg per trial (p90 8.0 deg)
  with `detrend=False`, and 4.8 deg (p90 22 deg) with `detrend=True`.
- RMSE (offset removed, September harness, 144 trials): flex-projected 15.09
  vs across-gravity 16.59 deg median; flex lower on 98 of 144, p = 1.4e-09.
- Test-retest (`detrend=False`, 38 groups, 158 trials): flex better on R2n
  (+0.314 [+0.006, +0.496]) and f (+0.099 [+0.003, +0.297]); across-gravity
  better on omega peak (-0.158 [-0.269, -0.055]); everything else a tie.

## 5. Which of the named changes main has

| change | on main? | basis |
|---|---|---|
| `d2e2cee` release at onset | **Partly.** Different mechanism, equivalent on 131/152 trials, wrong on about 10 | measured (4.2) |
| `f429356` Rust flex projection | **No** for the projection tier; **yes** for the across-gravity tier and the gyro-bias SNR gate (`68ce627`) | read from source; effect emulated in Python (4.4) |
| `9a555de` settle + countdown | **Yes.** `04f388d` and `9c5a345` are ancestors of main; `HoldState::Settled`, `settle_s`, wasm state code 4 and the auto-stop in `app.js` are all present | read from source |
| `61d5723` signed swing angle | **No, and it should stay out.** Not on `feat/rust-abs-flex-projection` either. Its own commit message records a corpus test-retest regression; `abs()` is load-bearing | read from source; not re-measured |
| `f_out_of_range` flag | **No**, in Python, Rust or C# | read from source (4.3) |

Also missing on main, found along the way:

| change | effect |
|---|---|
| `4bd13a2` bounded drift fallback | measured (4.1) |
| `fb46178` orient curves in `compare_pair` | measured (3.4); a measurement fix, not a scorer change |
| `6b20b1d` `hold_drift` evidence | main's `compute_pt_params` returns no `hold_drift`, so the "hold drifting" filter in `neuromech/corpus_rest.py` never fires. Read from source; effect not measured |

## 6. Which lineage is better, by measure

| measure | better | note |
|---|---|---|
| trials scored | main (162 vs 152) | replay fixes |
| test-retest, `detrend=False` | tie | no interval excludes zero on a comparably defined parameter |
| test-retest, `detrend=True` | September on neutral; leans September on A0 | `4bd13a2` |
| release placement | September | 12/12 vs 4/12 at extension where they disagree |
| RMSE, IMU side | tie | 14.68 vs 14.68 deg under one harness |
| RMSE as reported | September (14.7 vs 22.0 deg) | harness only (`fb46178`) |
| area_ratio, N, omega definitions | not ranked | deliberate later redefinitions on main |

## 7. Recommended port list

Port to main, in this order:

1. **`4bd13a2`** — bound the drift-correction baseline fallback. Smallest change,
   clearest measured effect, Python only on the September side.
2. **`fb46178`** — orient both curves in `compare_pair`. Without it main's
   reported RMSE is about 7 deg worse than the same curves deserve.
3. **`d2e2cee` + `35d3192` + `05b5a4e`** — the onset release detector, in Python,
   Rust and C# together. Keep main's lift-and-hold tests (`f1584ae`) as the
   acceptance check, and expect to re-derive `HEALTHY_REF`, since N and f change
   on the trials it fixes. The two late-firing trials in 4.2 need looking at
   first: September is not right on them either.
4. **`c2dfe67`** — extremum gap as the smoothing window, plus `F_PLAUSIBLE_MAX_HZ`
   and `f_out_of_range`, in all three ports. Justified by the bench, not by this
   corpus.
5. **`f429356`, projection tier only** — so the Rust core matches the Python
   reference. Confirm on the Rust itself first; the evidence here is a Python
   emulation, and the commit records an unresolved divergence on 7 of 34 bench
   trials.
6. **`6b20b1d`** — optional; makes an existing filter on main do something.

Do not port: `61d5723`; `9a555de` (already there); September's replay path, score
formula or `HEALTHY_REF` (main's are later and score more trials).

## 8. Not run, and limits

- **Rust and C# scorers were not executed** on either lineage. Everything about
  them is read from source; 4.4 is a Python emulation.
- **The motor bench was not re-run.** `c2dfe67` and `f429356` rest on bench
  evidence in their commit messages.
- **`61d5723` was not re-measured**; its regression is taken from its commit
  message and earlier notes.
- **`bench/retest-abs`, `bench/retest-diagnostics` and
  `fix/swing-angle-signed-abs-fold` were not run.** They add `61d5723` and
  diagnostics on top of the same scorer commits; only
  `feat/rust-abs-flex-projection` was scored.
- No attribution run was done that ports one fix at a time; sections 4.1 and 4.2
  attribute by inspecting the affected trials, not by ablation.
- No trial has a ground-truth release time, so "September places the release
  better" rests on the pre-release angle sitting at extension.
- Trials are grouped by participant, leg and condition across whatever sessions
  share that key, as the earlier test-retest script did.
