// Plays a rig file into StretchExperiment's setpoint shared variable: the C# twin of play_setpoints.ps1.
// Same rules: refuse a file over MaxDeg or not starting at 0, ramp in from the current setpoint, write the
// file's angle AT THE ELAPSED CLOCK TIME every 10 ms, Stop ramps back to 0, the end holds 2 s then ramps
// to 0, every write is logged. NI DataSocket (CWDSLib.CWDataSocket, installed with LabVIEW) is late-bound,
// so there is no interop assembly to ship; its write mode is 4 and read mode 2 (nids.dll type library).
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

        public string Url = "";
        public double MaxDeg = 45, ReturnDps = 10;
        public int PeriodMs = 10;
        public bool DryRun = true;
        public string LogDir = "sent";

        // read by the UI thread while playing
        public volatile bool Running;
        public double FileTimeS = -1;          // position in the file now (-1 when not in the file)
        public double LastSentDeg;
        public string Status = "";
        public string LastLog = "";
        public double MedianGapMs, P95GapMs, WorstGapMs;

        volatile bool stopAsked;
        Thread thread;
        object writer, reader;
        int failures;
        StringBuilder sent;
        System.Diagnostics.Stopwatch sw;

        public static string Check(double[] t, double[] sp, double maxDeg)
        {
            if (sp.Length < 2) return "the file has fewer than two rows";
            if (Math.Abs(t[0]) > 1e-6) return "the file's time must start at 0";
            if (Math.Abs(sp[0]) > 0.01) return "the file's first angle must be 0 (it is " + sp[0].ToString("F2", CultureInfo.InvariantCulture) + ")";
            double peak = 0; foreach (double v in sp) peak = Math.Max(peak, Math.Abs(v));
            if (peak > maxDeg) return string.Format(CultureInfo.InvariantCulture, "the movement reaches {0:F1} deg; the limit is {1:F1} deg", peak, maxDeg);
            return null;
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

        public void Start(double[] t, double[] sp)
        {
            string why = Check(t, sp, MaxDeg);
            if (why != null) throw new InvalidOperationException(why);
            if (Running) throw new InvalidOperationException("already playing");
            stopAsked = false; Running = true; Status = "starting";
            thread = new Thread(delegate() { Run(t, sp); });
            thread.SetApartmentState(ApartmentState.STA);         // ActiveX control: single-threaded apartment
            thread.IsBackground = true;
            thread.Priority = ThreadPriority.AboveNormal;
            thread.Start();
        }

        public void Stop() { stopAsked = true; }

        void Send(double v, string phase, double tf)
        {
            LastSentDeg = v;
            sent.AppendLine(string.Format(CultureInfo.InvariantCulture, "{0:F4},{1:F4},{2},{3:F4}", sw.Elapsed.TotalSeconds, tf, phase, v));
            if (DryRun) return;
            bool ok;
            try { ok = (bool)Call(writer, "SyncWrite", (object)v, 100); } catch { ok = false; }
            if (ok) failures = 0;
            else if (++failures >= 20)
                throw new IOException(string.Format(CultureInfo.InvariantCulture,
                    "20 writes in a row failed ({0}); the rig holds the last value it received, {1:F2} deg", SafeMsg(writer), v));
        }

        static string SafeMsg(object s) { try { return (string)Get(s, "LastMessage"); } catch { return "no message"; } }

        void WaitUntil(long dueMs)
        {
            // yield, never Sleep(1): Windows may ignore the 1 ms timer request and Sleep(1) then lasts 15.6 ms
            while (sw.ElapsedMilliseconds < dueMs) Thread.Sleep(0);
        }

        long NextTick(long t0) { return t0 + ((sw.ElapsedMilliseconds - t0) / PeriodMs + 1) * PeriodMs; }

        void Ramp(double from, double to, string phase)
        {
            double dist = Math.Abs(to - from);
            if (dist < 0.01) { Send(to, phase, -1); return; }
            double secs = dist / ReturnDps; long t0 = sw.ElapsedMilliseconds;
            while (true)
            {
                double e = (sw.ElapsedMilliseconds - t0) / 1000.0;
                if (e >= secs) { Send(to, phase, -1); break; }
                Send(from + (to - from) * e / secs, phase, -1);
                WaitUntil(NextTick(t0));
            }
        }

        static double At(double[] t, double[] sp, double sec)
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

        void Run(double[] t, double[] sp)
        {
            sent = new StringBuilder("t_wall_s,t_file_s,phase,setpoint_deg\r\n");
            sw = System.Diagnostics.Stopwatch.StartNew();
            var stamps = new System.Collections.Generic.List<long>();
            timeBeginPeriod(1);
            try
            {
                double? current = null;
                if (!DryRun)
                {
                    writer = NewSocket();
                    if (!(bool)Call(writer, "SyncConnectTo", Url, CwdsWrite, 5000))
                        throw new IOException("cannot connect to " + Url + ": " + SafeMsg(writer) + ". Check the address in Distributed System Manager.");
                    try
                    {
                        reader = NewSocket();
                        if ((bool)Call(reader, "SyncConnectTo", Url, CwdsRead, 3000) && (bool)Call(reader, "SyncRead", 2000))
                            current = Convert.ToDouble(Get(Get(reader, "Data"), "Value"), CultureInfo.InvariantCulture);
                    }
                    catch { current = null; }
                }
                if (current.HasValue && Math.Abs(current.Value) > 0.5)
                {
                    Status = string.Format(CultureInfo.InvariantCulture, "ramping from {0:F1} to 0 first", current.Value);
                    Ramp(current.Value, 0.0, "ramp_in");
                    WaitUntil(sw.ElapsedMilliseconds + 1000);
                }
                double dur = t[t.Length - 1];
                Status = DryRun ? "dry run (nothing sent)" : "playing";
                long t0 = sw.ElapsedMilliseconds;
                bool aborted = false;
                while (true)
                {
                    long now = sw.ElapsedMilliseconds; double e = (now - t0) / 1000.0;
                    stamps.Add(now);
                    FileTimeS = Math.Min(e, dur);
                    Send(At(t, sp, e), "play", e);
                    if (e >= dur) break;
                    if (stopAsked) { aborted = true; break; }
                    WaitUntil(NextTick(t0));
                }
                FileTimeS = -1;
                if (aborted) { Status = "stopped: returning to 0"; Ramp(LastSentDeg, 0.0, "abort_return"); Status = "stopped by you; back at 0"; }
                else
                {
                    long hold = sw.ElapsedMilliseconds + 2000;
                    while (sw.ElapsedMilliseconds < hold) { Send(sp[sp.Length - 1], "hold", -1); WaitUntil(sw.ElapsedMilliseconds + PeriodMs); }
                    Status = "returning to 0"; Ramp(sp[sp.Length - 1], 0.0, "return");
                    Status = "done";
                }
            }
            catch (Exception ex) { Status = "STOPPED: " + ex.Message; }
            finally
            {
                timeEndPeriod(1);
                FileTimeS = -1;
                foreach (object s in new object[] { writer, reader })
                    if (s != null) { try { Call(s, "SyncDisconnect", 1000); } catch { } }
                writer = reader = null;
                Gaps(stamps);
                try
                {
                    Directory.CreateDirectory(LogDir);
                    LastLog = Path.Combine(LogDir, DateTime.Now.ToString("yyyyMMdd-HHmmss", CultureInfo.InvariantCulture) + (DryRun ? "_dryrun" : "") + ".csv");
                    File.WriteAllText(LastLog, sent.ToString());
                }
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
