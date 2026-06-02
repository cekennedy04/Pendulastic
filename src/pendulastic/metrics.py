"""Pendulum test metric extraction from knee angle trajectories."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.signal import find_peaks, savgol_filter

from .pose import LandmarkFrame


def compute_knee_angles(
    landmarks: list[LandmarkFrame | None],
    smooth: bool = True,
    window_length: int = 11,
    polyorder: int = 3,
) -> np.ndarray:
    """Compute the knee flexion angle (degrees) for each frame.

    The angle is the interior angle at the knee joint formed by the
    hip–knee and knee–ankle vectors, measured in degrees.

    Args:
        landmarks: Per-frame landmark data (None entries are interpolated).
        smooth: Apply Savitzky–Golay smoothing to the angle trajectory.
        window_length: Window length for Savitzky–Golay filter (must be odd).
        polyorder: Polynomial order for Savitzky–Golay filter.

    Returns:
        1-D array of knee angles in degrees, length == len(landmarks).
    """
    angles = np.full(len(landmarks), np.nan)

    for i, frame in enumerate(landmarks):
        if frame is None:
            continue
        v1 = frame.hip - frame.knee
        v2 = frame.ankle - frame.knee
        cos_angle = np.dot(v1, v2) / (np.linalg.norm(v1) * np.linalg.norm(v2) + 1e-9)
        angles[i] = np.degrees(np.arccos(np.clip(cos_angle, -1.0, 1.0)))

    # Linear interpolation over NaN (failed detections)
    nan_mask = np.isnan(angles)
    if nan_mask.any():
        x = np.arange(len(angles))
        angles = np.interp(x, x[~nan_mask], angles[~nan_mask])

    if smooth and len(angles) > window_length:
        angles = savgol_filter(angles, window_length=window_length, polyorder=polyorder)

    return angles


@dataclass
class PendulumMetrics:
    """Quantitative metrics extracted from a single pendulum test trial."""

    first_flexion_amplitude_deg: float
    plateau_angle_deg: float
    relaxation_index: float
    num_oscillations: int
    damping_ratio: float | None
    logarithmic_decrement: float | None

    def to_dict(self) -> dict:
        return {
            "first_flexion_amplitude_deg": self.first_flexion_amplitude_deg,
            "plateau_angle_deg": self.plateau_angle_deg,
            "relaxation_index": self.relaxation_index,
            "num_oscillations": self.num_oscillations,
            "damping_ratio": self.damping_ratio,
            "logarithmic_decrement": self.logarithmic_decrement,
        }


def extract_pendulum_metrics(
    angles: np.ndarray,
    fps: float = 30.0,
    plateau_window_s: float = 1.0,
) -> PendulumMetrics:
    """Extract standard pendulum test metrics from a knee angle trajectory.

    Args:
        angles: 1-D knee angle trajectory in degrees.
        fps: Frame rate of the source video.
        plateau_window_s: Duration (seconds) at end of trial used to estimate plateau angle.

    Returns:
        PendulumMetrics dataclass.
    """
    plateau_frames = max(1, int(plateau_window_s * fps))
    plateau_angle = float(np.median(angles[-plateau_frames:]))

    # First flexion amplitude: deepest valley from the starting (extended) position
    start_angle = float(np.median(angles[:int(0.2 * fps)]))  # first 200 ms
    valleys, _ = find_peaks(-angles)
    first_flexion_amplitude = (
        float(start_angle - angles[valleys[0]]) if valleys.size > 0 else 0.0
    )

    # Relaxation index: ratio of first flexion amplitude to full range
    full_range = float(np.ptp(angles))
    relaxation_index = first_flexion_amplitude / full_range if full_range > 0 else 0.0

    # Number of oscillations (peaks in the trajectory)
    peaks, _ = find_peaks(angles, prominence=1.0)
    num_oscillations = int(peaks.size)

    # Damping characteristics from successive peak amplitudes
    damping_ratio, log_dec = _estimate_damping(angles, peaks, plateau_angle)

    return PendulumMetrics(
        first_flexion_amplitude_deg=round(first_flexion_amplitude, 2),
        plateau_angle_deg=round(plateau_angle, 2),
        relaxation_index=round(relaxation_index, 4),
        num_oscillations=num_oscillations,
        damping_ratio=damping_ratio,
        logarithmic_decrement=log_dec,
    )


def _estimate_damping(
    angles: np.ndarray,
    peaks: np.ndarray,
    plateau_angle: float,
) -> tuple[float | None, float | None]:
    """Estimate damping ratio and logarithmic decrement from peak amplitudes."""
    if peaks.size < 2:
        return None, None

    amplitudes = np.abs(angles[peaks] - plateau_angle)
    # Logarithmic decrement from successive peaks
    log_decs = np.log(amplitudes[:-1] / (amplitudes[1:] + 1e-9))
    log_dec = float(np.mean(log_decs))
    damping_ratio = log_dec / np.sqrt(4 * np.pi**2 + log_dec**2)

    return round(float(damping_ratio), 4), round(log_dec, 4)
