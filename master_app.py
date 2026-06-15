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
import json
import socket
import threading
from datetime import datetime

import tkinter as tk
from tkinter import ttk, messagebox

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
WEBCAM_INDEX = 0            # Change if your webcam is on a different index.
TARGET_FPS = 30.0          # Forced capture/write rate.
UDP_PORT = 5005            # Must match the slave listener.
UDP_BROADCAST_IP = "255.255.255.255"   # Broadcast to the whole subnet.
FRAME_WIDTH = 1280
FRAME_HEIGHT = 720


class MasterApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Biomechanics Master - Acquisition Control")
        self.root.geometry("480x620")
        self.root.resizable(False, False)

        # ---- Thread-safe recording state ----
        self.recording_flag = threading.Event()   # set() = keep recording
        self.capture_thread = None
        self.cap = None
        self.out = None

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

        # --- Camera Position 1-9 ---
        tk.Label(self.root, text="Camera Position:").grid(row=8, column=0, sticky="e", **pad)
        self.var_pos = tk.StringVar(value="1")
        self.drop_pos = ttk.Combobox(self.root, textvariable=self.var_pos, width=25,
                                     state="readonly",
                                     values=[str(i) for i in range(1, 10)])
        self.drop_pos.grid(row=8, column=1, sticky="w", **pad)

        # --- Camera Height ---
        tk.Label(self.root, text="Camera Height:").grid(row=9, column=0, sticky="e", **pad)
        self.var_height = tk.StringVar(value="Joint-Level")
        self.drop_height = ttk.Combobox(self.root, textvariable=self.var_height, width=25,
                                        state="readonly",
                                        values=["Low", "Joint-Level", "High"])
        self.drop_height.grid(row=9, column=1, sticky="w", **pad)

        # --- Trial Number ---
        tk.Label(self.root, text="Trial Number:").grid(row=10, column=0, sticky="e", **pad)
        self.var_trial = tk.StringVar(value="1")
        self.drop_trial = ttk.Combobox(self.root, textvariable=self.var_trial, width=25,
                                       state="readonly",
                                       values=["1", "2", "3"])
        self.drop_trial.grid(row=10, column=1, sticky="w", **pad)

        ttk.Separator(self.root, orient="horizontal").grid(
            row=11, column=0, columnspan=2, sticky="ew", padx=10, pady=10)

        # --- Control buttons ---
        self.btn_start = tk.Button(self.root, text="START RECORDING",
                                   bg="#1e7d34", fg="white",
                                   font=("Segoe UI", 11, "bold"),
                                   width=18, height=2, command=self.start_recording)
        self.btn_start.grid(row=12, column=0, padx=10, pady=12)

        self.btn_stop = tk.Button(self.root, text="STOP",
                                  bg="#a31515", fg="white",
                                  font=("Segoe UI", 11, "bold"),
                                  width=18, height=2, command=self.stop_recording,
                                  state="disabled")
        self.btn_stop.grid(row=12, column=1, padx=10, pady=12)

        # --- Status bar ---
        self.var_status = tk.StringVar(value="Idle - ready to record.")
        self.lbl_status = tk.Label(self.root, textvariable=self.var_status,
                                   relief="sunken", anchor="w", fg="#333")
        self.lbl_status.grid(row=13, column=0, columnspan=2, sticky="ew",
                             padx=10, pady=(4, 10))

        # Make sure resources are released if the window is closed.
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

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
        if self.recording_flag.is_set():
            return  # Already recording.

        try:
            pid = self._validate_inputs()
            participant_dir, video_path, rel_path = self._build_paths(pid)
            self._write_metadata(participant_dir, pid)

            # ---- Open webcam BEFORE telling the slave to start ----
            self.cap = cv2.VideoCapture(WEBCAM_INDEX, cv2.CAP_DSHOW)
            if not self.cap or not self.cap.isOpened():
                raise RuntimeError(
                    f"Webcam not detected at index {WEBCAM_INDEX}.\n\n"
                    "Check that the camera is plugged in and not in use by "
                    "another application (Zoom, Teams, Camera app)."
                )

            self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, FRAME_WIDTH)
            self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, FRAME_HEIGHT)
            self.cap.set(cv2.CAP_PROP_FPS, TARGET_FPS)

            # Use the actual frame size the camera gave us.
            actual_w = int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH)) or FRAME_WIDTH
            actual_h = int(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT)) or FRAME_HEIGHT

            fourcc = cv2.VideoWriter_fourcc(*"XVID")
            self.out = cv2.VideoWriter(video_path, fourcc, TARGET_FPS,
                                       (actual_w, actual_h))
            if not self.out or not self.out.isOpened():
                self.cap.release()
                self.cap = None
                raise RuntimeError(
                    f"Could not open the video file for writing:\n{video_path}\n\n"
                    "The XVID codec may be missing, or the folder is not writable."
                )

            # ---- Tell the slave to start ----
            start_msg = (
                f"START|id={pid}|position={self.var_pos.get()}|"
                f"height={self.var_height.get()}|trial={self.var_trial.get()}|"
                f"relpath={rel_path}"
            )
            self._send_udp(start_msg)

            # ---- Launch the capture loop on a background thread ----
            self.recording_flag.set()
            self.capture_thread = threading.Thread(
                target=self._capture_loop,
                args=(video_path, actual_w, actual_h),
                daemon=True,
            )
            self.capture_thread.start()

            self.btn_start.config(state="disabled")
            self.btn_stop.config(state="normal")
            self._lock_inputs(True)
            self.var_status.set(f"RECORDING -> {os.path.basename(video_path)}")

        except (ValueError, RuntimeError, OSError) as e:
            self._cleanup_capture()
            messagebox.showerror("Cannot Start Recording", str(e))
            self.var_status.set("Idle - start failed.")
        except Exception as e:
            self._cleanup_capture()
            messagebox.showerror(
                "Unexpected Error",
                f"An unexpected error occurred while starting:\n\n{type(e).__name__}: {e}"
            )
            self.var_status.set("Idle - start failed.")

    # ------------------------------------------------------------------
    # CAPTURE LOOP (runs on background thread)
    # ------------------------------------------------------------------
    def _capture_loop(self, video_path, width, height):
        """Read frames at ~30 fps and write them until the flag clears."""
        frame_interval = 1.0 / TARGET_FPS
        next_time = cv2.getTickCount() / cv2.getTickFrequency()
        try:
            while self.recording_flag.is_set():
                ret, frame = self.cap.read()
                if not ret:
                    # Camera was unplugged or stream ended.
                    self.recording_flag.clear()
                    self.root.after(0, lambda: messagebox.showerror(
                        "Camera Lost",
                        "The webcam stopped returning frames mid-recording.\n"
                        "The recording has been stopped and the file saved."
                    ))
                    break

                self.out.write(frame)

                # Live preview window (closing it does not stop recording).
                cv2.imshow("Recording Preview - close STOP button to end", frame)
                if cv2.waitKey(1) & 0xFF == 27:  # ESC also stops.
                    self.recording_flag.clear()
                    break

                # Simple pacing toward 30 fps.
                next_time += frame_interval
                now = cv2.getTickCount() / cv2.getTickFrequency()
                sleep_for = next_time - now
                if sleep_for > 0:
                    cv2.waitKey(max(1, int(sleep_for * 1000)))
        except Exception as e:
            self.recording_flag.clear()
            self.root.after(0, lambda: messagebox.showerror(
                "Recording Error",
                f"An error occurred during capture:\n\n{type(e).__name__}: {e}"
            ))
        finally:
            self._cleanup_capture()
            # Re-enable the UI from the main thread.
            self.root.after(0, self._reset_ui_after_stop)

    # ------------------------------------------------------------------
    # STOP
    # ------------------------------------------------------------------
    def stop_recording(self):
        if not self.recording_flag.is_set():
            return
        try:
            # 1) Flip the thread-safe flag so the loop exits.
            self.recording_flag.clear()

            # 2) Wait briefly for the capture thread to finish releasing.
            if self.capture_thread is not None:
                self.capture_thread.join(timeout=3.0)

            # 3) Tell the slave to stop.
            self._send_udp("STOP")
            self.var_status.set("Stopped - file saved.")
        except OSError as e:
            messagebox.showerror("Network Error on Stop", str(e))
        except Exception as e:
            messagebox.showerror(
                "Error Stopping",
                f"An error occurred while stopping:\n\n{type(e).__name__}: {e}"
            )
        finally:
            self._cleanup_capture()
            self._reset_ui_after_stop()

    # ------------------------------------------------------------------
    # CLEANUP HELPERS
    # ------------------------------------------------------------------
    def _cleanup_capture(self):
        """Safely release camera + writer + preview windows. Idempotent."""
        try:
            if self.out is not None:
                self.out.release()
        except Exception:
            pass
        finally:
            self.out = None
        try:
            if self.cap is not None:
                self.cap.release()
        except Exception:
            pass
        finally:
            self.cap = None
        try:
            cv2.destroyAllWindows()
        except Exception:
            pass

    def _reset_ui_after_stop(self):
        self.btn_start.config(state="normal")
        self.btn_stop.config(state="disabled")
        self._lock_inputs(False)
        if "RECORDING" in self.var_status.get():
            self.var_status.set("Idle - ready to record.")

    def _lock_inputs(self, locked):
        state = "disabled" if locked else "normal"
        ro_state = "disabled" if locked else "readonly"
        for w in (self.entry_id, self.entry_age, self.entry_weight):
            w.config(state=state)
        for w in (self.drop_sex, self.drop_diag, self.drop_pos,
                  self.drop_height, self.drop_trial):
            w.config(state=ro_state)

    def on_close(self):
        """Window-close handler: stop recording and release everything."""
        try:
            if self.recording_flag.is_set():
                self.recording_flag.clear()
                if self.capture_thread is not None:
                    self.capture_thread.join(timeout=3.0)
                try:
                    self._send_udp("STOP")
                except Exception:
                    pass
        finally:
            self._cleanup_capture()
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
