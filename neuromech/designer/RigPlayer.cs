// Plays a rig file into StretchExperiment's setpoint shared variable: the C# twin of play_setpoints.ps1.
// The safety rules are not here: both players take them from RigGuard.cs (labview/player/RigGuard.cs), so
// they cannot drift. Here: refuse what RigGuard.Check refuses (and log the refusal), refuse to start if the
// current setpoint cannot be read (never a step to 0 from wherever the arm is), ramp in from the current
// setpoint, write the file's angle AT THE ELAPSED CLOCK TIME every 10 ms through RigGuard.Backstop, stop and
// ramp back to 0 at 10 deg/s if the backstop ever engages while playing, Stop ramps back to 0, the end holds
// 2 s then ramps to 0, every write and every guard event is logged. Only one player writes at a time: a
// machine-wide lock (RigGuard.AcquireWriterLock) is held from before the ramp-in until the return to 0.
// Optional encoder watchdog (EncoderUrl): reads the arm's actual position every 50 ms while playing and stops
// the content and ramps the setpoint to 0 if the arm is past the limit + 3 deg or further from every recent
// setpoint than the following-error bound. It runs in this process: a second opinion, not a hardware cutoff.
//
// The backstop limits every write to 6 deg from the last value the rig TOOK (SyncWrite returned true) and from
// the last value tried. Any failed write during play or the end hold ends the play at once and starts the
// guarded return to 0. Every ramp is write-indexed: each write is one step from the last value the rig took,
// so a failed or late write is retried, never jumped over; it runs until the rig has taken the target (or
// RigGuard.RampBudgetMs has passed). Any error after the first write first tries that guarded ramp to 0; if it
// cannot get through, Status says RIG HOLDING LAST SETPOINT and sent\HOLDING.flag is left for the next start.
// Start refuses while that flag exists unless AckHolding is set (the UI asks the operator first).
//
// What this cannot do: if writes stop getting through, the rig HOLDS the last value it received; if this
// program is killed mid-play the rig also holds the last setpoint. The E-stop is the answer to both.
//
// NI DataSocket (CWDSLib.CWDataSocket, installed with LabVIEW) is late-bound, so there is no interop assembly
// to ship; its write mode is 4 and read mode 2 (nids.dll type library).
using System;
using System.Globalization;
using System.IO;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;

namespace Pendulastic
{
    public class RigPlayer
    {
        const int CwdsRead = 2, CwdsWrite = 4;
        [DllImport("winmm.dll")] static extern uint timeBeginPeriod(uint ms);
        [DllImport("winmm.dll")] static extern uint timeEndPeriod(uint ms);

        public delegate object SocketMaker();

        public string Url = "";
        public double MaxDeg = RigGuard.HardLimitDeg, ReturnDps = RigGuard.DefaultReturnDps;
        /// <summary>Fixed: the 6 deg-per-write speed rule assumes it.</summary>
        public int PeriodMs { get { return RigGuard.PeriodMs; } }
        public bool DryRun = true;
        public string LogDir = "sent";
        public string LogName = "";               // optional prefix for the sent log's file name
        /// <summary>Test seam: a stand-in for NI DataSocket. Anything other than NewSocket is a test.</summary>
        public SocketMaker MakeSocket = NewSocket;
        /// <summary>Test only: skip the load checks to exercise the per-write backstop. Honoured only in a dry run
        /// or with a fake socket, never against the rig.</summary>
        public bool TestBypassLoadChecks;
        /// <summary>Optional: the arm's ACTUAL position as a readable shared variable. Empty = encoder watchdog OFF.</summary>
        public string EncoderUrl = "";
        public SocketMaker MakeEncoderSocket = NewSocket;
        public string WatchdogState = "encoder watchdog OFF";

        // read by the UI thread while playing
        public volatile bool Running;
        public double FileTimeS = -1;          // position in the file now (-1 when not in the file)
        public double LastSentDeg;
        public string Status = "";
        public string LastLog = "";
        public string GuardEvent = "";         // the first backstop event of the last run ("" if none)
        public double MedianGapMs, P95GapMs, WorstGapMs;

        volatile bool stopAsked;
        Thread thread;
        object writer, reader;
        bool lastWriteOk = true;
        string lastWriteWhy = "";
        /// <summary>The last value the rig TOOK (a write that returned true): what it holds now.</summary>
        public double LastAckedDeg;
        /// <summary>True after an error whose guarded return to 0 could not get through.</summary>
        public bool Holding;
        /// <summary>The operator acknowledged a previous RIG HOLDING event (sent\HOLDING.flag); Start then deletes
        /// the flag. Without it Start refuses while the flag exists.</summary>
        public bool AckHolding;
        StringBuilder sent;
        System.Diagnostics.Stopwatch sw;
        double limit;
        string guardStop, watchdogStop;
        object encoder;
        double followBound;
        readonly double[] recent = new double[RigGuard.FollowWindowWrites + 1];
        int recentN, recentHead, encMissed;

        bool IsTest { get { return DryRun || MakeSocket != (SocketMaker)NewSocket; } }

        /// <summary>The load-time verdict both players give (null when playable).</summary>
        public static string Check(double[] t, double[] sp, double maxDeg)
        {
            GuardReport r = RigGuard.Check(t, sp, maxDeg);
            return r.Ok ? null : r.Reason;
        }

        public static object NewSocket()
        {
            Type ty = Type.GetTypeFromProgID("CWDSLib.CWDataSocket");
            if (ty == null) throw new InvalidOperationException("NI DataSocket (CWDSLib) is not registered for this program's width ("
                + (IntPtr.Size * 8) + "-bit). Try " + (IntPtr.Size == 4 ? "PendulumDesigner64.exe" : "PendulumDesigner.exe") + ".");
            try { return Activator.CreateInstance(ty); }
            catch (Exception ex)
            {
                throw new InvalidOperationException("NI DataSocket would not start in this " + (IntPtr.Size * 8) + "-bit program (" + ex.Message
                    + "). Try " + (IntPtr.Size == 4 ? "PendulumDesigner64.exe" : "PendulumDesigner.exe") + ".");
            }
        }

        static object Call(object o, string name, params object[] a) { return o.GetType().InvokeMember(name, BindingFlags.InvokeMethod, null, o, a); }
        static object Get(object o, string name) { return o.GetType().InvokeMember(name, BindingFlags.GetProperty, null, o, null); }

        /// <summary>NI's own variable picker. Must run on an STA thread (the UI thread is one).</summary>
        public static string PickUrl()
        {
            object s = NewSocket();
            object m = Missing.Value;
            bool ok = (bool)Call(s, "SelectURL", m, m, m, m);
            return ok ? (string)Get(s, "URL") : null;
        }

        string NewLogPath(string tag)
        {
            Directory.CreateDirectory(LogDir);
            string stem = (LogName.Length > 0 ? LogName + "_" : "") + DateTime.Now.ToString("yyyyMMdd-HHmmss", CultureInfo.InvariantCulture);
            return Path.Combine(LogDir, stem + tag + ".csv");
        }

        /// <summary>Write the one-row refusal log (so a refusal is on record next to the runs). Never throws.</summary>
        public void LogRefusal(string kind, string reason)
        {
            try { LastLog = NewLogPath("_refused"); File.WriteAllText(LastLog, RigGuard.RefusalLog(kind, reason)); }
            catch (Exception ex) { LastLog = "(could not write the refusal log: " + ex.Message + ")"; }
        }

        public void Start(double[] t, double[] sp)
        {
            if (Running) throw new InvalidOperationException("already playing");
            string lim = RigGuard.CheckLimit(MaxDeg);
            if (lim != null) { LogRefusal("operator_limit", lim); throw new InvalidOperationException(lim); }
            string rd = RigGuard.CheckReturnDps(ReturnDps);
            if (rd != null) { LogRefusal("return_speed", rd); throw new InvalidOperationException(rd); }
            string eu = DryRun ? null : RigGuard.EncoderUrlProblem(EncoderUrl, Url);
            if (eu != null) { LogRefusal("encoder", eu); throw new InvalidOperationException(eu); }
            if (!DryRun)
            {
                string flag = RigGuard.ReadHoldingFlag(LogDir);
                if (flag != null)
                {
                    if (!AckHolding)
                    {
                        string m = "a previous run ended with the rig holding a setpoint (sent\\HOLDING.flag: " + flag + "); acknowledge it first";
                        LogRefusal("holding_unacknowledged", m); throw new InvalidOperationException(m);
                    }
                    string cw = RigGuard.ClearHoldingFlag(LogDir);
                    if (cw != null)
                    {
                        string m = "acknowledged, but sent\\HOLDING.flag could not be deleted (" + cw + ")";
                        LogRefusal("holding_unacknowledged", m); throw new InvalidOperationException(m);
                    }
                }
                AckHolding = false;
            }
            if (TestBypassLoadChecks && !IsTest)
                throw new InvalidOperationException("the load checks can only be bypassed in a dry run or against a fake socket");
            if (!TestBypassLoadChecks)
            {
                GuardReport r = RigGuard.Check(t, sp, MaxDeg);
                if (!r.Ok) { LogRefusal(r.Kind, r.Reason); throw new InvalidOperationException(r.Reason); }
            }
            limit = MaxDeg;
            followBound = RigGuard.FollowBoundDeg(RigGuard.PredictedFollowingError(RigGuard.Stream(t, sp)));
            recentN = 0; recentHead = 0; encMissed = 0;
            stopAsked = false; Running = true; Status = "starting"; GuardEvent = ""; guardStop = null; watchdogStop = null;
            Holding = false; lastWriteOk = true; lastWriteWhy = "";
            WatchdogState = EncoderUrl.Trim().Length > 0 && !DryRun ? "encoder watchdog ON" : "encoder watchdog OFF";
            thread = new Thread(delegate() { Run(t, sp); });
            thread.SetApartmentState(ApartmentState.STA);         // ActiveX control: single-threaded apartment
            thread.IsBackground = true;
            thread.Priority = ThreadPriority.AboveNormal;
            thread.Start();
        }

        public void Stop() { stopAsked = true; }

        /// <summary>Wait for the play thread (tests).</summary>
        public void Join() { Thread th = thread; if (th != null) th.Join(); }

        void Note(string phase, double tf, double v, string note)
        {
            sent.AppendLine(string.Format(CultureInfo.InvariantCulture, "{0:F4},{1:F4},{2},{3:F4},{4}", sw.Elapsed.TotalSeconds, tf, phase, v, RigGuard.Note(note)));
        }

        void Send(double v, string phase, double tf)
        {
            string evt;
            double g = RigGuard.Backstop(v, LastAckedDeg, LastSentDeg, limit, out evt);
            if (evt != null)
            {
                Note("guard", tf, g, phase + ": " + evt);
                if (GuardEvent.Length == 0) GuardEvent = string.Format(CultureInfo.InvariantCulture, "{0} at {1:F2} s: {2}", phase, tf, evt);
                if (phase == "play" && guardStop == null) guardStop = GuardEvent;
            }
            LastSentDeg = g;
            recent[recentHead] = g; recentHead = (recentHead + 1) % recent.Length; if (recentN < recent.Length) recentN++;
            if (DryRun) { lastWriteOk = true; LastAckedDeg = g; Note(phase, tf, g, ""); return; }
            bool ok; string why = "";
            try { ok = (bool)Call(writer, "SyncWrite", (object)g, 100); if (!ok) why = SafeMsg(writer); }
            catch (Exception ex) { ok = false; why = ex.Message; }
            lastWriteOk = ok;
            if (ok) { LastAckedDeg = g; Note(phase, tf, g, ""); }
            else { lastWriteWhy = why; Note(phase, tf, g, "write failed: " + why); }
        }

        /// <summary>Play and the end hold stop at the FIRST failed write: the rig did not take it, and carrying on
        /// with the file would jump it when the link comes back. The guarded return steps from what it took.</summary>
        void EndOnFailedWrite(string where)
        {
            if (!lastWriteOk)
                throw new IOException(string.Format(CultureInfo.InvariantCulture, "a write failed during {0} ({1}); the rig holds {2:F2} deg",
                    where, lastWriteWhy, LastAckedDeg));
        }

        /// <summary>Best effort after an error: ramp to 0 through the backstop, tolerating failed writes, for at most
        /// the ramp's time + RampExtraS. Sets Holding when it could not get through.</summary>
        void SafeReturn(string why)
        {
            Note("error", -1, LastAckedDeg, why);
            Status = "ERROR (" + why + "): ramping the setpoint to 0";
            bool reached;
            try { reached = writer != null || DryRun ? Ramp(0.0, "abort_return") : false; }
            catch (Exception ex) { reached = false; Note("error", -1, LastAckedDeg, "the error return failed: " + ex.Message); }
            if (reached)
            {
                Note("error", -1, 0, "error return reached 0");
                Status = "STOPPED by an error (" + why + "); the setpoint was ramped back to 0";
            }
            else
            {
                Holding = true;
                string msg = string.Format(CultureInfo.InvariantCulture,
                    "RIG HOLDING LAST SETPOINT -- USE THE E-STOP (last value the rig took {0:F2} deg, last tried {1:F2} deg)", LastAckedDeg, LastSentDeg);
                Note("error", -1, LastAckedDeg, msg);
                Status = msg + " if the arm is not at rest. Error: " + why;
                if (!DryRun) RigGuard.WriteHoldingFlag(LogDir, "Pendulum Designer " + LogName, msg + "\r\nerror: " + why);
            }
        }

        static string SafeMsg(object s) { try { return (string)Get(s, "LastMessage"); } catch { return "no message"; } }

        void WaitUntil(long dueMs)
        {
            // yield, never Sleep(1): Windows may ignore the 1 ms timer request and Sleep(1) then lasts 15.6 ms
            while (sw.ElapsedMilliseconds < dueMs) Thread.Sleep(0);
        }

        long NextTick(long t0) { return t0 + ((sw.ElapsedMilliseconds - t0) / PeriodMs + 1) * PeriodMs; }

        /// <summary>Write-indexed: each write is one step (ReturnDps x 10 ms) from the last value the rig TOOK, so a
        /// failed or late write is retried from where the rig is, never jumped over. Runs until the rig has taken the
        /// target, or until RigGuard.RampBudgetMs. Returns whether it got there. STOP during the ramp-in cancels the play.</summary>
        bool Ramp(double to, string phase)
        {
            long t0 = sw.ElapsedMilliseconds;
            long deadline = t0 + RigGuard.RampBudgetMs(to - LastAckedDeg, ReturnDps);
            while (true)
            {
                Send(RigGuard.RampStep(LastAckedDeg, to, ReturnDps), phase, -1);
                if (lastWriteOk && Math.Abs(LastAckedDeg - to) < 1e-9) return true;
                if (sw.ElapsedMilliseconds >= deadline) return false;
                WaitUntil(NextTick(t0));
            }
        }

        /// <summary>The setpoint the rig holds now, or a refusal. Never guesses: no reading, no play.</summary>
        double ReadCurrent()
        {
            double cur;
            try
            {
                reader = MakeSocket();
                if (!(bool)Call(reader, "SyncConnectTo", Url, CwdsRead, 3000) || !(bool)Call(reader, "SyncRead", 2000))
                    throw new IOException(SafeMsg(reader));
                cur = Convert.ToDouble(Get(Get(reader, "Data"), "Value"), CultureInfo.InvariantCulture);
            }
            catch (Exception ex)
            {
                throw new GuardRefusal("setpoint_read", "could not read the current setpoint back (" + ex.Message + "), so the start cannot be"
                    + " ramped from it; not played. Check the variable in Distributed System Manager, put the arm at 0 (Manual Control"
                    + " -> Goto Position 0) and try again");
            }
            if (double.IsNaN(cur) || double.IsInfinity(cur))
                throw new GuardRefusal("setpoint_read", "the current setpoint reads " + cur.ToString(CultureInfo.InvariantCulture) + "; not played");
            if (Math.Abs(cur) > limit)
                throw new GuardRefusal("setpoint_read", string.Format(CultureInfo.InvariantCulture,
                    "the current setpoint reads {0:F2} deg, beyond the {1:F1} deg limit; bring the arm back with Manual Control first. Not played", cur, limit));
            return cur;
        }

        bool ReadEncoder(int timeoutMs, out double actual, out string why)
        {
            actual = double.NaN; why = "";
            try
            {
                if (!(bool)Call(encoder, "SyncRead", timeoutMs)) { why = SafeMsg(encoder); return false; }
                actual = Convert.ToDouble(Get(Get(encoder, "Data"), "Value"), CultureInfo.InvariantCulture);
                return true;
            }
            catch (Exception ex) { why = ex.Message; return false; }
        }

        /// <summary>One watchdog look at the arm (every RigGuard.WatchdogEveryTicks-th write while playing).</summary>
        void Watch(double tf)
        {
            if (encoder == null || watchdogStop != null) return;
            double a; string why;
            if (!ReadEncoder(RigGuard.WatchdogReadTimeoutMs, out a, out why))
            {
                if (++encMissed >= RigGuard.WatchdogMaxMissed)
                    watchdogStop = string.Format(CultureInfo.InvariantCulture, "the encoder could not be read {0} times in a row ({1})", encMissed, why);
            }
            else
            {
                encMissed = 0;
                Note("encoder", tf, a, "");
                string bad = RigGuard.WatchdogCheck(a, recent, recentN, limit, followBound);
                if (bad != null) watchdogStop = string.Format(CultureInfo.InvariantCulture, "at {0:F2} s {1}", tf, bad);
            }
            if (watchdogStop != null) Note("watchdog", tf, LastSentDeg, watchdogStop);
        }

        void ConnectEncoder(double current)
        {
            try
            {
                encoder = MakeEncoderSocket();
                if (!(bool)Call(encoder, "SyncConnectTo", EncoderUrl.Trim(), CwdsRead, 3000)) throw new IOException(SafeMsg(encoder));
            }
            catch (Exception ex)
            {
                throw new GuardRefusal("encoder", "the encoder watchdog is set up (" + EncoderUrl + ") but cannot connect (" + ex.Message
                    + "). Fix it, or clear the encoder variable to play with the watchdog OFF");
            }
            double a; string why;
            if (!ReadEncoder(2000, out a, out why))
                throw new GuardRefusal("encoder", "the encoder watchdog cannot read " + EncoderUrl + " (" + why + ")");
            if (double.IsNaN(a) || double.IsInfinity(a) || Math.Abs(a - current) > RigGuard.FollowFloorDeg)
                throw new GuardRefusal("encoder", string.Format(CultureInfo.InvariantCulture,
                    "the encoder variable reads {0:F2} deg while the setpoint reads {1:F2} deg: is it the arm's actual position? Not played", a, current));
            Note("encoder", -1, a, "watchdog ON; following-error bound " + followBound.ToString("F2", CultureInfo.InvariantCulture) + " deg");
        }

        class CancelledInRamp : Exception { }

        class GuardRefusal : Exception
        {
            public readonly string Kind;
            public GuardRefusal(string kind, string msg) : base(msg) { Kind = kind; }
        }

        void Run(double[] t, double[] sp)
        {
            sent = new StringBuilder(RigGuard.SentHeader + "\r\n");
            sw = System.Diagnostics.Stopwatch.StartNew();
            var stamps = new System.Collections.Generic.List<long>();
            string tag = DryRun ? "_dryrun" : "";
            System.Threading.Mutex writerLock = null;
            bool started = false;                       // true from the first write on: errors after it return to 0
            timeBeginPeriod(1);
            try
            {
                double current = 0.0;
                if (!DryRun)
                {
                    // one writer at a time, held from before the ramp-in until the return to 0 is complete
                    string lockWhy;
                    writerLock = RigGuard.AcquireWriterLock(out lockWhy);
                    if (writerLock == null) throw new GuardRefusal("writer_lock", lockWhy);
                    writer = MakeSocket();
                    if (!(bool)Call(writer, "SyncConnectTo", Url, CwdsWrite, 5000))
                        throw new IOException("cannot connect to " + Url + ": " + SafeMsg(writer) + ". Check the address in Distributed System Manager.");
                    current = ReadCurrent();
                    if (EncoderUrl.Trim().Length > 0) ConnectEncoder(current);
                    else Note("encoder", -1, double.NaN, "encoder watchdog OFF (no encoder variable picked)");
                }
                LastSentDeg = current; LastAckedDeg = current;
                recentN = 0; recentHead = 0;
                started = true;
                // warm up before any clock-indexed ramp or play (first COM write, first JIT): a slow first write
                // would make the next one jump along the ramp or into the file. Re-sends the held value.
                RigGuard.At(t, sp, 0.0);
                Send(LastSentDeg, "ramp_in", -1);
                EndOnFailedWrite("the first write");
                if (Math.Abs(current) > 0.5)
                {
                    Status = string.Format(CultureInfo.InvariantCulture, "ramping from {0:F1} to 0 first", current);
                    if (!Ramp(0.0, "ramp_in")) throw new IOException("the ramp-in did not reach 0");
                    if (stopAsked) throw new CancelledInRamp();
                    WaitUntil(sw.ElapsedMilliseconds + 1000);
                    if (stopAsked) throw new CancelledInRamp();
                }
                WaitUntil(sw.ElapsedMilliseconds + PeriodMs);   // one tick between the last held write and the first play write
                double dur = t[t.Length - 1];
                Status = DryRun ? "dry run (nothing sent)" : "playing";
                long t0 = sw.ElapsedMilliseconds;
                bool aborted = false;
                int tick = 0;
                while (true)
                {
                    long now = sw.ElapsedMilliseconds; double e = (now - t0) / 1000.0;
                    stamps.Add(now);
                    FileTimeS = Math.Min(e, dur);
                    Send(RigGuard.At(t, sp, e), "play", e);
                    EndOnFailedWrite("play");
                    if (guardStop != null) { aborted = true; break; }
                    if (++tick % RigGuard.WatchdogEveryTicks == 0) Watch(e);
                    if (watchdogStop != null) { aborted = true; break; }
                    if (e >= dur) break;
                    if (stopAsked) { aborted = true; break; }
                    WaitUntil(NextTick(t0));
                }
                FileTimeS = -1;
                bool back;
                if (guardStop != null)
                {
                    Status = "STOPPED by the guard: returning to 0";
                    Note("guard", -1, LastSentDeg, "playback stopped by the backstop: " + guardStop);
                    tag += "_guard";
                    back = Ramp(0.0, "abort_return");
                    Status = "STOPPED by the guard (" + guardStop + "); back at 0";
                }
                else if (watchdogStop != null)
                {
                    Status = "STOPPED by the encoder watchdog: returning to 0";
                    tag += "_watchdog";
                    back = Ramp(0.0, "abort_return");
                    Status = "STOPPED by the encoder watchdog (" + watchdogStop + "); setpoint back at 0. Check the arm; use the E-stop if it is not following";
                }
                else if (aborted)
                {
                    Status = "stopped: returning to 0";
                    Note("stop", -1, LastSentDeg, "stopped by STOP");
                    back = Ramp(0.0, "abort_return");
                    Status = "stopped by you; back at 0";
                }
                else
                {
                    long hold = sw.ElapsedMilliseconds + 2000; int ht = 0;
                    while (sw.ElapsedMilliseconds < hold && watchdogStop == null && !stopAsked)
                    {
                        Send(sp[sp.Length - 1], "hold", -1);
                        EndOnFailedWrite("the end hold");
                        if (++ht % RigGuard.WatchdogEveryTicks == 0) Watch(-1);
                        WaitUntil(sw.ElapsedMilliseconds + PeriodMs);
                    }
                    if (watchdogStop != null) { Status = "STOPPED by the encoder watchdog (" + watchdogStop + "); returning to 0"; tag += "_watchdog"; }
                    if (watchdogStop == null) Status = "returning to 0";
                    back = Ramp(0.0, "return");
                    Status = watchdogStop == null ? "done" : "STOPPED by the encoder watchdog (" + watchdogStop + "); setpoint back at 0";
                }
                if (!back) throw new IOException("the return to 0 did not complete in time");
            }
            catch (GuardRefusal ex)
            {
                Status = "REFUSED: " + ex.Message;
                Note("refused", -1, double.NaN, ex.Kind + ": " + ex.Message);
                tag += "_refused";
            }
            catch (CancelledInRamp)
            {
                Note("stop", -1, LastSentDeg, "cancelled during the ramp-in; the setpoint is at 0");
                Status = "cancelled during the ramp-in; the setpoint is at 0. Not played";
            }
            catch (Exception ex)
            {
                if (started) { SafeReturn(ex.Message); tag += "_error"; }
                else { Status = "STOPPED: " + ex.Message; Note("error", -1, LastSentDeg, ex.Message); }
            }
            finally
            {
                timeEndPeriod(1);
                FileTimeS = -1;
                foreach (object s in new object[] { writer, reader, encoder })
                    if (s != null) { try { Call(s, "SyncDisconnect", 1000); } catch { } }
                writer = reader = encoder = null;
                if (writerLock != null) { try { writerLock.ReleaseMutex(); } catch { } writerLock.Close(); }
                Gaps(stamps);
                try { LastLog = NewLogPath(tag); File.WriteAllText(LastLog, sent.ToString()); }
                catch (Exception ex) { LastLog = "(could not write the log: " + ex.Message + ")"; }
                Running = false;
            }
        }

        void Gaps(System.Collections.Generic.List<long> s)
        {
            if (s.Count < 2) return;
            var g = new double[s.Count - 1];
            for (int i = 1; i < s.Count; i++) g[i - 1] = s[i] - s[i - 1];
            Array.Sort(g);
            MedianGapMs = g[g.Length / 2]; P95GapMs = g[Math.Min(g.Length - 1, (int)(0.95 * g.Length))]; WorstGapMs = g[g.Length - 1];
        }
    }
}
