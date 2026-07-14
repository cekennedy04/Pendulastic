"""
download_pipeline_deps.py
=========================
Step 1: Installs the OpenMMLab stack (mmengine → mmcv → mmdet → mmpose)
Step 2: Downloads HRNet-W48-COCO checkpoint (the core model PosePipeline wraps)
Step 3: Verifies the full asset tree for both OpenPose and HRNet

Run from the Pendulastic directory using the project venv:
    .venv\\Scripts\\python.exe download_pipeline_deps.py
"""

import os
import sys
import subprocess
import urllib.request
import hashlib

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# ─────────────────────────────────────────────────────────────────────────────
# PATHS
# ─────────────────────────────────────────────────────────────────────────────

HRNET_CKPT_DIR  = os.path.join(BASE_DIR, "models", "PosePipeline", "checkpoints")
HRNET_CKPT_NAME = "hrnet_w48_coco_256x192-b9e0b3ab_20200708.pth"
HRNET_CKPT_PATH = os.path.join(HRNET_CKPT_DIR, HRNET_CKPT_NAME)

# mmpose v1.x HRNet-W48-COCO checkpoint (official OpenMMLab mirror)
HRNET_URL = (
    "https://download.openmmlab.com/mmpose/v1/body_2d_keypoint/"
    "topdown_heatmap/coco/"
    "td-hm_hrnet-w48_8xb32-210e_coco-256x192-0e67c616_20220913.pth"
)
HRNET_CKPT_FINAL = "td-hm_hrnet-w48_8xb32-210e_coco-256x192-0e67c616_20220913.pth"
HRNET_CKPT_PATH  = os.path.join(HRNET_CKPT_DIR, HRNET_CKPT_FINAL)

OPENPOSE_BIN    = os.path.join(BASE_DIR, "models", "openpose", "bin", "OpenPoseDemo.exe")
OPENPOSE_B25    = os.path.join(BASE_DIR, "models", "openpose", "models", "pose", "body_25",
                                "pose_iter_584000.caffemodel")
OPENPOSE_PROTO  = os.path.join(BASE_DIR, "models", "openpose", "models", "pose", "body_25",
                                "pose_deploy.prototxt")


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def ok(msg):  print(f"  [OK]  {msg}")
def miss(msg): print(f"  [!!]  {msg}")
def info(msg): print(f"  [--]  {msg}")

def pip(*args):
    cmd = [sys.executable, "-m", "pip", "install", "--quiet", *args]
    print(f"\n  $ {' '.join(cmd)}")
    r = subprocess.run(cmd, capture_output=False)
    if r.returncode != 0:
        print(f"  [ERR] pip failed — see output above")
        return False
    return True

def mim_install(*packages):
    """Install via openmim."""
    cmd = [sys.executable, "-m", "mim", "install", *packages]
    print(f"\n  $ {' '.join(cmd)}")
    r = subprocess.run(cmd, capture_output=False)
    if r.returncode != 0:
        print(f"  [ERR] mim install failed — see output above")
        return False
    return True

def download_file(url: str, dest: str, label: str) -> bool:
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    if os.path.isfile(dest):
        size_mb = os.path.getsize(dest) / 1e6
        ok(f"{label} already present ({size_mb:.1f} MB)")
        return True
    print(f"\n  Downloading {label} ...")
    print(f"    URL : {url}")
    print(f"    DEST: {dest}")
    try:
        def _progress(count, block, total):
            if total > 0:
                pct = count * block / total * 100
                bar = "#" * int(pct / 5)
                print(f"\r    [{bar:<20}] {min(pct,100):.0f}%", end="", flush=True)
        urllib.request.urlretrieve(url, dest, _progress)
        print()
        size_mb = os.path.getsize(dest) / 1e6
        ok(f"{label} downloaded ({size_mb:.1f} MB)")
        return True
    except Exception as e:
        print()
        miss(f"Download failed: {e}")
        return False

def check_pkg(pkg):
    try:
        __import__(pkg)
        return True
    except ImportError:
        return False


# ─────────────────────────────────────────────────────────────────────────────
# STEP 0 — ASSET AUDIT
# ─────────────────────────────────────────────────────────────────────────────

def audit():
    print("\n" + "="*65)
    print("ASSET AUDIT")
    print("="*65)

    print("\n--- OpenPose ---")
    assets_op = [
        (OPENPOSE_BIN,   "OpenPoseDemo.exe            [binary]"),
        (OPENPOSE_B25,   "pose_iter_584000.caffemodel  [BODY_25 weights]"),
        (OPENPOSE_PROTO, "pose_deploy.prototxt         [BODY_25 config]"),
    ]
    op_ready = True
    for path, label in assets_op:
        if os.path.isfile(path):
            mb = os.path.getsize(path) / 1e6
            ok(f"{label}  ({mb:.0f} MB)")
        else:
            miss(f"{label}  — MISSING at {path}")
            op_ready = False

    print("\n--- PosePipeline (HRNet-W48 via mmpose) ---")
    assets_pp = [
        (os.path.join(BASE_DIR, "models", "PosePipeline", "PosePipeline-main",
                      "pose_pipeline", "__init__.py"),
         "PosePipeline source code"),
        (HRNET_CKPT_PATH,
         "HRNet-W48 COCO checkpoint (.pth)"),
    ]
    pp_ready = True
    for path, label in assets_pp:
        if os.path.isfile(path):
            mb = os.path.getsize(path) / 1e6
            ok(f"{label}  ({mb:.0f} MB)")
        else:
            miss(f"{label}  — MISSING at {path}")
            pp_ready = False

    print("\n--- Python packages ---")
    pkgs = {
        "openmim":    "openmim",
        "mmengine":   "mmengine",
        "mmcv":       "mmcv",
        "mmdet":      "mmdet",
        "mmpose":     "mmpose",
    }
    for pkg, import_name in pkgs.items():
        if check_pkg(import_name):
            import importlib
            mod = importlib.import_module(import_name)
            ver = getattr(mod, "__version__", "?")
            ok(f"{pkg}=={ver}")
        else:
            miss(f"{pkg}  — NOT INSTALLED")
            pp_ready = False

    return op_ready, pp_ready


# ─────────────────────────────────────────────────────────────────────────────
# STEP 1 — INSTALL mmlab STACK
# ─────────────────────────────────────────────────────────────────────────────

def install_mmlab():
    print("\n" + "="*65)
    print("STEP 1 — Installing OpenMMLab stack")
    print("="*65)

    if not check_pkg("openmim"):
        print("\n  Installing openmim...")
        if not pip("openmim"):
            return False

    if not check_pkg("mmengine"):
        print("\n  Installing mmengine via mim...")
        if not mim_install("mmengine"):
            return False

    if not check_pkg("mmcv"):
        print("\n  Installing mmcv via mim ...")
        # mmcv-lite avoids the CUDA build step; works for CPU/ONNX inference
        if not mim_install("mmcv>=2.0.0rc4"):
            print("  Trying mmcv-lite fallback...")
            if not pip("mmcv-lite"):
                return False

    if not check_pkg("mmdet"):
        print("\n  Installing mmdet via mim...")
        if not mim_install("mmdet>=3.0.0"):
            return False

    if not check_pkg("mmpose"):
        print("\n  Installing mmpose via mim...")
        if not mim_install("mmpose>=1.0.0"):
            return False

    ok("All OpenMMLab packages installed.")
    return True


# ─────────────────────────────────────────────────────────────────────────────
# STEP 2 — DOWNLOAD HRNet-W48 CHECKPOINT
# ─────────────────────────────────────────────────────────────────────────────

def download_hrnet():
    print("\n" + "="*65)
    print("STEP 2 — Downloading HRNet-W48-COCO checkpoint")
    print("="*65)
    return download_file(HRNET_URL, HRNET_CKPT_PATH, "HRNet-W48-COCO (.pth)")


# ─────────────────────────────────────────────────────────────────────────────
# STEP 3 — WRITE mmpose config needed for HRNet-W48
# ─────────────────────────────────────────────────────────────────────────────

HRNET_W48_CONFIG = """\
# HRNet-W48 COCO 256x192 — mmpose v1.x format
# Auto-generated by download_pipeline_deps.py

_base_ = ['mmpose::body_2d_keypoint/topdown_heatmap/coco/td-hm_hrnet-w48_8xb32-210e_coco-256x192.py']
"""

def write_hrnet_config():
    cfg_dir  = os.path.join(BASE_DIR, "models", "PosePipeline", "checkpoints")
    cfg_path = os.path.join(cfg_dir, "hrnet_w48_coco_256x192.py")
    os.makedirs(cfg_dir, exist_ok=True)
    if not os.path.isfile(cfg_path):
        with open(cfg_path, "w") as fh:
            fh.write(HRNET_W48_CONFIG)
        ok(f"Config written: {cfg_path}")
    else:
        ok(f"Config already present: {cfg_path}")
    return cfg_path


# ─────────────────────────────────────────────────────────────────────────────
# STEP 4 — FINAL VERIFICATION
# ─────────────────────────────────────────────────────────────────────────────

def final_check():
    print("\n" + "="*65)
    print("FINAL VERIFICATION")
    print("="*65)

    all_ok = True

    checks = [
        (OPENPOSE_BIN,    "OpenPose binary"),
        (OPENPOSE_B25,    "OpenPose BODY_25 weights"),
        (OPENPOSE_PROTO,  "OpenPose BODY_25 prototxt"),
        (HRNET_CKPT_PATH, "HRNet-W48 checkpoint"),
    ]
    for path, label in checks:
        if os.path.isfile(path):
            mb = os.path.getsize(path) / 1e6
            ok(f"{label}  ({mb:.0f} MB)")
        else:
            miss(f"{label}  MISSING — {path}")
            all_ok = False

    pkg_checks = ["mmengine", "mmcv", "mmdet", "mmpose"]
    for pkg in pkg_checks:
        if check_pkg(pkg):
            import importlib
            mod = importlib.import_module(pkg)
            ok(f"  {pkg}=={getattr(mod,'__version__','?')}")
        else:
            miss(f"  {pkg} NOT INSTALLED")
            all_ok = False

    print()
    if all_ok:
        print("  ALL ASSETS PRESENT — environment is ready.")
        print()
        print("  Next step: run  .venv\\Scripts\\python.exe run_new_models_evaluate.py")
    else:
        print("  SOME ASSETS MISSING — review errors above and re-run.")

    return all_ok


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

def main():
    print("\nPendulastic — Pipeline Dependency Installer")
    print("Python:", sys.executable)

    op_ready, pp_ready = audit()

    if op_ready:
        print("\n  OpenPose: already fully ready — skipping.")
    else:
        print("\n  OpenPose: binary/weights are missing from models/openpose/.")
        print("  These cannot be auto-downloaded (requires manual CMake build or")
        print("  the pre-built Windows installer from github.com/CMU-Perceptual-Computing-Lab/openpose).")
        print("  Please check your models/openpose/ folder.")

    if not pp_ready:
        ok1 = install_mmlab()
        ok2 = download_hrnet()
        cfg  = write_hrnet_config()
        if ok1 and ok2:
            print(f"\n  Config file : {cfg}")
            print(f"  Checkpoint  : {HRNET_CKPT_PATH}")
    else:
        print("\n  PosePipeline/HRNet: already fully ready — skipping.")

    final_check()


if __name__ == "__main__":
    main()
