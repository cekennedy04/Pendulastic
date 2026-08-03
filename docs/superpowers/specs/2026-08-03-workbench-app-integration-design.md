# Merge Pendulastic Workbench Into the Main App UI — Design Spec

**Status:** Approved
**Date:** 2026-08-03

---

## 1. Goal

The Pendulastic Workbench (multi-modal IMU/OptiTrack/MediaPipe trial comparison) is
currently a fully separate program — `pendulastic_workbench.py`, launched with its own
`App(tk.Tk).mainloop()`. This has caused real confusion: a researcher checking for
Workbench changes in the familiar main app (`pendulastic_app.py`) sees nothing, because
it's a different program entirely.

This feature embeds the Workbench's panels (`TrialLoadPanel`, `WorkbenchView`) directly
into `pendulastic_app.py`'s own window, reachable as a third option from its existing
landing screen (`ModeSelectView`), alongside "Live Recording Session" and "Upload &
Analyze." The standalone `pendulastic_workbench.py` entry point is **preserved** as a
lightweight alternate way to launch just the Workbench panels for focused testing/
development — not removed.

---

## 2. File Impact Matrix

| File | Nature of change |
|---|---|
| `pendulastic_app.py` | Guarded import of `pendulastic_workbench`/`workbench_engine` (`_WORKBENCH_AVAIL`); `App.__init__` constructs `self._workbench_load`/`self._workbench_view`; new `App._enter_workbench_mode()`, `App.on_load_trial()`, `App.get_trial_meta()`, `App.on_workbench_load_another()`; `App.on_back_to_mode_select()` extended to also hide the two new panels; `ModeSelectView` gets a third button |
| `pendulastic_workbench.py` | `TrialLoadPanel` gains a "← Back to Main Menu" button calling `self.controller.on_back_to_mode_select()`; `WorkbenchView` gains a "← Load Different Trial" button calling `self.controller.on_workbench_load_another()`; standalone `App` gains matching `on_back_to_mode_select()` (no-op) and `on_workbench_load_another()` (real: returns to its own `TrialLoadPanel`) |
| `tests/test_app.py` | New tests for `App._enter_workbench_mode`, `App.on_load_trial`, `App.on_back_to_mode_select`'s extended behavior |
| `tests/test_pendulastic_workbench.py` | New tests for the two new buttons/controller methods on both `TrialLoadPanel`/`WorkbenchView` and the standalone `App` |

No changes to `workbench_engine.py`, `analysis_pipeline.py`, or any of the Workbench's
ingestion/metrics/alignment logic — this is purely a UI-hosting change.

---

## 3. Guarded Import

Mirroring this file's own established pattern (`_IMU_AVAIL`, `_CV2_AVAIL`,
`_VIEWER_AVAIL`, `_PT_AVAIL`, `_MPL_AVAIL`) so a missing Workbench dependency (e.g. one
of the 6 HPE-model libraries `analysis_pipeline.py` imports) degrades gracefully instead
of crashing the whole app at startup:

```python
try:
    from pendulastic_workbench import TrialLoadPanel, WorkbenchView
    import workbench_engine as _wb_engine
    _WORKBENCH_AVAIL = True
except Exception:
    TrialLoadPanel = WorkbenchView = None
    _wb_engine = None
    _WORKBENCH_AVAIL = False
```

`App.__init__` only constructs `self._workbench_load`/`self._workbench_view` when
`_WORKBENCH_AVAIL` is `True`; `ModeSelectView`'s third button is only wired to a live
command when the panels exist (see Section 5).

`pendulastic_workbench.py` already calls `matplotlib.use("TkAgg")` unconditionally at
import time, same as `pendulastic_app.py`'s own guarded `matplotlib.use("TkAgg")` call —
calling it twice with the same backend is a documented no-op, not a conflict.

---

## 4. Controller Wiring

`App.__init__`, alongside its existing panel construction:

```python
self._workbench_trial_meta: dict = {}
if _WORKBENCH_AVAIL:
    self._workbench_load = TrialLoadPanel(self, controller=self)
    self._workbench_view = WorkbenchView(self, controller=self)
```

(`self._workbench_trial_meta` is initialized unconditionally, even when
`_WORKBENCH_AVAIL` is `False`, since `get_trial_meta()` below reads it — though it's
never actually populated with anything when the panels don't exist.)

New `App` methods (moved/adapted from `pendulastic_workbench.App`, reusing the same
`workbench_engine` calls — no new ingestion logic):

```python
def _enter_workbench_mode(self) -> None:
    if not _WORKBENCH_AVAIL:
        messagebox.showinfo(
            "Workbench Unavailable",
            "The Multi-Modal Comparison workbench could not be loaded in this "
            "environment (a required dependency is missing).")
        return
    self._mode_select.pack_forget()
    self._workbench_load.pack(fill="both", expand=True)
    self._state = "workbench_load"

def get_trial_meta(self) -> dict:
    return dict(self._workbench_trial_meta)

def on_load_trial(self, selection: dict) -> None:
    # Identical body to pendulastic_workbench.App.on_load_trial today:
    # loads whichever of imu/optitrack/video were selected via
    # _wb_engine.load_imu_trial/load_optitrack_trial, starts the async
    # HPE thread if video+models were selected, then switches panels:
    ...
    self._workbench_load.pack_forget()
    self._workbench_view.pack(fill="both", expand=True)
    self._workbench_view.set_traces(traces)
    ...

def on_workbench_load_another(self) -> None:
    self._workbench_view.pack_forget()
    self._workbench_load.pack(fill="both", expand=True)
```

`App.on_back_to_mode_select()` (existing method) gains two more `pack_forget()` calls:

```python
def on_back_to_mode_select(self) -> None:
    self._acq.pack_forget()
    self._post.pack_forget()
    self._upload_meta.pack_forget()
    if _WORKBENCH_AVAIL:
        self._workbench_load.pack_forget()
        self._workbench_view.pack_forget()
    self._mode_select.pack(fill="both", expand=True)
    ...
```

`ModeSelectView._build_widgets()` gains a third button, grid-positioned in a new row
below the existing two (design spec keeps the existing two-column button layout;
the third button spans both columns on its own row rather than forcing a 3-column
layout that would cramp the existing two):

```python
tk.Button(
    self,
    text="Multi-Modal Comparison\nIMU · OptiTrack · Video",
    font=("Segoe UI", 12, "bold"),
    bg=_AMBER, fg="white",
    width=24, height=4,
    command=self.controller._enter_workbench_mode,
).grid(row=3, column=0, columnspan=2, padx=40, pady=(0, 24), sticky="n")
```

(`_AMBER` already exists as a defined color constant in `pendulastic_app.py`, used
elsewhere — chosen here simply to visually distinguish this third button from the
existing green/blue pair, not for any semantic reason.)

The button's command is always `self.controller._enter_workbench_mode` — no
conditional wiring at construction time. The availability branch lives entirely inside
that one method (Section 4), so the button is always shown (consistent layout) and
never raises `AttributeError` when `_WORKBENCH_AVAIL` is `False`.

---

## 5. Navigation

- **`TrialLoadPanel`** gains a "← Back to Main Menu" button, positioned in a new header
  row above the existing "Pendulastic Workbench" title label, calling
  `self.controller.on_back_to_mode_select()`.
- **`WorkbenchView`** gains a "← Load Different Trial" button, positioned in the
  existing `top_controls` row (next to the reference selector), calling
  `self.controller.on_workbench_load_another()`.
- Both controller methods must exist on *both* `App` classes since `TrialLoadPanel`/
  `WorkbenchView` are shared code:
  - `pendulastic_app.App.on_back_to_mode_select()`: real behavior (Section 4).
  - `pendulastic_workbench.App.on_back_to_mode_select()`: no-op — there is no landing
    screen to return to in standalone mode; clicking the button simply does nothing.
  - `pendulastic_app.App.on_workbench_load_another()`: same body as Section 4.
  - `pendulastic_workbench.App.on_workbench_load_another()`: real behavior — returns to
    its own `self._load_panel` (this also fixes the pre-existing standalone-app gap
    where there was previously no way to analyze a second trial without restarting the
    whole program — a welcome side effect, not additional scope, since the same button
    is needed for the embedded case regardless).

**Maintenance note:** `TrialLoadPanel`/`WorkbenchView` are shared code driven by duck
typing, not a shared base class or `Protocol` — nothing enforces that
`pendulastic_app.App` and `pendulastic_workbench.App` implement the same controller
method set beyond convention. A future change that adds a new controller call from
either panel (e.g. a new button) must add a matching method to *both* `App` classes, or
the untested one breaks with an `AttributeError` at click-time, not at import time. This
is a deliberate tradeoff, not an oversight — with exactly two implementations, a formal
interface is more machinery than the problem warrants. Testing Plan items 4-7 below are
what actually catches drift between the two: every controller method either panel calls
must have a test on both `App` classes.

---

## 6. Testing Plan

1. **`App._enter_workbench_mode`** (`tests/test_app.py`) — clicking the mode-select
   button (or calling the method directly) hides `_mode_select` and shows
   `_workbench_load`.
2. **`App.on_back_to_mode_select` extended behavior** (`tests/test_app.py`) — from
   workbench-load or workbench-view state, confirms both new panels are hidden and
   `_mode_select` is shown again.
3. **`App.on_workbench_load_another`** (`tests/test_app.py`) — from workbench-view
   state, confirms `_workbench_view` hides and `_workbench_load` re-shows.
4. **`TrialLoadPanel`'s back button** (`tests/test_pendulastic_workbench.py`) — invoking
   it calls `controller.on_back_to_mode_select()` (a fake controller in the test,
   matching this file's existing `_Ctrl` convention).
5. **`WorkbenchView`'s "load different trial" button** (`tests/test_pendulastic_workbench.py`)
   — invoking it calls `controller.on_workbench_load_another()`.
6. **Standalone `pendulastic_workbench.App`'s `on_back_to_mode_select()` is a genuine
   no-op** — calling it doesn't raise and doesn't change which panel is shown.
7. **Standalone `pendulastic_workbench.App`'s `on_workbench_load_another()`** — from its
   `WorkbenchView`, confirms it returns to its own `TrialLoadPanel`.
8. **`_WORKBENCH_AVAIL is False` fallback** (`tests/test_app.py`) — with the guarded
   import monkeypatched to have failed, clicking the third mode-select button shows the
   informational messagebox rather than raising `AttributeError`.

---

## 7. Out of Scope

- Any change to `workbench_engine.py`'s ingestion/metrics/alignment functions, or to
  `TrialLoadPanel`/`WorkbenchView`'s comparison/annotation/export behavior — this is
  purely about *where* those panels are hosted, not what they do.
- Removing or altering the standalone `pendulastic_workbench.py` entry point — explicitly
  kept per this session's decision.
- Porting `pendulastic_viewer.py`'s broader annotation toolset (person-pick, manual
  marker placement, track/retrack, path-correction) — a separate, already-identified
  piece of future work, decomposed independently of this integration.
