"""Decode the LabVIEW ``.bin`` stretch-trial files from the motor rig.

File format (verified against ``ck_app_pt_test/*.bin`` on 2026-09-11):

* raw stream of IEEE-754 float64, **big-endian** (LabVIEW default), no header
* interleaved by sample, 7 channels per sample (6 for files older than 2015-06-20)
* 1000 Hz (measured from d(position)/dt vs the velocity channel on all 18 files)

Channel order, per the supervisor's MATLAB notes::

    1 Position            [deg]
    2 Velocity            [deg/s]
    3 Position set point  [deg]
    4 Torque              [N*m]
    5 EMG1: FDS
    6 EMG2: EDC
    7 Velocity set point  [deg/s]   (only after 2015-06-20)

The MATLAB reference this ports::

    t2 = fread(fid, 'double');           % NB: needs fopen(..., 'ieee-be') on a PC
    t  = reshape(t2, 6, []);
    tnormtbase  = mean(t(4, 1:150));
    tnormtbase2 = mean(t(4, end-150:end));
    if abs(tnormtbase2) < abs(tnormtbase), tnormtbase = tnormtbase2; end
    tnormt = t(4,:) - tnormtbase;
    [b, a] = butter(2, 10/250, 'low');   % normalised Wn = 0.04 -> 20 Hz at 1 kHz
    tnormt = filtfilt(b, a, tnormt);

Usage::

    python labview_bin_reader.py ck_app_pt_test                 # every .bin -> decoded/*.csv
    python labview_bin_reader.py ck_app_pt_test/foo.bin --plot  # one file, plus a PNG

    from labview_bin_reader import read_labview_bin, normalize_torque
    trial = read_labview_bin("ck_app_pt_test/ck_app_pt_testStretchTrial01Fast.bin")
    trial["time"], trial["position"], trial["torque"] ...
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

import numpy as np
from scipy.signal import butter, filtfilt

FS_HZ = 1000.0
BYTES_PER_DOUBLE = 8

CHANNELS_7 = ("position", "velocity", "position_setpoint", "torque",
              "emg_fds", "emg_edc", "velocity_setpoint")
CHANNELS_6 = CHANNELS_7[:6]

# The MATLAB reference uses butter(2, 10/250): Wn is relative to Nyquist, so the
# cutoff is 0.04 * (fs/2) = 20 Hz at the measured 1 kHz. Keep the *normalised*
# value so the output matches the supervisor's numbers exactly.
TORQUE_FILTER_WN = 10 / 250
BASELINE_SAMPLES = 150


def read_labview_bin(path: str | Path, n_channels: int | None = None,
                     fs_hz: float = FS_HZ) -> dict[str, np.ndarray]:
    """Return ``{"time": ..., "position": ..., ...}`` with one 1-D array per channel.

    ``n_channels`` is inferred from the file size when not given: 7 if the size
    divides by 56 bytes, else 6 if it divides by 48.
    """
    path = Path(path)
    size = path.stat().st_size
    if size == 0:
        raise ValueError(f"{path.name}: empty file (0 bytes) - the trial never recorded")
    if size % BYTES_PER_DOUBLE:
        raise ValueError(f"{path.name}: {size} bytes is not a whole number of doubles")

    if n_channels is None:
        if size % (7 * BYTES_PER_DOUBLE) == 0:
            n_channels = 7
        elif size % (6 * BYTES_PER_DOUBLE) == 0:
            n_channels = 6
        else:
            raise ValueError(f"{path.name}: {size // 8} doubles is not a multiple of 6 or 7")
    elif size % (n_channels * BYTES_PER_DOUBLE):
        raise ValueError(f"{path.name}: {size // 8} doubles is not a multiple of {n_channels}")

    raw = np.fromfile(path, dtype=">f8")        # big-endian float64
    data = raw.reshape(-1, n_channels).T        # (channels, samples), like MATLAB's t
    names = CHANNELS_7 if n_channels == 7 else CHANNELS_6

    out = {"time": np.arange(data.shape[1]) / fs_hz}
    out.update(zip(names, data))
    return out


def normalize_torque(torque: np.ndarray, wn: float = TORQUE_FILTER_WN,
                     baseline_samples: int = BASELINE_SAMPLES) -> np.ndarray:
    """Port of the supervisor's ``tnormt``: subtract the quieter of the two end
    baselines, then zero-phase 2nd-order Butterworth low-pass.

    MATLAB's ``1:150`` is 150 samples but ``end-150:end`` is 151; both are kept
    as-is so results match the reference to the last digit.
    """
    torque = np.asarray(torque, dtype=float)
    if len(torque) < baseline_samples + 1:
        raise ValueError(f"need > {baseline_samples} samples for the baseline, got {len(torque)}")
    base_start = torque[:baseline_samples].mean()
    base_end = torque[-(baseline_samples + 1):].mean()
    base = base_end if abs(base_end) < abs(base_start) else base_start
    b, a = butter(2, wn, btype="low")
    # MATLAB filtfilt pads with 3*max(len(a),len(b)) samples; scipy's default is
    # 3*max(len(a),len(b)) too, so this matches without extra arguments.
    return filtfilt(b, a, torque - base)


def write_csv(trial: dict[str, np.ndarray], out_path: Path) -> None:
    cols = list(trial)
    with open(out_path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(cols)
        w.writerows(zip(*(trial[c] for c in cols)))


def plot_trial(trial: dict[str, np.ndarray], out_path: Path, title: str) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    t = trial["time"]
    fig, axes = plt.subplots(4, 1, sharex=True, figsize=(11, 9))
    axes[0].plot(t, trial["position"], lw=0.8, label="position")
    axes[0].plot(t, trial["position_setpoint"], lw=0.8, ls="--", label="set point")
    axes[0].set_ylabel("deg")
    axes[1].plot(t, trial["velocity"], lw=0.6, label="velocity")
    if "velocity_setpoint" in trial:
        axes[1].plot(t, trial["velocity_setpoint"], lw=0.8, ls="--", label="set point")
    axes[1].set_ylabel("deg/s")
    axes[2].plot(t, trial["torque"], lw=0.6, alpha=0.5, label="raw")
    axes[2].plot(t, trial["torque_norm"], lw=0.8, label="baseline-removed, 20 Hz LP")
    axes[2].set_ylabel("N·m")
    axes[3].plot(t, trial["emg_fds"], lw=0.6, label="EMG FDS")
    axes[3].plot(t, trial["emg_edc"], lw=0.6, label="EMG EDC")
    axes[3].set_ylabel("V")
    axes[3].set_xlabel("time [s]")
    for ax in axes:
        ax.legend(loc="upper right", fontsize=8)
        ax.grid(alpha=0.3)
    fig.suptitle(title)
    fig.tight_layout()
    fig.savefig(out_path, dpi=110)
    plt.close(fig)


def convert(path: Path, out_dir: Path, plot: bool) -> str:
    trial = read_labview_bin(path)
    trial["torque_norm"] = normalize_torque(trial["torque"])
    out_dir.mkdir(exist_ok=True)
    write_csv(trial, out_dir / (path.stem + ".csv"))
    if plot:
        plot_trial(trial, out_dir / (path.stem + ".png"), path.stem)
    n = len(trial["time"])
    return (f"{path.name}: {n} samples ({n / FS_HZ:.1f} s), "
            f"{len(trial) - 2} channels, pos {trial['position'].min():.1f}..{trial['position'].max():.1f} deg")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("target", help=".bin file or a folder of them")
    ap.add_argument("--out", help="output folder (default: <folder>/decoded)")
    ap.add_argument("--plot", action="store_true", help="also save a PNG per trial")
    args = ap.parse_args(argv)

    target = Path(args.target)
    files = sorted(target.glob("*.bin")) if target.is_dir() else [target]
    out_dir = Path(args.out) if args.out else (target if target.is_dir() else target.parent) / "decoded"

    failures = 0
    for f in files:
        try:
            print(convert(f, out_dir, args.plot))
        except ValueError as e:
            failures += 1
            print(f"SKIP {e}", file=sys.stderr)
    print(f"\nwrote {len(files) - failures} file(s) to {out_dir}" + (f", skipped {failures}" if failures else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
