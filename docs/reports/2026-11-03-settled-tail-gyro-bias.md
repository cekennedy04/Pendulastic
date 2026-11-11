# Settled-tail gyro-bias re-estimate (plan B12): measured, not shipped

Date: 2026-11-03 · `python evaluate_settled_tail_gyro_bias.py OUT_DIR` (main @ 14d78b4)

## Question

The 2026-10-26 dry run found the phone's agreement with the encoder was bias-limited (plan §5a). Its estimate:
- estimating gyro bias only from the stillness before release leaves about 0.01–0.02 °/s;
- through the settled tail and area ratio, that moves PT7 by up to about 0.15.

B12 proposed re-estimating the bias in the settled tail before scoring. That would be a three-language scorer change (Python, Rust, C#), so it was measured on real phones first.

## Method

Every phone trial on this checkout was replayed twice: 179 trials, all discovered without exclusion scopes.

1. **Production:** `imu_calibration_tuner.replay_trial`.
2. **Same replay, re-estimated bias:** from release onward, the bias is replaced by one estimated from raw gyro, under the same rules production uses (stillness window, SNR rule, motion bound), in the last still window inside the scorer's neutral window (the last 25 % of the record).

The instrumented replay was checked to reproduce production exactly when no re-estimate is applied (179 of 179 identical).

Both replays were scored with `compute_pt_params(detrend=False)` and `mas_estimate`. Where a trial had a matched OptiTrack recording (78 trials, 8 people), that recording was scored the same way and used as the referee.

## Result

| | |
|---|---|
| Trials with a still tail to estimate from | 98 of 179 (69 none, 12 never zeroed) |
| Re-estimated minus pre-release bias | median **0.81 °/s** (IQR 0.51–1.68, max 3.2) |
| PT7 change | median 0.000, \|ΔPT7\| median 0.006, p90 0.034, max 0.19 |
| MAS band changed | 5 of 85 trials interpretable both ways (6 %); 2 more flip interpretability |
| A0 change | \|ΔA0\| median 4.1°, max **37.7°** |
| \|PT7 phone − OptiTrack\| | 0.150 before, 0.148 after; 40 trials closer, 38 further; 5 of 8 people closer |
| \|area_ratio phone − OptiTrack\| | 0.052 both; 41 closer, 37 further |
| MAS band agreement with OptiTrack | 21 % before, 23 % after |

## Reading

**The premise doesn't hold on real phones.**
- The bias seen in a "still" settled tail differs from the pre-release estimate by about 0.8 °/s. That's 40–80 times the dry run's synthetic residual, and far more than a phone gyro's bias drifts in seconds.
- So what the re-estimate picks up is mostly not bias. It's slow real motion of a leg that passes the stillness test, or a pre-release estimate that was already off.
- Correcting the tail treats that as error. On some trials it moves A0 by tens of degrees.

**It doesn't improve accuracy.**
- Against OptiTrack, the re-estimate is a coin flip: 40 trials closer, 38 further.
- MAS band agreement moves by 2 points, from a base of 21 %.
- The phone-vs-OptiTrack disagreement is dominated by something else. The single-sensor geometry over-read and the area-ratio drift artefact are already on record.

**Decision:** B12 is closed as measured and not shipped. No scorer, Rust or C# change was made.

A different design, such as a correction confined to the flex axis or a stricter stillness test, would need its own evidence of benefit against OptiTrack before anyone builds it. This run gives no reason to expect one.

## Limits

- **Referee:** OptiTrack shrinks angles (plan §5b), so it's an imperfect referee. The test here was only whether the re-estimate moves the phone *toward* it. It doesn't.
- **Coverage:** 179 trials, not the 206 of the scoped corpus. Main's discovery has no exclusion scopes, so it also drops trials whose only fault is a missing OptiTrack match.
