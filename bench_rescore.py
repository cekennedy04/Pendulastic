"""Re-score a phone trial and its encoder ring through the SAME scorer.

The bench question is not "what did the app say" -- it is "how far is the app
from truth, when both are measured the same way." So this feeds BOTH signals
through `pendulastic_pt_score.compute_pt_params`, with the release index handed
in explicitly on each side, and never lets the app's own release detector
(`_detect_release`, which fires on the pre-release lift and returned index 0 on
all 35 pilot trials) into the path.

Truth  = encoder `position`, 1 kHz, release = the setpoint ramp time from
         setpoint_schedule.csv.
Phone  = the raw IMU stream replayed through the production AHRS pipeline
         (imu_calibration_tuner.replay_trial), release = the same physical
         instant located on the phone's own signal.

One trapezoid encoder file holds four ring-downs (segments 2/4/6/8); a phone
recording is matched to ONE of them, so the caller supplies (encoder_csv,
segment).  Nothing here guesses the pairing -- see bench_signature_match() for
the pilot-data heuristic, but a logged (file, segment) always wins.
"""
from __future__ import annotations

import json
import os
import numpy as np
import pandas as pd

import pendulastic_pt_score as pt
import imu_calibration_tuner as tuner

#: The seven Popovic metrics, in the order the app and the scorer both use.
PT7 = ["R2n", "N", "phi_max_ratio", "omega_max_n", "omega_min_n", "f", "area_ratio"]
#: Extra diagnostics worth carrying into the comparison sheet.
DIAG = ["A0_deg", "A1_deg", "omega_peak_deg_s", "neutral_deg", "spasticity_type"]

#: Production replay settings -- the config the shipping app runs, so the
#: re-score measures what ships rather than a bench-tuned variant. Mirrors
#: motor_bench._replay_config()'s intent.
REPLAY = {"beta": 0.10, "ema_alpha": 0.30, "flex_axis_capture": True, "gravity_seed": True}


# ──────────────────────────────────────────────────────────────────────────────
# encoder side
# ──────────────────────────────────────────────────────────────────────────────
def load_schedule(decoded_dir):
    """The per-trial segment table LabVIEW's decoder writes alongside the CSVs."""
    return pd.read_csv(os.path.join(decoded_dir, "setpoint_schedule.csv"))


def encoder_ring(decoded_dir, stem, segment, settle_s=10.0):
    """Truth for one ring: encoder position from the ramp into the next hold.

    Returns (t, position_deg, release_idx) with t starting at 0 and release_idx
    the first sample -- the ramp end is the release, exactly known, no detection.
    """
    sched = load_schedule(decoded_dir)
    seg = sched[(sched["trial"] == stem) & (sched["segment"] == segment)]
    if seg.empty:
        raise ValueError(f"no segment {segment} for {stem}")
    row = seg.iloc[0]
    if row["kind"] != "ramp":
        raise ValueError(f"segment {segment} of {stem} is {row['kind']}, not a ramp")
    d = pd.read_csv(os.path.join(decoded_dir, stem + ".csv"))
    t = d["time"].to_numpy()
    t0, t1 = float(row["t_end_s"]), float(row["t_end_s"]) + settle_s
    m = (t >= t0) & (t < min(t1, t[-1]))
    return t[m] - t[m][0], d["position"].to_numpy()[m], 0


# ──────────────────────────────────────────────────────────────────────────────
# phone side
# ──────────────────────────────────────────────────────────────────────────────
def load_jsonl(path):
    return [json.loads(l) for l in open(path, encoding="utf-8") if l.strip()]


def capture_order(session_dir):
    """Return the session's trials in TRUE capture order, from the manifest
    timestamps -- NOT the phone's trial numbers, which are scrambled relative
    to the order trials were actually run (see the app numbering bug). Each row
    is (capture_rank, phone_trial_number, jsonl_filename, timestamp). Fill the
    tracker's condition rows against capture_rank, not the number in the name.
    """
    man = json.load(open(glob_one(session_dir, "*manifest*.json"), encoding="utf-8"))
    def trial_num(t):
        return int(t["file"].rsplit("trial", 1)[1].split(".")[0].split(" ")[0])
    ordered = sorted(man["trials"], key=lambda t: t["timestamp"])
    return [(rank, trial_num(t), t["file"], t["timestamp"]) for rank, t in enumerate(ordered, 1)]


def glob_one(d, pat):
    import glob as _g
    hits = _g.glob(os.path.join(d, pat))
    if not hits:
        raise FileNotFoundError(f"no {pat} in {d}")
    return hits[0]


def phone_release_idx(t, ang, rate_thresh=1.0, sustain_s=0.1, skip_s=0.3):
    """First sustained departure from rest, on the replayed angle series.

    A bare threshold latches onto any twitch (this is the sustain requirement
    the pilot showed was needed: an unsustained detector locked onto a 5 deg
    sync pulse 9 s early). `rate_thresh` is deg per tick-second; `ang` may carry
    a leading NaN (replay_trial's documented tick-0 contract), handled here.
    """
    fin = np.isfinite(ang)
    if fin.sum() < 5:
        return None
    dt = np.median(np.diff(t[fin]))
    rate = np.abs(np.gradient(np.where(fin, ang, np.nan)))
    for i in range(len(t)):
        if t[i] - t[0] < skip_s or not np.isfinite(rate[i]):
            continue
        j = int(np.searchsorted(t, t[i] + sustain_s))
        window = rate[i:j]
        if len(window) and np.all(np.isfinite(window)) and np.all(window > rate_thresh * dt):
            return i
    return None


def phone_curve(jsonl_path):
    """Replay the raw stream through the production pipeline -> (t, angle_deg)."""
    samples = load_jsonl(jsonl_path)
    t, ang = tuner.replay_trial(samples, REPLAY, tick_s=tuner.ANALYSIS_TICK_S)
    return np.asarray(t, float), np.asarray(ang, float)


# ──────────────────────────────────────────────────────────────────────────────
# the comparison
# ──────────────────────────────────────────────────────────────────────────────
def _score(t, ang, release_idx, detrend):
    p = pt.compute_pt_params(np.asarray(t, float), np.asarray(ang, float),
                             release_idx=release_idx, detrend=detrend)
    return p


def rescore_pair(jsonl_path, decoded_dir, encoder_stem, segment,
                 app_params=None, detrend=True):
    """Truth vs phone for one matched (phone trial, encoder ring).

    Returns a flat dict: encoder_<k>, phone_<k>, err_<k> for every PT7 metric,
    plus the diagnostics and the release indices actually used. `app_params`
    (the manifest's params block) is carried through as app_<k> so the sheet can
    show all three columns: what the app claimed live, what a correct re-score
    gives, and truth.
    """
    et, eang, erel = encoder_ring(decoded_dir, encoder_stem, segment)
    enc = _score(et, eang, erel, detrend)

    pt_, pang = phone_curve(jsonl_path)
    prel = phone_release_idx(pt_, pang)
    ph = _score(pt_, pang, prel, detrend) if prel is not None else None

    out = {
        "phone_file": os.path.basename(jsonl_path),
        "encoder_file": encoder_stem, "segment": segment,
        "encoder_release_idx": erel, "phone_release_idx": prel,
        "phone_refused_here": ph is None,
    }
    for k in PT7 + DIAG:
        ev = enc.get(k) if enc else None
        pv = ph.get(k) if ph else None
        out[f"encoder_{k}"] = ev
        out[f"phone_{k}"] = pv
        if isinstance(ev, (int, float)) and isinstance(pv, (int, float)):
            out[f"err_{k}"] = pv - ev
        if app_params is not None:
            av = app_params.get({"R2n": "r2n", "N": "n", "A0_deg": "a0_deg",
                                  "A1_deg": "a1_deg"}.get(k, k))
            out[f"app_{k}"] = av
    return out


# ──────────────────────────────────────────────────────────────────────────────
# pilot-data pairing: signature match on frequency (the metric that survives)
# ──────────────────────────────────────────────────────────────────────────────
def _gyro_signature(jsonl_path):
    """Rough (A0, f) from raw gyro-y -- for MATCHING only, never for scoring."""
    recs = load_jsonl(jsonl_path)
    g = [(r["t"], r["v"][1]) for r in recs if r["sensor"] == "gyro"]
    if len(g) < 20:
        return None, None
    t = np.array([a for a, _ in g]); gy = np.array([b for _, b in g]) * 180 / np.pi
    t = t - t[0]; gy = gy - gy[t < 0.5].mean()
    ang = np.concatenate([[0], np.cumsum(0.5 * (gy[1:] + gy[:-1]) * np.diff(t))])
    rel = None
    for i in range(len(t)):
        if t[i] < 0.3:
            continue
        j = int(np.searchsorted(t, t[i] + 0.1))
        if j < len(t) and np.all(np.abs(gy[i:j]) > 30):
            rel = i; break
    if rel is None:
        return None, None
    a = ang - ang[t > t[-1] - 1.5].mean(); seg = a[rel:]; ts = t[rel:]
    A0 = float(abs(seg[np.argmax(np.abs(seg[:int(0.7 * len(seg))]))])) if len(seg) > 10 else None
    nz = np.abs(seg) > 1.0; cr = int(np.sum(np.diff(np.sign(seg[nz])) != 0))
    big = np.where(np.abs(seg) > 1.5)[0]
    f = cr / (2 * (ts[big[-1]] - ts[big[0]])) if len(big) > 2 and cr >= 2 else None
    return A0, f


def fill_sheet(xlsx_path, jsonl_dir, decoded_dir, detrend=True):
    """Read the Trials sheet, and for every row that names a Phone JSONL file
    AND an Encoder file + segment, re-score the pair and write the six ENCODER
    TRUTH columns back. Matches by the 'Phone JSONL file' cell; a filename that
    is not found under jsonl_dir is skipped with a note. Formulas and formatting
    are preserved (openpyxl only touches the six target cells)."""
    import glob
    import openpyxl
    wb = openpyxl.load_workbook(xlsx_path)
    ws = wb["Trials"]
    hdr = {ws.cell(row=2, column=i).value: i for i in range(1, ws.max_column + 1)}
    disk = {os.path.basename(p): p for p in glob.glob(os.path.join(jsonl_dir, "**", "*.jsonl"), recursive=True)}
    truth_cols = {"enc A0": "encoder_A0_deg", "enc N": "encoder_N", "enc f": "encoder_f",
                  "enc R2n": "encoder_R2n", "enc wmax": "encoder_omega_max_n", "enc area": "encoder_area_ratio"}
    done = skip = 0
    for r in range(4, ws.max_row + 1):
        jf = ws.cell(row=r, column=hdr["Phone JSONL file"]).value
        ef = ws.cell(row=r, column=hdr["Encoder file"]).value
        sg = ws.cell(row=r, column=hdr["Encoder seg"]).value
        if not (jf and ef and sg):
            continue
        path = disk.get(str(jf).strip())
        if not path:
            print(f"  row {r}: JSONL '{jf}' not found under {jsonl_dir} — skipped"); skip += 1; continue
        try:
            res = rescore_pair(path, decoded_dir, str(ef).strip(), int(sg), detrend=detrend)
        except Exception as e:
            print(f"  row {r}: {jf} -> {e}"); skip += 1; continue
        for cellhdr, reskey in truth_cols.items():
            v = res.get(reskey)
            if isinstance(v, (int, float)):
                ws.cell(row=r, column=hdr[cellhdr], value=round(float(v), 3))
        done += 1
        print(f"  row {r}: {jf} vs {ef} seg{sg} -> enc f={res.get('encoder_f')}")
    wb.save(xlsx_path)
    print(f"filled {done} row(s), skipped {skip}; saved {xlsx_path}")


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Re-score phone/encoder pairs.")
    ap.add_argument("--capture-order", metavar="SESSION_DIR",
                    help="print the session's JSONL files in true capture order (by manifest timestamp) — the phone's trial numbers are scrambled, so align the tracker to this rank")
    ap.add_argument("--sheet", help="Bench tracker .xlsx: fill ENCODER TRUTH from its Trials rows")
    ap.add_argument("--jsonl-dir", default="Pendulastic_Mobile_App_Trial", help="root to find phone JSONL files")
    ap.add_argument("--decoded", default="ck_app_pt_test/decoded", help="encoder decoded CSV dir")
    ap.add_argument("jsonl", nargs="?"); ap.add_argument("encoder_stem", nargs="?"); ap.add_argument("segment", nargs="?", type=int)
    a = ap.parse_args()
    if a.capture_order:
        print(f"{'rank':>4}  {'phone#':>6}  {'timestamp':>13}  file")
        for rank, num, fn, ts in capture_order(a.capture_order):
            print(f"{rank:>4}  {num:>6}  {ts:>13}  {fn}")
    elif a.sheet:
        fill_sheet(a.sheet, a.jsonl_dir, a.decoded)
    elif a.jsonl and a.encoder_stem and a.segment is not None:
        r = rescore_pair(a.jsonl, a.decoded, a.encoder_stem, a.segment)
        w = max(len(k) for k in r)
        for k, v in r.items():
            print(f"  {k:<{w}}  {v}")
    else:
        ap.error("either --sheet <file>, or positional jsonl encoder_stem segment")
