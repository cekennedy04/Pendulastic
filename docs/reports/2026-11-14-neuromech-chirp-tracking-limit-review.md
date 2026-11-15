# Review: neuromech tracking limit derived from the session's chirp (2026-11-14)

Independent review of `origin/neuromech/chirp-derived-tracking-limit` at `b0d18c9`, one commit on top of
`1505fb8` (the head of PR #96, reviewed twice already and not re-reviewed here). The change replaces the fixed
25 ms tracking limit in `neuromech/session.py` with checks derived from the session's `R000` chirp. The
author's account is section B (B.1 to B.11) of `docs/reports/2026-11-11-neuromech-release-alignment.md` on the
branch. The request it answers is sections 3d and 3e of
`docs/reports/2026-11-12-neuromech-release-alignment-rereview.md` on `origin/docs/release-alignment-rereview`.
The reviewer wrote none of the code. Nothing was pushed to the branch under review and no study data was read.

Everything under "ran" was run in a throwaway detached worktree at `b0d18c9` with the project `.venv`, on a
scenario library built there (`python -m neuromech.build`, 67 scenarios). **It is all simulation.** No rig,
no LabVIEW log and no phone recording was involved, so nothing here says how a real rig behaves; it says how
the checks behave on simulated rigs, including simulated rigs that are not the model the checks assume. The
probe harness is the reviewer's own and is not committed; section 9 says what it does.

## Verdict

**Fixes required, then ready for a draft PR.** Not a rejection: the structure is right, the code does what
section B says it does, the gates pass, and the two limits the re-review recorded as admitted (a half-speed
start and a 6 deg forward lurch in every run) are now caught, by a check that went on catching them on every
mismatched rig I put them on. With `--tracking-limit-ms`, or without a chirp, runs are judged as `1505fb8`
judged them, so the branch is not worse than its base on any input I found.

The part that is not ready is the tracking half, `measured > max(1.15 x predicted, 4 ms)`:

1. **It flags good runs inside the author's own model family.** B.3's "no good run is flagged on any plant"
   holds on the plants B.3 samples (I reproduced seven of them, 0 flags) and fails between them: 11 Hz with
   zeta 1.0 flags 7 of 264 good runs, 12 Hz with zeta 1.0 flags 10 of 264, and a 20-run session on the 11 Hz
   plant through the real `score` exits 1 with two good runs "tracking flagged" (section 3.1). The cause is the
   form of the floor, not its value.
2. **1.15 is fitted to the simulator.** It is defensible as a placeholder and only as that. The plant
   simulated is the model that is fitted, and the ratio measures everything the model leaves out. Departures
   I would call small put a large share of good runs over it with the chirp still trusted (section 3):
   the arm settling to 0.99 of the setpoint, 23 % of good runs flagged; a slow closed-loop dipole worth 2 % of
   gain at 0.1 to 0.3 Hz, 64 to 68 %; loop stiffness 10 % lower at the hold angle than at the chirp's, 5 %; encoder noise of
   0.1 deg rms instead of 0.02, 10 to 42 % on the faster or better-damped plants; a chirp recorded at 10 %
   more bandwidth than the runs, 42 %. A composite of mild departures gives 37 %. The author says the margin is
   provisional and that "anything the model leaves out lands in this ratio" (B.3, B.9); what section B does
   not convey is how little it takes.
3. **The release half is the part that works.** Across the 148 linear conditions of section 3 with a trusted
   chirp and no fault at the release (plants inside the model family, extra poles and resonances, static and
   slow gain, noise, quantisation, chirp gain, Player timing: 24,002 usable good runs) the release check raised
   no flag while the tracking ratio raised 2,161. It moved only under nonlinearity strong enough
   to shift the onset (section 3.5). And it still caught both admitted limits on every mismatched rig I put
   them on (section 3.8).
4. **The only way to relax the tracking ratio turns the release check off.** `--tracking-limit-ms` judges
   "by MS alone". On a rig where the ratio false-flags, the operator's one documented way out removes the
   check this branch exists to add: a 6 deg lurch in every run with `--tracking-limit-ms 25` is back to 12 of
   20 runs "scored" (section 5.3).

Required before the PR leaves draft (section 7 has the detail and the evidence for each):

1. Fix the floor so that good runs just above it are not flagged (measured remedy: floor added in quadrature),
   with a test on a plant in the 10 to 12 Hz, zeta 1.0 region, and correct B.3's sentence.
2. Give the operator a way to relax or disable the tracking ratio that leaves the release check on.
3. State in B.9 and the README, with numbers, how sensitive the tracking ratio is to mismatch, and correct the
   description of the saturated-chirp window (it is not "225 to 250 deg/s": it depends on the plant, and at
   250 deg/s it is 58 of 132 deliveries blamed, not 2 of 8).
4. Tests: the margin (1.20, 1.25), the floor (3, 6) and the chirp R2 (0.92, 0.975) survive mutation once the
   assertions on the literals are set aside, and eight branch mutants survive outright, one of them the exit
   status on a chirp finding (section 5.2).

The chirp trust gate's gap (a mildly saturated chirp is trusted and good runs are blamed as "early") is, in my
judgement, **acceptable as a documented limit and not blocking**: it is loud, it exits 1, and nothing is scored
wrongly. Section 4 says what would make it less misleading.

## 1. The code (question 1)

Read: `git diff 1505fb8 b0d18c9 -- neuromech/session.py neuromech/dryrun.py`, and `_delivered_release`,
`_tracking`, `_session_lag`, `score` and `ilc.identify` / `ilc.locate` as they stand.

**Is the prediction computed the way the measurement is?** Yes. `_plant_prediction` passes the command
through `_model_response`, then calls the same `_delivered_release` and `_tracking` with the same command,
swing mask and commanded release that `score` uses for the recording, on the same time base, with one second
of held setpoint appended so a late model is not cut off. `_model_response` agrees with `dryrun.plant_response`
(scipy `lsim`) to 8e-12 deg on a Player staircase in my check, and with an independent RK4 integration to 2e-8.
Two asymmetries remain, both intended and both consequential:

- the recording contains the Player's staircase, its lateness and the encoder's noise; the prediction contains
  none of them. That is what the 4 ms floor is for, and it is why the floor's form matters (section 3.1);
- the model has no gain term. `ilc.identify` fits bandwidth, damping and delay to the frequency response from
  0.3 to 12 Hz with unit static gain, and `_tracking` does not remove a static offset. Any static or slow gain
  departure therefore lands in the ratio in full and is invisible to the trust gate (section 3.3).

**Ways a faulty run passes that the tests miss.**

- The model's own release is used whatever `_delivered_release` says about it. Its `flag` and `kind` are not
  looked at in `_plant_prediction`. I found no case where that produced a wrong verdict, and on the 3 Hz plant
  (where the gate calls some recordings "ambiguous") the predicted lag still agreed with good runs to 1 ms.
- `if source == "plant" and np.isfinite(pred["time_err_ms"])` falls through to the fixed-limit branch when the
  prediction is not finite: that run is then held to 25 ms, the release check is skipped, and
  `tracking_limit_source` still says `plant`. I could not construct an input that reaches it (removing the
  guard changes no test, section 5.2). It should either be unreachable by construction or say what it did.
- Setpoint-side faults, below.

**Is the logged-setpoint release check circular?** By construction it is blind to anything that is in the
logged setpoint: the model is driven by the log, so a setpoint that lurches, starts slowly or stalls produces a
model that does the same. Measured (identified plant, eight scenarios x three deliveries, the fault applied to
the setpoint the rig received so that it is in the log and the arm follows it as the plant does):

| fault in every run, on the setpoint | tracking: measured / predicted | tracking flagged | release minus predicted, ms | release flagged | flagged |
|---|---|---|---|---|---|
| none | 0.96 to 1.02 | 0 | -0.2 to +0.4 | 0 | 0 of 24 |
| setpoint lurches 6 deg over 150 ms from +30 ms | 1.34 to 3.11 | 24 | -0.2 to +0.3 | 0 | 24 of 24 |
| setpoint lurches 3 deg | 1.11 to 2.02 | 20 | -0.3 to +0.3 | 0 | 20 of 24 |
| setpoint starts at 50 % over 0.3 s | 1.45 to 1.68 | 24 | -0.3 to +0.2 | 0 | 24 of 24 |
| setpoint starts at 70 % over 0.3 s | 1.15 to 1.35 | 23 | -0.3 to +0.3 | 0 | 23 of 24 |
| Player writes nothing for 20 ms at the release | 0.99 to 1.03 | 0 | -0.1 to +0.3 | 0 | **0 of 24** |
| Player writes nothing for 40 ms at the release | 1.23 to 1.29 | 24 | -0.1 to +0.4 | 0 | 24 of 24 |
| Player writes nothing for 80 ms at the release | 2.74 to 2.88 | 24 | -0.1 to +0.3 | 0 | 24 of 24 |
| Player clock 1 % slow from the release | 0.63 to 0.93 | 0 | -0.2 to +0.3 | 0 | **0 of 24** |
| Player clock 3 % slow from the release | 1.12 to 3.17 | 19 | -0.2 to +0.3 | 0 | 19 of 24 |
| setpoint 10 ms late from the release | 1.47 to 1.56 | 24 | -0.1 to +0.4 | 0 | 24 of 24 |

So the release check sees none of these (as designed), and the command-driven tracking check sees the larger
ones. **The author's choice in B.10 not to drive the tracking prediction from the logged setpoint is
consistent and is load-bearing**: it is the only thing in `score` that compares what the rig received with
the command (`score` does not run `check-log`, and `ilc.locate` takes the best offset without looking at the
residual; the correlation between log and command stayed at 0.997 or more in every row above, so `check-log`'s
0.99 would not have refused them either). Two consequences the report does not draw: a Player fault that
happens to lower the tracking error passes both checks (the 1 % slow clock), and if the tracking ratio is
relaxed or overridden on a real rig, nothing guards the setpoint. A direct, model-free comparison of the logged
setpoint with the command would do this job better than routing it through the ratio (recommendation, section
7).

Could a fault in the setpoint log hide itself? A constant skew between the setpoint and position channels is
absorbed by the identified delay (it is in the chirp too) and cancels; I did not construct a log whose setpoint
channel is wrong in one run only.

**Other paths, read and exercised through `score` (section 5.3).** No chirp planned, chirp log missing, chirp
not identifiable, chirp not trusted, override with and without a chirp: each produced the source, note and exit
status B.1's table says. A chirp log that is a flat line is caught as "not identified" (0 usable frequency
bins), `CHIRP FINDING` is printed, the session falls back to 25 ms and exits 1.

## 2. The author's tables, sampled (question 2)

Own harness: own linear plant, own Player staircase, own fault functions written from B.2's descriptions; only
`session._delivered_release`, `_tracking`, `_plant_prediction`, `_plant_verdict`, `_chirp_trust`,
`chirp_command`, `ilc.locate` and `ilc.identify` come from the branch.

**Good runs, each plant judged against the fit from its own chirp** (66 scenarios x 2 noise seeds, tick phase
random; B.3 has 264 deliveries per plant, this is 132):

| plant | fit (Hz, zeta, delay ms) | R2, misfit | usable | tracking, ms | measured / predicted | release minus predicted, ms | flagged |
|---|---|---|---|---|---|---|---|
| identified, 6.35 Hz, 0.23 | 6.34, 0.234, -0.2 | 0.998, 1.3 % | 132 | 9.1 to 19.1 | 0.96 to 1.04 | -0.3 to +0.5 | 0 |
| 30 % slower, 30 % less damped | 4.46, 0.170, 0.2 | 0.996, 3.3 % | 132 | 20.2 to 36.3 | 1.00 to 1.04 | -0.9 to +0.1 | 0 |
| 3 Hz, zeta 0.7 | 3.00, 0.699, -0.2 | 0.999, 0.7 % | 115 | 4.5 to 39.0 | 0.92 to 1.09 | -0.6 to +0.8 | 0 |
| 8.3 Hz, 0.23 | 8.27, 0.232, -0.0 | 0.998, 1.3 % | 132 | 4.7 to 11.8 | 0.94 to 1.06 | -0.3 to +0.5 | 0 |
| 12 Hz, zeta 0.7 | 11.67, 0.700, -0.5 | 0.994, 1.1 % | 132 | 0.2 to 3.5 | 0.76 to 11.36 | -0.4 to +0.5 | 0 |
| 6.35 Hz, zeta 0.5 | 6.34, 0.502, -0.1 | 0.999, 0.9 % | 132 | 2.7 to 6.3 | 0.85 to 1.13 | -0.3 to +0.5 | 0 |
| identified + 40 ms | 6.34, 0.235, 39.7 | 0.998, 1.4 % | 132 | 9.0 to 19.1 | 0.95 to 1.03 | -0.3 to +0.4 | 0 |

The fits, R2 and misfit match B.3 to the digit printed. **Zero false flags on these seven plants: reproduced.**
With six seeds (396 deliveries) the identified, zeta 0.5, 8.3 Hz and 12 Hz plants stayed at 0 flags; the
largest good-run tracking error at 12 Hz was 3.5 ms (B.3: 3.2, and 3.3 in an uncommitted sweep), 0.5 ms under
the floor.

**Faults in every run** (eight scenarios x three deliveries, chirp healthy). Flagged of usable; "+n" are runs
the release gate flagged first:

| fault | identified | slow, less damped | 8.3 Hz | 3 Hz, 0.7 | 12 Hz, 0.7 |
|---|---|---|---|---|---|
| sluggish start, 50 % over 0.3 s (re-review limit b) | 24 of 24 | 24 of 24 | 24 of 24 | 2 of 2 (+22) | 24 of 24 |
| lurch 6 deg over 150 ms from +30 ms (limit a) | 24 of 24 | 24 of 24 | 24 of 24 | 24 of 24 | 24 of 24 |
| start at 70 % | 24 of 24 | 24 of 24 | 24 of 24 | 12 of 12 (+12) | 24 of 24 |
| start at 80 % | 12 of 24 | 1 of 24 | 17 of 24 | 16 of 17 (+7) | 5 of 24 |
| start at 90 % (declared miss) | 0 of 24 | 0 of 24 | 0 of 24 | 0 of 21 (+3) | 0 of 24 |
| lurch 1 deg (declared miss) | 0 of 24 | 20 of 24 | 6 of 24 | 24 of 24 | 0 of 24 |
| lurch 2 deg | 23 of 24 | 24 of 24 | 6 of 24 | 24 of 24 | 9 of 24 |
| stiction 2 ms (declared miss) | 0 of 24 | 0 of 24 | 3 of 24 | 0 of 24 | 0 of 24 |
| stiction 5 ms | 24 of 24 | 0 of 24 | 24 of 24 | 0 of 24 | 0 of 24 |
| stiction 10 ms | 24 of 24 | 24 of 24 | 24 of 24 | 24 of 24 | 24 of 24 |

- **Both formerly admitted limits are caught: reproduced.** Limit (b): measured / predicted 1.35 to 1.60 on the
  identified plant (B.4: the same), every run under the old 25 ms, release +9.8 to +11.3 ms from predicted.
  Limit (a): release -13.8 to -9.9 ms from predicted, flagged 24 of 24 by the release check; the tracking ratio
  alone catches 12 of 24 (B.4: 12).
- **The declared misses are misses: reproduced.** Start at 90 %, 0 flagged on every plant; lurch 1 deg and
  stiction 2 ms, 0 of 24 on the identified plant, with the release offset carrying the fault's sign (-3.4 to
  -2.0 ms; +1.1 to +1.7 ms).
- Cells differ from B.4 by a few runs where the count is partial (start at 80 %: 12 against 14 on the
  identified plant, 5 against 2 at 12 Hz; stiction 2 ms at 8.3 Hz: 3 against 8). Different noise seeds and
  tick phases; the same picture.

## 3. Model mismatch (question 3)

Every table in this section is **good runs only**: no fault at the release, nothing wrong with the arm beyond
what the row says about the rig. Every flag is therefore a false flag (the two exceptions are marked). The
chirp goes through the same rig as the runs unless the row says otherwise, and the verdict uses the branch's
constants. 66 scenarios x 2 seeds, tick phase random, 132 deliveries per row unless stated. "trk" and "rel" are
runs flagged by the tracking ratio and by the release check.

### 3.1 The author's own model family, between the plants of B.3

Second order plus delay, nothing else, dry-run noise (0.02 deg). 66 scenarios x 4 seeds = 264 per plant.

| plant | usable | tracking, ms | measured / predicted | flagged (all by the tracking ratio) |
|---|---|---|---|---|
| 7 Hz, zeta 0.7 | 264 | 0.7 to 9.4 | 0.82 to 5.37 | **2** |
| 8 Hz, zeta 0.7 | 264 | 0.6 to 7.2 | 0.69 to 4.86 | **1** |
| 10 Hz, zeta 1.0 | 264 | 2.3 to 12.0 | 0.88 to 1.25 | **2** |
| 11 Hz, zeta 1.0 | 264 | 2.1 to 10.5 | 0.92 to 1.18 | **7** |
| 12 Hz, zeta 1.0 | 264 | 1.7 to 9.2 | 0.92 to 1.25 | **10** |
| 10 Hz, zeta 0.7 (6 seeds) | 396 | 0.2 to 5.0 | 0.44 to 8.22 | **1** |
| the other sixteen of 7, 8, 9, 10, 11 Hz x zeta 0.35, 0.5, 0.7, 1.0 (10 Hz zeta 0.7 among them: 0 at four seeds) | 4,219 | | | 0 |

The flagged runs are all of one kind: predicted 3.4 to 5.5 ms, measured 0.6 to 0.9 ms more (for example
`C-A0-high` 5.5 measured against 4.7 predicted at 7 Hz; `R-reflex-sat` 4.9 against 4.2 and `B-habit1` 5.4
against 4.7 at 11 Hz). The staircase and noise add roughly 3 ms in quadrature to whatever the plant produces.
`max(1.15 x predicted, 4)` allows that only while the prediction is under 3.5 ms; from there to about 6 ms
the allowance is 0.5 to 0.9 ms and good runs exceed it. Several plants show ratios over 1.15 without a flag
only because those runs happen to sit under 4 ms (9 Hz zeta 0.5: 1.22 at 3.9 ms).

Through the real program (`plan --chirp`, `.bin` logs, `score` CLI, 20 scenarios chosen to include the ones
above): the 11 Hz, zeta 1.0 rig exits 1 with 2 of 20 good runs "tracking flagged"; the identified plant, the
same way, exits 0 with 20 scored.

A floor added in quadrature, `sqrt((1.15 x predicted)^2 + 4^2)`, evaluated on the same runs (my arithmetic on
the branch's own `tracking_time_err_ms` and `tracking_pred_ms`, not a change I made to the code):

| good runs | usable | flagged, `max` | flagged, quadrature |
|---|---|---|---|
| 7 Hz zeta 0.7; 8 Hz zeta 0.7; 10, 11, 12 Hz zeta 1.0 | 1,320 | 22 | 0 |
| 6.35 Hz zeta 0.5, noise 0.05 deg | 264 | 8 | 0 |
| 6.35 Hz zeta 0.5, noise 0.1 deg | 264 | 58 | 10 |
| 8.3 Hz, noise 0.1 deg | 264 | 15 | 1 |

and what it costs on faults (tracking half only; 24 deliveries each): start at 70 % on the identified plant 23
-> 19 (the release check flags all 24 either way); sluggish 50 %, stiction 5 and 10 ms, lurch 6 deg on the
identified, slow and 12 Hz plants: unchanged in all fifteen cells.

### 3.2 Third order and extra resonance

| rig | fit (Hz, zeta, delay ms) | R2, misfit | measured / predicted (median) | trk | release minus predicted, ms | rel |
|---|---|---|---|---|---|---|
| 6.35/0.23 x real pole at 50 Hz | 6.33, 0.235, 2.9 | 0.998, 1.4 % | 0.95 to 1.04 (1.00) | 0 | -0.4 to +0.4 | 0 |
| ... pole at 30 Hz | 6.32, 0.238, 4.7 | 0.998, 1.6 % | 0.93 to 1.02 (0.98) | 0 | -0.5 to +0.4 | 0 |
| ... pole at 20 Hz | 6.29, 0.244, 6.9 | 0.997, 2.1 % | 0.87 to 1.00 (0.95) | 0 | -0.9 to +0.4 | 0 |
| ... pole at 15 Hz | 6.25, 0.252, 8.7 | 0.996, 3.0 % | 0.82 to 0.96 (0.92) | 0 | -1.2 to +0.2 | 0 |
| ... pole at 10 Hz | 6.17, 0.273, 11.9 | 0.991, 5.0 % | 0.69 to 0.92 (0.83) | 0 | -2.3 to +0.7 | 0 |
| 8.3 Hz x pole at 50 to 10 Hz (five rows) | 8.26 to 8.08 | 0.998 to 0.988 | medians 0.98 to 0.76 | 0 | -2.2 to +0.7 | 0 |
| slow plant x pole at 50 to 10 Hz (five rows) | 4.45 to 4.41 | 0.996 to 0.992 | medians 1.02 to 0.92 | 0 | -3.2 to +0.1 | 0 |
| x anti-resonance 15 Hz / resonance 18 Hz, zeta 0.03 | 6.25, 0.246, -1.4 | 0.995, 3.0 % | 0.85 to 0.99 (0.94) | 0 | -1.4 to +0.4 | 0 |
| x anti-resonance 11 / resonance 13 Hz, zeta 0.05 | 6.06, 0.260, -4.4 | 0.978, 5.3 % | 0.82 to 0.92 (0.85) | 0 | -0.2 to +1.1 | 0 |
| x anti-resonance 8 / resonance 9 Hz, zeta 0.05 | 10.08, 0.433, 8.9 | 0.648, 28 % | chirp refused | | | |
| 0.9 x plant + 0.1 x mode at 18 Hz (zeta 0.05) | 6.19, 0.255, -3.6 | 0.992, 3.9 % | 0.80 to 0.97 (0.89) | 0 | -1.4 to +0.8 | 0 |
| 0.9 x plant + 0.1 x mode at 12 Hz (zeta 0.05) | 6.09, 0.255, -5.7 | 0.983, 4.6 % | 0.83 to 0.96 (0.89) | 0 | +1.1 to +2.9 | 0 |
| 0.8 x plant + 0.2 x mode at 25 Hz (zeta 0.1) | 6.03, 0.280, -7.2 | 0.976, 6.4 % | 0.62 to 0.90 (0.78) | 0 | -2.6 to +0.7 | 0 |

No false flag: unmodelled fast dynamics are absorbed into the identified delay and damping, and they push the
ratio **down**. That direction is quiet, and it costs sensitivity: with good runs at a median 0.83 a fault has
to add 39 % to be flagged by the ratio. Section 3.8 measures what is still caught. A resonance inside the
chirp's band is refused by the trust gate, correctly.

### 3.3 Static and slow gain (the model has no gain term)

| rig (6.35 Hz, 0.23 unless stated) | fit | R2, misfit | tracking, ms | measured / predicted (median) | trk | rel | flagged |
|---|---|---|---|---|---|---|---|
| arm reaches 0.995 x the setpoint | 6.34, 0.235 | 0.998, 1.4 % | 9.2 to 21.0 | 0.96 to 1.58 (1.02) | 13 | 0 | **10 %** |
| 0.99 x | 6.34, 0.236 | 0.998, 1.6 % | 10.4 to 35.9 | 0.95 to 2.71 (1.04) | 30 | 0 | **23 %** |
| 0.98 x | 6.34, 0.239 | 0.998, 2.2 % | 12.0 to 69.0 | 0.93 to 5.23 (1.11) | 48 | 0 | **36 %** |
| 0.97 x | 6.35, 0.241 | 0.998, 3.0 % | 12.3 to 102.9 | 0.92 to 7.85 (1.20) | 81 | 0 | **61 %** |
| 1.005 x / 1.01 x / 1.02 x / 1.03 x | | misfit 1.4 to 2.7 % | up to 104.7 | up to 7.73 | 4 / 6 / 24 / 34 | 0 | 3 / 5 / 18 / 26 % |
| 8.3 Hz, 0.98 x | 8.27, 0.237 | 0.998, 2.2 % | 7.2 to 68.5 | 0.93 to 8.07 (1.21) | 81 | 0 | **61 %** |
| slow dipole at 0.3 Hz, mid-band gain +1 % over dc | 6.34, 0.231 | misfit 1.5 % | 11.4 to 20.0 | 1.01 to 1.23 (1.08) | 8 | 0 | 6 % |
| ... +2 % | 6.34, 0.229 | misfit 1.9 % | 13.2 to 20.7 | 1.05 to 1.50 (1.17) | 85 | 0 | **64 %** |
| ... +3 % | 6.34, 0.227 | misfit 2.5 % | 14.5 to 23.7 | 1.09 to 1.72 (1.26) | 126 | 0 | **95 %** |
| ... -1 % / -2 % / -3 % | | misfit 1.6 to 2.7 % | | medians 0.93 / 0.89 / 0.88 | 0 | 0 | 0 |
| dipole at 0.1 Hz, +1 % / +2 % / +3 % | | misfit 1.5 to 2.6 % | | medians 1.08 / 1.18 / 1.31 | 25 / 90 / 122 | 0 | 19 / 68 / 92 % |
| dipole at 0.1 Hz, -3 % / -5 % | | misfit 2.9, 4.5 % | | medians 1.07 / 1.32 | 27 / 111 | 0 | 20 / 84 % |
| dipole at 1 Hz, +2 % / +3 % | | misfit 1.7, 2.1 % | | medians 1.13 / 1.18 | 17 / 122 | 0 | 13 / 92 % |

A static gain is what an encoder scale that differs from the setpoint's, or a loop without enough integral
action under load, looks like; a slow dipole is what integral action itself leaves in a closed loop (a pole
and zero close together well below the bandwidth). The chirp fit is trusted in every row (R2 0.995 or more, misfit
under 5 %): the fit is dominated by 1 to 10 Hz and cannot see either. The scenarios hit hardest are the slow
ones, where the command's rms speed is small and a constant fraction of a 55 deg hold becomes tens of
milliseconds of "tracking time error". Through the real `score`, 20 runs: 0.99 x gives 7 flagged and exit 1,
0.98 x gives 9. I do not know the size of either effect on the real rig. Nobody does yet, and that is the
point: at 1 % it already decides the session.

### 3.4 Encoder noise and quantisation

| | 6.35 Hz, 0.23 | 8.3 Hz, 0.23 | 12 Hz, 0.7 | 6.35 Hz, 0.5 |
|---|---|---|---|---|
| noise 0.02 deg rms (the dry run) | 0 of 132 | 0 of 132 | 0 of 132 | 0 of 132 |
| noise 0.05 deg | 0 of 132 | 0 of 132 | 0 of 132 | **14 of 132 (11 %)** |
| noise 0.1 deg | 0 of 132 | **13 of 132 (10 %)** | **14 of 132 (11 %)** | **56 of 132 (42 %)** |
| noise 0.2 deg | 4 of 64 (6 %) | 11 of 62 (18 %) | 23 of 62 (37 %) | 22 of 59 (37 %) |
| quantised to 0.09 deg (4,000 counts/rev) | 0 of 132 | 0 of 132 | 0 of 132 | not run |
| quantised to 0.18 deg (2,000 counts/rev) | 0 of 132 | not run | not run | not run |
| quantised to 0.36 deg (1,000 counts/rev) | 2 of 132 (2 %) | 13 of 132 (10 %) | 37 of 132 (28 %) | not run |

All by the tracking ratio; the release check raised none (release minus predicted within 3.3 ms at 0.36 deg,
within 2.1 ms at 0.1 deg of noise). At 0.2 deg of noise the release gate of PR #96 already refuses half the
runs ("usable" 59 to 64 of 132); that gate is not under review here. Noise adds to the measured error and not
to the prediction, so a multiplicative margin cannot absorb it; a plant with a small predicted error (well
damped, or fast) is exposed first. The run's own `enc_hold_rms_deg` measures this noise and is already in the
report.

### 3.5 Mild nonlinearity

Plant integrated with RK4 at 2 kHz; the linear control through the same integrator gives 0 of 132 and ratios
0.96 to 1.04.

| rig (6.35 Hz, 0.23 at the chirp's working point) | fit | misfit | measured / predicted (median) | trk | release minus predicted, ms | rel | flagged |
|---|---|---|---|---|---|---|---|
| loop stiffness -5 % at the 55 deg hold (the chirp is at 0 +- 8 deg) | 6.34, 0.234 | 1.4 % | 0.99 to 1.11 (1.06) | 0 | +0.0 to +1.8 | 0 | 0 |
| ... -10 % | 6.34, 0.234 | 1.7 % | 1.02 to 1.27 (1.10) | 6 | +0.7 to +3.8 | 0 | 5 % |
| ... -20 % | 6.33, 0.234 | 2.6 % | 0.99 to 1.58 (1.21) | 110 | +1.1 to +8.7 | 55 | **88 %** |
| ... +5 % / +10 % / +20 % | | 1.5 to 2.7 % | medians 0.95 / 0.92 / 0.83 | 0 | down to -4.3 | 0 / 0 / 2 | 0 / 0 / 2 % |
| damping -20 % / -10 % / +10 % / +20 % at 55 deg | | 1.3 to 1.4 % | 0.77 to 1.20 | 2 / 0 / 0 / 4 | -2.2 to +2.1 | 0 | 2 / 0 / 0 / 3 % |
| Coulomb friction worth 0.05 deg of loop error | 6.35, 0.236 | 1.5 % | 0.95 to 1.11 (1.04) | 0 | +0.3 to +2.0 | 0 | 0 |
| ... 0.1 deg | 6.35, 0.239 | 1.8 % | 0.94 to 1.25 (1.07) | 5 | +0.6 to +3.5 | 0 | 4 % |
| ... 0.2 deg | 6.34, 0.244 | 2.9 % | 1.00 to 1.45 (1.13) | 48 | +1.2 to +6.9 | 28 | **42 %** |
| loop deadband 0.05 deg | 6.32, 0.234 | 1.4 % | 0.97 to 1.21 (1.10) | 8 | +0.1 to +3.2 | 0 | 6 % |
| loop deadband 0.1 deg (B.6's row) | 6.31, 0.235 | 1.8 % | 0.94 to 1.53 (1.17) | 92 | +0.0 to +7.1 | 32 | **71 %** |
| quadratic damping, +15 % / +30 % / +60 % at 300 deg/s | zeta 0.279 / 0.314 / 0.367 | 2.6 to 4.9 % | medians 1.06 / 1.10 / 1.15 | 0 / 16 / 65 | down to -4.6 | 0 / 0 / 4 | 0 / 12 / 52 % |
| soft torque saturation (tanh) at 80,000 / 50,000 / 30,000 deg/s^2 | 6.27 / 6.15 / 5.82 Hz | 1.9 / 3.4 / 8.7 % | medians 0.98 / 0.95 / 0.86 | 0 | -2.2 to +0.3 | 0 | 0 |
| loop gain +10 % / +20 % at 5 deg of error | 6.90 / 7.40 Hz | 3.0 / 5.6 % | medians 1.08 / 1.14 | 7 / 49 | up to +6.2 | 0 / 125 | 5 / **95 %** |
| loop gain -10 % at 5 deg of error | 5.72 Hz | 4.6 % | 0.80 to 0.98 (0.92) | 0 | -4.4 to -1.1 | 5 | 4 % |
| load the loop does not cancel, steady error 0.21 / 0.43 deg at 55 deg | 6.34, 0.234 | 1.3 % | up to 1.61 / 2.76 | 4 / 11 | -0.2 to +0.6 | 0 | 3 / 8 % |

B.6's deadband figure reproduces on the author's eight scenarios at phase 0 (1.15 to 1.20, 8 of 8; B.6 has 1.17
to 1.20 with a differently written deadband). Over the library it is 0.94 to 1.53 and 71 %. Whether a 0.1 deg
deadband or 0.1 deg of friction is "a fault the check found" or "this rig" is a judgement, and section B makes
it in favour of "fault" (B.6 item 1). On a rig where it is simply how the drive behaves, the session cannot
pass. The chirp is trusted in every row, and it has to be: it runs at 0 +- 8 deg and the releases start at 55.

### 3.6 The chirp at a slightly different gain than the runs

Runs on 6.35 Hz, 0.23; the chirp recorded on a plant that differs as stated (B.9 lists this as not verified).

| chirp plant | fit | measured / predicted (median) | trk | release minus predicted, ms | rel | flagged |
|---|---|---|---|---|---|---|
| bandwidth x 0.90 | 5.71, 0.231 | 0.78 to 0.92 (0.88) | 0 | -3.7 to -1.6 | 0 | 0 |
| x 0.95 / x 0.97 | 6.02 / 6.15 | medians 0.94 / 0.96 | 0 | -1.9 to -0.2 | 0 | 0 |
| x 1.03 / x 1.05 | 6.54 / 6.67 | medians 1.05 / 1.07, up to 1.14 | 0 | +0.5 to +1.9 | 0 | 0 |
| x 1.10 | 7.00, 0.235 | 1.06 to 1.27 (1.14) | 55 | +1.6 to +3.4 | 0 | **42 %** |
| damping x 0.8 / x 0.9 / x 1.1 | zeta 0.189 / 0.211 / 0.256 | medians 0.90 / 0.95 / 1.06 | 0 | -1.1 to +2.0 | 0 | 0 |
| damping x 1.2 | 6.34, 0.279 | 1.05 to 1.22 (1.13) | 10 | -1.9 to -0.0 | 0 | 8 % |

Five per cent either way is tolerated; ten per cent of bandwidth is not, and the release offset is then within
0.3 to 0.6 ms of its own limit.

### 3.7 The Player, and a composite

The Player with up to 6 or 9 ms of lateness, a 5 or 20 ms period, or no lateness, on the 6.35, 8.3 and 12 Hz
plants (fifteen rows): 1 flag in 1,980 good runs (12 Hz with 9 ms lateness, measured 4.1 ms). With 5 % of
writes dropped: 6, 58 and 0 of 132 flagged; with 20 %: 3, 10 and 65 (the 12 Hz chirp is then refused). Those
are Player faults and the flags are arguably right; they are counted in the totals of the verdict.

Several mild departures at once, on the identified plant:

| rig | fit | misfit | measured / predicted (median) | trk | release minus predicted, ms | rel | flagged |
|---|---|---|---|---|---|---|---|
| stiffness -3 % at 55 deg, friction 0.03 deg, dipole +0.5 %, pole at 40 Hz, noise 0.03 deg, 8,000 counts/rev | 6.33, 0.237, 3.6 | 1.5 % | 0.99 to 1.25 (1.09) | 5 | +0.0 to +1.8 | 0 | 4 % |
| stiffness -5 %, friction 0.05 deg, dipole +1 %, pole at 30 Hz, noise 0.05 deg, 4,000 counts/rev | 6.32, 0.238, 4.7 | 1.9 % | 1.00 to 1.43 (1.14) | 49 | +0.3 to +4.0 | 0 | **37 %** |
| the same with the stiffness and dipole signs reversed | 6.32, 0.243, 4.7 | 2.4 % | 0.77 to 1.03 (0.90) | 0 | -1.4 to +1.9 | 0 | 0 |
| stiffness -10 %, friction 0.1 deg, dipole +2 %, pole at 20 Hz, noise 0.05 deg, 4,000 counts/rev | 6.29, 0.244, 6.8 | 2.6 % | 1.06 to 1.86 (1.28) | 125 | +0.6 to +6.5 | 14 | **95 %** |

### 3.8 Faults on rigs where good runs sit below the prediction

Eight scenarios x three deliveries; flagged of 24, with the tracking / release split.

| rig | sluggish 50 % | start at 70 % | stiction 5 ms | stiction 10 ms | lurch 6 deg | lurch 2 deg |
|---|---|---|---|---|---|---|
| identified (control) | 24 (24 / 24) | 24 (23 / 24) | 24 (24 / 0) | 24 (24 / 24) | 24 (12 / 24) | 23 (6 / 23) |
| x pole at 15 Hz | 24 (24 / 24) | 24 (16 / 24) | **0** | 24 (24 / 24) | 24 (6 / 24) | 24 (0 / 24) |
| x pole at 10 Hz | 24 (24 / 24) | 24 (8 / 24) | **0** | 24 (24 / 24) | 24 (6 / 24) | 24 (0 / 24) |
| dipole at 1 Hz, mid-band gain -5 % | 24 (20 / 24) | 24 (0 / 24) | **0** | 24 (16 / 24) | 24 (6 / 24) | 20 (0 / 20) |
| dipole at 0.3 Hz, mid-band gain -3 % | 24 (24 / 24) | 24 (13 / 24) | 1 (0 / 1) | 24 (24 / 24) | 24 (6 / 24) | 16 (0 / 16) |
| 0.8 x plant + 0.2 x mode at 25 Hz | 24 (24 / 24) | 24 (2 / 24) | **0** | 24 (24 / 24) | 24 (12 / 24) | 20 (3 / 20) |

Both admitted limits stay caught on every row, by the release check. What is lost is what only the tracking
ratio caught: 5 ms of stiction goes from 24 of 24 to 0.

### 3.9 Answer

1.15 is not a property of the method; it is the spread of this simulator's Player and noise on plants with a
predicted error above about 6 ms. It is 0.01 above the worst good run of B.3 because B.3's plants are where it
was set. Between those plants it is already exceeded (3.1), and outside the model family the ratio moves by
far more than 0.15 in either direction for departures the trust gate cannot see: up (loud: good runs flagged,
exit 1) for static gain, slow dynamics, softening at the hold angle, friction and noise; down (quiet:
sensitivity lost) for unmodelled fast dynamics. I would not defend the claim that a real rig's good runs will
sit under 1.15, and neither does the author (B.9). I do think it is acceptable to merge with a placeholder,
provided that (i) the placeholder does not false-flag inside its own model family, (ii) the documentation says
what section 3 measured and not only "a 0.1 deg deadband", and (iii) relaxing it does not cost the release
check. Those are required changes 1 to 3.

The release check against the logged setpoint is a different matter: a difference of two onset times that share
the Player's phase and the locate offset. It was within 4 ms on good runs in every linear, noise, quantisation,
chirp-gain and Player probe, and its tolerance has real room (good runs within 1 ms on the model family,
within 3.7 ms at a 10 % bandwidth error). It is the result of this branch.

## 4. The chirp trust gate (question 4)

Rate-limited setpoint ahead of the plant, the chirp through the same rig, 132 good deliveries per row. The
second figure is the same runs judged against a healthy chirp's fit, which separates "this run really was
rate-limited" (a true flag: a few library scenarios are themselves limited at 400 deg/s) from "the chirp was".

| rate limit | fit from this rig's chirp | R2, misfit | trusted | flagged against its own chirp (trk, rel) | flagged against a healthy chirp |
|---|---|---|---|---|---|
| 600 deg/s | 6.32, 0.232, 1.2 | 0.998, 1.5 % | yes | 0 | 0 |
| 400 | 6.31, 0.231, 2.1 | 0.996, 2.1 % | yes | 4 (4, 0) | 4 |
| 300 | 6.24, 0.230, 3.0 | 0.990, 4.5 % | yes | 8 (8, 0) | 8 |
| 275 | 6.19, 0.231, 3.3 | 0.985, 5.8 % | yes | **16** (8, 8) | 8 |
| 250 | 6.12, 0.234, 3.6 | 0.977, 7.6 % | yes | **66** (8, 58) | 10 |
| 225 | 6.02, 0.240, 4.1 | 0.963, 9.9 % | yes | **130** (10, 124) | 11 |
| 210 | 5.96, 0.247, 4.7 | 0.953, 11.5 % | no (misfit) | fixed 25 ms, exit 1 | |
| 200 | 5.92, 0.254, 5.2 | 0.945, 12.6 % | no (R2) | fixed 25 ms, exit 1 | |
| 150 | 5.71, 0.317, 9.1 | 0.911, 18.2 % | no (R2) | fixed 25 ms, exit 1 | |
| 8.3 Hz plant, 300 | 8.27, 0.248, 4.2 | 0.973, 6.3 % | yes | **57** (8, 49) | |
| 8.3 Hz plant, 250 | 8.32, 0.272, 6.2 | 0.936, 10.4 % | no (R2) | | |
| slow plant, 250 | 4.46, 0.168, 4.6 | 0.989, 5.6 % | yes | **32** (8, 24) | |
| slow plant, 225 | 4.45, 0.167, 5.4 | 0.984, 7.0 % | yes | **130** (10, 124) | |

**The gap reproduces**, with three corrections to B.6 item 3 and the README:

- it is not "225 to 250 deg/s". It opens at 275 on the identified plant, sits at 300 on the 8.3 Hz plant
  (refused by 250) and at 225 to 250 on the slow plant;
- at 250 deg/s it is 58 of 132 good runs blamed as "early", not 2 of 8: the author's eight scenarios are among
  the least affected;
- tightening the misfit limit to 7 % (B.6 item 3's untaken trade) would not close it on the slow plant, where
  the chirp is trusted at 5.6 and 7.0 % misfit with 24 and 124 runs blamed.

**Is blaming the runs acceptable?** As a documented limit, yes, and not blocking. The failure is loud (every
run flagged the same way, exit 1), no run is scored as a clean delivery that should not be, and the README's
recognition rule held in every row above (flags all "early", by a similar amount, `plant_misfit` over 5 %).
The sentence the operator reads is wrong, though: "the arm did not leave the hold as this rig's plant does
(early: a lurch, or something pushing the arm)". The program has what it needs to say so itself: when most
scored runs are flagged in the same direction by a similar amount and `plant_misfit` is over 5 %, it can print
one session-level sentence that the chirp is the suspect, as `_session_lag` already does for a consistent
lag. The underlying cause is that the chirp asks for 500 deg/s (8 deg at 10 Hz) and a release for much less; a
chirp whose amplitude falls with frequency so that its peak speed stays near the library's would not saturate
a drive that the runs do not. Both are recommendations.

## 5. Tests, gates and mutations (question 5)

### 5.1 Gates, ran at `b0d18c9`

| gate | author | reproduced |
|---|---|---|
| `python -m pytest neuromech/tests -q` | 228 passed in 269 s | **228 passed** in 272 s, 0 failed, 0 skipped |
| `python neuromech/designer/parity/run_parity.py` | PARITY OK, 355 cases | **PARITY OK**, 355 cases, exit 0 (run while probes were using the machine) |
| `python -m neuromech.dryrun --seed 11 --discard` | 9 of 9, no flags, exit 0 | **9 of 9** in band on encoder and phone, scored 9, release flagged 0, tracking flagged 0, exit 0; lag 27.4 ms (22.7 to 28.4); plant 6.34 Hz, 0.234, -0.2 ms, R2 0.998, misfit 1.2 %; tracking 1.00 of predicted (0.98 to 1.01); release +0.2 ms (-0.0 to +0.3); limits 15.9 to 21.8 ms |

Every figure in B.11's dry-run row matched.

### 5.2 Mutations

Each mutant is one textual change to `session.py` in a private copy of the package, run against
`neuromech/tests/test_session_dryrun.py` (129 tests). B.8 changed each constant by a large step (off, or to a
value far away); I changed them by small steps, and I changed branches.

**Constants.** Three tests assert the constants themselves: `test_the_plant_derived_limit_in_numbers` (the
literals), `test_with_a_chirp_each_run_is_held_to_its_own_plants_prediction` (the printed sentence
"max(1.15 x predicted, 4 ms)" and `limit == 1.15 x predicted`) and one line of
`test_a_chirp_the_plant_cannot_follow_is_not_trusted` (`(CHIRP_MIN_R2, CHIRP_MAX_MISFIT) == (0.95, 0.10)`).
Those are worth having, and they follow the number wherever a later edit takes it. The column "behaviour"
is the result with those assertions set aside (the first two tests deselected, the one line removed).

| mutant | whole file | behaviour | killed by |
|---|---|---|---|
| margin 1.15 -> 1.12 | killed | killed | the two "not caught" cases, the real-file good session, the slower rig |
| margin 1.15 -> 1.20 | killed | **survives** | only the printed sentence |
| margin 1.15 -> 1.25 | killed | **survives** | only the printed sentence |
| floor 4 -> 3 ms | killed | **survives** | only the printed sentence |
| floor 4 -> 6 ms | killed | **survives** | only the printed sentence |
| release tolerance 4 -> 6 ms | killed | killed | the real-file sluggish session |
| release tolerance 4 -> 3 ms, 4 -> 2 ms | killed | killed | the 1 deg lurch "not caught" case |
| chirp R2 0.95 -> 0.90 | killed | killed | the 150 deg/s chirp, in memory and through files |
| chirp R2 0.95 -> 0.92 | killed | **survives** | only the literal |
| chirp R2 0.95 -> 0.975 | killed | **survives** | only the literal |
| chirp misfit 0.10 -> 0.20 | killed | killed | the 2 deg stick-slip chirp |
| chirp misfit 0.10 -> 0.13, 0.10 -> 0.075 | killed | killed, by `test_a_chirp_the_plant_cannot_follow_is_not_trusted` | that test asserts the reason contains "(limit 10 %)"; its chirps have misfits of 18.2, 14.5 and 1.3 %, and the neighbouring test's 1 deg stick-slip chirp 7.2 %, all on the same side of 7.5, 10 and 13 %, so I take the kill to be the printed limit and not the behaviour (inferred, not separated by a run) |

So the suite pins the margin from below and not from above (nothing in it is a fault between 1.15 and 1.25
that only the ratio catches: 5 ms of stiction is that fault, at 1.19 to 1.25 with the release inside its
tolerance), does not pin the floor in either direction (B.3 records that a 3 ms floor would have flagged one
run "in an uncommitted harness"; no committed test fails at 3), and pins the chirp R2 only against 0.90.

**Branches.** Nothing deselected.

| mutant | result |
|---|---|
| `_tracking_limit`: `max` -> `min` | killed (23 tests) |
| release check flags late only; early only | killed; killed |
| "late" / "early" wording swapped | killed |
| the fixed-limit branch never flags | killed |
| the override does not stop the plant judging | killed |
| an untrusted chirp is used anyway | killed |
| a missing chirp log does not record its reason | killed |
| `chirp_flags` ignores "not trusted" | killed |
| release predicted from the command instead of the logged setpoint | killed (15 tests) |
| release prediction skipped (no logged setpoint passed) | killed |
| `CHIRP FINDING` not printed | killed |
| identified delay applied with the wrong sign; ignored | killed; killed |
| a plant-flagged run still counts as a delivery | killed |
| R2 branch never refuses; misfit branch never refuses | killed; killed |
| tracking half of the plant verdict off | killed |
| `tracking_limit_source` always `plant`; override recorded as `plant` | killed (the first by a crash in a fixture); killed |
| **`main()` exit status ignores `chirp_flags`** | **survives** |
| **`chirp_flags` ignores a chirp that was "not identified"** | **survives** |
| **the `try` around `ilc.identify` catches nothing** | **survives** |
| **a chirp command that does not move is not refused** (`len(moving) < 100`) | **survives** |
| **misfit taken over the whole record, not while the chirp is moving** | **survives** |
| **the model is not run past the command's end** (`n1 = 0`) | **survives** |
| **rows without a per-run limit carry 0, not the fixed number** (`fillna`) | **survives** |
| **the non-finite-prediction guard removed** | **survives** |

The first survivor matters most. "A chirp the rig cannot follow is a finding, so `score` exits 1" is one of
the four behaviours the change is described by, and no test fails when the exit status stops looking at it:
`test_an_untrusted_chirp_falls_back_and_fails_the_exit_status` scores a rig limited to 150 deg/s, whose runs
are over the fixed 25 ms on their own, so the status is 1 through `tracking_flags` either way. The behaviour
itself is there: my flat-line chirp session has 20 runs scored, none flagged, and exits 1 (section 5.3). The
next two are the unidentifiable-chirp path, which no test enters. The rest are small.

### 5.3 Paths through the real program

One planned session (`plan --chirp`, 20 scenarios + `R000`), `.bin` logs written by my harness, scored through
`session.main(["score", ...])`:

| logs | exit | `tracking_limit_source` | scored | tracking flagged (tracking reason / early / late) | release flagged | limits, ms |
|---|---|---|---|---|---|---|
| identified plant | 0 | plant | 20 | 0 | 0 | 10.7 to 17.8 |
| 11 Hz, zeta 1.0 (good runs) | **1** | plant | 18 | **2** (2 / 0 / 0) | 0 | 4.0 to 8.6 |
| 12 Hz, zeta 1.0 (good runs) | 0 | plant | 20 | 0 | 0 | 4.0 to 7.5 |
| arm reaches 0.99 x the setpoint (good runs) | **1** | plant | 13 | **7** (7 / 0 / 0) | 0 | 10.7 to 17.6 |
| arm reaches 0.98 x the setpoint (good runs) | **1** | plant | 11 | **9** (9 / 0 / 0) | 0 | 10.6 to 17.5 |
| 6.35 Hz zeta 0.5, noise 0.1 deg (good runs) | **1** | plant | 13 | **7** (7 / 0 / 0) | 0 | 4.0 to 6.9 |
| chirp recorded at bandwidth +10 % (good runs) | **1** | plant | 12 | **8** (8 / 0 / 0) | 0 | 8.6 to 15.1 |
| lurch 6 deg in every run | 1 | plant | 1 | 17 (11 / 17 / 0) | 2 | 15.8 to 25.0 |
| lurch 6 deg in every run, `--tracking-limit-ms 25` | 1 | override | **12** | 6 | 2 | 25.0 |
| lurch 6 deg in every run, `R000.bin` missing | 1 | fixed | **12** | 6 | 2 | 25.0 |
| sluggish 50 % in every run, `--tracking-limit-ms 25` | **0** | override | **20** | 0 | 0 | 25.0 |
| `R000.bin` is a flat line | 1 | fixed | 20 | 0 | 0 | 25.0 |

The in-memory harness and the real program agree in kind (the 12 Hz, zeta 1.0 session happened to draw no
flag in 20 runs; over 264 it draws 10). The lurch session has one run of 20 that neither check flagged with a
trusted chirp; B.4's 24 of 24 is on the eight review scenarios, and this session has twelve others. The 25.0 at the top of the limits column in that
row is a release-flagged run carrying the fixed number, as the code comments say it does.

The two override rows are the fourth point of the verdict. They behave exactly as documented ("judged by MS
alone"). The half-speed start with `--tracking-limit-ms 25` exits 0 with 20 of 20 scored, which is `1505fb8`'s
behaviour and the admitted limit (b) back again.

## 6. Line endings, README, report, columns (question 6)

- `git diff --stat 1505fb8 b0d18c9` and the same with `--ignore-cr-at-eol` are identical (5 files, 1071
  insertions, 33 deletions); the diff contains no carriage return; `git ls-files --eol` gives `i/lf w/lf` for
  all five files.
- README, "228 tests": true. README dry-run paragraph (1.00 of predicted, 0.98 to 1.01; +0.2 ms; 15.9 to 21.8
  ms; 9 of 9): true.
- README, "a start at 90 % speed, a 1 deg lurch and 2 ms of stiction pass": true on the identified plant.
- README, "A drive that saturates only slightly on the chirp (limited to 225 to 250 deg/s in the simulation)
  is still trusted and makes good runs look 4 to 5 ms early": the mechanism is true; the range is for one plant
  and one set of eight scenarios (section 4). Required change 3.
- B.3, "No good run is flagged on any of the fifteen plants": I reproduced seven of the fifteen and found no
  flag. As a statement about the method it does not hold between them (section 3.1). Required change 1.
- B.3, "1.15 ... is close on both sides: 0.01 above the worst good run": true of those plants. B.9's "a 0.1 deg
  deadband alone takes them past the margin": true, and the least of it (section 3). Required change 3.
- B.7, the release check predicted from the command moves with the tick phase and the one predicted from the
  logged setpoint does not: consistent with what I measured (good runs within 1 ms at random tick phase on
  every plant of section 2), and the mutant that predicts the release from the command is killed (section
  5.2).
- B.6, stick-slip rows: re-implemented from the description and reproduced on the author's eight scenarios
  at phase 0. Breakaway 0.25 deg: 1.04 to 1.08, 0 of 8. 0.5 deg: 1.12 to 1.24, 4 of 8. 1.0 deg: 1.35 to 1.76,
  8 of 8, misfit 7.2 %, trusted. 2.0 deg: misfit 14.5 %, refused. All as B.6 has them. Over the 66 scenarios
  the smaller ones reach further than the eight suggest: 2 of 66 at 0.25 deg, 27 of 66 at 0.5 deg.
- B.8's mutation table: not re-run as written; section 5.2 is my own. B.8's "No constant can be zeroed or
  opened with the suite still passing" is true and is a weaker statement than it reads: see 5.2.
- `report.csv` columns: `tracking_pred_ms`, `tracking_limit_ms`, `enc_release_lag_pred_ms`,
  `enc_lag_minus_pred_ms`, `tracking_limit_source`, `tracking_limit_note` and `plant_misfit` are named in the
  README; `tracking_pred_ms`, `enc_lag_minus_pred_ms`, `tracking_limit_source` and `tracking_limit_note` in
  the `session.py` docstring. **`plant_trusted` and `plant_untrusted_why` are documented only in B.1**, not in
  the README or the docstring. Minor.

## 7. Changes

### Required

1. **The floor.** Good runs on second-order plants between B.3's samples are flagged (7 of 264 at 11 Hz zeta
   1.0, 10 of 264 at 12 Hz zeta 1.0, exit 1 through `score`; section 3.1). The floor is there for error the
   prediction does not contain, and that error does not vanish when the prediction passes 3.5 ms. Adding it
   in quadrature removed all 22 flags in five plants and cost 4 of 24 tracking flags on one fault that the
   release check catches anyway. Whatever form is chosen: a committed test on a plant in that region (the
   current tests cover 12 Hz zeta 0.7, where the prediction is under 3 ms, and plants where it is over 6), and
   B.3's sentence corrected to what was sampled.
2. **Do not make the release check the price of relaxing the tracking ratio.** Today the choice is the plant
   (both checks) or `--tracking-limit-ms` (neither). Section 3 says the ratio will probably need relaxing on
   the first real session; section 5.3 shows what is lost when it is. A separate, recorded option for the
   margin, or an override that keeps the release check, either would do.
3. **Say what was measured.** B.9 and the README's provisional paragraph should carry the sensitivity of the
   tracking ratio in numbers (at least: static gain, slow dipole, stiffness at the hold angle, noise, chirp
   gain; this review's tables can be cited or re-measured), say plainly that a tracking flag against the
   plant means "more error than the fitted model predicts" and is not yet evidence about the arm, and replace
   "225 to 250 deg/s" and "2 of 8" with the plant-dependent picture of section 4.
4. **Tests.** (a) A session whose only reason to exit 1 is the chirp finding (runs clean under the fixed limit,
   chirp refused), and one whose chirp cannot be identified at all: today the exit status can stop looking at
   `chirp_flags`, and `chirp_flags` can stop reporting "not identified", with every test passing. (b)
   Behavioural tests that fail when the margin is 1.20 or 1.25 and when the floor is 3 or 6, without relying
   on the literal or the printed sentence (5 ms of stiction on the identified plant is the natural case for
   the margin; change 1's test covers the floor). (c) The remaining survivors of section 5.2 are small; pin
   them or, for the non-finite-prediction guard, make the branch say what it did.

### Recommended, not blocking

- A direct comparison of the logged setpoint with the command in `score` (the residual after `ilc.locate`), so
  that Player faults are caught by something that does not depend on the plant model or the margin (section 1).
- A session-level sentence when most runs are flagged the same way by a similar amount and `plant_misfit` is
  over 5 %: suspect the chirp (section 4). And a chirp whose peak speed is bounded near the library's.
- Use the run's own `enc_hold_rms_deg` to allow for encoder noise (section 3.4).
- A gain term in the model, or the mean offset over the hold removed before `_tracking`, considered together
  with what ILC needs from `ilc.identify` (section 3.3). Not a small change and not one to make without rig
  data.
- `plant_trusted` and `plant_untrusted_why` in the README's column list.
- The reason sentence for a tracking flag ends "the arm did not follow this command". On the evidence of
  section 3 it should claim less.

## 8. Not verified

- **Anything about the real rig.** No rig, no LabVIEW log, no phone. Every mismatch in section 3 is a guess at
  what a rig might do, at magnitudes I chose; none is a measurement of this rig. The sizes that matter (static
  gain, slow dynamics, friction, encoder noise and resolution) are unknown to me.
- B.3's other eight plants, B.4's other eleven faults and two other plants, the "fault pasted onto the chirp"
  table and the acceleration-limit rows of B.6: not re-run. B.6's deadband, stick-slip and rate-limit rows
  were (sections 3.5, 4 and 6).
- B.8's mutation table as written, and the claim that the 52 new tests fail on `1505fb8`'s `session.py`.
- The phone path against the new checks (the checks are on the encoder; the dry run's phone columns were
  unchanged in the gate).
- ILC-corrected commands through the new path; one faulty run among good ones; half a session faulty.
- A log whose setpoint channel is wrong in one run only; a chirp and runs with different Player behaviour.
- More than one chirp run in a session (the last one read decides; read in the code, not run).
- The mutation runs used a private copy of the tracked `neuromech` package and root modules with the library
  built in the review worktree; `test_session_dryrun.py` only (129 tests), not the other test files.
- `nm_figs`, the Rust core, the C# designer beyond the parity gate, the PT scorer and the band thresholds:
  untouched by the branch and not examined.

## 9. How this was run

Windows, project `.venv`, `PYTHONIOENCODING=utf-8`, `OPENBLAS_NUM_THREADS=1`, a detached worktree at
`b0d18c9`, removed afterwards together with the `neuromech/out` and `neuromech/sessions` it produced. The
harness (not committed):

- **A delivery**: scenario command -> a Player staircase written for the review (10 ms period, up to 3 ms of
  lateness per write, tick phase 0 or uniform over 0 to 10 ms; period, lateness and dropped writes variable) ->
  1.5 s of idle log before and 1.0 s after -> a rig -> optional fault on the arm's motion or on the setpoint
  -> white encoder noise (0.02 deg rms unless stated) -> optional quantisation -> `ilc.locate` ->
  `_delivered_release`, `_tracking`, `_plant_prediction`, `_plant_verdict` exactly as `score` calls them.
- **A rig**: linear ones are continuous transfer functions discretised with a first-order hold (checked
  against `dryrun.plant_response` to 8e-12 deg); nonlinear ones are `y'' = f(u, y, v)` integrated with RK4
  at 2 kHz with the setpoint interpolated inside the sample (checked against the linear plant to 2e-8 deg).
  None of them is `session._model_response`.
- **A chirp**: `session.chirp_command()` delivered through the same rig, the same Player and the same noise,
  located and identified as `score` does, and passed through `_chirp_trust`. Each rig is judged against the
  fit from its own chirp unless the row says otherwise.
- **Faults**: written from B.2's descriptions, not copied from the test module.
- **Real-file runs** (section 5.3): `session.plan(..., chirp=True)` into a temporary sessions root, `.bin`
  logs in LabVIEW's seven-channel big-endian layout, `session.main(["score", ...])`, exit status read from
  `SystemExit`.
- **Mutations**: one private copy of the package per mutant, one textual change in `session.py`, then
  `pytest neuromech/tests/test_session_dryrun.py`.
