// The movement the designer makes: a released lower leg as a damped pendulum with a stretch reflex.
//
// Kinematic stand-in for neuromech.model_rt, small enough to run hundreds of times while searching for a
// target. Angles are RIG degrees (neuromech.build convention): 0 = where a passive leg hangs, + = toward
// extension, so a leg released from full extension starts at +DepthDeg. The phone sees the knee angle
// 180 - Scale*DepthDeg + Scale*x(t) (180 = full extension), which is what is scored.
//
//   x'' = -wn^2 (x - ToneDeg) - 2 zeta wn x' + reflex
//   reflex = ReflexGain * max(0, -x'(t - delay) - ReflexThreshold)      (resists FLEXION velocity only)
//
// The rig file is motor_reference.preamble (3 sync pulses at 0, min-jerk lift, 3 s still hold) then the
// swing, at 1 kHz, starting at 0 as every player file must.
using System;

namespace Pendulastic
{
    public class Knobs
    {
        public double FreqHz = 0.9;            // undamped natural frequency of the swing
        public double Damping = 0.05;          // passive damping ratio zeta
        public double ReflexGain = 0.0;        // 1/s: deg/s^2 of braking per deg/s over threshold
        public double ReflexThresholdDps = 60; // flexion speed where the reflex starts
        public double ReflexDelayMs = 40;      // stretch-reflex latency
        public double ToneDeg = 0.0;           // where the leg settles, above the passive rest
        public double DepthDeg = 55.0;         // passive rest below full extension (rest knee 125)
        public double Scale = 0.75;            // rig amplitude / leg amplitude (rig range is +/-45)
        public double SwingS = 15.0;           // record length after release

        public Knobs Clone() { return (Knobs)MemberwiseClone(); }
    }

    public class Movement
    {
        public double[] RigT, RigDeg;          // 1 kHz player file: what the rig is told
        public double ReleaseT;                // where the swing starts in the rig file
        public double[] KneeT, KneeDeg;        // 100 Hz, 3 s hold + swing: what the phone should read
        public double KneeReleaseT;
    }

    public static class PendulumModel
    {
        public const double FsRig = 1000.0, FsScore = 100.0, HoldS = 3.0;
        // motor_reference constants: LEAD_S, PULSE_COUNT/DEG/WIDTH/GAP, POST_PULSE_S, RAMP_S, PRE_ROLL_S
        const double LeadS = 0.2, PulseDeg = 5.0, PulseWidthS = 0.08, PulseGapS = 0.25, PostPulseS = 1.0, RampS = 4.0, PreRollS = 3.0;
        const int PulseCount = 3;

        /// <summary>Swing in leg (unscaled) rig degrees at 1 kHz from release.</summary>
        public static double[] Swing(Knobs k)
        {
            int n = (int)Math.Round(k.SwingS * FsRig);
            double dt = 1.0 / FsRig, wn = 2 * Math.PI * k.FreqHz;
            int lag = Math.Max(0, (int)Math.Round(k.ReflexDelayMs / 1000.0 * FsRig));
            var x = new double[n]; var v = new double[n];
            x[0] = k.DepthDeg; v[0] = 0;
            for (int i = 0; i < n - 1; i++)
            {
                // semi-implicit Euler at 1 kHz: stable and exact enough for wn < 30 rad/s
                double vd = i - lag >= 0 ? v[i - lag] : 0.0;
                double reflex = k.ReflexGain * Math.Max(0.0, -vd - k.ReflexThresholdDps);
                double a = -wn * wn * (x[i] - k.ToneDeg) - 2 * k.Damping * wn * v[i] + reflex;
                v[i + 1] = v[i] + a * dt;
                x[i + 1] = x[i] + v[i + 1] * dt;
            }
            return x;
        }

        public static double[] Preamble(double a0)
        {
            var seg = new System.Collections.Generic.List<double>();
            Fill(seg, 0.0, LeadS);
            for (int p = 0; p < PulseCount; p++) { Fill(seg, PulseDeg, PulseWidthS); Fill(seg, 0.0, PulseGapS); }
            Fill(seg, 0.0, PostPulseS);
            int nr = (int)Math.Round(RampS * FsRig);
            for (int i = 0; i < nr; i++)
            {
                double u = i / (double)Math.Max(nr - 1, 1);
                seg.Add(a0 * (10 * u * u * u - 15 * u * u * u * u + 6 * u * u * u * u * u));
            }
            Fill(seg, a0, PreRollS);
            return seg.ToArray();
        }

        static void Fill(System.Collections.Generic.List<double> s, double v, double secs)
        {
            int n = (int)Math.Round(secs * FsRig);
            for (int i = 0; i < n; i++) s.Add(v);
        }

        public static Movement Make(Knobs k)
        {
            double[] sw = Swing(k);
            var m = new Movement();
            double a0 = k.Scale * sw[0];
            double[] pre = Preamble(a0);
            int n = pre.Length + sw.Length;
            m.RigT = new double[n]; m.RigDeg = new double[n];
            for (int i = 0; i < n; i++)
            {
                m.RigT[i] = i / FsRig;
                m.RigDeg[i] = i < pre.Length ? pre[i] : k.Scale * sw[i - pre.Length];
            }
            m.ReleaseT = pre.Length / FsRig;
            m.KneeReleaseT = HoldS;
            m.KneeT = null; m.KneeDeg = null;
            KneeFromRig(m, k.Scale * sw[0]);
            return m;
        }

        /// <summary>The phone's view of a rig file: 3 s still at extension, then the swing on the scorer's 100 Hz grid.
        /// Release is from full extension, so the knee reads 180 - (a0 - rig).</summary>
        public static void KneeFromRig(Movement m, double a0)
        {
            int r = (int)Math.Round(m.ReleaseT * FsRig);
            int nSw = m.RigDeg.Length - r;
            int nHold = (int)Math.Round(HoldS * FsScore);
            int nG = (int)Math.Floor((nSw - 1) / FsRig * FsScore) + 1;
            m.KneeT = new double[nHold + nG]; m.KneeDeg = new double[nHold + nG];
            for (int i = 0; i < nHold; i++) { m.KneeT[i] = i / FsScore; m.KneeDeg[i] = 180.0; }
            for (int j = 0; j < nG; j++)
            {
                double ts = j / FsScore, xi = ts * FsRig;
                int i0 = Math.Min((int)Math.Floor(xi), nSw - 2); double f = xi - i0;
                double rig = m.RigDeg[r + i0] + f * (m.RigDeg[r + i0 + 1] - m.RigDeg[r + i0]);
                m.KneeT[nHold + j] = HoldS + ts;
                m.KneeDeg[nHold + j] = 180.0 - (a0 - rig);
            }
            m.KneeReleaseT = HoldS;
        }

        /// <summary>A rig file made by this program or by neuromech.session: the release is where the preamble ends.</summary>
        public static Movement FromRigFile(double[] t, double[] sp)
        {
            var m = new Movement { RigT = t, RigDeg = sp };
            int r = (int)Math.Round((LeadS + PulseCount * (PulseWidthS + PulseGapS) + PostPulseS + RampS + PreRollS) * FsRig);
            if (r >= sp.Length - 200) r = 0;               // not a preamble file (e.g. the smoke sine): score from the start
            m.ReleaseT = r / FsRig;
            KneeFromRig(m, sp[r]);
            return m;
        }

        public static double PreambleS { get { return LeadS + PulseCount * (PulseWidthS + PulseGapS) + PostPulseS + RampS + PreRollS; } }
    }
}
