# Rig phone angle vs the app's own pipeline

2026-11-10. Branch `eval/rig-phone-angle-vs-app`, cut from `origin/feat/synthetic-trial-generator` (PR #92, 11c048d).
Driver: `neuromech/eval_phone_angle_paths.py`. Everything here is simulation; no phone and no rig were involved.

## Question

Two code paths turn a phone recording into a knee angle and a score:

1. **The app** (`webapp/src/worker.js` -> wasm core: AHRS, hold/release state machine, 50 ms ticks + EMA 0.3, scorer).
2. **Rig scoring** (`neuromech/session.py`): `_phone_angle` integrates raw gyro-y, `_align_phone` puts it on the
   encoder clock from the three sync pulses and removes a bias, `_score_trace` scores it at 100 Hz with the release
   taken from the command.

How far can 2 sit from 1, and which should rig scoring use?

## Short answer

- **Measured.** With the swing on gyro-y, path 2 reproduces the encoder truth: angle RMSE 0.3-1.1 deg over the
  swing, |dPT7| <= 0.016, A0 within 1.0 deg, 49 of 56 runs in the truth's band.
- **Measured.** The app, on a rig recording, is not a usable score as things stand. Fed the recording the bench
  procedure produces (phone started before the command), it takes the first sync pulse as the release in 64 of 64
  runs and gets the truth's band in 1 of 8 scenarios. Fed the same samples from after the pulses, it scores 6 of 8
  scenarios; on those its angle is right to 0.7-1.7 deg against the motion smoothed as the app smooths it, but its
  PT7 sits **0.06 to 0.15 above the encoder truth** and its band matches the truth in 17 of 42 runs.
- **Measured.** Almost none of that gap is angle error. The same true motion, ticked and smoothed as the app does
  it and scored with the release given, is already 0.07-0.15 above the 100 Hz score. The app is within 0.018 of that.
- **The two paths differ by 0.056-0.146 PT7 (median 0.063)** and agree on band in 24 of 42 runs where both score.
- **Recommendation.** Keep path 2 for rig scoring, but stop calling its output "what the app would report": it
  is not. Add path 1 as a second, separately labelled column (offline replay from after the sync pulses), because
  the difference between the columns is the thing the rig exists to measure. Details at the end.

## Method

For each scenario, sensor and mount, one rig run is synthesised and recorded once:

| piece | how |
|---|---|
| motion (truth, T) | the scenario's rig command from `build.rig_setpoint` (sync pulses, 4 s min-jerk lift, 3 s hold, swing) through `dryrun.plant_response` (the identified plant, 6.35 Hz, zeta 0.23), 1.5 s idle before and 1 s after. No Player staircase, no encoder noise. This trace is used as the encoder log **and** as the truth. Its score is `session._score_trace`, i.e. what `session.score` reports as `enc_`. |
| phone | `phone_trial.synthesize` on that motion (`lift=False, rest_s=0, hold_s=0`, so the generator adds nothing but a 7 s quiet tail), started 0.8 s before the log, written as the app's `.jsonl`. `Phone()` is the "real" sensor (60 Hz, 1.5 ms jitter, bias, noise, 0.25 m lever arm), `Phone.ideal()` the perfect one; the mount is set separately. |
| path 2 (P2) | `_phone_angle` -> `_align_phone(te, log, t_cmd0, tp, wp)` -> `_score_trace(tq - t_cmd0, ang, rest, t_rel, t_hold)`, the calls `session.score` makes, with the truth trace standing in for the LabVIEW log. |
| path 1, whole file (P1f) | `phone_trial.run_app` on the recording as written. This is the bench procedure in `neuromech/README.md` section 3: start the phone, start the log, run the Player. |
| path 1, after the pulses (P1) | `run_app` on the same lines from 0.3 s after the last sync pulse: rest, lift, hold, swing, tail. |
| app footing (P1T) | the true angle through `phone_trial.app_ticks` (50 ms hold, EMA 0.3) and `compute_pt_params` with the release given. P1 - P1T is the app's pipeline; P1T - T is the app's tick rate and smoothing. |

Mounts are named for the sensor axis the swing lands on: `y` (0, 0, 90), `y+tilt` (6, -4, 99), and the generator's
own two, `x` (0, 0, 0, what `--ideal` uses) and `x+tilt` (6, -4, 9, the generator's default). The y mounts exist
because `_phone_angle` reads `v[1]` and nothing else; `y+tilt` is the generator's default tilt turned 90 deg about z.

Scenarios: A-P0, A-P1, A-P1p, M-tone_L2, M-tone_L4, B-claspknife, B-clonus, built-in pendulum. **A-P2 and A-P3 do
not exist on this scorer** (`build` reports both bands unreachable; rising-branch peak PT7 0.42), so the A group
stops at 1+. Runs: 8 scenarios x 2 sensors x 4 mounts at seed 11 (64), plus seeds 12-14 on the real sensor,
`y+tilt` (24). Bands are `pt_to_mas(PT7)` as `session._summ` uses, "refused" when not interpretable; the app's
three zones are given beside them. Wasm built locally from this branch (build id 07e7a4caaa90).

Reproduce:

```bash
(cd webapp && npm run build:wasm) && git checkout -- webapp/src/build-id.js
python -m neuromech.build                         # 11 min here
python -m neuromech.eval_phone_angle_paths        # 8 min; rows in neuromech/out/eval_phone_angle_paths/results.csv
python -m neuromech.eval_phone_angle_paths --sensors real --mounts y+tilt --seeds 12,13,14 --out neuromech/out/eval_phone_angle_paths_seeds
```

## Results

### Swing on gyro-y (`y`, `y+tilt`; both sensors; 7 runs per scenario, 56 in all)

PT7 as min-max over the 7 runs. RMSE in degrees over the swing, worst of 7. "P1 rmse" is against the motion itself
/ against the motion smoothed as the app smooths it.

| scenario | T PT7 (band) | P2 PT7 (band) | P2 rmse | P1T PT7 (band) | P1 PT7 (band) | P1 rmse | A0: T / P2 worst / P1 range |
|---|---|---|---|---|---|---|---|
| A-P0 | 0.053 (0) | 0.052-0.054 (0) | 1.04 | 0.121 (0 or 1) | 0.113-0.123 (0 or 1) | 6.8 / 1.7 | 53.7 / -1.0 / 49.7-51.9 |
| A-P1 | 0.171 (1) | 0.187-0.188 (1) | 0.88 | 0.246 (1) | 0.244-0.250 (1) | 5.0 / 1.7 | 49.1 / -0.9 / 45.2-46.1 |
| A-P1p | 0.350 (1+) | 0.350 (1+) | 0.82 | 0.388 (1+) | 0.600 (**refused**) | 3.6 / 1.5 | 45.4 / -0.8 / 5.9-6.5 |
| M-tone_L2 | 0.204 (1) | 0.219-0.220 (1) | 0.86 | 0.275 (1) | 0.277-0.281 (1 or 1+) | 4.6 / 1.7 | 48.4 / -0.9 / 44.6-45.5 |
| M-tone_L4 | 0.416 (1+) | 0.413-0.416 (1+) | 0.83 | 1.435 (refused) | 0.600 (**refused**) | 3.4 / 1.5 | 44.3 / -0.8 / 7.0-7.5 |
| B-claspknife | 0.116 (0) | 0.128-0.131 (**1**) | 0.93 | 0.196 (1) | 0.190-0.196 (1) | 5.8 / 1.0 | 55.2 / -1.0 / 52.0-53.3 |
| B-clonus | 0.075 (0) | 0.075-0.077 (0) | 1.07 | 0.219 (1) | 0.216-0.223 (1) | 5.0 / 1.7 | 45.4 / -0.8 / 41.5-42.3 |
| pendulum | 0.050 (0) | 0.050-0.065 (0) | 1.05 | 0.130 (1) | 0.128-0.133 (1) | 7.0 / 1.0 | 55.2 / -0.9 / 52.1-53.2 |

Headline distances over those runs:

| pair | angle | PT7 | band agreement | zone agreement |
|---|---|---|---|---|
| P2 vs truth | RMSE 0.32-1.07 deg (median 0.73) | <= 0.016 (median 0.002) | 49 / 56 | 56 / 56 |
| P1 vs truth (scored runs) | RMSE 4.6-7.0 deg raw; 0.7-1.7 deg on the app's footing | 0.061-0.148 above | 17 / 42 | 28 / 42 |
| P1 vs its own footing P1T (scored runs) | as above, 0.7-1.7 deg | <= 0.018 (median 0.002) | 35 / 42 | 42 / 42 |
| P1 vs P2 (scored runs) | - | P1 higher by 0.056-0.146 (median 0.063) | 24 / 42 | 28 / 42 |
| P1 vs truth (all runs) | - | 14 of 56 refused | 17 / 56 | 28 / 56 |

A0: path 2 within 1.0 deg of truth (low by 1.6-1.9 % on `y+tilt`); the app low by 2.7-4.0 deg.
Release: the app's live detector fired 0.060-0.077 s after the planted release, and its scorer put the release
0.108-0.110 s after it, on every run it scored. Path 2 does not detect a release; it takes it from the command, and
its clock sat 10.3-13.3 ms early.

**Band changes between paths**: A-P0 (path 2 always 0; app 0 or 1, it straddles the 0.12 edge), M-tone_L2 (app 1+
in the ideal-sensor runs, 1 otherwise), B-clonus and pendulum (path 2 band 0, app band 1, every run), and A-P1p and
M-tone_L4 (path 2 1+, app refuses). A-P1 and B-claspknife agree between paths, and for B-claspknife both disagree
with the truth.

### Swing on gyro-x (`x`, `x+tilt`: the generator's own mounts; 32 runs)

| mount | path 2 | app (P1) |
|---|---|---|
| `x` | sync refused, 16 / 16 (`signal never rises` on the ideal sensor, `found 1 pulse edges` on the real one). No angle. | unchanged from the y mounts (PT7 within 0.002) |
| `x+tilt` | status **"ok"**, sign +, in 16 / 16, with A0 6.9-9.2 deg for a true 44-55 deg (0.155-0.166 of it), RMSE 8.3-11.9 deg, every run refused by the scorer's excursion gate | unchanged |

### The whole recording, as the bench procedure makes it (P1f; 64 runs, all mounts)

The app armed on the stillness before the command and declared the release 8.97 s before the planted one, at the
first sync pulse, in 64 of 64 runs. What it then scored, by scenario (identical across mounts and sensors):

| scenario | result |
|---|---|
| A-P1, M-tone_L2 | scored, scorer's release 0.11 s after planted, PT7 0.260-0.273 and 0.292-0.301 (truth 0.171, 0.204); band 1 and 1+ |
| A-P0 | refused, "unscorable" |
| A-P1p, M-tone_L4, B-claspknife, B-clonus, pendulum | scorer's release 6.0-6.1 s early (inside the lift); "uninterpretable", N = 0 |

## Where the two paths agree

- **The angle itself.** Both pipelines recover the motion to about a degree when given a fair input: path 2
  0.3-1.1 deg RMSE against the encoder, the app 0.7-1.7 deg against the same motion on its own footing. The AHRS and
  the raw integration are not where the difference is. (Measured.)
- **Frequency**: f within 0.02 Hz of truth on both paths where both score.
- **The three-zone verdict from path 2** matched the truth in 56 of 56 runs.

## Where they diverge, and why

Ordered by size of effect on the score.

**1. Tick rate and smoothing: +0.07 to +0.15 PT7. Measured.** The app scores a 20 Hz zero-order-held, EMA-smoothed
trace; path 2 scores 60 Hz samples interpolated to 100 Hz, unsmoothed. On identical true motion with the release
given, the app's footing lowers `phi_max_ratio` (0.60 -> 0.46 on A-P0), `omega_max_n` (5.0 -> 4.0) and `R2n` (1.09
-> 0.98), and PT7 rises from 0.053 to 0.121. B-clonus is the extreme: `omega_max_n` 8.0 -> 2.6 because the 6 Hz
beats are gone at 20 Hz, PT7 0.075 -> 0.219. The same lag is why the app's raw angle RMSE is 4.6-7.0 deg while its
RMSE on its own footing is 0.7-1.7. M-tone_L4 is refused on the app's footing even with the release given (N = 0,
PT7 1.435). This is the README section 7 "about 0.05 above" gap, measured here at 0.07-0.15 on rig motion.

**2. Release detection in the app on a rig recording. Measured; mechanism inferred from the code.**
 - *Live release on the first sync pulse (64 / 64 on the whole file).* 1.5 s of stillness arms the hold, and a
   5 deg pulse through the plant exceeds the 1.0 rad/s capture rate. The zero pose is then the rest pose, not the
   hold, and the trace the scorer sees is not a release from extension.
 - *Scorer's release inside the lift (A-P1p and M-tone_L4, 14 / 14 after-pulse runs).* Live release was correct
   (+0.077 s) but `scoring.rs::detect_release` returned a point 6.0-6.1 s early, A0 came out at 6-7.5 deg and the
   trial was called uninterpretable. The generator's own trial of the same two scenarios scores with the release
   0.08-0.09 s late, so this is specific to the rig preamble. Inferred cause: `lift_hold_end` accepts a
   lift-and-hold only if the lift reaches its plateau within `LIFT_REACH_MAX_S` = 2.5 s and the limb later swings
   back through the starting angle. The rig lift takes 4 s (`motor_reference.RAMP_S`), and starts from the PASSIVE
   rest, which a toned limb that settles above it may never return through. Not confirmed by stepping the Rust.
 - Minimal reproduction of both:
   `python -m neuromech.eval_phone_angle_paths --sids A-P1p --sensors ideal --mounts y` (P1f: live release -8.97 s;
   P1: live +0.077 s, scored -6.09 s, A0 6.5 deg), against
   `python -m neuromech.phone_trial --sids A-P1p` (scored release +0.081 s, PT 0.402).

**3. Mount misalignment. Measured.** The app is mount-invariant (PT7 within 0.002 across four mounts). Path 2
reads one sensor axis, so its angle is the true angle times the cosine between the knee axis and sensor y: 0.981-
0.984 on `y+tilt` (A0 low by 0.8-1.0 deg), 0.155-0.166 on `x+tilt`, nothing on `x`. PT7's ratios are scale-free, so
a modest tilt does not move the score (0.052 on both y mounts). The hazard is the large one: on `x+tilt`
`_align_phone` returns normally and `session.score` would print `ok (sign +1, ...)` for an angle one sixth of the
truth. In these runs the 25 deg excursion gate then refused every one, but nothing in path 2 itself notices, and a
mount about 60 deg off axis would halve the angle and still pass the gate on a 55 deg swing (inference from the
cosine, not run).
   Reproduction: `python -m neuromech.eval_phone_angle_paths --sids pendulum --sensors ideal --mounts x+tilt`
   -> `P2_status ok`, `P2_A0_deg` 8.6 against 55.2.

**4. Path 2's clock runs 10-13 ms early, and the scorer's N is on a knife edge. Measured.** `_align_phone`'s lag
was -10.3 ms with a perfect sensor and -10.3 to -13.3 ms with the real one, every run. That is where path 2's
whole PT7 error comes from: shifting the TRUTH trace 10 ms early, with no phone involved, drops N by 0.5 and moves
PT7 by +0.012 to +0.015 on A-P1, B-claspknife and the pendulum (0.1714 -> 0.1863, 0.1159 -> 0.1280, 0.0500 ->
0.0639), and resampling the truth to 60 Hz alone does the same to A-P1. On B-claspknife that crosses the 0 / 1
edge in 7 of 7 runs. Inferred cause of the offset: the phone's pulse edges are found on 60 Hz samples, so each
is late by up to one sample, against encoder edges at 1 kHz.
   Reproduction of the scorer half, no phone: score `rig_run('A-P1', man)` with `session._score_trace(t - 0.010, ...)`
   against `_score_trace(t, ...)`.

**5. The app's zero pose is taken after the leg has started to fall: A0 low by 2.7-4.0 deg. Measured.** The app
captures its zero on the first sample past 1.0 rad/s. The hold then reads low and the rest high (end-of-swing error
+0.7 to +2.0 deg on every run). The generator already reports this as "leg fallen at zero pose". Path 2 has no such
term because it zeros on the stillness before the pulses.

**6. Gyro bias and drift: small in both. Measured.** Path 2's still-window median gave -0.022 to -0.052 deg/s
against a planted -0.057 on that axis, leaving at most 0.035 deg/s; its end-of-swing error was -0.13 to -0.96 deg
with the real sensor and -0.20 with the ideal one. The app showed no drift beyond the zero-pose offset above.
Neither moved a score by itself.

## Recommendation

1. **Keep path 2 as the rig's phone score for now.** It is the only one of the two that works on a rig recording
   today, it tracks the encoder to 0.016 PT7, and it is on the same footing as the encoder and manifest numbers it
   is compared with.
2. **Do not read path 2 as the app.** `session.py` and README section 4 describe it as "what the app itself can
   see". Measured here, the app reports 0.06-0.15 PT7 higher on the same recording and a different band in 18 of
   the 42 runs where both score. A rig session that passes on path 2 has validated a phone gyro and the Python
   scorer, not the shipped pipeline.
3. **Add path 1 to `session.score` as its own columns** (`app_*`), by cropping each recording to just after the
   last sync pulse (the encoder alignment already gives that instant) and replaying it with
   `phone_trial.run_app`. Judge it against the encoder trace put on the app's footing (`app_ticks`), not against
   `enc_*`, and report `app - enc` separately as the footing gap. The numbers the app shows on screen during a rig
   run should not be used: with the phone started before the command they come from a release on a sync pulse.
4. **Before trusting either in a session**, three things need an owner decision or a fix, none made here:
   the app scorer's release on the 4 s rig lift (item 2), path 2's silent acceptance of an off-axis mount (item 3:
   either read the dominant gyro axis or refuse when gyro-y carries a minority of the rotation), and path 2's
   10 ms early clock together with the scorer's half-count step in N (item 4), which is what moves B-claspknife
   across a band.
5. Which footing is *right* (20 Hz smoothed or 100 Hz) is not answered here. The rig can answer it; this report
   only shows the two footings give different bands on the same motion in 33 of 56 runs.

## Limits

- **Simulated physics is a vertical-plane swing.** The generator builds gravity into the sensor frame as for a
  hanging leg. The real rig arm is reported to swing in a horizontal plane with the phone flat, where the
  accelerometer carries no angle information and the app's angle is gyro only. That case was **not run**: it
  needs a change to the generator, which was out of scope. The app's AHRS result here should not be assumed to
  carry over to the horizontal rig.
- The encoder is the truth trace: no Player staircase, no encoder noise, no tracking error. Path 2's numbers are
  therefore a lower bound on a real session's (`dryrun` reports phone vs encoder |dPT7| up to 0.015 with those in).
- Path 2 was run with the truth trace as its encoder log, not a LabVIEW `.bin`; `read_labview_bin` and
  `ilc.locate` were not exercised (the command start was given exactly).
- One realistic sensor model, four seeds on one mount, one seed elsewhere. A real phone's errors, the browser's
  event delivery and the 3-sample batch cadence (`--batch 3`) were not tested.
- A-P2 and A-P3 could not be run: they are not in the scenario library on this scorer.
- The causes marked "inferred" (items 2 and 4, and the 60 deg example in item 3) come from reading the code and
  from which scenarios fail, not from an isolating experiment.
