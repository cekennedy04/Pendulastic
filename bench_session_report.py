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

Pairing: phone trials are ordered by manifest capture timestamp (export trial numbers
can be scrambled), encoder files by their Trial<NN> number, then matched one-to-one
within +/- --window by angle cross-correlation. Check `match_q` (>=0.5) and `enc_file`.

Block (ringdown vs step) comes from the rig log when given, else is inferred from the
encoder trace (a ring-down crosses zero many times; the staircase only twice).

Rig log (optional CSV, one row per encoder file; extra columns are carried through):
  encoder_trial,block,P,I,D,lift,release,step_speed,notes
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
    for m in manifests:
        with open(m, encoding="utf-8") as fh:
            man = json.load(fh)
        base = os.path.dirname(m)
        for t in man.get("trials", []):
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
def load_rig_log(path):
    """{encoder trial number: row dict}. `encoder_trial` may be a number or a filename."""
    if not path:
        return {}
    out = {}
    with open(path, newline="", encoding="utf-8-sig") as fh:
        for row in csv.DictReader(fh):
            key = (row.get("encoder_trial") or row.get("encoder_file") or "").strip()
            if not key:
                continue
            n = int(key) if key.isdigit() else bd.trial_num(key)
            out[n] = {k: (v.strip() if isinstance(v, str) else v) for k, v in row.items()}
    return out


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
            rig_log=None, window=3, since=None, block=None):
    os.makedirs(out, exist_ok=True)
    trials = load_phone(phone, since)
    if not trials:
        raise SystemExit(f"no phone trials found in {phone}")
    encs = bd.list_encoders(enc_dir, glob_pat, min_num, max_num)
    if not encs:
        raise SystemExit(f"no non-empty encoder files matching {glob_pat!r} in {enc_dir}")
    log = load_rig_log(rig_log)

    gyro = {}
    for t in trials:
        gyro[t["path"]] = bd.phone_gyro(t["path"]) if t["exported"] else None
    pairs = bd.pair_by_order([(t["path"], gyro[t["path"]]) for t in trials], encs, window)

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
        blk = block or (lr.get("block") or "").lower() or infer_block(enc)
        if blk.startswith("ring"):
            blk = "ringdown"
        r.update(enc_file=os.path.basename(ef), enc_trial=n, match_q=round(q, 3),
                 lag_s=round(lag, 3), polarity=sign, block=blk)
        for k in ("P", "I", "D", "lift", "release", "step_speed", "notes"):
            if k in lr:
                r[k] = lr[k]
        pg = gyro[t["path"]]
        if blk == "ringdown":
            r.update(bd.score_ringdown(pg, enc))
        else:
            srows = bd.score_steps(pg, enc, lag)
            for s in srows:
                step_rows.append(dict(capture_rank=t["capture_rank"], enc_file=r["enc_file"],
                                      step_speed=lr.get("step_speed", ""), **s))
            r["n_steps"] = len(srows)
            for a in ("phone_over", "enc_over", "phone_rise", "enc_rise",
                      "phone_settle", "enc_settle"):
                v = bd.col(srows, a)
                r[a] = float(np.nanmedian(v)) if np.isfinite(v).any() else np.nan
        keep[t["path"]] = (pg, enc, q, lag, sign, r)
        rows.append(r)

    bd.write_csv(os.path.join(out, "trials.csv"), rows)
    if step_rows:
        bd.write_csv(os.path.join(out, "steps.csv"), step_rows)
    figs = make_figures(rows, keep, out)
    md = summary_markdown(trials, encs, rows, step_rows, figs, phone, enc_dir)
    with open(os.path.join(out, "summary.md"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write(md)
    print(md)
    return rows, step_rows


def _fmt(v, nd=2):
    return "-" if v is None or not np.isfinite(v) else f"{v:.{nd}f}"


def summary_markdown(trials, encs, rows, step_rows, figs, phone, enc_dir):
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
    L += ["", "App faults (ERROR screen) are not saved or exported: count them from the run log.",
          "Research-mode recordings are never scored, so they are not counted as refusals."]

    paired = [r for r in rows if r.get("enc_file")]
    lowq = [r for r in paired if r["match_q"] < 0.5]
    L += ["", "## Pairing", "",
          f"{len(paired)} of {len(trials)} phone trials paired to an encoder file; "
          f"{len(encs) - len(paired)} encoder files unpaired; "
          f"{len(lowq)} pairs with match_q < 0.5"
          + (" (check: " + ", ".join(f"rank {r['capture_rank']}" for r in lowq) + ")" if lowq else "")
          + "."]

    ring = [r for r in rows if r.get("block") == "ringdown"]
    if ring:
        L += ["", "## Ring-down: phone gyro vs encoder", "",
              "| P | n | f phone (Hz) | f enc (Hz) | median abs df % | N phone | N enc "
              "| tau phone (s) | tau enc (s) | median abs dtau (s) |",
              "|---|---|---|---|---|---|---|---|---|---|"]
        groups = {}
        for r in ring:
            groups.setdefault(r.get("P", "") or "(no log)", []).append(r)

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
              "| step speed | steps | overshoot phone % | overshoot enc % | median abs d overshoot % "
              "| median abs d rise (s) | median abs d settle (s) |",
              "|---|---|---|---|---|---|---|"]
        groups = {}
        for s in step_rows:
            groups.setdefault(s.get("step_speed") or "(no log)", []).append(s)
        for sp, g in sorted(groups.items(), key=lambda kv: str(kv[0])):
            L.append(f"| {sp} | {len(g)} | {_fmt(np.nanmedian(bd.col(g, 'phone_over')), 1)} | "
                     f"{_fmt(np.nanmedian(bd.col(g, 'enc_over')), 1)} | "
                     f"{_fmt(bd.med_abs_diff(g, 'phone_over', 'enc_over'), 1)} | "
                     f"{_fmt(bd.med_abs_diff(g, 'phone_rise', 'enc_rise'), 3)} | "
                     f"{_fmt(bd.med_abs_diff(g, 'phone_settle', 'enc_settle'), 3)} |")
    if figs:
        L += ["", "## Figures", ""] + [f"- `{f}`" for f in figs]
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
            return (abs(_num(r.get("P")) - 1.0) if np.isfinite(_num(r.get("P"))) else 0.0) - r["match_q"]
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
    ap.add_argument("--block", choices=["ringdown", "step"], help="force every trial to one block")
    a = ap.parse_args(argv)
    analyze(a.phone, a.encoder, a.out, a.glob, a.min_num, a.max_num, a.rig_log,
            a.window, a.since, a.block)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
