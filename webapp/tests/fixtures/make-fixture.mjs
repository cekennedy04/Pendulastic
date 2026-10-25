// Generates webapp/tests/fixtures/swing.json: a synthetic pendulum-test
// trial, forward-simulated the same way tests/test_web_export_contract.py
// (repo root) simulates one -- a rigid body rotating about its x axis by
// theta(t), gravity rotated into the sensor frame gives accel, theta's own
// derivative gives the gyro rate. No recorded capture is used or required
// (task-4 dispatch Correction 2): the scratch-directory capture the original
// brief pointed at does not exist in this repo, and a forward-simulated
// trial is deterministic, reviewable, and carries no participant data.
//
// Units here are the ones `WasmSession::push_batch` actually expects --
// checked against webapp/src/wasm/mobile_imu_core.d.ts, not the brief's
// prose -- which differ from test_web_export_contract.py's on two axes:
//   - gyro must already be rad/s (the Python contract test emits deg/s for
//     imu_calibration_tuner.replay_trial, a different consumer);
//   - timestamps are milliseconds, one flat `[t_ms, ax, ay, az, gx, gy, gz]`
//     record per sample (the Python contract emits two separate
//     accel/gyro records per sample with `t` in seconds).
//
// Run with: node webapp/tests/fixtures/make-fixture.mjs
import { writeFileSync } from 'node:fs';

const FS = 60.0; // iOS Safari's DeviceMotion rate, measured 2026-08-24 (see
                  // tests/test_web_export_contract.py).
const HOLD_S = 2.0; // >= GYRO_BIAS_WINDOW_S (1.0s) so the calm gate can
                     // qualify before the release detector is ever asked to
                     // arm.
const TOTAL_S = 9.0;
const A0_DEG = 45.0;
const F_HZ = 1.0;
const TAU = 1.2;
const G = 9.81;

// Small non-zero static offsets, matching test_web_export_contract.py's
// simulation, so the trial isn't suspiciously perfect: still comfortably
// inside GYRO_STATIONARY_MAX_RAD_S (0.9 rad/s) and ZERO_CAPTURE_GUARD_RAD_S
// (0.3 rad/s) during the hold, and ACCEL_STATIONARY_MAX_MPS2 (0.18 m/s^2)
// on accel.
const ACCEL_OFFSET = [0.010, 0.005, -0.008]; // m/s^2
const GYRO_OFFSET_DEG_S = [0.23, -0.11, 0.17]; // deg/s, converted below

const degToRad = (d) => (d * Math.PI) / 180;

function sample(t) {
  let theta = 0.0;
  let rate = 0.0;
  if (t >= HOLD_S) {
    const tt = t - HOLD_S;
    const a = degToRad(A0_DEG);
    const w = 2.0 * Math.PI * F_HZ;
    const env = Math.exp(-tt / TAU);
    theta = a * (1.0 - env * Math.cos(w * tt));
    rate = a * env * (Math.cos(w * tt) / TAU + w * Math.sin(w * tt));
  }
  const s = Math.sin(theta);
  const c = Math.cos(theta);
  const ax = ACCEL_OFFSET[0];
  const ay = s * G + ACCEL_OFFSET[1];
  const az = c * G + ACCEL_OFFSET[2];
  const gx = rate + degToRad(GYRO_OFFSET_DEG_S[0]);
  const gy = degToRad(GYRO_OFFSET_DEG_S[1]);
  const gz = degToRad(GYRO_OFFSET_DEG_S[2]);
  return [t * 1000.0, ax, ay, az, gx, gy, gz];
}

const nSamples = Math.round(TOTAL_S * FS);
const flat = [];
for (let i = 0; i < nSamples; i++) {
  flat.push(...sample(i / FS));
}

// Chunk into per-flush batches of 3 samples (21 doubles), mimicking how the
// live listener (Task 5) will coalesce a handful of DeviceMotionEvents
// before posting to the worker, rather than one push_batch call per sample.
const SAMPLES_PER_BATCH = 3;
const stride = 7 * SAMPLES_PER_BATCH;
const batches = [];
for (let i = 0; i < flat.length; i += stride) {
  batches.push(flat.slice(i, i + stride));
}

const out = new URL('./swing.json', import.meta.url);
writeFileSync(out, JSON.stringify({ batches }));
console.log(`wrote ${out.pathname.replace(/^\/([A-Za-z]):/, '$1:')} -- ${nSamples} samples, ${batches.length} batches`);
