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


# ── The mismatch check (plan B13), emulator-only against a pinned real reference ────────────
#
# Fixed 2026-11-02, BEFORE any revised severity axis (the HESS group K first) was measured
# against it, and revised the same day on a second-opinion review (Codex), still before: the
# verdict is NO FLAG rather than PASS, and the per-profile band is a bootstrap prediction band.
# Changing a constant below after seeing a candidate's result defeats the check;
# re-pin the reference instead (B11's settled geometry will need that) and say so.
#
# The real side is pinned as one row per leg (median trial), with people anonymised to an
# index: a bootstrap needs the legs, not the recordings. From it:
#   b       OLS of log peak on log A0 across legs
#   CI      95 % interval for b, bootstrapping PEOPLE (a person's two legs are not independent)
#   band    per bootstrap draw (people resampled, line refitted) one leg's residual about the
#           refitted line plus the refit's slope error times the profile's log A0 step: the
#           central 95 % of that is where one real leg would land, slope uncertainty, clustering
#           and a non-normal spread all carried, with no normal multiplier assumed
# An emulator profile set is judged relative to an anchor scenario (default A-P0, the healthy
# preset), which removes the emulator's healthy-level offset (281 vs ~346 deg/s, report):
#   per profile  r = (log peak_p - log peak_anchor) - b * (log A0_p - log A0_anchor);
#                MISMATCH if r is outside the band: the fall speed moved further than a real
#                leg's does at that A0. This is what catches a catch that slows the fall at fixed
#                A0. The band is between-leg spread, not a clinical tolerance, so the report
#                states the smallest change it would flag; smaller slowing passes unseen.
#                Several profiles each tested at 95 % flag more often than 5 % by chance; for a
#                screen that errs toward flagging, that is accepted.
#   per set      log-log slope over anchor + profiles; MISMATCH if outside CI. Judged only when
#                the set's A0 spans at least MIN_A0_SPAN (max/min, a log range of 0.18): over a
#                narrower span a deterministic slope is a ratio of two small differences and
#                means nothing. Inside the CI is a compatibility screen with ~11 people, not a
#                match. This catches peak and A0 falling
#                together (slope ~1), which a single profile's residual can hide.
# Verdict: MISMATCH if any profile or the set slope is flagged; otherwise NO FLAG, or NO FLAG
# (slope not judged) when the span gate leaves only the per-profile test, which then cannot
# speak to speed-excursion coupling at all. NO FLAG is a failure to flag, not equivalence and
# not a realism certificate (plan section 6 step 5).
REFERENCE = os.path.join(ROOT, "neuromech", "fall_speed_reference.json")
BOOT_N, BOOT_SEED = 2000, 0
BAND = (2.5, 97.5)
MIN_A0_SPAN = 1.2


def per_leg(rows: list[dict]) -> list[dict]:
    """Repeated trials of one leg are not independent: summarise each leg by its median trial."""
    legs = {}
    for r in rows:
        legs.setdefault((r["participant"], r.get("leg")), []).append(r)
    return [dict(participant=k[0], leg=k[1], study=v[0].get("study"), mas=v[0].get("mas"), trials=len(v),
                 A0=float(np.median([r["A0"] for r in v])), peak=float(np.median([r["peak"] for r in v])))
            for k, v in legs.items()]


def pin_reference(real: list[dict], path: str = REFERENCE) -> dict:
    """Write the real per-leg reference the emulator-only check reads. People become 1..n."""
    import pendulastic_pt_score as pts
    legs = sorted(per_leg([r for r in real if _measurable(r)]), key=lambda g: (str(g["participant"]), str(g["leg"])))
    ids = {p: i + 1 for i, p in enumerate(dict.fromkeys(g["participant"] for g in legs))}
    ref = dict(
        what="real per-leg peak fall speed vs A0 (median trial per leg), neuromech.fall_speed",
        a0_source="OptiTrack (load_optitrack); shrinks angles (plan 5b), re-pin on B11 geometry",
        score_formula_id=pts.SCORE_FORMULA_ID, scorer_fingerprint=_fingerprint(),
        legs=[dict(person=ids[g["participant"]], study=g["study"], mas=g["mas"], trials=g["trials"],
                   A0=round(g["A0"], 3), peak=round(g["peak"], 2)) for g in legs])
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(ref, fh, indent=1)
        fh.write("\n")
    return ref


def load_reference(path: str = REFERENCE) -> dict:
    if not os.path.exists(path):
        raise SystemExit(f"{path} not found -- pin it from the recordings with --pin")
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def reference_stats(legs: list[dict]) -> dict:
    x = np.log([g["A0"] for g in legs]); y = np.log([g["peak"] for g in legs])
    b, a = np.polyfit(x, y, 1)
    s = float(np.std(y - (a + b * x), ddof=2))
    people = sorted({g["person"] for g in legs})
    rng = np.random.default_rng(BOOT_SEED)
    boots, draws = [], []
    for _ in range(BOOT_N):
        pick = rng.choice(people, size=len(people), replace=True)
        idx = [i for p in pick for i, g in enumerate(legs) if g["person"] == p]
        if np.ptp(x[idx]) > 0:
            bb, aa = np.polyfit(x[idx], y[idx], 1)
            boots.append(bb)
            j = idx[rng.integers(len(idx))]
            draws.append((bb - b, y[j] - (aa + bb * x[j])))   # (slope error, one leg's residual)
    lo, hi = np.percentile(boots, [2.5, 97.5])
    return dict(b=float(b), s=s, ci=(float(lo), float(hi)), legs=len(legs), people=len(people),
                draws=np.asarray(draws))


def band(stats: dict, dx: float) -> tuple[float, float]:
    """Central 95 % of where one real leg's anchored residual lands for a log A0 step dx."""
    d = stats["draws"]
    lo, hi = np.percentile(d[:, 1] + d[:, 0] * dx, BAND)
    return float(lo), float(hi)


def check(emu: list[dict], sids: list[str], ref: dict, anchor: str = "A-P0") -> dict:
    """Judge one emulator profile set against the pinned reference by the rule fixed above."""
    st_ = reference_stats(ref["legs"])
    by = {r["sid"]: r for r in emu}
    if anchor not in by:
        raise SystemExit(f"anchor {anchor} is not in the manifest")
    missing = [s for s in sids if s not in by]
    if missing:
        raise SystemExit(f"not in the manifest (or no measurable fall): {missing}")
    h = by[anchor]
    profiles = []
    for sid in sids:
        if sid == anchor:
            continue
        p = by[sid]
        dx, dy = np.log(p["A0"] / h["A0"]), np.log(p["peak"] / h["peak"])
        r = float(dy - st_["b"] * dx)
        lo, hi = band(st_, dx)
        profiles.append(dict(sid=sid, A0=p["A0"], peak=p["peak"], resid=r, band=(lo, hi),
                             mismatch=not (lo <= r <= hi)))
    pts_ = [h] + [by[p["sid"]] for p in profiles]
    a0s = [r["A0"] for r in pts_]
    span = max(a0s) / min(a0s)
    slope = None
    if span >= MIN_A0_SPAN and len(pts_) >= 2:
        slope = float(np.polyfit(np.log(a0s), np.log([r["peak"] for r in pts_]), 1)[0])
    slope_mismatch = slope is not None and not (st_["ci"][0] <= slope <= st_["ci"][1])
    if not profiles:
        raise SystemExit("no profiles to judge besides the anchor")
    if slope_mismatch or any(p["mismatch"] for p in profiles):
        verdict = "MISMATCH"
    else:
        verdict = "NO FLAG" if slope is not None else "NO FLAG (slope not judged)"
    return dict(anchor=anchor, ref=st_, profiles=profiles, span=span, slope=slope,
                slope_mismatch=slope_mismatch, verdict=verdict)


def check_report(res: dict, name: str) -> str:
    st_ = res["ref"]
    lo0, hi0 = band(st_, 0.0)
    out = [f"{name}: {res['verdict']}  (anchor {res['anchor']}; reference {st_['legs']} legs / "
           f"{st_['people']} people, b {st_['b']:+.2f} [95% CI {st_['ci'][0]:+.2f}, {st_['ci'][1]:+.2f}])",
           f"  at equal A0 it flags only a fall-speed change beyond {np.expm1(lo0):+.0%} / {np.expm1(hi0):+.0%}",
           f"  {'profile':10s} {'A0':>6s} {'peak':>7s} {'resid':>7s} {'band':>17s}"]
    for p in res["profiles"]:
        out.append(f"  {p['sid']:10s} {p['A0']:6.1f} {p['peak']:7.1f} {p['resid']:+7.3f} "
                   f"[{p['band'][0]:+.3f}, {p['band'][1]:+.3f}]" + ("  MISMATCH" if p["mismatch"] else ""))
    if res["slope"] is None:
        out.append(f"  set slope not judged: A0 spans x{res['span']:.2f} < x{MIN_A0_SPAN}")
    else:
        out.append(f"  set slope {res['slope']:+.2f} over A0 x{res['span']:.2f}"
                   + ("  MISMATCH (outside the CI)" if res["slope_mismatch"] else "  (inside the CI)"))
    return "\n".join(out)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--opti-root", help="where OptiTrack_Recordings is")
    ap.add_argument("--rec-root", help="where Recordings is (participant metadata)")
    ap.add_argument("--mas-csv", help="mas_scores.csv, if not beside this checkout")
    ap.add_argument("--limit", type=int, help="score at most this many uncached trials, then report")
    ap.add_argument("--pin", action="store_true", help="write the real per-leg reference from the recordings")
    ap.add_argument("--emulator-only", action="store_true",
                    help="no recordings: judge the emulator against the pinned reference")
    ap.add_argument("--check", default="A", help="comma-separated groups or scenario ids to judge (default A)")
    ap.add_argument("--anchor", default="A-P0", help="healthy scenario the set is judged relative to")
    a = ap.parse_args(argv)
    os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
    emu = emulator_rows()
    if not a.emulator_only:
        if not (a.opti_root and a.rec_root):
            ap.error("--opti-root and --rec-root are needed unless --emulator-only")
        real = score_cohort(a.opti_root, a.rec_root, a.mas_csv, limit=a.limit)
        if a.pin:
            ref = pin_reference(real)
            print(f"pinned {len(ref['legs'])} legs -> {REFERENCE}")
        print()
        print(report(real, emu))
        print()
        print(ablation())
        print()
    ref = load_reference()
    import pendulastic_pt_score as pts
    if ref.get("score_formula_id") != pts.SCORE_FORMULA_ID:
        print(f"note: reference pinned under {ref.get('score_formula_id')}, scorer is {pts.SCORE_FORMULA_ID}")
    groups = {r["group"] for r in emu}
    for item in a.check.split(","):
        item = item.strip()
        sids = [r["sid"] for r in emu if r["group"] == item] if item in groups else [item]
        print(check_report(check(emu, sids, ref, anchor=a.anchor), f"set {item}"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
