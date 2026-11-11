# Review: neuromech session release alignment (2026-11-11)

Independent review of `origin/neuromech/session-release-alignment` at `e2257cc` ("score the delivered
motion from the delivered release"), against `origin/main` at `e9a3a01`. The reviewer did not write the
change. Nothing was pushed to that branch and no study data was read.

Everything under "ran" was run in a throwaway worktree at `e2257cc` with the project `.venv`, on a library
built there (`python -m neuromech.build`, 67 scenarios). It is all simulation. The review harness is not
committed; section 9 says what it does so the numbers can be reproduced.

## Verdict

**Approve with required changes.**

The central design decision is sound and I could not break it: the onset-matched release is where the
delivered arm actually leaves the hold, by markers that know nothing about the score, and it follows the
plant when the plant is changed. It was not chosen only because it makes the scores match.

What is not ready is the guard around it. A hold that moves can give a release that is wrong by 20 to
60 ms with no flag, the gates are absolute numbers taken from one simulated plant, and the rewritten test
does not notice the method drifting by 8 ms.

Required, in priority order:

1. **Make an ambiguous onset fail.** Hold motion gives the fit competing minima, and the wrong one can sit
   inside the plausible range under the 0.3 deg limit (section 5). The residual does not separate right
   from wrong, so tightening it is not enough: check that the delivered hold is still before the release,
   check that the minimum is unique, and add tests with hold motion.
2. **Add a session-level lag check and stop gating on absolute lag alone.** The lag is a rig property. A
   run far from the session median is the suspect one. A session where every run sits at one consistent
   lag outside -10 to +80 ms is a rig that differs from the model, and the tool should say that and offer
   a recorded override instead of leaving every run unscored with a per-run message (section 4).
3. **Fix the phone's lower gate.** The phone trace reads 8 to 20 ms earlier than the encoder at 59 Hz, so
   a phone trace is flagged at -10 ms whenever the arm tracks well (section 4). Gate the phone against
   the encoder only, or correct the offset.
4. **Pin the method in the tests.** Changing the onset from 2 deg to 10 deg passes all 34 tests in
   `test_session_dryrun.py` (section 6). Pin the dry-run lag and a multi-scenario N count.
5. **Correct two statements in the report and docstrings** (section 7): the residual limit is not "15 %
   of the onset", and the N count does not support 2 deg over any other release between +30 and +50 ms.

Recommended, not blocking: score the phone over the command's span (one argument; it removes the
`D-short` phone discrepancy, section 6), and count flagged runs in the printed percentages.

## 1. The central question: right definition, or fitted to the score?

**Answer: it is the right definition, on the evidence below. The author's own argument for it (it fixes
more scenarios) is not the evidence; the evidence is where the arm is at that instant.**

### What the release has to mean

`build.score` scores the command with `release_idx = len(pre)`: the first swing sample, which still
equals the hold. So the intended score is taken from the last still sample. `compute_pt_params` then
reads `A0` from the release sample and lays the swing-centre boxcar from it, and every still sample after
the release raises the start-of-record shoulder. For the delivered score to be comparable, the delivered
release has to be the same thing: the last instant the delivered arm is still at the hold. That is "the
limb leaves the hold", not "the shift that best overlays the two traces".

### Where the onset-matched release sits (ran)

All 67 scenarios, baseline plant, seed 11, through an in-memory copy of the encoder path (Player hold,
plant, 0.02 deg noise, `ilc.locate`). Independent markers on the delivered trace:

- *band exit*: the last sample within x deg of the hold level before the trace leaves for good;
- *parabola*: constant-acceleration fit over 1 to 6 deg of fall, extrapolated back to the hold;
- *tangent*: line through the 10 % and 30 % points of the first drop, extrapolated back.

| release handed to the scorer | lag, median (min to max), ms | N differs from intended | band differs |
|---|---|---|---|
| commanded (before) | 0 | 44 | 5 |
| cross-correlation over the first drop | 14.5 (12.0 to 24.5) | 33 | 5 |
| **onset match (the change)** | **27.4 (22.6 to 33.9)** | **10** | **0** |
| band exit, 0.1 deg | 34.0 | 12 | 0 |
| band exit, 0.2 deg | 41.0 | 9 | 0 |
| band exit, 0.5 deg | 53.0 | 11 | 0 |
| parabola back-extrapolation | 30.6 | 12 | 0 |
| tangent back-extrapolation | 81.5 | 11 | 7 |
| constant +20 ms | | 24 | 2 |
| constant +30 ms | | 10 | 0 |
| constant +40 ms | | 10 | 0 |
| constant +50 ms | | 10 | 1 |
| constant +60 ms | | 11 | 1 |

(The lags are in the session's time base, where `locate` places the command about 3.5 ms late; the
author's 19.2 ms and 30.4 ms are the same quantities in the true time base. My cross-correlation window
is my own, so its counts differ from the author's 25 / 3. Band-exit minima on `D-wobble` are meaningless
and excluded from the medians' interpretation.)

Three things follow.

- **At the onset-matched release the delivered arm is 0.04 deg from its hold level** (median; 0.37 deg
  maximum outside `D-wobble`), against 0.02 deg of encoder noise. It has not visibly moved.
- **It is early, not late, relative to a human mark.** A 0.1 deg band marker lands 6.5 ms after it, a
  0.2 deg marker 13.5 ms after, the parabola 4.0 ms after. The same band markers applied to the command
  itself land 11 ms and 16 ms after the command's true release, so the onset-matched release sits where
  the command's own release sits relative to a human's eye: at the back-extrapolated start.
- **The cross-correlation point is 13 ms earlier still**, at an instant when the arm is flat. It is the
  right number for "how late is the swing" and leaves still samples in the record. The author's claim on
  this point holds.

So the 19 ms versus 30 ms question is settled by the trace, not by the score: with no dead time in the
plant the arm still stays within noise of the hold for about 27 ms, because the Player holds the first
change for up to 13 ms and a second-order plant answers a parabola with a quartic.

### What the author's evidence does not show

The N and band counts cannot tell the 2 deg onset from any other release in a wide plateau: a constant
+30 or +40 ms gives the same 10 and 0. "Fixes 34 of 44 against 19 of 44" shows that the release must be
later than 19 ms; it does not show that 27 ms is the right amount. The report should say so. What
separates the measured lag from a constant is section 2: the constant stops working as soon as the plant
changes.

### The threshold dependence

The author's figures (30.5 ms at 1 deg down to 20.6 ms at 10 deg) are the rounded corner, and inside the
method the choice is flat where it matters: onset 1, 2 deg and pre-window 0.02, 0.05 s all give a median
of 27.4 to 27.5 ms and 10 or 11 wrong N. At 5 deg the median is 24.3 ms (13 wrong, 2 flagged) and at
10 deg 19.7 ms (15 wrong of 56, 11 flagged). 2 deg is inside the flat region, not on a tuned peak.

## 2. Robustness under a changed plant (ran)

67 scenarios per plant, seed 11. "human" is the score from the independent 0.1 deg band marker. Flagged
runs are excluded from the "after" counts.

| plant | flagged | lag median (min to max, excl. D-wobble), ms | N wrong before / after | band wrong before / after | N wrong at constant +30 / +40 ms | band wrong, human marker |
|---|---|---|---|---|---|---|
| identified (6.35 Hz, 0.23) | 0 | 27.5 (22.6 to 28.4) | 42 / 10 | 5 / 0 | 10 / 10 | 0 |
| wn -30 % | 1 | 38.6 (30.5 to 42.6) | 50 / 14 | 6 / 3 | 25 / 13 | 3 |
| wn -20 % | 1 | 34.4 (27.5 to 36.7) | 49 / 12 | 5 / 0 | 14 / 10 | 0 |
| wn +20 % | 0 | 22.3 (17.5 to 23.6) | 40 / 7 | 3 / 0 | 8 / 9 | 0 |
| wn +30 % | 0 | 20.3 (15.6 to 22.1) | 40 / 8 | 3 / 0 | 8 / 9 | 0 |
| zeta -30 % | 0 | 26.3 (20.4 to 26.9) | 44 / 11 | 5 / 0 | 11 / 11 | 0 |
| zeta +30 % | 0 | 28.6 (23.7 to 29.6) | 42 / 10 | 5 / 0 | 7 / 8 | 0 |
| wn -30 %, zeta -30 % | 1 | 37.5 (29.8 to 41.7) | 50 / 13 | 7 / 2 | 26 / 13 | 2 |
| wn +30 %, zeta -30 % | 0 | 18.8 (13.5 to 21.5) | 39 / 7 | 3 / 0 | 7 / 8 | 0 |
| + 5 ms transport delay | 0 | 32.5 (27.6 to 33.4) | 46 / 9 | 5 / 0 | 9 / 10 | 0 |
| + 10 ms | 0 | 37.5 (32.6 to 38.4) | 50 / 10 | 5 / 0 | 23 / 10 | 0 |
| + 20 ms | 0 | 47.5 (43.3 to 48.4) | 48 / 10 | 5 / 0 | 40 / 23 | 0 |
| + 40 ms | 0 | 67.4 (62.6 to 68.4) | 47 / 9 | 6 / 0 | 50 / 44 | 0 |
| + 60 ms | **67** | 87.5 (83.3 to 88.4) | 48 / none scored | 6 / none scored | 47 / 48 | 0 |
| wn -30 % + 20 ms | 1 | 58.6 (50.3 to 62.5) | 49 / 14 | 7 / 3 | 44 / 42 | 3 |
| zeta 0.5 | 0 | 32.3 | 43 / 10 | 3 / 1 | 8 / 8 | 1 |
| zeta 0.7 | 0 | 35.4 | 39 / 11 | 2 / 1 | 17 / 4 | 1 |
| zeta 1.0 | 0 | 39.5 | 33 / 14 | 4 / 2 | 20 / 16 | 2 |
| 3 Hz, zeta 0.7 | 0 | 61.6 (41.3 to 74.5) | 41 / 17 | 5 / 4 | 39 / 34 | 4 |
| 12 Hz, zeta 0.7 | 0 | 20.6 | 31 / 7 | 2 / 1 | 4 / 6 | 0 |

- **The method is not tuned to the identified plant.** Across plus or minus 30 % in bandwidth and
  damping and up to 40 ms of pure delay, N wrong falls from 31-50 to 7-14 and the measured lag moves with
  the plant. A transport delay of d ms moves the lag by d to within 0.1 ms.
- **A constant offset is tuned to the plant; the measurement is not.** +30 ms is as good as the method on
  the identified plant and leaves 40 of 67 wrong with 20 ms of delay.
- **The band misses that remain are the plant's.** With wn -30 % the two catch scenarios and `H-HM3`
  leave their band; the independent marker gives the same three. With heavier damping it is `D-short`,
  and the independent marker agrees. The release is not manufacturing or hiding these.
- **It tracks the independent marker**: onset minus 0.1 deg band exit is -10.5 to -0.5 ms (median per plant) across the
  plus or minus 30 % set. It drifts only on a slow overdamped arm (3 Hz, zeta 0.7: +11.5 ms, arm 0.21 deg
  off the hold at the declared release).
- The single flag in the slow-plant rows is `D-wobble` (section 5). With 60 ms of delay all 67 runs are
  flagged (section 4).

### Phone sample rate and jitter (ran)

In-memory copy of the phone path (`_align_phone`, `_delivered_release`, `_score_trace`), 67 scenarios,
baseline plant.

| phone rate, jitter sd | sync refused | flagged | N wrong before / after | band wrong before / after | phone lag minus encoder lag, median (min to max), ms |
|---|---|---|---|---|---|
| 200 Hz, 0.5 ms | 0 | 0 | 42 / 12 | 5 / 1 | -1.9 (-4.4 to +0.2) |
| 100 Hz, 1.0 ms | 0 | 0 | 35 / 13 | 6 / 1 | -4.9 (-9.2 to +0.4) |
| 59 Hz, none | 0 | 0 | 26 / 13 | 3 / 1 | -13.9 (-16.1 to -12.2) |
| 59 Hz, 1.5 ms | 0 | 0 | 32 / 13 | 5 / 1 | -10.0 (-16.2 to -2.0) |
| 59 Hz, 4.0 ms | 0 | 0 | 27 / 13 | 5 / 1 | -9.4 (-19.3 to +1.2) |
| 30 Hz, 1.5 ms | 0 | 3 | 19 / 16 | 2 / 1 | -24.3 (-63.7 to -5.8) |
| 30 Hz, 4.0 ms | 1 | 4 | 20 / 16 | 1 / 1 | -22.0 (-68.3 to -7.1) |
| 25 Hz, 3.0 ms | 1 | 18 | 15 / 10 | 1 / 1 | -28.4 (-85.2 to -9.6) |

The phone result holds from 59 to 200 Hz and jitter does not hurt it. The one band miss in every row is
`D-short` (section 6). The phone-minus-encoder offset is not a clock error: it scales with the sample
period (about 0.6 to 0.8 of a sample), so it comes from integrating and interpolating a corner on a
coarse grid. It was there before this change. It matters for the gates (section 4).

## 3. Can a genuinely wrong delivery be reported as in band? (ran)

Yes, in three of the cases I built. In none of them is the cause a release slid past the true departure;
in two the session has nothing that looks at the fault.

Eight scenarios (`A-P0`, `B-claspknife`, `M-tone_L2`, `R-friction`, `B-flexor`, `D-short`, `M-tone_L4`,
`A-P1p`), baseline plant, with a fault added to the arm's motion. "human" scores from the 0.1 deg band
marker.

| fault at the release | flagged | in band: before / after / human | lag, median ms | tracking RMSE, median deg | what the session says |
|---|---|---|---|---|---|
| none | 0 | 4 / 8 / 8 | 27.5 | 0.66 | |
| sticks 40 ms, then catches up | 0 | 4 / 7 / 6 | 53.6 | 0.77 | scored, lag reported |
| sticks 60 ms | 0 | 4 / 5 / 4 | 64.3 | 0.86 | scored worse (mean PT7 -0.019) |
| sticks 100 ms | 8 | | 90.3 | 1.18 | flagged: lag |
| early swing at 50 % gain, recovering over 0.3 s | 0 | 4 / 5 / 5 | 37.5 | 0.90 | scored worse (-0.026) |
| at 30 % gain, over 0.3 s | 0 | 4 / 4 / 4 | 43.4 | 1.02 | scored worse (-0.034) |
| **at 30 % gain, over 0.6 s** | 0 | 5 / **8** / 6 | 49.0 | **2.70** | **in band, no flag** |
| kicks back 1.5 deg off the hold | 8 | | | 0.67 | flagged: onset mismatch |
| kicks back 3 deg | 8 | | | 0.69 | flagged |
| lurches forward 3 deg | 8 | | | 0.66 | flagged: lag |
| **lurches forward 6 deg over 150 ms, from +30 ms** | 0 | 7 / 7 / 7 | **4.4** | 0.62 | **scored; lag wrong by about 25 ms, no flag** |
| **hold sags 1 deg in the last 0.3 s** | 0 | 4 / **8** / 4 | 31.6 | 0.63 | **in band, no flag** |
| **hold sags 2 deg** | 0 | 4 / **8** / 4 | 35.5 | 0.60 | **in band, no flag** |
| slack: drops 2 deg at the release | 8 | | | 0.61 | flagged: lag |
| slack: drops 4 deg | 8 | | | 0.59 | flagged |

- **Slow start over 0.6 s.** The arm delivers 30 % of the early swing, tracking error is 1.6 to 6.8 deg,
  and all eight runs are in band with PT7 within 0.016 of intended. The release is not the mechanism: it
  is within 5 ms of the independent marker and the two score alike (`B-claspknife` 0.125 against 0.126).
  The scorer is insensitive to this fault and the session has no gate on tracking error. The only trace
  it leaves is a lag of 48 to 51 ms where the rest of a session would show 27. That is what required
  change 2 is for.
- **Hold sag.** The arm creeps 1 to 2 deg before the release. The fit takes the release 0.9 to 1.7 deg
  into that motion (residual 0.06 to 0.12 deg, under the limit) and reports eight of eight in band, where
  the commanded release gave four and a marker at the first departure gives four. `A0` is read low by the
  sag. This one is the release absorbing a hold fault. A check that the hold is still over the pre-window
  would catch it.
- **Forward lurch.** The lag is reported as 0.3 to 5.9 ms when the arm left at about 29 ms; residual 0.17
  to 0.21 deg, under the limit. Bands are unchanged here, but the audit column is wrong and unflagged.
- Stiction is handled honestly: the lag follows it, the score gets worse, and beyond 80 ms it is flagged.
  Kick-back and slack are flagged.

## 4. The gates

**Lag, -10 to +80 ms.** Justified only by the simulated distribution ("about three times the simulated
value"); the report says so. Consequences I ran:

- 60 ms of extra transport delay puts every run at 83 to 88 ms: **67 of 67 flagged, nothing scored**,
  exit status 1. On a real rig whose command path adds latency the model does not have, that is the first
  session. Refusing is defensible once; the message is not. Each run prints `lag +87.5 ms is outside the
  plausible -10 to +80 ms`. Nothing says the lag is the same on every run, that this is the rig and not a
  fault, that the limit came from a simulation, or what to do. There is no override short of editing the
  constant. A person at the bench will read 67 faults.
- Within the range a single bad run is invisible: the 49 ms slow start above passes in a session whose
  other runs sit at 27 ms.

Both are fixed by the check the author considered and did not build: compare each run with the session
median, and treat a consistent out-of-range session as a rig finding with a clear message and a recorded
override.

**Phone lower limit.** The phone trace reads earlier than the encoder by a sample-rate-dependent amount
(section 2), and the same -10 ms limit is applied to it. Ran:

- arm at 20 Hz, zeta 0.7, with the Player hold: encoder lag 13.4 ms, phone lag median -0.9 ms, 2 of 63
  phone traces flagged at -11 and -12 ms;
- arm tracking almost perfectly: encoder lag 5.3 ms, the three phone traces that synced all flagged at
  -10 to -13 ms;
- after three ILC iterations on the baseline plant (as `dryrun.ilc_demo` runs them): encoder lag 17.5 to
  18.7 ms, phone lag +0.4 to +3.8 ms, 10 to 14 ms from the limit;
- a 30 Hz phone on the baseline plant: 3 to 4 of 67 flagged.

So the better the rig tracks, the more phone traces go unscored. The phone already has the right check
(`PHONE_SYNC_TOL_S` against the encoder); the absolute limit should not apply to it, and the 35 ms
tolerance should be centred on the known offset or the offset removed in `_align_phone`.

**Residual, 0.3 deg.** The report calls it "15 % of the onset". The fit removes the mean, and the
reference window (50 ms of hold plus the first 2 deg) has a standard deviation of 0.56 deg (median over
67 commands; 0.49 to 1.11). An arm that never leaves the hold scores 0.56. The limit is 54 % of the
signal, and a clean fit scores 0.03 (0.11 at most outside `D-wobble`). The limit is loose by about a
factor of two, but it is also the wrong instrument: among the unflagged hold-oscillation runs of section 5,
releases off by more than 10 ms have residuals from 0.07 to 0.29 deg and releases within 5 ms have
residuals up to 0.24 deg. A limit of 0.15 would catch 48 of the 63 wrong ones and flag 13 of 139 good
ones.

**Phone tolerance, 35 ms.** "Two phone samples" at 59 Hz, but with a systematic -8 to -14 ms offset the
headroom is one sample on one side and three on the other.

**Fallback to the commanded release.** Read: there is none. A flagged encoder run `continue`s before
`_score_trace`; a flagged phone trace sets `phone_status` only; `ilc_demo` writes NaN. A search optimum at
the edge of the range lands outside the plausible range and is flagged. The one silent path is the wrong
minimum inside the range (section 5).

## 5. Hold motion: a wrong release without a flag (ran)

This is the main defect.

**`D-wobble` in the final code.** Its command wobbles 3 deg at 2.5 Hz in the hold. Against `M-tone_L0`,
the same scenario without the wobble:

| plant | D-wobble lag, ms | M-tone_L0 lag, ms | residual, deg | result |
|---|---|---|---|---|
| identified | 32.3 to 34.6 | 26.4 | 0.19 to 0.20 | scored, 6 to 8 ms late, N correct |
| zeta -30 % | 34.5 to 35.7 | 25.4 | 0.18 | scored, 9 to 10 ms late |
| wn -8 % | 34.7 to 36.3 | 28.6 to 28.9 | 0.24 | scored |
| wn -10 % and slower | -29.5 | 29.4 to 33.4 | 0.25 to 0.28 | flagged (lag) |
| **wn -10 % to -20 %, + 20 ms delay** | **-9.7 to -9.3** | 49.3 to 53.4 | 0.25 to 0.27 | **scored, 60 ms early, no flag** |
| **wn -10 % to -20 %, + 25 to 30 ms delay** | **-5.4 to +0.6** | 54.3 to 61.4 | 0.25 to 0.27 | **scored, 60 ms early, no flag** |

The cost function has two minima about 64 ms apart. Which one wins depends on the plant, and the wrong
one is flagged only when it happens to fall below -10 ms. A rig a little slower than the model with 20 ms
of latency scores `D-wobble` from a release 60 ms before the arm leaves the hold (PT7 0.048 against the
sibling's 0.038; N and band survive because the scenario is mid-band). The author's own first window
(0.10 s) did this at 0.6 ms on the identified plant; the 0.05 s window moved the problem, it did not
remove it.

**Hold oscillation the command did not ask for.** A decaying oscillation added to the delivered hold of
five ordinary scenarios (`A-P0`, `B-claspknife`, `M-tone_L2`, `R-friction`, `D-short`), eight phases each:

| amplitude, frequency | flagged of 40 | lag error among the unflagged, ms | unflagged with error over 10 ms | N changed | band changed |
|---|---|---|---|---|---|
| 0.2 deg, 2.5 Hz | 0 | -2.9 to +2.8 | 0 | 0 | 0 |
| 0.2 deg, 6.35 Hz | 0 | -5.0 to +4.1 | 0 | 0 | 0 |
| 0.5 deg, 2.5 Hz | 0 | -7.9 to +6.1 | 0 | 0 | 0 |
| **0.5 deg, 6.35 Hz** | 0 | **-20.9 to +9.2** | **10** | 4 | **4** |
| **1.0 deg, 2.5 Hz** | 0 | **-18.1 to +11.7** | **14** | 2 | **4** |
| **1.0 deg, 6.35 Hz** | 25 | **-36.0 to +17.0** | **15** | 4 | **4** |
| 1.5 deg, 2.5 Hz | 6 | -35.2 to +16.2 | 23 | 4 | 5 |

Half a degree of ringing at the plant's own resonance, which is what a lightly damped arm (zeta 0.23) does
after the lift, moves the release by up to 21 ms and changes the reported band in 4 of 40 runs with
nothing flagged. `B-claspknife` and `R-friction` return to band 0, the miss this change exists to remove.
The four runs that change band have residuals of 0.11 to 0.20 deg.

Required. The residual cannot do this job alone (section 4: wrong releases at 0.07 deg, right ones at
0.24). What distinguishes these runs is visible before the fit: the delivered trace is not still in the
hold. So (a) measure the delivered hold over the last few tenths of a second before the release and flag,
or at least mark as uncertain, a run whose hold moves by more than the command's own hold plus noise;
(b) reject a fit whose cost has a second minimum nearly as deep as the first; (c) compare the lag with
the session median (required change 2), which would have caught the large errors in both tables; and
(d) test them, including `D-wobble` on a plant other than the identified one. `D-wobble` itself commands
a moving hold, so for it the honest outcome is "release uncertain", reported as such.

## 6. The rewritten test, and the listed regressions

### `test_default_session_delivers_every_band_and_the_edge_scenarios_stay_on_their_edge`

The old test asserted that the set of misses is exactly the two catch scenarios. It guarded two things:
nothing else misses, and the known behaviour is pinned. The new test asserts every run is scored and in
band, the two catch runs have the intended N, 0.12 < PT7 < 0.132, and the undershoot exceeds 0.005.

- It still guards "nothing misses", and it would catch a return to the commanded release. Rewriting it
  was right: the old pin asserted a defect of the session program.
- **The bounds are the new numbers with slack, not principles.** `> 0.12` repeats the band check. `< 0.132`
  is the observed 0.127 to 0.129 plus 0.003. The undershoot bound (`> 0.005`) implies `< 0.134` and is the
  one with a physical reading (the plant's share, 0.011).
- **It does not pin the method.** I set `RELEASE_ONSET_DEG` to 10. The median lag goes from 27.4 to
  19.7 ms, 11 of 67 scenarios are flagged and 15 of the remaining 56 have a wrong N. **All 34 tests in the
  file pass**, because the nine default runs and the pure-delay cases do not move. No test fixes the lag on
  the dry-run plant: `test_the_release_is_recorded_for_every_scored_trace` only checks it is inside the
  -10 to +80 ms gate.
- Reproduced through the real dry run: 9 of 9 at seeds 11, 5 and 23 (5 and 23 were not among the
  author's), catch runs at 0.127 to 0.128, lag median 27.4 to 27.5 ms.

Required: assert the default session's encoder lag (median 25 to 30 ms on this plant, say), and assert N
against intended on a wider set than the nine. The pure-delay tests are good and should stay.

### Regressions

| item | finding | evidence (ran) |
|---|---|---|
| `C-N-high`, `C-wmin-high` right N to wrong N | **Acceptable; scorer knife edge, pre-existing.** The intended N contains the start-of-record half-cycle. | The command itself, scored 2 ms after its own release, loses it (27.5 to 27.0, 15.0 to 14.5) and stays there to 60 ms. No PT7 or band effect. |
| `C-area-high` right N to wrong N | **Acceptable; same edge from the other side.** | Its own command gains a half-cycle at +24 ms and later. |
| `D-wobble` lag | **Defect in this change.** | Section 5: 6 to 10 ms late on the identified plant, 60 ms early and unflagged on a slower plant with delay. |
| `D-short` phone 0.142 against encoder 0.446 | **Pre-existing, and a one-argument fix in the line this change rewrites.** The phone is scored to the end of its recording, the encoder over the command's span. | Phone scored with `tail_s`: 0.445, band 2, N 2.5. Encoder scored with 0.9 s of its post-roll: 0.141, band 1, N 3.0. The whole discrepancy is the scored span. It is also the only phone band miss in the author's table. |
| phone minus encoder -19.5 to -2.7 ms | **Pre-existing offset, acceptable to absorb, but it breaks the phone's lower gate.** | Scales with sample period (-1.9 ms at 200 Hz to -24 ms at 30 Hz). Section 4. |

I did not find a regression the author did not list on the identified plant.

## 7. Code quality and scope

- **Scope.** The diff touches `neuromech/session.py`, `neuromech/dryrun.py`,
  `neuromech/tests/test_session_dryrun.py`, `neuromech/README.md` and the report. Nothing else. The scorer,
  `build.py`, scenarios, Rust and C# are untouched. `_score_trace`'s signature changed; its only other
  caller is `dryrun.ilc_demo`, which was updated (`make_cases.py` mentions it in a comment only).
- **Line endings.** `git diff --stat origin/main...e2257cc` and the same with `--ignore-cr-at-eol` are
  identical (785 insertions, 29 deletions, 5 files). `git ls-files --eol` shows `i/lf` for the changed
  files. `git diff --check` is clean.
- **Report columns for refused traces.** Written. `cmd_release_s`, `enc_release_s`, `enc_release_lag_ms`
  and `enc_release_resid_deg` are set before the flag is tested; the phone's four and
  `ph_minus_enc_lag_ms` likewise. They are NaN when the command never moves or the recording does not
  cover the window, which is correct. `report.csv` is written before the exit status is set.
- **Error handling.** A `ValueError` from `_score_trace` on the encoder path is uncaught and would stop
  the whole session; it needs a hold starting within 0.05 s of the command's start, which no built command
  has. On the phone path the surrounding `except Exception` reports a release or scoring error as
  `sync refused: ...`, which mislabels it.
- **Printed summary.** "delivered band == intended band: X % of N runs" counts scored runs only. A session
  with eight flagged and one scored prints "100 % of 1 runs" above the flag list. The phone "band
  agreement" does count a flagged phone trace on a scored run, as a disagreement, although the text below
  says flagged traces are not in the figures. Put the flagged count in the headline.
- **Statements to correct.** (a) "15 % of the onset" for the residual limit (section 4). (b) The report's
  case for 2 deg rests on N counts that a constant +30 to +50 ms matches (section 1); the hold-departure
  measurement is the argument and should be in the docstring. (c) README still says "90 tests" on `main`
  and is updated to 133 here, which is correct.
- **Scoring grid.** Laying the 100 Hz grid from the release is a reasonable change and removes a 10 ms
  rounding step. It is a second behavioural change riding in the same commit; the pure-delay tests cover
  it.
- **Log ending with the command.** Scored on a span shorter by the lag with no warning, as the author
  notes. Not exercised here.

## 8. Gates (ran, at `e2257cc`)

| gate | result |
|---|---|
| `python -m pytest neuromech/tests -q` | **133 passed**, 0 failed, 0 skipped, 174 s |
| `python neuromech/designer/parity/run_parity.py` | **PARITY OK**, 355 cases, every field within tolerance |
| `python -m neuromech.dryrun --seed {11, 5, 23} --discard` | 9 of 9 in band each, no flags, exit 0 |

## 9. What was verified, and how

**By running**

- Library build, the two gates and three real dry runs above.
- Sections 1 to 6 through a harness that imports the branch's `session`, `dryrun` and `ilc` unmodified and
  repeats the encoder branch of `session.score` in memory: `dryrun.player_hold`, `dryrun.plant_response`
  with a chosen plant, noise, `ilc.locate`, `session._delivered_release`, `session._score_trace`,
  `session._summ`. "Before" is `origin/main`'s `_score_trace` copied verbatim. The phone tables add
  `session._align_phone` on a gyro synthesised as `dryrun.write_phone_jsonl` does. Its baseline reproduces
  the author's direction and size (42 to 10 wrong N, 5 to 0 wrong band; the author reports 41 to 8 through
  the file path).
- The mutation in section 6 (edited in the throwaway worktree, then reverted).
- Line-ending and whitespace checks.

**By reading**

- `session.py`, `dryrun.py`, the test diff, the README diff and the author's report in full;
  `build.score` and `build.score_angle`; `compute_pt_params` from the release handling to the count;
  `ilc.locate` and `ilc.identify`; the 2026-11-10 diagnosis and the 2026-11-11 scorer analysis (first
  sections).
- That no path falls back to the commanded release, and that the report columns are written for refused
  traces.

**Not verified**

- Anything on the rig, a real LabVIEW log or a real phone. Every number here is from the simulated plant,
  Player and phone, as the author's are.
- The author's 67-scenario before/after through the real file path, and seeds 3 and 42 of it. My sweeps use
  seed 11 and the in-memory path; only the nine default scenarios went through real files.
- The author's 20 window combinations (I ran 7).
- `node neuromech/labview/verify_formula_node.js`, the CI python list outside `neuromech/tests`, and the
  Rust and webapp jobs.
- `nm_figs.fig_dryrun` and `dryrun.ilc_demo` end to end (the ILC figures in section 4 repeat its loop in
  memory).
- Whether the real loop rounds the release corner as a second-order model does. Section 2 says the method
  follows the plant if it does not; it does not say what the real lag is.
