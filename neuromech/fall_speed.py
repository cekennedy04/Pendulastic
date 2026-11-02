"""Does the emulator fall like a real leg? Peak fall speed against first excursion.

    python -m neuromech.fall_speed --opti-root <OptiTrack_Recordings> --rec-root <Recordings>
    python -m neuromech.fall_speed ... --limit 20      # score at most 20 more trials, then report

The question. omega_max_n = omega_peak_deg_s / A0_deg, so the scorer's velocity term is
only a velocity measurement if peak speed and excursion move independently of the ratio's
intent. In the emulator (neuromech.model_rt) the severity axis brakes the FALL: tone and the
velocity reflex cut peak speed nearly in half up the MAS ladder while A0 barely moves. Real
legs were measured on 2026-10-27 to do the opposite -- peak speed flat at ~350 deg/s across
A0 27-75 deg, impaired legs falling as fast as controls and losing excursion instead. This
module measures that on the cohort, by one defined method, beside the emulator's manifest.

Per real trial: every baseline OptiTrack trial with a MEASURABLE fall (the recalibration's
set -- `pre`/`default` condition, a leg with a clinical MAS, excluded_trials.json honoured --
scored by the production path, load_optitrack -> compute_pt_params, keeping 5 < A0 <= the
scorer's MAX_INTERPRETABLE_A0_DEG). Not only the trials the scorer would GRADE: the grading
gate is a floor on A0, so restricting to graded trials would select on the very variable
this regresses on, and drop exactly the short-swing trials the question is about. The
graded-only subset is reported beside it for comparison. Per emulator
scenario: neuromech/out/scenario_manifest.csv, so run `python -m neuromech.build` first, on
the scorer you want compared.

Reported, for each side: Spearman(peak, A0), the log-log slope of peak on A0 (a pendulum
whose peak scales with its swing gives 1.0; one whose peak is set by the release gives 0),
and the medians. Real trials are split by study (participant_paths, never a folder name) and
by clinical MAS. Unit caveat: the trial is the unit here because the question is about the
mechanics of one fall, not a property of a leg; participant counts are printed beside it.

Results are cached per trial (neuromech/out/fall_speed_cache.jsonl, keyed by path and the
scorer's fingerprint) because one OptiTrack load is ~20 s and this machine has little memory:
--limit lets it run in chunks, and a re-run only scores what is missing.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import statistics as st
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
OUT = os.path.join(ROOT, "neuromech", "out")
CACHE = os.path.join(OUT, "fall_speed_cache.jsonl")


def _fingerprint() -> str:
    h = hashlib.sha1()
    for mod in ("pendulastic_pt_score.py", "pt_report_common.py"):
        with open(os.path.join(ROOT, mod), "rb") as fh:
            h.update(fh.read())
    return h.hexdigest()[:12]


def _trials(opti_root, mas_csv):
    import pt_report_common as prc
    import spasticity_grouping as sg
    prc.OPTI_ROOT = os.path.abspath(os.path.expanduser(opti_root))
    if mas_csv:
        sg.MAS_CSV = os.path.abspath(os.path.expanduser(mas_csv))
    mas = sg.load_mas_by_leg(condition_prefix="pre")
    out = []
    for rec in prc.discover_all_trials():
        cond = str(rec["condition"]).lower()
        if not (cond.startswith("pre") or rec["condition"] == "default"):
            continue
        key = (str(rec["participant"]), rec["leg"])
        if key in mas:
            out.append(dict(path=rec["path"], participant=key[0], leg=key[1], mas=str(mas[key])))
    return out


def score_cohort(opti_root, rec_root, mas_csv, limit=None, verbose=True) -> list[dict]:
    import participant_paths as pp
    import pendulastic_pt_score as pts
    fp = _fingerprint()
    done = {}
    if os.path.exists(CACHE):
        for line in open(CACHE, encoding="utf-8"):
            r = json.loads(line)
            if r.get("fp") == fp:
                done[r["path"]] = r
    trials = _trials(opti_root, mas_csv)
    todo = [t for t in trials if t["path"] not in done]
    if limit is not None:
        todo = todo[:limit]
    if verbose:
        print(f"  {len(trials)} baseline trials, {len(done)} cached, scoring {len(todo)}", flush=True)
    rec_root = os.path.abspath(os.path.expanduser(rec_root))
    os.makedirs(OUT, exist_ok=True)
    with open(CACHE, "a", encoding="utf-8") as fh:
        for i, t in enumerate(todo, 1):
            row = dict(t, fp=fp, interpretable=False,
                       study=pp.group_for_diagnosis(pp.diagnosis_from_metadata(rec_root, t["participant"]))
                       or "ungrouped")
            try:
                tt, ang = pts.load_optitrack(t["path"])
                prm = pts.compute_pt_params(tt, ang)
            except Exception as e:                                   # noqa: BLE001
                prm, row["error"] = None, repr(e)[:160]
            if prm:
                est = pts.mas_estimate(prm)
                row.update(interpretable=bool(est["interpretable"]), A0=float(prm["A0_deg"]),
                           peak=float(prm["omega_peak_deg_s"]))
            fh.write(json.dumps(row) + "\n"); fh.flush()
            done[t["path"]] = row
            if verbose and i % 10 == 0:
                print(f"    scored {i}/{len(todo)}", flush=True)
    return [done[t["path"]] for t in trials if t["path"] in done]


def scaling(a0, peak) -> dict:
    from scipy.stats import spearmanr
    a0 = np.asarray(a0, float); peak = np.asarray(peak, float)
    return dict(n=len(a0), rho=float(spearmanr(peak, a0).correlation),
                slope=float(np.polyfit(np.log(a0), np.log(peak), 1)[0]),
                peak=float(np.median(peak)), a0=float(np.median(a0)))


def emulator_rows() -> list[dict]:
    import csv
    path = os.path.join(OUT, "scenario_manifest.csv")
    if not os.path.exists(path):
        raise SystemExit(f"{path} not found -- run `python -m neuromech.build` first")
    rows = []
    for r in csv.DictReader(open(path, encoding="utf-8")):
        try:
            a0, om = float(r["A0_deg"]), float(r["omega_max_n"])
        except (KeyError, ValueError):
            continue
        if np.isfinite(a0) and np.isfinite(om) and a0 > 0:
            rows.append(dict(sid=r["sid"], group=r["group"], A0=a0, peak=om * a0))
    return rows


def _measurable(r: dict) -> bool:
    import pendulastic_pt_score as pts
    a0, pk = r.get("A0"), r.get("peak")
    return (a0 is not None and pk is not None and np.isfinite(a0) and np.isfinite(pk)
            and 5.0 < a0 <= pts.MAX_INTERPRETABLE_A0_DEG)


def report(real: list[dict], emu: list[dict]) -> str:
    ok = [r for r in real if _measurable(r)]
    lines = [f"{'set':34s} {'trials':>6s} {'people':>6s} {'rho':>6s} {'slope':>6s} {'peak':>6s} {'A0':>5s}"]

    def line(name, rows, people="-"):
        if len(rows) < 3:
            lines.append(f"{name:34s} {len(rows):6d}  (too few)")
            return
        s = scaling([r["A0"] for r in rows], [r["peak"] for r in rows])
        lines.append(f"{name:34s} {s['n']:6d} {people!s:>6s} {s['rho']:+6.2f} {s['slope']:+6.2f} "
                     f"{s['peak']:6.0f} {s['a0']:5.1f}")

    def ppl(rows):
        return len({r["participant"] for r in rows})

    def per_leg(rows):
        # Repeated trials of one leg are not independent: summarise each leg by its median
        # trial and regress across legs, so a leg with eight trials counts once.
        legs = {}
        for r in rows:
            legs.setdefault((r["participant"], r["leg"]), []).append(r)
        return [dict(participant=k[0], A0=float(np.median([r["A0"] for r in v])),
                     peak=float(np.median([r["peak"] for r in v]))) for k, v in legs.items()]

    line("REAL, all measurable falls", ok, ppl(ok))
    line("REAL, per leg (leg medians)", per_leg(ok), ppl(ok))
    graded = [r for r in ok if r.get("interpretable")]
    line("REAL, graded by the scorer only", graded, ppl(graded))
    for study in sorted({r["study"] for r in ok}):
        sub = [r for r in ok if r["study"] == study]
        line(f"REAL, {study}", sub, ppl(sub))
    sub = [r for r in ok if r["mas"] != "0"]
    line("REAL, clinical MAS >= 1", sub, ppl(sub))
    line("EMULATOR, all scenarios (library mix)", emu)
    for g in sorted({r["group"] for r in emu}):
        line(f"EMULATOR, group {g}", [r for r in emu if r["group"] == g])
    lines.append("")
    lines.append("slope: log-log slope of peak fall speed on A0. 1.0 = peak scales with the swing;")
    lines.append("0 = peak independent of the swing. rho = Spearman(peak, A0). peak in deg/s, A0 in")
    lines.append("deg. The 'all scenarios' slope depends on how the library was composed; group A (the")
    lines.append("MAS ladder) and the ablation are the severity-axis comparisons.")
    return "\n".join(lines)


def ablation(rung: str = "2") -> str:
    """Each component of the published severity axis applied ALONE at one ladder rung.

    Says which part of the model changes the fall. Uses the calibrated ladder in
    neuromech/out/ladder.json, so it describes the library that was built.
    """
    from dataclasses import replace
    from neuromech import build as B, scenarios as S
    lad = json.load(open(os.path.join(OUT, "ladder.json")))
    if rung not in lad:
        return f"(no rung {rung!r} in ladder.json: {sorted(lad)})"
    lam = lad[rung]
    full, base = S.severity(lam), S.severity(0.0)
    rows = [("healthy (lambda 0)", base), (f"rung {rung}, full (lambda {lam:g})", full),
            ("tone only", replace(base, tone=full.tone)),
            ("k_f only", replace(base, k_f=full.k_f)),
            ("k_df only", replace(base, k_df=full.k_df))]
    out = [f"{'severity component at rung ' + rung:34s} {'A0':>6s} {'peak':>7s}"]
    for name, prm in rows:
        sc = S.Scenario("ablation", "A", "", prm)
        t, th, _, _ = B.render(sc)
        p, _ = B.score(t, th, sc)
        out.append(f"{name:34s} {p['A0_deg']:6.1f} {p['omega_peak_deg_s']:7.1f}")
    return "\n".join(out)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--opti-root", required=True, help="where OptiTrack_Recordings is")
    ap.add_argument("--rec-root", required=True, help="where Recordings is (participant metadata)")
    ap.add_argument("--mas-csv", help="mas_scores.csv, if not beside this checkout")
    ap.add_argument("--limit", type=int, help="score at most this many uncached trials, then report")
    a = ap.parse_args(argv)
    os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
    real = score_cohort(a.opti_root, a.rec_root, a.mas_csv, limit=a.limit)
    print()
    print(report(real, emulator_rows()))
    print()
    print(ablation())
    return 0


if __name__ == "__main__":
    sys.exit(main())
