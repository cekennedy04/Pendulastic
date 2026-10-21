"""SSR step-response extractor — IMU side, from GYRO INTEGRATION.

Reads the phone raw-log JSONL files from an SSR staircase session and, for each,
integrates the raw swing-axis gyro (gyro-y, rad/s) to an angle, segments the
trace into settled dwells (gyro near zero), and reports per step:

    IMU delta  = settled(to) - settled(from)   [mean of the last SETTLE_WIN_S]
    drift      = linear slope of the "to" dwell [deg/s]

Why gyro, not the app's fused angle: on the HORIZONTAL rig the accelerometer
sees no in-plane rotation (gravity stays on z), so the AHRS fusion corrupts the
angle — validated against the 1 kHz encoder, gyro-integration tracks step Δ to
~0.9 deg (R²=1.00) while the fused angle misses by ~60 deg. Use gyro here.

Usage:
    python ssr_extract.py                       # scans ~/Downloads for *.jsonl
    python ssr_extract.py <dir_or_file> [...]   # explicit files/dirs
    python ssr_extract.py --out ssr_imu.csv <dir>

Name each raw log with its step size, e.g. ssr_A10_rep1.jsonl, and the
commanded From/To (0, +A, -A per the 0->+A->-A->+A->0 pattern) are filled in.
"""
from __future__ import annotations
import os, sys, re, csv, json, glob
import numpy as np

HOLD_RATE_RADS = 0.20   # |gyro-y| below this (rad/s ~= 11 deg/s) = settled
MIN_DWELL_S    = 0.8
SETTLE_WIN_S   = 1.0
EXPECTED_DWELLS = 5     # 0, +A, -A, +A, 0
PATTERN_A = [(0.0, +1.0), (+1.0, -1.0), (-1.0, +1.0), (+1.0, 0.0)]  # steps 1..4


def load_gyro_angle(path):
    """Return (t_s, angle_deg) from integrating raw gyro-y, and the raw rate."""
    recs = [json.loads(l) for l in open(path, encoding="utf-8") if l.strip()]
    g = [(r["t"], r["v"][1]) for r in recs if r.get("sensor") == "gyro"]
    if len(g) < 50:
        return None
    g.sort()
    t = np.array([x[0] for x in g], float)
    gy = np.array([x[1] for x in g], float)
    if np.median(np.diff(t)) > 0.5:      # ms -> s
        t = t / 1000.0
    t = t - t[0]
    ang = np.concatenate([[0.0], np.cumsum((gy[1:] + gy[:-1]) / 2 * np.diff(t))]) * 180 / np.pi
    return t, ang, np.abs(gy)


def find_dwells(t, rate_mag):
    still = rate_mag < HOLD_RATE_RADS
    out, i, n = [], 0, len(t)
    while i < n:
        if still[i]:
            j = i
            while j < n and still[j]:
                j += 1
            if t[j-1] - t[i] >= MIN_DWELL_S:
                out.append((i, j-1))
            i = j
        else:
            i += 1
    return out


def dwell_stats(t, ang, span):
    i0, i1 = span
    tt, aa = t[i0:i1+1], ang[i0:i1+1]
    win = tt >= (tt[-1] - SETTLE_WIN_S)
    settled = float(np.mean(aa[win])) if win.any() else float(np.mean(aa))
    drift = float(np.polyfit(tt[win]-tt[win][0], aa[win], 1)[0]) if win.sum() >= 2 else float("nan")
    return settled, drift, float(tt[-1]-tt[0])


def parse_A(name):
    m = re.search(r"[Aa]_?(\d+(?:\.\d+)?)", os.path.basename(name))
    return float(m.group(1)) if m else None


def extract_file(path):
    loaded = load_gyro_angle(path)
    if loaded is None:
        return {"file": os.path.basename(path), "error": "too few gyro samples"}
    t, ang, rate = loaded
    dwells = find_dwells(t, rate)
    stats = [dwell_stats(t, ang, d) for d in dwells]
    base = stats[0][0] if stats else 0.0
    A = parse_A(path)
    steps = []
    for k in range(len(stats) - 1):
        s_from, _, _ = stats[k]
        s_to, drift_to, _ = stats[k+1]
        row = {"step": k+1, "imu_from_rel": round(s_from-base, 2),
               "imu_to_rel": round(s_to-base, 2), "imu_delta": round(s_to-s_from, 2),
               "drift_dps": round(drift_to, 3)}
        if A is not None and k < len(PATTERN_A):
            fr, to = PATTERN_A[k]
            row["cmd_from"] = round(fr*A, 1); row["cmd_to"] = round(to*A, 1)
        steps.append(row)
    return {"file": os.path.basename(path), "A": A, "n_dwells": len(dwells),
            "dwell_durs": [round(s[2], 1) for s in stats], "steps": steps}


def gather(paths):
    if not paths:
        paths = [os.path.join(os.path.expanduser("~"), "Downloads")]
    files = []
    for p in paths:
        if os.path.isdir(p):
            files += sorted(glob.glob(os.path.join(p, "*.jsonl")))
        elif os.path.isfile(p):
            files.append(p)
    return files


def main(argv):
    out_csv, args = None, []
    it = iter(argv)
    for a in it:
        if a == "--out": out_csv = next(it, None)
        else: args.append(a)
    files = gather(args)
    if not files:
        print("no .jsonl files found (looked in Downloads by default)"); return 1
    all_rows = []
    for path in files:
        r = extract_file(path)
        print("\n" + "=" * 66); print(r["file"])
        if "error" in r: print("  ERROR:", r["error"]); continue
        flag = "" if r["n_dwells"] == EXPECTED_DWELLS else f"  <-- expected {EXPECTED_DWELLS} dwells"
        print(f"  A={r['A']}  dwells found: {r['n_dwells']}{flag}  durs(s)={r['dwell_durs']}")
        print(f"  {'step':>4} {'From°':>7} {'To°':>7} {'IMU Δ':>8} {'drift°/s':>9}")
        for s in r["steps"]:
            fr = s.get("cmd_from", s["imu_from_rel"]); to = s.get("cmd_to", s["imu_to_rel"])
            print(f"  {s['step']:>4} {fr:>7} {to:>7} {s['imu_delta']:>8} {s['drift_dps']:>9}")
            all_rows.append({"file": r["file"], "A": r["A"], **s})
    if out_csv:
        cols = ["file","A","step","cmd_from","cmd_to","imu_from_rel","imu_to_rel","imu_delta","drift_dps"]
        with open(out_csv, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore"); w.writeheader(); w.writerows(all_rows)
        print(f"\nwrote {out_csv}  ({len(all_rows)} step rows)")
    print("\nGyro-integration angle (horizontal-rig correct). Paste IMU Δ + drift into "
          "the SSR tab; Encoder Δ comes from the decoded .bin.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
