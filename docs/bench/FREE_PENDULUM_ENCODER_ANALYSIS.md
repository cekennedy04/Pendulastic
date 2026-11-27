# Free-pendulum encoder validation: running the analysis

Goes with `FREE_PENDULUM_ENCODER_RUN_SHEET.md` and the plan
(`docs/plans/2026-11-18-001-plan-free-pendulum-encoder-weight-validation.md`). The script is
`encoder_pendulum_validation.py`; it is the first part of the analyser the plan calls
`pendulum_fixture_report.py` (section 11). Tests: `tests/test_encoder_pendulum_validation.py`
(estimators, refusals, the session) and `tests/test_encoder_pendulum_independent.py` (a ground
truth that shares no code with the script or its synthesiser).

**Status.** Built and tested on synthetic trials only. No real pendulum has been recorded.
The encoder file format is assumed to be the motor rig's LabVIEW `.bin`. Every acceptance
limit is **PROPOSED - pending supervisor confirmation before session S1**, and so is the
choice of which comparison each limit is applied to (see "Provisional constants"). No MAS
grade or clinical statement follows from any output.

## Run it

```
# after a session, from the repo root, with the venv
python encoder_pendulum_validation.py session Pendulastic_Mobile_App_Trial/pendulum_fixture/<YYYY-MM-DD>_<session>_<phone> --plots

# several sessions (both phones) in one report
python encoder_pendulum_validation.py session <folder S1> <folder S2> --out <report folder>

# one swing, no session folder (the sample rate and channel count must be stated)
python encoder_pendulum_validation.py trial --phone X.jsonl --encoder pf1126_001.bin \
    --encoder-fs 1000 --encoder-channels 7 --radius-m 0.30 --nominal-release-deg 40 --plots

# a synthetic session, to see the output before the bench (not a measurement)
python encoder_pendulum_validation.py synth --out <scratch folder>
```

Output goes to `<session folder>/report/` (or `--out`): `summary.md` (read this), `trials.csv`
(every number, one row per swing), `peaks.json` (peak-by-peak table) and, with `--plots`, an
overlay per swing. Exit code 0 = all swings analysed, 1 = at least one swing could not be
analysed (it stays in the table with the reason), 2 = refused (the message says why).

**The exit code says whether the analysis ran, not what it found.** A session whose overall
verdict is FAIL or UNRESOLVED still exits 0. That is deliberate: every limit is PROPOSED, a
FAIL is a result and not a tool error, and the synthetic session itself fails `Z*` and A5.
The verdict is the `OVERALL (<phone>): ...` line, printed last on stdout and as
"**Overall, ...**" under each phone's criteria in `summary.md`. Do not gate anything on the
exit code alone.

What is refused (exit 2), and what is only "not analysed" (exit 1):

| Problem | `session` | `trial` |
|---|---|---|
| `session.json` or the manifest is not valid JSON, has an unknown key or lacks a required field; no `rig_log.csv` or a missing column; counts that do not agree; a `release_angle_deg` that is not a number | REFUSED, exit 2 | n/a |
| `rig_log.csv` with a row of more fields than the header (an unquoted comma in the notes), or not a readable CSV | REFUSED, exit 2, with the line number | n/a |
| `rig_log.csv` not in UTF-8 (saved from Excel as "CSV", with a degree sign or an accent) | read as Windows-1252, with a WARNING and a "Rig log encoding" line in the report; not refused | n/a |
| `rig_log.csv` in UTF-16 (Excel's "Unicode Text"), or delimited by semicolons or tabs | REFUSED, exit 2, naming the encoding or the delimiter | n/a |
| A `rig_log.csv` row that stops before `trial_id`, `encoder_trial` or `app_result` (cut short) | REFUSED, exit 2, with the line number. A row that only lacks trailing optional columns (`notes`) is read | n/a |
| A stated pairing that is not whole or not consistent: `phone_file` filled on some rows and not others, or naming a file twice or one the manifest does not list; a manifest `trial_id` on some trials and not others, or not one-to-one with the rig log's; a `phone_file` column and manifest `trial_id`s that contradict each other | REFUSED, exit 2 | n/a |
| A channel count the encoder file cannot be (its size is not a whole number of such samples, or the "position" it gives is not continuous) | that swing "not analysed", exit 1 (every swing, if `encoder.n_channels` is wrong) | REFUSED, exit 2 |
| A phone export with a non-finite (NaN) or malformed sample, or wrong units | that swing "not analysed", exit 1 | REFUSED, exit 2 |
| No zero pose in the replay (the app produced no angle) | that swing "not analysed", exit 1, **and counted on its own line**: "N swing(s): app took no zero pose". How it should count is an open owner decision | REFUSED, exit 2 |
| `encoder.sample_rate_hz`, `n_channels` or `counts_per_rev` in `session.json` that is not a positive JSON number (`"1000 Hz"`) | REFUSED, exit 2, naming the field | n/a |
| A `session.json` that mis-describes the encoder: on every analysed swing the encoder's period is not the phone's (wrong `sample_rate_hz`), or its release angle is a constant multiple of the rig log's (wrong `position_units` or `counts_per_rev`) | REFUSED, exit 2, naming the field; nothing is reported | not checked |

A channel count that happens to give
a smooth trace (for example twice the true count, which reads every other position sample)
is **not** caught by these checks; it has not been tried.

A line on stderr saying `using DEFAULT_CONFIG` comes from importing the desktop server
module and does not apply: the phone angle is always replayed with `REFERENCE_REPLAY_CONFIG`.

## What must be recorded at the bench (owner checklist)

The script cannot run without the items marked **required**. The others are carried into the
report and listed there as TO FILL until supplied.

**Per session, in `session.json`** (copy `2026-11-18-free-pendulum-session-template.json`):

- [ ] **required** `session` (P0, S1 ... S7) and `phone.platform` (`iphone` or `android`).
- [ ] **required** `encoder.format`: `labview_bin` if the new VI writes the motor rig's layout
      (headerless big-endian float64, interleaved, position first). Anything else needs a
      reader first (below): send one sample file and its channel list before the session.
- [ ] **required** `encoder.sample_rate_hz`: the logged rate. The `.bin` holds no time
      column, so a wrong rate silently stretches the encoder's time.
- [ ] **required** (for `labview_bin`) `encoder.n_channels`: channels per sample. The file
      does not say, and nothing is inferred.
- [ ] `encoder.position_units`: `deg`, or `counts` together with `encoder.counts_per_rev`
      (after x4 decoding). `encoder.sign` (+1 or -1) is optional: polarity is resolved.
- [ ] `encoder.model`, `encoder.counts_per_rev`, `encoder.daq` (the DAQ or counter card),
      `encoder.zero_deg` (the reading with the arm hanging still).
- [ ] `phone_radius_m`: **measured**, pivot axis to the centre of the phone, with
      `phone_radius_measured: true`. A number without that flag is treated as not measured
      and the report prints 0.30 m as a PLACEHOLDER.
- [ ] `phone.model`, `phone.os`, `phone.browser`, `app_build`, `app_algorithm` (app footer),
      `phone_orientation`, `pivot_tilt_deg`, `date`, `operator`, `analyser_commit`.
- [ ] `weights`: the measured mass (kg, scale to 10 g) and radius of each stack, by ID
      (`W1` ... `W4`; `W0` is taken as 0). Without them criterion A7 is not assessable.
- [ ] No other keys: an unknown or misspelt key in `session.json` is refused, and so is an
      option in the `encoder` block that the reader does not take.

**Per swing:**

- [ ] One row in `rig_log.csv` (the template), **including failed attempts**: `seq`,
      `trial_id` (as printed on the run sheet, `PF-S1-W3-A40-Q-17`), `encoder_trial` (the
      encoder file number), `condition`, `release_angle_deg`, `mode`, `app_result`
      (`scored`, `NOT SCORABLE`, or `ERROR: ...`), `stopped_by` (`auto` or `60s`), `notes`.
      The `trial_id`, `encoder_trial` and `app_result` columns must exist. Write the release
      angle actually set: it is checked against the encoder (8 deg). A `release_angle_deg`
      that is not a number is refused (exit 2, naming the row). If it is blank the angle is
      read from the trial ID; if the trial ID does not carry one either, the check cannot be
      made and the swing is flagged `release_angle_not_checked` and not assessed.
- [ ] One encoder file per swing in `encoder/`, named so that its **last number is the
      `encoder_trial`**: `pf<MMDD>_NNN.bin`. Numbers are never reused within a session: two
      files ending in the same number are refused. Leave no stray files in `encoder/`: the
      number of files must equal the number of rows that name one.
- [ ] The app export in `phone/`: the `*-manifest.json` and every `*.jsonl`, exported from
      the browser tab in one go. File names are left as the app writes them. Every trial in
      the manifest needs its numeric `timestamp`: it is what orders the phone trials.
- [ ] Start the encoder log before the lift and stop it last, so it covers the hold, the
      whole ring-down and at least 5 s of rest. **Keep the phone recording until the arm has
      hung still for 2 s as well**: a phone record that stops while the arm is still
      swinging is flagged `phone_ends_before_settled` and the swing is not assessed.
- [ ] Hold the arm still against the peg for 5 s and raise it slowly. The release is found
      as the last rise out of stillness with a swing after it; a hold shorter than about
      1 s, or a hand still moving at release, is flagged (`hold_not_still`) and the swing
      is not assessed. Let go cleanly: the app zeroes when the gyro reaches 1 rad/s, and
      every degree the arm creeps before that is a degree off the absolute reading. A lift
      fast enough for the app to take as the release puts its zero pose in the lift
      (`zero_not_at_release`): the swing is then not assessed.

**Pairing rule** (the plan's): rig-log rows whose `app_result` starts with `ERROR` are
dropped; the rest are matched in order to the exported phone trials sorted by manifest
timestamp. The timestamps order the session only if every trial has a finite number that
no other trial shares. If one is missing, not a number, NaN, or equal to another's (all
zero, say), they cannot: the manifest's own order is used for all of them, a WARNING is
printed, the report says so under the session and every swing carries
`manifest_order_unverified`.

**An unverified pairing is neither passed nor failed.** The numbers of such a session are
still computed and printed, but nothing checks that each phone trial is beside its own
encoder file, and a swing compared with another swing's encoder file fails A3 and A5 with
nothing wrong in the app. So no criterion reads PASS **or FAIL**: a line that would have
passed reads `INCOMPLETE (pairing unverified)`, one that would have failed reads
`UNRESOLVED (pairing unverified; as paired, this line would fail)`, each swing's cells
read "..., as paired (pairing unverified: not a verdict)", and the OVERALL line gives the
reason itself: "pairing unverified, so no line is passed or failed". The same holds under
`--allow-count-mismatch` when rig-log rows and phone trials differ in number (below).

**Stating the pairing instead of inferring it.** Either of these replaces the order rule
(timestamps are then not needed and the manifest's order does not matter), and the report's
"Paired by" line says which was used:

- a `phone_file` column in `rig_log.csv` naming the exported `.jsonl` of each row that is
  not an ERROR (all such rows, or none); or
- a `trial_id` on every trial in the manifest, equal to the rig log's (the app does not
  write one today; this is for a manifest edited or exported with it).

The pairing must then be one-to-one or the session is refused. A statement must also be
whole: a `phone_file` on some rows and not others, or a `trial_id` on some manifest trials
and not others, is refused rather than ignored (the part that is there may contradict the
order). If both are given they must say the same thing; a contradiction is refused, and
agreement is recorded on the "Paired by" line. The rig log's own
`trial_id` column does not do this by itself: it names the row, not the phone file.

If the counts differ (rows against phone trials, or rows naming an encoder file against
encoder files) the script stops (`COUNT MISMATCH`) rather than guess. Fix the log.
`--allow-count-mismatch` pairs in order up to the shorter list, for looking at the numbers
only: whatever is left over (a phone trial, a rig-log row, an encoder file no row names) is
in the table as `unpaired`, and **no criterion can read PASS**. If it is the rig-log rows
and the phone trials that differ in number, every row after the point where they diverge
is paired with the wrong swing, and the script does not know where that point is (the
missing one may be the first): every pairing is unverified, and no criterion reads FAIL
either (see above). A stray encoder file alone does not do that: encoder files are paired
by the number the row names, so the lines read INCOMPLETE and a FAIL stands.

**Equal counts do not prove the pairing.** A wrong swing of the same shape correlates above
0.9, so the cross-correlation does not discriminate. What can catch a shift by one: the
encoder's release angle against the log's (`release_angle_mismatch`, 8 deg); the phone's
own static tilt between the hold and the rest, from its accelerometer, against the
encoder's release angle (`phone_tilt_release_deg`, `phone_tilt_minus_encoder_deg`, 8 deg);
and the ratio of first-swing peak speeds (outside 0.80 to 1.25), read together as set out
under "What each flag does". Two repeats at the same angle and weight swapped with each
other cannot be told apart from the data; only the log can.

**A wrong pairing can look like an app failure.** Two swings at the same release angle but
different weights, paired with each other's files, pass every pairing check above (same
release angle, same static tilt, similar peak speed). The arm's frequency and decay differ,
so the swing arrives as an ordinary, unflagged FAIL on A3, A5 or the lag, with nothing in
the report to tell it from a faulty app. In the second review's synthetic check 38 of 60
such swaps did so (that figure is the reviewer's and has not been re-run here). Mitigation,
at the bench and before reading any FAIL as the app's: the rig log carries the `trial_id`
of every swing; check that the log and the export agree in order at every block boundary
(each change of weight or angle), and state the pairing with a `phone_file` column where
you can.

**So can an encoder scale error.** An encoder reading 5% large (a wrong counts per
revolution, or degrees taken for counts) gives a gain of 0.95 against the EMA-matched
encoder on every swing: an ordinary, unflagged FAIL on A3 with nothing wrong in the app.
(A gyro 5% large gives the same FAIL the other way, and that one is the phone's.) The
phone's static tilt between hold and rest owes nothing to the encoder, the gyro or the
app. Where the encoder's release angle is a like fraction larger or smaller than that tilt
on every assessed swing of a session (at least three swings over two release angles, all
on one side, more than 3% in the median and within 3% of each other;
`ENCODER_SCALE_NOTE_FRAC`), the report prints a "Note, session ...: Encoder scale?" line
under the criteria. It is a note: it gates nothing and changes no verdict, and an
accelerometer that is itself off would raise it too. Check the encoder's scale before
reading a session-wide A3 FAIL as the app's.

**Under 3% the note is silent.** An encoder scale error smaller than `ENCODER_SCALE_NOTE_FRAC`
raises no note and no flag: it arrives as a silent FAIL on A2 to A5 (the gain in A3 first),
looking exactly like the app's. The margin is thin to begin with: on clean synthetic data
the gain against the EMA-matched encoder is already about 0.986 at a 20 deg release (0.996
at 40 deg), against a window of 0.97 to 1.03, so an encoder reading 2% large fails A3 at
20 deg with nothing wrong anywhere else. Calibrate the encoder's scale against a known
angle before the first session.

**A session.json that mis-describes the encoder.** The `.bin` carries no sample rate and no
units, so `session.json` is believed. Three things are checked:

- `encoder.sample_rate_hz`, `encoder.n_channels` and `encoder.counts_per_rev` must be JSON
  numbers, positive (and whole, for the channel count). `"1000 Hz"` is REFUSED, exit 2,
  naming the field.
- If, on **every** analysed swing of a session (at least two), the encoder's swing period
  is under 0.80 or over 1.25 times the phone's (`ENCODER_PERIOD_RATIO_OK`), the session is
  REFUSED naming `encoder.sample_rate_hz`: one pendulum has one period. 500 Hz written for
  a 1 kHz file gives a ratio of 2 on every swing.
- If on every analysed swing the encoder's release angle is under 0.40 or over 2.50 times
  the rig log's (`ENCODER_ANGLE_RATIO_OK`), the session is REFUSED naming
  `encoder.position_units` and `counts_per_rev`. `counts` written for a file in degrees at
  8000 counts per revolution gives 0.045 on every swing.

Before this such a session read as six UNRESOLVED swings. Swings on both sides of a band (a
pairing shifted between 20 and 40 deg) or a single swing are not refused: the per-swing
flags handle those. A sample rate wrong by under 20% is not caught here.

**Still unknown, and the owner's to supply:** encoder model, counts per revolution, DAQ and
log format; the measured phone radius; the supervisor's confirmation of the limits and of
the comparison each is judged on.

## The four comparisons, and the raw gyro

Every agreement figure is over the **swing window**: release to settled. Settled means the
encoder's rate stays under 0.05 rad/s for 2 s **and both records cover that stretch**. If the
arm never settles, or the phone record (or the encoder log, or a `60s` row's 50 s cap) ends
before those 2 s are over, the window runs to the end of the shorter record, `settled` is
false, and the swing is flagged `not_settled` (with `phone_ends_before_settled` when it is
the phone record that stops short) and is **not assessed**: its figures are over part of a
swing and are printed for reading only. The hold and the rest are reported apart
(`rmse_hold_*`, `bias_hold_*`, `rmse_rest_*`, `bias_rest_*`). Each figure carries the suffix
of the comparison it comes from:

| Suffix | Phone | Encoder | Forgives |
|---|---|---|---|
| `_abs` (absolute) | app angle, clocks aligned | angle from the **hold pose** | nothing: what a reader of the app is told, against the truth |
| `_clock` (clock-aligned) | app angle, clocks aligned | angle from the **app's own zero pose** | the app's late zero pose |
| `_lagrm` (lag-removed) | app angle, shifted by the fitted lag | angle from the app's zero pose | the zero pose and the lag |
| `_matched` (EMA-matched) | app angle, clocks aligned | the same, through the footing's EMA | the zero pose and the footing's smoothing |
| `_gyro` (raw gyro) | gyro about its principal axis, hold bias removed, integrated | angle from the same start | no fusion or smoothing is involved |

On the independent ground-truth swing (40 deg, perfect sensor) the app's RMSE is 0.7 deg
lag-removed and 3.4 deg absolute; `_abs` agrees with the true angle recomputed outside the
script to 0.25 deg. The difference is the zero pose (about 1.6 deg there) and the lag.

**All comparisons are of magnitudes.** The app's angle is the size of the rotation from its
zero pose; the encoder is compared the same way. A wrong polarity, or a swing to the wrong
side, cannot be detected by this script.

## What the report gives, per swing

| Quantity | Column(s) in `trials.csv` | Note |
|---|---|---|
| Clock offset by release, and by cross-correlation | `clock_offset_release_s`, `clock_offset_xcorr_s`, `clock_offset_diff_ms` | From angular rate only. `--align` picks which one is used (release by default). More than 20 ms apart: `clock_alignments_disagree`, not assessed. |
| Encoder description checks | `period_ratio_encoder_to_phone`, `release_from_rest_deg` against `release_nominal_deg` | The swing's half-period on each instrument's own clock. See "A session.json that mis-describes the encoder". |
| Clock-rate ratio | `clock_rate_ppm`, `clock_rate_ppm_se` | From zero crossings over the ring-down. One swing resolves it to about 15 to 50 ppm on the synthetic trials, so A9 is judged on the session's weighted mean with 2 SE. |
| Lag of the app's angle behind its own gyro | `lag_ms`, `ema_expected_lag_ms`, `lag_minus_expected_ms`, `lag_matched_ms`, `lag_pegged_at_ms` | See "Timing" below. |
| RMSE, bias, SD, 95% limits of agreement, r, gain | `rmse_*`, `bias_*`, `sd_*`, `loa_lo_*`, `loa_hi_*`, `r_*`, `gain_*` for `abs`, `clock`, `lagrm`, `matched` | Swing window. Also `rmse_first_swing_lagrm`, `rmse_ringdown_lagrm`. |
| The three gains | `gain_gyro`, `gain_lagrm`, `gain_matched` (and `gain_abs`, `gain_clock`) | Side by side in every table. |
| Sensitivity to the zero instant | `gain_matched_zero_pm20ms_span`, `bias_lagrm_zero_pm20ms_span` | What moving the zero instant by +/-20 ms does. |
| Peak-by-peak amplitude error | `first_excursion_error_deg` (app-zero), `first_excursion_error_abs_deg` (from the hold), `peak_error_*`, `peaks.json` | One far-side peak per cycle. |
| Period and frequency error | `period_error_pct`, `f_error_pct` | Same estimator on both. |
| Decay, damping ratio, swing count | `tau_error_pct`, `zeta_error`, `zeta_error_pct`, `n_swings_phone`, `n_swings_encoder`, `n_swings_error` | Height-weighted log fit. The count is made on each source's own peak heights. It is this script's count, **not the app scorer's N**. |
| Pairing checks | `release_from_rest_deg`, `release_angle_minus_nominal_deg`, `peak_rate_gain`, `xcorr_quality`, `phone_tilt_release_deg`, `phone_tilt_minus_encoder_deg` | The tilt is the angle between the accelerometer's mean in the hold and at rest; blank if the arm was not seen to settle. |
| Coverage | `settled`, `settle_after_release_s`, `swing_window_s`, `phone_record_ends_after_release_s` | |
| The off-axis phone | `phone_radius_m`, `phone_radius_measured`, `l_eq_small_angle_m`, `tangential_accel_fraction`, `centripetal_peak_m_s2`, `tangential_peak_m_s2`, `accel_dev_from_g_max_m_s2`, `accel_gate_open_fraction` | See below. |
| The app's zero pose | `app_zero_exact`, `zero_after_release_ms`, `zero_pose_fall_deg`, `fold_fraction` | See below. |
| Verdicts | `pass_*`, `pass_A1_A5` (A1 to A5 only), `not_assessed_because`, `swing_verdict`, `pairing_verified`, `judged_on` | Blank = not assessed. `pass_A1_A5` is false if any one part is false, blank only if none failed and one could not be reached. `swing_verdict` is the cell the report prints (`pass`, `FAIL`, `NOT ASSESSED`, `NOT ASSESSED - UNRESOLVED (cause not determined)`, `not analysed`). **Where `pairing_verified` is False the `pass_*` columns are what the swing reads as paired and are not verdicts**; `swing_verdict` says so in words. |

`summary.md` repeats the run sheet's table (Seq, Trial ID, Condition, Release, Mode, Encoder
file, App result, Stopped by) with the results beside each row, then a weight by release-angle
grid, tables by condition, release angle, mode and phone, one line per criterion, and the
list of provisional constants.

## Verdicts

The logic, in the order the script applies it. Each step is decided in one place.

**1. One swing has one state** (`swing_state`):

| State | When | In the table |
|---|---|---|
| not analysed | the row has an error instead of numbers (a file that would not read, no zero pose, unpaired) | `not analysed` |
| NOT ASSESSED | a gating flag for what the fixture, the operator or the log did: `hold_not_still`, `release_angle_mismatch`, `release_angle_not_checked`, `not_settled`, `zero_instant_approximate`, `no_peaks` | `NOT ASSESSED` |
| UNRESOLVED | a gating flag behind which the phone or the app itself may be the reason, and the data cannot say: `pairing_suspect`, `release_not_where_xcorr_expects`, `clock_alignments_disagree`, `phone_gap`, `phone_ends_before_settled`, `zero_not_at_release`, `lag_pegged` | `NOT ASSESSED - UNRESOLVED (cause not determined)` |
| assessed | no gating flag | `pass` or `FAIL` on A1 to A5 |

A gated swing is reported in full (every number is in the table and in `trials.csv`) and
every `pass_*` of it is blank, with the reason in `not_assessed_because`. **No cause is
attributed to an UNRESOLVED swing.** See "Deferred until bench data".

**2. The swing's cell is A1 to A5 and nothing else** (`pass_A1_A5`, printed as the `A1-A5`
column and written as `swing_verdict`). **`Z*`, `L*` and `A9*` are not part of it**: they
are session lines, so every swing can read "pass" under an overall FAIL on `Z*`. The
sentence above the table says so.

**3. One line per criterion**, judged on the assessed swings and the pooled figures. Each
line shows `assessed/total` and names the comparison it used ("Judged on").

- **NOT ASSESSABLE**: nothing to judge.
- **FAIL**: one assessed swing fails, or the pooled figure does. A verdict that could not
  be reached on another swing does not hide it.
- **PASS**: every attempted swing was assessed and passes, on a pairing whose counts agreed
  and whose order was verified or stated.
- **INCOMPLETE**: the assessed swings pass, but some swing was not assessed or not
  analysed, or the counts did not agree (`--allow-count-mismatch`).

**4. Each line is then qualified once, by the session's state:**

- **Pairing unverified** (see "An unverified pairing is neither passed nor failed"): no
  line is passed or failed. A FAIL reads `UNRESOLVED (pairing unverified; as paired, this
  line would fail)`; INCOMPLETE and NOT ASSESSABLE read `... (pairing unverified)`.
- **A swing is UNRESOLVED** (step 1): a line that is not a FAIL reads **UNRESOLVED**
  instead of INCOMPLETE (NOT ASSESSABLE has `UNRESOLVED` appended). The missing swing is
  not neutral: it may be the failure. Find out why it was set aside before reading
  anything into the line. A FAIL on the swings that were assessed stays a FAIL.

**5. One overall verdict per phone**, under the criteria and last on stdout: the worst
line. FAIL if any line fails; else UNRESOLVED; else INCOMPLETE (a line incomplete or not
assessable); else PASS. It names the lines responsible and the lines NOT EVALUATED (A6,
A8), which a PASS does not cover, and says "pairing unverified" whenever that is why. It
does not change the exit code (see "Run it").

A1 to A5, A7 and A9 are judged. A6 (PT score) and A8 (live record against its replay) are
NOT EVALUATED. A9 uses the agreement of the two clock alignments (20 ms) in place of the
end tap, which is not read. `L*` (lag) and `Z*` (zero-pose fall) are not in the plan; they
are printed as their own lines.

### What each flag does

PROPOSED, like the limits: the rules below are this script's and await the supervisor.
The encoder is the witness of what the arm did. The table says what each flag means and
what happens to the swing. It does **not** say whose fault an UNRESOLVED swing is.

| Flag | What it means | What happens |
|---|---|---|
| `hold_not_still` | The **encoder** shows the arm moving in the last second of the hold (over 0.10 rad/s rms), or the phone record has under 1 s before the release | Not assessed |
| `phone_hold_not_still` | The phone's gyro is over that limit while the encoder shows the arm still. A noisy or biased gyro and a phone loose in its clamp cannot be told apart | **Judged** on its numbers; flag printed. Check the clamp |
| `phone_rate_off` | First-swing peak speed outside 0.80 to 1.25 of the encoder's, while the rates have one shape (cross-correlation at least 0.80) and the phone's own static tilt between hold and rest is within 8 deg of the encoder's release angle. The tilt comes from the accelerometer at rest and owes nothing to the gyro's gain or to the app, so the pair is corroborated and the phone is wrong | **Judged**: A3 (and whatever else) fails on its numbers |
| `pairing_suspect` | The rates differ in shape, or the peak speeds disagree and the static tilt does not vouch for the pair (it disagrees too, or is not available because the arm was not seen to settle) | Not assessed; **UNRESOLVED** |
| `phone_tilt_differs_from_encoder` | A wrong pair at similar speed, or a phone that moved in its clamp; peak speed and shape agree | Judged on its numbers; flag printed |
| `release_angle_mismatch` | The encoder's release angle is not the rig log's (8 deg); the phone is not involved | Not assessed |
| `release_angle_not_checked` | No release angle in the log or the trial ID, so the check was not made | Not assessed |
| `release_not_where_xcorr_expects` | The phone has no release candidate within 1 s of where cross-correlation puts the encoder's: a wrong pair, or a gyro not following the arm | Not assessed; **UNRESOLVED** |
| `clock_alignments_disagree` | The release alignment and the cross-correlation alignment put the two clocks more than 20 ms apart (`PROVISIONAL_SYNC_DISAGREE_S`), so the clock offset is not known and nothing compared across it means anything, whichever `--align` picks. Typical cause: the phone alone moving for two or three samples shortly before the release (in its clamp), which pre-empts the phone's release candidate; the release alignment then lands about a third of a second early. Such a swing used to read `lag_pegged` or a plain A1-A5 FAIL with a gain near 0 | Not assessed; **UNRESOLVED**. The `A9*` line counts such swings. `--align xcorr` shows the numbers on the other alignment, still not assessed |
| `phone_gap` | The export lost samples; the comparison cannot be trusted across the gap | Not assessed; **UNRESOLVED** |
| `not_settled` | The arm did not settle inside the window (or a `60s` row) | Not assessed |
| `phone_ends_before_settled` | The phone record stops while the arm is still swinging: stopped early by the operator, or an auto-stop mid-swing | Not assessed; **UNRESOLVED** |
| `zero_not_at_release` | The app took its zero pose more than 0.5 s from the encoder's release (`ZERO_WINDOW_S`). **The cause is not determined**: the app, the phone moving in its clamp, a knock on the arm or a bounce off the peg, a brisk lift, or a wrong pairing all do it | Not assessed; **UNRESOLVED** |
| `zero_instant_approximate` | The zero instant could not be read exactly from the replay | Not assessed |
| `lag_pegged` | No lag within the 300 ms search fits: "not a lag". The app's processing, a wrong pair, or clocks that are not aligned: not determined | Not assessed; **UNRESOLVED**. The `L*` line counts such swings |
| `no_peaks` | No far-side peak above the floor on the encoder | Not assessed |
| `manifest_order_unverified` | The pairing rests on an order nothing checked | Numbers printed "as paired"; no line of the session reads PASS or FAIL |
| `radius_placeholder` | No measured phone radius in `session.json` | Judged; flag printed |

A recording in which the replay takes no zero pose at all is "not analysed", and counted
on a line of its own ("N swing(s): app took no zero pose"). How it should count is an
**open owner decision** (see "Deferred until bench data").

## Provisional constants awaiting the supervisor

All in `encoder_pendulum_validation.py`; the report prints the values in force.

| Constant | Value | What it is |
|---|---|---|
| `PROVISIONAL_A1_RMSE_EACH_DEG`, `PROVISIONAL_A1_RMSE_MEDIAN_DEG` | 5.0, 2.0 | Plan A1 |
| `PROVISIONAL_A2_BIAS_DEG`, `PROVISIONAL_A2_LOA_DEG` | 1.0, 5.0 | Plan A2 |
| `PROVISIONAL_A3_GAIN`, `PROVISIONAL_A3_R_MIN` | 0.97 to 1.03, 0.995 | Plan A3 |
| `PROVISIONAL_A4_EXCURSION_DEG`, `PROVISIONAL_A4_EXCURSION_FRAC` | 3.0, 0.05 | Plan A4 |
| `PROVISIONAL_A5_F_FRAC`, `PROVISIONAL_A5_TAU_FRAC`, `PROVISIONAL_A5_N` | 0.02, 0.10, 1 | Plan A5 |
| `PROVISIONAL_A7_SLOPE_DEG_PER_KG` | 0.25 | Plan A7 |
| `PROVISIONAL_A9_CLOCK_PPM` | 200 | Plan A9, applied to the session mean plus 2 SE |
| `PROVISIONAL_A1_A2_VARIANT` | `"lagrm"` | **Decision needed.** Which comparison A1 and A2 are judged on. `lagrm` is the plan's "at the fitted lag"; it forgives the app's zero pose and lag. `abs` forgives nothing. |
| `PROVISIONAL_A3_GAIN_VARIANT` | `"matched"` | **Decision needed.** Which gain A3 is judged on: `matched`, `lagrm` or `gyro`. See below. |
| `PROVISIONAL_SYNC_DISAGREE_S` | 0.020 | Not in the plan: release against cross-correlation, in place of the end tap |
| `PROVISIONAL_LAG_VS_FOOTING_MS` | 20 | Not in the plan: fitted lag against the footing's EMA at the swing's frequency |
| `PROVISIONAL_ZERO_POSE_FALL_MAX_DEG` | 1.0 | Not in the plan: how far the arm may fall before the app's zero pose |

**Gating thresholds: PROPOSED too.** These are not acceptance limits, but each decides
whether a swing is judged at all (set aside with a flag, or the file or session refused),
so each can turn a FAIL into "not assessed" or the reverse. None is in the plan.
The report prints them in the same table, marked `PROPOSED gating threshold`
(`GATING_THRESHOLD_NAMES` in the script).

| Constant | Value | What it decides |
|---|---|---|
| `HOLD_STILL_MAX_RAD_S` | 0.10 rad/s | rms rate over the last second of the hold: `hold_not_still` (encoder), `phone_hold_not_still` (phone only) |
| `PAIRING_MIN_XCORR` | 0.80 | cross-correlation of the two rates below this: `pairing_suspect` |
| `PAIRING_PEAK_RATE_RATIO` | 0.80 to 1.25 | phone/encoder first-swing peak speed outside this: `phone_rate_off` (judged) or `pairing_suspect` |
| `PAIRING_RELEASE_ANGLE_TOL_DEG` | 8 deg | encoder release angle against the log's (`release_angle_mismatch`), and against the phone's static tilt (which of the two above) |
| `ZERO_WINDOW_S` | -0.5 to +0.5 s | the app's zero pose outside this window of the release: `zero_not_at_release` (not assessed, UNRESOLVED, no cause given) |
| `PROVISIONAL_SYNC_DISAGREE_S` (above) | 0.020 s | also a gate: release and cross-correlation alignments further apart than this, `clock_alignments_disagree` (not assessed, UNRESOLVED) |
| `RELEASE_CANDIDATE_FRAC`, `RELEASE_STILL_S`, `RELEASE_TRAVEL_S` | 0.25, 0.5 s, 2 s | what counts as a release: a rise through a quarter of the top speed out of half a second of stillness, the last one with at least half the busiest candidate's travel in the next 2 s. Decides where the release, and so everything, is |
| `RELEASE_NEAR_TOL_S` | 1.0 s | the phone's release further than this from where cross-correlation puts the encoder's: `release_not_where_xcorr_expects` |
| `SETTLED_RATE_RAD_S`, `SETTLED_FOR_S` | 0.05 rad/s, 2 s | settled; otherwise `not_settled` |
| `TAIL_AFTER_SETTLE_S` | 3 s | how long after settling the record is read (the rest window, the static tilt) |
| `MIN_SWING_COVERED_S` | 2 s | less swing than this covered by both records: the swing is not analysed |
| `LAG_SEARCH_S` | 0.30 s | a fitted lag on this bound: `lag_pegged` (not assessed, UNRESOLVED); also the tail of the phone record that is not compared |
| `CAPPED_SWING_WINDOW_S` | 50 s | how much of a `60s` row is read |
| `PHONE_GAP_MAX_FACTOR` | 5 | a gyro interval over this many median intervals: `phone_gap` |
| `PEAK_FLOOR_FRAC`, `PEAK_FLOOR_DEG` | 0.05, 1 deg | the floor a peak must clear to be scored and counted (A4, A5): `no_peaks` |
| `ENCODER_MAX_PLAUSIBLE_DEG_S`, `ENCODER_JUMP_MIN_DEG`, `ENCODER_MAX_JUMP_FRACTION` | 2000 deg/s, 2 deg, 0.1% | an encoder log with more than this share of samples stepping faster than an arm can is refused as not a position (wrong `n_channels`, rate, units or byte order) |
| `ENCODER_PERIOD_RATIO_OK` | 0.80 to 1.25 | the encoder's swing period over the phone's, on every analysed swing of a session, outside this on one side: the session is refused (`encoder.sample_rate_hz` is wrong) |
| `ENCODER_ANGLE_RATIO_OK` | 0.40 to 2.50 | the encoder's release angle over the rig log's, likewise: the session is refused (`encoder.position_units`, `counts_per_rev` or the channel is wrong) |

**The A3 decision.** The footing's EMA takes about 6% off a 0.8 Hz swing. Judged against
the unsmoothed encoder (`gain_lagrm`), a perfect sensor reads 0.92 to 0.94 and fails, and an
app reading 5% high reads 0.98 and passes: the window is re-centred on a phone that reads 3
to 7% high. Judged against the EMA-matched encoder (`gain_matched`), a perfect sensor reads
0.99 to 1.00 and passes and one reading 5% high reads 1.05 and fails. The script uses
`matched` by default and prints that with every A3 verdict; a test in each test file
(`...five_percent_high...`) pins this down. `matched` certifies the sensor and fusion, **not**
the displayed amplitude, which is still about 6% low because of the smoothing: that is what
`gain_abs` and `gain_lagrm` show. Which of these the validation is a claim about is the
supervisor's decision.

**The zero-pose limit.** On clean synthetic releases the arm has fallen 0.5 to 3.1 deg when
the app zeroes (largest at 20 deg), so `Z*` FAILS at 1.0 deg on every synthetic session.
That is the size of the effect, not a tuned outcome: the limit needs a decision too.

**The limits fail a perfect synthetic sensor.** In every clean synthetic session tried in
the fourth review (24 of 24) the overall verdict was FAIL (A5, A7, Z*): A5 on the swing
count at 20 deg, A7 because it requires A1 to A5 in every cell, `Z*` as above. The report
says so under its constants table. No limit was changed in response; it is an open
supervisor decision. (A test shows the pipeline can print an overall PASS end to end with
two limits loosened for that test only, `RELAXED_FOR_THE_PASS_PATH_TEST_ONLY`.)

## Things to know when reading it

- **The phone angle is the Python reference replay**, `imu_calibration_tuner.replay_trial`
  with `REFERENCE_REPLAY_CONFIG` on the 10 ms grid (footing `ref-100hz-ema0.5-flexrec`). It
  is not the shipped wasm (`webapp/scripts/replay-trial.mjs`), which the plan names as
  signal A. If the reference config changes the script refuses to run until its footing
  label is updated; a recording in which the replay takes no zero pose has no app angle and
  is reported as not analysed.
- **Timing: what the lag is, and what cannot be seen.** The clocks are aligned on the gyro
  against the encoder's velocity. A delay between the motion and the phone's timestamp is
  therefore absorbed into the clock offset and is **not observable** here; only the end tap
  or a hardware sync could show it. `lag_ms` is the app's processed angle behind its own
  gyro. It is judged (`L*`) against what the footing's EMA alone gives at the swing's
  frequency, taken from the discrete 10 ms filter: about 64 ms at 0.8 Hz (the continuous
  formula's 69 ms is not used). A fit that ends on the edge of the 300 ms search is flagged
  `lag_pegged` and reported as `lag_pegged_at_ms`, not as a lag. The swing is not assessed
  and UNRESOLVED, and the `L*` line counts such swings ("not a lag"). It is not called an
  app fault: a wrong pair or clocks that are not aligned do the same.
- **The app's zero pose is not the hold.** It is taken at the last sample before the gyro
  reaches 1 rad/s, 40 to 110 ms after the release on the synthetic trials. `_clock`,
  `_lagrm` and `_matched` refer the encoder to the same instant and so do not count the fall
  as error; `_abs` does, and `Z*` judges the fall itself. The instant is read exactly from
  the replay's own quaternions (with a second replay on 1 ms ticks when no 10 ms tick falls
  in the gap, used only if it takes the identical zero quaternion). If it still cannot be
  read exactly it is looked for within 0.5 s of the release only, flagged
  `zero_instant_approximate`, and the swing is not assessed. A zero pose more than 0.5 s
  from the release is flagged `zero_not_at_release`: the swing is not assessed and is
  UNRESOLVED. The cause is not determined (the app, the phone moving in its clamp, a knock,
  a brisk lift, or a wrong pairing).
- **What depends on the zero instant.** The gain does not (under 0.01 for +/-20 ms at 40
  and 60 deg, 0.02 at 20 deg). The app-zero **bias** does: about 1 deg per 20 ms, because
  the arm is moving at 1 rad/s there. The instant is known to one phone sample, so the
  `_clock`, `_lagrm` and `_matched` biases carry up to about half a degree from this alone,
  against an A2 limit of 1 deg. `bias_abs` does not depend on it.
- **The magnitude folds a little.** On the return swing a lightly damped arm can pass the
  app's zero pose, and the app's angle (a magnitude) turns back up. The encoder is folded
  the same way and peaks are read on the far side only. On clean synthetic releases
  `fold_fraction` is 0.001 to 0.004 at a damping ratio of 0.03 (20 to 60 deg), 0.002 or less
  at 40 and 60 deg even at 0.005, and reaches 0.02 (damping 0.005) to 0.06 (0.002) only at
  20 deg. An earlier version of this note said the plan's "never folds" does not hold for a
  bare bearing; that overstated it.
- **The swing count.** Counted on each source against the same floor (5% of the first
  amplitude, at least 1 deg). The app's smoothed peaks are about 6% lower, so at 20 deg it
  counts fewer than the encoder: 2 fewer in this repo's synthetic session, 3 fewer seen at
  20 deg in the fourth review, against a limit of 1. The synthetic sessions FAIL A5 on the
  count there. This is the script's count, not the app scorer's N.
- **Radius.** The gyro angle does not depend on where the phone is clamped. The
  accelerometer does: centripetal `r*omega^2` along the arm, tangential `r*alpha` across it,
  so the across-arm reading is `g*sin(theta)*(1 - r/L_eq)`. The fusion uses the
  accelerometer below 0.3 rad/s, which is every turning point and all of the late ring-down.
  On the synthetic trials the first swing is the same at every radius, and the late
  ring-down and rest level drift with radius. Session S5 is what tests this on hardware.
- **`stopped_by = 60s`** rows are compared up to 50 s after the release, before the hand,
  and are flagged `not_settled` (reported, not passed) unless the arm settled before then.
  A raw-mode end tap falls outside the window (it closes 3 s after the arm settles).

## A different encoder log format

The reader sits behind a small adapter. One format is implemented: `labview_bin`.

```python
import numpy as np
import encoder_pendulum_validation as V

def read_my_daq(path, *, sample_rate_hz, zero_deg=0.0) -> V.EncoderTrace:
    # keyword options = the "encoder" block of session.json, less format/model/daq/file_prefix
    t_s, angle_deg = ...                               # seconds on the DAQ clock; degrees
    return V.EncoderTrace(t_s=t_s, angle_deg=angle_deg, fs_hz=sample_rate_hz, source=str(path))

V.register_encoder_reader("my_daq", read_my_daq)       # then "encoder": {"format": "my_daq", ...}
```

`t_s` must be strictly increasing. Any constant zero and either sign are accepted. Add the
reader to `ENCODER_READERS` in the script and one test that writes a small file and reads it
back. Name the options explicitly (no `**opts`): an option the reader does not take is then
refused instead of ignored. `daq_tbd` is a placeholder that raises with these instructions.

## Deferred until bench data

**Attribution of a fault to the app was removed** (fifth round, on the owner's decision to
split the work). Earlier versions of this script tried to say whose fault a zero pose away
from the release was: the app's (`zero_misplaced_by_app`, a FAIL on an `APP*` line), the
operator's (`zero_on_other_motion`), the phone's in its clamp (`zero_on_phone_only_motion`),
or undecidable (`zero_encoder_cannot_tell`), using the encoder's wide-band rate
(`ZERO_WIDEBAND_SPAN_S` and four more thresholds), the phone's raw gyro, and a minimum
encoder resolution (`zero_rule_min_counts_per_rev`). A lag on the edge of the search
(`lag_pegged`) was an app fault too. All of that is gone: the flags, the thresholds, the
`app_fault`, `app_fault_suspected`, `pass_no_app_fault` and `pass_all` columns, the "App
fault" column, the `APP*` line and the encoder-resolution warning.

Why, from the fourth review's probes:

- **The one case called an app fault cannot occur.** The reference replay takes its zero
  pose only on a raw gyro sample of 1.0 rad/s or more after a still hold. A zero that
  "neither the encoder nor the phone's gyro explains" therefore does not exist in what the
  replay produces: the gyro always explains it. The rule could only fire on a zero planted
  by a test.
- **The routes that were left blamed the app for the clamp.** A phone moving in its clamp
  for two or three samples shortly before the release was read as `lag_pegged`, an app
  fault, when it had only misled the clock alignment (now `clock_alignments_disagree`).
- A wrong `sample_rate_hz` or `position_units` in `session.json` read as a session of
  suspected app faults (now refused).

What is kept: a zero pose more than 0.5 s from the release is **one** flag,
`zero_not_at_release`; a lag that is not a lag is `lag_pegged`. Both gate the swing (NOT
ASSESSED) and make it UNRESOLVED, and the report says the cause is not determined. To bring
attribution back needs recordings of the real thing: a knock on the arm, a phone slipping
in its clamp, a brisk lift, on the real encoder at its real resolution and noise, so that a
rule can be shown to tell them apart before it is trusted to blame anyone.

Also open, and the owner's:

- **A swing on which the app took no zero pose.** It is "not analysed" (exit 1, no line can
  read PASS), and counted on its own line in the summary and on stdout: "N swing(s): app
  took no zero pose". Whether such a swing is a failed swing of the app, an operator error
  or neutral is an **open owner decision**; the script does not decide it.
- **The limits.** On a perfect synthetic sensor the PROPOSED limits currently give
  FAIL (A5, A7, Z*) in every clean synthetic session tried in the fourth review (24 of 24).
  No limit was changed. Whether the limits move, or the estimators behind A5 and `Z*` do,
  is an open supervisor decision.

## Not verified

- No real pendulum data. All recovery figures are from synthetic trials: the repo's
  (`encoder_pendulum_synth.py`, the phone model of `neuromech/phone_trial.py`) and an
  independent one in `tests/test_encoder_pendulum_independent.py` (adaptive ODE solver,
  different sensor frame, files written directly).
- Which comparison A1 to A3 should be judged on, and every limit: not decided.
- Polarity and side of the swing: not detectable (magnitudes are compared).
- Latency of the phone's sensor path: not observable (the clocks are aligned on the gyro).
- The app scorer's own N and PT parameters; the shipped wasm; the live record (A6, A8).
- The encoder format, sample rate and channel layout of the new pivot encoder.
- Android exports (sample rate, timestamp units, axis convention) have only been simulated
  as a 100 Hz phone.
- A real hand release. A simulated release with the hand's support fading over 0.2 to 0.8 s
  is analysed and its zero-pose fall (3 to 6 deg) is reported and fails `Z*`, but a push, a
  snag or a bounce off the peg has not been simulated.
- A pairing shifted between two repeats of the same angle and weight cannot be detected.
- A wrong pair at the same angle across two weights is not detected either, and is worse:
  it arrives as an ordinary unflagged FAIL on A3, A5 or the lag (38 of 60 synthetic swaps
  in the second review; not re-run here), reading exactly like a faulty app. See "A wrong
  pairing can look like an app failure" for the mitigation.
- Whose fault a zero pose away from the release is: **not determined by this script** (see
  "Deferred until bench data"). No real knock, clamp slip or gyro glitch has been recorded.
- An encoder scale error and an accelerometer error cannot be told apart by the
  "Encoder scale?" note; it was tried on a synthetic encoder scaled x1.05 only.
- A rig log in an encoding other than UTF-8, UTF-16 (refused) or Windows-1252 is read as
  Windows-1252 and may show wrong characters in the notes; nothing checks that.
- A `60s` row is read to 50 s and is never passed unless the arm settled before then; a
  lightly damped arm that needs longer cannot be validated by this script as it stands.
- The pairing rules ("What each flag does") have been exercised on synthetic faults only: a
  gyro scale of 0.78 and 1.30, an app zero moved into the hold and onto a brisk lift, an
  app angle shifted by 400 ms, a phone-only gyro blip before the release. The phone's static
  tilt as a pairing witness assumes a horizontal pivot and a phone that does not move in
  its clamp; it has not been tried on hardware.
- The two session-wide refusals for a mis-described encoder (period ratio, release-angle
  ratio) were tried on 500 Hz and 2 kHz written for a 1 kHz file, and on `counts` written
  for a degrees file, synthetic sessions of two swings. A sample rate wrong by less than
  20%, or a scale wrong by less than a factor of 2, is not refused (the scale note covers
  3% and up; between them see "So can an encoder scale error").
- The existing motor-rig recordings are horizontal (gravity on the rotation axis) and
  motor-driven; the read-only run on them (with the first version of this script) exercised
  the reader and the alignment only, and has not been repeated with this version.
