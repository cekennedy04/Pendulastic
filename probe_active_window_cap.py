"""THROWAWAY PROBE -- not production code.

Question: `_ACTIVE_WINDOW_CAP_SEC = 4.0` truncates oscillation counting, so N
measures frequency x window rather than damping (both cohort arms sit at ~91%
of their own 4*f ceiling). Published healthy legs oscillate 9 times, which
needs ~9 s.

Does lifting the cap restore N's discriminative range, or does it resurrect
the noise-inflation bug the cap was added to fix (N read 0.5 with a 3 s tail
vs 28.5 with a 30 s tail)?

Measures, per trial, across a sweep of caps:
  - N, f, R2n, and the composite
  - whether the recording is even long enough for a longer window to matter
  - the N-vs-f correlation (does N decouple from frequency?)

Run:  ~/miniconda3/python.exe probe_active_window_cap.py
"""
from __future__ import annotations

import glob
import os
import warnings

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

import pendulastic_pt_score as pt

CAPS = [4.0, 6.0, 8.0, 10.0, 15.0, 1e9]     # 1e9 == effectively unbounded
CAP_LABEL = {1e9: "none"}


def load_trials():
    """Raw phone IMU angle series. Modality is not the point here -- the
    window question is about the counting algorithm, which runs on whatever
    curve it is handed."""
    out = []
    for f in sorted(glob.glob("Recordings/**/*_imu.csv", recursive=True)):
        parts = os.path.normpath(f).split(os.sep)
        try:
            i = parts.index("Recordings")
            folder, leg, cond = parts[i + 1], parts[i + 2], parts[i + 3]
        except (ValueError, IndexError):
            continue
        try:
            d = pd.read_csv(f, comment="#")
        except Exception:
            continue
        if "prox_pitch" not in d or "phone_ts_ms" not in d or len(d) < 200:
            continue
        t = d.phone_ts_ms.to_numpy(float) / 1000.0
        t -= t[0]
        a = d.prox_pitch.to_numpy(float)
        ok = np.isfinite(t) & np.isfinite(a)
        if ok.sum() < 200:
            continue
        out.append({"arm": folder.split("_")[0], "folder": folder, "leg": leg,
                    "cond": cond, "t": t[ok], "a": a[ok],
                    "dur": float(t[ok][-1])})
    return out


def main() -> int:
    trials = load_trials()
    print(f"loaded {len(trials)} trials; "
          f"median duration {np.median([x['dur'] for x in trials]):.1f} s, "
          f"p10 {np.percentile([x['dur'] for x in trials], 10):.1f} s\n")

    rows = []
    for cap in CAPS:
        pt._ACTIVE_WINDOW_CAP_SEC = cap
        for x in trials:
            try:
                p = pt.compute_pt_params(x["t"], x["a"])
            except Exception:
                p = None
            if not p:
                continue
            rows.append({"cap": cap, "arm": x["arm"], "folder": x["folder"],
                         "dur": x["dur"], "N": p.get("N"), "f": p.get("f"),
                         "R2n": p.get("R2n"),
                         "A0": p.get("A0_deg"),
                         "score": pt.compute_pt_score(p)})
    pt._ACTIVE_WINDOW_CAP_SEC = 4.0          # restore
    d = pd.DataFrame(rows)

    print("=== N by window cap ===")
    print(f"{'cap':>6s} {'n':>5s} {'N med':>7s} {'N p95':>7s} {'N max':>8s} "
          f"{'corr(N,f)':>10s} {'ctrl N':>7s} {'affected N':>11s} {'gap':>7s}")
    for cap in CAPS:
        s = d[d.cap == cap]
        if s.empty:
            continue
        ctrl = s[s.arm == "Control"].N.median()
        aff = s[s.arm.isin(["MINT", "CHAT"])].N.median()
        c = np.corrcoef(s.N.fillna(0), s.f.fillna(0))[0, 1]
        lab = CAP_LABEL.get(cap, f"{cap:.0f}s")
        print(f"{lab:>6s} {len(s):5d} {s.N.median():7.2f} "
              f"{s.N.quantile(.95):7.2f} {s.N.max():8.1f} {c:10.3f} "
              f"{ctrl:7.2f} {aff:11.2f} {aff - ctrl:7.2f}")

    print("\n=== does the noise-inflation bug return? ===")
    base = d[d.cap == 4.0].set_index(["folder", "arm"]).N
    for cap in CAPS[1:]:
        s = d[d.cap == cap]
        blown = (s.N > 12).sum()
        print(f"  cap {CAP_LABEL.get(cap, f'{cap:.0f}s'):>5s}: "
              f"{blown:3d} trials with N > 12 "
              f"(implausible for a {np.median([x['dur'] for x in trials]):.0f}s "
              f"recording at ~1 Hz)")

    print("\n=== is there even signal left to count? ===")
    for w in (4, 6, 8, 10, 15):
        frac = np.mean([x["dur"] > w for x in trials])
        print(f"  recordings longer than {w:2d}s: {frac * 100:5.1f}%")

    print("\n=== composite score by cap ===")
    for cap in CAPS:
        s = d[d.cap == cap]
        if s.empty:
            continue
        ctrl = s[s.arm == "Control"].score.median()
        aff = s[s.arm.isin(["MINT", "CHAT"])].score.median()
        print(f"  cap {CAP_LABEL.get(cap, f'{cap:.0f}s'):>5s}: "
              f"control {ctrl:7.3f}   affected {aff:7.3f}   "
              f"separation {aff - ctrl:+7.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
