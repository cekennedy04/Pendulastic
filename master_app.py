"""
==============================================================================
 Part 1 - LAPTOP MASTER APPLICATION (GUI + Webcam + UDP Trigger)
==============================================================================
 Multi-camera biomechanics benchmarking acquisition tool.

 This application runs on the laptop that drives the experiment. It:
   * Collects participant metadata through a Tkinter GUI.
   * Records a 30 fps webcam video on a dedicated background thread so the
     GUI never freezes.
   * Saves video to:  [Root]/Participant_[ID]/Position_[X]/Height_[Y]/Trial_[Z].avi
   * Writes metadata.json into the participant folder.
   * Broadcasts a START / STOP packet over UDP port 5005 so the OptiTrack
     slave machine starts/stops Motive recording in sync.

 Run on Windows with:   python master_app.py

 Requires:   pip install opencv-python
 (tkinter, socket, threading, json, os are part of the standard library.)
==============================================================================
"""

import os
import time
import json
import socket
import threading
from datetime import datetime

import tkinter as tk
from tkinter import ttk, messagebox

# On Windows, the MSMF backend can hang for 30-120 seconds opening a USB camera
# because of hardware Media Foundation Transforms. Disabling them makes camera
# open near-instant. This MUST be set before OpenCV (cv2) is imported.
os.environ.setdefault("OPENCV_VIDEOIO_MSMF_ENABLE_HW_TRANSFORMS", "0")

# OpenCV is the only third-party dependency. Guard the import so a missing
# package produces a clear instruction instead of a raw traceback.
try:
    import cv2
except ImportError:
    # Tkinter may not be up yet, so fall back to a console message too.
    import sys
    print("ERROR: OpenCV is not installed. Run:  pip install opencv-python")
    try:
        _root = tk.Tk()
        _root.withdraw()
        messagebox.showerror(
            "Missing Dependency",
            "OpenCV (cv2) is not installed.\n\n"
            "Open Command Prompt and run:\n\n    pip install opencv-python"
        )
    except Exception:
        pass
    sys.exit(1)


# -----------------------------------------------------------------------------
# Configuration constants
# -----------------------------------------------------------------------------
ROOT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "Recordings")
TARGET_FPS = 30.0          # Forced capture/write rate.
UDP_PORT = 5005            # Must match the slave listener.
UDP_BROADCAST_IP = "255.255.255.255"   # Broadcast to the whole subnet.
FRAME_WIDTH = 1280
FRAME_HEIGHT = 720

# Capture backends to probe, in order. On Windows 11, MSMF often enumerates USB
# UVC webcams (e.g. Logitech) that the older DSHOW backend misses; DSHOW is kept
# as a fallback. The selected camera carries its own backend so recording opens
# it exactly the way the probe found it.
CAMERA_BACKENDS = [("MSMF", cv2.CAP_MSMF), ("DSHOW", cv2.CAP_DSHOW)]
MAX_CAMERA_INDEX = 5       # Probe indices 0..MAX_CAMERA_INDEX.
PREVIEW_WINDOW = "Pendulastic Camera"   # Fixed window name for the live preview.


def read_with_warmup(cap, attempts=15, delay=0.1):
    """
    Try to read a frame, retrying to absorb MSMF/USB warm-up latency.

    The MSMF backend often fails the first read() right after opening a camera
    (it returns before the stream is flowing). Returns (ok, frame).
    """
    for _ in range(attempts):
        ret, frame = cap.read()
        if ret and frame is not None:
            return True, frame
        time.sleep(delay)
    return False, None


def enumerate_cameras():
    """
    Probe for working cameras across the preferred backends.

    Returns a list of dicts: {"index", "backend", "backend_name", "label"}.
    A camera index already found on an earlier (preferred) backend is not
    re-listed for a later backend, so the Logitech shows up once.
    """
    found = []
    seen_indices = set()
    for backend_name, backend_flag in CAMERA_BACKENDS:
        for idx in range(MAX_CAMERA_INDEX + 1):
            if idx in seen_indices:
                continue
            cap = cv2.VideoCapture(idx, backend_flag)
            ok = cap.isOpened()
            ret = False
            if ok:
                # Warm-up read so a flaky first frame doesn't hide a good camera.
                ret, _ = read_with_warmup(cap, attempts=8, delay=0.05)
            cap.release()
            if ok and ret:
                seen_indices.add(idx)
                found.append({
                    "index": idx,
                    "backend": backend_flag,
                    "backend_name": backend_name,
                    "label": f"Camera {idx} ({backend_name})",
                })
    return found


class MasterApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Biomechanics Master - Acquisition Control")
        self.root.geometry("480x780")
        self.root.resizable(False, False)

        # ---- Camera + recording state (thread-safe) ----
        self.streaming_flag = threading.Event()   # preview thread runs while set
        self.writing_flag = threading.Event()     # frames written to disk while set
        self.cam_thread = None
        self.cap = None
        self.out = None
        self.out_lock = threading.Lock()          # guards self.out across threads
        self.frame_size = (FRAME_WIDTH, FRAME_HEIGHT)
        self._codec_warmed = False                # XVID DLL pre-loaded once

        self._build_ui()

    # ------------------------------------------------------------------
    # UI CONSTRUCTION
    # ------------------------------------------------------------------
    def _build_ui(self):
        pad = {"padx": 10, "pady": 4}

        title = tk.Label(self.root, text="Participant & Trial Setup",
                         font=("Segoe UI", 13, "bold"))
        title.grid(row=0, column=0, columnspan=2, pady=(12, 8))

        # --- Participant ID ---
        tk.Label(self.root, text="Participant ID:").grid(row=1, column=0, sticky="e", **pad)
        self.entry_id = tk.Entry(self.root, width=28)
        self.entry_id.grid(row=1, column=1, sticky="w", **pad)

        # --- Age ---
        tk.Label(self.root, text="Age:").grid(row=2, column=0, sticky="e", **pad)
        self.entry_age = tk.Entry(self.root, width=28)
        self.entry_age.grid(row=2, column=1, sticky="w", **pad)

        # --- Weight ---
        tk.Label(self.root, text="Weight (kg):").grid(row=3, column=0, sticky="e", **pad)
        self.entry_weight = tk.Entry(self.root, width=28)
        self.entry_weight.grid(row=3, column=1, sticky="w", **pad)

        # --- Sex dropdown ---
        tk.Label(self.root, text="Sex:").grid(row=4, column=0, sticky="e", **pad)
        self.var_sex = tk.StringVar(value="Female")
        self.drop_sex = ttk.Combobox(self.root, textvariable=self.var_sex, width=25,
                                     state="readonly",
                                     values=["Female", "Male", "Intersex", "Prefer not to say"])
        self.drop_sex.grid(row=4, column=1, sticky="w", **pad)

        # --- Diagnosis dropdown ---
        tk.Label(self.root, text="Diagnosis:").grid(row=5, column=0, sticky="e", **pad)
        self.var_diag = tk.StringVar(value="MS")
        self.drop_diag = ttk.Combobox(self.root, textvariable=self.var_diag, width=25,
                                      state="readonly",
                                      values=["MS", "Stroke", "Unaffected Control",
                                              "Other Motor Impairment"])
        self.drop_diag.grid(row=5, column=1, sticky="w", **pad)

        ttk.Separator(self.root, orient="horizontal").grid(
            row=6, column=0, columnspan=2, sticky="ew", padx=10, pady=10)

        cfg = tk.Label(self.root, text="Camera Configuration",
                       font=("Segoe UI", 13, "bold"))
        cfg.grid(row=7, column=0, columnspan=2, pady=(0, 8))

        # --- Camera (auto-detected; single-camera rig) ---
        tk.Label(self.root, text="Camera:").grid(row=8, column=0, sticky="e", **pad)
        cam_frame = tk.Frame(self.root)
        cam_frame.grid(row=8, column=1, sticky="w", **pad)
        self.var_cam = tk.StringVar(value="")
        self.lbl_cam = tk.Label(cam_frame, textvariable=self.var_cam, width=18,
                                relief="sunken", anchor="w", bg="white")
        self.lbl_cam.pack(side="left", ipady=1)
        self.btn_rescan = tk.Button(cam_frame, text="Rescan", command=self.rescan_cameras)
        self.btn_rescan.pack(side="left", padx=(6, 0))

        # --- Camera Position 1-3 ---
        tk.Label(self.root, text="Camera Position:").grid(row=9, column=0, sticky="e", **pad)
        self.var_pos = tk.StringVar(value="1")
        self.drop_pos = ttk.Combobox(self.root, textvariable=self.var_pos, width=25,
                                     state="readonly",
                                     values=["1", "2", "3"])
        self.drop_pos.grid(row=9, column=1, sticky="w", **pad)

        # --- Camera Height ---
        tk.Label(self.root, text="Camera Height:").grid(row=10, column=0, sticky="e", **pad)
        self.var_height = tk.StringVar(value="Joint-Level")
        self.drop_height = ttk.Combobox(self.root, textvariable=self.var_height, width=25,
                                        state="readonly",
                                        values=["Low", "Joint-Level", "High"])
        self.drop_height.grid(row=10, column=1, sticky="w", **pad)

        # --- Trial Number ---
        tk.Label(self.root, text="Trial Number:").grid(row=11, column=0, sticky="e", **pad)
        self.var_trial = tk.StringVar(value="1")
        self.drop_trial = ttk.Combobox(self.root, textvariable=self.var_trial, width=25,
                                       state="readonly",
                                       values=["1", "2", "3"])
        self.drop_trial.grid(row=11, column=1, sticky="w", **pad)

        ttk.Separator(self.root, orient="horizontal").grid(
            row=12, column=0, columnspan=2, sticky="ew", padx=10, pady=10)

        # --- Control buttons ---
        self.btn_start = tk.Button(self.root, text="START RECORDING",
                                   bg="#1e7d34", fg="white",
                                   font=("Segoe UI", 11, "bold"),
                                   width=18, height=2, command=self.start_recording)
        self.btn_start.grid(row=13, column=0, padx=10, pady=12)

        self.btn_stop = tk.Button(self.root, text="STOP",
                                  bg="#a31515", fg="white",
                                  font=("Segoe UI", 11, "bold"),
                                  width=18, height=2, command=self.stop_recording,
                                  state="disabled")
        self.btn_stop.grid(row=13, column=1, padx=10, pady=12)

        ttk.Separator(self.root, orient="horizontal").grid(
            row=14, column=0, columnspan=2, sticky="ew", padx=10, pady=10)

        # --- Batch evaluation (active only when not recording) ---
        self.btn_evaluate = tk.Button(self.root, text="RUN BATCH EVALUATION",
                                      bg="#1f3a93", fg="white",
                                      font=("Segoe UI", 12, "bold"),
                                      height=2, command=self.start_batch_evaluation)
        self.btn_evaluate.grid(row=15, column=0, columnspan=2, sticky="ew",
                               padx=10, pady=(0, 8))

        # --- Status bar ---
        self.var_status = tk.StringVar(value="Idle - ready to record.")
        self.lbl_status = tk.Label(self.root, textvariable=self.var_status,
                                   relief="sunken", anchor="w", fg="#333")
        self.lbl_status.grid(row=16, column=0, columnspan=2, sticky="ew",
                             padx=10, pady=(4, 10))

        # Detect the camera now that all widgets exist.
        self._cameras = []
        self._active_cam = None
        self.rescan_cameras()

        # Make sure resources are released if the window is closed.
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    # ------------------------------------------------------------------
    # CAMERA SELECTION
    # ------------------------------------------------------------------
    def rescan_cameras(self):
        """Detect the connected camera, then pre-open it so START is instant."""
        if self.writing_flag.is_set():
            return  # never rescan mid-recording
        # Drop any existing stream before re-probing.
        self._close_camera()

        self.var_status.set("Scanning for camera...")
        self.root.update_idletasks()
        try:
            self._cameras = enumerate_cameras()
        except Exception as e:
            self._cameras = []
            messagebox.showerror(
                "Camera Scan Failed",
                f"Could not scan for the camera:\n\n{type(e).__name__}: {e}"
            )

        # Single-camera rig: use the first camera found (MSMF is probed first).
        self._active_cam = self._cameras[0] if self._cameras else None
        if self._active_cam is not None:
            self.var_cam.set(self._active_cam["label"])
            # Pre-open + start the live preview so pressing START is instant.
            self._open_camera()
        else:
            self.var_cam.set("(none detected)")
            self.var_status.set(
                "No camera detected - check USB / close other apps, then Rescan."
            )

    def _selected_camera(self):
        """Return (index, backend) for the active camera, or raise ValueError."""
        if self._active_cam is None:
            raise ValueError(
                "No camera detected. Plug in the USB webcam and click 'Rescan'."
            )
        return self._active_cam["index"], self._active_cam["backend"]

    # ------------------------------------------------------------------
    # INPUT VALIDATION
    # ------------------------------------------------------------------
    def _validate_inputs(self):
        """Return a sanitized participant ID or raise ValueError."""
        pid = self.entry_id.get().strip()
        if not pid:
            raise ValueError("Participant ID cannot be empty.")
        # Block characters that are illegal in Windows folder names.
        illegal = set('<>:"/\\|?*')
        if any(ch in illegal for ch in pid):
            raise ValueError('Participant ID contains illegal characters: < > : " / \\ | ? *')

        age = self.entry_age.get().strip()
        if age and not age.isdigit():
            raise ValueError("Age must be a whole number.")

        weight = self.entry_weight.get().strip()
        if weight:
            try:
                float(weight)
            except ValueError:
                raise ValueError("Weight must be a number (e.g. 72.5).")
        return pid

    # ------------------------------------------------------------------
    # PATH + METADATA HELPERS
    # ------------------------------------------------------------------
    def _build_paths(self, pid):
        """Build and create the directory tree. Returns (participant_dir, video_path, rel_path)."""
        position = self.var_pos.get()
        height = self.var_height.get()
        trial = self.var_trial.get()

        participant_dir = os.path.join(ROOT_DIR, f"Participant_{pid}")
        trial_dir = os.path.join(participant_dir,
                                 f"Position_{position}",
                                 f"Height_{height}")
        # exist_ok=True makes this safe to call repeatedly.
        os.makedirs(trial_dir, exist_ok=True)

        video_path = os.path.join(trial_dir, f"Trial_{trial}.avi")

        # Relative path is what the slave machine recreates under its own root.
        rel_path = os.path.join(f"Participant_{pid}",
                                f"Position_{position}",
                                f"Height_{height}")
        return participant_dir, video_path, rel_path

    def _write_metadata(self, participant_dir, pid):
        """Write/refresh metadata.json in the participant folder."""
        metadata = {
            "participant_id": pid,
            "age": self.entry_age.get().strip(),
            "weight_kg": self.entry_weight.get().strip(),
            "sex": self.var_sex.get(),
            "diagnosis": self.var_diag.get(),
            "last_trial": {
                "camera_position": self.var_pos.get(),
                "camera_height": self.var_height.get(),
                "trial_number": self.var_trial.get(),
            },
            "last_updated": datetime.now().isoformat(timespec="seconds"),
        }
        meta_path = os.path.join(participant_dir, "metadata.json")
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(metadata, f, indent=4)

    # ------------------------------------------------------------------
    # UDP BROADCAST
    # ------------------------------------------------------------------
    def _send_udp(self, message):
        """Broadcast a UTF-8 string to the slave on UDP_PORT."""
        sock = None
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            sock.sendto(message.encode("utf-8"), (UDP_BROADCAST_IP, UDP_PORT))
        except OSError as e:
            raise OSError(
                f"Could not broadcast on UDP port {UDP_PORT}. The port may be "
                f"blocked by the firewall or already in use.\n\nDetails: {e}"
            )
        finally:
            if sock is not None:
                sock.close()

    # ------------------------------------------------------------------
    # START
    # ------------------------------------------------------------------
    def start_recording(self):
        """Begin writing the already-streaming camera to disk + trigger the slave.

        The camera is pre-opened and streaming, so this is near-instant: create
        the writer, fire the UDP START, and flip the writing flag.
        """
        if self.writing_flag.is_set():
            return  # Already recording.

        try:
            pid = self._validate_inputs()
            participant_dir, video_path, rel_path = self._build_paths(pid)
            self._write_metadata(participant_dir, pid)

            # Make sure the camera is live (it normally already is). Opening here
            # is the slow path and only happens if pre-open failed earlier.
            if self.cap is None or not self.streaming_flag.is_set():
                if not self._open_camera():
                    self.var_status.set("Idle - start failed (no camera).")
                    return

            # Create the writer for this trial at the live frame size.
            fourcc = cv2.VideoWriter_fourcc(*"XVID")
            writer = cv2.VideoWriter(video_path, fourcc, TARGET_FPS, self.frame_size)
            if not writer or not writer.isOpened():
                raise RuntimeError(
                    f"Could not open the video file for writing:\n{video_path}\n\n"
                    "The XVID codec may be missing, or the folder is not writable."
                )
            with self.out_lock:
                self.out = writer

            # Tell the slave, then start writing (both near-instant).
            start_msg = (
                f"START|id={pid}|position={self.var_pos.get()}|"
                f"height={self.var_height.get()}|trial={self.var_trial.get()}|"
                f"relpath={rel_path}"
            )
            self._send_udp(start_msg)
            self.writing_flag.set()

            self.btn_start.config(state="disabled")
            self.btn_stop.config(state="normal")
            self._lock_inputs(True)
            self.var_status.set(f"RECORDING -> {os.path.basename(video_path)}")

        except (ValueError, RuntimeError, OSError) as e:
            self.writing_flag.clear()
            self._finalize_writer()
            messagebox.showerror("Cannot Start Recording", str(e))
            self.var_status.set("Idle - start failed.")
        except Exception as e:
            self.writing_flag.clear()
            self._finalize_writer()
            messagebox.showerror(
                "Unexpected Error",
                f"An unexpected error occurred while starting:\n\n{type(e).__name__}: {e}"
            )
            self.var_status.set("Idle - start failed.")

    # ------------------------------------------------------------------
    # CAMERA STREAM (persistent preview; runs on a background thread)
    # ------------------------------------------------------------------
    def _open_camera(self):
        """Open the selected camera and start the live preview thread. Returns bool.

        Slow path (~0.5-3 s on first MSMF open). Run from the main thread so the
        camera is warm and streaming BEFORE the user presses START.
        """
        if self.cap is not None and self.streaming_flag.is_set():
            return True
        try:
            cam_index, cam_backend = self._selected_camera()
        except ValueError as e:
            messagebox.showerror("No Camera", str(e))
            return False

        self.var_status.set(f"Opening camera (index {cam_index})...")
        self.root.update_idletasks()

        cap = cv2.VideoCapture(cam_index, cam_backend)
        if not cap.isOpened():
            cap.release()
            messagebox.showerror(
                "Camera Error",
                f"Could not open camera index {cam_index}.\n\n"
                "Make sure the USB webcam is plugged in and not in use by another "
                "app (Zoom, Teams, Camera), then click 'Rescan'."
            )
            self.var_status.set("Idle - camera failed to open.")
            return False

        cap.set(cv2.CAP_PROP_FRAME_WIDTH, FRAME_WIDTH)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, FRAME_HEIGHT)
        cap.set(cv2.CAP_PROP_FPS, TARGET_FPS)

        warm_ok, _ = read_with_warmup(cap, attempts=20, delay=0.1)
        if not warm_ok:
            cap.release()
            messagebox.showerror(
                "Camera Error",
                f"Camera index {cam_index} opened but returned no frames.\n\n"
                "It may be in use by another app. Close it and click 'Rescan'."
            )
            self.var_status.set("Idle - camera returned no frames.")
            return False

        self.cap = cap
        self.frame_size = (int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)) or FRAME_WIDTH,
                           int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)) or FRAME_HEIGHT)
        self.streaming_flag.set()
        self.cam_thread = threading.Thread(target=self._stream_loop, daemon=True)
        self.cam_thread.start()

        # Pre-load the XVID codec once so the FIRST recording starts instantly
        # (the codec DLL init otherwise adds ~1.5 s to the first START).
        if not self._codec_warmed:
            self.var_status.set("Warming up video codec...")
            self.root.update_idletasks()
            self._warmup_codec()
            self._codec_warmed = True

        self.var_status.set(f"Camera live: {self.var_cam.get()} - ready to record.")
        return True

    def _warmup_codec(self):
        """Create and discard a throwaway XVID writer to pre-load the codec."""
        tmp = os.path.join(ROOT_DIR, "._codec_warmup.avi")
        try:
            fourcc = cv2.VideoWriter_fourcc(*"XVID")
            w = cv2.VideoWriter(tmp, fourcc, TARGET_FPS, self.frame_size)
            if w is not None:
                w.release()
        except Exception:
            pass
        finally:
            try:
                if os.path.exists(tmp):
                    os.remove(tmp)
            except OSError:
                pass

    def _stream_loop(self):
        """Continuously read frames; preview always, write to disk while recording."""
        miss = 0
        try:
            while self.streaming_flag.is_set():
                ret, frame = self.cap.read()
                if not ret or frame is None:
                    miss += 1
                    if miss > 30:                     # persistent failure
                        self.streaming_flag.clear()
                        self.root.after(0, self._on_camera_lost)
                        break
                    time.sleep(0.01)
                    continue
                miss = 0

                # Write the clean frame to disk if we are recording.
                if self.writing_flag.is_set():
                    with self.out_lock:
                        if self.out is not None:
                            if (frame.shape[1], frame.shape[0]) != self.frame_size:
                                self.out.write(cv2.resize(frame, self.frame_size))
                            else:
                                self.out.write(frame)
                    cv2.putText(frame, "REC", (18, 40), cv2.FONT_HERSHEY_SIMPLEX,
                                1.0, (0, 0, 255), 2, cv2.LINE_AA)

                cv2.imshow(PREVIEW_WINDOW, frame)
                cv2.waitKey(1)
        except Exception as e:
            self.streaming_flag.clear()
            self.root.after(0, lambda: self._on_camera_error(e))
        finally:
            try:
                cv2.destroyAllWindows()
            except Exception:
                pass

    def _on_camera_lost(self):
        """Handle the camera dropping out (called on the main thread)."""
        was_recording = self.writing_flag.is_set()
        self.writing_flag.clear()
        self._finalize_writer()
        if was_recording:
            try:
                self._send_udp("STOP")
            except Exception:
                pass
        self._close_camera()
        self._reset_ui_after_stop()
        messagebox.showerror(
            "Camera Lost",
            "The camera stopped returning frames.\n"
            + ("The recording was stopped and saved.\n" if was_recording else "")
            + "Click 'Rescan' to reconnect."
        )
        self.var_status.set("Camera lost - click 'Rescan' to reconnect.")

    def _on_camera_error(self, exc):
        self._on_camera_lost()
        messagebox.showerror(
            "Camera Error",
            f"The camera stream hit an error:\n\n{type(exc).__name__}: {exc}"
        )

    # ------------------------------------------------------------------
    # STOP
    # ------------------------------------------------------------------
    def stop_recording(self):
        """Stop writing to disk and trigger the slave. Camera stays live."""
        if not self.writing_flag.is_set():
            return
        try:
            self.writing_flag.clear()     # stop writing frames immediately
            self._finalize_writer()       # finalize and close the .avi
            self._send_udp("STOP")
            self.var_status.set("Stopped - file saved. Camera still live.")
        except OSError as e:
            messagebox.showerror("Network Error on Stop", str(e))
        except Exception as e:
            messagebox.showerror(
                "Error Stopping",
                f"An error occurred while stopping:\n\n{type(e).__name__}: {e}"
            )
        finally:
            self._reset_ui_after_stop()

    # ------------------------------------------------------------------
    # BATCH EVALUATION
    # ------------------------------------------------------------------
    def start_batch_evaluation(self):
        """Kick off the offline benchmarking pipeline on a background thread."""
        if self.writing_flag.is_set():
            messagebox.showwarning(
                "Recording In Progress",
                "Please stop the current recording before running batch evaluation."
            )
            return

        self.btn_evaluate.config(state="disabled")
        self.btn_start.config(state="disabled")
        self.var_status.set("Batch evaluation: scanning for trial pairs...")

        eval_thread = threading.Thread(
            target=self._batch_evaluation_worker,
            daemon=True,
        )
        eval_thread.start()

    def _batch_evaluation_worker(self):
        """Runs in a background thread; never touches Tk widgets directly."""
        try:
            import analysis_pipeline as ap

            def _progress(idx, total, pair):
                self.root.after(0, lambda: self.var_status.set(
                    f"[{idx}/{total}] Processing Trial_{pair['trial']} "
                    f"(Pos {pair['position']}, {pair['height']})..."
                ))

            results = ap.run_batch_analysis(ROOT_DIR, progress_callback=_progress)
            output_path = os.path.join(ROOT_DIR, ap.EVAL_RESULTS_FILENAME)
            ap.export_results(results, output_path)

            best = results["aggregate"]["best_overall"]
            if best is None:
                summary = ("No matching Trial_X.avi + Trial_X_optitrack.csv pairs "
                           "produced a successful comparison.\n\n"
                           "Make sure OptiTrack CSV exports sit next to each .avi.")
            else:
                summary = (
                    f"Best Model: {best['model']}\n"
                    f"Best Position: {best['position']}\n"
                    f"Best Height: {best['height']}\n"
                    f"Mean RMSE: {best['mean_rmse_deg']:.2f} deg "
                    f"(over {best['n_trials']} trial(s))"
                )

            agg = results["aggregate"]
            summary += (
                f"\n\nSuccessful comparisons: {agg['ok_comparisons']}"
                f"  |  Failed: {agg['failed_comparisons']}"
            )
            if agg["failed_comparisons"]:
                summary += ("\n(Failures are per-model, e.g. a missing ONNX file "
                            "or no person detected - see evaluation_results.json.)")

            n_found = results["num_trials_found"]
            self.root.after(0, lambda: self.var_status.set(
                f"Batch evaluation complete: {n_found} trial pair(s)."
            ))
            self.root.after(0, lambda: messagebox.showinfo(
                "Batch Evaluation Complete",
                f"{summary}\n\nFull results written to:\n{output_path}"
            ))

        except ImportError as e:
            self.root.after(0, lambda: messagebox.showerror(
                "Missing Dependency",
                "Batch evaluation could not import its analysis dependencies.\n\n"
                "Run:  pip install -r requirements.txt\n"
                "(needs numpy, scipy, opencv-python, mediapipe, onnxruntime)\n\n"
                f"Details: {e}"
            ))
            self.root.after(0, lambda: self.var_status.set(
                "Idle - batch evaluation failed (missing dependency)."))
        except Exception as e:
            self.root.after(0, lambda: messagebox.showerror(
                "Batch Evaluation Error",
                f"An unexpected error occurred during batch evaluation:\n\n"
                f"{type(e).__name__}: {e}"
            ))
            self.root.after(0, lambda: self.var_status.set(
                "Idle - batch evaluation failed."))
        finally:
            self.root.after(0, self._reset_ui_after_evaluation)

    def _reset_ui_after_evaluation(self):
        # Only re-enable if we are not mid-recording (defensive).
        if not self.writing_flag.is_set():
            self.btn_evaluate.config(state="normal")
            self.btn_start.config(state="normal")

    # ------------------------------------------------------------------
    # CLEANUP HELPERS
    # ------------------------------------------------------------------
    def _finalize_writer(self):
        """Release the VideoWriter (finalize the .avi). Idempotent, thread-safe."""
        with self.out_lock:
            if self.out is not None:
                try:
                    self.out.release()
                except Exception:
                    pass
                self.out = None

    def _close_camera(self):
        """Stop the preview thread and release the camera. Idempotent."""
        self.streaming_flag.clear()
        if self.cam_thread is not None:
            self.cam_thread.join(timeout=2.0)
            self.cam_thread = None
        try:
            if self.cap is not None:
                self.cap.release()
        except Exception:
            pass
        finally:
            self.cap = None

    def _reset_ui_after_stop(self):
        self.btn_start.config(state="normal")
        self.btn_stop.config(state="disabled")
        self._lock_inputs(False)
        if "RECORDING" in self.var_status.get():
            live = self.streaming_flag.is_set()
            self.var_status.set(
                "Camera live - ready to record." if live
                else "Idle - click 'Rescan' to reconnect camera."
            )

    def _lock_inputs(self, locked):
        state = "disabled" if locked else "normal"
        ro_state = "disabled" if locked else "readonly"
        for w in (self.entry_id, self.entry_age, self.entry_weight):
            w.config(state=state)
        for w in (self.drop_sex, self.drop_diag, self.drop_pos,
                  self.drop_height, self.drop_trial):
            w.config(state=ro_state)
        # Camera rescan and batch evaluation are only available when not recording.
        self.btn_rescan.config(state=state)
        self.btn_evaluate.config(state=state)

    def on_close(self):
        """Window-close handler: stop recording and release the camera."""
        try:
            was_recording = self.writing_flag.is_set()
            self.writing_flag.clear()
            self._finalize_writer()
            if was_recording:
                try:
                    self._send_udp("STOP")
                except Exception:
                    pass
        finally:
            self._close_camera()
            self.root.destroy()


def main():
    try:
        os.makedirs(ROOT_DIR, exist_ok=True)
    except OSError as e:
        # Cannot even create the root folder - warn and exit.
        r = tk.Tk()
        r.withdraw()
        messagebox.showerror(
            "Storage Error",
            f"Could not create the recordings folder:\n{ROOT_DIR}\n\nDetails: {e}"
        )
        return

    root = tk.Tk()
    MasterApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
