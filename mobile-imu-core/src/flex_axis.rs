//! Causal anatomical flex-axis estimator, ported op-for-op from the Python
//! reference `imu_flex_axis.FlexAxisEstimator`, so the Rust core projects the
//! swing onto the SAME axis the offline/validated pipeline uses instead of the
//! axis-agnostic total-rotation magnitude (which counts yaw drift as flexion).
//!
//! Accumulates the second moment `M = Σ v vᵀ` of the above-threshold gyro
//! vectors (NOT mean-centred: a rotation axis is a direction, so the second
//! moment about the origin is the right quantity), exposes a provisional
//! single-sample axis immediately, and commits the dominant eigenvector after
//! `MIN_COMMIT_SAMPLES`. The committed axis is levelled onto the plane
//! perpendicular to gravity when it is within `MAX_GRAVITY_TILT_COS` of
//! horizontal. Sign is irrelevant here: the extraction is `abs(...)`, so no
//! sign pin is needed (unlike the signed variant on the sibling branch).

use crate::ahrs::Vec3;

pub const FLEX_THRESHOLD: f64 = 1.0; // rad/s; DEFAULT_THRESHOLD in Python
pub const MIN_COMMIT_SAMPLES: usize = 25;
pub const MAX_GRAVITY_TILT_COS: f64 = 0.5; // 0.5 => 30° off horizontal

fn dot3(a: Vec3, b: Vec3) -> f64 {
    a[0] * b[0] + a[1] * b[1] + a[2] * b[2]
}
fn norm3(v: Vec3) -> f64 {
    dot3(v, v).sqrt()
}
fn cross3(a: Vec3, b: Vec3) -> Vec3 {
    [
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    ]
}
fn det3(m: [[f64; 3]; 3]) -> f64 {
    m[0][0] * (m[1][1] * m[2][2] - m[1][2] * m[2][1])
        - m[0][1] * (m[1][0] * m[2][2] - m[1][2] * m[2][0])
        + m[0][2] * (m[1][0] * m[2][1] - m[1][1] * m[2][0])
}

/// Eigenvector for eigenvalue `lambda` of symmetric `m`: the null space of
/// `M - λI`, found as the largest-magnitude cross product of two of its rows.
fn eigenvector_for(m: [[f64; 3]; 3], lambda: f64) -> Option<Vec3> {
    let mut a = m;
    for i in 0..3 {
        a[i][i] -= lambda;
    }
    let rows = [a[0], a[1], a[2]];
    let cands = [
        cross3(rows[0], rows[1]),
        cross3(rows[0], rows[2]),
        cross3(rows[1], rows[2]),
    ];
    let best = cands
        .iter()
        .cloned()
        .max_by(|x, y| norm3(*x).partial_cmp(&norm3(*y)).unwrap())?;
    let n = norm3(best);
    if n.is_finite() && n > 1e-12 {
        Some([best[0] / n, best[1] / n, best[2] / n])
    } else {
        None
    }
}

/// Unit dominant eigenvector (largest eigenvalue) of a 3×3 symmetric matrix,
/// via Smith's (1961) closed-form eigenvalues. Matches numpy.linalg.eigh's
/// dominant eigenvector up to sign (which `abs()` extraction discards).
pub fn dominant_eigvec(m: [[f64; 3]; 3]) -> Option<Vec3> {
    let p1 = m[0][1].powi(2) + m[0][2].powi(2) + m[1][2].powi(2);
    if p1 == 0.0 {
        // Diagonal: dominant eigenvector is the axis of the largest diagonal.
        let d = [m[0][0], m[1][1], m[2][2]];
        let mut i = 0;
        if d[1] > d[i] {
            i = 1;
        }
        if d[2] > d[i] {
            i = 2;
        }
        let mut v = [0.0; 3];
        v[i] = 1.0;
        return Some(v);
    }
    let q = (m[0][0] + m[1][1] + m[2][2]) / 3.0;
    let p2 = (m[0][0] - q).powi(2) + (m[1][1] - q).powi(2) + (m[2][2] - q).powi(2) + 2.0 * p1;
    let p = (p2 / 6.0).sqrt();
    if !(p > 0.0) {
        return None;
    }
    let mut b = m;
    for i in 0..3 {
        b[i][i] -= q;
    }
    for i in 0..3 {
        for j in 0..3 {
            b[i][j] /= p;
        }
    }
    let r = (det3(b) / 2.0).clamp(-1.0, 1.0);
    let phi = r.acos() / 3.0;
    let eig1 = q + 2.0 * p * phi.cos(); // the largest eigenvalue
    eigenvector_for(m, eig1)
}

pub struct FlexAxisEstimator {
    threshold: f64,
    min_samples: usize,
    m: [[f64; 3]; 3],
    n: usize,
    axis: Option<Vec3>,
    committed: bool,
    first: Option<Vec3>,
    gravity: Option<Vec3>,
}

impl FlexAxisEstimator {
    pub fn new() -> Self {
        Self {
            threshold: FLEX_THRESHOLD,
            min_samples: MIN_COMMIT_SAMPLES,
            m: [[0.0; 3]; 3],
            n: 0,
            axis: None,
            committed: false,
            first: None,
            gravity: None,
        }
    }

    pub fn axis(&self) -> Option<Vec3> {
        self.axis
    }
    pub fn committed(&self) -> bool {
        self.committed
    }

    /// Feed one raw gyro vector, optionally with the sensor-frame gravity
    /// direction. Sub-threshold samples and post-commit calls are ignored, so
    /// callers may drive it unconditionally.
    pub fn update(&mut self, v: Vec3, gravity: Option<Vec3>) {
        if self.committed {
            return;
        }
        let mag = norm3(v);
        if !mag.is_finite() || mag < self.threshold {
            return;
        }
        if self.gravity.is_none() {
            if let Some(g) = gravity {
                let gn = norm3(g);
                if gn.is_finite() && gn > 1e-6 {
                    self.gravity = Some([g[0] / gn, g[1] / gn, g[2] / gn]);
                }
            }
        }
        let u = [v[0] / mag, v[1] / mag, v[2] / mag];
        if self.first.is_none() {
            self.first = Some(u);
            self.axis = Some(u); // provisional, identical to the old 1-sample capture
        }
        for i in 0..3 {
            for j in 0..3 {
                self.m[i][j] += v[i] * v[j];
            }
        }
        self.n += 1;
        if self.n >= self.min_samples {
            self.commit();
        }
    }

    fn commit(&mut self) {
        let axis = match dominant_eigvec(self.m) {
            Some(a) => a,
            None => {
                self.committed = true; // keep the provisional axis
                return;
            }
        };
        let mut axis = axis;
        // Level: drop the component along gravity, unless too far from horizontal.
        if let Some(g) = self.gravity {
            let along = dot3(axis, g);
            if along.abs() <= MAX_GRAVITY_TILT_COS {
                let leveled = [axis[0] - along * g[0], axis[1] - along * g[1], axis[2] - along * g[2]];
                let ln = norm3(leveled);
                if ln.is_finite() && ln > 1e-6 {
                    axis = [leveled[0] / ln, leveled[1] / ln, leveled[2] / ln];
                }
            }
        }
        self.axis = Some(axis);
        self.committed = true;
    }
}

impl Default for FlexAxisEstimator {
    fn default() -> Self {
        Self::new()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn approx_parallel(a: Vec3, b: Vec3, tol: f64) -> bool {
        // up to sign
        (dot3(a, b).abs() - 1.0).abs() < tol
    }

    #[test]
    fn dominant_eigvec_of_a_rank_one_moment_is_its_axis() {
        // M = k * a aᵀ has dominant eigenvector a.
        let a = [0.3, 0.7, -0.64807407];
        let an = norm3(a);
        let a = [a[0] / an, a[1] / an, a[2] / an];
        let mut m = [[0.0; 3]; 3];
        for i in 0..3 {
            for j in 0..3 {
                m[i][j] = 5.0 * a[i] * a[j];
            }
        }
        // add a little isotropic mass so it is not exactly rank-1
        for i in 0..3 {
            m[i][i] += 0.01;
        }
        let v = dominant_eigvec(m).unwrap();
        assert!(approx_parallel(v, a, 1e-6), "got {v:?} want {a:?}");
    }

    #[test]
    fn dominant_eigvec_of_a_diagonal_matrix_picks_the_largest() {
        let m = [[2.0, 0.0, 0.0], [0.0, 9.0, 0.0], [0.0, 0.0, 1.0]];
        let v = dominant_eigvec(m).unwrap();
        assert!(approx_parallel(v, [0.0, 1.0, 0.0], 1e-9), "got {v:?}");
    }

    #[test]
    fn estimator_commits_the_swing_axis() {
        let axis = [0.0, 1.0, 0.0];
        let mut est = FlexAxisEstimator::new();
        for _ in 0..MIN_COMMIT_SAMPLES {
            // gyro mostly about +y with a little noise off-axis
            est.update([0.05, 3.0, -0.04], Some([0.0, 0.0, -1.0]));
        }
        assert!(est.committed());
        assert!(approx_parallel(est.axis().unwrap(), axis, 1e-2), "got {:?}", est.axis());
    }

    #[test]
    fn sub_threshold_samples_are_ignored() {
        let mut est = FlexAxisEstimator::new();
        for _ in 0..100 {
            est.update([0.1, 0.1, 0.1], None); // |v| ≈ 0.17 < 1.0
        }
        assert!(!est.committed());
        assert!(est.axis().is_none());
    }
}
