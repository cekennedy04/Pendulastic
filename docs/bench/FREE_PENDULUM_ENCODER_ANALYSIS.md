# Free-pendulum encoder validation: running the analysis

Goes with `FREE_PENDULUM_ENCODER_RUN_SHEET.md` and the plan
(`docs/plans/2026-11-18-001-plan-free-pendulum-encoder-weight-validation.md`). The script is
`encoder_pendulum_validation.py`; it is the first part of the analyser the plan calls
`pendulum_fixture_report.py` (section 11). Tests: `tests/test_encoder_pendulum_validation.py`.

**Status.** Built and tested on synthetic trials only. No real pendulum has been recorded.
The encoder file format is assumed to be the motor rig's LabVIEW `.bin`. Every acceptance
limit is **PROPOSED - pending supervisor confirmation before session S1**. No MAS grade or
clinical statement follows from any output.

## Run it

```
# after a session, from the repo root, with the venv
python encoder_pendulum_validation.py session Pendulastic_Mobile_App_Trial/pendulum_fixture/<YYYY-MM-DD>_<session>_<phone> --plots

# several sessions (both phones) in one report
python encoder_pendulum_validation.py session <folder S1> <folder S2> --out <report folder>

# one swing, no session folder
python encoder_pendulum_validation.py trial --phone X.jsonl --encoder pf1126_001.bin --radius-m 0.30 --plots

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
- [ ] `encoder.n_channels` if the VI does not write 6 or 7 channels per sample.
- [ ] `encoder.position_units`: `deg`, or `counts` together with `encoder.counts_per_rev`
      (after x4 decoding). `encoder.sign` (+1 or -1) is optional: polarity is resolved.
- [ ] `encoder.model`, `encoder.counts_per_rev`, the DAQ or counter card, `encoder.zero_deg`
      (the reading with the arm hanging still).
- [ ] `phone_radius_m`: **measured**, pivot axis to the centre of the phone, with
      `phone_radius_measured: true`. Until then the report prints 0.30 m as a PLACEHOLDER.
- [ ] `phone.model`, `phone.os`, `phone.browser`, `app_build`, `app_algorithm` (app footer),
      `phone_orientation`, `pivot_tilt_deg`, `date`, `operator`, `analyser_commit`.
- [ ] `weights`: the measured mass (kg, scale to 10 g) and radius of each stack, by ID
      (`W1` ... `W4`; `W0` is taken as 0). Without them criterion A7 is not assessable.

**Per swing:**

- [ ] One row in `rig_log.csv` (the template), **including failed attempts**: `seq`,
      `trial_id` (as printed on the run sheet, `PF-S1-W3-A40-Q-17`), `encoder_trial` (the
      encoder file number), `condition`, `release_angle_deg`, `mode`, `app_result`
      (`scored`, `NOT SCORABLE`, or `ERROR: ...`), `stopped_by` (`auto` or `60s`), `notes`.
- [ ] One encoder file per swing in `encoder/`, named so that its **last number is the
      `encoder_trial`**: `pf<MMDD>_NNN.bin`. Numbers are never reused within a day.
- [ ] The app export in `phone/`: the `*-manifest.json` and every `*.jsonl`, exported from
      the browser tab in one go. File names are left as the app writes them.
- [ ] Start the encoder log before the lift and stop it last, so it covers the hold, the
      whole ring-down and at least 5 s of rest.
- [ ] Hold the arm still against the peg for 5 s and raise it slowly. The release is found
      as the last rise out of stillness with a swing after it; a hold shorter than about
      1 s, or a hand still moving at release, is flagged (`hold_not_still`).

**Pairing rule** (the plan's): rig-log rows whose `app_result` starts with `ERROR` are
dropped; the rest are matched in order to the exported phone trials sorted by manifest
timestamp. If the two counts differ the script stops (`COUNT MISMATCH`) rather than guess.
Fix the log; `--allow-count-mismatch` pairs up to the shorter list and says so in the report.

**Still unknown, and the owner's to supply:** encoder model, counts per revolution, DAQ and
log format; the measured phone radius; the supervisor's confirmation of the limits.

## What the report gives, per swing

| Quantity | Column(s) in `trials.csv` | Note |
|---|---|---|
| Clock offset by release, and by cross-correlation | `clock_offset_release_s`, `clock_offset_xcorr_s`, `clock_offset_diff_ms` | From angular rate only. `--align` picks which one is used (release by default). |
| Clock-rate ratio | `clock_rate_ppm` | From zero crossings over the ring-down. |
| Lag of the app's angle | `lag_ms`, `lag_matched_ms`, `ema_expected_lag_ms` | What is left once the clocks agree. |
| RMSE, bias, 95% limits of agreement, r, gain | `rmse`, `bias`, `loa_lo`, `loa_hi`, `r`, `gain` | Lag removed. `*_clock`: lag not removed. `*_matched`: against the encoder through the footing's EMA. Also by segment: `rmse_hold`, `rmse_first_swing`, `rmse_ringdown`, `rmse_rest`. |
| Peak-by-peak amplitude error | `first_excursion_error_deg`, `peak_error_mean_deg`, `peak_error_max_abs_deg`, `peaks.json` | One far-side peak per cycle. |
| Period and frequency error | `period_error_pct`, `f_error_pct` | Same estimator on both. |
| Decay and damping-ratio error | `tau_error_pct`, `zeta_error`, `zeta_error_pct`, `n_swings_error` | Height-weighted log fit of the peaks. |
| Raw gyro against the encoder | `gyro_gain`, `gyro_rmse_clock`, `gyro_lag_ms` | No fusion, no smoothing: the sensor on its own. |
| The off-axis phone | `phone_radius_m`, `phone_radius_measured`, `l_eq_small_angle_m`, `tangential_accel_fraction`, `centripetal_peak_m_s2`, `tangential_peak_m_s2`, `accel_dev_from_g_max_m_s2`, `accel_gate_open_fraction` | See below. |
| The app's zero pose | `zero_after_release_ms`, `zero_pose_fall_deg`, `fold_fraction` | See below. |

`summary.md` repeats the run sheet's table (Seq, Trial ID, Condition, Release, Mode, Encoder
file, App result, Stopped by) with the results beside each row, then a weight by release-angle
grid, tables by condition, release angle, mode and phone, and one line per plan criterion.
A1 to A5, A7 and A9 are judged; A6 (PT score) and A8 (live record against its replay) are
listed as NOT EVALUATED. A9 uses the agreement of the two clock alignments (20 ms) in place
of the end tap, which is not read.

## Things to know when reading it

- **The phone angle is the Python reference replay**, `imu_calibration_tuner.replay_trial`
  with `REFERENCE_REPLAY_CONFIG` on the 10 ms grid (footing `ref-100hz-ema0.5-flexrec`). It
  is not the shipped wasm (`webapp/scripts/replay-trial.mjs`), which the plan names as
  signal A. If the reference config changes the script refuses to run until its footing
  label is updated; a recording in which the replay takes no zero pose has no app angle and
  is reported as not analysed.
- **Lag and gain carry the footing's own smoothing.** The EMA has a time constant of about
  72 ms. At 0.8 Hz that is about 65 ms of lag and about 6% off the swing's amplitude, on a
  perfect sensor. On the synthetic 60 Hz session the lag-removed gain is 0.93 to 0.97, which is
  **outside the proposed A3 limit (0.97 to 1.03) by construction** (the synthetic sessions
  also miss A5 on the swing count at 20 deg, by 2 against a limit of 1). `gain_matched` and
  `gyro_gain` say whether the sensor and fusion are tracking. Whether A3 should be judged on
  `gain`, on `gain_matched`, or on the raw gyro is a decision for the supervisor before S1.
- **The app's zero pose is not the hold.** It is taken at the last sample before the gyro
  reaches 1 rad/s, by which time the arm has fallen (`zero_pose_fall_deg`: 0.5 to 3 deg on the
  synthetic trials, largest at 20 deg). The encoder is referred to the same instant, so this
  does not count as error, but that instant is known only to one phone sample; at 1 rad/s one
  sample is about 1 deg. Expect a bias of a few tenths of a degree from this alone.
- **The magnitude folds.** On a lightly damped arm the return swing passes the zero pose
  and the app's angle (a magnitude) turns back up. The encoder is folded the same way and
  peaks are read on the far side only. `fold_fraction` says how much of the swing is affected.
  The plan's statement that the angle never folds does not hold for a bare bearing.
- **Radius.** The gyro angle does not depend on where the phone is clamped. The
  accelerometer does: centripetal `r*omega^2` along the arm, tangential `r*alpha` across it,
  so the across-arm reading is `g*sin(theta)*(1 - r/L_eq)`. The fusion uses the
  accelerometer below 0.3 rad/s, which is every turning point and all of the late ring-down.
  On the synthetic trials the first swing is the same at every radius, and the late
  ring-down and rest level drift with radius (rest offset -0.6 deg on the axis, -1.0 deg at
  0.40 m). Session S5 is what tests this on hardware.
- **`stopped_by = 60s`** rows are compared up to 50 s after the release, before the hand.
  A raw-mode end tap falls outside the window (it closes 3 s after the arm settles).

## A different encoder log format

The reader sits behind a small adapter. One format is implemented: `labview_bin`.

```python
import numpy as np
import encoder_pendulum_validation as V

def read_my_daq(path, **opts) -> V.EncoderTrace:      # opts = the "encoder" block of session.json
    t_s, angle_deg = ...                               # seconds on the DAQ clock; degrees
    return V.EncoderTrace(t_s=t_s, angle_deg=angle_deg, fs_hz=opts["sample_rate_hz"], source=str(path))

V.register_encoder_reader("my_daq", read_my_daq)       # then "encoder": {"format": "my_daq", ...}
```

`t_s` must be strictly increasing. Any constant zero and either sign are accepted. Add the
reader to `ENCODER_READERS` in the script and one test that writes a small file and reads it
back. `daq_tbd` is a placeholder that raises with these instructions.

## Not verified

- No real pendulum data. All recovery figures are from synthetic trials
  (`encoder_pendulum_synth.py`: a nonlinear damped pendulum, the phone model of
  `neuromech/phone_trial.py`, a quantised 1 kHz encoder).
- The encoder format, sample rate and channel layout of the new pivot encoder.
- Android exports (sample rate, timestamp units, axis convention) have only been simulated
  as a 100 Hz phone.
- A hand release. The synthetic release is clean; a push or a snag moves the release-based
  alignment, which is why the cross-correlation is reported beside it.
- Parity of the Python replay with the shipped wasm on a pendulum swing.
- The existing motor-rig recordings are horizontal (gravity on the rotation axis) and
  motor-driven; a read-only run on them exercises the reader and the alignment only.
