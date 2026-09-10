# IMU characterisation against a motor-driven reference

Created: 2026-09-09
Status: design, approved in brainstorming; not yet implemented

## Why

Every accuracy number this project has was measured against OptiTrack, and
OptiTrack has turned out to be a moving reference: its curve shape changed at
`87d7645`, 4% of its trials contain a discontinuity no knee can make, and its
datum stopped claiming an origin. The instrument has never been compared
against a reference that is exactly known.

A motor with an encoder is that reference. It is exact, repeatable, available
without a participant, and it can produce amplitudes the corpus does not
contain — including the collapsed swings where the excursion floor sits and
where no human trial has ever been measured against truth.

Four questions are open and all four are currently argued from corpus
statistics rather than from a known answer:

1. **A0 over-read.** Phone A0 runs 1.489x optical, with Spearman 0.053 (p 0.60)
   across 100 paired trials — no correlation at all, so not a gain. The
   standing explanation is single-sensor geometry: one IMU on the shank counts
   thigh motion as knee flexion. Never tested against truth.
2. **Tail drift.** Estimated at -0.735 deg/s from indirect evidence. It drives
   `neutral_deg`, which drives `area_ratio`, which is 67.6% of the phone's PT7
   and the reason phone MAS grades failed 9 of 10 against a clinician. Never
   measured directly.
3. **The excursion floor.** `MIN_INTERPRETABLE_A0_DEG = 25.0` has had its
   justification measured false (KD12) and holds because every alternative is
   worse. Nothing has ever tested where A0 extraction actually breaks.
4. **The analysis grid.** 20 Hz vs 100 Hz rests on two synthetic fixtures that
   disagree about the direction of the change.

## Scope

**In:** a rigidly mounted phone on a motor-driven single segment, swept across
amplitude, frequency and damping, compared against encoder truth.

**Out:** the neuromechanical spasticity model. That is a separate, later piece
of work — this design deliberately uses position control, not torque control,
so no physics is being emulated and none of it can confound the measurement.

**Explicitly cannot answer:** why phone A0 over-reads on a human. A motor rig
has a fixed base and one rotating arm, so the thigh never moves. The rig tests
a case the human does not present. See "Reading the result" below, which is the
part most likely to be misused.

## Architecture

Three pieces; one is new code in this repository.

**LabVIEW RT** drives the arm through the trajectory in position control and
logs encoder angle at 1 kHz. Position control removes motor impedance, friction
compensation error and torque-loop delay from the measurement path, leaving the
encoder as an unyielding baseline.

Trajectory points are pre-computed on the host and streamed to the 1 kHz
deterministic loop through an RT FIFO, rather than read from file inside the
loop. Evaluating the analytic form inline is arithmetically fine; the reason
not to is that file I/O jitter would couple into the deterministic loop.

**The phone** records through the unmodified production path. If the app were
modified for the bench, the thing characterised would not be the thing that
ships.

**Offline analysis** — new, in this repository — joins encoder truth to
phone-derived angle and reports the comparisons.

The trajectory generator is **shared**: the same analytic definition produces
what LabVIEW commands and what the analysis expects, so a disagreement between
them is a measurement difference rather than a transcription error.

## Trajectory

    theta(t) = A0 * exp(-t / tau) * cos(2 * pi * f * t)

preceded by three sharp sync pulses.

| axis | values | why |
|---|---|---|
| A0 | 90, 70, 50, 40, 30, 28, 25, 22, 20, 15, 10, 5 deg | dense around the 25 deg floor, which is the point |
| f | 0.6, 0.9, 1.2 Hz | brackets `HEALTHY_REF['f']` = 0.9137 |
| tau | ~2, ~5, ~9 cycles to 3 deg | spans spastic to healthy decay |

Full A0 sweep at nominal f and tau (12 combos), then f and tau varied at three
A0 levels (12 combos, not 18 -- the nominal f/tau pair is already in the sweep),
three repeats each = 72 runs, plus a 20-repeat repeatability block at
a0 = 50 deg: **92 runs**, 23.1 min of commanded motion plus reset time.

The count is whatever `motor_reference.sweep_matrix()` returns; this paragraph
describes it and has been wrong twice. Read the code, not this line.

## Synchronisation

Three sharp pulses before the swing, aligned on their edges.

**Not cross-correlation.** The existing IMU/OptiTrack path carries a ~0.7 s
skew from the NatNet handshake, and cross-correlation on a near-periodic signal
aliases onto a wrong-by-nearly-one-cycle lag — `compare_pair` already carries a
bound specifically to stop that. A pulse edge has no such ambiguity.

## Analysis

Per trial, against known truth:

- `A0_imu / A0_true` as a function of `A0_true`
- settled-tail slope, in deg/s, measured rather than inferred
- angle RMSE against truth, with no datum or polarity confound to remove
- the same trial scored at 20 Hz and at 100 Hz
- the same trial scored at `detrend=False` (what the phone ships) and
  `detrend=True` (what analysis uses)

The detrend comparison is the first chance to say which convention is *right*
rather than which is conventional: with an analytic zero line, one of them is
closer to truth and the flag currently moves phone PT7 by 0.363.

## Reading the result

Both outcomes are informative and lead to different actions, which is the test
of whether the experiment is worth running.

**If A0 is recovered accurately on the bench**, the over-read comes from
something the bench does not reproduce. That is *three* candidates, not one:

  - two-segment geometry (thigh motion counted as knee flexion)
  - out-of-plane motion (measured per-trial by `workbench_engine.lateral_motion`,
    median 24.7% of rotation off the flexion axis)
  - strap compliance, which the rigid mount deliberately excludes

Two-sensor capture follows only if the other two are also excluded. The
strapped variant of this same sweep isolates the third for one extra fixture,
and becomes considerably more valuable if the bench comes back clean.

**If A0 is wrong on the bench**, the pipeline has a defect, and the geometry
explanation this project has held since August is wrong. That is the more
consequential outcome and the cheaper one to act on.

## Testing

The analysis code is testable without the rig: the trajectory generator
produces truth analytically, so the joining, extraction and comparison can all
be exercised against synthetic encoder logs before any hardware exists. The
rig-dependent part is only the data capture.

Specifically:

- the generator reproduces its own A0, f and decay when re-measured
- sync-pulse detection finds the three edges under added noise, and refuses
  rather than guessing when fewer than three are present
- the joiner refuses a phone log and an encoder log whose pulse counts disagree
- A0 extraction on a noiseless synthetic returns the commanded A0
- both grids and both detrend conventions are scored on every trial

## Open questions

- Bench session length: 92 runs total 23.1 min of COMMANDED MOTION (measured
  from run_duration_s). Reset, mount and re-arm time between runs is the
  unknown that decides whether this is an afternoon or a day.
- Encoder log format: assumed to be a file the analysis can read, with a
  timestamp and an angle column. Not yet specified.
- Motor model number: notes carry both `JR 14-MCH-1` and `JR16M4CH-1`. This
  changes torque constant and peak current; it does not affect this design,
  which is position-controlled, but it must be settled before the torque-mode
  neuromechanical work.
