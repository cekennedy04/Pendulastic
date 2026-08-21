"""
summarize_partial_sweep.py
===========================
Reads sweep_mediapipe_preprocessing.py's cache (currently 657/675 pairs,
97.3% complete -- the sweep keeps getting killed by the environment before
finishing the last ~18 pairs) and reports the same per-candidate summary
main() would have written, using only what's already cached. Never calls
score_candidate / opens a landmarker, so this is fast (no MediaPipe
inference) and safe to run alongside a still-in-progress sweep chunk.

Run:
    .venv\\Scripts\\python.exe summarize_partial_sweep.py
"""
import csv
import os

import sweep_mediapipe_preprocessing as smp

PARTIAL_CSV = os.path.join(smp.OUT_DIR, "partial_preprocessing_sweep_results.csv")


def main():
    trials = smp.rpc.discover_video_trials()
    print(f"{len(trials)} trial(s) with video + OptiTrack ground truth found.")

    model_path = os.path.join(smp.BASE_DIR, "models", "mediapipe",
                              f"pose_landmarker_{smp.MODEL_VARIANT}.task")
    cache = smp._load_cache()
    stat_cache = {}
    impl_fp = smp._implementation_fingerprint()

    rows = []
    missing = []

    for candidate in smp.CANDIDATES:
        rmses = []
        for trial in trials:
            try:
                cache_key = smp._cache_key(trial, candidate["key"], model_path,
                                           stat_cache, impl_fp)
            except Exception as e:
                missing.append((candidate["key"], trial["trial_key"], f"key-error: {e}"))
                continue
            if cache_key in cache:
                rmses.append(cache[cache_key])
            else:
                missing.append((candidate["key"], trial["trial_key"], "not cached"))

        summary = smp._summarize_candidate(rmses, len(trials))
        rows.append({
            "candidate": candidate["key"], "n_trials": len(trials),
            "n_scored": summary["n_scored"],
            "median_rmse_deg": summary["median_rmse_deg"],
            "mean_rmse_deg": summary["mean_rmse_deg"],
            "pct_under_10deg": summary["pct_under_10deg"],
        })

        median_rmse = summary["median_rmse_deg"]
        mean_rmse = summary["mean_rmse_deg"]
        pct_under_goal = summary["pct_under_10deg"]
        median_str = f"{median_rmse:.2f}" if median_rmse is not None else "n/a"
        mean_str = f"{mean_rmse:.2f}" if mean_rmse is not None else "n/a"
        print(f"{candidate['key']:16s} n_scored={summary['n_scored']}/{len(trials)}  "
             f"median={median_str} deg  mean={mean_str} deg  %<10deg={pct_under_goal:.1f}%")

    with open(PARTIAL_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    print(f"-> {PARTIAL_CSV}")

    print(f"\n{len(missing)} pair(s) not yet cached:")
    for candidate_key, trial_key, reason in missing:
        print(f"  {candidate_key} / {trial_key}: {reason}")


if __name__ == "__main__":
    main()
