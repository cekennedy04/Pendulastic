//! `mobile-imu-core` — the shared Rust core for the phone-only IMU pendulum
//! app (see `docs/plans/2026-08-21-001-feat-phone-imu-pendulum-app-plan.md`).
//!
//! This crate is the single source of truth for the sensor-fusion/scoring
//! algorithm, shared into native iOS and Android apps via UniFFI (KTD2) once
//! U3 adds that binding layer. U1/U2 (this module and `goniometry`/`scoring`,
//! not yet added) are plain Rust with no UniFFI dependency — they're
//! buildable and testable on any machine with a Rust toolchain, which is
//! exactly why the plan sequences them before U3's cross-compilation work.
//!
//! **Magnetometer note (KTD10):** the live desktop reference deliberately
//! excludes magnetometer correction from `MadgwickAhrs::update` (`mag:
//! None`) — a real trial's magnetometer stream froze mid-recording from
//! indoor magnetic disturbance, and yaw isn't clinically relevant to knee
//! flexion. `mag` remains a parameter (not removed) so the option is there
//! if a future methodology comparison wants it, matching the reference's own
//! `params["use_mag"]` escape hatch — but callers should pass `None` for any
//! real capture.

pub mod ahrs;
pub mod calibration;
pub mod goniometry;
pub mod replay;
pub mod resample;
pub mod scoring;
pub mod session;
pub mod signal;
pub mod stillness;
