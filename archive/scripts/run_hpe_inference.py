"""
run_hpe_inference.py
====================
Lightweight HPE inference for Pendulastic clinical trials.
No interactive review, no annotated videos — just knee-angle CSVs.

Design assumptions
------------------
* Patient is horizontal on a treatment table  → trunk is stable (hip doesn't drift)
* Assessor is always present (duo=True)       → YOLO picks the horizontal person
* Active leg auto-detected via YOLO pre-pass  → moving knee has higher y-variance
* Biomechanical gate in mediapipe_worker      → floor/therapist ankle rejected

Folder structure (add new participants here):
    Recordings/Participant_<ID>_<left|right>_<duo|solo>/
        Position_<P>/Height_<H>/Trial_<T>.[avi|mp4]

Usage:
    .venv\\Scripts\\python.exe -u run_hpe_inference.py
    .venv\\Scripts\\python.exe -u run_hpe_inference.py --pid 3
    .venv\\Scripts\\python.exe -u run_hpe_inference.py --families mediapipe,rtmpose,hrnet
    .venv\\Scripts\\python.exe -u run_hpe_inference.py --families all
"""
from __future__ import annotations
import argparse
import sys

import pendulastic_pipeline as _pp

# The 6 model families for the clinical evaluation
CLINICAL_FAMILIES: set[str] = {"mediapipe", "movenet", "rtmpose", "vitpose", "hrnet", "yolo"}


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Pendulastic HPE inference — knee-angle CSVs only, no annotation.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--pid",      default=None,
                    help="Restrict to participant ID substring, e.g. --pid 3")
    ap.add_argument("--families", default="all",
                    help='Comma-separated model families, or "all". '
                         f'Choices: {", ".join(sorted(CLINICAL_FAMILIES))}. Default: all')
    args = ap.parse_args()

    # Participant filter
    if args.pid:
        _pp.TARGET_PARTICIPANTS = [args.pid]

    # Model filter — exclude guided variants (need click data)
    if args.families.strip().lower() == "all":
        wanted = CLINICAL_FAMILIES
    else:
        wanted = {f.strip().lower() for f in args.families.split(",")}

    active = [
        e for e in _pp.MODEL_REGISTRY
        if e.family.lower() in wanted and not e.variant.endswith("_guided")
    ]
    if not active:
        ap.error(f"No models matched --families {args.families!r}. "
                 f"Available families: {', '.join(sorted({e.family for e in _pp.MODEL_REGISTRY}))}")

    # Discover trials
    videos = _pp.discover_videos()
    if not videos:
        print(f"No trial videos found.\n  VIDEO_ROOT = {_pp.VIDEO_ROOT}")
        sys.exit(1)

    duo_n = sum(1 for v in videos if v["is_duo"])
    print(f"\nDiscovered {len(videos)} trial(s)  [{duo_n} duo / {len(videos)-duo_n} solo]")
    print(f"Models ({len(active)}): {', '.join(f'{e.family}/{e.variant}' for e in active)}\n")

    # ── Step 1: YOLO pre-pass — detect which leg is swinging in each trial ──
    print("=" * 60)
    print("ACTIVE-LEG DETECTION  (YOLO pre-pass)")
    print("=" * 60)
    _pp._detect_all_leg_sides(videos)

    # ── Step 2: Inference — no annotation step ──────────────────────────────
    print("\n" + "=" * 60)
    print(f"INFERENCE  ({len(active)} models × {len(videos)} trials)")
    print("  Trunk assumed stable → hip treated as fixed landmark")
    print("  Ankle biomechanical gate active → floor detections → NaN")
    print("=" * 60 + "\n")

    for i, entry in enumerate(active, 1):
        label = f"{entry.family}/{entry.variant}"
        print(f"[{i:2d}/{len(active)}] {label}")
        _pp._infer_one_entry(videos, entry)

    print("\nDone. Run run_hpe_analysis.py to compute statistics.")


if __name__ == "__main__":
    main()
