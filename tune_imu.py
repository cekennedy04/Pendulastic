#!/usr/bin/env python3
"""
tune_imu.py
===========
Standalone CLI for the IMU adaptive self-tuning calibration loop. Runs the
same grid search / persistence engine as the live app's post-recording
trigger (imu_calibration_tuner.py), against one or more previously-recorded
raw IMU JSONL logs.

Usage:
    .venv\\Scripts\\python.exe tune_imu.py <raw_log.jsonl> [<raw_log2.jsonl> ...]
    .venv\\Scripts\\python.exe tune_imu.py <raw_log.jsonl> --force
"""
from __future__ import annotations

import argparse
import json
import sys

from imu_calibration_tuner import (
    TUNING_GRID, replay_trial, score_waveform, load_config, save_config,
    _is_improvement, _now_iso,
)


def load_raw_log(path: str) -> list:
    """Return this log's raw samples, or [] with a printed warning if the
    file can't be read at all (missing path, permission error, etc.) --
    treated the same as an empty/all-malformed log by the caller, rather
    than raising an unhandled traceback for a typo'd CLI argument."""
    samples = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    samples.append(json.loads(line))
                except ValueError:
                    continue
    except OSError as e:
        print(f"Warning: could not read {path}: {e}")
        return []
    return samples


def _average_tune(raw_logs: list) -> dict:
    """Grid search where each candidate's penalty is averaged across all
    provided logs — a more robust pick than tuning against a single trial."""
    results = []
    for params in TUNING_GRID:
        penalties = []
        all_pass = True
        for raw_samples in raw_logs:
            t, angle = replay_trial(raw_samples, params)
            if len(t) == 0:
                penalties.append(1e6)
                all_pass = False
                continue
            scored = score_waveform(t, angle)
            penalties.append(scored["penalty"])
            all_pass = all_pass and scored["passes"]
        avg_penalty = sum(penalties) / len(penalties)
        results.append({"params": params, "penalty": avg_penalty, "passes": all_pass})

    passing = [r for r in results if r["passes"]]
    pool = passing if passing else results
    return min(pool, key=lambda r: r["penalty"])


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("raw_logs", nargs="+", help="Path(s) to *_raw.jsonl trial logs")
    parser.add_argument("--force", action="store_true",
                        help="Persist the winning config even if it doesn't "
                             "improve on the current one")
    args = parser.parse_args(argv)

    loaded = [(p, load_raw_log(p)) for p in args.raw_logs]
    dropped = [p for p, s in loaded if not s]
    if dropped:
        print(f"Skipping {len(dropped)} log(s) with no valid samples: {', '.join(dropped)}")
    raw_log_sets = [s for _, s in loaded if s]
    if not raw_log_sets:
        print("No valid samples found in any provided raw log.")
        return 1

    best = _average_tune(raw_log_sets)
    current = load_config()

    print(f"Best configuration: {best['params']}")
    print(f"Average penalty: {best['penalty']:.3f}  passes={best['passes']}")

    if args.force or _is_improvement(best, current):
        save_config({
            "beta": best["params"]["beta"],
            "ema_alpha": best["params"]["ema_alpha"],
            "flex_axis_capture": best["params"]["flex_axis_capture"],
            "gravity_seed": best["params"]["gravity_seed"],
            "method": best["params"].get("method", "relative"),
            "penalty": best["penalty"],
            "passes": best["passes"],
            "tuned_at": _now_iso(),
            "source_trial": ",".join(args.raw_logs),
        })
        print("Saved to imu_calibration_config.json")
    elif not best["passes"]:
        print("No configuration met the physical constraints — nothing persisted.")
    else:
        print("Did not improve on the current persisted configuration — nothing persisted "
              "(use --force to override).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
