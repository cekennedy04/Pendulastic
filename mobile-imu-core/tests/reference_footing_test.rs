//! The reference footing (crate 0.4.0; flexion axis fitted on the whole
//! recording from 0.5.0): the phone replays a trial the way
//! `imu_calibration_tuner.replay_trial` does for the corpus -- 10 ms ticks,
//! EMA 0.5 per 50 ms rescaled to the tick, swing projected onto a flexion
//! axis learnt from the swing. Golden series come from `replay_trial` itself
//! on synthetic raw logs (`fixtures/gen_fixtures.py`, the REF_* block).

#[path = "fixtures/golden.rs"]
mod golden;

use mobile_imu_core::ahrs::Vec3;
use mobile_imu_core::replay::{
    replay, FlexAxisFit, Method, RawSample, ReplayConfig, Sensor, SCORING_FOOTING,
};
use mobile_imu_core::resample::{rescale_ema_alpha, ANALYSIS_TICK_S, TICK_S};
use mobile_imu_core::stillness::ZERO_CAPTURE_GUARD_RAD_S;

fn xyz(flat: &[f64], i: usize) -> Vec3 {
    [flat[3 * i], flat[3 * i + 1], flat[3 * i + 2]]
}

fn log(t: &[f64], ts_ms: &[i64], accel: &[f64], gyro: &[f64]) -> Vec<RawSample> {
    assert_eq!(accel.len(), 3 * t.len());
    assert_eq!(gyro.len(), 3 * t.len());
    let mut out = Vec::with_capacity(2 * t.len());
    for i in 0..t.len() {
        out.push(RawSample { t: t[i], ts_ms: ts_ms[i], sensor: Sensor::Accel, v: xyz(accel, i) });
        out.push(RawSample { t: t[i], ts_ms: ts_ms[i], sensor: Sensor::Gyro, v: xyz(gyro, i) });
    }
    out
}

/// Positional comparison at 1e-9; a NaN on one side only is a failure.
fn assert_series(tag: &str, got: &[f64], want: &[f64]) {
    assert_eq!(got.len(), want.len(), "{tag}: length");
    let mut compared = 0;
    for (i, (g, w)) in got.iter().zip(want).enumerate() {
        assert_eq!(g.is_nan(), w.is_nan(), "{tag}[{i}]: got {g}, want {w}");
        if w.is_finite() {
            assert!((g - w).abs() < 1e-9, "{tag}[{i}]: got {g:.12}, want {w:.12}");
            compared += 1;
        }
    }
    assert!(compared > 100, "{tag}: only {compared} finite ticks compared");
}

fn check(tag: &str, t: &[f64], ts: &[i64], acc: &[f64], gyr: &[f64], want_t: &[f64], want: &[f64]) {
    let r = replay(&log(t, ts, acc, gyr), &ReplayConfig::reference())
        .unwrap_or_else(|e| panic!("{tag}: {e:?}"));
    assert_series(&format!("{tag} t"), &r.t, want_t);
    assert_series(&format!("{tag} angle"), &r.angle_deg, want);
}

#[test]
fn an_in_plane_swing_matches_the_reference_replay() {
    check("REF_INPLANE", golden::REF_INPLANE_RAW_T, golden::REF_INPLANE_RAW_TS_MS,
          golden::REF_INPLANE_RAW_ACCEL, golden::REF_INPLANE_RAW_GYRO,
          golden::REF_INPLANE_T, golden::REF_INPLANE_ANG);
}

#[test]
fn a_swing_with_a_yaw_component_matches_the_reference_replay() {
    check("REF_YAW", golden::REF_YAW_RAW_T, golden::REF_YAW_RAW_TS_MS,
          golden::REF_YAW_RAW_ACCEL, golden::REF_YAW_RAW_GYRO,
          golden::REF_YAW_T, golden::REF_YAW_ANG);
}

fn opening() -> ReplayConfig {
    ReplayConfig { flex_axis_fit: FlexAxisFit::Opening, ..ReplayConfig::reference() }
}

#[test]
fn a_release_transient_matches_the_reference_replay_on_both_fits() {
    check("REF_TRANSIENT", golden::REF_TRANSIENT_RAW_T, golden::REF_TRANSIENT_RAW_TS_MS,
          golden::REF_TRANSIENT_RAW_ACCEL, golden::REF_TRANSIENT_RAW_GYRO,
          golden::REF_TRANSIENT_T, golden::REF_TRANSIENT_ANG);
    let l = log(golden::REF_TRANSIENT_RAW_T, golden::REF_TRANSIENT_RAW_TS_MS,
                golden::REF_TRANSIENT_RAW_ACCEL, golden::REF_TRANSIENT_RAW_GYRO);
    let old = replay(&l, &opening()).unwrap();
    assert_series("REF_TRANSIENT opening", &old.angle_deg, golden::REF_TRANSIENT_OPENING_ANG);
}

#[test]
fn the_opening_fit_still_matches_the_reference_replay() {
    let l = log(golden::REF_YAW_RAW_T, golden::REF_YAW_RAW_TS_MS,
                golden::REF_YAW_RAW_ACCEL, golden::REF_YAW_RAW_GYRO);
    let old = replay(&l, &opening()).unwrap();
    assert_series("REF_YAW opening", &old.angle_deg, golden::REF_YAW_OPENING_ANG);
}

#[test]
fn the_whole_recording_fit_is_not_pulled_by_a_release_transient() {
    // The swing is about sensor x; the first 0.2 s carries a burst about y.
    let l = log(golden::REF_TRANSIENT_RAW_T, golden::REF_TRANSIENT_RAW_TS_MS,
                golden::REF_TRANSIENT_RAW_ACCEL, golden::REF_TRANSIENT_RAW_GYRO);
    let off = |r: &mobile_imu_core::replay::TrialResult| {
        r.flex_axis.unwrap()[0].abs().min(1.0).acos().to_degrees()
    };
    let rec = replay(&l, &ReplayConfig::reference()).unwrap();
    let old = replay(&l, &opening()).unwrap();
    assert!(off(&rec) < 12.0, "whole-recording axis is {} deg off", off(&rec));
    assert!(off(&old) > 2.0 * off(&rec), "opening {} vs recording {}", off(&old), off(&rec));
}

#[test]
fn handling_after_a_quiet_spell_does_not_reach_the_axis() {
    // The late burst carries more than twice the swing's gyro energy (the
    // fixture generator asserts it), about sensor y; the swing is about x.
    check("REF_RUNON", golden::REF_RUNON_RAW_T, golden::REF_RUNON_RAW_TS_MS,
          golden::REF_RUNON_RAW_ACCEL, golden::REF_RUNON_RAW_GYRO,
          golden::REF_RUNON_T, golden::REF_RUNON_ANG);
    let l = log(golden::REF_RUNON_RAW_T, golden::REF_RUNON_RAW_TS_MS,
                golden::REF_RUNON_RAW_ACCEL, golden::REF_RUNON_RAW_GYRO);
    let r = replay(&l, &ReplayConfig::reference()).unwrap();
    let ax = r.flex_axis.unwrap();
    assert!(ax[0].abs() > 0.999, "axis followed the handling: {ax:?}");
}

/// Degrees between the fitted axis and sensor x, which every fixture below
/// swings about.
fn off_x(l: &[RawSample], cfg: &ReplayConfig) -> f64 {
    replay(l, cfg).unwrap().flex_axis.unwrap()[0].abs().min(1.0).acos().to_degrees()
}

/// Pre-merge review, 2026-11-19 (F1). A fit with few fast samples used to stay
/// open to the end of the recording, so 49 samples of swing followed by
/// handling gave an axis 90 degrees off. Each count either side of
/// QUIET_STOP_MIN_SAMPLES is pinned against the Python reference, and none may
/// do worse than the opening rule does on the same log.
#[test]
fn few_fast_samples_then_handling_does_not_take_the_axis() {
    macro_rules! case {
        ($tag:literal, $t:ident, $ts:ident, $a:ident, $g:ident, $wt:ident, $w:ident) => {{
            check($tag, golden::$t, golden::$ts, golden::$a, golden::$g, golden::$wt, golden::$w);
            let l = log(golden::$t, golden::$ts, golden::$a, golden::$g);
            let (rec, old) = (off_x(&l, &ReplayConfig::reference()), off_x(&l, &opening()));
            assert!(rec < 5.0, "{}: whole-recording axis {rec} deg off the swing", $tag);
            assert!(rec <= old + 1.0, "{}: recording {rec} deg, opening {old} deg", $tag);
        }};
    }
    case!("REF_CLIFF24", REF_CLIFF24_RAW_T, REF_CLIFF24_RAW_TS_MS, REF_CLIFF24_RAW_ACCEL,
          REF_CLIFF24_RAW_GYRO, REF_CLIFF24_T, REF_CLIFF24_ANG);
    case!("REF_CLIFF25", REF_CLIFF25_RAW_T, REF_CLIFF25_RAW_TS_MS, REF_CLIFF25_RAW_ACCEL,
          REF_CLIFF25_RAW_GYRO, REF_CLIFF25_T, REF_CLIFF25_ANG);
    case!("REF_CLIFF49", REF_CLIFF49_RAW_T, REF_CLIFF49_RAW_TS_MS, REF_CLIFF49_RAW_ACCEL,
          REF_CLIFF49_RAW_GYRO, REF_CLIFF49_T, REF_CLIFF49_ANG);
    case!("REF_WEAKBUMP", REF_WEAKBUMP_RAW_T, REF_WEAKBUMP_RAW_TS_MS, REF_WEAKBUMP_RAW_ACCEL,
          REF_WEAKBUMP_RAW_GYRO, REF_WEAKBUMP_T, REF_WEAKBUMP_ANG);
}

/// The same bump three seconds after a weaker swing: QUIET_STOP_WEAK_S has
/// closed the fit, which the opening rule (still short of 25 samples) cannot
/// do, so here the whole-recording fit must beat it.
#[test]
fn a_bump_long_after_a_weak_swing_is_kept_out_entirely() {
    check("REF_WEAKLATE", golden::REF_WEAKLATE_RAW_T, golden::REF_WEAKLATE_RAW_TS_MS,
          golden::REF_WEAKLATE_RAW_ACCEL, golden::REF_WEAKLATE_RAW_GYRO,
          golden::REF_WEAKLATE_T, golden::REF_WEAKLATE_ANG);
    let l = log(golden::REF_WEAKLATE_RAW_T, golden::REF_WEAKLATE_RAW_TS_MS,
                golden::REF_WEAKLATE_RAW_ACCEL, golden::REF_WEAKLATE_RAW_GYRO);
    let (rec, old) = (off_x(&l, &ReplayConfig::reference()), off_x(&l, &opening()));
    assert!(rec < 1.0, "whole-recording axis {rec} deg off the swing");
    assert!(old > 45.0, "the opening rule was expected to be taken by the bump, got {old} deg");
}

#[test]
fn a_swing_with_few_qualifying_samples_matches_the_reference_replay() {
    check("REF_NOCOMMIT", golden::REF_NOCOMMIT_RAW_T, golden::REF_NOCOMMIT_RAW_TS_MS,
          golden::REF_NOCOMMIT_RAW_ACCEL, golden::REF_NOCOMMIT_RAW_GYRO,
          golden::REF_NOCOMMIT_T, golden::REF_NOCOMMIT_ANG);
}

#[test]
fn the_yaw_fixture_separates_the_flex_tier_from_the_gravity_tier() {
    // Guards the fixture: if flex projection and across-gravity agreed on it,
    // the yaw test above could not tell a missing flex tier from a working one.
    let l = log(golden::REF_YAW_RAW_T, golden::REF_YAW_RAW_TS_MS,
                golden::REF_YAW_RAW_ACCEL, golden::REF_YAW_RAW_GYRO);
    let flex = replay(&l, &ReplayConfig::reference()).unwrap();
    let grav = replay(&l, &ReplayConfig { flex_axis_capture: false, ..ReplayConfig::reference() }).unwrap();
    assert!(flex.flex_axis.is_some());
    assert!(grav.flex_axis.is_none());
    let sep = flex
        .angle_deg
        .iter()
        .zip(&grav.angle_deg)
        .filter(|(a, b)| a.is_finite() && b.is_finite())
        .map(|(a, b)| (a - b).abs())
        .fold(0.0, f64::max);
    assert!(sep > 1.0, "tiers differ by only {sep}");
}

#[test]
fn the_footing_name_describes_the_reference_config() {
    let r = ReplayConfig::reference();
    assert_eq!(SCORING_FOOTING, "ref-100hz-ema0.5-flexrec");
    assert_eq!(r.flex_axis_fit, mobile_imu_core::replay::FlexAxisFit::Recording);
    assert_eq!(r.tick_s, ANALYSIS_TICK_S);
    assert_eq!(ANALYSIS_TICK_S, 0.01);
    assert_eq!(r.ema_alpha, 0.5);
    assert!(r.flex_axis_capture);
    assert_eq!(r.method, Method::Relative);
    assert!(!r.use_mag);
    let d = ReplayConfig::default();
    assert_eq!((d.tick_s, d.ema_alpha, d.flex_axis_capture), (r.tick_s, r.ema_alpha, r.flex_axis_capture));
    let old = ReplayConfig::app_pre_v0_4();
    assert_eq!((old.tick_s, old.ema_alpha, old.flex_axis_capture), (TICK_S, 0.3, false));
}

#[test]
fn the_ema_rescale_matches_the_reference() {
    // imu_calibration_tuner._rescale_ema_alpha(0.5, 0.01), printed at 17 digits.
    assert!((rescale_ema_alpha(0.5, ANALYSIS_TICK_S) - 0.129_449_436_703_875_88).abs() < 1e-15);
    assert_eq!(rescale_ema_alpha(0.3, TICK_S), 0.3);
    assert_eq!(rescale_ema_alpha(1.0, ANALYSIS_TICK_S), 1.0);
    assert_eq!(rescale_ema_alpha(1.7, ANALYSIS_TICK_S), 1.0);
    assert_eq!(rescale_ema_alpha(-0.2, ANALYSIS_TICK_S), 0.0);
    // Same time constant: five 10 ms steps retain what one 50 ms step does.
    let a = rescale_ema_alpha(0.5, ANALYSIS_TICK_S);
    assert!(((1.0 - a).powi(5) - 0.5).abs() < 1e-12);
}

/// Calm hold, then rotation about x clearing the capture threshold for
/// `x_steps` samples, then rotation about y at 2 rad/s for 40 samples.
fn two_axis_log(x_steps: usize) -> (Vec<RawSample>, usize) {
    const CALM: usize = 150;
    let mut out = Vec::new();
    let mut first_y = 0;
    for i in 0..(CALM + x_steps + 40 + 60) {
        let t = i as f64 / 100.0;
        let ts_ms = (t * 1000.0).round() as i64;
        out.push(RawSample { t, ts_ms, sensor: Sensor::Accel, v: [0.0, 0.0, 1.0] });
        let g = if i < CALM {
            [0.01, 0.0, 0.0]
        } else if i < CALM + 3 {
            // ramp through the guard so the detector sees a departure
            [ZERO_CAPTURE_GUARD_RAD_S + 0.3 * (i - CALM + 1) as f64, 0.0, 0.0]
        } else if i < CALM + x_steps {
            [2.0, 0.0, 0.0]
        } else if i < CALM + x_steps + 40 {
            if first_y == 0 {
                first_y = out.len();
            }
            [0.0, 2.0, 0.0]
        } else {
            [0.01, 0.0, 0.0]
        };
        out.push(RawSample { t, ts_ms, sensor: Sensor::Gyro, v: g });
    }
    (out, first_y)
}

#[test]
fn under_a_release_override_the_axis_is_learnt_from_the_override_onward() {
    // Rule (2026-11-16 footing plan, section 3.1; unchanged by the 0.5.0
    // whole-recording fit): the estimator is fed from the sample that
    // captures the zero pose, whichever path captured it. The reference has
    // no override, so this pins the Rust behaviour on its own.
    let (l, first_y) = two_axis_log(60);
    assert_eq!(l[first_y].sensor, Sensor::Gyro);

    let auto = replay(&l, &ReplayConfig::reference()).expect("auto release");
    let ax = auto.flex_axis.expect("auto axis");
    assert!(auto.release_idx < first_y);
    assert!(ax[0].abs() > 0.99, "auto-released swing commits to x: {ax:?}");

    let cfg = ReplayConfig { release_override: Some(first_y), ..ReplayConfig::reference() };
    let over = replay(&l, &cfg).expect("overridden release");
    let oy = over.flex_axis.expect("override axis");
    assert_eq!(over.release_idx, first_y);
    assert!(oy[1].abs() > 0.99, "override moves the feed start with the zero: {oy:?}");
}

// ---- the axis fit's report: flags only, pinned against fit_report() --------

/// Every REF_* log: the report `replay` returns is the one the Python
/// reference's `FlexAxisEstimator.fit_report` gives on the same log.
#[test]
fn the_axis_fit_report_matches_the_reference_on_every_fixture() {
    macro_rules! case {
        ($tag:literal, $t:ident, $ts:ident, $a:ident, $g:ident, $fit:ident, $flags:ident) => {{
            let l = log(golden::$t, golden::$ts, golden::$a, golden::$g);
            let r = replay(&l, &ReplayConfig::reference()).unwrap();
            let rep = r.axis_fit.unwrap_or_else(|| panic!("{}: no report", $tag));
            let (n_fit, closed, late_n, ratio, span) = golden::$fit;
            assert_eq!((rep.n_fit, rep.closed, rep.late_n), (n_fit, closed, late_n), "{}", $tag);
            assert!((rep.late_energy_ratio - ratio).abs() <= 1e-9 * ratio.abs().max(1.0),
                    "{}: ratio {} want {}", $tag, rep.late_energy_ratio, ratio);
            assert!((rep.max_span_s - span).abs() < 1e-9, "{}: span {} want {}", $tag, rep.max_span_s, span);
            assert_eq!(rep.flags(), golden::$flags.to_vec(), "{}", $tag);
            // The opening fit has no report.
            assert!(replay(&l, &opening()).unwrap().axis_fit.is_none(), "{}", $tag);
        }};
    }
    case!("REF_INPLANE", REF_INPLANE_RAW_T, REF_INPLANE_RAW_TS_MS, REF_INPLANE_RAW_ACCEL,
          REF_INPLANE_RAW_GYRO, REF_INPLANE_AXIS_FIT, REF_INPLANE_AXIS_FLAGS);
    case!("REF_YAW", REF_YAW_RAW_T, REF_YAW_RAW_TS_MS, REF_YAW_RAW_ACCEL,
          REF_YAW_RAW_GYRO, REF_YAW_AXIS_FIT, REF_YAW_AXIS_FLAGS);
    case!("REF_NOCOMMIT", REF_NOCOMMIT_RAW_T, REF_NOCOMMIT_RAW_TS_MS, REF_NOCOMMIT_RAW_ACCEL,
          REF_NOCOMMIT_RAW_GYRO, REF_NOCOMMIT_AXIS_FIT, REF_NOCOMMIT_AXIS_FLAGS);
    case!("REF_TRANSIENT", REF_TRANSIENT_RAW_T, REF_TRANSIENT_RAW_TS_MS, REF_TRANSIENT_RAW_ACCEL,
          REF_TRANSIENT_RAW_GYRO, REF_TRANSIENT_AXIS_FIT, REF_TRANSIENT_AXIS_FLAGS);
    case!("REF_RUNON", REF_RUNON_RAW_T, REF_RUNON_RAW_TS_MS, REF_RUNON_RAW_ACCEL,
          REF_RUNON_RAW_GYRO, REF_RUNON_AXIS_FIT, REF_RUNON_AXIS_FLAGS);
    case!("REF_CLIFF24", REF_CLIFF24_RAW_T, REF_CLIFF24_RAW_TS_MS, REF_CLIFF24_RAW_ACCEL,
          REF_CLIFF24_RAW_GYRO, REF_CLIFF24_AXIS_FIT, REF_CLIFF24_AXIS_FLAGS);
    case!("REF_CLIFF25", REF_CLIFF25_RAW_T, REF_CLIFF25_RAW_TS_MS, REF_CLIFF25_RAW_ACCEL,
          REF_CLIFF25_RAW_GYRO, REF_CLIFF25_AXIS_FIT, REF_CLIFF25_AXIS_FLAGS);
    case!("REF_CLIFF49", REF_CLIFF49_RAW_T, REF_CLIFF49_RAW_TS_MS, REF_CLIFF49_RAW_ACCEL,
          REF_CLIFF49_RAW_GYRO, REF_CLIFF49_AXIS_FIT, REF_CLIFF49_AXIS_FLAGS);
    case!("REF_WEAKBUMP", REF_WEAKBUMP_RAW_T, REF_WEAKBUMP_RAW_TS_MS, REF_WEAKBUMP_RAW_ACCEL,
          REF_WEAKBUMP_RAW_GYRO, REF_WEAKBUMP_AXIS_FIT, REF_WEAKBUMP_AXIS_FLAGS);
    case!("REF_WEAKLATE", REF_WEAKLATE_RAW_T, REF_WEAKLATE_RAW_TS_MS, REF_WEAKLATE_RAW_ACCEL,
          REF_WEAKLATE_RAW_GYRO, REF_WEAKLATE_AXIS_FIT, REF_WEAKLATE_AXIS_FLAGS);
    case!("REF_FLAG_BRIEF", REF_FLAG_BRIEF_RAW_T, REF_FLAG_BRIEF_RAW_TS_MS, REF_FLAG_BRIEF_RAW_ACCEL,
          REF_FLAG_BRIEF_RAW_GYRO, REF_FLAG_BRIEF_AXIS_FIT, REF_FLAG_BRIEF_AXIS_FLAGS);
    case!("REF_FLAG_EARLY", REF_FLAG_EARLY_RAW_T, REF_FLAG_EARLY_RAW_TS_MS, REF_FLAG_EARLY_RAW_ACCEL,
          REF_FLAG_EARLY_RAW_GYRO, REF_FLAG_EARLY_AXIS_FIT, REF_FLAG_EARLY_AXIS_FLAGS);
    case!("REF_FLAG_GAP", REF_FLAG_GAP_RAW_T, REF_FLAG_GAP_RAW_TS_MS, REF_FLAG_GAP_RAW_ACCEL,
          REF_FLAG_GAP_RAW_GYRO, REF_FLAG_GAP_AXIS_FIT, REF_FLAG_GAP_AXIS_FLAGS);
}

/// The three known regressions of the stop rule against the opening rule
/// (PR #130). The whole-recording axis is wrong on each, the opening axis is
/// right, the angle still matches the reference replay tick for tick, and the
/// report carries the flag that says the axis is not to be trusted.
#[test]
fn each_known_regression_of_the_stop_rule_is_flagged() {
    macro_rules! case {
        ($tag:literal, $t:ident, $ts:ident, $a:ident, $g:ident, $wt:ident, $w:ident, $flag:literal) => {{
            check($tag, golden::$t, golden::$ts, golden::$a, golden::$g, golden::$wt, golden::$w);
            let l = log(golden::$t, golden::$ts, golden::$a, golden::$g);
            let (rec, old) = (off_x(&l, &ReplayConfig::reference()), off_x(&l, &opening()));
            assert!(old < 6.0 && rec > 50.0, "{}: opening {old} deg, recording {rec} deg", $tag);
            let rep = replay(&l, &ReplayConfig::reference()).unwrap().axis_fit.unwrap();
            assert!(rep.flags().contains(&$flag), "{}: {:?}", $tag, rep);
        }};
    }
    // Brief motion at the zero, a hold over two seconds, then the swing.
    case!("REF_FLAG_BRIEF", REF_FLAG_BRIEF_RAW_T, REF_FLAG_BRIEF_RAW_TS_MS, REF_FLAG_BRIEF_RAW_ACCEL,
          REF_FLAG_BRIEF_RAW_GYRO, REF_FLAG_BRIEF_T, REF_FLAG_BRIEF_ANG, "axis_fit_weak_then_motion");
    // Other motion before the quiet second is up.
    case!("REF_FLAG_EARLY", REF_FLAG_EARLY_RAW_T, REF_FLAG_EARLY_RAW_TS_MS, REF_FLAG_EARLY_RAW_ACCEL,
          REF_FLAG_EARLY_RAW_GYRO, REF_FLAG_EARLY_T, REF_FLAG_EARLY_ANG, "axis_fit_spans_quiet");
    // A sample dropout of over two seconds inside the quiet.
    case!("REF_FLAG_GAP", REF_FLAG_GAP_RAW_T, REF_FLAG_GAP_RAW_TS_MS, REF_FLAG_GAP_RAW_ACCEL,
          REF_FLAG_GAP_RAW_GYRO, REF_FLAG_GAP_T, REF_FLAG_GAP_ANG, "axis_fit_spans_quiet");
}

/// Independent review of the flags, 2026-11-20. A gyro sample stamped 0 left
/// the previous-stamp at 0, and the next sample's difference from 0 (its
/// absolute timestamp) went into the span: a pause of 1.7e9 s on an ordinary
/// swing. An unknown interval counts as the nominal 10 ms, and the session
/// reports what `replay` does.
#[test]
fn a_sample_with_no_timestamp_does_not_count_as_a_gap() {
    use mobile_imu_core::session::TrialSession;
    let ts: Vec<i64> = golden::REF_INPLANE_RAW_TS_MS.iter().map(|t| t + 1_763_000_000_000).collect();
    let clean = log(golden::REF_INPLANE_RAW_T, &ts, golden::REF_INPLANE_RAW_ACCEL, golden::REF_INPLANE_RAW_GYRO);
    let want = replay(&clean, &ReplayConfig::reference()).unwrap().axis_fit.unwrap();
    assert!(want.flags().is_empty() && want.max_span_s < 0.5, "{want:?}");

    let mut holed = clean.clone();
    let mut k = 0;
    for s in holed.iter_mut() {
        // every third gyro sample of the swing loses its stamp
        if s.sensor == Sensor::Gyro && s.t > 2.0 && s.t < 4.0 {
            if k % 3 == 0 {
                s.ts_ms = 0;
            }
            k += 1;
        }
    }
    let got = replay(&holed, &ReplayConfig::reference()).unwrap().axis_fit.unwrap();
    assert!(got.flags().is_empty() && got.max_span_s < 0.5, "{got:?}");

    let mut sess = TrialSession::new(ReplayConfig::reference());
    for s in &clean {
        sess.push(*s);
    }
    assert_eq!(sess.axis_fit(), Some(want));
}
