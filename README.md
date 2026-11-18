# Pendulastic

Pendulastic measures lower-limb spasticity with the Wartenberg pendulum test, using a phone's motion
sensors (IMU). The study covers three groups: unaffected controls, stroke and MS.

The repository holds **three programs**. Each has its own folder and its own instructions:

| Program | What it does | Where it is | How to start it |
|---|---|---|---|
| **1. Pendulastic phone app** | Records the pendulum test with the phone's IMU and scores it on the phone | [`webapp/`](webapp/) (scoring engine in Rust: [`mobile-imu-core/`](mobile-imu-core/)) | Open https://pendulastic-app.vercel.app in Safari on an iPhone → *Share* → *Add to Home Screen*. Details: [`webapp/README.md`](webapp/README.md) |
| **2. Motor rig (knee emulator)** | Plays simulated pendulum swings on the motor rig with a 1 kHz encoder, so the phone can be checked against a known movement | [`neuromech/`](neuromech/): model [`model_rt.py`](neuromech/model_rt.py), Pendulum Designer [`designer/`](neuromech/designer/), plain player [`labview/player/`](neuromech/labview/player/) | `python -m neuromech.rig_bundle` builds the folder that goes on the rig PC; there, open `START_HERE.txt` and follow `RIG_CHECKLIST.txt`. Details: [`neuromech/README.md`](neuromech/README.md) |
| **3. Sync recorder** | Records the phone IMU, OptiTrack (Motive) and the camera at the same time for participant sessions | Repo root: [`pendulastic_app.py`](pendulastic_app.py), with [`pendulastic_imu_server.py`](pendulastic_imu_server.py) (phone) and [`motive_sync.py`](motive_sync.py) (OptiTrack) | `.venv\Scripts\python.exe pendulastic_app.py` (setup under [Installation](#installation)) |

What is being worked on, and what is decided, is in [`STATUS.md`](STATUS.md).

**Status:** none of these programs is clinically validated. The phone app shows a "not validated" banner, and rig
results are not reported as PT scores.

---

## Research Aims

| Aim | Description |
|-----|-------------|
| **Aim 1: Sensor characterization** | Measure how accurately the phone's gyroscope and accelerometer track angle against the motor rig's encoder (static and dynamic) |
| **Aim 2: Open-source Pendulastic** | An open-source, easy-to-use app for the pendulum test, tried with physical therapists and participants |
| **Aim 3: Neuro-mechanical model** | Test the app against a model of the spastic knee played on the motor rig |
| **Aim 4: IMU vs markerless** | Compare the IMU and markerless motion capture at measuring the pendulum-test parameters in MS, stroke and control participants |

---

## Origin: the markerless-video pipeline (no longer developed)

The project started as a markerless video pipeline: MediaPipe pose estimation on ordinary video, producing the same
pendulum-test metrics. That code is still here (`src/pendulastic`, the HPE scripts at the repo root) and is used for
the Aim 4 comparison, but it is not under active development. The sections below describe it and may be out of date.

### Key features of the video pipeline

- **Markerless pose estimation** — tracks hip, knee, and ankle landmarks using MediaPipe; no wearable sensors or reflective markers required
- **Automated knee angle trajectory extraction** — computes continuous joint angles from raw landmark coordinates
- **Pendulum test metric quantification**:
  - First flexion amplitude
  - Plateau (resting) angle
  - Relaxation index (RI)
  - Number of oscillations
  - Oscillatory dynamics and damping characteristics (logarithmic decrement, damping ratio)
- **Quantitative spasticity scoring** — derived metric composites for comparison against clinical scales
- **Portable and accessible** — runs on standard video from any camera; designed for clinical and remote-assessment settings
- **IMU-based pathway (separate research track)** — `pendulastic_imu_server.py` / `imu_calibration_tuner.py` compute
  the same pendulum-test metrics from a phone or wearable IMU via AHRS sensor fusion, for reliability comparison
  against the markerless-video pipeline above

---

## Project Structure

> Illustrative, not literal — shows the core `src/pendulastic` package layout. The repo root also
> holds 100+ additional scripts, apps, and status docs (`pendulastic_app.py`, `master_app.py`,
> `pendulastic_imu_server.py`, `imu_calibration_tuner.py`, etc.; see `STATUS.md`) that change
> too often to keep an exhaustive tree in sync here.

```
Pendulastic/
├── data/
│   ├── raw/                  # Raw video recordings
│   ├── processed/            # Extracted landmark and angle data
│   └── results/              # Computed metrics and reports
├── notebooks/                # Exploratory analysis and validation notebooks
├── src/
│   └── pendulastic/
│       ├── __init__.py
│       ├── video.py          # Video I/O and preprocessing
│       ├── pose.py           # Markerless pose estimation
│       ├── metrics.py        # Pendulum test metric extraction
│       ├── stats.py          # Reliability and validity analyses
│       └── utils.py          # Shared utilities
├── tests/
│   ├── test_video.py
│   ├── test_pose.py
│   ├── test_metrics.py
│   └── test_stats.py
├── requirements.txt
├── setup.py
└── README.md
```

---

## Installation

```bash
git clone https://github.com/your-username/Pendulastic.git
cd Pendulastic
python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # macOS/Linux
pip install -r requirements.txt
```

### Headless Linux also needs two system libraries

On Windows and macOS `pip install` is enough. On a headless Linux box — a CI
runner, a container, a cloud dev session — MediaPipe additionally needs the
GL dispatch libraries, which are not Python packages and so are not in
`requirements.txt`:

```bash
sudo apt-get install -y libegl1 libgles2
```

Without them `mediapipe.Image(...)` raises
`OSError: libEGL.so.1: cannot open shared object file` (and then
`libGLESv2.so.2`) the first time a frame is handed to the pose detector.
Nothing else fails, and the message never mentions MediaPipe, so the symptom
is **12 failures in `tests/test_capture_coverage_hpe_session.py`** —
all of them stopping at the same line, `_pump`'s
`"worker stopped consuming after 0 frames"`, because the worker catches the
error per frame and the detector is therefore never called. The code is fine;
the box is missing a library. Diagnosed 2026-11-02, after those 12 had been
carried as unexplained pre-existing failures.

---

## Mobile app (`webapp/`)

`webapp/` is a self-contained iOS Safari app for capturing and scoring the
pendulum test on the phone itself: motion is captured from the phone's IMU,
fused and scored by the `mobile-imu-core/` Rust engine compiled to
WebAssembly, and all 20 parameters are shown on-device. It is **not
validated** and says so in-app.

**To try it, you do not need to build anything:** it is deployed at
**https://pendulastic-app.vercel.app**. Open that in Safari on an iPhone and
use *Share → Add to Home Screen* — install it rather than bookmarking it,
because iOS discards a non-installed site's stored recordings after ~7 days.

To build or deploy it from source (Node + Rust toolchain required), see
[`webapp/README.md`](webapp/README.md) — in particular
[Build step](webapp/README.md#build-step-required-before-serving-or-testing)
and [Deploying to Vercel](webapp/README.md#deploying-to-vercel-the-live-app).
The live site is updated by a local build and CLI upload, not by pushing to
this repo, so what is deployed is whatever was last uploaded.

---

## Quick Start

```python
from pendulastic.video import load_video, preprocess_frames
from pendulastic.pose import extract_landmarks
from pendulastic.metrics import compute_knee_angles, extract_pendulum_metrics

frames = load_video("data/raw/participant_01.mp4")
frames = preprocess_frames(frames)
landmarks = extract_landmarks(frames)
angles = compute_knee_angles(landmarks)
metrics = extract_pendulum_metrics(angles)

print(metrics)
```

---

## Dependencies

| Library | Purpose |
|---------|---------|
| `opencv-python` | Video capture, decoding, and frame processing |
| `mediapipe` | Markerless pose estimation |
| `numpy` | Numerical computation |
| `scipy` | Signal processing and curve fitting |
| `pandas` | Tabular data management |
| `matplotlib` | Visualisation |
| `scikit-learn` | Machine learning and scoring models |

See [requirements.txt](requirements.txt) for pinned versions.

---

## License

This project is licensed under the MIT License. See [LICENSE](LICENSE) for details.

---

## Citation

If you use Pendulastic in your research, please cite (citation forthcoming upon publication).
