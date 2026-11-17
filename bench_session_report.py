"""One-command bench session report: phone app export vs 1 kHz encoder.

    python bench_session_report.py --phone <export dir | manifest.json | trial.jsonl> \
        --encoder <dir of .bin> --out <dir> [--rig-log rig_log.csv] [--since 2026-11-18]

Writes into --out:
  trials.csv        one row per phone trial (app status + paired encoder + metrics)
  steps.csv         one row per detected step (step trials only)
  summary.md        phone-vs-encoder tables (f, N, tau, overshoot), app export/refusal
                    counts, ALGORITHM_VERSION(s) seen in the export
  fig_ringdown_overlay.png, fig_f_scatter.png, fig_decay_scatter.png
  fig_step_overlay.png (only if step trials were found)

What it measures (horizontal rig): the phone's RAW GYRO channel (gyro-y integrated)
against the encoder, with the same estimator on both sides (bench_dynamics.py), plus
whether the app captured / exported / scored each trial. It deliberately does NOT
report the app's fused angle or PT score as an accuracy number: on a horizontal plane
gravity lies on the rotation axis and the fused angle is not assessable. The app's own
f / N are carried in trials.csv as `app_fused_*` diagnostics only.

Pairing. Phone trials are ordered by manifest capture timestamp (export trial numbers
can be scrambled).
  * With --rig-log (recommended): the log IS the pairing. Rows whose app_result starts
    with ERROR (the app saved nothing) are dropped, the rest are taken in encoder-trial
    order and paired in sequence with the exported phone trials. Cross-correlation
    only measures the lag and verifies; a count mismatch is reported.
  * Without a log: a monotone sequence alignment on post-release cross-correlation
    (bench_dynamics.pair_by_order). It can tell different P values apart, but NOT
    repeats of one condition, so a missing trial may be skipped at the wrong repeat.
Either way one polarity is used for the whole session (majority vote, or
--polarity). Rows are flagged when match_q < 0.5 or, for ring-downs, when phone and
encoder f differ by more than 10 % (a likely mis-pair). Check the `flag` column.

Block (ringdown vs step) comes from --block, else the rig log (R/ring/ringdown,
S/step), else is inferred from the encoder trace (two-sided holds = staircase).

Rig log (CSV, one row per encoder file, ERROR trials included; extra columns are
carried through):
  encoder_trial,block,P,I,D,lift,release,step_speed,app_result,notes
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import glob
import json
import os
from collections import Counter

import numpy as np

import bench_dynamics as bd

INK = "#14212B"       # encoder (truth)
TEAL = "#00819C"      # phone gyro
MUTED = "#8894A0"


# ── phone export ───────────────────────────────────────────────────────────────
def _trial_entry(path, meta=None, manifest=None):
    meta = meta or {}
    params = meta.get("params")
    return dict(
        file=os.path.basename(path), path=path,
        timestamp=meta.get("timestamp"),
        algorithm_version=meta.get("algorithm_version")
        or (manifest or {}).get("algorithm_version"),
        schema=(manifest or {}).get("schema"),
        has_manifest=manifest is not None,
        exported=os.path.exists(path),
        scored=params is not None if manifest is not None else None,
        scorable=meta.get("scorable", params is not None) if manifest is not None else None,
        capture_quality=meta.get("capture_quality"),
        analysis_refusal=meta.get("analysis_refusal"),
        excluded=meta.get("excluded_at") is not None,
        research=bool(meta.get("research")),
        quick_test=bool(meta.get("quick_test")),
        app_fused_f=(params or {}).get("f"),
        app_fused_n=(params or {}).get("n"),
    )


def load_phone(spec, since=None):
    """Phone trials from an export dir, a manifest, or a single .jsonl, in capture order."""
    trials = []
    if os.path.isfile(spec) and spec.endswith(".json"):
        manifests = [spec]
    elif os.path.isfile(spec):
        manifests = []
        trials.append(_trial_entry(spec, {"timestamp": os.path.getmtime(spec) * 1000}))
    else:
        manifests = sorted(glob.glob(os.path.join(spec, "*manifest*.json"))
                           + glob.glob(os.path.join(spec, "*", "*manifest*.json")))
        if not manifests:                                  # loose jsonl, no manifest
            for p in sorted(glob.glob(os.path.join(spec, "*.jsonl")), key=bd.trial_num):
                if not os.path.basename(p).startswith("_DUP_"):
                    trials.append(_trial_entry(p, {"timestamp": os.path.getmtime(p) * 1000}))
    seen = set()
    for m in manifests:
        with open(m, encoding="utf-8") as fh:
            man = json.load(fh)
        base = os.path.dirname(m)
        for t in man.get("trials", []):
            if t["file"] in seen:                          # same trial in two manifests
                continue
            seen.add(t["file"])
            trials.append(_trial_entry(os.path.join(base, t["file"]), t, man))
    if since:
        cut = dt.datetime.fromisoformat(since).timestamp() * 1000
        trials = [t for t in trials if (t["timestamp"] or 0) >= cut]
    trials.sort(key=lambda t: (t["timestamp"] or 0))
    for rank, t in enumerate(trials, 1):
        t["capture_rank"] = rank
        t["time"] = (dt.datetime.fromtimestamp(t["timestamp"] / 1000).isoformat(timespec="seconds")
                     if t["timestamp"] else "")
    return trials


# ── rig log ────────────────────────────────────────────────────────────────────
BLOCK_ALIASES = {"r": "ringdown", "ring": "ringdown", "ringdown": "ringdown",
                 "ring-down": "ringdown", "s": "step", "step": "step", "steps": "step"}


def normalize_block(v, where=""):
    """'R'/'ring'/'ringdown' -> 'ringdown', 'S'/'step' -> 'step', '' -> None."""
    v = (v or "").strip().lower()
    if not v:
        return None
    if v not in BLOCK_ALIASES:
        raise SystemExit(f"unknown block {v!r}{where}: use R/ring/ringdown or S/step")
    return BLOCK_ALIASES[v]


def pkey(v):
    """'1', '1.0', ' 1 ' -> '1'; non-numbers pass through stripped."""
    x = _num(v)
    return f"{x:g}" if np.isfinite(x) else (v or "").strip()


def load_rig_log(path):
    """Rig-log rows in encoder-trial order, each with `_trial` (int), `_block`,
    `_error` (app_result starts with ERROR) and `_unscorable`. UTF-8 (with or without
    BOM) first, then Windows cp1252 (Excel's "CSV" save)."""
    if not path:
        return []
    text = None
    for enc in ("utf-8-sig", "cp1252"):
        try:
            with open(path, newline="", encoding=enc) as fh:
                text = fh.read()
            break
        except UnicodeDecodeError:
            continue
    rows = []
    for i, row in enumerate(csv.DictReader(text.splitlines()), 2):
        row = {(k or "").strip(): (v.strip() if isinstance(v, str) else v) for k, v in row.items()}
        key = row.get("encoder_trial") or row.get("encoder_file") or ""
        if not key:
            continue
        row["_trial"] = int(key) if key.isdigit() else bd.trial_num(key)
        row["_block"] = normalize_block(row.get("block"), f" on {os.path.basename(path)} line {i}")
        res = (row.get("app_result") or "").strip().upper()
        row["_error"] = res.startswith("ERROR")
        row["_unscorable"] = "NOT SCORABLE" in res or res == "UNSCORABLE"
        for k in ("P", "step_speed"):
            if k in row:
                row[k] = pkey(row[k])
        rows.append(row)
    rows.sort(key=lambda r: r["_trial"])
    return rows


def pair_from_log(trials, gyro, encs, log_rows, polarity=None):
    """Pair exported phone trials (capture order) with the logged, non-ERROR encoder
    trials (trial order) in sequence. Cross-correlation measures lag and verifies only.
    Returns (pairs {phone_path: (enc, q, lag, sign)}, notes [str])."""
    by_num = {bd.trial_num(e): e for e in encs}
    notes = []
    usable = [r for r in log_rows if not r["_error"]]
    absent = [r["_trial"] for r in usable if r["_trial"] not in by_num]
    if absent:
        notes.append(f"logged encoder trials with no .bin found: {absent}")
    usable = [r for r in usable if r["_trial"] in by_num]
    phones = [t for t in trials if gyro.get(t["path"]) is not None]
    if len(phones) != len(usable):
        notes.append(f"COUNT MISMATCH: {len(phones)} exported phone trials vs {len(usable)} "
                     f"logged non-ERROR encoder trials -- pairing stops at the shorter list; "
                     f"fix the log (missing row? ERROR not marked?) before trusting it")
    seq = list(zip(phones, usable))

    def _align(sign):
        out = {}
        for t, r in seq:
            pg = gyro[t["path"]]
            ef = by_num[r["_trial"]]
            te, pe, _ = bd.encoder(ef)
            q, lag, sg = bd.xcorr_align(pg[0], pg[1], te, pe, sign=sign)
            out[t["path"]] = (ef, q, lag, sg)
        return out

    pairs = _align(polarity)
    if polarity is None and pairs:
        pairs = _align(bd.session_polarity(pairs))
    return pairs, notes


def infer_block(enc, hold_s=0.5, still_deg=1.0, far_frac=0.3):
    """'step' if the encoder HOLDS still away from zero on both sides (the staircase
    0 -> +A -> -A -> +A -> 0); else 'ringdown' (a Fast Trial holds on one side only,
    then returns and rings about zero)."""
    t, x, _ = enc
    dt = float(np.median(np.diff(t)))
    n = max(1, int(hold_s / dt))
    amax = float(np.max(np.abs(x))) or 1.0
    signs = set()
    for k in range(0, len(x) - n, max(1, n // 2)):
        if np.ptp(x[k:k + n]) < still_deg:              # position, not the noisy velocity
            m = float(np.mean(x[k:k + n]))
            if abs(m) > far_frac * amax:
                signs.add(np.sign(m))
    return "step" if len(signs) == 2 else "ringdown"


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return np.nan


# ── analysis ───────────────────────────────────────────────────────────────────
def analyze(phone, enc_dir, out, glob_pat="*.bin", min_num=None, max_num=None,
            rig_log=None, window=3, since=None, block=None, polarity=None):
    os.makedirs(out, exist_ok=True)
    block = normalize_block(block, " (--block)")
    trials = load_phone(phone, since)
    if not trials:
        raise SystemExit(f"no phone trials found in {phone}")
    encs = bd.list_encoders(enc_dir, glob_pat, min_num, max_num)
    if not encs:
        raise SystemExit(f"no non-empty encoder files matching {glob_pat!r} in {enc_dir}")
    log_rows = load_rig_log(rig_log)
    log = {r["_trial"]: r for r in log_rows}

    gyro = {}
    for t in trials:
        gyro[t["path"]] = bd.phone_gyro(t["path"]) if t["exported"] else None
    if log_rows:
        pairs, notes = pair_from_log(trials, gyro, encs, log_rows, polarity)
        method = "rig log (sequence; cross-correlation verifies)"
    else:
        phones = [(t["path"], gyro[t["path"]]) for t in trials]
        pairs = bd.pair_by_order(phones, encs, window, sign=polarity)
        if polarity is None and pairs:
            pairs = bd.pair_by_order(phones, encs, window, sign=bd.session_polarity(pairs))
        method = "capture order + post-release cross-correlation (no rig log)"
        notes = []
        if len(pairs) != len(encs) or len(pairs) != len([t for t in trials if gyro[t["path"]]]):
            notes.append("phone and encoder counts differ: without a rig log a missing trial "
                         "cannot be placed among repeats of one condition -- pass --rig-log")
    signs = {v[3] for v in pairs.values()}
    session = dict(method=method, notes=notes,
                   polarity=(signs.pop() if len(signs) == 1 else "mixed") if signs else "-")

    rows, step_rows, keep = [], [], {}
    for t in trials:
        r = {k: t[k] for k in ("capture_rank", "file", "time", "algorithm_version", "exported",
                               "scored", "capture_quality", "analysis_refusal", "excluded",
                               "research", "app_fused_f", "app_fused_n")}
        r["gyro_samples"] = 0 if gyro[t["path"]] is None else len(gyro[t["path"]][0])
        pr = pairs.get(t["path"])
        if pr is None:
            r["block"] = "unpaired"
            rows.append(r)
            continue
        ef, q, lag, sign = pr
        enc = bd.encoder(ef)
        n = bd.trial_num(ef)
        lr = log.get(n, {})
        blk = block or lr.get("_block") or infer_block(enc)
        r.update(enc_file=os.path.basename(ef), enc_trial=n, match_q=round(q, 3),
                 lag_s=round(lag, 3), polarity=sign, block=blk)
        for k in ("P", "I", "D", "lift", "release", "step_speed", "app_result", "notes"):
            if k in lr:
                r[k] = lr[k]
        pg = gyro[t["path"]]
        flags = []
        if q < 0.5:
            flags.append("low match_q")
        if blk == "ringdown":
            r.update(bd.score_ringdown(pg, enc, lag))
            if np.isfinite(r["df_pct"]) and abs(r["df_pct"]) > 10:
                flags.append("f differs >10% (likely mis-pair)")
        else:
            srows = bd.score_steps(pg, enc, lag, sign)
            for st in srows:
                step_rows.append(dict(capture_rank=t["capture_rank"], enc_file=r["enc_file"],
                                      step_speed=lr.get("step_speed", ""), **st))
            r["n_steps"] = len(srows)
            r["n_steps_rejected"] = int(np.sum(~np.isfinite(bd.col(srows, "phone_over"))))
            for a in ("phone_over", "enc_over", "phone_rise", "enc_rise",
                      "phone_settle", "enc_settle"):
                v = bd.col(srows, a)
                r[a] = float(np.nanmedian(v)) if np.isfinite(v).any() else np.nan
        r["flag"] = "; ".join(flags)
        keep[t["path"]] = (pg, enc, q, lag, sign, r)
        rows.append(r)

    bd.write_csv(os.path.join(out, "trials.csv"), rows)
    if step_rows:
        bd.write_csv(os.path.join(out, "steps.csv"), step_rows)
    figs = make_figures(rows, keep, out)
    md = summary_markdown(trials, encs, rows, step_rows, figs, phone, enc_dir, log_rows, session)
    with open(os.path.join(out, "summary.md"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write(md)
    print(md)
    return rows, step_rows


def _fmt(v, nd=2):
    return "-" if v is None or not np.isfinite(v) else f"{v:.{nd}f}"


def summary_markdown(trials, encs, rows, step_rows, figs, phone, enc_dir,
                     log_rows=(), session=None):
    L = ["# Bench session: phone app vs encoder", ""]
    L.append(f"- Phone export: `{phone}`  ({len(trials)} trials)")
    L.append(f"- Encoder: `{enc_dir}`  ({len(encs)} non-empty .bin files)")
    vers = Counter(t["algorithm_version"] or "(not in export)" for t in trials)
    L.append("- App ALGORITHM_VERSION seen: " + ", ".join(f"`{v}` x{n}" for v, n in vers.items()))
    schemas = {t["schema"] for t in trials if t["schema"]}
    if schemas:
        L.append("- Export schema: " + ", ".join(f"`{s}`" for s in sorted(schemas)))
    L += ["", "## App capture / export", "",
          "| exported | scored | not scored (refused) | excluded | research (raw) |",
          "|---|---|---|---|---|"]
    exp = sum(t["exported"] for t in trials)
    has_m = any(t["has_manifest"] for t in trials)
    scored = sum(bool(t["scored"]) for t in trials) if has_m else None
    refused = sum(t["scored"] is False and not t["research"] for t in trials) if has_m else None
    L.append(f"| {exp}/{len(trials)} | {scored if has_m else '-'} | {refused if has_m else '-'} | "
             f"{sum(t['excluded'] for t in trials)} | {sum(t['research'] for t in trials)} |")
    cq = Counter(t["capture_quality"] for t in trials if t["capture_quality"])
    ar = Counter(t["analysis_refusal"] for t in trials if t["analysis_refusal"])
    if cq:
        L.append("")
        L.append("capture_quality: " + ", ".join(f"{k} x{v}" for k, v in cq.items()))
    if ar:
        L.append("analysis_refusal: " + ", ".join(f"{k} x{v}" for k, v in ar.items()))
    L += ["", "Research-mode recordings are never scored, so they are not counted as refusals."]
    if log_rows:
        by_trial = {r.get("enc_trial"): r for r in rows if r.get("enc_trial") is not None}
        groups = {}
        for lr in log_rows:
            cond = (f"ring-down P={lr.get('P', '?')}" if lr["_block"] == "ringdown"
                    else f"step speed={lr.get('step_speed', '?')}" if lr["_block"] == "step"
                    else "(block not logged)")
            groups.setdefault(cond, []).append(lr)
        L += ["", "From the rig log (ERROR trials are not saved or exported, so the log is "
                  "their only record):", "",
              "| condition | logged | ERROR | NOT SCORABLE (log) | not scored (export) |",
              "|---|---|---|---|---|"]
        for cond, g in groups.items():
            exp_ns = sum(1 for lr in g if by_trial.get(lr["_trial"], {}).get("scored") is False
                         and not by_trial.get(lr["_trial"], {}).get("research"))
            L.append(f"| {cond} | {len(g)} | {sum(lr['_error'] for lr in g)} | "
                     f"{sum(lr['_unscorable'] for lr in g)} | {exp_ns} |")
    else:
        L += ["App faults (ERROR screen) are not saved or exported: pass --rig-log with an "
              "app_result column to count them."]

    paired = [r for r in rows if r.get("enc_file")]
    flagged = [r for r in paired if r.get("flag")]
    session = session or {}
    L += ["", "## Pairing", "",
          f"Method: {session.get('method', '-')}. Session polarity: {session.get('polarity', '-')}.",
          "",
          f"{len(paired)} of {len(trials)} phone trials paired to an encoder file; "
          f"{len(encs) - len(paired)} encoder files unpaired; {len(flagged)} flagged."]
    for n in session.get("notes", []):
        L += ["", f"**{n}**"]
    if flagged:
        L += ["", "| capture rank | encoder trial | match_q | flag |", "|---|---|---|---|"]
        L += [f"| {r['capture_rank']} | {r['enc_trial']} | {r['match_q']} | {r['flag']} |"
              for r in flagged]

    ring = [r for r in rows if r.get("block") == "ringdown"]
    if ring:
        L += ["", "## Ring-down: phone gyro vs encoder", "",
              "| P | n | f phone (Hz) | f enc (Hz) | median abs df % | N phone | N enc "
              "| tau phone (s) | tau enc (s) | median abs dtau (s) |",
              "|---|---|---|---|---|---|---|---|---|---|"]
        groups = {}
        for r in ring:
            groups.setdefault(pkey(r.get("P", "")) or "(no log)", []).append(r)

        def _key(k):
            return (_num(k) if np.isfinite(_num(k)) else 1e9, str(k))
        for p in sorted(groups, key=_key) + (["all"] if len(groups) > 1 else []):
            g = ring if p == "all" else groups[p]
            med = lambda k: float(np.nanmedian(bd.col(g, k))) if np.isfinite(bd.col(g, k)).any() else np.nan
            L.append(f"| {p} | {len(g)} | {_fmt(med('phone_f'))} | {_fmt(med('enc_f'))} | "
                     f"{_fmt(np.nanmedian(np.abs(bd.col(g, 'df_pct'))) if np.isfinite(bd.col(g, 'df_pct')).any() else np.nan, 1)} | "
                     f"{_fmt(med('phone_N'), 1)} | {_fmt(med('enc_N'), 1)} | "
                     f"{_fmt(med('phone_tau'))} | {_fmt(med('enc_tau'))} | "
                     f"{_fmt(bd.med_abs_diff(g, 'phone_tau', 'enc_tau'))} |")
        pf, ef = bd.paired(ring, "phone_f", "enc_f")
        if len(pf) >= 3 and np.ptp(ef) > 0:
            gn, b = np.polyfit(ef, pf, 1)
            r2 = np.corrcoef(ef, pf)[0, 1] ** 2
            L += ["", f"Frequency, phone vs encoder over {len(pf)} trials: gain {gn:.3f}, "
                      f"intercept {b:+.3f} Hz, R2 {r2:.4f}."]
    if step_rows:
        L += ["", "## Step response: phone gyro vs encoder (steps detected on encoder)", "",
              "| step speed | steps (scored / detected) | overshoot phone % | overshoot enc % "
              "| median abs d overshoot % "
              "| median abs d rise (s) | median abs d settle (s) |",
              "|---|---|---|---|---|---|---|"]
        groups = {}
        for s in step_rows:
            groups.setdefault(s.get("step_speed") or "(no log)", []).append(s)
        for sp, g0 in sorted(groups.items(), key=lambda kv: str(kv[0])):
            # only steps the phone side scored (sign and size matched the encoder), so
            # both medians describe the same steps
            g = [x for x in g0 if np.isfinite(x.get("phone_over", np.nan))]
            L.append(f"| {sp} | {len(g)} / {len(g0)} | {_fmt(np.nanmedian(bd.col(g, 'phone_over')), 1)} | "
                     f"{_fmt(np.nanmedian(bd.col(g, 'enc_over')), 1)} | "
                     f"{_fmt(bd.med_abs_diff(g, 'phone_over', 'enc_over'), 1)} | "
                     f"{_fmt(bd.med_abs_diff(g, 'phone_rise', 'enc_rise'), 3)} | "
                     f"{_fmt(bd.med_abs_diff(g, 'phone_settle', 'enc_settle'), 3)} |")
    if figs:
        L += ["", "## Figures", ""] + [f"- `{f}`" for f in figs]
    if step_rows:
        L += ["", "A detected step is not scored when the phone step differs in sign or by more "
                  "than 0.8-1.25x in size from the encoder step (misalignment, or a small "
                  "ring the encoder segmenter counted as a step)."]
    L += ["", "## Scope", "",
          "Horizontal rig: these are SENSOR-CHANNEL numbers (phone gyro vs encoder). "
          "The app's fused angle and PT score are not assessable on this geometry "
          "(gravity on the rotation axis) and are not an accuracy result. "
          "Amplitude/A0 belongs on the tilted rig.", ""]
    return "\n".join(L)


# ── figures ────────────────────────────────────────────────────────────────────
def _style(ax):
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    ax.spines["left"].set_color(MUTED)
    ax.spines["bottom"].set_color(MUTED)
    ax.tick_params(colors="#3A4650", labelsize=11)
    ax.grid(alpha=0.25, lw=0.8)


def make_figures(rows, keep, out):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams.update({"font.size": 12, "axes.titlesize": 14, "axes.labelsize": 12})
    figs = []
    ring = [r for r in rows if r.get("block") == "ringdown" and np.isfinite(_num(r.get("enc_f")))]

    if ring:   # 1. one representative ring-down overlay
        def _score(r):
            return ((abs(_num(r.get("P")) - 1.0) if np.isfinite(_num(r.get("P"))) else 0.0)
                    - r["match_q"] + (10.0 if r.get("flag") else 0.0))
        best = min(ring, key=_score)
        path = next(p for p, v in keep.items() if v[5] is best)
        pg, enc, q, lag, sign, r = keep[path]
        te, pe, _ = enc
        tp, ap = pg[0] - lag, sign * pg[1]
        # offset-match the phone over the encoder window (gyro integration has no absolute zero)
        m = (tp >= te[0]) & (tp <= te[-1])
        if m.any():
            ap = ap - np.median(ap[m]) + np.median(np.interp(tp[m], te, pe))
        fig, ax = plt.subplots(figsize=(10, 4.6))
        ax.plot(te, pe, color=INK, lw=2.0, label="Encoder (1 kHz, truth)")
        ax.plot(tp[m], ap[m], color=TEAL, lw=1.6, ls="--", label="Phone gyro (integrated)")
        ax.set_xlabel("Time (s)")
        ax.set_ylabel("Arm angle (deg)")
        ptxt = f"P = {r['P']}, " if r.get("P") else ""
        ax.set_title(f"Ring-down, {ptxt}{r['enc_file']}", loc="left")
        ax.text(0.99, 0.04,
                f"f  {r['phone_f']:.2f} / {r['enc_f']:.2f} Hz\n"
                f"N  {_fmt(r['phone_N'], 1)} / {_fmt(r['enc_N'], 1)}\n"
                f"tau  {_fmt(r['phone_tau'])} / {_fmt(r['enc_tau'])} s\n(phone / encoder)",
                transform=ax.transAxes, ha="right", va="bottom", fontsize=11, color="#3A4650",
                bbox=dict(boxstyle="round,pad=0.4", fc="white", ec=MUTED, alpha=0.9))
        ax.legend(frameon=False, loc="upper right")
        _style(ax)
        fig.tight_layout()
        figs.append(_save(fig, out, "fig_ringdown_overlay.png", plt))

        # 2. f phone vs encoder
        fig, ax = plt.subplots(figsize=(6.2, 5.6))
        pf, ef = bd.col(ring, "phone_f"), bd.col(ring, "enc_f")
        P = bd.col(ring, "P") if any("P" in r for r in ring) else np.full(len(ring), np.nan)
        ok = np.isfinite(pf) & np.isfinite(ef)
        lo, hi = 0.0, float(np.nanmax(np.r_[pf[ok], ef[ok]])) * 1.15
        ax.plot([lo, hi], [lo, hi], color=MUTED, lw=1.2, ls=":", label="identity")
        if np.isfinite(P[ok]).any():
            sc = ax.scatter(ef[ok], pf[ok], c=P[ok], cmap="viridis", s=70,
                            edgecolor="white", linewidth=1.5, zorder=3)
            fig.colorbar(sc, ax=ax, label="P term")
        else:
            ax.scatter(ef[ok], pf[ok], color=TEAL, s=70, edgecolor="white", linewidth=1.5, zorder=3)
        if ok.sum() >= 3 and np.ptp(ef[ok]) > 0:
            gn, b = np.polyfit(ef[ok], pf[ok], 1)
            r2 = np.corrcoef(ef[ok], pf[ok])[0, 1] ** 2
            ax.text(0.04, 0.96, f"gain {gn:.3f}   R$^2$ {r2:.4f}\n"
                                f"median |df| {np.nanmedian(np.abs(bd.col(ring, 'df_pct'))):.1f} %   n = {ok.sum()}",
                    transform=ax.transAxes, va="top", fontsize=11, color="#3A4650")
        ax.set_xlim(lo, hi)
        ax.set_ylim(lo, hi)
        ax.set_aspect("equal")
        ax.set_xlabel("Encoder frequency (Hz)")
        ax.set_ylabel("Phone gyro frequency (Hz)")
        ax.set_title("Ring-down frequency", loc="left")
        _style(ax)
        fig.tight_layout()
        figs.append(_save(fig, out, "fig_f_scatter.png", plt))

        # 3. decay tau and swing count N
        fig, axes = plt.subplots(1, 2, figsize=(10, 4.8))
        for ax, (a, b, lab) in zip(axes, [("phone_tau", "enc_tau", "Decay time constant tau (s)"),
                                         ("phone_N", "enc_N", "Swing count N")]):
            A, B = bd.col(ring, a), bd.col(ring, b)
            k = np.isfinite(A) & np.isfinite(B)
            if k.any():
                top = float(np.max(np.r_[A[k], B[k]])) * 1.15
                ax.plot([0, top], [0, top], color=MUTED, lw=1.2, ls=":")
                ax.scatter(B[k], A[k], color=TEAL, s=70, edgecolor="white", linewidth=1.5, zorder=3)
                ax.set_xlim(0, top)
                ax.set_ylim(0, top)
                ax.text(0.04, 0.96, f"median |diff| {np.median(np.abs(A[k] - B[k])):.2f}   n = {k.sum()}",
                        transform=ax.transAxes, va="top", fontsize=11, color="#3A4650")
            ax.set_aspect("equal")
            ax.set_xlabel("Encoder")
            ax.set_ylabel("Phone gyro")
            ax.set_title(lab, loc="left")
            _style(ax)
        fig.tight_layout()
        figs.append(_save(fig, out, "fig_decay_scatter.png", plt))

    steps = [(p, v) for p, v in keep.items() if v[5].get("block") == "step" and v[5].get("n_steps")]
    if steps:   # 4. one step staircase overlay
        p, (pg, enc, q, lag, sign, r) = max(steps, key=lambda kv: kv[1][2])
        te, pe, _ = enc
        tp, ap = pg[0] - lag, sign * pg[1]
        m = (tp >= te[0]) & (tp <= te[-1])
        if m.any():
            ap = ap - np.median(ap[m][:50]) + np.median(pe[:100])
        fig, ax = plt.subplots(figsize=(10, 4.6))
        ax.plot(te, pe, color=INK, lw=2.0, label="Encoder (1 kHz, truth)")
        ax.plot(tp[m], ap[m], color=TEAL, lw=1.6, ls="--", label="Phone gyro (integrated)")
        sp = f"step speed {r['step_speed']}, " if r.get("step_speed") else ""
        ax.set_title(f"Step staircase, {sp}{r['enc_file']}", loc="left")
        ax.set_xlabel("Time (s)")
        ax.set_ylabel("Arm angle (deg)")
        ax.legend(frameon=False, loc="upper right")
        _style(ax)
        fig.tight_layout()
        figs.append(_save(fig, out, "fig_step_overlay.png", plt))
    return figs


def _save(fig, out, name, plt):
    fig.savefig(os.path.join(out, name), dpi=200, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return name


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--phone", required=True, help="phone export dir, *-manifest.json, or one .jsonl")
    ap.add_argument("--encoder", required=True, help="folder of encoder .bin files")
    ap.add_argument("--out", required=True, help="output folder")
    ap.add_argument("--rig-log", help="CSV: encoder_trial,block,P,I,D,lift,release,step_speed,notes")
    ap.add_argument("--glob", default="*.bin", help="encoder filename glob (default *.bin)")
    ap.add_argument("--min-num", type=int, help="keep encoder files with trial number >= this")
    ap.add_argument("--max-num", type=int, help="keep encoder files with trial number <= this")
    ap.add_argument("--since", help="keep phone trials captured on/after this ISO date/time")
    ap.add_argument("--window", type=int, default=3, help="+/- capture-order pairing window")
    ap.add_argument("--block", help="force every trial to one block (R/ringdown or S/step)")
    ap.add_argument("--polarity", choices=["auto", "1", "-1"], default="auto",
                    help="phone-vs-encoder sign for the whole session (default: majority vote)")
    a = ap.parse_args(argv)
    analyze(a.phone, a.encoder, a.out, a.glob, a.min_num, a.max_num, a.rig_log,
            a.window, a.since, a.block, None if a.polarity == "auto" else int(a.polarity))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
