// Working backwards: from a MAS grade, or from target Popovic parameters, to model knobs.
// Every candidate is scored by the C# port of the production scorer (Scoring.Score), so what the
// designer reports is what the scorer says about the movement it will actually play.
using System;
using System.Collections.Generic;

namespace Pendulastic
{
    public class DesignResult
    {
        public Knobs Knobs; public Movement Movement; public Metrics Metrics;
        public string Note = "";
    }

    public static class Designer
    {
        // PT7 the emulator aims at for each grade: neuromech/scenarios.py MAS_TARGETS, plus 4 from the far
        // side of Popovic's 0.78 boundary. These are PT7-BAND presets, not clinical MAS (plan D5).
        public static readonly string[] Grades = { "0", "1", "1+", "2", "3", "4" };
        public static double Pt7Target(string grade)
        {
            switch (grade)
            {
                case "0": return 0.06; case "1": return 0.20; case "1+": return 0.36;
                case "2": return 0.52; case "3": return 0.69; case "4": return 0.85;
            }
            throw new ArgumentException("unknown grade " + grade);
        }

        // what a "unit" of error is for each parameter when matching several at once
        public static readonly Dictionary<string, double> Unit = new Dictionary<string, double>
        {
            { "R2n", 0.1 }, { "N", 0.5 }, { "f", 0.05 }, { "area_ratio", 0.02 },
            { "phi_max_ratio", 0.05 }, { "omega_max_n", 0.5 }, { "omega_min_n", 0.5 }, { "pt7", 0.02 }
        };

        /// <summary>One severity axis through the knobs: 0 = healthy-like, 1 = stiff, braked, toned.</summary>
        public static Knobs Severity(Knobs b, double s)
        {
            var k = b.Clone();
            k.Damping = 0.05 + 0.55 * s;
            k.ReflexGain = 45.0 * s;
            k.ReflexThresholdDps = 60.0 * (1 - 0.6 * s);
            k.ToneDeg = 18.0 * s;
            return k;
        }

        public static DesignResult Evaluate(Knobs k)
        {
            var m = PendulumModel.Make(k);
            return new DesignResult { Knobs = k, Movement = m, Metrics = Scoring.Score(m) };
        }

        // ── the published severity axis (De Groote 2018), by lambda ──────────────────────────────────────
        static double peakLam = double.NaN, peakPt7 = double.NaN;

        static Knobs AxisKnobs(double lam, double scale)
        {
            return new Knobs { Lambda = lam, Scale = scale, SwingS = DeGroote.DurationS };
        }

        /// <summary>PT7 the scorer gives the axis at this lambda played 1:1: neuromech.build.pt7_at.</summary>
        public static double? Pt7At(double lam) { return Evaluate(AxisKnobs(lam, 1.0)).Metrics.Pt7; }

        /// <summary>(lambda, PT7) where the axis stops rising: neuromech.build.rising_branch_peak (0 to 2 in 0.05
        /// steps, 1:1). Measured, not hard-coded: it moves whenever the scorer's reference constants move.</summary>
        public static void RisingBranchPeak(out double lam, out double pt7)
        {
            if (double.IsNaN(peakLam))
            {
                double bl = 0.0, bv = Pt7At(0.0) ?? 0.0;
                for (double l = 0.05; l <= 2.0 + 1e-9; l += 0.05)
                {
                    double? v = Pt7At(l);
                    if (v.HasValue && v.Value > bv) { bl = l; bv = v.Value; }
                }
                peakLam = bl; peakPt7 = bv;
            }
            lam = peakLam; pt7 = peakPt7;
        }

        /// <summary>The published severity axis at lambda, at the basis' rig amplitude scale. The other knobs are
        /// not used. The note says which side of the PT7 peak it is on and what the 1:1 library swing scores.</summary>
        public static DesignResult ForLambda(double lam, Knobs basis)
        {
            var inv = System.Globalization.CultureInfo.InvariantCulture;
            // the basis' other knobs ride along unused, so a scale-down or a reload shows them unchanged
            Knobs k = basis.Clone(); k.Lambda = lam; k.SwingS = DeGroote.DurationS;
            var res = Evaluate(k);
            double pl, pp; RisingBranchPeak(out pl, out pp);
            res.Note = string.Format(inv, "Published severity axis (De Groote 2018), lambda {0:F2}: tone {1:F2} N.m, k_f {2:F3}, k_df {3:F4}.",
                                     lam, 2.0 * lam, 0.75 * lam, 0.025 * lam);
            res.Note += lam > pl + 1e-9
                ? string.Format(inv, " PAST THE PT7 PEAK (PT7 {0:F3} at lambda {1:F2}): the falling branch. This leg is stiffer than the peak's and the score grades it milder.", pp, pl)
                : string.Format(inv, " On the rising branch (PT7 peaks at {0:F3}, lambda {1:F2}).", pp, pl);
            if (Math.Abs(basis.Scale - 1.0) > 1e-9)
            {
                var one = Evaluate(AxisKnobs(lam, 1.0)).Metrics;
                res.Note += string.Format(inv, " Scored here at rig amplitude scale {0:F2}; played 1:1 it scores PT7 {1} (the library's expected value).",
                                          basis.Scale, one.Pt7.HasValue ? one.Pt7.Value.ToString("F3", inv) : "-");
            }
            res.Note += " Only the rig amplitude scale knob applies; the other knobs are not used.";
            return res;
        }

        public static DesignResult ForGrade(string grade, Knobs basis)
        {
            double target = Pt7Target(grade);
            // the score is not monotonic along any one axis (a stiff enough leg stops swinging and is
            // refused), so scan first and refine around the best interpretable point
            int nScan = 31; double bestS = -1, bestErr = double.MaxValue;
            var errs = new double[nScan];
            for (int i = 0; i < nScan; i++)
            {
                double s = i / (double)(nScan - 1);
                var r = Evaluate(Severity(basis, s));
                errs[i] = r.Metrics.Interpretable && r.Metrics.Pt7.HasValue ? Math.Abs(r.Metrics.Pt7.Value - target) : double.MaxValue;
                if (errs[i] < bestErr) { bestErr = errs[i]; bestS = s; }
            }
            if (bestS < 0) return new DesignResult { Note = "No point on the severity axis scores as interpretable." };
            double lo = Math.Max(0, bestS - 1.0 / (nScan - 1)), hi = Math.Min(1, bestS + 1.0 / (nScan - 1));
            for (int it = 0; it < 30; it++)        // golden-section on |PT7 - target|
            {
                double a = hi - 0.618 * (hi - lo), b = lo + 0.618 * (hi - lo);
                if (Pt7Err(basis, a, target) < Pt7Err(basis, b, target)) hi = b; else lo = a;
            }
            var res = Evaluate(Severity(basis, 0.5 * (lo + hi)));
            if (!res.Metrics.Interpretable || !res.Metrics.Pt7.HasValue || Pt7Err(basis, 0.5 * (lo + hi), target) > bestErr)
                res = Evaluate(Severity(basis, bestS));
            double got = res.Metrics.Pt7.HasValue ? res.Metrics.Pt7.Value : double.NaN;
            if (!(Math.Abs(got - target) <= 0.01))
            {
                // the one-knob axis has a cliff: once fewer than ~1.5 swings remain the scorer cannot measure f,
                // and PT7 jumps from ~0.66 to ~1.35. Above that, search all four knobs for the score directly.
                // Start from more than the axis's best point: on the 5-term scorer the MAS 3 band is reached
                // only near damping 0.35 with no reflex, off the axis, and a search started on it stalls at 0.57.
                var starts = new List<Knobs> { res.Knobs };
                foreach (double d in new[] { 0.25, 0.35, 0.45 })
                {
                    var s = basis.Clone(); s.Damping = d; s.ReflexGain = 0.0; starts.Add(s);
                }
                foreach (var start in starts)
                {
                    var alt = ForParams(new Dictionary<string, double> { { "pt7", target } }, start);
                    double ga = alt.Metrics.Pt7.HasValue ? alt.Metrics.Pt7.Value : double.NaN;
                    if (alt.Metrics.Interpretable && Math.Abs(ga - target) < Math.Abs(got - target)) { res = alt; got = ga; }
                }
            }
            res.Note = string.Format(System.Globalization.CultureInfo.InvariantCulture,
                "Aimed at PT7 {0:F2} (MAS {1} preset); got {2:F3}.", target, grade, got);
            if (Math.Abs(got - target) > 0.03)
                res.Note += " This model cannot get closer from here; try other frequency/depth settings or Parameters mode.";
            return res;
        }

        static double Pt7Err(Knobs b, double s, double target)
        {
            var r = Evaluate(Severity(b, s));
            return r.Metrics.Interpretable && r.Metrics.Pt7.HasValue ? Math.Abs(r.Metrics.Pt7.Value - target) : double.MaxValue;
        }

        // ── several targets at once: Nelder-Mead over (frequency, damping, reflex gain, tone) ──
        static Knobs FromVec(Knobs b, double[] u)
        {
            var k = b.Clone();
            k.FreqHz = Clamp(Math.Exp(u[0]), 0.3, 4.0);
            k.Damping = Clamp(u[1], 0.0, 0.95);
            k.ReflexGain = Clamp(u[2], 0.0, 150.0);
            k.ToneDeg = Clamp(u[3], 0.0, 40.0);
            return k;
        }

        static double[] ToVec(Knobs k) { return new double[] { Math.Log(k.FreqHz), k.Damping, k.ReflexGain, k.ToneDeg }; }
        static double Clamp(double v, double lo, double hi) { return v < lo ? lo : (v > hi ? hi : v); }

        public static double Cost(Metrics m, Dictionary<string, double> targets)
        {
            if (m == null || !m.Scored) return 1e6;
            double c = 0;
            foreach (var kv in targets)
            {
                double v;
                if (kv.Key == "pt7") { if (!m.Pt7.HasValue) return 1e6; v = m.Pt7.Value; }
                else if (!m.Values.TryGetValue(kv.Key, out v) || double.IsNaN(v)) return 1e6;
                double e = (v - kv.Value) / Unit[kv.Key];
                c += e * e;
            }
            if (!m.Interpretable) c += 25;     // prefer movements the scorer will grade
            return c;
        }

        public static DesignResult ForParams(Dictionary<string, double> targets, Knobs basis)
        {
            if (targets.Count == 0) return new DesignResult { Note = "Tick at least one parameter to aim at." };
            DesignResult best = null; double bestC = double.MaxValue;
            // a few starts along the severity axis, then simplex from the best of them
            var starts = new List<Knobs> { basis };
            foreach (double s in new double[] { 0.0, 0.05, 0.1, 0.15, 0.2 }) starts.Add(Severity(basis, s));
            foreach (double tone in new double[] { 8.0, 16.0 })        // the toned side of the space, which the axis skips
                foreach (double z in new double[] { 0.15, 0.3 })
                {
                    var k = basis.Clone(); k.ToneDeg = tone; k.Damping = z; k.ReflexGain = 2; starts.Add(k);
                }
            foreach (var k in starts)
            {
                var r = Evaluate(k);
                double c = Cost(r.Metrics, targets);
                if (c < bestC) { bestC = c; best = r; }
            }
            double[] x = NelderMead(u => Cost(Evaluate(FromVec(basis, u)).Metrics, targets), ToVec(best.Knobs),
                                    new double[] { 0.25, 0.1, 15.0, 5.0 }, 160);
            var fin = Evaluate(FromVec(basis, x));
            if (Cost(fin.Metrics, targets) > bestC) fin = best;
            var parts = new List<string>();
            foreach (var kv in targets)
            {
                double v = double.NaN;
                if (kv.Key == "pt7") { if (fin.Metrics.Pt7.HasValue) v = fin.Metrics.Pt7.Value; }
                else fin.Metrics.Values.TryGetValue(kv.Key, out v);
                parts.Add(string.Format(System.Globalization.CultureInfo.InvariantCulture, "{0} {1:G4} (aim {2:G4})", kv.Key, v, kv.Value));
            }
            fin.Note = "Closest found: " + string.Join(", ", parts.ToArray()) + ".";
            if (Cost(fin.Metrics, targets) > targets.Count)
                fin.Note += " Not all targets can be met together by this model; the ones furthest off conflict with the rest.";
            return fin;
        }

        /// <summary>For a live design over the playback limit: the largest rig amplitude scale (0.01 steps, from the
        /// guard's own estimate down to minScale) at which the movement passes every RigGuard rule with its predicted
        /// delivered peak at least RigGuard.ScaleMarginDeg under the limit, RESCORED at that scale (only the swing
        /// scales; the 5 deg sync pulses do not). Null if no scale does, or the design has no knobs (a file).</summary>
        public static DesignResult ScaledToLimit(DesignResult d, double limit, double minScale)
        {
            if (d == null || d.Knobs == null || d.Movement == null) return null;
            GuardReport g = RigGuard.Check(d.Movement.RigT, d.Movement.RigDeg, limit);
            if (!g.Ok && !g.Scalable) return null;
            double s0 = d.Knobs.Scale, target = limit - RigGuard.ScaleMarginDeg, s = s0;
            if (g.PredictedPeakDeg > 0) s = Math.Min(s, s0 * target / g.PredictedPeakDeg);
            if (!double.IsNaN(g.ScaleToFit)) s = Math.Min(s, s0 * g.ScaleToFit);
            s = Math.Floor(s * 100.0 + 1e-9) / 100.0;
            for (; s >= minScale - 1e-9; s = Math.Round(s - 0.01, 2))
            {
                Knobs k = d.Knobs.Clone(); k.Scale = s;
                DesignResult r = Evaluate(k);
                GuardReport sg = RigGuard.Check(r.Movement.RigT, r.Movement.RigDeg, limit);
                if (sg.Ok && sg.PredictedPeakDeg <= target + 1e-9) return r;
            }
            return null;
        }

        public delegate double Fn(double[] u);

        public static double[] NelderMead(Fn f, double[] x0, double[] step, int maxEval)
        {
            int n = x0.Length; var p = new double[n + 1][]; var fv = new double[n + 1];
            for (int i = 0; i <= n; i++)
            {
                p[i] = (double[])x0.Clone();
                if (i > 0) p[i][i - 1] += step[i - 1];
                fv[i] = f(p[i]);
            }
            int evals = n + 1;
            while (evals < maxEval)
            {
                Array.Sort(fv, p);
                if (Math.Abs(fv[n] - fv[0]) < 1e-6) break;
                var c = new double[n];
                for (int i = 0; i < n; i++) for (int j = 0; j < n; j++) c[j] += p[i][j] / n;
                double[] xr = Lerp(c, p[n], -1.0); double fr = f(xr); evals++;
                if (fr < fv[0])
                {
                    double[] xe = Lerp(c, p[n], -2.0); double fe = f(xe); evals++;
                    if (fe < fr) { p[n] = xe; fv[n] = fe; } else { p[n] = xr; fv[n] = fr; }
                }
                else if (fr < fv[n - 1]) { p[n] = xr; fv[n] = fr; }
                else
                {
                    double[] xc = Lerp(c, p[n], 0.5); double fc = f(xc); evals++;
                    if (fc < fv[n]) { p[n] = xc; fv[n] = fc; }
                    else
                    {
                        for (int i = 1; i <= n; i++) { p[i] = Lerp(p[0], p[i], 0.5); fv[i] = f(p[i]); evals++; }
                    }
                }
            }
            Array.Sort(fv, p);
            return p[0];
        }

        static double[] Lerp(double[] c, double[] x, double t)
        {
            var r = new double[c.Length];
            for (int i = 0; i < c.Length; i++) r[i] = c[i] + t * (x[i] - c[i]);
            return r;
        }
    }
}
