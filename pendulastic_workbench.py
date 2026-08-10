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
import pendulastic_storage
import longitudinal_dashboard
import pendulastic_pt_score
import workbench_style as ws

MILESTONE_LABELS = ["First Peak Extension", "Maximum Flexion", "Rest/Settled"]


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
        self._imu_format = tk.StringVar(value="jsonl")
        self._component_paths = {k: tk.StringVar(value="")
                                 for k in ("accel", "gyro", "mag", "imu")}
        self._component_status = {k: tk.StringVar(value="")
                                  for k in ("accel", "gyro", "mag", "imu")}
        self._component_validations: dict = {}
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

        format_row = tk.Frame(files_card, bg=ws.PALETTE["PANEL"])
        format_row.pack(fill="x", pady=3)
        tk.Label(format_row, text="IMU format:", bg=ws.PALETTE["PANEL"],
                 fg=ws.PALETTE["FG"], font=ws.FONT_BODY, width=32, anchor="w").pack(side="left")
        tk.Radiobutton(format_row, text="Single raw log (.jsonl)", variable=self._imu_format,
                      value="jsonl", command=self._on_imu_format_changed,
                      bg=ws.PALETTE["PANEL"], fg=ws.PALETTE["FG"], font=ws.FONT_BODY,
                      selectcolor=ws.PALETTE["SURFACE"], activebackground=ws.PALETTE["PANEL"],
                      activeforeground=ws.PALETTE["FG"]).pack(side="left")
        tk.Radiobutton(format_row, text="Split CSV (4 files)", variable=self._imu_format,
                      value="split_csv", command=self._on_imu_format_changed,
                      bg=ws.PALETTE["PANEL"], fg=ws.PALETTE["FG"], font=ws.FONT_BODY,
                      selectcolor=ws.PALETTE["SURFACE"], activebackground=ws.PALETTE["PANEL"],
                      activeforeground=ws.PALETTE["FG"]).pack(side="left", padx=(12, 0))

        self._imu_jsonl_frame = tk.Frame(files_card, bg=ws.PALETTE["PANEL"])
        self._file_row(self._imu_jsonl_frame, "Phone IMU raw log (.jsonl)", self._imu_path,
                       [("IMU log", "*.jsonl"), ("All files", "*.*")], name="imu")
        self._imu_jsonl_frame.pack(fill="x")

        self._imu_split_frame = tk.Frame(files_card, bg=ws.PALETTE["PANEL"])
        self._build_split_csv_rows(self._imu_split_frame)
        self._imu_split_frame.pack(fill="x")

        self._on_imu_format_changed()

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
        ws.secondary_button(self, "View Participant Dashboard",
                            lambda: self.controller.on_view_dashboard()).pack(pady=(0, 16))

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
            tk.Label(parent, textvariable=self._component_status[kind], anchor="w",
                    justify="left", wraplength=260
                    ).grid(row=i, column=3, sticky="w", padx=(8, 12))

    def _on_imu_format_changed(self) -> None:
        if self._imu_format.get() == "split_csv":
            self._imu_jsonl_frame.pack_forget()
            self._imu_split_frame.pack(fill="x")
        else:
            self._imu_split_frame.pack_forget()
            self._imu_jsonl_frame.pack(fill="x")

    def _browse_component(self, kind: str) -> None:
        path = filedialog.askopenfilename(filetypes=self._COMPONENT_FILETYPES)
        if not path:
            return
        # Validate first, then update the path StringVar and the status
        # label together from the same result -- never leave a stale
        # ok=True validation (from a PREVIOUS successful browse) associated
        # with a path the UI is now displaying that wasn't actually
        # validated (e.g. if this browse's validation fails).
        result = engine.validate_component_csv(path, kind)
        self._component_validations[kind] = dict(result, path=path)
        self._component_paths[kind].set(path)
        if result["ok"]:
            self._component_status[kind].set(
                f"✓ {result['n_samples']} samples @ {result['fs_eff']:.1f} Hz")
        else:
            self._component_status[kind].set(f"✗ {os.path.basename(path)}: {result['error']}")

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

        imu_format = self._imu_format.get()
        return {
            "imu_format": imu_format,
            "imu_path": self._imu_path.get() or None,
            "imu_components": dict(self._component_validations) if imu_format == "split_csv" else {},
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
        self._release_marks: dict = {}       # {label: {"t_trace": float, "source": "auto"|"manual"}}
        self._lag_provenance: dict = {}      # {label: "auto"|"manual"}
        self._armed_release_label: Optional[str] = None
        self._release_artists: dict = {}     # {label: (line_artist, text_artist)}
        self._release_entry_vars: dict = {}  # {label: tk.StringVar}
        self._release_buttons: dict = {}     # {label: tk.Button}
        self._pending_milestone = tk.StringVar(value=MILESTONE_LABELS[0])
        self._raw_diagnostics: Optional[dict] = None
        self._build_widgets()

    _PER_TRACE_COLS = ("label", "pt_score", "mas", "area_ratio", "N", "f_hz", "R2n",
                       "phi_max_ratio", "omega_max_n", "omega_min_n")
    _PER_TRACE_HDRS = ("Trace", "PT(7p)", "MAS", "Area Ratio", "N", "f (Hz)", "R2n",
                       "φmax_ratio", "ωmax_n", "ωmin_n")
    _PER_TRACE_W    = (100, 70, 50, 90, 60, 70, 70, 90, 80, 80)

    _VS_REF_COLS = ("label", "reference", "rmse_deg", "mae_deg", "lag_sec",
                    "timing_offset_sec", "status")
    _VS_REF_HDRS = ("Trace", "Reference", "RMSE (deg)", "MAE (deg)",
                    "Lag (s)", "Timing Offset (s)", "Status")
    _VS_REF_W    = (100, 100, 90, 90, 70, 130, 110)

    def _build_widgets(self) -> None:
        paned = ttk.PanedWindow(self, orient="horizontal",
                                style=ws.STYLE_PANEDWINDOW)
        paned.pack(fill="both", expand=True)

        left = tk.Frame(paned, bg=ws.PALETTE["BG"])
        paned.add(left, weight=1)

        self._video_label = tk.Label(left, bg="black",
                                     highlightbackground=ws.PALETTE["BORDER"],
                                     highlightthickness=1)
        self._video_label.pack(fill="both", expand=True, padx=8, pady=8)

        self._scrubber = ttk.Scale(left, from_=0, to=0, orient="horizontal",
                                   style=ws.STYLE_SCALE,
                                   variable=self._scrub_var, command=self._on_scrub)
        self._scrubber.pack(fill="x", padx=8, pady=4)

        self._right = tk.Frame(paned, bg=ws.PALETTE["BG"])
        paned.add(self._right, weight=1)

        top_controls = tk.Frame(self._right, bg=ws.PALETTE["BG"])
        top_controls.pack(fill="x", padx=8, pady=4)
        tk.Label(top_controls, text="Reference:", bg=ws.PALETTE["BG"],
                 fg=ws.PALETTE["FG"], font=ws.FONT_BODY).pack(side="left")
        self._reference_menu = ttk.OptionMenu(top_controls, self._reference_var, "",
                                              style=ws.STYLE_MENUBUTTON)
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
                      MILESTONE_LABELS[0], *MILESTONE_LABELS,
                      style=ws.STYLE_MENUBUTTON).pack(side="left", padx=6)
        ws.secondary_button(annot_toolbar, "Mark Here",
                            self._on_mark_milestone).pack(side="left", padx=6)
        ws.secondary_button(annot_toolbar, "Save Trial to Dashboard",
                            self._on_save_trial_clicked).pack(side="right", padx=6)
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

        # Pack order matters here (and is the reason the tables are built
        # before the plot canvas even though they render *below* it): pack
        # honors each child's requested size in packing order and only hands
        # leftover space to expand=True children. With the plot canvas packed
        # first, its 400px requested figure height was claimed before the
        # tables were considered, and at App's own minsize(900, 600) the
        # vs-reference table got 0 height and never mapped. Packing the tables
        # first with side="bottom" reserves their requested height (and keeps
        # them visually below the plot), leaving the canvas to absorb whatever
        # remains.
        tables_frame = tk.Frame(self._right, bg=ws.PALETTE["BG"])
        tables_frame.pack(side="bottom", fill="x", padx=8, pady=4)

        per_trace_card = ws.card_frame(tables_frame, "PER-TRACE METRICS")
        per_trace_card.pack(fill="x", pady=(0, 6))
        self._per_trace_tree = self._make_metrics_treeview(
            per_trace_card, self._PER_TRACE_COLS, self._PER_TRACE_HDRS, self._PER_TRACE_W)

        vs_ref_card = ws.card_frame(tables_frame, "VS-REFERENCE METRICS")
        vs_ref_card.pack(fill="x", pady=(0, 6))
        self._vs_ref_tree = self._make_metrics_treeview(
            vs_ref_card, self._VS_REF_COLS, self._VS_REF_HDRS, self._VS_REF_W)

        raw_diag_card = ws.card_frame(tables_frame, "RAW SENSOR CROSS-CHECKS")
        raw_diag_card.pack(fill="x")
        self._raw_diag_label = tk.Label(
            raw_diag_card, text="(independent of PT score fusion -- none loaded)",
            bg=ws.PALETTE["PANEL"], fg=ws.PALETTE["FG"], font=ws.FONT_BODY,
            justify="left", anchor="w")
        self._raw_diag_label.pack(fill="x", padx=8, pady=4)

        self._fig = Figure(figsize=(6, 4), dpi=100)
        self._fig.patch.set_facecolor(ws.PALETTE["BG"])
        self._ax = self._fig.add_subplot(111)
        self._style_axes()
        self._plot_canvas = FigureCanvasTkAgg(self._fig, master=self._right)
        self._plot_canvas.get_tk_widget().pack(side="top", fill="both", expand=True,
                                               padx=8, pady=4)
        self._fig.canvas.mpl_connect("button_press_event", self._on_plot_click)

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
        tree = ttk.Treeview(wrap, style=ws.STYLE_TREEVIEW, columns=cols,
                            show="headings", height=4, selectmode="none")
        for key, hdr, w in zip(cols, hdrs, widths):
            tree.heading(key, text=hdr)
            tree.column(key, width=w, anchor="center", stretch=False)
        tree.column(cols[0], anchor="w", stretch=True)
        sb = ttk.Scrollbar(wrap, orient="vertical", style=ws.STYLE_SCROLLBAR,
                           command=tree.yview)
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
        self._release_entry_vars = {}
        self._release_buttons = {}
        self._armed_release_label = None

        for label, (t, angle) in traces.items():
            # Styled "chip" per trace (design spec Section 4) -- card-colored
            # with a border and consistent padding, so this row reads as part
            # of the dark toolbar stack instead of a band of default-gray
            # system widgets between the toolbar and the plot.
            row = tk.Frame(self._visibility_frame, bg=ws.PALETTE["PANEL"],
                           padx=6, pady=3,
                           highlightbackground=ws.PALETTE["BORDER"],
                           highlightthickness=1)
            row.pack(side="left", padx=4)

            var = prev_visible[label] if label in prev_visible else tk.BooleanVar(value=True)
            self._visible_vars[label] = var
            tk.Checkbutton(row, text=label, variable=var,
                          command=self._on_visibility_changed,
                          bg=ws.PALETTE["PANEL"], fg=ws.PALETTE["FG"],
                          font=ws.FONT_BODY, selectcolor=ws.PALETTE["SURFACE"],
                          activebackground=ws.PALETTE["PANEL"],
                          activeforeground=ws.PALETTE["FG"]).pack(side="left")

            lag_var = prev_lag[label] if label in prev_lag else tk.StringVar(value="")
            self._lag_override_vars[label] = lag_var
            tk.Label(row, text="lag(s):", bg=ws.PALETTE["PANEL"],
                     fg=ws.PALETTE["FG2"], font=ws.FONT_SMALL).pack(side="left")
            lag_entry = tk.Entry(row, textvariable=lag_var, width=6,
                                 bg=ws.PALETTE["SURFACE"], fg=ws.PALETTE["FG"],
                                 insertbackground=ws.PALETTE["FG"],
                                 relief="flat", highlightthickness=1,
                                 highlightbackground=ws.PALETTE["BORDER"],
                                 font=ws.FONT_BODY)
            lag_entry.pack(side="left")
            lag_entry.bind("<Return>", lambda e, l=label: self._on_lag_entry_commit(l))
            lag_entry.bind("<FocusOut>", lambda e, l=label: self._on_lag_entry_commit(l))

            existing_mark = self._release_marks.get(label)
            release_var = tk.StringVar(
                value=(f"{existing_mark['t_trace']:.3f}" if existing_mark else ""))
            self._release_entry_vars[label] = release_var
            release_btn = tk.Button(
                row, text="Mark Release", command=lambda l=label: self._on_arm_release(l),
                bg=ws.PALETTE["BTN"], fg=ws.PALETTE["FG"], relief="flat", bd=0,
                padx=6, pady=2, font=ws.FONT_SMALL, cursor="hand2")
            release_btn.pack(side="left", padx=(6, 2))
            self._release_buttons[label] = release_btn
            release_entry = tk.Entry(row, textvariable=release_var, width=6,
                                     bg=ws.PALETTE["SURFACE"], fg=ws.PALETTE["FG"],
                                     insertbackground=ws.PALETTE["FG"], relief="flat",
                                     highlightthickness=1, highlightbackground=ws.PALETTE["BORDER"],
                                     font=ws.FONT_BODY)
            release_entry.pack(side="left")
            release_entry.bind("<Return>", lambda e, l=label: self._on_release_entry_commit(l))
            release_entry.bind("<FocusOut>", lambda e, l=label: self._on_release_entry_commit(l))
            tk.Button(row, text="Clear", command=lambda l=label: self._on_clear_release(l),
                      bg=ws.PALETTE["BTN"], fg=ws.PALETTE["FG"], relief="flat", bd=0,
                      padx=6, pady=2, font=ws.FONT_SMALL, cursor="hand2").pack(side="left", padx=(2, 6))

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
          (area_ratio, N, f, etc.) as the sub-metric breakdown, plus the
          composite Popović PT score and MAS estimate (pt_score, mas) --
          computed from pendulastic_pt_score.compute_pt_params (the
          function HEALTHY_REF was calibrated against), NOT from the
          windowed params, whose omega_min_n/phi_max_ratio/area_ratio are
          on different scales. pt_score/mas are both None together when
          compute_pt_params returns None (insufficient signal) for that
          trace. A per-modality diagnostic, not a comparison. Includes the
          reference trace itself.
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
            params = engine.windowed_pt_params(t, y)
            full_params = pendulastic_pt_score.compute_pt_params(t, y)
            if full_params is not None:
                params["pt_score"] = pendulastic_pt_score.compute_pt_score(full_params)
                params["mas"] = pendulastic_pt_score.pt_to_mas(params["pt_score"])
            else:
                params["pt_score"] = None
                params["mas"] = None
            out["per_trace"][label] = params

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
            if pt["pt_score"] is not None:
                pt_str = f"{pt['pt_score']:.3f}"
                mas_str = str(pt["mas"])
            else:
                pt_str = "n/a"
                mas_str = "n/a"
            self._per_trace_tree.insert("", "end", values=(
                label, pt_str, mas_str,
                f"{pt['area_ratio']:.3f}", f"{pt['N']:.1f}",
                f"{pt['f']:.2f}", f"{pt['R2n']:.3f}", f"{pt['phi_max_ratio']:.3f}",
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

        if self._raw_diagnostics is not None:
            peak_vel = self._raw_diagnostics["peak_gyro_velocity_dps"]
            release_t = self._raw_diagnostics["accel_release_time_sec"]
            release_str = (f"t={release_t:.2f}s" if release_t is not None
                           else "unavailable (sample rate too low)")
            self._raw_diag_label.configure(
                text=f"Peak angular velocity (raw gyro): {peak_vel:.1f} deg/s   |   "
                     f"Release detected (raw accel, 5Hz low-pass): {release_str}")
        else:
            self._raw_diag_label.configure(
                text="(independent of PT score fusion -- none loaded)")

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

    def _on_lag_entry_commit(self, label: str) -> None:
        self._lag_provenance[label] = "manual"
        self._recompute_metrics()

    def _on_arm_release(self, label: str) -> None:
        if (self._armed_release_label is not None
                and self._armed_release_label in self._release_buttons):
            self._release_buttons[self._armed_release_label].configure(text="Mark Release")
        self._armed_release_label = label
        self._release_buttons[label].configure(text="Click plot…")

    def _on_clear_release(self, label: str) -> None:
        self._release_marks.pop(label, None)
        self._lag_provenance.pop(label, None)
        if label in self._release_entry_vars:
            self._release_entry_vars[label].set("")
        if label in self._release_artists:
            for artist in self._release_artists[label]:
                if artist.axes is not None:
                    artist.remove()
            del self._release_artists[label]
        if self._armed_release_label == label:
            self._armed_release_label = None
            self._release_buttons[label].configure(text="Mark Release")
        self._plot_canvas.draw_idle()
        self._recompute_release_lags()

    def _on_release_entry_commit(self, label: str) -> None:
        text = self._release_entry_vars[label].get().strip()
        if not text:
            return
        try:
            t_val = float(text)
        except ValueError:
            return
        self._release_marks[label] = {"t_trace": t_val, "source": "manual"}
        self._draw_release_artist(label, t_val)
        self._plot_canvas.draw_idle()
        self._recompute_release_lags()

    def _draw_release_artist(self, label: str, t_trace: float) -> None:
        if label in self._release_artists:
            for artist in self._release_artists[label]:
                if artist.axes is not None:
                    artist.remove()
            del self._release_artists[label]
        color = (self._trace_lines[label].get_color()
                if label in self._trace_lines else "#DC2626")
        line_artist = self._ax.axvline(t_trace, color=color, linewidth=1.2, linestyle=":")
        text_artist = self._ax.annotate(
            f"R:{label}", xy=(t_trace, self._ax.get_ylim()[1]),
            rotation=90, va="top", ha="right", fontsize=7, color=color)
        self._release_artists[label] = (line_artist, text_artist)

    def _recompute_release_lags(self) -> None:
        self._recompute_metrics()

    def _draw_milestone_artist(self, label: str, t_sec: float) -> None:
        if not hasattr(self, "_annotation_artists"):
            self._annotation_artists = {}
        if label in self._annotation_artists:
            for artist in self._annotation_artists[label]:
                artist.remove()
        text_artist = self._ax.annotate(
            label, xy=(t_sec, self._ax.get_ylim()[1]),
            rotation=90, va="top", ha="right", fontsize=7, color="#DC2626")
        line_artist = self._ax.axvline(t_sec, color="#DC2626", linewidth=0.8, linestyle="--")
        self._annotation_artists[label] = (text_artist, line_artist)

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

    def set_raw_diagnostics(self, diagnostics: Optional[dict]) -> None:
        self._raw_diagnostics = diagnostics
        self._recompute_metrics()

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

    def _visible_traces(self) -> dict:
        """Only the currently-checked traces, mirroring the visibility filter
        get_metrics_snapshot() applies (a trace with no visibility var is
        treated as visible). Without this the traces CSV would export hidden
        traces that the per-trace and vs-reference CSVs -- both fed from
        get_metrics_snapshot() -- have already excluded."""
        return {label: pair for label, pair in self._traces.items()
                if self._visible_vars.get(label, tk.BooleanVar(value=True)).get()}

    def _on_export_traces_csv(self) -> None:
        participant_id, session_date = self._meta_ids()
        fieldnames, rows = engine.traces_to_csv_rows(
            self._visible_traces(), participant_id, session_date)
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

    def _save_current_trial(self, participant_id: str, leg: str,
                            session_label: str, date: str) -> None:
        """Core save logic, factored out of the dialog's Save button so
        tests can call it directly without simulating Toplevel widgets."""
        snapshot = self.get_metrics_snapshot()
        visible_traces = {
            label: (t, y) for label, (t, y) in self._traces.items()
            if self._visible_vars.get(label, tk.BooleanVar(value=True)).get()
        }
        metrics_by_label = {
            label: snapshot["per_trace"][label] for label in visible_traces
            if label in snapshot["per_trace"]
        }
        pendulastic_storage.save_trial(
            participant_id, leg, session_label, date,
            visible_traces, metrics_by_label, self._reference_var.get())

    def _reference_trace_pt_score(self):
        """The current reference trace's pt_score (float), or None if
        there's no reference trace or its pt_score is None (insufficient
        signal). The save dialog refuses to save when this is None."""
        snapshot = self.get_metrics_snapshot()
        ref_label = self._reference_var.get()
        per_trace = snapshot["per_trace"].get(ref_label)
        if per_trace is None:
            return None
        return per_trace["pt_score"]

    def _on_save_trial_clicked(self) -> None:
        import datetime as _datetime

        dialog = tk.Toplevel(self)
        dialog.title("Save Trial to Dashboard")
        dialog.transient(self)

        participant_var = tk.StringVar(value="")
        leg_var = tk.StringVar(value="left")
        label_var = tk.StringVar(value="")
        date_var = tk.StringVar(value=_datetime.date.today().isoformat())
        status_var = tk.StringVar(value="")

        tk.Label(dialog, text="Participant ID:").grid(row=0, column=0, sticky="w", padx=8, pady=4)
        tk.Entry(dialog, textvariable=participant_var).grid(row=0, column=1, padx=8, pady=4)

        tk.Label(dialog, text="Leg:").grid(row=1, column=0, sticky="w", padx=8, pady=4)
        ttk.OptionMenu(dialog, leg_var, "left", "left", "right").grid(
            row=1, column=1, sticky="w", padx=8, pady=4)

        tk.Label(dialog, text="Session label:").grid(row=2, column=0, sticky="w", padx=8, pady=4)
        tk.Entry(dialog, textvariable=label_var).grid(row=2, column=1, padx=8, pady=4)

        tk.Label(dialog, text="Date (YYYY-MM-DD):").grid(row=3, column=0, sticky="w", padx=8, pady=4)
        tk.Entry(dialog, textvariable=date_var).grid(row=3, column=1, padx=8, pady=4)

        tk.Label(dialog, textvariable=status_var, fg="#B45309").grid(
            row=4, column=0, columnspan=2, sticky="w", padx=8, pady=(0, 4))

        def on_confirm() -> None:
            participant_id = participant_var.get().strip()
            session_label = label_var.get().strip()
            date = date_var.get().strip()
            if not participant_id or not session_label:
                status_var.set("Participant ID and session label are required.")
                return
            try:
                _datetime.datetime.strptime(date, "%Y-%m-%d")
            except ValueError:
                status_var.set("Date must be YYYY-MM-DD.")
                return
            if self._reference_trace_pt_score() is None:
                status_var.set(
                    "Reference trace has no usable PT score (insufficient signal) -- "
                    "cannot save.")
                return

            leg = leg_var.get()
            existing = pendulastic_storage.load_history(participant_id)
            already_present = any(
                s["label"] == session_label and s["date"] == date
                for s in existing["legs"][leg]["sessions"])
            if already_present and not messagebox.askyesno(
                    "Overwrite session?",
                    f"A session '{session_label}' on {date} already exists for "
                    f"{participant_id}/{leg}. Overwrite it?"):
                return

            self._save_current_trial(participant_id, leg, session_label, date)
            dialog.destroy()
            messagebox.showinfo("Saved", f"Trial saved to {participant_id}/{leg}.")

        button_row = tk.Frame(dialog)
        button_row.grid(row=5, column=0, columnspan=2, pady=8)
        tk.Button(button_row, text="Save", command=on_confirm).pack(side="left", padx=6)
        tk.Button(button_row, text="Cancel", command=dialog.destroy).pack(side="left", padx=6)

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
        """Clicking the plot seeks the video to the nearest frame -- unless
        a trace is currently armed for release-marking (self._armed_release_
        label), in which case the click marks that trace's release instead
        and does not also seek the video."""
        if event.inaxes is not self._ax or event.xdata is None:
            return
        if self._armed_release_label is not None:
            self._mark_release_at(self._armed_release_label, event.xdata)
            return
        if self._fps <= 0:
            return
        fi = int(round(event.xdata * self._fps))
        fi = max(0, min(fi, self._n_frames - 1))
        self._scrub_var.set(fi)
        self._on_scrub(str(fi))

    def _mark_release_at(self, label: str, x_click: float) -> None:
        """Snaps x_click to `label`'s own nearest actual sample timestamp --
        event.xdata is already in that trace's native time coordinates,
        since each trace was plotted as self._ax.plot(t, angle) with its
        own t array (design spec Section 2a)."""
        t_arr, _y_arr = self._traces.get(label, (np.array([]), np.array([])))
        if len(t_arr) == 0:
            self._armed_release_label = None
            if label in self._release_buttons:
                self._release_buttons[label].configure(text="Mark Release")
            return
        idx = int(np.argmin(np.abs(t_arr - x_click)))
        snapped_t = float(t_arr[idx])
        self._release_marks[label] = {"t_trace": snapped_t, "source": "manual"}
        self._release_entry_vars[label].set(f"{snapped_t:.3f}")
        self._draw_release_artist(label, snapped_t)
        self._armed_release_label = None
        self._release_buttons[label].configure(text="Mark Release")
        self._plot_canvas.draw_idle()
        self._recompute_release_lags()

    def current_frame_index(self) -> int:
        """Reads the scrubber's bound Tkinter variable directly -- never
        infers the frame from canvas paint state (design spec Section 6's
        stale-frame binding requirement)."""
        return int(round(self._scrub_var.get()))

    def current_time_sec(self) -> float:
        return self.current_frame_index() / self._fps if self._fps > 0 else 0.0


class DashboardView(tk.Frame):
    """Participant Dashboard: participant/leg/trace-label picker that
    renders longitudinal_dashboard.render_dashboard()'s 3-panel figure.

    controller: App instance -- receives on_dashboard_back()."""

    def __init__(self, parent, controller) -> None:
        super().__init__(parent)
        self.controller = controller
        self._participant_var = tk.StringVar(value="")
        self._leg_var = tk.StringVar(value="left")
        self._trace_var = tk.StringVar(value="")
        self._status_var = tk.StringVar(value="")
        self._canvas = None
        self._build_widgets()

    def _build_widgets(self) -> None:
        top = tk.Frame(self)
        top.pack(fill="x", padx=8, pady=6)

        tk.Button(top, text="← Back",
                 command=lambda: self.controller.on_dashboard_back()).pack(side="left")

        tk.Label(top, text="Participant:").pack(side="left", padx=(12, 2))
        self._participant_menu = ttk.OptionMenu(top, self._participant_var, "")
        self._participant_menu.pack(side="left")

        tk.Label(top, text="Leg:").pack(side="left", padx=(12, 2))
        ttk.OptionMenu(top, self._leg_var, "left", "left", "right").pack(side="left")

        tk.Label(top, text="Trace:").pack(side="left", padx=(12, 2))
        self._trace_menu = ttk.OptionMenu(top, self._trace_var, "")
        self._trace_menu.pack(side="left")

        tk.Button(top, text="Load", command=self._on_load_clicked).pack(side="left", padx=12)

        tk.Label(self, textvariable=self._status_var, fg="#B45309").pack(fill="x", padx=8)

        self._canvas_frame = tk.Frame(self)
        self._canvas_frame.pack(fill="both", expand=True, padx=8, pady=4)

    def refresh_participants(self) -> None:
        """Repopulates the participant dropdown from disk -- called each
        time the panel is shown, since trials may have been saved since
        the dropdown was last built."""
        ids = pendulastic_storage.list_participant_ids()
        menu = self._participant_menu["menu"]
        menu.delete(0, "end")
        for pid in ids:
            menu.add_command(label=pid, command=lambda p=pid: self._participant_var.set(p))
        if ids and self._participant_var.get() not in ids:
            self._participant_var.set(ids[0])

    def _on_load_clicked(self) -> None:
        participant_id = self._participant_var.get().strip()
        leg = self._leg_var.get()
        if not participant_id:
            self._status_var.set("Select a participant.")
            return

        history = pendulastic_storage.load_history(participant_id)
        skipped = history.get("_skipped", [])
        if skipped:
            self._status_var.set(
                f"Skipped {len(skipped)} corrupted session(s) for participant "
                f"{history['participant_id']}.")
        else:
            self._status_var.set("")

        trace_labels = sorted({
            label
            for session in history["legs"][leg]["sessions"]
            for label in session.get("traces", {})
        })
        menu = self._trace_menu["menu"]
        menu.delete(0, "end")
        for label in trace_labels:
            menu.add_command(label=label, command=lambda l=label: self._trace_var.set(l))
        if trace_labels and self._trace_var.get() not in trace_labels:
            self._trace_var.set(trace_labels[0])
        elif not trace_labels:
            self._trace_var.set("")

        for widget in self._canvas_frame.winfo_children():
            widget.destroy()
        self._canvas = None

        trace_label = self._trace_var.get()
        if not trace_label:
            return

        fig = longitudinal_dashboard.render_dashboard(history, leg, trace_label)
        self._canvas = FigureCanvasTkAgg(fig, master=self._canvas_frame)
        self._canvas.get_tk_widget().pack(fill="both", expand=True)
        self._canvas.draw_idle()


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
        self._imu_reference: list = []
        self._status_var = tk.StringVar(value="")

        self._load_panel = TrialLoadPanel(self, controller=self)
        self._workbench_view = WorkbenchView(self, controller=self)
        self._dashboard_view = DashboardView(self, controller=self)
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

    def on_view_dashboard(self) -> None:
        self._load_panel.pack_forget()
        self._workbench_view.pack_forget()
        self._dashboard_view.refresh_participants()
        self._dashboard_view.pack(fill="both", expand=True)

    def on_dashboard_back(self) -> None:
        self._dashboard_view.pack_forget()
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
            "participant_id": selection["participant_id"],
            "session_date": selection["session_date"],
            "models": selection["models"],
            "femur_length_cm": selection["femur_length_cm"],
            "tibia_length_cm": selection["tibia_length_cm"],
        }

        ft_ratio = None
        method_override = None
        if selection["femur_length_cm"] and selection["tibia_length_cm"]:
            # Both limb lengths supplied means the researcher wants the
            # personalized-ratio Ockendon path validated -- force the
            # method rather than silently no-op if the persisted config's
            # method is "relative".
            ft_ratio = selection["femur_length_cm"] / selection["tibia_length_cm"]
            method_override = "ockendon_flipped"

        if imu_format == "split_csv":
            components = selection.get("imu_components", {})
            if all(components.get(k, {}).get("ok") for k in ("accel", "gyro", "mag", "imu")):
                try:
                    t, angle, imu_reference = engine.load_imu_trial_from_components(
                        components, ft_ratio=ft_ratio, method=method_override)
                    traces["imu"] = (t, angle)
                    self._trial_meta["imu_paths"] = {
                        k: components.get(k, {}).get("path")
                        for k in ("accel", "gyro", "mag", "imu")}
                    # imu_reference (the full parsed raw-IMU row list) is
                    # kept off self._trial_meta so it never flows into
                    # export_session()'s output -- it can be megabytes for a
                    # real trial. Stored separately for in-memory
                    # cross-check use only.
                    self._imu_reference = imu_reference
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
