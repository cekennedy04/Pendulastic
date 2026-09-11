"""
evaluate_pt7_discrimination.py
==============================
Does the PT7 composite separate diagnosed legs from assumed-healthy ones?

Scores the WHOLE OptiTrack cohort -- controls and patients -- and caches every
per-trial result, then reports discrimination. Run once (~50 min at ~19 s per
trial in load_optitrack); the analysis afterwards is instant.

RESULT (2026-09-10, 229 trials scored of 285 found, after the 4-second cap,
the HEALTHY_REF recalibration and the omega_min_n fix all landed):

    group      trials  legs   PT7 median   N median
    Control        46    15       0.2280       6.50
    MS            155    18       0.2143       6.00
    stroke         28     7       0.3000       5.00

    DISCRIMINATIVE POWER, max(AUC, 1-AUC):

    param            all patients   stroke only   direction
    f                       0.698         0.766   patients higher  *
    R2n                     0.611         0.641   patients LOWER
    phi_max_ratio           0.543         0.727   patients LOWER
    N                       0.527         0.652   patients LOWER
    PT7                     0.517         0.573   --
    area_ratio              0.510         0.555   patients LOWER
    omega_min_n             0.506         0.566   patients LOWER
    omega_max_n             0.500         0.575   patients higher

THE FINDING

The composite is WORSE THAN FOUR OF ITS OWN COMPONENTS. PT7 sits at 0.517,
barely off chance, while `f` alone reaches 0.698 across all patients and 0.766
in stroke -- above the 0.7 bar Whelan 2018 used. R2n (0.611), phi_max_ratio
and N also beat it.

So the problem is not the measurement. Individual parameters carry real
signal, and in the expected direction: R2n, phi_max_ratio and N are all LOWER
in patients, which is exactly what the one-sided penalties in
compute_pt_score_breakdown assume. Summing seven equally-weighted deviations
dilutes the three that discriminate with four that do not.

Note also that MS barely separates from control at all (PT7 0.2143 vs 0.2280,
slightly LOWER) while stroke does (0.3000). The MS cohort here is largely
MAS 1 -- mild -- which is consistent with Whelan's own finding that the
pendulum test separates MAS 0 from MAS > 0 but cannot grade severity.

WHAT THIS DOES NOT SAY

Nothing here validates the instrument. Controls are assumed healthy, not
examined (see evaluate_healthy_ref_v04.py), so "patient vs control" is really
"diagnosed vs enrolled-as-control". A weighted or subset composite is the
obvious next question and is NOT attempted here.

Usage:
    miniconda3/python.exe evaluate_pt7_discrimination.py
"""
import json, os, re, sys, glob
import numpy as np

ROOT = "C:/Users/cladi/Pendulastic"
OUT = ("C:/Users/cladi/AppData/Local/Temp/claude/"
       "C--Users-cladi/2037fe88-f614-4162-8a4b-47072e640659/scratchpad/cohort_all.json")
sys.path.insert(0, ROOT + "/.claude/worktrees/webapp-restyle")
import pendulastic_pt_score as P

KEYS = ['R2n', 'N', 'phi_max_ratio', 'omega_max_n', 'omega_min_n', 'f', 'area_ratio']
excluded = json.load(open(os.path.join(ROOT, 'excluded_trials.json')))

out = []
folders = sorted(glob.glob(os.path.join(ROOT, 'OptiTrack_Recordings', '*')))
for d in folders:
    base = os.path.basename(d)
    if not os.path.isdir(d) or base.startswith('Participant_test'):
        continue
    group = base.split('_')[0]          # Control / MINT / CHAT
    pid = base.split('_')[-1]
    for csv in sorted(glob.glob(os.path.join(d, '**', '*.csv'), recursive=True)):
        rel = os.path.relpath(csv, d).replace(os.sep, '/')
        parts = rel.split('/')
        side = parts[0].lower()
        cond = parts[1] if len(parts) > 2 else 'pre'
        m = re.search(r'trial[_-]?(\d+)', os.path.basename(csv), re.I)
        tno = m.group(1) if m else '?'
        key = "%s_%s_%s_T%s" % (pid, side, cond, tno)
        rec = {"pid": pid, "group": group, "side": side, "cond": cond,
               "trial": tno, "key": key}
        if key in excluded:
            rec["status"] = "excluded_trials.json"
        elif pid == '9' and side == 'right':
            rec["status"] = "P9 duplicate side"
        else:
            try:
                t, ang = P.load_optitrack(csv)
                r = P.compute_pt_params(t, ang, None, True)
                if r is None:
                    rec["status"] = "unscorable"
                else:
                    rec["status"] = "ok"
                    for k in KEYS:
                        v = r[k]
                        rec[k] = float(v) if np.isfinite(v) else None
                    rec["A0_deg"] = float(r["A0_deg"])
                    rec["PT7"] = float(P.compute_pt_score(r, P.HEALTHY_REF))
            except Exception as e:
                rec["status"] = "%s: %s" % (type(e).__name__, str(e)[:70])
        out.append(rec)
        print(base, side, tno, rec["status"][:50], flush=True)

json.dump(out, open(OUT, "w"), indent=1)
ok = [r for r in out if r["status"] == "ok"]
print("")
print("cached %d records (%d scored) -> %s" % (len(out), len(ok), OUT))


# ── Analysis: does PT7 separate patients from controls? ─────────────────────

def analyse(path=OUT):
    import itertools
    recs = [r for r in json.load(open(path)) if r['status'] == 'ok']

    def auc(pos, neg):
        pos = [p for p in pos if p is not None and np.isfinite(p)]
        neg = [n for n in neg if n is not None and np.isfinite(n)]
        if not pos or not neg:
            return float('nan')
        w = sum(1 for a, b in itertools.product(pos, neg) if a > b)
        t = sum(1 for a, b in itertools.product(pos, neg) if a == b)
        return (w + 0.5 * t) / (len(pos) * len(neg))

    g = {}
    for r in recs:
        g.setdefault(r['group'], []).append(r)
    ctrl, ms, st = g.get('Control', []), g.get('MINT', []), g.get('CHAT', [])
    pts = ms + st

    print("\n%-10s %7s %6s %10s %8s" % ('group', 'trials', 'legs', 'PT7 median', 'N median'))
    for name, rs in (('Control', ctrl), ('MS', ms), ('stroke', st)):
        if not rs:
            continue
        legs = len({(r['pid'], r['side']) for r in rs})
        print("%-10s %7d %6d %10.4f %8.2f" % (
            name, len(rs), legs,
            np.median([r['PT7'] for r in rs]), np.median([r['N'] for r in rs])))

    # Direction-aware: several parameters separate in the OPPOSITE polarity to
    # "patients score higher", and a raw AUC hides that as if it were noise.
    print("\nDISCRIMINATIVE POWER, max(AUC, 1-AUC). 0.5 = no information;")
    print("Whelan 2018 used 0.7 as the acceptability bar.\n")
    print("%-16s %13s %12s   %s" % ('param', 'all patients', 'stroke only', 'direction'))
    rows = []
    for k in ['R2n', 'N', 'phi_max_ratio', 'omega_max_n', 'omega_min_n',
              'f', 'area_ratio', 'PT7']:
        a = auc([r.get(k) for r in pts], [r.get(k) for r in ctrl])
        b = auc([r.get(k) for r in st], [r.get(k) for r in ctrl])
        rows.append((max(a, 1 - a), k, max(a, 1 - a), max(b, 1 - b),
                     'patients higher' if a >= 0.5 else 'patients LOWER'))
    for _, k, a, b, d in sorted(rows, reverse=True):
        print("%-16s %13.3f %12.3f   %s%s" % (k, a, b, d, ' *' if a >= 0.65 else ''))


if __name__ == '__main__' and os.path.exists(OUT):
    analyse()
