// Headless check of the designer: every MAS preset and one parameter target, each movement written out so
// test_designer.py can rescore it with the Python scorer. Usage: SelfTest.exe <out dir>
using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Text;

namespace Pendulastic
{
    static class SelfTest
    {
        static readonly CultureInfo Inv = CultureInfo.InvariantCulture;

        static void Write(string dir, string name, DesignResult r, StringBuilder summary)
        {
            var m = r.Movement; var sb = new StringBuilder();
            for (int i = 0; i < m.KneeT.Length; i++) sb.Append(m.KneeT[i].ToString("R", Inv)).Append(',').Append(m.KneeDeg[i].ToString("R", Inv)).Append('\n');
            File.WriteAllText(Path.Combine(dir, name + "_knee.csv"), sb.ToString());
            sb.Length = 0;
            for (int i = 0; i < m.RigT.Length; i++) sb.Append(m.RigT[i].ToString("F4", Inv)).Append(',').Append(m.RigDeg[i].ToString("F4", Inv)).Append('\n');
            File.WriteAllText(Path.Combine(dir, name + "_rig.csv"), sb.ToString());
            double peak = 0; foreach (double d in m.RigDeg) peak = Math.Max(peak, Math.Abs(d));
            var x = r.Metrics; double v;
            summary.AppendLine(string.Join(",", new[] { name,
                x.Pt7.HasValue ? x.Pt7.Value.ToString("R", Inv) : "", x.Interpretable ? "1" : "0", x.Zone, x.Mas,
                x.Values.TryGetValue("R2n", out v) ? v.ToString("R", Inv) : "", x.Values.TryGetValue("N", out v) ? v.ToString("R", Inv) : "",
                peak.ToString("R", Inv), m.ReleaseT.ToString("R", Inv), r.Knobs == null ? "" : r.Knobs.Damping.ToString("R", Inv) }));
        }

        static int Main(string[] args)
        {
            string dir = args[0]; Directory.CreateDirectory(dir);
            var summary = new StringBuilder("name,pt7,interpretable,zone,mas,R2n,N,peak_rig_deg,release_t,damping\n");
            var sw = System.Diagnostics.Stopwatch.StartNew();
            foreach (string g in Designer.Grades) Write(dir, "mas_" + g.Replace("+", "p"), Designer.ForGrade(g, new Knobs()), summary);
            var t = new Dictionary<string, double> { { "R2n", 0.6 }, { "N", 3.0 } };
            Write(dir, "params_R2n0.6_N3", Designer.ForParams(t, new Knobs()), summary);
            Write(dir, "healthy_default", Designer.Evaluate(new Knobs()), summary);
            // the published severity axis by lambda (DeGroote.cs), to axis.csv: played 1:1 (axis_*), where
            // test_designer.py holds the trace and its score to neuromech.model_rt and the library's expected
            // values, and lambda 1.4 at the default 0.70 scale (lam_1.4), the swing the rig can actually play
            var axis = new StringBuilder("name,pt7,interpretable,zone,mas,R2n,N,peak_rig_deg,release_t,damping\n");
            foreach (double lam in new[] { 1.0, 1.2, 1.4 })
                Write(dir, "axis_" + lam.ToString("F1", Inv), Designer.ForLambda(lam, new Knobs { Scale = 1.0 }), axis);
            Write(dir, "lam_1.4", Designer.ForLambda(1.4, new Knobs()), axis);
            File.WriteAllText(Path.Combine(dir, "axis.csv"), axis.ToString());
            double pl, pp; Designer.RisingBranchPeak(out pl, out pp);
            var d14 = Designer.ForLambda(1.4, new Knobs());
            GuardReport g14 = RigGuard.Check(d14.Movement.RigT, d14.Movement.RigDeg, RigGuard.HardLimitDeg);
            GuardReport g11 = RigGuard.Check(Designer.ForLambda(1.4, new Knobs { Scale = 1.0 }).Movement.RigT,
                                             Designer.ForLambda(1.4, new Knobs { Scale = 1.0 }).Movement.RigDeg, RigGuard.HardLimitDeg);
            var scaled = Designer.ScaledToLimit(Designer.ForLambda(1.4, new Knobs { Scale = 1.0 }), RigGuard.HardLimitDeg, 0.2);
            File.WriteAllText(Path.Combine(dir, "axis_facts.csv"),
                "peak_lam,peak_pt7,rest_deg,ok_x070,pred_x070,ok_x1,scaled_to\n" + string.Join(",", new[] {
                    pl.ToString("R", Inv), pp.ToString("R", Inv), DeGroote.PassiveRestDeg.ToString("R", Inv),
                    g14.Ok ? "1" : "0", g14.PredictedPeakDeg.ToString("R", Inv), g11.Ok ? "1" : "0",
                    scaled == null ? "" : scaled.Knobs.Scale.ToString("R", Inv) }) + "\n");
            Console.WriteLine(d14.Note);
            File.WriteAllText(Path.Combine(dir, "summary.csv"), summary.ToString());
            Console.WriteLine(summary.ToString());
            Console.WriteLine("seconds " + sw.Elapsed.TotalSeconds.ToString("F1", Inv));
            return 0;
        }
    }
}
