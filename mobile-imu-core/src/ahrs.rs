//! Madgwick MARG filter — ported from `pendulastic_imu_server.py`'s
//! `MadgwickAHRS` class (lines ~134-301), the `_gravity_seed` tilt-alignment
//! helper, and the small quaternion utilities it depends on.
//!
//! Faithful port, not a redesign (U1's Approach): parameter names, thresholds,
//! and comments explaining *why* a given constant/gate exists are carried over
//! verbatim from the Python source so this stays traceable against it.

/// A quaternion in [w, x, y, z] order, matching the Python source's `np.array`
/// convention exactly (not the more common [x, y, z, w] some crates use).
pub type Quat = [f64; 4];
pub type Vec3 = [f64; 3];

/// Madgwick filter gain. Higher = trusts accel/mag more (faster drift
/// correction, noisier); lower = trusts gyro more. 0.041 is Madgwick's
/// suggested MARG value.
pub const BETA: f64 = 0.041;

/// `MadgwickAHRS.update()`'s accelerometer-correction gate: below this angular
/// velocity, treat the sensor as still enough that its accel reading is a
/// trustworthy gravity reference. Magnitude-proximity-to-g alone (the gate's
/// other, older condition) is not sufficient — a slowly swinging/settling
/// pendulum has real centripetal/tangential acceleration whose magnitude can
/// sit within the gate's tolerance of g while its direction is meaningfully
/// off from true gravity, so correcting toward it steers orientation toward
/// the pendulum's own motion instead of doing nothing. 0.3 rad/s (~17 deg/s)
/// matches this codebase's existing "recently calm" bar
/// (`ZERO_CAPTURE_GUARD_RAD_S` in `calibration.rs`) rather than introducing a
/// new one; confirmed via corpus-wide validation against every real trial
/// with an OptiTrack match that it lowers RMSE broadly, not just on the
/// trials that motivated it.
pub const ACCEL_CORRECTION_GYRO_MAX_RAD_S: f64 = 0.3;

fn norm3(v: Vec3) -> f64 {
    (v[0] * v[0] + v[1] * v[1] + v[2] * v[2]).sqrt()
}

fn norm4(q: Quat) -> f64 {
    (q[0] * q[0] + q[1] * q[1] + q[2] * q[2] + q[3] * q[3]).sqrt()
}

/// Quaternion conjugate.
pub fn qconj(q: Quat) -> Quat {
    [q[0], -q[1], -q[2], -q[3]]
}

/// Hamilton product `a * b`.
pub fn qmul(a: Quat, b: Quat) -> Quat {
    [
        a[0] * b[0] - a[1] * b[1] - a[2] * b[2] - a[3] * b[3],
        a[0] * b[1] + a[1] * b[0] + a[2] * b[3] - a[3] * b[2],
        a[0] * b[2] - a[1] * b[3] + a[2] * b[0] + a[3] * b[1],
        a[0] * b[3] + a[1] * b[2] - a[2] * b[1] + a[3] * b[0],
    ]
}

/// Wrap an angle difference into `[-180, 180)`.
pub fn wrap180(deg: f64) -> f64 {
    (deg + 180.0).rem_euclid(360.0) - 180.0
}

/// Return `(roll, pitch, yaw)` in degrees — ZYX convention.
/// roll ≈ abduction/adduction, pitch ≈ flexion/extension, yaw ≈ rotation.
pub fn quat_to_euler_deg(q: Quat) -> (f64, f64, f64) {
    let (q1, q2, q3, q4) = (q[0], q[1], q[2], q[3]);
    let roll = (2.0 * (q1 * q2 + q3 * q4)).atan2(1.0 - 2.0 * (q2 * q2 + q3 * q3));
    let sin_p = (2.0 * (q1 * q3 - q4 * q2)).clamp(-1.0, 1.0);
    let pitch = sin_p.asin();
    let yaw = (2.0 * (q1 * q4 + q2 * q3)).atan2(1.0 - 2.0 * (q3 * q3 + q4 * q4));
    (roll.to_degrees(), pitch.to_degrees(), yaw.to_degrees())
}

/// Tilt-alignment quaternion: shortest rotation from sensor-Z-up (AHRS default
/// identity) to the measured gravity direction.
///
/// The Madgwick filter's equilibrium for q=[1,0,0,0] has the accelerometer
/// reading `[0,0,+1]·g` (sensor Z aligned with the gravity reaction vector).
/// A phone mounted face-down reads ≈`[0,0,-1]·g` — nearly 180° from identity —
/// so without seeding the filter needs tens of seconds to converge. Seeding
/// from the first accel packet eliminates that delay completely.
///
/// Derivation: shortest-arc quaternion from `[0,0,1]` to `g_hat`.
/// `q_unnorm = [1 + g_hat·ẑ, cross(ẑ, g_hat)] = [1 + gz, [-gy, gx, 0]]`,
/// `|q_unnorm| = sqrt(2·(1 + gz))`. Special case `gz ≈ -1` (anti-aligned):
/// rotate 180° around X instead.
pub fn gravity_seed(accel: Vec3) -> Quat {
    let n = norm3(accel);
    if n < 1e-9 {
        return [1.0, 0.0, 0.0, 0.0];
    }
    let (gx, gy, gz) = (accel[0] / n, accel[1] / n, accel[2] / n);
    let denom = (2.0 * (1.0 + gz)).max(0.0).sqrt();
    if denom < 1e-9 {
        // gz ≈ -1: 180° — pick X axis.
        return [0.0, 1.0, 0.0, 0.0];
    }
    [(1.0 + gz) / denom, -gy / denom, gx / denom, 0.0]
}

/// Quaternion orientation filter with gyroscope prediction and
/// accelerometer/magnetometer gradient-descent correction (the two-step
/// predict/correct structure described in Madgwick's reference paper).
pub struct MadgwickAhrs {
    pub beta: f64,
    pub q: Quat,
    /// Slow EMA of `|accel|`; self-calibrates to g in any units (g's or m/s²).
    a_est: f64,
}

impl MadgwickAhrs {
    pub fn new(beta: f64) -> Self {
        Self {
            beta,
            q: [1.0, 0.0, 0.0, 0.0],
            a_est: 0.0,
        }
    }

    pub fn reset(&mut self) {
        self.q = [1.0, 0.0, 0.0, 0.0];
    }

    /// `gyro`: rad/s. `accel`: any unit. `mag`: `None` to disable
    /// magnetometer correction (KTD10 — the live desktop path deliberately
    /// excludes it; see `lib.rs`'s crate-level docs). `dt`: seconds since
    /// previous update.
    pub fn update(&mut self, gyro: Vec3, accel: Vec3, mag: Option<Vec3>, dt: f64) {
        let (q1, q2, q3, q4) = (self.q[0], self.q[1], self.q[2], self.q[3]);
        let (gx, gy, gz) = (gyro[0], gyro[1], gyro[2]);

        // Prediction: integrate angular velocity.
        let mut q_dot = [
            0.5 * (-q2 * gx - q3 * gy - q4 * gz),
            0.5 * (q1 * gx + q3 * gz - q4 * gy),
            0.5 * (q1 * gy - q2 * gz + q4 * gx),
            0.5 * (q1 * gz + q2 * gy - q3 * gx),
        ];

        // Correction: gradient descent toward accel (+ mag) reference.
        let a_norm = norm3(accel);
        self.a_est = if self.a_est == 0.0 {
            a_norm
        } else {
            0.999 * self.a_est + 0.001 * a_norm
        };
        // Skip the accelerometer correction step during high-impact transients
        // (magnitude check) and during any meaningful rotation (gyro-magnitude
        // check) so the gravity-direction estimate is not distorted by linear
        // accel from an actively swinging/settling sensor.
        let omega_mag = norm3(gyro);
        let do_correct = self.a_est > 1e-9
            && (0.9 * self.a_est..=1.1 * self.a_est).contains(&a_norm)
            && omega_mag < ACCEL_CORRECTION_GYRO_MAX_RAD_S;

        if a_norm > 1e-9 && do_correct {
            let (ax, ay, az) = (accel[0] / a_norm, accel[1] / a_norm, accel[2] / a_norm);

            let m_norm = mag.map(norm3).unwrap_or(0.0);
            let use_mag = m_norm > 1e-9;

            let step = if use_mag {
                let m = mag.unwrap();
                let (mx, my, mz) = (m[0] / m_norm, m[1] / m_norm, m[2] / m_norm);
                // Earth-frame magnetic reference, tilt-compensated.
                let hx = 2.0
                    * (mx * (0.5 - q3 * q3 - q4 * q4) + my * (q2 * q3 - q1 * q4)
                        + mz * (q2 * q4 + q1 * q3));
                let hy = 2.0
                    * (mx * (q2 * q3 + q1 * q4) + my * (0.5 - q2 * q2 - q4 * q4)
                        + mz * (q3 * q4 - q1 * q2));
                let bx = (hx * hx + hy * hy).sqrt();
                let bz = 2.0
                    * (mx * (q2 * q4 - q1 * q3) + my * (q3 * q4 + q1 * q2)
                        + mz * (0.5 - q2 * q2 - q3 * q3));

                // Objective function (gravity + magnetic field residuals).
                let f = [
                    2.0 * (q2 * q4 - q1 * q3) - ax,
                    2.0 * (q1 * q2 + q3 * q4) - ay,
                    2.0 * (0.5 - q2 * q2 - q3 * q3) - az,
                    2.0 * bx * (0.5 - q3 * q3 - q4 * q4) + 2.0 * bz * (q2 * q4 - q1 * q3) - mx,
                    2.0 * bx * (q2 * q3 - q1 * q4) + 2.0 * bz * (q1 * q2 + q3 * q4) - my,
                    2.0 * bx * (q1 * q3 + q2 * q4) + 2.0 * bz * (0.5 - q2 * q2 - q3 * q3) - mz,
                ];
                let j = [
                    [-2.0 * q3, 2.0 * q4, -2.0 * q1, 2.0 * q2],
                    [2.0 * q2, 2.0 * q1, 2.0 * q4, 2.0 * q3],
                    [0.0, -4.0 * q2, -4.0 * q3, 0.0],
                    [
                        -2.0 * bz * q3,
                        2.0 * bz * q4,
                        -4.0 * bx * q3 - 2.0 * bz * q1,
                        -4.0 * bx * q4 + 2.0 * bz * q2,
                    ],
                    [
                        -2.0 * bx * q4 + 2.0 * bz * q2,
                        2.0 * bx * q3 + 2.0 * bz * q1,
                        2.0 * bx * q2 + 2.0 * bz * q4,
                        -2.0 * bx * q1 + 2.0 * bz * q3,
                    ],
                    [
                        2.0 * bx * q3,
                        2.0 * bx * q4 - 4.0 * bz * q2,
                        2.0 * bx * q1 - 4.0 * bz * q3,
                        2.0 * bx * q2,
                    ],
                ];
                jt_dot_f(&j, &f)
            } else {
                // IMU-only fallback (no magnetometer): gravity residual only.
                let f = [
                    2.0 * (q2 * q4 - q1 * q3) - ax,
                    2.0 * (q1 * q2 + q3 * q4) - ay,
                    2.0 * (0.5 - q2 * q2 - q3 * q3) - az,
                ];
                let j = [
                    [-2.0 * q3, 2.0 * q4, -2.0 * q1, 2.0 * q2],
                    [2.0 * q2, 2.0 * q1, 2.0 * q4, 2.0 * q3],
                    [0.0, -4.0 * q2, -4.0 * q3, 0.0],
                ];
                jt_dot_f(&j, &f)
            };

            let s_norm = norm4(step);
            if s_norm > 1e-9 {
                for i in 0..4 {
                    q_dot[i] -= self.beta * (step[i] / s_norm);
                }
            }
        }

        for i in 0..4 {
            self.q[i] += q_dot[i] * dt;
        }
        let n = norm4(self.q);
        if n > 1e-9 {
            for i in 0..4 {
                self.q[i] /= n;
            }
        }
    }

    /// Return `(roll, pitch, yaw)` in degrees — ZYX convention.
    pub fn euler_deg(&self) -> (f64, f64, f64) {
        quat_to_euler_deg(self.q)
    }
}

/// `J^T · f` for a Jacobian with 4 columns and however many rows `f` has
/// (3 for gravity-only, 6 for gravity+mag) — generic over row count via a
/// slice-of-rows so both cases share one implementation.
fn jt_dot_f<const N: usize>(j: &[[f64; 4]; N], f: &[f64; N]) -> Quat {
    let mut out = [0.0; 4];
    for col in 0..4 {
        let mut acc = 0.0;
        for row in 0..N {
            acc += j[row][col] * f[row];
        }
        out[col] = acc;
    }
    out
}
