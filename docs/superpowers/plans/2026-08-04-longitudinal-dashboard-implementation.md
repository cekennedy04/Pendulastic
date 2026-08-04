# Longitudinal Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a researcher save trial-by-trial evaluation data per participant from the
Pendulastic Workbench, and render a 3-panel comparative figure (waveform overlay,
parameter bar chart, PT score trend) across saved sessions.

**Architecture:** Two new modules — `pendulastic_storage.py` (local JSON persistence
under `participants/{id}/history.json`) and `longitudinal_dashboard.py` (headless,
Tk-free matplotlib figure builder) — plus additive UI in `pendulastic_workbench.py`: a
save action on `WorkbenchView`, and a new `DashboardView` panel reached from
`TrialLoadPanel` via `App`'s existing panel-swap pattern.

**Tech Stack:** Python, Tkinter, matplotlib (`FigureCanvasTkAgg`), pytest.

## Global Constraints

- **Prerequisite dependency:** this entire feature depends on the PT-score panel work
  landing first — `WorkbenchView.get_metrics_snapshot()["per_trace"][label]` does not
  expose `pt_score`/`mas` until it lands. Task 1 below lands or verifies it. No task
  after Task 1 may be started until Task 1's own tests pass. **As of plan-writing time,
  that work is already complete on the unmerged branch `worktree-workbench-pt-score-panel`
  (commit `542e659`) — Task 1 merges it in, it does not re-implement it.**
- **`pt_score`/`mas` are `Optional`** — both are `None` together when
  `pendulastic_pt_score.compute_pt_params` reports insufficient signal for a trace.
  Every consumer of `pt_score` in this plan (Tasks 5, 7, 8) must handle `None`
  explicitly; treating it as always-a-float is a bug, not a simplification.
- Metric keys are exactly `workbench_engine.windowed_pt_params`'s 7 keys — `R2n`, `N`,
  `phi_max_ratio`, `omega_max_n`, `omega_min_n`, `f`, `area_ratio` — plus `pt_score`/
  `mas`. Never renamed (design spec §5). Note `pt_score`/`mas` are computed from a
  *separate* function (`pendulastic_pt_score.compute_pt_params`), not derived from
  these 7 keys — they are independent sibling keys in the same per-trace dict.
- Reference baselines and PT zone thresholds are imported from
  `pendulastic_pt_score.py` (`HEALTHY_REF`, `PT_HEALTHY_MAX`, `PT_BORDERLINE_MAX`),
  never redefined in the new modules.
- `pendulastic_storage.py` has zero Tkinter/matplotlib dependency — it is pure
  file I/O, importable and testable headlessly.
- `longitudinal_dashboard.py` has zero Tkinter dependency — `render_dashboard()`
  returns a `matplotlib.figure.Figure` without touching any Tk canvas, so its tests
  never need a display or `tk.Tk()` root.
- Atomic writes follow the existing `imu_calibration_config.py` precedent exactly:
  write to `<path>.tmp`, then `os.replace(tmp_path, path)`.
- `participants/` is added to `.gitignore` — history files hold real clinical data
  and must never be committed (same treatment as `models/`, `Recordings/`).
- This feature does not read or write `web/api/`'s `participant_db`/`trial_db` —
  that's a separate, unrelated app with its own in-memory store.
- Tk tests in `tests/test_pendulastic_workbench.py` reuse the file's existing
  `_get_root()` / `_Ctrl` / `_traces(*labels)` fixtures — no new fixtures needed.

---

### Task 1: Land the prerequisite PT-score panel work

**Files:**
- None created/modified directly by this task's own diff — it merges an existing,
  already-implemented, already-reviewed branch (`worktree-workbench-pt-score-panel`,
  tip commit `542e659`) into this feature's branch. That branch's own commits already
  touch `pendulastic_workbench.py` (imports; `get_metrics_snapshot`;
  `_recompute_metrics`) and `tests/test_pendulastic_workbench.py`.
- One conflict is expected and must be resolved by hand: both branches independently
  added `docs/superpowers/plans/2026-08-04-workbench-pt-score-panel.md` with different
  content (this feature branch has the plan's original text; the other branch has a
  version corrected after its own review — commit message "docs: correct PT-score
  panel plan after final review found the semantic mismatch"). **Keep the other
  branch's version** (`git checkout --theirs` for that one path) — it's the
  post-review-corrected text, the original is stale.

**Interfaces:**
- Produces: `WorkbenchView.get_metrics_snapshot()["per_trace"][label]` gains
  `"pt_score": Optional[float]` and `"mas": Optional[str]` keys (both `None` together
  when `pendulastic_pt_score.compute_pt_params` reports insufficient signal),
  alongside the existing 7 raw `windowed_pt_params` keys. Every later task in this plan
  reads `pt_score`/`mas` from that snapshot and must handle the `None` case.

- [ ] **Step 1: Merge the branch**

Run: `git merge worktree-workbench-pt-score-panel`

Expected: a conflict on exactly one file,
`docs/superpowers/plans/2026-08-04-workbench-pt-score-panel.md`. If any *other* file
conflicts, stop and report — that means this feature branch and the PT-score branch
have diverged in a way this plan didn't anticipate.

- [ ] **Step 2: Resolve the expected conflict**

Run: `git checkout --theirs "docs/superpowers/plans/2026-08-04-workbench-pt-score-panel.md" && git add "docs/superpowers/plans/2026-08-04-workbench-pt-score-panel.md" && git commit --no-edit`

- [ ] **Step 3: Verify the full prerequisite-relevant test suite passes**

Run: `python -m pytest tests/test_pendulastic_workbench.py tests/test_workbench_engine.py tests/test_pt_score.py -v`
Expected: PASS (all tests).

- [ ] **Step 4: Confirm the exact interface this plan depends on, including the `None` case**

Run: `python -c "
import tkinter as tk, numpy as np
from pendulastic_workbench import WorkbenchView
class _Ctrl:
    def get_trial_meta(self): return {}
r = tk.Tk(); r.withdraw()
wv = WorkbenchView(r, _Ctrl())
t = np.linspace(0, 4, 400)
wv.set_traces({'imu': (t, 140 + 20*np.cos(t)), 'flat': (t, np.full_like(t, 140.0))})
r.update()
snap = wv.get_metrics_snapshot()
pt = snap['per_trace']['imu']
assert isinstance(pt['pt_score'], float) and isinstance(pt['mas'], str)
flat = snap['per_trace']['flat']
assert (flat['pt_score'] is None) == (flat['mas'] is None)
print('OK:', sorted(pt.keys()), 'flat pt_score:', flat['pt_score'])
r.destroy()
"`
Expected: prints `OK: [...9 keys...] flat pt_score: None` (a flat/degenerate signal
has insufficient signal for `compute_pt_params`) without raising. If this fails, do
not proceed to Task 2 — the prerequisite is not actually landed correctly.

---

### Task 2: `pendulastic_storage.py` — participant ID normalization + defensive `load_history`

**Files:**
- Create: `pendulastic_storage.py`
- Modify: `.gitignore`
- Test: `tests/test_pendulastic_storage.py` (new)

**Interfaces:**
- Produces: `normalize_participant_id(participant_id: str) -> str`,
  `load_history(participant_id: str) -> dict`. Both are pure functions with no Tk/
  matplotlib dependency, importable from any test.
- Consumes: nothing from earlier tasks (this is the first code task).

- [ ] **Step 1: Add `participants/` to `.gitignore`**

Open `.gitignore` and add, under the existing "Generated acquisition output" section:

```
# Longitudinal dashboard participant history (real clinical data; never committed)
participants/
```

- [ ] **Step 2: Write the failing tests for normalization and defensive loading**

Create `tests/test_pendulastic_storage.py`:

```python
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest

import pendulastic_storage as storage


@pytest.fixture(autouse=True)
def _isolated_participants_dir(tmp_path, monkeypatch):
    """Every test gets its own empty participants/ directory so tests never
    read/write real data or interfere with each other."""
    monkeypatch.setattr(storage, "PARTICIPANTS_DIR", str(tmp_path / "participants"))
    yield


def test_normalize_participant_id_strips_and_uppercases():
    assert storage.normalize_participant_id(" p5 ") == "P5"
    assert storage.normalize_participant_id("p5") == "P5"
    assert storage.normalize_participant_id("P5") == "P5"


def test_load_history_missing_file_returns_empty_skeleton():
    history = storage.load_history("P5")
    assert history["participant_id"] == "P5"
    assert history["legs"]["left"]["sessions"] == []
    assert history["legs"]["right"]["sessions"] == []
    assert "_skipped" not in history


def test_load_history_corrupt_json_returns_empty_skeleton():
    path = os.path.join(storage.PARTICIPANTS_DIR, "P5")
    os.makedirs(path, exist_ok=True)
    with open(os.path.join(path, "history.json"), "w", encoding="utf-8") as f:
        f.write("{not valid json")

    history = storage.load_history("P5")
    assert history["legs"]["left"]["sessions"] == []


def test_load_history_skips_malformed_session_and_reports_it():
    path = os.path.join(storage.PARTICIPANTS_DIR, "P5")
    os.makedirs(path, exist_ok=True)
    good_session = {
        "label": "Initial", "date": "2026-07-07", "reference_trace": "imu",
        "traces": {"imu": {"t": [0.0], "angle": [140.0],
                           "metrics": {"pt_score": 0.1, "mas": "0"}}},
    }
    bad_session = {"label": "Broken", "date": "not-a-date"}
    raw = {
        "participant_id": "P5",
        "legs": {"left": {"sessions": [good_session, bad_session]},
                 "right": {"sessions": []}},
    }
    with open(os.path.join(path, "history.json"), "w", encoding="utf-8") as f:
        json.dump(raw, f)

    history = storage.load_history("P5")
    sessions = history["legs"]["left"]["sessions"]
    assert len(sessions) == 1
    assert sessions[0]["label"] == "Initial"
    assert len(history["_skipped"]) == 1
    assert "Broken" not in history["_skipped"][0] or "date" in history["_skipped"][0]
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `python -m pytest tests/test_pendulastic_storage.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'pendulastic_storage'`

- [ ] **Step 4: Implement `pendulastic_storage.py`**

Create `pendulastic_storage.py`:

```python
"""
pendulastic_storage.py
=======================
Local, per-participant persistence for the Workbench's longitudinal
dashboard: participants/{id}/history.json. Purely local -- no relation to
web/api's separate in-memory participant_db/trial_db (a different app).

See docs/superpowers/specs/2026-08-04-longitudinal-dashboard-design.md.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime

logger = logging.getLogger(__name__)

PARTICIPANTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "participants")


def normalize_participant_id(participant_id: str) -> str:
    """" p5 ", "p5", and "P5" must all resolve to the same
    participants/P5/history.json, or a typo'd case/whitespace variant
    silently creates a duplicate participant folder."""
    return participant_id.strip().upper()


def _history_path(participant_id: str) -> str:
    return os.path.join(PARTICIPANTS_DIR, normalize_participant_id(participant_id),
                        "history.json")


def _empty_history(participant_id: str) -> dict:
    return {
        "participant_id": normalize_participant_id(participant_id),
        "legs": {"left": {"sessions": []}, "right": {"sessions": []}},
    }


def _session_skip_reason(session) -> str:
    """Returns "" if session is well-formed, else the reason it's being
    skipped."""
    if not isinstance(session, dict):
        return "not a dict"
    for key in ("label", "date", "reference_trace", "traces"):
        if key not in session:
            return f"missing '{key}'"
    try:
        datetime.fromisoformat(session["date"])
    except (ValueError, TypeError):
        return f"unparseable date {session.get('date')!r}"
    return ""


def load_history(participant_id: str) -> dict:
    """Defensive read: a missing file, corrupt JSON, missing keys, or a
    malformed session never raises -- each problem is either defaulted or
    the offending session is skipped and reported in "_skipped", so a
    shorter-than-expected history reads as "corrupted", not "this trial
    was never recorded" (design spec Section 5)."""
    pid = normalize_participant_id(participant_id)
    try:
        with open(_history_path(pid), "r", encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, ValueError):
        return _empty_history(pid)

    if not isinstance(raw, dict):
        return _empty_history(pid)

    history = _empty_history(pid)
    skipped = []
    legs = raw.get("legs")
    if isinstance(legs, dict):
        for leg in ("left", "right"):
            leg_data = legs.get(leg)
            if not isinstance(leg_data, dict):
                continue
            sessions = leg_data.get("sessions")
            if not isinstance(sessions, list):
                continue
            kept = []
            for session in sessions:
                reason = _session_skip_reason(session)
                if reason:
                    msg = f"skipped malformed session for {pid}/{leg}: {reason}"
                    logger.warning(msg)
                    skipped.append(msg)
                else:
                    kept.append(session)
            history["legs"][leg]["sessions"] = kept
    if skipped:
        history["_skipped"] = skipped
    return history
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_pendulastic_storage.py -v`
Expected: PASS (all 4 tests)

- [ ] **Step 6: Commit**

```bash
git add pendulastic_storage.py tests/test_pendulastic_storage.py .gitignore
git commit -m "feat: add pendulastic_storage with normalized, defensive history loading"
```

---

### Task 3: `pendulastic_storage.py` — `save_trial` (validation, atomic write, upsert)

**Files:**
- Modify: `pendulastic_storage.py`
- Test: `tests/test_pendulastic_storage.py`

**Interfaces:**
- Consumes: `normalize_participant_id`, `load_history`, `_empty_history` (Task 2, same
  module).
- Produces: `save_trial(participant_id: str, leg: str, session_label: str, date: str, traces: dict, metrics_by_label: dict, reference_trace: str) -> None`.
  `traces`: `{label: (t: Sequence[float], angle: Sequence[float])}`.
  `metrics_by_label`: `{label: dict}` — each value is one
  `get_metrics_snapshot()["per_trace"][label]` dict (7 param keys + `pt_score`/`mas`).
  Raises `ValueError` for a bad `leg` or a `date` not matching `YYYY-MM-DD`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_pendulastic_storage.py`:

```python
def _traces_and_metrics():
    traces = {"imu": ([0.0, 0.1, 0.2], [140.0, 138.0, 135.0])}
    metrics = {"imu": {"R2n": 0.95, "N": 6.0, "phi_max_ratio": 0.79,
                       "omega_max_n": 7.17, "omega_min_n": 0.01, "f": 1.0,
                       "area_ratio": 0.13, "pt_score": 0.115, "mas": "0"}}
    return traces, metrics


def test_save_trial_round_trip():
    traces, metrics = _traces_and_metrics()
    storage.save_trial("p5", "left", "Initial", "2026-07-07", traces, metrics, "imu")

    history = storage.load_history("P5")
    sessions = history["legs"]["left"]["sessions"]
    assert len(sessions) == 1
    session = sessions[0]
    assert session["label"] == "Initial"
    assert session["date"] == "2026-07-07"
    assert session["reference_trace"] == "imu"
    assert session["traces"]["imu"]["angle"] == [140.0, 138.0, 135.0]
    assert session["traces"]["imu"]["metrics"]["pt_score"] == 0.115


def test_save_trial_rejects_bad_date():
    traces, metrics = _traces_and_metrics()
    with pytest.raises(ValueError):
        storage.save_trial("P5", "left", "Initial", "07/07/2026", traces, metrics, "imu")


def test_save_trial_rejects_bad_leg():
    traces, metrics = _traces_and_metrics()
    with pytest.raises(ValueError):
        storage.save_trial("P5", "middle", "Initial", "2026-07-07", traces, metrics, "imu")


def test_save_trial_does_not_clobber_other_leg_or_date():
    traces, metrics = _traces_and_metrics()
    storage.save_trial("P5", "left", "Initial", "2026-07-07", traces, metrics, "imu")
    storage.save_trial("P5", "right", "Initial", "2026-07-07", traces, metrics, "imu")
    storage.save_trial("P5", "left", "Post-Training", "2026-07-17", traces, metrics, "imu")

    history = storage.load_history("P5")
    assert len(history["legs"]["left"]["sessions"]) == 2
    assert len(history["legs"]["right"]["sessions"]) == 1
    left_labels = {s["label"] for s in history["legs"]["left"]["sessions"]}
    assert left_labels == {"Initial", "Post-Training"}


def test_save_trial_upserts_matching_label_and_date():
    traces, metrics = _traces_and_metrics()
    storage.save_trial("P5", "left", "Initial", "2026-07-07", traces, metrics, "imu")

    traces2, metrics2 = _traces_and_metrics()
    metrics2["imu"]["pt_score"] = 0.5   # reprocessed with a different result
    storage.save_trial("P5", "left", "Initial", "2026-07-07", traces2, metrics2, "imu")

    history = storage.load_history("P5")
    sessions = history["legs"]["left"]["sessions"]
    assert len(sessions) == 1   # replaced, not duplicated
    assert sessions[0]["traces"]["imu"]["metrics"]["pt_score"] == 0.5
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_pendulastic_storage.py -k save_trial -v`
Expected: FAIL with `AttributeError: module 'pendulastic_storage' has no attribute 'save_trial'`

- [ ] **Step 3: Implement `save_trial`**

Add to `pendulastic_storage.py` (after `load_history`):

```python
def save_trial(participant_id: str, leg: str, session_label: str, date: str,
              traces: dict, metrics_by_label: dict, reference_trace: str) -> None:
    """Upserts one session into participants/{id}/history.json under
    legs[leg]["sessions"]. Matches on (session_label, date): a session
    already present with that exact label+date is replaced in place
    (guards against a researcher re-saving the same trial after
    reprocessing, or double-clicking Save); any other label/date
    combination is appended. Writes atomically (temp file + os.replace,
    matching imu_calibration_config.save_config's precedent) so a crash
    mid-write can't corrupt the file. Never touches other legs' or other
    (label, date) sessions."""
    if leg not in ("left", "right"):
        raise ValueError(f"leg must be 'left' or 'right', got {leg!r}")
    try:
        datetime.strptime(date, "%Y-%m-%d")
    except ValueError as e:
        raise ValueError(f"date must be YYYY-MM-DD, got {date!r}") from e

    pid = normalize_participant_id(participant_id)
    history = load_history(pid)
    history.pop("_skipped", None)

    session = {
        "label": session_label,
        "date": date,
        "reference_trace": reference_trace,
        "traces": {
            label: {
                "t": list(t),
                "angle": list(angle),
                "metrics": metrics_by_label[label],
            }
            for label, (t, angle) in traces.items()
        },
    }

    sessions = history["legs"][leg]["sessions"]
    for i, existing in enumerate(sessions):
        if existing["label"] == session_label and existing["date"] == date:
            sessions[i] = session
            break
    else:
        sessions.append(session)

    dir_path = os.path.join(PARTICIPANTS_DIR, pid)
    os.makedirs(dir_path, exist_ok=True)
    path = os.path.join(dir_path, "history.json")
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(history, f, indent=2)
    os.replace(tmp_path, path)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_pendulastic_storage.py -v`
Expected: PASS (all 9 tests)

- [ ] **Step 5: Commit**

```bash
git add pendulastic_storage.py tests/test_pendulastic_storage.py
git commit -m "feat: add pendulastic_storage.save_trial with validation, atomic write, and upsert"
```

---

### Task 4: `pendulastic_storage.py` — `list_participant_ids`

**Files:**
- Modify: `pendulastic_storage.py`
- Test: `tests/test_pendulastic_storage.py`

**Interfaces:**
- Consumes: `PARTICIPANTS_DIR` (Task 2).
- Produces: `list_participant_ids() -> list[str]`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_pendulastic_storage.py`:

```python
def test_list_participant_ids_normalizes_and_sorts():
    traces, metrics = _traces_and_metrics()
    storage.save_trial("p9", "left", "Initial", "2026-07-07", traces, metrics, "imu")
    storage.save_trial(" P2 ", "left", "Initial", "2026-07-07", traces, metrics, "imu")

    assert storage.list_participant_ids() == ["P2", "P9"]


def test_list_participant_ids_empty_dir_returns_empty_list():
    assert storage.list_participant_ids() == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_pendulastic_storage.py -k list_participant_ids -v`
Expected: FAIL with `AttributeError: module 'pendulastic_storage' has no attribute 'list_participant_ids'`

- [ ] **Step 3: Implement `list_participant_ids`**

Add to `pendulastic_storage.py` (at the end of the file):

```python
def list_participant_ids() -> list[str]:
    """Scans participants/*/history.json. Returned IDs are already
    normalized (they're the directory names save_trial created)."""
    if not os.path.isdir(PARTICIPANTS_DIR):
        return []
    ids = []
    for name in sorted(os.listdir(PARTICIPANTS_DIR)):
        if os.path.isfile(os.path.join(PARTICIPANTS_DIR, name, "history.json")):
            ids.append(name)
    return ids
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_pendulastic_storage.py -v`
Expected: PASS (all 11 tests)

- [ ] **Step 5: Commit**

```bash
git add pendulastic_storage.py tests/test_pendulastic_storage.py
git commit -m "feat: add pendulastic_storage.list_participant_ids"
```

---

### Task 5: `longitudinal_dashboard.py` — session sorting/filtering + waveform overlay panel

**Files:**
- Create: `longitudinal_dashboard.py`
- Test: `tests/test_longitudinal_dashboard.py` (new)

**Interfaces:**
- Consumes: a `pendulastic_storage.load_history(...)`-shaped dict (Task 2/3) — tests
  build this dict literally, no import of `pendulastic_storage` needed.
  `pendulastic_pt_score.HEALTHY_REF`, `PT_HEALTHY_MAX`, `PT_BORDERLINE_MAX` (existing).
- Produces: `render_dashboard(history: dict, leg: str, trace_label: str) -> matplotlib.figure.Figure` with exactly 3 axes (`fig.axes[0]` = waveform, `[1]` = bar chart,
  `[2]` = PT trend — later tasks fill in `[1]` and `[2]`'s content, not their
  existence). Private helper `_sorted_sessions_with_trace(history, leg, trace_label) -> list[dict]`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_longitudinal_dashboard.py`:

```python
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import longitudinal_dashboard as dash


def _session(label, date, pt_score=0.1, trace_label="imu", extra_metrics=None):
    metrics = {"R2n": 0.9, "N": 6.0, "phi_max_ratio": 0.8, "omega_max_n": 7.0,
              "omega_min_n": 0.01, "f": 1.0, "area_ratio": 0.1, "pt_score": pt_score,
              "mas": "0"}
    if extra_metrics is not None:
        metrics = extra_metrics
    return {
        "label": label, "date": date, "reference_trace": trace_label,
        "traces": {trace_label: {"t": [0.0, 0.1, 0.2], "angle": [140.0, 138.0, 135.0],
                                 "metrics": metrics}},
    }


def _history(sessions, leg="left"):
    return {"participant_id": "P5",
           "legs": {"left": {"sessions": []}, "right": {"sessions": []},
                    **{leg: {"sessions": sessions}}}}


def test_sorted_sessions_with_trace_filters_and_sorts_by_date():
    s_later = _session("Follow-up", "2026-08-01")
    s_earlier = _session("Initial", "2026-07-07")
    s_missing_trace = _session("Other", "2026-07-20", trace_label="optitrack")
    history = _history([s_later, s_earlier, s_missing_trace])

    result = dash._sorted_sessions_with_trace(history, "left", "imu")

    assert [s["label"] for s in result] == ["Initial", "Follow-up"]


def test_render_dashboard_returns_figure_with_three_axes():
    history = _history([_session("Initial", "2026-07-07")])
    fig = dash.render_dashboard(history, "left", "imu")
    assert len(fig.axes) == 3


def test_render_dashboard_waveform_legend_has_pt_scores():
    history = _history([_session("Initial", "2026-07-07", pt_score=0.115)])
    fig = dash.render_dashboard(history, "left", "imu")
    legend = fig.axes[0].get_legend()
    assert legend is not None
    assert "PT=0.115" in legend.get_texts()[0].get_text()


def test_render_dashboard_waveform_legend_shows_na_for_none_pt_score():
    """pt_score/mas are None together when compute_pt_params reports
    insufficient signal (design spec Section 2) -- the legend must render
    "PT=n/a", not crash trying to format None with :.3f."""
    history = _history([_session("Initial", "2026-07-07", pt_score=None)])
    fig = dash.render_dashboard(history, "left", "imu")
    legend = fig.axes[0].get_legend()
    assert "PT=n/a" in legend.get_texts()[0].get_text()


def test_render_dashboard_empty_history_does_not_raise():
    history = _history([])
    fig = dash.render_dashboard(history, "left", "imu")
    assert len(fig.axes) == 3
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_longitudinal_dashboard.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'longitudinal_dashboard'`

- [ ] **Step 3: Implement `longitudinal_dashboard.py` (sorting helper + skeleton + waveform panel)**

Create `longitudinal_dashboard.py`:

```python
"""
longitudinal_dashboard.py
==========================
Renders the 3-panel longitudinal comparison figure (waveform overlay,
parameter bar chart, PT score trend) for one participant/leg from a
pendulastic_storage.load_history() history dict. Zero Tkinter dependency
-- callers embed the returned Figure however they like.

See docs/superpowers/specs/2026-08-04-longitudinal-dashboard-design.md.
"""
from __future__ import annotations

from datetime import datetime

from matplotlib.figure import Figure

from pendulastic_pt_score import HEALTHY_REF, PT_HEALTHY_MAX, PT_BORDERLINE_MAX

PARAM_KEYS = ["R2n", "N", "phi_max_ratio", "omega_max_n", "omega_min_n", "f", "area_ratio"]


def _sorted_sessions_with_trace(history: dict, leg: str, trace_label: str) -> list:
    """Sessions for `leg` that have `trace_label`, sorted by date -- never
    by JSON insertion/append order, so a backfilled earlier-dated session
    saved after a later one still renders in the correct chronological
    position (design spec Section 6). A session whose date can't be
    parsed is excluded; load_history() already flagged that via
    "_skipped", this is not a second place to raise about it."""
    sessions = history.get("legs", {}).get(leg, {}).get("sessions", [])
    dated = []
    for session in sessions:
        if trace_label not in session.get("traces", {}):
            continue
        try:
            d = datetime.fromisoformat(session["date"])
        except (ValueError, TypeError, KeyError):
            continue
        dated.append((d, session))
    dated.sort(key=lambda pair: pair[0])
    return [session for _d, session in dated]


def render_dashboard(history: dict, leg: str, trace_label: str) -> Figure:
    sessions = _sorted_sessions_with_trace(history, leg, trace_label)

    fig = Figure(figsize=(9, 11), dpi=100)
    ax_wave = fig.add_subplot(3, 1, 1)
    ax_bar = fig.add_subplot(3, 1, 2)
    ax_trend = fig.add_subplot(3, 1, 3)

    _render_waveform_overlay(ax_wave, sessions, trace_label)
    _render_parameter_bars(ax_bar, sessions, trace_label)
    _render_pt_trend(ax_trend, sessions, trace_label)

    fig.tight_layout()
    return fig


def _render_waveform_overlay(ax, sessions: list, trace_label: str) -> None:
    ax.set_xlabel("Time since release (s)")
    ax.set_ylabel("Knee angle (deg)")
    ax.set_title("Waveform overlay")
    for session in sessions:
        trace = session["traces"][trace_label]
        t = trace["t"]
        angle = trace["angle"]
        t0 = t[0] if t else 0.0
        t_aligned = [ti - t0 for ti in t]
        pt_score = trace["metrics"]["pt_score"]
        pt_str = f"{pt_score:.3f}" if pt_score is not None else "n/a"
        ax.plot(t_aligned, angle, label=f"{session['label']} (PT={pt_str})")
    if sessions:
        ax.legend(fontsize=8)


def _render_parameter_bars(ax, sessions: list, trace_label: str) -> None:
    """Placeholder body -- filled in by Task 6."""
    ax.set_ylabel("Parameter value")
    ax.set_title("Parameter comparison vs healthy reference")


def _render_pt_trend(ax, sessions: list, trace_label: str) -> None:
    """Placeholder body -- filled in by Task 7."""
    ax.set_xlabel("Session")
    ax.set_ylabel("PT score")
    ax.set_title("Longitudinal PT score trend")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_longitudinal_dashboard.py -v`
Expected: PASS (all 5 tests)

- [ ] **Step 5: Commit**

```bash
git add longitudinal_dashboard.py tests/test_longitudinal_dashboard.py
git commit -m "feat: add longitudinal_dashboard with chronological sorting and waveform overlay panel"
```

---

### Task 6: `longitudinal_dashboard.py` — parameter bar chart (strict single-trace filtering)

**Files:**
- Modify: `longitudinal_dashboard.py`
- Test: `tests/test_longitudinal_dashboard.py`

**Interfaces:**
- Consumes: `PARAM_KEYS`, `HEALTHY_REF` (Task 5, same module/import).
- Produces: `_render_parameter_bars(ax, sessions, trace_label)` now draws real bars
  (was a title-only placeholder from Task 5).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_longitudinal_dashboard.py`:

```python
def test_render_dashboard_bar_chart_covers_all_params():
    history = _history([_session("Initial", "2026-07-07")])
    fig = dash.render_dashboard(history, "left", "imu")
    ax_bar = fig.axes[1]
    xtick_labels = [t.get_text() for t in ax_bar.get_xticklabels()]
    assert xtick_labels == dash.PARAM_KEYS


def test_render_dashboard_bar_chart_drops_session_missing_a_param():
    """Strict single-trace filtering (design spec Section 6): a session
    whose selected-trace metrics are missing one of the 7 params must be
    dropped from the bar chart entirely, never partially rendered or
    backfilled from another trace in that same session."""
    complete = _session("Initial", "2026-07-07")
    incomplete_metrics = {"R2n": 0.9, "N": 6.0, "pt_score": 0.1, "mas": "0"}
    incomplete = _session("Post-Training", "2026-07-17", extra_metrics=incomplete_metrics)
    history = _history([complete, incomplete])

    fig = dash.render_dashboard(history, "left", "imu")
    ax_bar = fig.axes[1]
    legend = ax_bar.get_legend()
    labels = [t.get_text() for t in legend.get_texts()]
    assert labels == ["Initial"]


def test_render_dashboard_bar_chart_empty_sessions_does_not_raise():
    history = _history([])
    fig = dash.render_dashboard(history, "left", "imu")
    assert fig.axes[1].get_legend() is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_longitudinal_dashboard.py -k bar_chart -v`
Expected: FAIL — `test_render_dashboard_bar_chart_covers_all_params` fails because
`ax_bar.get_xticklabels()` is empty (placeholder body sets no ticks); the "drops
session" test fails because both sessions are silently included.

- [ ] **Step 3: Implement `_render_parameter_bars`**

Replace the placeholder `_render_parameter_bars` in `longitudinal_dashboard.py`:

```python
def _render_parameter_bars(ax, sessions: list, trace_label: str) -> None:
    ax.set_ylabel("Parameter value")
    ax.set_title("Parameter comparison vs healthy reference")

    # Strict single-trace filtering: every bar in a session's group comes
    # from traces[trace_label]["metrics"] only -- never from another trace
    # present in the same session, even as a fallback for a missing param.
    # A session missing any of the 7 params is dropped whole, not
    # partially filled, so a grouped bar never mixes readings from two
    # different sensors (design spec Section 6).
    usable = [s for s in sessions
             if all(key in s["traces"][trace_label]["metrics"] for key in PARAM_KEYS)]
    if not usable:
        return

    n_sessions = len(usable)
    n_params = len(PARAM_KEYS)
    width = 0.8 / n_sessions
    x = list(range(n_params))

    for i, session in enumerate(usable):
        metrics = session["traces"][trace_label]["metrics"]
        values = [metrics[key] for key in PARAM_KEYS]
        offsets = [xi + i * width for xi in x]
        ax.bar(offsets, values, width=width, label=session["label"])

    for i, key in enumerate(PARAM_KEYS):
        ax.hlines(HEALTHY_REF[key], i - 0.1, i + n_sessions * width, colors="black",
                  linestyles="dashed", linewidth=1)

    ax.set_xticks([xi + (n_sessions * width) / 2 for xi in x])
    ax.set_xticklabels(PARAM_KEYS, rotation=30, ha="right")
    ax.legend(fontsize=8)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_longitudinal_dashboard.py -v`
Expected: PASS (all 8 tests)

- [ ] **Step 5: Commit**

```bash
git add longitudinal_dashboard.py tests/test_longitudinal_dashboard.py
git commit -m "feat: render parameter bar chart with strict single-trace filtering"
```

---

### Task 7: `longitudinal_dashboard.py` — PT score trend (zone bands + delta annotations)

**Files:**
- Modify: `longitudinal_dashboard.py`
- Test: `tests/test_longitudinal_dashboard.py`

**Interfaces:**
- Consumes: `PT_HEALTHY_MAX`, `PT_BORDERLINE_MAX` (Task 5 import).
- Produces: `_render_pt_trend(ax, sessions, trace_label)` now draws the trend line,
  zone bands, and delta annotations (was a title-only placeholder).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_longitudinal_dashboard.py`:

```python
def test_render_dashboard_pt_trend_has_three_zone_bands():
    history = _history([_session("Initial", "2026-07-07")])
    fig = dash.render_dashboard(history, "left", "imu")
    ax_trend = fig.axes[2]
    # axhspan draws a PolyCollection per band.
    from matplotlib.collections import PolyCollection
    bands = [c for c in ax_trend.collections if isinstance(c, PolyCollection)]
    assert len(bands) == 3


def test_render_dashboard_pt_trend_annotates_delta_between_sessions():
    s1 = _session("Initial", "2026-07-07", pt_score=0.100)
    s2 = _session("Post-Training", "2026-07-17", pt_score=0.150)
    history = _history([s1, s2])
    fig = dash.render_dashboard(history, "left", "imu")
    ax_trend = fig.axes[2]
    texts = [a.get_text() for a in ax_trend.texts]
    assert any("+50%" in t for t in texts)


def test_render_dashboard_pt_trend_single_session_no_delta_and_no_raise():
    history = _history([_session("Initial", "2026-07-07")])
    fig = dash.render_dashboard(history, "left", "imu")
    ax_trend = fig.axes[2]
    assert ax_trend.texts == ()  or list(ax_trend.texts) == []


def test_render_dashboard_pt_trend_excludes_none_pt_score_session():
    """A session whose selected trace has pt_score=None (insufficient
    signal, design spec Section 2) must be excluded from the trend line
    and from Delta% calculations against its neighbors -- as if it lacked
    trace_label entirely, for trend purposes only. It may still appear in
    the waveform overlay (Task 5) and the bar chart (Task 6)."""
    s1 = _session("Initial", "2026-07-07", pt_score=0.100)
    s2 = _session("Mid", "2026-07-12", pt_score=None)
    s3 = _session("Post-Training", "2026-07-17", pt_score=0.150)
    history = _history([s1, s2, s3])
    fig = dash.render_dashboard(history, "left", "imu")
    ax_trend = fig.axes[2]

    line = ax_trend.lines[0]
    assert list(line.get_ydata()) == [0.100, 0.150]
    texts = [a.get_text() for a in ax_trend.texts]
    assert any("+50%" in t for t in texts)   # delta computed across Initial -> Post-Training directly


def test_render_dashboard_pt_trend_all_none_does_not_raise():
    history = _history([_session("Initial", "2026-07-07", pt_score=None)])
    fig = dash.render_dashboard(history, "left", "imu")
    ax_trend = fig.axes[2]
    assert len(ax_trend.lines) == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_longitudinal_dashboard.py -k pt_trend -v`
Expected: FAIL — zero bands found, no `+50%` annotation present, `None` in `scores`
crashes `max(scores)`/`ax.plot(xs, scores)` on the two new tests.

- [ ] **Step 3: Implement `_render_pt_trend`**

Replace the placeholder `_render_pt_trend` in `longitudinal_dashboard.py`:

```python
def _render_pt_trend(ax, sessions: list, trace_label: str) -> None:
    ax.set_xlabel("Session")
    ax.set_ylabel("PT score")
    ax.set_title("Longitudinal PT score trend")

    # A session whose selected trace has pt_score=None (insufficient
    # signal) is excluded from the trend line and from Delta% against its
    # neighbors -- as if it lacked trace_label entirely, for trend
    # purposes only (design spec Section 2/6). It's still eligible for
    # the waveform overlay and bar chart, which don't filter on this.
    usable = [s for s in sessions
             if s["traces"][trace_label]["metrics"]["pt_score"] is not None]

    if not usable:
        ax.axhspan(0, PT_HEALTHY_MAX, color="#22C55E", alpha=0.15)
        ax.axhspan(PT_HEALTHY_MAX, PT_BORDERLINE_MAX, color="#EAB308", alpha=0.15)
        ax.axhspan(PT_BORDERLINE_MAX, PT_BORDERLINE_MAX * 1.5, color="#EF4444", alpha=0.15)
        return

    scores = [s["traces"][trace_label]["metrics"]["pt_score"] for s in usable]
    ylim_max = max(PT_BORDERLINE_MAX * 1.5, max(scores) * 1.2)
    ax.axhspan(0, PT_HEALTHY_MAX, color="#22C55E", alpha=0.15)
    ax.axhspan(PT_HEALTHY_MAX, PT_BORDERLINE_MAX, color="#EAB308", alpha=0.15)
    ax.axhspan(PT_BORDERLINE_MAX, ylim_max, color="#EF4444", alpha=0.15)
    ax.set_ylim(0, ylim_max)

    labels = [s["label"] for s in usable]
    xs = list(range(len(usable)))
    ax.plot(xs, scores, marker="o", color="#1D4ED8")
    ax.set_xticks(xs)
    ax.set_xticklabels(labels, rotation=30, ha="right")

    for i in range(1, len(scores)):
        prev, curr = scores[i - 1], scores[i]
        if prev == 0:
            continue
        pct = (curr - prev) / prev * 100.0
        ax.annotate(f"{pct:+.0f}%", xy=(i, curr), xytext=(0, 8),
                   textcoords="offset points", ha="center", fontsize=8)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_longitudinal_dashboard.py -v`
Expected: PASS (all 12 tests)

- [ ] **Step 5: Commit**

```bash
git add longitudinal_dashboard.py tests/test_longitudinal_dashboard.py
git commit -m "feat: render PT score trend with clinical zone bands and delta annotations"
```

---

### Task 8: `WorkbenchView` — "Save Trial to Dashboard" action

**Files:**
- Modify: `pendulastic_workbench.py` (imports near line 29; `WorkbenchView` — new method
  after `_on_export_clicked`, new button in `_build_widgets`'s `annot_toolbar`)
- Test: `tests/test_pendulastic_workbench.py`

**Note on line numbers:** Task 1 merges in a branch this plan doesn't control the exact
diff of, so exact post-merge line numbers in `pendulastic_workbench.py` aren't knowable
ahead of time. Every insertion point below is given as an exact code snippet to search
for — locate the real position by matching the snippet, not by counting lines.

**Interfaces:**
- Consumes: `pendulastic_storage.save_trial` (Task 3), `self.get_metrics_snapshot()`
  (existing, now includes `pt_score: Optional[float]`/`mas: Optional[str]` per Task 1),
  `self._traces`, `self._visible_vars`, `self._reference_var` (existing `WorkbenchView`
  state).
- Produces: `WorkbenchView._save_current_trial(participant_id, leg, session_label, date) -> None` — the testable core of the save action, factored out from the dialog so
  tests don't need to simulate `Toplevel` widgets. `WorkbenchView._reference_trace_pt_score() -> Optional[float]` — the current reference trace's `pt_score`, or `None`
  if there's no reference trace or its `pt_score` is `None`; the dialog's Save button
  refuses to proceed when this returns `None`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_pendulastic_workbench.py` (near the other `WorkbenchView` tests):

```python
def test_save_current_trial_persists_only_visible_traces():
    import pendulastic_storage
    from pendulastic_workbench import WorkbenchView
    r = _get_root()
    wv = WorkbenchView(r, _Ctrl())
    wv.set_traces(_traces("imu", "optitrack"))
    wv._visible_vars["optitrack"].set(False)   # hide optitrack
    r.update()

    wv._save_current_trial("test-p1", "left", "Initial", "2026-07-07")

    history = pendulastic_storage.load_history("test-p1")
    sessions = history["legs"]["left"]["sessions"]
    assert len(sessions) == 1
    saved_traces = sessions[0]["traces"]
    assert set(saved_traces.keys()) == {"imu"}
    assert "pt_score" in saved_traces["imu"]["metrics"]


def test_reference_trace_pt_score_returns_none_for_insufficient_signal():
    """A session without a usable PT score for its reference trace isn't
    useful to a longitudinal PT-score dashboard (design spec Section 7) --
    the save dialog's Save button refuses to proceed when this returns
    None. Tested directly against the helper rather than by simulating
    the Toplevel dialog's widgets."""
    from pendulastic_workbench import WorkbenchView
    r = _get_root()
    wv = WorkbenchView(r, _Ctrl())
    t = np.linspace(0, 4, 400)
    wv.set_traces({"flat": (t, np.full_like(t, 140.0))})   # flat signal -> insufficient
    r.update()
    assert wv._reference_var.get() == "flat"

    assert wv._reference_trace_pt_score() is None


def test_reference_trace_pt_score_returns_float_for_valid_signal():
    from pendulastic_workbench import WorkbenchView
    r = _get_root()
    wv = WorkbenchView(r, _Ctrl())
    wv.set_traces(_traces("imu"))
    r.update()

    assert isinstance(wv._reference_trace_pt_score(), float)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_pendulastic_workbench.py -k "save_current_trial or reference_trace_pt_score" -v`
Expected: FAIL with `AttributeError: 'WorkbenchView' object has no attribute '_save_current_trial'` (and, for the two new tests, `has no attribute '_reference_trace_pt_score'`)

- [ ] **Step 3: Add the `pendulastic_storage` import**

In `pendulastic_workbench.py`, find `import workbench_engine as engine` (near the top,
after the `matplotlib`/`FigureCanvasTkAgg`/`Figure` imports) and add directly below it:

```python
import pendulastic_storage
```

- [ ] **Step 4: Add `_save_current_trial` and the dialog method**

Find `_on_export_clicked` in `pendulastic_workbench.py` (it ends with
`messagebox.showinfo("Export complete", f"Session exported to {out_path}")`) and insert
the following two methods directly after it, before `def load_video`:

```python
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
```

- [ ] **Step 5: Add the button**

In `_build_widgets`, find the `annot_toolbar` block ending with:

```python
        tk.Button(annot_toolbar, text="Export Session...",
                 command=self._on_export_clicked).pack(side="right", padx=6)
```

and insert directly after it:

```python
        tk.Button(annot_toolbar, text="Save Trial to Dashboard",
                 command=self._on_save_trial_clicked).pack(side="right", padx=6)
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `python -m pytest tests/test_pendulastic_workbench.py -k "save_current_trial or reference_trace_pt_score" -v`
Expected: PASS (all 3 tests)

- [ ] **Step 7: Run the full Workbench test suite to confirm no regressions**

Run: `python -m pytest tests/test_pendulastic_workbench.py -v`
Expected: PASS (all tests)

- [ ] **Step 8: Commit**

```bash
git add pendulastic_workbench.py tests/test_pendulastic_workbench.py
git commit -m "feat: add Save Trial to Dashboard action to WorkbenchView"
```

---

### Task 9: `DashboardView` — participant/leg/trace picker + figure rendering

**Files:**
- Modify: `pendulastic_workbench.py` (imports; new `DashboardView` class, placed after
  `WorkbenchView` and before `class App`)
- Test: `tests/test_pendulastic_workbench.py`

**Interfaces:**
- Consumes: `pendulastic_storage.list_participant_ids`, `load_history` (Tasks 2–4);
  `longitudinal_dashboard.render_dashboard` (Tasks 5–7); `FigureCanvasTkAgg` (already
  imported for `WorkbenchView`).
- Produces: `DashboardView(parent, controller)` — a `tk.Frame` with
  `refresh_participants()` (repopulates the participant dropdown from disk) and an
  internal `_on_load_clicked()` that renders the 3-panel figure and surfaces a
  skipped-session status line. `controller` must implement `on_dashboard_back()`
  (wired in Task 10).

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_pendulastic_workbench.py`:

```python
def test_dashboard_view_load_renders_three_axes_figure():
    import pendulastic_storage
    from pendulastic_workbench import DashboardView
    traces = {"imu": ([0.0, 0.1], [140.0, 138.0])}
    metrics = {"imu": {"R2n": 0.9, "N": 6.0, "phi_max_ratio": 0.8, "omega_max_n": 7.0,
                       "omega_min_n": 0.01, "f": 1.0, "area_ratio": 0.1,
                       "pt_score": 0.1, "mas": "0"}}
    pendulastic_storage.save_trial("test-dv1", "left", "Initial", "2026-07-07",
                                   traces, metrics, "imu")

    r = _get_root()
    dv = DashboardView(r, _Ctrl())
    dv.refresh_participants()
    dv._participant_var.set("TEST-DV1")
    dv._leg_var.set("left")
    dv._on_load_clicked()
    r.update()

    assert dv._trace_var.get() == "imu"
    assert dv._canvas is not None
    assert len(dv._canvas.figure.axes) == 3


def test_dashboard_view_shows_skipped_session_status():
    import json
    import pendulastic_storage
    from pendulastic_workbench import DashboardView

    path = os.path.join(pendulastic_storage.PARTICIPANTS_DIR, "TEST-DV2")
    os.makedirs(path, exist_ok=True)
    raw = {"participant_id": "TEST-DV2",
          "legs": {"left": {"sessions": [{"label": "Broken", "date": "bad-date"}]},
                   "right": {"sessions": []}}}
    with open(os.path.join(path, "history.json"), "w", encoding="utf-8") as f:
        json.dump(raw, f)

    r = _get_root()
    dv = DashboardView(r, _Ctrl())
    dv._participant_var.set("TEST-DV2")
    dv._leg_var.set("left")
    dv._on_load_clicked()
    r.update()

    assert "Skipped 1" in dv._status_var.get()
```

Add `import os` to the top of `tests/test_pendulastic_workbench.py` if not already
present (check the existing `import os, sys` line first).

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_pendulastic_workbench.py -k dashboard_view -v`
Expected: FAIL with `ImportError: cannot import name 'DashboardView'`

- [ ] **Step 3: Add the `longitudinal_dashboard` import**

In `pendulastic_workbench.py`, directly below the `import pendulastic_storage` line
added in Task 8, add:

```python
import longitudinal_dashboard
```

- [ ] **Step 4: Implement `DashboardView`**

In `pendulastic_workbench.py`, find `class App(tk.Tk):` and insert the new class
directly before it (after the end of `WorkbenchView`):

```python
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
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_pendulastic_workbench.py -k dashboard_view -v`
Expected: PASS (both tests)

- [ ] **Step 6: Commit**

```bash
git add pendulastic_workbench.py tests/test_pendulastic_workbench.py
git commit -m "feat: add DashboardView with participant/leg/trace picker and figure rendering"
```

---

### Task 10: Wire `DashboardView` into `App`'s panel-swap navigation

**Files:**
- Modify: `pendulastic_workbench.py` (`TrialLoadPanel._build_widgets`; `App.__init__`;
  new `App.on_view_dashboard`/`App.on_dashboard_back`)
- Test: `tests/test_pendulastic_workbench.py`

**Note on line numbers:** same caveat as Task 8 — locate by snippet, not by the line
numbers below.

**Interfaces:**
- Consumes: `DashboardView` (Task 9).
- Produces: `App.on_view_dashboard() -> None`, `App.on_dashboard_back() -> None`. The
  existing `TrialLoadPanel.controller.on_back_to_mode_select`/
  `WorkbenchView.controller.on_workbench_load_another` pattern is unchanged.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_pendulastic_workbench.py`:

```python
def test_load_panel_view_dashboard_button_switches_to_dashboard_view():
    from pendulastic_workbench import App
    app = App()
    try:
        app.update()
        app.on_view_dashboard()
        app.update()
        assert app._dashboard_view.winfo_ismapped()
        assert not app._load_panel.winfo_ismapped()
    finally:
        app.destroy()


def test_dashboard_back_returns_to_load_panel():
    from pendulastic_workbench import App
    app = App()
    try:
        app.update()
        app.on_view_dashboard()
        app.update()
        app.on_dashboard_back()
        app.update()
        assert app._load_panel.winfo_ismapped()
        assert not app._dashboard_view.winfo_ismapped()
    finally:
        app.destroy()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_pendulastic_workbench.py -k "view_dashboard or dashboard_back" -v`
Expected: FAIL with `AttributeError: 'App' object has no attribute 'on_view_dashboard'`

- [ ] **Step 3: Instantiate `DashboardView` in `App.__init__`**

Find, in `App.__init__`:

```python
        self._load_panel = TrialLoadPanel(self, controller=self)
        self._workbench_view = WorkbenchView(self, controller=self)
        self._load_panel.pack(fill="both", expand=True)
```

and change it to:

```python
        self._load_panel = TrialLoadPanel(self, controller=self)
        self._workbench_view = WorkbenchView(self, controller=self)
        self._dashboard_view = DashboardView(self, controller=self)
        self._load_panel.pack(fill="both", expand=True)
```

- [ ] **Step 4: Add `on_view_dashboard`/`on_dashboard_back`**

Find `on_workbench_load_another`:

```python
    def on_workbench_load_another(self) -> None:
        self._workbench_view.pack_forget()
        self._load_panel.pack(fill="both", expand=True)
```

and insert directly after it:

```python
    def on_view_dashboard(self) -> None:
        self._load_panel.pack_forget()
        self._workbench_view.pack_forget()
        self._dashboard_view.refresh_participants()
        self._dashboard_view.pack(fill="both", expand=True)

    def on_dashboard_back(self) -> None:
        self._dashboard_view.pack_forget()
        self._load_panel.pack(fill="both", expand=True)
```

- [ ] **Step 5: Add the "View Participant Dashboard" button to `TrialLoadPanel`**

In `TrialLoadPanel._build_widgets`, find:

```python
        tk.Button(self, text="Load Trial", command=self._on_load_clicked
                 ).grid(row=8, column=0, columnspan=3, pady=16)
```

and insert directly after it:

```python
        tk.Button(self, text="View Participant Dashboard",
                 command=lambda: self.controller.on_view_dashboard()
                 ).grid(row=9, column=0, columnspan=3, pady=(0, 16))
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `python -m pytest tests/test_pendulastic_workbench.py -k "view_dashboard or dashboard_back" -v`
Expected: PASS (both tests)

- [ ] **Step 7: Run the full test suite to confirm no regressions**

Run: `python -m pytest tests/test_pendulastic_workbench.py tests/test_workbench_engine.py tests/test_pt_score.py tests/test_pendulastic_storage.py tests/test_longitudinal_dashboard.py -v`
Expected: PASS (all tests across all five files)

- [ ] **Step 8: Commit**

```bash
git add pendulastic_workbench.py tests/test_pendulastic_workbench.py
git commit -m "feat: wire DashboardView into App panel-swap navigation"
```

---

## Self-Review Notes

- **Spec coverage:** §2 prerequisite → Task 1. §5 schema/normalize/upsert/defensive
  load → Tasks 2–4. §6 sorting/skip/strict-filtering/3 panels → Tasks 5–7. §7 save
  dialog/DashboardView/navigation → Tasks 8–10. `.gitignore` → Task 2 Step 1.
- **Placeholder scan:** none in final state — Task 5's `_render_parameter_bars`/
  `_render_pt_trend` bodies are explicitly documented as "filled in by Task N" and are
  in fact completed by Tasks 6–7 later in this same plan, not left unfinished at the
  plan's end.
- **Type consistency:** `save_trial`'s `traces`/`metrics_by_label` parameter shapes
  match what `WorkbenchView._save_current_trial` (Task 8) constructs from
  `self._traces`/`get_metrics_snapshot()`. `render_dashboard`'s `history` parameter
  shape matches exactly what `pendulastic_storage.load_history` (Tasks 2–4) returns,
  including the optional `"_skipped"` key that `DashboardView._on_load_clicked` (Task 9)
  reads.
- **Line-number risk:** Tasks 8 and 10 modify `pendulastic_workbench.py` regions whose
  exact post-merge line numbers Task 1 doesn't fix in advance (it merges an existing
  branch rather than applying a known diff); every insertion point in those tasks is
  specified as an exact code snippet to locate, not a bare line number.
- **`pt_score: Optional[float]` coverage:** every reader of `pt_score` handles `None`
  explicitly — waveform legend (Task 5: `"PT=n/a"`), PT trend panel (Task 7: session
  excluded from the line and Δ% calculations), and the save dialog (Task 8:
  `_reference_trace_pt_score()` blocks the save outright when the *reference* trace's
  score is `None`; non-reference visible traces may still persist a `None` `pt_score`
  unblocked, per design spec §2/§7). The bar chart (Task 6) never reads `pt_score` at
  all, so it needs no such handling.
