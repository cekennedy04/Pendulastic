"""
evaluate_healthy_ref_v04.py
===========================
Validation task V0.4: recalibrate HEALTHY_REF against the OptiTrack control
cohort, using the scoring that actually ships.

Scores every Control_* OptiTrack trial and CACHES the per-trial parameters to
JSON, so control-set questions ("what if we drop P2?") cost nothing instead of
another 20 minutes -- load_optitrack is ~19 s per trial and scoring is 0.1 s.

Honours excluded_trials.json, and drops P9's right side: its Left and Right
files are byte-identical across all 5 trials (verified by md5), so that
participant would otherwise contribute one leg counted twice.

RESULT (2026-09-09, 46 trials scored of 77 found, 9 participants, 15 legs):

    param            current    all controls   spread across subsets
    R2n               1.0321        0.9907       0.958 - 1.024
    N                 3.5000        6.5000       6.0   - 7.5     <- robust
    phi_max_ratio     0.6386        0.6667       0.654 - 0.668
    omega_max_n       6.7684        9.5672       7.57  - 9.82
    omega_min_n       0.0010        0.0011       0.0011- 0.0016
    f                 0.9137        0.8839       0.870 - 0.890
    area_ratio        0.0768        0.0945       0.082 - 0.156    <- 90% spread

WHAT THIS ESTABLISHES

N = 3.5 is wrong and always was. It is robust at 6.0-7.5 across every control
subset tried, which independently confirms that 3.5 was the 4-second
active-window cap rather than a control median -- controls really do swing
about 6.5 times.

WHAT IT CANNOT ESTABLISH

area_ratio moves 90% depending on which controls are included, so this cohort
cannot pin it. Which is awkward, since area_ratio is the parameter the
swing-centred frame changed.

AND THE LIMIT THAT NO SUBSET FIXES

Every control leg in mas_scores.csv is `assessed_by = ASSUMED`. All 17 of them.
Every clinician-EXAMINED leg in the dataset (AN 2, VL 6, WD 10) is an MS
patient. So there is no examined healthy leg anywhere in this data, and any
"healthy reference" computed from it inherits that, however the arithmetic is
done. Also note P23 contributes 7 of the 46 trials and has no mas_scores.csv
row at all -- not even an assumed one.

The rig failures are real and worth reading: 26 of 72 attempted trials failed,
P2 losing 9 of 16 to "Both clusters are triangles ... rig geometry is
unsupported", "Cluster is never fully tracked (optical coverage 0.0%)" and
"Cluster shows no rotation; no hinge axis exists". P2 is one of the four
participants the CURRENT reference was calibrated on.

Usage:
    miniconda3/python.exe evaluate_healthy_ref_v04.py
"""
import json, os, re, sys, glob
import numpy as np

ROOT = "C:/Users/cladi/Pendulastic"
OUT = ("C:/Users/cladi/AppData/Local/Temp/claude/"
       "C--Users-cladi/2037fe88-f614-4162-8a4b-47072e640659/scratchpad/v04_trials.json")
sys.path.insert(0, ROOT + "/.claude/worktrees/webapp-restyle")
import pendulastic_pt_score as P

KEYS = ['R2n', 'N', 'phi_max_ratio', 'omega_max_n', 'omega_min_n', 'f', 'area_ratio']
excluded = json.load(open(os.path.join(ROOT, 'excluded_trials.json')))

out = []
for d in sorted(glob.glob(os.path.join(ROOT, 'OptiTrack_Recordings', 'Control_*'))):
    pid = os.path.basename(d).split('_')[-1]
    for csv in sorted(glob.glob(os.path.join(d, '**', '*.csv'), recursive=True)):
        rel = os.path.relpath(csv, d).replace(os.sep, '/')
        parts = rel.split('/')
        side = parts[0].lower()
        cond = parts[1] if len(parts) > 2 else 'pre'
        m = re.search(r'trial[_-]?(\d+)', os.path.basename(csv), re.I)
        tno = m.group(1) if m else '?'
        key = "%s_%s_%s_T%s" % (pid, side, cond, tno)
        rec = {"pid": pid, "side": side, "cond": cond, "trial": tno, "key": key}
        if key in excluded:
            rec["status"] = "excluded_trials.json"
        elif pid == '9' and side == 'right':
            rec["status"] = "P9 duplicate side (byte-identical to left)"
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
            except Exception as e:
                rec["status"] = "%s: %s" % (type(e).__name__, e)
        out.append(rec)
        print(rec["pid"], rec["side"], rec["trial"], rec["status"][:60], flush=True)

json.dump(out, open(OUT, "w"), indent=1)
ok = [r for r in out if r["status"] == "ok"]
print("")
print("cached %d records (%d scored) -> %s" % (len(out), len(ok), OUT))
