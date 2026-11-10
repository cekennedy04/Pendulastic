# Neuromech dry run: why two runs miss their band (2026-11-10)

Diagnosis only. No scenario, scorer or threshold was changed. Everything under
"Measured" was run on `origin/main` at `75109c5` in a clean worktree with the
project `.venv`; everything under "Inferred" is labelled as such.

## Summary

- `B-claspknife` and `M-catch_release_high` are **one scenario built twice**:
  both are `Params(catch_gain=4.0, catch_vth=1.2, catch_release_deg=25.0)`
  (`neuromech/scenarios.py:99`, `neuromech/profiles.py:71`). Their setpoints,
  intended PT7 (0.1394) and delivered PT7 are identical. The "two misses" are one.
- The 0 / 1 band edge is PT7 = 0.12 (`pendulastic_pt_score._MAS`, `pt <= 0.12`
  is band "0"). The scenario intends 0.139, **0.019 above the edge**, and the
  dry run delivers 0.116 (encoder), 0.004 below it.
- The 0.023 undershoot is not tracking error in the usual sense and it is not
  the 100 Hz staircase. **55 % of it (0.0128) is one extra half-cycle in N**,
  counted because the scorer is handed the *command's* release time while the
  delivered motion starts about 15-20 ms later; 30 % (0.0070) is the plant's
  lightly damped resonance raising peak velocity by 7 %; the rest is small.
- It is **deterministic**: 40 delivery seeds give 0.1146-0.1168 (sd 0.0006),
  band 0 every time; five full dry runs (seeds 1, 3, 7, 11, 42) all give 7 of 9.
- The same N half-step hits **40 of 67 scenarios**. It only shows as a miss
  where the intended PT7 is within about 0.02 of an edge. Across the whole
  library, 5 of 67 flip band under the dry-run delivery, not 2.
- It became a miss at **PR #79** (HEALTHY_REF re-derivation, merged `1792bbc`,
  2026-10-28). The undershoot was already there before #79; #79 moved the
  intended PT7 from 0.174 to 0.139 and so removed the margin that hid it.

## Reproduction

```
python -m neuromech.build            # 67 scenarios
python -m neuromech.dryrun --seed 11
```

```
run   scenario                  track  PT7 int    enc  phone   band int/enc/phone
R001  M-tone_L2                  0.51    0.225  0.203  0.204   1/1/1
R002  M-tone_L4                  0.28    0.420  0.415  0.416   1+/1+/1+
R003  A-P1p                      0.35    0.365  0.350  0.350   1+/1+/1+
R004  M-fall_no_ringdown         0.58    0.343  0.337  0.338   1+/1+/1+
R005  B-clonus                   1.22    0.080  0.075  0.075   0/0/0
R006  C-f-high                   2.79    0.235  0.238  0.238   1/1/1
R007  B-claspknife               0.73    0.139  0.115  0.129   1/0/1
R008  M-catch_release_high       0.73    0.139  0.116  0.117   1/0/0
R009  A-P0                       0.80    0.060  0.053  0.053   0/0/0

delivered band == intended band: 78% of 9 runs;  median tracking RMSE 0.73 deg
```

Note that `M-tone_L2` undershoots by the same amount (0.022) and passes only
because it sits mid-band. The premise that the other scenarios undershoot "far
less" holds for some (A-P0, A-P1p, M-tone_L4: 0.005-0.015) but not all.

## Measured

### 1. Where the edge is and how bands are assigned

`neuromech/session.py::_summ` takes the band from `mas_estimate(...)["mas"]`,
which is `pt_to_mas`: the Popovic table `_MAS = [(0.12,"0"),(0.28,"1"),
(0.44,"1+"),(0.60,"2"),(0.78,"3")]`. The intended band comes from the same
function applied to the model trace at build time (`neuromech/build.py::score`).
`PT_HEALTHY_MAX` / `PT_BORDERLINE_MAX` (the zone thresholds) are not involved.

### 2. Which stage of the delivery moves PT7

The command was scored through the session's own path (`session._score_trace`)
after each stage of `dryrun.write_encoder_bin` applied separately (seed 11):

| B-claspknife | PT7 | band | N | omega_max_n | tracking RMSE |
|---|---|---|---|---|---|
| command, scored as a log | 0.1392 | 1 | 6.0 | 3.712 | 0 |
| 100 Hz staircase only | 0.1388 | 1 | 6.0 | 3.761 | 0.30 deg |
| plant only | 0.1159 | 0 | 6.5 | 3.999 | 0.63 deg |
| plant + staircase | 0.1161 | 0 | 6.5 | 3.986 | 0.88 deg |
| plant + staircase + encoder noise | 0.1159 | 0 | 6.5 | 3.985 | 0.88 deg |

The staircase costs 0.0004 and the 0.02 deg encoder noise nothing. The plant
model (2nd order, 6.35 Hz, zeta 0.23) accounts for all of it.

### 3. Which scored terms move

Per-term contribution to PT7 (`compute_pt_score_breakdown`), command vs full
delivery:

| term | command | delivered | change | share |
|---|---|---|---|---|
| N | 0.0128 | 0.0000 | -0.0128 | 55 % |
| omega_max_n | 0.0712 | 0.0642 | -0.0070 | 30 % |
| phi_max_ratio | 0.0457 | 0.0437 | -0.0020 | 9 % |
| R2n | 0.0029 | 0.0018 | -0.0011 | 5 % |
| f | 0.0066 | 0.0061 | -0.0005 | 2 % |
| area_ratio | 0 | 0 | 0 | |
| **PT7** | **0.1392** | **0.1159** | **-0.0233** | |

N is counted in half-cycles against a reference of 6.5 over six scored terms,
so one half-cycle is worth 0.5 / (6 x 6.5) = 0.0128 of PT7. N went 6.0 -> 6.5.
The margin to the edge is 0.0194, so the N step alone does not cross it
(0.1264, band 1) and neither does the velocity term alone; both together do.

### 4. Where the extra half-cycle comes from

It is not in the tail. The last counted extrema are the same in the command
and the delivered trace (+3.04 vs +3.10 deg at 6.6 s, against a 2.75 deg
threshold; the next one, 2.4 deg, is rejected in both). The extra one is the
**first**: a "peak" 0.07 s after release, at phi = +53.6 deg, i.e. while the leg
is still at the top of its first drop.

The scorer detects extrema in a swing-centred frame (`phi - _swing_centre`,
a one-period boxcar). At the start of the record that centre falls quickly, so
the centred trace rises for a few samples before it falls. Reproducing the
scorer's own steps on both traces:

| | centred-frame local max after release | prominence | threshold (`min_amp`) | result |
|---|---|---|---|---|
| command | at 0.05 s | 1.68 deg | 2.75 deg | rejected, N 6.0 |
| delivered | at 0.07 s | 4.15 deg | 2.76 deg | counted, N 6.5 |

The delivered trace is scored from the command's release time
(`session.score`: `t_rel = t[argmax(phase == 2)]`), but the arm starts moving
later than the command. That lag is what lifts the shoulder over the threshold.
Two direct tests:

- A **pure delay** of the command, with no change of shape, flips N by itself:
  B-claspknife at >= 15 ms (PT7 0.1392 -> 0.1261), M-tone_L2 at >= 20 ms
  (0.2250 -> 0.2100), A-P0 at >= 10 ms (N 6.5 -> 7.0, no PT7 effect because N is
  only penalised below 6.5). A pure gain of 0.98-1.05 changes nothing.
- On the full delivery, handing the scorer a release **20 ms later** removes
  the extra half-cycle: B-claspknife N 6.0, PT7 0.1281, band 1; M-tone_L2 N 4.5,
  PT7 0.2184. It holds from +20 to +60 ms (A-P0 needs +30 ms).

So the first 0.0128 is a release-alignment artefact of how the session scores a
log, exposed by a count that is knife-edged at the start of the record.

### 5. What the catch contributes

Delivered / commanded omega_max_n is 1.073 for the catch scenario against a
library median of 1.033 (A-P0 1.030, M-tone_L2 1.035). The catch-and-release
is therefore about twice as sensitive to the plant in this one term, worth
0.0070 of PT7 here against 0.0035-0.0040 for a plain swing. This is the part
the catch behaviour makes sensitive; it is the smaller part.

Plant sweep on B-claspknife (no staircase, no noise):

| wn \ zeta | 0.23 | 0.40 | 0.70 | 1.00 |
|---|---|---|---|---|
| 4 Hz | 0.1015 (0) | 0.1164 (0) | 0.1281 (1) | 0.1397 (1) |
| 5 Hz | 0.1091 (0) | 0.1191 (0) | 0.1269 (1) | 0.1353 (1) |
| 6.35 Hz | **0.1159 (0)** | 0.1212 (1) | 0.1264 (1) | 0.1324 (1) |
| 8 Hz | 0.1209 (1) | 0.1229 (1) | 0.1264 (1) | 0.1304 (1) |
| 10 Hz | 0.1374 (1) | 0.1248 (1) | 0.1263 (1) | 0.1290 (1) |
| 15 Hz | 0.1380 (1) | 0.1387 (1) | 0.1260 (1) | 0.1277 (1) |

The band depends on the rig's damping and bandwidth. At the identified tune it
is band 0; a modestly better-damped or faster loop gives band 1 by 0.001-0.003.
Only where the shoulder is not counted (N 6.0: the 0.137-0.139 cells) does the
scenario deliver what it intends.

### 6. Determinism

- 40 seeds of the delivery (Player jitter + encoder noise) on each of
  B-claspknife and M-catch_release_high: PT7 0.1146-0.1168, mean 0.1157,
  sd 0.0006, N 6.5 and band 0 in all 40. The edge is 7 sd away.
- Full `python -m neuromech.dryrun` at seeds 1, 3, 7, 11, 42: 7 of 9 every
  time, the same two runs, encoder PT7 0.115-0.117.
- The **phone** band for these two is not stable across seeds: phone PT7 is
  0.114-0.117 in seven of ten readings and 0.128-0.129 in three (seeds 1, 11,
  42), i.e. band 0 or 1 depending on the seed.

### 7. Whole library under the same delivery (seed 11)

All 67 scenarios, command vs delivered through the session path:

- N changed in 43; in 40 the delivered trace has a counted extremum within
  0.2 s of release that the command does not.
- Median PT7 change -0.0066; the scenarios that take the N step and are
  penalised for it sit at -0.020 to -0.025.
- **5 flip band**: B-claspknife and M-catch_release_high (1 -> 0, 0.019 from
  the edge), R-friction (1 -> 0, intended 0.1365, 0.017 from the edge),
  B-flexor (1+ -> 1, intended 0.291, 0.011 from the edge), D-short (2 -> 1+,
  intended 0.441, 0.001 from the edge).
- Near an edge but not flipped: C-R2n-low (0.275, 0.005 below 0.28),
  M-tone_L1 (0.114, 0.006 below 0.12), H-HM3 (0.291), M-tone_L4 (0.420).

### 8. ILC

Three iterations of `ilc.update` through the same synthetic delivery:

| iteration | tracking RMSE | PT7 | band | N |
|---|---|---|---|---|
| 0 | 0.88 deg | 0.1155 | 0 | 6.5 |
| 1 | 0.28 deg | 0.1180 | 0 | 6.5 |
| 2 | 0.17 deg | 0.1192 | 0 | 6.5 |
| 3 | 0.14 deg | 0.1204 | 1 | 6.5 |

ILC recovers the velocity part and never the N half-step. After three
corrections the scenario is in band by 0.0004. (M-tone_L2: 0.2043 -> 0.2100
against an intended 0.2251, N stays 5.0.)

### 9. History

`git log --since=2026-10-26` on `neuromech/` and `pendulastic_pt_score.py`,
then three commits exported with `git archive`, built and dry-run with today's
`.venv`:

| commit | date | B-claspknife intended -> delivered | bands | default run |
|---|---|---|---|---|
| `2139e7f` emulator landed (the "9/9" note is in this commit) | 10-26 | 0.236 -> 0.214 | 1 / 1 | 8 of 9 (M-catch_release_high 0.283 -> 0.219, 1+ -> 1; it was a different render then) |
| `4ea9cb7` main just before #79 | 10-27 | 0.174 -> 0.153 | 1 / 1 | 8 of 9 (B-clonus 0.134 -> 0.102) |
| `1792bbc` #79 merged | 10-28 | 0.139 -> 0.116 | 1 / 0 | default list no longer builds (A-P2); the two catch runs miss |
| `75109c5` today | 11-03 | 0.139 -> 0.116 | 1 / 0 | 7 of 9 |

- The undershoot is 0.021-0.024 at every commit. It predates #79.
- #79 changed the outcome. With the command's own parameters, the old
  reference gives omega_max_n 0.1020 + phi_max_ratio 0.0504 + f 0.0056 +
  N 0.0128 + R2n 0.0029 = 0.174; the new one gives 0.139. Almost all of the
  drop is the omega_max_n reference going 9.57 -> 6.48 (term 0.102 -> 0.071).
- I could not reproduce 9 of 9 at the commit that carries the note; it gives
  8 of 9 at seed 11 with today's environment. The default scenario list has
  also changed twice since (A-P3 -> M-tone_L4, A-P2 -> A-P1p), and `9510d2d`
  (#89) already documents the two misses in `dryrun.py` and pins them in
  `test_default_session_misses_exactly_the_two_edge_scenarios`.

## Inferred (not measured)

- The dry-run lag is about what the model predicts: the plant's low-frequency
  delay is 2 zeta / wn = 11.5 ms and the Player's hold adds about 6.5 ms on
  average (half the 10 ms period plus mean lateness), 18 ms together, just over
  the 15 ms at which the shoulder is counted. That arithmetic matches the
  measurements but the two contributions were not separated experimentally
  beyond section 2.
- The real rig will show the same behaviour **if** its closed loop resembles
  the identified plant. The dry run cannot test the real rig's nonlinearity,
  and section 5 shows the band depends on damping and bandwidth.
- The phone's seed-dependent band (section 6) is probably the same shoulder
  being counted or not depending on where the phone's 59 Hz samples fall
  relative to the release; I did not trace it.
- The same start-of-record sensitivity could affect real phone trials whose
  release index lands 15 ms or more before the drop. Not checked on the corpus.

## Options

1. **Report band-edge scenarios as such.** In the session report, flag any run
   whose intended PT7 is within a stated distance of an edge (0.03 would cover
   every flip found here) and report band agreement with and without them.
   Cost: a small change to `session._print` and the report columns, one test.
   Changes no number. Leaves a 0.013 bias on 40 of 67 scenarios in place and
   leaves the rig's first sessions reporting misses that are not delivery
   faults.

2. **Retarget the catch scenario away from the edge.** Measured sweep with
   `catch_vth` and `catch_release_deg` as shipped: `catch_gain` 5 -> intended
   0.166, delivered 0.141; `catch_gain` 6 -> intended 0.198 (band-1 centre is
   0.20), delivered 0.172; 7 -> 0.208 / 0.183. Cost: a scenario-definition
   change in two places that must stay identical (`scenarios.py`,
   `profiles.py`), a library rebuild, the pinned 7-of-9 test rewritten, and a
   check on `severity_grading_test`, which iterates the profiles. Fixes this
   scenario only; R-friction, B-flexor and D-short still flip, and the next
   reference re-derivation can move any scenario onto an edge again.

3. **Tighten tracking with ILC.** Cost: three extra rig runs per scenario per
   session, and re-identification after any gain change. Measured result: in
   band by 0.0004 after three iterations, with N still wrong. Not sufficient
   on its own.

4. **Score the delivered motion from its own release.** In
   `neuromech/session.py`, hand the scorer the release of the delivered motion
   rather than the command's: either the command release plus the lag the
   session already measures (the chirp identification, or the setpoint vs
   position cross-correlation), or the drop onset detected on the encoder.
   Measured by shifting the release in a script, not by implementing it: +20 ms
   restores N in the three scenarios tested and puts B-claspknife at 0.128,
   band 1. Cost: a change to the session scorer only (not
   `pendulastic_pt_score.py`, no threshold), every delivered number
   re-baselined, the pinned test updated, and the choice of lag estimate needs
   care (A-P0 needed +30 ms). Leaves a residual of about -0.011 on the catch
   scenario from the plant's resonance, which is real delivery error and is
   what ILC is for; B-claspknife would then sit 0.008 above the edge, so it
   remains an edge scenario.

5. **Make the N count robust at the start of the record** (in the scorer).
   Cost: a scorer change, so corpus re-validation and the Rust and C# ports
   re-brought to parity. Out of scope for this diagnosis; listed because the
   knife-edge lives there and option 4 only stops the session from stepping on
   it.

## Recommendation

Option 4, before the first real rig session, with the edge flag of option 1
added in the same change. More than half of the miss is the session scoring a
log from the wrong instant, not the rig failing to deliver, and it biases 40 of
67 scenarios by one half-cycle of N. Retargeting first would move one scenario
off the edge and leave that bias in every delivered-vs-intended comparison the
rig sessions are meant to produce. Once the release is aligned, what remains
(about 0.011 here) is genuine tracking error, ILC addresses it, and whether the
catch scenario should also be moved to mid-band (option 2, `catch_gain` 6) is a
separate and smaller decision. Whether to harden the scorer's N count
(option 5) should wait for a corpus check of how often a real trial's first
counted extremum falls within 0.2 s of release.

## Not done

- Nothing was run on rig hardware or on real phone recordings.
- Option 4 was not implemented; its effect was measured by shifting the
  release time passed to the unchanged scorer.
- The historical commits were run from `git archive` exports with the current
  `.venv`, not the environment of their day.
- The 9-of-9 note was not reproduced.
