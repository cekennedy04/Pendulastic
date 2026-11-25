# Which clock should corpus phone recordings be replayed on?

Date: 2026-11-25 (revised after independent review of PR #137). Analysis only:
no replay, scorer, Rust core, web app or `STATUS.md` change. Script:
`corpus_replay_clock.py` (tests: `tests/test_corpus_replay_clock.py`).
Aggregates only; no participant identifiers and no per-trial tables.

## Summary

**The claim from the smoothing study (PR #136) is true.** Corpus phone
recordings are replayed on the desktop's receive clock, the samples arrive in
bursts, and about 79% of in-swing 10 ms ticks repeat the previous tick. The
phone's own timestamps are present and clean on every loadable trial.

**What moves on the phone clock.** Every row compares the same trials on both
clocks (reference footing, whole-recording axis):

| Quantity | Trials | Receive clock | Phone clock | Moves? |
|---|---|---|---|---|
| In-swing ticks that repeat the previous tick (median) | zeroed | 0.79 | 0.006 | yes |
| Sensor-angle roughness about its own 6 Hz curve (median) | zeroed | 3.17 deg | 0.16 deg | yes |
| Sensor vs plate, offset removed (median) | 145 | 2.58 deg | 1.37 deg | yes, better |
| Sensor RMSE, hold-zeroed (the 2026-11-18 "3.9 deg") | 131 | 3.84 deg | 2.71 deg | yes, better |
| App angle vs plate, gain (the "0.93") | 131 | 0.931 | 0.935 | no |
| App angle vs plate, RMSE (the "4.8 deg") | 131 | 4.84 deg | 5.35 deg | no systematic move |
| Verdict agreement with gap-filled optics | 133 | 126 | 126 | no |
| N equal to gap-filled optics | 133 | 70 | 83 | yes, better |
| PT ICC against gap-filled optics | 133 | 0.896 | 0.879 | no |

Receive to phone, 144 trials scored on both: the stored PT score moves by more
than 0.05 / 0.1 / 0.3 on 7 / 5 / 1 trials, one verdict of 136 changes, and N
changes on 28. The noise floor for N, from moving only the tick grid by 5 ms,
is 6 to 8 trials (section 6).

**Two things change with the clock, not one:**

1. where samples fall on the tick grid (the staircase), and
2. when the pre-release bias calibration fires (many times on the receive
   clock, once on the phone clock).

The sensor improvement is the first: it is the same size with the bias
calibration on or off (section 6). The corpus does not show the second to be
an improvement: with the calibration switched off, results are no worse on
either clock.

**Recommendation:** replay the corpus on the phone clock, for the tick
placement. Do not adopt it on the argument that the bias calibration then
"runs as the app runs it" until that calibration has been judged on its own.
Details in "Recommendation".

## 1. Which timestamp each path uses

| Path | Tick grid / stillness clock | Integration step | Evidence |
|---|---|---|---|
| Corpus replay (Python) | desktop receive time | phone timestamp | `reconstruct_imu_raw_logs.py:43` (`t` = `timestamp_ms`/1000), `:47`, `:61`; `workbench_engine.py:1026-1030`, `:1108` (sorted by `t`); `imu_calibration_tuner.py:449-452` (grid from first `t`), `:463` (tick snapshot), `:487-503` (dt from `phone_ts_ms`), `:474`, `:559`, `:683`, `:718` (hold, calm and stillness logic on `t`) |
| Live desktop server | desktop receive time | phone timestamp | `pendulastic_imu_server.py:639` (`now = time.time()`), `:651`, `:670-675`; logged by `_log_raw_csv` `:1394-1412` |
| Web app (Rust core via wasm) | phone | phone | `webapp/src/capture.js:34` (`event.timeStamp`); `mobile-imu-core/src/wasm.rs:38-41`; `replay.rs:33-37`, `:426`, `:452` |
| Synthetic phone | phone | phone | `neuromech/phone_trial.py:331-338` |
| Validation | as corpus replay | as corpus replay | `imu_optitrack_validation.py:261-265` |

The split CSVs carry both stamps: `timestamp_ms` (desktop `time.time()` at
receive) and `phone_ts_ms` (phone epoch, whole milliseconds).

**Completeness.** 179 corpus trials, 159 paired with OptiTrack, 22 sessions.
175 load; 4 (all paired) have an empty accelerometer file and are unreadable
on either clock. On all 175:

- every accelerometer, gyro and magnetometer sample has a phone stamp
  (about 249 k accel, 249 k gyro, 240 k mag samples);
- no duplicate stamps, no backward stamps, no clock breaks over 2 s;
- phone rate 100.2 Hz (range 100.19 to 100.27), interval median 10 ms, p99 11 ms;
- lost-sample gaps (over 25 ms): one each in accel and gyro across the corpus
  (largest 40 ms); the magnetometer has 83 across 11 trials, and one trial's
  magnetometer runs at 1 Hz;
- accel and gyro are stamped separately: the nearest-stamp gap is typically
  1 ms, worst per trial typically 5 ms.

All trials are single-phone (proximal role only). No replay failed for want of
a phone stamp; the script records that as its own reason, separate from a
trial that never zeroes.

## 2. Burst structure

Gyro, per-trial medians:

| | Receive clock | Phone clock |
|---|---|---|
| Gap between bursts | 57 ms (p10-p90 46 to 99) | none (10 ms spacing) |
| Samples per burst | 7.3 (5.0 to 10.3) | 1 |
| Receive intervals under 2 ms | 84% | - |
| Largest gap in a trial | 112 ms median, 524 ms worst | 40 ms worst |
| 10 ms ticks with no new sample, whole recording | 0.80 (0.76 to 0.87) | 0.007 |
| In-swing ticks repeating the previous tick | 0.79 (0.75 to 0.85) | 0.006 |

The "about 90 ms" in the claim is true for roughly half the corpus. There are
two regimes: about 47 ms and 5 samples per burst (95 trials), and about 97 ms
and 10 samples per burst (80 trials). Of 22 sessions, 10 are entirely long, 8
entirely short, 4 mixed. By month: July about 47 ms, August mostly about
96 ms, September about 47 ms. The repeated-tick share is about the same in
both regimes (by session 0.75 to 0.86), because either burst spacing is much
longer than a 10 ms tick.

## 3. OptiTrack alignment, offset and drift

Alignment does **not** depend on the receive clock in any strong way. OptiTrack
time is take-relative and the lag is found by matching the two angle curves
(`imu_optitrack_validation.py:320-386`: "The instruments share no clock"). The
only tie to desktop time is the weak duration-based starting guess
(`workbench_engine.py:121-143`).

Measured between the two phone-side clocks:

- **Offset** (smallest receive minus phone, per trial): -0.76 to +1.58 s.
  Within a session it moves by 0 to 75 ms (median about 11 ms); across sessions
  by seconds. It has to be estimated per trial.
- **Latency** beyond the fastest packet: median 43 ms, p90 93 ms, worst 542 ms.
- **Drift**: median 64 ppm; over a trial (median 13 s) that is 1 ms typically,
  7 ms at worst. Negligible within a trial.
- **Fitted lag**, phone minus receive: median -35 ms (p10-p90 -52 to -7), which
  is the removed network latency.
- The alignment landed somewhere else (more than 0.25 s away) on 2 of 145
  trials. One has optical coverage of only a quarter of the swing and is
  ambiguous on both clocks; the other fits a period away on the phone clock.

**Without those two trials** (143 aligned) nothing in the summary changes
character:

| Same alignment on both clocks | Trials | Receive | Phone |
|---|---|---|---|
| Sensor vs plate, offset removed | 143 | 2.59 deg | 1.37 deg |
| Sensor RMSE, hold-zeroed | 131 | 3.84 deg | 2.71 deg |
| App gain (median / p10) | 143 | 0.931 / 0.843 | 0.934 / 0.833 |
| App RMSE | 131 | 4.84 deg | 5.35 deg |
| Stored PT moved over 0.05 / 0.1 / 0.3 | 142 | - | 7 / 5 / 1 |
| N changed | 142 | - | 28 |
| Verdict agreement with gap-filled optics | 131 | 125 | 125 |
| N equal to gap-filled optics | 131 | 69 | 82 |
| PT ICC against gap-filled optics | 131 | 0.951 | 0.924 |

The largest score disagreement with optics, on both clocks, is one of the two,
which is why the ICC level is higher without them. The step between clocks is
the same.

The phone clock used here is `phone_ts/1000 + min(receive - phone)` per trial.

## 4. Paired validation on both clocks

155 paired trials replay; 146 zero on both clocks (9 never zero on either),
144 are scored on both, 137 are interpretable on each. The receive-clock rows
reproduce PR #130's published "after" figures exactly, and receive/opening-fit
reproduces its "before" figures.

### The 2026-11-18 figures

| | Published | Receive, all held | Phone, all held | Same 131 trials |
|---|---|---|---|---|
| Sensor RMSE, hold-zeroed | 3.9 deg | 3.86 (132 trials) | 2.71 (131 trials) | 3.84 -> 2.71 |
| App RMSE | 4.8 deg | 4.79 | 5.35 | 4.84 -> 5.35 |
| App gain | 0.93 | 0.931 | 0.932 | 0.931 -> 0.935 |

- **Sensor RMSE moves**, 3.9 to 2.7 deg. Per trial it is lower by more than
  0.5 deg on 72 and higher on 16. The tail does not improve (p90 6.8 on both).
- **Gain does not move.**
- **App RMSE does not improve.** The median goes up by 0.5 deg but there is no
  systematic per-trial shift: the median per-trial change is -0.01 deg
  (p10-p90 -1.7 to +1.8), lower by more than 0.5 on 32 trials and higher on 41,
  p90 9.9 to 9.7. With the offset removed the app angle is slightly better
  (2.09 to 1.79 deg).

### Score, N and verdict

| Receive -> phone, 144 scored on both | Stored score | On-screen (no detrend) |
|---|---|---|
| PT moved by more than 0.05 | 7 | 11 |
| more than 0.1 | 5 | 7 |
| more than 0.3 | 1 (largest 0.50) | 4 (largest 1.13) |
| Verdict changed | 1 of 136 | 4 of 137 |
| N changed | 28 (18 up, 10 down) | 26 (17 up, 9 down) |

All five stored-score moves over 0.1 are swings with N of 2 or fewer, and three
of them are uninterpretable on at least one clock. Interpretability: one trial
lost, one gained.

### Direction against optics

| Against gap-filled optical score | Closer | Further |
|---|---|---|
| PT, all trials | 77 | 63 |
| PT, movers over 0.05 | 3 | 4 |
| PT, movers over 0.1 | 3 | 3 |
| N | 24 | 8 |

| Agreement with gap-filled optics, same 133 trials | Receive | Phone |
|---|---|---|
| Verdict | 126 (kappa 0.895) | 126 (kappa 0.895) |
| N equal | 70 | 83 |
| PT ICC | 0.896 | 0.879 |
| PT ICC without the largest disagreement | 0.951 | 0.924 |
| Median absolute PT difference | 0.018 | 0.017 |

(Over each clock's own interpretable trials the same figures read 126 of 133
and 127 of 134 for the verdict, and 75 and 87 of 141 for N. Those are
different trial sets and are not quoted above.)

The score moves are not directional. N moves toward optics. The PT ICC does
not improve; it is carried by one or two large disagreements on either clock.
PR #136 reported an ICC step of 0.81 to 0.85 for the phone clock; that compared
different trial sets (its residual filter admits 130 trials on one clock and
139 on the other). On the same trials there is no gain.

### PR #130 on the phone clock

| Opening 25 samples -> whole recording | Receive | Phone |
|---|---|---|
| Axis error, median | 21.1 -> 10.9 deg | 21.4 -> 10.7 deg |
| Axis error over 30 deg | 34 -> 4 trials | 37 -> 3 trials |
| App gain, median | 0.854 -> 0.931 | 0.861 -> 0.932 |
| App RMSE, median | 8.67 -> 4.79 deg | 8.55 -> 5.35 deg |
| App bias | -8.1 -> -3.4 deg | -8.5 -> -3.7 deg |
| Interpretable | 133 -> 137 | 132 -> 137 |

The comparison holds, with the same size, on the phone clock.

## 5. Release detector, zero and stillness on the phone clock

- **Zero**: 146 trials zero on both clocks, 9 on neither, none on only one. The
  zero is taken at the same gyro sample on all 146. The zero pose differs by a
  median 0.4 deg (p90 1.1, largest 2.8).
- **Stillness** behaves differently. The stillness window needs a 0.95 s span
  of a 1.0 s window (`pendulastic_imu_server.py:417`; the Rust core has the
  same rule, `stillness.rs:60-64`). On the receive clock the bursts make that
  test flicker, so the rising edge that triggers the bias calibration occurs a
  median 5 times per trial, up to 48, and never on 11 trials. On the phone
  clock it occurs exactly once on 142 of 146 trials, at the first still
  window, which is what the Rust core does (`replay.rs:401-413`).
- These are counts of **stillness rising edges, not of applied estimates**. On
  each edge the replay re-estimates the bias but applies a gyro axis only if it
  passes its own significance test and size bound; that was not observed here.
- On the receive clock the last such edge was a median 0.16 s before the zero;
  on the phone clock 1.6 s before (up to 8 s).
- The mean gyro rate over the window the bias is taken from is about
  0.02 rad/s (p90 0.05 to 0.06) on either clock. For a held leg that is mostly
  leg motion rather than sensor bias.

The detector and the zero are valid on the phone clock. Whether the bias
calibration is worth having, on either clock, is the next section.

## 6. Staircase or bias calibration? And the noise floor

**The bias-off variant.** The reference replay with the stillness check
answering "not still" throughout. That disables the gyro-bias calibration
**and the accelerometer-bias calibration** (the replay estimates both on the
same edge), so it removes both, not the gyro's alone. Analysis only.

### Levels, same trials on all four

| | Receive, bias on | Receive, bias off | Phone, bias on | Phone, bias off |
|---|---|---|---|---|
| Sensor RMSE, hold-zeroed (131 trials), median | 3.84 | 3.48 | 2.71 | 2.39 |
| the same, p90 | 6.77 | 7.28 | 6.83 | 6.85 |
| Sensor vs plate, offset removed (145), median | 2.58 | 2.35 | 1.37 | 1.21 |
| App RMSE (131), median | 4.84 | 5.04 | 5.35 | 5.18 |
| the same, p90 | 9.92 | 9.20 | 9.68 | 9.32 |
| App gain (131), median | 0.931 | 0.927 | 0.935 | 0.933 |
| PT ICC against gap-filled optics (133) | 0.896 | 0.893 | 0.879 | 0.880 |
| Verdict agreement with gap-filled optics (133) | 126 | 125 | 126 | 126 |
| N equal to gap-filled optics (133) | 70 | 78 | 83 | 83 |

What this shows:

- **The clock step is the staircase.** Sensor RMSE falls by 1.13 deg with the
  calibration on and by 1.09 deg with it off. Almost none of 3.84 -> 2.71 is
  the bias calibration re-firing.
- **The calibration itself does not help, on either clock.** Switching it off
  lowers the median sensor RMSE by 0.3 to 0.4 deg on both clocks. App RMSE is
  mixed (0.2 worse on the receive clock, 0.2 better on the phone clock). ICC
  and verdict agreement do not move. This agrees with the reviewer's 24-trial
  sample.
- So the phone clock's single firing is **not shown to be better** than the
  receive clock's re-firing; neither is shown to be better than no calibration.

### Are the bias-off movers a subset of the bias-on movers?

No. Receive to phone, 144 trials scored on all four:

| | Bias on | Bias off | Both | Bias on only | Bias off only |
|---|---|---|---|---|---|
| Stored PT moved over 0.05 | 7 | 4 | 3 | 4 | 1 |
| over 0.1 | 5 | 3 | 2 | 3 | 1 |
| over 0.3 | 1 | 1 | 0 | 1 | 1 |
| N changed | 28 | 15 | 8 | 20 | 7 |
| Verdict changed (136) | 1 | 0 | 0 | 1 | 0 |

Fewer trials move with the calibration off, but they are largely different
trials (8 of the 15 N changes are shared). The counts therefore do **not**
divide into a staircase share and a bias share, and the earlier wording
("about half") is withdrawn. What the counts support: the clock moves N on 28
trials with the calibration on and on 15 with it off, and the app-angle RMSE
change per trial spans -1.7 to +1.8 deg (p10-p90) with it on against -0.3 to
+0.2 with it off.

### Noise floor: the tick grid moved by 5 ms, samples untouched

| Same clock, grid origin shifted 5 ms (144 scored) | Receive | Phone |
|---|---|---|
| N changed | 8 | 6 |
| Stored PT moved over 0.05 | 0 (largest 0.021) | 0 (largest 0.017) |
| Verdict changed | 0 | 0 |
| Sensor residual changed by over 0.5 deg | 0 | 0 |
| App RMSE change, p10-p90 | -0.05 to +0.06 deg | -0.04 to +0.06 deg |

N changes on 6 to 8 of 144 trials from where the grid happens to fall. So
"N changed on 28" is roughly 20 above the floor, and the bias-off "15" roughly
double the floor. The PT score moves over 0.05 have no floor under them: none
arises from the grid alone.

## 7. Recommendation

**Replay the corpus on the phone clock, as a tick-placement fix.** That part
is supported on its own:

- it is the clock the app uses, and the stamps are complete and clean;
- it removes about 1.1 deg of sensor error that is an artefact of the replay,
  with the bias calibration on or off;
- N agrees with optics on more trials (70 -> 83 of 133);
- the zero, the alignment, the gain and the verdicts do not move.

It is not a fix for anything clinical: app gain, app RMSE and score agreement
with optics do not get better.

**Condition.** The phone clock also changes when the bias calibration fires,
and the two cannot be adopted separately: on the phone clock the stillness
test stops flickering whether one wants that or not. The corpus gives no
evidence that the resulting single early estimate is better than re-firing,
and none that either beats no calibration. So:

- adopt the phone clock for the tick placement, and say in the adoption that
  the bias-calibration timing changes with it as a side effect, not as a
  benefit;
- before or with that adoption, decide the bias calibration on its own
  evidence (keep, gate harder, or remove), on the phone clock, with the gyro
  and accelerometer parts separated. That decision touches the shipped core,
  which has the same rule, so it is not a corpus-only question.

If that decision is not made first, the score and N changes on adoption should
be reported as "clock plus bias timing", not attributed to the clock alone.

**What adopting it would need** (not done here):

1. One tracked restamp at the loader level (the component binder in
   `workbench_engine.py` and `reconstruct_imu_raw_logs.py`), per-trial offset
   from the fastest packet, so every consumer gets it.
2. A stated rule for trials without usable phone stamps: refuse, or an
   explicit, labelled fallback. No silent default.
3. A footing label for the time base, and a version bump.
4. Parity: run the Rust core on the restamped real trials against the Python
   replay.
5. A corpus before/after with each changed trial explained, and independent
   review, as the Quality bar asks.
6. Leave the live desktop server alone; it has no later information to
   restamp with.

**Numbers that go stale on adoption:** the 2026-11-18 sensor figures
(3.9 becomes 2.7 deg); the absolute before/after values in PR #130 (conclusion
unchanged); PR #136's tables, which should be read on the phone clock;
test-retest figures and any threshold work derived from phone replays (not
recomputed here). `HEALTHY_REF` is derived from OptiTrack curves
(`derive_healthy_ref.py:151`), so it does not move.

## Second derivation

- A separate script re-derived an evenly spaced sample of the paired trials
  (17 trials, 32 scored replays; one trial never zeroes on either clock) by a
  different route: a different loader, a different phone-clock origin,
  `replay_trial` and the scorer called directly, an own-code residual on the
  optical frames, and repeated ticks counted from arrival times alone.
  - PT within 0.0007 on all 32; N and verdict identical on all 32.
  - Repeated-tick share within 0.03.
  - Sensor residual, sample medians: 2.86 against 2.88 deg (receive) and
    1.14 against 1.31 deg (phone). Per trial the two residual definitions
    differ by up to 1.2 deg; the direction and size of the clock effect agree.
- For this revision the whole corpus was replayed again from an empty cache.
  Every aggregate of the first run came back identical.
- The receive-clock results reproduce PR #130's published tables exactly, and
  the claim's own figures (79%, 3.2 deg, 2.6 to 1.4 deg, 5 trials over 0.1)
  were reproduced independently of PR #136's script. The one difference: N
  changes on 28 trials here against 27 there.

## Not verified

- The Rust core and wasm build were not run on restamped trials.
- Test-retest reliability was not recomputed on the phone clock.
- Each score mover is classed (low N), not explained trial by trial.
- The bias-off variant removes the gyro and the accelerometer calibration
  together; neither was switched off alone.
- Whether a bias estimate was actually applied on a given stillness edge was
  not observed; only the edges are counted.
- Accel/gyro pairing order is not separated from tick placement.
- The noise floor is one shift (5 ms) per clock, not a distribution over
  shifts.
- The cause of the two burst regimes (phone app batching or network) is unknown.
- Whether `phone_ts_ms` is sensor time or app send time is inferred from its
  regular 10 ms spacing, not confirmed from the phone app.
- The phone-clock replay is still 100 Hz with separately stamped sensors, not
  the web app's 60 Hz paired events. The app's own recordings were not
  examined; none is paired with optics.
- The 20 unpaired trials are inventoried only.
- No confidence intervals. 13 participants plus one test folder; trials within
  a participant are not independent. Differences of a few tenths of a degree
  between medians, and of a few trials between counts, are not established.
- Drift is resolved only to a few hundred ppm per trial.
- The `sync_offset_s` header in the merged IMU files was not compared with the
  offset used here.
- The true lag of the two trials whose alignment moved is not established.
- The four trials with an empty accelerometer file were not investigated.

## Regenerate

```
python corpus_replay_clock.py --cache-dir <a folder outside the repo> --out <a file outside the repo>
python -m pytest tests/test_corpus_replay_clock.py
```

Recordings are read in place and never copied. The script refuses a cache or
an output file inside the repository. The cache is keyed on the recording, the
reference replay config and the source of the replay modules, and a cached
trial missing a variant stops the run. The tests use a synthetic bursty phone
(including three synthetic trials run end to end) and need no participant
data.
