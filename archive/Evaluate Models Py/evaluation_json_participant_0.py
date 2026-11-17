"""
evaluation_json_participant_0.py
================================
Pendulum Test - HPE Model Evaluation vs OptiTrack Gold Standard
Participant 0 / Trial 1

Fixes applied vs previous version:
  - No np.abs(): signals oscillate naturally through positive and negative
  - Correct hold window: 0.0 - 4.0 s (fully extended at START of trial)
  - Robust JSON key matching for MediaPipe / RTMPose / MMPose
  - 4th-order zero-phase Butterworth low-pass at 6 Hz (filtfilt)
  - Adaptive peak-detection distance based on estimated sampling rate
"""

import os
import json
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from scipy.signal import find_peaks, butter, filtfilt

# ==============================================================================
# 1. PATH CONFIGURATION
# ==============================================================================
OPTITRACK_CSV  = r"C:\Users\cladi\Pendulastic\OptiTrack_Recordings\Participant_0\Position_2\Height_Joint-Level\trial_1_optitrack.csv"
EVALUATION_JSON = r"C:\Users\cladi\OneDrive\Desktop\Participant_0\Results\evaluation_results.json"
OUTPUT_PLOT_PATH = r"C:\Users\cladi\OneDrive\Desktop\Participant_0\Results\Participant_0_Kinematic_Comparison.png"

HOLD_START    = 0.0   # Fully extended hold begins at trial start
HOLD_END      = 4.0   # Pendulum released at ~4.2 s
POST_RELEASE  = 4.0   # Everything after this is the swing phase

FILTER_ORDER  = 4
FILTER_CUTOFF = 6.0   # Hz - removes keypoint jitter without phase shift

# ==============================================================================
# 2. SIGNAL PROCESSING HELPERS
# ==============================================================================
def estimate_fs(times):
    """Estimate sampling rate from median inter-sample interval."""
    dts = np.diff(times)
    valid = dts[dts > 0]
    return 1.0 / np.median(valid) if len(valid) > 0 else 100.0


def butter_lowpass_filtfilt(signal, cutoff_hz, fs_hz, order=4):
    """Zero-phase 4th-order Butterworth low-pass filter via filtfilt."""
    nyq = 0.5 * fs_hz
    if cutoff_hz >= nyq:
        print(f"  [warn] Cutoff {cutoff_hz} Hz >= Nyquist {nyq:.1f} Hz; filter skipped.")
        return signal
    b, a = butter(order, cutoff_hz / nyq, btype='low', analog=False)
    min_samples = 3 * max(len(a), len(b))
    if len(signal) < min_samples:
        print(f"  [warn] Signal too short ({len(signal)} samples) for filtfilt; filter skipped.")
        return signal
    return filtfilt(b, a, signal)


def condition_signal(times, angles, label="signal"):
    """
    Full conditioning pipeline for one signal:
      1. Estimate fs from time array.
      2. Apply zero-phase Butterworth low-pass at FILTER_CUTOFF Hz.
      3. Compute mean during the hold window (HOLD_START..HOLD_END) and
         subtract it so that fully extended = 0 deg.
         -- NO np.abs() --  Oscillations go both positive and negative.
    Returns (times, calibrated_angles).
    """
    fs = estimate_fs(times)
    print(f"  [{label}] fs = {fs:.1f} Hz  |  {len(times)} samples")

    filtered = butter_lowpass_filtfilt(angles, FILTER_CUTOFF, fs, order=FILTER_ORDER)

    hold_mask = (times >= HOLD_START) & (times <= HOLD_END)
    if hold_mask.any():
        baseline = np.mean(filtered[hold_mask])
    else:
        baseline = filtered[0]
        print(f"  [{label}] Warning: no samples in hold window; using t[0] as baseline.")
    print(f"  [{label}] Hold-window baseline = {baseline:.3f} deg  =>  subtracted")

    return times, filtered - baseline


# ==============================================================================
# 3. OPTITRACK CSV LOADER
# ==============================================================================
def _knee_angle_from_2d(df):
    """
    Compute knee flexion from 2D pixel coordinates (hip/knee/ankle).
    v1 = Hip - Knee,  v2 = Ankle - Knee
    angle = arctan2(v1.x*v2.y - v1.y*v2.x,  v1.x*v2.x + v1.y*v2.y)
    Returns angle in degrees.
    """
    lo = {c.lower(): c for c in df.columns}

    def col(name):
        return pd.to_numeric(df[lo[name]], errors='coerce').values

    v1x = col('hip_x')   - col('knee_x')
    v1y = col('hip_y')   - col('knee_y')
    v2x = col('ankle_x') - col('knee_x')
    v2y = col('ankle_y') - col('knee_y')

    return np.degrees(np.arctan2(v1x * v2y - v1y * v2x,
                                  v1x * v2x + v1y * v2y))


def load_optitrack_csv(csv_path):
    """
    1. Find the 'Frame' header row to skip Motive metadata.
    2. If 2D coordinate columns (hip_x/y, knee_x/y, ankle_x/y) exist,
       compute the knee angle via the arctan2 vector method.
    3. Otherwise look for a named angle column (knee_angle_deg / angle).
    4. Fall back to brute-force variance: pick the numeric column with the
       largest variance in the post-release window (highest physical swing).
    Returns (zeroed_times, raw_angle_deg).
    """
    print(f"\n[OptiTrack] Loading: {csv_path}")

    # Locate the 'Frame' header row
    header_idx = None
    with open(csv_path, 'r', encoding='utf-8-sig') as f:
        for i, line in enumerate(f):
            tokens = [t.strip() for t in line.split(',')]
            if tokens and tokens[0].strip().lower() == 'frame':
                header_idx = i
                break

    if header_idx is None:
        raise ValueError("[OptiTrack] Could not find the 'Frame' header row.")

    df = pd.read_csv(csv_path, skiprows=header_idx)
    df.columns = [c.strip() for c in df.columns]
    cols_lo = {c.lower(): c for c in df.columns}

    # --- Time ---
    time_candidates = ('Time', 'Time (Seconds)', 'time', 'timestamp', 'Time_Seconds')
    time_col = next((c for c in time_candidates if c in df.columns), None)
    if time_col is None:
        time_col = df.columns[1]   # second column is always time in Motive exports
    raw_times = pd.to_numeric(df[time_col], errors='coerce').ffill().bfill().values
    zeroed_times = raw_times - raw_times[0]
    print(f"  Duration: {zeroed_times[-1]:.2f} s  |  {len(zeroed_times)} samples")

    # --- Angle ---
    coord_keys = {'hip_x', 'hip_y', 'knee_x', 'knee_y', 'ankle_x', 'ankle_y'}
    if coord_keys.issubset(set(cols_lo.keys())):
        print("  2D coordinate columns detected -> computing angle via arctan2.")
        angle = _knee_angle_from_2d(df)

    else:
        named_candidates = ('knee_angle_deg', 'knee_angle', 'angle', 'knee_flexion',
                            'knee_flexion_deg')
        named_col = next((cols_lo[c] for c in named_candidates if c in cols_lo), None)
        if named_col:
            print(f"  Named angle column found: '{named_col}'")
            angle = pd.to_numeric(df[named_col], errors='coerce').ffill().bfill().values
        else:
            # Brute-force: highest post-release variance
            post_mask = zeroed_times > POST_RELEASE
            best_col, max_var = None, -1.0
            for col in df.columns[2:]:
                series = pd.to_numeric(df[col], errors='coerce').ffill().bfill().values
                v = np.var(series[post_mask]) if post_mask.any() else np.var(series)
                if v > max_var:
                    max_var, best_col = v, col
            print(f"  Variance-selected column: '{best_col}'  (var={max_var:.2f})")
            angle = pd.to_numeric(df[best_col], errors='coerce').ffill().bfill().values

    return zeroed_times, angle


# ==============================================================================
# 4. EVALUATION JSON LOADER
# ==============================================================================
# Maps every expected spelling variant to a canonical display name.
_JSON_KEY_MAP = {
    "mediapipe":   "MediaPipe",
    "media_pipe":  "MediaPipe",
    "rtmpose":     "RTMPose",
    "rtm_pose":    "RTMPose",
    "mmpose":      "MMPose",
    "mm_pose":     "MMPose",
}
MODEL_ORDER = ["MediaPipe", "RTMPose", "MMPose"]


def load_evaluation_json(json_path):
    """
    Parse evaluation_results.json, locate the Trial 1 entry, and return a
    dict keyed by canonical model names (MediaPipe / RTMPose / MMPose).
    """
    print(f"\n[JSON] Loading: {json_path}")
    with open(json_path, 'r') as f:
        data = json.load(f)

    # Locate Trial 1
    models_raw = {}
    for entry in data.get('trials', []):
        trial_val = str(entry.get('trial', '')).strip().lower()
        if trial_val in ('1', 'trial_1', 'trial1') or trial_val.endswith('_1'):
            models_raw = entry.get('models', {})
            print(f"  Found Trial 1 entry with {len(models_raw)} model(s).")
            break

    if not models_raw:
        first = (data.get('trials') or [{}])[0]
        models_raw = first.get('models', {})
        print(f"  Warning: no 'trial=1' entry found; falling back to first entry "
              f"({len(models_raw)} model(s)).")

    # Normalise keys
    models = {}
    for raw_key, payload in models_raw.items():
        canonical = _JSON_KEY_MAP.get(raw_key.strip().lower())
        if canonical:
            models[canonical] = payload
            print(f"  Mapped '{raw_key}' -> '{canonical}'")
        else:
            models[raw_key] = payload

    return models


# ==============================================================================
# 5. PEAK DETECTION
# ==============================================================================
def find_two_peaks(times, angles, label="signal"):
    """
    Return global indices of the 1st and 2nd major peaks in the post-release
    swing phase (t > POST_RELEASE).

    Checks both positive and negative lobes and uses the set with larger
    mean prominence, so it works regardless of the sign convention of the
    angle calculation.  Peak-to-peak minimum distance is adaptive: 0.3 s.
    """
    post_mask = times > POST_RELEASE
    if not post_mask.any():
        print(f"  [{label}] Warning: no samples after t={POST_RELEASE} s.")
        return np.array([], dtype=int)

    post_idx   = np.where(post_mask)[0]
    post_angle = angles[post_idx]
    post_times = times[post_idx]

    fs_est   = estimate_fs(post_times)
    min_dist = max(10, int(0.3 * fs_est))   # 300 ms minimum between peaks

    pos_peaks, pos_props = find_peaks( post_angle, distance=min_dist, prominence=3.0)
    neg_peaks, neg_props = find_peaks(-post_angle, distance=min_dist, prominence=3.0)

    if len(pos_peaks) == 0 and len(neg_peaks) == 0:
        print(f"  [{label}] Warning: no peaks detected post-release.")
        return np.array([], dtype=int)

    pos_prom = np.mean(pos_props['prominences']) if len(pos_peaks) > 0 else 0.0
    neg_prom = np.mean(neg_props['prominences']) if len(neg_peaks) > 0 else 0.0

    # Take the first two peaks of the dominant lobe
    local_peaks = pos_peaks if pos_prom >= neg_prom else neg_peaks
    global_peaks = post_idx[local_peaks[:2]]

    peak_angles = angles[global_peaks]
    peak_times  = times[global_peaks]
    print(f"  [{label}] Peaks found: {len(global_peaks)}"
          f"  |  t = {np.round(peak_times, 2)}  |  angle = {np.round(peak_angles, 2)} deg")
    return global_peaks


# ==============================================================================
# 6. MAIN PIPELINE
# ==============================================================================
print("=" * 70)
print("Pendulum Test  --  HPE Model Evaluation  (Participant 0, Trial 1)")
print("=" * 70)

# --- Load ---
print("\n--- Step 1: Load raw data ---")
opti_time_raw, opti_angle_raw = load_optitrack_csv(OPTITRACK_CSV)
json_models = load_evaluation_json(EVALUATION_JSON)

# --- Condition OptiTrack ---
print("\n--- Step 2: Condition OptiTrack signal ---")
opti_time, opti_angle = condition_signal(opti_time_raw, opti_angle_raw, label="OptiTrack")

# --- Condition model signals ---
print("\n--- Step 3: Condition HPE model signals ---")
model_data = {}
for model_name in MODEL_ORDER:
    payload = json_models.get(model_name)
    if payload is None:
        print(f"  [{model_name}] Not found in JSON - skipping.")
        continue

    t_raw = np.array(payload.get("time_sec",       payload.get("time",  [])), dtype=float)
    a_raw = np.array(payload.get("knee_angle_deg",  payload.get("angle", [])), dtype=float)

    if len(t_raw) == 0 or len(a_raw) == 0:
        print(f"  [{model_name}] Empty arrays - skipping.")
        continue

    t_zeroed = t_raw - t_raw[0]   # align to t = 0 independently
    t_cond, a_cond = condition_signal(t_zeroed, a_raw, label=model_name)
    model_data[model_name] = {"time": t_cond, "angle": a_cond}

# --- Peak detection ---
print("\n--- Step 4: Peak detection ---")
opti_peaks = find_two_peaks(opti_time, opti_angle, label="OptiTrack")

for model_name, md in model_data.items():
    md["peaks"] = find_two_peaks(md["time"], md["angle"], label=model_name)

# ==============================================================================
# 7. CONSOLE METRICS TABLE
# ==============================================================================
print("\n" + "=" * 75)
print(f"  OSCILLATION ERROR SUMMARY  (Delta = Model - OptiTrack)")
print("=" * 75)

for swing_idx in range(2):
    if swing_idx >= len(opti_peaks):
        print(f"  Swing {swing_idx + 1}: OptiTrack peak not detected.")
        continue

    g = opti_peaks[swing_idx]
    opti_val = opti_angle[g]
    opti_t   = opti_time[g]

    print(f"\n  Swing {swing_idx + 1}  --  OptiTrack: {opti_val:+.2f} deg  at  t = {opti_t:.2f} s")
    print(f"  {'-' * 55}")
    print(f"  {'Model':<12}  {'Angle (deg)':>12}  {'Delta (deg)':>12}  {'t_peak (s)':>12}")
    print(f"  {'-' * 55}")

    for model_name, md in model_data.items():
        m_peaks = md["peaks"]
        if len(m_peaks) == 0:
            print(f"  {model_name:<12}  {'no peak':>12}  {'---':>12}  {'---':>12}")
            continue
        # Temporally closest model peak to this OptiTrack peak
        best_idx = m_peaks[np.argmin(np.abs(md["time"][m_peaks] - opti_t))]
        m_val    = md["angle"][best_idx]
        m_t      = md["time"][best_idx]
        delta    = m_val - opti_val
        print(f"  {model_name:<12}  {m_val:>+11.2f}  {delta:>+11.2f}  {m_t:>11.2f}")
        md[f"matched_peak_swing{swing_idx + 1}"] = best_idx   # save for plot

print("\n" + "=" * 75)

# ==============================================================================
# 8. VISUALIZATION
# ==============================================================================
MODEL_STYLES = {
    "MediaPipe": {"color": "#1f77b4", "linestyle": "--",  "marker": "^", "ms": 90},
    "RTMPose":   {"color": "#ff7f0e", "linestyle": "-.",  "marker": "s", "ms": 80},
    "MMPose":    {"color": "#2ca02c", "linestyle": ":",   "marker": "D", "ms": 75},
}

fig, ax = plt.subplots(figsize=(14, 7))

# OptiTrack
ax.plot(opti_time, opti_angle,
        color="black", linewidth=2.5, zorder=4,
        label="OptiTrack (Gold Standard)")
if len(opti_peaks) > 0:
    ax.scatter(opti_time[opti_peaks], opti_angle[opti_peaks],
               color="black", s=160, edgecolors="white", zorder=6,
               label="OptiTrack peaks")

# Models
for model_name, md in model_data.items():
    sty = MODEL_STYLES.get(model_name, {"color": "gray", "linestyle": "--",
                                         "marker": "o", "ms": 75})
    ax.plot(md["time"], md["angle"],
            color=sty["color"], linestyle=sty["linestyle"],
            linewidth=1.8, alpha=0.85, zorder=3,
            label=model_name)

    if len(md["peaks"]) > 0:
        ax.scatter(md["time"][md["peaks"]], md["angle"][md["peaks"]],
                   color=sty["color"], s=sty["ms"],
                   edgecolors="black", marker=sty["marker"], zorder=5)

# Annotations
ax.axvline(x=POST_RELEASE, color="crimson", linestyle=":", linewidth=1.3, alpha=0.8,
           label=f"Release (~t={POST_RELEASE:.1f} s)")
ax.axhline(y=0, color="gray", linestyle="-", linewidth=0.8, alpha=0.4)

ax.set_title(
    "Participant 0 - Trial 1: Full-Timeline Knee Angle  (Pendulum Test)",
    fontsize=13, fontweight='bold')
ax.set_xlabel(
    "Normalized Time From Recording Start (s)  [t = 0 = first sample]",
    fontsize=11)
ax.set_ylabel(
    "Knee Flexion Angle (deg)  [0 deg = fully extended baseline]",
    fontsize=11)
ax.grid(True, linestyle=":", alpha=0.55)
ax.legend(loc="upper right", frameon=True, facecolor="white",
          framealpha=0.95, fontsize=9)

plt.tight_layout()
os.makedirs(os.path.dirname(OUTPUT_PLOT_PATH), exist_ok=True)
plt.savefig(OUTPUT_PLOT_PATH, dpi=300, bbox_inches='tight')
plt.close()

print(f"\nPlot saved to: {OUTPUT_PLOT_PATH}")
print("=" * 70)
