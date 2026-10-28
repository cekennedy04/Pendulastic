"""Prove the C# PtScore port agrees with the Python scorer, field by field.

    python neuromech/designer/parity/run_parity.py [--regen] [--repeats 3]

1. writes the cases (make_cases.py) unless cases/index.csv already exists or --regen,
2. builds ParityMain.exe + PtScore.cs with the .NET 3.5 compiler (C# 3.0) -- the target is a
   stock Windows 7 machine, so the stock compiler is the one that has to accept the code,
3. runs it over every case,
4. scores every case with neuromech.build.score_angle (+ mas_estimate, the breakdown, pt_zone,
   pt_to_mas, the two gates) and compares: floats to REL_TOL relative (ABS_FLOOR absolute near
   zero), counts / extrema indices / strings / refusals / errors exactly,
5. prints a per-field table and exits 1 on any mismatch.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
import struct
import subprocess
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
DESIGNER = os.path.dirname(HERE)
CASES = os.path.join(HERE, "cases")
BIN = os.path.join(HERE, "bin")
CSC = r"C:\Windows\Microsoft.NET\Framework\v3.5\csc.exe"
REL_TOL = 1e-6
ABS_FLOOR = 1e-12

FLOAT_FIELDS = ["R2n", "N", "phi_max_ratio", "omega_max_n", "omega_min_n", "omega_peak_deg_s", "f", "area_ratio",
                "A0_deg", "A1_deg", "first_trough_depth", "neutral_deg", "neutral_deg_raw", "pre_release_deg",
                "P_plus", "P_minus", "P_total",
                "term_R2n", "term_N", "term_phi_max_ratio", "term_omega_max_n", "term_f", "term_area_ratio", "pt7"]
EXACT_FIELDS = ["error", "grid_len", "ridx", "has_params", "quality_warn", "phi_negated", "spasticity_type",
                "pk_i", "tr_i", "mas", "zone", "interpretable", "reason", "pt_zone", "pt_to_mas",
                "excursion_ok", "oscillation_ok", "refused"]
PARAM_ONLY = set(FLOAT_FIELDS[:-1]) | {"quality_warn", "phi_negated", "spasticity_type", "pk_i", "tr_i"}


def _unhex(h: str) -> float:
    return struct.unpack(">d", bytes.fromhex(h))[0]


def ensure_cases(regen=False, n_random=200):
    if regen or not os.path.exists(os.path.join(CASES, "index.csv")):
        sys.path.insert(0, HERE)
        import make_cases
        make_cases.main(["--n-random", str(n_random)])


def build_exe(platform="anycpu"):
    if not os.path.exists(CSC):
        raise FileNotFoundError(CSC)
    os.makedirs(BIN, exist_ok=True)
    exe = os.path.join(BIN, f"ParityMain_{platform}.exe")
    cmd = [CSC, "/nologo", "/optimize+", "/warn:4", "/target:exe", f"/platform:{platform}", f"/out:{exe}",
           os.path.join(DESIGNER, "PtScore.cs"), os.path.join(HERE, "ParityMain.cs")]
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError("csc failed:\n" + p.stdout + p.stderr)
    warn = [l for l in p.stdout.splitlines() if "warning" in l]
    return exe, warn


def run_csharp(exe, repeats=3):
    out = os.path.join(BIN, os.path.basename(exe)[:-4] + "_results.jsonl")
    p = subprocess.run([exe, CASES, out, str(repeats)], capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError("ParityMain failed:\n" + p.stdout + p.stderr)
    res = {}
    with open(out, encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                d = json.loads(line)
                res[d["case_id"]] = {k: (_unhex(v[2:]) if isinstance(v, str) and v.startswith("x:") else v) for k, v in d.items()}
    return res, p.stdout.strip()


def _read_case(path):
    t, a = [], []
    with open(path) as fh:
        next(fh)
        for line in fh:
            c = line.rstrip("\n").split(",")
            t.append(_unhex(c[2])); a.append(_unhex(c[3]))
    return np.array(t, float), np.array(a, float)


def python_results():
    sys.path.insert(0, ROOT)
    os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
    from neuromech.build import score_angle, FS_SCORE
    import pendulastic_pt_score as P
    res, rows = {}, list(csv.DictReader(open(os.path.join(CASES, "index.csv"), newline="")))
    t_total = 0.0
    for r in rows:
        t, a = _read_case(os.path.join(CASES, r["file"]))
        rel = _unhex(r["release_t_hex"])
        d = {"case_id": r["case_id"], "category": r["category"], "error": None}
        try:
            t1 = time.perf_counter()
            prm, est = score_angle(t, a, rel)
            t_total += time.perf_counter() - t1
            tg = np.arange(t[0], t[-1], 1 / FS_SCORE)
            d["grid_len"] = int(len(tg)); d["ridx"] = int(np.searchsorted(tg, rel))
            d["has_params"] = prm is not None
            if prm is not None:
                for k in FLOAT_FIELDS:
                    if k in prm:
                        d[k] = float(prm[k])
                for k in ("quality_warn", "phi_negated"):
                    d[k] = bool(prm[k])
                d["spasticity_type"] = prm["spasticity_type"]
                d["pk_i"] = [int(i) for i in prm["pk_i"]]; d["tr_i"] = [int(i) for i in prm["tr_i"]]
                for k, v in P.compute_pt_score_breakdown(prm).items():
                    d["term_" + k] = float(v)
            d["pt7"] = None if est["pt7"] is None else float(est["pt7"])
            d["mas"] = est["mas"]; d["zone"] = est["zone"]; d["interpretable"] = bool(est["interpretable"])
            d["reason"] = est["reason"]
            d["pt_zone"] = P.pt_zone(est["pt7"])
            d["pt_to_mas"] = None if est["pt7"] is None else P.pt_to_mas(est["pt7"])
            d["excursion_ok"] = bool(P.excursion_ok(prm)); d["oscillation_ok"] = bool(P.oscillation_ok(prm))
            d["refused"] = None if est["interpretable"] else est["reason"]
        except Exception as e:  # mirrored: C# throws where Python raises
            d["error"] = type(e).__name__
        res[r["case_id"]] = d
    return res, rows, t_total


def _err_kind(v):
    return None if v is None else str(v).split(":")[0]


def compare(py, cs, rows):
    stats = {k: dict(n=0, max_abs=0.0, max_rel=0.0, bad=0) for k in FLOAT_FIELDS + EXACT_FIELDS}
    mismatches = []
    for r in rows:
        cid = r["case_id"]
        p, c = py[cid], cs.get(cid)
        if c is None:
            mismatches.append((cid, "missing", "", "")); continue
        pe = p.get("error")
        ce = c.get("error")
        # Python raises IndexError/ValueError where C# throws ArgumentException: compare "errored or not"
        stats["error"]["n"] += 1
        if (pe is None) != (ce is None):
            stats["error"]["bad"] += 1; mismatches.append((cid, "error", pe, ce)); continue
        if pe is not None:
            continue
        has = p["has_params"]
        for k in FLOAT_FIELDS:
            if (k in PARAM_ONLY or k.startswith("term_")) and not has:
                continue
            a, b = p.get(k), c.get(k)
            st = stats[k]; st["n"] += 1
            if a is None or b is None:
                if (a is None) != (b is None):
                    st["bad"] += 1; mismatches.append((cid, k, a, b))
                continue
            if math.isnan(a) and math.isnan(b):
                continue
            da = abs(a - b); scale = max(abs(a), abs(b))
            dr = da / scale if scale > 0 else 0.0
            st["max_abs"] = max(st["max_abs"], da)
            if scale > 1e-6:          # relative error is meaningless on values at rounding level
                st["max_rel"] = max(st["max_rel"], dr)
            if not (da <= ABS_FLOOR or dr <= REL_TOL):
                st["bad"] += 1; mismatches.append((cid, k, a, b))
        for k in EXACT_FIELDS:
            if k == "error" or (k in PARAM_ONLY and not has):
                continue
            a, b = p.get(k), c.get(k)
            st = stats[k]; st["n"] += 1
            if a != b:
                st["bad"] += 1; mismatches.append((cid, k, a, b))
    return stats, mismatches


def report(stats, mismatches, rows, py, cs, cs_summary, py_total, warn):
    lines = []
    cats = {}
    for r in rows:
        cats[r["category"]] = cats.get(r["category"], 0) + 1
    lines.append(f"cases: {len(rows)}  (" + ", ".join(f"{k} {v}" for k, v in cats.items()) + ")")
    n_none = sum(1 for r in rows if py[r["case_id"]].get("error") is None and not py[r["case_id"]]["has_params"])
    n_ref = sum(1 for r in rows if py[r["case_id"]].get("error") is None and py[r["case_id"]]["has_params"]
                and not py[r["case_id"]]["interpretable"])
    n_err = sum(1 for r in rows if py[r["case_id"]].get("error") is not None)
    lines.append(f"python outcome: {len(rows) - n_none - n_ref - n_err} graded, {n_ref} refused by a gate, "
                 f"{n_none} params=None, {n_err} raised")
    lines.append(f"{'field':22s} {'n':>5s} {'max |abs|':>11s} {'max rel':>11s} {'bad':>4s}")
    for k in FLOAT_FIELDS:
        s = stats[k]
        lines.append(f"{k:22s} {s['n']:5d} {s['max_abs']:11.3e} {s['max_rel']:11.3e} {s['bad']:4d}")
    for k in EXACT_FIELDS:
        s = stats[k]
        lines.append(f"{k:22s} {s['n']:5d} {'exact':>11s} {'':>11s} {s['bad']:4d}")
    times = [c["time_ms"] for c in cs.values() if c.get("time_ms") is not None]
    if times:
        lines.append(f"C# ScoreAngle per case: median {np.median(times):.3f} ms, max {np.max(times):.3f} ms "
                     f"(n={len(times)}); Python score_angle: {1000 * py_total / max(len(rows), 1):.3f} ms/case")
    lines.append("ParityMain: " + cs_summary)
    if warn:
        lines.append("csc warnings: " + "; ".join(warn))
    if mismatches:
        lines.append(f"MISMATCHES: {len(mismatches)}")
        for m in mismatches[:40]:
            lines.append(f"  {m[0]:34s} {m[1]:18s} py={m[2]!r:.80} cs={m[3]!r:.80}")
    else:
        lines.append("PARITY OK: every field within tolerance on every case")
    return "\n".join(lines)


def run(regen=False, repeats=3, n_random=200, platform="anycpu"):
    ensure_cases(regen, n_random)
    exe, warn = build_exe(platform)
    cs, cs_summary = run_csharp(exe, repeats)
    py, rows, py_total = python_results()
    stats, mm = compare(py, cs, rows)
    return (len(mm) == 0), report(stats, mm, rows, py, cs, cs_summary, py_total, warn)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--regen", action="store_true", help="rewrite the cases first")
    ap.add_argument("--repeats", type=int, default=3)
    ap.add_argument("--n-random", type=int, default=200)
    ap.add_argument("--platform", default="anycpu", choices=["anycpu", "x86", "x64"],
                    help="x86 forces the 32-bit (x87) JIT a 32-bit Windows 7 machine would use")
    a = ap.parse_args(argv)
    ok, text = run(a.regen, a.repeats, a.n_random, a.platform)
    print(f"platform: {a.platform}")
    print(text)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
