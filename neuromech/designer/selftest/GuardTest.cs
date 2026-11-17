// Headless checks of the playback guard as the Designer runs it (RigGuard.cs + RigPlayer.cs), driven by
// neuromech/tests/test_rig_guard.py.
//
//   GuardTest.exe check <dir> <limit>     one line per *.csv in dir: name,verdict,kind,predicted,step,peak,scale,reason
//   GuardTest.exe designs <dir> <limit> [scale]   every MAS preset at the default (or given) rig scale: guard
//                                         verdict, and the scaled design the Designer would offer
//   GuardTest.exe player <work dir>       the RigPlayer refusal / backstop cases against a fake DataSocket;
//                                         one line per case (PASS/FAIL), exit 1 if any failed
using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;

namespace Pendulastic
{
    public class FakeData
    {
        double v; public Func<double> Source;
        public double Value { get { return Source != null ? Source() : v; } set { v = value; } }
    }

    /// <summary>The members of CWDSLib.CWDataSocket that RigPlayer calls (late-bound), and nothing else.</summary>
    public class FakeSocket
    {
        public bool ReadOk = true, WriteOk = true, ConnectOk = true;
        public int Writes;
        public List<double> Written = new List<double>();
        FakeData data = new FakeData();
        public FakeData Data { get { return data; } }
        public string LastMessage { get { return "fake DataSocket"; } }
        public bool SyncConnectTo(string url, int mode, int timeoutMs) { return ConnectOk; }
        public int Reads, ReadOkFor = int.MaxValue;
        public bool SyncRead(int timeoutMs) { Reads++; return ReadOk && Reads <= ReadOkFor; }
        public bool SyncWrite(object v, int timeoutMs) { Writes++; Written.Add(Convert.ToDouble(v, CultureInfo.InvariantCulture)); return WriteOk; }
        public bool SyncDisconnect(int timeoutMs) { return true; }
    }

    static class GuardTest
    {
        static readonly CultureInfo Inv = CultureInfo.InvariantCulture;
        static int failed;
        static string work;

        static void Report(string name, bool ok, string detail)
        {
            if (!ok) failed++;
            Console.WriteLine(name + "," + (ok ? "PASS" : "FAIL") + "," + RigGuard.Note(detail));
        }

        static void File2(string path, out double[] t, out double[] sp)
        {
            GuardReport bad = RigGuard.Parse(File.ReadAllLines(path), out t, out sp);
            if (bad != null) throw new InvalidOperationException(bad.Reason);
        }

        static int Check(string dir, double limit)
        {
            string[] files = Directory.GetFiles(dir, "*.csv");
            Array.Sort(files, StringComparer.Ordinal);
            foreach (string f in files)
            {
                double[] t, sp;
                GuardReport r = RigGuard.Parse(File.ReadAllLines(f), out t, out sp);
                if (r == null) r = RigGuard.Check(t, sp, limit);
                Console.WriteLine(Path.GetFileName(f) + "," + RigGuard.Describe(r));
            }
            return 0;
        }

        static double[] T(params double[] v) { return v; }

        /// <summary>A slow file: 0 for 0.2 s, up to amp over 1 s, back down over 1 s.</summary>
        static void Slow(double amp, out double[] t, out double[] sp)
        {
            int n = 2201; t = new double[n]; sp = new double[n];
            for (int i = 0; i < n; i++)
            {
                double s = i / 1000.0; t[i] = s;
                sp[i] = s < 0.2 ? 0 : (s < 1.2 ? amp * (s - 0.2) : amp * Math.Max(0, 2.2 - s));
            }
        }

        static RigPlayer Player(FakeSocket fake, string name)
        {
            RigPlayer p = new RigPlayer();
            p.LogDir = Path.Combine(work, "sent"); p.LogName = name; p.ReturnDps = 50;
            p.Url = "fake://setpoint";
            if (fake != null) { p.DryRun = false; p.MakeSocket = delegate() { return fake; }; }
            else p.DryRun = true;
            return p;
        }

        static string[] Rows(RigPlayer p) { return File.Exists(p.LastLog) ? File.ReadAllLines(p.LastLog) : new string[0]; }

        static bool Has(string[] rows, string phase)
        {
            foreach (string r in rows) { string[] c = r.Split(','); if (c.Length > 2 && c[2] == phase) return true; }
            return false;
        }

        static readonly string[] WritePhases = { "ramp_in", "play", "hold", "return", "abort_return" };

        /// <summary>Every value written (the sent log's write rows, not its event rows): max |v| and max step.</summary>
        static void Sent(string[] rows, out double peak, out double step, out double lastV, out int count)
        {
            peak = 0; step = 0; lastV = double.NaN; count = 0; double prev = double.NaN;
            for (int i = 1; i < rows.Length; i++)
            {
                string[] c = rows[i].Split(',');
                if (c.Length < 4 || Array.IndexOf(WritePhases, c[2]) < 0) continue;
                double v = double.Parse(c[3], Inv);
                peak = Math.Max(peak, Math.Abs(v));
                if (!double.IsNaN(prev)) step = Math.Max(step, Math.Abs(v - prev));
                prev = v; lastV = v; count++;
            }
        }

        static bool Throws(RigPlayer p, double[] t, double[] sp, out string msg)
        {
            msg = "";
            try { p.Start(t, sp); p.Join(); return false; }
            catch (InvalidOperationException ex) { msg = ex.Message; return true; }
        }

        static int Players(string dir)
        {
            work = dir; Directory.CreateDirectory(dir);
            double[] t, sp; string msg;
            Slow(10, out t, out sp);

            // 1. the hard limit can only be lowered
            RigPlayer p = Player(null, "max45"); p.MaxDeg = 45;
            Report("operator_limit_above_hard_limit", Throws(p, t, sp, out msg) && msg.Contains("hard limit") && p.LastLog.Contains("_refused"), msg);
            p = Player(null, "max30"); p.MaxDeg = 30;
            Report("operator_limit_lowered_plays", !Throws(p, t, sp, out msg) && p.Status == "done", p.Status);

            // 2. load-time refusals (each also leaves a refusal log)
            double[] tn = T(0, 0.5, 1), spn = T(0, double.NaN, 0);
            p = Player(null, "nan");
            Report("refuse_nan", Throws(p, tn, spn, out msg) && msg.Contains("NaN") && File.Exists(p.LastLog), msg);
            p = Player(null, "inf");
            Report("refuse_inf", Throws(p, tn, T(0, double.PositiveInfinity, 0), out msg) && msg.Contains("Infinity"), msg);
            p = Player(null, "over");
            Report("refuse_over_limit", Throws(p, T(0, 1, 2), T(0, 41, 0), out msg) && msg.Contains("limit is 40.0"), msg);
            p = Player(null, "speed");
            Report("refuse_over_speed", Throws(p, T(0, 0.5, 0.51, 1), T(0, 0, 7, 7), out msg) && msg.Contains("600 deg/s"), msg);
            p = Player(null, "start");
            Report("refuse_bad_start_angle", Throws(p, T(0, 1), T(2, 0), out msg) && msg.Contains("first angle"), msg);
            p = Player(null, "start_t");
            Report("refuse_bad_start_time", Throws(p, T(0.5, 1), T(0, 0), out msg) && msg.Contains("start at 0"), msg);

            // 3. the current setpoint must be readable, finite and inside the limit; no step to 0
            FakeSocket f = new FakeSocket(); f.ReadOk = false;
            p = Player(f, "readfail"); p.Start(t, sp); p.Join();
            Report("refuse_setpoint_read_failure", p.Status.StartsWith("REFUSED") && f.Writes == 0 && p.LastLog.Contains("_refused"), p.Status + " writes=" + f.Writes);
            f = new FakeSocket(); f.Data.Value = double.NaN;
            p = Player(f, "readnan"); p.Start(t, sp); p.Join();
            Report("refuse_setpoint_read_nan", p.Status.StartsWith("REFUSED") && f.Writes == 0, p.Status);
            f = new FakeSocket(); f.Data.Value = 45;
            p = Player(f, "readfar"); p.Start(t, sp); p.Join();
            Report("refuse_setpoint_beyond_limit", p.Status.StartsWith("REFUSED") && f.Writes == 0, p.Status);
            f = new FakeSocket(); f.Data.Value = 3;
            p = Player(f, "ramp"); p.Start(t, sp); p.Join();
            double pk, st, lv; int cnt;
            Sent(Rows(p), out pk, out st, out lv, out cnt);
            Report("ramp_in_from_current", p.Status == "done" && Has(Rows(p), "ramp_in") && f.Written.Count > 0 && Math.Abs(f.Written[0] - 3) < 0.2 && lv == 0 && st <= 6,
                   p.Status + " first=" + (f.Written.Count > 0 ? f.Written[0].ToString(Inv) : "-") + " maxstep=" + st.ToString(Inv));

            // 4. the per-write backstop: stops the play and returns to 0 at the return speed
            double[] tb, spb; Slow(45, out tb, out spb);           // passes nothing but the parser; bypass to reach the backstop
            p = Player(null, "bypass_real"); p.DryRun = false; p.TestBypassLoadChecks = true;
            Report("bypass_refused_against_the_rig", Throws(p, tb, spb, out msg) && msg.Contains("fake socket"), msg);
            p = Player(null, "backstop_limit"); p.TestBypassLoadChecks = true; p.Start(tb, spb); p.Join();
            string[] rows = Rows(p);
            Sent(rows, out pk, out st, out lv, out cnt);
            Report("backstop_clamps_and_stops_at_the_limit", p.Status.Contains("guard") && Has(rows, "guard") && Has(rows, "abort_return")
                   && pk <= 40 + 1e-9 && st <= 6 + 1e-9 && lv == 0 && p.LastLog.Contains("_guard"),
                   p.Status + " peak=" + pk.ToString(Inv) + " step=" + st.ToString(Inv));
            p = Player(null, "backstop_nan"); p.TestBypassLoadChecks = true;
            double[] tq = T(0, 0.3, 0.6, 0.9), spq = T(0, 2, double.NaN, 0);
            p.Start(tq, spq); p.Join(); rows = Rows(p);
            Sent(rows, out pk, out st, out lv, out cnt);
            Report("backstop_never_sends_nan", p.Status.Contains("guard") && Has(rows, "guard") && !string.Join("\n", rows).Contains(",play,NaN") && lv == 0, p.Status);
            double[] ts = T(0, 0.5, 0.505, 1.5), sps = T(0, 0, 9, 9);   // 9 deg between two 10 ms writes
            p = Player(null, "backstop_step"); p.TestBypassLoadChecks = true; p.Start(ts, sps); p.Join(); rows = Rows(p);
            Sent(rows, out pk, out st, out lv, out cnt);
            Report("backstop_limits_step_and_stops", p.Status.Contains("guard") && st <= 6 + 1e-9 && lv == 0, p.Status + " step=" + st.ToString(Inv));

            // 5. failed writes: the player stops and says the rig holds the last value (it cannot do more)
            f = new FakeSocket(); f.WriteOk = false;
            p = Player(f, "writefail"); p.Start(t, sp); p.Join();
            Report("write_failures_stop_and_say_the_rig_holds", p.Status.StartsWith("STOPPED") && p.Status.Contains("holds the last value") && f.Writes == 20, p.Status);

            // 6. one writer at a time (both directions with PowerShell are in test_rig_guard.py)
            string lockWhy;
            System.Threading.Mutex held = RigGuard.AcquireWriterLock(out lockWhy);
            Report("lock_taken_by_test", held != null, lockWhy);
            f = new FakeSocket();
            System.Threading.Thread other = new System.Threading.Thread(delegate()
            {
                RigPlayer q = Player(f, "locked"); q.Start(t, sp); q.Join();
                Report("lock_refuses_a_second_player", q.Status.StartsWith("REFUSED") && q.Status.Contains("another player") && f.Writes == 0, q.Status);
            });
            other.Start(); other.Join();                          // a different thread: a mutex is re-entrant on its owner
            if (held != null) { held.ReleaseMutex(); held.Close(); }
            f = new FakeSocket();
            p = Player(f, "unlocked"); p.Start(t, sp); p.Join();
            Report("lock_released_plays", p.Status == "done" && f.Writes > 0, p.Status);

            // 7. the optional encoder watchdog
            f = new FakeSocket();
            p = Player(f, "wd_off"); p.Start(t, sp); p.Join(); rows = Rows(p);
            Report("watchdog_off_is_said_and_logged", p.WatchdogState == "encoder watchdog OFF" && string.Join("\n", rows).Contains("encoder watchdog OFF"), p.WatchdogState);
            FakeSocket w = new FakeSocket(); FakeSocket enc = new FakeSocket();
            enc.Data.Source = delegate() { return w.Written.Count > 0 ? w.Written[w.Written.Count - 1] : 0.0; };
            p = Player(w, "wd_track"); p.EncoderUrl = "fake://encoder"; p.MakeEncoderSocket = delegate() { return enc; };
            p.Start(t, sp); p.Join(); rows = Rows(p);
            Report("watchdog_on_tracking_arm_plays", p.Status == "done" && p.WatchdogState == "encoder watchdog ON" && Has(rows, "encoder") && !Has(rows, "watchdog"), p.Status);
            w = new FakeSocket(); enc = new FakeSocket();                          // the arm does not move
            p = Player(w, "wd_stuck"); p.EncoderUrl = "fake://encoder"; p.MakeEncoderSocket = delegate() { return enc; };
            p.Start(t, sp); p.Join(); rows = Rows(p);
            Sent(rows, out pk, out st, out lv, out cnt);
            Report("watchdog_stops_an_arm_that_does_not_follow", p.Status.Contains("encoder watchdog") && Has(rows, "watchdog") && Has(rows, "abort_return")
                   && lv == 0 && pk < 10 && p.LastLog.Contains("_watchdog"), p.Status + " peak=" + pk.ToString(Inv));
            w = new FakeSocket(); enc = new FakeSocket(); enc.ReadOkFor = 3;
            enc.Data.Source = delegate() { return w.Written.Count > 0 ? w.Written[w.Written.Count - 1] : 0.0; };
            p = Player(w, "wd_lost"); p.EncoderUrl = "fake://encoder"; p.MakeEncoderSocket = delegate() { return enc; };
            p.Start(t, sp); p.Join();
            Report("watchdog_stops_when_the_encoder_goes_silent", p.Status.Contains("could not be read 10 times"), p.Status);
            w = new FakeSocket(); enc = new FakeSocket(); enc.ReadOk = false;
            p = Player(w, "wd_noread"); p.EncoderUrl = "fake://encoder"; p.MakeEncoderSocket = delegate() { return enc; };
            p.Start(t, sp); p.Join();
            Report("watchdog_configured_but_unreadable_refuses", p.Status.StartsWith("REFUSED") && w.Writes == 0, p.Status);
            w = new FakeSocket(); enc = new FakeSocket(); enc.Data.Value = 12;
            p = Player(w, "wd_wrongvar"); p.EncoderUrl = "fake://encoder"; p.MakeEncoderSocket = delegate() { return enc; };
            p.Start(t, sp); p.Join();
            Report("watchdog_wrong_variable_refuses", p.Status.StartsWith("REFUSED") && p.Status.Contains("actual position") && w.Writes == 0, p.Status);
            double[] rec = new double[] { 10, 10.5, 11 };
            Report("unit_watchdog_over_limit", RigGuard.WatchdogCheck(43.5, new double[] { 40 }, 1, 40, 50) != null, "");
            Report("unit_watchdog_at_limit_plus_3_ok", RigGuard.WatchdogCheck(42.9, new double[] { 40 }, 1, 40, 5) == null, "");
            Report("unit_watchdog_following", RigGuard.WatchdogCheck(17, rec, 3, 40, 5) != null && RigGuard.WatchdogCheck(15.9, rec, 3, 40, 5) == null, "");
            Report("unit_watchdog_nan", RigGuard.WatchdogCheck(double.NaN, rec, 3, 40, 5) != null, "");

            // 8. RigGuard.Backstop on its own
            string evt;
            Report("unit_backstop_nan_holds", RigGuard.Backstop(double.NaN, 3, 40, out evt) == 3 && evt != null, evt);
            Report("unit_backstop_limit", RigGuard.Backstop(50, 39, 40, out evt) == 40 && evt != null, evt);
            Report("unit_backstop_step", RigGuard.Backstop(20, 0, 40, out evt) == 6 && evt != null, evt);
            Report("unit_backstop_neg_step", RigGuard.Backstop(-20, 0, 40, out evt) == -6 && evt != null, evt);
            Report("unit_backstop_quiet", RigGuard.Backstop(5, 0, 40, out evt) == 5 && evt == null, "");
            Report("unit_backstop_operator_above_hard", RigGuard.Backstop(45, 39.5, 45, out evt) == 40 && evt != null, evt);
            return failed == 0 ? 0 : 1;
        }

        /// <summary>Every MAS preset at the default scale: its guard verdict, and the scaled design the Designer would
        /// offer (rig file and knee trace written so Python can recheck the guard and rescore the scaled swing).</summary>
        static int Designs(string dir, double limit, double scale)
        {
            Directory.CreateDirectory(dir);
            Console.WriteLine("name,scale0,raw0,pred0,ok0,kind0,scale1,raw1,pred1,ok1,pt7_0,pt7_1,interpretable1,mas1");
            foreach (string grade in Designer.Grades)
            {
                string name = "mas_" + grade.Replace("+", "p");
                Knobs basis = new Knobs(); basis.Scale = scale;
                DesignResult d = Designer.ForGrade(grade, basis);
                GuardReport g0 = RigGuard.Check(d.Movement.RigT, d.Movement.RigDeg, limit);
                DesignResult s = g0.Ok ? d : Designer.ScaledToLimit(d, limit, 0.2);
                GuardReport g1 = s == null ? new GuardReport() : RigGuard.Check(s.Movement.RigT, s.Movement.RigDeg, limit);
                if (s != null)
                {
                    System.Text.StringBuilder sb = new System.Text.StringBuilder();
                    for (int i = 0; i < s.Movement.RigT.Length; i++)
                        sb.Append(s.Movement.RigT[i].ToString("F4", Inv)).Append(',').Append(s.Movement.RigDeg[i].ToString("F4", Inv)).Append('\n');
                    File.WriteAllText(Path.Combine(dir, name + "_scaled_rig.csv"), sb.ToString());
                    sb.Length = 0;
                    for (int i = 0; i < s.Movement.KneeT.Length; i++)
                        sb.Append(s.Movement.KneeT[i].ToString("R", Inv)).Append(',').Append(s.Movement.KneeDeg[i].ToString("R", Inv)).Append('\n');
                    File.WriteAllText(Path.Combine(dir, name + "_scaled_knee.csv"), sb.ToString());
                }
                Console.WriteLine(string.Join(",", new string[] { name,
                    d.Knobs.Scale.ToString("R", Inv), g0.RawPeakDeg.ToString("R", Inv), g0.PredictedPeakDeg.ToString("R", Inv), g0.Ok ? "1" : "0", g0.Kind,
                    s == null ? "" : s.Knobs.Scale.ToString("R", Inv), g1.RawPeakDeg.ToString("R", Inv), g1.PredictedPeakDeg.ToString("R", Inv), g1.Ok ? "1" : "0",
                    d.Metrics.Pt7.HasValue ? d.Metrics.Pt7.Value.ToString("R", Inv) : "",
                    s != null && s.Metrics.Pt7.HasValue ? s.Metrics.Pt7.Value.ToString("R", Inv) : "",
                    s != null && s.Metrics.Interpretable ? "1" : "0", s == null ? "" : s.Metrics.Mas }));
            }
            return 0;
        }

        /// <summary>Hold the writer lock for some seconds (the PowerShell player must refuse meanwhile).</summary>
        static int Hold(double secs)
        {
            string why; System.Threading.Mutex m = RigGuard.AcquireWriterLock(out why);
            if (m == null) { Console.WriteLine("BUSY," + RigGuard.Note(why)); return 1; }
            Console.WriteLine("HELD"); Console.Out.Flush();
            System.Threading.Thread.Sleep((int)(secs * 1000));
            m.ReleaseMutex(); m.Close();
            return 0;
        }

        /// <summary>Wait (up to secs) until another player holds the lock, then show a Designer player refusing.</summary>
        static int Probe(string dir, double secs)
        {
            work = dir; Directory.CreateDirectory(dir);
            System.Diagnostics.Stopwatch sw = System.Diagnostics.Stopwatch.StartNew();
            while (sw.Elapsed.TotalSeconds < secs)
            {
                string why; System.Threading.Mutex m = RigGuard.AcquireWriterLock(out why);
                if (m != null) { m.ReleaseMutex(); m.Close(); System.Threading.Thread.Sleep(50); continue; }
                double[] t, sp; Slow(5, out t, out sp);
                FakeSocket f = new FakeSocket();
                RigPlayer p = Player(f, "probe"); p.Start(t, sp); p.Join();
                bool ok = p.Status.StartsWith("REFUSED") && p.Status.Contains("another player") && f.Writes == 0;
                Console.WriteLine((ok ? "REFUSED_WHILE_HELD," : "NOT_REFUSED,") + RigGuard.Note(p.Status));
                return ok ? 0 : 1;
            }
            Console.WriteLine("NEVER_HELD"); return 1;
        }

        static int Main(string[] args)
        {
            if (args.Length >= 2 && args[0] == "hold") return Hold(double.Parse(args[1], Inv));
            if (args.Length >= 3 && args[0] == "probe") return Probe(args[1], double.Parse(args[2], Inv));
            if (args.Length >= 3 && args[0] == "check") return Check(args[1], double.Parse(args[2], Inv));
            if (args.Length >= 3 && args[0] == "designs")
                return Designs(args[1], double.Parse(args[2], Inv), args.Length >= 4 ? double.Parse(args[3], Inv) : Knobs.DefaultScale);
            if (args.Length >= 2 && args[0] == "player") return Players(args[1]);
            Console.Error.WriteLine("usage: GuardTest check <dir> <limit> | GuardTest player <work dir>");
            return 2;
        }
    }
}
