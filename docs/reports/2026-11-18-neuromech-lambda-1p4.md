# Severity axis at lambda 1.0 to 1.4: what the scorer and the app do past the PT7 peak

2026-11-18. Scorer `pt5trough-ref-2026-11-16`, core 0.4.0, model hash `f04d1a2e2c72`. Simulation only:
nothing here was played on the rig.

The supervisor asked for a trajectory near lambda 1.4 to see how the phone and the scorer perform there.
Lambda is the one knob of the published severity axis (`scenarios.severity`: tone 2.0λ N·m, k_f 0.75λ,
k_df 0.025λ). PT7 peaks at 0.4035 at lambda 0.80 and falls after that, so every row below from lambda 1.0
on is on the falling branch: a stiffer leg, graded milder.

## Short version

- **Reference scorer, played 1:1:** lambda 1.4 scores PT7 0.170, band 1. Lambda 1.0 and 1.2 are also band 1
  (0.257, 0.224). The 1+ preset (lambda 0.774) is less stiff and scores higher (0.360).
- **The rig cannot play it 1:1.** The release is 55° from the passive rest; the guard's limit is ±40°. The
  largest scale that passes is 0.70 (commanded and predicted delivered peak 38.5°).
- **At 0.70 the scorer refuses it.** The excursion A0 drops from 36.1° to 25.3°, under the scorer's 30° floor
  ("Insufficient excursion"). The same happens at lambda 1.0 and 1.2. Grading lambda 1.4 needs a scale of
  0.84, a 46° release, which the guard refuses. The guard and the floor are both unchanged.
- **The app never finds the release at lambda 1.4**, at either scale, with or without sensor noise. The leg
  catches 6° into the drop after 0.2 s, having reached only 55°/s; the app sees the hold being disturbed,
  not a release, and returns "unscorable". At lambda 1.0 and 1.2 played 1:1 the app agrees with the
  reference to 0.003 PT.

## What limited lambda before this change

- **Python: nothing.** `scenarios.severity(lam)` takes any value; the S group already had 1, 1.5, 2, 3, 6.
  The MAS ladder (`build.calibrate_ladder`) bisects only the rising branch, 0 to the peak, by design.
  `profiles._LADDER_LAMBDA` stops at 0.80 and `dryrun.py` plays ladder presets; neither caps lambda.
- **Designer: it had no lambda at all.** Its model (`PendulumModel.cs`) is a kinematic stand-in (a linear
  damped pendulum with a velocity reflex), not the De Groote model. Its own "severity" axis is a 0-to-1 mix
  of damping, reflex gain and tone, and the MAS mode searches that. The only way to see a library swing was
  *Open file*.

## Results

Trajectory, played 1:1 (model angle: 0 = full extension, passive rest −55.0°):

| lambda | first reversal, fastest flexion before it | deepest flexion | settles at | A0 | branch |
|---|---|---|---|---|---|
| 0.774 (A-P1p) | −47.2° at 0.53 s, 135°/s | −48.4° at 1.46 s | −44.6° | 44.6° | rising |
| 0.80 | −45.2° at 0.53 s | −47.8° at 1.41 s | −44.2° | 44.3° | peak |
| 1.0 (S-lam1) | −29.4° at 0.50 s, 88°/s | −50.0° at 1.16 s | −41.5° | 41.5° | falling |
| 1.2 (S-lam1.2) | −14.3° at 0.38 s, 66°/s | −54.2° at 1.01 s | −38.8° | 38.8° | falling |
| 1.4 (S-lam1.4) | −6.2° at 0.21 s, 55°/s | −54.1° at 0.85 s | −36.0° | 36.1° | falling |
| 1.5 (S-lam1.5) | −4.8° at 0.16 s, 51°/s | −52.7° at 0.81 s | −34.6° | 34.7° | falling |

Up to the peak the first reversal is the bottom of the first drop. Past the peak it is an early catch: the
short-range stiffness and tone stop the leg 5° to 30° into the drop, it then falls almost to the passive
rest, and it settles 13° to 20° above it.

Reference scorer, played 1:1 (`neuromech.build.score`, the manifest's expected values):

| lambda | PT7 | band | R2n | N | phi_max_ratio | omega_max_n | f (Hz) | area_ratio | edge distance |
|---|---|---|---|---|---|---|---|---|---|
| 0.774 | 0.360 | 1+ | 0.650 | 2.5 | 0.117 | 3.02 | 0.971 | 0.166 | middle of 1+ |
| 0.80 | 0.403 | 1+ | 0.627 | 2.0 | 0.108 | 2.92 | 1.020 | 0.176 | 0.037 below 0.44 |
| 1.0 | 0.257 | 1 | 0.835 | 3.5 | 0.263 | 2.11 | 1.024 | 0.105 | 0.023 below 0.28 (allowlisted) |
| 1.2 | 0.224 | 1 | 0.871 | 3.5 | 0.323 | 2.89 | 0.906 | 0.043 | 0.056 below 0.28 |
| 1.4 | 0.170 | 1 | 0.935 | 4.0 | 0.408 | 3.73 | 0.902 | 0.027 | 0.050 above 0.12 |
| 1.5 | 0.162 | 1 | 0.948 | 4.0 | 0.425 | 3.90 | 0.902 | 0.026 | 0.042 above 0.12 |

Rig guard (`rig_guard.check_trajectory`, ±40°, 6° per 10 ms write, predicted delivered peak):

| lambda | 1:1 | scale needed | at 0.70: commanded / predicted peak | in `library_x0.70/` | scale needed to be graded |
|---|---|---|---|---|---|
| 1.0 | refused (55° release) | 0.70 | 38.5° / 38.5° | yes | 0.73 (40.1° release: refused) |
| 1.2 | refused | 0.70 | 38.5° / 38.5° | yes | 0.78 (42.9°: refused) |
| 1.4 | refused | 0.70 | 38.5° / 38.5° | yes | 0.84 (46.2°: refused) |

App against reference. The app column is the synthetic phone recording (60 Hz, default noise, seed 11)
replayed through the shipped worker and a fresh wasm build of this commit; "ideal" is the same with a perfect
sensor. The app shows zones, not bands.

| lambda | scale | reference | app (default phone) | app (ideal sensor) |
|---|---|---|---|---|
| 0.774 | 1:1 | 0.360, band 1+ | **1.232, impaired** (N 0.5, f not measured) | 0.366, impaired |
| 0.774 | 0.70 | 0.360, band 1+ (A0 31.2°) | 1.266, uninterpretable (A0 27.0°) | 0.362, uninterpretable (A0 27.4°) |
| 1.0 | 1:1 | 0.257, band 1 | 0.259, borderline (A0 38.9°) | 0.252, borderline |
| 1.0 | 0.70 | refused, A0 29.1° | 0.234, uninterpretable (A0 22.6°) | 0.205, uninterpretable |
| 1.2 | 1:1 | 0.224, band 1 | 0.221, borderline (A0 35.1°) | 0.222, borderline |
| 1.2 | 0.70 | refused, A0 27.2° | **release not found, unscorable** | release not found |
| 1.4 | 1:1 | 0.170, band 1 | **release not found, unscorable** | release not found |
| 1.4 | 0.70 | refused, A0 25.3° | **release not found, unscorable** | release not found |
| 1.5 | 1:1 | 0.162, band 1 | release not found, unscorable | release not found |

## Reading the app results

- **Release detection fails where the pre-catch speed is under about 57°/s.** That is the app's capture
  threshold (`FLEX_CAPTURE_THRESHOLD`, 1.0 rad/s). It is crossed at lambda 1.0 (88°/s; 62°/s at 0.70) and
  at lambda 1.2 played 1:1 (66°/s), and not at lambda 1.2 at 0.70 (46°/s) or lambda 1.4 (55°/s; 39°/s at
  0.70). In the failing replays the app goes ready → moving → holding 0.16 s after the planted release and
  never reaches "released"; the real drop then starts from a hold the app no longer trusts. The match with
  the threshold is consistent across all nine rows, but the Rust state machine was not stepped through.
- **A0 reads 2° to 4° low in the app at 1:1** and 6° low at 0.70 (the known zero-pose lag: the leg has
  already fallen a little when the hold pose is taken). At 0.70 that pushes lambda 0.774 under the 30° floor
  in the app (27.0°) while the reference still grades it (31.2°).
- **The 1.232 at lambda 0.774 on the default phone is not from this change** (A-P1p is untouched): the app
  counts N 0.5 and cannot measure f. It is listed as a follow-up, not investigated here.
- Had the release been found at lambda 1.4, the scorer at the app's 60 Hz smoothed rate gives 0.199 for the
  true motion against 0.170 at 100 Hz (`truth.json`): a further 0.03.

## What this means for a rig test at lambda 1.4

As things stand the rig can play `S-lam1.4_x0.70.csv` safely, the encoder can be compared with the command,
and neither scorer will grade it: the reference refuses for excursion and the app does not find the release.
Getting a graded lambda 1.4 swing on the rig needs one of these, each an owner decision and none taken here:

1. Re-zero the rig command so the swing is centred. The 1:1 swing spans +55° to +0.9° of rig angle and never
   goes below zero; shifted by about −27° it would span +28° to −26°, inside ±40° with the guard unchanged.
   This changes the rig convention (0 = passive rest) and where the arm hangs relative to gravity.
2. A different limb geometry (shallower rest), which re-specifies the whole library (`Params.incline_deg`).
3. Accept that lambda ≥ 1.0 is an encoder-tracking probe only at 0.70.

The release-detection miss is independent of the rig: it is in the app, and it appears 1:1 too.

## What changed in the code

- `neuromech/scenarios.py`: `S-lam1.2` and `S-lam1.4`, titled "past the PT7 peak at lambda 0.80: falling
  branch". Existing ids and titles are unchanged; the library is 66 scenarios. Neither is near a band edge
  and neither duplicates another swing, so no allowlist entry was added.
- `neuromech/designer/DeGroote.cs`: a C# port of `model_rt.simulate` for the reference terms. The Designer
  has a fourth mode, *Severity axis (De Groote), lambda* (0 to 3), which plays that model at the rig
  amplitude scale and says which side of the PT7 peak the lambda is on. `test_designer.py` holds the C# trace
  to Python's within 1e-6° and its PT7, band, R2n and N to the library's at lambda 1.0, 1.2 and 1.4, the PT7
  peak to `build.rising_branch_peak`, and the 0.70 refusal and guard verdict to Python's.
- `rig_bundle.py` is unchanged: `library_x0.70/` picks the two new files up (57 of 66 pass the guard).

## How to reproduce

```
python -m neuromech.build
python -m neuromech.phone_trial --sids S-lam1,S-lam1.2,S-lam1.4          # needs npm run build:wasm in webapp/
python -m pytest neuromech/tests/test_designer.py
```

The 0.70 replays scale the swing about the release angle (`0.70 * theta`) before `phone_trial.synthesize`,
which is what the phone sees when the rig plays the scaled file.
