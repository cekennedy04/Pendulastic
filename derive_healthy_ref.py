"""
derive_healthy_ref.py
=====================
Re-derive HEALTHY_REF's DATA-DERIVED entries from the control cohort, using the
scorer and reconstruction that ship, over exactly the trials the scorer grades.

Replaces evaluate_healthy_ref_v04.py, which (a) imported the scorer from the
retired feat/webapp-workbench-restyle worktree, (b) dropped P9's RIGHT side as
the duplicate when capture timestamps prove the right-leg take is the real one
(excluded_trials.json now excludes P9 LEFT), and (c) took every scored trial,
including ones mas_estimate refuses to grade.

Population: participants whose OptiTrack folder carries the control prefix
(participant_paths.CONTROL_PREFIX), baseline condition only (pre* / default),
excluded_trials.json honoured, and only INTERPRETABLE trials -- the ones the
excursion and oscillation gates let through. Interpretability depends on A0
and A1 only, never on HEALTHY_REF, so this is not circular.

Why "interpretable only" matters for omega_max_n in particular: it is peak
angular velocity divided by A0, and across control trials it falls steeply as
A0 rises (Spearman rho = -0.88, 51 trials, 2026-10-27; median 12.2 below
25 deg, 9.1 at 30-45, 6.0 above 45). The leg is released from about horizontal
whatever its resting angle, so peak velocity is roughly fixed while A0 -- which
is measured from that resting angle -- varies. A median over trials the scorer
refuses (or over trials whose release was mis-detected, which shrinks A0) is a
median of a different population from the one the reference is applied to.

RESULT 2026-10-27 (39 interpretable trials, 12 legs, 7 people: P6, 7, 8, 9,
10, 12, 23), trial median, leave-one-person-out range in brackets:

    R2n            0.9467  [0.915 - 0.966]   literature anchor 1.0 kept
    N              6.0000  [6.00  - 6.50 ]   literature anchor 6.5 kept
    phi_max_ratio  0.6407  [0.625 - 0.653]   was 0.6667
    omega_max_n    6.4839  [6.351 - 6.910]   was 9.5672
    omega_min_n   -6.3624  [-6.689 - -6.289] was -9.4152 (diagnostic only)
    f              0.9511  [0.942 - 0.980]   was 0.8839
    area_ratio     0.1766  [0.154 - 0.213]   was 0.0945, still the least stable

Standing limit, unchanged by any of this: every control leg is
assessed_by = ASSUMED. There is no clinician-examined healthy leg in the data.

Usage:
    .venv/Scripts/python.exe derive_healthy_ref.py --opti-root OptiTrack_Recordings
    # optional: --cache DIR stores/reads (t, angle) per trial as .npz, because
    # load_optitrack is ~19 s per trial and scoring is ~0.1 s. An entry is
    # reused only for the same source file and the same scorer source.
    # Fails closed (exit 2) if any baseline control trial cannot be loaded;
    # pass --allow-unloadable to accept the smaller cohort explicitly
    # (2026-10-27: P2 right solo T4/T5 are unloadable rig-geometry failures).
"""
import argparse
import hashlib
import os
import sys

import numpy as np

import participant_paths as pp
import pendulastic_pt_score as pts
import pt_report_common as prc

KEYS = ["R2n", "N", "phi_max_ratio", "omega_max_n", "omega_min_n", "f", "area_ratio"]


def _is_control(pid) -> bool:
    d = pp.find_participant_dir(prc.OPTI_ROOT, str(pid))
    return bool(d) and os.path.basename(d).startswith(pp.CONTROL_PREFIX + "_")


def _loader_fingerprint() -> str:
    """Hash of the scorer module's source. load_optitrack and every
    reconstruction step it calls live there, so a cached angle series built by
    different code no longer matches and is re-read rather than reused."""
    with open(pts.__file__, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()[:16]


def _source_stamp(path: str) -> str:
    st = os.stat(path)
    return f"{os.path.abspath(path)}|{st.st_size}|{int(st.st_mtime)}"


def _load(rec, cache, key, fingerprint):
    """(t, angle) for one trial, through the cache when it is still valid.
    A cache entry is valid only for the same source file (path, size, mtime)
    read by the same scorer source -- otherwise a rerun after new recordings,
    a different --opti-root or a reconstruction fix would silently reuse old
    angles and still print a plausible table."""
    hit = cache and os.path.join(cache, key + ".npz")
    stamp = _source_stamp(rec["path"])
    if hit and os.path.exists(hit):
        z = np.load(hit)
        if "source" in z and "loader" in z and                 str(z["source"]) == stamp and str(z["loader"]) == fingerprint:
            return z["t"], z["ang"]
    t, ang = pts.load_optitrack(rec["path"])
    if hit:
        np.savez(hit, t=t, ang=ang, source=stamp, loader=fingerprint)
    return t, ang


def control_trials(cache=None):
    """([(pid, leg, params)] for every interpretable baseline control trial,
    [keys of baseline control trials that could not be loaded]).

    Live recordings only (include_archive=False): discovery de-duplicates by
    real path, not by trial, so an archive copy of a live trial would
    otherwise be counted twice."""
    out, unloadable = [], []
    fingerprint = _loader_fingerprint()
    for rec in prc.discover_all_trials(include_archive=False):
        cond = str(rec["condition"]).lower()
        if not (cond.startswith("pre") or cond == "default"):
            continue
        pid, leg = str(rec["participant"]), rec["leg"]
        if not _is_control(pid):
            continue
        key = f"{pid}_{leg}_{rec['condition']}_T{rec.get('trial')}"
        try:
            t, ang = _load(rec, cache, key, fingerprint)
        except Exception as e:                       # noqa: BLE001
            print(f"  {key}: unloadable ({type(e).__name__}: {e})")
            unloadable.append(key)
            continue
        p = pts.compute_pt_params(t, ang)
        if p and pts.mas_estimate(p)["interpretable"]:
            out.append((pid, leg, p))
    return out, unloadable


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--opti-root", help="where OptiTrack_Recordings actually is")
    ap.add_argument("--cache", help="directory for per-trial (t, angle) .npz files")
    ap.add_argument("--allow-unloadable", action="store_true",
                    help="derive anyway when some baseline control trials cannot be "
                         "loaded (they are listed); without it the run fails closed")
    a = ap.parse_args(argv)
    if a.opti_root:
        prc.OPTI_ROOT = os.path.abspath(os.path.expanduser(a.opti_root))
    if a.cache:
        os.makedirs(a.cache, exist_ok=True)

    rows, unloadable = control_trials(a.cache)
    if unloadable and not a.allow_unloadable:
        print(f"\n{len(unloadable)} baseline control trial(s) could not be loaded. A median "
              "over a silently smaller cohort is a different reference; fix or exclude them "
              "(excluded_trials.json), or rerun with --allow-unloadable to accept it.")
        return 2
    if not rows:
        print("no interpretable control trials found")
        return 1
    pids = sorted({r[0] for r in rows}, key=lambda s: int(s) if s.isdigit() else s)
    legs = sorted({(r[0], r[1]) for r in rows})
    print(f"{len(rows)} interpretable trials, {len(legs)} legs, {len(pids)} people: "
          f"{', '.join('P' + p for p in pids)}"
          + (f"; {len(unloadable)} unloadable, accepted" if unloadable else "") + "\n")
    print(f"{'param':14s} {'current':>9s} {'trial med':>9s} {'leg med':>9s}   leave-one-person-out")
    for k in KEYS:
        vals = np.array([r[2][k] for r in rows], float)
        leg_meds = [np.median([r[2][k] for r in rows if (r[0], r[1]) == lg]) for lg in legs]
        loo = [np.median([r[2][k] for r in rows if r[0] != p]) for p in pids]
        print(f"{k:14s} {pts.HEALTHY_REF[k]:9.4f} {np.median(vals):9.4f} "
              f"{np.median(leg_meds):9.4f}   {min(loo):.4f} - {max(loo):.4f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
