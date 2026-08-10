# Per-Trace Release-Start Alignment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a researcher mark each loaded trace's own release moment by clicking directly on its curve, auto-seed that mark from the existing (currently unwired) `detect_release_t0()`, and use the marks to auto-fill the existing per-trace `lag(s)` override that already drives RMSE/MAE/PT-score comparison — fixing the delay between OptiTrack's and the phone IMU's release marks.

**Architecture:** A new dedicated `_release_marks`/`_lag_provenance` store on `WorkbenchView` (separate from the existing global-milestone `_annotations` dict), populated by clicking a trace's own plotted curve (`event.xdata` in that trace's native time units — the only mechanism that is actually correct across traces with independent clocks) or a numeric fallback entry, auto-seeded per trace via a small correctness fix to `pendulastic_pt_score.detect_release_t0`. A new `workbench_engine.release_lag_sec()` computes the offset between two marks; `_recompute_release_lags()` writes it into the existing `lag(s)` `Entry` per trace, tracking whether that field is auto-derived or hand-typed so a researcher's manual tuning is never silently overwritten, and clearing (not leaving stale) any auto-derived value that no longer has a valid basis.

**Tech Stack:** Python, Tkinter, matplotlib (`FigureCanvasTkAgg`), numpy. No new dependencies.

## Global Constraints

- `_detect_release`'s threshold algorithm (`0.08 * signal_range`, baseline computation) is never modified — see spec Section 4 and Section 2 (other callers: `compute_pt_params`, `pt_report_common.py`, `validate_controls.py` depend on its current behavior).
- No changes to trace time arrays, on-disk CSVs, or PT-score windowing — only the existing `lag(s)` override's *value* gets populated differently.
- Every new piece of state introduced by this feature (`_release_marks`, `_lag_provenance`) must reset on a genuinely new trial load (`App.on_load_trial()`) but survive the async-HPE-results re-call to `set_traces()` for the *same* trial — exactly matching the existing precedent for `_visible_vars`/`_lag_override_vars`.
- Follow this repo's existing test conventions exactly: shared `_get_root()` Tk singleton + `_Ctrl` stub controller + `_traces()` helper in `tests/test_pendulastic_workbench.py`; plain pure-function tests with no Tk in `tests/test_workbench_engine.py` and `tests/test_pt_score.py`.

---

## Task 1: `workbench_engine.release_lag_sec` — pure lag computation

**Files:**
- Modify: `workbench_engine.py` (add function near `compare_pair`, e.g. after line 155's end of `compare_pair`)
- Test: `tests/test_workbench_engine.py` (append)

**Interfaces:**
- Produces: `release_lag_sec(ref_t: float, test_t: float) -> float` — `ref_t - test_t`, raises `ValueError` if either input is non-finite.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_workbench_engine.py`:

```python
def test_release_lag_sec_returns_ref_minus_test():
    assert engine.release_lag_sec(2.5, 1.0) == pytest.approx(1.5)
    assert engine.release_lag_sec(1.0, 2.5) == pytest.approx(-1.5)


def test_release_lag_sec_rejects_non_finite_input():
    with pytest.raises(ValueError):
        engine.release_lag_sec(float("nan"), 1.0)
    with pytest.raises(ValueError):
        engine.release_lag_sec(1.0, float("inf"))
```

(Check the top of the file already has `import pytest` and `import workbench_engine as engine` — if the module is imported under a different alias, e.g. plain `import workbench_engine`, match whatever's already there instead of introducing a second alias.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_workbench_engine.py -k release_lag_sec -v`
Expected: FAIL with `AttributeError: module 'workbench_engine' has no attribute 'release_lag_sec'`

- [ ] **Step 3: Write minimal implementation**

Add to `workbench_engine.py`, after `compare_pair`'s closing (find the end of that function — it returns the RMSE/MAE dict):

```python
def release_lag_sec(ref_t: float, test_t: float) -> float:
    """Time offset to shift a test trace by so its release aligns with the
    reference trace's release: shifted_test_t = test_t + release_lag_sec(...)
    matches compare_pair()'s lag_override_sec convention directly."""
    ref_t = float(ref_t)
    test_t = float(test_t)
    if not (math.isfinite(ref_t) and math.isfinite(test_t)):
        raise ValueError("ref_t and test_t must both be finite.")
    return ref_t - test_t
```

`math` is already imported at the top of `workbench_engine.py` (line 15).

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_workbench_engine.py -k release_lag_sec -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add workbench_engine.py tests/test_workbench_engine.py
git commit -m "feat: add release_lag_sec pure lag computation to workbench_engine"
```

---

## Task 2: `workbench_engine.release_marks_to_csv_rows` — pure export formatter

**Files:**
- Modify: `workbench_engine.py` (add function after `annotations_to_csv_rows`, currently the file's last function, ending ~line 802)
- Test: `tests/test_workbench_engine.py` (append)

**Interfaces:**
- Consumes: nothing from Task 1.
- Produces: `release_marks_to_csv_rows(release_marks: dict, participant_id: str, session_date: str) -> tuple[list[str], list[dict]]` where `release_marks` has shape `{label: {"t_trace": float, "source": "auto"|"manual"}}` (the shape `WorkbenchView._release_marks`/`get_release_marks()` will use starting in Task 4).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_workbench_engine.py`:

```python
def test_release_marks_to_csv_rows_empty_returns_no_rows():
    fieldnames, rows = engine.release_marks_to_csv_rows({}, "P5", "2026-08-04")
    assert rows == []
    assert fieldnames == ["participant_id", "session_date", "label", "t_trace", "source"]


def test_release_marks_to_csv_rows_one_row_per_trace():
    release_marks = {
        "imu": {"t_trace": 1.23, "source": "manual"},
        "optitrack": {"t_trace": 0.98, "source": "auto"},
    }
    fieldnames, rows = engine.release_marks_to_csv_rows(release_marks, "P5", "2026-08-04")
    assert len(rows) == 2
    row = next(r for r in rows if r["label"] == "imu")
    assert row == {
        "participant_id": "P5", "session_date": "2026-08-04",
        "label": "imu", "t_trace": 1.23, "source": "manual",
    }
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_workbench_engine.py -k release_marks_to_csv_rows -v`
Expected: FAIL with `AttributeError`

- [ ] **Step 3: Write minimal implementation**

Add to `workbench_engine.py`, after `annotations_to_csv_rows`:

```python
def release_marks_to_csv_rows(release_marks: dict, participant_id: str,
                              session_date: str) -> tuple:
    """One row per per-trace release mark from WorkbenchView.get_release_marks()."""
    fieldnames = ["participant_id", "session_date", "label", "t_trace", "source"]
    rows = []
    for label, mark in release_marks.items():
        rows.append({
            "participant_id": participant_id,
            "session_date": session_date,
            "label": label,
            "t_trace": float(mark["t_trace"]),
            "source": mark["source"],
        })
    return fieldnames, rows
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_workbench_engine.py -k release_marks_to_csv_rows -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add workbench_engine.py tests/test_workbench_engine.py
git commit -m "feat: add release_marks_to_csv_rows export formatter to workbench_engine"
```

---

## Task 3: `pendulastic_pt_score.detect_release_t0` — input validation + real "no release" signal

**Files:**
- Modify: `pendulastic_pt_score.py:724-740` (`detect_release_t0`)
- Test: `tests/test_pt_score.py` (append)

**Interfaces:**
- Produces: `detect_release_t0(t, signal, baseline_sec=0.6) -> float` — same signature, but now also raises `ValueError` on mismatched-length/non-monotonic `t`, and raises (instead of silently returning a bogus baseline-boundary time) when `_detect_release` never found a threshold crossing.
- `_detect_release` itself (`pendulastic_pt_score.py:707-721`) and `align_to_release` (`:743-751`) are **not modified** — do not touch them in this task.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_pt_score.py`:

```python
def test_detect_release_t0_rejects_mismatched_lengths():
    from pendulastic_pt_score import detect_release_t0
    t = np.arange(10) / 30.0
    signal = np.zeros(9)
    with pytest.raises(ValueError):
        detect_release_t0(t, signal)


def test_detect_release_t0_rejects_non_monotonic_time():
    from pendulastic_pt_score import detect_release_t0
    t = np.array([0.0, 0.1, 0.05, 0.2, 0.3, 0.4, 0.5, 0.6])
    signal = np.array([180.0, 179.0, 178.0, 180.0, 165.0, 160.0, 158.0, 157.0])
    with pytest.raises(ValueError):
        detect_release_t0(t, signal)


def test_detect_release_t0_raises_on_flat_signal_instead_of_returning_baseline_index():
    """Regression: a constant signal has signal_range=0, so _detect_release's
    forward scan never crosses its own threshold and silently falls through
    to the baseline-window boundary index. Before this fix, detect_release_t0
    returned that boundary time as if it were a real release -- exactly the
    "bogus auto-seeded mark" failure mode the workbench's auto-seed feature
    must not hit."""
    from pendulastic_pt_score import detect_release_t0
    t = np.arange(90) / 30.0
    signal = np.full(90, 180.0)
    with pytest.raises(ValueError):
        detect_release_t0(t, signal, baseline_sec=1.0)
```

`pytest` and `np` are already imported at the top of `tests/test_pt_score.py`; if `pytest` isn't yet imported there, add `import pytest` alongside the existing `import numpy as np`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_pt_score.py -k detect_release_t0 -v`
Expected: `test_detect_release_t0_rejects_mismatched_lengths` and `_rejects_non_monotonic_time` FAIL (no `ValueError` raised — mismatched-length actually raises a numpy broadcasting/IndexError today, not a clean `ValueError`; non-monotonic silently "succeeds" with a wrong answer). `_raises_on_flat_signal...` FAILS because today it returns a float instead of raising.

- [ ] **Step 3: Write minimal implementation**

Replace `detect_release_t0` in `pendulastic_pt_score.py` (lines 724-740):

```python
def detect_release_t0(t: np.ndarray, signal: np.ndarray,
                      baseline_sec: float = 0.6) -> float:
    """
    Detect the release instant t0, as an absolute time value (same units as
    `t`), from a raw trial signal — e.g. IMU tilt magnitude or an OptiTrack-
    derived angle. Savitzky-Golay filters the signal, then runs the adaptive
    -threshold detector (_detect_release) on it. Returning a time rather than
    a sample index lets independently-sampled trials (different frame rates
    or device clocks) each be synchronized to their own release moment.

    Raises ValueError if t/signal don't match, t isn't non-decreasing, fewer
    than 4 finite samples remain, or no release is ever detected (the
    adaptive threshold is never crossed -- see _detect_release; that case
    would otherwise silently return the baseline window's own boundary time
    as if it were a real release).
    """
    t = np.asarray(t, dtype=float)
    signal = np.asarray(signal, dtype=float)
    if t.shape != signal.shape:
        raise ValueError("t and signal must have the same shape.")
    if len(t) >= 2 and np.any(np.diff(t) < 0):
        raise ValueError("t must be non-decreasing.")
    mask = np.isfinite(signal) & np.isfinite(t)
    if mask.sum() < 4:
        raise ValueError("Need at least 4 finite samples to detect release.")
    t_c = t[mask]
    sig_s = _sg(signal[mask])
    baseline_i = max(3, int(np.searchsorted(t_c, t_c[0] + baseline_sec)))
    baseline_i = min(baseline_i, len(t_c) - 1)
    rel_i = _detect_release(t_c, sig_s, baseline_sec=baseline_sec)
    if rel_i == baseline_i:
        raise ValueError("No release detected: signal never crossed the adaptive threshold.")
    return float(t_c[rel_i])
```

Note: `baseline_i` is computed here with the exact same formula `_detect_release` uses internally
(`pendulastic_pt_score.py:710-711`) so the two stay in lockstep without changing
`_detect_release`'s own return contract (which `compute_pt_params`, `pt_report_common.py`, and
`validate_controls.py` all still depend on returning an always-valid index).

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_pt_score.py -k detect_release_t0 -v`
Expected: PASS

- [ ] **Step 5: Run the full existing detect_release_t0/align_to_release suite to confirm no regression**

Run: `.venv\Scripts\python.exe -m pytest tests/test_pt_score.py -v`
Expected: all PASS, including `test_detect_release_t0_returns_absolute_time_of_release`,
`test_align_to_release_zeroes_time_axis_at_t0`, and
`test_imu_and_optitrack_trials_overlay_after_independent_t0_alignment` — these all use synthetic
damped-sinusoid signals with a genuine crossing, monotonic time, and matching lengths, so the new
validation must not affect them.

- [ ] **Step 6: Commit**

```bash
git add pendulastic_pt_score.py tests/test_pt_score.py
git commit -m "fix: detect_release_t0 validates input and raises on no-release-detected instead of returning bogus baseline time"
```

---

## Task 4: `WorkbenchView` foundational state + `MILESTONE_LABELS` trim

**Files:**
- Modify: `pendulastic_workbench.py:37-38` (`MILESTONE_LABELS`), `:288-304` (`WorkbenchView.__init__`)
- Test: `tests/test_pendulastic_workbench.py` (append)

**Interfaces:**
- Produces: `WorkbenchView._release_marks: dict`, `._lag_provenance: dict`, `._armed_release_label: Optional[str]`, `._release_artists: dict`, `._release_entry_vars: dict`, `._release_buttons: dict` — all consumed by Tasks 5-9.
- `MILESTONE_LABELS` becomes `["First Peak Extension", "Maximum Flexion", "Rest/Settled"]`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_pendulastic_workbench.py`:

```python
def test_milestone_labels_no_longer_include_release_start():
    from pendulastic_workbench import MILESTONE_LABELS
    assert "Release Start" not in MILESTONE_LABELS
    assert MILESTONE_LABELS == ["First Peak Extension", "Maximum Flexion", "Rest/Settled"]


def test_workbench_view_starts_with_empty_release_mark_state():
    from pendulastic_workbench import WorkbenchView
    r = _get_root()
    wv = WorkbenchView(r, _Ctrl())
    assert wv._release_marks == {}
    assert wv._lag_provenance == {}
    assert wv._armed_release_label is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_pendulastic_workbench.py -k "milestone_labels_no_longer or release_mark_state" -v`
Expected: FAIL — `"Release Start"` is still present; `WorkbenchView` has no `_release_marks` attribute.

- [ ] **Step 3: Write minimal implementation**

In `pendulastic_workbench.py`, change line 37-38:

```python
MILESTONE_LABELS = ["First Peak Extension", "Maximum Flexion", "Rest/Settled"]
```

In `WorkbenchView.__init__` (after the existing `self._annotations: dict = {}` line, currently line 301, before `self._pending_milestone = ...`), add:

```python
self._release_marks: dict = {}       # {label: {"t_trace": float, "source": "auto"|"manual"}}
self._lag_provenance: dict = {}      # {label: "auto"|"manual"}
self._armed_release_label: Optional[str] = None
self._release_artists: dict = {}     # {label: (line_artist, text_artist)}
self._release_entry_vars: dict = {}  # {label: tk.StringVar}
self._release_buttons: dict = {}     # {label: tk.Button}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_pendulastic_workbench.py -k "milestone_labels_no_longer or release_mark_state" -v`
Expected: PASS

- [ ] **Step 5: Run the full existing workbench test suite to confirm no regression**

Run: `.venv\Scripts\python.exe -m pytest tests/test_pendulastic_workbench.py -v`
Expected: all PASS (dropping `"Release Start"` from `MILESTONE_LABELS` doesn't affect any existing test — verified in spec Section 2a that no existing test asserts on that specific label being in `MILESTONE_LABELS`).

- [ ] **Step 6: Commit**

```bash
git add pendulastic_workbench.py tests/test_pendulastic_workbench.py
git commit -m "feat: add per-trace release-mark state, drop Release Start from global milestones"
```

---

## Task 5: Per-trace chip-row marking UI + lag-entry manual provenance

**Files:**
- Modify: `pendulastic_workbench.py:479-521` (`set_traces()`'s per-label chip-building loop), add new methods near `_on_mark_milestone` (`:688-699`)
- Test: `tests/test_pendulastic_workbench.py` (append)

**Interfaces:**
- Consumes: `self._release_marks`, `._release_entry_vars`, `._release_buttons`, `._armed_release_label` (Task 4).
- Produces: `_on_arm_release(label)`, `_on_clear_release(label)`, `_on_release_entry_commit(label)` — all consumed by manual testing/Task 6/7 integration. Sets `self._lag_provenance[label] = "manual"` from the existing lag-entry `<Return>`/`<FocusOut>` bindings.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_pendulastic_workbench.py`:

```python
def test_mark_release_button_and_entry_exist_per_trace():
    from pendulastic_workbench import WorkbenchView
    r = _get_root()
    wv = WorkbenchView(r, _Ctrl())
    wv.set_traces(_traces("imu", "optitrack"))
    r.update()

    assert "imu" in wv._release_buttons
    assert "optitrack" in wv._release_entry_vars


def test_arm_release_sets_armed_label_and_button_text():
    from pendulastic_workbench import WorkbenchView
    r = _get_root()
    wv = WorkbenchView(r, _Ctrl())
    wv.set_traces(_traces("imu", "optitrack"))
    r.update()

    wv._on_arm_release("imu")
    r.update()

    assert wv._armed_release_label == "imu"
    assert "Click" in wv._release_buttons["imu"].cget("text")


def test_arm_release_disarms_previous_trace():
    from pendulastic_workbench import WorkbenchView
    r = _get_root()
    wv = WorkbenchView(r, _Ctrl())
    wv.set_traces(_traces("imu", "optitrack"))
    r.update()

    wv._on_arm_release("imu")
    wv._on_arm_release("optitrack")
    r.update()

    assert wv._armed_release_label == "optitrack"
    assert wv._release_buttons["imu"].cget("text") == "Mark Release"


def test_release_entry_commit_stores_manual_mark():
    from pendulastic_workbench import WorkbenchView
    r = _get_root()
    wv = WorkbenchView(r, _Ctrl())
    wv.set_traces(_traces("imu"))
    r.update()

    wv._release_entry_vars["imu"].set("1.5")
    wv._on_release_entry_commit("imu")
    r.update()

    assert wv._release_marks["imu"] == {"t_trace": 1.5, "source": "manual"}


def test_release_entry_commit_ignores_unparseable_text():
    from pendulastic_workbench import WorkbenchView
    r = _get_root()
    wv = WorkbenchView(r, _Ctrl())
    wv.set_traces(_traces("imu"))
    r.update()

    wv._release_entry_vars["imu"].set("not-a-number")
    wv._on_release_entry_commit("imu")
    r.update()

    assert "imu" not in wv._release_marks


def test_clear_release_removes_mark_and_resets_entry():
    from pendulastic_workbench import WorkbenchView
    r = _get_root()
    wv = WorkbenchView(r, _Ctrl())
    wv.set_traces(_traces("imu"))
    r.update()
    wv._release_marks["imu"] = {"t_trace": 1.0, "source": "manual"}

    wv._on_clear_release("imu")
    r.update()

    assert "imu" not in wv._release_marks
    assert wv._release_entry_vars["imu"].get() == ""


def test_lag_entry_manual_edit_sets_manual_provenance():
    from pendulastic_workbench import WorkbenchView
    r = _get_root()
    wv = WorkbenchView(r, _Ctrl())
    wv.set_traces(_traces("imu"))
    r.update()

    wv._lag_override_vars["imu"].set("0.42")
    wv._on_lag_entry_commit("imu")
    r.update()

    assert wv._lag_provenance["imu"] == "manual"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_pendulastic_workbench.py -k "release_button or arm_release or release_entry_commit or clear_release or lag_entry_manual" -v`
Expected: FAIL — none of `_on_arm_release`/`_on_clear_release`/`_on_release_entry_commit`/`_on_lag_entry_commit` exist yet; `_release_buttons`/`_release_entry_vars` are never populated by `set_traces()`.

- [ ] **Step 3: Write minimal implementation**

In `set_traces()` (`pendulastic_workbench.py`), inside the per-label loop (after the existing `lag_entry.bind("<FocusOut>", ...)` block, currently ending at line 511, and before `line, = self._ax.plot(t, angle, label=label)` at line 513), replace the two existing bind calls and add the new widgets:

```python
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
```

Also, near the top of `set_traces()`, alongside the existing `self._visible_vars = {}` / `self._lag_override_vars = {}` reset block (currently lines 476-477), add:

```python
        self._release_entry_vars = {}
        self._release_buttons = {}
        self._armed_release_label = None
```

Add the new methods near `_on_mark_milestone` (after it, before `_draw_milestone_artist`, i.e. after line 699):

```python
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
```

This task's own tests call `_on_clear_release`/`_on_release_entry_commit`, both of which need
`_draw_release_artist` (final, complete version — no later task modifies it) and
`_recompute_release_lags` (a real, working, intentionally minimal version for this task only —
Task 9 replaces its body with the full provenance-aware logic; until then it correctly just
refreshes the metrics tables). Add both now:

```python
    def _draw_release_artist(self, label: str, t_trace: float) -> None:
        if label in self._release_artists:
            for artist in self._release_artists[label]:
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
```

(Task 9 replaces `_recompute_release_lags`'s body with the real provenance-aware computation.
`_draw_release_artist` written here is already final — no later task modifies it.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_pendulastic_workbench.py -k "release_button or arm_release or release_entry_commit or clear_release or lag_entry_manual" -v`
Expected: PASS

- [ ] **Step 5: Run the full existing workbench test suite to confirm no regression**

Run: `.venv\Scripts\python.exe -m pytest tests/test_pendulastic_workbench.py -v`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add pendulastic_workbench.py tests/test_pendulastic_workbench.py
git commit -m "feat: add per-trace release-mark UI (arm/clear/numeric entry) and lag-entry manual provenance"
```

---

## Task 6: Click-to-mark on the plot

**Files:**
- Modify: `pendulastic_workbench.py:934-943` (`_on_plot_click`)
- Test: `tests/test_pendulastic_workbench.py` (append)

**Interfaces:**
- Consumes: `self._armed_release_label`, `self._traces`, `self._draw_release_artist` (Task 5), `self._recompute_release_lags` (Task 5 stub, Task 9 final).
- Produces: `_mark_release_at(label, x_click)` — a small helper so `_on_plot_click` stays a thin dispatcher, matching the existing style where `_on_plot_click` already delegates to `_on_scrub`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_pendulastic_workbench.py`:

```python
class _FakeClickEvent:
    def __init__(self, inaxes, xdata):
        self.inaxes = inaxes
        self.xdata = xdata


def test_plot_click_while_armed_marks_release_and_disarms():
    from pendulastic_workbench import WorkbenchView
    r = _get_root()
    wv = WorkbenchView(r, _Ctrl())
    wv.set_traces(_traces("imu"))
    r.update()
    wv._on_arm_release("imu")

    t_arr, _ = wv._traces["imu"]
    click_x = float(t_arr[10])
    wv._on_plot_click(_FakeClickEvent(inaxes=wv._ax, xdata=click_x))
    r.update()

    assert wv._release_marks["imu"]["t_trace"] == pytest.approx(t_arr[10])
    assert wv._release_marks["imu"]["source"] == "manual"
    assert wv._armed_release_label is None
    assert wv._release_buttons["imu"].cget("text") == "Mark Release"


def test_plot_click_snaps_to_nearest_sample():
    from pendulastic_workbench import WorkbenchView
    r = _get_root()
    wv = WorkbenchView(r, _Ctrl())
    wv.set_traces(_traces("imu"))
    r.update()
    wv._on_arm_release("imu")

    t_arr, _ = wv._traces["imu"]
    # Click slightly off-grid, between sample 10 and 11 but closer to 10.
    click_x = float(t_arr[10]) + 0.3 * (float(t_arr[11]) - float(t_arr[10]))
    wv._on_plot_click(_FakeClickEvent(inaxes=wv._ax, xdata=click_x))

    assert wv._release_marks["imu"]["t_trace"] == pytest.approx(t_arr[10])


def test_plot_click_without_arming_falls_back_to_video_seek():
    from pendulastic_workbench import WorkbenchView
    r = _get_root()
    wv = WorkbenchView(r, _Ctrl())
    wv.set_traces(_traces("imu"))
    wv._fps = 30.0
    wv._n_frames = 100
    r.update()

    wv._on_plot_click(_FakeClickEvent(inaxes=wv._ax, xdata=1.0))
    r.update()

    assert "imu" not in wv._release_marks
    assert wv._scrub_var.get() == pytest.approx(30.0)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_pendulastic_workbench.py -k "plot_click" -v`
Expected: `test_plot_click_while_armed_marks_release_and_disarms` and
`test_plot_click_snaps_to_nearest_sample` FAIL (armed clicks currently always fall through to video
seek, never mark anything). `test_plot_click_without_arming_falls_back_to_video_seek` currently
PASSES already (existing behavior) — confirm it stays green after Step 3, it's here as a
regression guard.

- [ ] **Step 3: Write minimal implementation**

Replace `_on_plot_click` (`pendulastic_workbench.py:934-943`):

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_pendulastic_workbench.py -k "plot_click" -v`
Expected: PASS (all three)

- [ ] **Step 5: Run the full existing workbench test suite to confirm no regression**

Run: `.venv\Scripts\python.exe -m pytest tests/test_pendulastic_workbench.py -v`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add pendulastic_workbench.py tests/test_pendulastic_workbench.py
git commit -m "feat: click-to-mark release on a trace's own curve, snapped to nearest sample"
```

---

## Task 7: Fix pre-existing `axvline` leak in global-milestone redraw

**Files:**
- Modify: `pendulastic_workbench.py:701-709` (`_draw_milestone_artist`)
- Test: `tests/test_pendulastic_workbench.py` (append)

**Interfaces:**
- No new interfaces — internal correctness fix, same signature.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_pendulastic_workbench.py`:

```python
def test_remarking_same_milestone_does_not_leak_axvline():
    """Regression: _draw_milestone_artist only ever removed the old text
    annotation, never the old axvline, so re-marking the same milestone
    within one session (without an intervening set_traces() call, which
    would have wiped it via _ax.clear()) accumulated stray dashed lines."""
    from pendulastic_workbench import WorkbenchView
    r = _get_root()
    wv = WorkbenchView(r, _Ctrl())
    wv.set_traces(_traces("imu"))
    r.update()

    wv._scrub_var.set(10)
    wv._on_mark_milestone()
    lines_after_first_mark = len(wv._ax.lines)

    wv._scrub_var.set(20)
    wv._on_mark_milestone()
    r.update()

    assert len(wv._ax.lines) == lines_after_first_mark, (
        "re-marking the same milestone must replace, not accumulate, its axvline")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/test_pendulastic_workbench.py -k remarking_same_milestone -v`
Expected: FAIL — line count grows by 1 on the second mark.

- [ ] **Step 3: Write minimal implementation**

Replace `_draw_milestone_artist` (`pendulastic_workbench.py:701-709`):

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv\Scripts\python.exe -m pytest tests/test_pendulastic_workbench.py -k remarking_same_milestone -v`
Expected: PASS

- [ ] **Step 5: Run the full existing workbench test suite to confirm no regression**

Run: `.venv\Scripts\python.exe -m pytest tests/test_pendulastic_workbench.py -v`
Expected: all PASS, including `test_milestone_annotation_survives_second_set_traces_call`, which
only asserts membership (`marked_label in wv._annotation_artists`) and line color, both unaffected
by the tuple-vs-single-artist storage change.

- [ ] **Step 6: Commit**

```bash
git add pendulastic_workbench.py tests/test_pendulastic_workbench.py
git commit -m "fix: stop leaking a stray axvline on every milestone re-mark within a session"
```

---

## Task 8: Auto-seed release marks via `detect_release_t0`

**Files:**
- Modify: `pendulastic_workbench.py:455-537` (`set_traces()`)
- Test: `tests/test_pendulastic_workbench.py` (append)

**Interfaces:**
- Consumes: `pendulastic_pt_score.detect_release_t0` (Task 3, already imported as `pendulastic_pt_score` at `pendulastic_workbench.py:34`).
- Produces: auto-seeded entries in `self._release_marks` with `"source": "auto"`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_pendulastic_workbench.py`:

```python
def _release_traces(*labels, n=120, fps=30.0):
    """Like _traces(), but with a real hold-then-swing release event (not a
    pure sine from t=0) so detect_release_t0 has something to find."""
    t = np.arange(n) / fps
    out = {}
    for i, label in enumerate(labels):
        ang = np.full(n, 180.0)
        hold = int(fps)
        for j in range(hold, n):
            tj = (j - hold) / fps
            ang[j] = 165.0 - i + 15.0 * np.exp(-0.3 * tj) * np.cos(2 * np.pi * 0.9 * tj)
        out[label] = (t, ang)
    return out


def test_set_traces_auto_seeds_release_mark_for_new_trace():
    from pendulastic_workbench import WorkbenchView
    r = _get_root()
    wv = WorkbenchView(r, _Ctrl())
    wv.set_traces(_release_traces("imu"))
    r.update()

    assert "imu" in wv._release_marks
    assert wv._release_marks["imu"]["source"] == "auto"
    assert 0.8 <= wv._release_marks["imu"]["t_trace"] <= 1.2


def test_set_traces_leaves_degenerate_trace_unmarked_no_exception():
    from pendulastic_workbench import WorkbenchView
    r = _get_root()
    wv = WorkbenchView(r, _Ctrl())
    t = np.arange(90) / 30.0
    flat = {"imu": (t, np.full(90, 180.0))}

    wv.set_traces(flat)   # must not raise
    r.update()

    assert "imu" not in wv._release_marks


def test_set_traces_does_not_reseed_already_marked_trace():
    from pendulastic_workbench import WorkbenchView
    r = _get_root()
    wv = WorkbenchView(r, _Ctrl())
    traces = _release_traces("imu")
    wv.set_traces(traces)
    r.update()
    wv._release_marks["imu"] = {"t_trace": 99.0, "source": "manual"}

    wv.set_traces(traces)  # same trial's async-reload style re-call
    r.update()

    assert wv._release_marks["imu"] == {"t_trace": 99.0, "source": "manual"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_pendulastic_workbench.py -k "auto_seed" -v`
Expected: FAIL — `set_traces()` never populates `_release_marks` yet.

- [ ] **Step 3: Write minimal implementation**

In `set_traces()`, immediately after `self._traces = traces` (currently line 470), before the
`for widget in self._visibility_frame.winfo_children(): widget.destroy()` block, add:

```python
        for label, (t, angle) in traces.items():
            if label in self._release_marks:
                continue
            try:
                t0 = pendulastic_pt_score.detect_release_t0(np.asarray(t), np.asarray(angle))
            except Exception:
                continue
            self._release_marks[label] = {"t_trace": t0, "source": "auto"}
```

Add a new method mirroring the existing `_redraw_annotations()` pattern (place it right after
`_redraw_annotations`, `pendulastic_workbench.py:711-720`):

```python
    def _redraw_release_marks(self) -> None:
        self._release_artists = {}
        for label, mark in self._release_marks.items():
            if label in self._traces:
                self._draw_release_artist(label, mark["t_trace"])
```

At the end of `set_traces()`, after `self._redraw_annotations()` (currently followed by
`self._plot_canvas.draw_idle()` and `self._update_export_csv_state()`), call it and trigger the
auto-align pass:

```python
        self._redraw_annotations()
        self._redraw_release_marks()
        self._plot_canvas.draw_idle()
        self._update_export_csv_state()
        self._recompute_release_lags()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_pendulastic_workbench.py -k "auto_seed" -v`
Expected: PASS

- [ ] **Step 5: Run the full existing workbench test suite to confirm no regression**

Run: `.venv\Scripts\python.exe -m pytest tests/test_pendulastic_workbench.py -v`
Expected: all PASS. In particular, `test_set_traces_new_label_gets_defaults` and
`test_milestone_annotation_survives_second_set_traces_call` use `_traces()` (pure sine from
`t=0`, no hold phase) — confirm auto-seed either finds a plausible crossing or safely no-ops for
those without raising; either outcome is fine since those tests don't assert on `_release_marks`.

- [ ] **Step 6: Commit**

```bash
git add pendulastic_workbench.py tests/test_pendulastic_workbench.py
git commit -m "feat: auto-seed per-trace release marks via detect_release_t0 in set_traces"
```

---

## Task 9: Provenance-aware `_recompute_release_lags`

**Files:**
- Modify: `pendulastic_workbench.py:346` (reference-var `trace_add` callback), replace the Task 5 stub body of `_recompute_release_lags` (added in Task 5, currently `self._recompute_metrics()` only)
- Test: `tests/test_pendulastic_workbench.py` (append)

**Interfaces:**
- Consumes: `workbench_engine.release_lag_sec` (Task 1), `self._release_marks`, `self._lag_provenance`, `self._lag_override_vars` (Task 4/5).
- Produces: final `_recompute_release_lags()` behavior per spec Section 3.5.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_pendulastic_workbench.py`:

```python
def test_recompute_release_lags_fills_auto_field_from_marks():
    from pendulastic_workbench import WorkbenchView
    r = _get_root()
    wv = WorkbenchView(r, _Ctrl())
    wv.set_traces(_traces("imu", "optitrack"))
    r.update()
    wv._reference_var.set("optitrack")
    wv._release_marks["optitrack"] = {"t_trace": 2.0, "source": "manual"}
    wv._release_marks["imu"] = {"t_trace": 0.5, "source": "auto"}

    wv._recompute_release_lags()
    r.update()

    assert wv._lag_override_vars["imu"].get() == "1.500"
    assert wv._lag_provenance["imu"] == "auto"


def test_recompute_release_lags_never_overwrites_manual_provenance():
    from pendulastic_workbench import WorkbenchView
    r = _get_root()
    wv = WorkbenchView(r, _Ctrl())
    wv.set_traces(_traces("imu", "optitrack"))
    r.update()
    wv._reference_var.set("optitrack")
    wv._release_marks["optitrack"] = {"t_trace": 2.0, "source": "manual"}
    wv._release_marks["imu"] = {"t_trace": 0.5, "source": "manual"}
    wv._lag_override_vars["imu"].set("9.999")
    wv._lag_provenance["imu"] = "manual"

    wv._recompute_release_lags()
    r.update()

    assert wv._lag_override_vars["imu"].get() == "9.999"


def test_recompute_release_lags_clears_stale_auto_field_when_mark_removed():
    from pendulastic_workbench import WorkbenchView
    r = _get_root()
    wv = WorkbenchView(r, _Ctrl())
    wv.set_traces(_traces("imu", "optitrack"))
    r.update()
    wv._reference_var.set("optitrack")
    wv._release_marks["optitrack"] = {"t_trace": 2.0, "source": "manual"}
    wv._release_marks["imu"] = {"t_trace": 0.5, "source": "auto"}
    wv._recompute_release_lags()
    r.update()
    assert wv._lag_override_vars["imu"].get() == "1.500"

    del wv._release_marks["imu"]
    wv._recompute_release_lags()
    r.update()

    assert wv._lag_override_vars["imu"].get() == ""


def test_recompute_release_lags_noop_when_neither_marked():
    from pendulastic_workbench import WorkbenchView
    r = _get_root()
    wv = WorkbenchView(r, _Ctrl())
    wv.set_traces(_traces("imu", "optitrack"))
    r.update()
    wv._reference_var.set("optitrack")

    wv._recompute_release_lags()   # must not raise
    r.update()

    assert wv._lag_override_vars["imu"].get() == ""


def test_reference_change_triggers_release_lag_recompute():
    from pendulastic_workbench import WorkbenchView
    r = _get_root()
    wv = WorkbenchView(r, _Ctrl())
    wv.set_traces(_traces("imu", "optitrack"))
    r.update()
    wv._release_marks["imu"] = {"t_trace": 0.5, "source": "auto"}
    wv._release_marks["optitrack"] = {"t_trace": 2.0, "source": "auto"}

    wv._reference_var.set("optitrack")
    r.update()

    assert wv._lag_override_vars["imu"].get() == "1.500"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_pendulastic_workbench.py -k "recompute_release_lags or reference_change_triggers" -v`
Expected: FAIL — `_recompute_release_lags` currently (Task 5's stub) only calls
`_recompute_metrics()` and never touches lag fields; the reference `trace_add` callback still calls
`_recompute_metrics()` directly, not `_recompute_release_lags()`.

- [ ] **Step 3: Write minimal implementation**

Replace the `_recompute_release_lags` stub body (added in Task 5) with:

```python
    def _recompute_release_lags(self) -> None:
        ref_label = self._reference_var.get()
        ref_mark = self._release_marks.get(ref_label)
        for label in self._traces:
            if label == ref_label:
                continue
            if self._lag_provenance.get(label) == "manual":
                continue
            lag_var = self._lag_override_vars.get(label)
            if lag_var is None:
                continue
            test_mark = self._release_marks.get(label)
            if ref_mark is not None and test_mark is not None:
                lag = engine.release_lag_sec(ref_mark["t_trace"], test_mark["t_trace"])
                lag_var.set(f"{lag:.3f}")
                self._lag_provenance[label] = "auto"
            elif lag_var.get().strip():
                lag_var.set("")
                self._lag_provenance.pop(label, None)
        self._recompute_metrics()
```

Change the reference-var callback registration (`pendulastic_workbench.py:346`):

```python
        self._reference_var.trace_add("write", lambda *a: self._recompute_release_lags())
```

(replacing `lambda *a: self._recompute_metrics()`).

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_pendulastic_workbench.py -k "recompute_release_lags or reference_change_triggers" -v`
Expected: PASS

- [ ] **Step 5: Run the full existing workbench test suite to confirm no regression**

Run: `.venv\Scripts\python.exe -m pytest tests/test_pendulastic_workbench.py -v`
Expected: all PASS. In particular, confirm `_recompute_metrics()` still gets called on every
reference change (it's called at the end of `_recompute_release_lags()`, so the metrics tables
still refresh exactly as before).

- [ ] **Step 6: Commit**

```bash
git add pendulastic_workbench.py tests/test_pendulastic_workbench.py
git commit -m "feat: provenance-aware auto-fill/clear of lag(s) from release marks"
```

---

## Task 10: Trial-scoped reset

**Files:**
- Modify: `pendulastic_workbench.py` (add `reset_for_new_trial()` method to `WorkbenchView`, near `set_traces`), `:1101-1169` (`App.on_load_trial`)
- Test: `tests/test_pendulastic_workbench.py` (append)

**Interfaces:**
- Produces: `WorkbenchView.reset_for_new_trial() -> None`.
- Consumed by: `App.on_load_trial()`, called immediately before its existing `self._workbench_view.set_traces(traces)` (currently line 1169).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_pendulastic_workbench.py`:

```python
def test_reset_for_new_trial_clears_release_marks_and_lag_text():
    from pendulastic_workbench import WorkbenchView
    r = _get_root()
    wv = WorkbenchView(r, _Ctrl())
    wv.set_traces(_traces("imu", "optitrack"))
    r.update()
    wv._release_marks["imu"] = {"t_trace": 1.0, "source": "manual"}
    wv._lag_provenance["imu"] = "manual"
    wv._lag_override_vars["imu"].set("0.42")

    wv.reset_for_new_trial()
    r.update()

    assert wv._release_marks == {}
    assert wv._lag_provenance == {}
    assert wv._lag_override_vars["imu"].get() == ""
    assert wv._armed_release_label is None


def test_reset_for_new_trial_then_set_traces_does_not_leak_old_lag_into_same_labels():
    """Loading a genuinely new trial that happens to reuse the same trace
    labels (imu, optitrack) must not silently carry over the previous
    trial's release-derived lag value -- reset_for_new_trial() must run
    before set_traces()'s own prev_lag-preservation reuses the same
    StringVar object."""
    from pendulastic_workbench import WorkbenchView
    r = _get_root()
    wv = WorkbenchView(r, _Ctrl())
    wv.set_traces(_traces("imu", "optitrack"))
    r.update()
    wv._lag_override_vars["imu"].set("7.0")
    wv._lag_provenance["imu"] = "manual"

    wv.reset_for_new_trial()
    wv.set_traces(_traces("imu", "optitrack"))  # "new trial", same labels
    r.update()

    assert wv._lag_override_vars["imu"].get() == ""
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_pendulastic_workbench.py -k reset_for_new_trial -v`
Expected: FAIL — `reset_for_new_trial` doesn't exist yet.

- [ ] **Step 3: Write minimal implementation**

Add to `WorkbenchView`, near `set_traces` (e.g. immediately before it):

```python
    def reset_for_new_trial(self) -> None:
        """Clears per-trace release-mark state before a genuinely new trial
        loads (design spec Section 3.6) -- must run before set_traces(),
        whose own prev_lag-preservation would otherwise reuse the same
        StringVar object (and its stale text) for a same-named label."""
        self._release_marks = {}
        self._lag_provenance = {}
        for lag_var in self._lag_override_vars.values():
            lag_var.set("")
        for release_var in self._release_entry_vars.values():
            release_var.set("")
        self._armed_release_label = None
        self._release_artists = {}
```

In `App.on_load_trial()` (`pendulastic_workbench.py`), immediately before the existing
`self._workbench_view.set_traces(traces)` call (currently line 1169):

```python
        self._load_panel.pack_forget()
        self._workbench_view.pack(fill="both", expand=True)
        self._workbench_view.reset_for_new_trial()
        self._workbench_view.set_traces(traces)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_pendulastic_workbench.py -k reset_for_new_trial -v`
Expected: PASS

- [ ] **Step 5: Run the full existing workbench test suite to confirm no regression**

Run: `.venv\Scripts\python.exe -m pytest tests/test_pendulastic_workbench.py -v`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add pendulastic_workbench.py tests/test_pendulastic_workbench.py
git commit -m "feat: reset release marks/lag provenance on new trial load, not async HPE merge"
```

---

## Task 11: "Release Marks..." CSV export

**Files:**
- Modify: `pendulastic_workbench.py:365-383` (export menu setup), `:722-723` (add `get_release_marks` near `get_annotations`), `:743-748` (`_update_export_csv_state`), `:782-806` (add `_on_export_release_marks_csv` near the other export handlers)
- Test: `tests/test_pendulastic_workbench.py` (append)

**Interfaces:**
- Consumes: `workbench_engine.release_marks_to_csv_rows` (Task 2).
- Produces: `WorkbenchView.get_release_marks() -> dict`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_pendulastic_workbench.py`:

```python
def test_get_release_marks_returns_copy():
    from pendulastic_workbench import WorkbenchView
    r = _get_root()
    wv = WorkbenchView(r, _Ctrl())
    wv.set_traces(_traces("imu"))
    r.update()
    wv._release_marks["imu"] = {"t_trace": 1.0, "source": "manual"}

    marks = wv.get_release_marks()
    assert marks == {"imu": {"t_trace": 1.0, "source": "manual"}}
    marks["imu"]["t_trace"] = 99.0
    assert wv._release_marks["imu"]["t_trace"] == 1.0, "must be a shallow copy at the top level"


def test_export_csv_menu_release_marks_entry_disabled_when_no_marks():
    from pendulastic_workbench import WorkbenchView
    r = _get_root()
    wv = WorkbenchView(r, _Ctrl())
    wv.set_traces(_traces("imu"))
    r.update()

    assert wv._export_csv_menu.entrycget(4, "state") == "disabled"

    wv._release_marks["imu"] = {"t_trace": 1.0, "source": "manual"}
    wv._update_export_csv_state()
    r.update()

    assert wv._export_csv_menu.entrycget(4, "state") == "normal"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_pendulastic_workbench.py -k "get_release_marks or export_csv_menu_release_marks" -v`
Expected: FAIL — `get_release_marks` doesn't exist; the menu has no 5th entry (index 4).

- [ ] **Step 3: Write minimal implementation**

Add `get_release_marks`, right after `get_annotations` (`pendulastic_workbench.py:722-723`):

```python
    def get_release_marks(self) -> dict:
        return {label: dict(mark) for label, mark in self._release_marks.items()}
```

In the export menu setup (`pendulastic_workbench.py`, inside `_build_widgets`, right after the
existing `self._export_csv_menu.add_command(label="Annotations...", ...)` line, currently ~380):

```python
        self._export_csv_menu.add_command(label="Release Marks...",
                                          command=self._on_export_release_marks_csv)
```

Update `_update_export_csv_state` (`pendulastic_workbench.py:743-748`):

```python
    def _update_export_csv_state(self) -> None:
        has_traces = bool(self._traces)
        has_annotations = bool(self._annotations)
        has_release_marks = bool(self._release_marks)
        for i in (0, 1, 2):
            self._export_csv_menu.entryconfig(i, state="normal" if has_traces else "disabled")
        self._export_csv_menu.entryconfig(3, state="normal" if has_annotations else "disabled")
        self._export_csv_menu.entryconfig(4, state="normal" if has_release_marks else "disabled")
```

Add the export handler near the other `_on_export_*_csv` methods (`pendulastic_workbench.py`,
after `_on_export_annotations_csv`, currently ending at line 806):

```python
    def _on_export_release_marks_csv(self) -> None:
        participant_id, session_date = self._meta_ids()
        fieldnames, rows = engine.release_marks_to_csv_rows(
            self.get_release_marks(), participant_id, session_date)
        self._prompt_and_write_csv("release_marks", fieldnames, rows)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_pendulastic_workbench.py -k "get_release_marks or export_csv_menu_release_marks" -v`
Expected: PASS

- [ ] **Step 5: Run the full existing workbench test suite to confirm no regression**

Run: `.venv\Scripts\python.exe -m pytest tests/test_pendulastic_workbench.py -v`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add pendulastic_workbench.py tests/test_pendulastic_workbench.py
git commit -m "feat: add Release Marks CSV export"
```

---

## Task 12: Full regression + spec-coverage verification

**Files:** none modified — verification only.

- [ ] **Step 1: Run the entire test suite**

Run: `.venv\Scripts\python.exe -m pytest tests/ -v`
Expected: all PASS, no skips due to import errors.

- [ ] **Step 2: Spec-coverage check against `docs/superpowers/specs/2026-08-10-release-start-alignment-design.md`**

Walk each numbered subsection (3.1 through 3.7) and confirm a task above implements it:
- 3.1 dedicated storage → Task 4
- 3.2 `detect_release_t0` fix → Task 3
- 3.3 marking mechanism (arm/click/snap/numeric fallback/clear) → Tasks 5, 6
- 3.4 auto-seed → Task 8
- 3.5 provenance-aware auto-align → Tasks 1, 9
- 3.6 trial-scoped reset → Task 10
- 3.7 export → Tasks 2, 11
- Section 3.3's marker redraw-leak fix → Task 7 (also fixes the pre-existing global-milestone
  instance of the same bug, per spec Section 3.3's explicit call-out)

- [ ] **Step 3: Manual smoke test (requires a real or synthetic trial)**

Run the app: `.venv\Scripts\python.exe pendulastic_app.py`, open the Workbench, load a trial with
both IMU and OptiTrack CSVs (or any two traces). Confirm:
- Both traces show an auto-seeded release marker (or are left unmarked if their data is degenerate)
  immediately after loading, no clicking required.
- Clicking "Mark Release" on one trace, then clicking a different point on that trace's own curve,
  moves its marker and updates its `lag(s)` field once a reference is also marked.
- Typing directly into a trace's `lag(s)` field, pressing Enter, then re-marking that trace's
  release (or the reference's) does **not** silently overwrite the typed value.
- "Load Different Trial" followed by loading a new trial with the same trace labels shows no
  leftover release markers or lag values from the previous trial.
- The "Release Marks..." CSV export produces a `label, t_trace, source` row per marked trace.

- [ ] **Step 4: Final commit (only if Step 3 surfaces a fix)**

If the manual smoke test finds nothing to fix, no commit is needed for this task. If it does,
make the minimal fix, re-run the full suite (Step 1), and commit with a message describing what
the smoke test caught.
