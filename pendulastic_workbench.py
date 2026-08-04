"""
pendulastic_workbench.py
=========================
Pendulastic Workbench: an interactive multi-modal (phone IMU / MediaPipe-
family HPE video / OptiTrack) trial comparison tool. Follows
pendulastic_app.py's plain-Tkinter panel-swap architecture.

See docs/superpowers/specs/2026-07-31-pendulastic-workbench-design.md.
"""
from __future__ import annotations

import json
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
        self._imu_format = tk.StringVar(value="jsonl")
        self._component_paths = {k: tk.StringVar(value="")
                                 for k in ("accel", "gyro", "mag", "imu")}
        self._component_status = {k: tk.StringVar(value="")
                                  for k in ("accel", "gyro", "mag", "imu")}
        self._component_validations: dict = {}
        self._video_path = tk.StringVar(value="")
        self._optitrack_path = tk.StringVar(value="")
        self._femur_cm = tk.StringVar(value="")
        self._tibia_cm = tk.StringVar(value="")
        self._model_vars = {name: tk.BooleanVar(value=False)
                            for name in analysis_pipeline.MODEL_FUNCTIONS}
        self._browse_buttons: dict = {}
        self._build_widgets()

    def _build_widgets(self) -> None:
        pad = {"padx": 12, "pady": 6}

        self._back_button = tk.Button(
            self, text="← Back to Main Menu",
            command=lambda: self.controller.on_back_to_mode_select())
        self._back_button.grid(row=0, column=0, columnspan=3, sticky="w", padx=12, pady=(6, 0))

        tk.Label(self, text="Pendulastic Workbench", font=("Segoe UI", 14, "bold")
                ).grid(row=1, column=0, columnspan=3, sticky="w", **pad)

        tk.Label(self, text="IMU format:").grid(row=2, column=0, sticky="w", **pad)
        format_frame = tk.Frame(self)
        format_frame.grid(row=2, column=1, columnspan=2, sticky="w", **pad)
        tk.Radiobutton(format_frame, text="Single raw log (.jsonl)", variable=self._imu_format,
                      value="jsonl", command=self._on_imu_format_changed).pack(side="left")
        tk.Radiobutton(format_frame, text="Split CSV (4 files)", variable=self._imu_format,
                      value="split_csv", command=self._on_imu_format_changed
                      ).pack(side="left", padx=(12, 0))

        self._imu_jsonl_frame = tk.Frame(self)
        self._file_row(self._imu_jsonl_frame, 0, "Phone IMU raw log (.jsonl)", self._imu_path,
                       [("IMU log", "*.jsonl"), ("All files", "*.*")], name="imu")
        self._imu_jsonl_frame.grid(row=3, column=0, columnspan=3, sticky="we")

        self._imu_split_frame = tk.Frame(self)
        self._build_split_csv_rows(self._imu_split_frame)
        self._imu_split_frame.grid(row=3, column=0, columnspan=3, sticky="we")

        self._on_imu_format_changed()

        self._file_row(self, 4, "Video (.mp4/.avi)", self._video_path,
                       [("Video", "*.mp4 *.avi"), ("All files", "*.*")], name="video")
        self._file_row(self, 5, "OptiTrack CSV", self._optitrack_path,
                       [("CSV", "*.csv"), ("All files", "*.*")], name="optitrack")

        tk.Label(self, text="HPE models to run:").grid(
            row=6, column=0, sticky="nw", **pad)
        model_frame = tk.Frame(self)
        model_frame.grid(row=6, column=1, columnspan=2, sticky="w", **pad)
        for i, name in enumerate(analysis_pipeline.MODEL_FUNCTIONS):
            tk.Checkbutton(model_frame, text=name, variable=self._model_vars[name]
                          ).grid(row=i // 3, column=i % 3, sticky="w", padx=4)

        tk.Label(self, text="Femur length (cm, optional):").grid(
            row=7, column=0, sticky="w", **pad)
        tk.Entry(self, textvariable=self._femur_cm, width=10).grid(
            row=7, column=1, sticky="w", **pad)

        tk.Label(self, text="Tibia length (cm, optional):").grid(
            row=8, column=0, sticky="w", **pad)
        tk.Entry(self, textvariable=self._tibia_cm, width=10).grid(
            row=8, column=1, sticky="w", **pad)

        tk.Button(self, text="Load Trial", command=self._on_load_clicked
                 ).grid(row=9, column=0, columnspan=3, pady=16)

    _COMPONENT_LABELS = {"accel": "Accelerometer", "gyro": "Gyroscope",
                         "mag": "Magnetometer", "imu": "Raw IMU"}
    _COMPONENT_FILETYPES = [("CSV", "*.csv"), ("All files", "*.*")]

    def _build_split_csv_rows(self, parent) -> None:
        for i, kind in enumerate(("accel", "gyro", "mag", "imu")):
            tk.Label(parent, text=self._COMPONENT_LABELS[kind]).grid(
                row=i, column=0, sticky="w", padx=12, pady=4)
            tk.Entry(parent, textvariable=self._component_paths[kind], width=36,
                    state="readonly").grid(row=i, column=1, sticky="we", padx=4)
            tk.Button(parent, text="Browse...",
                     command=lambda k=kind: self._browse_component(k)
                     ).grid(row=i, column=2, sticky="w", padx=4)
            tk.Label(parent, textvariable=self._component_status[kind], anchor="w", width=32
                    ).grid(row=i, column=3, sticky="w", padx=(8, 12))

    def _on_imu_format_changed(self) -> None:
        if self._imu_format.get() == "split_csv":
            self._imu_jsonl_frame.grid_remove()
            self._imu_split_frame.grid()
        else:
            self._imu_split_frame.grid_remove()
            self._imu_jsonl_frame.grid()

    def _browse_component(self, kind: str) -> None:
        path = filedialog.askopenfilename(filetypes=self._COMPONENT_FILETYPES)
        if not path:
            return
        self._component_paths[kind].set(path)
        result = engine.validate_component_csv(path, kind)
        self._component_validations[kind] = dict(result, path=path)
        if result["ok"]:
            self._component_status[kind].set(
                f"✓ {result['n_samples']} samples @ {result['fs_eff']:.1f} Hz")
        else:
            self._component_status[kind].set(f"✗ {result['error']}")

    def _file_row(self, parent, row: int, label: str, var: tk.StringVar, filetypes,
                  name: str) -> None:
        tk.Label(parent, text=label).grid(row=row, column=0, sticky="w", padx=12, pady=6)
        tk.Entry(parent, textvariable=var, width=48, state="readonly").grid(
            row=row, column=1, sticky="we", padx=4)
        btn = tk.Button(parent, text="Browse...",
                       command=lambda: self._browse(var, filetypes))
        btn.grid(row=row, column=2, sticky="w", padx=4)
        self._browse_buttons[name] = btn

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

        imu_format = self._imu_format.get()
        return {
            "imu_format": imu_format,
            "imu_path": self._imu_path.get() or None,
            "imu_components": dict(self._component_validations) if imu_format == "split_csv" else {},
            "video_path": self._video_path.get() or None,
            "optitrack_path": self._optitrack_path.get() or None,
            "models": [name for name, var in self._model_vars.items() if var.get()],
            "femur_length_cm": _parse_float(self._femur_cm.get()),
            "tibia_length_cm": _parse_float(self._tibia_cm.get()),
        }

    def _on_load_clicked(self) -> None:
        selection = self.get_selection()

        if selection["imu_format"] == "split_csv":
            has_any = any(self._component_paths[k].get() for k in ("accel", "gyro", "mag", "imu"))
            missing_or_invalid = [k for k in ("accel", "gyro", "mag", "imu")
                                  if not selection["imu_components"].get(k, {}).get("ok")]
            if has_any and missing_or_invalid:
                messagebox.showerror(
                    "Incomplete IMU intake",
                    "The following component(s) still need a valid file before the IMU "
                    "trace can be bound: " + ", ".join(missing_or_invalid))
                return
            imu_ready = has_any and not missing_or_invalid
        else:
            imu_ready = bool(selection["imu_path"])

        if not any([imu_ready, selection["video_path"], selection["optitrack_path"]]):
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
        self._annotations: dict = {}     # {label: (frame_index, t_sec)}
        self._pending_milestone = tk.StringVar(value=MILESTONE_LABELS[0])
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
        self._load_another_button = tk.Button(
            top_controls, text="← Load Different Trial",
            command=lambda: self.controller.on_workbench_load_another())
        self._load_another_button.pack(side="right", padx=6)

        annot_toolbar = tk.Frame(self._right)
        annot_toolbar.pack(fill="x", padx=8, pady=4)
        tk.Label(annot_toolbar, text="Milestone:").pack(side="left")
        ttk.OptionMenu(annot_toolbar, self._pending_milestone,
                      MILESTONE_LABELS[0], *MILESTONE_LABELS).pack(side="left", padx=6)
        tk.Button(annot_toolbar, text="Mark Here",
                 command=self._on_mark_milestone).pack(side="left", padx=6)
        tk.Button(annot_toolbar, text="Export Session...",
                 command=self._on_export_clicked).pack(side="right", padx=6)

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
        """traces: {label: (t, angle)}. Rebuilds the plot and the visibility/
        lag-override widgets, but preserves each *already-present* label's
        chosen visibility, lag override, and (if still present) the
        manually-selected reference -- set_traces() is called a second time
        when async HPE video models finish after IMU/OptiTrack have already
        loaded (design spec Section 3), and a researcher who configured
        their comparison during that "slow step" wait must not have it
        silently reset out from under them. Only genuinely new labels get
        fresh defaults."""
        prev_reference = self._reference_var.get()
        prev_visible    = dict(self._visible_vars)
        prev_lag        = dict(self._lag_override_vars)
        prev_scrub_t    = self.current_time_sec()

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

            var = prev_visible[label] if label in prev_visible else tk.BooleanVar(value=True)
            self._visible_vars[label] = var
            tk.Checkbutton(row, text=label, variable=var,
                          command=self._on_visibility_changed).pack(side="left")

            lag_var = prev_lag[label] if label in prev_lag else tk.StringVar(value="")
            self._lag_override_vars[label] = lag_var
            tk.Label(row, text="lag(s):", font=("Segoe UI", 7)).pack(side="left")
            lag_entry = tk.Entry(row, textvariable=lag_var, width=6)
            lag_entry.pack(side="left")
            lag_entry.bind("<Return>", lambda e: self._recompute_metrics())
            lag_entry.bind("<FocusOut>", lambda e: self._recompute_metrics())

            line, = self._ax.plot(t, angle, label=label)
            line.set_visible(var.get())
            self._trace_lines[label] = line

        self._ax.legend(
            [l for l in self._trace_lines.values() if l.get_visible()],
            [lbl for lbl, l in self._trace_lines.items() if l.get_visible()],
            fontsize=8)
        self._axvline = self._ax.axvline(prev_scrub_t, color="#94A3B8", linewidth=0.8)

        menu = self._reference_menu["menu"]
        menu.delete(0, "end")
        for label in traces:
            menu.add_command(label=label,
                            command=lambda l=label: self._reference_var.set(l))
        if prev_reference and prev_reference in traces:
            self._reference_var.set(prev_reference)
        else:
            default_ref = self._default_reference(traces)
            if default_ref:
                self._reference_var.set(default_ref)

        self._redraw_annotations()
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

    def _on_mark_milestone(self) -> None:
        """Stale-frame binding (design spec Section 6): reads
        current_frame_index()/current_time_sec(), which read the scrubber's
        bound Tkinter variable directly -- never whatever frame the video
        canvas has actually finished painting."""
        label = self._pending_milestone.get()
        fi = self.current_frame_index()
        t_sec = self.current_time_sec()
        self._annotations[label] = (fi, t_sec)
        self._draw_milestone_artist(label, t_sec)
        self._plot_canvas.draw_idle()

    def _draw_milestone_artist(self, label: str, t_sec: float) -> None:
        if not hasattr(self, "_annotation_artists"):
            self._annotation_artists = {}
        if label in self._annotation_artists:
            self._annotation_artists[label].remove()
        self._annotation_artists[label] = self._ax.annotate(
            label, xy=(t_sec, self._ax.get_ylim()[1]),
            rotation=90, va="top", ha="right", fontsize=7, color="#DC2626")
        self._ax.axvline(t_sec, color="#DC2626", linewidth=0.8, linestyle="--")

    def _redraw_annotations(self) -> None:
        """set_traces()'s _ax.clear() wipes the plotted milestone markers,
        but self._annotations (the data get_annotations()/export reads)
        is untouched by it -- only the visual artists need recreating so
        a researcher's already-marked milestones don't appear to vanish
        the next time set_traces() runs (e.g. when async HPE results
        merge in after IMU/OptiTrack already loaded)."""
        self._annotation_artists = {}
        for label, (_fi, t_sec) in self._annotations.items():
            self._draw_milestone_artist(label, t_sec)

    def get_annotations(self) -> dict:
        return dict(self._annotations)

    def export_session_to(self, out_path: str, trial_meta: dict) -> None:
        session = engine.export_session(
            trial_meta, self.get_annotations(), self.get_metrics_snapshot())
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(session, f, indent=2)

    def _on_export_clicked(self) -> None:
        out_path = filedialog.asksaveasfilename(
            defaultextension=".json", filetypes=[("JSON", "*.json")])
        if not out_path:
            return
        self.export_session_to(out_path, self.controller.get_trial_meta())
        messagebox.showinfo("Export complete", f"Session exported to {out_path}")

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


class App(tk.Tk):
    """Owns panel switching between TrialLoadPanel and WorkbenchView,
    matching pendulastic_app.py's App class pattern (pack/pack_forget
    between pre-built panel instances)."""

    def __init__(self) -> None:
        super().__init__()
        self.title("Pendulastic Workbench")
        self.geometry("1200x800")
        self.resizable(True, True)
        self.minsize(900, 600)

        self._trial_meta: dict = {}
        self._status_var = tk.StringVar(value="")

        self._load_panel = TrialLoadPanel(self, controller=self)
        self._workbench_view = WorkbenchView(self, controller=self)
        self._load_panel.pack(fill="both", expand=True)
        tk.Label(self, textvariable=self._status_var, anchor="w").pack(
            side="bottom", fill="x", padx=8, pady=2)

    def get_trial_meta(self) -> dict:
        return dict(self._trial_meta)

    def on_back_to_mode_select(self) -> None:
        """No-op in standalone mode -- there is no landing screen to return
        to here; this only exists so TrialLoadPanel's back button has a
        controller method to call regardless of which App hosts it."""
        pass

    def on_workbench_load_another(self) -> None:
        self._workbench_view.pack_forget()
        self._load_panel.pack(fill="both", expand=True)

    def on_load_trial(self, selection: dict) -> None:
        """Loads whichever of the three modalities were selected (design
        spec Section 2: 2-of-3 is valid) and switches to WorkbenchView.
        Video HPE model inference runs on a background thread since it's
        the slow step (design spec Section 3); IMU/OptiTrack loading is
        fast enough to run inline. IMU input is either a single JSONL raw
        log or four independently-validated split-CSV components (design
        spec 2026-08-04-sequential-csv-intake) -- TrialLoadPanel.get_selection()
        distinguishes the two via selection["imu_format"]."""
        traces = {}
        imu_format = selection.get("imu_format", "jsonl")
        self._trial_meta = {
            "video_path": selection["video_path"],
            "optitrack_path": selection["optitrack_path"],
            "models": selection["models"],
            "femur_length_cm": selection["femur_length_cm"],
            "tibia_length_cm": selection["tibia_length_cm"],
        }

        ft_ratio = None
        method_override = None
        if selection["femur_length_cm"] and selection["tibia_length_cm"]:
            ft_ratio = selection["femur_length_cm"] / selection["tibia_length_cm"]
            method_override = "ockendon_flipped"

        if imu_format == "split_csv":
            components = selection.get("imu_components", {})
            if all(components.get(k, {}).get("ok") for k in ("accel", "gyro", "mag", "imu")):
                try:
                    t, angle, imu_reference = engine.load_imu_trial_from_components(
                        components, ft_ratio=ft_ratio, method=method_override)
                    traces["imu"] = (t, angle)
                    self._trial_meta["imu_paths"] = {k: components[k]["path"] for k in components}
                    self._trial_meta["imu_reference"] = imu_reference
                except Exception as e:
                    messagebox.showerror("IMU load error", f"{type(e).__name__}: {e}")
        elif selection["imu_path"]:
            self._trial_meta["imu_path"] = selection["imu_path"]
            try:
                t, angle = engine.load_imu_trial(
                    selection["imu_path"], ft_ratio=ft_ratio, method=method_override)
                traces["imu"] = (t, angle)
            except Exception as e:
                messagebox.showerror("IMU load error", f"{type(e).__name__}: {e}")

        if selection["optitrack_path"]:
            try:
                t, angle, method = engine.load_optitrack_trial(selection["optitrack_path"])
                traces["optitrack"] = (t, angle)
                self._trial_meta["optitrack_method"] = method
            except Exception as e:
                messagebox.showerror("OptiTrack load error", f"{type(e).__name__}: {e}")

        self._load_panel.pack_forget()
        self._workbench_view.pack(fill="both", expand=True)
        self._workbench_view.set_traces(traces)

        if selection["video_path"]:
            self._workbench_view.load_video(selection["video_path"])
            if selection["models"]:
                self._load_video_models_async(selection["video_path"], selection["models"], traces)

    def _load_video_models_async(self, video_path: str, models: list, traces: dict) -> None:
        """Runs load_video_trial on a background thread (design spec
        Section 3: full-video pose inference x N models is the slow step)
        and surfaces progress via progress_cb -- Tkinter widgets may only
        be touched from the main thread, so both the progress update and
        the final traces update are marshalled through self.after(0, ...)."""
        import threading

        self._status_var.set(f"Running {len(models)} HPE model(s)... 0%")

        def on_progress(fraction: float) -> None:
            self.after(0, lambda: self._status_var.set(
                f"Running {len(models)} HPE model(s)... {fraction * 100:.0f}%"))

        def worker():
            results = engine.load_video_trial(video_path, models, progress_cb=on_progress)
            def apply():
                for name, result in results.items():
                    if isinstance(result, dict) and "error" in result:
                        print(f"[warn] model {name!r} failed: {result['error']}")
                        continue
                    traces[name] = result
                self._workbench_view.set_traces(traces)
                self._status_var.set("")
            self.after(0, apply)

        threading.Thread(target=worker, daemon=True).start()


if __name__ == "__main__":
    App().mainloop()
