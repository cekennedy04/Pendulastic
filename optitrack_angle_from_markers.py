"""
optitrack_angle_from_markers.py
================================
Computes knee flexion angles from raw unlabeled marker positions in an
OptiTrack CSV export where rigid body solving produced zero quaternions,
OR directly from the binary .tak file's Channels.dat stream.

The script:
  1. Reads the 6 unlabeled marker columns (3 per segment).
  2. Separates thigh markers (low 3D variance, nearly fixed) from shank
     markers (high variance, swinging) using a variance threshold.
  3. Estimates the knee joint centre as the centroid of the thigh marker
     cluster (the cluster closest to the knee in space).
  4. Estimates the shank long-axis direction per frame using the first
     principal component of the 3 shank marker positions.
  5. Computes the deviation-from-extension angle:
       angle = arccos( (-thigh_hat) · shank_hat )
     — 0° at full extension (shank colinear with thigh), increasing with flexion.
     The pt_score.py Format-C loader applies 180 - angle to convert to the
     standard interior-angle convention before further analysis.
  6. Writes a CSV with columns: frame, time_sec, knee_angle_deg.

Usage:
    python optitrack_angle_from_markers.py <optitrack_csv_or_tak> [output_csv]

If output_csv is omitted, writes next to the input file with suffix
_computed_angles.csv.

Can also be imported:
    from optitrack_angle_from_markers import (
        compute_angles_from_optitrack_csv,
        compute_angles_from_tak,
    )
"""
from __future__ import annotations

import argparse
import csv
import math
import os
import struct
import sys
from typing import Optional

import numpy as np


# ---------------------------------------------------------------------------
# CSV parsing
# ---------------------------------------------------------------------------

def _find_data_start(lines: list[str]) -> int:
    """Return index of the first line whose first cell looks like a frame number."""
    for i, ln in enumerate(lines):
        cell = ln.split(",")[0].strip()
        if cell.lstrip("-").isdigit():
            return i
    raise ValueError("No numeric frame rows found in CSV.")


def _find_unlabeled_col_start(lines: list[str]) -> int:
    """
    Return the column index of the first 'Unlabeled' marker by scanning the
    row-2 header (type row). OptiTrack format: row 0 = metadata, row 2 = type.
    """
    for ln in lines[:8]:
        cells = ln.split(",")
        for ci, cell in enumerate(cells):
            if "unlabeled" in cell.lower():
                return ci
    raise ValueError("Could not find 'Unlabeled' marker columns in CSV header.")


def load_optitrack_csv(csv_path: str):
    """
    Parse an OptiTrack multi-row-header CSV.

    Returns
    -------
    times  : (N,) float array   — time in seconds
    markers: (N, 6, 3) float array — positions of 6 unlabeled markers
    frames : (N,) int array     — frame indices
    """
    with open(csv_path, "r", newline="") as fh:
        raw = fh.read()
    lines = raw.splitlines()

    data_start = _find_data_start(lines)
    unlabeled_col = _find_unlabeled_col_start(lines)

    frames_list, times_list, markers_list = [], [], []

    for ln in lines[data_start:]:
        row = ln.split(",")
        if not row[0].strip().lstrip("-").isdigit():
            continue
        try:
            frame = int(row[0].strip())
            t     = float(row[1].strip())
        except (ValueError, IndexError):
            continue

        # Extract 6 markers × 3 coords = 18 values starting at unlabeled_col
        needed = unlabeled_col + 18
        if len(row) < needed:
            continue
        try:
            vals = [float(v.strip()) if v.strip() else float("nan")
                    for v in row[unlabeled_col:unlabeled_col + 18]]
        except ValueError:
            continue

        frames_list.append(frame)
        times_list.append(t)
        markers_list.append(vals)

    if not frames_list:
        raise ValueError("No valid data rows found.")

    frames  = np.array(frames_list, dtype=int)
    times   = np.array(times_list,  dtype=float)
    markers = np.array(markers_list, dtype=float).reshape(-1, 6, 3)

    return frames, times, markers


# ---------------------------------------------------------------------------
# Thigh / shank identification
# ---------------------------------------------------------------------------

def _marker_3d_std(markers: np.ndarray, idx: int) -> float:
    """3D standard deviation of marker <idx> across all frames (NaN-safe)."""
    pts = markers[:, idx, :]          # (N, 3)
    valid = ~np.any(np.isnan(pts), axis=1)
    if valid.sum() < 5:
        return float("nan")
    return float(np.sqrt(np.sum(np.var(pts[valid], axis=0))))


def identify_segments(markers: np.ndarray, variance_ratio_thresh: float = 3.0):
    """
    Split 6 markers into thigh (low variance) and shank (high variance) groups.

    Returns
    -------
    thigh_idx : list[int]  — marker indices (0-5) belonging to thigh
    shank_idx : list[int]  — marker indices (0-5) belonging to shank
    """
    stds = [_marker_3d_std(markers, i) for i in range(6)]
    order = sorted(range(6), key=lambda i: stds[i])

    low_std  = np.nanmean([stds[i] for i in order[:3]])
    high_std = np.nanmean([stds[i] for i in order[3:]])

    if low_std < 1e-9 or (high_std / low_std) < variance_ratio_thresh:
        ratio = high_std / max(low_std, 1e-9)
        print(f"  WARNING: thigh/shank variance ratio {ratio:.1f}x < threshold "
              f"{variance_ratio_thresh:.1f}x — segment separation uncertain "
              f"(stds: {['%.4f' % s for s in stds]})")

    return order[:3], order[3:]


# ---------------------------------------------------------------------------
# Angle computation
# ---------------------------------------------------------------------------

def _pca_axis(pts: np.ndarray) -> np.ndarray:
    """
    Return the first principal component (unit vector along longest extent)
    of a set of 3-D points (shape (k, 3)).
    """
    centred = pts - pts.mean(axis=0)
    _, _, Vt = np.linalg.svd(centred, full_matrices=False)
    return Vt[0]  # first right singular vector = PC1


def _gravity_direction(markers: np.ndarray, thigh_idx: list[int]) -> np.ndarray:
    """
    Estimate the gravity (down) direction from the thigh marker cluster.
    The thigh is stationary, so we look at its mean position over the trial
    and assume gravity is anti-parallel to the vertical axis in the lab.
    Since OptiTrack axes vary, we pick the axis along which all thigh markers
    have the LOWEST variance (which is mostly pointing into the seat, not down).
    We then pick the axis with the HIGHEST mean Z-like value as up.

    Simple heuristic: gravity = -Y if Y is the tallest axis, -Z otherwise.
    Works for most OptiTrack coordinate conventions.
    """
    # Pick the lab axis whose mean value across thigh markers is largest
    # (that axis = 'up' in the lab)
    thigh_pts = markers[:, thigh_idx, :].reshape(-1, 3)
    valid = ~np.any(np.isnan(thigh_pts), axis=1)
    means = np.nanmean(thigh_pts[valid], axis=0)
    up_axis = int(np.argmax(np.abs(means)))
    g = np.zeros(3)
    g[up_axis] = -1.0   # gravity points down = negative of "up"
    return g


def compute_knee_angles(
    markers: np.ndarray,
    thigh_idx: list[int],
    shank_idx: list[int],
) -> np.ndarray:
    """
    Compute the deviation-from-extension angle for each frame.

    Output convention: 0° = fully extended (shank colinear with thigh),
    increasing as the knee flexes.  The caller (pt_score.py Format C loader)
    applies the transform ``180 - angle`` to convert to the standard interior
    angle convention (180° = full extension, decreasing with flexion).

    Strategy
    --------
    * Thigh long axis = first PC of thigh markers across the whole trial
      (stable because the thigh is nearly stationary).
    * Shank long axis per frame = first PC of the 3 shank markers at that
      frame, oriented so its positive end points away from the thigh centroid.
    * Angle = arccos( (−thigh_hat) · shank_hat ) where −thigh_hat is the
      proximal-to-distal thigh direction and shank_hat is the proximal-to-
      distal shank direction.  When the leg is straight these are parallel
      → dot = 1 → angle = 0°.

    Note: the per-frame 3-point PCA for the shank axis is sensitive to the
    marker arrangement within the cluster (triangular vs collinear placement).

    Returns (N,) degrees array (NaN where data missing).
    """
    N = markers.shape[0]
    angles = np.full(N, float("nan"))

    # Thigh long axis (static): PCA over all frames
    thigh_all = markers[:, thigh_idx, :].reshape(-1, 3)
    valid_thigh = ~np.any(np.isnan(thigh_all), axis=1)
    if valid_thigh.sum() < 10:
        return angles
    thigh_axis = _pca_axis(thigh_all[valid_thigh])  # unit vec, arbitrary sign

    # Mean thigh centroid (static reference for sign convention)
    thigh_centroid_mean = np.nanmean(thigh_all[valid_thigh], axis=0)

    for i in range(N):
        shank_pts = markers[i, shank_idx, :]    # (3, 3)
        thigh_pts = markers[i, thigh_idx, :]    # (3, 3)

        if np.any(np.isnan(shank_pts)) or np.any(np.isnan(thigh_pts)):
            continue

        # Shank long axis this frame
        shank_axis = _pca_axis(shank_pts)

        # Orient shank_axis: distal end should point AWAY from thigh centroid
        shank_centroid = shank_pts.mean(axis=0)
        distal_dir = shank_centroid - thigh_centroid_mean
        if np.dot(shank_axis, distal_dir) < 0:
            shank_axis = -shank_axis

        # Orient thigh_axis: proximal end points away from shank (upward)
        proximal_dir = thigh_centroid_mean - shank_centroid
        thigh_hat = thigh_axis.copy()
        if np.dot(thigh_hat, proximal_dir) < 0:
            thigh_hat = -thigh_hat

        # Hip-knee-ankle angle = angle between the two segment vectors
        # meeting at the knee (thigh pointing up, shank pointing down at rest)
        # We need the angle measured at the knee:
        #   proximal_thigh -> knee -> distal_shank
        # which equals the angle between (-thigh_hat) and shank_hat
        cos_a = np.clip(np.dot(-thigh_hat, shank_axis), -1.0, 1.0)
        angles[i] = math.degrees(math.acos(cos_a))

    return angles


# ---------------------------------------------------------------------------
# Main function
# ---------------------------------------------------------------------------

def compute_angles_from_optitrack_csv(
    csv_path: str,
    output_path: Optional[str] = None,
    variance_ratio_thresh: float = 3.0,
) -> str:
    """
    Process one OptiTrack CSV and write a knee-angle CSV.

    Returns the path of the written output file.
    """
    if output_path is None:
        base, _ = os.path.splitext(csv_path)
        output_path = base + "_computed_angles.csv"

    frames, times, markers = load_optitrack_csv(csv_path)

    thigh_idx, shank_idx = identify_segments(markers, variance_ratio_thresh)

    stds = [_marker_3d_std(markers, i) for i in range(6)]
    print("  Marker 3D std (m): %s" % ["%.4f" % s for s in stds])
    print("  Thigh markers: %s  Shank markers: %s" % (thigh_idx, shank_idx))

    angles = compute_knee_angles(markers, thigh_idx, shank_idx)

    valid = np.sum(~np.isnan(angles))
    print("  Valid angle frames: %d / %d" % (valid, len(angles)))
    if valid > 0:
        print("  Angle range: %.1f – %.1f deg" % (
            float(np.nanmin(angles)), float(np.nanmax(angles))))

    with open(output_path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["frame", "time_sec", "knee_angle_deg"])
        for fr, t, a in zip(frames, times, angles):
            w.writerow([fr, round(float(t), 6),
                        round(float(a), 4) if not math.isnan(a) else ""])

    print("  Written: %s" % output_path)
    return output_path


# ---------------------------------------------------------------------------
# Direct .tak binary reader (reads Channels.dat, no CSV export needed)
# ---------------------------------------------------------------------------

def _parse_channels_dat(raw: bytes):
    """
    Parse Channels.dat binary stream from an OptiTrack .tak file.

    Returns list of dicts with keys: type, name, n_frames, positions
    where positions is a list of (frame_idx, x, y, z) tuples for
    Vector3fChannel entries, or None for other types.

    Format (per channel):
        [optional 8-byte global magic at file start]
        [2-byte class name length][ASCII class name]
        [1-byte separator][2-byte channel name length][UTF-16LE name]
        [16-byte GUID][12-byte padding]
        [4-byte uint32 frame count]
        [frame count × entry bytes]
          Vector3fChannel: [uint32 frame_idx][float32 x][float32 y][float32 z]  = 16B
          QuaternionChannel: [uint32 frame_idx][float32 ×4]                     = 20B
          Float32Channel/FloatChannel: [uint32 frame_idx][float32]              = 8B
    """
    if len(raw) <= 10:
        return []

    pos = 0
    # Skip global 8-byte file header if present (magic f3 0a 39 4c)
    if raw[:4] == b"\xf3\x0a\x39\x4c":
        pos = 8

    channels = []
    while pos < len(raw) - 20:
        if pos + 2 > len(raw):
            break
        cl_len = struct.unpack_from("<H", raw, pos)[0]
        if cl_len < 5 or cl_len > 30 or pos + 2 + cl_len > len(raw):
            pos += 1
            continue
        cls_bytes = raw[pos + 2: pos + 2 + cl_len]
        if not cls_bytes.endswith(b"Channel"):
            pos += 1
            continue
        try:
            cls_name = cls_bytes.decode("ascii")
        except Exception:
            pos += 1
            continue

        pos += 2 + cl_len
        if pos + 3 > len(raw):
            break
        pos += 1  # separator byte
        ch_name_len = struct.unpack_from("<H", raw, pos)[0]
        pos += 2
        if ch_name_len < 1 or ch_name_len > 64 or pos + ch_name_len * 2 > len(raw):
            break
        try:
            ch_name = raw[pos: pos + ch_name_len * 2].decode("utf-16-le")
        except Exception:
            break
        pos += ch_name_len * 2

        # GUID (16B) + padding (12B) = 28B
        pos += 28
        if pos + 4 > len(raw):
            break

        n_frames = struct.unpack_from("<I", raw, pos)[0]
        pos += 4
        if n_frames < 1 or n_frames > 100_000:
            continue

        entry_size = {"Vector3fChannel": 16, "QuaternionChannel": 20,
                      "FloatChannel": 8, "Float32Channel": 8}.get(cls_name, None)
        if entry_size is None:
            break

        expected_bytes = n_frames * entry_size
        if pos + expected_bytes > len(raw):
            break

        ch = {"type": cls_name, "name": ch_name, "n_frames": n_frames, "positions": None}
        if cls_name == "Vector3fChannel":
            entries = []
            for _ in range(n_frames):
                fi, x, y, z = struct.unpack_from("<Ifff", raw, pos)
                entries.append((fi, x, y, z))
                pos += 16
            ch["positions"] = entries
        else:
            pos += expected_bytes  # skip non-position channels

        channels.append(ch)

    return channels


def load_optitrack_tak(tak_path: str):
    """
    Load 3D marker positions directly from the binary .tak file's Channels.dat.

    Returns
    -------
    frames  : (N,) int array
    times   : (N,) float array  (reconstructed from frame index, requires fps from MetaData)
    markers : (N, M, 3) float array  M = number of tracked markers (typically 6)
    fps     : float
    """
    try:
        import olefile  # type: ignore
    except ImportError:
        raise ImportError("olefile is required: pip install olefile")

    ole = olefile.OleFileIO(tak_path)

    # Read fps from MetaData.dat (JSON fragment contains "FrameRate")
    fps = 120.0
    try:
        meta_raw = ole.openstream("MetaData.dat").read()
        import json, re
        m = re.search(rb'"FrameRate"\s*:\s*([\d.]+)', meta_raw)
        if m:
            fps = float(m.group(1))
    except Exception:
        pass

    chan_raw = ole.openstream("Channels.dat").read()
    ole.close()

    channels = _parse_channels_dat(chan_raw)
    vec3_channels = [ch for ch in channels
                     if ch["type"] == "Vector3fChannel" and ch["positions"]]

    if not vec3_channels:
        raise ValueError("No Vector3fChannel data found in Channels.dat (empty?). "
                         "Reconstruction required — run Motive with license dongle.")

    # Drop channels where ALL frames have (0, 0, 0) — failed rigid body channels
    def _is_zero_channel(ch):
        xyz = [(x, y, z) for _, x, y, z in ch["positions"]]
        nonzero = [(x, y, z) for x, y, z in xyz if abs(x) + abs(y) + abs(z) > 1e-6]
        return len(nonzero) == 0

    marker_channels = [ch for ch in vec3_channels if not _is_zero_channel(ch)]

    if not marker_channels:
        raise ValueError("All Vector3fChannel entries are zero (rigid body solving failed). "
                         "Run Motive reconstruction with license dongle.")

    # Use first channel to determine frame range
    n_frames = marker_channels[0]["n_frames"]
    frame_indices = [fi for fi, _, _, _ in marker_channels[0]["positions"]]

    # Build markers array: (N_frames, N_markers, 3)
    n_markers = len(marker_channels)
    positions = np.full((n_frames, n_markers, 3), float("nan"))
    for mi, ch in enumerate(marker_channels):
        for row_i, (fi, x, y, z) in enumerate(ch["positions"]):
            if row_i < n_frames:
                positions[row_i, mi, :] = (x, y, z)

    frames = np.array(frame_indices, dtype=int)
    times = frames.astype(float) / fps

    return frames, times, positions, fps, n_markers


def compute_angles_from_tak(
    tak_path: str,
    output_path: Optional[str] = None,
    variance_ratio_thresh: float = 3.0,
) -> str:
    """
    Process one OptiTrack .tak file and write a knee-angle CSV directly from
    the binary Channels.dat (no CSV export step required).

    Returns the path of the written output file.
    """
    if output_path is None:
        base, _ = os.path.splitext(tak_path)
        output_path = base + "_computed_angles.csv"

    print("  Loading tak binary: %s" % os.path.basename(tak_path))
    frames, times, markers, fps, n_markers = load_optitrack_tak(tak_path)
    print("  Found %d marker channels, %d frames, %.1f Hz" % (n_markers, len(frames), fps))

    if n_markers < 6:
        print("  WARNING: only %d markers found (expected 6). Results may be unreliable." % n_markers)

    # Use first 6 markers if more than 6 (e.g., Motive tracked extras)
    markers_6 = markers[:, :6, :] if n_markers >= 6 else markers

    thigh_idx, shank_idx = identify_segments(markers_6, variance_ratio_thresh)

    stds = [_marker_3d_std(markers_6, i) for i in range(min(6, n_markers))]
    print("  Marker 3D std (m): %s" % ["%.4f" % s for s in stds])
    print("  Thigh markers: %s  Shank markers: %s" % (thigh_idx, shank_idx))

    angles = compute_knee_angles(markers_6, thigh_idx, shank_idx)

    valid = int(np.sum(~np.isnan(angles)))
    print("  Valid angle frames: %d / %d" % (valid, len(angles)))
    if valid > 0:
        print("  Angle range: %.1f – %.1f deg" % (
            float(np.nanmin(angles)), float(np.nanmax(angles))))

    with open(output_path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["frame", "time_sec", "knee_angle_deg"])
        for fr, t, a in zip(frames, times, angles):
            w.writerow([int(fr), round(float(t), 6),
                        round(float(a), 4) if not math.isnan(a) else ""])

    print("  Written: %s" % output_path)
    return output_path


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(
        description="Compute knee angles from OptiTrack CSV or .tak binary")
    ap.add_argument("input", help="OptiTrack .csv export or .tak binary path")
    ap.add_argument("output", nargs="?", default=None,
                    help="Output CSV path (default: <input>_computed_angles.csv)")
    args = ap.parse_args()

    if not os.path.isfile(args.input):
        print("ERROR: not found:", args.input, file=sys.stderr)
        sys.exit(1)

    if args.input.lower().endswith(".tak"):
        compute_angles_from_tak(args.input, args.output)
    else:
        compute_angles_from_optitrack_csv(args.input, args.output)


if __name__ == "__main__":
    main()
