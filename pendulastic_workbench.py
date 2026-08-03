"""
pendulastic_workbench.py
=========================
Pendulastic Workbench: an interactive multi-modal (phone IMU / MediaPipe-
family HPE video / OptiTrack) trial comparison tool. Follows
pendulastic_app.py's plain-Tkinter panel-swap architecture.

See docs/superpowers/specs/2026-07-31-pendulastic-workbench-design.md.
"""
from __future__ import annotations

import os
from typing import Optional

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import cv2
from PIL import Image, ImageTk

import numpy as np
import matplotlib
matplotlib.use("TkAgg")
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure

import analysis_pipeline
import workbench_engine as engine

MILESTONE_LABELS = ["Release Start", "First Peak Extension",
                    "Maximum Flexion", "Rest/Settled"]


def _mean_nearest_extremum_offset(ref_times, test_times) -> Optional[float]:
    """Mean absolute time offset between each ref extremum and its nearest
    test extremum -- the "timing jitter across oscillation cycles" metric
    (design spec Section 4). Returns None if either curve has no detected
    extrema (nothing to compare)."""
    if len(ref_times) == 0 or len(test_times) == 0:
        return None
    offsets = [float(np.min(np.abs(test_times - rt))) for rt in ref_times]
    return float(np.mean(offsets))


class TrialLoadPanel(tk.Frame):
    """3 independent file pickers (IMU raw log / video / OptiTrack CSV),
    HPE model checkboxes, and optional femur/tibia length fields for
    Ockendon-ratio personalization (design spec Section 3a).

    controller: App instance -- receives on_load_trial(selection: dict)."""

    def __init__(self, parent, controller) -> None:
        super().__init__(parent)
        self.controller = controller
        self._imu_path = tk.StringVar(value="")
        self._video_path = tk.StringVar(value="")
        self._optitrack_path = tk.StringVar(value="")
        self._femur_cm = tk.StringVar(value="")
        self._tibia_cm = tk.StringVar(value="")
        self._model_vars = {name: tk.BooleanVar(value=False)
                            for name in analysis_pipeline.MODEL_FUNCTIONS}
        self._build_widgets()

    def _build_widgets(self) -> None:
        pad = {"padx": 12, "pady": 6}

        tk.Label(self, text="Pendulastic Workbench", font=("Segoe UI", 14, "bold")
                ).grid(row=0, column=0, columnspan=3, sticky="w", **pad)

        self._file_row(1, "Phone IMU raw log (.jsonl)", self._imu_path,
                       [("JSONL", "*.jsonl"), ("All files", "*.*")])
        self._file_row(2, "Video (.mp4/.avi)", self._video_path,
                       [("Video", "*.mp4 *.avi"), ("All files", "*.*")])
        self._file_row(3, "OptiTrack CSV", self._optitrack_path,
                       [("CSV", "*.csv"), ("All files", "*.*")])

        tk.Label(self, text="HPE models to run:").grid(
            row=4, column=0, sticky="nw", **pad)
        model_frame = tk.Frame(self)
        model_frame.grid(row=4, column=1, columnspan=2, sticky="w", **pad)
        for i, name in enumerate(analysis_pipeline.MODEL_FUNCTIONS):
            tk.Checkbutton(model_frame, text=name, variable=self._model_vars[name]
                          ).grid(row=i // 3, column=i % 3, sticky="w", padx=4)

        tk.Label(self, text="Femur length (cm, optional):").grid(
            row=5, column=0, sticky="w", **pad)
        tk.Entry(self, textvariable=self._femur_cm, width=10).grid(
            row=5, column=1, sticky="w", **pad)

        tk.Label(self, text="Tibia length (cm, optional):").grid(
            row=6, column=0, sticky="w", **pad)
        tk.Entry(self, textvariable=self._tibia_cm, width=10).grid(
            row=6, column=1, sticky="w", **pad)

        tk.Button(self, text="Load Trial", command=self._on_load_clicked
                 ).grid(row=7, column=0, columnspan=3, pady=16)

    def _file_row(self, row: int, label: str, var: tk.StringVar, filetypes) -> None:
        tk.Label(self, text=label).grid(row=row, column=0, sticky="w", padx=12, pady=6)
        tk.Entry(self, textvariable=var, width=48, state="readonly").grid(
            row=row, column=1, sticky="we", padx=4)
        tk.Button(self, text="Browse...",
                 command=lambda: self._browse(var, filetypes)).grid(
            row=row, column=2, sticky="w", padx=4)

    def _browse(self, var: tk.StringVar, filetypes) -> None:
        path = filedialog.askopenfilename(filetypes=filetypes)
        if path:
            var.set(path)

    def get_selection(self) -> dict:
        """Snapshot of the current form state. Numeric fields left blank
        parse to None (Section 3a: leaving them blank keeps the default
        1.2 femur:tibia ratio unchanged)."""
        def _parse_float(s: str) -> Optional[float]:
            s = s.strip()
            if not s:
                return None
            try:
                return float(s)
            except ValueError:
                return None

        return {
            "imu_path": self._imu_path.get() or None,
            "video_path": self._video_path.get() or None,
            "optitrack_path": self._optitrack_path.get() or None,
            "models": [name for name, var in self._model_vars.items() if var.get()],
            "femur_length_cm": _parse_float(self._femur_cm.get()),
            "tibia_length_cm": _parse_float(self._tibia_cm.get()),
        }

    def _on_load_clicked(self) -> None:
        selection = self.get_selection()
        if not any([selection["imu_path"], selection["video_path"],
                   selection["optitrack_path"]]):
            messagebox.showerror("No trial data",
                                 "Select at least one of: IMU log, video, OptiTrack CSV.")
            return
        self.controller.on_load_trial(selection)


class WorkbenchView(tk.Frame):
    """Main workbench panel: synced video scrubber + multi-trace plot +
    annotation toolbar + metrics readout (built up across Tasks 11-14).

    controller: App instance."""

    def __init__(self, parent, controller) -> None:
        super().__init__(parent)
        self.controller = controller
        self._cap: Optional[cv2.VideoCapture] = None
        self._fps: float = 30.0
        self._n_frames: int = 0
        self._photo = None   # keep a reference so Tk doesn't garbage-collect it
        self._scrub_var = tk.DoubleVar(value=0.0)
        self._traces: dict = {}          # {label: (t, angle)}
        self._trace_lines: dict = {}     # {label: matplotlib Line2D}
        self._visible_vars: dict = {}    # {label: tk.BooleanVar}
        self._lag_override_vars: dict = {}   # {label: tk.StringVar}, blank = auto
        self._reference_var = tk.StringVar(value="")
        self._build_widgets()

    def _build_widgets(self) -> None:
        paned = ttk.PanedWindow(self, orient="horizontal")
        paned.pack(fill="both", expand=True)

        left = tk.Frame(paned)
        paned.add(left, weight=1)

        self._video_label = tk.Label(left, bg="black")
        self._video_label.pack(fill="both", expand=True)

        self._scrubber = ttk.Scale(left, from_=0, to=0, orient="horizontal",
                                   variable=self._scrub_var,
                                   command=self._on_scrub)
        self._scrubber.pack(fill="x", padx=8, pady=4)

        self._right = tk.Frame(paned)
        paned.add(self._right, weight=1)

        top_controls = tk.Frame(self._right)
        top_controls.pack(fill="x", padx=8, pady=4)
        tk.Label(top_controls, text="Reference:").pack(side="left")
        self._reference_menu = ttk.OptionMenu(top_controls, self._reference_var, "")
        self._reference_menu.pack(side="left", padx=6)
        self._reference_var.trace_add("write", lambda *a: self._recompute_metrics())

        self._visibility_frame = tk.Frame(self._right)
        self._visibility_frame.pack(fill="x", padx=8, pady=4)

        self._fig = Figure(figsize=(6, 4), dpi=100)
        self._ax = self._fig.add_subplot(111)
        self._ax.set_xlabel("Time (s)")
        self._ax.set_ylabel("Knee Angle (deg)")
        self._plot_canvas = FigureCanvasTkAgg(self._fig, master=self._right)
        self._plot_canvas.get_tk_widget().pack(fill="both", expand=True, padx=8, pady=4)
        self._fig.canvas.mpl_connect("button_press_event", self._on_plot_click)

        self._metrics_text = tk.Text(self._right, height=8, state="disabled")
        self._metrics_text.pack(fill="x", padx=8, pady=4)

    def set_traces(self, traces: dict) -> None:
        """traces: {label: (t, angle)}. Rebuilds the plot, the visibility
        checkboxes (each paired with a manual lag-override field, design
        spec Section 4), and the reference-selector menu from scratch."""
        self._traces = traces
        for widget in self._visibility_frame.winfo_children():
            widget.destroy()
        self._ax.clear()
        self._ax.set_xlabel("Time (s)")
        self._ax.set_ylabel("Knee Angle (deg)")
        self._trace_lines = {}
        self._visible_vars = {}
        self._lag_override_vars = {}

        for label, (t, angle) in traces.items():
            row = tk.Frame(self._visibility_frame)
            row.pack(side="left", padx=4)

            var = tk.BooleanVar(value=True)
            self._visible_vars[label] = var
            tk.Checkbutton(row, text=label, variable=var,
                          command=self._on_visibility_changed).pack(side="left")

            lag_var = tk.StringVar(value="")
            self._lag_override_vars[label] = lag_var
            tk.Label(row, text="lag(s):", font=("Segoe UI", 7)).pack(side="left")
            lag_entry = tk.Entry(row, textvariable=lag_var, width=6)
            lag_entry.pack(side="left")
            lag_entry.bind("<Return>", lambda e: self._recompute_metrics())
            lag_entry.bind("<FocusOut>", lambda e: self._recompute_metrics())

            line, = self._ax.plot(t, angle, label=label)
            self._trace_lines[label] = line

        self._ax.legend(fontsize=8)
        self._axvline = self._ax.axvline(0, color="#94A3B8", linewidth=0.8)

        menu = self._reference_menu["menu"]
        menu.delete(0, "end")
        for label in traces:
            menu.add_command(label=label,
                            command=lambda l=label: self._reference_var.set(l))
        default_ref = self._default_reference(traces)
        if default_ref:
            self._reference_var.set(default_ref)

        self._plot_canvas.draw_idle()

    def _default_reference(self, traces: dict) -> str:
        """OptiTrack present -> OptiTrack; else IMU present -> IMU; else the
        first-loaded HPE model (design spec Section 4)."""
        if "optitrack" in traces:
            return "optitrack"
        if "imu" in traces:
            return "imu"
        return next(iter(traces), "")

    def _on_visibility_changed(self) -> None:
        for label, line in self._trace_lines.items():
            line.set_visible(self._visible_vars[label].get())
        self._ax.legend(
            [l for l in self._trace_lines.values() if l.get_visible()],
            [lbl for lbl, l in self._trace_lines.items() if l.get_visible()],
            fontsize=8)
        self._plot_canvas.draw_idle()
        self._recompute_metrics()

    def _lag_override_for(self, label: str) -> Optional[float]:
        raw = self._lag_override_vars.get(label)
        if raw is None:
            return None
        text = raw.get().strip()
        if not text:
            return None
        try:
            return float(text)
        except ValueError:
            return None

    def get_metrics_snapshot(self) -> dict:
        """Two distinct metric families (design spec Sections 4 and 4a),
        computed only over *visible* traces (hiding a trace excludes it
        from both):

        - "per_trace": each visible trace's own windowed_pt_params
          (area_ratio, N, f, etc.) -- a per-modality diagnostic, not a
          comparison. Includes the reference trace itself.
        - "vs_reference": every other visible trace's compare_pair result
          against the reference-selector's chosen reference, plus a
          timing_offset_sec (extrema_jitter-based "timing jitter across
          oscillation cycles" metric). Manual per-trace lag overrides are
          honored here.

        Both the live display (_recompute_metrics) and export (Task 14)
        call this one method, so what a researcher sees is exactly what
        gets exported."""
        ref_label = self._reference_var.get()
        out = {"reference": ref_label, "per_trace": {}, "vs_reference": {}}
        if not ref_label or ref_label not in self._traces:
            return out

        for label, (t, y) in self._traces.items():
            if not self._visible_vars.get(label, tk.BooleanVar(value=True)).get():
                continue
            out["per_trace"][label] = engine.windowed_pt_params(t, y)

        ref_t, ref_y = self._traces[ref_label]
        ref_jitter = engine.extrema_jitter(ref_t, ref_y)

        for label, (t, y) in self._traces.items():
            if label == ref_label:
                continue
            if not self._visible_vars.get(label, tk.BooleanVar(value=True)).get():
                continue
            lag_override = self._lag_override_for(label)
            result = engine.compare_pair(ref_t, ref_y, t, y, lag_override_sec=lag_override)
            if result["status"] == "ok":
                test_jitter = engine.extrema_jitter(t, y)
                result = dict(result)
                result["timing_offset_sec"] = _mean_nearest_extremum_offset(
                    ref_jitter["cycle_times"], test_jitter["cycle_times"])
            out["vs_reference"][label] = result
        return out

    def _recompute_metrics(self) -> None:
        """Renders get_metrics_snapshot() as text in the metrics readout:
        one line per visible trace's own PT parameters, then one line per
        non-reference visible trace's comparison against the reference."""
        snapshot = self.get_metrics_snapshot()
        self._metrics_text.configure(state="normal")
        self._metrics_text.delete("1.0", "end")
        ref_label = snapshot["reference"]
        if not ref_label:
            self._metrics_text.configure(state="disabled")
            return

        for label, pt in snapshot["per_trace"].items():
            self._metrics_text.insert(
                "end",
                f"{label}: area_ratio={pt['area_ratio']:.3f}  N={pt['N']:.1f}  "
                f"f={pt['f']:.2f} Hz\n")

        self._metrics_text.insert("end", "\n")

        for label, result in snapshot["vs_reference"].items():
            if result["status"] == "ok":
                jitter_str = (f"{result['timing_offset_sec']:.3f}s"
                             if result["timing_offset_sec"] is not None else "n/a")
                line = (f"{label} vs {ref_label}: RMSE={result['rmse_deg']:.1f} deg  "
                       f"MAE={result['mae_deg']:.1f} deg  lag={result['lag_sec']:.2f}s  "
                       f"jitter={jitter_str}\n")
            else:
                line = f"{label} vs {ref_label}: {result['error']}\n"
            self._metrics_text.insert("end", line)
        self._metrics_text.configure(state="disabled")

    def load_video(self, path: str) -> None:
        if self._cap is not None:
            self._cap.release()
        self._cap = cv2.VideoCapture(path)
        self._fps = self._cap.get(cv2.CAP_PROP_FPS) or 30.0
        self._n_frames = int(self._cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 1
        self._scrubber.configure(to=max(0, self._n_frames - 1))
        self._scrub_var.set(0)
        self.seek_to_frame(0)

    def seek_to_frame(self, fi: int) -> None:
        if self._cap is None:
            return
        fi = max(0, min(fi, self._n_frames - 1))
        self._cap.set(cv2.CAP_PROP_POS_FRAMES, fi)
        ret, frame = self._cap.read()
        if not ret or frame is None:
            return
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        img = Image.fromarray(rgb)
        self._photo = ImageTk.PhotoImage(img)
        self._video_label.configure(image=self._photo)

    def _on_scrub(self, value_str: str) -> None:
        fi = int(round(float(value_str)))
        self.seek_to_frame(fi)
        if hasattr(self, "_axvline"):
            t_now = self.current_time_sec()
            self._axvline.set_xdata([t_now, t_now])
            self._plot_canvas.draw_idle()

    def _on_plot_click(self, event) -> None:
        """Clicking the plot seeks the video to the nearest frame --
        generalizes pendulastic_viewer.py's single-purpose release-frame
        click handler into an arbitrary seek (design spec Section 5)."""
        if event.inaxes is not self._ax or event.xdata is None or self._fps <= 0:
            return
        fi = int(round(event.xdata * self._fps))
        fi = max(0, min(fi, self._n_frames - 1))
        self._scrub_var.set(fi)
        self._on_scrub(str(fi))

    def current_frame_index(self) -> int:
        """Reads the scrubber's bound Tkinter variable directly -- never
        infers the frame from canvas paint state (design spec Section 6's
        stale-frame binding requirement)."""
        return int(round(self._scrub_var.get()))

    def current_time_sec(self) -> float:
        return self.current_frame_index() / self._fps if self._fps > 0 else 0.0
