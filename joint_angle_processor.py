"""
Knee Joint Angle Processor for the Popovic Pendulum Test
========================================================

Ingests an OptiTrack quaternion export (two rigid bodies), reconstructs the
knee joint-angle trajectory using gimbal-stable rigid-body kinematics, and
extracts the quantitative descriptors used in the Popovic / Bajd pendulum
test of spasticity.

Reference:
    Popovic D.B., Bajd T. (2018). "Pendulum Test: Quantified Assessment of the
    Type and Level of Spasticity in Persons with CNS Lesions."
    Serbian Journal of Electrical Engineering, 15(1), 1-12.
"""

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from scipy.interpolate import CubicSpline
from scipy.signal import find_peaks, medfilt

# ============================================================
# CONFIGURATION
# ============================================================
CSV_PATH = r"C:\Users\cladi\Downloads\ReTest_Solved.csv"

# Rigid-body names exactly as written in the OptiTrack header.
# BODY_DISTAL is the swinging segment (shank), BODY_PROXIMAL the reference (thigh).
BODY_DISTAL   = "Shin"
BODY_PROXIMAL = "TopThigh"

ROLLING_MEDIAN_WINDOW = 5      # frames; jitter-removal median filter (must be odd)

# --- Peak detection (operates on the rectified deviation from rest, in degrees) ---
PEAK_MIN_PROMINENCE = 3.0      # deg; ignore swings smaller than this
PEAK_MIN_DISTANCE_S = 0.15     # s ; minimum spacing between successive swing extrema

# --- Segment selection ---
# A contiguous (gap-free) block must contain at least this many detected swing
# extrema (peaks) to qualify as "pendulum motion". The qualifying segment with
# the most swings is analysed; if none qualify, we fall back to the longest one.
MIN_OSCILLATIONS = 2

# --- Rest / settling estimation ---
REST_WINDOW_S       = 1.5      # s ; trailing window used to estimate the resting angle
SETTLE_AMPLITUDE_DEG = 3.0     # deg; motion is considered "damped" below this amplitude

# --- Output ---
SHOW_PLOT = True
SAVE_PLOT_PATH = None          # e.g. r"C:\...\pendulum.png", or None to skip saving
# ============================================================


class RigidBodyNotFoundError(ValueError):
    """Raised when a requested rigid-body name is absent from the CSV header."""


class JointAngleProcessor:
    """
    Modular processor: parse -> interpolate -> kinematics -> filter -> features.

    Typical use:
        proc = JointAngleProcessor(CSV_PATH, BODY_DISTAL, BODY_PROXIMAL)
        proc.run()
    """

    def __init__(self, csv_path=CSV_PATH,
                 body_distal=BODY_DISTAL, body_proximal=BODY_PROXIMAL,
                 median_window=ROLLING_MEDIAN_WINDOW):
        self.csv_path = csv_path
        self.body_distal = body_distal
        self.body_proximal = body_proximal
        self.median_window = median_window if median_window % 2 == 1 else median_window + 1

        # Populated by the pipeline:
        self.segment = None       # slice of the best swing segment chosen by the
                                  # oscillation-aware find_best_swing_segment() logic
        self.time = None          # 1-D time vector (s) over the valid tracking window
        self.angle_raw = None     # joint angle (deg) before filtering
        self.angle = None         # joint angle (deg) after rolling-median filter
        self.features = None      # dict of the seven pendulum descriptors

    # ----------------------------------------------------------------
    # 1. DYNAMIC HEADER PARSING
    # ----------------------------------------------------------------
    def _read_raw(self):
        """Read every row as raw strings, preserving blank lines."""
        return pd.read_csv(self.csv_path, header=None, dtype=str,
                           skip_blank_lines=False).values.tolist()

    def _locate_header_rows(self, rows):
        """
        Scan the multi-row OptiTrack header to find:
          - the 'component' row  (cells: Frame, Time, X, Y, Z, W, ...)
          - the 'type' row       (cells: Rotation / Position)
          - the 'name' row       (cells: rigid-body names)
        Returns (name_row, type_row, comp_row, data_start_index).
        Nothing is hard-coded to a fixed line number.
        """
        def cell(v):
            return ("" if v is None else str(v)).strip()

        comp_idx = None
        for i, r in enumerate(rows):
            if r and cell(r[0]).lower() == "frame":
                comp_idx = i
                break
        if comp_idx is None:
            raise ValueError("Could not locate the 'Frame' header row in the CSV.")

        comp_row = [cell(v) for v in rows[comp_idx]]

        # The 'type' (Rotation/Position) row is the nearest row above comp_row
        # that contains the word 'Rotation'.
        type_idx = None
        for i in range(comp_idx - 1, -1, -1):
            if any("rotation" in cell(v).lower() for v in rows[i]):
                type_idx = i
                break
        if type_idx is None:
            raise ValueError("Could not locate the 'Rotation/Position' header row.")
        type_row = [cell(v) for v in rows[type_idx]]

        # The 'name' row is the nearest row above type_row that names a rigid body.
        name_idx = None
        for i in range(type_idx - 1, -1, -1):
            joined = " ".join(cell(v) for v in rows[i]).lower()
            if self.body_distal.lower() in joined or self.body_proximal.lower() in joined:
                name_idx = i
                break
        if name_idx is None:
            raise ValueError("Could not locate the rigid-body name header row.")
        name_row = [cell(v) for v in rows[name_idx]]

        return name_row, type_row, comp_row, comp_idx + 1

    def _quat_columns(self, name_row, type_row, comp_row, body):
        """
        Return the column indices for body's rotation quaternion in (x, y, z, w)
        order. Raises RigidBodyNotFoundError if the body is missing.
        """
        if not any(c.lower() == body.lower() for c in name_row):
            present = sorted({c for c in name_row if c})
            raise RigidBodyNotFoundError(
                f"Rigid body '{body}' not found in CSV header. "
                f"Bodies present: {present}")

        wanted = {"x": None, "y": None, "z": None, "w": None}
        for col, (nm, tp, comp) in enumerate(zip(name_row, type_row, comp_row)):
            if nm.lower() != body.lower():
                continue
            if tp.lower() != "rotation":
                continue
            key = comp.lower()
            if key in wanted:
                wanted[key] = col

        missing = [k for k, v in wanted.items() if v is None]
        if missing:
            raise RigidBodyNotFoundError(
                f"Rigid body '{body}' is missing rotation component(s) "
                f"{missing} in the CSV header.")
        return [wanted["x"], wanted["y"], wanted["z"], wanted["w"]]

    def _time_column(self, comp_row):
        for col, c in enumerate(comp_row):
            if "time" in c.lower():
                return col
        raise ValueError("Could not locate the timestamp column.")

    def load(self):
        """Parse the CSV and store time + raw quaternion arrays for both bodies."""
        rows = self._read_raw()
        name_row, type_row, comp_row, data_start = self._locate_header_rows(rows)

        t_col = self._time_column(comp_row)
        cols_d = self._quat_columns(name_row, type_row, comp_row, self.body_distal)
        cols_p = self._quat_columns(name_row, type_row, comp_row, self.body_proximal)

        # Build a numeric frame from the data rows (skip blank rows).
        data_rows = [r for r in rows[data_start:] if r and any(str(v).strip() for v in r)]
        data = pd.DataFrame(data_rows).apply(pd.to_numeric, errors="coerce")

        self._t_full = data.iloc[:, t_col].to_numpy(float)
        self._quat_d_full = data.iloc[:, cols_d].to_numpy(float)  # x,y,z,w
        self._quat_p_full = data.iloc[:, cols_p].to_numpy(float)
        return self

    # ----------------------------------------------------------------
    # 2. MISSING-DATA INTERPOLATION (Cubic Spline)
    # ----------------------------------------------------------------
    @staticmethod
    def _interpolate_gaps(t, arr):
        """
        Fill internal NaNs in each column of `arr` with a cubic spline fitted
        to the valid samples. Leading/trailing NaNs cannot be interpolated
        (they would require extrapolation) and are left as NaN for the caller
        to trim.
        """
        out = arr.copy()
        for j in range(arr.shape[1]):
            col = arr[:, j]
            valid = ~np.isnan(col)
            if valid.sum() < 2:
                raise ValueError(f"Column {j} has too few valid samples to interpolate.")
            cs = CubicSpline(t[valid], col[valid])
            # Only fill gaps that lie *between* the first and last valid sample.
            lo, hi = np.where(valid)[0][[0, -1]]
            internal = (~valid) & (np.arange(len(col)) > lo) & (np.arange(len(col)) < hi)
            out[internal, j] = cs(t[internal])
        return out

    def _all_contiguous_segments(self):
        """
        Return every *gap-free* block (as a slice) where BOTH rigid bodies are
        tracked, ordered as they occur in the recording.
        """
        # Boolean mask: True where BOTH bodies are tracked.
        mask = (~np.isnan(self._quat_d_full).any(axis=1)) & \
               (~np.isnan(self._quat_p_full).any(axis=1))

        # Locate the rising/falling edges of contiguous True runs.
        # prepend/append 0 guarantees edges are detected even at the array ends.
        diff = np.diff(mask.astype(int), prepend=0, append=0)
        starts = np.where(diff == 1)[0]
        ends = np.where(diff == -1)[0]      # exclusive end indices
        return [slice(int(s), int(e)) for s, e in zip(starts, ends)]

    def find_best_swing_segment(self):
        """
        Select the contiguous segment that best captures the pendulum motion.

        Strategy:
          1. Scan every gap-free segment and count its detected swing peaks.
          2. Among segments with at least MIN_OSCILLATIONS peaks, return the one
             with the most peaks (the richest oscillatory block).
          3. If no segment qualifies, fall back to the longest segment overall.

        Returns a slice over the data rows.
        """
        segments = self._all_contiguous_segments()
        if not segments:
            raise ValueError("No continuous tracking segment found "
                             "(both bodies are never tracked simultaneously).")

        best_seg, best_key = None, (-1, -1)
        for sl in segments:
            # Need enough samples to median-filter and to define a swing.
            if (sl.stop - sl.start) < max(self.median_window, 2):
                continue
            _, filt = self._segment_angle(sl)
            peaks, _, _, _ = self._find_swing_peaks(filt, self._t_full[sl])
            if peaks.size < MIN_OSCILLATIONS:
                continue
            # Rank by swing count first, then by segment length (tie-break:
            # a longer block is more likely the genuine swing than a brief burst).
            key = (peaks.size, sl.stop - sl.start)
            if key > best_key:
                best_seg, best_key = sl, key

        if best_seg is not None:
            return best_seg

        # Fallback: the longest contiguous block (may just be a static hold).
        return max(segments, key=lambda s: s.stop - s.start)

    # ----------------------------------------------------------------
    # 3. RIGID-BODY KINEMATICS
    # ----------------------------------------------------------------
    @staticmethod
    def _quat_to_local_x(q):
        """
        Rotate the body's local X-axis into the global frame.
        q is (N, 4) in (x, y, z, w) order.
            v_x = [1 - 2(y^2 + z^2), 2(xy + zw), 2(xz - yw)]
        Returns (N, 3) unit vectors.
        """
        x, y, z, w = q[:, 0], q[:, 1], q[:, 2], q[:, 3]
        vx = np.stack([
            1.0 - 2.0 * (y * y + z * z),
            2.0 * (x * y + z * w),
            2.0 * (x * z - y * w),
        ], axis=1)
        norm = np.linalg.norm(vx, axis=1, keepdims=True)
        norm[norm == 0] = 1.0
        return vx / norm

    @staticmethod
    def _vector_angle(v_a, v_b):
        """
        Gimbal-stable angle between two vector streams:
            theta = atan2(||v_a x v_b||, v_a . v_b)
        Returns angle in degrees, (N,).
        """
        cross = np.cross(v_a, v_b)
        dot = np.sum(v_a * v_b, axis=1)
        return np.degrees(np.arctan2(np.linalg.norm(cross, axis=1), dot))

    def _segment_angle(self, sl):
        """
        Compute the joint-angle trace for one contiguous segment:
            interpolate -> local-X vectors -> angle -> rolling-median filter.
        Cubic-spline interpolation is applied ONLY within this segment, so it
        never bridges an occlusion. Returns (raw_angle, filtered_angle).
        """
        t = self._t_full[sl]
        q_d = self._interpolate_gaps(t, self._quat_d_full[sl])
        q_p = self._interpolate_gaps(t, self._quat_p_full[sl])

        v_d = self._quat_to_local_x(q_d)
        v_p = self._quat_to_local_x(q_p)
        angle = self._vector_angle(v_d, v_p)

        # 5-frame rolling median to suppress tracking jitter.
        filt = medfilt(angle, kernel_size=self.median_window)
        return angle, filt

    def compute_joint_angle(self):
        """
        Kinematic chain restricted to the segment that best captures the swing.

        The joint angle is computed ONLY over the contiguous block selected by
        find_best_swing_segment(), so the Popovic parameters reflect genuine
        pendulum motion rather than interpolated occlusion gaps.
        """
        sl = self.find_best_swing_segment()
        self.segment = sl   # exposed for inspection / debugging
        self.time = self._t_full[sl]
        self.angle_raw, self.angle = self._segment_angle(sl)
        return self.angle

    # ----------------------------------------------------------------
    # 4. POPOVIC PENDULUM FEATURE EXTRACTION
    # ----------------------------------------------------------------
    @staticmethod
    def _find_swing_peaks(angles, time):
        """
        Shared swing detector used both for segment selection and feature
        extraction. Estimates the resting angle, rectifies the deviation so that
        every swing (flexion or extension) becomes a positive peak, and returns:
            (peaks, properties, resting_angle, rectified_deviation).
        """
        angles = np.asarray(angles, float)
        time = np.asarray(time, float)
        dt = float(np.median(np.diff(time)))
        min_dist = max(1, int(round(PEAK_MIN_DISTANCE_S / dt)))

        # Resting angle: the angle the leg settles to once damped.
        n_rest = max(1, int(round(REST_WINDOW_S / dt)))
        resting_angle = float(np.median(angles[-n_rest:]))

        # Rectified deviation: every swing (flex or extend) becomes a peak.
        rectified = np.abs(angles - resting_angle)
        peaks, props = find_peaks(rectified,
                                  prominence=PEAK_MIN_PROMINENCE,
                                  distance=min_dist)
        return peaks, props, resting_angle, rectified

    def extract_pendulum_features(self, angles, time):
        """
        Extract the seven pendulum-test descriptors from a knee-angle trace.

        Returns a dict with:
            peak_amplitude_first_swing_deg
            num_oscillations
            mean_oscillation_frequency_hz
            total_motion_duration_s
            log_decay_rate_per_s
            resting_angle_deg
            first_swing_duration_s
        """
        time = np.asarray(time, float)
        peaks, props, resting_angle, rectified = self._find_swing_peaks(angles, time)

        # Defensive: if no oscillation is detected, return zeros/NaNs gracefully.
        if peaks.size == 0:
            self.features = dict(
                peak_amplitude_first_swing_deg=0.0,
                num_oscillations=0,
                mean_oscillation_frequency_hz=float("nan"),
                total_motion_duration_s=0.0,
                log_decay_rate_per_s=float("nan"),
                resting_angle_deg=resting_angle,
                first_swing_duration_s=0.0,
                _peaks=peaks,
            )
            return self.features

        peak_times = time[peaks]
        peak_amps = rectified[peaks]

        # 1) Peak amplitude of the 1st swing (deg, relative to rest).
        first_amp = float(peak_amps[0])

        # 2) Number of oscillations before rest: count swing extrema whose
        #    amplitude is still above the settling threshold.
        active = peak_amps >= SETTLE_AMPLITUDE_DEG
        # Each successive extremum (flex, extend, flex, ...) is a half cycle.
        num_half_swings = int(active.sum())
        num_oscillations = num_half_swings / 2.0

        # 3) Mean oscillation frequency: successive extrema are half a period
        #    apart, so period = 2 * mean(inter-peak interval).
        if peaks.size >= 2:
            half_periods = np.diff(peak_times)
            mean_period = 2.0 * float(np.mean(half_periods))
            mean_freq = 1.0 / mean_period if mean_period > 0 else float("nan")
        else:
            mean_freq = float("nan")

        # 4) Total duration of motion: release (t0) until the swings damp out
        #    (last extremum above the settling threshold).
        t0 = float(time[0])
        active_idx = np.where(active)[0]
        last_active_peak = peaks[active_idx[-1]] if active_idx.size else peaks[0]
        total_duration = float(time[last_active_peak] - t0)

        # 5) Logarithmic decay rate of the peak amplitudes.
        #    Fit ln(amplitude) = ln(C) - sigma * t  ->  slope = -sigma.
        if peaks.size >= 2 and np.all(peak_amps > 0):
            slope = np.polyfit(peak_times, np.log(peak_amps), 1)[0]
            log_decay_rate = float(-slope)            # 1/s, positive = decaying
        else:
            log_decay_rate = float("nan")

        # 6) Resting angle offset (deg).
        # (computed above)

        # 7) Duration of the 1st swing: release -> first extremum (first half period).
        first_swing_duration = float(peak_times[0] - t0)

        self.features = dict(
            peak_amplitude_first_swing_deg=first_amp,
            num_oscillations=num_oscillations,
            mean_oscillation_frequency_hz=mean_freq,
            total_motion_duration_s=total_duration,
            log_decay_rate_per_s=log_decay_rate,
            resting_angle_deg=resting_angle,
            first_swing_duration_s=first_swing_duration,
            _peaks=peaks,            # kept for plotting; not a reported parameter
        )
        return self.features

    # ----------------------------------------------------------------
    # 5. ORCHESTRATION + VISUALISATION
    # ----------------------------------------------------------------
    def run(self):
        """End-to-end: load -> angle -> features -> plot -> print."""
        self.load()
        self.compute_joint_angle()
        feats = self.extract_pendulum_features(self.angle, self.time)

        self._print_report(feats)
        self._plot(feats)
        return feats

    def print_segment_diagnostics(self):
        """
        Print a per-segment summary of the contiguous tracking blocks: how many
        were found, each one's time span/duration, and how many swing extrema it
        contains. Useful for confirming after a capture that the marker setup
        produced a single, long, occlusion-free swing rather than many short
        fragments. Requires load() to have been called first.
        """
        if getattr(self, "_t_full", None) is None:
            raise RuntimeError("Call load() before print_segment_diagnostics().")

        segments = self._all_contiguous_segments()
        chosen = self.find_best_swing_segment() if segments else None
        min_len = max(self.median_window, 2)

        print("\n" + "=" * 72)
        print(f" SEGMENT DIAGNOSTICS  -  {self.body_distal} vs {self.body_proximal}")
        print("=" * 72)
        print(f" Contiguous tracking segments found: {len(segments)}")
        print(f" {'#':>2}  {'frames':>6}  {'start_s':>8}  {'end_s':>8}  "
              f"{'dur_s':>7}  {'swings':>6}   note")
        print(" " + "-" * 70)
        max_swings = 0
        for i, sl in enumerate(segments):
            n = sl.stop - sl.start
            t0 = float(self._t_full[sl.start])
            t1 = float(self._t_full[sl.stop - 1])
            if n < min_len:
                swings = "n/a"
            else:
                _, filt = self._segment_angle(sl)
                peaks, _, _, _ = self._find_swing_peaks(filt, self._t_full[sl])
                swings = int(peaks.size)
                max_swings = max(max_swings, swings)
            mark = "<-- CHOSEN" if chosen is not None and \
                   sl == chosen else ""
            print(f" {i:>2}  {n:>6}  {t0:>8.3f}  {t1:>8.3f}  "
                  f"{t1 - t0:>7.3f}  {str(swings):>6}   {mark}")
        print("=" * 72)
        if chosen is not None:
            # A real swing segment was found only if some block met the threshold;
            # otherwise find_best_swing_segment() returned the longest-block fallback.
            qualifies = max_swings >= MIN_OSCILLATIONS
            note = ("qualifying swing segment"
                    if qualifies else "FALLBACK - longest block only, NO clean swing")
            print(f" Selected for analysis: frames {chosen.start}-{chosen.stop} "
                  f"({note}).")
            print(f" MIN_OSCILLATIONS threshold = {MIN_OSCILLATIONS}.")
        print("=" * 72 + "\n")

    def _print_report(self, feats):
        print("\n" + "=" * 60)
        print(" POPOVIC PENDULUM TEST  -  Knee Joint Angle Features")
        print("=" * 60)
        labels = [
            ("peak_amplitude_first_swing_deg", "1) Peak amplitude, 1st swing", "deg"),
            ("num_oscillations",               "2) Number of oscillations",    ""),
            ("mean_oscillation_frequency_hz",  "3) Mean oscillation frequency","Hz"),
            ("total_motion_duration_s",        "4) Total motion duration",     "s"),
            ("log_decay_rate_per_s",           "5) Logarithmic decay rate",    "1/s"),
            ("resting_angle_deg",              "6) Resting angle offset",      "deg"),
            ("first_swing_duration_s",         "7) Duration of 1st swing",     "s"),
        ]
        for key, label, unit in labels:
            val = feats[key]
            print(f"   {label:<34} {val:10.4f} {unit}")
        print("=" * 60 + "\n")

    def _plot(self, feats):
        peaks = feats.get("_peaks", np.array([], dtype=int))
        fig, ax = plt.subplots(figsize=(11, 5))
        ax.plot(self.time, self.angle_raw, color="0.8", lw=0.8,
                label="Raw angle")
        ax.plot(self.time, self.angle, color="C0", lw=1.4,
                label=f"Filtered ({self.median_window}-frame median)")
        ax.axhline(feats["resting_angle_deg"], color="C3", ls="--", lw=1.0,
                   label=f"Resting angle ({feats['resting_angle_deg']:.1f} deg)")
        if peaks.size:
            ax.plot(self.time[peaks], self.angle[peaks], "v", color="C1",
                    ms=8, label="Detected swing extrema")
        ax.set_xlabel("Time (s)")
        ax.set_ylabel("Knee joint angle (deg)")
        ax.set_title(f"Pendulum Test  -  {self.body_distal} vs {self.body_proximal}")
        ax.legend(loc="best", fontsize=9)
        ax.grid(alpha=0.3)
        fig.tight_layout()
        if SAVE_PLOT_PATH:
            fig.savefig(SAVE_PLOT_PATH, dpi=150)
            print(f"Plot saved to {SAVE_PLOT_PATH}")
        if SHOW_PLOT:
            plt.show()
        return fig


if __name__ == "__main__":
    JointAngleProcessor(CSV_PATH, BODY_DISTAL, BODY_PROXIMAL).run()
