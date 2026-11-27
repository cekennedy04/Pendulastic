"""
control_pt_score_distribution.py
================================
How do control participants score on the current scorer, against a 0.2 line?

Analysis only. Nothing here changes the scorer, HEALTHY_REF, the Rust core or
the app; the module imports them and reads the recordings in place.

Every trial of every participant is scored from up to five curves:

  phone_screen   the app's angle, replayed from the raw phone samples on the
                 reference footing (imu_calibration_config.REFERENCE_REPLAY_CONFIG,
                 named explicitly, at imu_calibration_tuner.ANALYSIS_TICK_S),
                 scored with detrend=False -- the convention the app shows.
  phone_stored   the same curve scored with detrend=True -- the convention the
                 app stores.
  shank          the OptiTrack shank plate's rotation about its own swing axis
                 (imu_optitrack_validation.optical_curves), as recorded.
  shank_filled   the same, dropped frames linearly interpolated (the
                 "v1b_gaps_filled" idea of pt_disagreement_diag.py, over the
                 whole take).
  loader         the knee angle from pendulastic_pt_score.load_optitrack: the
                 curve derive_healthy_ref.py derived HEALTHY_REF from.

Groups come from each participant's metadata.json through participant_paths;
no folder name is spelled here. Trials listed in excluded_trials.json are
scored too but reported apart, never pooled.

Circularity. HEALTHY_REF's data-derived entries are medians over the
interpretable baseline control trials on the `loader` curve. This module
rebuilds that set with derive_healthy_ref's own rules, marks which trials are
in it, and re-scores every control trial against a reference derived WITHOUT
that trial's participant (leave one person out).

Per-trial output is local only (--per-trial-csv); the repository is public.
The report gets aggregates and numeric participant IDs, nothing else.

Usage:
    .venv/Scripts/python.exe control_pt_score_distribution.py \
        --per-trial-csv <somewhere outside the repo>/control_pt_scores.csv \
        --report docs/reports/2026-11-25-control-pt-score-distribution.md
"""
from __future__ import annotations

import argparse
import csv
import glob
import os
import sys
from collections import Counter
from typing import Iterable, Optional

import numpy as np

SUPERVISOR_LINE = 0.2
CURVES = ("phone_screen", "phone_stored", "shank", "shank_filled", "loader")
CURVE_LABELS = {
    "phone_screen": "phone, on-screen (detrend off)",
    "phone_stored": "phone, stored (detrend on)",
    "shank": "optical shank, as recorded",
    "shank_filled": "optical shank, gaps filled",
    "loader": "optical knee angle (loader)",
}
#: HEALTHY_REF entries that are medians of the control cohort and are summed
#: into the score. R2n and N are literature anchors and do not move.
DATA_DERIVED_SCORED = ("phi_max_ratio", "f", "area_ratio")
BEGIN, END = "<!-- BEGIN GENERATED TABLES -->", "<!-- END GENERATED TABLES -->"


# ── pure statistics (tested without participant data) ────────────────────────

def fill_gaps(t: np.ndarray, y: np.ndarray) -> np.ndarray:
    """y with its non-finite samples linearly interpolated over t."""
    t, y = np.asarray(t, float), np.asarray(y, float)
    ok = np.isfinite(y)
    if ok.all() or not ok.any():
        return y.copy()
    return np.interp(t, t[ok], y[ok])


def distribution(values: Iterable[float], lines: Iterable[float]) -> dict:
    """n, median, quartiles, range, and the share strictly under each line."""
    v = np.asarray([x for x in values if x is not None and np.isfinite(x)], float)
    lines = list(lines)
    if len(v) == 0:
        return {"n": 0, "median": None, "q1": None, "q3": None, "min": None,
                "max": None, "under": {ln: None for ln in lines},
                "n_under": {ln: 0 for ln in lines}}
    q1, med, q3 = np.percentile(v, [25, 50, 75])
    return {"n": int(len(v)), "median": float(med), "q1": float(q1), "q3": float(q3),
            "min": float(v.min()), "max": float(v.max()),
            "under": {ln: float(np.mean(v < ln)) for ln in lines},
            "n_under": {ln: int(np.sum(v < ln)) for ln in lines}}


def by_leg(rows: list, curve: str, field: str = "pt") -> dict:
    """{leg_key: [interpretable scores]} for one curve. A leg is one leg in one
    session; a trial with no leg key is left out."""
    out: dict = {}
    for r in rows:
        value = r.get(f"{curve}_{field}")
        if r.get("leg_key") and r.get(f"{curve}_status") == "scored" and value is not None:
            out.setdefault(r["leg_key"], []).append(float(value))
    return out


def leg_medians(rows: list, curve: str, field: str = "pt") -> list:
    return [float(np.median(v)) for v in by_leg(rows, curve, field).values()]


def retest_spread(rows: list, curve: str, line: float = SUPERVISOR_LINE) -> dict:
    """Trial-to-trial spread within a leg, over legs with two or more scored
    trials: the range per leg, the pooled within-leg SD, and how many legs
    have trials on both sides of `line`."""
    legs = [np.asarray(v, float) for v in by_leg(rows, curve).values() if len(v) >= 2]
    if not legs:
        return {"n_legs": 0, "n_trials": 0, "range_median": None, "range_max": None,
                "sd_within": None, "straddle": 0}
    ranges = [float(v.max() - v.min()) for v in legs]
    dof = sum(len(v) - 1 for v in legs)
    ss = sum(float(np.sum((v - v.mean()) ** 2)) for v in legs)
    return {"n_legs": len(legs), "n_trials": int(sum(len(v) for v in legs)),
            "range_median": float(np.median(ranges)), "range_max": float(max(ranges)),
            "sd_within": float(np.sqrt(ss / dof)),
            "straddle": int(sum(1 for v in legs if v.min() < line <= v.max()))}


def status_counts(rows: list, curve: str) -> Counter:
    """How each trial ended on one curve; trials without that source are 'absent'."""
    return Counter(r.get(f"{curve}_status", "absent") for r in rows)


def claim_verdict(n_trials: int, n_trials_under: int, n_legs: int, n_legs_under: int) -> str:
    """"holds" only if every scored trial is under the line; "does not hold"
    if half the legs or fewer have their median under it; otherwise "partly
    holds". No scored trial at all is "cannot be said"."""
    if n_trials == 0 or n_legs == 0:
        return "cannot be said"
    if n_trials_under == n_trials:
        return "holds"
    if 2 * n_legs_under <= n_legs:
        return "does not hold"
    return "partly holds"


def reference_without(ref_trials: list, pid: Optional[str], shipped: dict) -> Optional[dict]:
    """`shipped` with its data-derived scored entries replaced by the medians
    over `ref_trials` (dicts with "pid" and the parameters) of everyone but
    `pid` (None leaves nobody out). None if nobody else is left."""
    others = [t for t in ref_trials if t["pid"] != pid]
    if not others:
        return None
    out = dict(shipped)
    for key in DATA_DERIVED_SCORED:
        out[key] = float(np.median([t[key] for t in others]))
    return out


# ── scoring one curve ────────────────────────────────────────────────────────

def score_angle(t, angle, detrend: bool = True, pt=None, reason_class=None) -> dict:
    """One curve on the scorer's own convention through compute_pt_params and
    mas_estimate. status is "scored", "refused: <why>", "unscored" (no swing
    found) or "error: <exception>"; pt is set only when status is "scored"."""
    if pt is None:
        import pendulastic_pt_score as pt
    if reason_class is None:
        from imu_optitrack_validation import _reason_class as reason_class
    try:
        params = pt.compute_pt_params(np.asarray(t, float), np.asarray(angle, float),
                                      detrend=detrend)
    except Exception as exc:                            # noqa: BLE001
        return {"status": f"error: scorer raised {type(exc).__name__}"}
    if not params:
        return {"status": "unscored"}
    est = pt.mas_estimate(params)
    breakdown = pt.compute_pt_score_breakdown(params)
    out = {"status": "scored" if est["interpretable"] else "refused: " + reason_class(est["reason"]),
           "pt": float(est["pt7"]) if est["interpretable"] else None,
           "pt_refused": None if est["interpretable"] else float(est["pt7"]),
           "A0": float(params["A0_deg"]),
           "flags": "|".join(params.get("quality_flags") or []),
           "top_term": max(breakdown, key=breakdown.get) if est["interpretable"] else "",
           "params": {k: float(params[k]) for k in pt.ALL_PT_KEYS if k in params}}
    return out


def _put(row: dict, curve: str, res: dict) -> None:
    for k, v in res.items():
        row[f"{curve}_{k}"] = v


# ── the corpus (reads recordings; not exercised by the tests) ────────────────

def is_baseline(condition: str) -> bool:
    """derive_healthy_ref's rule, verbatim: pre* or default."""
    c = str(condition).lower()
    return c.startswith("pre") or c == "default"


def discover() -> tuple:
    """(rows, notes): one row per trial that has a phone recording, an
    OptiTrack take, or both, excluded ones included and marked."""
    import batch_imu_vs_optitrack_rmse as batch
    import imu_optitrack_validation as v
    import pt_report_common as prc

    prc.OPTI_ROOT, prc.REC_ROOT = batch.OPTI_ROOT, batch.REC_ROOT
    excluded = prc.load_excluded_trials()
    rows, notes = {}, Counter()

    def row_for(parsed):
        key = prc.trial_key(parsed["participant"], parsed["leg"], parsed["condition"],
                            parsed["trial"])
        if key not in rows:
            pid = parsed["participant"]
            rows[key] = {"trial_key": key, "pid": pid, "group": v.group_of(pid),
                         "leg": parsed["leg"], "condition": parsed["condition"],
                         "leg_key": key.rsplit("_T", 1)[0],
                         "baseline": is_baseline(parsed["condition"]),
                         "excluded": key in excluded}
        return rows[key]

    optical = prc.discover_all_trials(include_archive=False, include_excluded=True)
    notes["optical_duplicate_keys"] = len(prc.duplicate_trial_keys(optical))
    for rec in optical:
        row_for(rec).setdefault("optitrack_path", rec["path"])
    for imu_path in sorted(glob.glob(os.path.join(batch.REC_ROOT, "**", "Trial_*_imu.csv"),
                                     recursive=True)):
        opti = batch.find_optitrack_match(imu_path, batch.REC_ROOT, batch.OPTI_ROOT)
        parsed = (prc._parse_trial_path(opti, batch.OPTI_ROOT) if opti else None) \
            or prc._parse_trial_path(imu_path, batch.REC_ROOT)
        if parsed is None:
            notes["phone_trials_not_attributable"] += 1
            continue
        row = row_for(parsed)
        row["phone"] = batch.derive_component_paths(imu_path)
        if opti:
            row.setdefault("optitrack_path", opti)
    return list(rows.values()), dict(notes)


class PhoneTrialUnusable(ValueError):
    """A phone trial that cannot be replayed; the message names no file."""


def replay_phone(components: dict) -> dict:
    """The app's angle for one phone trial on the reference footing, with the
    axis-fit report. The config is named, never loaded from a file."""
    import imu_calibration_config
    import imu_calibration_tuner
    import workbench_engine as engine

    validations = {kind: engine.validate_component_csv(components[kind], kind)
                   for kind in ("accel", "gyro", "mag", "imu")}
    bad = [k for k, val in validations.items() if not val["ok"]]
    if bad:
        raise PhoneTrialUnusable("unreadable component file")
    bound = engine.bind_split_csv_components(validations)
    capture: dict = {}
    t, angle = imu_calibration_tuner.replay_trial(
        bound["fusion_samples"], dict(imu_calibration_config.REFERENCE_REPLAY_CONFIG),
        tick_s=imu_calibration_tuner.ANALYSIS_TICK_S, capture=capture)
    if len(t) == 0:
        raise PhoneTrialUnusable("never zeroed, no release motion found")
    return {"t": t, "angle": angle, "axis_fit": capture.get("axis_fit") or {}}


def evaluate(row: dict) -> dict:
    """Score every curve the trial has. Never raises."""
    import imu_optitrack_validation as v
    import pendulastic_pt_score as pt

    for curve in CURVES:
        row[f"{curve}_status"] = "absent"
    if row.get("phone"):
        try:
            ph = replay_phone(row["phone"])
            row["axis_fit_flags"] = "|".join(ph["axis_fit"].get("flags") or [])
            _put(row, "phone_screen", score_angle(ph["t"], ph["angle"], detrend=False))
            _put(row, "phone_stored", score_angle(ph["t"], ph["angle"], detrend=True))
        except Exception as exc:                        # noqa: BLE001
            why = str(exc) if isinstance(exc, PhoneTrialUnusable) else type(exc).__name__
            for curve in ("phone_screen", "phone_stored"):
                row[f"{curve}_status"] = f"error: replay, {why}"
    if row.get("optitrack_path"):
        try:
            opt = v.optical_curves(row["optitrack_path"])
            flex = opt["shank_flex"]
            row["optical_gap_share"] = float(1.0 - np.isfinite(flex).mean())
            row["optical_flags"] = "|".join(opt["flags"])
            _put(row, "shank", score_angle(opt["t"], 180.0 - flex))
            _put(row, "shank_filled", score_angle(opt["t"], 180.0 - fill_gaps(opt["t"], flex)))
        except Exception as exc:                        # noqa: BLE001
            for curve in ("shank", "shank_filled"):
                row[f"{curve}_status"] = f"error: curve {type(exc).__name__}"
        try:
            t, ang = pt.load_optitrack(row["optitrack_path"])
            _put(row, "loader", score_angle(t, ang))
        except Exception as exc:                        # noqa: BLE001
            row["loader_status"] = f"error: curve {type(exc).__name__}"
    return row


def mark_reference_set(rows: list, shipped: dict, shipped_pids: Optional[set] = None) -> list:
    """Mark the trials derive_healthy_ref takes TODAY (control by metadata AND
    by OptiTrack folder, baseline, not excluded, interpretable on the loader
    curve) and re-score every control row against that set's medians, with
    nobody left out (pt_rederived) and with the trial's own participant left
    out (pt_loo). Returns the reference trials as dicts of pid + parameters.

    `shipped_pids`: the participants whose trials were in the derivation that
    produced the shipped HEALTHY_REF, when that is known from re-running the
    code that shipped it. The loader has changed since, so today's set is not
    that set; without the argument today's participants stand in."""
    import derive_healthy_ref as dhr
    import pendulastic_pt_score as pt

    ref_trials = []
    for r in rows:
        r["in_healthy_ref"] = bool(
            r["group"] == "control" and not r["excluded"] and r["baseline"]
            and r.get("loader_status") == "scored" and dhr._is_control(r["pid"]))
        if r["in_healthy_ref"]:
            ref_trials.append(dict(r["loader_params"], pid=r["pid"]))
    ref_pids = shipped_pids if shipped_pids is not None else {t["pid"] for t in ref_trials}
    everyone = reference_without(ref_trials, None, shipped)
    for r in rows:
        if r["group"] != "control":
            continue
        r["pid_in_healthy_ref"] = r["pid"] in ref_pids
        for field, ref in (("pt_rederived", everyone),
                           ("pt_loo", reference_without(ref_trials, r["pid"], shipped))):
            for curve in CURVES:
                if ref is not None and r.get(f"{curve}_status") == "scored":
                    r[f"{curve}_{field}"] = float(pt.compute_pt_score(r[f"{curve}_params"], ref))
    return ref_trials


# ── the report ───────────────────────────────────────────────────────────────

def _f(x, nd=3) -> str:
    return "n/a" if x is None else f"{x:.{nd}f}"


def _share(d: dict, line: float) -> str:
    if not d["n"]:
        return "n/a"
    return f"{d['n_under'][line]}/{d['n']} ({100 * d['under'][line]:.0f}%)"


def _dist_table(sets: list, lines: list, field: str = "pt") -> list:
    """sets: [(label, rows)]. One row per (set, curve, unit)."""
    head = ["set", "curve", "unit", "n", "median", "IQR", "range"] + [f"< {ln:g}" for ln in lines]
    out = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    for label, rows in sets:
        for curve in CURVES:
            per_trial = [r.get(f"{curve}_{field}") for r in rows
                         if r.get(f"{curve}_status") == "scored"]
            for unit, values in (("trial", per_trial), ("leg", leg_medians(rows, curve, field))):
                d = distribution(values, lines)
                if not d["n"]:
                    continue
                out.append("| " + " | ".join(
                    [label, CURVE_LABELS[curve], unit, str(d["n"]), _f(d["median"]),
                     f"{_f(d['q1'])} to {_f(d['q3'])}", f"{_f(d['min'])} to {_f(d['max'])}"]
                    + [_share(d, ln) for ln in lines]) + " |")
    return out


def _count(rows, what) -> str:
    pids = {r["pid"] for r in rows}
    legs = {(r["pid"], r["leg"]) for r in rows}
    sessions = {r["leg_key"] for r in rows}
    return (f"| {what} | {len(pids)} | {len(legs)} | {len(sessions)} | {len(rows)} | "
            f"{sum(1 for r in rows if r.get('phone'))} | "
            f"{sum(1 for r in rows if r.get('optitrack_path'))} | "
            f"{sum(1 for r in rows if r.get('phone') and r.get('optitrack_path'))} |")


def _ids(rows) -> str:
    pids = sorted({r["pid"] for r in rows}, key=lambda s: (not s.isdigit(), int(s) if s.isdigit() else 0, s))
    return ", ".join(pids) if pids else "none"


def build_tables(rows: list, notes: dict, zone_lines: tuple, meta: dict) -> str:
    """The generated block of the report: markdown, aggregates only."""
    lines = sorted({SUPERVISOR_LINE, *zone_lines})
    live = [r for r in rows if not r["excluded"]]
    ctl = [r for r in live if r["group"] == "control"]
    ctl_excl = [r for r in rows if r["excluded"] and r["group"] == "control"]
    ctl_in = [r for r in ctl if r.get("pid_in_healthy_ref")]
    ctl_out = [r for r in ctl if not r.get("pid_in_healthy_ref")]
    others = sorted({r["group"] for r in live} - {"control"})
    L = [BEGIN, "",
         f"Generated by `control_pt_score_distribution.py`. Scorer `{meta['formula']}`, "
         f"footing `{meta['footing']}`, replay config `{meta['config']}` at tick {meta['tick']} s.",
         "", "### G1. What was scored", "",
         "| set | participants | legs | leg sessions | trials | with phone | with OptiTrack | with both |",
         "|---|---|---|---|---|---|---|---|",
         _count(ctl, "control, in corpus"),
         _count(ctl_excl, "control, in `excluded_trials.json` (reported apart)")]
    for g in others:
        grp = [r for r in live if r["group"] == g]
        L.append(_count(grp, f"{g}, in corpus (all sessions)"))
        L.append(_count([r for r in grp if r["baseline"]], f"{g}, baseline sessions only"))
    L += ["", f"Control participant IDs in corpus: {_ids(ctl)}. With a phone recording: "
          f"{_ids([r for r in ctl if r.get('phone')])}. Excluded control trials belong to: {_ids(ctl_excl)}.",
          f"Phone trials whose path names no single participant and leg (left out): "
          f"{notes.get('phone_trials_not_attributable', 0)}. Trial keys shared by two OptiTrack files: "
          f"{notes.get('optical_duplicate_keys', 0)}.",
          "", "### G2. Controls: PT score per trial and per leg (shipped `HEALTHY_REF`)", "",
          "Scored (interpretable) trials only; a leg is one leg in one session and its value is the "
          "median of its scored trials.", ""]
    L += _dist_table([("control", ctl)], lines)

    L += ["", "### G3. Controls: how each trial ended", "",
          "| curve | trials with the source | scored | refused | unscored | error | reasons (not scored) |",
          "|---|---|---|---|---|---|---|"]
    for curve in CURVES:
        c = status_counts(ctl, curve)
        have = sum(n for s, n in c.items() if s != "absent")
        why = "; ".join(f"{s} x{n}" for s, n in sorted(c.items())
                        if s not in ("scored", "absent")) or "none"
        L.append(f"| {CURVE_LABELS[curve]} | {have} | {c.get('scored', 0)} | "
                 f"{sum(n for s, n in c.items() if s.startswith('refused'))} | {c.get('unscored', 0)} | "
                 f"{sum(n for s, n in c.items() if s.startswith('error'))} | {why} |")

    ctl_phone = [r for r in ctl if r.get("phone")]
    fit = Counter(f for r in ctl_phone for f in (r.get("axis_fit_flags") or "").split("|") if f)
    unscored_fit = Counter(f for r in ctl_phone if r.get("phone_screen_status") != "scored"
                           for f in (r.get("axis_fit_flags") or "").split("|") if f)
    L += ["", f"Axis-fit flags on the {len(ctl_phone)} control phone trials: "
          + (", ".join(f"{k} x{n}" for k, n in fit.most_common()) or "none")
          + "; on the ones not scored: "
          + (", ".join(f"{k} x{n}" for k, n in unscored_fit.most_common()) or "none") + "."]
    L += ["", "### G4. Controls: trial-to-trial spread within a leg", "",
          f"Legs with two or more scored trials. \"Straddle\" = legs with trials on both sides of {SUPERVISOR_LINE:g}.",
          "", "| curve | legs | trials | range within leg, median | largest | pooled within-leg SD | straddle |",
          "|---|---|---|---|---|---|---|"]
    for curve in CURVES:
        s = retest_spread(ctl, curve)
        if s["n_legs"]:
            L.append(f"| {CURVE_LABELS[curve]} | {s['n_legs']} | {s['n_trials']} | {_f(s['range_median'])} | "
                     f"{_f(s['range_max'])} | {_f(s['sd_within'])} | {s['straddle']} |")

    L += ["", "### G5. Circularity: who is in `HEALTHY_REF`, and scores with that person left out", "",
          f"Participants in the derivation that produced the shipped `HEALTHY_REF` ({meta['shipped_source']}): "
          f"{_ids(ctl_in)}. Control participants in the corpus outside it: {_ids(ctl_out)}.", "",
          f"The derivation set as rebuilt today with the current loader (control, baseline, not excluded, "
          f"interpretable on the loader curve): {meta['ref_n_trials']} trials, {meta['ref_n_legs']} legs, "
          f"{meta['ref_n_pids']} participants (IDs {meta['ref_pids']}). "
          "Data-derived entries, shipped against the median of today's set: "
          + "; ".join(f"`{k}` {meta['shipped'][k]:.4f} / {meta['rederived'][k]:.4f}"
                      for k in DATA_DERIVED_SCORED) + ".", "",
          "\"Re-derived\" scores use today's set medians; \"own data left out\" uses today's set without "
          "the trial's own participant.", ""]
    L += _dist_table([("in shipped derivation, shipped ref", ctl_in)], lines)
    L += _dist_table([("outside shipped derivation (held out), shipped ref", ctl_out)], lines)[2:]
    L += _dist_table([("all controls, re-derived ref", ctl)], lines, field="pt_rederived")[2:]
    L += _dist_table([("all controls, own data left out", ctl)], lines, field="pt_loo")[2:]
    L += _dist_table([("excluded control trials, shipped ref", ctl_excl)], lines)[2:]

    over = [r for r in ctl if any(r.get(f"{c}_status") == "scored" and r[f"{c}_pt"] >= SUPERVISOR_LINE
                                  for c in CURVES)]
    L += ["", f"### G6. Control trials at or over {SUPERVISOR_LINE:g}", "",
          f"{len(over)} of {len(ctl)} control trials are at or over {SUPERVISOR_LINE:g} on at least one curve; "
          f"they belong to participants {_ids(over)}.", "",
          "| curve | trials at or over | of scored | participants | A0 deg, median (range) | A0 of trials under, median | "
          "dropped optical frames, median share: over / under | largest score term | scorer flags | "
          "axis-fit / optical flags |",
          "|---|---|---|---|---|---|---|---|---|---|"]
    for curve in CURVES:
        scored = [r for r in ctl if r.get(f"{curve}_status") == "scored"]
        hi = [r for r in scored if r[f"{curve}_pt"] >= SUPERVISOR_LINE]
        lo = [r for r in scored if r[f"{curve}_pt"] < SUPERVISOR_LINE]
        if not scored:
            continue
        a0 = [r[f"{curve}_A0"] for r in hi]
        terms = Counter(r[f"{curve}_top_term"] for r in hi)
        flags = Counter(f for r in hi for f in (r.get(f"{curve}_flags") or "").split("|") if f)
        side_key = "axis_fit_flags" if curve.startswith("phone") else "optical_flags"
        side = Counter(f for r in hi for f in (r.get(side_key) or "").split("|") if f)
        fmt = lambda c: ", ".join(f"{k} x{n}" for k, n in c.most_common()) or "none"   # noqa: E731
        gap = lambda rs: _f(float(np.median([r["optical_gap_share"] for r in rs  # noqa: E731
                                             if r.get("optical_gap_share") is not None])), 2) \
            if any(r.get("optical_gap_share") is not None for r in rs) else "n/a"
        L.append(f"| {CURVE_LABELS[curve]} | {len(hi)} | {len(scored)} | {_ids(hi)} | "
                 + (f"{np.median(a0):.0f} ({min(a0):.0f} to {max(a0):.0f})" if a0 else "n/a")
                 + f" | {_f(float(np.median([r[f'{curve}_A0'] for r in lo])) if lo else None, 0)} | "
                 f"{gap(hi)} / {gap(lo)} | {fmt(terms)} | {fmt(flags)} | {fmt(side)} |")

    L += ["", "### G7. Other groups, for contrast (group level only)", ""]
    sets = []
    for g in others:
        grp = [r for r in live if r["group"] == g]
        base = [r for r in grp if r["baseline"]]
        sets += [(f"{g}, baseline", base)] if len(base) == len(grp) else \
            [(f"{g}, baseline", base), (f"{g}, all sessions", grp)]
    L += _dist_table(sets, lines)
    L += ["", "| set | curve | trials with the source | scored | refused | unscored | error | reasons (not scored) |",
          "|---|---|---|---|---|---|---|---|"]
    for label, grp in sets:
        for curve in CURVES:
            c = status_counts(grp, curve)
            have = sum(n for s, n in c.items() if s != "absent")
            why = "; ".join(f"{s} x{n}" for s, n in sorted(c.items())
                            if s not in ("scored", "absent")) or "none"
            if have:
                L.append(f"| {label} | {CURVE_LABELS[curve]} | {have} | {c.get('scored', 0)} | "
                         f"{sum(n for s, n in c.items() if s.startswith('refused'))} | {c.get('unscored', 0)} | "
                         f"{sum(n for s, n in c.items() if s.startswith('error'))} | {why} |")

    L += ["", f"### G8. Does \"controls score under {SUPERVISOR_LINE:g}\" hold?", "",
          "| curve | participants with a scored trial | scored trials under | legs with median under | verdict |",
          "|---|---|---|---|---|"]
    for curve in CURVES:
        dt = distribution([r.get(f"{curve}_pt") for r in ctl if r.get(f"{curve}_status") == "scored"],
                          [SUPERVISOR_LINE])
        dl = distribution(leg_medians(ctl, curve), [SUPERVISOR_LINE])
        who = len({r["pid"] for r in ctl if r.get(f"{curve}_status") == "scored"})
        L.append(f"| {CURVE_LABELS[curve]} | {who} | {_share(dt, SUPERVISOR_LINE)} | {_share(dl, SUPERVISOR_LINE)} | "
                 f"{claim_verdict(dt['n'], dt['n_under'][SUPERVISOR_LINE], dl['n'], dl['n_under'][SUPERVISOR_LINE])} |")
    L += ["", END]
    return "\n".join(L)


def write_report(path: str, block: str) -> None:
    """Replace the generated block of the report, keeping the prose around
    it; a report that does not exist yet is created holding only the block."""
    text = ""
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
    if BEGIN in text and END in text:
        text = text[:text.index(BEGIN)] + block + text[text.index(END) + len(END):]
    else:
        text = (text.rstrip("\n") + "\n\n" if text else "") + block + "\n"
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def write_per_trial(path: str, rows: list) -> None:
    skip = {"phone", "optitrack_path"} | {f"{c}_params" for c in CURVES}
    fields = sorted({k for r in rows for k in r} - skip)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--report", required=True, help="markdown report whose generated block is rewritten")
    ap.add_argument("--per-trial-csv", help="local per-trial table; keep it out of the repository")
    ap.add_argument("--shipped-ref-pids",
                    help="comma-separated numeric IDs of the participants in the derivation that "
                         "produced the shipped HEALTHY_REF, from re-running the code that shipped it")
    a = ap.parse_args(argv)

    import imu_calibration_config
    import imu_calibration_tuner
    import pendulastic_pt_score as pt

    rows, notes = discover()
    for n, row in enumerate(rows):
        evaluate(row)
        print(f"\r{n + 1}/{len(rows)}", end="", file=sys.stderr, flush=True)
    print(file=sys.stderr)
    shipped_pids = ({p.strip() for p in a.shipped_ref_pids.split(",") if p.strip()}
                    if a.shipped_ref_pids else None)
    ref_trials = mark_reference_set(rows, pt.HEALTHY_REF, shipped_pids)
    ref_rows = [r for r in rows if r.get("in_healthy_ref")]
    meta = {
        "formula": pt.SCORE_FORMULA_ID, "config": "REFERENCE_REPLAY_CONFIG",
        "footing": "ref-100hz-ema0.5-flexrec"
        if imu_calibration_config.REFERENCE_REPLAY_CONFIG.get("flex_axis_fit") == "recording"
        else "unknown",
        "tick": imu_calibration_tuner.ANALYSIS_TICK_S,
        "ref_n_trials": len(ref_rows), "ref_n_legs": len({(r["pid"], r["leg"]) for r in ref_rows}),
        "ref_n_pids": len({r["pid"] for r in ref_rows}), "ref_pids": _ids(ref_rows),
        "shipped_source": "given with --shipped-ref-pids" if shipped_pids is not None
        else "not given; today's set stands in",
        "shipped": {k: pt.HEALTHY_REF[k] for k in DATA_DERIVED_SCORED},
        "rederived": {k: float(np.median([t[k] for t in ref_trials])) if ref_trials else float("nan")
                      for k in DATA_DERIVED_SCORED},
    }
    if a.per_trial_csv:
        write_per_trial(a.per_trial_csv, rows)
    write_report(a.report, build_tables(rows, notes, (pt.PT_HEALTHY_MAX, pt.PT_BORDERLINE_MAX), meta))
    return 0


if __name__ == "__main__":
    sys.exit(main())
