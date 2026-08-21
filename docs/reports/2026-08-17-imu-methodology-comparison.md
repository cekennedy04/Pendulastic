# IMU angle-computation methodology comparison — 2026-08-17

Investigating whether alternative IMU sensor-fusion/angle-computation approaches
(prompted by an externally-sourced methodology spec pasted into chat) reduce
real measured RMSE-vs-OptiTrack, vs this project's current default. All numbers
are real RMSE against OptiTrack ground truth across the same 53 real trials
(`batch_imu_vs_optitrack_rmse.py`'s discover_trials()), not a self-consistency
heuristic. Diagnostics live in `evaluate_ockendon_methodology.py` and
`evaluate_tuning_grid_methodology.py` (not pipeline-wired, one-off scripts).

## Baseline

**relative** (current default: AHRS-fused bilateral/solo relative angle) —
mean **16.8°**, median **14.0°**, 1/53 trials under the 5° clinical goal.

## Results so far

| Method | Mean RMSE | Median RMSE | Under 5° | Notes |
|---|---|---|---|---|
| relative (baseline) | 16.8° | 14.0° | 1/53 | Current production default |
| ockendon (as originally wired) | 150.5° | 149.7° | 0/53 | Broken — see bug below |
| ockendon_flipped (as originally wired) | 59.9° | 54.1° | 0/53 | Broken — see bug below |
| ockendon (bug fixed) | 112.7° | 107.8° | 0/53 | Still bad |
| **ockendon_flipped (bug fixed)** | **28.5°** | **27.9°** | 1/53 | Best of the Ockendon variants, still ~70% worse than baseline |

## Bug found and fixed: `_beta_from_quats()` Euler-pitch gimbal lock

`imu_calibration_tuner.py`'s Ockendon β input was computed as
`wrap180(pitch_cur - pitch_zero)` using Euler-angle pitch extraction, which is
unreliable near ±90° — exactly the range a real pendulum swing's tibia passes
through. Fixed by reusing `_swing_from_quats()`'s quaternion-delta rotation
magnitude (the same computation already validated for the `relative` method)
instead. This alone cut `ockendon_flipped`'s mean RMSE more than in half
(59.9° → 28.5°), confirming it was a real, substantial bug — but even fixed,
the Ockendon single-segment trig model still underperforms `relative` on this
dataset. Likely remaining gap: `OCKENDON_FT_RATIO=1.2` is a fixed population-
average femur:tibia ratio, not measured per participant, and the model has no
mechanism to correct for mounting/alignment imprecision the way the
two-quaternion relative method implicitly does.

**`ft_ratio` sweep result (done):** RMSE decreases *monotonically* as
`ft_ratio` increases from 1.0 all the way to 3.0 (mean 38.4° → 18.8°), never
finding a minimum in-range. This is a mathematical artifact, not a real
optimum: as `ft_ratio → infinity`, `arccos(sin(beta)/ft_ratio) → 90°`, so
`kappa_flipped → 180 - beta` -- i.e. the model degenerates toward the same
underlying quantity `relative` already computes, just approached from a
different parametric family. Any apparent improvement from raising
`ft_ratio` past realistic anatomy is really "become more like `relative`,"
not a genuine Ockendon-model win. Over the *physiologically real* adult
femur:tibia range (~1.0-1.3), RMSE is 28.5-38.4° -- meaningfully worse than
`relative`'s 16.8° at every plausible value. **Conclusion: the Ockendon
single-segment trig model does not beat the current method on this dataset
at any anatomically real ratio.** Not pursuing this further.

## Magnetometer fusion toggle

Added `params["use_mag"]` to `imu_calibration_tuner.replay_trial()` (default
`False`, unchanged behavior) to test the pasted spec's "9-axis" claim
(accel+gyro+mag) against the current mag-free default, which was deliberately
disabled 2026-08-10 after a real trial's mag stream froze mid-recording and
steered orientation toward a wrong heading. Plumbing tested
(`test_replay_trial_use_mag_true_passes_real_magnetometer_data`); real-RMSE
comparison not yet run — queued after the full tuning-grid sweep.

**Result (done):** essentially no difference. `use_mag=True` on top of the
grid-search best combo scores mean **14.89°** / median **11.11°** vs.
`use_mag=False`'s **14.84°** / **10.98°** — a wash, well within trial-to-trial
noise. Per-trial deltas are small and mixed (roughly half the trials get
marginally better, half marginally worse; largest single-trial swing was
~3.7°). **Conclusion: re-enabling magnetometer fusion neither meaningfully
helps nor hurts on this dataset.** This is a clean negative result for the
spec's "9-axis" claim, and also confirms the 2026-08-10 decision to disable
mag wasn't costing real accuracy — it was a wash then too, just reasoned
from a single anecdotal frozen-mag-stream trial rather than measured against
the full real-RMSE corpus like this. Leaving mag disabled (simpler, one
fewer failure mode from a frozen/disturbed compass) is still the right call.

## Full AHRS tuning-grid sweep (done)

Ran `imu_calibration_tuner.TUNING_GRID` (144 combos: beta × ema_alpha ×
flex_axis_capture × gravity_seed × method) against real OptiTrack RMSE
directly (not `score_waveform()`'s ground-truth-free self-consistency
heuristic, which exists for field use only) via `evaluate_tuning_grid_methodology.py`.
Took ~55 min for all 144 combos.

**Result: every single one of the top 15 combos is `method: relative`** — no
`ockendon`/`ockendon_flipped` combo ever cracks the top 15 regardless of
beta/ema_alpha/flex_axis_capture/gravity_seed, confirming the ft_ratio
sweep's conclusion from a different angle.

**Best combo found:** `beta=0.041, ema_alpha=0.5, flex_axis_capture=True,
gravity_seed=True, method=relative` → mean **14.84°**, median **10.98°**,
2/53 under the 5° goal.

**Important side-finding: the currently-persisted live config is not this.**
`imu_calibration_config.load_config()` (what the live app actually runs)
returns `beta=0.08, ema_alpha=0.1, flex_axis_capture=True, gravity_seed=True`
— auto-tuned 2026-07-31 from a *single* trial
(`PID_tester_LEG_Right_MS_TRIAL_5_imu.csv`) via `score_waveform()`'s
self-consistency heuristic, which has **no OptiTrack ground truth in the
loop at all**. That's also the config the 16.8°/14.0° baseline used (both
`batch_imu_vs_optitrack_rmse.py` and this comparison's baseline load
`load_config()` by default). The grid-search-identified combo above is a
**real, ~12% mean / ~22% median RMSE reduction** over what's actually
running today, found using real ground truth across 53 trials instead of
one trial's self-consistency score.

**Optimization idea:** persist the grid-search-found combo
(`beta=0.041, ema_alpha=0.5, flex_axis_capture=True, gravity_seed=True`) as
the new default config, and/or wire `imu_calibration_tuner.tune()`'s
persisted-config path to score candidates against real OptiTrack RMSE (when
available) instead of only `score_waveform()`'s ground-truth-free heuristic
— the single-trial auto-tune from 2026-07-31 has had no real-RMSE validation
since it was persisted.

## Summary: lowest RMSE found

**Winner: `relative` method, `beta=0.041, ema_alpha=0.5, flex_axis_capture=True,
gravity_seed=True, use_mag=False`** (i.e. today's method, re-tuned) —
mean **14.84°**, median **10.98°**, 2/53 under the 5° goal. This is not the
externally-pasted methodology winning; it's this project's own existing
approach, just with better-than-currently-persisted filter parameters found
via real ground-truth search instead of the single-trial heuristic tune
currently live.

| Rank | Config | Mean | Median | Under 5° |
|---|---|---|---|---|
| 1 | relative, tuned (beta=0.041, alpha=0.5) | **14.84°** | **10.98°** | 2/53 |
| 2 | relative, +magnetometer | 14.89° | 11.11° | 2/53 |
| 3 | relative, currently-persisted config (beta=0.08, alpha=0.1) | 16.83° | 13.99° | 1/53 |
| 4 | ockendon_flipped, best realistic ft_ratio (~1.3) | 28.5° | 27.9° | 1/53 |
| 5 | MediaPipe/vision, full model, untuned | 36.0° | 33.3° | 0/37 |
| 6 | ockendon_flipped, as originally wired (buggy) | 59.9° | 54.1° | 0/53 |
| 7 | ockendon, bug-fixed | 112.7° | 107.8° | 0/53 |
| 8 | ockendon, as originally wired (buggy) | 150.5° | 149.7° | 0/53 |

## MediaPipe/vision pipeline comparison (2026-08-17, prompted by a pasted
"developer spec" file with unverifiable citations)

The pasted `pendulastic-developer-spec.md` file (untracked, no bibliography —
its bracketed citation numbers don't resolve to any real source list) turned
out to contain no new IMU-specific RMSE-reduction technique beyond the
already-tested Ockendon model. Its one genuinely different idea, Section 2.2
("2D image-plane keypoint goniometry" via MediaPipe hip/knee/ankle landmarks
+ `atan2`), is a real technique this project already partially implements —
so instead of chasing the unverifiable IMU claims further, tested that vision
pipeline's own real RMSE-vs-OptiTrack for comparison.

Used `rmse_pipeline_common.py`'s existing discovery/scoring/caching
(`discover_video_trials()`, `score_mediapipe_candidate()`, "full" model,
vis_thresh=0.5 — the variant this project's `batch_mediapipe.py` already
extracts by convention) on the intersection of trials with both a video and
IMU log (45 trials). The run was externally interrupted at 37/45 trials
(background process killed, not by design) but the aggregate is already
stable at that sample size:

**MediaPipe (full, vis_thresh=0.5): mean 36.0°, median 33.3°, 0/37 under the
5° goal.** Substantially worse than the IMU `relative` method at any config
tested (14.8°-16.8°), and worse than every Ockendon variant except the
originally-buggy unflipped one. This vision config has never been tuned —
`rmse_pipeline_common.load_best_config()` returns no promoted MediaPipe
candidate yet, so there may be headroom via `vis_thresh`/model-variant
sweeping the same way the IMU grid search found headroom — but the untuned
starting point is far behind IMU, not ahead of it.

**Optimization ideas for MediaPipe, if revisited:** sweep `vis_thresh` and
`model_variant` (lite/full/heavy) the same way `evaluate_tuning_grid_methodology.py`
did for IMU, now that landmark extraction is cached under
`sweep_cache/landmarks/` and won't re-run inference on a re-sweep; investigate
whether the 2D image-plane `atan2` formula (Section 2.2) — not yet
implemented/tested standalone — does better than whatever angle computation
this project's existing MediaPipe path currently uses.

## Bias-vs-scatter decomposition (2026-08-18) — the real lead

RMSE decomposes exactly as `RMSE² = bias² + residual_std²` (bias = mean
signed error, already computed per-trial in `imu_vs_optitrack_rmse.csv`).
Ran this decomposition on all 53 trials (winning config):

- Raw RMSE: mean 14.84°, median 10.98°
- **Constant per-trial bias alone explains 67.6% (mean) / 76.0% (median) of
  the RMSE.**
- Bias-removed residual (genuine tracking scatter): mean **9.71°**, median
  **7.37°** — 36/53 trials under 10°, 12/53 under 5° (vs. 2/53 raw).

**This is the strongest lead of the whole investigation.** Most of the
measured error isn't random tracking noise or drift accumulating during the
swing — it's a roughly *constant offset per trial*, which is exactly the
signature the P16 alternating-trial auto-tare investigation (session start,
2026-08-17) already found: a bad/inconsistent zero-calibration reference
pose, not a fusion-quality problem. The methodology comparisons above (AHRS
tuning, Ockendon model, magnetometer, MediaPipe) were all attacking
*residual tracking accuracy* — but ~2/3 to 3/4 of the error is calibration
bias, a completely different problem with a completely different fix.

**Update 2026-08-18 — this lead did not pan out into an implementable fix.**
Two follow-up diagnostics were run:

1. **Live vs. offline calibration** — could not be tested. The historical
   53-trial dataset was recorded with an older tool (`master_app.py`) whose
   `Trial_N_imu.csv` files store only raw per-segment Euler angles
   (`hip_pitch_deg`, `prox_pitch`, `dist_pitch`, ...), not a fused
   `knee_angle_deg` — there is no "live calibrated output" saved anywhere in
   this dataset to compare against the offline-replay numbers this whole
   investigation has been reporting. Answering this needs a fresh recording
   with today's app and OptiTrack running together.

2. **Release-anchored rezero** (per the literature agent's suggestion:
   re-zero both IMU and OptiTrack at their own independently-detected
   release instants) — real but modest and uneven. Excluding one outlier
   trial, mean/median RMSE were essentially a wash (15.92°→16.10° mean,
   11.34°→11.39° median), while the number of trials under the 5° goal
   tripled (2→6/60). Not the clean win the idealized bias-decomposition
   estimate suggested.

Chasing that one outlier trial (`Participant_15\Left\pre\Trial_1`, 28.5° RMSE)
to find a generalizable "bad zero-capture" signature **did not generalize**:
properly time-aligned (an earlier pass compared unaligned timelines — a
mistake caught and corrected), that trial's IMU gyro shows no significant
motion at all until t=2.49s, well after OptiTrack's independently-detected
release — a per-trial data/mounting problem, not an algorithmic zero-capture
bug. Checking whether "how late is the first real gyro motion" distinguishes
good from bad trials elsewhere: it doesn't. Two of the original catastrophic
P16 trials (49.6°/49.7° RMSE) and two genuinely good ones from the same
participant/session (5.5°/6.7° RMSE) all show their first real motion at
nearly the same time (~t=2.5-3.0s). No clean, general, implementable
signature for "this zero-capture is bad" was found across trials investigated.

**Conclusion:** the bias is real and substantial (confirmed several
independent ways), but a targeted algorithmic fix for it was not found
within this investigation's scope. Decision: stop here rather than
implement a fix on an unproven mechanism. The two other paths considered
and not taken were (a) a more rigorous multi-signal correlation analysis
across all 53 trials (first-motion timing, hold-buffer duration,
`sync_offset_s`, noise floor, vs. bias magnitude) and (b) a fresh, carefully
controlled recording with OptiTrack to establish whether the remaining gap
is protocol/hardware rather than software-fixable at all. Either is a
reasonable next session's starting point if this is revisited.

## Not yet tried

- **9-axis EKF** (replacing Madgwick) — the spec's other major claim. Biggest
  implementation lift of everything in the spec. Given every other
  spec-inspired change (tibial-inclination model, magnetometer fusion) has
  either underperformed or been a wash on this real dataset, and the actual
  best result so far came from re-tuning the *existing* method rather than
  adopting anything new from the spec, an EKF rewrite is a large lift with
  no evidence yet that it would pay off here — recommend deferring unless
  specifically requested.
- **Sample-rate sensitivity** (100Hz vs coarser) — can be simulated by
  decimating existing raw logs rather than needing new recordings. Not yet
  run.
- Spec components not relevant to IMU-angle RMSE (Schmitt-trigger vision
  tracking, image-space leg gating, Popović scoring) intentionally excluded —
  those affect a different subsystem (MediaPipe tracking / downstream
  scoring), not IMU fusion accuracy.

## Recommended next action — DONE (2026-08-18)

Persisted `{beta: 0.041, ema_alpha: 0.5, flex_axis_capture: true,
gravity_seed: true, method: "relative"}` to the live `imu_calibration_config.json`,
replacing the single-trial, ground-truth-free auto-tune from 2026-07-31.
Verified by re-running `batch_imu_vs_optitrack_rmse.py` against the new
config: mean=14.843°, median=10.977°, 2/53 under the 5° goal — matches the
grid search prediction exactly.

**Known risk, not yet addressed:** `pendulastic_app.py`'s `_run_imu_tuning` →
`imu_calibration_tuner.tune_and_persist()` runs automatically after every
new recording and can silently overwrite this config the moment it finds
any `score_waveform()`-passing candidate (that heuristic has no OptiTrack
ground truth in the loop at all, unlike this session's grid search). The
persisted config's `passes` field is `False` under that heuristic, so it is
one auto-tune away from being silently replaced. If more recordings happen
before this is addressed, re-check `imu_calibration_config.json` against
this document's chosen values before trusting it's still active.

## Investigation closed here (2026-08-18)

Per user decision: stopping with the config win as the concrete deliverable,
rather than pursuing an unproven bias-mechanism fix further. See the "Update
2026-08-18" note under the bias-decomposition section above for why the
auto-tare/zero-capture fix was not implemented despite being the
investigation's strongest lead going in.
