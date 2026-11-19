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
  scale the guard suggests is 0.70 (commanded and predicted delivered peak 38.5°; 0.72 is the last that
  passes outright, and at 0.72 lambda 1.0 is still under the excursion floor at 29.9°).
- **At 0.70 the scorer refuses it.** The excursion A0 drops from 36.1° to 25.3°, under the scorer's 30° floor
  ("Insufficient excursion"). The same happens at lambda 1.0 and 1.2. Grading lambda 1.4 needs a scale of
  0.84, a 46° release, which the guard refuses. The guard and the floor are both unchanged.
- **Re-centred, it plays at full size and is graded (owner decision, 2026-11-18).** Shifting the hold and
  the swing down by 28° puts the lambda 1.4 swing at +27.0° to −27.1° of rig angle (predicted delivered peak
  27.6°), inside the unchanged ±40° guard, and the score does not depend on the offset: PT7 0.170, band 1, A0
  36.1°, the 1:1 library's expected values. See "The re-centred render" below.
- **On the ideal model trace the app never finds the release at lambda 1.4**, at either scale, with or
  without sensor noise. The leg catches 6° into the drop after 0.2 s, having reached only 55°/s; the app sees
  the hold being disturbed, not a release, and returns "unscorable". At lambda 1.0 and 1.2 played 1:1 the app
  agrees with the reference to 0.003 PT. **This does not transfer to the rig as stated:** the arm the guard
  models overshoots the early catch (55 → 73°/s), so on the predicted arm motion the app does find the
  release at 1:1 and scores 0.14 to 0.18 depending on the plant. See "Second opinion" below.
- **Re-centred files and the phone app** (simulated). A 27° lift spread over the 1:1 lift's 4 s peaks at
  0.22 rad/s, under the app's 0.3 rad/s stillness guard, and the app ended the trial before the swing. Fixed
  in the render on 2026-11-19: the re-centred lift starts about 2 s later and rises at the 1:1 lift's speed.
  The app's own score from a whole-file capture is still wrong (it takes a sync pulse as the release). See
  "Second opinion".

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

## The re-centred render (owner decision, 2026-11-18)

At 0.70 the rig can play `S-lam1.4_x0.70.csv` safely and neither scorer will grade it. Three ways out were put
to the owner (re-centre the swing, a shallower limb geometry, or use lambda ≥ 1.0 as a tracking probe only);
the owner chose **re-centre the swing**.

The 1:1 swing at lambda 1.4 spans +55° to +0.9° of rig angle and never goes below zero, so it is 54° wide and
only its position is the problem. `rig_bundle.recentred_render` subtracts a constant from the hold and the
swing; the offset comes in during the lift, so the file still starts at 0, nothing steps, and the three
5° sync pulses are untouched. The lift itself is shorter and later than the 1:1 lift (see "Second opinion", 1). The offset is the whole degree that centres the hold and swing about 0, per file.

| file (`library_recentred/`) | offset | hold | lowest | predicted delivered peak | guard at ±40° | score re-centred | 1:1 expected |
|---|---|---|---|---|---|---|---|
| `S-lam1_recentred.csv` | 30° | +25.0° | −25.0° | 25.2° | passes | 0.257, band 1, A0 41.5° | 0.257, band 1 |
| `S-lam1.2_recentred.csv` | 28° | +27.0° | −27.2° | 27.6° | passes | 0.224, band 1, A0 38.8° | 0.224, band 1 |
| `S-lam1.4_recentred.csv` | 28° | +27.0° | −27.1° | 27.6° | passes | 0.170, band 1, A0 36.1° | 0.170, band 1 |

- **The guard is unchanged.** The same `rig_guard.check_trajectory(limit_deg=40)` passes these files, and the
  bundle reads each back with `rig_guard.check_file` like every other library file.
- **The score is offset-invariant, so nothing has to be undone.** Scoring the re-centred rig trace as it is
  (`session._score_trace`), with the offset undone (`rig_bundle.undo_recentre`), and through the bench path for a
  played file (`match_trials.delivered`, and the Designer's *Open file*) all give the 1:1 expected PT7, band and
  A0; the first two agree to 1e-9. `undo_recentre` exists for anything that needs the absolute rig angle.
- **Labelling.** The folder has its own `README.txt` ("RE-CENTRED RENDER ... the rig's zero is NOT the passive
  rest"), `INDEX.csv` records `offset_deg`, hold, lowest angle and predicted peak per scenario, and
  `MANIFEST` records the offsets. `START_HERE.txt` names the folder and says to label such trials "recentred".
- **What it changes at the bench.** The arm's zero is no longer where the leg hangs: the hold is at +27°, the
  swing ends at −9.0° (lambda 1.4 settles 19° above the passive rest; lambda 1.2 ends at −11.8°, lambda 1.0 at −16.5°), and the player then returns to 0.
  On a vertical-plane rig the arm is therefore not hanging plumb at the model's rest; the commanded trajectory
  is position-controlled either way, but the phone's gravity reference sees the shifted angles.

**Other scenarios.** Of the 63 scenarios the guard refuses 1:1, 34 would pass it re-centred: A-P1, A-P1p,
S-lam1, S-lam1.2, S-lam1.4, S-lam1.5, S-lam2, S-lam3, S-lam6, B-extensor, B-mixed, B-claspknife, B-spasm,
B-habit1 to 5, C-R2n-low, C-N-zero, M-tone_L2, L3, L4, both M-catch_and_hold, R-reflex-vel, -len, -sat,
H-HM1 to 4, K-HM1, K-HM2. The 29 that do not are the passive and lightly damped swings, which go as far below
the rest as they start above it (A-P0 spans 84°). Only the three lambda files ship; `library_recentred/INDEX.csv`
lists the rest as "fits re-centred, not shipped", and adding any is one tuple (`rig_bundle.RECENTRED_SIDS`)
and an owner decision.

The release-detection miss on the model trace is independent of this render: it is in the app, it appears 1:1
too, and a separate session has it. Whether it appears on the rig depends on the arm (next section).

## Second opinion (2026-11-18)

The owner asked for a contrarian review. An independent reviewer (a separate agent given the diff, not these
conclusions; the Gemini call hung and was stopped) tried to break the guard handling, the offset-invariance,
the C# port and this note. Everything in this section is simulation: the synthetic phone replayed through the
app's wasm build, with the arm motion from the guard's identified plant (`rig_guard.sim_response`). Both
experiments were re-run here and reproduced.

**1. A re-centred file could end the app's trial before the swing (fixed in the render, 2026-11-19).** Replaying the WHOLE rig file for
lambda 1.4 (pulses, lift, hold, swing; capture started 3 s before the file) through the app:

| file | app "released" (file time) | app "settled" (file time) | app result |
|---|---|---|---|
| 1:1 | 0.22 s (first sync pulse) | 18.1 s, after the swing | PT 0.175, A0 35.0° (reference 0.170) |
| x0.70 | 0.22 s | 17.6 s | PT 0.410, A0 13.4° |
| re-centred, lift over the full 4 s (as first pushed) | 0.22 s | **6.3 s** (lift ends 6.19 s, true release 9.19 s) | PT 0.957, A0 26.0°, from the pulses and lift only |
| re-centred, later lift at the 1:1 speed (now) | 0.22 s | 18.1 s, after the swing | PT 0.417, A0 9.0° |

The app takes the first 5° pulse as the release on every library file (existing behaviour). It then needs 5 s
below 0.3 rad/s to call the trial settled (`ZERO_CAPTURE_GUARD_RAD_S`, `session.rs` `advance_settle`). The 1:1
lift peaks at 0.45 rad/s and resets that count; the re-centred lift is 27° in the same 4 s, 0.22 rad/s, and
does not; the x0.70 lift (0.315 rad/s) clears it by 5%. So the statement above that "alignment works as on any
other file" held for the encoder and the command, not for the phone app.

The owner said to proceed with what seemed best, and the render was changed (2026-11-19): in a re-centred file
the arm stays at 0 for about 2 s longer and then rises to the hold on a min-jerk profile shortened in
proportion (27/55 of 4 s), so it peaks at the 1:1 lift's 0.45 rad/s and ends at the same sample. The preamble
length, the hold, the release time and the swing are unchanged, so `match_trials`, the Designer's *Open file*
and the score are unaffected (the tests above still pass); the guard passes with the same predicted peaks
(25.2°, 27.6°, 27.6°); the still time between the last pulse and the lift is 3.3 s, under the app's 5 s.
With it the app settles after the swing, as on the 1:1 file. `INDEX.csv` has the lift speed
(`lift_peak_rad_s`).

**Still open, and the app's:** with a whole-file capture the app takes the first sync pulse as the release.
Its on-screen score is then right for the 1:1 file (0.175) and wrong for the x0.70 and re-centred files
(0.41, A0 9° to 13°). The recording now contains the swing, so the encoder log or an offline rescoring can
grade it; the live score from such a capture cannot be trusted on those files. That is in the app's release
logic, which another session has, and the folder's `README.txt` says so.

**2. "The app never finds the release at lambda 1.4" is a property of the ideal trace.** The plant amplifies
the early catch: pre-catch peak speed commanded → predicted delivered is 88 → 103°/s at lambda 1.0, 66 → 87 at
1.2 and 55 → 73 at 1.4, against the 57.3°/s threshold. Swing-only replay of the predicted arm motion:

| lambda 1.4 | release | app PT | app A0 |
|---|---|---|---|
| model trace (the table above) | not found | – | – |
| arm, 1:1 or re-centred, nominal plant | found | 0.184 | 33.4° |
| arm, 1:1, natural frequency ×0.9 | found | 0.141 | 29.7° (under the floor) |
| arm, 1:1, natural frequency ×1.1, damping 0.18 | found | 0.175 | 32.0° |
| arm, x0.70, all three plants | not found | – | – |

Lambda 1.2 at 0.70, "release not found" on the model trace, is found on the predicted arm for all three plants
(PT 0.19 to 0.20, A0 21° to 23°). So at the bench the app's lambda 1.4 result will depend on the rig's tuning,
and the phone should be compared with the score of what the encoder logged, not with the 1:1 expected value.
The mechanism given above (the catch revokes the calm qualification) matches `calibration.rs`.

**3. Smaller corrections made in this note and the code.** PT7 does not keep falling: it has a minimum near
lambda 1.6 (0.157) and rises slowly above it (0.171 at 2.0, 0.243 at 3.0; not graded from 2.8, A0 under 30°),
so "falling branch" is right for 1.0 to 1.5 only (the Designer's text and the `scenarios.py` comment now say
so; the new scenarios' titles are unchanged). The 0.70 and "ends near −8°" statements above were corrected. The
re-centred files need a player limit of 28° or more, and `RIG_CHECKLIST.txt` has no stage that takes the arm to
a large negative angle before them (the folder README now says both). `export_recentred` used the 1 kHz
default when masking the lift; it now passes the file's rate.

**4. Not changed, for the owner to know.** CI runs on Linux, where `test_designer.py` and the C# half of
`test_rig_guard.py` are skipped (no `csc.exe`) and the `export_recentred` test is skipped (`neuromech/out` is
not tracked). The C# port's parity is therefore held on a Windows machine only. The C# and Python scorers
differ by 4e-7 to 6e-7 in PT7 near lambda 0.80 on identical traces (an existing difference in the `PtScore.cs`
port); the new peak test's 1e-6 tolerance sits just above it.

**What the reviewer could not break.** Guard and player handling of negative angles (limit, ramp, watchdog,
end hold, return to 0 and every abort path are symmetric in sign); offset-invariance of the reference score
(all 66 scenarios, offsets −60 to +200, worst PT7 difference 4e-14, also on a plant-filtered noisy trace); the
render at eight sample rates; `DeGroote.cs` against `model_rt.simulate` at twelve other lambdas from 0 to 6
(trace within 6e-14°); and the tables in this note.

## What changed in the code

- `neuromech/scenarios.py`: `S-lam1.2` and `S-lam1.4`, titled "past the PT7 peak at lambda 0.80: falling
  branch". Existing ids and titles are unchanged; the library is 66 scenarios. Neither is near a band edge
  and neither duplicates another swing, so no allowlist entry was added.
- `neuromech/designer/DeGroote.cs`: a C# port of `model_rt.simulate` for the reference terms. The Designer
  has a fourth mode, *Severity axis (De Groote), lambda* (0 to 3), which plays that model at the rig
  amplitude scale and says which side of the PT7 peak the lambda is on. `test_designer.py` holds the C# trace
  to Python's within 1e-6° and its PT7, band, R2n and N to the library's at lambda 1.0, 1.2 and 1.4, the PT7
  peak to `build.rising_branch_peak`, and the 0.70 refusal and guard verdict to Python's.
- `rig_bundle.py`: `library_x0.70/` picks the two new files up (57 of 66 pass the guard), and a third folder,
  `library_recentred/`, holds the re-centred render of `S-lam1`, `S-lam1.2` and `S-lam1.4`. `library/` and
  `library_x0.70/` are otherwise unchanged. `test_rig_bundle.py` and `test_designer.py` hold the guard verdict
  and the offset-invariance of the score.

## How to reproduce

```
python -m neuromech.build
python -m neuromech.phone_trial --sids S-lam1,S-lam1.2,S-lam1.4          # needs npm run build:wasm in webapp/
python -m pytest neuromech/tests/test_designer.py
```

The 0.70 replays scale the swing about the release angle (`0.70 * theta`) before `phone_trial.synthesize`,
which is what the phone sees when the rig plays the scaled file.
