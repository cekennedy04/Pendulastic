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
        public int FailWritesFrom = int.MaxValue, FailWritesTo = int.MaxValue;     // writes number From..To (1-based) fail
        public List<double> Taken = new List<double>();                              // the writes that returned true
        public bool SyncWrite(object v, int timeoutMs)
        {
            Writes++; double d = Convert.ToDouble(v, CultureInfo.InvariantCulture); Written.Add(d);
            bool ok = WriteOk && !(Writes >= FailWritesFrom && Writes <= FailWritesTo);
            if (ok) Taken.Add(d);
            return ok;
        }

        /// <summary>The largest change between two successive values the rig TOOK.</summary>
        public double MaxTakenStep()
        {
            double m = 0;
            for (int i = 1; i < Taken.Count; i++) m = Math.Max(m, Math.Abs(Taken[i] - Taken[i - 1]));
            return m;
        }
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
            // content between the 10 ms grid points: a 5 deg 50 Hz wiggle reads 0 at every k*10 ms but not between
            int na = 2001; double[] ta = new double[na], spa = new double[na];
            for (int i = 0; i < na; i++) { ta[i] = i / 1000.0; spa[i] = 5 * Math.Sin(2 * Math.PI * 50 * ta[i]); }
            p = Player(null, "alias50hz");
            Report("refuse_alias_between_grid_points", Throws(p, ta, spa, out msg) && msg.Contains("within 10 ms"), msg);
            p = Player(null, "spike");
            Report("refuse_1ms_spike_off_grid", Throws(p, T(0, 0.504, 0.505, 0.506, 1), T(0, 0, 30, 0, 0), out msg) && msg.Contains("within 10 ms"), msg);
            p = Player(null, "rdps0"); p.ReturnDps = 0;
            Report("refuse_return_speed_0", Throws(p, t, sp, out msg) && msg.Contains("return speed"), msg);
            p = Player(null, "rdps1000"); p.ReturnDps = 1000;
            Report("refuse_return_speed_1000", Throws(p, t, sp, out msg) && msg.Contains("return speed"), msg);
            Report("period_is_not_settable", !typeof(RigPlayer).GetProperty("PeriodMs").CanWrite && new RigPlayer().PeriodMs == RigGuard.PeriodMs, "");
            p = Player(new FakeSocket(), "enc_is_sp"); p.EncoderUrl = p.Url;
            Report("refuse_encoder_equal_to_setpoint", Throws(p, t, sp, out msg) && msg.Contains("is the setpoint variable"), msg);
            GuardReport cols = RigGuard.Parse(new string[] { "0,0", "1,0,99" }, out tn, out spn);
            Report("refuse_three_columns", cols != null && cols.Kind == "parse", cols == null ? "accepted" : cols.Reason);

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
            double pk, st, lv; int cnt; string[] rows;
            Sent(Rows(p), out pk, out st, out lv, out cnt);
            Report("ramp_in_from_current", p.Status == "done" && Has(Rows(p), "ramp_in") && f.Written.Count > 0 && Math.Abs(f.Written[0] - 3) < 0.2 && lv == 0 && st <= 6,
                   p.Status + " first=" + (f.Written.Count > 0 ? f.Written[0].ToString(Inv) : "-") + " maxstep=" + st.ToString(Inv));
            f = new FakeSocket(); f.Data.Value = 10;                                // STOP during the ramp-in: no play
            p = Player(f, "stop_in_ramp"); p.Start(t, sp); p.Stop(); p.Join(); rows = Rows(p);
            Sent(rows, out pk, out st, out lv, out cnt);
            Report("stop_during_ramp_in_cancels_the_play", p.Status.StartsWith("cancelled") && !Has(rows, "play") && lv == 0 && f.Written[f.Written.Count - 1] == 0, p.Status);

            // 4. the per-write backstop: stops the play and returns to 0 at the return speed
            double[] tb, spb; Slow(45, out tb, out spb);           // passes nothing but the parser; bypass to reach the backstop
            p = Player(null, "bypass_real"); p.DryRun = false; p.TestBypassLoadChecks = true;
            Report("bypass_refused_against_the_rig", Throws(p, tb, spb, out msg) && msg.Contains("fake socket"), msg);
            p = Player(null, "backstop_limit"); p.TestBypassLoadChecks = true; p.Start(tb, spb); p.Join();
            rows = Rows(p);
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

            // 5. failed writes: a guarded return to 0 first; RIG HOLDING only when that cannot get through
            string flagDir = Path.Combine(work, "sent");
            RigGuard.ClearHoldingFlag(flagDir);
            f = new FakeSocket(); f.WriteOk = false;
            p = Player(f, "writefail"); p.Start(t, sp); p.Join(); rows = Rows(p);
            Report("write_failures_try_to_return_then_say_the_rig_holds", p.Holding && p.Status.StartsWith("RIG HOLDING") && f.Writes > 20
                   && Has(rows, "abort_return") && p.LastLog.Contains("_error") && string.Join("\n", rows).Contains("write failed"),
                   p.Status + " writes=" + f.Writes);
            Report("rig_holding_leaves_the_flag", RigGuard.ReadHoldingFlag(flagDir) != null && RigGuard.ReadHoldingFlag(flagDir).Contains("RIG HOLDING"), "");
            f = new FakeSocket();
            p = Player(f, "flag_noack");
            Report("holding_flag_unacknowledged_refuses", Throws(p, t, sp, out msg) && msg.Contains("HOLDING.flag") && f.Writes == 0
                   && p.LastLog.Contains("_refused") && RigGuard.ReadHoldingFlag(flagDir) != null, msg);
            p = Player(null, "flag_dry");                                          // a dry run sends nothing: not asked
            Report("holding_flag_ignored_by_a_dry_run", !Throws(p, t, sp, out msg) && p.Status == "done" && RigGuard.ReadHoldingFlag(flagDir) != null, p.Status + msg);
            f = new FakeSocket();
            p = Player(f, "flag_ack"); p.AckHolding = true;
            Report("holding_flag_acknowledged_plays_and_is_deleted", !Throws(p, t, sp, out msg) && p.Status == "done" && f.Writes > 0
                   && RigGuard.ReadHoldingFlag(flagDir) == null && !p.AckHolding, p.Status + msg);
            // 5b. the rig took writes 1..59, missed 60..79 (mid-rise of a 35 deg swing), takes them again from 80:
            //     play ends at the first failure, and the return steps from what the rig took (no jump on recovery)
            double[] t35, sp35; Slow(35, out t35, out sp35);
            f = new FakeSocket(); f.FailWritesFrom = 60; f.FailWritesTo = 79;
            p = Player(f, "intermittent"); p.Start(t35, sp35); p.Join(); rows = Rows(p);
            int firstFail = -1, playAfter = 0;
            for (int i = 1; i < rows.Length; i++)
            {
                string[] c = rows[i].Split(',');
                if (firstFail < 0 && rows[i].Contains("write failed")) firstFail = i;
                else if (firstFail >= 0 && c.Length > 2 && c[2] == "play") playAfter++;
            }
            Report("intermittent_write_failures_never_jump_the_rig", !p.Holding && p.Status.Contains("ramped back to 0") && f.MaxTakenStep() <= 6 + 1e-9
                   && f.Taken[f.Taken.Count - 1] == 0 && firstFail > 0 && rows[firstFail].Split(',')[2] == "play" && playAfter == 0
                   && p.LastLog.Contains("_error") && RigGuard.ReadHoldingFlag(flagDir) == null,
                   p.Status + " max taken step=" + f.MaxTakenStep().ToString(Inv) + " play rows after the failure=" + playAfter);

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
            Report("watchdog_stops_when_the_encoder_goes_silent", p.Status.Contains("could not be read 5 times"), p.Status);
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
            // against the last value the rig TOOK and the last one tried: within 6 deg of both
            Report("unit_backstop_acked_limits_a_recovery_jump", RigGuard.Backstop(12, 2, 5, 40, out evt) == 8 && evt != null, evt);
            Report("unit_backstop_attempt_limits_too", RigGuard.Backstop(-5, 2, 5, 40, out evt) == -1 && evt != null, evt);
            Report("unit_backstop_two_quiet", RigGuard.Backstop(4, 2, 5, 40, out evt) == 4 && evt == null, "");
            Report("unit_ramp_step_from_acked", RigGuard.RampStep(10, 0, 50) == 9.5 && RigGuard.RampStep(0.3, 0, 50) == 0 && Math.Abs(RigGuard.RampStep(-10, 0, 10) + 9.9) < 1e-12, "");
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
