# Web App Core + Capture Loop — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A Safari page that captures a pendulum trial entirely on the phone and displays the 7 Popović parameters, with no laptop involved.

**Architecture:** `mobile-imu-core` gains two pure-Rust modules — a batch orchestrator (`replay.rs`, lifted out of an existing test harness) and a streaming session with the live hold gates (`session.rs`) — then a thin `#[wasm_bindgen]` veneer carrying no logic. A `webapp/` PWA captures `devicemotion` on the main thread (forced: window-only API), batches at 50 ms, and transfers buffers to a worker that owns the WASM instance.

**Tech Stack:** Rust 2021 (zero runtime dependencies on native builds), `wasm-bindgen` gated to `wasm32`, vanilla ES modules (no framework), Node 24 for tests, GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-08-24-web-app-design.md`

## Prerequisite

**Branch `mobile-imu-core-u2-scoring` must land on `main` first.** Task 1 lifts the orchestrator out of `mobile-imu-core/tests/pipeline_test.rs`, which exists only on that branch. Verify before starting:

```bash
git merge-base --is-ancestor mobile-imu-core-u2-scoring main && echo OK || echo "MERGE U2 FIRST"
```

## Global Constraints

Every task's requirements implicitly include these. Values are copied verbatim from the spec.

- **Filter chain is fixed:** Madgwick fusion, Savitzky-Golay smoothing. No Mahony, Kalman, or Butterworth may be introduced. (Spec §2.3)
- **Rust ≡ Python to < 1e-13 degrees**, pinned by `mobile-imu-core/tests/pipeline_test.rs`. Any change that breaks this equivalence is a defect, not a tradeoff. (Spec §2.3)
- **Native builds stay dependency-free.** `[dependencies]` must remain empty; `wasm-bindgen` goes under `[target.'cfg(target_arch = "wasm32")'.dependencies]` so `cargo test` pulls nothing. (Spec §7)
- **Sensor capture runs on the main thread.** `DeviceMotionEvent` is window-only and absent from workers; `requestPermission()` requires a user gesture on iOS. (Spec §2.1)
- **Transfer, don't share.** 50 ms batches via `postMessage(buf, [buf])`. No `SharedArrayBuffer`, no COOP/COEP. (Spec §2.1)
- **`release_quat` is captured at release**, inside the gyro branch, *before* that sample is integrated. Never at end-of-hold. (Spec §4.3)
- **Composite score and severity zone are never persisted** — derived at read time. (Spec §3.5)
- **Algorithm constants** (do not re-derive): `BETA` 0.041, `ema_alpha` 0.3, `TICK_S` 0.05, `GYRO_BIAS_WINDOW_S` 1.0 (× 0.95 span), `ZERO_CAPTURE_GUARD_RAD_S` 0.3, `FLEX_CAPTURE_THRESHOLD` 1.0, `MAX_HOLD_DRIFT_DEG` 5.0 (**uncalibrated** — spec §4.2).
- **Capability floor:** ≥ 50 Hz sustained, zero `dt == 0`, zero `dt` outside `(0, 500) ms`. (Spec §1.4)
- **No clinical claims.** The app shows a persistent "research capture only — not validated" banner until Gate G0 passes. (Spec §8)

## File Structure

```
mobile-imu-core/
  Cargo.toml                 modify — crate-type, wasm32-gated wasm-bindgen
  src/replay.rs              create — batch orchestrator over a full raw log
  src/session.rs             create — streaming session + hold/drift/release gates
  src/wasm.rs                create — #[wasm_bindgen] veneer, zero logic
  src/lib.rs                 modify — module wiring
  tests/replay_test.rs       create
  tests/session_test.rs      create
  tests/pipeline_test.rs     modify — call replay::replay(), drop local harness

webapp/
  package.json               create
  index.html                 create
  src/worker.js              create — owns the WASM instance
  src/capture.js             create — main-thread listener, batching, transfer
  src/app.js                 create — UI state machine
  tests/worker.test.js       create
  tests/fixtures/            create — recorded captures for the replay seam

.github/workflows/ci.yml     create
```

`replay.rs` and `session.rs` are split deliberately: `replay.rs` scores a finished log (what export/re-scoring needs, spec §3.5), `session.rs` drives a live capture (what the UI needs, spec §5). Both call the same ported stages; neither owns algorithm logic.

---

### Task 1: Extract the replay orchestrator into `src/replay.rs`

`pipeline_test.rs` currently carries the raw-log walk as a test-local harness, with a comment saying it belongs in `src` once U3 lands. WASM cannot call a test harness. The existing 5 pipeline tests are the safety net for this move.

**Files:**
- Create: `mobile-imu-core/src/replay.rs`
- Create: `mobile-imu-core/tests/replay_test.rs`
- Modify: `mobile-imu-core/src/lib.rs`
- Modify: `mobile-imu-core/tests/pipeline_test.rs`

**Interfaces:**
- Consumes: `ahrs::{MadgwickAhrs, gravity_seed, qconj, qmul, Quat, Vec3}`, `calibration::{ReleaseDetector, calibrate_gyro_bias, calibrate_accel_bias}`, `stillness::{is_stationary_window, SampleBuf, GYRO_BIAS_WINDOW_S}`, `resample::{tick_resample, ema_smooth, TICK_S}`, `goniometry::{ockendon_deg, OCKENDON_FT_RATIO}`
- Produces: `replay::{Sensor, RawSample, ReplayConfig, Method, TrialError, TrialResult, replay}`

- [ ] **Step 1: Write the failing test**

Create `mobile-imu-core/tests/replay_test.rs`:

```rust
//! Orchestrator tests. Behaviour equivalence with the pre-extraction harness
//! is covered by pipeline_test.rs, which scores a real log; these cover the
//! error paths that log cannot reach.

use mobile_imu_core::replay::{replay, Method, RawSample, ReplayConfig, Sensor, TrialError};

/// A log that never leaves the calm band: the release detector can never fire.
fn calm_log(n: usize) -> Vec<RawSample> {
    let mut out = Vec::new();
    for i in 0..n {
        let t = i as f64 / 60.0;
        let ts_ms = (t * 1000.0).round() as i64;
        out.push(RawSample { t, ts_ms, sensor: Sensor::Accel, v: [0.0, 0.0, 9.81] });
        out.push(RawSample { t, ts_ms, sensor: Sensor::Gyro, v: [0.01, 0.0, 0.0] });
    }
    out
}

#[test]
fn a_log_with_no_release_reports_release_never_detected() {
    let cfg = ReplayConfig::default();
    let err = replay(&calm_log(600), &cfg).unwrap_err();
    assert_eq!(err, TrialError::ReleaseNeverDetected);
}

#[test]
fn a_log_too_short_to_score_is_rejected_before_release_detection() {
    let cfg = ReplayConfig::default();
    let err = replay(&calm_log(3), &cfg).unwrap_err();
    assert_eq!(err, TrialError::InsufficientSamples);
}

#[test]
fn the_method_selects_between_relative_and_ockendon() {
    // Both must be reachable through the public config; pipeline_test pins
    // their numeric output against Python.
    let cfg = ReplayConfig { method: Method::Ockendon, ..ReplayConfig::default() };
    assert_eq!(cfg.method, Method::Ockendon);
    assert_eq!(ReplayConfig::default().method, Method::Relative);
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd mobile-imu-core && cargo test --test replay_test`
Expected: FAIL — `unresolved import mobile_imu_core::replay`

- [ ] **Step 3: Write the implementation**

Create `mobile-imu-core/src/replay.rs`. This is a move, not a redesign: the body is the harness currently in `pipeline_test.rs::replay`, with the fixture-reading parts replaced by a `&[RawSample]` argument and the `Option` return replaced by `Result`.

```rust
//! Batch orchestration: walk a complete raw log, produce a scored trajectory.
//!
//! This is the sequencing the Python reference performs in
//! `imu_calibration_tuner.replay_trial` — bias calibration, release capture,
//! per-sample fusion, tick resample, EMA. It owns no algorithm: every stage is
//! a call into `ahrs`, `calibration`, `stillness`, `resample`, `goniometry`.
//!
//! Ordering is a contract, not a style choice. Accel must be processed before
//! gyro at the same timestamp (the gyro branch reads the stored accel), and
//! `release_quat` is snapshotted BEFORE the firing sample is integrated — the
//! two instants differ by the hold's accumulated drift, measured at 8.7° on a
//! real capture (spec §4.2).

use crate::ahrs::{gravity_seed, qconj, qmul, MadgwickAhrs, Quat, Vec3};
use crate::calibration::{calibrate_accel_bias, calibrate_gyro_bias, ReleaseDetector};
use crate::goniometry::{ockendon_deg, OCKENDON_FT_RATIO};
use crate::resample::{ema_smooth, tick_resample, TICK_S};
use crate::stillness::{is_stationary_window, SampleBuf, GYRO_BIAS_WINDOW_S};

#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum Sensor {
    Accel,
    Mag,
    Gyro,
}

#[derive(Clone, Copy, Debug)]
pub struct RawSample {
    /// Seconds. Drives the tick grid.
    pub t: f64,
    /// Milliseconds. Drives `dt`; absent or constant timing silently
    /// fabricates `dt = 0.01` in the reference (spec §3.4).
    pub ts_ms: i64,
    pub sensor: Sensor,
    pub v: Vec3,
}

#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum Method {
    /// `180 - swing`; the persisted live default.
    Relative,
    /// Ockendon & Gilbert applied to the swing as tibial inclination.
    Ockendon,
}

#[derive(Clone, Copy, Debug)]
pub struct ReplayConfig {
    pub beta: f64,
    pub ema_alpha: f64,
    pub method: Method,
    /// KTD10: the live path passes no magnetometer even when one is present.
    pub use_mag: bool,
    /// KTD9 `set_release_override`: index into the sample slice.
    pub release_override: Option<usize>,
}

impl Default for ReplayConfig {
    fn default() -> Self {
        Self {
            beta: crate::ahrs::BETA,
            ema_alpha: 0.3,
            method: Method::Relative,
            use_mag: false,
            release_override: None,
        }
    }
}

#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum TrialError {
    /// Fewer samples than any stage can work with.
    InsufficientSamples,
    /// No qualifying release: recoverable retroactively via
    /// `ReplayConfig::release_override` (KTD9), never by re-recording.
    ReleaseNeverDetected,
}

#[derive(Clone, Debug)]
pub struct TrialResult {
    /// Tick times, relative to the first sample.
    pub t: Vec<f64>,
    /// EMA-smoothed angle on the tick grid. Index 0 is NaN by contract.
    pub angle_deg: Vec<f64>,
    /// Orientation at the release instant — the trial's zero pose.
    pub release_quat: Quat,
    /// Index into the input slice where release fired.
    pub release_idx: usize,
}

fn sub3(a: Vec3, b: Vec3) -> Vec3 {
    [a[0] - b[0], a[1] - b[1], a[2] - b[2]]
}

/// Rotation magnitude from the zero pose, in degrees. Quaternion delta rather
/// than differenced Euler angles: pitch extraction is unreliable near ±90°,
/// exactly where a pendulum swing's tibia passes.
fn swing_deg(q_zero: Quat, q_cur: Quat) -> f64 {
    let d = qmul(qconj(q_zero), q_cur);
    2.0 * d[0].abs().clamp(-1.0, 1.0).acos().to_degrees()
}

pub fn replay(samples: &[RawSample], cfg: &ReplayConfig) -> Result<TrialResult, TrialError> {
    if samples.len() < 40 {
        return Err(TrialError::InsufficientSamples);
    }

    let mut ahrs = MadgwickAhrs::new(cfg.beta);
    let mut detector = ReleaseDetector::new();
    let mut gyro_hold: SampleBuf = Vec::new();
    let mut accel_hold: SampleBuf = Vec::new();
    let mut gyro_bias: Vec3 = [0.0; 3];
    let mut accel_bias: Vec3 = [0.0; 3];
    let mut accel: Option<Vec3> = None;
    let mut mag: Option<Vec3> = None;
    let mut last_ts: Option<i64> = None;
    let mut seeded = false;
    let mut calib_was_stable = false;
    let mut zero: Option<(Quat, usize)> = None;

    let mut sample_t: Vec<f64> = Vec::with_capacity(samples.len());
    let mut sample_q: Vec<Quat> = Vec::with_capacity(samples.len());

    for (i, s) in samples.iter().enumerate() {
        match s.sensor {
            Sensor::Accel => {
                accel_hold.push((s.t, s.v));
                let cutoff = s.t - GYRO_BIAS_WINDOW_S;
                accel_hold.retain(|(t, _)| *t >= cutoff);
                if !seeded {
                    ahrs.q = gravity_seed(s.v);
                    seeded = true;
                }
                accel = Some(sub3(s.v, accel_bias));
            }
            Sensor::Mag => mag = Some(s.v),
            Sensor::Gyro => {
                let dt = match last_ts {
                    Some(prev) if s.ts_ms != 0 => {
                        let d = (s.ts_ms - prev) as f64 / 1000.0;
                        if d > 0.0 && d < 0.5 {
                            d
                        } else {
                            0.01
                        }
                    }
                    _ => 0.01,
                };
                last_ts = Some(s.ts_ms);

                let forced = cfg.release_override == Some(i);
                if (detector.on_gyro_sample(s.v, &gyro_hold, s.t) || forced) && zero.is_none() {
                    zero = Some((ahrs.q, i));
                }

                if zero.is_none() {
                    let stable = is_stationary_window(&gyro_hold, &accel_hold, s.t);
                    if stable && !calib_was_stable {
                        if let Some(b) = calibrate_gyro_bias(&gyro_hold) {
                            gyro_bias = b;
                        }
                        if let Some(b) = calibrate_accel_bias(&accel_hold) {
                            accel_bias = b;
                        }
                        calib_was_stable = true;
                    } else {
                        calib_was_stable = stable;
                    }
                }

                gyro_hold.push((s.t, s.v));
                let cutoff = s.t - GYRO_BIAS_WINDOW_S;
                gyro_hold.retain(|(t, _)| *t >= cutoff);

                if let Some(a) = accel {
                    let m = if cfg.use_mag { mag } else { None };
                    ahrs.update(sub3(s.v, gyro_bias), a, m, dt);
                }
            }
        }
        sample_t.push(s.t);
        sample_q.push(ahrs.q);
    }

    let (release_quat, release_idx) = zero.ok_or(TrialError::ReleaseNeverDetected)?;

    let angle_raw: Vec<f64> = sample_q
        .iter()
        .map(|q| {
            let sw = swing_deg(release_quat, *q);
            match cfg.method {
                Method::Relative => 180.0 - sw,
                Method::Ockendon => ockendon_deg(sw, OCKENDON_FT_RATIO),
            }
        })
        .collect();

    let (t, held) = tick_resample(&sample_t, &angle_raw, TICK_S);
    Ok(TrialResult {
        t,
        angle_deg: ema_smooth(&held, cfg.ema_alpha),
        release_quat,
        release_idx,
    })
}
```

Add to `mobile-imu-core/src/lib.rs`, keeping modules alphabetical:

```rust
pub mod goniometry;
pub mod replay;
pub mod resample;
```

- [ ] **Step 4: Run the new test to verify it passes**

Run: `cd mobile-imu-core && cargo test --test replay_test`
Expected: PASS, 3 tests

- [ ] **Step 5: Rewire `pipeline_test.rs` onto the extracted module**

Delete the local `Sensor`, `RawSample`, `Method`, `swing_deg`, `sub3`, and `replay` definitions from `tests/pipeline_test.rs`. Replace the imports and the harness call:

```rust
use mobile_imu_core::replay::{replay, Method, RawSample, ReplayConfig, Sensor, TrialResult};

/// Rebuild the chronological sample stream `gen_fixtures.py` handed to
/// `replay_trial`: per step, accel first, then a mag sample every
/// `E2E_MAG_STRIDE`-th step, then gyro.
fn raw_log() -> Vec<RawSample> {
    let n = golden::E2E_RAW_T.len();
    let mut out = Vec::new();
    let mut mag_i = 0usize;
    for i in 0..n {
        let (t, ts_ms) = (golden::E2E_RAW_T[i], golden::E2E_RAW_TS_MS[i]);
        out.push(RawSample { t, ts_ms, sensor: Sensor::Accel, v: xyz(golden::E2E_RAW_ACCEL, i) });
        if i % golden::E2E_MAG_STRIDE == 0 {
            out.push(RawSample { t, ts_ms, sensor: Sensor::Mag, v: xyz(golden::E2E_RAW_MAG, mag_i) });
            mag_i += 1;
        }
        out.push(RawSample { t, ts_ms, sensor: Sensor::Gyro, v: xyz(golden::E2E_RAW_GYRO, i) });
    }
    out
}

fn run(method: Method, use_mag: bool) -> TrialResult {
    let cfg = ReplayConfig {
        beta: golden::E2E_BETA,
        ema_alpha: golden::E2E_EMA_ALPHA,
        method,
        use_mag,
        release_override: None,
    };
    replay(&raw_log(), &cfg).expect("release must be detected")
}
```

Then update each test body to use `run(...)`, e.g.:

```rust
#[test]
fn full_pipeline_reproduces_the_python_replay() {
    let r = run(Method::Relative, false);
    assert_series_close(&r.t, golden::TRIAL_E2E_T, 1e-12, "tick times");
    assert_series_close(&r.angle_deg, golden::TRIAL_E2E_ANG, ANGLE_TOL_DEG, "replayed angle");
}
```

- [ ] **Step 6: Run the whole suite — the equivalence must be untouched**

Run: `cd mobile-imu-core && cargo test`
Expected: PASS. 48 tests (45 existing + 3 new). `full_pipeline_reproduces_the_python_replay` passing is the proof the move changed no behaviour; if it fails, the extraction is wrong, not the fixture.

- [ ] **Step 7: Commit**

```bash
git add mobile-imu-core/src/replay.rs mobile-imu-core/src/lib.rs \
        mobile-imu-core/tests/replay_test.rs mobile-imu-core/tests/pipeline_test.rs
git commit -m "refactor: lift the raw-log orchestrator out of the test into src/replay.rs"
```

---

### Task 2: Streaming session with the hold-drift gate

`replay.rs` scores a finished log. The UI needs live state during the hold — and the spec's new fourth quality gate, which no existing code implements.

**Files:**
- Create: `mobile-imu-core/src/session.rs`
- Create: `mobile-imu-core/tests/session_test.rs`
- Modify: `mobile-imu-core/src/lib.rs`

**Interfaces:**
- Consumes: `replay::{RawSample, ReplayConfig, Sensor, TrialError, TrialResult, replay}`, `stillness::{ZERO_CAPTURE_GUARD_RAD_S, GYRO_BIAS_WINDOW_S}`, `scoring::{PtParams, compute_pt_params}`
- Produces: `session::{HoldState, TrialSession, MAX_HOLD_DRIFT_DEG}`

- [ ] **Step 1: Write the failing test**

Create `mobile-imu-core/tests/session_test.rs`:

```rust
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd mobile-imu-core && cargo test --test session_test`
Expected: FAIL — `unresolved import mobile_imu_core::session`

- [ ] **Step 3: Write the implementation**

Create `mobile-imu-core/src/session.rs`:

```rust
//! Live capture session: accumulates a raw log while reporting the hold state
//! the UI needs, then scores through `replay` when the trial ends.

use crate::calibration::ReleaseDetector;
use crate::replay::{replay, RawSample, ReplayConfig, Sensor, TrialError, TrialResult};
use crate::scoring::{compute_pt_params, PtParams};
use crate::stillness::{SampleBuf, GYRO_BIAS_WINDOW_S, ZERO_CAPTURE_GUARD_RAD_S};

/// Maximum accumulated pose rotation permitted across the pre-release hold.
///
/// **Not calibrated.** 5° is a starting value that would have caught the 8.7°
/// reference capture; one trial is not a calibration. This carries the same
/// status KTD11 gives its attachment-stability and swing-range thresholds:
/// derived from shadow-study data, not chosen here (spec §4.2).
pub const MAX_HOLD_DRIFT_DEG: f64 = 5.0;

#[derive(Clone, Copy, PartialEq, Debug)]
pub enum HoldState {
    /// Rate gate unsatisfied, or drift exceeded and the hold was revoked.
    Moving,
    /// Calm and accumulating. Both figures are for display: a clinician needs
    /// to know *which* gate is unsatisfied, because the corrective action for
    /// motion and for drift are different.
    Holding { calm_s: f64, drift_deg: f64 },
    /// Both gates satisfied; a release now will be trusted.
    Ready,
    /// Release fired.
    Released,
}

pub struct TrialSession {
    cfg: ReplayConfig,
    samples: Vec<RawSample>,
    state: HoldState,
    calm_since: Option<f64>,
    drift: [f64; 3],
    last_gyro_t: Option<f64>,
    /// Live release detection, so the UI can show RELEASED during the swing.
    /// `replay` runs its own detector when scoring; both call the same ported
    /// type, so this duplicates usage, never logic.
    detector: ReleaseDetector,
    gyro_hold: SampleBuf,
}

fn norm3(v: [f64; 3]) -> f64 {
    (v[0] * v[0] + v[1] * v[1] + v[2] * v[2]).sqrt()
}

impl TrialSession {
    pub fn new(cfg: ReplayConfig) -> Self {
        Self {
            cfg,
            samples: Vec::new(),
            state: HoldState::Moving,
            calm_since: None,
            drift: [0.0; 3],
            last_gyro_t: None,
            detector: ReleaseDetector::new(),
            gyro_hold: Vec::new(),
        }
    }

    pub fn state(&self) -> HoldState {
        self.state
    }

    pub fn sample_count(&self) -> usize {
        self.samples.len()
    }

    pub fn push(&mut self, s: RawSample) {
        if s.sensor == Sensor::Gyro && self.state != HoldState::Released {
            self.advance_hold(s.v, s.t);
        }
        self.samples.push(s);
    }

    fn advance_hold(&mut self, omega: [f64; 3], t: f64) {
        let dt = match self.last_gyro_t {
            Some(prev) if t > prev && t - prev < 0.5 => t - prev,
            _ => 0.0,
        };
        self.last_gyro_t = Some(t);

        // Release detection reads the buffer as of just BEFORE this sample, so
        // a genuine release's own ramp-up cannot poison the calm check gating
        // it. Same ordering contract `replay` observes.
        let fired = self.detector.on_gyro_sample(omega, &self.gyro_hold, t);
        self.gyro_hold.push((t, omega));
        let cutoff = t - GYRO_BIAS_WINDOW_S;
        self.gyro_hold.retain(|(tt, _)| *tt >= cutoff);
        if fired {
            self.state = HoldState::Released;
            return;
        }

        if norm3(omega) >= ZERO_CAPTURE_GUARD_RAD_S {
            self.reset_hold();
            return;
        }

        let start = *self.calm_since.get_or_insert(t);
        // Net vector rotation, not path length: what offsets the zero pose is
        // where the sensor ENDED UP, and opposing wobble genuinely cancels.
        for k in 0..3 {
            self.drift[k] += omega[k] * dt;
        }
        let drift_deg = norm3(self.drift).to_degrees();

        if drift_deg > MAX_HOLD_DRIFT_DEG {
            self.reset_hold();
            return;
        }

        let calm_s = t - start;
        self.state = if calm_s >= 0.95 * GYRO_BIAS_WINDOW_S {
            HoldState::Ready
        } else {
            HoldState::Holding { calm_s, drift_deg }
        };
    }

    fn reset_hold(&mut self) {
        self.state = HoldState::Moving;
        self.calm_since = None;
        self.drift = [0.0; 3];
    }

    /// Score the accumulated log. Consumes nothing — a caller may re-finish
    /// with a different `release_override` to honour a clinician's scrub.
    pub fn finish(&self, release_override: Option<usize>) -> Result<(TrialResult, PtParams), TrialError> {
        let cfg = ReplayConfig { release_override, ..self.cfg };
        let r = replay(&self.samples, &cfg)?;
        let p = compute_pt_params(&r.t, &r.angle_deg, None, false)
            .ok_or(TrialError::InsufficientSamples)?;
        Ok((r, p))
    }
}
```

Add to `mobile-imu-core/src/lib.rs`:

```rust
pub mod scoring;
pub mod session;
pub mod signal;
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd mobile-imu-core && cargo test --test session_test`
Expected: PASS, 4 tests

- [ ] **Step 5: Run the whole suite**

Run: `cd mobile-imu-core && cargo test`
Expected: PASS, 52 tests. The pipeline equivalence test must still pass.

- [ ] **Step 6: Commit**

```bash
git add mobile-imu-core/src/session.rs mobile-imu-core/src/lib.rs \
        mobile-imu-core/tests/session_test.rs
git commit -m "feat: add live capture session with the cumulative hold-drift gate"
```

---

### Task 3: WASM veneer and build

**Files:**
- Modify: `mobile-imu-core/Cargo.toml`
- Create: `mobile-imu-core/src/wasm.rs`
- Modify: `mobile-imu-core/src/lib.rs`

**Interfaces:**
- Consumes: `session::{TrialSession, HoldState}`, `replay::{RawSample, ReplayConfig, Sensor}`
- Produces: JS class `WasmSession` with `push_batch(Float64Array)`, `state_code() -> number`, `calm_s() -> number`, `drift_deg() -> number`, `sample_count() -> number`, `finish() -> string | undefined`

- [ ] **Step 1: Add the wasm32-gated dependency**

Modify `mobile-imu-core/Cargo.toml`:

```toml
[lib]
# rlib for `cargo test`; cdylib for the wasm32 build. Adding cdylib does not
# affect native builds.
crate-type = ["cdylib", "rlib"]

[dependencies]

# Gated to wasm32 so native `cargo test` stays dependency-free, which is what
# keeps the crate trivially portable (spec §7).
[target.'cfg(target_arch = "wasm32")'.dependencies]
wasm-bindgen = "0.2"
```

- [ ] **Step 2: Write the veneer**

Create `mobile-imu-core/src/wasm.rs`. It carries **no logic** — every method delegates. Logic here would be untested, because it cannot be reached by `cargo test`.

```rust
//! `#[wasm_bindgen]` veneer. Deliberately logic-free: anything implemented
//! here is invisible to `cargo test` and therefore unverified. All behaviour
//! lives in `session`/`replay`, which are pure Rust and covered.

use wasm_bindgen::prelude::*;

use crate::replay::{RawSample, ReplayConfig, Sensor};
use crate::session::{HoldState, TrialSession};

#[wasm_bindgen]
pub struct WasmSession {
    inner: TrialSession,
}

#[wasm_bindgen]
impl WasmSession {
    #[wasm_bindgen(constructor)]
    pub fn new(beta: f64, ema_alpha: f64) -> WasmSession {
        let cfg = ReplayConfig { beta, ema_alpha, ..ReplayConfig::default() };
        WasmSession { inner: TrialSession::new(cfg) }
    }

    /// Flat batch, 7 doubles per sample: `[t_ms, ax, ay, az, gx, gy, gz]`.
    /// Accel is pushed before gyro for each sample, matching the ordering
    /// contract the whole pipeline depends on. Gyro must already be rad/s.
    pub fn push_batch(&mut self, buf: &[f64]) {
        for c in buf.chunks_exact(7) {
            let t = c[0] / 1000.0;
            let ts_ms = c[0].round() as i64;
            self.inner.push(RawSample { t, ts_ms, sensor: Sensor::Accel, v: [c[1], c[2], c[3]] });
            self.inner.push(RawSample { t, ts_ms, sensor: Sensor::Gyro, v: [c[4], c[5], c[6]] });
        }
    }

    /// 0 Moving, 1 Holding, 2 Ready, 3 Released.
    pub fn state_code(&self) -> u8 {
        match self.inner.state() {
            HoldState::Moving => 0,
            HoldState::Holding { .. } => 1,
            HoldState::Ready => 2,
            HoldState::Released => 3,
        }
    }

    pub fn calm_s(&self) -> f64 {
        match self.inner.state() {
            HoldState::Holding { calm_s, .. } => calm_s,
            HoldState::Ready => 0.95,
            _ => 0.0,
        }
    }

    pub fn drift_deg(&self) -> f64 {
        match self.inner.state() {
            HoldState::Holding { drift_deg, .. } => drift_deg,
            _ => 0.0,
        }
    }

    pub fn sample_count(&self) -> usize {
        self.inner.sample_count()
    }

    /// JSON of the 7-parameter result, or `undefined` when unscorable.
    /// Returning `undefined` rather than throwing keeps `TrialError` a value
    /// the UI branches on, per KTD7 (never a panic across the boundary).
    pub fn finish(&self) -> Option<String> {
        let (_, p) = self.inner.finish(None).ok()?;
        Some(format!(
            concat!(
                "{{\"r2n\":{},\"n\":{},\"phi_max_ratio\":{},\"omega_max_n\":{},",
                "\"omega_min_n\":{},\"f\":{},\"area_ratio\":{},\"omega_peak_deg_s\":{},",
                "\"a0_deg\":{},\"a1_deg\":{},\"first_trough_depth\":{},\"neutral_deg\":{},",
                "\"neutral_deg_raw\":{},\"pre_release_deg\":{},\"p_plus\":{},",
                "\"p_minus\":{},\"p_total\":{},\"quality_warn\":{},\"phi_negated\":{}}}"
            ),
            p.r2n, p.n, p.phi_max_ratio, p.omega_max_n, p.omega_min_n, p.f,
            p.area_ratio, p.omega_peak_deg_s, p.a0_deg, p.a1_deg,
            p.first_trough_depth, p.neutral_deg, p.neutral_deg_raw,
            p.pre_release_deg, p.p_plus, p.p_minus, p.p_total,
            p.quality_warn, p.phi_negated
        ))
    }
}
```

Add to `mobile-imu-core/src/lib.rs`:

```rust
pub mod stillness;

#[cfg(target_arch = "wasm32")]
pub mod wasm;
```

- [ ] **Step 3: Verify native builds are unaffected**

Run: `cd mobile-imu-core && cargo test`
Expected: PASS, 52 tests. `wasm.rs` is not compiled for the host target, so nothing changes.

- [ ] **Step 4: Install the wasm toolchain and build**

```bash
rustup target add wasm32-unknown-unknown
cargo install wasm-bindgen-cli
cargo build --release --target wasm32-unknown-unknown
wasm-bindgen target/wasm32-unknown-unknown/release/mobile_imu_core.wasm \
  --out-dir ../webapp/src/wasm --target web
```

Expected: `webapp/src/wasm/mobile_imu_core_bg.wasm` and `mobile_imu_core.js` exist.

> If `cargo install` stalls, note that this machine has a known pattern of package downloaders hanging without timeout. Prefer a prebuilt `wasm-bindgen-cli` release archive fetched with `curl --retry`.

- [ ] **Step 5: Commit**

```bash
git add mobile-imu-core/Cargo.toml mobile-imu-core/src/wasm.rs mobile-imu-core/src/lib.rs
git commit -m "feat: add wasm-bindgen veneer over the capture session"
```

---

### Task 4: Worker with the replay seam

The worker owns the WASM instance. It accepts batches from *either* the live listener or a recorded fixture — that seam is what makes device testing possible at all, since no browser automation can synthesise `DeviceMotionEvent` in Safari (spec §6, L5).

**Files:**
- Create: `webapp/package.json`
- Create: `webapp/src/worker.js`
- Create: `webapp/tests/worker.test.js`
- Create: `webapp/tests/fixtures/swing.json`

**Interfaces:**
- Consumes: `WasmSession` from Task 3
- Produces: worker message protocol — in `{type:'start'|'batch'|'finish', buf?}`, out `{type:'state', code, calm_s, drift_deg}` and `{type:'result', params}` / `{type:'error', reason}`

- [ ] **Step 1: Create the package manifest**

`webapp/package.json`:

```json
{
  "name": "pendulastic-webapp",
  "private": true,
  "type": "module",
  "scripts": {
    "test": "node --test tests/"
  }
}
```

- [ ] **Step 2: Write the failing test**

`webapp/tests/worker.test.js`:

```js
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createSession } from '../src/worker.js';

const fixture = JSON.parse(readFileSync(new URL('./fixtures/swing.json', import.meta.url)));

test('a recorded swing scores through the same path as a live capture', async () => {
  const s = await createSession({ beta: 0.041, emaAlpha: 0.3 });
  for (const batch of fixture.batches) s.pushBatch(Float64Array.from(batch));
  const result = s.finish();
  assert.ok(result, 'fixture must be scorable');
  assert.ok(result.n >= 0, 'n present');
  assert.ok(Number.isFinite(result.a0_deg), 'a0_deg finite');
});

test('an all-calm log reports no release rather than throwing', async () => {
  const s = await createSession({ beta: 0.041, emaAlpha: 0.3 });
  const calm = [];
  for (let i = 0; i < 600; i++) calm.push(i * 16.667, 0, 0, 9.81, 0.01, 0, 0);
  s.pushBatch(Float64Array.from(calm));
  assert.equal(s.finish(), undefined);
});
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd webapp && npm test`
Expected: FAIL — cannot find `../src/worker.js`

- [ ] **Step 4: Write the worker**

`webapp/src/worker.js`:

```js
// Owns the WASM instance. Accepts batches from the live listener or from a
// recorded fixture through the same entry point -- that seam is the only way
// to test capture automatically, because no driver can synthesise a real
// DeviceMotionEvent in Safari.
import init, { WasmSession } from './wasm/mobile_imu_core.js';

let ready = null;

export async function createSession({ beta, emaAlpha }) {
  ready ??= init();
  await ready;
  const inner = new WasmSession(beta, emaAlpha);
  return {
    pushBatch: (buf) => inner.push_batch(buf),
    state: () => ({
      code: inner.state_code(),
      calm_s: inner.calm_s(),
      drift_deg: inner.drift_deg(),
    }),
    finish: () => {
      const json = inner.finish();
      return json === undefined ? undefined : JSON.parse(json);
    },
  };
}

// Worker entry point. Kept separate from createSession so tests can drive the
// session directly without a Worker host.
if (typeof self !== 'undefined' && typeof self.postMessage === 'function') {
  let session = null;
  self.onmessage = async (e) => {
    const m = e.data;
    if (m.type === 'start') {
      session = await createSession(m.cfg);
      self.postMessage({ type: 'state', ...session.state() });
    } else if (m.type === 'batch') {
      session.pushBatch(new Float64Array(m.buf));
      self.postMessage({ type: 'state', ...session.state() });
    } else if (m.type === 'finish') {
      const params = session.finish();
      self.postMessage(params ? { type: 'result', params }
                              : { type: 'error', reason: 'unscorable' });
    }
  };
}
```

- [ ] **Step 5: Generate the fixture from a real capture**

```bash
node -e "
const raw = JSON.parse(require('fs').readFileSync(process.argv[1]));
const b = [];
for (const s of raw.samples) {
  b.push(s.ts, s.ax, s.ay, s.az,
         s.beta*Math.PI/180, s.gamma*Math.PI/180, s.alpha*Math.PI/180);
}
const batches = [];
for (let i = 0; i < b.length; i += 7*3) batches.push(b.slice(i, i + 7*3));
require('fs').writeFileSync('webapp/tests/fixtures/swing.json',
  JSON.stringify({batches}));
" <path-to-a-recorded-capture>.json
```

Any capture from the spike harness works. Do **not** commit a capture containing participant data.

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd webapp && npm test`
Expected: PASS, 2 tests

- [ ] **Step 7: Commit**

```bash
git add webapp/package.json webapp/src/worker.js webapp/tests/
git commit -m "feat: add capture worker with a replay seam for automated testing"
```

---

### Task 5: Main-thread capture

**Files:**
- Create: `webapp/src/capture.js`

**Interfaces:**
- Consumes: worker protocol from Task 4
- Produces: `startCapture({ onState, onResult, onError }) -> { stop() }`

- [ ] **Step 1: Write the capture module**

`webapp/src/capture.js`:

```js
// Runs on the MAIN THREAD, and must: DeviceMotionEvent is exposed only on
// window, and requestPermission() must be called from a user gesture on iOS.
// The handler stays minimal so UI work never delays or drops sensor events.

const BATCH_MS = 50;          // matches TICK_S; the production _IMU_PAGE cadence
const FLOATS_PER_SAMPLE = 7;  // [t_ms, ax, ay, az, gx, gy, gz]
const CAP = 64;               // 50ms at 60Hz is ~3 samples; ample headroom
const DEG2RAD = Math.PI / 180;

export async function startCapture({ onState, onResult, onError }) {
  if (typeof DeviceMotionEvent === 'undefined') {
    onError('This browser does not expose motion sensors.');
    return { stop() {} };
  }
  if (typeof DeviceMotionEvent.requestPermission === 'function') {
    const granted = await DeviceMotionEvent.requestPermission();
    if (granted !== 'granted') {
      onError('Motion permission denied. Reload the tab and tap Start to retry.');
      return { stop() {} };
    }
  }

  let wakeLock = null;
  try { wakeLock = await navigator.wakeLock?.request('screen'); } catch { /* best effort */ }

  const worker = new Worker(new URL('./worker.js', import.meta.url), { type: 'module' });
  worker.onmessage = (e) => {
    const m = e.data;
    if (m.type === 'state') onState(m);
    else if (m.type === 'result') onResult(m.params);
    else if (m.type === 'error') onError(m.reason);
  };
  worker.postMessage({ type: 'start', cfg: { beta: 0.041, emaAlpha: 0.3 } });

  let buf = new Float64Array(CAP * FLOATS_PER_SAMPLE);
  let n = 0;

  const onMotion = (event) => {
    const a = event.accelerationIncludingGravity;
    const r = event.rotationRate;
    if (!a || a.x === null || !r || r.beta === null) return;
    if (n >= CAP) return;                    // dropped rather than reallocating mid-handler
    const o = n * FLOATS_PER_SAMPLE;
    // event.timeStamp is stamped at event CREATION, so it survives main-thread
    // contention. Using a handler-time clock instead is what collapsed dt to
    // zero in the 2026-08-17 defect.
    buf[o] = event.timeStamp;
    buf[o + 1] = a.x; buf[o + 2] = a.y; buf[o + 3] = a.z;
    // Spec axis mapping: beta->x, gamma->y, alpha->z. Browser sends deg/s.
    buf[o + 4] = r.beta * DEG2RAD;
    buf[o + 5] = r.gamma * DEG2RAD;
    buf[o + 6] = r.alpha * DEG2RAD;
    n++;
  };

  const flush = () => {
    if (n === 0) return;
    const out = buf.subarray(0, n * FLOATS_PER_SAMPLE).slice();
    n = 0;
    // Transfer ownership -- no copy, and no COOP/COEP isolation required.
    worker.postMessage({ type: 'batch', buf: out.buffer }, [out.buffer]);
  };

  window.addEventListener('devicemotion', onMotion);
  const timer = setInterval(flush, BATCH_MS);

  return {
    stop() {
      clearInterval(timer);
      window.removeEventListener('devicemotion', onMotion);
      flush();
      worker.postMessage({ type: 'finish' });
      wakeLock?.release?.().catch(() => {});
    },
  };
}
```

- [ ] **Step 2: Verify the worker still passes its tests**

Run: `cd webapp && npm test`
Expected: PASS, 2 tests. `capture.js` is browser-only and is exercised in Task 6 on device.

- [ ] **Step 3: Commit**

```bash
git add webapp/src/capture.js
git commit -m "feat: add main-thread devicemotion capture with 50ms transfer batching"
```

---

### Task 6: UI state machine and result view

**Files:**
- Create: `webapp/index.html`
- Create: `webapp/src/app.js`

**Interfaces:**
- Consumes: `startCapture` from Task 5
- Produces: none (leaf)

- [ ] **Step 1: Write the page**

`webapp/index.html`:

```html
<!DOCTYPE html>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, user-scalable=no">
<title>Pendulastic</title>
<link rel="stylesheet" href="./src/app.css">
<body>
  <div id="banner">research capture only — not validated</div>
  <button id="start">Start trial</button>
  <div id="guide">tap Start</div>
  <dl id="gates">
    <dt>hold</dt><dd id="calm">—</dd>
    <dt>drift</dt><dd id="drift">—</dd>
  </dl>
  <button id="stop" hidden>Stop</button>
  <table id="result" hidden></table>
  <script type="module" src="./src/app.js"></script>
</body>
```

The banner is required by spec §8 and is not conditional until G0 passes.

- [ ] **Step 2: Write the state machine**

`webapp/src/app.js`:

```js
import { startCapture } from './capture.js';

const el = (id) => document.getElementById(id);
const STATES = ['MOVING\nhold still', 'HOLDING', 'READY\nrelease now', 'RELEASED\nlet it settle'];
const CLASSES = ['moving', 'holding', 'ready', 'fired'];

let session = null;

function onState({ code, calm_s, drift_deg }) {
  const g = el('guide');
  g.className = CLASSES[code];
  g.textContent = code === 1 ? `HOLDING ${calm_s.toFixed(1)}s` : STATES[code];
  // Both gates are surfaced separately: the corrective action for motion and
  // for drift differ, so "it failed" is not enough for the clinician.
  el('calm').textContent = `${calm_s.toFixed(2)} s / 0.95 s`;
  el('drift').textContent = `${drift_deg.toFixed(2)}° / 5.00°`;
}

function onResult(p) {
  el('result').hidden = false;
  el('result').innerHTML = Object.entries(p)
    .map(([k, v]) => `<tr><td>${k}</td><td>${
      typeof v === 'number' ? v.toFixed(4) : v}</td></tr>`)
    .join('');
}

function onError(reason) {
  el('guide').className = 'moving';
  el('guide').textContent = reason;
}

el('start').addEventListener('click', async () => {
  el('start').hidden = true;
  el('stop').hidden = false;
  el('result').hidden = true;
  session = await startCapture({ onState, onResult, onError });
});

el('stop').addEventListener('click', () => {
  session?.stop();
  el('stop').hidden = true;
  el('start').hidden = false;
});
```

- [ ] **Step 3: Verify on a real iPhone**

Serve `webapp/` over HTTPS (reuse `get_or_create_self_signed_cert` from `pendulastic_phone_server.py`), open in Safari, and run one trial: hold until READY, release, settle, Stop.

Expected: the guide reaches READY only after ~1 s of genuine stillness; the drift figure climbs visibly during a slow creep; a scored trial shows 19 parameters.

- [ ] **Step 4: Commit**

```bash
git add webapp/index.html webapp/src/app.js
git commit -m "feat: add capture UI with separate motion and drift gate readouts"
```

---

### Task 7: CI

The repo has no `.github/` at all. The export-contract test is the highest-value check here, because the failure it guards is silent.

**Files:**
- Create: `.github/workflows/ci.yml`

- [ ] **Step 1: Write the workflow**

```yaml
name: ci
on: [push, pull_request]

jobs:
  rust:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: rustup target add wasm32-unknown-unknown
      - run: cargo test --manifest-path mobile-imu-core/Cargo.toml
      - run: cargo clippy --manifest-path mobile-imu-core/Cargo.toml --all-targets
      # Proves the veneer compiles for the target that actually ships.
      - run: cargo build --manifest-path mobile-imu-core/Cargo.toml
             --release --target wasm32-unknown-unknown

  export-contract:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: '3.12' }
      - run: pip install numpy scipy pytest
      # Guards a SILENT failure: a malformed export replays as an unscorable
      # trial, or worse, as a plausible wrong one.
      - run: pytest tests/test_web_export_contract.py -q

  webapp:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with: { node-version: '24' }
      - run: npm test --prefix webapp
```

- [ ] **Step 2: Verify locally before pushing**

```bash
cargo test --manifest-path mobile-imu-core/Cargo.toml
/c/Users/cladi/miniconda3/python.exe -m pytest tests/test_web_export_contract.py -q
npm test --prefix webapp
```

Expected: 52 Rust tests, 5 Python tests, 2 Node tests, all passing.

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/ci.yml
git commit -m "ci: add the first pipeline -- rust, export contract, webapp"
```

---

## Self-Review

**Spec coverage.** §1 (unit disposition) → Tasks 1–3 replace U3. §2.1–2.2 (execution architecture) → Tasks 4–5. §2.3 (fixed filter chain) → Global Constraints, enforced by Task 1 Step 6. §2.4 (feedforward only) → nothing in this plan adds optimisation. §4.2 (both stillness gates) → Task 2. §4.3 (`release_quat` at release) → Task 1's `replay`. §4.4 (release override) → `ReplayConfig::release_override`, wired in Task 1, surfaced in `TrialSession::finish`. §5 (state machine) → Task 6. §6 L1/L4/L5 → Tasks 1–2, 4. §7 (build, CI) → Tasks 3, 7. §8 (banner) → Task 6.

**Deferred to later plans, deliberately:** §3 in full (IndexedDB, install gate, export gating) → Plan 2. §3.4's export emitter → Plan 2; the *contract* is already pinned by `tests/test_web_export_contract.py`, which Task 7 puts in CI. §6 L7 (the 8-day eviction soak) → Plan 2, since it needs persistence to exist. U8–U12 → Plans 3–4.

**Type consistency.** `RawSample`/`Sensor`/`ReplayConfig`/`TrialError` are defined once in Task 1 and imported by Tasks 2–3. `HoldState`'s four variants map 1:1 onto `state_code()`'s 0–3 in Task 3 and `STATES`/`CLASSES` in Task 6. `push_batch`'s 7-float stride is fixed in Task 3 and matches `FLOATS_PER_SAMPLE` in Task 5 and the fixture generator in Task 4.

**Known gap, stated rather than hidden.** `WasmSession::finish` hand-formats JSON to avoid pulling `serde` into the wasm build. If the field list drifts from `PtParams`, nothing catches it — the same class of drift that made §3.3 machine-generated. A later task should generate this from the struct, or add a test that asserts the key set matches.
