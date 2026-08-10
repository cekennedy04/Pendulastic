# Full-Report HPE/IMU Accuracy Comparison Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend the per-participant full report (`pt_report_common.make_report_figure()`, run via `run_pt_analysis.py <pid>`) from a 3×2 grid showing only OptiTrack ground truth into a 5×2 grid + caption that shows how MediaPipe/phone-IMU tracking and PT7/MAS scoring compare to OptiTrack, adds clinician MAS context, MS-vs-Control cohort context, and a data-completeness accounting of every discovered trial file.

**Architecture:** Additive changes to four existing modules (`pendulastic_pt_score.py`, `pt_report_common.py`, `pt_cohort_common.py`, `run_pt_analysis.py`) plus one new sidecar CSV output. No new files, no new dependencies. Every new function is either standalone/additive (zero regression risk to existing heavily-tested code) or a narrow, signature-compatible extension (existing callers keep working unchanged via default parameters).

**Tech Stack:** Python, matplotlib (figure/table plotting), numpy, pytest + `monkeypatch` (existing test convention in this repo — see `tests/test_pt_report_common.py`, `tests/test_pt_cohort_common.py`).

## Global Constraints

- 7-parameter Popovic PT score computation (`pendulastic_pt_score.compute_pt_params`/`compute_pt_score`) is never modified — every PT7 value in this feature, from any source, goes through the exact same function.
- `_detect_release`'s threshold algorithm is never modified.
- `ms_vs_healthy_analysis.py` and `pendulastic_pt_score.py`'s own single-trial plots (`_make_plot`, `_make_hpe_all_plot`) are never modified except where Task 1 explicitly extends `load_hpe_model_curves()` with a default-`False` opt-in parameter — their existing calls into it must keep behaving identically.
- Every new/modified function that can encounter missing or malformed data degrades to "unavailable"/omitted, never raises out of `make_report_figure()` — matching this module's existing non-fatal convention (`load_excluded_trials()`, `collect_participant()`'s try/except).
- Existing tests in `tests/test_pt_report_common.py`, `tests/test_pt_cohort_common.py`, `tests/test_pt_score.py`, `tests/test_mas_validation.py` must all still pass unchanged after every task — run the full suite at the end of each task's steps, not just the new test.
- Design source of truth: `docs/superpowers/specs/2026-08-10-full-report-hpe-accuracy-design.md` (revised after 3 Codex review passes). Where this plan makes a simplification the spec didn't explicitly call out (Task 4's `trial_candidates()` is additive rather than refactoring `discover_all_trials()`/`collect_participant()` to filter over it), the task states the reason inline.

---

## File Structure

| File | New/changed responsibility |
|---|---|
| `pendulastic_pt_score.py` | `load_hpe_model_curves()` gains an opt-in `return_rejected` parameter (Task 1). |
| `pt_report_common.py` | `attach_rmse()` keeps curve arrays + picks a deterministic candidate (Task 2). New: `release_aligned_hpe_curve()` (Task 3), `trial_candidates()` (Task 4), `clinician_mas_matches()` (Task 6), `write_clinician_mas_sidecar()` (Task 7), `_draw_rmse_axes()` extracted from `make_rmse_figure()` (Task 9), `_draw_row5_table()` (Task 10), `_build_caption_text()` (Task 11). `make_report_figure()` grows to a 5×2 grid (Task 12). |
| `pt_cohort_common.py` | `_collect_arm_data()` gains a `summaries_by_pid` return value (Task 8). New: `build_cohort_snapshot()`, `write_cohort_artifacts()`, `leg_cohort_reference()` (Task 8). `run_cohort_comparison()` becomes a thin combinator of the two new functions, preserving every existing test unchanged. |
| `run_pt_analysis.py` | `main()` calls `build_cohort_snapshot()` once before the per-participant loop and threads the snapshot into every report/artifact call (Task 13). |
| `tests/test_pt_report_common.py` | Extended with tests for every new function (Tasks 1–12, inline per task). |
| `tests/test_pt_cohort_common.py` | Extended with tests for Task 8's new functions. |

No new files are created. Task boundaries below are drawn so each is independently testable and reviewable, in dependency order (later tasks consume earlier tasks' new functions).

---

## Task 1: `load_hpe_model_curves()` gains an opt-in rejection-accounting mode

**Files:**
- Modify: `pendulastic_pt_score.py:1103-1333` (`load_hpe_model_curves` and its nested `_evaluate_candidate`)
- Test: `tests/test_pt_score.py`

**Interfaces:**
- Consumes: nothing new (existing `pt._sg`, `_clean_hpe_angle`, `_replay_raw_imu_fallback` unchanged).
- Produces: `load_hpe_model_curves(pid_str, pos, trial, t_opti, angle_raw, neutral_deg, csv_files=None, return_rejected=False) -> list[dict] | tuple[list[dict], list[dict]]`. Default behavior (`return_rejected=False`) is byte-for-byte identical to today — same return type (`list[dict]`), same filtering, same sort. When `return_rejected=True`, returns `(accepted, rejected)` where `accepted` is exactly what the function returns today, and `rejected` is `list[dict]` with `{"name": str, "reason": str}` for every candidate CSV/replay that `_evaluate_candidate` rejected. `reason` is one of: `"low_valid_fraction"`, `"insufficient_reference_window"`, `"insufficient_swing_samples"`, `"did_not_track_swing"`.

Today, `_evaluate_candidate` returns `None` on rejection with no reason attached, and the caller (`load_hpe_model_curves`) silently drops rejected candidates entirely — there's no way for a caller to know a MediaPipe CSV existed for a trial but got filtered out. This task makes that visible without touching the filtering logic itself or any existing caller's behavior.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_pt_score.py` (check the existing imports at the top of that file first — it already imports `pendulastic_pt_score as pt` per this repo's convention):

```python
def test_load_hpe_model_curves_default_unchanged_when_return_rejected_false():
    """return_rejected=False (the default) must keep today's exact return
    shape -- a bare list, not a tuple -- so every existing caller
    (pendulastic_pt_score.py's own single-trial plots) is unaffected."""
    import numpy as np
    t = np.linspace(0, 2, 60)
    angle = 180 - 40 * np.sin(np.pi * t / 2) * (t < 1.0)
    result = pt.load_hpe_model_curves("999_left_pre", "1", "1", t, angle, 180.0, csv_files=[])
    assert isinstance(result, list)
    assert result == []


def test_load_hpe_model_curves_return_rejected_true_gives_tuple(tmp_path, monkeypatch):
    """return_rejected=True must give (accepted, rejected), both lists,
    even when nothing was ever discovered (no csv_files, no replay
    fallback) -- rejected is [] in that case, not a crash."""
    import numpy as np
    t = np.linspace(0, 2, 60)
    angle = 180 - 40 * np.sin(np.pi * t / 2) * (t < 1.0)
    accepted, rejected = pt.load_hpe_model_curves(
        "999_left_pre", "1", "1", t, angle, 180.0, csv_files=[], return_rejected=True)
    assert accepted == []
    assert rejected == []


def test_load_hpe_model_curves_return_rejected_reports_did_not_track_swing(tmp_path):
    """A candidate CSV whose knee_angle_deg never leaves neutral (flat
    signal) fails the swing-tracking quality gate -- with
    return_rejected=True this must show up in `rejected` with a reason,
    not silently vanish the way it does today."""
    import numpy as np
    import pandas as pd

    t_opti = np.linspace(0, 2, 60)
    angle_opti = 180 - 40 * np.sin(np.pi * t_opti / 2) * (t_opti < 1.0)

    flat_csv = tmp_path / "P_T_1_mediapipe.csv"
    t_m = np.linspace(0, 2, 60)
    pd.DataFrame({"time_sec": t_m, "knee_angle_deg": np.full(60, 180.0)}).to_csv(flat_csv, index=False)

    accepted, rejected = pt.load_hpe_model_curves(
        "999_left_pre", "1", "1", t_opti, angle_opti, 180.0,
        csv_files=[str(flat_csv)], return_rejected=True)
    assert accepted == []
    assert len(rejected) == 1
    assert rejected[0]["name"] == "mediapipe"
    assert rejected[0]["reason"] == "did_not_track_swing"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_pt_score.py -k return_rejected -v`
Expected: FAIL — `load_hpe_model_curves() got an unexpected keyword argument 'return_rejected'`.

- [ ] **Step 3: Implement `return_rejected`**

In `pendulastic_pt_score.py`, change the signature at line 1103-1105:

```python
def load_hpe_model_curves(pid_str: str, pos: str, trial: str,
                          t_opti: np.ndarray, angle_raw: np.ndarray,
                          neutral_deg: float, csv_files: Optional[list] = None,
                          return_rejected: bool = False) -> list:
```

Update the docstring's Returns section (after line 1125) to document the new parameter:

```
    return_rejected: when True, returns (accepted, rejected) instead of
    just accepted -- rejected is a list of {"name": str, "reason": str}
    for every candidate _evaluate_candidate filtered out. Default False
    keeps today's exact return shape for every existing caller.
```

Change every early `return []` in the body (lines 1173, 1202, 1214, 1218, 1329) to route through a small local helper so both return modes share one exit path. Add right after the docstring (after line 1126, before `MAX_HPE_OVERLAY = 8`):

```python
    def _finish(accepted_list, rejected_list):
        return (accepted_list, rejected_list) if return_rejected else accepted_list
```

Then replace each bare `return []` in the function body with `return _finish([], [])`, **except** the final return at line 1333 which becomes:

```python
    return _finish(candidates[:MAX_HPE_OVERLAY], rejected)
```

Now thread rejection reasons through `_evaluate_candidate`. Change it to return `(candidate_or_None, reason_or_None)` instead of just `candidate_or_None`. Update each of its 4 `return None` statements (originally at what are now relative lines within the nested function — locate by content, not fixed numbers, since Step 1's edits shift line numbers):

- `if raw_pct < 5: return None` → `if raw_pct < 5: return None, "low_valid_fraction"`
- `if ref_valid.sum() < 3: return None` → `if ref_valid.sum() < 3: return None, "insufficient_reference_window"`
- `if sw_mask.sum() < 3: return None` → `if sw_mask.sum() < 3: return None, "insufficient_swing_samples"`
- The inversion-check block's `else: return None   # neither direction tracks the swing` → `else: return None, "did_not_track_swing"`
- `if model_peak / opti_peak < 0.30: return None   # model didn't track the swing` → `if model_peak / opti_peak < 0.30: return None, "did_not_track_swing"`
- The success path `return {"name": model_name, "t": t_m, "ang": cleaned, "raw_pct": raw_pct, "rmse": rmse}` → `return {"name": model_name, "t": t_m, "ang": cleaned, "raw_pct": raw_pct, "rmse": rmse}, None`

Then update the two call sites inside `load_hpe_model_curves` that call `_evaluate_candidate` (in the `for csv_path in csv_files:` loop and the `if _replayed_imu is not None:` block) to collect a `rejected` list:

```python
    candidates = []
    rejected = []
    for csv_path in csv_files:
        bn = os.path.basename(csv_path)
        m = re.search(r"_T_\d+_(.+?)\.csv$", bn, re.I)
        if not m:
            continue
        model_name = m.group(1)

        try:
            df = pd.read_csv(csv_path)
            if "knee_angle_deg" not in df.columns or "time_sec" not in df.columns:
                continue
            t_m   = df["time_sec"].values.astype(float)
            ang_m = df["knee_angle_deg"].values.astype(float)
        except Exception:
            continue

        cand, reason = _evaluate_candidate(model_name, t_m, ang_m)
        if cand is not None:
            candidates.append(cand)
        elif reason is not None:
            rejected.append({"name": model_name, "reason": reason})

    if _replayed_imu is not None:
        cand, reason = _evaluate_candidate("imu_viewer", *_replayed_imu)
        if cand is not None:
            candidates.append(cand)
        elif reason is not None:
            rejected.append({"name": "imu_viewer", "reason": reason})

    if not candidates:
        return _finish([], rejected)

    candidates.sort(key=lambda d: d["rmse"] if np.isfinite(d.get("rmse", np.nan)) else 1e9)
    return _finish(candidates[:MAX_HPE_OVERLAY], rejected)
```

Note the pre-swing-detection early returns (`opti_peak < 3.0`, `not in_swing.any()`) happen *before* any candidate CSV is even evaluated — these represent "OptiTrack itself has no scoreable swing," not a per-candidate rejection, so they correctly return `_finish([], [])` (no rejected entries, since no candidate was ever evaluated against them).

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_pt_score.py -k "return_rejected or hpe_model_curves" -v`
Expected: PASS.

- [ ] **Step 5: Run the full existing test_pt_score.py suite to confirm no regression**

Run: `.venv\Scripts\python.exe -m pytest tests/test_pt_score.py -v`
Expected: PASS, same pass count as before this task plus the 3 new tests.

- [ ] **Step 6: Commit**

```bash
git add pendulastic_pt_score.py tests/test_pt_score.py
git commit -m "feat: add return_rejected accounting mode to load_hpe_model_curves"
```

---

## Task 2: `attach_rmse()` keeps curve arrays and picks a deterministic candidate

**Files:**
- Modify: `pt_report_common.py:578-599` (`attach_rmse`)
- Test: `tests/test_pt_report_common.py`

**Interfaces:**
- Consumes: `pendulastic_pt_score.load_hpe_model_curves()` (Task 1's signature, called with `return_rejected=False` — this task doesn't need rejection accounting, only the accepted candidates).
- Produces: `attach_rmse(by_leg_tp)` — same signature and same `rec["mediapipe_rmse"]`/`rec["imu_rmse"]` keys as today, **plus** `rec["mediapipe_curve"]` and `rec["imu_curve"]`, each either `None` or `{"t": ndarray, "ang": ndarray}` — the exact `t`/`ang` arrays from the single deterministic candidate used for that trial's RMSE too (first-by-RMSE-sort match per source, not "whichever the loop saw last").

Today's `attach_rmse` (read it at `pt_report_common.py:578-599` before editing) iterates `curves` and does `if c["name"].startswith("mediapipe"): rec["mediapipe_rmse"] = c.get("rmse")` — with multiple MediaPipe-name matches, this overwrites on every match, silently keeping whichever came last in the (already RMSE-sorted) list rather than the best one. This task fixes that by taking the *first* match per source (curves are already sorted best-RMSE-first by `load_hpe_model_curves`) instead of overwriting through the whole loop.

- [ ] **Step 1: Write the failing tests**

```python
def test_attach_rmse_keeps_curve_arrays(monkeypatch):
    import numpy as np
    fake_curves = [
        {"name": "mediapipe", "t": np.array([0.0, 1.0]), "ang": np.array([180.0, 150.0]), "rmse": 3.5},
        {"name": "imu_viewer", "t": np.array([0.0, 1.0]), "ang": np.array([180.0, 155.0]), "rmse": 5.0},
    ]
    rec = {"pid": "13_left_pre", "trial": "1", "t_raw": np.array([0.0, 1.0]),
          "angle_raw": np.array([180.0, 152.0]), "neutral_deg_raw": 180.0}
    by_leg_tp = {("left", "pre"): [rec]}
    monkeypatch.setattr(common.pt, "load_hpe_model_curves", lambda *a, **k: fake_curves)

    common.attach_rmse(by_leg_tp)

    assert rec["mediapipe_rmse"] == 3.5
    assert rec["mediapipe_curve"] is not None
    assert list(rec["mediapipe_curve"]["ang"]) == [180.0, 150.0]
    assert rec["imu_rmse"] == 5.0
    assert rec["imu_curve"] is not None


def test_attach_rmse_deterministic_candidate_not_overwritten(monkeypatch):
    """Two mediapipe-name matches -- today's loop silently keeps whichever
    came last. Curves arrive sorted best-RMSE-first, so the FIRST match
    (RMSE=2.0) must win, not the second (RMSE=9.0)."""
    import numpy as np
    fake_curves = [
        {"name": "mediapipe", "t": np.array([0.0]), "ang": np.array([180.0]), "rmse": 2.0},
        {"name": "mediapipe_alt", "t": np.array([0.0]), "ang": np.array([170.0]), "rmse": 9.0},
    ]
    rec = {"pid": "13_left_pre", "trial": "1", "t_raw": np.array([0.0]),
          "angle_raw": np.array([180.0]), "neutral_deg_raw": 180.0}
    by_leg_tp = {("left", "pre"): [rec]}
    monkeypatch.setattr(common.pt, "load_hpe_model_curves", lambda *a, **k: fake_curves)

    common.attach_rmse(by_leg_tp)

    assert rec["mediapipe_rmse"] == 2.0
    assert list(rec["mediapipe_curve"]["ang"]) == [180.0]


def test_attach_rmse_no_curves_leaves_curve_fields_none(monkeypatch):
    rec = {"pid": "13_left_pre", "trial": "1", "t_raw": [0.0],
          "angle_raw": [180.0], "neutral_deg_raw": 180.0}
    by_leg_tp = {("left", "pre"): [rec]}
    monkeypatch.setattr(common.pt, "load_hpe_model_curves", lambda *a, **k: [])

    common.attach_rmse(by_leg_tp)

    assert rec.get("mediapipe_curve") is None
    assert rec.get("imu_curve") is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_pt_report_common.py -k attach_rmse -v`
Expected: FAIL — `AttributeError` or `KeyError` on `mediapipe_curve` (doesn't exist yet), and the overwrite test fails because today's code keeps the *last* match, not the first.

- [ ] **Step 3: Implement**

Read the current `attach_rmse` at `pt_report_common.py:578-599` first (it hasn't changed since design time — confirm before editing). Replace its body with:

```python
def attach_rmse(by_leg_tp):
    """Best-effort MediaPipe/IMU RMSE + curve lookup for every scored trial,
    via pt.load_hpe_model_curves's standard Recordings/Participant_{pid}/
    Position_1/Height_Joint-Level/ convention (with its own recursive
    Session_*/ fallback). Silently finds nothing (not an error) for
    participants/conditions laid out differently or never processed through
    MediaPipe -- those simply won't have RMSE bars or curve overlays.
    Mutates trial records in place and returns by_leg_tp for chaining.

    Deterministic candidate: curves arrive sorted best-RMSE-first from
    load_hpe_model_curves, so the FIRST mediapipe-name match and the FIRST
    imu_viewer match are used -- not whichever the loop happens to see
    last -- and that same candidate's rmse and t/ang curve are stored
    together, so a later consumer (the waveform overlay, the PT7 score,
    the RMSE bar) can never end up looking at mismatched candidates for
    the same trial."""
    for trials in by_leg_tp.values():
        for rec in trials:
            try:
                curves = pt.load_hpe_model_curves(
                    rec["pid"], "1", rec["trial"],
                    rec["t_raw"], rec["angle_raw"], rec["neutral_deg_raw"])
            except Exception:
                curves = []
            mediapipe_curve = next((c for c in curves if c["name"].startswith("mediapipe")), None)
            imu_curve = next((c for c in curves if c["name"] == "imu_viewer"), None)
            rec["mediapipe_rmse"] = mediapipe_curve.get("rmse") if mediapipe_curve else None
            rec["mediapipe_curve"] = {"t": mediapipe_curve["t"], "ang": mediapipe_curve["ang"]} if mediapipe_curve else None
            rec["imu_rmse"] = imu_curve.get("rmse") if imu_curve else None
            rec["imu_curve"] = {"t": imu_curve["t"], "ang": imu_curve["ang"]} if imu_curve else None
    return by_leg_tp
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_pt_report_common.py -k attach_rmse -v`
Expected: PASS.

- [ ] **Step 5: Run the full existing test suite**

Run: `.venv\Scripts\python.exe -m pytest tests/test_pt_report_common.py tests/test_rmse_pipeline_common.py -v`
Expected: PASS (check `test_rmse_pipeline_common.py` doesn't import/monkeypatch `attach_rmse` in a way this breaks — read it first if any test fails).

- [ ] **Step 6: Commit**

```bash
git add pt_report_common.py tests/test_pt_report_common.py
git commit -m "fix: attach_rmse keeps curve arrays and picks deterministic candidate"
```

---

## Task 3: `release_aligned_hpe_curve()` — per-source release alignment

**Files:**
- Modify: `pt_report_common.py` (add new function near `release_aligned_waveform`, i.e. after line 111)
- Test: `tests/test_pt_report_common.py`

**Interfaces:**
- Consumes: `pt._sg`, `pt._detect_release` (both already imported into `pt_report_common.py` as `pt`).
- Produces: `release_aligned_hpe_curve(mdl_t, mdl_ang) -> tuple[np.ndarray, np.ndarray] | None`. Returns `(t_plot, a_plot)` — same shape/semantics as `release_aligned_waveform`'s return — or `None` if the input fails validation (fewer than 4 finite samples, or non-monotonic `t`). Never raises.

Mirrors `release_aligned_waveform` (`pt_report_common.py:84-111`) exactly, but takes a raw `(t, ang)` pair directly instead of a trial record dict, since HPE/IMU curves don't have the same record shape as OptiTrack trials.

- [ ] **Step 1: Write the failing tests**

```python
def test_release_aligned_hpe_curve_aligns_release_to_zero():
    import numpy as np
    t = np.linspace(0, 3, 90)
    # Held at 180 for 1s, then swings down -- same shape release_aligned_waveform's
    # own docstring describes for OptiTrack.
    ang = np.where(t < 1.0, 180.0, 180.0 - 30.0 * np.sin((t - 1.0) * 2))
    result = common.release_aligned_hpe_curve(t, ang)
    assert result is not None
    t_plot, a_plot = result
    # Release should land near t=0 in the shifted output -- the original
    # hold-then-swing transition was at t=1.0 in input coordinates.
    assert abs(t_plot[np.argmin(np.abs(t_plot))]) < 0.2


def test_release_aligned_hpe_curve_rejects_too_few_samples():
    import numpy as np
    result = common.release_aligned_hpe_curve(np.array([0.0, 0.1, 0.2]), np.array([180.0, 179.0, 178.0]))
    assert result is None


def test_release_aligned_hpe_curve_rejects_non_monotonic_time():
    import numpy as np
    t = np.array([0.0, 0.2, 0.1, 0.3, 0.4])
    ang = np.array([180.0, 179.0, 178.0, 177.0, 176.0])
    result = common.release_aligned_hpe_curve(t, ang)
    assert result is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_pt_report_common.py -k release_aligned_hpe -v`
Expected: FAIL — `AttributeError: module 'pt_report_common' has no attribute 'release_aligned_hpe_curve'`.

- [ ] **Step 3: Implement**

Add immediately after `release_aligned_waveform` (after line 111 of `pt_report_common.py`):

```python
def release_aligned_hpe_curve(mdl_t, mdl_ang):
    """Per-source mirror of release_aligned_waveform() for an HPE/IMU curve
    (mdl_t, mdl_ang) rather than a trial record dict -- SG-smooth, detect
    release with pt._detect_release on the raw/smoothed (not detrended)
    signal, shift so that curve's OWN detected release lands at t=0.
    Without this, a source's timing offset from OptiTrack would visually
    read as an angle-tracking error instead of a timing artifact.

    Owns its own input validation (pt._detect_release doesn't reject
    degenerate input): fewer than 4 finite samples, or non-monotonic t,
    returns None rather than raising or producing a bogus alignment --
    same "unavailable for this timepoint" path callers already use for a
    missing/rejected HPE curve. No separate short-window guard is needed:
    pt._sg() degrades gracefully (returns the signal unsmoothed) for a
    window too small to fit, rather than raising, matching how
    _detect_release() already tolerates short input.

    Known caveat (2026-08-10 full-report-hpe-accuracy design spec §5.1):
    pt._detect_release silently returns its own baseline-window boundary
    index when no real threshold crossing is found, rather than raising --
    a flat/degenerate curve that passes the validation here but has no
    real release can still silently misalign. Tracked by the separate
    2026-08-10 release-start-alignment spec's still-unimplemented fix,
    not addressed here."""
    mask = np.isfinite(mdl_ang) & np.isfinite(mdl_t)
    if mask.sum() < 4:
        return None
    t_masked = mdl_t[mask]
    a_masked = mdl_ang[mask]
    if np.any(np.diff(t_masked) <= 0):
        return None
    a_smooth = pt._sg(a_masked, w=15, p=3)
    release_idx = pt._detect_release(t_masked, a_smooth)
    release_idx = max(0, min(release_idx, len(t_masked) - 1))
    t_release = t_masked[release_idx]
    y_off = 180.0 - float(a_smooth[release_idx])
    t_plot = t_masked - t_release
    a_plot = a_masked + y_off
    return t_plot, a_plot
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_pt_report_common.py -k release_aligned_hpe -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add pt_report_common.py tests/test_pt_report_common.py
git commit -m "feat: add release_aligned_hpe_curve for per-source waveform alignment"
```

---

## Task 4: `trial_candidates()` — standalone data-completeness enumeration

**Files:**
- Modify: `pt_report_common.py` (add new function after `load_excluded_trials`, i.e. after line 193)
- Test: `tests/test_pt_report_common.py`

**Interfaces:**
- Consumes: `load_excluded_trials()`, `_parse_trial_path()`, `pt.load_optitrack()`, `score_trial()` — all existing, unchanged.
- Produces: `trial_candidates(participant_id, include_archive=True) -> list[dict]`, one dict per raw discovered file: `{"leg": str|None, "condition": str|None, "trial": str|None, "path": str, "status": str, "reason": str|None, "record": dict|None}`. `status` is one of `"unparseable"`, `"invalid_path"`, `"excluded"`, `"unreadable"`, `"unscoreable"`, `"scored"`. `reason` carries the exclusion reason string for `"excluded"`, else `None`. `record` carries the full scored trial dict (same shape `score_trial()` produces) only when `status == "scored"`, else `None`.

**Deliberate simplification vs. the design spec:** the spec described `discover_all_trials()`/`collect_participant()` "becoming thin filters" over this function. This task does **not** refactor either of those two heavily-used, already-tested functions — `trial_candidates()` is fully standalone and does its own raw glob, independently. This is lower regression risk (zero chance of changing `discover_all_trials()`/`collect_participant()`'s existing behavior for any other caller) at the cost of a small amount of duplicated glob/parse logic, which this codebase already tolerates elsewhere (`pendulastic_pt_score.py` has its own separate path-discovery logic from `pt_report_common.py`). The completeness-tally requirement only needs `trial_candidates()` to *exist and be correct* — it doesn't require touching the two existing functions.

- [ ] **Step 1: Write the failing tests**

```python
def test_trial_candidates_classifies_invalid_path(tmp_path, monkeypatch):
    invalid_dir = tmp_path / "Participant_13_left" / "INVALID_bad_run"
    invalid_dir.mkdir(parents=True)
    csv_path = invalid_dir / "trial_1_optitrack.csv"
    csv_path.write_text("t,angle\n0,180\n")
    monkeypatch.setattr(common, "OPTI_ROOT", str(tmp_path))
    monkeypatch.setattr(common, "ARCHIVE_ROOT", "/nonexistent")
    monkeypatch.setattr(common, "load_excluded_trials", lambda: {})

    candidates = common.trial_candidates("13", include_archive=False)
    assert len(candidates) == 1
    assert candidates[0]["status"] == "invalid_path"


def test_trial_candidates_classifies_unparseable(tmp_path, monkeypatch):
    # No participant number in the path at all -- _parse_trial_path returns None.
    bad_dir = tmp_path / "left"
    bad_dir.mkdir(parents=True)
    csv_path = bad_dir / "trial_1_optitrack.csv"
    csv_path.write_text("t,angle\n0,180\n")
    monkeypatch.setattr(common, "OPTI_ROOT", str(tmp_path))
    monkeypatch.setattr(common, "ARCHIVE_ROOT", "/nonexistent")
    monkeypatch.setattr(common, "load_excluded_trials", lambda: {})

    candidates = common.trial_candidates("13", include_archive=False)
    assert len(candidates) == 1
    assert candidates[0]["status"] == "unparseable"


def test_trial_candidates_classifies_excluded_with_reason(tmp_path, monkeypatch):
    rec_dir = tmp_path / "Participant_13_left_pre"
    rec_dir.mkdir(parents=True)
    csv_path = rec_dir / "trial_1_optitrack.csv"
    csv_path.write_text("t,angle\n0,180\n")
    monkeypatch.setattr(common, "OPTI_ROOT", str(tmp_path))
    monkeypatch.setattr(common, "ARCHIVE_ROOT", "/nonexistent")
    monkeypatch.setattr(common, "load_excluded_trials",
                        lambda: {"13_left_pre_T1": "active muscle intervention"})

    candidates = common.trial_candidates("13", include_archive=False)
    assert len(candidates) == 1
    assert candidates[0]["status"] == "excluded"
    assert candidates[0]["reason"] == "active muscle intervention"


def test_trial_candidates_classifies_unreadable(tmp_path, monkeypatch):
    rec_dir = tmp_path / "Participant_13_left_pre"
    rec_dir.mkdir(parents=True)
    csv_path = rec_dir / "trial_1_optitrack.csv"
    csv_path.write_text("not,a,valid,optitrack,csv\n")
    monkeypatch.setattr(common, "OPTI_ROOT", str(tmp_path))
    monkeypatch.setattr(common, "ARCHIVE_ROOT", "/nonexistent")
    monkeypatch.setattr(common, "load_excluded_trials", lambda: {})
    monkeypatch.setattr(common.pt, "load_optitrack", lambda path: (_ for _ in ()).throw(ValueError("bad csv")))

    candidates = common.trial_candidates("13", include_archive=False)
    assert len(candidates) == 1
    assert candidates[0]["status"] == "unreadable"


def test_trial_candidates_classifies_unscoreable_and_scored(tmp_path, monkeypatch):
    import numpy as np
    rec_dir = tmp_path / "Participant_13_left_pre"
    rec_dir.mkdir(parents=True)
    (rec_dir / "trial_1_optitrack.csv").write_text("t,angle\n0,180\n")
    monkeypatch.setattr(common, "OPTI_ROOT", str(tmp_path))
    monkeypatch.setattr(common, "ARCHIVE_ROOT", "/nonexistent")
    monkeypatch.setattr(common, "load_excluded_trials", lambda: {})
    monkeypatch.setattr(common.pt, "load_optitrack",
                        lambda path: (np.array([0.0, 1.0]), np.array([180.0, 179.0])))
    monkeypatch.setattr(common, "score_trial", lambda pid, trial, t, angle: None)

    candidates = common.trial_candidates("13", include_archive=False)
    assert len(candidates) == 1
    assert candidates[0]["status"] == "unscoreable"

    monkeypatch.setattr(common, "score_trial",
                        lambda pid, trial, t, angle: {"pid": pid, "trial": trial, "pt7": 0.5})
    candidates = common.trial_candidates("13", include_archive=False)
    assert candidates[0]["status"] == "scored"
    assert candidates[0]["record"]["pt7"] == 0.5


def test_trial_candidates_only_this_participant(tmp_path, monkeypatch):
    for pid in ("13", "14"):
        rec_dir = tmp_path / f"Participant_{pid}_left_pre"
        rec_dir.mkdir(parents=True)
        (rec_dir / "trial_1_optitrack.csv").write_text("t,angle\n0,180\n")
    monkeypatch.setattr(common, "OPTI_ROOT", str(tmp_path))
    monkeypatch.setattr(common, "ARCHIVE_ROOT", "/nonexistent")
    monkeypatch.setattr(common, "load_excluded_trials", lambda: {})
    monkeypatch.setattr(common.pt, "load_optitrack", lambda path: (_ for _ in ()).throw(ValueError()))

    candidates = common.trial_candidates("13", include_archive=False)
    assert len(candidates) == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_pt_report_common.py -k trial_candidates -v`
Expected: FAIL — `AttributeError: module 'pt_report_common' has no attribute 'trial_candidates'`.

- [ ] **Step 3: Implement**

Add after `load_excluded_trials` (after line 193 of `pt_report_common.py`, before `discover_all_trials`):

```python
def trial_candidates(participant_id, include_archive=True):
    """Every trial_*_optitrack.csv discovered for this participant, kept
    regardless of exclusion/validity/scoreability, each tagged with a
    terminal status -- the data source for the full report's data-
    completeness caption line (2026-08-10 full-report-hpe-accuracy design
    spec §5.6). Standalone from discover_all_trials()/collect_participant()
    by design (see this function's task in the implementation plan for
    why) -- does its own raw glob rather than filtering their output, so
    it can preserve candidates they intentionally drop (INVALID paths,
    excluded trials) plus files _parse_trial_path() can't attribute to a
    participant/leg at all (status "unparseable").

    Never raises -- matches this module's other discovery/loader
    conventions. status is one of:
      "unparseable"  -- _parse_trial_path() returned None
      "invalid_path" -- "INVALID" in the path
      "excluded"     -- key present in load_excluded_trials(); reason set
      "unreadable"   -- pt.load_optitrack() raised
      "unscoreable"  -- score_trial() returned None
      "scored"       -- record set to the scored trial dict
    """
    excluded = load_excluded_trials()
    out = []
    seen = set()
    roots = [OPTI_ROOT] + ([ARCHIVE_ROOT] if include_archive and os.path.isdir(ARCHIVE_ROOT) else [])
    for root in roots:
        for csv_path in glob.glob(os.path.join(root, "**", "trial_*_optitrack.csv"), recursive=True):
            real = os.path.realpath(csv_path)
            if real in seen:
                continue
            seen.add(real)

            if "INVALID" in csv_path.upper():
                rec = _parse_trial_path(csv_path, root)
                if rec is None or rec["participant"] != participant_id:
                    if rec is not None and rec["participant"] != participant_id:
                        continue
                out.append({"leg": None, "condition": None, "trial": None, "path": csv_path,
                           "status": "invalid_path", "reason": None, "record": None})
                continue

            rec = _parse_trial_path(csv_path, root)
            if rec is None:
                # Can't attribute to a participant at all -- can't filter
                # by participant_id either, so every unparseable file in
                # the tree is included regardless of which pid was asked
                # for. Callers building a per-participant completeness
                # tally should treat this bucket as tree-wide, not
                # per-participant, and say so in the UI copy.
                out.append({"leg": None, "condition": None, "trial": None, "path": csv_path,
                           "status": "unparseable", "reason": None, "record": None})
                continue
            if rec["participant"] != participant_id:
                continue

            key = trial_key(rec["participant"], rec["leg"], rec["condition"], rec["trial"])
            if key in excluded:
                out.append({"leg": rec["leg"], "condition": rec["condition"], "trial": rec["trial"],
                           "path": csv_path, "status": "excluded", "reason": excluded[key], "record": None})
                continue

            try:
                t, angle = pt.load_optitrack(csv_path)
            except Exception:
                out.append({"leg": rec["leg"], "condition": rec["condition"], "trial": rec["trial"],
                           "path": csv_path, "status": "unreadable", "reason": None, "record": None})
                continue

            pid_key = f"{participant_id}_{rec['leg']}_{rec['condition']}"
            record = score_trial(pid_key, rec["trial"], t, angle)
            if record is None:
                out.append({"leg": rec["leg"], "condition": rec["condition"], "trial": rec["trial"],
                           "path": csv_path, "status": "unscoreable", "reason": None, "record": None})
            else:
                out.append({"leg": rec["leg"], "condition": rec["condition"], "trial": rec["trial"],
                           "path": csv_path, "status": "scored", "reason": None, "record": record})
    return out
```

`import glob` and `import os` are already present at the top of `pt_report_common.py` (used by `discover_all_trials` already) — no new imports needed.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_pt_report_common.py -k trial_candidates -v`
Expected: PASS.

- [ ] **Step 5: Run the full existing test suite**

Run: `.venv\Scripts\python.exe -m pytest tests/test_pt_report_common.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add pt_report_common.py tests/test_pt_report_common.py
git commit -m "feat: add trial_candidates for full data-completeness accounting"
```

---

## Task 5: `clinician_mas_matches()` — local-import lookup into `mas_scores.csv`

**Files:**
- Modify: `pt_report_common.py` (add new function; a good location is right after `trial_candidates`, added in Task 4)
- Test: `tests/test_pt_report_common.py`

**Interfaces:**
- Consumes: `mas_validation.MAS_CSV`, `mas_validation.load_mas_scores()`, `mas_validation._tokenize_condition()`, `mas_validation._valid_grade()` — all existing, unchanged, imported **locally inside the function**, not at module scope (see rationale below).
- Produces: `clinician_mas_matches(participant_id, leg, condition) -> list[dict]`, sorted most-recent-first by parsed `assessed_date` (real date parsing, blank/unparseable dates sort last — see Step 3), each item the original `mas_scores.csv` row dict (with `assessed_date` still the original string). Empty list if `mas_scores.csv` doesn't exist or nothing matches.

**Why the import must be local, not at the top of `pt_report_common.py`:** `mas_validation.py` already does `import pt_report_common as common` at module scope (`mas_validation.py:51`). A module-scope `import mas_validation` in `pt_report_common.py` would be circular. A function-local import is safe because by the time this function is ever called (during report generation, long after both modules finished their own initial load), Python's module cache already has both fully initialized — verified directly: this is standard, well-established Python behavior for breaking an otherwise-circular import, not a workaround specific to this codebase.

- [ ] **Step 1: Write the failing tests**

```python
def test_clinician_mas_matches_local_import_does_not_raise(tmp_path, monkeypatch):
    """Regression test for the circular-import risk: calling this function
    must not raise ImportError/circular-import errors."""
    empty_csv = tmp_path / "mas_scores.csv"
    empty_csv.write_text("participant,leg,condition,diagnosis,mas_grade,assessed_by,assessed_date\n")
    import mas_validation as mv
    monkeypatch.setattr(mv, "MAS_CSV", str(empty_csv))
    result = common.clinician_mas_matches("13", "left", "pre")
    assert result == []


def test_clinician_mas_matches_returns_all_matches_sorted_by_date(tmp_path, monkeypatch):
    csv_path = tmp_path / "mas_scores.csv"
    csv_path.write_text(
        "participant,leg,condition,diagnosis,mas_grade,assessed_by,assessed_date\n"
        "13,left,pre,MS,1,VL,8/6/2026\n"
        "13,left,pre,MS,1+,VL,12/1/2026\n"
    )
    import mas_validation as mv
    monkeypatch.setattr(mv, "MAS_CSV", str(csv_path))

    result = common.clinician_mas_matches("13", "left", "pre")
    assert len(result) == 2
    # 12/1/2026 is chronologically AFTER 8/6/2026 -- lexicographic string
    # sort would get this backwards ("1" < "8"). Most-recent-first means
    # the 12/1/2026 row comes first.
    assert result[0]["assessed_date"] == "12/1/2026"
    assert result[1]["assessed_date"] == "8/6/2026"


def test_clinician_mas_matches_blank_date_sorts_last(tmp_path, monkeypatch):
    csv_path = tmp_path / "mas_scores.csv"
    csv_path.write_text(
        "participant,leg,condition,diagnosis,mas_grade,assessed_by,assessed_date\n"
        "13,left,pre,MS,1,VL,\n"
        "13,left,pre,MS,1+,VL,8/6/2026\n"
    )
    import mas_validation as mv
    monkeypatch.setattr(mv, "MAS_CSV", str(csv_path))

    result = common.clinician_mas_matches("13", "left", "pre")
    assert result[0]["assessed_date"] == "8/6/2026"
    assert result[1]["assessed_date"] == ""


def test_clinician_mas_matches_excludes_invalid_grade(tmp_path, monkeypatch):
    csv_path = tmp_path / "mas_scores.csv"
    csv_path.write_text(
        "participant,leg,condition,diagnosis,mas_grade,assessed_by,assessed_date\n"
        "13,left,pre,MS,not_a_grade,VL,8/6/2026\n"
    )
    import mas_validation as mv
    monkeypatch.setattr(mv, "MAS_CSV", str(csv_path))

    assert common.clinician_mas_matches("13", "left", "pre") == []


def test_clinician_mas_matches_condition_bag_of_tokens(tmp_path, monkeypatch):
    csv_path = tmp_path / "mas_scores.csv"
    csv_path.write_text(
        "participant,leg,condition,diagnosis,mas_grade,assessed_by,assessed_date\n"
        "13,left,1 week post,MS,2,VL,8/6/2026\n"
    )
    import mas_validation as mv
    monkeypatch.setattr(mv, "MAS_CSV", str(csv_path))

    # "week_1_post" and "1 week post" tokenize to the same set.
    result = common.clinician_mas_matches("13", "left", "week_1_post")
    assert len(result) == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_pt_report_common.py -k clinician_mas_matches -v`
Expected: FAIL — `AttributeError: module 'pt_report_common' has no attribute 'clinician_mas_matches'`.

- [ ] **Step 3: Implement**

Add to `pt_report_common.py`, after `trial_candidates`:

```python
def _parse_mas_assessed_date(date_str):
    """mas_scores.csv's assessed_date column is free-text M/D/YYYY (e.g.
    "8/6/2026"), not zero-padded or ISO -- lexicographic string sort would
    silently misorder "12/1/2026" before "8/6/2026". Returns a
    datetime.date, or None for blank/unparseable (sorted last by callers,
    never raised)."""
    import datetime
    date_str = (date_str or "").strip()
    if not date_str:
        return None
    try:
        return datetime.datetime.strptime(date_str, "%m/%d/%Y").date()
    except ValueError:
        return None


def clinician_mas_matches(participant_id, leg, condition):
    """All valid mas_scores.csv rows for this participant/leg/condition,
    sorted most-recent-first by assessed_date. Local import of
    mas_validation -- see this function's docstring in the implementation
    plan for why it can't be a module-scope import (mas_validation.py
    already imports pt_report_common). Reuses mas_validation's own
    bag-of-tokens condition matching and grade validation rather than
    reimplementing them, so this stays consistent with
    mas_validation.py's own pair_pt_and_mas()/_pt_lookup_factory()."""
    import mas_validation as mv
    if not os.path.isfile(mv.MAS_CSV):
        return []
    wanted = mv._tokenize_condition(condition)
    rows = mv.load_mas_scores(mv.MAS_CSV)
    matches = [r for r in rows
              if r.get("participant") == participant_id
              and r.get("leg") == leg
              and mv._tokenize_condition(r.get("condition", "")) == wanted
              and mv._valid_grade(r.get("mas_grade", ""))]
    matches.sort(key=lambda r: _parse_mas_assessed_date(r.get("assessed_date")) or datetime.date.min,
                reverse=True)
    return matches
```

Add `import datetime` to `pt_report_common.py`'s top-level imports (near the existing `import os`, `import re` block) since the sort key above uses `datetime.date.min` at module scope of the function — alternatively keep the `import datetime` local to `_parse_mas_assessed_date` (already done above) and add a second local `import datetime` inside `clinician_mas_matches` right before the `matches.sort(...)` line, to avoid a top-level import purely for one internal default value. Use the local-import approach for both functions, consistent with this task's local-import theme:

```python
def clinician_mas_matches(participant_id, leg, condition):
    import datetime
    import mas_validation as mv
    if not os.path.isfile(mv.MAS_CSV):
        return []
    wanted = mv._tokenize_condition(condition)
    rows = mv.load_mas_scores(mv.MAS_CSV)
    matches = [r for r in rows
              if r.get("participant") == participant_id
              and r.get("leg") == leg
              and mv._tokenize_condition(r.get("condition", "")) == wanted
              and mv._valid_grade(r.get("mas_grade", ""))]
    matches.sort(key=lambda r: _parse_mas_assessed_date(r.get("assessed_date")) or datetime.date.min,
                reverse=True)
    return matches
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_pt_report_common.py -k clinician_mas_matches -v`
Expected: PASS.

- [ ] **Step 5: Run the full existing test suite, including mas_validation's own tests**

Run: `.venv\Scripts\python.exe -m pytest tests/test_pt_report_common.py tests/test_mas_validation.py -v`
Expected: PASS — confirms the local import didn't break `mas_validation.py`'s own module-scope import of `pt_report_common`.

- [ ] **Step 6: Commit**

```bash
git add pt_report_common.py tests/test_pt_report_common.py
git commit -m "feat: add clinician_mas_matches with local import to avoid circularity"
```

---

## Task 6: `write_clinician_mas_sidecar()` — the complete-record CSV

**Files:**
- Modify: `pt_report_common.py` (add after `clinician_mas_matches`, Task 5)
- Test: `tests/test_pt_report_common.py`

**Interfaces:**
- Consumes: `clinician_mas_matches()` (Task 5) output shape, `OUT_DIR` (existing module constant).
- Produces: `write_clinician_mas_sidecar(participant_id, matches_by_leg_condition, out_dir=None) -> str`. `matches_by_leg_condition` is `{(leg, condition): list[dict]}` (the caller — Task 10 — already has this shape from calling `clinician_mas_matches()` per leg/condition while building Row 5). Writes `P{participant_id}_clinician_mas.csv` under `out_dir` (defaults to `OUT_DIR`, matching every other artifact writer in this module) with every match, untruncated — the figure (Task 10) may show only the 2 most recent, but this file is always the complete record. Returns the written path.

- [ ] **Step 1: Write the failing test**

```python
def test_write_clinician_mas_sidecar_writes_every_match(tmp_path):
    matches_by_leg_condition = {
        ("left", "pre"): [
            {"participant": "13", "leg": "left", "condition": "pre", "mas_grade": "1",
            "assessed_by": "VL", "assessed_date": "12/1/2026"},
            {"participant": "13", "leg": "left", "condition": "pre", "mas_grade": "1+",
            "assessed_by": "VL", "assessed_date": "8/6/2026"},
        ],
        ("right", "pre"): [
            {"participant": "13", "leg": "right", "condition": "pre", "mas_grade": "2",
            "assessed_by": "VL", "assessed_date": "8/6/2026"},
        ],
    }
    out_path = common.write_clinician_mas_sidecar("13", matches_by_leg_condition, out_dir=str(tmp_path))

    import csv
    with open(out_path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 3
    assert {r["mas_grade"] for r in rows} == {"1", "1+", "2"}


def test_write_clinician_mas_sidecar_empty_matches_still_writes_header(tmp_path):
    out_path = common.write_clinician_mas_sidecar("13", {}, out_dir=str(tmp_path))
    import csv
    with open(out_path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert rows == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_pt_report_common.py -k clinician_mas_sidecar -v`
Expected: FAIL — function doesn't exist yet.

- [ ] **Step 3: Implement**

```python
def write_clinician_mas_sidecar(participant_id, matches_by_leg_condition, out_dir=None):
    """Complete, untruncated clinician-MAS record for this participant --
    Row 5 of the full report (see _draw_row5_table) may only show the 2
    most recent matches per leg/condition for density reasons, but this
    file always has every match, so 'all relevant participant data is
    included' is true of the report's full output (figure + sidecar),
    not just the PNG in isolation. Same pattern this codebase already
    uses for the MS-vs-Control cohort artifacts (ms_vs_control_stats.csv
    as the complete record, the PNG as a bounded summary)."""
    out_dir = out_dir or OUT_DIR
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, f"P{participant_id}_clinician_mas.csv")
    fieldnames = ["participant", "leg", "condition", "diagnosis", "mas_grade",
                 "assessed_by", "assessed_date"]
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        for matches in matches_by_leg_condition.values():
            for row in matches:
                w.writerow(row)
    return out_path
```

`import csv` — check whether `pt_report_common.py` already imports `csv` at the top (it doesn't appear in the file's current import block based on earlier reads — `os`, `re`, `sys`, `glob`, `json`, `matplotlib`, `numpy` are the imports seen so far). Add `import csv` to the top-level import block if not already present.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_pt_report_common.py -k clinician_mas_sidecar -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add pt_report_common.py tests/test_pt_report_common.py
git commit -m "feat: add write_clinician_mas_sidecar for the complete MAS record"
```

---

## Task 7: `pt_cohort_common.py` — cohort snapshot builder + leave-one-out reference

**Files:**
- Modify: `pt_cohort_common.py:346-370` (`_collect_arm_data`), and add new functions after `run_cohort_comparison` (after line 424)
- Test: `tests/test_pt_cohort_common.py`

**Interfaces:**
- Consumes: `classify_participant`, `aggregate_participant_summary`, `compute_cohort_stats`, `current_qualifying_participants`, `build_composition_rows`, `common.collect_participant` — all existing, unchanged.
- Produces:
  - `_collect_arm_data(pids) -> (summaries, raw_trials, contributing_pids, summaries_by_pid)` — same first three return values as today, **plus** a 4th: `summaries_by_pid: {(pid, leg): dict | None}`, needed for leave-one-out cohort references (a plain per-arm list, today's existing shape, has no pid attached to each summary).
  - `build_cohort_snapshot() -> dict` — the schema below.
  - `write_cohort_artifacts(snapshot)` — writes `cohort_composition.csv`, `ms_vs_control_stats.csv`, `ms_vs_control_boxplots.png` from an already-built snapshot, no rescanning.
  - `run_cohort_comparison()` — becomes `write_cohort_artifacts(build_cohort_snapshot())`, preserving today's exact external behavior so every existing test in `tests/test_pt_cohort_common.py` (5 tests directly call `pcc.run_cohort_comparison()`) keeps passing unchanged.
  - `leg_cohort_reference(snapshot, participant_id, leg) -> dict | None` — `{"ms_median": float|None, "ms_n": int, "control_median": float|None, "control_n": int, "leave_one_out_arm": "MS"|"Control"|None}`, or `None` if the snapshot has no comparison (either arm empty).

`build_cohort_snapshot()`'s schema (verified directly against `make_cohort_comparison_figure()`'s and `write_composition_csv()`'s existing parameter lists at lines 296-305, 431-433, so `write_cohort_artifacts` can call them with zero recollection):

```python
{
    "composition_rows": [...],               # build_composition_rows() output
    "ms_pids": [...], "control_pids": [...],
    "ms_summaries": {...} | None, "control_summaries": {...} | None,
    "ms_raw": {...} | None, "control_raw": {...} | None,
    "summaries_by_pid": {...},                # {} when either arm is empty
    "ms_n_participants": int | None, "ms_n_trials": int | None,
    "control_n_participants": int | None, "control_n_trials": int | None,
    "stats_rows": [...] | None,
    "n_excluded_unclassified": int,
}
```

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_pt_cohort_common.py` (check its existing imports/monkeypatch conventions at the top first — it imports `pt_cohort_common as pcc` per the file's existing test names like `pcc.run_cohort_comparison()`):

```python
def test_collect_arm_data_returns_summaries_by_pid(monkeypatch):
    fake_by_leg_tp = {
        ("left", "pre"): [{"pid": "13_left_pre", "trial": "1", "pt7": 0.3,
                          "R2n": 0.9, "N": 3.0, "phi_max_ratio": 0.5, "omega_max_n": 1.0,
                          "omega_min_n": 0.2, "f": 1.5, "area_ratio": 0.1}],
        ("right", "pre"): [],
    }
    monkeypatch.setattr(pcc.common, "collect_participant", lambda pid: (fake_by_leg_tp, []))

    summaries, raw_trials, contributing_pids, summaries_by_pid = pcc._collect_arm_data(["13"])

    assert summaries_by_pid[("13", "left")] is not None
    assert summaries_by_pid[("13", "left")]["pt7"] == 0.3
    assert summaries_by_pid[("13", "right")] is None


def test_build_cohort_snapshot_skipped_when_arm_empty(monkeypatch):
    monkeypatch.setattr(pcc, "current_qualifying_participants", lambda: {"13"})
    monkeypatch.setattr(pcc, "build_composition_rows",
                        lambda pids: [{"pid": "13", "group": "MS", "source": "metadata",
                                      "diagnosis": "MS", "n_trials_left": 4, "n_trials_right": 4}])

    snapshot = pcc.build_cohort_snapshot()

    assert snapshot["ms_pids"] == ["13"]
    assert snapshot["control_pids"] == []
    assert snapshot["stats_rows"] is None
    assert snapshot["ms_summaries"] is None


def test_write_cohort_artifacts_no_recollection_when_arm_empty(monkeypatch, tmp_path):
    """write_cohort_artifacts must render entirely from the snapshot --
    patch collect_participant to raise if it's ever called from within
    this function, proving no rescanning happens."""
    monkeypatch.setattr(pcc.common, "collect_participant",
                        lambda pid: (_ for _ in ()).throw(AssertionError("should not recollect")))
    monkeypatch.setattr(pcc, "COMPOSITION_CSV", str(tmp_path / "cohort_composition.csv"))
    snapshot = {
        "composition_rows": [{"pid": "13", "group": "MS", "source": "metadata", "diagnosis": "MS",
                             "n_trials_left": 4, "n_trials_right": 4}],
        "ms_pids": ["13"], "control_pids": [], "ms_summaries": None, "control_summaries": None,
        "ms_raw": None, "control_raw": None, "summaries_by_pid": {},
        "ms_n_participants": None, "ms_n_trials": None,
        "control_n_participants": None, "control_n_trials": None,
        "stats_rows": None, "n_excluded_unclassified": 0,
    }
    pcc.write_cohort_artifacts(snapshot)   # must not raise


def test_run_cohort_comparison_still_works_as_combinator(monkeypatch, tmp_path):
    """run_cohort_comparison() must remain callable exactly as today's
    tests already call it -- this is the back-compat contract for the 5
    existing tests in this file that call pcc.run_cohort_comparison()
    directly."""
    monkeypatch.setattr(pcc, "current_qualifying_participants", lambda: set())
    monkeypatch.setattr(pcc, "COMPOSITION_CSV", str(tmp_path / "cohort_composition.csv"))
    pcc.run_cohort_comparison()   # must not raise, same as before this task


def test_leg_cohort_reference_leave_one_out_for_own_arm(monkeypatch):
    snapshot = {
        "ms_pids": ["13", "14"], "control_pids": ["6", "7"],
        "summaries_by_pid": {
            ("13", "left"): {"pt7": 0.30}, ("14", "left"): {"pt7": 0.50},
            ("6", "left"): {"pt7": 0.10}, ("7", "left"): {"pt7": 0.20},
        },
    }
    ref = pcc.leg_cohort_reference(snapshot, "13", "left")
    # MS arm excludes participant 13 -> only 14's 0.50 remains.
    assert ref["ms_median"] == 0.50
    assert ref["ms_n"] == 1
    # Control arm is untouched -- participant 13 isn't in it.
    assert ref["control_median"] == 0.15   # median of 0.10, 0.20
    assert ref["control_n"] == 2
    assert ref["leave_one_out_arm"] == "MS"


def test_leg_cohort_reference_none_when_not_comparable():
    assert pcc.leg_cohort_reference({"ms_pids": ["13"], "control_pids": []}, "13", "left") is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_pt_cohort_common.py -k "collect_arm_data_returns_summaries_by_pid or build_cohort_snapshot or write_cohort_artifacts or leg_cohort_reference or still_works_as_combinator" -v`
Expected: FAIL — `_collect_arm_data` returns a 3-tuple today, unpacking 4 values raises `ValueError`; `build_cohort_snapshot`/`write_cohort_artifacts`/`leg_cohort_reference` don't exist.

- [ ] **Step 3: Implement**

First, update `_collect_arm_data` (lines 346-370 of `pt_cohort_common.py`):

```python
def _collect_arm_data(pids):
    """pids -> (summaries, raw_trials, contributing_pids, summaries_by_pid).
    summaries / raw_trials: {"left": [...], "right": [...]}. summaries holds
    one aggregate_participant_summary() dict per participant that had at
    least one scored trial for that leg (the statistical layer --
    compute_cohort_stats and the figure's box/whiskers read only from
    this). raw_trials holds every individual scored trial record (the
    figure's descriptive-layer background jitter only -- never used for
    a statistic). contributing_pids is the post-filter participant set,
    which can be smaller than `pids` itself (see aggregate_participant_
    summary's None case, design spec §7.2 step 4). summaries_by_pid is
    {(pid, leg): summary|None} for every pid in `pids` -- needed by
    leg_cohort_reference() to compute a leave-one-out median when the
    report's own participant is a member of the arm being shown as their
    reference; the plain list in `summaries` has no pid attached to each
    entry, so it can't support excluding one participant."""
    summaries = {"left": [], "right": []}
    raw_trials = {"left": [], "right": []}
    contributing_pids = set()
    summaries_by_pid = {}
    for pid in pids:
        by_leg_tp, _ = common.collect_participant(pid)
        for leg in _LEGS:
            trials = [r for (leg_key, _cond), recs in by_leg_tp.items()
                     if leg_key == leg for r in recs]
            raw_trials[leg].extend(trials)
            summary = aggregate_participant_summary(trials)
            summaries_by_pid[(pid, leg)] = summary
            if summary is not None:
                summaries[leg].append(summary)
                contributing_pids.add(pid)
    return summaries, raw_trials, contributing_pids, summaries_by_pid
```

Now update `run_cohort_comparison`'s call site (it currently unpacks 3 values at two call sites — lines 414-415 in the original) — this will be replaced entirely by the new functions below, so no separate patch needed there; `run_cohort_comparison` is being rewritten in this same step.

Add the new functions after `run_cohort_comparison` (replace the whole existing `run_cohort_comparison` function body, lines 396-424, with the three functions below — `build_cohort_snapshot`, `write_cohort_artifacts`, and a thin `run_cohort_comparison` that combines them):

```python
def build_cohort_snapshot():
    """I/O-bearing snapshot builder (reads participant_groups.json/
    metadata.json, calls discovery and collect_participant() -- writes
    nothing). Not a pure function in the no-I/O sense, only in the
    no-side-effects sense. Returns a single snapshot dict used by both
    per-participant reports (pt_report_common.make_report_figure(), via
    leg_cohort_reference()) and the end-of-run cohort artifacts
    (write_cohort_artifacts()), so the two never rescan independently and
    diverge within one run_pt_analysis.py invocation. Takes no arguments
    -- always recomputes the full qualifying set (design spec §6.1)."""
    pids = current_qualifying_participants()
    rows = build_composition_rows(pids)
    n_excluded_unclassified = sum(1 for r in rows if r["group"] in ("Excluded", "Unclassified"))

    ms_pids = [r["pid"] for r in rows if r["group"] == "MS"]
    control_pids = [r["pid"] for r in rows if r["group"] == "Control"]

    if not ms_pids or not control_pids:
        return {
            "composition_rows": rows, "ms_pids": ms_pids, "control_pids": control_pids,
            "ms_summaries": None, "control_summaries": None,
            "ms_raw": None, "control_raw": None, "summaries_by_pid": {},
            "ms_n_participants": None, "ms_n_trials": None,
            "control_n_participants": None, "control_n_trials": None,
            "stats_rows": None, "n_excluded_unclassified": n_excluded_unclassified,
        }

    ms_summaries, ms_raw, ms_contrib, ms_by_pid = _collect_arm_data(ms_pids)
    control_summaries, control_raw, control_contrib, control_by_pid = _collect_arm_data(control_pids)
    stats_rows = compute_cohort_stats(ms_summaries, control_summaries)

    return {
        "composition_rows": rows, "ms_pids": ms_pids, "control_pids": control_pids,
        "ms_summaries": ms_summaries, "control_summaries": control_summaries,
        "ms_raw": ms_raw, "control_raw": control_raw,
        "summaries_by_pid": {**ms_by_pid, **control_by_pid},
        "ms_n_participants": len(ms_contrib), "ms_n_trials": sum(len(v) for v in ms_raw.values()),
        "control_n_participants": len(control_contrib),
        "control_n_trials": sum(len(v) for v in control_raw.values()),
        "stats_rows": stats_rows, "n_excluded_unclassified": n_excluded_unclassified,
    }


def write_cohort_artifacts(snapshot):
    """Writes cohort_composition.csv (always), and ms_vs_control_stats.csv
    / ms_vs_control_boxplots.png (only when both arms are non-empty) from
    an already-built snapshot -- zero rediscovery, zero recollection.
    Renamed from today's run_cohort_comparison(), which now only writes
    artifacts from a snapshot instead of recomputing one."""
    write_composition_csv(snapshot["composition_rows"])
    print_composition_banner(snapshot["composition_rows"])

    if not snapshot["ms_pids"] or not snapshot["control_pids"]:
        print(f"Cohort comparison skipped: {len(snapshot['ms_pids'])} MS / "
             f"{len(snapshot['control_pids'])} Control qualifying participants "
             f"(need >=1 in each arm).")
        return

    write_stats_csv(snapshot["stats_rows"], STATS_CSV)
    make_cohort_comparison_figure(
        snapshot["ms_summaries"], snapshot["ms_raw"],
        snapshot["ms_n_participants"], snapshot["ms_n_trials"],
        snapshot["control_summaries"], snapshot["control_raw"],
        snapshot["control_n_participants"], snapshot["control_n_trials"],
        snapshot["n_excluded_unclassified"], FIGURE_PNG, snapshot["stats_rows"])


def run_cohort_comparison():
    """Back-compat combinator: build_cohort_snapshot() + write_cohort_artifacts().
    Kept so every existing caller/test that calls run_cohort_comparison()
    directly keeps working unchanged. run_pt_analysis.py's main() (Task 8
    of the implementation plan) calls the two halves directly instead, so
    it can also pass the snapshot into per-participant reports before the
    artifacts are written."""
    write_cohort_artifacts(build_cohort_snapshot())


def leg_cohort_reference(snapshot, participant_id, leg):
    """{"ms_median", "ms_n", "control_median", "control_n",
    "leave_one_out_arm"} for one leg, using leave-one-out on whichever arm
    `participant_id` itself belongs to (small cohorts make an inclusive
    median partially self-referential -- design spec §5.6). Returns None
    when the snapshot has no comparison at all (either arm empty)."""
    if not snapshot["ms_pids"] or not snapshot["control_pids"]:
        return None

    def _median_excluding(arm_pids, exclude_pid):
        vals = [snapshot["summaries_by_pid"][(pid, leg)]["pt7"]
                for pid in arm_pids
                if pid != exclude_pid and snapshot["summaries_by_pid"].get((pid, leg)) is not None]
        return (float(np.median(vals)) if vals else None), len(vals)

    is_ms = participant_id in snapshot["ms_pids"]
    is_control = participant_id in snapshot["control_pids"]
    ms_median, ms_n = _median_excluding(snapshot["ms_pids"], participant_id if is_ms else None)
    control_median, control_n = _median_excluding(snapshot["control_pids"], participant_id if is_control else None)
    return {"ms_median": ms_median, "ms_n": ms_n,
           "control_median": control_median, "control_n": control_n,
           "leave_one_out_arm": "MS" if is_ms else ("Control" if is_control else None)}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_pt_cohort_common.py -v`
Expected: PASS — including every pre-existing test in the file (the 5 `run_cohort_comparison()` tests must still pass unchanged, proving the combinator preserves behavior).

- [ ] **Step 5: Commit**

```bash
git add pt_cohort_common.py tests/test_pt_cohort_common.py
git commit -m "feat: split cohort comparison into build_cohort_snapshot + write_cohort_artifacts"
```

---

## Task 8: RMSE plotting extracted into a shared helper

**Files:**
- Modify: `pt_report_common.py:602-660` (`make_rmse_figure`)
- Test: `tests/test_pt_report_common.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `_draw_rmse_axes(ax, leg, by_leg_tp, timepoints, methodologies=("mediapipe", "imu")) -> bool` (returns whether any bars were drawn, same boolean `make_rmse_figure` already tracks as `any_bars`). `make_rmse_figure()` keeps its exact existing signature and behavior, now implemented by calling this helper per leg — used by both the standalone `P{pid}_rmse.png` and the new Row 4 (Task 11).

- [ ] **Step 1: Write the failing test**

```python
def test_draw_rmse_axes_returns_true_when_bars_drawn():
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots()
    rec = {"trial": "1", "mediapipe_rmse": 3.2, "imu_rmse": None}
    by_leg_tp = {"left": [rec]}
    timepoints = [("pre", "Pre", "#d62728")]

    any_bars = common._draw_rmse_axes(ax, "left", {("left", "pre"): [rec]}, timepoints)

    assert any_bars is True
    plt.close(fig)


def test_draw_rmse_axes_returns_false_when_no_data():
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots()
    any_bars = common._draw_rmse_axes(ax, "left", {}, [("pre", "Pre", "#d62728")])
    assert any_bars is False
    plt.close(fig)


def test_make_rmse_figure_unchanged_behavior(tmp_path, monkeypatch):
    """Regression: make_rmse_figure()'s own external contract (return
    shape, output file) must be identical after the extraction."""
    monkeypatch.setattr(common, "OUT_DIR", str(tmp_path))
    rec = {"trial": "1", "mediapipe_rmse": 3.2, "imu_rmse": 4.1}
    by_leg_tp = {("left", "pre"): [rec], ("right", "pre"): []}
    timepoints = [("pre", "Pre", "#d62728")]

    out_path, any_bars = common.make_rmse_figure("P13", by_leg_tp, timepoints, "P13_rmse.png")

    assert any_bars is True
    assert out_path == str(tmp_path / "P13_rmse.png") or out_path == os.path.join(str(tmp_path), "P13_rmse.png")
    assert os.path.isfile(out_path)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_pt_report_common.py -k "draw_rmse_axes or make_rmse_figure_unchanged" -v`
Expected: FAIL — `_draw_rmse_axes` doesn't exist yet (the `make_rmse_figure_unchanged` test should currently pass against the un-refactored function; run it first standalone to confirm baseline behavior before refactoring).

- [ ] **Step 3: Implement**

Read the current `make_rmse_figure` at `pt_report_common.py:602-660` in full before editing (its body is the per-leg loop this task extracts). Replace it with:

```python
def _draw_rmse_axes(ax, leg, by_leg_tp, timepoints, methodologies=("mediapipe", "imu")):
    """Per-trial RMSE bars vs OptiTrack for one leg's axes -- extracted
    from make_rmse_figure() so both the standalone P{pid}_rmse.png and
    Row 4 of the full report (make_report_figure(), Task 11) draw from
    the exact same code and can never drift apart. Returns whether any
    bars were drawn."""
    ax.set_facecolor('#f8f9fa')
    ax.grid(True, color=BG_GRID, linestyle='-', linewidth=0.8, axis='y')

    bar_records = []
    for tp_key, tp_label, _ in timepoints:
        for rec in by_leg_tp.get((leg, tp_key), []):
            has_mp = "mediapipe" in methodologies and rec.get("mediapipe_rmse") is not None
            has_imu = "imu" in methodologies and rec.get("imu_rmse") is not None
            if has_mp or has_imu:
                bar_records.append((tp_label, rec))

    any_bars = False
    if bar_records:
        any_bars = True
        labels = [f"{tp}\nT{rec['trial']}" for tp, rec in bar_records]
        xpos = np.arange(len(bar_records))
        width = 0.36
        if "mediapipe" in methodologies:
            vals = [rec.get("mediapipe_rmse", np.nan) if rec.get("mediapipe_rmse") is not None else np.nan
                   for _, rec in bar_records]
            ax.bar(xpos - width / 2, vals, width, color=_C_MEDIAPIPE, label="OptiTrack vs MediaPipe", zorder=3)
        if "imu" in methodologies:
            vals = [rec.get("imu_rmse", np.nan) if rec.get("imu_rmse") is not None else np.nan
                   for _, rec in bar_records]
            ax.bar(xpos + width / 2, vals, width, color=_C_IMU, label="OptiTrack vs IMU (Viewer)", zorder=3)
        ax.set_xticks(xpos)
        ax.set_xticklabels(labels, fontsize=7.5)
        ax.legend(loc='upper left', fontsize=8, framealpha=0.9)
    else:
        ax.text(0.5, 0.5, "No MediaPipe/IMU comparison data found\nfor the selected methodology",
               transform=ax.transAxes, ha='center', va='center', color='#888888', fontsize=10)
        ax.set_xticks([])

    ax.set_ylabel('RMSE (deg)', fontsize=8)
    ax.tick_params(labelsize=8)
    return any_bars


def make_rmse_figure(participant_label, by_leg_tp, timepoints, out_filename,
                     methodologies=("mediapipe", "imu"), save=True, return_fig=False):
    """1x2 grid (Left, Right): per-trial RMSE bars vs OptiTrack, for whichever
    of MediaPipe/IMU data is actually available. `methodologies` filters
    which series to show even if both were found. Unchanged external
    behavior from before the _draw_rmse_axes extraction (Task 8 of the
    implementation plan)."""
    attach_rmse(by_leg_tp)
    fig, axes = plt.subplots(1, 2, figsize=(15, 5.5), facecolor='white')
    any_bars = False

    for col_idx, (leg, leg_label) in enumerate((("left", "Left"), ("right", "Right"))):
        ax = axes[col_idx]
        bars_this_leg = _draw_rmse_axes(ax, leg, by_leg_tp, timepoints, methodologies)
        any_bars = any_bars or bars_this_leg
        ax.set_title(f'{participant_label} {leg_label} – RMSE vs OptiTrack', fontsize=10, fontweight='bold', pad=10)

    fig.suptitle(f"{participant_label} — Flexion-Angle RMSE Agreement (lower = better)",
                fontsize=11, y=1.0, color='#333333')
    plt.tight_layout(rect=[0, 0, 1, 0.94])
    out_path = None
    if save:
        out_path = os.path.join(OUT_DIR, out_filename)
        fig.savefig(out_path, dpi=150, facecolor='white')
        print(f"-> {out_path}")
    if return_fig:
        return out_path, fig
    plt.close(fig)
    return out_path, any_bars
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_pt_report_common.py -k "draw_rmse_axes or make_rmse_figure" -v`
Expected: PASS.

- [ ] **Step 5: Run the full existing test suite**

Run: `.venv\Scripts\python.exe -m pytest tests/test_pt_report_common.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add pt_report_common.py tests/test_pt_report_common.py
git commit -m "refactor: extract _draw_rmse_axes shared by make_rmse_figure and Row 4"
```

---

## Task 9: Row 1 waveform overlay — HPE/IMU curves on the existing OptiTrack trace

**Files:**
- Modify: `pt_report_common.py:326-343` (Row 1 of `make_report_figure`'s `for tp_key, tp_label, color in timepoints:` waveform loop)
- Test: `tests/test_pt_report_common.py`

**Interfaces:**
- Consumes: `rec["mediapipe_curve"]`/`rec["imu_curve"]` (Task 2), `release_aligned_hpe_curve()` (Task 3).
- Produces: no new function — this task modifies Row 1's existing plotting loop in place. Documented here so Task 12 (grid wiring) can reference the exact change.

This task is deferred to be applied as part of Task 12's `make_report_figure()` rewrite, since Row 1's loop is inside that function and Task 12 already needs to touch the whole function for the grid-size change. Combining them avoids editing the same ~15-line block twice. **This task's tests are written now** (against a small extracted helper, so they don't depend on Task 12 landing first) and Task 12 wires the helper into the real Row 1 loop.

Extract a small pure helper first:

```python
def _hpe_overlay_series(rec):
    """For one trial record (after attach_rmse), returns
    [(source_label, linestyle, t_plot, a_plot), ...] for whichever of
    mediapipe_curve/imu_curve are present and release-align successfully.
    OptiTrack itself is plotted separately by the existing Row 1 loop
    (unchanged) -- this only covers the two overlay sources."""
    out = []
    for key, label, linestyle in (("mediapipe_curve", "MediaPipe", "--"), ("imu_curve", "IMU", ":")):
        curve = rec.get(key)
        if curve is None:
            continue
        aligned = release_aligned_hpe_curve(curve["t"], curve["ang"])
        if aligned is None:
            continue
        t_plot, a_plot = aligned
        out.append((label, linestyle, t_plot, a_plot))
    return out
```

- [ ] **Step 1: Write the failing tests**

```python
def test_hpe_overlay_series_includes_both_sources_when_present():
    import numpy as np
    t = np.linspace(0, 3, 90)
    ang = np.where(t < 1.0, 180.0, 180.0 - 30.0 * np.sin((t - 1.0) * 2))
    rec = {"mediapipe_curve": {"t": t, "ang": ang}, "imu_curve": {"t": t, "ang": ang}}

    series = common._hpe_overlay_series(rec)

    labels = {s[0] for s in series}
    assert labels == {"MediaPipe", "IMU"}
    linestyles = {s[0]: s[1] for s in series}
    assert linestyles["MediaPipe"] == "--"
    assert linestyles["IMU"] == ":"


def test_hpe_overlay_series_skips_missing_curve():
    rec = {"mediapipe_curve": None, "imu_curve": None}
    assert common._hpe_overlay_series(rec) == []


def test_hpe_overlay_series_skips_alignment_failure(monkeypatch):
    import numpy as np
    rec = {"mediapipe_curve": {"t": np.array([0.0]), "ang": np.array([180.0])}, "imu_curve": None}
    # Single-sample curve fails release_aligned_hpe_curve's own validation (< 4 samples).
    series = common._hpe_overlay_series(rec)
    assert series == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_pt_report_common.py -k hpe_overlay_series -v`
Expected: FAIL — function doesn't exist.

- [ ] **Step 3: Implement**

Add `_hpe_overlay_series` (shown above) to `pt_report_common.py`, placed after `release_aligned_hpe_curve` (Task 3).

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_pt_report_common.py -k hpe_overlay_series -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add pt_report_common.py tests/test_pt_report_common.py
git commit -m "feat: add _hpe_overlay_series helper for Row 1 waveform overlay"
```

---

## Task 10: Row 5 — PT7/MAS source-agreement table

**Files:**
- Modify: `pt_report_common.py` (add new function, used by Task 12)
- Test: `tests/test_pt_report_common.py`

**Interfaces:**
- Consumes: `pt.load_hpe_model_curves(..., return_rejected=True)` (Task 1), `_hpe_overlay_series`-equivalent candidate data already attached by `attach_rmse` (Task 2, via `rec["mediapipe_curve"]`/`rec["imu_curve"]`), `pt.compute_pt_params`/`pt.compute_pt_score`/`pt.pt_to_mas` (existing, unchanged), `clinician_mas_matches()` (Task 5).
- Produces: `_draw_row5_table(ax, leg, by_leg_tp, timepoints, participant_id) -> dict[tuple, list[dict]]`. Draws the table onto `ax` and returns `matches_by_leg_condition` (the clinician-MAS matches actually used, in the exact shape `write_clinician_mas_sidecar` — Task 6 — expects), so Task 12 can call the sidecar writer once per leg with this task's own output rather than recomputing matches a second time.

For each timepoint (row) and this leg, per source (MediaPipe, IMU):
- Compute PT7 from the source's own curve (`rec["mediapipe_curve"]`/`rec["imu_curve"]`) via `pt.compute_pt_params` + `pt.compute_pt_score`, only when both exist and produce a non-`None` result.
- Compute a per-source OptiTrack-paired PT7: the mean OptiTrack `pt7` across only the trials in this timepoint that have a scoreable curve for *that* source (MediaPipe's paired baseline and IMU's paired baseline are independent — they may include different trial subsets).
- **Three real stages, via a fresh `load_hpe_model_curves(..., return_rejected=True)` call per trial** — deliberately NOT read from `rec["mediapipe_curve"]`/`rec["imu_curve"]` for the accounting counts (those are set by `attach_rmse`, Task 2, which only ever calls `load_hpe_model_curves` with the `return_rejected=False` default and so only ever sees post-gate survivors — reusing them here would silently collapse "no candidate CSV" and "candidate CSV rejected by the quality gate" into the same count, exactly the bug Task 1 exists to let a caller distinguish). Stage 1 (`n_candidate`): name-matching entry in either the `accepted` or `rejected` list. Stage 2 (`n_passed_gate`): name-matching entry in `accepted`. Stage 3 (`n_scored`): `_row5_source_pt7()` (reading `rec["mediapipe_curve"]`/`rec["imu_curve"]`, which is fine to reuse for the curve *data itself*, just not for gate accounting) returns non-`None`.

- [ ] **Step 1: Write the failing tests**

```python
def test_draw_row5_table_shows_per_source_paired_baselines(monkeypatch):
    import numpy as np
    import matplotlib.pyplot as plt

    t = np.linspace(0, 3, 90)
    ang = np.where(t < 1.0, 180.0, 180.0 - 30.0 * np.sin((t - 1.0) * 2))

    def fake_pt7(params):
        return 0.25

    monkeypatch.setattr(common.pt, "compute_pt_params", lambda t_, a_: {"fake": True})
    monkeypatch.setattr(common.pt, "compute_pt_score", fake_pt7)
    monkeypatch.setattr(common.pt, "pt_to_mas", lambda score: "1")
    monkeypatch.setattr(common.pt, "load_hpe_model_curves",
                        lambda *a, **k: ([{"name": "mediapipe", "t": t, "ang": ang, "rmse": 2.0}], []))
    monkeypatch.setattr(common, "clinician_mas_matches", lambda pid, leg, cond: [])

    rec = {"pid": "13_left_pre", "trial": "1", "pt7": 0.30, "t_raw": t, "angle_raw": ang,
          "neutral_deg_raw": 180.0, "mediapipe_curve": {"t": t, "ang": ang}, "imu_curve": None}
    by_leg_tp = {("left", "pre"): [rec]}
    timepoints = [("pre", "Pre", "#d62728")]

    fig, ax = plt.subplots()
    matches = common._draw_row5_table(ax, "left", by_leg_tp, timepoints, "13")

    assert isinstance(matches, dict)
    plt.close(fig)


def test_draw_row5_table_three_stage_accounting_distinguishes_gate_from_scoring(monkeypatch):
    """The whole point of Task 1's return_rejected mode: a trial can have a
    candidate CSV that gets REJECTED by the quality gate (never reaches
    attach_rmse's rec["mediapipe_curve"] at all), which must show up as
    "had a candidate" but NOT "passed gate" -- distinct from a trial with
    zero candidate CSVs at all. This regression-tests that _draw_row5_table
    actually calls load_hpe_model_curves(return_rejected=True) itself
    rather than only reading attach_rmse's already-filtered rec["mediapipe_curve"]."""
    import numpy as np
    import matplotlib.pyplot as plt

    t = np.linspace(0, 3, 90)
    ang = np.where(t < 1.0, 180.0, 180.0 - 30.0 * np.sin((t - 1.0) * 2))
    call_log = []

    def fake_load_curves(pid, pos, trial, t_opti, angle_raw, neutral_deg, csv_files=None, return_rejected=False):
        call_log.append(return_rejected)
        assert return_rejected is True, "Row 5 must request rejection accounting, not the default"
        if trial == "1":
            return [], [{"name": "mediapipe", "reason": "did_not_track_swing"}]
        return [], []

    monkeypatch.setattr(common.pt, "load_hpe_model_curves", fake_load_curves)
    monkeypatch.setattr(common, "clinician_mas_matches", lambda pid, leg, cond: [])

    rec_rejected = {"pid": "13_left_pre", "trial": "1", "pt7": 0.30, "t_raw": t, "angle_raw": ang,
                    "neutral_deg_raw": 180.0, "mediapipe_curve": None, "imu_curve": None}
    rec_no_candidate = {"pid": "13_left_pre", "trial": "2", "pt7": 0.32, "t_raw": t, "angle_raw": ang,
                        "neutral_deg_raw": 180.0, "mediapipe_curve": None, "imu_curve": None}
    by_leg_tp = {("left", "pre"): [rec_rejected, rec_no_candidate]}
    timepoints = [("pre", "Pre", "#d62728")]

    fig, ax = plt.subplots()
    common._draw_row5_table(ax, "left", by_leg_tp, timepoints, "13")

    assert True in call_log   # confirms return_rejected=True was actually requested
    plt.close(fig)


def test_draw_row5_table_returns_clinician_matches_used(monkeypatch):
    import matplotlib.pyplot as plt
    fake_matches = [{"participant": "13", "leg": "left", "condition": "pre", "mas_grade": "1",
                    "assessed_by": "VL", "assessed_date": "8/6/2026"}]
    monkeypatch.setattr(common, "clinician_mas_matches", lambda pid, leg, cond: fake_matches)
    monkeypatch.setattr(common.pt, "load_hpe_model_curves", lambda *a, **k: ([], []))

    rec = {"pid": "13_left_pre", "trial": "1", "pt7": 0.30, "t_raw": [0.0], "angle_raw": [180.0],
          "neutral_deg_raw": 180.0, "mediapipe_curve": None, "imu_curve": None}
    by_leg_tp = {("left", "pre"): [rec]}
    timepoints = [("pre", "Pre", "#d62728")]

    fig, ax = plt.subplots()
    matches = common._draw_row5_table(ax, "left", by_leg_tp, timepoints, "13")

    assert matches[("left", "pre")] == fake_matches
    plt.close(fig)


def test_draw_row5_table_empty_timepoints_does_not_raise():
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots()
    matches = common._draw_row5_table(ax, "left", {}, [], "13")
    assert matches == {}
    plt.close(fig)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_pt_report_common.py -k draw_row5_table -v`
Expected: FAIL — function doesn't exist.

- [ ] **Step 3: Implement**

```python
def _row5_source_pt7(rec, curve_key):
    """PT7 (7-param, same compute_pt_score used for OptiTrack everywhere
    else in this module) computed directly from one source's own curve on
    this trial record, or None if the curve is missing or fails to
    score. curve_key is "mediapipe_curve" or "imu_curve" (set by
    attach_rmse, Task 2)."""
    curve = rec.get(curve_key)
    if curve is None:
        return None
    params = pt.compute_pt_params(curve["t"], curve["ang"])
    if params is None:
        return None
    return pt.compute_pt_score(params)


def _draw_row5_table(ax, leg, by_leg_tp, timepoints, participant_id):
    """PT7/MAS source-agreement table for one leg: OptiTrack vs MediaPipe
    and OptiTrack vs IMU, each with its OWN paired OptiTrack baseline
    (not one shared number -- MediaPipe and IMU can pass the quality gate
    on different trial subsets), three-stage missing/rejected/unscored
    accounting, and clinician MAS shown at most 2-most-recent with a
    '+N more' note (full record goes to the sidecar CSV -- see
    write_clinician_mas_sidecar, Task 6). Returns
    {(leg, condition): matches_used} so the caller can write the sidecar
    from the exact matches this table displayed, without recomputing."""
    ax.axis("off")
    matches_by_leg_condition = {}
    rows = []

    for tp_key, tp_label, color in timepoints:
        trials = by_leg_tp.get((leg, tp_key), [])
        if not trials:
            continue

        # Per-trial gate accounting, fetched fresh with return_rejected=True
        # -- attach_rmse's rec["mediapipe_curve"]/rec["imu_curve"] only
        # carries POST-gate survivors, so re-deriving "had a candidate at
        # all" from those keys would conflate "no candidate CSV" with
        # "candidate CSV existed but was rejected by the quality gate",
        # exactly the distinction Task 1's return_rejected mode exists to
        # preserve. One call per trial, reused for both sources below.
        gate_by_trial = {}
        for r in trials:
            try:
                accepted, rejected = pt.load_hpe_model_curves(
                    r["pid"], "1", r["trial"], r["t_raw"], r["angle_raw"], r["neutral_deg_raw"],
                    return_rejected=True)
            except Exception:
                accepted, rejected = [], []
            gate_by_trial[id(r)] = (accepted, rejected)

        for source_label, curve_key, name_matches in (
            ("MediaPipe", "mediapipe_curve", lambda n: n.startswith("mediapipe")),
            ("IMU", "imu_curve", lambda n: n == "imu_viewer"),
        ):
            n_candidate = 0
            n_passed_gate = 0
            for r in trials:
                accepted, rejected = gate_by_trial[id(r)]
                has_accepted = any(name_matches(c["name"]) for c in accepted)
                has_rejected = any(name_matches(c["name"]) for c in rejected)
                if has_accepted or has_rejected:
                    n_candidate += 1
                if has_accepted:
                    n_passed_gate += 1

            source_pt7s = [_row5_source_pt7(r, curve_key) for r in trials if r.get(curve_key) is not None]
            n_scored = sum(1 for v in source_pt7s if v is not None)
            paired_opti = [r["pt7"] for r in trials if r.get(curve_key) is not None]
            opti_paired_mean = float(np.mean(paired_opti)) if paired_opti else None
            source_mean = float(np.mean([v for v in source_pt7s if v is not None])) if n_scored else None
            delta = (source_mean - opti_paired_mean) if (source_mean is not None and opti_paired_mean is not None) else None

            rows.append({
                "timepoint": tp_label, "source": source_label,
                "opti_paired_pt7": opti_paired_mean, "opti_paired_n": len(paired_opti),
                "source_pt7": source_mean, "source_mas": pt.pt_to_mas(source_mean) if source_mean is not None else None,
                "delta": delta,
                "n_candidate": n_candidate, "n_passed_gate": n_passed_gate,
                "n_total": len(trials), "n_scored": n_scored,
            })

        matches = clinician_mas_matches(participant_id, leg, tp_key)
        matches_by_leg_condition[(leg, tp_key)] = matches

    if not rows:
        ax.text(0.5, 0.5, "No timepoints available", transform=ax.transAxes,
               ha="center", va="center", color="#888888", fontsize=9)
        return matches_by_leg_condition

    def _fmt_pt7(v):
        return f"{v:.3f}" if v is not None else "—"

    def _fmt_delta(v):
        return f"{v:+.3f}" if v is not None else "—"

    caveat = ("Algorithm agreement, not independent clinical scores — PT7 computed identically "
             "across sources but on curves with different sampling, smoothing, and cleaning "
             "characteristics.")
    ax.text(0.0, 1.0, caveat, transform=ax.transAxes, fontsize=6.5, style="italic",
           color="#666666", ha="left", va="top", wrap=True)

    table_rows = []
    for r in rows:
        clin = matches_by_leg_condition.get((leg, next(tp for tp, lbl, _ in timepoints if lbl == r["timepoint"])), [])
        clin_shown = clin[:2]
        clin_txt = "; ".join(f"{m['mas_grade']} ({m.get('assessed_date') or 'date n/a'})" for m in clin_shown)
        if len(clin) > 2:
            clin_txt += f" +{len(clin) - 2} more"
        table_rows.append([
            r["timepoint"], r["source"],
            f"{_fmt_pt7(r['opti_paired_pt7'])} (n={r['opti_paired_n']})",
            f"{_fmt_pt7(r['source_pt7'])}" + (f" [{r['source_mas']}]" if r["source_mas"] else ""),
            _fmt_delta(r["delta"]),
            f"{r['n_candidate']}/{r['n_total']} had candidate, "
            f"{r['n_passed_gate']}/{r['n_candidate'] or 1} passed gate, "
            f"{r['n_scored']}/{r['n_passed_gate'] or 1} scored",
            clin_txt or "—",
        ])

    tbl = ax.table(cellText=table_rows,
                   colLabels=["Timepoint", "Source", "OptiTrack (paired)", "Source PT7 [MAS]",
                             "Δ", "Accounting", "Clinician MAS"],
                   loc="center", cellLoc="center")
    tbl.auto_set_font_size(False)
    tbl.set_fontsize(6.5)
    tbl.scale(1, 1.4)
    return matches_by_leg_condition
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_pt_report_common.py -k draw_row5_table -v`
Expected: PASS.

- [ ] **Step 5: Run the full existing test suite**

Run: `.venv\Scripts\python.exe -m pytest tests/test_pt_report_common.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add pt_report_common.py tests/test_pt_report_common.py
git commit -m "feat: add Row 5 PT7/MAS source-agreement table"
```

---

## Task 11: Caption assembly — cohort reference and data-completeness lines

**Files:**
- Modify: `pt_report_common.py` (add new function, used by Task 12)
- Test: `tests/test_pt_report_common.py`

**Interfaces:**
- Consumes: `leg_cohort_reference()` (Task 7, from `pt_cohort_common`), `trial_candidates()` (Task 4).
- Produces: `_build_caption_text(participant_label, participant_id, by_leg_tp, timepoints, cohort_snapshot) -> str`. `cohort_snapshot` may be `None` (e.g. a standalone call outside `run_pt_analysis.py`'s wired flow) — in that case the cohort-reference lines are simply omitted, not an error.

- [ ] **Step 1: Write the failing tests**

```python
def test_build_caption_text_includes_cohort_reference_when_snapshot_present(monkeypatch):
    import pt_cohort_common as pcc
    fake_ref = {"ms_median": 0.41, "ms_n": 2, "control_median": 0.15, "control_n": 3,
               "leave_one_out_arm": "MS"}
    monkeypatch.setattr(pcc, "leg_cohort_reference", lambda snap, pid, leg: fake_ref)
    monkeypatch.setattr(common, "trial_candidates", lambda pid, include_archive=True: [])

    text = common._build_caption_text("P13", "13", {("left", "pre"): []}, [("pre", "Pre", "#d62728")],
                                      cohort_snapshot={"ms_pids": ["13"], "control_pids": ["6"]})

    assert "MS arm median" in text
    assert "0.41" in text
    assert "leave-one-out" in text


def test_build_caption_text_omits_cohort_reference_when_snapshot_none():
    text = common._build_caption_text("P13", "13", {("left", "pre"): []}, [("pre", "Pre", "#d62728")],
                                      cohort_snapshot=None)
    assert "MS arm median" not in text


def test_build_caption_text_includes_data_completeness(monkeypatch):
    fake_candidates = [
        {"leg": "left", "condition": "pre", "status": "scored"},
        {"leg": "left", "condition": "pre", "status": "scored"},
        {"leg": "left", "condition": "pre", "status": "excluded", "reason": "active muscle"},
        {"leg": "left", "condition": "pre", "status": "unreadable"},
    ]
    monkeypatch.setattr(common, "trial_candidates", lambda pid, include_archive=True: fake_candidates)

    text = common._build_caption_text("P13", "13", {("left", "pre"): []}, [("pre", "Pre", "#d62728")],
                                      cohort_snapshot=None)

    assert "Left/pre" in text or "Left/Pre" in text
    assert "2 scored" in text
    assert "1 excluded" in text
    assert "1 unreadable" in text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_pt_report_common.py -k build_caption_text -v`
Expected: FAIL — function doesn't exist.

- [ ] **Step 3: Implement**

```python
def _build_caption_text(participant_label, participant_id, by_leg_tp, timepoints, cohort_snapshot):
    """Caption block below the full-report figure: leg-specific MS/Control
    cohort reference (leave-one-out for the participant's own arm, if
    any) plus a per-leg/condition data-completeness tally. Every `n` is
    explicitly labeled by what it counts -- never a bare number.
    cohort_snapshot=None (or either arm empty) simply omits the cohort
    lines rather than raising."""
    lines = []

    if cohort_snapshot is not None:
        import pt_cohort_common as pcc
        for leg in ("left", "right"):
            own_trials = [r for (leg_key, _cond), recs in by_leg_tp.items()
                         if leg_key == leg for r in recs]
            if not own_trials:
                continue
            own_pt7 = float(np.mean([r["pt7"] for r in own_trials]))
            ref = pcc.leg_cohort_reference(cohort_snapshot, participant_id, leg)
            if ref is None:
                continue
            leg_label = leg.capitalize()
            parts = [f"{leg_label} leg — this participant PT7 (n={len(own_trials)} trials): {own_pt7:.2f}"]
            ms_suffix = " (leave-one-out)" if ref["leave_one_out_arm"] == "MS" else ""
            control_suffix = " (leave-one-out)" if ref["leave_one_out_arm"] == "Control" else ""
            ms_txt = f"{ref['ms_median']:.2f}" if ref["ms_median"] is not None else "n/a"
            control_txt = f"{ref['control_median']:.2f}" if ref["control_median"] is not None else "n/a"
            parts.append(f"MS arm median (n={ref['ms_n']} contributing participants{ms_suffix}): {ms_txt}")
            parts.append(f"Control arm median (n={ref['control_n']}{control_suffix}): {control_txt}")
            lines.append(" | ".join(parts))

    candidates = trial_candidates(participant_id)
    by_leg_condition = {}
    for c in candidates:
        if c["leg"] is None:
            continue
        key = (c["leg"], c["condition"])
        by_leg_condition.setdefault(key, {"recorded": 0, "excluded": 0, "unreadable": 0,
                                          "unscoreable": 0, "scored": 0})
        if c["status"] in ("excluded",):
            by_leg_condition[key]["excluded"] += 1
        elif c["status"] == "unreadable":
            by_leg_condition[key]["unreadable"] += 1
        elif c["status"] == "unscoreable":
            by_leg_condition[key]["unscoreable"] += 1
        elif c["status"] == "scored":
            by_leg_condition[key]["scored"] += 1
        if c["status"] != "invalid_path":
            by_leg_condition[key]["recorded"] += 1

    plotted_keys = {(leg_key, cond_key) for (leg_key, cond_key) in by_leg_tp.keys()}
    for (leg, cond), tally in sorted(by_leg_condition.items()):
        if (leg, cond) not in plotted_keys:
            continue
        lines.append(f"{leg.capitalize()}/{cond}: {tally['recorded']} recorded, "
                     f"{tally['excluded']} excluded, {tally['unreadable']} unreadable, "
                     f"{tally['unscoreable']} unscoreable, {tally['scored']} scored")

    return "\n".join(lines)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_pt_report_common.py -k build_caption_text -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add pt_report_common.py tests/test_pt_report_common.py
git commit -m "feat: add caption assembly with cohort reference and data completeness"
```

---

## Task 12: `make_report_figure()` grid growth — wire everything into the 5×2 report

**Files:**
- Modify: `pt_report_common.py:303-423` (`make_report_figure`)
- Test: `tests/test_pt_report_common.py`

**Interfaces:**
- Consumes: everything from Tasks 1–11.
- Produces: `make_report_figure(participant_label, by_leg_tp, timepoints, out_filename, caveat_text, cohort_snapshot=None, save=True, return_fig=False)` — new optional `cohort_snapshot=None` parameter (default preserves today's call sites in `run_pt_analysis.py`, `p13_full_report.py`, `p5_full_report.py` until Task 13 updates `run_pt_analysis.py` to pass one). Grid grows from `plt.subplots(3, 2, ...)` to `plt.subplots(5, 2, ...)`. Row 1 gains HPE/IMU overlays (via `_hpe_overlay_series`, Task 9). Row 2/3 unchanged. Row 4 becomes `_draw_rmse_axes` (Task 8) per leg. Row 5 becomes `_draw_row5_table` (Task 10) per leg, followed by `write_clinician_mas_sidecar` (Task 6) using its returned matches. Caption text from `_build_caption_text` (Task 11) is added via `fig.text(...)` below the grid. `figsize` grows from `(15, 13)` to `(15, 21)` to fit two more rows without cramping the existing three.

- [ ] **Step 1: Write the failing tests**

```python
def test_make_report_figure_5x2_grid_with_all_rows(tmp_path, monkeypatch):
    import numpy as np
    monkeypatch.setattr(common, "OUT_DIR", str(tmp_path))
    monkeypatch.setattr(common.pt, "load_hpe_model_curves", lambda *a, **k: ([], []))
    monkeypatch.setattr(common, "clinician_mas_matches", lambda pid, leg, cond: [])
    monkeypatch.setattr(common, "trial_candidates", lambda pid, include_archive=True: [])

    t = np.linspace(0, 3, 90)
    ang = np.where(t < 1.0, 180.0, 180.0 - 30.0 * np.sin((t - 1.0) * 2))
    rec = {"pid": "13_left_pre", "trial": "1", "pt7": 0.3, "t_raw": t, "angle_raw": ang,
          "neutral_deg_raw": 180.0, "R2n": 0.9, "N": 3.0, "phi_max_ratio": 0.5,
          "omega_max_n": 1.0, "omega_min_n": 0.2, "f": 1.5, "area_ratio": 0.1,
          "mediapipe_curve": None, "imu_curve": None, "mediapipe_rmse": None, "imu_rmse": None}
    by_leg_tp = {("left", "pre"): [rec], ("right", "pre"): []}
    timepoints = [("pre", "Pre", "#d62728")]

    out_path, fig = common.make_report_figure("P13", by_leg_tp, timepoints, "P13_full_report.png",
                                               "test caveat", cohort_snapshot=None,
                                               save=False, return_fig=True)

    assert len(fig.axes) >= 10   # 5 rows x 2 cols, at minimum (table axes count too)
    import matplotlib.pyplot as plt
    plt.close(fig)


def test_make_report_figure_default_cohort_snapshot_none_does_not_raise(tmp_path, monkeypatch):
    """Existing callers (p13_full_report.py, p5_full_report.py,
    run_pt_analysis.py before Task 13) don't pass cohort_snapshot -- the
    default must keep working exactly as before this task."""
    monkeypatch.setattr(common, "OUT_DIR", str(tmp_path))
    monkeypatch.setattr(common.pt, "load_hpe_model_curves", lambda *a, **k: ([], []))
    monkeypatch.setattr(common, "clinician_mas_matches", lambda pid, leg, cond: [])
    monkeypatch.setattr(common, "trial_candidates", lambda pid, include_archive=True: [])

    out_path = common.make_report_figure("P13", {}, [], "P13_full_report.png", "caveat")
    assert out_path == os.path.join(str(tmp_path), "P13_full_report.png")
    assert os.path.isfile(out_path)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_pt_report_common.py -k "make_report_figure_5x2 or make_report_figure_default_cohort" -v`
Expected: FAIL — `cohort_snapshot` isn't an accepted keyword yet, grid is still 3×2.

- [ ] **Step 3: Implement**

Read `make_report_figure` at `pt_report_common.py:303-423` in full before editing (this is the largest single edit in the plan). Apply these changes to the existing function body:

1. Signature (line 303-304):

```python
def make_report_figure(participant_label, by_leg_tp, timepoints, out_filename, caveat_text,
                       cohort_snapshot=None, save=True, return_fig=False):
```

2. Grid creation (line 308):

```python
    fig, axes = plt.subplots(5, 2, figsize=(15, 21), facecolor='white')
```

3. Row 1's waveform loop (lines 326-343) — inside the existing `for tp_key, tp_label, color in timepoints:` loop, immediately after the existing `ax.plot(t_plot, a_plot, color=color, linewidth=1.5, label=f'{tp_label} (PT={rep["pt7"]:.2f}, T{rep["trial"]})')` line, add:

```python
            for source_label, linestyle, hpe_t, hpe_a in _hpe_overlay_series(rep):
                ax.plot(hpe_t, hpe_a, color=color, linewidth=1.1, linestyle=linestyle,
                       alpha=0.85, label=f'{tp_label} {source_label}')
```

Rows 2 and 3 (the parameter bars and PT-score trend, currently at `axes[1, col_idx]` / `axes[2, col_idx]`) are unchanged — no edits.

4. After the existing Row 3 block closes (end of the `for col_idx, (leg, leg_label) in enumerate(...)` loop, right before the `fig.suptitle(...)` call), add Row 4 and Row 5:

```python
    for col_idx, (leg, leg_label) in enumerate((("left", "Left"), ("right", "Right"))):
        ax4 = axes[3, col_idx]
        _draw_rmse_axes(ax4, leg, by_leg_tp, timepoints)
        ax4.set_title(f'{participant_label} {leg_label} – RMSE vs OptiTrack', fontsize=10, fontweight='bold', pad=10)

        ax5 = axes[4, col_idx]
        matches_used = _draw_row5_table(ax5, leg, by_leg_tp, timepoints, participant_label.lstrip("P"))
        if matches_used:
            write_clinician_mas_sidecar(participant_label.lstrip("P"), matches_used)
        ax5.set_title(f'{participant_label} {leg_label} – PT7/MAS Source Agreement', fontsize=10, fontweight='bold', pad=10)
```

(`participant_label.lstrip("P")` matches this module's own convention — `run_pt_analysis.py`'s `label = f"P{pid}"` — so passing `"P13"` back out as `"13"` recovers the raw participant id `clinician_mas_matches`/`write_clinician_mas_sidecar` expect. This assumes `participant_label` always has the `P{pid}` shape every existing call site already uses; document this assumption in a comment.)

5. Caption text — replace the existing `fig.suptitle(...)` call (near the end of the function) with the suptitle plus a new caption block:

```python
    fig.suptitle(f"{participant_label} — Full Report (7-parameter Popovic PT score)\n{caveat_text}",
                fontsize=10, y=0.998, color='#333333')
    caption = _build_caption_text(participant_label, participant_label.lstrip("P"), by_leg_tp, timepoints, cohort_snapshot)
    if caption:
        fig.text(0.02, 0.005, caption, fontsize=6.5, color="#444444", va="bottom", ha="left")
    plt.tight_layout(rect=[0, 0.04, 1, 0.975])
```

(The `rect` bottom margin grows from `0.0` to `0.04` to leave room for the caption text; the top margin `0.965` → `0.975` is a minor adjustment for the taller 5-row figure — both values are visual tuning to verify in Task 13's manual run, not hard requirements.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_pt_report_common.py -k "make_report_figure" -v`
Expected: PASS.

- [ ] **Step 5: Run the full existing test suite**

Run: `.venv\Scripts\python.exe -m pytest tests/test_pt_report_common.py -v`
Expected: PASS — every test in the file, including all tasks' additions and every test that existed before this plan started.

- [ ] **Step 6: Commit**

```bash
git add pt_report_common.py tests/test_pt_report_common.py
git commit -m "feat: grow make_report_figure to 5x2 grid with HPE overlay, Row 4/5, caption"
```

---

## Task 13: Wire `run_pt_analysis.py` to build and thread the cohort snapshot

**Files:**
- Modify: `run_pt_analysis.py:37-131` (imports, `run_for_participant`, `main`)
- Test: `tests/test_pt_report_common.py` (existing file already imports `run_pt_analysis`, per Task 1's conventions)

**Interfaces:**
- Consumes: `pt_cohort_common.build_cohort_snapshot()`, `pt_cohort_common.write_cohort_artifacts()` (Task 7), `pt_report_common.make_report_figure(..., cohort_snapshot=...)` (Task 12).
- Produces: `run_for_participant(pid, cohort_snapshot=None)` — new optional parameter, threaded through to `make_report_figure`. `main()` calls `build_cohort_snapshot()` once at the top (before the per-participant loop), passes it to every `run_for_participant()` call, and calls `write_cohort_artifacts(snapshot)` at the end instead of the old `run_cohort_comparison()` call.

- [ ] **Step 1: Write the failing test**

```python
def test_main_builds_cohort_snapshot_once_before_participant_loop(monkeypatch):
    calls = []
    monkeypatch.setattr(run_pt_analysis.common, "list_participants", lambda: {"13": {}})
    monkeypatch.setattr(run_pt_analysis, "run_for_participant",
                        lambda pid, cohort_snapshot=None: calls.append(("run_for_participant", pid, cohort_snapshot)) or [])
    fake_snapshot = {"ms_pids": [], "control_pids": []}
    monkeypatch.setattr(run_pt_analysis.pt_cohort_common, "build_cohort_snapshot",
                        lambda: (calls.append(("build_cohort_snapshot",)) or fake_snapshot))
    monkeypatch.setattr(run_pt_analysis.pt_cohort_common, "write_cohort_artifacts",
                        lambda snap: calls.append(("write_cohort_artifacts", snap)))
    monkeypatch.setattr(run_pt_analysis, "_mas_scored_participants", lambda: set())
    monkeypatch.setattr(sys, "argv", ["run_pt_analysis.py"])

    run_pt_analysis.main()

    assert calls[0] == ("build_cohort_snapshot",)
    assert calls[1] == ("run_for_participant", "13", fake_snapshot)
    assert calls[2] == ("write_cohort_artifacts", fake_snapshot)
```

Add `import sys` to the test file if not already present (check first — most test files in this repo already import `sys` per the `sys.path.insert` boilerplate at the top).

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/test_pt_report_common.py -k main_builds_cohort_snapshot -v`
Expected: FAIL — `run_pt_analysis.run_for_participant` doesn't accept `cohort_snapshot`, `main()` doesn't call `build_cohort_snapshot`/`write_cohort_artifacts` in that order yet.

- [ ] **Step 3: Implement**

In `run_pt_analysis.py`, change `run_for_participant` (line 47) to accept and thread the snapshot:

```python
def run_for_participant(pid, cohort_snapshot=None):
    counts = leg_trial_counts(pid)
    first_ts = common.first_recording_time(pid)
    if first_ts is None:
        print(f"P{pid}: no discoverable trials yet. Skipping.")
        return []

    elapsed = time.time() - first_ts
    if elapsed < READY_AFTER_SECONDS:
        print(f"P{pid}: right={counts['right']} left={counts['left']} trials -- "
              f"first recording landed {elapsed / 60:.1f} min ago, waiting "
              f"{(READY_AFTER_SECONDS - elapsed) / 60:.1f} more min before analyzing. Skipping.")
        return []

    print(f"P{pid}: right={counts['right']} left={counts['left']} trials -- "
          f"{elapsed / 60:.0f} min since first recording, generating figures...")
    by_leg_tp, timepoints = common.collect_participant(pid)
    label = f"P{pid}"
    outputs = []

    outputs.append(common.make_report_figure(
        label, by_leg_tp, timepoints, f"P{pid}_full_report.png",
        caveat_text=f"Auto-generated {READY_AFTER_SECONDS // 60} min after the first recording "
                   f"landed, from whatever trials were available at that point "
                   f"(right={counts['right']}, left={counts['left']}).",
        cohort_snapshot=cohort_snapshot))
```

(The rest of `run_for_participant`'s body — the `make_rmse_figure` call and the reference-participant comparison loop — is unchanged; only the `make_report_figure` call gains the new keyword argument.)

Add `import pt_cohort_common` near the top of `run_pt_analysis.py` if it isn't already imported at module scope — check line 37 area first (the file already has `import pt_cohort_common` at line 37 per the earlier read of this file, so this import likely already exists; if so, skip adding it).

Change `main()` (lines 104-126):

```python
def main():
    if len(sys.argv) > 1:
        pids = [sys.argv[1]]
    else:
        pids = list(common.list_participants().keys())

    cohort_snapshot = pt_cohort_common.build_cohort_snapshot()

    qualified = set()
    for pid in pids:
        if run_for_participant(pid, cohort_snapshot=cohort_snapshot):
            qualified.add(pid)

    ready_for_mas = qualified & _mas_scored_participants()
    if ready_for_mas:
        print(f"{len(ready_for_mas)} participant(s) now have both trial data and MAS scores on file "
             f"-- run mas_validation.py to refresh the validation report.")

    try:
        pt_cohort_common.write_cohort_artifacts(cohort_snapshot)
    except Exception as e:
        # A malformed hand-edit to participant_groups.json (or any other
        # cohort-comparison failure) shouldn't take down the whole run --
        # the per-participant reports above already succeeded.
        print(f"Cohort comparison failed: {e}")
```

Note: `build_cohort_snapshot()` itself is now called *unguarded* (before the `try`), while `write_cohort_artifacts()` stays inside the `try`. This is deliberate — if snapshot-building itself fails (e.g. a malformed `participant_groups.json`), per-participant reports should still get *some* cohort context rather than none, so wrap the snapshot build in its own try too, falling back to `None`:

```python
    try:
        cohort_snapshot = pt_cohort_common.build_cohort_snapshot()
    except Exception as e:
        print(f"Cohort snapshot build failed, reports will omit cohort context: {e}")
        cohort_snapshot = None
```

Use this version (replacing the unguarded line above) in the final implementation.

- [ ] **Step 4: Run the test to verify it passes**

Run: `.venv\Scripts\python.exe -m pytest tests/test_pt_report_common.py -k main_builds_cohort_snapshot -v`
Expected: PASS.

- [ ] **Step 5: Run the full existing test suite for both affected modules**

Run: `.venv\Scripts\python.exe -m pytest tests/test_pt_report_common.py tests/test_pt_cohort_common.py tests/test_pt_score.py tests/test_mas_validation.py -v`
Expected: PASS — every test across every module this plan touched.

- [ ] **Step 6: Commit**

```bash
git add run_pt_analysis.py tests/test_pt_report_common.py
git commit -m "feat: wire cohort snapshot through run_pt_analysis.py's main()"
```

---

## Task 14: Manual end-to-end verification

**Files:** none (verification only, no code changes)

Per the design spec's own testing note (§8): visual figure output isn't unit-tested in this module, matching its existing convention (`make_report_figure`/`make_rmse_figure`/`make_cohort_comparison_figure` have never had pixel-level tests, only "does it produce a file without raising" tests). This task is the equivalent manual check for the new rows.

- [ ] **Step 1: Run against a real participant with existing MediaPipe/IMU/OptiTrack data**

```bash
.venv\Scripts\python.exe run_pt_analysis.py 13
```

Expected: no traceback; `Model_Analysis_Outputs/PT_Scores/P13_full_report.png`, `P13_rmse.png`, and `P13_clinician_mas.csv` are all written (or updated).

- [ ] **Step 2: Open `P13_full_report.png` and visually confirm**

- Row 1 shows dashed/dotted MediaPipe/IMU overlays alongside the solid OptiTrack trace, roughly release-aligned with it (not offset by a full second or more — a large offset would indicate `release_aligned_hpe_curve`'s alignment isn't working as intended for real data, worth a follow-up investigation, not necessarily a plan bug).
- Row 4 matches the standalone `P13_rmse.png`'s bars exactly (same shared-helper code).
- Row 5's table is legible at the figure's saved DPI — if timepoints/clinician rows overflow readably, note it; the truncation policy (Task 10, at most 2 clinician matches, `"+N more"`) should already prevent runaway growth, but confirm visually rather than assuming.
- Caption text at the bottom is present and legible, not clipped by the figure bounds.

- [ ] **Step 3: Run against a participant with >6 timepoints or several clinician MAS rows, if one exists in the current dataset**

Check `mas_scores.csv` and `participant_groups.json` for a participant with the most rows/conditions on record (per the design spec's own testing note about verifying the truncation policy against a real dense case — check `mas_scores.csv`'s current contents first to identify a candidate participant, e.g. via `grep <pid> mas_scores.csv`.) Confirm Row 5 truncates rather than overflowing the figure.

- [ ] **Step 4: Run the full project test suite once more, end to end**

```bash
.venv\Scripts\python.exe -m pytest tests/ -v
```

Expected: PASS, zero regressions across the entire test suite (not just the files this plan touched) — this is the final gate before considering the feature complete.

- [ ] **Step 5: No commit for this task** (verification only) — if Step 2 or 3 surfaces a real visual problem, open a small follow-up task/commit to fix it rather than silently accepting it.

---

## Self-Review Notes

**Spec coverage check** (against `docs/superpowers/specs/2026-08-10-full-report-hpe-accuracy-design.md`):
- §5.1 Row 1 overlay + release alignment → Tasks 2, 3, 9, 12.
- §5.2 Row 2 unchanged → no task (explicitly left alone, confirmed by Task 12 touching only Rows 1/4/5 + caption).
- §5.3 Row 3 unchanged → no task (same).
- §5.4 Row 4 RMSE → Task 8, wired in Task 12.
- §5.5 Row 5 table, 3-stage accounting, per-source paired baselines, clinician MAS + sidecar → Tasks 1, 5, 6, 10, wired in Task 12.
- §5.6 Caption (cohort reference + data completeness) → Tasks 4, 7 (`leg_cohort_reference`), 11, wired in Task 12.
- §6 `build_cohort_snapshot()`/`write_cohort_artifacts()` → Task 7.
- §7 Error handling → covered inline in each task's implementation (non-fatal `None`/omission paths throughout — no task raises on missing data).
- §9's fixes (circular import, unparseable status, per-source baselines, sidecar CSV, snapshot completeness) → Tasks 4, 5, 7, 10 respectively.
- Task 13 covers the plan's own §9-equivalent wiring requirement (run_pt_analysis.py main()).

**Placeholder scan:** no "TBD"/"TODO"/"handle appropriately" language anywhere above; every step has real code or a real shell command.

**Type/signature consistency check:** `attach_rmse` (Task 2) produces `rec["mediapipe_curve"]`/`rec["imu_curve"]`, consumed by the same keys in Task 9's `_hpe_overlay_series` and Task 10's `_draw_row5_table`/`_row5_source_pt7`. `trial_candidates()` (Task 4) returns `status` values consumed identically in Task 11's `_build_caption_text`. `build_cohort_snapshot()` (Task 7)'s dict keys (`ms_pids`, `control_pids`, `summaries_by_pid`, etc.) are consumed identically by `leg_cohort_reference()` (same task) and Task 11's caption assembly. `clinician_mas_matches()` (Task 5) return shape (list of raw CSV row dicts) is consumed identically by Task 6's sidecar writer and Task 10's Row 5 table. Confirmed consistent across all 14 tasks.
