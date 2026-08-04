# Workbench PT-Score Panel Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The Workbench's per-trace metrics readout (`pendulastic_workbench.py`'s
`_recompute_metrics`) currently prints only `area_ratio`, `N`, and `f` per trace — it
never surfaces the composite Popović PT score or the other four PT sub-parameters
(`R2n`, `phi_max_ratio`, `omega_max_n`, `omega_min_n`) that `workbench_engine.
windowed_pt_params` already computes. Bind the existing `pendulastic_pt_score.
compute_pt_score`/`pt_to_mas` helpers onto each visible trace's windowed PT
parameters and surface the PT score, MAS estimate, and full 7-parameter breakdown in
the readout text (and therefore also in session export, which reuses the same
snapshot dict).

**Architecture:** `windowed_pt_params` (`workbench_engine.py`) already returns a dict
whose 7 keys (`R2n`, `N`, `phi_max_ratio`, `omega_max_n`, `f`, `area_ratio`,
`omega_min_n`) exactly match the keys `pendulastic_pt_score.compute_pt_score` expects.
No changes to `workbench_engine.py` or `pendulastic_pt_score.py` are needed — the fix
is purely at the binding/display layer in `pendulastic_workbench.py`:
`get_metrics_snapshot()` calls `compute_pt_score`/`pt_to_mas` on each per-trace params
dict and adds the results as two new keys (`pt_score`, `mas`); `_recompute_metrics()`
renders them.

**Tech Stack:** Python, Tkinter, pytest.

## Global Constraints

- Do not modify `workbench_engine.py`, `pendulastic_pt_score.py`, `analysis_pipeline.py`,
  or anything under the $t_0$ waveform-alignment or sequential 4-component CSV intake
  work — this change is confined to `pendulastic_workbench.py` and its test file.
- `windowed_pt_params`'s existing return contract (exactly the 7 raw parameter keys)
  must not change — tests in `tests/test_workbench_engine.py` assert against it
  directly (`result["area_ratio"]`, `result["N"]`). The new `pt_score`/`mas` keys are
  added in `pendulastic_workbench.py`, one layer up, not inside `windowed_pt_params`.
- `vs_reference` (RMSE/MAE/lag/jitter) is unaffected — PT score is a per-trace
  (single-stream) diagnostic, not a cross-modality comparison, so it belongs in
  `per_trace`, not `vs_reference`.
- Formatting must match the existing precedent in `pendulastic_app.py`'s
  `_show_pt_metrics_from_sources` (`f"{score:.3f}"` for PT score, MAS rendered as a
  plain string) so the two panels read consistently.
- A degenerate trace (flat/near-flat signal, `windowed_pt_params` returns all-zero
  params) must still produce a `pt_score`/`mas` line, not a crash or an omitted line —
  `compute_pt_score` already handles all-zero params without raising.

---

### Task 1: Bind PT score into the metrics snapshot and render it

**Files:**
- Modify: `pendulastic_workbench.py:1-29` (imports), `pendulastic_workbench.py:326-369`
  (`get_metrics_snapshot`), `pendulastic_workbench.py:371-401` (`_recompute_metrics`)
- Test: `tests/test_pendulastic_workbench.py`

**Interfaces:**
- Consumes: `pendulastic_pt_score.compute_pt_score(params: dict, ref: dict = HEALTHY_REF) -> float`
  and `pendulastic_pt_score.pt_to_mas(pt: float) -> str` (both already exist and are
  unmodified). `engine.windowed_pt_params(t, angle) -> dict` (already exists, unmodified;
  keys: `R2n`, `N`, `phi_max_ratio`, `omega_max_n`, `f`, `area_ratio`, `omega_min_n`).
- Produces: `get_metrics_snapshot()["per_trace"][label]` now also has `"pt_score": float`
  and `"mas": str` keys, in addition to the 7 existing raw-parameter keys. No other
  method signatures change.

- [ ] **Step 1: Write the failing test for the snapshot binding**

```python
def test_get_metrics_snapshot_includes_pt_score_and_mas():
    """The per-trace snapshot must expose the composite Popović PT score and
    its MAS estimate, not just the raw sub-parameters -- a researcher reading
    the exported JSON needs the same PT score the panel displays."""
    from pendulastic_workbench import WorkbenchView
    r = _get_root()
    wv = WorkbenchView(r, _Ctrl())
    fs = 100.0
    t = np.arange(0, 4.0, 1.0 / fs)
    decay = np.exp(-0.4 * t)
    angle = 140.0 + 40.0 * decay * np.cos(2 * np.pi * 1.0 * t)
    wv.set_traces({"imu": (t, angle)})
    r.update()

    snapshot = wv.get_metrics_snapshot()
    pt = snapshot["per_trace"]["imu"]
    assert isinstance(pt["pt_score"], float)
    assert pt["pt_score"] >= 0.0
    assert isinstance(pt["mas"], str)
```

Add this test to `tests/test_pendulastic_workbench.py`, after
`test_set_traces_repositions_scrub_indicator_to_current_time` (matches the file's
existing `_get_root()`/`_Ctrl()` fixtures — no new fixtures needed).

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_pendulastic_workbench.py::test_get_metrics_snapshot_includes_pt_score_and_mas -v`
Expected: FAIL with `KeyError: 'pt_score'`

- [ ] **Step 3: Add the import**

In `pendulastic_workbench.py`, after the existing `import workbench_engine as engine`
(line 29):

```python
import pendulastic_pt_score
```

- [ ] **Step 4: Bind the PT score inside `get_metrics_snapshot`**

Replace the per-trace loop body (`pendulastic_workbench.py:348-351`):

```python
        for label, (t, y) in self._traces.items():
            if not self._visible_vars.get(label, tk.BooleanVar(value=True)).get():
                continue
            out["per_trace"][label] = engine.windowed_pt_params(t, y)
```

with:

```python
        for label, (t, y) in self._traces.items():
            if not self._visible_vars.get(label, tk.BooleanVar(value=True)).get():
                continue
            params = engine.windowed_pt_params(t, y)
            params["pt_score"] = pendulastic_pt_score.compute_pt_score(params)
            params["mas"] = pendulastic_pt_score.pt_to_mas(params["pt_score"])
            out["per_trace"][label] = params
```

Also update the method's docstring (`pendulastic_workbench.py:331-333`) to mention the
new keys:

```python
        - "per_trace": each visible trace's own windowed_pt_params
          (area_ratio, N, f, etc.) plus the composite Popović PT score and MAS
          estimate computed from those same windowed params (pt_score, mas) --
          a per-modality diagnostic, not a comparison. Includes the reference
          trace itself.
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/test_pendulastic_workbench.py::test_get_metrics_snapshot_includes_pt_score_and_mas -v`
Expected: PASS

- [ ] **Step 6: Write the failing test for the rendered text**

Add to `tests/test_pendulastic_workbench.py`:

```python
def test_recompute_metrics_shows_pt_score_and_submetric_breakdown():
    """The readout text must surface the PT score and the full 7-parameter
    Popović breakdown per trace, not just area_ratio/N/f -- a researcher
    should never have to open the JSON export to see R2n or omega_max_n."""
    from pendulastic_workbench import WorkbenchView
    r = _get_root()
    wv = WorkbenchView(r, _Ctrl())
    fs = 100.0
    t = np.arange(0, 4.0, 1.0 / fs)
    decay = np.exp(-0.4 * t)
    angle = 140.0 + 40.0 * decay * np.cos(2 * np.pi * 1.0 * t)
    wv.set_traces({"imu": (t, angle)})
    r.update()

    text = wv._metrics_text.get("1.0", "end")
    snapshot = wv.get_metrics_snapshot()
    pt = snapshot["per_trace"]["imu"]
    assert f"PT={pt['pt_score']:.3f}" in text
    assert f"MAS {pt['mas']}" in text
    assert "R2n=" in text
    assert "phi_max_ratio=" in text
    assert "omega_max_n=" in text
    assert "omega_min_n=" in text
```

- [ ] **Step 7: Run test to verify it fails**

Run: `python -m pytest tests/test_pendulastic_workbench.py::test_recompute_metrics_shows_pt_score_and_submetric_breakdown -v`
Expected: FAIL (assertion on `"PT="` / `"R2n="` not found in text)

- [ ] **Step 8: Render the PT score and sub-metric breakdown**

Replace the per-trace rendering loop (`pendulastic_workbench.py:383-387`):

```python
        for label, pt in snapshot["per_trace"].items():
            self._metrics_text.insert(
                "end",
                f"{label}: area_ratio={pt['area_ratio']:.3f}  N={pt['N']:.1f}  "
                f"f={pt['f']:.2f} Hz\n")
```

with:

```python
        for label, pt in snapshot["per_trace"].items():
            self._metrics_text.insert(
                "end",
                f"{label}: PT={pt['pt_score']:.3f} (MAS {pt['mas']})  "
                f"area_ratio={pt['area_ratio']:.3f}  N={pt['N']:.1f}  "
                f"f={pt['f']:.2f} Hz\n"
                f"    R2n={pt['R2n']:.3f}  phi_max_ratio={pt['phi_max_ratio']:.3f}  "
                f"omega_max_n={pt['omega_max_n']:.2f}  "
                f"omega_min_n={pt['omega_min_n']:.3f}\n")
```

- [ ] **Step 9: Run test to verify it passes**

Run: `python -m pytest tests/test_pendulastic_workbench.py::test_recompute_metrics_shows_pt_score_and_submetric_breakdown -v`
Expected: PASS

- [ ] **Step 10: Run the full existing Workbench + engine test suites to confirm no regressions**

Run: `python -m pytest tests/test_pendulastic_workbench.py tests/test_workbench_engine.py tests/test_pt_score.py -v`
Expected: PASS (all tests, including the two new ones)

- [ ] **Step 11: Commit**

```bash
git add pendulastic_workbench.py tests/test_pendulastic_workbench.py
git commit -m "feat: surface Popovic PT score and full sub-metric breakdown in Workbench panel"
```

---

## Self-Review Notes

- **Spec coverage:** "Bind PT Calculator" -> Step 4. "Surface PT score alongside
  area_ratio/N/f/RMSE/lag" -> Step 8 (PT score sits in the same per-trace line as
  area_ratio/N/f; RMSE/lag remain on their own existing `vs_reference` lines directly
  below, unchanged). "Sub-parameters populated, not defaulted to zero/omitted" -> Step 8
  prints all 7 params; they were already computed by `windowed_pt_params`, just never
  displayed. "Don't touch $t_0$ alignment / CSV intake" -> Global Constraints; this plan
  never opens `analysis_pipeline.py` or the CSV-intake files.
- **Placeholder scan:** none — every step has literal code.
- **Type consistency:** `pt_score` is a `float` (matches `compute_pt_score`'s return
  type); `mas` is a `str` (matches `pt_to_mas`'s return type) throughout both new tests
  and the implementation.
