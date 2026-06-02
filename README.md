# Pendulastic

A markerless computer vision framework for quantifying lower-limb spasticity in people with Multiple Sclerosis (MS) via automated Wartenberg pendulum test analysis.

---

## Overview

Spasticity assessment in MS currently relies on subjective clinical scales (Modified Ashworth Scale, Tardieu Scale), which are observer-dependent and impractical for remote or longitudinal monitoring. The Wartenberg pendulum test offers a biomechanically grounded alternative, but traditional implementations require expensive motion capture systems or inertial measurement units (IMUs).

**Pendulastic** automates the pendulum test using standard smartphone or computer cameras and markerless pose estimation, producing quantitative, clinically interpretable spasticity metrics from ordinary video recordings.

---

## Research Aims

| Aim | Description |
|-----|-------------|
| **Aim 1** | Develop a markerless video-based pendulum test analysis framework |
| **Aim 2** | Establish reliability and agreement with existing measurement systems (IMUs, motion capture) |
| **Aim 3** | Evaluate clinical validity by comparing derived metrics with MAS and Tardieu Scale scores |

---

## Key Features

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

---

## Project Structure

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
