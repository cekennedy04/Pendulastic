// PtScore.cs -- C# 3.0 / .NET 3.5 port of the Pendulastic pendulum-test scorer.
//
// Ports exactly ONE path, the production one:
//   neuromech/build.py: score_angle(t, angle_pend_deg, release_t)
//     -> resample to the scorer's 100 Hz grid (np.arange + np.interp)
//     -> pendulastic_pt_score.compute_pt_params(tg - tg[0], ang, release_idx=ridx, detrend=False)
//     -> pendulastic_pt_score.mas_estimate(prm)   (compute_pt_score_breakdown, pt_zone, pt_to_mas,
//                                                  excursion_ok, oscillation_ok)
// release_idx is always given and detrend is always False on that path, so release auto-detection
// (_detect_release) and the drift correction (_settled_tail_drift_slope, polyfit baseline) are never
// reached and are not ported. Every gate / refusal ON the path is kept.
//
// Angle convention: Pendulastic angle, 180 = full extension.
// numpy / scipy routines are reimplemented with their edge semantics (np.arange fill, np.interp,
// np.median, pairwise np.sum / np.mean, np.gradient's uniform/non-uniform branches, scipy
// find_peaks height->distance->prominence order, savgol_filter mode='interp').
// Parity against the Python is proved by neuromech/designer/parity/run_parity.py.
//
// Compiles with C:\Windows\Microsoft.NET\Framework\v3.5\csc.exe (C# 3.0). No dependencies.

using System;
using System.Collections.Generic;
using System.Globalization;
using System.Text;

namespace Pendulastic
{
    /// <summary>Everything score_angle / mas_estimate produce that is a scalar (plus the extrema).</summary>
    public sealed class PtResult
    {
        // ---- resampling (score_angle) ----
        public int GridLength;          // len(tg)
        public int ReleaseIndex;        // ridx = searchsorted(tg, release_t)

        // ---- compute_pt_params ----
        /// <summary>False exactly when compute_pt_params returned None.</summary>
        public bool HasParams;
        /// <summary>Why compute_pt_params returned None (C#-only diagnostic; Python gives no reason).</summary>
        public string NoneReason;

        public double R2n = double.NaN;
        public double N = double.NaN;
        public double phi_max_ratio = double.NaN;
        public double omega_max_n = double.NaN;
        public double omega_min_n = double.NaN;
        public double omega_peak_deg_s = double.NaN;
        public double f = double.NaN;
        public double area_ratio = double.NaN;
        public bool quality_warn;
        public double A0_deg = double.NaN;
        public double A1_deg = double.NaN;
        public double first_trough_depth = double.NaN;
        public double neutral_deg = double.NaN;
        public double neutral_deg_raw = double.NaN;
        public double pre_release_deg = double.NaN;
        public bool phi_negated;
        public double P_plus = double.NaN;
        public double P_minus = double.NaN;
        public double P_total = double.NaN;
        public string spasticity_type;
        public int[] PeakIdx = new int[0];     // pk_i, indices into the post-release arrays
        public int[] TroughIdx = new int[0];   // tr_i

        // ---- compute_pt_score_breakdown / compute_pt_score ----
        /// <summary>Per-term deviation, ordered as PtScore.ScoredKeys. Null when there are no params.</summary>
        public double[] Breakdown;
        /// <summary>PT score (mas_estimate's "pt7"); null when compute_pt_params returned None.</summary>
        public double? Pt7;

        // ---- mas_estimate ----
        public bool ExcursionOk;
        public bool OscillationOk;
        public bool Interpretable;
        /// <summary>mas_estimate "mas" (Popovic table); null when not interpretable.</summary>
        public string Mas;
        /// <summary>mas_estimate "zone"; null when not interpretable.</summary>
        public string Zone;
        /// <summary>mas_estimate "reason" ("" when interpretable).</summary>
        public string Reason = "";
        /// <summary>pt_zone(pt7) regardless of the gates ("unknown" when pt7 is null).</summary>
        public string PtZone = "unknown";
        /// <summary>pt_to_mas(pt7) regardless of the gates; null when pt7 is null.</summary>
        public string PtToMas;
        /// <summary>Null when scored; otherwise the refusal (mas_estimate's reason).</summary>
        public string Refused;

        public double Term(string key)
        {
            if (Breakdown == null) return double.NaN;
            for (int i = 0; i < PtScore.ScoredKeys.Length; i++)
                if (PtScore.ScoredKeys[i] == key) return Breakdown[i];
            throw new ArgumentException("not a scored key: " + key);
        }
    }

    public static class PtScore
    {
        // ── constants (pendulastic_pt_score.py / neuromech/build.py) ────────────────
        public const double FS_SCORE = 100.0;
        public const double SG_WINDOW_S = 0.10;
        public const double OSCILLATION_GAP_FACTOR = 2.5;
        public const double AREA_RATIO_WARN = 0.55;
        public const double PT_HEALTHY_MAX = 0.1709;
        public const double PT_BORDERLINE_MAX = 0.3528;
        public const string PT_ZONES_CALIBRATED_FOR = "pt7-2026-08-24";
        public const string SCORE_FORMULA_ID = "pt5rescaled-2026-10-27";
        public const double EXCURSION_REF_MEAN_DEG = 46.6;
        public const double EXCURSION_REF_SD_DEG = 11.1;
        public const int EXCURSION_REF_N = 53;
        public const double MIN_INTERPRETABLE_A0_DEG = 30.0;   // 25 -> 30 on 2026-10-27, see pendulastic_pt_score
        public const double MAX_INTERPRETABLE_A0_DEG = 120.0;

        /// <summary>_PARAM_KEYS: the scored set (omega_min_n diagnostic since pt6, omega_max_n since pt5).</summary>
        public static readonly string[] ScoredKeys = new string[] { "R2n", "N", "phi_max_ratio", "f", "area_ratio" };

        /// <summary>HEALTHY_REF, same keys and values as the Python.</summary>
        public static readonly Dictionary<string, double> HEALTHY_REF = MakeRef();

        static Dictionary<string, double> MakeRef()
        {
            Dictionary<string, double> d = new Dictionary<string, double>();
            d["R2n"] = 1.0;
            d["N"] = 6.5;
            d["phi_max_ratio"] = 0.6667;
            d["omega_max_n"] = 9.5672;
            d["omega_min_n"] = -9.4152;
            d["f"] = 0.8839;
            d["area_ratio"] = 0.0945;
            return d;
        }

        static readonly double[] MAS_T = new double[] { 0.12, 0.28, 0.44, 0.60, 0.78 };
        static readonly string[] MAS_L = new string[] { "0", "1", "1+", "2", "3" };

        public const string NO_PARAMS_REASON = "No PT parameters: the trial could not be scored.";

        // ═════════════════════════════════════════════════════════════════════
        // score_angle
        // ═════════════════════════════════════════════════════════════════════

        /// <summary>neuromech/build.py score_angle: Pendulastic angle (180 = full extension) on any
        /// time base -> 100 Hz grid -> compute_pt_params(release_idx, detrend=False) -> mas_estimate.</summary>
        public static PtResult ScoreAngle(double[] t, double[] anglePendDeg, double releaseT)
        {
            if (t == null || anglePendDeg == null) throw new ArgumentNullException("t");
            if (t.Length != anglePendDeg.Length) throw new ArgumentException("t and angle differ in length");
            if (t.Length == 0) throw new ArgumentException("empty input (Python: IndexError on t[0])");
            double[] tg = Arange(t[0], t[t.Length - 1], 1.0 / FS_SCORE);
            if (tg.Length == 0) throw new ArgumentException("empty 100 Hz grid (Python: IndexError on tg[0])");
            double[] ang = Interp(tg, t, anglePendDeg);
            int ridx = SearchSortedLeft(tg, releaseT);
            double[] t0 = new double[tg.Length];
            double tg0 = tg[0];
            for (int i = 0; i < tg.Length; i++) t0[i] = tg[i] - tg0;

            PtResult r = new PtResult();
            r.GridLength = tg.Length;
            r.ReleaseIndex = ridx;
            ComputePtParams(t0, ang, ridx, r);
            MasEstimate(r);
            return r;
        }

        // ═════════════════════════════════════════════════════════════════════
        // compute_pt_params (release_idx given, detrend=False)
        // ═════════════════════════════════════════════════════════════════════

        static void ComputePtParams(double[] t, double[] angleRaw, int releaseIdx, PtResult r)
        {
            int nTot = angleRaw.Length;
            List<int> finiteIdx = new List<int>(nTot);
            for (int i = 0; i < nTot; i++) if (IsFinite(angleRaw[i])) finiteIdx.Add(i);
            if (finiteIdx.Count < 40) { r.HasParams = false; r.NoneReason = "fewer than 40 finite samples"; return; }

            int nc = finiteIdx.Count;
            double[] t_c = new double[nc];
            double[] ang_c_raw = new double[nc];
            for (int i = 0; i < nc; i++) { t_c[i] = t[finiteIdx[i]]; ang_c_raw[i] = angleRaw[finiteIdx[i]]; }

            // release_idx given: map into the finite-only array, clip.
            int rel_i = SearchSortedLeftInt(finiteIdx, releaseIdx);
            rel_i = Math.Max(0, Math.Min(rel_i, nc - 1));

            // detrend=False -> ang_c = ang_c_raw (no slope).
            double[] ang_c = ang_c_raw;
            double[] ang_s = Sg(ang_c, MedianDt(t_c), SG_WINDOW_S, 3);

            int pre_n = Math.Max(3, Math.Min(20, rel_i));
            double pre_release_deg;
            if (rel_i > 0)
                pre_release_deg = NanMedian(Slice(ang_s, Math.Max(0, rel_i - pre_n), rel_i));
            else
                pre_release_deg = ang_s.Length > 0 ? ang_s[0] : 180.0;

            double[] t_r = Slice(t_c, rel_i, nc);
            double[] ang_r = Slice(ang_s, rel_i, nc);
            int L = t_r.Length;
            if (L < 25) { r.HasParams = false; r.NoneReason = "fewer than 25 post-release samples"; return; }

            double fps_eff = L / PyMax(t_r[L - 1] - t_r[0], 0.1);
            int min_dist = Math.Max(3, PyInt(fps_eff / 3.5));

            int tail_start = Math.Min(PyInt(0.75 * L), L - 1);
            double neutral = NanMedian(Slice(ang_r, tail_start, L));
            double[] ang_r_raw = Slice(ang_c_raw, rel_i, nc);
            double neutral_deg_raw = NanMedian(Slice(ang_r_raw, tail_start, ang_r_raw.Length));

            double[] phi = new double[L];
            for (int i = 0; i < L; i++) phi[i] = ang_r[i] - neutral;
            double A0_raw = phi[0];
            if (Math.Abs(A0_raw) < 3.0) { r.HasParams = false; r.NoneReason = "|A0_raw| < 3 deg (release within 3 deg of neutral)"; return; }
            bool phi_negated = A0_raw < 0;
            if (phi_negated)
            {
                for (int i = 0; i < L; i++) phi[i] = -phi[i];
                A0_raw = Math.Abs(A0_raw);
            }

            double dt_r = MedianDt(t_r);
            double[] phi_s = Sg(phi, dt_r, SG_WINDOW_S, 2);

            int first_n = Math.Max(5, PyInt(0.20 * L));
            double A0 = PyMax(NanMax(Slice(phi_s, 0, Math.Min(first_n, L))), A0_raw);

            double min_amp = PyMax(1.0, 0.05 * A0);
            double[] neg_phi_s = Negate(phi_s);
            int[] pk = FindPeaks(phi_s, min_amp, min_dist, min_amp);
            int[] tr = FindPeaks(neg_phi_s, min_amp, min_dist, min_amp);

            double _dt_c = L > 1 ? Median(Diff(t_r)) : 0.0;
            int[] ext0 = SortedConcat(pk, tr);
            double[] phi_centre = new double[L];
            if (ext0.Length >= 3 && _dt_c > 0)
            {
                double[] te = new double[ext0.Length];
                for (int i = 0; i < ext0.Length; i++) te[i] = t_r[ext0[i]];
                double half = Median(Diff(te));
                if (half > 0)
                {
                    phi_centre = SwingCentre(phi_s, _dt_c, 2.0 * half);
                    double[] sc = new double[L];
                    for (int i = 0; i < L; i++) sc[i] = phi_s[i] - phi_centre[i];
                    pk = FindPeaks(sc, min_amp, min_dist, min_amp);
                    tr = FindPeaks(Negate(sc), min_amp, min_dist, min_amp);
                }
            }

            double window_end_t = ActiveOscillationWindowEnd(t_r, ang_r, pk, tr, neutral, A0);
            pk = FilterByTime(pk, t_r, window_end_t);
            tr = FilterByTime(tr, t_r, window_end_t);

            // ── 1. R2n (A1 = peak-to-peak of the first oscillation) ──
            double[] phi_g = new double[L];
            for (int i = 0; i < L; i++) phi_g[i] = phi[i] - phi_centre[i];
            int firstNegTr = -1;
            for (int k = 0; k < tr.Length; k++) if (phi_g[tr[k]] < -min_amp) { firstNegTr = tr[k]; break; }
            double A1, first_trough_depth;
            if (firstNegTr >= 0)
            {
                first_trough_depth = Math.Abs(phi[firstNegTr]);
                A1 = A0 + first_trough_depth;
            }
            else { A1 = 0.0; first_trough_depth = 0.0; }
            double R2n = A0 > 1e-3 ? A1 / (1.6 * A0) : 0.0;

            // ── 2. N ──
            int n_pos = 0, n_neg = 0;
            for (int k = 0; k < pk.Length; k++) if (phi_g[pk[k]] > min_amp) n_pos++;
            for (int k = 0; k < tr.Length; k++) if (phi_g[tr[k]] < -min_amp) n_neg++;
            double N = (n_pos + n_neg) / 2.0;

            // ── 6. f ──
            int[] all_sig = SortedConcat(pk, tr);
            double f;
            if (all_sig.Length >= 4)
            {
                double[] t_ext = new double[all_sig.Length];
                for (int i = 0; i < all_sig.Length; i++) t_ext[i] = t_r[all_sig[i]];
                double[] half_p = Diff(t_ext);
                double med_hp = Median(half_p);
                List<double> valid = new List<double>();
                for (int i = 0; i < half_p.Length; i++)
                    if (Math.Abs(half_p[i] - med_hp) < 1.5 * med_hp) valid.Add(half_p[i]);
                double period = valid.Count > 0 ? 2.0 * Mean(valid.ToArray()) : 0.0;
                f = period > 1e-6 ? 1.0 / period : 0.0;
            }
            else f = 0.0;

            // ── 3. phi_max_ratio ──
            double phi_max_ratio;
            if (firstNegTr >= 0)
            {
                double ftt = t_r[firstNegTr];
                double window_end = ftt + (f > 0.2 ? 1.5 / f : 2.5);
                bool any = false; double best = 0.0;
                for (int k = 0; k < pk.Length; k++)
                {
                    int i = pk[k];
                    if (phi[i] > min_amp && t_r[i] > ftt && t_r[i] < window_end)
                    {
                        if (!any) { best = phi[i]; any = true; }
                        else if (phi[i] > best) best = phi[i];   // Python max(): first maximum wins
                    }
                }
                phi_max_ratio = (any ? best : 0.0) / A0;
            }
            else phi_max_ratio = 0.0;

            // ── 4 & 5. omega ──
            double[] omega_s = Sg(Gradient(phi, t_r), dt_r, SG_WINDOW_S, 2);
            double[] omega_abs = new double[L];
            for (int i = 0; i < L; i++) omega_abs[i] = Math.Abs(omega_s[i]);
            double omega_peak = NanMax(omega_abs);
            double omega_max_n = omega_peak / A0;
            List<double> swing = new List<double>();
            for (int i = 0; i < L; i++) if (Math.Abs(phi[i]) > min_amp) swing.Add(omega_s[i]);
            double omega_min_n = swing.Count > 5 ? NanMin(swing.ToArray()) / A0 : 0.0;

            // ── 7. area ratio ──
            double EXTEND_S = 4.5;
            double dt_mean = L > 1 ? Mean(Diff(t_r)) : 1.0 / 30.0;
            int n_ext = Math.Max(1, PyInt(EXTEND_S / dt_mean));
            int M = L + n_ext;
            double[] t_ar = new double[M];
            for (int i = 0; i < L; i++) t_ar[i] = t_r[i];
            for (int k = 1; k <= n_ext; k++) t_ar[L - 1 + k] = t_r[L - 1] + ((double)k) * dt_mean;
            double[] phi_c_ar = new double[L];
            for (int i = 0; i < L; i++) phi_c_ar[i] = phi[i] - phi_centre[i];
            double phi_rest_c = NanMedian(Slice(phi_c_ar, Math.Max(PyInt(0.80 * L), 1), L));
            double[] phi_ar = new double[M];
            for (int i = 0; i < L; i++) phi_ar[i] = phi_c_ar[i];
            for (int i = L; i < M; i++) phi_ar[i] = phi_rest_c;
            double[] wp = new double[M - 1];
            double[] wm = new double[M - 1];
            for (int i = 0; i < M - 1; i++)
            {
                double d = t_ar[i + 1] - t_ar[i];
                double mid = (phi_ar[i] + phi_ar[i + 1]) / 2.0;
                wp[i] = d * NpMaximum(mid, 0.0);
                wm[i] = d * NpMaximum(-mid, 0.0);
            }
            double P_plus = PairwiseSum(wp);
            double P_minus = PairwiseSum(wm);
            double P_total = P_plus + P_minus;
            double area_ratio = P_total > 1e-6 ? Math.Abs(P_plus - P_minus) / P_total : 1.0;

            string stype;
            if (P_plus > P_minus * 1.25) stype = "extension";
            else if (P_minus > P_plus * 1.25) stype = "flexion";
            else stype = "balanced";

            r.HasParams = true;
            r.R2n = R2n; r.N = N; r.phi_max_ratio = phi_max_ratio;
            r.omega_max_n = omega_max_n; r.omega_min_n = omega_min_n; r.omega_peak_deg_s = omega_peak;
            r.f = f; r.area_ratio = area_ratio; r.quality_warn = area_ratio > AREA_RATIO_WARN;
            r.A0_deg = A0; r.A1_deg = A1; r.first_trough_depth = first_trough_depth;
            r.neutral_deg = neutral; r.neutral_deg_raw = neutral_deg_raw; r.pre_release_deg = pre_release_deg;
            r.phi_negated = phi_negated;
            r.P_plus = P_plus; r.P_minus = P_minus; r.P_total = P_total;
            r.PeakIdx = pk; r.TroughIdx = tr;
            r.spasticity_type = stype;
        }

        static int[] FilterByTime(int[] idx, double[] t, double tEnd)
        {
            List<int> o = new List<int>(idx.Length);
            for (int k = 0; k < idx.Length; k++) if (t[idx[k]] <= tEnd) o.Add(idx[k]);
            return o.ToArray();
        }

        // _active_oscillation_window_end
        static double ActiveOscillationWindowEnd(double[] t_r, double[] ang_r, int[] pk, int[] tr, double neutral, double A0)
        {
            int[] ext = SortedConcat(pk, tr);
            if (ext.Length > 0)
            {
                double[] times = new double[ext.Length];
                for (int i = 0; i < ext.Length; i++) times[i] = t_r[ext[i]];
                if (times.Length == 1) return times[0];
                double[] gaps = Diff(times);
                double med = Median(gaps);
                if (med <= 0) return times[times.Length - 1];
                double limit = OSCILLATION_GAP_FACTOR * med;
                if (times[0] - t_r[0] > limit) return t_r[0];
                for (int i = 0; i < gaps.Length; i++) if (gaps[i] > limit) return times[i];
                return times[times.Length - 1];
            }
            double tol = PyMax(2.0, 0.05 * A0);
            int n = ang_r.Length;
            // first i with near_neutral[i:] all true; never -> n-1
            int lastFalse = -1;
            for (int i = n - 1; i >= 0; i--)
            {
                if (!(Math.Abs(ang_r[i] - neutral) <= tol)) { lastFalse = i; break; }
            }
            int settle = (lastFalse == n - 1) ? n - 1 : lastFalse + 1;
            return t_r[settle];
        }

        // _swing_centre
        static double[] SwingCentre(double[] phi, double dt, double period_s)
        {
            int len = phi.Length;
            int n = dt > 0 ? PyInt(PyRound(period_s / dt)) : 0;
            if (n < 3 || n >= len) return new double[len];
            if (n % 2 == 0) n += 1;
            int pad = n / 2;
            double[] padded = new double[len + 2 * pad];
            for (int i = 0; i < pad; i++) { padded[i] = phi[0]; padded[pad + len + i] = phi[len - 1]; }
            for (int i = 0; i < len; i++) padded[pad + i] = phi[i];
            double w = 1.0 / n;               // np.ones(n) / n
            int outLen = padded.Length - n + 1;
            double[] o = new double[len];
            for (int i = 0; i < outLen && i < len; i++)
            {
                double s = 0.0;
                for (int k = 0; k < n; k++) s += padded[i + k] * w;
                o[i] = s;
            }
            return o;
        }

        // ═════════════════════════════════════════════════════════════════════
        // mas_estimate / compute_pt_score_breakdown / pt_zone / pt_to_mas / gates
        // ═════════════════════════════════════════════════════════════════════

        static void MasEstimate(PtResult r)
        {
            if (!r.HasParams)
            {
                r.Pt7 = null; r.Mas = null; r.Zone = null; r.Interpretable = false;
                r.Reason = NO_PARAMS_REASON; r.Refused = r.Reason;
                r.PtZone = PtZoneOf(null); r.PtToMas = null;
                r.ExcursionOk = false; r.OscillationOk = false;
                return;
            }
            r.Breakdown = ComputePtScoreBreakdown(r);
            double score = 0.0;
            for (int i = 0; i < r.Breakdown.Length; i++) score += r.Breakdown[i];
            r.Pt7 = score;
            r.PtZone = PtZoneOf(score);
            r.PtToMas = PtToMas(score);
            r.ExcursionOk = ExcursionOk(r);
            r.OscillationOk = OscillationOk(r);
            if (!r.ExcursionOk)
            {
                double a0 = r.A0_deg;
                if (IsFinite(a0) && a0 > MAX_INTERPRETABLE_A0_DEG)
                    r.Reason = "Impossible excursion: the leg moved " + Fmt(a0, 1) + " deg, above the " + Fmt(MAX_INTERPRETABLE_A0_DEG, 0) + " deg "
                        + "ceiling. A0 is the initial extension of an interior knee angle, which "
                        + "cannot exceed 180 deg at all, and the largest value measured across this "
                        + "corpus is 89.8 deg. A number this size means the reconstruction failed, "
                        + "not that the leg swung far -- check the trial's anatomical reference "
                        + "before reading anything into the score.";
                else
                    r.Reason = "Insufficient excursion: the leg moved " + Fmt(IsFinite(a0) ? a0 : double.NaN, 1) + " deg, below the " + Fmt(MIN_INTERPRETABLE_A0_DEG, 0) + " deg "
                        + "floor for interpreting PT7 (control mean " + Fmt(EXCURSION_REF_MEAN_DEG, 1) + ", sd " + Fmt(EXCURSION_REF_SD_DEG, 1) + ", n="
                        + EXCURSION_REF_N.ToString(CultureInfo.InvariantCulture) + "). "
                        + "PT7's parameters are ratios normalised on the swing, so they stop tracking "
                        + "severity once the swing collapses -- a stiff, high-tone leg and a poorly "
                        + "positioned trial both swing too little to grade. Repeat the trial and check "
                        + "positioning, release and sensor placement; if a full lift still swings this "
                        + "little, record the reduced excursion and assess tone clinically.";
                r.Interpretable = false; r.Mas = null; r.Zone = null; r.Refused = r.Reason;
                return;
            }
            if (!r.OscillationOk)
            {
                double a0 = r.A0_deg;
                r.Reason = "No measurable oscillation: the leg moved " + Fmt(IsFinite(a0) ? a0 : double.NaN, 1) + " deg and then stopped, "
                    + "with no first swing to measure (A1 = 0, N = 0). Five of PT7's parameters "
                    + "are properties OF the oscillation -- the relaxation index, the cycle "
                    + "count, the frequency and the return ratio -- so when there is none they "
                    + "are not small, they are undefined. Scoring them as zero makes a limb that "
                    + "never swung look maximally spastic by arithmetic rather than by "
                    + "measurement. A leg that drops and holds is a real and severe clinical "
                    + "finding; it is just not a finding PT7 can grade. Record it as an arrest "
                    + "and note the angle.";
                r.Interpretable = false; r.Mas = null; r.Zone = null; r.Refused = r.Reason;
                return;
            }
            r.Interpretable = true;
            r.Mas = PtToMas(score);
            r.Zone = PtZoneOf(score);
            r.Reason = "";
            r.Refused = null;
        }

        static double ParamOf(PtResult r, string k)
        {
            switch (k)
            {
                case "R2n": return r.R2n;
                case "N": return r.N;
                case "phi_max_ratio": return r.phi_max_ratio;
                case "omega_max_n": return r.omega_max_n;
                case "omega_min_n": return r.omega_min_n;
                case "f": return r.f;
                case "area_ratio": return r.area_ratio;
            }
            return 0.0;
        }

        /// <summary>compute_pt_score_breakdown against HEALTHY_REF, ordered as ScoredKeys.</summary>
        public static double[] ComputePtScoreBreakdown(PtResult r)
        {
            const double DENOM_FLOOR = 0.1;
            int nParams = ScoredKeys.Length;
            // 90776a5: divide by the terms this trial can score, not a fixed count.
            // Mirrors compute_pt_score_breakdown's _scorable: a zero reference, or an
            // uncomputable f (f < 0.1 or N < 2), leaves the divisor.
            int nScored = 0;
            for (int j = 0; j < nParams; j++)
            {
                string k = ScoredKeys[j];
                double ph;
                if (!HEALTHY_REF.TryGetValue(k, out ph) || ph == 0) continue;
                if (k == "f" && (ParamOf(r, k) < 0.1 || r.N < 2.0)) continue;
                nScored++;
            }
            if (nScored == 0) nScored = nParams;
            double[] o = new double[nParams];
            for (int j = 0; j < nParams; j++)
            {
                string k = ScoredKeys[j];
                double pij = ParamOf(r, k);
                double phj;
                if (!HEALTHY_REF.TryGetValue(k, out phj)) phj = 0.0;
                if (phj == 0) { o[j] = 0.0; continue; }
                double delta = pij - phj;
                double denom = nScored * PyMax(Math.Abs(phj), DENOM_FLOOR);
                double dev;
                if (k == "N" || k == "R2n" || k == "phi_max_ratio" || k == "omega_max_n")
                    dev = PyMax(0.0, -delta) / denom;
                else if (k == "area_ratio" || k == "omega_min_n")
                    dev = PyMax(0.0, delta) / denom;
                else
                {
                    if (pij < 0.1 || r.N < 2.0) dev = 0.0;
                    else dev = Math.Abs(delta) / denom;
                }
                o[j] = dev;
            }
            return o;
        }

        public static string PtToMas(double pt)
        {
            for (int i = 0; i < MAS_T.Length; i++) if (pt <= MAS_T[i]) return MAS_L[i];
            return "4";
        }

        public static string PtZoneOf(double? score)
        {
            if (!score.HasValue || !IsFinite(score.Value)) return "unknown";
            double s = score.Value;
            if (s < PT_HEALTHY_MAX) return "healthy";
            if (s > PT_BORDERLINE_MAX) return "impaired";
            return "borderline";
        }

        public static bool OscillationOk(PtResult r)
        {
            if (r == null || !r.HasParams) return false;
            double a1 = r.A1_deg;
            if (!IsFinite(a1)) return false;
            return a1 > 0.0;
        }

        public static bool ExcursionOk(PtResult r)
        {
            if (r == null || !r.HasParams) return false;
            double a0 = r.A0_deg;
            if (!IsFinite(a0)) return false;
            return MIN_INTERPRETABLE_A0_DEG <= a0 && a0 <= MAX_INTERPRETABLE_A0_DEG;
        }

        // ═════════════════════════════════════════════════════════════════════
        // numpy / scipy equivalents
        // ═════════════════════════════════════════════════════════════════════

        /// <summary>np.arange(start, stop, step) for float64: length ceil((stop-start)/step),
        /// buf[0]=start, buf[1]=start+step, buf[i]=start+i*(buf[1]-buf[0]).</summary>
        public static double[] Arange(double start, double stop, double step)
        {
            double tmp = (stop - start) / step;
            if (double.IsNaN(tmp)) throw new ArgumentException("arange: NaN length");
            long len;
            if (tmp == 0.0 && (stop - start) != 0.0) len = (tmp < 0 || (1.0 / tmp) < 0) ? 0 : 1;
            else len = (long)Math.Ceiling(tmp);
            if (len <= 0) return new double[0];
            double[] b = new double[len];
            b[0] = start;
            if (len == 1) return b;
            b[1] = start + step;
            double delta = b[1] - b[0];
            for (long i = 2; i < len; i++) b[i] = start + i * delta;
            return b;
        }

        /// <summary>np.interp(x, xp, fp) with numpy's compiled_base semantics.</summary>
        public static double[] Interp(double[] x, double[] xp, double[] fp)
        {
            int n = xp.Length;
            double[] o = new double[x.Length];
            double lval = fp[0], rval = fp[n - 1];
            if (n == 1)
            {
                double xv0 = xp[0], fv0 = fp[0];
                for (int i = 0; i < x.Length; i++)
                {
                    double xv = x[i];
                    o[i] = (xv < xv0) ? lval : ((xv > xv0) ? rval : fv0);
                }
                return o;
            }
            for (int i = 0; i < x.Length; i++)
            {
                double xv = x[i];
                if (double.IsNaN(xv)) { o[i] = xv; continue; }
                int j = BinarySearchRight(xv, xp);
                if (j == -1) o[i] = lval;
                else if (j == n) o[i] = rval;
                else if (j == n - 1) o[i] = fp[j];
                else if (xp[j] == xv) o[i] = fp[j];
                else
                {
                    double slope = (fp[j + 1] - fp[j]) / (xp[j + 1] - xp[j]);
                    double v = slope * (xv - xp[j]) + fp[j];
                    if (double.IsNaN(v))
                    {
                        v = slope * (xv - xp[j + 1]) + fp[j + 1];
                        if (double.IsNaN(v) && fp[j] == fp[j + 1]) v = fp[j];
                    }
                    o[i] = v;
                }
            }
            return o;
        }

        // numpy binary_search_with_guess result: -1 if key < arr[0]; len if key > arr[last];
        // else the largest j with arr[j] <= key.
        static int BinarySearchRight(double key, double[] arr)
        {
            int len = arr.Length;
            if (key > arr[len - 1]) return len;
            if (key < arr[0]) return -1;
            int lo = 0, hi = len;   // find first index with arr[idx] > key
            while (lo < hi)
            {
                int mid = lo + ((hi - lo) >> 1);
                if (arr[mid] <= key) lo = mid + 1; else hi = mid;
            }
            return lo - 1;
        }

        /// <summary>np.searchsorted(a, v, side='left').</summary>
        public static int SearchSortedLeft(double[] a, double v)
        {
            int lo = 0, hi = a.Length;
            while (lo < hi)
            {
                int mid = lo + ((hi - lo) >> 1);
                if (a[mid] < v) lo = mid + 1; else hi = mid;
            }
            return lo;
        }

        static int SearchSortedLeftInt(List<int> a, int v)
        {
            int lo = 0, hi = a.Count;
            while (lo < hi)
            {
                int mid = lo + ((hi - lo) >> 1);
                if (a[mid] < v) lo = mid + 1; else hi = mid;
            }
            return lo;
        }

        static double MedianDt(double[] t)
        {
            if (t.Length < 2) return 1.0 / 30.0;
            double dt = Median(Diff(t));
            return dt > 0 ? dt : 1.0 / 30.0;
        }

        /// <summary>_sg: Savitzky-Golay over win_s seconds; scipy savgol_filter(mode='interp').</summary>
        static double[] Sg(double[] sig, double dt, double win_s, int p)
        {
            int n = sig.Length;
            int w = PyInt(PyRound(win_s / dt));
            if (w % 2 == 0) w += 1;
            if (w < p + 2) w = ((p + 2) % 2 == 1) ? p + 2 : p + 3;
            w = Math.Min(w, (n % 2 == 0) ? n - 1 : n);
            w = (w % 2 == 1) ? w : w - 1;   // Python % : w may be negative only for n < 2
            if (w >= p + 2) return SavgolInterp(sig, w, p);
            return (double[])sig.Clone();
        }

        static readonly Dictionary<long, double[,]> _hatCache = new Dictionary<long, double[,]>();

        /// <summary>Hat matrix H = Q Q^T of the w x (p+1) Vandermonde on positions 0..w-1:
        /// the least-squares polynomial fit evaluated back on the window. Row w/2 is the
        /// savgol_coeffs convolution kernel (deriv 0); the edge rows reproduce _fit_edge.</summary>
        static double[,] HatMatrix(int w, int p)
        {
            long key = (long)w * 256 + p;
            double[,] H;
            lock (_hatCache)
            {
                if (_hatCache.TryGetValue(key, out H)) return H;
            }
            int m = p + 1;
            int h = w / 2;
            double[][] q = new double[m][];
            for (int j = 0; j < m; j++)
            {
                double[] v = new double[w];
                for (int k = 0; k < w; k++) v[k] = Math.Pow(k - h, j);   // centred for conditioning
                // modified Gram-Schmidt, two passes
                for (int pass = 0; pass < 2; pass++)
                    for (int i = 0; i < j; i++)
                    {
                        double d = 0.0;
                        for (int k = 0; k < w; k++) d += q[i][k] * v[k];
                        for (int k = 0; k < w; k++) v[k] -= d * q[i][k];
                    }
                double nrm = 0.0;
                for (int k = 0; k < w; k++) nrm += v[k] * v[k];
                nrm = Math.Sqrt(nrm);
                for (int k = 0; k < w; k++) v[k] /= nrm;
                q[j] = v;
            }
            H = new double[w, w];
            for (int a = 0; a < w; a++)
                for (int b = 0; b < w; b++)
                {
                    double s = 0.0;
                    for (int j = 0; j < m; j++) s += q[j][a] * q[j][b];
                    H[a, b] = s;
                }
            // the kernel row is symmetric in exact arithmetic; make it exactly so
            for (int k = 1; k <= h; k++)
            {
                double avg = 0.5 * (H[h, h - k] + H[h, h + k]);
                H[h, h - k] = avg; H[h, h + k] = avg;
            }
            lock (_hatCache) { _hatCache[key] = H; }
            return H;
        }

        static double[] SavgolInterp(double[] x, int w, int p)
        {
            int n = x.Length;
            if (w > n) throw new ArgumentException("savgol: window_length > len(x)");
            double[,] H = HatMatrix(w, p);
            int h = w / 2;
            double[] y = new double[n];
            // interior: scipy correlate1d symmetric form, centre term first, then outer -> inner pairs
            double c0 = H[h, h];
            for (int i = h; i < n - h; i++)
            {
                double s = x[i] * c0;
                for (int j = h; j >= 1; j--) s += (x[i - j] + x[i + j]) * H[h, h - j];
                y[i] = s;
            }
            // edges: polynomial fitted to the first / last w samples, evaluated on the first / last h
            for (int i = 0; i < h; i++)
            {
                double s = 0.0;
                for (int k = 0; k < w; k++) s += H[i, k] * x[k];
                y[i] = s;
            }
            int ws = n - w;
            for (int i = n - h; i < n; i++)
            {
                int row = i - ws;
                double s = 0.0;
                for (int k = 0; k < w; k++) s += H[row, k] * x[ws + k];
                y[i] = s;
            }
            return y;
        }

        /// <summary>np.gradient(f, x), edge_order=1, with numpy's constant-spacing shortcut.</summary>
        static double[] Gradient(double[] fv, double[] x)
        {
            int n = fv.Length;
            double[] o = new double[n];
            double[] dx = Diff(x);
            bool uniform = true;
            for (int i = 1; i < dx.Length; i++) if (!(dx[i] == dx[0])) { uniform = false; break; }
            if (uniform)
            {
                double d = dx[0];
                for (int i = 1; i < n - 1; i++) o[i] = (fv[i + 1] - fv[i - 1]) / (2.0 * d);
                o[0] = (fv[1] - fv[0]) / d;
                o[n - 1] = (fv[n - 1] - fv[n - 2]) / d;
            }
            else
            {
                for (int i = 1; i < n - 1; i++)
                {
                    double dx1 = dx[i - 1], dx2 = dx[i];
                    double a = -(dx2) / (dx1 * (dx1 + dx2));
                    double b = (dx2 - dx1) / (dx1 * dx2);
                    double c = dx1 / (dx2 * (dx1 + dx2));
                    o[i] = a * fv[i - 1] + b * fv[i] + c * fv[i + 1];
                }
                o[0] = (fv[1] - fv[0]) / dx[0];
                o[n - 1] = (fv[n - 1] - fv[n - 2]) / dx[dx.Length - 1];
            }
            return o;
        }

        /// <summary>scipy.signal.find_peaks(x, height=, distance=, prominence=) -- minimum bounds only.</summary>
        public static int[] FindPeaks(double[] x, double height, int distance, double prominence)
        {
            int n = x.Length;
            // _local_maxima_1d (plateau midpoints)
            List<int> peaks = new List<int>();
            int i = 1, iMax = n - 1;
            while (i < iMax)
            {
                if (x[i - 1] < x[i])
                {
                    int ia = i + 1;
                    while (ia < iMax && x[ia] == x[i]) ia++;
                    if (x[ia] < x[i])
                    {
                        peaks.Add((i + ia - 1) / 2);
                        i = ia;
                    }
                }
                i++;
            }
            // height
            List<int> p1 = new List<int>();
            foreach (int pk in peaks) if (height <= x[pk]) p1.Add(pk);
            // distance (_select_by_peak_distance)
            int ps = p1.Count;
            int dist = distance;   // ceil(int) == int
            bool[] keep = new bool[ps];
            for (int k = 0; k < ps; k++) keep[k] = true;
            int[] order = new int[ps];
            for (int k = 0; k < ps; k++) order[k] = k;
            // np.argsort(priority) ascending; stable on ties (see report)
            double[] pri = new double[ps];
            for (int k = 0; k < ps; k++) pri[k] = x[p1[k]];
            StableArgsort(pri, order);
            for (int ii = ps - 1; ii >= 0; ii--)
            {
                int j = order[ii];
                if (!keep[j]) continue;
                int k = j - 1;
                while (0 <= k && p1[j] - p1[k] < dist) { keep[k] = false; k--; }
                k = j + 1;
                while (k < ps && p1[k] - p1[j] < dist) { keep[k] = false; k++; }
            }
            List<int> p2 = new List<int>();
            for (int k = 0; k < ps; k++) if (keep[k]) p2.Add(p1[k]);
            // prominence (_peak_prominences, wlen=None)
            List<int> p3 = new List<int>();
            foreach (int pk in p2)
            {
                double xp = x[pk];
                double leftMin = xp;
                int ii = pk;
                while (0 <= ii && x[ii] <= xp) { if (x[ii] < leftMin) leftMin = x[ii]; ii--; }
                double rightMin = xp;
                ii = pk;
                while (ii <= n - 1 && x[ii] <= xp) { if (x[ii] < rightMin) rightMin = x[ii]; ii++; }
                double prom = xp - Math.Max(leftMin, rightMin);
                if (prominence <= prom) p3.Add(pk);
            }
            return p3.ToArray();
        }

        static void StableArgsort(double[] key, int[] order)
        {
            // merge sort on (key, index); NaN sorts last like numpy
            int n = order.Length;
            if (n < 2) return;
            int[] tmp = new int[n];
            MergeSort(key, order, tmp, 0, n);
        }

        static bool Less(double a, double b)
        {
            if (double.IsNaN(a)) return false;
            if (double.IsNaN(b)) return true;
            return a < b;
        }

        static void MergeSort(double[] key, int[] a, int[] tmp, int lo, int hi)
        {
            if (hi - lo < 2) return;
            int mid = (lo + hi) / 2;
            MergeSort(key, a, tmp, lo, mid);
            MergeSort(key, a, tmp, mid, hi);
            int i = lo, j = mid, k = lo;
            while (i < mid && j < hi)
            {
                if (Less(key[a[j]], key[a[i]])) tmp[k++] = a[j++]; else tmp[k++] = a[i++];
            }
            while (i < mid) tmp[k++] = a[i++];
            while (j < hi) tmp[k++] = a[j++];
            for (k = lo; k < hi; k++) a[k] = tmp[k];
        }

        // ---- reductions ----

        /// <summary>np.median (NaN in -> NaN out); mean of the two middle values for even n.</summary>
        public static double Median(double[] a)
        {
            int n = a.Length;
            if (n == 0) return double.NaN;
            for (int i = 0; i < n; i++) if (double.IsNaN(a[i])) return double.NaN;
            double[] s = (double[])a.Clone();
            Array.Sort(s);
            if (n % 2 == 1) return s[n / 2];
            return (s[n / 2 - 1] + s[n / 2]) / 2.0;
        }

        public static double NanMedian(double[] a)
        {
            List<double> v = new List<double>(a.Length);
            for (int i = 0; i < a.Length; i++) if (!double.IsNaN(a[i])) v.Add(a[i]);
            if (v.Count == 0) return double.NaN;
            return Median(v.ToArray());
        }

        static double NanMax(double[] a)
        {
            bool any = false; double m = double.NaN;
            for (int i = 0; i < a.Length; i++)
            {
                if (double.IsNaN(a[i])) continue;
                if (!any || a[i] > m) { m = a[i]; any = true; }
            }
            return m;
        }

        static double NanMin(double[] a)
        {
            bool any = false; double m = double.NaN;
            for (int i = 0; i < a.Length; i++)
            {
                if (double.IsNaN(a[i])) continue;
                if (!any || a[i] < m) { m = a[i]; any = true; }
            }
            return m;
        }

        /// <summary>numpy's float64 add.reduce: 0.0 + pairwise_sum (blocks of 8, PW_BLOCKSIZE 128).</summary>
        public static double PairwiseSum(double[] a)
        {
            return 0.0 + Pairwise(a, 0, a.Length);
        }

        static double Pairwise(double[] a, int lo, int n)
        {
            if (n < 8)
            {
                double res = 0.0;
                for (int i = 0; i < n; i++) res += a[lo + i];
                return res;
            }
            else if (n <= 128)
            {
                double r0 = a[lo], r1 = a[lo + 1], r2 = a[lo + 2], r3 = a[lo + 3];
                double r4 = a[lo + 4], r5 = a[lo + 5], r6 = a[lo + 6], r7 = a[lo + 7];
                int i;
                int lim = n - (n % 8);
                for (i = 8; i < lim; i += 8)
                {
                    r0 += a[lo + i]; r1 += a[lo + i + 1]; r2 += a[lo + i + 2]; r3 += a[lo + i + 3];
                    r4 += a[lo + i + 4]; r5 += a[lo + i + 5]; r6 += a[lo + i + 6]; r7 += a[lo + i + 7];
                }
                double res = ((r0 + r1) + (r2 + r3)) + ((r4 + r5) + (r6 + r7));
                for (; i < n; i++) res += a[lo + i];
                return res;
            }
            else
            {
                int n2 = n / 2;
                n2 -= n2 % 8;
                return Pairwise(a, lo, n2) + Pairwise(a, lo + n2, n - n2);
            }
        }

        static double Mean(double[] a)
        {
            return PairwiseSum(a) / a.Length;
        }

        // ---- small helpers ----

        static double[] Diff(double[] a)
        {
            if (a.Length < 2) return new double[0];
            double[] d = new double[a.Length - 1];
            for (int i = 0; i < d.Length; i++) d[i] = a[i + 1] - a[i];
            return d;
        }

        static double[] Slice(double[] a, int start, int stop)
        {
            if (start < 0) start = 0;
            if (stop > a.Length) stop = a.Length;
            if (stop < start) stop = start;
            double[] o = new double[stop - start];
            Array.Copy(a, start, o, 0, o.Length);
            return o;
        }

        static double[] Negate(double[] a)
        {
            double[] o = new double[a.Length];
            for (int i = 0; i < a.Length; i++) o[i] = -a[i];
            return o;
        }

        static int[] SortedConcat(int[] a, int[] b)
        {
            int[] o = new int[a.Length + b.Length];
            Array.Copy(a, 0, o, 0, a.Length);
            Array.Copy(b, 0, o, a.Length, b.Length);
            Array.Sort(o);
            return o;
        }

        static bool IsFinite(double v) { return !double.IsNaN(v) && !double.IsInfinity(v); }

        /// <summary>Python max(a, b) for floats: returns a unless b &gt; a.</summary>
        static double PyMax(double a, double b) { return (b > a) ? b : a; }

        /// <summary>np.maximum(a, b): NaN propagates from a.</summary>
        static double NpMaximum(double a, double b) { return (a >= b || double.IsNaN(a)) ? a : b; }

        /// <summary>Python round(x) -> nearest, ties to even.</summary>
        static double PyRound(double x) { return Math.Round(x, MidpointRounding.ToEven); }

        /// <summary>Python int(x): truncate toward zero.</summary>
        static int PyInt(double x)
        {
            if (double.IsNaN(x) || double.IsInfinity(x)) throw new ArgumentException("int() of non-finite");
            return (int)Math.Truncate(x);
        }

        /// <summary>Python format(x, '.Nf'): exact binary value, round half to even. N in {0,1}.</summary>
        public static string Fmt(double x, int decimals)
        {
            if (double.IsNaN(x)) return "nan";
            if (double.IsInfinity(x)) return x > 0 ? "inf" : "-inf";
            bool neg = x < 0 || (x == 0 && 1.0 / x < 0);
            double ax = Math.Abs(x);
            long bits = BitConverter.DoubleToInt64Bits(ax);
            int exp = (int)((bits >> 52) & 0x7FF);
            long mant = bits & 0xFFFFFFFFFFFFFL;
            if (exp == 0) exp = 1; else mant |= 1L << 52;
            int e2 = exp - 1075;                  // ax = mant * 2^e2
            long scale = 1;
            for (int i = 0; i < decimals; i++) scale *= 10;
            long q;                                 // round_half_even(ax * 10^decimals)
            if (e2 >= 0)
            {
                if (e2 > 8 || mant > (long.MaxValue >> (e2 + 4)) / scale)
                    return ax.ToString("F" + decimals.ToString(CultureInfo.InvariantCulture), CultureInfo.InvariantCulture); // out of range for exact path
                q = (mant << e2) * scale;
            }
            else
            {
                int sh = -e2;
                long num = mant * scale;           // < 2^53 * 10 fits
                if (sh >= 62) q = 0;               // value * scale < 2^57 / 2^62 < 0.5 -> 0 (x small)
                else
                {
                    long ip = num >> sh;
                    long rem = num - (ip << sh);
                    long half = 1L << (sh - 1);
                    if (rem > half || (rem == half && (ip & 1) == 1)) ip++;
                    q = ip;
                }
            }
            string digits = q.ToString(CultureInfo.InvariantCulture);
            string s;
            if (decimals == 0) s = digits;
            else
            {
                while (digits.Length <= decimals) digits = "0" + digits;
                s = digits.Substring(0, digits.Length - decimals) + "." + digits.Substring(digits.Length - decimals);
            }
            return neg ? "-" + s : s;
        }
    }
}
