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

A CANDIDATE FIX, measured but NOT adopted: measure the areas about the swing's
own midline -- interpolated between successive extrema -- instead of about the
settled angle. `area_ratio_about_midline` at the foot of this file is that
prototype, and `compare_midline()` prints the table, so the figures below can
be re-derived rather than taken on trust:

    sag      area_ratio now    about midline
    5 deg          0.277           0.031
    10 deg         0.511           0.103
    20 deg         0.824           0.356

It is a PROTOTYPE and is wired into nothing. Adopting it changes area_ratio,
and therefore PT7, on every asymmetric trial in the existing corpus, so it is
a scoring decision rather than a bug fix.

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


# ── The candidate fix, so its numbers are reproducible rather than quoted ────
#
# NOT wired into pendulastic_pt_score. This is the prototype the module
# docstring refers to: measure the swing's asymmetry about its OWN midline
# instead of about the settled angle. The midline is interpolated between
# successive extrema, so it follows a drifting centre instead of assuming the
# limb ends where it swung.

def area_ratio_about_midline(r):
    """`area_ratio` measured about the swing's own midline.

    Returns None when there are too few extrema to define a midline at all,
    which is a genuine 'cannot measure' rather than a symmetric result.
    """
    t_r = np.asarray(r['t_r'], dtype=float)
    phi = np.asarray(r['phi'], dtype=float)
    ex = np.sort(np.concatenate([
        np.asarray(r['pk_i'], dtype=int), np.asarray(r['tr_i'], dtype=int)]))
    if len(ex) < 3:
        return None

    # Midpoint of each consecutive extremum pair: for a decaying oscillation
    # that is the instantaneous centre, whatever the centre is doing.
    mid_t = [(t_r[ex[i]] + t_r[ex[i + 1]]) / 2.0 for i in range(len(ex) - 1)]
    mid_v = [(phi[ex[i]] + phi[ex[i + 1]]) / 2.0 for i in range(len(ex) - 1)]
    centre = np.interp(t_r, mid_t, mid_v, left=mid_v[0], right=mid_v[-1])

    d = phi - centre
    # Bounded to the oscillation itself; integrating the tail would re-import
    # the very baseline problem this is removing.
    m = (t_r >= t_r[ex[0]]) & (t_r <= t_r[ex[-1]])
    p_plus = float(np.trapezoid(np.clip(d[m], 0.0, None), t_r[m]))
    p_minus = float(np.trapezoid(np.clip(-d[m], 0.0, None), t_r[m]))
    total = p_plus + p_minus
    return abs(p_plus - p_minus) / total if total > 1e-9 else None


def compare_midline():
    print('\n--- CANDIDATE FIX: asymmetry about the swing midline, not the '
          'settled angle ---')
    print(f'{"sag":>6} {"troughs":>8} {"area_ratio now":>15} {"about midline":>14}')
    for creep in (0.0, 2.0, 5.0, 10.0, 20.0):
        t, ang = build(creep=creep)
        r = P.compute_pt_params(t, ang, None, False)
        fixed = area_ratio_about_midline(r)
        shown = f'{fixed:.3f}' if fixed is not None else 'not measurable'
        print(f'{creep:6.1f} {len(r["tr_i"]):8d} {r["area_ratio"]:15.3f} {shown:>14}')
    print('      The oscillation is identical in every row. Any spread in the')
    print('      "now" column is baseline, not limb.')


compare_midline()


# ── Why A0 was left measuring release-above-REST ────────────────────────────
#
# The centred frame fixed the symmetry integral and the extremum gates, but A0
# still absorbs post-swing sag, which is the whole residual PT7 spread (0.058).
# The obvious next step is to re-base A0 too. It was measured, and it is worse.

def compare_a0_definitions():
    print('\n--- A0: three definitions, and why the current one stays ---')
    a0_true = 45.0

    def semi_amplitude(r):
        """Half the first peak-to-trough excursion. A DIFFERENCE of two points
        on the same curve, so any additive baseline cancels by construction."""
        phi = np.asarray(r['phi'])
        tr = r['tr_i']
        return float((phi[0] - phi[int(tr[0])]) / 2.0) if len(tr) else float('nan')

    print('\n  sensitivity to SAG (damping fixed):')
    print(f'  {"sag":>6} {"release-rest":>13} {"semi-amplitude":>15}')
    a, c = [], []
    for creep in (0.0, 5.0, 10.0, 20.0):
        t, ang = build(creep=creep)
        r = P.compute_pt_params(t, ang, None, False)
        a.append(r['A0_deg']); c.append(semi_amplitude(r))
        print(f'  {creep:6.1f} {r["A0_deg"]:13.2f} {c[-1]:15.2f}')
    print(f'  {"spread":>6} {max(a)-min(a):13.2f} {max(c)-min(c):15.2f}')

    print('\n  sensitivity to DAMPING (no sag):')
    print(f'  {"damping":>7} {"release-rest":>13} {"semi-amplitude":>15}')
    a, c = [], []
    for lam in (0.25, 0.45, 0.8, 1.2, 2.0):
        dt = 1/20.0
        ts = np.arange(0.0, 10.0, dt)
        ang = np.concatenate([
            np.full(int(1.2/dt), REST + a0_true),
            REST + a0_true*np.exp(-lam*ts)*np.cos(2*np.pi*FREQ*ts),
            np.full(int(6.0/dt), REST)])
        t = np.arange(len(ang))*dt
        r = P.compute_pt_params(t, ang, None, False)
        a.append(r['A0_deg']); c.append(semi_amplitude(r))
        print(f'  {lam:7.2f} {r["A0_deg"]:13.2f} {c[-1]:15.2f}')
    print(f'  {"spread":>7} {max(a)-min(a):13.2f} {max(c)-min(c):15.2f}')

    print('\n  VERDICT: semi-amplitude trades a 19 deg sag sensitivity for an 11 deg')
    print('  DAMPING sensitivity, and its error grows monotonically with damping.')
    print('  A0 is the spasticity grouping variable wherever a clinical MAS grade')
    print('  is absent, and damping IS the spasticity signal -- so that definition')
    print('  would make A0 partly a restatement of what it must stay independent')
    print('  of. release-above-rest is damping-invariant and stays.')


compare_a0_definitions()
