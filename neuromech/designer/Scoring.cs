// What the designer shows about a movement: the production scorer (PtScore.cs, parity-tested against
// pendulastic_pt_score through neuromech.build.score_angle) run on the knee angle the phone should read.
using System;
using System.Collections.Generic;
using System.Globalization;

namespace Pendulastic
{
    public class Metrics
    {
        public bool Scored;              // the scorer returned parameters at all
        public bool Interpretable;       // mas_estimate grades it (excursion and oscillation gates pass)
        public double? Pt7;              // the score the code calls pt7 (6 terms since 2026-10-26)
        public string Zone = "", Mas = "", Reason = "";
        public Dictionary<string, double> Values = new Dictionary<string, double>();
    }

    public static class Scoring
    {
        public static Metrics Score(Movement m)
        {
            var r = new Metrics();
            PtResult p;
            try { p = PtScore.ScoreAngle(m.KneeT, m.KneeDeg, m.KneeReleaseT); }
            catch (Exception ex) { r.Reason = "the scorer could not run: " + ex.Message; return r; }
            r.Scored = p.HasParams;
            if (!p.HasParams) { r.Reason = p.NoneReason ?? "the scorer returned no parameters"; return r; }
            r.Interpretable = p.Interpretable;
            r.Pt7 = p.Pt7;
            r.Zone = p.Zone ?? ""; r.Mas = p.Mas ?? ""; r.Reason = p.Reason ?? "";
            r.Values["R2n"] = p.R2n; r.Values["N"] = p.N; r.Values["f"] = p.f; r.Values["area_ratio"] = p.area_ratio;
            r.Values["phi_max_ratio"] = p.phi_max_ratio; r.Values["omega_max_n"] = p.omega_max_n;
            r.Values["omega_min_n"] = p.omega_min_n; r.Values["A0_deg"] = p.A0_deg; r.Values["A1_deg"] = p.A1_deg;
            return r;
        }

        public static string HealthyRef(string p)
        {
            double v;
            return PtScore.HEALTHY_REF.TryGetValue(p, out v) ? v.ToString("G4", CultureInfo.InvariantCulture) : "-";
        }
    }
}
