"""
pendulastic_workbench.py
=========================
Pendulastic Workbench: an interactive multi-modal (phone IMU / MediaPipe-
family HPE video / OptiTrack) trial comparison tool. Follows
pendulastic_app.py's plain-Tkinter panel-swap architecture.

See docs/superpowers/specs/2026-07-31-pendulastic-workbench-design.md.
"""
from __future__ import annotations

import datetime
import json
import csv
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
import workbench_style as ws

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
        super().__init__(parent, bg=ws.PALETTE["BG"])
        self.controller = controller
        self._imu_path = tk.StringVar(value="")
        self._video_path = tk.StringVar(value="")
        self._optitrack_path = tk.StringVar(value="")
        self._participant_id = tk.StringVar(value="")
        self._session_date = tk.StringVar(
            value=datetime.datetime.now().strftime("%Y-%m-%d"))
        self._femur_cm = tk.StringVar(value="")
        self._tibia_cm = tk.StringVar(value="")
        self._model_vars = {name: tk.BooleanVar(value=False)
                            for name in analysis_pipeline.MODEL_FUNCTIONS}
        self._browse_buttons: dict = {}
        self._build_widgets()

    def _build_widgets(self) -> None:
        header = tk.Frame(self, bg=ws.PALETTE["BG"])
        header.pack(fill="x", padx=12, pady=(10, 4))
        self._back_button = ws.secondary_button(
            header, "← Back to Main Menu",
            lambda: self.controller.on_back_to_mode_select())
        self._back_button.pack(side="left")
        tk.Label(header, text="Pendulastic Workbench", bg=ws.PALETTE["BG"],
                 fg=ws.PALETTE["FG"], font=ws.FONT_TITLE).pack(side="left", padx=(16, 0))

        files_card = ws.card_frame(self, "TRIAL FILES")
        files_card.pack(fill="x", padx=12, pady=6)
        self._file_row(files_card, "Phone IMU raw log (.jsonl or split CSV)",
                       self._imu_path,
                       [("IMU log", "*.jsonl *.csv"), ("All files", "*.*")], name="imu")
        self._file_row(files_card, "Video (.mp4/.avi)", self._video_path,
                       [("Video", "*.mp4 *.avi"), ("All files", "*.*")], name="video")
        self._file_row(files_card, "OptiTrack CSV", self._optitrack_path,
                       [("CSV", "*.csv"), ("All files", "*.*")], name="optitrack")

        session_card = ws.card_frame(self, "PARTICIPANT & SESSION")
        session_card.pack(fill="x", padx=12, pady=6)
        srow = tk.Frame(session_card, bg=ws.PALETTE["PANEL"])
        srow.pack(fill="x")
        tk.Label(srow, text="Participant ID:", bg=ws.PALETTE["PANEL"],
                 fg=ws.PALETTE["FG"], font=ws.FONT_BODY).grid(
            row=0, column=0, sticky="w", padx=(0, 6), pady=4)
        tk.Entry(srow, textvariable=self._participant_id, width=18,
                 font=ws.FONT_BODY).grid(row=0, column=1, sticky="w", padx=(0, 20), pady=4)
        tk.Label(srow, text="Session Date:", bg=ws.PALETTE["PANEL"],
                 fg=ws.PALETTE["FG"], font=ws.FONT_BODY).grid(
            row=0, column=2, sticky="w", padx=(0, 6), pady=4)
        tk.Entry(srow, textvariable=self._session_date, width=12,
                 font=ws.FONT_BODY).grid(row=0, column=3, sticky="w", pady=4)

        models_card = ws.card_frame(self, "HPE MODELS TO RUN")
        models_card.pack(fill="x", padx=12, pady=6)
        model_frame = tk.Frame(models_card, bg=ws.PALETTE["PANEL"])
        model_frame.pack(fill="x")
        for i, name in enumerate(analysis_pipeline.MODEL_FUNCTIONS):
            tk.Checkbutton(model_frame, text=name, variable=self._model_vars[name],
                          bg=ws.PALETTE["PANEL"], fg=ws.PALETTE["FG"], font=ws.FONT_BODY,
                          selectcolor=ws.PALETTE["SURFACE"],
                          activebackground=ws.PALETTE["PANEL"],
                          activeforeground=ws.PALETTE["FG"]
                         ).grid(row=i // 3, column=i % 3, sticky="w", padx=4, pady=2)

        pers_card = ws.card_frame(self, "PERSONALIZATION (OPTIONAL)")
        pers_card.pack(fill="x", padx=12, pady=6)
        prow = tk.Frame(pers_card, bg=ws.PALETTE["PANEL"])
        prow.pack(fill="x")
        tk.Label(prow, text="Femur length (cm):", bg=ws.PALETTE["PANEL"],
                 fg=ws.PALETTE["FG"], font=ws.FONT_BODY).grid(
            row=0, column=0, sticky="w", padx=(0, 6), pady=4)
        tk.Entry(prow, textvariable=self._femur_cm, width=10,
                 font=ws.FONT_BODY).grid(row=0, column=1, sticky="w", padx=(0, 20), pady=4)
        tk.Label(prow, text="Tibia length (cm):", bg=ws.PALETTE["PANEL"],
                 fg=ws.PALETTE["FG"], font=ws.FONT_BODY).grid(
            row=0, column=2, sticky="w", padx=(0, 6), pady=4)
        tk.Entry(prow, textvariable=self._tibia_cm, width=10,
                 font=ws.FONT_BODY).grid(row=0, column=3, sticky="w", pady=4)

        ws.primary_button(self, "Load Trial", self._on_load_clicked).pack(pady=16)

    def _file_row(self, parent, label: str, var: tk.StringVar, filetypes,
                  name: str) -> None:
        row = tk.Frame(parent, bg=ws.PALETTE["PANEL"])
        row.pack(fill="x", pady=3)
        tk.Label(row, text=label, bg=ws.PALETTE["PANEL"], fg=ws.PALETTE["FG"],
                 font=ws.FONT_BODY, width=32, anchor="w").pack(side="left")
        tk.Entry(row, textvariable=var, width=40, state="readonly",
                 font=ws.FONT_BODY).pack(side="left", padx=4, fill="x", expand=True)
        btn = ws.secondary_button(row, "Browse...", lambda: self._browse(var, filetypes))
        btn.pack(side="left", padx=4)
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

        return {
            "imu_path": self._imu_path.get() or None,
            "video_path": self._video_path.get() or None,
            "optitrack_path": self._optitrack_path.get() or None,
            "participant_id": self._participant_id.get().strip(),
            "session_date": self._session_date.get().strip(),
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
        super().__init__(parent, bg=ws.PALETTE["BG"])
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

    _PER_TRACE_COLS = ("label", "area_ratio", "N", "f_hz", "R2n",
                       "omega_max_n", "omega_min_n")
    _PER_TRACE_HDRS = ("Trace", "Area Ratio", "N", "f (Hz)", "R2n",
                       "ωmax_n", "ωmin_n")
    _PER_TRACE_W    = (110, 90, 60, 70, 70, 80, 80)

    _VS_REF_COLS = ("label", "reference", "rmse_deg", "mae_deg", "lag_sec",
                    "timing_offset_sec", "status")
    _VS_REF_HDRS = ("Trace", "Reference", "RMSE (deg)", "MAE (deg)",
                    "Lag (s)", "Timing Offset (s)", "Status")
    _VS_REF_W    = (100, 100, 90, 90, 70, 130, 110)

    def _build_widgets(self) -> None:
        paned = ttk.PanedWindow(self, orient="horizontal")
        paned.pack(fill="both", expand=True)

        left = tk.Frame(paned, bg=ws.PALETTE["BG"])
        paned.add(left, weight=1)

        self._video_label = tk.Label(left, bg="black",
                                     highlightbackground=ws.PALETTE["BORDER"],
                                     highlightthickness=1)
        self._video_label.pack(fill="both", expand=True, padx=8, pady=8)

        self._scrubber = ttk.Scale(left, from_=0, to=0, orient="horizontal",
                                   variable=self._scrub_var, command=self._on_scrub)
        self._scrubber.pack(fill="x", padx=8, pady=4)

        self._right = tk.Frame(paned, bg=ws.PALETTE["BG"])
        paned.add(self._right, weight=1)

        top_controls = tk.Frame(self._right, bg=ws.PALETTE["BG"])
        top_controls.pack(fill="x", padx=8, pady=4)
        tk.Label(top_controls, text="Reference:", bg=ws.PALETTE["BG"],
                 fg=ws.PALETTE["FG"], font=ws.FONT_BODY).pack(side="left")
        self._reference_menu = ttk.OptionMenu(top_controls, self._reference_var, "")
        self._reference_menu.pack(side="left", padx=6)
        self._reference_var.trace_add("write", lambda *a: self._recompute_metrics())
        self._load_another_button = ws.secondary_button(
            top_controls, "← Load Different Trial",
            lambda: self.controller.on_workbench_load_another())
        self._load_another_button.pack(side="right", padx=6)

        annot_toolbar = tk.Frame(self._right, bg=ws.PALETTE["BG"])
        annot_toolbar.pack(fill="x", padx=8, pady=4)
        tk.Label(annot_toolbar, text="Milestone:", bg=ws.PALETTE["BG"],
                 fg=ws.PALETTE["FG"], font=ws.FONT_BODY).pack(side="left")
        ttk.OptionMenu(annot_toolbar, self._pending_milestone,
                      MILESTONE_LABELS[0], *MILESTONE_LABELS).pack(side="left", padx=6)
        ws.secondary_button(annot_toolbar, "Mark Here",
                            self._on_mark_milestone).pack(side="left", padx=6)
        ws.secondary_button(annot_toolbar, "Export Session (JSON)...",
                            self._on_export_clicked).pack(side="right", padx=6)
        self._export_csv_button = tk.Menubutton(
            annot_toolbar, text="Export CSV ▾", bg=ws.PALETTE["BTN"],
            fg=ws.PALETTE["FG"], activebackground=ws.PALETTE["BTN_ACT"],
            activeforeground="#FFFFFF", relief="flat", bd=0, padx=10, pady=4,
            font=ws.FONT_BODY, cursor="hand2")
        self._export_csv_menu = tk.Menu(
            self._export_csv_button, tearoff=0, bg=ws.PALETTE["PANEL"],
            fg=ws.PALETTE["FG"], activebackground=ws.PALETTE["BTN_ACT"],
            activeforeground="#FFFFFF")
        self._export_csv_menu.add_command(label="Traces...",
                                          command=self._on_export_traces_csv)
        self._export_csv_menu.add_command(label="Per-Trace Metrics...",
                                          command=self._on_export_per_trace_csv)
        self._export_csv_menu.add_command(label="Comparison Metrics...",
                                          command=self._on_export_vs_reference_csv)
        self._export_csv_menu.add_command(label="Annotations...",
                                          command=self._on_export_annotations_csv)
        self._export_csv_button.configure(menu=self._export_csv_menu)
        self._export_csv_button.pack(side="right", padx=6)

        self._visibility_frame = tk.Frame(self._right, bg=ws.PALETTE["BG"])
        self._visibility_frame.pack(fill="x", padx=8, pady=4)

        self._fig = Figure(figsize=(6, 4), dpi=100)
        self._fig.patch.set_facecolor(ws.PALETTE["BG"])
        self._ax = self._fig.add_subplot(111)
        self._style_axes()
        self._plot_canvas = FigureCanvasTkAgg(self._fig, master=self._right)
        self._plot_canvas.get_tk_widget().pack(fill="both", expand=True, padx=8, pady=4)
        self._fig.canvas.mpl_connect("button_press_event", self._on_plot_click)

        tables_frame = tk.Frame(self._right, bg=ws.PALETTE["BG"])
        tables_frame.pack(fill="x", padx=8, pady=4)

        per_trace_card = ws.card_frame(tables_frame, "PER-TRACE METRICS")
        per_trace_card.pack(fill="x", pady=(0, 6))
        self._per_trace_tree = self._make_metrics_treeview(
            per_trace_card, self._PER_TRACE_COLS, self._PER_TRACE_HDRS, self._PER_TRACE_W)

        vs_ref_card = ws.card_frame(tables_frame, "VS-REFERENCE METRICS")
        vs_ref_card.pack(fill="x")
        self._vs_ref_tree = self._make_metrics_treeview(
            vs_ref_card, self._VS_REF_COLS, self._VS_REF_HDRS, self._VS_REF_W)

        self._recompute_metrics()

    def _style_axes(self) -> None:
        self._ax.set_facecolor(ws.PALETTE["SURFACE"])
        self._ax.set_xlabel("Time (s)", color=ws.PALETTE["FG2"])
        self._ax.set_ylabel("Knee Angle (deg)", color=ws.PALETTE["FG2"])
        self._ax.tick_params(colors=ws.PALETTE["FG2"])
        for spine in self._ax.spines.values():
            spine.set_color(ws.PALETTE["BORDER"])
        self._ax.grid(True, color=ws.PALETTE["BORDER"], linewidth=0.5, alpha=0.6)

    def _make_metrics_treeview(self, parent, cols, hdrs, widths) -> ttk.Treeview:
        wrap = tk.Frame(parent, bg=ws.PALETTE["PANEL"])
        wrap.pack(fill="x")
        tree = ttk.Treeview(wrap, style="Workbench.Treeview", columns=cols,
                            show="headings", height=4, selectmode="none")
        for key, hdr, w in zip(cols, hdrs, widths):
            tree.heading(key, text=hdr)
            tree.column(key, width=w, anchor="center", stretch=False)
        tree.column(cols[0], anchor="w", stretch=True)
        sb = ttk.Scrollbar(wrap, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=sb.set)
        tree.pack(side="left", fill="x", expand=True)
        sb.pack(side="right", fill="y")
        return tree

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
        self._style_axes()
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
        self._update_export_csv_state()

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
        """Populates both metrics Treeview tables from get_metrics_snapshot()
        -- the same method CSV export (Task 5) reads from, so displayed and
        exported values are always identical. Shows a single 'No data yet'
        placeholder row per table when its source dict is empty, rather
        than rendering a blank (ambiguous empty-vs-broken) table."""
        snapshot = self.get_metrics_snapshot()

        for tree in (self._per_trace_tree, self._vs_ref_tree):
            for item in tree.get_children():
                tree.delete(item)

        if not snapshot["per_trace"]:
            self._per_trace_tree.insert(
                "", "end", values=("No data yet", "", "", "", "", "", ""))
        for label, pt in snapshot["per_trace"].items():
            self._per_trace_tree.insert("", "end", values=(
                label, f"{pt['area_ratio']:.3f}", f"{pt['N']:.1f}",
                f"{pt['f']:.2f}", f"{pt['R2n']:.3f}",
                f"{pt['omega_max_n']:.3f}", f"{pt['omega_min_n']:.3f}"))

        if not snapshot["vs_reference"]:
            self._vs_ref_tree.insert(
                "", "end", values=("No data yet", "", "", "", "", "", ""))
        ref_label = snapshot["reference"]
        for label, result in snapshot["vs_reference"].items():
            if result["status"] == "ok":
                jitter_str = (f"{result['timing_offset_sec']:.3f}"
                             if result["timing_offset_sec"] is not None else "n/a")
                self._vs_ref_tree.insert("", "end", values=(
                    label, ref_label, f"{result['rmse_deg']:.2f}",
                    f"{result['mae_deg']:.2f}", f"{result['lag_sec']:.2f}",
                    jitter_str, "ok"))
            else:
                self._vs_ref_tree.insert("", "end", values=(
                    label, ref_label, "", "", "", "", result["error"]))

        self._update_export_csv_state()

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
        self._update_export_csv_state()

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

    def _update_export_csv_state(self) -> None:
        has_traces = bool(self._traces)
        has_annotations = bool(self._annotations)
        for i in (0, 1, 2):
            self._export_csv_menu.entryconfig(i, state="normal" if has_traces else "disabled")
        self._export_csv_menu.entryconfig(3, state="normal" if has_annotations else "disabled")

    def _meta_ids(self) -> tuple:
        meta = self.controller.get_trial_meta()
        return meta.get("participant_id", ""), meta.get("session_date", "")

    def _default_csv_filename(self, prefix: str) -> str:
        participant_id, session_date = self._meta_ids()
        parts = [prefix, participant_id or "session"] + ([session_date] if session_date else [])
        return "_".join(parts) + ".csv"

    def _prompt_and_write_csv(self, prefix: str, fieldnames: list, rows: list) -> None:
        out_path = filedialog.asksaveasfilename(
            title=f"Save {prefix.replace('_', ' ').title()} CSV",
            initialfile=self._default_csv_filename(prefix),
            defaultextension=".csv",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")])
        if not out_path:
            return
        with open(out_path, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=fieldnames)
            w.writeheader()
            w.writerows(rows)
        messagebox.showinfo("Exported", f"Saved to:\n{out_path}")

    def _on_export_traces_csv(self) -> None:
        participant_id, session_date = self._meta_ids()
        fieldnames, rows = engine.traces_to_csv_rows(self._traces, participant_id, session_date)
        self._prompt_and_write_csv("traces", fieldnames, rows)

    def _on_export_per_trace_csv(self) -> None:
        participant_id, session_date = self._meta_ids()
        snapshot = self.get_metrics_snapshot()
        fieldnames, rows = engine.per_trace_metrics_to_csv_rows(
            snapshot["per_trace"], participant_id, session_date)
        self._prompt_and_write_csv("per_trace_metrics", fieldnames, rows)

    def _on_export_vs_reference_csv(self) -> None:
        participant_id, session_date = self._meta_ids()
        snapshot = self.get_metrics_snapshot()
        fieldnames, rows = engine.vs_reference_metrics_to_csv_rows(
            snapshot["reference"], snapshot["vs_reference"], participant_id, session_date)
        self._prompt_and_write_csv("comparison_metrics", fieldnames, rows)

    def _on_export_annotations_csv(self) -> None:
        participant_id, session_date = self._meta_ids()
        fieldnames, rows = engine.annotations_to_csv_rows(
            self.get_annotations(), participant_id, session_date)
        self._prompt_and_write_csv("annotations", fieldnames, rows)

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
        ws.apply_ttk_theme(self)
        self.configure(bg=ws.PALETTE["BG"])

        self._trial_meta: dict = {}
        self._status_var = tk.StringVar(value="")

        self._load_panel = TrialLoadPanel(self, controller=self)
        self._workbench_view = WorkbenchView(self, controller=self)
        self._load_panel.pack(fill="both", expand=True)
        tk.Label(self, textvariable=self._status_var, anchor="w",
                bg=ws.PALETTE["PANEL"], fg=ws.PALETTE["FG3"],
                font=ws.FONT_SMALL).pack(side="bottom", fill="x", padx=8, pady=2)

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
        fast enough to run inline."""
        traces = {}
        self._trial_meta = {
            "imu_path": selection["imu_path"],
            "video_path": selection["video_path"],
            "optitrack_path": selection["optitrack_path"],
            "participant_id": selection["participant_id"],
            "session_date": selection["session_date"],
            "models": selection["models"],
            "femur_length_cm": selection["femur_length_cm"],
            "tibia_length_cm": selection["tibia_length_cm"],
        }

        if selection["imu_path"]:
            ft_ratio = None
            method_override = None
            if selection["femur_length_cm"] and selection["tibia_length_cm"]:
                ft_ratio = selection["femur_length_cm"] / selection["tibia_length_cm"]
                # Supplying limb lengths means the researcher wants to validate
                # the personalized-ratio Ockendon path (Section 3a) -- ft_ratio
                # alone does nothing unless the IMU trace actually runs through
                # ockendon_deg, so force the method rather than silently no-op
                # if the persisted tuning config's method is "relative".
                method_override = "ockendon_flipped"
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
