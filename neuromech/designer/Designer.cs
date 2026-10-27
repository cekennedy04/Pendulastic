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
                var alt = ForParams(new Dictionary<string, double> { { "pt7", target } }, res.Knobs);
                double ga = alt.Metrics.Pt7.HasValue ? alt.Metrics.Pt7.Value : double.NaN;
                if (alt.Metrics.Interpretable && Math.Abs(ga - target) < Math.Abs(got - target)) { res = alt; got = ga; }
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
