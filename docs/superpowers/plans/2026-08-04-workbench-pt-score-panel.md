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

## Addendum (post-final-review correction)

The final whole-branch review found that this plan's core premise was wrong:
`windowed_pt_params`'s 7 keys match `compute_pt_score`'s expected keys *by name*, but
not *by semantics*. Confirmed by direct inspection:

- `pendulastic_pt_score.py:760`: `omega_min_n = nanmin(|omega|, during swing) / A0` — a
  true minimum; `HEALTHY_REF["omega_min_n"] = 0.002` ("near-zero by physics"),
  penalized only when *above* reference.
- `workbench_engine.py:248`: `omega_min_n = np.max(pos_vel) / A0` — a **maximum**, not a
  minimum at all.

Feeding the workbench's `omega_min_n` (~4-6) into `compute_pt_score`'s
`omega_min_n`-above-reference penalty (denominator `7 * max(0.002, 0.1) = 0.7`)
dominates the score: 48 synthetic healthy pendulum traces (swept across sample rate,
damping, and frequency) all scored MAS 4 (max severity), while a degenerate all-zero
trace scored a healthier MAS 2 — the grade was anti-correlated with data quality.
`phi_max_ratio` (sign-flipped between the two implementations) and `area_ratio`
(~250x scale mismatch, because `windowed_pt_params` deliberately excludes the resting
tail that `compute_pt_params` deliberately appends 4.5s of) compound the problem — 3 of
7 terms are not comparable to `HEALTHY_REF`'s control-derived medians.

**Corrected approach:** compute `pt_score`/`mas` from
`pendulastic_pt_score.compute_pt_params(t, y)` (the function `HEALTHY_REF` was actually
calibrated against) instead of from `windowed_pt_params`'s output. Continue rendering
`windowed_pt_params`'s 7 raw parameters as the sub-metric breakdown — that function's
own docstring already scopes itself as "an additive, more-robust presentation specific
to this workbench... not a replacement for `pendulastic_pt_score.compute_pt_params`'s
own area_ratio used elsewhere (e.g. PT scoring)", which is exactly the distinction this
correction restores. `compute_pt_params` returns `None` when the signal has fewer than
40 finite samples, fewer than 25 post-release samples, or `|A0| < 3.0` degrees; render
`PT=n/a (insufficient signal)` in that case rather than a fabricated number.

Also label the line to disambiguate from `pendulastic_app.py`'s existing `PT=` display
(`_show_pt_metrics_from_sources`, `pendulastic_app.py:1069`), which uses
`compute_pt_score_simple` (4-param) rather than this panel's `compute_pt_score`
(7-param) — same label, different formula, on two different panels of the same app.

### Task 2: Fix the PT-score binding to use compute_pt_params

**Files:**
- Modify: `pendulastic_workbench.py` (`get_metrics_snapshot`, `_recompute_metrics`)
- Test: `tests/test_pendulastic_workbench.py`

**Interfaces:**
- Consumes: `pendulastic_pt_score.compute_pt_params(t: np.ndarray, angle_raw: np.ndarray) -> Optional[dict]`
  (unmodified, pre-existing; returns `None` on insufficient signal, else a dict with
  `R2n`, `N`, `phi_max_ratio`, `omega_max_n`, `omega_min_n`, `f`, `area_ratio`, plus
  `quality_warn`/diagnostics). `pendulastic_pt_score.compute_pt_score(params, ref=HEALTHY_REF) -> float`
  and `pt_to_mas(pt: float) -> str` (unmodified).
- Produces: `get_metrics_snapshot()["per_trace"][label]` keeps its 7 `windowed_pt_params`
  keys (the sub-metric breakdown, unchanged from Task 1) plus `"pt_score": Optional[float]`
  and `"mas": Optional[str]` — both `None` together when `compute_pt_params` returns `None`
  for that trace, never a fabricated 0.0/"n/a"-as-a-string-only-in-one-of-two-keys split.

- [ ] **Step 1: Write the failing tests**

Replace the two Task 1 tests in `tests/test_pendulastic_workbench.py` (both currently
under-assert per the final review's Important finding #3 — `pt_score >= 0.0` can never
fail) with:

```python
def test_get_metrics_snapshot_pt_score_is_healthy_for_clean_damped_signal():
    """A clean, healthy-shaped damped pendulum swing must score in the healthy
    band -- the composite score must come from compute_pt_params (the function
    HEALTHY_REF was actually calibrated against), not from windowed_pt_params
    (whose omega_min_n is a maximum, not the minimum compute_pt_score expects,
    which previously scored this exact signal as MAS 4 / maximum severity)."""
    from pendulastic_workbench import WorkbenchView
    import pendulastic_pt_score
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
    assert pt["pt_score"] is not None
    assert pt["pt_score"] < pendulastic_pt_score.PT_HEALTHY_MAX
    assert pt["mas"] in ("0", "1")


def test_get_metrics_snapshot_pt_score_none_for_insufficient_signal():
    """A too-short/flat trace (compute_pt_params returns None) must report
    pt_score=None and mas=None together -- never a fabricated 0.0 score or a
    None/string split between the two keys."""
    from pendulastic_workbench import WorkbenchView
    r = _get_root()
    wv = WorkbenchView(r, _Ctrl())
    t = np.arange(0, 3.0, 1 / 60)
    angle = np.full_like(t, 180.0)
    wv.set_traces({"imu": (t, angle)})
    r.update()

    snapshot = wv.get_metrics_snapshot()
    pt = snapshot["per_trace"]["imu"]
    assert pt["pt_score"] is None
    assert pt["mas"] is None


def test_recompute_metrics_shows_pt_score_and_submetric_breakdown():
    """The readout text must surface the disambiguated composite PT score
    (labeled distinctly from pendulastic_app.py's PT= line, which uses a
    different 4-parameter formula) and the full 7-parameter windowed
    breakdown per trace."""
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
    assert f"PT(7p)={pt['pt_score']:.3f}" in text
    assert f"MAS {pt['mas']}" in text
    assert "R2n=" in text
    assert "phi_max_ratio=" in text
    assert "omega_max_n=" in text
    assert "omega_min_n=" in text


def test_recompute_metrics_shows_na_for_insufficient_signal():
    """A trace too short/flat for compute_pt_params must render an explicit
    'n/a' rather than a fabricated PT=0.000 or a crash."""
    from pendulastic_workbench import WorkbenchView
    r = _get_root()
    wv = WorkbenchView(r, _Ctrl())
    t = np.arange(0, 3.0, 1 / 60)
    angle = np.full_like(t, 180.0)
    wv.set_traces({"imu": (t, angle)})
    r.update()

    text = wv._metrics_text.get("1.0", "end")
    assert "PT(7p)=n/a (insufficient signal)" in text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_pendulastic_workbench.py -k "pt_score or submetric_breakdown or insufficient" -v`
Expected: FAIL — `test_get_metrics_snapshot_pt_score_is_healthy_for_clean_damped_signal`
fails on `assert pt["pt_score"] < pendulastic_pt_score.PT_HEALTHY_MAX` (currently ~6.8,
not < 0.09); the two `None`-path tests fail because `pt_score`/`mas` are still computed
via `windowed_pt_params`, never `None`; the breakdown test fails on the `PT(7p)=` label
(current text has `PT=`, no disambiguation suffix).

- [ ] **Step 3: Fix `get_metrics_snapshot`**

Replace the per-trace loop body added in Task 1:

```python
        for label, (t, y) in self._traces.items():
            if not self._visible_vars.get(label, tk.BooleanVar(value=True)).get():
                continue
            params = engine.windowed_pt_params(t, y)
            params["pt_score"] = pendulastic_pt_score.compute_pt_score(params)
            params["mas"] = pendulastic_pt_score.pt_to_mas(params["pt_score"])
            out["per_trace"][label] = params
```

with:

```python
        for label, (t, y) in self._traces.items():
            if not self._visible_vars.get(label, tk.BooleanVar(value=True)).get():
                continue
            params = engine.windowed_pt_params(t, y)
            full_params = pendulastic_pt_score.compute_pt_params(t, y)
            if full_params is not None:
                params["pt_score"] = pendulastic_pt_score.compute_pt_score(full_params)
                params["mas"] = pendulastic_pt_score.pt_to_mas(params["pt_score"])
            else:
                params["pt_score"] = None
                params["mas"] = None
            out["per_trace"][label] = params
```

Update the `get_metrics_snapshot` docstring's `per_trace` bullet to:

```python
        - "per_trace": each visible trace's own windowed_pt_params
          (area_ratio, N, f, etc.) as the sub-metric breakdown, plus the
          composite Popović PT score and MAS estimate (pt_score, mas) --
          computed from pendulastic_pt_score.compute_pt_params (the
          function HEALTHY_REF was calibrated against), NOT from the
          windowed params, whose omega_min_n/phi_max_ratio/area_ratio are
          on different scales. pt_score/mas are both None together when
          compute_pt_params returns None (insufficient signal) for that
          trace. A per-modality diagnostic, not a comparison. Includes the
          reference trace itself.
```

- [ ] **Step 4: Fix `_recompute_metrics`**

Replace the per-trace rendering loop added in Task 1:

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

with:

```python
        for label, pt in snapshot["per_trace"].items():
            if pt["pt_score"] is not None:
                pt_str = f"PT(7p)={pt['pt_score']:.3f} (MAS {pt['mas']})"
            else:
                pt_str = "PT(7p)=n/a (insufficient signal)"
            self._metrics_text.insert(
                "end",
                f"{label}: {pt_str}  "
                f"area_ratio={pt['area_ratio']:.3f}  N={pt['N']:.1f}  "
                f"f={pt['f']:.2f} Hz\n"
                f"    R2n={pt['R2n']:.3f}  phi_max_ratio={pt['phi_max_ratio']:.3f}  "
                f"omega_max_n={pt['omega_max_n']:.2f}  "
                f"omega_min_n={pt['omega_min_n']:.3f}\n")
```

`PT(7p)=` disambiguates this panel's 7-parameter `compute_pt_score` from
`pendulastic_app.py`'s `PT=` line (`_show_pt_metrics_from_sources`,
`pendulastic_app.py:1069`), which is `compute_pt_score_simple` (4-parameter) — same
app, different formula, addressing the final review's Important finding #4.

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_pendulastic_workbench.py -k "pt_score or submetric_breakdown or insufficient" -v`
Expected: PASS (all 4 tests)

- [ ] **Step 6: Run the full existing Workbench + engine + PT-score test suites**

Run: `python -m pytest tests/test_pendulastic_workbench.py tests/test_workbench_engine.py tests/test_pt_score.py -v`
Expected: PASS (all tests, no regressions)

- [ ] **Step 7: Commit**

```bash
git add pendulastic_workbench.py tests/test_pendulastic_workbench.py docs/superpowers/plans/2026-08-04-workbench-pt-score-panel.md
git commit -m "fix: compute Workbench PT score from compute_pt_params, not windowed_pt_params"
```

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
