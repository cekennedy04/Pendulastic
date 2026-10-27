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
            File.WriteAllText(Path.Combine(dir, "summary.csv"), summary.ToString());
            Console.WriteLine(summary.ToString());
            Console.WriteLine("seconds " + sw.Elapsed.TotalSeconds.ToString("F1", Inv));
            return 0;
        }
    }
}
