# The IMU grades a healthy leg MAS 4, and the reason is baseline drift

**2026-09-08** — the first clinical anchor in the corpus, a three-channel
comparison built on it, and the mechanism behind the IMU's composite failing.

## The anchor

MINT_25 was the same participant as MINT_4, re-recorded (operator-confirmed).
Merged: the June session became `june_pre` — legs captured 2026-06-25 (right)
and 2026-06-29 (left), the four-days-apart anomaly behind the long-standing
"P4 left/right unresolved" note — and 2026-09-08 became `pre`, a clean
single-day session with 10 trials across all three channels.

**The clinician graded both legs MAS 0 for the September session.** That makes
it the first trial set in this project where a channel can be scored against a
human assessor rather than against another instrument.

| channel | exact | within one grade | over-graded |
|---|---|---|---|
| OptiTrack | **6/7 (86%)** | **7/7 (100%)** | 1/7 |
| phone IMU | 1/10 (10%) | 1/10 (10%) | 9/10 |
| MediaPipe 2D | 0/8 (0%) | 0/8 (0%) | 8/8 |

IMU grades were `['4','4','4','4','4','4','0','4','4','1+']` on a leg a clinician
called normal. MediaPipe's were `['1+','3','4','4','2','2','4']`.

Two things follow. OptiTrack is externally validated for the first time, which
retroactively supports `87d7645` being a fix rather than a regression. And both
derived channels fail — the IMU as badly as MediaPipe — on a session where the
IMU's angle tracking is the best in the corpus (11.8 deg median RMSE). Good
tracking, wrong answer.

## What the IMU is actually seeing

`area_ratio` contributes **84.7%** of the IMU's PT7 on this session. Every other
scored parameter is near zero.

| parameter | healthy ref | IMU median | OptiTrack median | penalty direction |
|---|---|---|---|---|
| **area_ratio** | 0.077 | **0.759** | 0.033 | above |
| R2n | 1.032 | 0.813 | 0.982 | below |
| N | 5.50 | 4.5 | 7.0 | below |
| phi_max_ratio | 0.639 | 0.420 | 0.532 | below |
| omega_max_n | 6.768 | 4.768 | 6.974 | below |
| omega_min_n | 0.001 | 0.0003 | 0.0033 | above |
| f | 0.914 | 0.621 | 0.949 | both |

`area_ratio = |P⁺ − P⁻| / P_total` measures how lopsided the swing sits about
`neutral_deg`, and `neutral_deg` is the median of the settled tail. The phone is
still drifting through that tail, so the resting angle lands in the wrong place
and the whole oscillation falls on one side.

P4 Left/pre T5, the same trial through all three:

| | neutral | P⁺ | P⁻ | area_ratio | samples above neutral |
|---|---|---|---|---|---|
| OptiTrack | −47.1° | 48.8 | 43.2 | **0.061** | 53% |
| IMU, `detrend=False` | **118.0°** | 89.2 | 17.2 | 0.677 | 75% |
| IMU, `detrend=True` | **137.1°** | 21.2 | 53.4 | 0.432 | 45% |

**The IMU's estimate of where the leg rests moves 19 degrees on one flag**, on a
leg with roughly 57 degrees of total excursion. OptiTrack does not drift, so its
neutral is stable and its areas balance.

The chain is: phone drift → wrong resting angle → apparent swing asymmetry →
`area_ratio` saturates → PT7 saturates → MAS 4.

This is also the mechanism behind the detrend-convention finding recorded
earlier: the flag moves the IMU's PT7 by 0.363 and OptiTrack's by 0.050 because
it is moving the IMU's neutral, and OptiTrack has no drift for it to move. One
root cause, now named rather than observed.

### Neither existing knob rescues it

| variant | median area_ratio | MAS 0 on |
|---|---|---|
| `detrend=False` (production) | 0.759 | 1/10 |
| `detrend=True` | 0.411 | 0/10 |
| `compute_pt_score_simple` (drops area_ratio and f) | — | 2/10 |

The four-parameter score already excludes `area_ratio` on the documented grounds
that it is unreliable, and it does help — grades fall to
`['3','2','1','1','0','3','0','1+','1+','1']` — but it does not reach the
clinical answer. The residual is real: `phi_max_ratio` 0.42 against a 0.64
reference and `omega_max_n` 4.77 against 6.77 say the IMU under-reads peak
angular velocity, which the composite reads as damping.

## Three-channel comparison over the corpus

`generate_methodology_comparison_figures.py` →
`Model_Analysis_Outputs/Methodology_Comparison/`. Seven figures; no trial is
excluded, the only filter is whether an OptiTrack export exists to score
against, and the 7 trials whose reference looks wrapped stay in every median and
are marked rather than dropped.

**Angle.** MediaPipe 16.8 deg, phone IMU 13.6 deg median offset-removed RMSE,
IMU better on 62% of 134 paired trials. Adding the September session moved this
substantially — before it, the two channels were a tie at 21.8 / 22.9 with the
IMU better on 45%. Ten trials from one clean session should not swing a corpus
median that far, which suggests the earlier tie was substantially a data-quality
artefact of the older sessions rather than a property of the sensors.

**Parameters.** ICC(2,1) against OptiTrack: the IMU wins 7 of 8, with MediaPipe
negative on three. `area_ratio` is the one MediaPipe wins, by 0.01 — noise.

| parameter | IMU | MediaPipe |
|---|---|---|
| omega_min_n | **+0.98** | +0.38 |
| N | **+0.62** | +0.33 |
| phi_max_ratio | **+0.57** | +0.14 |
| f | +0.24 | +0.14 |
| R2n | +0.15 | −0.13 |
| area_ratio | +0.15 | +0.16 |
| omega_max_n | +0.02 | −0.05 |
| A0_deg | −0.02 | −0.15 |

**Composite.** Under each channel's own production convention the two are tied
(pooled |ΔPT7| 0.447 vs 0.460; IMU better on 6 of 12 participants). Scored under
a matched detrend convention the IMU separates — 0.247, r 0.630 against
MediaPipe's 0.460 / 0.364, better on 10 of 12 participants. The largest single
term in the comparison is a boolean flag, not a sensor.

**Coverage is not the confound.** Interpolating MediaPipe across its undetected
frames so it emits a continuous trace costs +0.00 deg median over 108 trials.
MediaPipe's parity at the angle level is not an artefact of skipping the frames
it fails on. That objection was raised twice and is wrong.

## What this means for the claim path

The working assumption has been that the IMU is the sole claim path and
MediaPipe is a reported negative result. This evidence splits that:

- The IMU's **angle** and its **parameters** are genuinely better than
  MediaPipe's — 62% of trials, ICC 7 of 8.
- The IMU's **grades** are not usable on the only clinical case available, and
  the failure is a drift artefact in one parameter rather than sensor noise.

So the claim path may need to rest on OptiTrack-validated parameters rather than
on IMU-derived MAS.

**What this does not establish.** One participant, one timepoint, one grade
level. MAS 0 tests only the healthy end, and a channel that over-grades
everything is indistinguishable here from one that is miscalibrated. More
clinical anchors at other grades are needed before this is conclusive. What it
does establish is the mechanism, which was previously suspected and is now
measured.

## Open, and not mine to close

- `neutral_deg`, `area_ratio` and the detrend convention are in the scoring
  path. Nothing here was changed.
- June says P4 left 0 / right 1+; September says 0 / 0. Either the right leg
  improved over ten weeks, or the June grading is wrong — the same question the
  2026-08-24 transposition note left open, and its stated rationale ("right
  showed a damped spastic signature") is now in tension with a clinician reading
  that leg as normal. September is the better-controlled session.
- The September recordings arrived as `Recordings/MINT_MINT_25` because
  `metadata.json` carried `participant_id: "MINT_25"` and the recorder prefixed
  an already-prefixed id. `pid_from_dir_name("MINT_MINT_25")` returns
  `"MINT_25"`, which resolves to no participant, so the session was invisible to
  every path in the pipeline until it was found by hand. Worth a guard in
  `master_app` where the id is captured.
- `metadata.MINT_25.json` is archived beside `MINT_4/metadata.json`, not merged:
  the two disagree on age (60 vs 66) and weight (60 vs 1, the latter clearly a
  typo). Reconciling two records for one person is an operator decision.
