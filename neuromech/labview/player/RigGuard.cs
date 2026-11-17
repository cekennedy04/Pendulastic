// RigGuard: the playback safety rules, in ONE place for both players.
//
//   * play_setpoints.ps1 (PLAY.bat) loads this file with Add-Type at start-up and refuses to play if it cannot;
//   * Pendulum Designer (RigPlayer.cs / MainForm.cs) compiles it in (build_designer.py adds it);
//   * neuromech/rig_guard.py is the Python twin for the analysis PC (plan/bundle), and the cross-player test
//     (neuromech/tests/test_rig_guard.py) feeds the same fixture files to all three and requires the same
//     accept/refuse verdict and the same predicted peak.
//
// Written in C# 2.0 on purpose: Windows PowerShell 2.0's Add-Type compiles C# 2.0 (no var, lambdas,
// auto-properties or object initializers here). Owner decisions 2026-11-17, not to be changed here:
// hard limit +/-40 deg rig angle (an operator setting can only lower it), max setpoint speed 600 deg/s
// (6 deg per 10 ms write), files over the limit are refused, never clipped mid-swing.
//
// Load-time refusal, first failing rule wins (Kind in brackets):
//   fewer than two rows [rows]; a header line [header]; a field that is not a number [parse];
//   any NaN/Inf [nonfinite]; time not starting at 0 [start_time]; first angle not 0 [start_angle];
//   time not strictly increasing [time_order]; any |angle| > limit [peak];
//   any change > 6 deg between two moments up to 10 ms apart on the players' 1 ms clock [speed];
//   predicted delivered peak > limit [predicted].
// The players read the file at whole milliseconds of their own clock, nominally every 10 ms, but a late tick
// moves a write to any millisecond. So the speed rule looks at every pair of milliseconds up to 10 ms apart
// (not just the 0/10/20 ms grid: a 50 Hz wiggle or a 1 ms spike hides between grid points), and the
// prediction is run for all ten 1 ms phases of the 10 ms grid; the worst phase counts.
// The predicted delivered peak is the 10 ms staircase the player sends, held 2 s at the end, passed through
// the identified closed-loop rig response (2nd order, 6.35 Hz, zeta 0.23, measured 2026-10-26 from 23 SSR
// logs: gain 1.26 at 3 Hz, ~2.0 at 5 Hz) AND through five detuned copies of it (natural frequency -10 %/+10 %,
// the session-to-session drift the LABVIEW spec tolerates before re-identifying; damping 0.18, i.e. 0.05
// less damped than measured). The largest |position| over all six is the prediction. It is a simulation,
// not a guarantee: a rig whose resonance moved more than 10 % or lost more damping than that is outside it.
//
// Per-write backstop (Backstop): a NaN/Inf is never sent (the last value is held), a value beyond the limit
// is clamped to it, and the change from the last value sent is limited to 6 deg. Any engagement during
// play means a file that passed the load checks is being sent wrong: the players stop and ramp to 0.
using System;
using System.Collections.Generic;
using System.Globalization;

namespace Pendulastic
{
    public class GuardReport
    {
        public bool Ok;
        public string Kind = "";
        public string Reason = "";
        public double LimitDeg = double.NaN;
        public double RawPeakDeg = double.NaN, RawPeakT = double.NaN;
        public double MaxStepDeg = double.NaN, MaxStepT = double.NaN;
        public double PredictedPeakDeg = double.NaN, PredictedPeakT = double.NaN;
        /// <summary>Whole-file scale factor that would pass (NaN when it already passes or cannot be scaled).</summary>
        public double ScaleToFit = double.NaN;

        /// <summary>A failure that a smaller amplitude would cure (not a malformed file).</summary>
        public bool Scalable { get { return Kind == "peak" || Kind == "speed" || Kind == "predicted"; } }
    }

    public static class RigGuard
    {
        public const double HardLimitDeg = 40.0;
        public const int PeriodMs = 10;
        public const double MaxStepDeg = 6.0;                 // per 10 ms write: 600 deg/s
        public const double MaxSpeedDps = 600.0;
        public const double ScaleMarginDeg = 1.0;             // a scale-down aims at limit - 1 deg predicted
        public const double PlantWnHz = 6.35, PlantZeta = 0.23;
        public static readonly double[] VariantWnHz = new double[] { 5.715, 6.35, 6.985 };
        public static readonly double[] VariantZeta = new double[] { 0.23, 0.18 };
        public const double SimStepS = 0.001;                 // 10 substeps per write
        public const double TailS = 2.0;                      // the players hold the last value 2 s
        public const double StartTol = 1e-6, StartAngleTol = 0.01, Eps = 1e-9;

        // returns to 0 (STOP, q/Esc, guard/watchdog stop, end of a run, error paths): the speed is an operator
        // setting within [1, 60] deg/s; every ramp keeps writing until the value SENT equals the target, or
        // until its planned time plus RampExtraS has passed (then the caller reports the rig holding)
        public const double DefaultReturnDps = 10.0, MinReturnDps = 1.0, MaxReturnDps = 60.0;
        public const double RampExtraS = 5.0;

        static readonly CultureInfo Inv = CultureInfo.InvariantCulture;

        /// <summary>Null when a return speed is usable.</summary>
        public static string CheckReturnDps(double dps)
        {
            if (double.IsNaN(dps) || double.IsInfinity(dps) || dps < MinReturnDps - Eps || dps > MaxReturnDps + Eps)
                return "the return speed must be " + F(MinReturnDps, 0) + " to " + F(MaxReturnDps, 0) + " deg/s (got " + dps.ToString(Inv) + ")";
            return null;
        }

        static string F(double v, int dec) { return v.ToString("F" + dec.ToString(Inv), Inv); }

        /// <summary>The limit to play under: the operator's setting, which may only lower the hard limit.</summary>
        public static string CheckLimit(double operatorDeg)
        {
            if (double.IsNaN(operatorDeg) || double.IsInfinity(operatorDeg) || operatorDeg <= 0)
                return "the angle limit must be a positive number of degrees (got " + operatorDeg.ToString(Inv) + ")";
            if (operatorDeg > HardLimitDeg + Eps)
                return "the angle limit " + F(operatorDeg, 1) + " deg is above the hard limit of " + F(HardLimitDeg, 0)
                    + " deg built into the players; it can only be lowered";
            return null;
        }

        static bool IsNonFiniteWord(string s)
        {
            string w = s.Trim().ToLowerInvariant().TrimStart('+', '-');
            return w == "nan" || w == "inf" || w == "infinity";
        }

        /// <summary>Parse a player file (t_s,setpoint_deg per line, no header). Null on success, else the refusal.</summary>
        public static GuardReport Parse(string[] lines, out double[] t, out double[] sp)
        {
            List<double> tl = new List<double>(), sl = new List<double>();
            t = null; sp = null;
            int row = 0;
            for (int i = 0; i < lines.Length; i++)
            {
                string s = lines[i].Trim();
                if (s.Length == 0) continue;
                row++;
                string[] p = s.Split(',');
                double a, b;
                bool okA = double.TryParse(p[0].Trim(), NumberStyles.Float, Inv, out a);
                if (row == 1 && !okA && !IsNonFiniteWord(p[0]))
                    return Fail("header", "the file starts with a header row ('" + Clip(s) + "'); player files have none."
                        + " Use the session's player\\ file, not commands\\");
                if (p.Length != 2)
                    return Fail("parse", "row " + row + " has " + p.Length + " columns, not two (t_s,setpoint_deg): '" + Clip(s) + "'");
                bool okB = double.TryParse(p[1].Trim(), NumberStyles.Float, Inv, out b);
                if (!okA) { if (IsNonFiniteWord(p[0])) a = double.NaN; else return Fail("parse", "row " + row + ": the time '" + Clip(p[0]) + "' is not a number"); }
                if (!okB) { if (IsNonFiniteWord(p[1])) b = double.NaN; else return Fail("parse", "row " + row + ": the angle '" + Clip(p[1]) + "' is not a number"); }
                tl.Add(a); sl.Add(b);
            }
            t = tl.ToArray(); sp = sl.ToArray();
            return null;
        }

        static string Clip(string s) { s = s.Trim(); return s.Length > 40 ? s.Substring(0, 40) + "..." : s; }

        static GuardReport Fail(string kind, string reason)
        {
            GuardReport r = new GuardReport();
            r.Ok = false; r.Kind = kind; r.Reason = reason;
            return r;
        }

        /// <summary>The file's angle at elapsed time sec: what both players send (linear interpolation).</summary>
        public static double At(double[] t, double[] sp, double sec)
        {
            int n = t.Length;
            if (sec <= 0) return sp[0];
            if (sec >= t[n - 1]) return sp[n - 1];
            int j = Array.BinarySearch(t, sec);
            if (j >= 0) return sp[j];
            j = ~j - 1;
            double f = (sec - t[j]) / (t[j + 1] - t[j]);
            return sp[j] + f * (sp[j + 1] - sp[j]);
        }

        /// <summary>The writes an on-time player sends: At(k * 10 ms) until the clock reaches the end, then the last angle.</summary>
        public static double[] Stream(double[] t, double[] sp) { return Stream(t, sp, 0); }

        /// <summary>The same stream with every write phaseMs (0..9) later: At((k * 10 + phaseMs) ms).</summary>
        public static double[] Stream(double[] t, double[] sp, int phaseMs)
        {
            double dur = t[t.Length - 1];
            List<double> o = new List<double>();
            for (int k = 0; ; k++)
            {
                double e = (k * PeriodMs + phaseMs) / 1000.0;
                if (e >= dur) { o.Add(sp[sp.Length - 1]); break; }
                o.Add(At(t, sp, e));
            }
            return o.ToArray();
        }

        /// <summary>Peak |arm angle| of one 2nd-order closed loop driven by the 10 ms staircase plus the 2 s hold.</summary>
        public static double SimPeak(double[] stream, double wnHz, double zeta, out double atS)
        {
            double w = 2 * Math.PI * wnHz, sg = zeta * w, wd = w * Math.Sqrt(1 - zeta * zeta), dt = SimStepS;
            double ex = Math.Exp(-sg * dt), c = Math.Cos(wd * dt), s = Math.Sin(wd * dt);
            // exact zero-order-hold discretisation of y'' = w^2 (u - y) - 2 zeta w y'
            double a11 = ex * (c + sg / wd * s), a12 = ex * s / wd;
            double a21 = -ex * w * w / wd * s, a22 = ex * (c - sg / wd * s);
            double b1 = 1 - a11, b2 = -a21;
            int sub = (int)Math.Round(PeriodMs / 1000.0 / dt);
            int tail = (int)Math.Round(TailS / dt);
            double y = 0, v = 0, peak = 0; atS = 0;
            int step = 0;
            int total = stream.Length * sub + tail;
            for (int i = 0; i < total; i++)
            {
                double u = i < stream.Length * sub ? stream[i / sub] : stream[stream.Length - 1];
                double yn = a11 * y + a12 * v + b1 * u;
                double vn = a21 * y + a22 * v + b2 * u;
                y = yn; v = vn; step++;
                if (Math.Abs(y) > peak) { peak = Math.Abs(y); atS = step * dt; }
            }
            return peak;
        }

        /// <summary>The largest change between the angles the players could send at two whole milliseconds of their
        /// clock up to one write period (10 ms) apart, starting from a setpoint at 0. atS is the earlier moment.</summary>
        public static double MaxStep(double[] t, double[] sp, out double atS)
        {
            double dur = t[t.Length - 1];
            int m = (int)Math.Ceiling(dur * 1000.0 - 1e-9);
            if (m < 1) m = 1;
            double[] g = new double[m + 1];
            for (int i = 0; i < m; i++) g[i] = At(t, sp, i / 1000.0);
            g[m] = sp[sp.Length - 1];                          // at and after the end: the last angle
            double best = Math.Abs(g[0]); atS = 0;
            for (int i = 0; i < m; i++)
                for (int d = 1; d <= PeriodMs && i + d <= m; d++)
                {
                    double x = Math.Abs(g[i + d] - g[i]);
                    if (x > best) { best = x; atS = i / 1000.0; }
                }
            return best;
        }

        /// <summary>The predicted peak over all ten 1 ms phases of the 10 ms write grid (atS on the file's clock).</summary>
        public static double PredictedPeakAllPhases(double[] t, double[] sp, out double atS)
        {
            double best = 0; atS = 0;
            for (int p = 0; p < PeriodMs; p++)
            {
                double at; double pk = PredictedPeak(Stream(t, sp, p), out at);
                if (pk > best) { best = pk; atS = at + p / 1000.0; }
            }
            return best;
        }

        /// <summary>The conservative predicted delivered peak: the largest over the nominal plant and its detuned copies.</summary>
        public static double PredictedPeak(double[] stream, out double atS)
        {
            double best = 0; atS = 0;
            foreach (double wn in VariantWnHz)
                foreach (double z in VariantZeta)
                {
                    double at; double p = SimPeak(stream, wn, z, out at);
                    if (p > best) { best = p; atS = at; }
                }
            return best;
        }

        /// <summary>Every load-time rule. limitDeg is the operator's limit (checked against the hard limit first).</summary>
        public static GuardReport Check(double[] t, double[] sp, double limitDeg)
        {
            string lim = CheckLimit(limitDeg);
            if (lim != null) return Fail("operator_limit", lim);
            int n = t == null ? 0 : t.Length;
            if (n < 2 || sp.Length != n) return Fail("rows", "the file has fewer than two rows");
            for (int i = 0; i < n; i++)
            {
                if (double.IsNaN(t[i]) || double.IsInfinity(t[i]))
                    return Fail("nonfinite", "row " + (i + 1) + ": the time is " + t[i].ToString(Inv) + " (not a number)");
                if (double.IsNaN(sp[i]) || double.IsInfinity(sp[i]))
                    return Fail("nonfinite", "at t = " + F(t[i], 3) + " s (row " + (i + 1) + ") the angle is " + sp[i].ToString(Inv));
            }
            if (Math.Abs(t[0]) > StartTol) return Fail("start_time", "the file's time must start at 0 (it starts at " + t[0].ToString(Inv) + " s)");
            if (Math.Abs(sp[0]) > StartAngleTol) return Fail("start_angle", "the file's first angle must be 0 (it is " + F(sp[0], 2) + " deg); every player file starts at 0");
            for (int i = 1; i < n; i++)
                if (t[i] <= t[i - 1]) return Fail("time_order", "time does not increase at row " + (i + 1) + " (t = " + t[i].ToString(Inv) + " s after " + t[i - 1].ToString(Inv) + " s)");

            GuardReport r = new GuardReport();
            r.LimitDeg = limitDeg;
            r.RawPeakDeg = 0; r.RawPeakT = 0;
            int firstOver = -1;
            for (int i = 0; i < n; i++)
            {
                double a = Math.Abs(sp[i]);
                if (a > r.RawPeakDeg) { r.RawPeakDeg = a; r.RawPeakT = t[i]; }
                if (firstOver < 0 && a > limitDeg + Eps) firstOver = i;
            }
            double at;
            r.MaxStepDeg = MaxStep(t, sp, out at);
            r.MaxStepT = at;
            r.PredictedPeakDeg = PredictedPeakAllPhases(t, sp, out at);
            r.PredictedPeakT = at;

            if (firstOver >= 0)
            {
                r.Kind = "peak";
                r.Reason = "at t = " + F(t[firstOver], 3) + " s the angle is " + F(sp[firstOver], 2) + " deg (peak " + F(r.RawPeakDeg, 2)
                    + " deg at " + F(r.RawPeakT, 3) + " s); the limit is " + F(limitDeg, 1) + " deg";
            }
            else if (r.MaxStepDeg > MaxStepDeg + Eps)
            {
                r.Kind = "speed";
                r.Reason = "from t = " + F(r.MaxStepT, 3) + " s the setpoint changes by " + F(r.MaxStepDeg, 2) + " deg within 10 ms ("
                    + F(r.MaxStepDeg * 1000.0 / PeriodMs, 0) + " deg/s); the limit is " + F(MaxStepDeg, 0) + " deg per write (" + F(MaxSpeedDps, 0) + " deg/s)";
            }
            else if (r.PredictedPeakDeg > limitDeg + Eps)
            {
                r.Kind = "predicted";
                r.Reason = "the arm is predicted to reach " + F(r.PredictedPeakDeg, 2) + " deg near t = " + F(r.PredictedPeakT, 2)
                    + " s (the commanded peak is " + F(r.RawPeakDeg, 2) + " deg; the rig overshoots fast content, up to 2x at 5 Hz); the limit is "
                    + F(limitDeg, 1) + " deg";
            }
            else { r.Ok = true; return r; }
            r.ScaleToFit = ScaleToFit(r, limitDeg);
            return r;
        }

        /// <summary>Largest 0.01-step factor for the WHOLE stream (a linear system: everything scales with it) that
        /// passes with the predicted peak at most limit - ScaleMarginDeg. NaN if the file is already fine.</summary>
        public static double ScaleToFit(GuardReport r, double limitDeg)
        {
            double s = 1.0;
            if (r.RawPeakDeg > 0) s = Math.Min(s, limitDeg / r.RawPeakDeg);
            if (r.MaxStepDeg > 0) s = Math.Min(s, MaxStepDeg / r.MaxStepDeg);
            if (r.PredictedPeakDeg > 0) s = Math.Min(s, (limitDeg - ScaleMarginDeg) / r.PredictedPeakDeg);
            return Math.Floor(s * 100.0 + 1e-9) / 100.0;
        }

        /// <summary>The per-write backstop. Returns the value to send; evt is null when nothing was changed.</summary>
        public static double Backstop(double v, double last, double limitDeg, out string evt)
        {
            evt = null;
            double lim = Math.Min(limitDeg, HardLimitDeg);
            if (double.IsNaN(lim) || lim <= 0) lim = HardLimitDeg;
            if (double.IsNaN(last) || double.IsInfinity(last)) last = 0;
            if (double.IsNaN(v) || double.IsInfinity(v))
            {
                evt = "non-finite setpoint (" + v.ToString(Inv) + ") not sent; held " + F(last, 4) + " deg";
                v = last;
            }
            double g = v;
            if (g > lim) { evt = Join(evt, "setpoint " + F(v, 4) + " deg beyond the limit; clamped to " + F(lim, 1)); g = lim; }
            else if (g < -lim) { evt = Join(evt, "setpoint " + F(v, 4) + " deg beyond the limit; clamped to " + F(-lim, 1)); g = -lim; }
            double d = g - last;
            if (d > MaxStepDeg + Eps) { evt = Join(evt, "change of " + F(d, 3) + " deg in one write; limited to " + F(MaxStepDeg, 0)); g = last + MaxStepDeg; }
            else if (d < -MaxStepDeg - Eps) { evt = Join(evt, "change of " + F(d, 3) + " deg in one write; limited to -" + F(MaxStepDeg, 0)); g = last - MaxStepDeg; }
            if (g > lim) g = lim; else if (g < -lim) g = -lim;
            return g;
        }

        static string Join(string a, string b) { return a == null ? b : a + "; " + b; }

        // -- single writer: the Designer and play_setpoints.ps1 never write the setpoint at the same time --
        public const string WriterLockName = "PendulasticSetpointWriter";

        /// <summary>Take the machine-wide writer lock (Global\ only: a fallback to a per-session Local\ name would let
        /// two players write at once). Returns the held mutex, or null with why set when another player holds it or
        /// the lock cannot be opened (e.g. made by a player running as administrator). The caller must
        /// ReleaseMutex on the SAME thread when its return to 0 is complete. A lock abandoned by a player that
        /// died counts as taken over (its process is gone; the ramp-in starts from the setpoint it left).</summary>
        public static System.Threading.Mutex AcquireWriterLock(out string why)
        {
            why = null;
            System.Threading.Mutex m = null;
            try { m = new System.Threading.Mutex(false, "Global\\" + WriterLockName); }
            catch (Exception e)
            {
                why = "the setpoint-writer lock (Global\\" + WriterLockName + ") cannot be opened (" + e.GetType().Name + "): another"
                    + " player is probably running as administrator or as another user. Close every player (the Pendulum Designer"
                    + " and PLAY.bat), then start this one again the same way as the others";
                return null;
            }
            bool got;
            try { got = m.WaitOne(0, false); }
            catch (System.Threading.AbandonedMutexException) { got = true; }
            if (!got)
            {
                m.Close();
                why = "another player (the Pendulum Designer or PLAY.bat) is writing the setpoint right now; only one may."
                    + " Let it finish (or stop it) first";
                return null;
            }
            return m;
        }

        // -- optional encoder watchdog (needs the arm's ACTUAL position as a readable variable) --
        // This runs in the same host process as the player: it is a second opinion that ramps the SETPOINT
        // to 0, not an independent hardware cutoff. The E-stop remains the protection.
        public const double WatchdogOverLimitDeg = 3.0;       // abort when |actual| > limit + 3 deg
        public const double FollowFloorDeg = 5.0;             // following-error bound = 5 deg + 1.5 x predicted
        public const double FollowModelFactor = 1.5;
        public const int FollowWindowWrites = 15;             // compare with any setpoint sent in the last 150 ms
        public const int WatchdogEveryTicks = 5;              // read the encoder every 5th write (50 ms)
        public const int WatchdogReadTimeoutMs = 20;
        public const int WatchdogMaxMissed = 10;              // 10 failed reads in a row (~0.5 s) abort

        /// <summary>The largest following error the identified plant (and its detuned copies) produces on this
        /// stream, measured as the watchdog measures it: |arm - nearest setpoint sent in the last 150 ms|.</summary>
        public static double PredictedFollowingError(double[] stream)
        {
            double worst = 0;
            foreach (double wn in VariantWnHz)
                foreach (double z in VariantZeta)
                {
                    double w = 2 * Math.PI * wn, sg = z * w, wd = w * Math.Sqrt(1 - z * z), dt = SimStepS;
                    double ex = Math.Exp(-sg * dt), c = Math.Cos(wd * dt), s = Math.Sin(wd * dt);
                    double a11 = ex * (c + sg / wd * s), a12 = ex * s / wd, a21 = -ex * w * w / wd * s, a22 = ex * (c - sg / wd * s);
                    double b1 = 1 - a11, b2 = -a21;
                    int sub = (int)Math.Round(PeriodMs / 1000.0 / dt);
                    double y = 0, v = 0;
                    for (int k = 0; k < stream.Length; k++)
                        for (int j = 0; j < sub; j++)
                        {
                            double u = stream[k];
                            double yn = a11 * y + a12 * v + b1 * u, vn = a21 * y + a22 * v + b2 * u;
                            y = yn; v = vn;
                            double e = double.MaxValue;
                            for (int q = Math.Max(0, k - FollowWindowWrites); q <= k; q++) e = Math.Min(e, Math.Abs(y - stream[q]));
                            if (e > worst) worst = e;
                        }
                }
            return worst;
        }

        /// <summary>Null when the picked encoder variable is usable: it must not be the setpoint variable itself
        /// (the watchdog would then only ever see what it sent).</summary>
        public static string EncoderUrlProblem(string encoderUrl, string setpointUrl)
        {
            if (encoderUrl == null || setpointUrl == null) return null;
            string a = encoderUrl.Trim().TrimEnd('/').ToLowerInvariant(), b = setpointUrl.Trim().TrimEnd('/').ToLowerInvariant();
            if (a.Length > 0 && a == b)
                return "the encoder variable is the setpoint variable (" + encoderUrl.Trim() + "); pick the variable that holds the arm's"
                    + " ACTUAL position, or none (watchdog OFF)";
            return null;
        }

        public static double FollowBoundDeg(double predictedFollowingError) { return FollowFloorDeg + FollowModelFactor * predictedFollowingError; }

        /// <summary>The watchdog's verdict on one encoder reading against the recent setpoints (oldest first).
        /// Null when fine.</summary>
        public static string WatchdogCheck(double actual, double[] recentSent, int count, double limitDeg, double followBoundDeg)
        {
            if (double.IsNaN(actual) || double.IsInfinity(actual)) return "the encoder reads " + actual.ToString(Inv);
            if (Math.Abs(actual) > limitDeg + WatchdogOverLimitDeg)
                return "the arm is at " + F(actual, 2) + " deg, past the " + F(limitDeg, 1) + " deg limit + " + F(WatchdogOverLimitDeg, 0) + " deg";
            if (count <= 0) return null;
            double e = double.MaxValue;
            for (int i = 0; i < count; i++) e = Math.Min(e, Math.Abs(actual - recentSent[i]));
            if (e > followBoundDeg)
                return "the arm is at " + F(actual, 2) + " deg, " + F(e, 2) + " deg from every setpoint sent in the last "
                    + (FollowWindowWrites * PeriodMs).ToString(Inv) + " ms (bound " + F(followBoundDeg, 2) + " deg)";
            return null;
        }

        /// <summary>Text with no commas or line breaks, for the note column of a sent log.</summary>
        public static string Note(string s)
        {
            if (s == null) return "";
            return s.Replace(",", ";").Replace("\r", " ").Replace("\n", " ");
        }

        /// <summary>The sent log's header (both players).</summary>
        public const string SentHeader = "t_wall_s,t_file_s,phase,setpoint_deg,note";

        /// <summary>The one-row sent log written when a file is refused before anything is sent.</summary>
        public static string RefusalLog(string kind, string reason)
        {
            return SentHeader + "\r\n0.0000,-1.0000,refused,," + Note(kind + ": " + reason) + "\r\n";
        }

        /// <summary>One line for a summary listing (used by the C# self-test and the cross-player test).</summary>
        public static string Describe(GuardReport r)
        {
            return (r.Ok ? "ok" : "refused") + "," + r.Kind + "," + r.PredictedPeakDeg.ToString("R", Inv) + "," + r.MaxStepDeg.ToString("R", Inv)
                + "," + r.RawPeakDeg.ToString("R", Inv) + "," + r.ScaleToFit.ToString("R", Inv) + "," + Note(r.Reason);
        }
    }
}
