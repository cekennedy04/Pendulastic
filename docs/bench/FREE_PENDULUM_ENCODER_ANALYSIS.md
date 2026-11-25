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
      angle actually set: it is checked against the encoder (8 deg).
- [ ] One encoder file per swing in `encoder/`, named so that its **last number is the
      `encoder_trial`**: `pf<MMDD>_NNN.bin`. Numbers are never reused within a session: two
      files ending in the same number are refused. Leave no stray files in `encoder/`: the
      number of files must equal the number of rows that name one.
- [ ] The app export in `phone/`: the `*-manifest.json` and every `*.jsonl`, exported from
      the browser tab in one go. File names are left as the app writes them.
- [ ] Start the encoder log before the lift and stop it last, so it covers the hold, the
      whole ring-down and at least 5 s of rest.
- [ ] Hold the arm still against the peg for 5 s and raise it slowly. The release is found
      as the last rise out of stillness with a swing after it; a hold shorter than about
      1 s, or a hand still moving at release, is flagged (`hold_not_still`) and the swing
      is not assessed. Let go cleanly: the app zeroes when the gyro reaches 1 rad/s, and
      every degree the arm creeps before that is a degree off the absolute reading.

**Pairing rule** (the plan's): rig-log rows whose `app_result` starts with `ERROR` are
dropped; the rest are matched in order to the exported phone trials sorted by manifest
timestamp. If the counts differ (rows against phone trials, or rows naming an encoder file
against encoder files) the script stops (`COUNT MISMATCH`) rather than guess. Fix the log;
`--allow-count-mismatch` pairs up to the shorter list and says so in the report.

**Equal counts do not prove the pairing.** A wrong swing of the same shape correlates above
0.9, so the cross-correlation does not discriminate. What can catch a shift by one: the
encoder's release angle against the log's (`release_angle_mismatch`, 8 deg) and the ratio of
first-swing peak speeds (`pairing_suspect`, outside 0.80 to 1.25). Two repeats at the same
angle and weight swapped with each other cannot be told apart from the data; only the log can.

**Still unknown, and the owner's to supply:** encoder model, counts per revolution, DAQ and
log format; the measured phone radius; the supervisor's confirmation of the limits and of
the comparison each is judged on.

## The four comparisons, and the raw gyro

Every agreement figure is over the **swing window** (release to settled; to the end of the
record if the arm never settles, which is flagged). The hold and the rest are reported apart
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
| Clock offset by release, and by cross-correlation | `clock_offset_release_s`, `clock_offset_xcorr_s`, `clock_offset_diff_ms` | From angular rate only. `--align` picks which one is used (release by default). |
| Clock-rate ratio | `clock_rate_ppm`, `clock_rate_ppm_se` | From zero crossings over the ring-down. One swing resolves it to about 15 to 50 ppm on the synthetic trials, so A9 is judged on the session's weighted mean with 2 SE. |
| Lag of the app's angle behind its own gyro | `lag_ms`, `ema_expected_lag_ms`, `lag_minus_expected_ms`, `lag_matched_ms`, `lag_pegged_at_ms` | See "Timing" below. |
| RMSE, bias, SD, 95% limits of agreement, r, gain | `rmse_*`, `bias_*`, `sd_*`, `loa_lo_*`, `loa_hi_*`, `r_*`, `gain_*` for `abs`, `clock`, `lagrm`, `matched` | Swing window. Also `rmse_first_swing_lagrm`, `rmse_ringdown_lagrm`. |
| The three gains | `gain_gyro`, `gain_lagrm`, `gain_matched` (and `gain_abs`, `gain_clock`) | Side by side in every table. |
| Sensitivity to the zero instant | `gain_matched_zero_pm20ms_span`, `bias_lagrm_zero_pm20ms_span` | What moving the zero instant by +/-20 ms does. |
| Peak-by-peak amplitude error | `first_excursion_error_deg` (app-zero), `first_excursion_error_abs_deg` (from the hold), `peak_error_*`, `peaks.json` | One far-side peak per cycle. |
| Period and frequency error | `period_error_pct`, `f_error_pct` | Same estimator on both. |
| Decay, damping ratio, swing count | `tau_error_pct`, `zeta_error`, `zeta_error_pct`, `n_swings_phone`, `n_swings_encoder`, `n_swings_error` | Height-weighted log fit. The count is made on each source's own peak heights. It is this script's count, **not the app scorer's N**. |
| Pairing checks | `release_from_rest_deg`, `release_angle_minus_nominal_deg`, `peak_rate_gain`, `xcorr_quality` | |
| The off-axis phone | `phone_radius_m`, `phone_radius_measured`, `l_eq_small_angle_m`, `tangential_accel_fraction`, `centripetal_peak_m_s2`, `tangential_peak_m_s2`, `accel_dev_from_g_max_m_s2`, `accel_gate_open_fraction` | See below. |
| The app's zero pose | `app_zero_exact`, `zero_after_release_ms`, `zero_pose_fall_deg`, `fold_fraction` | See below. |
| Verdicts | `pass_*`, `pass_all` (A1 to A5), `not_assessed_because`, `judged_on` | Blank = not assessed. |

`summary.md` repeats the run sheet's table (Seq, Trial ID, Condition, Release, Mode, Encoder
file, App result, Stopped by) with the results beside each row, then a weight by release-angle
grid, tables by condition, release angle, mode and phone, one line per criterion, and the
list of provisional constants.

## Verdicts

- **A flagged swing is reported but not passed.** Any of `hold_not_still`, `phone_gap`,
  `pairing_suspect`, `release_angle_mismatch`, `release_not_where_xcorr_expects`,
  `not_settled`, `zero_instant_approximate`, `zero_not_at_release`, `lag_pegged`, `no_peaks`
  makes every verdict of that swing blank (NOT ASSESSED), with the reason in
  `not_assessed_because`. `radius_placeholder` does not gate: the angle comparison does not
  use the radius.
- **A criterion prints PASS only if every attempted swing was assessed and passed.** Each
  criterion line shows `assessed/total`. One failing swing gives FAIL; all assessed swings
  passing while some swing was not assessed or not analysed gives INCOMPLETE; nothing to
  judge gives NOT ASSESSABLE.
- **Every criterion line names the comparison it used** ("Judged on").
- A1 to A5, A7 and A9 are judged. A6 (PT score) and A8 (live record against its replay) are
  NOT EVALUATED. A9 uses the agreement of the two clock alignments (20 ms) in place of the
  end tap, which is not read. `L*` (lag) and `Z*` (zero-pose fall) are not in the plan; they
  are printed as their own lines and are **not** part of `pass_all`.

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

**The zero-pose limit.** On clean synthetic releases the arm has fallen 0.5 to 2.9 deg when
the app zeroes (largest at 20 deg), so `Z*` FAILS at 1.0 deg on every synthetic session.
That is the size of the effect, not a tuned outcome: the limit needs a decision too.

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
  `lag_pegged`, reported as `lag_pegged_at_ms`, and the swing is not assessed.
- **The app's zero pose is not the hold.** It is taken at the last sample before the gyro
  reaches 1 rad/s, 40 to 110 ms after the release on the synthetic trials. `_clock`,
  `_lagrm` and `_matched` refer the encoder to the same instant and so do not count the fall
  as error; `_abs` does, and `Z*` judges the fall itself. The instant is read exactly from
  the replay's own quaternions (with a second replay on 1 ms ticks when no 10 ms tick falls
  in the gap, used only if it takes the identical zero quaternion). If it still cannot be
  read exactly it is looked for within 0.5 s of the release only, flagged
  `zero_instant_approximate`, and the swing is not assessed; a zero pose more than 0.5 s
  from the release (the app zeroed on a lift) is flagged `zero_not_at_release`.
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
  counts up to 2 fewer than the encoder and the synthetic sessions FAIL A5 on the count
  there. This is the script's count, not the app scorer's N.
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
- The existing motor-rig recordings are horizontal (gravity on the rotation axis) and
  motor-driven; the read-only run on them (with the first version of this script) exercised
  the reader and the alignment only, and has not been repeated with this version.
