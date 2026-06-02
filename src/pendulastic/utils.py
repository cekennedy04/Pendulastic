"""Shared utilities — plotting, file I/O, and data export."""

from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


def plot_angle_trajectory(
    angles: np.ndarray,
    fps: float = 30.0,
    title: str = "Knee Angle Trajectory",
    save_path: str | Path | None = None,
) -> None:
    """Plot knee angle over time and optionally save to file."""
    time = np.arange(len(angles)) / fps
    fig, ax = plt.subplots(figsize=(10, 4))
    ax.plot(time, angles, linewidth=1.5, color="steelblue")
    ax.set_xlabel("Time (s)")
    ax.set_ylabel("Knee Angle (°)")
    ax.set_title(title)
    ax.grid(True, alpha=0.3)
    plt.tight_layout()

    if save_path is not None:
        fig.savefig(save_path, dpi=150)
    else:
        plt.show()

    plt.close(fig)


def save_angles_csv(
    angles: np.ndarray,
    path: str | Path,
    fps: float = 30.0,
) -> None:
    """Save angle trajectory to a CSV file with a time column."""
    time = np.arange(len(angles)) / fps
    pd.DataFrame({"time_s": time, "knee_angle_deg": angles}).to_csv(path, index=False)


def load_angles_csv(path: str | Path) -> tuple[np.ndarray, np.ndarray]:
    """Load angle trajectory from a CSV saved by :func:`save_angles_csv`.

    Returns:
        Tuple of (time_s, knee_angle_deg) arrays.
    """
    df = pd.read_csv(path)
    return df["time_s"].to_numpy(), df["knee_angle_deg"].to_numpy()


def ensure_dir(path: str | Path) -> Path:
    """Create directory (and parents) if it does not exist."""
    path = Path(path)
    path.mkdir(parents=True, exist_ok=True)
    return path
