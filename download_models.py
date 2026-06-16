"""
==============================================================================
 download_models.py - Automatic ONNX weight fetcher for analysis_pipeline.py
==============================================================================
 Downloads the pre-trained pose-estimation ONNX weights used by the live
 evaluation engine into ./models/<name>/ if they are not already present.

     python download_models.py            # fetch everything that's missing
     python download_models.py --force    # re-download even if present

 What gets fetched:
   * mediapipe-> PoseLandmarker .task bundle (modern Tasks API; the legacy
                 mp.solutions API was removed in recent mediapipe builds).
   * rtmpose  -> RTMPose body model (SimCC head), ONNX SDK bundle
   * mmpose   -> a second MMPose-project model (RTMPose-S by default), ONNX SDK

 IMPORTANT - VERIFY THE URLS BELOW.
 -----------------------------------------------------------------------------
 These point at OpenMMLab's public ONNX-SDK directory. The exact filenames
 carry date + hash suffixes that change between releases, and this script was
 authored without network access to confirm them. If a download 404s, the
 script prints the directory to browse and the exact local folder to drop the
 .onnx into. analysis_pipeline.py resolves ANY *.onnx inside models/<name>/,
 so a manually-placed file works identically.

 Tip: the `rtmlib` package (pip install rtmlib) bundles verified RTMPose ONNX
 URLs and is the easiest way to obtain known-good files if these go stale.
==============================================================================
"""

import os
import sys
import glob
import shutil
import zipfile
import tempfile
import urllib.request
import urllib.error

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
MODELS_DIR = os.path.join(PROJECT_DIR, "models")

# OpenMMLab public ONNX-SDK directory for RTMPose. Browse here if a URL fails:
_OMM = "https://download.openmmlab.com/mmpose/v1/projects/rtmposev1/onnx_sdk"
# Google's MediaPipe model bucket for the PoseLandmarker .task bundle:
_MP = "https://storage.googleapis.com/mediapipe-models/pose_landmarker"

# name -> {"url", "kind" ("onnx"|"task"), "browse"}
# (RTMPose ONNX filenames were verified to resolve on 2026-06-16.)
MODEL_REGISTRY = {
    "mediapipe": {
        # PoseLandmarker Tasks API model. Modern mediapipe (Tasks-only builds)
        # dropped the legacy mp.solutions API, so the .task file is required.
        "url": f"{_MP}/pose_landmarker_lite/float16/latest/pose_landmarker_lite.task",
        "kind": "task",
        "browse": "https://ai.google.dev/edge/mediapipe/solutions/vision/pose_landmarker#models",
    },
    "rtmpose": {
        "url": f"{_OMM}/rtmpose-m_simcc-body7_pt-body7_420e-256x192-e48f03d0_20230504.zip",
        "kind": "onnx",
        "browse": _OMM,
    },
    "mmpose": {
        # A second, smaller MMPose-project checkpoint so the two ONNX engines
        # differ. Swap in an HRNet heatmap .onnx here if you prefer (the
        # pipeline's decoder auto-detects SimCC vs heatmap outputs).
        "url": f"{_OMM}/rtmpose-s_simcc-body7_pt-body7_420e-256x192-acd4a1ef_20230504.zip",
        "kind": "onnx",
        "browse": _OMM,
    },
}

# File extension that signals a given model kind is already present.
_KIND_EXT = {"onnx": "*.onnx", "task": "*.task"}


def _has_model(model_dir, kind):
    pattern = _KIND_EXT.get(kind, "*.onnx")
    return bool(glob.glob(os.path.join(model_dir, "**", pattern), recursive=True))


def _report_progress(blocks, block_size, total_size):
    if total_size <= 0:
        return
    downloaded = blocks * block_size
    pct = min(100.0, downloaded * 100.0 / total_size)
    mb = downloaded / (1024 * 1024)
    total_mb = total_size / (1024 * 1024)
    sys.stdout.write(f"\r    {pct:5.1f}%  ({mb:6.1f} / {total_mb:6.1f} MB)")
    sys.stdout.flush()


def _download(url, dest_path):
    urllib.request.urlretrieve(url, dest_path, reporthook=_report_progress)
    sys.stdout.write("\n")


def _extract_onnx_from_zip(zip_path, model_dir):
    """Extract a zip and ensure at least one .onnx ends up in model_dir."""
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(model_dir)
    # Flatten: if the .onnx landed in a subfolder, that's fine - the pipeline
    # globs recursively. Just confirm one exists.
    return _has_model(model_dir, "onnx")


def fetch_model(name, spec, force=False):
    kind = spec.get("kind", "onnx")
    model_dir = os.path.join(MODELS_DIR, name)
    os.makedirs(model_dir, exist_ok=True)

    if _has_model(model_dir, kind) and not force:
        print(f"[skip] '{name}': {_KIND_EXT[kind]} already present in {model_dir}")
        return True

    if force:
        for f in glob.glob(os.path.join(model_dir, "**", "*"), recursive=True):
            if os.path.isfile(f):
                try:
                    os.remove(f)
                except OSError:
                    pass

    url = spec["url"]
    print(f"[get ] '{name}': {url}")

    tmp_fd, tmp_path = tempfile.mkstemp(suffix=os.path.splitext(url)[1] or ".bin")
    os.close(tmp_fd)
    try:
        _download(url, tmp_path)

        # NOTE: a MediaPipe .task bundle is itself a zip, so only ONNX models
        # get unpacked - .task files are saved intact as the model asset.
        if kind == "onnx" and zipfile.is_zipfile(tmp_path):
            ok = _extract_onnx_from_zip(tmp_path, model_dir)
            if not ok:
                raise RuntimeError("Downloaded zip contained no .onnx file.")
        else:
            dest = os.path.join(model_dir, os.path.basename(url))
            shutil.move(tmp_path, dest)

        print(f"[ ok ] '{name}': ready in {model_dir}")
        return True

    except (urllib.error.HTTPError, urllib.error.URLError, RuntimeError, OSError) as e:
        ext = _KIND_EXT.get(kind, "*.onnx")
        what = ("a PoseLandmarker .task bundle" if kind == "task"
                else "a COCO-17 pose .onnx (SimCC RTMPose or HRNet heatmap)")
        print("-" * 70)
        print(f"[FAIL] Could not auto-download '{name}'.")
        print(f"       Reason: {e}")
        print(f"       The URL filename/hash may be stale.")
        print(f"       1) Browse the source: {spec.get('browse', url)}")
        print(f"       2) Download {what}.")
        print(f"       3) Place the file here:  {model_dir}")
        print(f"       The pipeline uses any {ext} found in that folder.")
        print("-" * 70)
        return False
    finally:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass


def main():
    force = "--force" in sys.argv[1:]
    os.makedirs(MODELS_DIR, exist_ok=True)

    print("=" * 70)
    print(" Pendulastic - ONNX weight downloader")
    print(f" Target dir : {MODELS_DIR}")
    print("=" * 70)
    print("Fetching MediaPipe .task + RTMPose/MMPose .onnx weights...\n")

    results = {}
    for name, spec in MODEL_REGISTRY.items():
        results[name] = fetch_model(name, spec, force=force)
        print()

    print("=" * 70)
    ok = [n for n, v in results.items() if v]
    bad = [n for n, v in results.items() if not v]
    print(f" Ready : {', '.join(ok) if ok else '(none)'}")
    if bad:
        print(f" Missing: {', '.join(bad)}  <-- see manual instructions above")
    print("=" * 70)
    sys.exit(0 if not bad else 1)


if __name__ == "__main__":
    main()
