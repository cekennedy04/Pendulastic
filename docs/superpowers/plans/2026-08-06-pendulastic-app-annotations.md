# Pendulastic App Annotation Features Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Port `pendulastic_viewer.py`'s two-part annotation feature (auto plot annotations + annotated video export) into `pendulastic_app.py`'s `PostProcessingPanel`, sharing the plot-annotation drawing logic with the viewer instead of duplicating it.

**Architecture:** Extract the viewer's `_annotate_plot` marker-drawing logic into a new module-level `draw_pt_annotations(ax, params, manual_release=False)` function in `pendulastic_pt_score.py` (the module both files already import `compute_pt_params` from); both the viewer and `PostProcessingPanel` call it. For video export, `BiomechanicalEngine.run_offline_track` (pendulastic_app.py) is extended to optionally collect the per-frame `hip`/`knee`/`ankle` positions it already computes but currently discards, and a new export worker in `PostProcessingPanel` reuses the viewer's existing `_draw()` overlay-drawing helper and `TRAIL_LEN` constant via import.

**Tech Stack:** Python 3.13, Tkinter, matplotlib (`FigureCanvasTkAgg`), OpenCV (`cv2`), `pytest` with headless `tk.Tk()` + `.withdraw()` roots (existing convention in `tests/test_post_processing_panel.py`).

## Global Constraints

- Follow the existing guarded-import fallback pattern in `pendulastic_app.py` (`try: import X ... except Exception: X = None`) for every new cross-module import — never let a missing optional dependency crash the app at startup.
- Follow the existing background-thread + `self.after(0, ...)` pattern for any UI update triggered from a worker thread (already used by `_on_upload_video`) — never touch Tkinter widgets directly from a non-UI thread.
- No new third-party dependencies — everything needed (`cv2`, `numpy`, `matplotlib`) is already in `.venv`.
- Preserve backward compatibility of `BiomechanicalEngine.run_offline_track`'s existing signature/return type for all callers that don't opt in to the new parameter.
- Run tests with `.venv\Scripts\pytest tests\<file> -v` (Windows venv convention already used in this repo).

---

### Task 1: Extract `draw_pt_annotations` into `pendulastic_pt_score.py`, refactor the viewer to use it

**Files:**
- Modify: `pendulastic_pt_score.py` (add new function)
- Modify: `pendulastic_viewer.py:80-83` (import), `pendulastic_viewer.py:4593-4739` (`_annotate_plot`)
- Test: `tests/test_pt_score.py`

**Interfaces:**
- Produces: `draw_pt_annotations(ax, params: dict, manual_release: bool = False) -> list | None` — draws PT key-point markers (rest line, release line, A₀ bracket, first trough/peak with labels, faded later peaks/troughs, N-cycles badge) onto the given matplotlib `Axes`. Returns `None` immediately (drawing nothing) if `params` lacks sufficient data (`params.get("neutral_deg") is None or params.get("t_r") is None or len(params["t_r"]) < 2`); otherwise returns the list of created artists. Callers are responsible for clearing any previously-tracked artists before calling this (it does not track state itself — no `self`, no persistent list).

- [ ] **Step 1: Add `draw_pt_annotations` to `pendulastic_pt_score.py`**

Add this function anywhere after the module's existing imports (e.g. directly below the module docstring/imports, before `compute_pt_params`):

```python
def draw_pt_annotations(ax, params: dict, manual_release: bool = False) -> list | None:
    """Overlay clinical PT key-point markers on an angle-vs-time Axes.

    Draws a "Rest" line, release-point line, A0 amplitude bracket, a labeled
    dot on the first trough/peak (with phi_max ratio when available), faded
    dots on later peaks/troughs, and an "N cycles" badge -- driven entirely
    by the dict returned from compute_pt_params(). Returns None (drawing
    nothing) if params lacks enough data to annotate; otherwise returns the
    list of created artists for the caller to track/clear.
    """
    neutral        = params.get("neutral_deg")
    pre_release    = params.get("pre_release_deg")
    t_r            = params.get("t_r")
    ang_r          = params.get("ang_r")
    pk_i           = params.get("pk_i")
    tr_i           = params.get("tr_i")
    A0             = params.get("A0_deg")
    phi_ratio      = params.get("phi_max_ratio")
    N              = params.get("N")

    if neutral is None or t_r is None or len(t_r) < 2:
        return None

    artists: list = []

    # "Rest" annotation uses the pre-release held angle (not the settled tail)
    rest_angle = pre_release if pre_release is not None else neutral

    t0   = float(t_r[0])
    xlim = ax.get_xlim()
    ylim = ax.get_ylim()

    # ── pre-release rest line ────────────────────────────────────────────
    a = ax.axhline(rest_angle, color="#94A3B8", lw=1.0,
                    ls="--", alpha=0.8, zorder=2)
    artists.append(a)
    a = ax.text(
        xlim[0] + (xlim[1] - xlim[0]) * 0.01, rest_angle + 1.5,
        f"Rest  {rest_angle:.0f}°",
        color="#64748B", fontsize=7, va="bottom", ha="left",
        style="italic", zorder=3)
    artists.append(a)

    # ── release vertical line ────────────────────────────────────────────
    _rel_color = "#7C3AED" if manual_release else "#94A3B8"
    _rel_ls    = "-"       if manual_release else ":"
    _rel_lw    = 1.5       if manual_release else 1.0
    _rel_lbl   = "📍 release (manual)" if manual_release else "release"
    a = ax.axvline(t0, color=_rel_color, lw=_rel_lw,
                    ls=_rel_ls, alpha=0.85, zorder=2)
    artists.append(a)
    a = ax.text(t0 + 0.12, ylim[1] - 2,
                _rel_lbl, color=_rel_color,
                fontsize=7, va="top", ha="left", zorder=3)
    artists.append(a)

    # ── A₀: initial amplitude bracket (text left of release line) ────────
    if A0 is not None and A0 > 1:
        start_ang = neutral + A0
        bx = max(t0 - 0.25, xlim[0] + 0.1)
        a = ax.annotate(
            "", xy=(bx, neutral), xytext=(bx, start_ang),
            arrowprops=dict(arrowstyle="<->", color="#2563EB",
                            lw=1.0, mutation_scale=8))
        artists.append(a)
        a = ax.text(
            bx - 0.08, (neutral + start_ang) / 2,
            f"A₀\n{A0:.0f}°",
            color="#2563EB", fontsize=7, ha="right", va="center",
            fontweight="bold", zorder=3)
        artists.append(a)

    # ── first trough ─────────────────────────────────────────────────────
    if tr_i is not None and len(tr_i) > 0:
        ti = int(tr_i[0])
        if ti < len(t_r) and ti < len(ang_r):
            tx, ty = float(t_r[ti]), float(ang_r[ti])
            a = ax.plot(tx, ty, 'o',
                        color="#EA580C", ms=7, zorder=5,
                        markeredgecolor="#FFFFFF",
                        markeredgewidth=1)[0]
            artists.append(a)
            offset_y = -9 if ty - 9 > ylim[0] else 9
            va = "top" if offset_y < 0 else "bottom"
            a = ax.annotate(
                f"min  {ty:.0f}°",
                xy=(tx, ty), xytext=(tx + 0.25, ty + offset_y),
                color="#EA580C", fontsize=7, va=va, fontweight="bold",
                arrowprops=dict(arrowstyle="-", color="#EA580C",
                                lw=0.8), zorder=4)
            artists.append(a)

    # ── first return peak ─────────────────────────────────────────────────
    if pk_i is not None and len(pk_i) > 0:
        pi = int(pk_i[0])
        if pi < len(t_r) and pi < len(ang_r):
            px, py = float(t_r[pi]), float(ang_r[pi])
            a = ax.plot(px, py, 'o',
                        color="#16A34A", ms=7, zorder=5,
                        markeredgecolor="#FFFFFF",
                        markeredgewidth=1)[0]
            artists.append(a)
            lbl = f"ret  {py:.0f}°"
            if phi_ratio is not None:
                lbl = f"φmax={phi_ratio:.2f}  {py:.0f}°"
            a = ax.annotate(
                lbl,
                xy=(px, py), xytext=(px + 0.25, py + 7),
                color="#16A34A", fontsize=7, va="bottom",
                fontweight="bold",
                arrowprops=dict(arrowstyle="-", color="#16A34A",
                                lw=0.8), zorder=4)
            artists.append(a)

    # ── subsequent peaks (smaller, no labels) ────────────────────────────
    if pk_i is not None and len(pk_i) > 1:
        for _pi in list(pk_i[1:]):
            _pi = int(_pi)
            if _pi < len(t_r) and _pi < len(ang_r):
                a = ax.plot(float(t_r[_pi]), float(ang_r[_pi]),
                            'o', color="#16A34A", ms=4,
                            alpha=0.5, zorder=4)[0]
                artists.append(a)
    if tr_i is not None and len(tr_i) > 1:
        for _ti in list(tr_i[1:]):
            _ti = int(_ti)
            if _ti < len(t_r) and _ti < len(ang_r):
                a = ax.plot(float(t_r[_ti]), float(ang_r[_ti]),
                            'o', color="#EA580C", ms=4,
                            alpha=0.5, zorder=4)[0]
                artists.append(a)

    # ── N cycle count (top-right corner) ─────────────────────────────────
    if N is not None:
        a = ax.text(
            0.99, 0.97, f"N = {N:.0f} cycles",
            transform=ax.transAxes,
            color="#475569", fontsize=7.5, ha="right", va="top",
            fontweight="bold", zorder=3)
        artists.append(a)

    return artists
```

- [ ] **Step 2: Write failing tests for `draw_pt_annotations`**

Create/append to `tests/test_pt_score.py`:

```python
def test_draw_pt_annotations_returns_none_for_insufficient_data():
    from matplotlib.figure import Figure
    from pendulastic_pt_score import draw_pt_annotations

    fig = Figure()
    ax = fig.add_subplot(111)

    assert draw_pt_annotations(ax, {}) is None
    assert draw_pt_annotations(ax, {"neutral_deg": 170.0, "t_r": [0.0]}) is None


def test_draw_pt_annotations_returns_artists_for_valid_params():
    from matplotlib.figure import Figure
    from pendulastic_pt_score import draw_pt_annotations

    fig = Figure()
    ax = fig.add_subplot(111)
    ax.plot([0, 1, 2], [170, 150, 160])  # give the Axes real xlim/ylim

    t_r   = np.array([0.0, 0.1, 0.2, 0.3, 0.4])
    ang_r = np.array([170.0, 130.0, 145.0, 138.0, 142.0])
    params = {
        "neutral_deg": 175.0,
        "pre_release_deg": 178.0,
        "t_r": t_r,
        "ang_r": ang_r,
        "pk_i": np.array([2, 4]),
        "tr_i": np.array([1, 3]),
        "A0_deg": 8.0,
        "phi_max_ratio": 0.62,
        "N": 3.0,
    }

    artists = draw_pt_annotations(ax, params)
    assert artists is not None
    assert len(artists) > 0

    # Must not error when called again after ax.clear() -- matches the
    # PostProcessingPanel._plot_all_curves() clear-then-redraw pattern.
    ax.clear()
    artists2 = draw_pt_annotations(ax, params)
    assert artists2 is not None
    assert len(artists2) > 0


def test_draw_pt_annotations_manual_release_label():
    from matplotlib.figure import Figure
    from pendulastic_pt_score import draw_pt_annotations

    fig = Figure()
    ax = fig.add_subplot(111)
    ax.plot([0, 1], [170, 150])
    params = {"neutral_deg": 170.0, "t_r": np.array([0.0, 0.1]),
              "ang_r": np.array([170.0, 150.0])}

    artists = draw_pt_annotations(ax, params, manual_release=True)
    texts = [a.get_text() for a in artists if hasattr(a, "get_text")]
    assert any("manual" in t for t in texts)
```

- [ ] **Step 3: Run the new tests to verify they pass**

Run: `.venv\Scripts\pytest tests\test_pt_score.py -k draw_pt_annotations -v`
Expected: 3 passed (the function was written correct-by-construction from the viewer's existing, working code, so this should pass on first run — if it doesn't, compare against `pendulastic_viewer.py:4593-4739` line by line for a transcription error).

- [ ] **Step 4: Refactor `pendulastic_viewer.py` to call the shared function**

In `pendulastic_viewer.py:80-83`, change the import to also pull in the new function:

```python
    from pendulastic_pt_score import (compute_pt_params, compute_pt_score,
                                      compute_pt_score_simple, HEALTHY_REF, pt_to_mas,
                                      PT_HEALTHY_MAX, PT_BORDERLINE_MAX,
                                      load_optitrack, draw_pt_annotations)
```

Then replace the entire body of `_annotate_plot` (`pendulastic_viewer.py:4593-4739`, i.e. everything from `def _annotate_plot(self, params: dict):` through the closing `self._plot_canvas.draw_idle()` of that method) with:

```python
    def _annotate_plot(self, params: dict):
        """Overlay clinical key-point markers on the angle graph."""
        self._clear_annotations()

        artists = draw_pt_annotations(
            self._ax, params,
            manual_release=self._manual_release_fi is not None)
        if artists is None:
            self._plot_canvas.draw_idle()
            return
        self._plot_annots.extend(artists)

        # Redraw main trace: pre-release portion shown as flat 180° (leg extended)
        disp = self._display_angles()
        pairs = [(i / self.fps, a) for i, a in enumerate(disp) if math.isfinite(a)]
        if pairs:
            ts, angs = zip(*pairs)
            self._line_plot.set_data(ts, angs)

        self._ax.set_ylim(0, 185)   # guard after set_data: keep full 0–185° range
        self._ax.autoscale(enable=False, axis="y")
        self._plot_canvas.draw_idle()
```

This is behavior-identical to the original — same guard condition, same artists drawn (now via the shared function), same trace-redraw step.

- [ ] **Step 5: Smoke-test the viewer still imports cleanly**

Run: `.venv\Scripts\python.exe -c "import pendulastic_viewer"`
Expected: no exception (confirms the import change and the refactored method have no syntax/name errors). There is no existing automated test suite for `pendulastic_viewer.py`'s `_annotate_plot`, so this import smoke-test plus Step 3's coverage of the extracted logic is the verification for this task.

- [ ] **Step 6: Commit**

```bash
git add pendulastic_pt_score.py pendulastic_viewer.py tests/test_pt_score.py
git commit -m "feat: extract draw_pt_annotations into pendulastic_pt_score, share with viewer"
```

---

### Task 2: Add optional landmark collection to `BiomechanicalEngine.run_offline_track`

**Files:**
- Modify: `pendulastic_app.py:192-261`
- Test: `tests/test_biomechanical_engine.py`

**Interfaces:**
- Consumes: nothing new (uses existing `_PatientDetector`, `_MPBatchTracker`, `_VIEWER_AVAIL`, `_CV2_AVAIL`, `_cv2` module attributes already present in `pendulastic_app.py`).
- Produces: `BiomechanicalEngine.run_offline_track(video_path, progress_cb, leg="right", collect_landmarks=False) -> list | tuple[list, list]`. When `collect_landmarks=False` (default, unchanged), returns `angles: list[float]` exactly as before. When `True`, returns `(angles, landmarks)` where `landmarks[i]` is `(hip, knee, ankle)` (each a coordinate pair as returned by `tracker.step()`) or `None` if tracking wasn't available for frame `i`. `len(landmarks) == len(angles)` always.

- [ ] **Step 1: Write failing tests**

Append to `tests/test_biomechanical_engine.py`:

```python
@pytest.mark.skipif(not _CV2_OK, reason="cv2 not installed")
def test_run_offline_track_collect_landmarks_returns_tuple(tmp_path, monkeypatch):
    """collect_landmarks=True returns (angles, landmarks), same length,
    each landmark entry a 3-tuple or None."""
    import numpy as np

    video_path = str(tmp_path / "test2.avi")
    out = _cv2_test.VideoWriter(
        video_path, _cv2_test.VideoWriter_fourcc(*"XVID"),
        30.0, (320, 240))
    for _ in range(5):
        out.write(np.zeros((240, 320, 3), dtype=np.uint8))
    out.release()

    kps = np.zeros((17, 2), dtype=np.float32)
    kps[12] = [160, 60]
    kps[14] = [160, 120]
    kps[16] = [160, 200]

    class FakeDetector:
        def detect(self, frame):
            return kps, None

    class FakeTracker:
        def __init__(self, side, fps): pass
        def init(self, frame, hip, knee, ankle): pass
        def step(self, frame):
            return kps[12], kps[14], kps[16], 160.0

    monkeypatch.setattr(_app, "_PatientDetector", FakeDetector)
    monkeypatch.setattr(_app, "_MPBatchTracker",  FakeTracker)
    monkeypatch.setattr(_app, "_VIEWER_AVAIL", True)
    monkeypatch.setattr(_app, "_CV2_AVAIL", True)
    monkeypatch.setattr(_app, "_cv2", _cv2_test)

    engine = BiomechanicalEngine("rgb")
    angles, landmarks = engine.run_offline_track(
        video_path, lambda p: None, leg="right", collect_landmarks=True)

    assert len(angles) == 5
    assert len(landmarks) == 5
    for lm in landmarks:
        assert lm is None or len(lm) == 3


def test_run_offline_track_default_returns_list_not_tuple(monkeypatch):
    """collect_landmarks defaults to False -- return type must stay a plain
    list, matching every existing caller's expectation."""
    monkeypatch.setattr(_app, "_VIEWER_AVAIL", False)
    result = BiomechanicalEngine("rgb").run_offline_track(
        "nonexistent.mp4", lambda p: None, leg="right")
    assert result == []
    assert isinstance(result, list)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\pytest tests\test_biomechanical_engine.py -k collect_landmarks -v`
Expected: FAIL — `run_offline_track() got an unexpected keyword argument 'collect_landmarks'`.

- [ ] **Step 3: Implement `collect_landmarks`**

Replace `pendulastic_app.py:192-261` (the full `run_offline_track` method body, from `def run_offline_track(` through the final `return angles`) with:

```python
    def run_offline_track(
        self,
        video_path: str,
        progress_cb: Callable[[float], None],
        leg: str = "right",
        collect_landmarks: bool = False,
    ):
        """
        Offline MediaPipe tracking on a recorded video.
        Called on a background thread immediately after STOP (RGB methodology).

        Tracker API (from pendulastic_viewer.py):
          _PatientDetector().detect(frame) -> (patient_kps: ndarray(17,2) | None, _)
          _MPBatchTracker(side, fps).init(frame, hip, knee, ankle)
          tracker.step(frame) -> (hip, knee, ankle, angle_deg)

        COCO indices used: 11=L-hip, 12=R-hip, 13=L-knee, 14=R-knee,
                           15=L-ankle, 16=R-ankle

        When collect_landmarks is True, returns (angles, landmarks) where
        landmarks[i] is (hip, knee, ankle) for frame i, or None if pose
        tracking wasn't available for that frame -- len(landmarks) ==
        len(angles) always. When False (default), returns angles only,
        matching the original signature exactly.
        """
        if not (_VIEWER_AVAIL and _CV2_AVAIL):
            return ([], []) if collect_landmarks else []

        cap = _cv2.VideoCapture(video_path)
        if not cap.isOpened():
            return ([], []) if collect_landmarks else []

        fps_v  = cap.get(_cv2.CAP_PROP_FPS) or 30.0
        total  = int(cap.get(_cv2.CAP_PROP_FRAME_COUNT)) or 1

        # COCO column offsets: right leg offset=1, left leg offset=0
        col    = 1 if leg.lower() == "right" else 0
        hip_i  = 11 + col   # 12 (right) or 11 (left)
        knee_i = 13 + col   # 14 (right) or 13 (left)
        ank_i  = 15 + col   # 16 (right) or 15 (left)

        detector     = _PatientDetector()
        tracker      = _MPBatchTracker(leg.lower(), fps=fps_v)
        initialised  = False
        angles: list = []
        landmarks: list = []

        try:
            while True:
                ret, frame = cap.read()
                if not ret or frame is None:
                    break

                if not initialised:
                    patient_kps, _ = detector.detect(frame)
                    if patient_kps is not None and patient_kps.shape[0] >= 17:
                        hip   = patient_kps[hip_i].astype(float)
                        knee  = patient_kps[knee_i].astype(float)
                        ankle = patient_kps[ank_i].astype(float)
                        tracker.init(frame, hip, knee, ankle)
                        initialised = True

                if initialised:
                    try:
                        hip_p, knee_p, ank_p, angle = tracker.step(frame)
                        angles.append(float(angle) if angle is not None
                                      else float("nan"))
                        if collect_landmarks:
                            landmarks.append((hip_p, knee_p, ank_p))
                    except Exception:
                        angles.append(float("nan"))
                        if collect_landmarks:
                            landmarks.append(None)
                else:
                    angles.append(float("nan"))
                    if collect_landmarks:
                        landmarks.append(None)

                progress_cb(len(angles) / total)
        finally:
            cap.release()

        progress_cb(1.0)
        return (angles, landmarks) if collect_landmarks else angles
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\pytest tests\test_biomechanical_engine.py -v`
Expected: all pass, including the pre-existing `test_run_offline_track_returns_angle_per_frame` (confirms no regression for the default-argument case).

- [ ] **Step 5: Commit**

```bash
git add pendulastic_app.py tests/test_biomechanical_engine.py
git commit -m "feat: add optional landmark collection to run_offline_track"
```

---

### Task 3: Wire plot annotations into `PostProcessingPanel`

**Files:**
- Modify: `pendulastic_app.py:81-90` (import), `pendulastic_app.py:1079-1084` (`__init__`), `pendulastic_app.py:1201-1224` (`_plot_all_curves`), `pendulastic_app.py:1226-1262` (`_show_pt_metrics_from_sources`)
- Test: `tests/test_post_processing_panel.py`

**Interfaces:**
- Consumes: `draw_pt_annotations(ax, params, manual_release=False) -> list | None` from Task 1.
- Produces: `PostProcessingPanel._plot_annots: list`, `PostProcessingPanel._last_pt_params: dict | None` — both readable by Task 4/5 and by tests.

- [ ] **Step 1: Write failing tests**

Append to `tests/test_post_processing_panel.py` (needs `import math` already present at top of file):

```python
def test_load_trial_populates_plot_annotations():
    from pendulastic_app import PostProcessingPanel
    r = _get_root()
    p = PostProcessingPanel(r, _Ctrl())
    p.pack(fill="both", expand=True)
    angles = [180.0 - 40.0 * (1 - math.exp(-0.03 * i)) * (0.7 + 0.3 * math.sin(0.3 * i))
              for i in range(120)]
    meta = {"pid": "P1", "leg": "Right", "ms_status": "MS",
            "trial": 1, "sources": ["rgb"]}
    p.load_trial({"rgb": angles}, 30.0, meta,
                 "PID_P1_LEG_Right_MS_TRIAL_1.csv")
    r.update()
    assert p._last_pt_params is not None
    assert len(p._plot_annots) > 0


def test_plot_all_curves_resets_annotations_list():
    from pendulastic_app import PostProcessingPanel
    r = _get_root()
    p = PostProcessingPanel(r, _Ctrl())
    p.pack(fill="both", expand=True)
    angles = [180.0 - 40.0 * (1 - math.exp(-0.03 * i)) * (0.7 + 0.3 * math.sin(0.3 * i))
              for i in range(120)]
    meta = {"pid": "P1", "leg": "Right", "ms_status": "MS",
            "trial": 1, "sources": ["rgb"]}
    p.load_trial({"rgb": angles}, 30.0, meta,
                 "PID_P1_LEG_Right_MS_TRIAL_1.csv")
    r.update()
    first_annots = list(p._plot_annots)
    assert len(first_annots) > 0
    # Reloading clears + redraws; stale artist objects from the first pass
    # must not linger in the new list.
    p.load_trial({"rgb": angles}, 30.0, meta,
                 "PID_P1_LEG_Right_MS_TRIAL_1.csv")
    r.update()
    assert p._plot_annots is not first_annots
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\pytest tests\test_post_processing_panel.py -k plot_annotations -v`
Expected: FAIL with `AttributeError: 'PostProcessingPanel' object has no attribute '_plot_annots'`.

- [ ] **Step 3: Add the import, instance state, and wiring**

In `pendulastic_app.py:81-90`, add `draw_pt_annotations` to the import and its fallback:

```python
try:
    from pendulastic_pt_score import (
        compute_pt_params, compute_pt_score_simple, pt_to_mas,
        HEALTHY_REF, load_optitrack, draw_pt_annotations,
    )
    _PT_AVAIL = True
except Exception:
    compute_pt_params = compute_pt_score_simple = pt_to_mas = None
    HEALTHY_REF = load_optitrack = draw_pt_annotations = None
    _PT_AVAIL = False
```

In `PostProcessingPanel.__init__` (`pendulastic_app.py:1079-1084`), add two new attributes:

```python
    def __init__(self, parent, controller) -> None:
        super().__init__(parent)
        self.controller      = controller
        self._source_angles: dict  = {}
        self._fps: float           = 30.0
        self._meta: dict | None    = None
        self._plot_annots: list    = []
        self._last_pt_params: dict | None = None
        self._build_widgets()
```

In `_plot_all_curves` (`pendulastic_app.py:1201-1224`), reset the annotations list right after the axes clear (the clear already invalidated any old artist handles, so this is a plain reset, not a `.remove()` loop):

```python
    def _plot_all_curves(self) -> None:
        if not _MPL_AVAIL or self._canvas is None:
            return
        self._ax.clear()
        self._plot_annots = []
        n_curves = 0
```

(the rest of the method body is unchanged).

In `_show_pt_metrics_from_sources` (`pendulastic_app.py:1226-1262`), store the params dict and draw annotations right before the `return` that follows a successful score computation:

```python
            self.a1_var.set(f"{p['A1_deg']:.1f}")
            self.omega_var.set(f"{p['omega_peak_deg_s']:.1f}")
            self.n_var.set(f"{p['N']:.1f}")
            self.f_var.set(f"{p['f']:.2f}")
            self.r2n_var.set(f"{p['R2n']:.3f}")
            self.mas_var.set(str(mas))
            self.score_var.set(f"{score:.3f}")

            self._last_pt_params = p
            if self._canvas is not None and draw_pt_annotations is not None:
                artists = draw_pt_annotations(self._ax, p)
                if artists is not None:
                    self._plot_annots = artists
                    self._canvas.draw_idle()
            return
        self.status_var.set("PT scoring: no valid source data.")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\pytest tests\test_post_processing_panel.py -v`
Expected: all pass, including every pre-existing test in the file (confirms no regression — in particular `test_load_trial_imu_source_does_not_request_detrend`, which monkeypatches `compute_pt_params` to return `None` and must still pass since the new annotation code only runs on the non-`None` path).

- [ ] **Step 5: Commit**

```bash
git add pendulastic_app.py tests/test_post_processing_panel.py
git commit -m "feat: draw PT annotations on PostProcessingPanel's angle plot"
```

---

### Task 4: Capture HPE landmarks and add the disabled export button

**Files:**
- Modify: `pendulastic_app.py:64-70` (import — fallback values only, `_draw`/`TRAIL_LEN` land here but are unused until Task 5), `pendulastic_app.py:1079-1084` (`__init__`), `pendulastic_app.py:1089-1091` (columnconfigure), `pendulastic_app.py:1155-1159` (button row), `pendulastic_app.py:1264-1302` (`_on_upload_video` / `_add_hpe_overlay`)
- Test: `tests/test_post_processing_panel.py`

**Interfaces:**
- Consumes: `BiomechanicalEngine.run_offline_track(..., collect_landmarks=True) -> (angles, landmarks)` from Task 2.
- Produces: `PostProcessingPanel._video_path: str | None`, `PostProcessingPanel._hpe_leg: str`, `PostProcessingPanel._hpe_landmarks: list | None`, `PostProcessingPanel.btn_export_video: tk.Button` (starts `state="disabled"`, flips to `state="normal"` once both a video path and landmarks are present) — all consumed by Task 5.

- [ ] **Step 1: Write failing tests**

Append to `tests/test_post_processing_panel.py`:

```python
def test_export_video_button_exists_and_starts_disabled():
    from pendulastic_app import PostProcessingPanel
    r = _get_root()
    p = PostProcessingPanel(r, _Ctrl())
    p.pack(fill="both", expand=True); r.update()
    assert hasattr(p, "btn_export_video")
    assert str(p.btn_export_video["state"]) == "disabled"


def test_add_hpe_overlay_with_landmarks_enables_export_button():
    from pendulastic_app import PostProcessingPanel
    r = _get_root()
    p = PostProcessingPanel(r, _Ctrl())
    p.pack(fill="both", expand=True)
    p._video_path = "fake_video.mp4"
    fake_angles = [175.0, 160.0, 145.0] * 20
    fake_landmarks = [((160, 60), (160, 120), (160, 200))] * 60
    p._add_hpe_overlay(fake_angles, fake_landmarks, fps=30.0)
    r.update()
    assert p._hpe_landmarks == fake_landmarks
    assert str(p.btn_export_video["state"]) == "normal"


def test_add_hpe_overlay_without_landmarks_leaves_export_button_disabled():
    from pendulastic_app import PostProcessingPanel
    r = _get_root()
    p = PostProcessingPanel(r, _Ctrl())
    p.pack(fill="both", expand=True)
    fake_angles = [175.0, 160.0, 145.0] * 20
    p._add_hpe_overlay(fake_angles, fps=30.0)   # landmarks defaults to None
    r.update()
    assert str(p.btn_export_video["state"]) == "disabled"
```

Note these run alongside the pre-existing `test_add_hpe_overlay_adds_to_source_angles` and `test_add_hpe_overlay_empty_updates_status_not_crash`, both of which call `p._add_hpe_overlay(fake_angles, fps=30.0)` with no `landmarks` argument — the new signature must keep `landmarks` as an optional second positional/keyword parameter defaulting to `None` so those keep passing unmodified.

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\pytest tests\test_post_processing_panel.py -k export_video -v`
Expected: FAIL with `AttributeError: 'PostProcessingPanel' object has no attribute 'btn_export_video'`.

- [ ] **Step 3: Add the fallback constants, instance state, button, and wiring**

In `pendulastic_app.py:64-70`, add fallback values for `_draw`/`TRAIL_LEN` (the import itself is extended in Task 5, but the fallback branch needs these names defined now so the module always has them):

```python
try:
    from pendulastic_viewer import _MPBatchTracker, _PatientDetector
    _VIEWER_AVAIL = True
except Exception:
    _MPBatchTracker = None
    _PatientDetector = None
    _VIEWER_AVAIL = False
```

(leave this block as-is for now — Task 5 changes it to also import `_draw`/`TRAIL_LEN`; this task doesn't need them yet).

In `PostProcessingPanel.__init__` (`pendulastic_app.py:1079-1084`, as already extended by Task 3), add three more attributes:

```python
    def __init__(self, parent, controller) -> None:
        super().__init__(parent)
        self.controller      = controller
        self._source_angles: dict  = {}
        self._fps: float           = 30.0
        self._meta: dict | None    = None
        self._plot_annots: list    = []
        self._last_pt_params: dict | None = None
        self._video_path: str | None = None
        self._hpe_leg: str           = "right"
        self._hpe_landmarks: list | None = None
        self._build_widgets()
```

In `_build_widgets` (`pendulastic_app.py:1089-1091`), add a 4th grid column:

```python
        self.rowconfigure(1, weight=1)
        self.columnconfigure(0, weight=1)
        self.columnconfigure(1, weight=1)
        self.columnconfigure(2, weight=1)
        self.columnconfigure(3, weight=1)
```

In the row-3 button block (`pendulastic_app.py:1155-1159`), add the new button after `btn_upload_video`:

```python
        self.btn_upload_video = tk.Button(
            self, text="🎥 Upload Video for HPE",
            font=("Segoe UI", 10), width=22, height=2,
            command=self._on_upload_video)
        self.btn_upload_video.grid(row=3, column=2, padx=10, pady=12, sticky="w")
        self.btn_export_video = tk.Button(
            self, text="🎬 Export Annotated Video",
            font=("Segoe UI", 10), width=22, height=2, state="disabled",
            command=lambda: self._cmd_export_annotated_video())
        self.btn_export_video.grid(row=3, column=3, padx=10, pady=12, sticky="w")
```

Replace `_on_upload_video` and `_add_hpe_overlay` (`pendulastic_app.py:1264-1302`) with:

```python
    def _on_upload_video(self) -> None:
        if not _VIEWER_AVAIL:
            messagebox.showerror(
                "HPE Unavailable",
                "pendulastic_viewer not importable — cannot run MediaPipe.")
            return
        path = filedialog.askopenfilename(
            title="Select video for HPE",
            filetypes=[("Video", "*.mp4 *.avi *.mov *.mkv"),
                       ("All files", "*.*")])
        if not path:
            return
        self.status_var.set("HPE processing: 0%")
        leg    = self._meta.get("leg", "right") if self._meta else "right"
        self._video_path = path
        self._hpe_leg     = leg
        engine = BiomechanicalEngine("rgb")

        def _progress(pct: float) -> None:
            self.after(0, lambda p=pct: self.status_var.set(
                f"HPE processing: {int(p * 100)}%"))

        def _run() -> None:
            angles, landmarks = engine.run_offline_track(
                path, _progress, leg=leg.lower(), collect_landmarks=True)
            self.after(0, lambda: self._add_hpe_overlay(angles, landmarks, fps=30.0))

        threading.Thread(target=_run, daemon=True).start()

    def _add_hpe_overlay(self, angles: list, landmarks: list | None = None,
                          fps: float = 30.0) -> None:
        if not angles:
            self.status_var.set(
                "HPE: no pose detected — check video or leg selection.")
            return
        self._source_angles["hpe_upload"] = angles
        self._hpe_landmarks = landmarks
        if not self._fps:
            self._fps = fps
        if not self.title_var.get():
            self.title_var.set("HPE upload")
        self._plot_all_curves()
        self._show_pt_metrics_from_sources()
        self.status_var.set(f"HPE overlay loaded — {len(angles)} frames")
        if landmarks and self._video_path:
            self.btn_export_video.config(state="normal")
```

Note: `_cmd_export_annotated_video` is referenced by the new button's `command=` but is not defined until Task 5. `command=self._cmd_export_annotated_video` (a direct bound-method reference) would fail immediately at construction time with `AttributeError`, since attribute lookup happens when `_build_widgets` runs, not when the button is clicked — that's why the button above uses `command=lambda: self._cmd_export_annotated_video()` instead: the lambda defers the attribute lookup until the button is actually clicked. Since the button starts disabled and none of this task's tests click it, `PostProcessingPanel(...)` instantiates cleanly with `_cmd_export_annotated_video` still undefined, and Task 5 can add the method afterward without touching this button-construction code again.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\pytest tests\test_post_processing_panel.py -v`
Expected: all pass, including every pre-existing test (confirms `_add_hpe_overlay`'s new optional `landmarks` parameter doesn't break the two existing call sites that omit it).

- [ ] **Step 5: Commit**

```bash
git add pendulastic_app.py tests/test_post_processing_panel.py
git commit -m "feat: capture HPE landmarks and add disabled export-video button"
```

---

### Task 5: Implement the annotated video export worker

**Files:**
- Modify: `pendulastic_app.py:64-70` (import — add `_draw`, `TRAIL_LEN`), `pendulastic_app.py` (add `_cmd_export_annotated_video`, `_export_annotated_worker`, `_on_export_video_done` methods to `PostProcessingPanel`, placed after `_add_hpe_overlay`)
- Test: `tests/test_post_processing_panel.py`

**Interfaces:**
- Consumes: `PostProcessingPanel._video_path`, `_hpe_landmarks`, `_source_angles["hpe_upload"]`, `btn_export_video` from Task 4; `_draw(frame, hip, knee, ankle, angle, trail, scale) -> np.ndarray` and `TRAIL_LEN: int` imported from `pendulastic_viewer.py`.
- Produces: `PostProcessingPanel._cmd_export_annotated_video() -> None`, `_export_annotated_worker(snap: dict, out_path: str) -> None`, `_on_export_video_done(out_path: str) -> None`.

- [ ] **Step 1: Write a failing test**

Append to `tests/test_post_processing_panel.py` (needs `import pytest` added to the file's top-level imports if not already present, and `_cv2_test`/`_CV2_OK` following the same guard pattern as `tests/test_biomechanical_engine.py`):

```python
import pytest
try:
    import cv2 as _cv2_test
    _CV2_OK = True
except ImportError:
    _CV2_OK = False


@pytest.mark.skipif(not _CV2_OK, reason="cv2 not installed")
def test_export_annotated_worker_writes_video_file(tmp_path, monkeypatch):
    import pendulastic_app as _app
    from pendulastic_app import PostProcessingPanel
    import numpy as np

    video_path = str(tmp_path / "src.avi")
    out = _cv2_test.VideoWriter(
        video_path, _cv2_test.VideoWriter_fourcc(*"XVID"),
        30.0, (320, 240))
    for _ in range(5):
        out.write(np.zeros((240, 320, 3), dtype=np.uint8))
    out.release()

    r = _get_root()
    p = PostProcessingPanel(r, _Ctrl())
    p.pack(fill="both", expand=True)
    monkeypatch.setattr(_app.messagebox, "showinfo", lambda *a, **kw: None)
    monkeypatch.setattr(_app.messagebox, "showerror", lambda *a, **kw: None)

    hip, kne, ank = (160.0, 60.0), (160.0, 120.0), (160.0, 200.0)
    snap = {
        "path": video_path,
        "fps": 30.0,
        "angles": [150.0, 152.0, 148.0, 151.0, 149.0],
        "landmarks": [(hip, kne, ank)] * 5,
    }
    out_path = str(tmp_path / "src_annotated.avi")

    p._export_annotated_worker(snap, out_path)
    r.update()

    assert os.path.exists(out_path)
    check = _cv2_test.VideoCapture(out_path)
    frame_count = int(check.get(_cv2_test.CAP_PROP_FRAME_COUNT))
    check.release()
    assert frame_count == 5
    assert "saved" in p.status_var.get().lower()
```

(`os` is already imported at the top of `tests/test_post_processing_panel.py`.)

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv\Scripts\pytest tests\test_post_processing_panel.py -k export_annotated_worker -v`
Expected: FAIL with `AttributeError: 'PostProcessingPanel' object has no attribute '_export_annotated_worker'`.

- [ ] **Step 3: Import `_draw`/`TRAIL_LEN` and implement the three methods**

In `pendulastic_app.py:64-70`, extend the import:

```python
try:
    from pendulastic_viewer import _MPBatchTracker, _PatientDetector, _draw, TRAIL_LEN
    _VIEWER_AVAIL = True
except Exception:
    _MPBatchTracker = None
    _PatientDetector = None
    _draw = None
    TRAIL_LEN = 150
    _VIEWER_AVAIL = False
```

Add these three methods to `PostProcessingPanel`, directly after `_add_hpe_overlay`:

```python
    def _cmd_export_annotated_video(self) -> None:
        if not self._video_path or not self._hpe_landmarks:
            messagebox.showinfo(
                "Export Video",
                "Upload a video for HPE and let tracking finish first.")
            return
        angles = self._source_angles.get("hpe_upload")
        if not angles:
            messagebox.showinfo("Export Video", "No HPE angle data to export.")
            return

        base, _ = os.path.splitext(self._video_path)
        default_name = os.path.basename(base) + "_annotated.mp4"
        out_path = filedialog.asksaveasfilename(
            title="Save Annotated Video",
            initialfile=default_name,
            initialdir=os.path.dirname(self._video_path),
            defaultextension=".mp4",
            filetypes=[("MP4 Video", "*.mp4"), ("AVI Video", "*.avi"),
                       ("All files", "*.*")],
        )
        if not out_path:
            return

        snap = {
            "path":      self._video_path,
            "fps":       self._fps or 30.0,
            "angles":    list(angles),
            "landmarks": list(self._hpe_landmarks),
        }

        self.btn_export_video.config(state="disabled")
        self.status_var.set("Exporting annotated video… 0%")
        threading.Thread(target=self._export_annotated_worker,
                         args=(snap, out_path), daemon=True).start()

    def _export_annotated_worker(self, snap: dict, out_path: str) -> None:
        angles    = snap["angles"]
        landmarks = snap["landmarks"]
        fps       = snap["fps"]
        n_total   = len(angles)

        cap2 = _cv2.VideoCapture(snap["path"])
        if not cap2.isOpened():
            self.after(0, lambda: (
                self.btn_export_video.config(state="normal"),
                self.status_var.set("Export failed: cannot re-open video file."),
                messagebox.showerror("Export failed",
                                     f"Could not open video for reading:\n{snap['path']}")
            ))
            return
        w = int(cap2.get(_cv2.CAP_PROP_FRAME_WIDTH))
        h = int(cap2.get(_cv2.CAP_PROP_FRAME_HEIGHT))

        ext = os.path.splitext(out_path)[1].lower()
        if ext == ".avi":
            fourcc_candidates = [
                _cv2.VideoWriter_fourcc(*"XVID"),
                _cv2.VideoWriter_fourcc(*"MJPG"),
            ]
        else:
            fourcc_candidates = [
                _cv2.VideoWriter_fourcc(*"avc1"),   # H.264 — best quality on Windows
                _cv2.VideoWriter_fourcc(*"mp4v"),   # MPEG-4 fallback
                _cv2.VideoWriter_fourcc(*"XVID"),   # last resort
            ]

        writer = None
        for fc in fourcc_candidates:
            w_ = _cv2.VideoWriter(out_path, fc, fps, (w, h))
            if w_.isOpened():
                writer = w_
                break
            w_.release()

        if writer is None:
            cap2.release()
            self.after(0, lambda: (
                self.btn_export_video.config(state="normal"),
                self.status_var.set("Export failed: no usable video codec found."),
                messagebox.showerror("Export failed",
                                     "Could not find a working video codec.\n"
                                     "Try saving as .avi instead of .mp4.")
            ))
            return

        rolling_trail = []

        try:
            for fi in range(n_total):
                ok, frame = cap2.read()
                if not ok:
                    break

                ang = angles[fi] if fi < len(angles) else float("nan")
                lm  = landmarks[fi] if fi < len(landmarks) else None
                hip, kne, ank = lm if lm is not None else (None, None, None)

                if ank is not None:
                    rolling_trail.append(ank)
                    if len(rolling_trail) > TRAIL_LEN:
                        rolling_trail.pop(0)

                overlay = _draw(frame, hip, kne, ank, ang,
                                list(rolling_trail), scale=1.0)

                if math.isfinite(ang):
                    ang_txt = f"{ang:.1f} deg"
                    _cv2.putText(overlay, ang_txt, (16, h - 18),
                                _cv2.FONT_HERSHEY_DUPLEX, 1.1,
                                (0, 0, 0), 4, _cv2.LINE_AA)
                    _cv2.putText(overlay, ang_txt, (16, h - 18),
                                _cv2.FONT_HERSHEY_DUPLEX, 1.1,
                                (80, 230, 140), 2, _cv2.LINE_AA)

                t_txt = f"{fi / fps:.2f} s"
                _cv2.putText(overlay, t_txt, (16, h - 52),
                            _cv2.FONT_HERSHEY_SIMPLEX, 0.65,
                            (0, 0, 0), 3, _cv2.LINE_AA)
                _cv2.putText(overlay, t_txt, (16, h - 52),
                            _cv2.FONT_HERSHEY_SIMPLEX, 0.65,
                            (190, 190, 190), 1, _cv2.LINE_AA)

                writer.write(overlay)

                if fi % 30 == 0:
                    pct = int(fi / max(n_total, 1) * 100)
                    self.after(0, lambda p=pct: self.status_var.set(
                        f"Exporting annotated video… {p}%"))

        except Exception as exc:
            cap2.release()
            writer.release()
            self.after(0, lambda e=str(exc): (
                self.btn_export_video.config(state="normal"),
                self.status_var.set(f"Export error: {e}"),
                messagebox.showerror("Export error", f"An error occurred during export:\n{e}")
            ))
            return

        cap2.release()
        writer.release()
        self.after(0, lambda p=out_path: self._on_export_video_done(p))

    def _on_export_video_done(self, out_path: str) -> None:
        self.btn_export_video.config(state="normal")
        name = os.path.basename(out_path)
        self.status_var.set(f"Annotated video saved: {name}")
        messagebox.showinfo("Export complete",
                            f"Annotated video saved:\n{out_path}")
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `.venv\Scripts\pytest tests\test_post_processing_panel.py -k export_annotated_worker -v`
Expected: PASS.

- [ ] **Step 5: Run the full test suite for regressions**

Run: `.venv\Scripts\pytest tests\test_post_processing_panel.py tests\test_biomechanical_engine.py tests\test_pt_score.py -v`
Expected: all pass.

- [ ] **Step 6: Manual verification in the real app**

Run: `.venv\Scripts\python.exe pendulastic_app.py`
- Go to Upload/analysis mode, upload a video for HPE, wait for tracking to finish.
- Confirm the angle plot shows the Rest line / A₀ bracket / peak-trough markers / N badge — the same visual elements `pendulastic_viewer.py` shows for a comparable trial.
- Confirm "🎬 Export Annotated Video" is disabled before upload and enabled after a successful HPE run.
- Click it, save a file, and confirm the resulting video plays with a skeleton/angle-arc overlay and readable angle/time text burned in, comparable to a video exported from `pendulastic_viewer.py` for the same source clip.
- This end-to-end check is manual because it depends on real MediaPipe tracking output and real video playback — call this out explicitly rather than treating the automated tests above as full coverage of it (per the spec's Testing section).

- [ ] **Step 7: Commit**

```bash
git add pendulastic_app.py tests/test_post_processing_panel.py
git commit -m "feat: implement annotated video export in PostProcessingPanel"
```
