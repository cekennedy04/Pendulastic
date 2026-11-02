"""Pair the Pendulum Designer's trial log with the phone export (and, optionally, the rig's encoder logs).

    python -m neuromech.match_trials --kit <folder with trials.csv> --phone-manifest <export>-manifest.json
                                     [--encoder-dir <folder of .bin>] [--out matched.csv]

Each trial is paired with one phone recording:
  * phone_trial filled in at the bench (the N of the phone file's "-trialN.jsonl") -> that file, always;
  * otherwise by capture order: the remaining recordings, sorted by the phone's own timestamp, go to the
    remaining trials in trial order. Phone file NUMBERS are not capture order (bench note 2026-09-14), so
    order comes from the manifest timestamp, never from the number.
The phone's clock is not the rig PC's, so every pairing is checked against ONE constant offset (the median
of phone time - PC start): a pair more than --tolerance-s off that offset is flagged, which is what an
extra or missing recording looks like.

With --encoder-dir, each trial's encoder_file is located in its log (the same search as session check-log)
and scored from the encoder position: what the rig actually delivered, beside what was designed and what
the phone measured. Writes <kit>/trials_matched.csv and prints the comparison.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

# designer column -> phone manifest params key
PHONE_KEYS = {"R2n": "r2n", "N": "n", "f": "f", "area_ratio": "area_ratio", "phi_max_ratio": "phi_max_ratio",
              "omega_max_n": "omega_max_n", "A0_deg": "a0_deg"}
FS = 1000.0


def _trial_no(fname: str):
    m = re.search(r"-trial(\d+)\.jsonl$", fname)
    return int(m.group(1)) if m else None


def _pc(s) -> float:
    return datetime.strptime(str(s), "%Y-%m-%d %H:%M:%S").timestamp()


def _in_order(pc: list[float], ph: list[float], tol: float = 20.0):
    """Which recording goes with each trial, and whether that is certain.

    An increasing assignment (capture order is kept on both sides) of min(n, m) pairs whose clock offsets
    agree best with one constant: extra recordings (false starts) or extra trials (a recording that never
    happened) are skipped wherever that fits the clock best. Every pair's offset is tried as the constant.
    Returns (assignment: list of recording index or None per trial, ambiguous: bool). Ambiguous means a
    DIFFERENT assignment fits the clock about as well -- e.g. evenly spaced trials with one recording
    missing, where the clock cannot say which one."""
    n, m = len(pc), len(ph)
    if min(n, m) == 0:
        return [None] * n, False
    if n == m:
        return list(range(n)), False
    swap = m < n                               # always skip on the longer side
    A, B = (ph, pc) if swap else (pc, ph)      # pair every A with one B, B increasing
    na, nb = len(A), len(B)
    sols = {}
    for c in {round(y - x, 3) for x in A for y in B} if not swap else {round(x - y, 3) for x in A for y in B}:
        INF = float("inf")
        D = [[INF] * (nb + 1) for _ in range(na + 1)]
        for j in range(nb + 1):
            D[0][j] = 0.0
        for i in range(1, na + 1):
            for j in range(i, nb + 1):
                d = (A[i - 1] - B[j - 1] - c) if swap else (B[j - 1] - A[i - 1] - c)
                D[i][j] = min(D[i][j - 1], D[i - 1][j - 1] + abs(d))
        i, j, a = na, nb, [None] * na
        while i > 0:
            if j > i and D[i][j] == D[i][j - 1]:
                j -= 1
            else:
                a[i - 1] = j - 1; i -= 1; j -= 1
        key = tuple(a)
        sols[key] = min(sols.get(key, INF), D[na][nb])
    ranked = sorted(sols.items(), key=lambda kv: kv[1])
    best = list(ranked[0][0])
    ambiguous = len(ranked) > 1 and ranked[1][1] - ranked[0][1] < tol
    if not swap:
        return best, ambiguous
    out = [None] * n                           # invert: recording r -> trial best[r]
    for r, t in enumerate(best):
        out[t] = r
    return out, ambiguous


def pair(trials: pd.DataFrame, phone: list[dict], tolerance_s: float = 20.0) -> pd.DataFrame:
    """trials: the designer's trials.csv. phone: the manifest's trials list. Returns trials + phone columns."""
    out = trials.copy()
    out["phone_file"] = ""; out["phone_time"] = ""; out["pairing"] = ""; out["clock_check"] = ""
    by_no = {_trial_no(p["file"]): p for p in phone}
    used = set()
    for i, r in out.iterrows():
        pt = str(r.get("phone_trial", "") or "").strip()
        if pt and pt.lower() != "nan":
            p = by_no.get(int(float(pt)))
            if p is None:
                out.at[i, "pairing"] = f"phone_trial {pt} is not in this export"; continue
            out.at[i, "phone_file"] = p["file"]; out.at[i, "pairing"] = "phone_trial"; used.add(p["file"])
    rest = sorted((p for p in phone if p["file"] not in used and not p.get("excluded_at")), key=lambda p: p["timestamp"])
    todo = [i for i in out.index if not out.at[i, "phone_file"] and not out.at[i, "pairing"]]
    pcs = [_pc(out.at[i, "pc_start"]) for i in todo]
    assign, ambiguous = _in_order(pcs, [p["timestamp"] / 1000.0 for p in rest], tolerance_s)
    for i, j in zip(todo, assign):
        if j is not None:
            out.at[i, "phone_file"] = rest[j]["file"]
            out.at[i, "pairing"] = "capture order" + ("  AMBIGUOUS: fill phone_trial" if ambiguous else "")
        else:
            out.at[i, "pairing"] = "no phone recording fits"
    skipped = len(rest) - sum(1 for j in assign if j is not None)
    if skipped > 0:
        print(f"NOTE: {skipped} phone recording(s) left unpaired (false starts?): they did not fit the session's clock")
    if ambiguous:
        print("WARNING: more than one pairing fits the clock equally well; type phone_trial for the trials marked AMBIGUOUS")
    # one clock offset for the whole session; a pair far from it is a mis-pair
    ts = {p["file"]: p["timestamp"] / 1000.0 for p in phone}
    offs = {}
    for i, r in out.iterrows():
        if not r["phone_file"]:
            continue
        pc = datetime.strptime(str(r["pc_start"]), "%Y-%m-%d %H:%M:%S").timestamp()
        offs[i] = ts[r["phone_file"]] - pc
        out.at[i, "phone_time"] = datetime.fromtimestamp(ts[r["phone_file"]]).strftime("%Y-%m-%d %H:%M:%S")
    if offs:
        med = float(np.median(list(offs.values())))
        for i, o in offs.items():
            d = o - med
            out.at[i, "clock_check"] = f"{d:+.0f} s" + ("  CHECK: far from the session's clock offset" if abs(d) > tolerance_s else "")
        print(f"phone clock - PC clock = {med:+.0f} s (median over {len(offs)} pairs)")
    pmap = {p["file"]: p.get("params") or {} for p in phone}
    for k, pk in PHONE_KEYS.items():
        out[f"phone_{k}"] = [pmap.get(f, {}).get(pk, np.nan) if f else np.nan for f in out["phone_file"]]
    return out


def delivered(kit: str, rig_file: str, bin_path: str) -> dict:
    """Score what the encoder says the arm did, for a designer trial file."""
    from labview_bin_reader import read_labview_bin
    from neuromech import ilc
    from neuromech.build import score_angle
    sp = np.loadtxt(os.path.join(kit, rig_file), delimiter=",")
    e = read_labview_bin(bin_path)
    log_sp = np.asarray(e["position_setpoint"], float)
    k0 = ilc.locate(sp[:, 1], np.concatenate([log_sp, np.full(len(sp), log_sp[-1])]), FS)
    pos = np.asarray(e["position"], float)[k0:k0 + len(sp)]
    n = len(pos); t = sp[:n, 0]
    rel = int(round(PREAMBLE_S * FS)) if len(sp) > PREAMBLE_S * FS + 200 else 0
    a0 = sp[rel, 1]
    knee = 180.0 - (a0 - pos)
    hold = np.full(int(3 * 100), 180.0)
    tg = np.arange(rel / FS, t[-1], 0.01)
    kg = np.interp(tg, t, knee)
    tt = np.arange(len(hold) + len(kg)) / 100.0
    prm, est = score_angle(tt, np.concatenate([hold, kg]), 3.0)
    d = {f"enc_{k}": (prm or {}).get(k, np.nan) for k in PHONE_KEYS}
    d["enc_pt7"] = est.get("pt7"); d["enc_zone"] = est.get("zone")
    return d


# motor_reference.preamble length: LEAD + 3 x (pulse + gap) + POST_PULSE + RAMP + PRE_ROLL
PREAMBLE_S = 0.2 + 3 * (0.08 + 0.25) + 1.0 + 4.0 + 3.0


def main():
    a = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    a.add_argument("--kit", required=True); a.add_argument("--phone-manifest", required=True)
    a.add_argument("--encoder-dir"); a.add_argument("--out"); a.add_argument("--tolerance-s", type=float, default=20.0)
    x = a.parse_args()
    trials = pd.read_csv(os.path.join(x.kit, "trials.csv"), dtype=str, keep_default_na=False)
    phone = json.load(open(x.phone_manifest, encoding="utf-8"))["trials"]
    out = pair(trials, phone, x.tolerance_s)
    if x.encoder_dir:
        rows = []
        for _i, r in out.iterrows():
            f = str(r.get("encoder_file", "")).strip()
            p = os.path.join(x.encoder_dir, f) if f else ""
            rows.append(delivered(x.kit, r["rig_file"], p) if p and os.path.exists(p) else {})
        out = pd.concat([out.reset_index(drop=True), pd.DataFrame(rows)], axis=1)
    dst = x.out or os.path.join(x.kit, "trials_matched.csv")
    out.to_csv(dst, index=False)
    show = ["trial", "label", "phone_file", "pairing", "clock_check", "pred_pt7"] + \
           [c for c in ("enc_pt7",) if c in out] + ["pred_f", "phone_f", "pred_N", "phone_N"]
    with pd.option_context("display.width", 200, "display.max_columns", 30):
        print(out[[c for c in show if c in out]].to_string(index=False))
    print(f"-> {dst}")


if __name__ == "__main__":
    main()
