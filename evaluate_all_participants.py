"""
evaluate_all_participants.py
============================
Master pendulum-test validation script.

Automatically discovers every (participant, position, trial) combination that
has both an OptiTrack gold-standard CSV and at least one model CSV.  No
participant IDs, family names, or file paths are hardcoded — drop a new model
CSV into the right directory and it appears in the next run.

Directory conventions (both roots are scanned for model CSVs):
  OptiTrack gold-standard:
    <BASE>/OptiTrack_Recordings/Participant_{id}/Position_{pos}/Height*-Level/
            trial_{n}_optitrack.csv          (case-insensitive)

  Model CSVs — may live in EITHER:
    <BASE>/OptiTrack_Recordings/Participant_{id}/Position_{pos}/Height*-Level/
    <BASE>/Recordings/Participant_{id}/Position_{pos}/Height*-Level/

  Filename pattern:
    P_{pid}_Pos_{pos}_H_*-Level_T_{trial}_{family}_{complexity}_{threshold}.csv

Output tree:
  <BASE>/pendulastic model analysis/all_participants/
    Participant_{id}/
      Position_{pos}/
        Trial_{trial}/
          {family}_grid.png
          summary.png
          raw_angles.png
          swing_rmse.png
    global_model_leaderboard.csv

Run:
    .venv\\Scripts\\python.exe evaluate_all_participants.py
"""

from __future__ import annotations

import collections
import dataclasses
import glob
import itertools
import math
import os
import re
from typing import Dict, List, Optional, Tuple

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from scipy.signal import find_peaks
from scipy.spatial.transform import Rotation as R


# ===========================================================================
# TUNABLE PARAMETERS — the only knobs you should touch
# ===========================================================================
BASE_DIR     = r"C:\Users\cladi\Pendulastic"
OUTPUT_ROOT  = os.path.join(BASE_DIR, "pendulastic model analysis", "all_participants")

# ─────────────────────────────────────────────────────────────────────────────
# TARGET FILTER  (Option A — directory targeting)
# Any participant whose pid contains at least one of these substrings is
# included.  Leave as [] to evaluate ALL discovered participants.
#
# Examples:
#   TARGET_PARTICIPANTS = ["2_"]           # all P2 variants
#   TARGET_PARTICIPANTS = ["2_left_duo"]   # just one folder
#   TARGET_PARTICIPANTS = []               # everyone
# ─────────────────────────────────────────────────────────────────────────────
TARGET_PARTICIPANTS: List[str] = []  # evaluate everyone

BASELINE_SEC = 3.0     # seconds of pre-release baseline for OptiTrack
PLOT_XLIM    = 14.0    # seconds to show on x-axis after release
PEAK_PROM    = 3.0     # minimum peak prominence for flex-signal detection (deg)

# Known families get specific colours/markers; anything else gets auto-assigned
FAMILY_PALETTE = {
    "mediapipe": ("#1976D2", "Blues",   "^"),
    "rtmpose":   ("#E65100", "Oranges", "s"),
    "mmpose":    ("#2E7D32", "Greens",  "D"),
    "fremocap":  ("#6A1B9A", "Purples", "P"),
    "yolo":      ("#B71C1C", "Reds",    "o"),
    "detrpose":  ("#00695C", "YlGn",    "v"),
    "openpose":  ("#F57F17", "YlOrBr",  "h"),
    "pose2sim":  ("#1A237E", "Blues",   "*"),
    "posepipeline": ("#4E342E", "Oranges", "x"),
}
_AUTO_COLORS  = ["#0277BD","#558B2F","#6A1B9A","#AD1457","#00695C",
                  "#E65100","#4527A0","#37474F","#BF360C"]
_AUTO_CMAPS   = ["GnBu","YlGn","RdPu","BuPu","PuBuGn","YlOrRd",
                  "BuGn","OrRd","PuRd"]
_AUTO_MARKERS = ["X","P","h","H","8","<",">","^","v"]

# Regex that parses a model CSV filename into components
_CSV_RE = re.compile(
    r"^P_(\w+)_Pos_(\w+)_H_.+?_T_(\d+)_([A-Za-z]\w*)_(.+)\.csv$",
    re.IGNORECASE,
)
# Regex that identifies an OptiTrack gold-standard CSV
_OPTI_RE = re.compile(r"trial_(\d+)_optitrack\.csv$", re.IGNORECASE)


# ===========================================================================
# DATA CLASSES
# ===========================================================================
@dataclasses.dataclass(frozen=True)
class VariantRecord:
    family:     str
    complexity: str
    threshold:  str
    csv_path:   str


@dataclasses.dataclass
class TrialContext:
    participant_id: str
    position:       str
    trial:          str
    optitrack_path: str
    output_dir:     str
    variants:       List[VariantRecord]

    @property
    def label(self) -> str:
        return f"P{self.participant_id} | Pos {self.position} | Trial {self.trial}"


# ===========================================================================
# DATA-INDEX — dynamic file discovery
# ===========================================================================
class DataIndex:
    """Scans the project tree and builds a list of TrialContext objects."""

    # Root directories to search for model CSVs
    MODEL_ROOTS = ["OptiTrack_Recordings", "Recordings"]

    def __init__(self, base_dir: str, output_root: str):
        self.base_dir    = base_dir
        self.output_root = output_root

    # ------------------------------------------------------------------
    def build(self) -> List[TrialContext]:
        opti_map  = self._find_optitrack()      # (pid, pos, trial) -> path
        model_map = self._find_model_csvs()     # (pid, pos, trial) -> [VariantRecord]

        contexts: List[TrialContext] = []
        for key in sorted(opti_map):
            pid, pos, trial = key
            # Option A — restrict to target participants
            if TARGET_PARTICIPANTS and not any(t in pid for t in TARGET_PARTICIPANTS):
                continue
            if key not in model_map:
                print(f"  [skip] ({pid}, pos={pos}, trial={trial}): "
                      f"OptiTrack found but no model CSVs")
                continue
            out_dir = os.path.join(
                self.output_root,
                f"Participant_{pid}",
                f"Position_{pos}",
                f"Trial_{trial}",
            )
            os.makedirs(out_dir, exist_ok=True)
            contexts.append(TrialContext(
                participant_id=pid,
                position=pos,
                trial=trial,
                optitrack_path=opti_map[key],
                output_dir=out_dir,
                variants=model_map[key],
            ))
        return contexts

    # ------------------------------------------------------------------
    def _find_optitrack(self) -> Dict[Tuple, str]:
        opti_root = os.path.join(self.base_dir, "OptiTrack_Recordings")
        result = {}
        for path in glob.glob(
                os.path.join(opti_root, "**", "*.csv"), recursive=True):
            bn = os.path.basename(path)
            m  = _OPTI_RE.search(bn)
            if not m:
                continue
            trial = m.group(1)
            pid, pos = self._pid_pos_from_path(path, opti_root)
            if pid and pos:
                result[(pid, pos, trial)] = path
        return result

    # ------------------------------------------------------------------
    def _find_model_csvs(self) -> Dict[Tuple, List[VariantRecord]]:
        result: Dict[Tuple, List[VariantRecord]] = collections.defaultdict(list)
        for root_name in self.MODEL_ROOTS:
            root = os.path.join(self.base_dir, root_name)
            if not os.path.isdir(root):
                continue
            for path in glob.glob(
                    os.path.join(root, "**", "*.csv"), recursive=True):
                bn = os.path.basename(path)
                m  = _CSV_RE.match(bn)
                if not m:
                    continue
                pid, pos, trial = m.group(1), m.group(2), m.group(3)
                family = m.group(4).lower()
                rest   = m.group(5)
                parts  = rest.rsplit("_", 1)
                complexity = parts[0] if len(parts) == 2 else rest
                threshold  = parts[1] if len(parts) == 2 else "?"
                result[(pid, pos, trial)].append(
                    VariantRecord(family, complexity, threshold, path)
                )
        # Deduplicate (same CSV discovered in two roots)
        for key in result:
            seen = set()
            deduped = []
            for v in result[key]:
                if v.csv_path not in seen:
                    seen.add(v.csv_path); deduped.append(v)
            result[key] = sorted(deduped,
                                  key=lambda v: (v.family, v.complexity,
                                                 float(v.threshold)
                                                 if v.threshold != "?" else 0))
        return dict(result)

    # ------------------------------------------------------------------
    @staticmethod
    def _pid_pos_from_path(path: str, root: str) -> Tuple[Optional[str], Optional[str]]:
        rel = os.path.relpath(path, root)
        parts = rel.replace("\\", "/").split("/")
        pid = pos = None
        for part in parts:
            pm = re.match(r"Participant_(\w+)$", part, re.I)
            if pm:
                pid = pm.group(1)
            pm = re.match(r"Position_(\w+)$", part, re.I)
            if pm:
                pos = pm.group(1)
        return pid, pos


# ===========================================================================
# FAMILY STYLE RESOLVER
# ===========================================================================
class StyleCache:
    def __init__(self):
        self._assigned: Dict[str, int] = {}
        self._idx = 0

    def get(self, family: str) -> Tuple[str, str, str]:
        fam = family.lower()
        if fam in FAMILY_PALETTE:
            col, cmap, mkr = FAMILY_PALETTE[fam]
            return col, cmap, mkr
        if fam not in self._assigned:
            i = self._idx % len(_AUTO_COLORS)
            self._assigned[fam] = self._idx
            self._idx += 1
        i = self._assigned[fam]
        return (_AUTO_COLORS[i % len(_AUTO_COLORS)],
                _AUTO_CMAPS[i % len(_AUTO_CMAPS)],
                _AUTO_MARKERS[i % len(_AUTO_MARKERS)])


# ===========================================================================
# SIGNAL PROCESSOR  (pure functions, no global state)
# ===========================================================================
class SignalProcessor:

    @staticmethod
    def load_optitrack(csv_path: str) -> Tuple[np.ndarray, np.ndarray, int]:
        header_idx = 0
        with open(csv_path, "r", encoding="utf-8-sig") as fh:
            for i, line in enumerate(fh):
                if line.split(",")[0].strip().lower() == "frame":
                    header_idx = i; break
        df = pd.read_csv(csv_path, skiprows=header_idx, header=0)
        df = df.apply(pd.to_numeric, errors="coerce").ffill().bfill()
        t  = df.iloc[:, 1].values.astype(float); t -= t[0]
        fs = max(1, round(1.0 / float(np.median(np.diff(t[t > 0])))))
        tx, ty, tz, tw = (df.iloc[:, c].values.astype(float) for c in [2, 3, 4, 5])
        sx, sy, sz, sw = (df.iloc[:, c].values.astype(float) for c in [9, 10, 11, 12])
        r_thigh  = R.from_quat(np.column_stack([tx, ty, tz, tw]))
        r_shank  = R.from_quat(np.column_stack([sx, sy, sz, sw]))
        knee_deg = np.degrees((r_thigh.inv() * r_shank).magnitude())
        return t, knee_deg, int(fs)

    @staticmethod
    def detect_optitrack_release(
            o_t: np.ndarray, o_ang: np.ndarray, o_fs: int
    ) -> Tuple[int, float, float, float]:
        n_base   = int(BASELINE_SEC * o_fs)
        baseline = float(np.nanmean(o_ang[:n_base]))
        search   = np.abs(o_ang[n_base:] - baseline)
        for thr in [5.0, 2.5, 2.0, 1.0]:
            hits = np.where(search > thr)[0]
            if len(hits):
                idx = int(n_base + hits[0])
                return idx, float(o_t[idx]), float(o_ang[idx]), baseline
        return 0, float(o_t[0]), float(o_ang[0]), baseline

    @staticmethod
    def load_model_csv(csv_path: str) -> Tuple[np.ndarray, np.ndarray, float]:
        df   = pd.read_csv(csv_path)
        m_t  = df["time_sec"].values.astype(float)
        m_ang = df["knee_angle_deg"].values.astype(float)
        diffs = np.diff(m_t[np.isfinite(m_t)])
        m_fps = round(1.0 / float(np.median(diffs[diffs > 0]))) if len(diffs) else 30.0
        return m_t, m_ang, float(m_fps)

    @staticmethod
    def detect_model_release(
            m_ang: np.ndarray, m_t: np.ndarray,
            plateau_thr: float = 165.0, drop_thr: float = 100.0
    ) -> Tuple[int, float, float]:
        below = np.where(m_ang < drop_thr)[0]
        if len(below) == 0:
            idx = int(np.argmax(m_ang))
            return idx, float(m_t[idx]), float(m_ang[idx])
        first_deep = int(below[0])
        if first_deep == 0:
            idx = int(np.argmax(m_ang))
            return idx, float(m_t[idx]), float(m_ang[idx])
        for thr in [plateau_thr, 155.0, 145.0, 135.0, 125.0]:
            mask  = (np.arange(len(m_ang)) < first_deep) & (m_ang >= thr)
            above = np.where(mask)[0]
            if len(above) > 0:
                idx = int(above[-1])
                return idx, float(m_t[idx]), float(m_ang[idx])
        sub = m_ang[:first_deep]
        idx = int(np.argmax(sub)) if len(sub) > 0 else 0
        return idx, float(m_t[idx]), float(m_ang[idx])

    @staticmethod
    def make_opti_flex(o_ang: np.ndarray, o_rel_idx: int, o_rel_ang: float) -> np.ndarray:
        return o_ang[o_rel_idx:] - o_rel_ang

    @staticmethod
    def make_model_flex(m_ang: np.ndarray, m_rel_idx: int, m_rel_ang: float) -> np.ndarray:
        return m_rel_ang - m_ang[m_rel_idx:]

    @staticmethod
    def find_flex_peaks(flex_sig: np.ndarray, fps: float) -> np.ndarray:
        guard    = max(1, int(0.1 * fps))
        min_dist = max(5, int(0.3 * fps))
        valid    = flex_sig[guard:]
        max_h    = float(np.nanmax(valid)) if np.any(np.isfinite(valid)) else 0.0
        min_h    = max(5.0, max_h * 0.20)
        pks, _   = find_peaks(flex_sig[guard:], distance=min_dist,
                               prominence=PEAK_PROM, height=min_h)
        return pks + guard

    @staticmethod
    def find_pits(signal: np.ndarray, peaks: np.ndarray) -> np.ndarray:
        pits = []
        for i in range(len(peaks) - 1):
            s = int(peaks[i]); e = min(int(peaks[i + 1]) + 1, len(signal))
            pits.append(int(np.argmin(signal[s:e])) + s)
        return np.array(pits, dtype=int)

    @classmethod
    def analyze(
            cls,
            o_flex: np.ndarray, o_t_rel: np.ndarray, o_fs: int,
            m_ang: np.ndarray, m_t: np.ndarray, m_fps: float,
            m_rel_idx: int, m_rel_ang: float,
    ) -> dict:
        m_flex  = cls.make_model_flex(m_ang, m_rel_idx, m_rel_ang)
        m_t_rel = m_t[m_rel_idx:] - m_t[m_rel_idx]

        o_peaks = cls.find_flex_peaks(o_flex, float(o_fs))
        m_peaks = cls.find_flex_peaks(m_flex, m_fps)
        o_pits  = cls.find_pits(o_flex, o_peaks)
        m_pits  = cls.find_pits(m_flex, m_peaks)

        if len(o_peaks) > 0 and len(m_peaks) > 0:
            time_shift = float(o_t_rel[o_peaks[0]]) - float(m_t_rel[m_peaks[0]])
        else:
            time_shift = 0.0
        m_t_plot = m_t_rel + time_shift

        # Match peaks
        o_t_pks, o_pk_v, m_pk_v, pk_sq = [], [], [], []
        for i, op in enumerate(o_peaks):
            ov = float(o_flex[op])
            o_t_pks.append(float(o_t_rel[op])); o_pk_v.append(ov)
            if i < len(m_peaks):
                mv = float(m_flex[m_peaks[i]])
                m_pk_v.append(mv); pk_sq.append((mv - ov) ** 2)
            else:
                m_pk_v.append(None); pk_sq.append(None)

        # Match pits
        o_t_pts, o_pt_v, m_pt_v, pt_sq = [], [], [], []
        for j, op in enumerate(o_pits):
            ov = float(o_flex[op])
            o_t_pts.append(float(o_t_rel[op])); o_pt_v.append(ov)
            if j < len(m_pits):
                mv = float(m_flex[m_pits[j]])
                m_pt_v.append(mv); pt_sq.append((mv - ov) ** 2)
            else:
                m_pt_v.append(None); pt_sq.append(None)

        all_sq = [v for v in pk_sq + pt_sq if v is not None]
        rmse   = float(np.sqrt(np.mean(all_sq))) if all_sq else float("nan")

        return dict(
            m_flex=m_flex, m_t_rel=m_t_rel, m_t_plot=m_t_plot,
            m_peaks=m_peaks, m_pits=m_pits,
            o_peaks=o_peaks, o_pits=o_pits,
            time_shift=time_shift,
            o_t_pks=o_t_pks, o_pk_v=o_pk_v, m_pk_v=m_pk_v,
            o_t_pts=o_t_pts, o_pt_v=o_pt_v, m_pt_v=m_pt_v,
            rmse=rmse,
            m_rel_ang=m_rel_ang,
            n_matched_peaks=sum(1 for v in m_pk_v if v is not None),
            n_matched_pits=sum(1 for v in m_pt_v if v is not None),
        )


# ===========================================================================
# PLOT ENGINE
# ===========================================================================
class PlotEngine:

    def __init__(self, style_cache: StyleCache):
        self.style = style_cache

    # ------------------------------------------------------------------
    def draw_variant_on_ax(
            self, ax: plt.Axes,
            o_flex: np.ndarray, o_t_rel: np.ndarray,
            res: dict, color: str, label: str,
    ) -> None:
        o_mask = o_t_rel >= -0.2
        m_mask = res["m_t_plot"] >= -0.2

        ax.plot(o_t_rel[o_mask], o_flex[o_mask],
                color="black", lw=1.8, zorder=5, label="OptiTrack")
        ax.plot(res["m_t_plot"][m_mask], res["m_flex"][m_mask],
                color=color, lw=1.8, alpha=0.85, zorder=4, label=label)

        for i, (ot, ov, mv) in enumerate(
                zip(res["o_t_pks"], res["o_pk_v"], res["m_pk_v"])):
            ax.scatter(ot, ov, color="black", s=60, zorder=8,
                       edgecolors="white", linewidths=0.9)
            ax.text(ot, ov + 1.5, str(i + 1),
                    ha="center", va="bottom", fontsize=7,
                    color="black", fontweight="bold")
            if mv is None:
                continue
            if i < len(res["m_peaks"]):
                xm = float(res["m_t_plot"][res["m_peaks"][i]])
                ax.scatter(xm, mv, color=color, s=50, zorder=7,
                           edgecolors="black", linewidths=0.7)
            ax.plot([ot, ot], [ov, mv],
                    color="gray", lw=1.0, ls="--", alpha=0.55, zorder=3)
            ax.text(ot + 0.1, (ov + mv) / 2, f"{abs(mv - ov):.1f}",
                    ha="left", va="center", fontsize=6.5, color="dimgray")

        for j, (ot, ov, mv) in enumerate(
                zip(res["o_t_pts"], res["o_pt_v"], res["m_pt_v"])):
            ax.scatter(ot, ov, color="black", s=45, marker="D", zorder=8,
                       edgecolors="white", linewidths=0.8)
            if mv is None:
                continue
            if j < len(res["m_pits"]):
                xm = float(res["m_t_plot"][res["m_pits"][j]])
                ax.scatter(xm, mv, color=color, s=40, marker="D", zorder=7,
                           facecolors="none", edgecolors=color, linewidths=0.7)
            ax.plot([ot, ot], [ov, mv],
                    color="steelblue", lw=0.9, ls=":", alpha=0.5, zorder=3)
            ax.text(ot + 0.1, (ov + mv) / 2, f"{abs(mv - ov):.1f}",
                    ha="left", va="center", fontsize=6, color="steelblue")

    # ------------------------------------------------------------------
    def family_grid(
            self, ctx: TrialContext,
            family: str,
            variant_list: List[VariantRecord],
            o_flex: np.ndarray, o_t_rel: np.ndarray, o_fs: int,
    ) -> Optional[dict]:
        """Render one family's complexity×threshold grid. Returns best result dict."""
        complexities = sorted(set(v.complexity for v in variant_list))
        thresholds   = sorted(set(v.threshold for v in variant_list),
                               key=lambda x: float(x) if x != "?" else 0)
        c_idx = {c: i for i, c in enumerate(complexities)}
        t_idx = {t: i for i, t in enumerate(thresholds)}
        nrows, ncols = len(complexities), len(thresholds)

        color, cmap_name, _ = self.style.get(family)
        cmap = plt.colormaps.get_cmap(cmap_name)

        fig, axes = plt.subplots(
            nrows, ncols,
            figsize=(max(5 * ncols, 10), max(4 * nrows, 5)),
            squeeze=False,
        )
        fig.suptitle(
            f"{ctx.label} | {family.upper()}\n"
            f"Knee Flex Deviation from Release (deg)  |  t=0 = release moment",
            fontsize=13, fontweight="bold", y=1.01,
        )

        plotted    = set()
        rmse_tab   = []
        best_rmse  = float("inf")
        best_entry = None

        for v in variant_list:
            row = c_idx[v.complexity]; col = t_idx[v.threshold]
            ax  = axes[row][col]; plotted.add((row, col))
            try:
                m_t, m_ang, m_fps = SignalProcessor.load_model_csv(v.csv_path)
            except Exception as exc:
                ax.set_title(f"{v.complexity}/{v.threshold}\n[load error]", fontsize=8)
                ax.axis("off"); continue

            m_rel_idx, _, m_rel_ang = SignalProcessor.detect_model_release(m_ang, m_t)
            res  = SignalProcessor.analyze(o_flex, o_t_rel, o_fs,
                                           m_ang, m_t, m_fps, m_rel_idx, m_rel_ang)
            rmse = res["rmse"]
            rmse_tab.append((v.complexity, v.threshold, rmse))

            if not math.isnan(rmse) and rmse < best_rmse:
                best_rmse  = rmse
                best_entry = dict(variant=v, res=res, rmse=rmse,
                                  label=f"{family} {v.complexity}  thr={v.threshold}",
                                  m_ang=m_ang, m_t=m_t, m_fps=m_fps)

            shade = cmap(0.4 + 0.5 * (row / max(nrows - 1, 1)))
            self.draw_variant_on_ax(ax, o_flex, o_t_rel, res, shade,
                                    f"{family} {v.complexity}")

            rmse_str = f"{rmse:.1f}" if not math.isnan(rmse) else "N/A"
            n_ext    = res["n_matched_peaks"] + res["n_matched_pits"]
            ax.text(0.03, 0.97, f"RMSE = {rmse_str} deg  (n={n_ext})",
                    transform=ax.transAxes, ha="left", va="top",
                    fontsize=10, fontweight="bold", color=shade,
                    bbox=dict(boxstyle="round,pad=0.4", facecolor="white",
                              edgecolor=shade, linewidth=2.0, alpha=0.97))
            ax.set_xlim(-0.3, PLOT_XLIM)
            ax.set_ylim(bottom=-5)
            ax.set_title(f"complexity={v.complexity}  |  threshold={v.threshold}",
                         fontsize=9, fontweight="bold")
            ax.set_xlabel("Time since release (s)", fontsize=8)
            ax.set_ylabel("Knee Flexion Deviation (deg)", fontsize=8)
            ax.axhline(0, color="gray", lw=0.6, ls=":", alpha=0.5)
            ax.legend(loc="upper right", fontsize=7, framealpha=0.85)
            ax.grid(True, ls=":", alpha=0.35); ax.tick_params(labelsize=7)

        for r in range(nrows):
            for c in range(ncols):
                if (r, c) not in plotted:
                    axes[r][c].set_visible(False)

        fig.tight_layout()
        fname = os.path.join(ctx.output_dir, f"{family}_grid.png")
        fig.savefig(fname, dpi=150, bbox_inches="tight")
        plt.close(fig)

        if rmse_tab:
            best_row = min((r for r in rmse_tab if not math.isnan(r[2])),
                           key=lambda x: x[2], default=None)
            if best_row:
                print(f"    {family:<14} {nrows}x{ncols} grid  "
                      f"best={best_row[0]} thr={best_row[1]} "
                      f"RMSE={best_row[2]:.1f} deg")

        return best_entry

    # ------------------------------------------------------------------
    def summary_panel(
            self, ctx: TrialContext,
            best_per_family: Dict[str, dict],
            o_flex: np.ndarray, o_t_rel: np.ndarray,
    ) -> None:
        fams  = sorted(best_per_family.keys())
        n_fam = len(fams)
        if n_fam == 0:
            return
        ncols = 2; nrows = math.ceil(n_fam / ncols)

        fig, axes = plt.subplots(nrows, ncols,
                                  figsize=(16, 5 * nrows), squeeze=False)
        fig.suptitle(
            f"{ctx.label} | Best Variant per Family\n"
            f"Knee Flex Deviation from Release (deg)  |  t=0 = release",
            fontsize=13, fontweight="bold", y=1.01,
        )
        for idx, fam in enumerate(fams):
            r, c = divmod(idx, ncols)
            ax   = axes[r][c]
            d    = best_per_family[fam]
            color, _, _ = self.style.get(fam)

            self.draw_variant_on_ax(ax, o_flex, o_t_rel, d["res"], color, d["label"])
            rmse_str = f"{d['rmse']:.1f}" if not math.isnan(d["rmse"]) else "N/A"
            n_ext    = d["res"]["n_matched_peaks"] + d["res"]["n_matched_pits"]
            ax.text(0.03, 0.97, f"RMSE = {rmse_str} deg  (n={n_ext})",
                    transform=ax.transAxes, ha="left", va="top",
                    fontsize=13, fontweight="bold", color=color,
                    bbox=dict(boxstyle="round,pad=0.45", facecolor="white",
                              edgecolor=color, linewidth=2.5, alpha=0.97))
            ax.set_title(fam.upper(), fontsize=13, fontweight="bold", color=color)
            ax.set_xlim(-0.3, PLOT_XLIM); ax.set_ylim(bottom=-5)
            ax.set_xlabel("Time since release (s)", fontsize=9)
            ax.set_ylabel("Knee Flexion Deviation (deg)", fontsize=9)
            ax.axhline(0, color="gray", lw=0.6, ls=":", alpha=0.5)
            ax.legend(loc="upper right", fontsize=8, framealpha=0.88)
            ax.grid(True, ls=":", alpha=0.35); ax.tick_params(labelsize=8)

        for idx in range(n_fam, nrows * ncols):
            rr, cc = divmod(idx, ncols); axes[rr][cc].set_visible(False)

        fig.tight_layout()
        fig.savefig(os.path.join(ctx.output_dir, "summary.png"),
                    dpi=150, bbox_inches="tight")
        plt.close(fig)

    # ------------------------------------------------------------------
    def raw_angles_panel(
            self, ctx: TrialContext,
            best_per_family: Dict[str, dict],
            o_ang: np.ndarray, o_t_rel: np.ndarray, o_rel_idx: int,
    ) -> None:
        fams  = sorted(best_per_family.keys())
        n_fam = len(fams)
        if n_fam == 0:
            return

        fig, axes = plt.subplots(n_fam, 1,
                                  figsize=(14, 4 * n_fam), squeeze=False,
                                  sharex=False)
        fig.suptitle(
            f"{ctx.label} | RAW Knee Angles — Model (left) vs OptiTrack (right)\n"
            f"Both shifted so t=0 = release moment",
            fontsize=12, fontweight="bold", y=1.01,
        )
        for idx, fam in enumerate(fams):
            ax    = axes[idx][0]
            d     = best_per_family[fam]
            res   = d["res"]
            color, _, _ = self.style.get(fam)

            ax2 = ax.twinx()
            o_mask = (o_t_rel >= -0.5) & (o_t_rel <= PLOT_XLIM)
            ax2.plot(o_t_rel[o_mask], o_ang[o_rel_idx:][o_mask],
                     color="black", lw=1.4, alpha=0.6, ls="--",
                     label="OptiTrack quat-mag (right)")
            ax2.set_ylabel("OptiTrack quat magnitude (deg)", fontsize=8, color="gray")
            ax2.tick_params(axis="y", labelsize=7, colors="gray")

            # Re-derive the model raw angle for the best CSV
            try:
                m_t, m_ang, m_fps = SignalProcessor.load_model_csv(d["variant"].csv_path)
                m_rel_idx, _, _ = SignalProcessor.detect_model_release(m_ang, m_t)
                m_t_rel  = m_t[m_rel_idx:] - m_t[m_rel_idx]
                m_t_plot = m_t_rel + res["time_shift"]
                m_ang_post = m_ang[m_rel_idx:]
                m_mask = (m_t_plot >= -0.5) & (m_t_plot <= PLOT_XLIM)
                ax.plot(m_t_plot[m_mask], m_ang_post[m_mask],
                        color=color, lw=1.8, label=f"{d['label']} (left)")
            except Exception:
                pass

            ax.set_ylabel("Model knee_angle_deg (deg)", fontsize=8, color=color)
            ax.tick_params(axis="y", labelsize=7, labelcolor=color)
            ax.set_xlabel("Time since release (s)", fontsize=8)
            ax.set_title(f"{fam.upper()} - best: {d['label']}", fontsize=9)
            ax.set_xlim(-0.5, PLOT_XLIM)
            ax.text(0.02, 0.03,
                    "Model: ~179 deg = extended, ~82 deg = max flex  |  "
                    "OptiTrack: higher quat-mag = more relative rotation",
                    transform=ax.transAxes, fontsize=6.5, color="gray", va="bottom")

            l1, la1 = ax.get_legend_handles_labels()
            l2, la2 = ax2.get_legend_handles_labels()
            ax.legend(l1 + l2, la1 + la2, loc="upper right",
                      fontsize=7, framealpha=0.85)
            ax.grid(True, ls=":", alpha=0.35)

        fig.tight_layout()
        fig.savefig(os.path.join(ctx.output_dir, "raw_angles.png"),
                    dpi=150, bbox_inches="tight")
        plt.close(fig)

    # ------------------------------------------------------------------
    def swing_rmse_panel(
            self, ctx: TrialContext,
            best_per_family: Dict[str, dict],
    ) -> None:
        """Per-swing RMSE bar chart — shows how error evolves across oscillations."""
        fams = sorted(best_per_family.keys())
        if not fams:
            return

        # Collect per-swing absolute errors for each family
        swing_data: Dict[str, Dict[int, float]] = {}
        for fam in fams:
            d = best_per_family[fam]; res = d["res"]
            errors = {}
            for i, (ov, mv) in enumerate(zip(res["o_pk_v"], res["m_pk_v"])):
                if mv is not None:
                    errors[i + 1] = abs(mv - ov)
            for j, (ov, mv) in enumerate(zip(res["o_pt_v"], res["m_pt_v"])):
                if mv is not None:
                    key = -(j + 1)  # negative key = pit
                    errors[key] = abs(mv - ov)
            swing_data[fam] = errors

        all_keys = sorted(set(itertools.chain.from_iterable(swing_data.values())))
        peak_keys = sorted([k for k in all_keys if k > 0])
        pit_keys  = sorted([k for k in all_keys if k < 0])
        labels    = [f"Pk{k}" for k in peak_keys] + [f"Pit{abs(k)}" for k in pit_keys]

        n_fam  = len(fams)
        width  = 0.8 / n_fam
        x_pos  = np.arange(len(labels))

        fig, ax = plt.subplots(figsize=(max(10, len(labels) * 0.9), 5))
        for i, fam in enumerate(fams):
            color, _, mkr = self.style.get(fam)
            vals = [swing_data[fam].get(k, float("nan"))
                    for k in peak_keys + pit_keys]
            offsets = x_pos + (i - n_fam / 2 + 0.5) * width
            bars = ax.bar(offsets, vals, width=width * 0.85,
                          color=color, alpha=0.82, label=fam, zorder=3)
            for bar, val in zip(bars, vals):
                if not math.isnan(val):
                    ax.text(bar.get_x() + bar.get_width() / 2,
                            bar.get_height() + 0.3,
                            f"{val:.1f}", ha="center", va="bottom",
                            fontsize=6.5, color=color)

        ax.set_xticks(x_pos); ax.set_xticklabels(labels, fontsize=8, rotation=30)
        ax.set_ylabel("Absolute error vs OptiTrack (deg)", fontsize=9)
        ax.set_title(
            f"{ctx.label} | Per-Swing Absolute Error — Best Variant per Family",
            fontsize=11, fontweight="bold",
        )
        ax.axvline(len(peak_keys) - 0.5, color="gray", lw=1.0, ls="--", alpha=0.5)
        ax.text(len(peak_keys) - 0.5, ax.get_ylim()[1] * 0.95,
                "peaks | pits", ha="center", va="top", fontsize=7, color="gray")
        ax.legend(loc="upper right", fontsize=8, framealpha=0.85)
        ax.grid(True, axis="y", ls=":", alpha=0.35); ax.tick_params(labelsize=8)

        fig.tight_layout()
        fig.savefig(os.path.join(ctx.output_dir, "swing_rmse.png"),
                    dpi=150, bbox_inches="tight")
        plt.close(fig)


# ===========================================================================
# MAIN EVALUATOR
# ===========================================================================
class PendulasticEvaluator:

    def __init__(self):
        self.output_root = OUTPUT_ROOT
        os.makedirs(self.output_root, exist_ok=True)
        self.index  = DataIndex(BASE_DIR, self.output_root)
        self.styles = StyleCache()
        self.plots  = PlotEngine(self.styles)
        self.all_records: List[dict] = []

    # ------------------------------------------------------------------
    def run(self) -> None:
        contexts = self.index.build()
        print(f"\nDiscovered {len(contexts)} (participant, position, trial) combination(s).\n")

        for ctx in contexts:
            self._process_trial(ctx)

        self._save_leaderboard()
        self._save_reliability_report()
        print(f"\nDone. All outputs in:\n  {self.output_root}")

    # ------------------------------------------------------------------
    def _process_trial(self, ctx: TrialContext) -> None:
        # Option B — skip if all standard output plots already exist
        _sentinel_plots = ["summary.png", "raw_angles.png", "swing_rmse.png"]
        if all(os.path.isfile(os.path.join(ctx.output_dir, f)) for f in _sentinel_plots):
            print(f"\n  {ctx.label} — [SKIPPING: plots already exist]")
            return

        print(f"\n{'=' * 70}")
        print(f"  {ctx.label}")
        print(f"{'=' * 70}")
        print(f"  OptiTrack: {os.path.basename(ctx.optitrack_path)}")

        try:
            o_t, o_ang, o_fs = SignalProcessor.load_optitrack(ctx.optitrack_path)
        except Exception as exc:
            print(f"  [ERROR loading OptiTrack] {exc}"); return

        o_rel_idx, o_rel_t, o_rel_ang, o_baseline = \
            SignalProcessor.detect_optitrack_release(o_t, o_ang, o_fs)

        o_flex  = SignalProcessor.make_opti_flex(o_ang, o_rel_idx, o_rel_ang)
        o_t_rel = o_t[o_rel_idx:] - o_t[o_rel_idx]
        o_peaks = SignalProcessor.find_flex_peaks(o_flex, float(o_fs))

        print(f"  release t={o_rel_t:.3f}s  angle={o_rel_ang:.2f} deg  "
              f"baseline={o_baseline:.2f} deg  n_peaks={len(o_peaks)}")

        if len(o_peaks) == 0:
            print("  [WARN] No OptiTrack peaks — skipping trial."); return

        # Group variants by family
        family_variants: Dict[str, List[VariantRecord]] = collections.defaultdict(list)
        for v in ctx.variants:
            family_variants[v.family].append(v)

        best_per_family: Dict[str, dict] = {}

        for family in sorted(family_variants.keys()):
            best = self.plots.family_grid(
                ctx, family, family_variants[family],
                o_flex, o_t_rel, o_fs,
            )
            if best:
                best_per_family[family] = best

        # Collect records for leaderboard
        for fam, best in best_per_family.items():
            res = best["res"]
            v   = best["variant"]
            for i, (ov, mv) in enumerate(zip(res["o_pk_v"], res["m_pk_v"])):
                if mv is not None:
                    self.all_records.append(dict(
                        participant=ctx.participant_id,
                        position=ctx.position,
                        trial=ctx.trial,
                        family=fam,
                        complexity=v.complexity,
                        threshold=v.threshold,
                        variant=f"{fam}_{v.complexity}_{v.threshold}",
                        extremum="peak",
                        swing=i + 1,
                        opti_deg=round(ov, 2),
                        model_deg=round(mv, 2),
                        error_deg=round(mv - ov, 2),
                        abs_err=round(abs(mv - ov), 2),
                    ))
            for j, (ov, mv) in enumerate(zip(res["o_pt_v"], res["m_pt_v"])):
                if mv is not None:
                    self.all_records.append(dict(
                        participant=ctx.participant_id,
                        position=ctx.position,
                        trial=ctx.trial,
                        family=fam,
                        complexity=v.complexity,
                        threshold=v.threshold,
                        variant=f"{fam}_{v.complexity}_{v.threshold}",
                        extremum="pit",
                        swing=j + 1,
                        opti_deg=round(ov, 2),
                        model_deg=round(mv, 2),
                        error_deg=round(mv - ov, 2),
                        abs_err=round(abs(mv - ov), 2),
                    ))

        # Generate plots for this trial
        if best_per_family:
            self.plots.summary_panel(ctx, best_per_family, o_flex, o_t_rel)
            print(f"  Saved: summary.png")

            self.plots.raw_angles_panel(ctx, best_per_family,
                                         o_ang, o_t_rel, o_rel_idx)
            print(f"  Saved: raw_angles.png")

            self.plots.swing_rmse_panel(ctx, best_per_family)
            print(f"  Saved: swing_rmse.png")

    # ------------------------------------------------------------------
    def _save_leaderboard(self) -> None:
        if not self.all_records:
            print("\n[WARN] No error records collected — leaderboard empty."); return

        df = pd.DataFrame(self.all_records)

        # Per-participant-position ranking
        for (pid, pos), grp in df.groupby(["participant", "position"]):
            ranking = (
                grp.groupby("variant")
                   .apply(lambda g: pd.Series({
                       "family":     g["family"].iloc[0],
                       "complexity": g["complexity"].iloc[0],
                       "threshold":  g["threshold"].iloc[0],
                       "n_extrema":  len(g),
                       "rmse_deg":   round(float(np.sqrt(np.mean(g["abs_err"] ** 2))), 2),
                       "mae_deg":    round(float(g["abs_err"].mean()), 2),
                   }), include_groups=False)
                   .sort_values("rmse_deg")
                   .reset_index()
            )
            ranking.index += 1
            out_dir = os.path.join(
                self.output_root, f"Participant_{pid}", f"Position_{pos}")
            os.makedirs(out_dir, exist_ok=True)
            csv_path = os.path.join(out_dir, "model_ranking.csv")
            ranking.to_csv(csv_path)
            print(f"\n  P{pid} Pos{pos} ranking -> {csv_path}")
            print(ranking[["variant", "n_extrema", "rmse_deg", "mae_deg"]].to_string())

        # Global leaderboard — aggregate across ALL participants and positions
        global_rank = (
            df.groupby("variant")
              .apply(lambda g: pd.Series({
                  "family":       g["family"].iloc[0],
                  "complexity":   g["complexity"].iloc[0],
                  "threshold":    g["threshold"].iloc[0],
                  "n_participants": g["participant"].nunique(),
                  "n_positions":    g["position"].nunique(),
                  "n_trials":       g["trial"].nunique(),
                  "n_extrema":      len(g),
                  "rmse_deg":       round(float(np.sqrt(np.mean(g["abs_err"] ** 2))), 2),
                  "mae_deg":        round(float(g["abs_err"].mean()), 2),
                  "max_err_deg":    round(float(g["abs_err"].max()), 2),
              }), include_groups=False)
              .sort_values("rmse_deg")
              .reset_index()
        )
        global_rank.index += 1

        out_path = os.path.join(self.output_root, "global_model_leaderboard.csv")
        global_rank.to_csv(out_path)
        print(f"\n{'=' * 70}")
        print("GLOBAL MODEL LEADERBOARD (all participants + positions)")
        print(f"{'=' * 70}")
        print(global_rank[["variant", "n_participants", "n_extrema",
                             "rmse_deg", "mae_deg", "max_err_deg"]].to_string())
        print(f"\nSaved: {out_path}")

    # ------------------------------------------------------------------
    def _save_reliability_report(self) -> None:
        """For each model family, compute per-trial RMSE
        (sqrt(mean(abs_err**2)) over that trial's matched peak/pit records,
        the same formula _save_leaderboard() uses per-variant) grouped by
        (participant, position, trial), then ICC(1,1) across each
        participant's trials with >=2 -- repeat-measures reliability of that
        family's tracking quality -- using reliability_stats, extracted from
        validate_controls.py since that file cannot currently import.
        Writes reliability_report.csv alongside global_model_leaderboard.csv.
        A family with no participant having >=2 trials gets icc_rmse left
        blank rather than a fabricated value."""
        import csv
        from collections import defaultdict
        import reliability_stats

        # (family, participant, position, trial) -> list of abs_err values
        by_trial = defaultdict(list)
        for rec in self.all_records:
            key = (rec["family"], rec["participant"], rec["position"], rec["trial"])
            by_trial[key].append(rec["abs_err"])

        # family -> participant -> [per-trial RMSE, ...]
        by_family_participant = defaultdict(lambda: defaultdict(list))
        for (family, participant, position, trial), errs in by_trial.items():
            trial_rmse = float(np.sqrt(np.mean(np.array(errs) ** 2)))
            by_family_participant[family][participant].append(trial_rmse)

        out_path = os.path.join(self.output_root, "reliability_report.csv")
        with open(out_path, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["family", "n_participants_with_repeats", "icc_rmse",
                            "icc_ci_lo", "icc_ci_hi"])
            for family, by_participant in sorted(by_family_participant.items()):
                groups = [v for v in by_participant.values() if len(v) >= 2]
                if not groups:
                    writer.writerow([family, 0, "", "", ""])
                    continue
                result = reliability_stats.icc_one_way(groups)
                writer.writerow([family, len(groups),
                                f"{result['icc']:.4f}" if not np.isnan(result["icc"]) else "",
                                f"{result['ci_lo']:.4f}" if not np.isnan(result["ci_lo"]) else "",
                                f"{result['ci_hi']:.4f}" if not np.isnan(result["ci_hi"]) else ""])
        print(f"Saved: {out_path}")


# ===========================================================================
# ENTRY POINT
# ===========================================================================
if __name__ == "__main__":
    PendulasticEvaluator().run()
