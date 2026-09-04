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

// ---- post-release settling -------------------------------------------------
// A trial ends when the limb has been still for SETTLE_TARGET_S. The gate is
// gyro-magnitude only (ZERO_CAPTURE_GUARD_RAD_S), NOT is_stationary_window:
// that stricter gyro+accel check is documented as never firing for a
// meaningful fraction of genuinely fine strapped-sensor trials, and with no
// time cap those trials would record forever.

/// Drives a session through hold -> release, the same way the release test
/// does, so settling is exercised through the real state machine rather than
/// a test-only back door. Returns the time to continue from.
fn release(sess: &mut TrialSession) -> f64 {
    let t = feed(sess, 120, [0.005, 0.0, 0.0], 0.0);
    assert!(matches!(sess.state(), HoldState::Ready), "setup: {:?}", sess.state());
    let t = feed(sess, 3, [1.5, 0.0, 0.0], t);
    assert!(matches!(sess.state(), HoldState::Released), "setup: {:?}", sess.state());
    t
}

/// Seconds of samples, as a 60 Hz count.
fn secs(n: f64) -> usize {
    (n * FS).round() as usize
}

#[test]
fn five_seconds_of_stillness_settles_the_trial() {
    let mut s = TrialSession::new(ReplayConfig::default());
    let t = release(&mut s);
    feed(&mut s, secs(5.2), [0.01, 0.0, 0.0], t);
    assert!(matches!(s.state(), HoldState::Settled), "got {:?}", s.state());
}

#[test]
fn settling_does_not_fire_before_the_target() {
    let mut s = TrialSession::new(ReplayConfig::default());
    let t = release(&mut s);
    feed(&mut s, secs(4.5), [0.01, 0.0, 0.0], t);
    assert!(matches!(s.state(), HoldState::Released), "got {:?}", s.state());
    assert!(s.settle_s() > 3.0, "should be accumulating, got {}", s.settle_s());
}

// The same reset shape reset_hold() uses: movement sends the accumulator back
// to zero, it does not merely pause it.
#[test]
fn movement_resets_the_settle_accumulator() {
    let mut s = TrialSession::new(ReplayConfig::default());
    let t = release(&mut s);
    let t = feed(&mut s, secs(3.0), [0.01, 0.0, 0.0], t);
    assert!(s.settle_s() > 1.0, "expected accumulation, got {}", s.settle_s());
    feed(&mut s, secs(0.3), [1.5, 0.0, 0.0], t);
    assert_eq!(s.settle_s(), 0.0);
    assert!(matches!(s.state(), HoldState::Released), "got {:?}", s.state());
}

// The population this app exists for. A limb with sustained clonus must never
// self-terminate -- there is deliberately no time cap, so the operator ends it.
#[test]
fn a_limb_that_never_settles_never_terminates() {
    let mut s = TrialSession::new(ReplayConfig::default());
    let t = release(&mut s);
    feed(&mut s, secs(30.0), [1.2, 0.0, 0.0], t);
    assert!(matches!(s.state(), HoldState::Released), "got {:?}", s.state());
    assert_eq!(s.settle_s(), 0.0);
}

#[test]
fn settled_is_terminal() {
    let mut s = TrialSession::new(ReplayConfig::default());
    let t = release(&mut s);
    let t = feed(&mut s, secs(5.2), [0.01, 0.0, 0.0], t);
    assert!(matches!(s.state(), HoldState::Settled));
    feed(&mut s, secs(2.0), [1.5, 0.0, 0.0], t);
    assert!(matches!(s.state(), HoldState::Settled), "got {:?}", s.state());
}

// Samples must keep being logged while settling, or the tail median that
// neutral_deg is computed from would be missing exactly the settled part --
// which is the whole reason for requiring a settle.
#[test]
fn samples_are_still_logged_while_settling() {
    let mut s = TrialSession::new(ReplayConfig::default());
    let t = release(&mut s);
    let before = s.sample_count();
    feed(&mut s, secs(2.0), [0.01, 0.0, 0.0], t);
    assert!(s.sample_count() > before, "settling must not stop the log");
}

// Settled freezes the accumulator too, not just the state. Without the
// explicit terminal arm in push(), advance_settle keeps running after
// completion and settle_s would reset to 0 on the next movement -- a
// completed trial reporting zero settled seconds. The state alone cannot
// catch that, because advance_settle never clears Settled.
#[test]
fn settle_s_freezes_once_settled() {
    let mut s = TrialSession::new(ReplayConfig::default());
    let t = release(&mut s);
    let t = feed(&mut s, secs(5.2), [0.01, 0.0, 0.0], t);
    let at_completion = s.settle_s();
    assert!(at_completion >= 5.0, "expected a completed settle, got {at_completion}");
    feed(&mut s, secs(2.0), [1.5, 0.0, 0.0], t);
    assert_eq!(s.settle_s(), at_completion, "a completed trial must not un-settle");
}
