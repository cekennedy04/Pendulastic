# Eliminate the Standalone Workbench Entry Point — Design Spec

**Status:** Approved
**Date:** 2026-08-14

---

## 1. Goal

Sub-project 1 of the broader "clean up Pendulastic for clinician/market use" effort: make
`pendulastic_app.py` the single launchable clinician-facing program.

The 2026-08-03 integration (`docs/superpowers/specs/2026-08-03-workbench-app-integration-design.md`)
embedded the Workbench's panels (`TrialLoadPanel`, `WorkbenchView`, `DashboardView`) inside
`pendulastic_app.py`, reachable as a "Multi-Modal Comparison" mode from its landing screen. That
spec *deliberately* kept `pendulastic_workbench.py` as a second, standalone entry point with its
own `App(tk.Tk)` class re-implementing the same controller methods
(`on_load_trial`, `_load_video_models_async`, `on_view_dashboard`, `on_dashboard_back`,
`on_workbench_load_another`) — a tradeoff the spec itself flagged as an ongoing drift risk since
nothing enforces the two `App` classes staying in sync beyond convention.

That risk has already materialized: `pendulastic_app.App.on_load_trial` gained raw-sensor
diagnostics support (`_workbench_raw_diagnostics` / `compute_raw_sensor_diagnostics` /
`set_raw_diagnostics`) that was never ported back to `pendulastic_workbench.App.on_load_trial`
(confirmed by direct diff of both method bodies). The standalone entry point is now running
observably stale logic.

This spec removes the standalone entry point entirely. `TrialLoadPanel`, `WorkbenchView`, and
`DashboardView` remain exactly where they are in `pendulastic_workbench.py` — they are genuinely
shared, reusable widgets, hosted by `pendulastic_app.py`'s `App` (the only controller
implementation from this point on).

---

## 2. Approaches Considered

- **A — Delete the standalone `App` + `__main__` (chosen).** Smallest diff; removes the exact
  drift risk observed; no cosmetic scope creep.
- **B — Same as A, plus rename `pendulastic_workbench.py`** (e.g. `workbench_panels.py`). Rejected:
  a clinician launches this app via a shortcut/batch file, never by typing the module filename, so
  the rename serves no part of the market-readiness goal — it only touches every import site and
  both test files for a benefit only an engineer reading the repo would notice.
- **C — Delete the file entirely; move the panel classes into `pendulastic_app.py`.** Rejected:
  `pendulastic_app.py` is 3,871 lines today; the three panel classes are ~1,308 lines combined.
  Merging them in would grow the file that's already the codebase's biggest single complaint by
  roughly a third — directly working against sub-project 2 (UI polish), which needs that file
  shrinking, not growing.

**Blast-radius check (performed before approval):** a repo-wide search for `pendulastic_workbench`
across `*.py/*.md/*.bat/*.ps1/*.json/*.yaml` shows the only *live-code* importers of the standalone
`App` are `pendulastic_app.py` (imports `TrialLoadPanel`/`WorkbenchView`/`DashboardView` only —
unaffected) and `tests/test_pendulastic_workbench.py` itself. `batch_imu_vs_optitrack_rmse.py`,
`mas_validation.py`, and `workbench_style.py` reference `pendulastic_workbench` only in comments,
not imports. The ~40 other hits are historical plan/spec docs under `docs/superpowers/{plans,specs}/`
— left untouched, per this project's existing convention of superseding rather than rewriting
history. `README.md`, `CLAUDE.md`, `DEPLOYMENT_PLAN.md`, and `ALGORITHM_LAUNCH_CONFIRMATION.md`
were checked directly and contain no reference to `pendulastic_workbench.py` or standalone
Workbench launch instructions — no changes needed there.

---

## 3. File Impact Matrix

| File | Nature of change |
|---|---|
| `pendulastic_workbench.py` | Delete the `App(tk.Tk)` class (lines 1360-1516 as of this writing) and its `if __name__ == "__main__": App().mainloop()` block. Replace with a `__main__` guard that prints a redirect message and exits (Section 4). Update the module docstring to describe the file as a panel library hosted by `pendulastic_app.py`, not a standalone app (Section 5). |
| `tests/test_pendulastic_workbench.py` | Delete the 5 tests that instantiate the standalone `App`: `test_standalone_app_back_to_mode_select_is_a_genuine_noop`, `test_standalone_app_load_another_returns_to_load_panel`, `test_on_load_trial_split_csv_binds_and_stores_imu_reference`, `test_load_panel_view_dashboard_button_switches_to_dashboard_view`, `test_dashboard_back_returns_to_load_panel`. This is a forced consequence, not a coverage tradeoff — these tests `from pendulastic_workbench import App`, which no longer exists, so they'd fail on import regardless. `tests/test_app.py` already covers the equivalent behavior against the real (embedded) `App` (see Section 6). |
| `docs/superpowers/specs/2026-08-03-workbench-app-integration-design.md` | Add a one-line "Superseded" note at the top pointing to this spec, so the historical record stays accurate without being rewritten. |
| `pendulastic_app.py` | No change. Its guarded import of `TrialLoadPanel`/`WorkbenchView`/`DashboardView` and its own controller methods are already the canonical implementation. |
| `workbench_engine.py`, `analysis_pipeline.py` | No change — this is a UI-hosting change only, same as the 2026-08-03 spec's own scope boundary. |

---

## 4. `__main__` Guard

```python
if __name__ == "__main__":
    print(
        "pendulastic_workbench.py no longer runs standalone -- its panels are "
        "hosted inside pendulastic_app.py.\n"
        "Run instead:  .venv\\Scripts\\python.exe pendulastic_app.py"
    )
    raise SystemExit(1)
```

Plain `print` + `SystemExit`, not a `messagebox`: there is no root `Tk` window to host a dialog at
this point, and creating one solely to display "don't run this file" would recreate the exact
standalone-app surface this spec removes. A clinician or developer running this file out of habit
gets an immediate, actionable terminal message instead of a silent no-op or an `AttributeError`.

---

## 5. Module Docstring Update

Current (lines 1-9):

```python
"""
pendulastic_workbench.py
=========================
Pendulastic Workbench: an interactive multi-modal (phone IMU / MediaPipe-
family HPE video / OptiTrack) trial comparison tool. Follows
pendulastic_app.py's plain-Tkinter panel-swap architecture.

See docs/superpowers/specs/2026-07-31-pendulastic-workbench-design.md.
"""
```

New:

```python
"""
pendulastic_workbench.py
=========================
Panel library for the Multi-Modal Comparison workbench (phone IMU / MediaPipe-
family HPE video / OptiTrack trial comparison): TrialLoadPanel, WorkbenchView,
DashboardView. Hosted inside pendulastic_app.py's App (its "Multi-Modal
Comparison" mode) -- this module has no standalone entry point.

See docs/superpowers/specs/2026-07-31-pendulastic-workbench-design.md and
docs/superpowers/specs/2026-08-14-eliminate-standalone-workbench-design.md.
"""
```

---

## 6. Testing Plan

1. **Coverage equivalence, verified before deletion** — every behavior the 5 doomed tests exercise
   against the standalone `App` has a matching test against `pendulastic_app.App` in
   `tests/test_app.py`:
   - `on_back_to_mode_select` no-op / real behavior -> `test_on_back_to_mode_select_hides_workbench_panels`
   - `on_workbench_load_another` -> `test_on_workbench_load_another_returns_to_trial_load_panel`
   - `on_load_trial` (split-csv) -> `test_on_load_trial_split_csv_binds_and_stores_imu_reference` (same name, `tests/test_app.py`)
   - `on_view_dashboard` -> `test_on_view_dashboard_shows_dashboard_and_hides_workbench_panels`
   - `on_dashboard_back` -> `test_on_dashboard_back_returns_to_trial_load_panel`
2. **Full suite green** — run the full test suite after the change; baseline is 193/194 with one
   pre-existing Tcl/Tk resource-exhaustion flake (`test_on_source_changed_does_not_crash`) that
   passes clean in isolation and is unrelated to this change.
3. **Manual smoke check** — launch `pendulastic_app.py`, enter "Multi-Modal Comparison" mode, load
   a trial, confirm no behavior change from before this spec.
4. **`python pendulastic_workbench.py`** — confirm it prints the redirect message and exits
   non-zero, rather than doing nothing or raising.

---

## 7. Out of Scope

- Any change to `workbench_engine.py`, `analysis_pipeline.py`, or the panels' own
  comparison/annotation/export behavior.
- Renaming `pendulastic_workbench.py` (Approach B, rejected in Section 2).
- Editing historical plan/spec docs under `docs/superpowers/{plans,specs}/` that reference the old
  standalone entry point — those are a historical record, not live documentation.
- File-size reduction of `pendulastic_app.py` itself — tracked separately as sub-project 2
  (UI/UX polish) of the broader market-readiness effort.
