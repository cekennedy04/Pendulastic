# Unified Pendulastic Desktop App — Design Spec
**Date:** 2026-07-28  
**Status:** Approved  

---

## 1. Goal

Combine `master_app.py` (acquisition) and `pendulastic_viewer.py` (post-processing) into a single desktop application: `pendulastic_app.py`. The existing files remain intact and runnable as standalone tools.

---

## 2. Module Structure

### New file: `pendulastic_app.py`

Contains five top-level definitions, in this order:

| Name | Type | Responsibility |
|---|---|---|
| `DataManager` | class | Filename construction + flat CSV write |
| `BiomechanicalEngine` | class | Angle pipeline, dispatched by methodology |
| `AcquisitionPanel` | `tk.Frame` subclass | Metadata form, switchboard, recording controls |
| `PostProcessingPanel` | `tk.Frame` subclass | Angle plot + PT metrics |
| `App` | `tk.Tk` subclass | Thin host: shared state, panel switching, port lifecycle |

### Imports from existing files (no modification to those files)

```
pendulastic_viewer.py     → _MPTracker, _ArcTracker, _PatientDetector,
                             compute_pt_params, compute_pt_score, pt_to_mas,
                             load_optitrack, HEALTHY_REF
pendulastic_pt_score.py   → (same symbols, via viewer or direct)
pendulastic_imu_server.py → start, stop, get_state, start_recording, stop_recording
motive_sync               → start_local_motive, stop_local_motive  (optional)
```

### Port ownership

| Port | Protocol | Owner | Lifecycle |
|---|---|---|---|
| 5000 | WebSocket (Sensor Stream) | `App.__init__` → `imu_server.start()` | Released in `App.on_close()` |
| 8888 | UDP goniometer | Started on IMU methodology select, stopped on switch or close | Managed by `AcquisitionPanel._set_methodology()` |

`PENDULASTIC_IMU_PORT` env var (already supported by `pendulastic_imu_server.py`) handles the edge case of running old and new apps simultaneously.

---

## 3. State Machine

```
                    countdown checkbox = True
IDLE ──START──►  COUNTDOWN ──reaches 0──► RECORDING
   │                   │
   │ countdown = False  └──CANCEL──► IDLE
   └────────────────────────────────► RECORDING
                                          │
                               STOP (RGB methodology)
                                          │
                                     PROCESSING ──done──► REVIEW
                                          │
                               STOP (IMU or OptiTrack)
                                          │
                                        REVIEW
                                          │
                               "← New Trial"
                                          │
                                        IDLE  (trial# auto-incremented)
```

### State rules

- **IDLE** — form editable, START enabled, telemetry canvas hidden (`grid_remove`).
- **COUNTDOWN** — form locked, START button mutated to CANCEL (same widget, same grid cell — `.config(text=, command=, bg=)` only). STOP disabled.
- **RECORDING** — form locked, STOP enabled, telemetry canvas visible (`grid`). `_tick()` drains IMU queue every 50 ms.
- **PROCESSING** — indeterminate `ttk.Progressbar`, "Running MediaPipe…" status. RGB offline tracking runs on a background thread; progress pumped via `root.after(0, ...)`.
- **REVIEW** — `AcquisitionPanel` unpacked, `PostProcessingPanel` packed full-window.

### START button contract (row 12, col 0 — never moves)

| State | `text` | `command` | `bg` |
|---|---|---|---|
| IDLE | `START RECORDING` | `_on_start` | `#1e7d34` (green) |
| COUNTDOWN | `CANCEL` | `_cancel_countdown` | `#c07000` (amber) |
| RECORDING | `START RECORDING` | — | green (disabled) |
| PROCESSING / REVIEW | `START RECORDING` | — | green (disabled) |

STOP button (row 12, col 1) independently enabled/disabled; label never changes.

---

## 4. AcquisitionPanel Layout

Fixed width 480 px, 2 columns. `columnconfigure(1, weight=1)`.

```
row  0  │ col 0–1 │  "Pendulastic — Trial Setup"  [Segoe UI 13 bold, center]
row  1  │ col 0–1 │  ttk.Separator
row  2  │ col 0   │  "Participant ID:"    │ col 1 │  Entry (width=22)
row  3  │ col 0   │  "Leg:"              │ col 1 │  Radiobutton  ○ Left  ○ Right
row  4  │ col 0   │  "MS Status:"        │ col 1 │  Combobox [MS | Stroke | Control | Other]
row  5  │ col 0   │  "Trial Number:"     │ col 1 │  Spinbox 1–99
row  6  │ col 0–1 │  ttk.Separator
row  7  │ col 0–1 │  "Methodology"  [Segoe UI 10 bold, left]
row  8  │ col 0–1 │  Radiobuttons:  ● OptiTrack   ● RGB   ● iPhone IMU
row  9  │ col 0–1 │  Modality status line  [Consolas 9, coloured dot + text]
row 10  │ col 0–1 │  ttk.Separator
row 11  │ col 0   │  ☐ "5-second countdown"        (col 1 empty)
row 12  │ col 0   │  START  [green, 13 pt bold]   │ col 1 │  STOP  [red, 13 pt bold]
row 13  │ col 0–1 │  Live telemetry canvas 240×80  [hidden at IDLE; shown at RECORDING]
row 14  │ col 0–1 │  Status bar  [sunken Label, full width]
```

**Telemetry canvas (row 13):** Renders a rolling sparkline of the live IMU angle plus a large numeric readout (e.g. `42.3°`). For RGB and OptiTrack methodologies it shows "— no live angle —" in grey. Populated by `App._tick()` draining `_imu_queue`.

---

## 5. PostProcessingPanel Layout

Full window, 2 columns. `rowconfigure(1, weight=1)`.

```
row  0  │ col 0–1 │  Trial filename as title  [bold, e.g. "PID_P1_LEG_Right_MS_TRIAL_1"]
row  1  │ col 0–1 │  FigureCanvasTkAgg  [expands to fill remaining height]
         │         │    · knee angle vs. time (seconds x, degrees y)
         │         │    · vertical dashed line at release frame (if set)
         │         │    · A1 / ω / λ / E annotated inline on curve
row  2  │ col 0–1 │  PT Metrics LabelFrame:
         │         │    A1=___°   ω=___rad/s   λ=___   E=___°   MAS=___   Score=___
row  3  │ col 0   │  "← New Trial"  [blue]    │ col 1 │  "📂 Load OptiTrack CSV"
row  4  │ col 0–1 │  Status bar
```

"← New Trial" pre-populates metadata from the just-completed trial and increments Trial Number by 1, then transitions to IDLE.

---

## 6. BiomechanicalEngine

One class, three code paths dispatched by `methodology: str`.

### IMU path (`"imu"`)

```python
def get_live_angle(self) -> float:
    state = _imu.get_state()
    return state["distal"]["pitch"]   # NaN if disconnected
```

- Reads the shank (distal) sensor's pitch directly: **θk = θshank − 0**.
- No proximal subtraction. No Rabé-Andersson fusion formula.
- `pendulastic_imu_server.start_recording()` / `stop_recording()` write the full raw CSV for archival; this method only reads the single field for display and the angle series.

### RGB path (`"rgb"`)

```python
def run_offline_track(self, video_path: str,
                      progress_cb: Callable[[float], None]) -> list[float]:
    tracker  = _MPTracker()
    detector = _PatientDetector()
    # frame loop: detect patient once, track per frame, collect angles
    return angles   # degrees, one per frame, indexed to frame number
```

Called on a background thread immediately after STOP. `progress_cb(pct)` is marshalled to the main thread via `root.after(0, ...)` to drive the PROCESSING progress bar. On completion, `App._on_processing_done(angles)` transitions to REVIEW.

### OptiTrack path (`"optitrack"`)

- `get_live_angle()` returns `float("nan")` — Motive owns live data.
- Optional live Motive sync via `motive_sync.start_local_motive()` / `stop_local_motive()`. Failure is a non-fatal warning (same behaviour as `master_app.py`).
- PostProcessingPanel starts with an empty curve and a "📂 Load OptiTrack CSV" prompt. On file select, `load_optitrack(path)` (imported from `pendulastic_viewer`) returns the angle series for plotting and PT scoring.

---

## 7. DataManager

```python
class DataManager:
    DATA_DIR = os.path.join(BASE_DIR, "data")

    @staticmethod
    def build_filename(pid: str, leg: str, ms_status: str, trial: int) -> str:
        leg_s = leg.capitalize()            # "Left" | "Right"
        ms_s  = ms_status.replace(" ", "_") # "Unaffected_Control", "MS", etc.
        return f"PID_{pid}_LEG_{leg_s}_{ms_s}_TRIAL_{trial}.csv"

    @classmethod
    def save_trial(cls, filename: str, angles: list[float],
                   fps: float, metadata: dict) -> str:
        os.makedirs(cls.DATA_DIR, exist_ok=True)
        path = os.path.join(cls.DATA_DIR, filename)
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["frame", "time_s", "knee_angle_deg",
                        "pid", "leg", "ms_status", "trial", "methodology"])
            for i, a in enumerate(angles):
                w.writerow([i, f"{i/fps:.4f}", f"{a:.3f}",
                            metadata["pid"], metadata["leg"],
                            metadata["ms_status"], metadata["trial"],
                            metadata["methodology"]])
        return path
```

All trial CSVs land in `data/` at the project root. No folder hierarchy.

---

## 8. Thread Safety Summary

| Resource | Shared between | Guard |
|---|---|---|
| IMU angle queue | IMU listener thread → main thread | `queue.Queue` (unbounded, drained every 50 ms) |
| IMU CSV file handle | IMU listener thread + main thread (close on STOP) | `threading.Lock` (existing in `pendulastic_imu_server`) |
| Camera `VideoWriter` | Camera thread + main thread (finalize on STOP) | `threading.Lock` (same pattern as `master_app.py`) |
| State enum | Main thread only | No lock needed (Tk single-threaded main loop) |

---

## 9. Out of Scope

- Batch evaluation (remains in `master_app.py`; not ported)
- Age / Weight / Sex fields (removed; not needed for trial CSVs)
- Camera position / height hierarchy (removed; replaced by flat filename)
- Multi-camera support (single webcam for RGB methodology)
