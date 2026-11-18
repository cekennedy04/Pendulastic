// The published severity axis in the Designer: De Groote et al. (2018) as neuromech/model_rt.py runs it.
//
// A line-for-line port of model_rt.simulate() for the REFERENCE terms only (gravity, viscous damping, the
// soft range walls, baseline tone, short-range stiffness on the first drop, the 50 ms-delayed force-feedback
// reflex), at Params() defaults: s 0.87, B 0.2, incline -35 deg, k_srs 1/1.5 deg. Every other module of
// model_rt is zero on this axis, so it is not here. One knob, lambda (neuromech.scenarios.severity):
//
//     tone = 2.0 * lambda N.m      k_f = 0.75 * lambda      k_df = 0.025 * lambda
//
// Same integrator as Python's default: one RK4 step per 1 ms tick, the delayed inputs held over the step,
// delayed derivatives as backward differences of a ring buffer. neuromech/tests/test_designer.py holds the
// trace this makes to the Python trace (and its score to the library's expected score) at lambda 1.0, 1.2
// and 1.4. Change model_rt and this file together.
//
// C# 3.0 (the .NET 3.5 compiler on a stock Windows 7), like the rest of the Designer.
using System;

namespace Pendulastic
{
    public static class DeGroote
    {
        // neuromech/pendulum_sim.py
        const double Lc0 = 0.256, M0 = 4.96, J0 = 0.446, G = 9.81;
        const double KLim = 3.0;
        const double TauSrs = 0.050, TauA = 0.010, Gamma = 0.050;
        const double DNominal = 0.2, LScaleNominal = 0.87;
        // neuromech/model_rt.py
        const double Dt = 1e-3;
        const double InclineDeg = -35.0;
        /// <summary>neuromech.scenarios.Scenario.duration: the record length the library's expected scores use.</summary>
        public const double DurationS = 14.0;

        static readonly double ThetaFlex = -110.0 * Math.PI / 180.0;
        static readonly double DThetaC = 1.5 * Math.PI / 180.0;
        static readonly int DelaySteps = (int)Math.Round(Gamma / Dt);
        static double restDeg = double.NaN;

        static double Blend(double x) { return 0.5 * (1.0 + Math.Tanh(1.2 * x)); }

        /// <summary>model_rt.simulate(Params(tone, k_f, k_df), theta0_deg=0, duration): theta in model degrees
        /// (0 = full extension, negative = flexion), n + 1 samples at 1 kHz from release.</summary>
        public static double[] Simulate(double tone, double kF, double kDf, double duration)
        {
            double s = LScaleNominal, B = DNominal;
            double lc = s * Lc0, m = s * s * M0, J = s * s * s * s * J0;
            double mgl = m * G * lc;
            double inc = InclineDeg * Math.PI / 180.0;
            double theta0 = 0.0;
            double kSrs = 1.0 / DThetaC;
            int n = (int)Math.Round(duration / Dt);
            int L = DelaySteps + 2;
            var bufW = new double[L]; var bufS = new double[L];
            int head = 0;
            bool srsActive = true;
            var y = new double[] { theta0, 0.0, 0.0, 0.0 };            // theta, omega, t_srs, t_r
            var k1 = new double[4]; var k2 = new double[4]; var k3 = new double[4]; var k4 = new double[4];
            var tmp = new double[4];
            var o = new double[n + 1];
            o[0] = 0.0;
            for (int i = 0; i < n; i++)
            {
                double t = i * Dt;
                if (srsActive && y[1] > 0.0 && t > 0.05) srsActive = false;
                int idx = ((head - DelaySteps) % L + L) % L;
                int idxPrev = ((head - DelaySteps - 1) % L + L) % L;
                double wDel = bufW[idx], sDel = bufS[idx];
                double wDelDot = i > DelaySteps ? (bufW[idx] - bufW[idxPrev]) / Dt : 0.0;
                double sDelDot = i > DelaySteps ? (bufS[idx] - bufS[idxPrev]) / Dt : 0.0;
                // delayed extensor excitation (Eq 8/9): constant over the step
                double eR = kF * (sDel - B * wDel) + kDf * (sDelDot - B * wDelDot);
                eR = Math.Max(0.0, eR);

                Derivs(y, k1, mgl, inc, J, B, tone, kSrs, theta0, srsActive, eR);
                for (int j = 0; j < 4; j++) tmp[j] = y[j] + 0.5 * Dt * k1[j];
                Derivs(tmp, k2, mgl, inc, J, B, tone, kSrs, theta0, srsActive, eR);
                for (int j = 0; j < 4; j++) tmp[j] = y[j] + 0.5 * Dt * k2[j];
                Derivs(tmp, k3, mgl, inc, J, B, tone, kSrs, theta0, srsActive, eR);
                for (int j = 0; j < 4; j++) tmp[j] = y[j] + Dt * k3[j];
                Derivs(tmp, k4, mgl, inc, J, B, tone, kSrs, theta0, srsActive, eR);
                for (int j = 0; j < 4; j++) y[j] = y[j] + Dt / 6.0 * (k1[j] + 2 * k2[j] + 2 * k3[j] + k4[j]);

                head = (head + 1) % L;
                bufW[head] = y[1]; bufS[head] = y[2];
                o[i + 1] = y[0] * (180.0 / Math.PI);
            }
            return o;
        }

        static void Derivs(double[] y, double[] d, double mgl, double inc, double J, double B, double tone,
                           double kSrs, double theta0, bool srsActive, double eR)
        {
            double th = y[0], w = y[1], ts = y[2], tr = y[3];
            double grav = -mgl * Math.Cos(th + inc);
            double damp = -B * w;
            double wall = -Math.Exp(KLim * (th - 0.0)) + Math.Exp(KLim * (ThetaFlex - th));
            // short-range stiffness (Eq 6/7), first drop only
            double k = kSrs * tone;
            double dth = th - theta0;
            double sVneg = Blend(-10.0 * w);
            double dTs;
            if (srsActive && sVneg > 1e-3)
            {
                double sLt = Blend(50.0 * (dth + DThetaC)), sGt = Blend(50.0 * (-dth - DThetaC));
                double drive = (sLt * (-ts - k * dth) + sGt * (-ts + k * DThetaC)) / TauA;
                dTs = sVneg * drive + Blend(10.0 * w) * (-ts) / TauSrs;
            }
            else dTs = -ts / TauSrs;
            double tau = grav + damp + wall + tone + ts + tr;
            d[0] = w; d[1] = tau / J; d[2] = dTs; d[3] = (eR - tr) / TauA;
        }

        /// <summary>model_rt.passive_rest_deg(Params()): where the passive leg hangs, by a 25 s simulation
        /// (median of the last 2 s). The rig's zero.</summary>
        public static double PassiveRestDeg
        {
            get
            {
                if (double.IsNaN(restDeg))
                {
                    double[] th = Simulate(0.0, 0.0, 0.0, 25.0);
                    var tail = new double[2000];
                    Array.Copy(th, th.Length - 2000, tail, 0, 2000);
                    Array.Sort(tail);
                    restDeg = 0.5 * (tail[999] + tail[1000]);
                }
                return restDeg;
            }
        }

        /// <summary>The swing at severity lambda in leg (unscaled) rig degrees, 1 kHz from release: model angle
        /// minus the passive rest, so it starts at +55 and a toned leg settles above 0 (neuromech.build).</summary>
        public static double[] SwingRigDeg(double lam, double swingS)
        {
            double[] th = Simulate(2.0 * lam, 0.75 * lam, 0.025 * lam, swingS);
            double rest = PassiveRestDeg;
            int n = (int)Math.Round(swingS / Dt);
            var x = new double[n];
            for (int i = 0; i < n; i++) x[i] = th[i] - rest;
            return x;
        }
    }
}
