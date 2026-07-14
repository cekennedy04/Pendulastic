"""
pendulastic_backend.py
======================
Clinical pendulum test backend for automated knee spasticity scoring.

Architecture
------------
  PendulasticConfig   — evidence-based protocol enforcement thresholds
  PendulasticTrial    — one swing recording → PT params + MAS grade (lazy)
  PendulasticSession  — N trials → session summary + export
  HPEExtractor        — abstract video → angle extractor
  MediaPipeExtractor  — MediaPipe Pose Landmarker v2 implementation

Typical CSV-based usage (research / batch):
    session = PendulasticSession("P007", "left")
    for csv in sorted(glob("trial_*.csv")):
        session.add_trial_from_csv(csv)
    s = session.summary()
    print(s.mas_mode, s.pt_median)
    session.export_json("session_P007.json")

Typical video usage (clinical bedside):
    extractor = MediaPipeExtractor("models/pose_landmarker_full.task")
    session = PendulasticSession("P007", "left")
    for video in ["t1.mp4", "t2.mp4", "t3.mp4"]:
        trial = extractor.process_to_trial(video, limb="left")
        session.add_trial(trial)
    session.export_json("session_P007.json")
"""
from __future__ import annotations

import json
import math
import os
import warnings
from collections import Counter
from dataclasses import asdict, dataclass, field
from typing import Optional

import numpy as np
import pandas as pd

from pendulastic_pt_score import (
    HEALTHY_REF,
    compute_pt_params,
    compute_pt_score,
    compute_pt_score_simple,
    pt_to_mas,
)

# ══════════════════════════════════════════════════════════════════════════════
# Configuration
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class PendulasticConfig:
    """
    Protocol enforcement and scoring parameters.

    All numeric thresholds are evidence-based:
      - min_knee_confidence / min_ankle_confidence:
          BlazePose mean joint angle error ≈10° below this (Source 1)
      - max_frame_dropout_pct:
          >20% dropout makes N and A0 estimates unreliable
      - min_A0_deg:
          Below 20° the SNR for markerless HPE is insufficient (P2 data)
      - min_trials:
          Single-trial MAS is unreliable (MAS ICC=0.56, Source 3)
      - outlier_pt_threshold:
          |PT_i − median| > 0.15 flags an outlier trial for review
      - mcid:
          Minimum clinically important difference for lower limb MAS (Source 3)
    """
    # Model
    model_path: str = ""

    # Frame quality
    min_knee_confidence:   float = 0.65
    min_ankle_confidence:  float = 0.65
    max_frame_dropout_pct: float = 20.0
    max_gap_frames:        int   = 3      # max consecutive frames to gap-fill

    # Trial quality
    min_A0_deg:    float = 20.0
    min_trials:    int   = 3
    outlier_pt_threshold: float = 0.15

    # Scoring variant
    use_4param: bool = True   # 4-param is more robust; set False for 6-param

    # Clinical reference
    mcid: float = 0.45       # lower-limb MAS MCID (ES=0.5, Source 3)


# ══════════════════════════════════════════════════════════════════════════════
# Trial
# ══════════════════════════════════════════════════════════════════════════════

class PendulasticTrial:
    """
    One pendulum swing recording.

    Inputs
    ------
    t              : time array (seconds, monotone increasing)
    knee_angle_deg : interior knee angle — ~180° when fully extended, decreases
                     with flexion.  Must follow the same convention as the
                     OptiTrack reference in pendulastic_pt_score.py.
    knee_conf      : per-frame knee visibility/confidence in [0, 1] (optional)
    ankle_conf     : per-frame ankle visibility/confidence in [0, 1] (optional)

    PT parameters are computed lazily on first property access.
    """

    def __init__(
        self,
        t: np.ndarray,
        knee_angle_deg: np.ndarray,
        knee_conf:  Optional[np.ndarray] = None,
        ankle_conf: Optional[np.ndarray] = None,
        trial_id:    str = "",
        source_path: str = "",
        config: Optional[PendulasticConfig] = None,
    ):
        self.t          = np.asarray(t,             dtype=float)
        self.knee_angle = np.asarray(knee_angle_deg, dtype=float)
        self.knee_conf  = (np.asarray(knee_conf,  dtype=float)
                           if knee_conf  is not None else np.ones_like(self.t))
        self.ankle_conf = (np.asarray(ankle_conf, dtype=float)
                           if ankle_conf is not None else np.ones_like(self.t))
        self.trial_id    = trial_id
        self.source_path = source_path
        self.config      = config or PendulasticConfig()

        self._params:         Optional[dict]      = None
        self._quality_flags:  Optional[list[str]] = None
        self._computed:       bool                = False

    # ── Lazy computation ───────────────────────────────────────────────────────

    def _compute(self) -> None:
        if self._computed:
            return
        self._computed     = True
        self._quality_flags = []
        cfg = self.config

        # ── 1. Per-frame confidence gate ──────────────────────────────────────
        low_knee = self.knee_conf  < cfg.min_knee_confidence
        low_ank  = self.ankle_conf < cfg.min_ankle_confidence
        bad      = low_knee | low_ank
        dropout_pct = 100.0 * float(np.mean(bad))
        if dropout_pct > cfg.max_frame_dropout_pct:
            self._quality_flags.append(
                f"high_dropout:{dropout_pct:.1f}%"
            )

        # ── 2. Gap-fill short low-confidence runs ─────────────────────────────
        ang = self.knee_angle.copy()
        if bad.any():
            ang[bad] = np.nan
            ang = (pd.Series(ang)
                   .interpolate(limit=cfg.max_gap_frames,
                                limit_direction="both")
                   .values)

        # ── 3. PT parameter estimation ────────────────────────────────────────
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            self._params = compute_pt_params(self.t, ang)

        if self._params is None:
            return

        # ── 4. Post-compute quality checks ────────────────────────────────────
        if self._params.get("A0_deg", 0) < cfg.min_A0_deg:
            self._quality_flags.append(
                f"low_A0:{self._params['A0_deg']:.1f}°"
            )
        if self._params.get("quality_warn", False):
            self._quality_flags.append("area_ratio")

    # ── Properties ─────────────────────────────────────────────────────────────

    @property
    def params(self) -> Optional[dict]:
        """Full dict from compute_pt_params, or None if computation failed."""
        self._compute()
        return self._params

    @property
    def pt_score(self) -> Optional[float]:
        p = self.params
        if p is None:
            return None
        return (compute_pt_score_simple(p, HEALTHY_REF) if self.config.use_4param
                else compute_pt_score(p, HEALTHY_REF))

    @property
    def mas_grade(self) -> Optional[str]:
        pt = self.pt_score
        return None if pt is None else pt_to_mas(pt)

    @property
    def quality_flags(self) -> list[str]:
        self._compute()
        return self._quality_flags or []

    @property
    def is_valid(self) -> bool:
        """PT params were successfully computed (A0 and quality flags may still warn)."""
        return self.params is not None

    @property
    def A0_deg(self) -> Optional[float]:
        p = self.params
        return None if p is None else p.get("A0_deg")

    @property
    def fps(self) -> float:
        if len(self.t) < 2:
            return 0.0
        return (len(self.t) - 1) / (self.t[-1] - self.t[0])

    def to_dict(self) -> dict:
        p = self.params
        return {
            "trial_id":       self.trial_id,
            "source_path":    self.source_path,
            "fps":            round(self.fps, 1),
            "n_frames":       int(len(self.t)),
            "duration_sec":   round(float(self.t[-1] - self.t[0]), 2) if len(self.t) > 1 else 0,
            "A0_deg":         round(p["A0_deg"],        2)    if p else None,
            "N":              float(p["N"])                    if p else None,
            "R2n":            round(p["R2n"],            4)   if p else None,
            "phi_max_ratio":  round(p["phi_max_ratio"],  4)   if p else None,
            "omega_max_n":    round(p["omega_max_n"],    4)   if p else None,
            "omega_min_n":    round(p["omega_min_n"],    4)   if p else None,
            "f_hz":           round(p["f"],               4)  if p else None,
            "area_ratio":     round(p["area_ratio"],     4)   if p else None,
            "pt_score":       round(self.pt_score, 4)         if self.pt_score is not None else None,
            "mas_grade":      self.mas_grade,
            "is_valid":       self.is_valid,
            "quality_flags":  self.quality_flags,
        }

    # ── Loaders ────────────────────────────────────────────────────────────────

    @classmethod
    def from_csv(
        cls,
        csv_path: str,
        trial_id:    str = "",
        config: Optional[PendulasticConfig] = None,
    ) -> "PendulasticTrial":
        """
        Load from a pre-extracted HPE angle CSV.

        Required columns : time_sec, knee_angle_deg
        Optional columns : knee_visibility | knee_conf
                           ankle_visibility | ankle_conf
        """
        df = pd.read_csv(csv_path)
        _required = {"time_sec", "knee_angle_deg"}
        missing   = _required - set(df.columns)
        if missing:
            raise ValueError(
                f"{os.path.basename(csv_path)}: missing columns {missing}. "
                f"Got: {list(df.columns)}"
            )
        df = df.dropna(subset=["time_sec", "knee_angle_deg"])

        def _col(df: pd.DataFrame, *candidates: str) -> Optional[np.ndarray]:
            for c in candidates:
                if c in df.columns:
                    return df[c].values.astype(float)
            return None

        return cls(
            t              = df["time_sec"].values.astype(float),
            knee_angle_deg = df["knee_angle_deg"].values.astype(float),
            knee_conf      = _col(df, "knee_conf",  "knee_visibility"),
            ankle_conf     = _col(df, "ankle_conf", "ankle_visibility"),
            trial_id       = trial_id or os.path.basename(csv_path),
            source_path    = csv_path,
            config         = config,
        )


# ══════════════════════════════════════════════════════════════════════════════
# Session summary
# ══════════════════════════════════════════════════════════════════════════════

_MAS_TO_NUM = {"0": 0, "1": 1, "1+": 1.5, "2": 2, "3": 3, "4": 4}


@dataclass
class SessionSummary:
    patient_id:  str
    limb:        str
    n_trials:    int
    n_valid:     int
    pt_scores:   list[Optional[float]]
    mas_scores:  list[Optional[str]]
    pt_median:   Optional[float]
    pt_iqr:      Optional[float]
    mas_mode:    Optional[str]      # most common grade; ties go to higher grade
    quality_ok:  bool               # True when n_valid >= config.min_trials
    warnings:    list[str]

    def to_dict(self) -> dict:
        return asdict(self)


# ══════════════════════════════════════════════════════════════════════════════
# Session
# ══════════════════════════════════════════════════════════════════════════════

class PendulasticSession:
    """
    One clinical assessment session: patient × limb × N pendulum trials.

    The session enforces the clinical protocol (≥3 trials, A0 ≥ 20°) and
    reports the median PT score ± IQR and modal MAS grade across valid trials.
    """

    def __init__(
        self,
        patient_id: str,
        limb: str,
        config: Optional[PendulasticConfig] = None,
    ):
        if limb not in ("left", "right"):
            raise ValueError("limb must be 'left' or 'right'")
        self.patient_id = patient_id
        self.limb       = limb
        self.config     = config or PendulasticConfig()
        self.trials:    list[PendulasticTrial] = []

    # ── Adding trials ──────────────────────────────────────────────────────────

    def add_trial(self, trial: PendulasticTrial) -> None:
        """Accept an already-constructed trial and attach the session's config."""
        trial.config    = self.config
        trial._computed = False
        self.trials.append(trial)

    def add_trial_from_csv(
        self, csv_path: str, trial_id: str = ""
    ) -> PendulasticTrial:
        t = PendulasticTrial.from_csv(
            csv_path,
            trial_id  = trial_id or f"T{len(self.trials) + 1}",
            config    = self.config,
        )
        self.trials.append(t)
        return t

    def add_trial_from_arrays(
        self,
        t:              np.ndarray,
        knee_angle_deg: np.ndarray,
        knee_conf:      Optional[np.ndarray] = None,
        ankle_conf:     Optional[np.ndarray] = None,
        trial_id:       str = "",
    ) -> PendulasticTrial:
        trial = PendulasticTrial(
            t              = t,
            knee_angle_deg = knee_angle_deg,
            knee_conf      = knee_conf,
            ankle_conf     = ankle_conf,
            trial_id       = trial_id or f"T{len(self.trials) + 1}",
            config         = self.config,
        )
        self.trials.append(trial)
        return trial

    # ── Session summary ────────────────────────────────────────────────────────

    def summary(self) -> SessionSummary:
        valid = [tr for tr in self.trials if tr.is_valid]
        pts   = [tr.pt_score  for tr in valid if tr.pt_score  is not None]
        mass  = [tr.mas_grade for tr in valid if tr.mas_grade is not None]

        pt_median: Optional[float] = float(np.median(pts)) if pts else None
        pt_iqr:    Optional[float] = (
            float(np.subtract(*np.percentile(pts, [75, 25])))
            if len(pts) >= 2 else None
        )

        # Modal MAS: most common grade; ties broken by the more severe grade
        mas_mode: Optional[str] = None
        if mass:
            c        = Counter(mass)
            max_cnt  = max(c.values())
            tied     = [g for g, cnt in c.items() if cnt == max_cnt]
            mas_mode = max(tied, key=lambda g: _MAS_TO_NUM.get(g, -1))

        session_warnings: list[str] = []
        n_valid = len(valid)

        if n_valid < self.config.min_trials:
            session_warnings.append(
                f"Only {n_valid} of {self.config.min_trials} required trials are valid — "
                "session result is unreliable"
            )

        # Outlier detection across trials
        if pt_median is not None:
            thresh = self.config.outlier_pt_threshold
            for tr in valid:
                if tr.pt_score is not None and abs(tr.pt_score - pt_median) > thresh:
                    session_warnings.append(
                        f"Trial {tr.trial_id}: PT={tr.pt_score:.3f} deviates "
                        f">{thresh:.2f} from session median ({pt_median:.3f})"
                    )

        # Propagate per-trial quality flags
        for tr in valid:
            for flag in tr.quality_flags:
                session_warnings.append(f"Trial {tr.trial_id}: {flag}")

        return SessionSummary(
            patient_id = self.patient_id,
            limb       = self.limb,
            n_trials   = len(self.trials),
            n_valid    = n_valid,
            pt_scores  = [round(p, 4) for p in pts],
            mas_scores = mass,
            pt_median  = round(pt_median, 4) if pt_median is not None else None,
            pt_iqr     = round(pt_iqr,    4) if pt_iqr    is not None else None,
            mas_mode   = mas_mode,
            quality_ok = n_valid >= self.config.min_trials,
            warnings   = session_warnings,
        )

    # ── Export ─────────────────────────────────────────────────────────────────

    def export_json(self, output_path: str) -> None:
        """Write full session + per-trial data as JSON."""
        s = self.summary()
        payload = {
            "schema_version": "1.0",
            "patient_id":     self.patient_id,
            "limb":           self.limb,
            "summary":        s.to_dict(),
            "trials":         [tr.to_dict() for tr in self.trials],
        }
        with open(output_path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2)
        print(f"Session saved -> {output_path}")

    def export_csv(self, output_path: str) -> None:
        """Write one row per trial as CSV (for batch analysis)."""
        rows = []
        for tr in self.trials:
            p = tr.params or {}
            rows.append({
                "patient_id":     self.patient_id,
                "limb":           self.limb,
                "trial_id":       tr.trial_id,
                "source_path":    tr.source_path,
                "fps":            round(tr.fps, 1),
                "is_valid":       tr.is_valid,
                "A0_deg":         round(p.get("A0_deg",       0), 2) if p else None,
                "N":              p.get("N")                          if p else None,
                "R2n":            round(p.get("R2n",          0), 4) if p else None,
                "phi_max_ratio":  round(p.get("phi_max_ratio",0), 4) if p else None,
                "omega_max_n":    round(p.get("omega_max_n",  0), 4) if p else None,
                "f_hz":           round(p.get("f",             0), 4) if p else None,
                "area_ratio":     round(p.get("area_ratio",   0), 4) if p else None,
                "pt_score":       round(tr.pt_score, 4)               if tr.pt_score is not None else None,
                "mas_grade":      tr.mas_grade,
                "quality_flags":  "|".join(tr.quality_flags),
            })
        pd.DataFrame(rows).to_csv(output_path, index=False)
        print(f"Trial data saved -> {output_path}")

    def print_summary(self) -> None:
        """Print a human-readable session summary to stdout."""
        s = self.summary()
        sep = "-" * 56
        print(f"\n{sep}")
        print(f"  Pendulastic Session -- {s.patient_id}  ({s.limb} limb)")
        print(sep)
        print(f"  Trials:   {s.n_valid}/{s.n_trials} valid")
        if s.pt_median is not None:
            print(f"  PT score: {s.pt_median:.3f}  (IQR ={s.pt_iqr:.3f})")
        print(f"  MAS mode: {s.mas_mode or 'N/A'}")
        print(f"  Per-trial: {list(zip([tr.trial_id for tr in self.trials], s.mas_scores))}")
        if s.warnings:
            print(f"\n  Warnings:")
            for w in s.warnings:
                print(f"    [!] {w}")
        print(f"{sep}\n")


# ══════════════════════════════════════════════════════════════════════════════
# HPE extractors
# ══════════════════════════════════════════════════════════════════════════════

class HPEExtractor:
    """
    Abstract base for extracting knee angle time series from a video.
    Subclass and implement extract_from_video().
    """

    def extract_from_video(self, video_path: str, limb: str) -> pd.DataFrame:
        """
        Run pose estimation on a video and return per-frame angles.

        Returns
        -------
        pd.DataFrame with at minimum:
            time_sec        — elapsed seconds from start
            knee_angle_deg  — interior knee angle (~180° = fully extended)
        Optionally:
            knee_visibility  — keypoint confidence in [0, 1]
            ankle_visibility — keypoint confidence in [0, 1]
        """
        raise NotImplementedError

    def process_to_trial(
        self,
        video_path:  str,
        limb:        str,
        trial_id:    str = "",
        config: Optional[PendulasticConfig] = None,
    ) -> PendulasticTrial:
        """Extract from video and wrap into a PendulasticTrial."""
        df = self.extract_from_video(video_path, limb)
        df = df.dropna(subset=["time_sec"])
        return PendulasticTrial(
            t              = df["time_sec"].values.astype(float),
            knee_angle_deg = df["knee_angle_deg"].values.astype(float),
            knee_conf      = df["knee_visibility"].values.astype(float)  if "knee_visibility"  in df else None,
            ankle_conf     = df["ankle_visibility"].values.astype(float) if "ankle_visibility" in df else None,
            trial_id       = trial_id or os.path.basename(video_path),
            source_path    = video_path,
            config         = config,
        )


class MediaPipeExtractor(HPEExtractor):
    """
    MediaPipe Pose Landmarker v2 implementation.

    Requires the .task model bundle (Apache 2.0):
        pose_landmarker_full.task   — best accuracy, ~30 FPS mobile
        pose_landmarker_lite.task   — fastest, lower accuracy

    Download:
        https://storage.googleapis.com/mediapipe-models/pose_landmarker/
        pose_landmarker_full/float16/latest/pose_landmarker_full.task

    Knee angle convention
    ─────────────────────
    Interior angle computed from hip–knee–ankle world landmarks.
    ~180° = leg fully extended; decreases toward flexion.
    Matches the OptiTrack interior-angle convention in pendulastic_pt_score.py.
    """

    # MediaPipe Pose Landmarker keypoint indices
    _LIMB_KP = {
        "right": {"hip": 23, "knee": 25, "ankle": 27},
        "left":  {"hip": 24, "knee": 26, "ankle": 28},
    }

    def __init__(self, model_path: str):
        if not os.path.isfile(model_path):
            raise FileNotFoundError(
                f"MediaPipe .task file not found: {model_path}\n"
                "Download pose_landmarker_full.task and update model_path."
            )
        self.model_path  = model_path
        self._landmarker = None

    def _load(self) -> None:
        if self._landmarker is not None:
            return
        try:
            import mediapipe as mp
            from mediapipe.tasks.python import vision as mp_vision
            from mediapipe.tasks import python as mp_python

            options = mp_vision.PoseLandmarkerOptions(
                base_options             = mp_python.BaseOptions(model_asset_path=self.model_path),
                output_segmentation_masks= False,
                num_poses                = 1,
                min_pose_detection_confidence = 0.30,
                min_pose_presence_confidence  = 0.30,
                min_tracking_confidence       = 0.30,
                running_mode             = mp_vision.RunningMode.VIDEO,
            )
            self._landmarker = mp_vision.PoseLandmarker.create_from_options(options)
        except ImportError as exc:
            raise ImportError(
                "mediapipe >= 0.10 required.  Install: pip install mediapipe"
            ) from exc

    @staticmethod
    def _interior_angle(hip, knee, ankle) -> float:
        """Interior knee angle in degrees from three world landmarks."""
        v_thigh = np.array([hip.x   - knee.x, hip.y   - knee.y, hip.z   - knee.z])
        v_shank = np.array([ankle.x - knee.x, ankle.y - knee.y, ankle.z - knee.z])
        n_t = np.linalg.norm(v_thigh)
        n_s = np.linalg.norm(v_shank)
        if n_t < 1e-6 or n_s < 1e-6:
            return np.nan
        cos_a = np.clip(np.dot(v_thigh, v_shank) / (n_t * n_s), -1.0, 1.0)
        return math.degrees(math.acos(cos_a))

    def extract_from_video(self, video_path: str, limb: str) -> pd.DataFrame:
        """
        Process a video file frame-by-frame and return per-frame angle data.

        Parameters
        ----------
        video_path : path to MP4, MOV, or any OpenCV-readable format
        limb       : 'left' or 'right'

        Returns
        -------
        DataFrame: time_sec, knee_angle_deg, knee_visibility, ankle_visibility
        """
        try:
            import cv2
        except ImportError:
            raise ImportError("pip install opencv-python")

        import mediapipe as mp  # noqa: F811

        self._load()
        kp = self._LIMB_KP[limb]

        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            raise IOError(f"Cannot open video: {video_path}")

        native_fps  = cap.get(cv2.CAP_PROP_FPS) or 30.0
        rows: list  = []
        frame_i = 0

        try:
            while True:
                ret, frame = cap.read()
                if not ret:
                    break
                time_ms = int(frame_i * 1000 / native_fps)
                frame_i += 1

                mp_img = mp.Image(
                    image_format=mp.ImageFormat.SRGB,
                    data=cv2.cvtColor(frame, cv2.COLOR_BGR2RGB),
                )
                result = self._landmarker.detect_for_video(mp_img, time_ms)

                if not result.pose_landmarks:
                    rows.append({
                        "time_sec":        time_ms / 1000.0,
                        "knee_angle_deg":  np.nan,
                        "knee_visibility":  0.0,
                        "ankle_visibility": 0.0,
                    })
                    continue

                lm    = result.pose_landmarks[0]
                hip   = lm[kp["hip"]]
                knee  = lm[kp["knee"]]
                ankle = lm[kp["ankle"]]

                rows.append({
                    "time_sec":        time_ms / 1000.0,
                    "knee_angle_deg":  self._interior_angle(hip, knee, ankle),
                    "knee_visibility":  knee.visibility,
                    "ankle_visibility": ankle.visibility,
                })
        finally:
            cap.release()

        return pd.DataFrame(rows)


# ══════════════════════════════════════════════════════════════════════════════
# Convenience: batch CSV runner (for new-model evaluation)
# ══════════════════════════════════════════════════════════════════════════════

def score_csv_folder(
    folder:     str,
    patient_id: str,
    limb:       str,
    config: Optional[PendulasticConfig] = None,
    glob_pattern: str = "*.csv",
) -> SessionSummary:
    """
    Score all CSV files in a folder as a single session.

    Example:
        s = score_csv_folder("Recordings/P007_left/", "P007", "left")
        print(s.mas_mode)
    """
    import glob as _glob
    csvs = sorted(_glob.glob(os.path.join(folder, glob_pattern)))
    if not csvs:
        raise FileNotFoundError(f"No CSV files matching '{glob_pattern}' in {folder}")

    session = PendulasticSession(patient_id, limb, config=config)
    for csv_path in csvs:
        try:
            session.add_trial_from_csv(csv_path)
        except Exception as exc:
            print(f"  Skip {os.path.basename(csv_path)}: {exc}")

    session.print_summary()
    return session.summary()


# ══════════════════════════════════════════════════════════════════════════════
# Self-test (quick smoke check with a synthetic signal)
# ══════════════════════════════════════════════════════════════════════════════

def _smoke_test() -> None:
    """Generate a synthetic healthy-like pendulum signal and verify the pipeline."""
    import math as _m

    fps = 60.0
    t   = np.arange(0, 12.0, 1 / fps)
    # Hold at ~110° (extended) for 1 s, then release into 70° pendulum
    neutral = 110.0
    A0      = 70.0
    omega   = 2 * _m.pi * 0.9   # ~0.9 Hz natural frequency
    gamma   = 0.07               # mild damping

    angle = np.where(
        t < 1.0,
        neutral,
        neutral - A0 * np.exp(-gamma * omega * (t - 1.0)) * np.cos(omega * (t - 1.0))
    )
    angle += np.random.default_rng(42).normal(0, 0.3, size=len(t))
    conf  = np.where(np.abs(angle - neutral) > 2, 0.90, 0.75)

    # Use small noise so all 3 trials reliably produce valid params
    session = PendulasticSession("TEST", "right")
    for i in range(3):
        noise = np.random.default_rng(i).normal(0, 0.15, size=len(t))
        session.add_trial_from_arrays(t, angle + noise, knee_conf=conf, ankle_conf=conf,
                                      trial_id=f"synthetic_T{i+1}")
    session.print_summary()
    s = session.summary()
    assert s.n_valid >= 2,         f"Smoke test: expected at least 2 valid trials, got {s.n_valid}"
    assert s.mas_mode == "0",     f"Smoke test: expected MAS=0, got {s.mas_mode}"
    assert s.pt_median is not None and s.pt_median < 0.20, \
        f"Smoke test: expected low PT (<0.20), got {s.pt_median}"
    print("Smoke test passed.")


if __name__ == "__main__":
    _smoke_test()
