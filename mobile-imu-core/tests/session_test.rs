//! The live gates. The drift gate is new logic, not a port: the reference has
//! no equivalent, because the desktop path never had to coach a hold in real
//! time. It exists because ZERO_CAPTURE_GUARD_RAD_S bounds angular RATE, so a
//! slow steady creep stays "calm" while moving the pose tens of degrees — 8.7°
//! on a real 2.6s capture (spec §4.2).

use mobile_imu_core::replay::{RawSample, ReplayConfig, Sensor};
use mobile_imu_core::session::{HoldState, TrialSession, MAX_HOLD_DRIFT_DEG};

const FS: f64 = 60.0;

fn feed(sess: &mut TrialSession, n: usize, gyro: [f64; 3], t0: f64) -> f64 {
    let mut t = t0;
    for _ in 0..n {
        let ts_ms = (t * 1000.0).round() as i64;
        sess.push(RawSample { t, ts_ms, sensor: Sensor::Accel, v: [0.0, 0.0, 9.81] });
        sess.push(RawSample { t, ts_ms, sensor: Sensor::Gyro, v: gyro });
        t += 1.0 / FS;
    }
    t
}

#[test]
fn a_steady_hold_reaches_ready() {
    let mut s = TrialSession::new(ReplayConfig::default());
    // 0.005 rad/s for 2s = 0.01 rad = 0.57 deg of drift: well inside the gate.
    feed(&mut s, 120, [0.005, 0.0, 0.0], 0.0);
    assert!(matches!(s.state(), HoldState::Ready), "got {:?}", s.state());
}

#[test]
fn a_slow_creep_stays_calm_but_never_arms() {
    let mut s = TrialSession::new(ReplayConfig::default());
    // 0.15 rad/s is under ZERO_CAPTURE_GUARD_RAD_S (0.3) throughout, so the
    // rate gate is satisfied the whole way — but it reaches MAX_HOLD_DRIFT_DEG
    // at t=0.582s, BEFORE the 0.95s calm window completes, so the hold can
    // never arm. The rate has to be chosen this way on purpose: at 0.05 rad/s
    // the window completes first (only 2.72 deg accumulated) and the hold arms
    // before being revoked later, which tests something weaker.
    feed(&mut s, 120, [0.15, 0.0, 0.0], 0.0);
    assert!(
        !matches!(s.state(), HoldState::Ready),
        "drift gate did not fire; got {:?}",
        s.state()
    );
}

#[test]
fn a_revoked_hold_can_be_earned_again_without_restarting_the_trial() {
    let mut s = TrialSession::new(ReplayConfig::default());
    // Arms, then drifts past the gate and is revoked...
    let t = feed(&mut s, 120, [0.05, 0.0, 0.0], 0.0);
    assert!(!matches!(s.state(), HoldState::Ready));
    // ...and a fresh steady hold re-arms it.
    feed(&mut s, 120, [0.001, 0.0, 0.0], t);
    assert!(matches!(s.state(), HoldState::Ready), "got {:?}", s.state());
}

#[test]
fn a_qualifying_burst_after_a_good_hold_reports_released() {
    let mut s = TrialSession::new(ReplayConfig::default());
    let t = feed(&mut s, 120, [0.005, 0.0, 0.0], 0.0);
    assert!(matches!(s.state(), HoldState::Ready));
    // >= FLEX_CAPTURE_THRESHOLD (1.0 rad/s) after a qualified calm window.
    feed(&mut s, 3, [1.5, 0.0, 0.0], t);
    assert!(matches!(s.state(), HoldState::Released), "got {:?}", s.state());
}

#[test]
fn drift_is_reported_while_holding_so_the_ui_can_show_it() {
    let mut s = TrialSession::new(ReplayConfig::default());
    feed(&mut s, 30, [0.05, 0.0, 0.0], 0.0);
    match s.state() {
        HoldState::Holding { drift_deg, .. } => {
            assert!(drift_deg > 0.5, "expected accumulating drift, got {drift_deg}");
            assert!(drift_deg < MAX_HOLD_DRIFT_DEG);
        }
        other => panic!("expected Holding, got {other:?}"),
    }
}

#[test]
fn motion_above_the_guard_resets_to_moving() {
    let mut s = TrialSession::new(ReplayConfig::default());
    let t = feed(&mut s, 120, [0.005, 0.0, 0.0], 0.0);
    assert!(matches!(s.state(), HoldState::Ready));
    feed(&mut s, 2, [0.9, 0.0, 0.0], t);
    assert!(matches!(s.state(), HoldState::Moving), "got {:?}", s.state());
}
