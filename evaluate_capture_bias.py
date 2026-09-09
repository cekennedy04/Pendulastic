"""
evaluate_capture_bias.py
========================
One-off research diagnostic (same category as evaluate_peak_detection.py and
evaluate_flex_axis_methods.py): which CAPTURE variables move the PT7 score
while the limb's own dynamics are held identical?

Every sweep below rebuilds the same underlying oscillation -- same amplitude,
same damping, same frequency -- and varies one thing about how it was
recorded. Any spread in PT7 is therefore bias, not signal.

Result (2026-09-09, 20 Hz baseline):

    settle tail length ........ 0.0000    <- clean
    pre-release hold length ... 0.0000    <- clean
    release amplitude ......... 0.0082
    sensor noise .............. 0.0246
    sample rate 20-120 Hz ..... 0.0313
    recording length .......... 0.0394
    POST-SWING SAG ............ 1.1468    <- 30x everything else combined

The sag case is the one that matters, and it was found from a clinician's
observation that troughs went missing when "they weren't swinging below the
settling angle".

WHY IT HAPPENS. `neutral` is the settled-tail median, and every parameter is
expressed relative to it (phi = ang - neutral). When a limb keeps creeping
into flexion after the oscillation has died, the swing happened about a HIGHER
centre than the angle it finally rests at. So:

  - troughs stop crossing neutral and are simply not detected (7 -> 3),
  - A0 = phi[0] absorbs the sag and inflates (44.98 -> 64.34 deg),
  - area_ratio, which integrates above/below NEUTRAL, explodes
    (0.008 -> 0.824) and carries 88% of the false impairment,
  - PT7 goes 0.0713 -> 1.2180 on an oscillation that never changed.

detrend=True does NOT fix it (1.218 -> 1.088): _settled_tail_drift_slope fits
a LINEAR slope on the settled tail, but the sag is exponential and most of it
happens during the swing.

A CANDIDATE FIX, prototyped and measured but NOT adopted here: measure the
areas about the swing's own midline -- interpolated between successive
extrema -- instead of about the settled angle. On the same sweep that takes
area_ratio from 0.277 to 0.031 at 5 deg of sag and 0.511 to 0.103 at 10 deg.
Adopting it changes area_ratio, and therefore PT7, on every asymmetric trial,
so it is a scoring decision rather than a bug fix.

Usage:
    miniconda3/python.exe evaluate_capture_bias.py
"""
import numpy as np, sys
sys.path.insert(0, '.')
import pendulastic_pt_score as P

REST, LAM, FREQ = 135.0, 0.45, 1.0

def build(fs=20.0, a0=45.0, hold=1.2, tail=6.0, swing=10.0, creep=0.0, mu=0.25, noise=0.0, seed=1):
    dt = 1.0 / fs
    ts = np.arange(0.0, swing, dt)
    h = np.full(int(hold / dt), REST + creep + a0)
    sw = REST + creep * np.exp(-mu * ts) + a0 * np.exp(-LAM * ts) * np.cos(2 * np.pi * FREQ * ts)
    tt = np.arange(0.0, tail, dt)
    tl = REST + creep * np.exp(-mu * (ts[-1] + dt + tt))
    ang = np.concatenate([h, sw, tl])
    if noise:
        ang = ang + np.random.default_rng(seed).normal(0, noise, len(ang))
    return np.arange(len(ang)) * dt, ang

def score(**kw):
    t, ang = build(**kw)
    r = P.compute_pt_params(t, ang, None, False)
    if r is None:
        return None
    return dict(N=r['N'], A0=r['A0_deg'], area=r['area_ratio'], R2n=r['R2n'],
                tr=len(r['tr_i']), pk=len(r['pk_i']), PT7=P.compute_pt_score(r))

def sweep(name, key, values, **fixed):
    print(f'\n--- {name} (identical limb dynamics throughout) ---')
    print(f'{key:>10} {"peaks":>6} {"troughs":>8} {"N":>6} {"A0":>7} {"area":>7} {"R2n":>7} {"PT7":>7}')
    out = []
    for v in values:
        r = score(**{key: v}, **fixed)
        if r is None:
            print(f'{v:>10} {"unscorable":>40}'); continue
        out.append(r['PT7'])
        print(f'{v:>10} {r["pk"]:6d} {r["tr"]:8d} {r["N"]:6.1f} {r["A0"]:7.2f} '
              f'{r["area"]:7.3f} {r["R2n"]:7.3f} {r["PT7"]:7.4f}')
    if out:
        print(f'{"":>10} PT7 spread: {max(out)-min(out):.4f}  ({min(out):.4f} .. {max(out):.4f})')

sweep('SAMPLE RATE', 'fs', [20.0, 30.0, 50.0, 60.0, 100.0, 120.0])
sweep('SETTLE TAIL LENGTH', 'tail', [1.0, 2.0, 5.0, 10.0, 30.0])
sweep('PRE-RELEASE HOLD LENGTH', 'hold', [0.6, 1.2, 3.0, 8.0])
sweep('RELEASE AMPLITUDE', 'a0', [15.0, 30.0, 45.0, 60.0, 75.0])
sweep('RECORDING LENGTH (swing window)', 'swing', [4.0, 6.0, 10.0, 20.0])
sweep('SENSOR NOISE', 'noise', [0.0, 0.1, 0.3, 0.6, 1.0])
sweep('POST-SWING SAG', 'creep', [0.0, 2.0, 5.0, 10.0, 20.0])
