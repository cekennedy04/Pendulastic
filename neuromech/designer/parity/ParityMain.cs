// ParityMain.cs -- console harness for the PtScore C# port.
//
//   ParityMain.exe <cases_dir> <out_json> [repeats]
//
// Reads <cases_dir>/index.csv (case_id,category,file,release_t,release_t_hex) and each case CSV
// (t,angle[,t_hex,angle_hex]); scores every case with Pendulastic.PtScore.ScoreAngle and writes
// one JSON object per line to <out_json>. Floats are written as "x:<16 hex digits>" (the exact
// IEEE-754 bits) so the comparison is not blurred by decimal round-tripping; when a case CSV
// carries *_hex columns those bits are read instead of the decimal text, for the same reason.
// Built with C:\Windows\Microsoft.NET\Framework\v3.5\csc.exe by run_parity.py.

using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.Text;
using Pendulastic;

namespace Pendulastic.Parity
{
    public static class ParityMain
    {
        static readonly CultureInfo IC = CultureInfo.InvariantCulture;

        public static int Main(string[] args)
        {
            if (args.Length < 2)
            {
                Console.Error.WriteLine("usage: ParityMain <cases_dir> <out_json> [repeats]");
                return 2;
            }
            string dir = args[0];
            string outPath = args[1];
            int repeats = args.Length > 2 ? int.Parse(args[2], IC) : 3;

            string[] index = File.ReadAllLines(Path.Combine(dir, "index.csv"));
            string[] hdr = index[0].Split(',');
            int cId = Array.IndexOf(hdr, "case_id"), cFile = Array.IndexOf(hdr, "file");
            int cRel = Array.IndexOf(hdr, "release_t"), cRelHex = Array.IndexOf(hdr, "release_t_hex");

            int nCases = 0;
            double totalMs = 0;
            using (StreamWriter w = new StreamWriter(outPath, false, new UTF8Encoding(false)))
            {
                for (int li = 1; li < index.Length; li++)
                {
                    if (index[li].Trim().Length == 0) continue;
                    string[] row = index[li].Split(',');
                    string id = row[cId];
                    double rel = cRelHex >= 0 && row[cRelHex].Length > 0 ? FromHex(row[cRelHex]) : double.Parse(row[cRel], NumberStyles.Float, IC);
                    double[] t, a;
                    ReadCase(Path.Combine(dir, row[cFile]), out t, out a);

                    StringBuilder sb = new StringBuilder();
                    sb.Append("{\"case_id\":").Append(Str(id));
                    try
                    {
                        PtResult r = PtScore.ScoreAngle(t, a, rel);   // warm-up / JIT, also the result
                        double[] ms = new double[repeats];
                        for (int k = 0; k < repeats; k++)
                        {
                            Stopwatch sw = Stopwatch.StartNew();
                            PtScore.ScoreAngle(t, a, rel);
                            sw.Stop();
                            ms[k] = sw.Elapsed.TotalMilliseconds;
                        }
                        Array.Sort(ms);
                        double med = ms[repeats / 2];
                        totalMs += med;
                        Emit(sb, r);
                        sb.Append(",\"time_ms\":").Append(med.ToString("R", IC));
                    }
                    catch (Exception ex)
                    {
                        sb.Append(",\"error\":").Append(Str(ex.GetType().Name + ": " + ex.Message));
                    }
                    sb.Append("}");
                    w.WriteLine(sb.ToString());
                    nCases++;
                }
            }
            Console.WriteLine("scored " + nCases.ToString(IC) + " cases; median ScoreAngle total "
                + totalMs.ToString("F1", IC) + " ms (" + (nCases > 0 ? (totalMs / nCases).ToString("F3", IC) : "0") + " ms/case)");
            return 0;
        }

        static void ReadCase(string path, out double[] t, out double[] a)
        {
            List<double> tl = new List<double>(), al = new List<double>();
            using (StreamReader rd = new StreamReader(path))
            {
                string[] h = rd.ReadLine().Split(',');
                int it = Array.IndexOf(h, "t"), ia = Array.IndexOf(h, "angle");
                int ith = Array.IndexOf(h, "t_hex"), iah = Array.IndexOf(h, "angle_hex");
                string line;
                while ((line = rd.ReadLine()) != null)
                {
                    if (line.Length == 0) continue;
                    string[] c = line.Split(',');
                    tl.Add(ith >= 0 ? FromHex(c[ith]) : double.Parse(c[it], NumberStyles.Float, IC));
                    al.Add(iah >= 0 ? FromHex(c[iah]) : ParseLoose(c[ia]));
                }
            }
            t = tl.ToArray(); a = al.ToArray();
        }

        static double ParseLoose(string s)
        {
            string l = s.Trim().ToLowerInvariant();
            if (l == "nan" || l.Length == 0) return double.NaN;
            if (l == "inf") return double.PositiveInfinity;
            if (l == "-inf") return double.NegativeInfinity;
            return double.Parse(s, NumberStyles.Float, IC);
        }

        static double FromHex(string h)
        {
            return BitConverter.Int64BitsToDouble(long.Parse(h, NumberStyles.HexNumber, IC));
        }

        static string Hex(double v)
        {
            return "x:" + BitConverter.DoubleToInt64Bits(v).ToString("x16", IC);
        }

        static string Str(string s)
        {
            if (s == null) return "null";
            StringBuilder b = new StringBuilder("\"");
            foreach (char ch in s)
            {
                if (ch == '"') b.Append("\\\"");
                else if (ch == '\\') b.Append("\\\\");
                else if (ch < 0x20 || ch > 0x7e) b.Append("\\u").Append(((int)ch).ToString("x4", IC));
                else b.Append(ch);
            }
            return b.Append('"').ToString();
        }

        static void F(StringBuilder sb, string k, double v) { sb.Append(",\"").Append(k).Append("\":\"").Append(Hex(v)).Append('"'); }
        static void B(StringBuilder sb, string k, bool v) { sb.Append(",\"").Append(k).Append("\":").Append(v ? "true" : "false"); }
        static void S(StringBuilder sb, string k, string v) { sb.Append(",\"").Append(k).Append("\":").Append(Str(v)); }
        static void I(StringBuilder sb, string k, int v) { sb.Append(",\"").Append(k).Append("\":").Append(v.ToString(IC)); }
        static void IA(StringBuilder sb, string k, int[] v)
        {
            sb.Append(",\"").Append(k).Append("\":[");
            for (int i = 0; i < v.Length; i++) { if (i > 0) sb.Append(','); sb.Append(v[i].ToString(IC)); }
            sb.Append(']');
        }

        static void Emit(StringBuilder sb, PtResult r)
        {
            I(sb, "grid_len", r.GridLength);
            I(sb, "ridx", r.ReleaseIndex);
            B(sb, "has_params", r.HasParams);
            S(sb, "none_reason", r.NoneReason);
            if (r.HasParams)
            {
                F(sb, "R2n", r.R2n); F(sb, "N", r.N); F(sb, "phi_max_ratio", r.phi_max_ratio);
                F(sb, "omega_max_n", r.omega_max_n); F(sb, "omega_min_n", r.omega_min_n);
                F(sb, "omega_peak_deg_s", r.omega_peak_deg_s); F(sb, "f", r.f); F(sb, "area_ratio", r.area_ratio);
                B(sb, "quality_warn", r.quality_warn);
                F(sb, "A0_deg", r.A0_deg); F(sb, "A1_deg", r.A1_deg); F(sb, "first_trough_depth", r.first_trough_depth);
                F(sb, "neutral_deg", r.neutral_deg); F(sb, "neutral_deg_raw", r.neutral_deg_raw);
                F(sb, "pre_release_deg", r.pre_release_deg);
                B(sb, "phi_negated", r.phi_negated);
                F(sb, "P_plus", r.P_plus); F(sb, "P_minus", r.P_minus); F(sb, "P_total", r.P_total);
                S(sb, "spasticity_type", r.spasticity_type);
                IA(sb, "pk_i", r.PeakIdx); IA(sb, "tr_i", r.TroughIdx);
                for (int i = 0; i < PtScore.ScoredKeys.Length; i++) F(sb, "term_" + PtScore.ScoredKeys[i], r.Breakdown[i]);
            }
            if (r.Pt7.HasValue) F(sb, "pt7", r.Pt7.Value); else sb.Append(",\"pt7\":null");
            S(sb, "mas", r.Mas); S(sb, "zone", r.Zone);
            B(sb, "interpretable", r.Interpretable); S(sb, "reason", r.Reason);
            S(sb, "pt_zone", r.PtZone); S(sb, "pt_to_mas", r.PtToMas);
            B(sb, "excursion_ok", r.ExcursionOk); B(sb, "oscillation_ok", r.OscillationOk);
            S(sb, "refused", r.Refused);
        }
    }
}
