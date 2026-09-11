//! Numeric primitives the U2 scoring port needs, reimplemented from the
//! numpy/scipy operations the Python reference calls.
//!
//! The crate deliberately has no dependencies (it cross-compiles to iOS and
//! Android via UniFFI in U3), so each scipy/numpy routine `compute_pt_params`
//! relies on is ported here rather than pulled in. Every function is pinned
//! against golden values generated from the live Python implementation — see
//! `tests/fixtures/gen_fixtures.py`. Where scipy's behavior is surprising
//! (Savitzky-Golay's edge handling, `find_peaks`' filter ordering), the
//! comments record what it actually does, because "reasonable"
//! reimplementations of those differ from scipy in ways that would silently
//! move a clinical score rather than fail loudly.

/// Solve a small dense `n x n` system by Gaussian elimination with partial
/// pivoting. Returns `None` if the matrix is singular to working precision.
///
/// Sized for the 2x2-4x4 systems polynomial fitting produces here, so the
/// O(n^3) cost is irrelevant and clarity wins over a factorization cache.
///
/// Indexed rather than iterated on purpose: pivoting and row elimination both
/// need two rows of the same matrix at once, which an iterator form can only
/// express through `split_at_mut` gymnastics that obscure a textbook
/// algorithm.
#[allow(clippy::needless_range_loop)]
fn solve(mut a: Vec<Vec<f64>>, mut b: Vec<f64>) -> Option<Vec<f64>> {
    let n = b.len();
    for col in 0..n {
        let (mut piv, mut best) = (col, a[col][col].abs());
        for row in col + 1..n {
            if a[row][col].abs() > best {
                best = a[row][col].abs();
                piv = row;
            }
        }
        if best < 1e-300 {
            return None;
        }
        a.swap(col, piv);
        b.swap(col, piv);
        for row in col + 1..n {
            let factor = a[row][col] / a[col][col];
            if factor == 0.0 {
                continue;
            }
            for k in col..n {
                a[row][k] -= factor * a[col][k];
            }
            b[row] -= factor * b[col];
        }
    }
    let mut x = vec![0.0; n];
    for row in (0..n).rev() {
        let mut acc = b[row];
        for k in row + 1..n {
            acc -= a[row][k] * x[k];
        }
        x[row] = acc / a[row][row];
    }
    Some(x)
}

/// Least-squares fit of a degree-`order` polynomial to `(x, y)`, returned as
/// coefficients in *increasing* power order about the centroid of `x`.
///
/// The abscissa is centered before fitting (`u = x - x_mean`). This is
/// mathematically identical to fitting in `x` directly but far better
/// conditioned — `np.polyfit` scales its Vandermonde columns for the same
/// reason, and without centering a degree-3 fit over a 15-wide window builds a
/// Gram matrix spanning ~7 orders of magnitude.
///
/// Returns `(coeffs, x_mean)`; evaluate with [`polyval_centered`].
fn polyfit_centered(x: &[f64], y: &[f64], order: usize) -> Option<(Vec<f64>, f64)> {
    let n = x.len();
    if n == 0 || n != y.len() {
        return None;
    }
    let x_mean = x.iter().sum::<f64>() / n as f64;
    let m = order + 1;
    // Normal equations: (V^T V) c = V^T y, with V the centered Vandermonde.
    // Accumulate the power sums directly rather than materializing V.
    let mut gram = vec![vec![0.0; m]; m];
    let mut rhs = vec![0.0; m];
    let mut powers = vec![1.0; m];
    for i in 0..n {
        let u = x[i] - x_mean;
        powers[0] = 1.0;
        for k in 1..m {
            powers[k] = powers[k - 1] * u;
        }
        for r in 0..m {
            for c in 0..m {
                gram[r][c] += powers[r] * powers[c];
            }
            rhs[r] += powers[r] * y[i];
        }
    }
    solve(gram, rhs).map(|c| (c, x_mean))
}

/// Evaluate a [`polyfit_centered`] result at `x`.
fn polyval_centered(coeffs: &[f64], x_mean: f64, x: f64) -> f64 {
    let u = x - x_mean;
    let mut acc = 0.0;
    for c in coeffs.iter().rev() {
        acc = acc * u + c;
    }
    acc
}

/// Savitzky-Golay convolution coefficients for the interior of the signal:
/// `y[i] = sum_j h[j] * x[i - halflen + j]`.
///
/// Mirrors `scipy.signal.savgol_coeffs(window_length, polyorder, use="dot")`
/// with the default centered `pos`: fit a degree-`polyorder` polynomial to
/// samples at offsets `-halflen ..= halflen` and evaluate it at offset 0.
///
/// scipy obtains these as the *minimum-norm* least-squares solution of the
/// underdetermined system `A h = e0`, where `A` is `(polyorder+1) x
/// window_length`. That solution is `h = A^T (A A^T)^-1 e0`, which is what
/// this computes. Solving a square system built from `A^T A` instead — the
/// reflex when porting a least-squares fit — is a different problem with a
/// different answer, since `A` here has more columns than rows.
fn savgol_coeffs(window_length: usize, polyorder: usize) -> Option<Vec<f64>> {
    if window_length == 0 || polyorder + 1 > window_length {
        return None;
    }
    let halflen = (window_length / 2) as isize;
    let m = polyorder + 1;
    // a[r][j] = offset_j ^ r
    let mut a = vec![vec![0.0; window_length]; m];
    for j in 0..window_length {
        let off = (j as isize - halflen) as f64;
        let mut p = 1.0;
        for a_row in a.iter_mut().take(m) {
            a_row[j] = p;
            p *= off;
        }
    }
    let mut gram = vec![vec![0.0; m]; m];
    for r in 0..m {
        for c in 0..m {
            gram[r][c] = (0..window_length).map(|j| a[r][j] * a[c][j]).sum();
        }
    }
    // deriv = 0 -> only the constant term of the fitted polynomial is wanted.
    let mut rhs = vec![0.0; m];
    rhs[0] = 1.0;
    let z = solve(gram, rhs)?;
    Some(
        (0..window_length)
            .map(|j| (0..m).map(|r| a[r][j] * z[r]).sum())
            .collect(),
    )
}

/// `scipy.signal.savgol_filter(x, window_length, polyorder)` with scipy's
/// default `mode="interp"`.
///
/// The edge behavior is the part worth stating explicitly, because it is not
/// what a padding-based implementation produces: scipy does **not** pad. The
/// interior is a straight convolution with the SG coefficients, and then the
/// first and last `halflen` outputs are *overwritten* by evaluating a single
/// degree-`polyorder` polynomial fitted to the first (respectively last)
/// `window_length` samples. Reflect/nearest/zero padding all give visibly
/// different values there, and those edge samples are load-bearing:
/// `compute_pt_params` reads `phi[0]`, the first post-release sample, directly
/// as `A0_raw` and rejects the whole trial if it is under 3 degrees.
///
/// Returns the input unchanged when the window is longer than the signal or
/// the polynomial order is too high to fit, matching how the reference's `_sg`
/// wrapper degrades rather than raising.
pub fn savgol_filter(x: &[f64], window_length: usize, polyorder: usize) -> Vec<f64> {
    let n = x.len();
    if window_length > n || window_length == 0 || polyorder + 1 > window_length {
        return x.to_vec();
    }
    let halflen = window_length / 2;
    let coeffs = match savgol_coeffs(window_length, polyorder) {
        Some(c) => c,
        None => return x.to_vec(),
    };

    let mut y = vec![0.0; n];
    for (i, y_i) in y.iter_mut().enumerate() {
        let mut acc = 0.0;
        for (j, h) in coeffs.iter().enumerate() {
            // Interior formula. Outputs within halflen of either end are
            // discarded and replaced by the polynomial fit below, so
            // out-of-range taps are skipped rather than padded with anything.
            let idx = i as isize - halflen as isize + j as isize;
            if idx >= 0 && (idx as usize) < n {
                acc += h * x[idx as usize];
            }
        }
        *y_i = acc;
    }

    let abscissa: Vec<f64> = (0..window_length).map(|i| i as f64).collect();
    // Leading edge: fit over x[0..window_length], evaluate at 0..halflen.
    if let Some((c, xm)) = polyfit_centered(&abscissa, &x[..window_length], polyorder) {
        for (i, y_i) in y.iter_mut().enumerate().take(halflen) {
            *y_i = polyval_centered(&c, xm, i as f64);
        }
    }
    // Trailing edge: fit over the last window_length samples, evaluated at the
    // offsets corresponding to the final halflen positions.
    if let Some((c, xm)) = polyfit_centered(&abscissa, &x[n - window_length..], polyorder) {
        for (i, y_i) in y.iter_mut().enumerate().skip(n - halflen) {
            let off = (i - (n - window_length)) as f64;
            *y_i = polyval_centered(&c, xm, off);
        }
    }
    y
}

/// Indices of local maxima, using `scipy.signal._local_maxima_1d`'s plateau
/// rule: a run of equal values that is higher than its neighbours on both
/// sides counts as one peak, reported at the run's *midpoint* (floor of the
/// average of its edges).
///
/// The plateau handling is not incidental. A naive `x[i-1] < x[i] > x[i+1]`
/// scan finds nothing at all on a flat top, and quantised sensor data
/// produces flat tops routinely — those would become silently missing
/// oscillation cycles rather than an error.
///
/// A plateau that runs into the final sample is not a peak (it never comes
/// back down), matching scipy's `i_ahead < i_max` bound.
fn local_maxima(x: &[f64]) -> Vec<usize> {
    let mut out = Vec::new();
    let n = x.len();
    if n < 3 {
        return out;
    }
    let i_max = n - 1;
    let mut i = 1usize;
    while i < i_max {
        if x[i - 1] < x[i] {
            let mut i_ahead = i + 1;
            while i_ahead < i_max && x[i_ahead] == x[i] {
                i_ahead += 1;
            }
            if x[i_ahead] < x[i] {
                out.push((i + i_ahead - 1) / 2);
                i = i_ahead;
            }
        }
        i += 1;
    }
    out
}

/// Topographic prominence of each peak, mirroring
/// `scipy.signal.peak_prominences` with `wlen=None`.
///
/// For each peak, walk outwards in both directions while the signal stays at
/// or below the peak's height, tracking the lowest value seen on each side;
/// the prominence is the peak minus the *higher* of those two minima. The
/// walk is unbounded (it stops only at a higher sample or the array edge),
/// which is what makes prominence measure "how much this rises above its
/// surroundings" rather than "how tall it is".
fn peak_prominences(x: &[f64], peaks: &[usize]) -> Vec<f64> {
    let n = x.len();
    peaks
        .iter()
        .map(|&peak| {
            let h = x[peak];
            let mut left_min = h;
            let mut i = peak as isize;
            while i >= 0 && x[i as usize] <= h {
                if x[i as usize] < left_min {
                    left_min = x[i as usize];
                }
                i -= 1;
            }
            let mut right_min = h;
            let mut j = peak;
            while j < n && x[j] <= h {
                if x[j] < right_min {
                    right_min = x[j];
                }
                j += 1;
            }
            h - left_min.max(right_min)
        })
        .collect()
}

/// Thin out `peaks` so no two are closer than `distance` samples, mirroring
/// `scipy.signal._select_by_peak_distance`.
///
/// Tallest-first greedy: the highest remaining peak keeps its place and
/// suppresses every neighbour within `distance`. It is NOT a simple
/// left-to-right sweep — on a decaying oscillation those give different
/// survivors, and the survivors determine the cycle count `N`.
fn select_by_peak_distance(peaks: &[usize], heights: &[f64], distance: usize) -> Vec<bool> {
    let mut keep = vec![true; peaks.len()];
    // Ascending by height; ties broken by position so the result is
    // deterministic (numpy's default argsort is not a stable sort, so exact
    // ties are one of the few places this can legitimately differ from
    // scipy — on real-valued sensor data they do not occur).
    let mut order: Vec<usize> = (0..peaks.len()).collect();
    order.sort_by(|&a, &b| {
        heights[a]
            .partial_cmp(&heights[b])
            .unwrap_or(std::cmp::Ordering::Equal)
            .then(a.cmp(&b))
    });
    for &j in order.iter().rev() {
        if !keep[j] {
            continue;
        }
        let mut k = j as isize - 1;
        while k >= 0 && peaks[j] - peaks[k as usize] < distance {
            keep[k as usize] = false;
            k -= 1;
        }
        let mut k = j + 1;
        while k < peaks.len() && peaks[k] - peaks[j] < distance {
            keep[k] = false;
            k += 1;
        }
    }
    keep
}

/// `scipy.signal.find_peaks(x, height=, distance=, prominence=)`.
///
/// The filters are applied in scipy's fixed order — **height, then distance,
/// then prominence** — which is not the order they are named in and not
/// negotiable: distance runs a tallest-first contest among whatever peaks
/// survive, so filtering by prominence first hands that contest a different
/// field of candidates and yields a different set of survivors. Prominence is
/// measured against the full signal, but only for peaks that made it that far.
pub fn find_peaks(
    x: &[f64],
    height: Option<f64>,
    distance: Option<usize>,
    prominence: Option<f64>,
) -> Vec<usize> {
    let mut peaks = local_maxima(x);

    if let Some(hmin) = height {
        peaks.retain(|&i| x[i] >= hmin);
    }
    if let Some(d) = distance {
        if d > 1 && !peaks.is_empty() {
            let heights: Vec<f64> = peaks.iter().map(|&i| x[i]).collect();
            let keep = select_by_peak_distance(&peaks, &heights, d);
            peaks = peaks
                .iter()
                .zip(keep)
                .filter_map(|(&p, k)| if k { Some(p) } else { None })
                .collect();
        }
    }
    if let Some(pmin) = prominence {
        let proms = peak_prominences(x, &peaks);
        peaks = peaks
            .iter()
            .zip(proms)
            .filter_map(|(&p, pr)| if pr >= pmin { Some(p) } else { None })
            .collect();
    }
    peaks
}

/// `np.gradient(y, x)` — second-order accurate central differences in the
/// interior, first-order one-sided differences at the two ends (numpy's
/// default `edge_order=1`).
///
/// The interior formula is the *unequal-spacing* one. Real IMU timestamps are
/// never perfectly even, and `(y[i+1] - y[i-1]) / (x[i+1] - x[i-1])` — the
/// obvious centred difference — is only correct when the two half-steps match.
/// This matters: `omega_max_n` and `omega_min_n` are two of the seven scored
/// parameters and both come straight off this derivative.
pub fn gradient(y: &[f64], x: &[f64]) -> Vec<f64> {
    let n = y.len();
    assert_eq!(n, x.len(), "gradient: y and x must have equal length");
    if n < 2 {
        return vec![0.0; n];
    }
    let mut out = vec![0.0; n];
    for i in 1..n - 1 {
        let hs = x[i] - x[i - 1];
        let hd = x[i + 1] - x[i];
        let denom = hs * hd * (hd + hs);
        out[i] = if denom == 0.0 {
            0.0
        } else {
            (hs * hs * y[i + 1] + (hd * hd - hs * hs) * y[i] - hd * hd * y[i - 1]) / denom
        };
    }
    let first = x[1] - x[0];
    out[0] = if first == 0.0 {
        0.0
    } else {
        (y[1] - y[0]) / first
    };
    let last = x[n - 1] - x[n - 2];
    out[n - 1] = if last == 0.0 {
        0.0
    } else {
        (y[n - 1] - y[n - 2]) / last
    };
    out
}

/// Sorted finite values of `v`, the shared preparation step for the
/// NaN-skipping order statistics below.
fn finite_sorted(v: &[f64]) -> Vec<f64> {
    let mut f: Vec<f64> = v.iter().copied().filter(|x| x.is_finite()).collect();
    f.sort_by(|a, b| a.partial_cmp(b).unwrap_or(std::cmp::Ordering::Equal));
    f
}

/// `np.nanpercentile(v, q)` with numpy's default `method="linear"`: the rank
/// is `q/100 * (count - 1)` and the result interpolates linearly between the
/// two bracketing order statistics.
///
/// `_detect_release` derives its adaptive threshold from the 97th-minus-3rd
/// percentile spread of the signal, so a percentile that rounds to a rank
/// instead of interpolating moves the detected release point — and every
/// parameter measured from it.
///
/// Returns NaN when no finite values are present, matching numpy (which warns
/// and yields NaN rather than raising).
pub fn nanpercentile(v: &[f64], q: f64) -> f64 {
    let f = finite_sorted(v);
    if f.is_empty() {
        return f64::NAN;
    }
    if f.len() == 1 {
        return f[0];
    }
    let rank = (q / 100.0) * (f.len() - 1) as f64;
    let lo = rank.floor();
    let hi = rank.ceil();
    if lo == hi {
        return f[lo as usize];
    }
    let frac = rank - lo;
    f[lo as usize] * (1.0 - frac) + f[hi as usize] * frac
}

/// `np.nanmedian(v)` — NaNs skipped, and for an even count of finite values
/// the mean of the two central ones (not either one alone).
///
/// Returns NaN when nothing finite remains.
pub fn nanmedian(v: &[f64]) -> f64 {
    let f = finite_sorted(v);
    if f.is_empty() {
        return f64::NAN;
    }
    let mid = f.len() / 2;
    if f.len() % 2 == 1 {
        f[mid]
    } else {
        0.5 * (f[mid - 1] + f[mid])
    }
}

/// `np.polyfit(x, y, 1)` — ordinary least-squares straight-line fit, returned
/// as `(slope, intercept)` to match numpy's leading-coefficient-first order.
///
/// Used for `compute_pt_params`' drift correction, which fits the pre-release
/// baseline only and extrapolates it across the trial. Returns `None` for
/// fewer than two points or a degenerate (zero-variance) abscissa, where the
/// slope is not defined — the caller skips detrending rather than dividing by
/// zero.
pub fn polyfit1(x: &[f64], y: &[f64]) -> Option<(f64, f64)> {
    let n = x.len();
    if n < 2 || n != y.len() {
        return None;
    }
    let nf = n as f64;
    let mean_x = x.iter().sum::<f64>() / nf;
    let mean_y = y.iter().sum::<f64>() / nf;
    let mut sxx = 0.0;
    let mut sxy = 0.0;
    for i in 0..n {
        let dx = x[i] - mean_x;
        sxx += dx * dx;
        sxy += dx * (y[i] - mean_y);
    }
    if sxx <= 0.0 {
        return None;
    }
    let slope = sxy / sxx;
    Some((slope, mean_y - slope * mean_x))
}
