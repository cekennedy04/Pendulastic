# Plan: phone angle tracking against an encoder on a free-swinging pendulum, at several weights

Date: 2026-11-18. Branch: `claude/encoder-weight-test-plan-a6e06a`.
Run sheet: `docs/bench/FREE_PENDULUM_ENCODER_RUN_SHEET.md`.

**Extends:** `2026-08-24-001-plan-mobile-app-validation-build-test.md` (section 3B, task V0.5,
"bench pendulum check", and decision D3) and the bench method in
`2026-09-04-001-feat-instrument-validation-plan.md`.
**Supersedes:** `docs/bench/TILTED_RIG_ANGLE_VALIDATION_PROTOCOL.md` and
`docs/bench/TILTED_RIG_RUN_SHEET.md` as the route to validating the app's own angle. The owner
chose a new gravity pendulum with an encoder over tilting the motor rig. Both documents carry a
banner pointing here. They were never run.

Status: plan only. Nothing here is built or measured. Hardware fields marked **TO FILL** are not
known yet and must not be assumed.

## Open items

1. **Encoder and logging: TO FILL.** Encoder model, counts per revolution, DAQ or counter card,
   sample rate and log file format (section 2).
2. **Acceptance limits: to confirm.** Every limit in section 8 is
   **PROPOSED - pending supervisor confirmation before session S1**.
3. **Phone radius: to measure.** 0.30 m is a default; measure the strap position used in
   participant sessions (section 3.3).
4. **Winter segment proportions: to check** against the lab's edition (sections 3.3 and 3.4).

## 1. Purpose and scope

The supervisor's first goal (2026-11-18) is to show that the phone **tracks motion accurately**.
The sequence is: (1) phone against an encoder on a fixture where the phone is off the rotation
axis and the arm swings freely; (2) a leg on the same encoder setup; (3) different weights;
(4) people, with OptiTrack. This plan covers the fixture with weights (Stage 1) and a leg
phantom on the same fixture (Stage 2).

What exists today, all from the horizontal motor rig and all measured before the AHRS seed fix
(#98):

| Evidence | Number | Limit |
|---|---|---|
| Static accuracy, raw gyro against the 1 kHz encoder | mean abs error 0.58°, max 3.7°, gain 0.997, bias −0.03°, 95% limits ±1.7°; 176 steps, 44 recordings | Raw gyro, not the app's angle |
| Dynamic accuracy, motor lifts | median frequency difference 0.4%, mean abs error 0.72°; 8 encoder-paired lifts | Motor-driven; no free release has encoder truth |
| The app's own angle | Not assessable | Gravity lies on the rotation axis of a horizontal swing, so the fusion has no reference |

This test fills the two gaps: free release swings with encoder truth, and the angle the shipped
app computes, in a plane where gravity is available to it.

**Questions, in order.**

1. Does the angle from the shipped app pipeline follow the encoder over the whole swing (hold,
   release, ring-down, rest)?
2. Does the agreement hold at every added weight, release angle and phone radius tested?
3. Do the frequency, decay and pendulum-test parameters computed from the phone match those
   computed from the encoder?
4. Does the same hold when the phone is strapped to a leg phantom instead of clamped?

**Not in scope.** No MAS grade, severity or clinical statement is made from any result here. PT
parameters and the PT score are compared only as numbers computed from two sources of the same
swing. Humans, OptiTrack, weights on a real leg, zone recalibration and any scorer change are
outside this plan.

## 2. Hardware facts

Answers from the owner, 2026-11-18:

| Item | Answer |
|---|---|
| Encoder | A separate encoder on the new pivot, read by LabVIEW or a DAQ card. Model and resolution not given. |
| Arm | Not built. Build to this plan. |
| Added weights | None yet. This plan specifies them. |
| Release and sync | Release by hand. Synchronise with a tap. |
| Phones | One iPhone and one Android phone. |
| Stage 2 | A leg phantom on the arm. |
| Session length | About 1 hour, about 30 swings. |
| Realism | Tracking only. The swing does not have to resemble a leg. |

**TO FILL before the pilot session (section 6):** encoder make and model; counts per revolution
and whether quadrature-decoded; DAQ or counter card; logged sample rate; log file format and
channel layout; iPhone and Android models and OS versions; the arm's built length, mass and
bearing type.

## 3. Fixture requirements

### 3.1 Encoder and logging

| Requirement | Value | Why |
|---|---|---|
| Resolution | 0.05° per count or finer (at least 2000 lines with ×4 decoding, 8000 counts per turn) | Ten times finer than the 0.5° level the phone is being held to |
| Mounting | Directly on the pivot shaft, no belt or gear; a rigid or bellows coupling | Backlash would appear as phone error |
| Sampling | Hardware-timed, 1 kHz preferred, 500 Hz minimum, with a time column from the DAQ clock | The existing analysis assumes a uniform 1 kHz truth |
| Zero | Zeroed with the arm hanging at rest at the start of each session; the zero logged | Truth is angle from vertical |
| File format | Preferred: the existing LabVIEW binary layout (interleaved float64, `time, position (deg), velocity (deg/s), ...`) so `labview_bin_reader.read_labview_bin` reads it unchanged. Otherwise CSV with `time_s, position_deg`. | Reuse |
| Independent check | Before the first session, compare the encoder with a digital inclinometer at 0, 30, 60 and 90° with the arm clamped. Agreement within 0.3°. | The encoder is the truth for everything else |

### 3.2 Arm and pivot

- Rigid arm, about **0.50 m** from the pivot axis to the tip. Slotted aluminium extrusion
  (20 or 30 mm) is suggested so the phone clamp and the mass carrier slide and lock at marked
  radii. Mark radii from the pivot axis every 0.05 m.
- Horizontal pivot axis, so the arm swings in a vertical plane. Check with a level; log the
  axis tilt. Ball bearings; no motor, gearbox or brake on the shaft.
- Free travel of at least ±75° from vertical with nothing in the swing path.
- Frame clamped or bolted to a bench. With the heaviest load held horizontal the pivot sees
  about 22 N·m (6 kg at 0.38 m); the frame must not rock.
- First bending resonance of the loaded arm well above the swing band. Check with a tap in the
  pilot: the ring after a tap should be above about 15 Hz in the phone gyro.
- **Angle stops:** a plate behind the arm with removable pegs or printed marks at 20, 40 and
  60° from vertical. The operator holds the arm against the peg and lets go. The encoder gives
  the real release angle, so the pegs only need to be repeatable to a few degrees.

### 3.3 Phone mount

- A rigid clamp on the arm, **off the rotation axis**. Primary radius **r = 0.30 m** from the
  pivot axis to the centre of the phone. This stands for the mid-shank strap position: with a
  shank of about 0.43 m (0.246 of a 1.75 m stature, Winter's segment table), mid-shank is 0.20 to
  0.30 m below the knee. **Measure the strap position used in participant sessions and use that
  radius if it differs; log it.**
- Mount the phone in the orientation the app's placement instructions give for a shin.
  Photograph it. The analysis takes the gyro axis with the largest variance as the swing axis
  and logs which one it was.
- The clamp must also lock at 0.10, 0.20 and 0.40 m (session S5).
- A tether on the phone. Phone on battery, no cable (cable drag is one of the checks listed in
  `STATUS.md`).

**Why the radius matters.** On a free pendulum the accelerometer does not read the tilt. At a
point r from the pivot, the tangential specific force is `g·sin(θ)·(1 − r/L_eq)`, where `L_eq`
is the equivalent simple-pendulum length (about 0.37 m for the loaded arm below). At
`r = L_eq` the accelerometer reads as if the phone were upright through the whole swing; at
r = 0.30 m it reads about a fifth of the true tilt. The app's filter stops correcting from the
accelerometer above 0.3 rad/s (`ACCEL_CORRECTION_GYRO_MAX_RAD_S`), but the arm passes through
zero speed at every turning point, where this error is largest. A real shank has the same
property. This is the mechanism the supervisor's "off the axis" condition tests, and it is why
the radius is logged and varied in S5.

### 3.4 Added weights

A carrier that clamps to the arm with its centre at **0.38 m** from the pivot, taking standard
plates. Weigh the carrier and each stack on a scale to ±10 g and log the measured value.

| ID | Added mass (carrier included) | Stands for | Predicted frequency |
|---|---|---|---|
| W0 | none (arm, clamp, phone) | bare arm | about 0.88 Hz |
| W1 | 1.0 kg | below the adult range | about 0.83 Hz |
| W2 | 3.0 kg | shank and foot of a 50 kg adult | about 0.82 Hz |
| W3 | 4.5 kg | shank and foot of a 75 kg adult | about 0.82 Hz |
| W4 | 6.0 kg | shank and foot of a 100 kg adult | about 0.81 Hz |

Basis: shank plus foot is 6.1% of body mass with its centre of mass at 0.606 of shank length
and radius of gyration 0.735 of shank length about the knee (Winter, *Biomechanics and Motor
Control of Human Movement*, segment table; check the edition in the lab). That gives 3.1 to
6.1 kg for 50 to 100 kg adults, an equivalent pendulum length of 0.35 to 0.41 m and a natural
frequency of 0.78 to 0.84 Hz, which is why the mass sits at 0.38 m. Predicted frequencies
assume a 0.45 kg arm and 0.30 kg phone and clamp; they are small-angle values and will be
replaced by the measured ones.

**What changing the weight can and cannot show.** An ideal pendulum's motion does not depend on
its mass, so the frequency barely moves from W1 to W4. What changes is the inertia (0.07 to
0.93 kg·m²), the decay (bearing friction matters less on a heavy arm), and the load on the
clamp and frame. The expected result is therefore **no dependence of phone error on weight**. A
dependence would point at the mount or the fixture flexing, not at the sensor. Frequency
dependence is covered separately by the radius and the bare arm.

### 3.5 Clock synchronisation

The phone and the DAQ share no clock. Three things are done, in this order of trust:

1. **Cross-correlation** of phone swing-axis rate against the encoder's rate over the whole
   swing (`bench_dynamics.xcorr_align`), refined to below one sample. This sets the lag for
   every recording. A free swing has ten or more cycles of signal, so this is well determined.
2. **Clock-rate check:** regress phone zero-crossing times on encoder zero-crossing times across
   the ring-down. The slope is the clock-rate ratio; the residual is timing jitter. Correct for
   the slope if it exceeds 100 parts per million.
3. **End tap (raw recordings only):** when the arm has been still for 6 s, one sharp knuckle tap
   on the arm, then 2 s still, then stop. The tap is a mechanical event both sensors see and is
   an independent check of the lag from step 1.

The tap is at the **end**, not the start, and is **never used in scored captures**. A tap before
the swing can trip the app's release gate (noted on 2026-11-18 in
`DYNAMIC_CHARACTERIZATION_RUN_SHEET.md`), and the offline scorer has taken a sync pulse as the
release before (`STATUS.md`, workstream 2). A replay ends at SETTLED, before an end tap.

A lag fitted by cross-correlation absorbs any constant phone delay. The lag is therefore
reported as a result, and the tap check is what says whether it is a real delay.

## 4. Conditions

### 4.1 Factors

| Factor | Levels |
|---|---|
| Weight | W0, W1, W2, W3, W4 |
| Release angle from vertical | 20°, 40°, 60° |
| Capture mode | **raw** (`?research=1`, Record (raw), end tap) and **Quick Test** (the normal scored capture, no tap) |
| Phone | iPhone, Android |
| Day | two days per phone, with the phone removed and re-clamped between |

Release angles: 20° is the smallest that reliably passes the app's 1 rad/s release threshold
(peak speed is about 100°/s at 20° and about 50°/s at 10°). 60° gives about 120° of travel,
which is the top of the range the scorer treats as interpretable. The pendulum never returns
past its release pose, so the app's magnitude angle (`swing_deg` takes `abs()` of rotation from
the zero pose) does not fold.

One session is 5 weights × 3 angles × 2 modes = **30 swings**. Each cell gets one raw and one
scored swing, so the live app and the replay are compared on matched conditions.

### 4.2 Sessions

| Session | Phone | Content | Swings |
|---|---|---|---|
| P0 | both | Pilot and commissioning (section 6). Not analysed for acceptance. | about 15 |
| S1 | iPhone | Weight matrix, day 1 | 30 |
| S2 | Android | Weight matrix, day 1 | 30 |
| S3 | iPhone | Weight matrix, day 2, phone re-clamped | 30 |
| S4 | Android | Weight matrix, day 2, phone re-clamped | 30 |
| S5 | iPhone | W3 only. Phone radius 0.10, 0.20, 0.40 m; phone yawed 15° on the clamp at 0.30 m; phone rolled 90° at 0.30 m. Each × 3 angles × 2 modes. | 30 |
| S6 | iPhone | Stage 2, leg phantom (section 5) | 30 |
| S7 | Android | Stage 2, leg phantom | 30 |

### 4.3 Order

Changing plates takes minutes, so weight is a block. Within a session the block order is random;
within a block the order of the two modes is random; within a mode the three angles are random.
The orders are generated from seed `20261118` and printed in the run sheet, which also carries
the generating code. The operator follows the printed order and does not reorder for
convenience.

### 4.4 Per-swing procedure (summary; the run sheet has the checklist)

Start the phone, start the encoder log, hold the arm against the peg for 5 s, release cleanly,
let it ring down. A bare ball-bearing pendulum may swing for minutes: if it has not settled at
**60 s**, press Stop (scored mode) or steady the arm by hand (raw mode) and log `stopped_by =
60s`. Tracking measures use the window before any hand contact.

## 5. Stage 2: leg phantom on the same encoder

A shank-and-foot phantom (a mannequin or prosthetic lower leg, or a moulded foam shank over a
tube) fixed to the same arm with the knee axis on the pivot. The phone is **strapped** to the
phantom with the strap and position used on participants, not clamped. Weigh the phantom; log
its mass and length. The encoder, logging, release angles, modes and analysis are unchanged.

| ID | Condition |
|---|---|
| L0 | Phantom alone, strap as on a participant |
| L1 | Phantom plus a 1 kg cuff at the ankle |
| L2 | Phantom plus a 2 kg cuff at the ankle |
| L3 | Phantom alone, phone removed and strapped on again |
| L4 | Phantom alone, strap deliberately one finger loose |

Each × 3 angles × 2 modes = 30 swings per phone (S6, S7). This stage asks whether a strap on a
leg-shaped, partly compliant surface behaves like the rigid clamp. L3 measures the effect of
re-mounting and L4 of a loose strap. The Stage 1 acceptance criteria apply; the difference
between Stage 2 and Stage 1 is reported whatever it is. A phantom has no soft tissue or muscle,
so a pass here does not carry over to a person. That is the OptiTrack stage.

## 6. Pilot session P0 (go or no-go for S1)

- Encoder against the inclinometer (section 3.1).
- One swing per weight from 40°: confirm the log reads back, measure the ring-down time and the
  frequency, and decide whether the 60 s cap is needed for each weight.
- Confirm the end tap is visible in both traces, and the arm resonance is above 15 Hz.
- Confirm both phones capture, export and score a pendulum swing in Quick Test, and that the
  export holds the raw samples for scored trials. Record the footer build and algorithm.
- Record each phone's actual sample rate.
- Run the analyser end to end on these swings. Fix anything that breaks. **Freeze the analysis
  code and the criteria in section 8 (commit hash logged) before S1.**

## 7. Measures

Three phone signals are compared with the encoder for every swing:

| Signal | What it is | Role |
|---|---|---|
| **A. App pipeline angle** | The raw export replayed through the shipped wasm and worker protocol (`webapp/scripts/replay-trial.mjs --trajectory`), giving the trajectory the app scores | **Primary** |
| B. Raw gyro angle | Swing-axis gyro integrated (`bench_dynamics.phone_gyro`), as on the motor rig | Sensor reference; links to the 0.58° result |
| C. Live app record | What the app stored for a Quick Test capture: parameters, PT score, release time | End-to-end check of the live capture |

The encoder is mapped to the app's convention before comparison. The app reports the magnitude
of rotation from its zero pose, about the learnt flexion axis. The encoder equivalent is
`|θ_enc(t) − θ_enc(zero)|`, with the zero pose taken at the same instant the app takes it.
**Confirm the zero-pose instant in `mobile-imu-core/src/replay.rs` when writing the analyser**
and state it in the report.

Per swing, phone signal against encoder, on a 10 ms grid after alignment:

- **Whole-swing agreement:** RMSE; bias (mean difference); 95% limits of agreement; Pearson r;
  gain (slope of phone on encoder). Computed over the whole recording and separately for the
  hold, the first swing, the ring-down and the rest.
- **Amplitude:** first-swing excursion and each later peak, phone against encoder.
- **Frequency and decay:** f, swing count N and decay time τ with the same estimator on both
  (`bench_dynamics.oscillation_metrics`).
- **Rest:** drift in °/s over the hold and over the settled tail; final rest offset.
- **Timing:** fitted lag, clock-rate ratio, tap lag minus fitted lag.
- **PT parameters, two ways:**
  1. *Same scorer, two sources.* `pendulastic_pt_score.compute_pt_params` and
     `compute_pt_score` on the encoder curve and on signal A, with the release given from the
     encoder and `detrend=False`. This isolates what tracking error does to the parameters.
  2. *App as shipped.* The parameters and PT score the app itself reports (signal C, and the
     replay's `params` / `pt_score`) against the encoder values from 1. This includes the app's
     own release detection.
  Reported for the five scored parameters, for `omega_max_n` (shown, not scored), for A0 and for
  the PT score, each as bias and limits of agreement.

Pooled over swings: Bland–Altman of per-swing mean difference; the same by weight, angle, phone,
day, mode and radius; error against added mass, release angle, radius and peak speed.

## 8. Acceptance criteria

**Every limit in this section is PROPOSED - pending supervisor confirmation before session S1.** The owner and supervisor
confirm or change them before S1; after that they are fixed. They apply to signal A unless
stated, per phone.

| # | Criterion | Limit (PROPOSED - pending supervisor confirmation before session S1) | Why this limit |
|---|---|---|---|
| A1 | Whole-swing RMSE, each swing | every swing ≤ 5.0°; median ≤ 2.0° | 5° is the project's standing angle goal (tilted-rig protocol; gate G0 of the 2026-08-24 plan). 2° is about the static 95% limit already shown for the raw sensor (±1.7°): the app's processing should add little to it. |
| A2 | Per-swing mean difference, pooled | bias within ±1.0°; 95% limits of agreement, including their 95% confidence bounds, within ±5.0° | Same 5° goal, stated with its uncertainty (section 9) |
| A3 | Gain and correlation, each swing | gain 0.97 to 1.03; r ≥ 0.995 | Static gain was 0.997 and peak-speed gain 1.011 on the motor rig. r is reported but is a weak test on its own. |
| A4 | First-swing excursion | within 3° or 5%, whichever is larger | The excursion is the largest single input to the PT parameters |
| A5 | Frequency, decay, count | f within 2%; τ within 10%; N within ±1 | The pass targets already used on the motor rig (`2026-11-18-app-on-motor-session.md`); medians there were 0.4 to 0.6% for f |
| A6 | PT score, same scorer on both sources | difference ≤ 0.03 on at least 90% of swings and ≤ 0.06 on all | 0.03 is the margin `neuromech.build` already treats as "too close to a band edge to trust". The healthy band is 0.17 wide. |
| A7 | No dependence on weight | A1 to A5 pass in every weight × angle cell; slope of RMSE on added mass, with its 95% interval, inside ±0.25° per kg | 0.25° per kg is 1.5° across the 6 kg range, under the A1 median limit |
| A8 | App behaviour | every Quick Test swing captured and exported with no `ERROR`; the live record equals the replay of its own raw export (parameters to 1e-6, same release sample) | The replay is only a valid stand-in for the app if this holds |
| A9 | Synchronisation | tap lag minus fitted lag within ±20 ms on every raw swing; clock-rate ratio within 200 ppm | 20 ms is about one phone sample |

Signal B (raw gyro) is held to A1, A3 and A5 as well (the same proposed limits). If B passes and A fails, the fault is in
the app's processing, not the sensor.

**What counts as a failure, and what it would mean.**

| Observation | Reading |
|---|---|
| A fails, B passes | Fusion or flexion-axis projection. Look at error against radius and at the turning points (section 3.3). |
| A and B both fail, error grows with weight or release angle | Mount or fixture flex, or clamp slip. Check the tap resonance and re-clamp. |
| A and B both fail at every condition, gain off | Gyro scale or axis misalignment; compare S5 yaw and roll conditions. |
| Pass on iPhone, fail on Android (or the reverse) | Platform sample rate or timestamps. |
| Pass day 1, fail day 2 | Mounting repeatability. |
| A1 to A5 pass, A6 fails | Tracking is good enough for the angle but not for a parameter. Report which parameter and its sensitivity. |
| A8 fails | The live capture differs from the replay. The acceptance results then rest on the live records only, and the difference is a bug to report. |
| A9 fails | Timing is not trustworthy. RMSE is still reported at the fitted lag, labelled as such. |
| Encoder check fails in P0 | Stop. Nothing is measured against an unverified truth. |

A failed criterion is reported as failed. Limits are not adjusted after data collection, and no
swing is dropped for its result (section 10).

## 9. Sample size

The unit is the **swing**. Samples within a swing are strongly correlated and are not counted as
independent.

- **Limits of agreement.** With n swings and SD s of the per-swing differences, the standard
  error of a limit is about `1.71·s/√n`. Per phone, the matrix is run twice (two days), n = 60:
  the 95% bound on each limit is about `2.40·s` from the bias. Meeting ±5° (A2) therefore needs
  s ≤ 2.1°. The static SD on the motor rig was about 0.87°, so the test can pass with margin and
  can fail clearly if the app's angle is two to three times noisier than the raw sensor. With
  one session (n = 30) the bound is `2.61·s`, needing s ≤ 1.9°: enough for a first read, not for
  the claim.
- **Weight effect.** Each weight has 12 swings per phone over two days. A difference between two
  weights of about 1.1 SD of per-swing RMSE is detectable at 80% power (two-sided 5%). The slope
  test in A7 uses all 60 swings and is more sensitive than that.
- **Repeats.** Each weight × angle × mode cell has one swing per session and two per phone over
  both days. That is too few to estimate within-cell repeatability by cell; repeatability is
  estimated across cells from the day 1 to day 2 pairs (30 pairs per phone).
- **Stage 2** has 30 swings per phone. It is read against Stage 1 as a difference with its
  interval, not as a separate pass at the full confidence of A2.

Total: 210 analysed swings in seven sessions of about an hour, plus the pilot.

## 10. Data handling

**Folders** (under `Pendulastic_Mobile_App_Trial/`, which is gitignored; nothing there is
committed):

```
pendulum_fixture/<YYYY-MM-DD>_<session>_<phone>/
  phone/         the app export: *-manifest.json and *.jsonl
  encoder/       pf<MMDD>_NNN.bin (or .csv)
  rig_log.csv    one row per attempted swing, from the template
  session.json   fixture facts for the day (below)
  photos/        phone mount, mass carrier, angle plate
  report/        analyser output
```

**Trial ID:** `PF-<session>-<condition>-A<angle>-<mode>-<seq>`, for example
`PF-S1-W3-A40-Q-17`. `mode` is `R` (raw) or `Q` (Quick Test). The ID is printed on the run
sheet and written in the rig log; the app's own file names are left as exported.

**Encoder files:** prefix `pf<MMDD>`, numbers continue through the day and are never reused.

**Rig log** (`docs/bench/2026-11-18-free-pendulum-rig-log-template.csv`):
`seq, trial_id, encoder_trial, condition, release_angle_deg, mode, app_result, stopped_by, notes`.
One row per attempt, including failed ones. As on the motor rig, the log is the pairing: rows
whose `app_result` starts with `ERROR` are dropped and the rest are matched in order to the
exported phone trials sorted by manifest timestamp. Cross-correlation only verifies.

**session.json:** date, operator, session ID, phone model and OS, browser, app build and
algorithm string from the footer, phone radius, phone orientation, encoder model and counts per
revolution, sample rate, encoder zero, pivot-axis tilt, measured mass of each weight stack and
its radius, arm length and mass, room temperature, and the commit hash of the analyser.

**Exclusions.** A swing is excluded only for a fault the operator wrote in the log at the time
(recorder not started, arm bumped, clamp seen to slip, release fumbled), before any result is
seen. Excluded swings are listed in the report with the reason and are repeated at the end of
the session if time allows. Nothing is excluded for a large error.

The repository is public. No participant data is involved in this test, and raw recordings stay
out of git.

## 11. Analysis plan

**Reused as is.**

| Code | Use |
|---|---|
| `labview_bin_reader.read_labview_bin` | Decode the encoder log (if the new logger writes the same layout; it takes `n_channels`) |
| `bench_dynamics.py`: `load_jsonl`, `phone_gyro`, `xcorr_align`, `oscillation_metrics`, `score_ringdown` | Raw-gyro angle, alignment, f / N / τ with one estimator on both sides |
| `bench_session_report.py` | The rig-log pairing rule, the manifest ordering and the report layout |
| `webapp/scripts/replay-trial.mjs --trajectory` and `neuromech/phone_trial.run_app` | The shipped pipeline on a raw export. Needs `npm run build:wasm` in `webapp/` first; restore `webapp/src/build-id.js` afterwards. |
| `pendulastic_pt_score.compute_pt_params`, `compute_pt_score` | PT parameters from the encoder curve and from the app trajectory |
| `bench_figures/bench_figs.py` (`st_bland`, `st_scatter`) and `pubstyle.py` | Bland–Altman and scatter figures in the existing style |

**New code.**

1. `pendulum_fixture_report.py`, one command per session, like `bench_session_report.py`:
   - an encoder loader for whatever format the new logger writes;
   - the mapping from encoder angle to the app's convention (section 7);
   - alignment: fitted lag, clock-rate regression, tap detection and the tap check;
   - per-swing measures for signals A, B and C, by segment;
   - PT parameters both ways;
   - pooled statistics with confidence intervals for the limits of agreement, by condition;
   - a pass or fail line for each criterion in section 8, and `trials.csv`, `summary.md` and
     figures (overlay per swing, Bland–Altman, error against mass, angle and radius).
2. Tests on synthetic swings with known truth, using `neuromech/phone_trial.synthesize`. It
   must be checked whether the synthesiser models a phone at a radius (lever-arm acceleration);
   if it does not, that is added, because the off-axis accelerometer term is the effect under
   test.
3. A reader check for the Android export (sample rate, timestamp units, axis convention).
4. The run-sheet order generator (the snippet in the run sheet), committed next to the analyser.

**Order of work.** Analyser and tests on synthetic data first; then P0 on real data; freeze;
then S1 to S7. The report for each session goes in `docs/reports/` with the commit hash, the app
build and the algorithm string. Numbers are not combined across app algorithm versions.

## 12. Risks

| Risk | Mitigation |
|---|---|
| The bare pendulum rings for minutes; the app does not settle | 60 s cap, logged per swing. If most swings hit the cap, add light friction at the pivot and record that it was added. |
| The app's release or hold detector misbehaves on a pendulum (a known defect class: a brisk lift fires the release detector) | Move the arm to the peg slowly; hold 5 s. Any mis-detected release is logged and reported under A8, not hidden. |
| Hand release gives a push or a snag | The encoder shows it. Rule in the log: a fumbled release is excluded and repeated. |
| Encoder file format differs from the motor rig's | Settled in P0, before the analyser is frozen. |
| Clock drift or dropped phone samples | Clock-rate regression and A9; phone sample intervals are checked per recording. |
| Arm or clamp flexes under 6 kg | Tap resonance check in P0; A7 is designed to show it. |
| Phone falls | Tether; clear floor area. |
| Pinch and impact from a 6 kg arm | One operator; hands clear below the arm before release; frame fixed to the bench. |
| C: drive is nearly full | Encoder logs are about 1 MB per minute at 1 kHz; a session is under 100 MB. Keep raw data off the worktree if space is short. |
| App is redeployed between sessions | Footer build and algorithm are logged per session; results are not pooled across algorithm versions. |
| Criteria tuned to the data | Criteria and analyser frozen at P0, by commit hash. |

## 13. What this test will not show

- Nothing about MAS, severity or any patient group.
- Nothing about soft tissue, strap slip on skin, or a knee that is not a fixed hinge. Stage 2 is
  a phantom.
- Nothing about the scorer's validity. It compares scorer output from two sources of one swing.
- Agreement on a damped but otherwise free pendulum. Swings with catches, plateaus or clonus are
  the motor rig's job (neuromech workstream).
- Results hold for the two phone models tested and the app algorithm version recorded.

## 14. After this plan

Stage 3 in the supervisor's sequence (weights on a real leg) and Stage 4 (people with OptiTrack)
each need their own plan, and Stage 4 needs the existing ethics cover checked. The radius and
strap findings from S5 to S7 decide the mounting instructions those stages start from.
