"""
pendulastic_ui.py
=================
Pendulastic Recording & Analysis Dashboard.

  Phase 1  Metadata entry + Start / Stop recording
  Phase 2  Automated post-processing (model inference + annotated videos)
  Phase 3  Interactive visualization dashboard

Run:
    .venv\\Scripts\\python.exe pendulastic_ui.py
Then open:  http://localhost:5050
"""

from __future__ import annotations

import json
import math
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Optional

import cv2
import numpy as np
import pandas as pd

from flask import Flask, jsonify, render_template_string, request
from flask_socketio import SocketIO

from natnet_client import NatNetClient

# ─────────────────────────────────────────────────────────────────────────────
# CONFIGURATION
# ─────────────────────────────────────────────────────────────────────────────

BASE_DIR     = r"C:\Users\cladi\Pendulastic"
OPTI_ROOT    = os.path.join(BASE_DIR, "OptiTrack_Recordings")
RECORD_ROOT  = os.path.join(BASE_DIR, "Recordings")
PIPELINE_SCRIPT = os.path.join(BASE_DIR, "run_pipeline_all.py")
EVAL_SCRIPT     = os.path.join(BASE_DIR, "evaluate_all_participants.py")
PYTHON_EXE      = os.path.join(BASE_DIR, ".venv", "Scripts", "python.exe")

# Approximate model parameters (millions) for complexity scatter
MODEL_PARAMS: dict = {
    "mediapipe":  3.9,  "movenet": 14.5,
    "rtmpose":   13.4,  "mmpose":  13.4,
    "yolo":       2.6,  "detrpose": 11.6,
    "pose2sim":  13.4,  "fremocap": 3.9,
}

# ─────────────────────────────────────────────────────────────────────────────
# FLASK + SOCKETIO
# ─────────────────────────────────────────────────────────────────────────────

app = Flask(__name__)
app.config["SECRET_KEY"] = "pendulastic-secret"
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")

# ─────────────────────────────────────────────────────────────────────────────
# RECORDING STATE
# ─────────────────────────────────────────────────────────────────────────────

class RecordingSession:
    def __init__(self):
        self.reset()

    def reset(self):
        self.active        = False
        self.metadata: dict = {}
        self.video_path    = ""
        self.opti_csv_path = ""
        self._cap: Optional[cv2.VideoCapture] = None
        self._writer: Optional[cv2.VideoWriter] = None
        self._natnet: Optional[NatNetClient]     = None
        self._video_thread: Optional[threading.Thread] = None
        self._stop_event   = threading.Event()

    # ── Output path builder ────────────────────────────────────────────────────
    def build_paths(self, meta: dict) -> tuple[str, str, str]:
        pid    = meta["participant_id"].strip()
        pos    = meta["position"].strip()
        height = re.sub(r"\s+", "_", meta["height"].strip())
        trial  = meta["trial_number"].strip()

        vid_dir  = os.path.join(RECORD_ROOT, f"Participant_{pid}",
                                f"Position_{pos}", f"Height_{height}")
        opti_dir = os.path.join(OPTI_ROOT,  f"Participant_{pid}",
                                f"Position_{pos}", f"Height_{height}")
        os.makedirs(vid_dir,  exist_ok=True)
        os.makedirs(opti_dir, exist_ok=True)

        video_path    = os.path.join(vid_dir,  f"Trial_{trial}.avi")
        opti_csv_path = os.path.join(opti_dir, f"trial_{trial}_optitrack.csv")
        return video_path, opti_csv_path, trial

    # ── Start ─────────────────────────────────────────────────────────────────
    def start(self, meta: dict, natnet_thigh_id: int, natnet_shank_id: int,
              camera_index: int) -> str:
        if self.active:
            return "Already recording."
        self.reset()
        self.metadata = meta
        self._stop_event.clear()

        video_path, opti_csv_path, _ = self.build_paths(meta)
        self.video_path    = video_path
        self.opti_csv_path = opti_csv_path

        # ── Webcam ───────────────────────────────────────────────────────────
        cap = cv2.VideoCapture(camera_index, cv2.CAP_DSHOW)
        if not cap.isOpened():
            cap = cv2.VideoCapture(camera_index)
        if not cap.isOpened():
            return f"Cannot open camera index {camera_index}."

        fps  = cap.get(cv2.CAP_PROP_FPS) or 30.0
        w    = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        h    = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        fourcc = cv2.VideoWriter_fourcc(*"XVID")
        writer = cv2.VideoWriter(video_path, fourcc, fps, (w, h))
        self._cap    = cap
        self._writer = writer

        # ── NatNet ───────────────────────────────────────────────────────────
        natnet = NatNetClient(thigh_id=natnet_thigh_id, shank_id=natnet_shank_id)

        def _on_frame(frame_no, t, thigh, shank):
            if thigh and shank:
                angle = NatNetClient.compute_knee_angle(thigh, shank)
                if not math.isnan(angle):
                    socketio.emit("live_angle", {"angle": round(angle, 1),
                                                  "frame": frame_no})

        natnet.on_frame = _on_frame
        natnet.start()
        self._natnet = natnet

        # ── Webcam capture thread ─────────────────────────────────────────────
        def _capture():
            while not self._stop_event.is_set():
                ok, frame = cap.read()
                if not ok:
                    break
                writer.write(frame)
            cap.release()
            writer.release()

        self._video_thread = threading.Thread(target=_capture, daemon=True)
        self._video_thread.start()
        self.active = True

        _emit_status(f"Recording started — saving to {os.path.basename(video_path)}", "recording")
        return "ok"

    # ── Stop ──────────────────────────────────────────────────────────────────
    def stop(self) -> tuple[str, str]:
        if not self.active:
            return "", ""
        self._stop_event.set()
        if self._video_thread:
            self._video_thread.join(timeout=5)
        if self._natnet:
            self._natnet.stop()
            self._natnet.save_csv(self.opti_csv_path)
            _emit_status(f"OptiTrack CSV saved: {os.path.basename(self.opti_csv_path)}", "processing")
        video_path, opti_csv_path = self.video_path, self.opti_csv_path
        self.active = False
        return video_path, opti_csv_path


SESSION = RecordingSession()


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _emit_status(msg: str, state: str = "idle") -> None:
    socketio.emit("status", {"message": msg, "state": state})
    print(f"[{state.upper()}] {msg}")


def _run_pipeline_async(video_path: str, opti_csv_path: str) -> None:
    """Run run_pipeline_all.py then evaluate_all_participants.py in a background thread."""

    def _worker():
        _emit_status("Running pose estimation pipeline…", "processing")
        try:
            result = subprocess.run(
                [PYTHON_EXE, PIPELINE_SCRIPT],
                cwd=BASE_DIR, capture_output=True, text=True, timeout=3600,
            )
            if result.returncode != 0:
                _emit_status(f"Pipeline error: {result.stderr[-300:]}", "error")
                return
        except subprocess.TimeoutExpired:
            _emit_status("Pipeline timed out (>60 min).", "error")
            return
        except Exception as exc:
            _emit_status(f"Pipeline exception: {exc}", "error")
            return

        _emit_status("Computing knee angle analysis…", "processing")
        try:
            subprocess.run(
                [PYTHON_EXE, EVAL_SCRIPT],
                cwd=BASE_DIR, timeout=1200,
            )
        except Exception as exc:
            _emit_status(f"Evaluation error: {exc}", "error")
            return

        _emit_status("Analysis complete. Loading dashboard…", "done")
        socketio.emit("processing_done", {})

    threading.Thread(target=_worker, daemon=True).start()


# ─────────────────────────────────────────────────────────────────────────────
# CHART DATA BUILDERS
# ─────────────────────────────────────────────────────────────────────────────

def _load_leaderboard() -> pd.DataFrame:
    path = os.path.join(BASE_DIR, "pendulastic model analysis",
                        "all_participants", "global_model_leaderboard.csv")
    if not os.path.isfile(path):
        return pd.DataFrame()
    return pd.read_csv(path, index_col=0)


def _load_trial_flexion(pid: str, pos: str, trial: str):
    """
    Returns (opti_t, opti_flex, model_traces) where
    model_traces = [{label, t, flex}]
    """
    from scipy.signal import find_peaks
    from scipy.spatial.transform import Rotation as R

    opti_dir = os.path.join(OPTI_ROOT, f"Participant_{pid}",
                            f"Position_{pos}", "Height_Joint-Level")
    opti_paths = list(Path(opti_dir).glob(f"*trial*{trial}*optitrack.csv"))
    if not opti_paths:
        return None, None, []

    opti_path = str(opti_paths[0])

    # Find header row
    header_idx = 0
    with open(opti_path, encoding="utf-8-sig") as fh:
        for i, line in enumerate(fh):
            if line.split(",")[0].strip().lower() == "frame":
                header_idx = i; break

    df_o = pd.read_csv(opti_path, skiprows=header_idx).apply(pd.to_numeric, errors="coerce").ffill().bfill()
    o_t  = df_o.iloc[:, 1].values.astype(float); o_t -= o_t[0]
    o_fs = max(1, round(1.0 / float(np.median(np.diff(o_t[o_t > 0])))))

    tx, ty, tz, tw = (df_o.iloc[:, c].values for c in [2,3,4,5])
    sx, sy, sz, sw = (df_o.iloc[:, c].values for c in [9,10,11,12])
    r_t = R.from_quat(np.column_stack([tx, ty, tz, tw]))
    r_s = R.from_quat(np.column_stack([sx, sy, sz, sw]))
    o_ang = np.degrees((r_t.inv() * r_s).magnitude())

    # Release detection
    n_base    = int(3.0 * o_fs)
    baseline  = float(np.nanmean(o_ang[:n_base]))
    search    = np.abs(o_ang[n_base:] - baseline)
    hits      = np.where(search > 5.0)[0]
    if len(hits) == 0:
        hits = np.where(search > 1.0)[0]
    rel_idx   = int(n_base + hits[0]) if len(hits) else 0

    o_flex  = o_ang[rel_idx:] - o_ang[rel_idx]
    o_t_rel = o_t[rel_idx:] - o_t[rel_idx]

    # Model traces
    model_traces = []
    csv_re = re.compile(
        r"^P_(\w+)_Pos_(\w+)_H_.+?_T_(\d+)_([A-Za-z]\w*)_(.+)\.csv$", re.I)
    for root in [opti_dir, os.path.join(RECORD_ROOT, f"Participant_{pid}",
                                         f"Position_{pos}", "Height_Joint-Level")]:
        if not os.path.isdir(root):
            continue
        for csv_f in sorted(Path(root).glob("*.csv")):
            bn = csv_f.name
            m  = csv_re.match(bn)
            if not m:
                continue
            if m.group(3) != trial:
                continue
            family, rest = m.group(4).lower(), m.group(5)
            label = f"{family}_{rest.replace('_', ' ')}"
            try:
                df_m = pd.read_csv(csv_f)
                m_t  = df_m["time_sec"].values.astype(float)
                m_ang = df_m["knee_angle_deg"].values.astype(float)
            except Exception:
                continue

            m_fps = round(1.0 / float(np.median(np.diff(m_t[np.isfinite(m_t)])))) if len(m_t) > 1 else 30

            # Release detection for model
            below = np.where(m_ang < 100)[0]
            if len(below) == 0:
                rel_m = int(np.argmax(m_ang))
            else:
                rel_m = 0
                for thr in [165, 155, 145, 135, 125]:
                    mask = (np.arange(len(m_ang)) < below[0]) & (m_ang >= thr)
                    hits_m = np.where(mask)[0]
                    if len(hits_m):
                        rel_m = int(hits_m[-1]); break

            m_flex  = m_ang[rel_m] - m_ang[rel_m:]
            m_t_rel = m_t[rel_m:] - m_t[rel_m]

            # Peak-match time shift
            def _peaks(sig, fps):
                guard = max(1, int(0.1*fps))
                hits2, _ = find_peaks(sig[guard:], distance=max(5, int(0.3*fps)),
                                      prominence=3.0, height=5.0)
                return hits2 + guard

            o_pks = _peaks(o_flex, o_fs)
            m_pks = _peaks(m_flex, m_fps)
            shift = (float(o_t_rel[o_pks[0]]) - float(m_t_rel[m_pks[0]])) if len(o_pks) and len(m_pks) else 0.0

            model_traces.append({
                "label": label,
                "t":     (m_t_rel + shift).tolist(),
                "flex":  m_flex.tolist(),
            })

    return o_t_rel.tolist(), o_flex.tolist(), model_traces


def _build_overlay_chart(pid, pos, trial) -> dict:
    o_t, o_flex, traces = _load_trial_flexion(pid, pos, trial)
    if o_t is None:
        return {}

    data = [{
        "type": "scatter", "mode": "lines",
        "name": "OptiTrack",
        "x": o_t, "y": o_flex,
        "line": {"color": "#000000", "width": 3},
    }]
    palette = ["#1976D2","#E65100","#2E7D32","#6A1B9A","#B71C1C",
               "#00695C","#F57F17","#4527A0","#37474F"]
    for i, tr in enumerate(traces):
        data.append({
            "type": "scatter", "mode": "lines",
            "name": tr["label"],
            "x": tr["t"], "y": tr["flex"],
            "line": {"color": palette[i % len(palette)], "width": 1.5},
            "opacity": 0.8,
        })

    layout = {
        "title": f"P{pid} | Pos{pos} | Trial {trial} — Knee Flexion (t=0 = release)",
        "xaxis": {"title": "Time (s)", "range": [-0.3, 14]},
        "yaxis": {"title": "Flexion deviation (deg)"},
        "legend": {"orientation": "v", "x": 1.01},
        "hovermode": "x unified",
        "plot_bgcolor": "#1a1a2e", "paper_bgcolor": "#0f0f1a",
        "font": {"color": "#e0e0e0"},
        "shapes": [{"type": "line", "x0": 0, "x1": 0, "yref": "paper",
                    "y0": 0, "y1": 1, "line": {"color": "#ff4444", "width": 2,
                                                 "dash": "dash"}}],
    }
    return {"data": data, "layout": layout}


def _build_swing_rmse_chart(pid, pos, trial) -> dict:
    lb = _load_leaderboard()
    if lb.empty:
        return {}

    _, _, traces = _load_trial_flexion(pid, pos, trial)
    if not traces:
        return {}

    # For each model compute per-swing error against optitrack
    _, o_flex, _ = _load_trial_flexion(pid, pos, trial)
    return {}   # placeholder — full version below uses per_swing from evaluator


def _build_complexity_chart() -> dict:
    lb = _load_leaderboard()
    if lb.empty:
        return {}

    x_vals, y_vals, labels, colors = [], [], [], []
    palette = ["#1976D2","#E65100","#2E7D32","#6A1B9A","#B71C1C",
               "#00695C","#F57F17","#4527A0","#37474F","#BF360C"]

    for i, row in lb.iterrows():
        family = str(row.get("family", "")).lower()
        params = MODEL_PARAMS.get(family, 10.0)
        rmse   = float(row.get("rmse_deg", 0))
        label  = str(row.get("variant", ""))
        x_vals.append(params); y_vals.append(rmse); labels.append(label)
        colors.append(palette[i % len(palette)])

    data = [{
        "type": "scatter", "mode": "markers+text",
        "x": x_vals, "y": y_vals,
        "text": labels,
        "textposition": "top center",
        "marker": {"size": 12, "color": colors,
                   "line": {"color": "#ffffff", "width": 1}},
        "hovertemplate": "<b>%{text}</b><br>Params: %{x:.1f}M<br>RMSE: %{y:.1f}°<extra></extra>",
    }]
    layout = {
        "title": "Model Complexity vs Accuracy",
        "xaxis": {"title": "Parameters (M)", "type": "log"},
        "yaxis": {"title": "RMSE (deg)"},
        "plot_bgcolor": "#1a1a2e", "paper_bgcolor": "#0f0f1a",
        "font": {"color": "#e0e0e0"},
        "showlegend": False,
        "annotations": [{"x": 3, "y": 50,
                          "text": "← More efficient", "showarrow": False,
                          "font": {"color": "#888888", "size": 10}}],
    }
    return {"data": data, "layout": layout}


# ─────────────────────────────────────────────────────────────────────────────
# ROUTES
# ─────────────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template_string(HTML_TEMPLATE)


@app.route("/api/start", methods=["POST"])
def api_start():
    meta = request.json or {}
    required = ["participant_id", "position", "height", "trial_number"]
    missing  = [f for f in required if not meta.get(f, "").strip()]
    if missing:
        return jsonify({"error": f"Missing fields: {', '.join(missing)}"}), 400

    thigh_id = int(meta.get("natnet_thigh_id", 1))
    shank_id = int(meta.get("natnet_shank_id", 2))
    cam_idx  = int(meta.get("camera_index",    0))

    result = SESSION.start(meta, thigh_id, shank_id, cam_idx)
    if result != "ok":
        return jsonify({"error": result}), 500
    return jsonify({"status": "recording"})


@app.route("/api/stop", methods=["POST"])
def api_stop():
    video_path, opti_csv = SESSION.stop()
    if not video_path:
        return jsonify({"error": "No active session."}), 400
    _emit_status("Recording stopped. Starting analysis…", "processing")
    _run_pipeline_async(video_path, opti_csv)
    return jsonify({"status": "processing",
                    "video": video_path, "optitrack": opti_csv})


@app.route("/api/charts/overlay")
def api_overlay():
    pid   = request.args.get("pid",   "0")
    pos   = request.args.get("pos",   "1")
    trial = request.args.get("trial", "1")
    return jsonify(_build_overlay_chart(pid, pos, trial))


@app.route("/api/charts/complexity")
def api_complexity():
    return jsonify(_build_complexity_chart())


@app.route("/api/leaderboard")
def api_leaderboard():
    lb = _load_leaderboard()
    if lb.empty:
        return jsonify([])
    return jsonify(lb.reset_index().to_dict("records"))


@app.route("/api/sessions")
def api_sessions():
    """Return list of available (participant, position, trial) combos."""
    combos = []
    for root in [OPTI_ROOT]:
        for opti_csv in Path(root).glob("**/trial_*_optitrack.csv"):
            parts = opti_csv.parts
            pid = pos = None
            for p in parts:
                m = re.match(r"Participant_(\w+)", p, re.I)
                if m: pid = m.group(1)
                m = re.match(r"Position_(\w+)", p, re.I)
                if m: pos = m.group(1)
            m = re.match(r"trial_(\d+)_optitrack", opti_csv.stem, re.I)
            trial = m.group(1) if m else "?"
            if pid and pos:
                combos.append({"pid": pid, "pos": pos, "trial": trial})
    return jsonify(combos)


@socketio.on("connect")
def on_connect():
    _emit_status("Connected to Pendulastic server.", "idle")


# ─────────────────────────────────────────────────────────────────────────────
# HTML TEMPLATE
# ─────────────────────────────────────────────────────────────────────────────

HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Pendulastic — Recording & Analysis</title>
<script src="https://cdn.socket.io/4.7.2/socket.io.min.js"></script>
<script src="https://cdn.plot.ly/plotly-2.30.0.min.js"></script>
<style>
  :root {
    --bg:        #0f0f1a;
    --panel:     #1a1a2e;
    --border:    #2a2a4a;
    --accent:    #4f8ef7;
    --accent2:   #00d4aa;
    --danger:    #ff4444;
    --warn:      #ffa500;
    --text:      #e0e0f0;
    --muted:     #888899;
    --success:   #00cc88;
  }
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
  body { background: var(--bg); color: var(--text); font-family: 'Segoe UI', system-ui, sans-serif;
         font-size: 14px; min-height: 100vh; }

  /* ── Layout ─────────────────────────────────────────────── */
  .layout { display: grid; grid-template-columns: 360px 1fr;
             grid-template-rows: auto 1fr; min-height: 100vh; gap: 0; }
  .sidebar { grid-row: 1 / 3; background: var(--panel); border-right: 1px solid var(--border);
              padding: 24px 20px; display: flex; flex-direction: column; gap: 20px; overflow-y: auto; }
  .topbar  { background: var(--panel); border-bottom: 1px solid var(--border);
              padding: 14px 24px; display: flex; align-items: center; gap: 16px; }
  .main    { padding: 24px; overflow-y: auto; }

  /* ── Topbar ──────────────────────────────────────────────── */
  .logo    { font-size: 20px; font-weight: 700; color: var(--accent); letter-spacing: 1px; }
  .status-pill { display: flex; align-items: center; gap: 8px; margin-left: auto;
                  background: var(--bg); border: 1px solid var(--border);
                  border-radius: 20px; padding: 6px 14px; font-size: 13px; }
  .dot { width: 10px; height: 10px; border-radius: 50%; background: var(--muted); }
  .dot.idle       { background: var(--muted); }
  .dot.recording  { background: var(--danger); animation: pulse 1s infinite; }
  .dot.processing { background: var(--warn);   animation: pulse 0.8s infinite; }
  .dot.done       { background: var(--success); }
  .dot.error      { background: var(--danger); }
  @keyframes pulse { 0%,100%{opacity:1} 50%{opacity:0.3} }

  .live-angle { font-size: 13px; color: var(--accent2);
                 background: var(--bg); border: 1px solid var(--border);
                 border-radius: 8px; padding: 6px 12px; }

  /* ── Sidebar sections ────────────────────────────────────── */
  .section { display: flex; flex-direction: column; gap: 12px; }
  .section-title { font-size: 11px; font-weight: 600; letter-spacing: 1.5px;
                    color: var(--muted); text-transform: uppercase; }

  label   { font-size: 12px; color: var(--muted); display: block; margin-bottom: 4px; }
  input   { width: 100%; background: var(--bg); border: 1px solid var(--border);
             border-radius: 8px; padding: 9px 12px; color: var(--text); font-size: 14px;
             transition: border-color .15s; outline: none; }
  input:focus { border-color: var(--accent); }
  .form-row { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }

  /* ── Buttons ─────────────────────────────────────────────── */
  .btn { border: none; border-radius: 10px; padding: 12px 20px; font-size: 14px;
          font-weight: 600; cursor: pointer; width: 100%; transition: all .15s;
          display: flex; align-items: center; justify-content: center; gap: 8px; }
  .btn-start  { background: var(--success); color: #000; }
  .btn-start:hover { filter: brightness(1.15); }
  .btn-stop   { background: var(--danger);  color: #fff; }
  .btn-stop:hover  { filter: brightness(1.15); }
  .btn-refresh { background: var(--accent); color: #fff; }
  .btn-refresh:hover { filter: brightness(1.15); }
  .btn:disabled { opacity: 0.4; cursor: not-allowed; }

  /* ── Log ─────────────────────────────────────────────────── */
  #log { background: var(--bg); border: 1px solid var(--border);
          border-radius: 8px; padding: 12px; height: 160px; overflow-y: auto;
          font-family: 'Consolas', monospace; font-size: 12px; }
  .log-line { padding: 2px 0; color: var(--muted); }
  .log-line.info       { color: var(--text); }
  .log-line.recording  { color: #ff8888; }
  .log-line.processing { color: var(--warn); }
  .log-line.done       { color: var(--success); }
  .log-line.error      { color: var(--danger); }

  /* ── Main panel sections ─────────────────────────────────── */
  .card { background: var(--panel); border: 1px solid var(--border);
           border-radius: 12px; padding: 20px; margin-bottom: 20px; }
  .card-title { font-size: 14px; font-weight: 600; color: var(--accent);
                 margin-bottom: 14px; display: flex; align-items: center; gap: 8px; }

  /* ── Session selector ────────────────────────────────────── */
  .session-bar { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }
  select { background: var(--bg); border: 1px solid var(--border); border-radius: 8px;
            padding: 8px 12px; color: var(--text); font-size: 14px; cursor: pointer; }

  /* ── Leaderboard table ───────────────────────────────────── */
  table  { width: 100%; border-collapse: collapse; font-size: 13px; }
  th     { background: var(--bg); color: var(--muted); font-weight: 600;
            padding: 8px 12px; text-align: left; font-size: 11px; letter-spacing: 0.5px;
            border-bottom: 1px solid var(--border); }
  td     { padding: 8px 12px; border-bottom: 1px solid var(--border); color: var(--text); }
  tr:hover td { background: rgba(79,142,247,.06); }
  .rank-1 td { color: #ffd700; font-weight: 700; }
  .rank-2 td { color: #c0c0c0; font-weight: 600; }
  .rank-3 td { color: #cd7f32; }
  .rmse-bar { height: 6px; border-radius: 3px; background: var(--accent);
               display: inline-block; vertical-align: middle; margin-left: 6px; }

  /* ── Chart containers ────────────────────────────────────── */
  .chart-box { width: 100%; min-height: 400px; }

  /* ── Spinner ─────────────────────────────────────────────── */
  .spinner { display: none; width: 32px; height: 32px; border: 3px solid var(--border);
              border-top-color: var(--accent); border-radius: 50%;
              animation: spin 0.8s linear infinite; margin: 20px auto; }
  @keyframes spin { to { transform: rotate(360deg); } }
</style>
</head>
<body>
<div class="layout">

  <!-- ── Sidebar ──────────────────────────────────────────── -->
  <aside class="sidebar">
    <div class="section-title">Session Metadata</div>
    <div class="section">
      <div class="form-row">
        <div>
          <label>Participant ID</label>
          <input id="f-pid"   placeholder="e.g. 2"  value="">
        </div>
        <div>
          <label>Trial Number</label>
          <input id="f-trial" placeholder="e.g. 1"  value="">
        </div>
      </div>
      <div>
        <label>Camera Position</label>
        <input id="f-pos" placeholder="e.g. 2" value="">
      </div>
      <div>
        <label>Camera Height &amp; Distance</label>
        <input id="f-height" placeholder="e.g. Joint-Level" value="Joint-Level">
      </div>
    </div>

    <div class="section-title">Hardware Settings</div>
    <div class="section">
      <div class="form-row">
        <div>
          <label>Webcam Index</label>
          <input id="f-cam" type="number" value="0" min="0">
        </div>
        <div>
          <label>OptiTrack IP</label>
          <input id="f-optiip" placeholder="127.0.0.1" value="127.0.0.1">
        </div>
      </div>
      <div class="form-row">
        <div>
          <label>Thigh RB ID</label>
          <input id="f-thigh" type="number" value="1" min="1">
        </div>
        <div>
          <label>Shank RB ID</label>
          <input id="f-shank" type="number" value="2" min="1">
        </div>
      </div>
    </div>

    <button class="btn btn-start" id="btn-start" onclick="startRecording()">
      &#9654; Start Recording Session
    </button>
    <button class="btn btn-stop" id="btn-stop" onclick="stopRecording()" disabled>
      &#9632; End Recording Session
    </button>

    <div class="section-title">Live Telemetry</div>
    <div id="log"></div>

    <button class="btn btn-refresh" onclick="loadDashboard()" style="margin-top:auto">
      &#8635; Refresh Dashboard
    </button>
  </aside>

  <!-- ── Top bar ──────────────────────────────────────────── -->
  <header class="topbar">
    <span class="logo">PENDULASTIC</span>
    <span style="color:var(--muted);font-size:12px">Knee Spasticity Analysis Suite</span>
    <div class="live-angle" id="live-angle">Knee: — °</div>
    <div class="status-pill">
      <div class="dot idle" id="status-dot"></div>
      <span id="status-text">Idle</span>
    </div>
  </header>

  <!-- ── Main panel ───────────────────────────────────────── -->
  <main class="main">

    <!-- Session selector -->
    <div class="card">
      <div class="card-title">&#128250; View Session Data</div>
      <div class="session-bar">
        <div>
          <label>Participant</label>
          <select id="sel-pid" onchange="loadDashboard()"></select>
        </div>
        <div>
          <label>Position</label>
          <select id="sel-pos" onchange="loadDashboard()"></select>
        </div>
        <div>
          <label>Trial</label>
          <select id="sel-trial" onchange="loadDashboard()"></select>
        </div>
      </div>
    </div>

    <!-- Overlay chart -->
    <div class="card">
      <div class="card-title">&#128200; Synchronized Knee Flexion — All Models (t=0 = release anchor)</div>
      <div class="spinner" id="spin-overlay"></div>
      <div class="chart-box" id="chart-overlay"></div>
    </div>

    <!-- Complexity scatter -->
    <div class="card">
      <div class="card-title">&#128308; Model Complexity vs Accuracy (RMSE vs Parameters)</div>
      <div class="spinner" id="spin-complexity"></div>
      <div class="chart-box" id="chart-complexity"></div>
    </div>

    <!-- Leaderboard -->
    <div class="card">
      <div class="card-title">&#127942; Global Model Leaderboard</div>
      <div class="spinner" id="spin-lb"></div>
      <div id="leaderboard-container"></div>
    </div>

  </main>
</div>

<script>
const socket = io();
let state = 'idle';

// ── Socket.IO events ──────────────────────────────────────────────────────────
socket.on('status', d => {
  addLog(d.message, d.state);
  setState(d.state);
});
socket.on('live_angle', d => {
  document.getElementById('live-angle').textContent = `Knee: ${d.angle}°`;
});
socket.on('processing_done', () => {
  loadDashboard();
  setState('done');
});

// ── State management ──────────────────────────────────────────────────────────
function setState(s) {
  state = s;
  document.getElementById('status-text').textContent =
    {idle:'Idle', recording:'Recording…', processing:'Processing…',
     done:'Analysis Ready', error:'Error'}[s] || s;
  const dot = document.getElementById('status-dot');
  dot.className = `dot ${s}`;
  document.getElementById('btn-start').disabled = s === 'recording' || s === 'processing';
  document.getElementById('btn-stop').disabled  = s !== 'recording';
}

// ── Log ───────────────────────────────────────────────────────────────────────
function addLog(msg, cls='info') {
  const log = document.getElementById('log');
  const ts  = new Date().toLocaleTimeString();
  const el  = document.createElement('div');
  el.className = `log-line ${cls}`;
  el.textContent = `[${ts}] ${msg}`;
  log.appendChild(el);
  log.scrollTop = log.scrollHeight;
}

// ── Recording ─────────────────────────────────────────────────────────────────
async function startRecording() {
  const meta = {
    participant_id: document.getElementById('f-pid').value,
    trial_number:   document.getElementById('f-trial').value,
    position:       document.getElementById('f-pos').value,
    height:         document.getElementById('f-height').value,
    natnet_thigh_id: document.getElementById('f-thigh').value,
    natnet_shank_id: document.getElementById('f-shank').value,
    camera_index:   document.getElementById('f-cam').value,
  };
  const resp = await fetch('/api/start', {
    method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify(meta),
  });
  const data = await resp.json();
  if (data.error) { addLog(`Error: ${data.error}`, 'error'); return; }
  setState('recording');
  addLog('Recording started.', 'recording');
}

async function stopRecording() {
  setState('processing');
  const resp = await fetch('/api/stop', { method: 'POST' });
  const data = await resp.json();
  if (data.error) { addLog(`Error: ${data.error}`, 'error'); return; }
  addLog('Recording stopped. Running analysis pipeline…', 'processing');
}

// ── Dashboard ─────────────────────────────────────────────────────────────────
async function populateSelectors() {
  const resp = await fetch('/api/sessions');
  const sessions = await resp.json();
  if (!sessions.length) return;

  const pids   = [...new Set(sessions.map(s => s.pid))].sort();
  const poses  = [...new Set(sessions.map(s => s.pos))].sort();
  const trials = [...new Set(sessions.map(s => s.trial))].sort();

  function fill(id, vals) {
    const sel = document.getElementById(id);
    sel.innerHTML = vals.map(v => `<option value="${v}">${v}</option>`).join('');
  }
  fill('sel-pid',   pids);
  fill('sel-pos',   poses);
  fill('sel-trial', trials);
}

async function loadDashboard() {
  const pid   = document.getElementById('sel-pid').value;
  const pos   = document.getElementById('sel-pos').value;
  const trial = document.getElementById('sel-trial').value;

  if (!pid) return;

  // Overlay chart
  show('spin-overlay', true);
  const oResp = await fetch(`/api/charts/overlay?pid=${pid}&pos=${pos}&trial=${trial}`);
  const oData = await oResp.json();
  show('spin-overlay', false);
  if (oData.data) Plotly.newPlot('chart-overlay', oData.data, oData.layout,
    {responsive:true, displayModeBar:false});

  // Complexity scatter
  show('spin-complexity', true);
  const cResp = await fetch('/api/charts/complexity');
  const cData = await cResp.json();
  show('spin-complexity', false);
  if (cData.data) Plotly.newPlot('chart-complexity', cData.data, cData.layout,
    {responsive:true, displayModeBar:false});

  // Leaderboard
  show('spin-lb', true);
  const lResp = await fetch('/api/leaderboard');
  const lb    = await lResp.json();
  show('spin-lb', false);
  renderLeaderboard(lb);
}

function show(id, vis) {
  document.getElementById(id).style.display = vis ? 'block' : 'none';
}

function renderLeaderboard(rows) {
  if (!rows.length) {
    document.getElementById('leaderboard-container').innerHTML =
      '<p style="color:var(--muted);padding:12px">No leaderboard data yet. Run a session first.</p>';
    return;
  }
  const maxRmse = Math.max(...rows.map(r => r.rmse_deg || 0));
  const cols = ['variant','family','n_participants','n_extrema','rmse_deg','mae_deg','max_err_deg'];
  const head = ['Model Variant','Family','Participants','Extrema','RMSE (°)','MAE (°)','Max Err (°)'];

  let html = '<table><thead><tr>' +
    head.map(h => `<th>${h}</th>`).join('') +
    '</tr></thead><tbody>';

  rows.forEach((r, i) => {
    const cls = i===0?'rank-1':i===1?'rank-2':i===2?'rank-3':'';
    const pct = ((r.rmse_deg||0) / maxRmse * 120).toFixed(0);
    html += `<tr class="${cls}">
      <td>${r.variant||'—'}</td>
      <td>${r.family||'—'}</td>
      <td style="text-align:center">${r.n_participants||0}</td>
      <td style="text-align:center">${r.n_extrema||0}</td>
      <td>${(r.rmse_deg||0).toFixed(1)}
        <span class="rmse-bar" style="width:${pct}px;opacity:0.5"></span>
      </td>
      <td>${(r.mae_deg||0).toFixed(1)}</td>
      <td>${(r.max_err_deg||0).toFixed(1)}</td>
    </tr>`;
  });
  html += '</tbody></table>';
  document.getElementById('leaderboard-container').innerHTML = html;
}

// ── Init ──────────────────────────────────────────────────────────────────────
window.addEventListener('load', async () => {
  await populateSelectors();
  loadDashboard();
});
</script>
</body>
</html>
"""

# ─────────────────────────────────────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("Pendulastic UI starting at http://localhost:5050")
    socketio.run(app, host="0.0.0.0", port=5050, debug=False)
