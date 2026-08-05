# Acquisition Screen Clinician UX & App-Wide Style Unification — Design Spec

**Status:** Approved (brainstorming complete, pending user review of this document)
**Date:** 2026-08-05

## 1. Problem & Existing Landscape

The user is dissatisfied with `pendulastic_app.py`'s interface on two counts:
visual style, and information density on the acquisition/recording screen.

**Visual style.** The app is visually fragmented today. `pendulastic_app.py`
has its own hardcoded palette (`_GREEN = "#1e7d34"`, `_BLUE = "#1f3a93"`,
default system-gray `tk.Frame`/`tk.Label` backgrounds, a hardcoded
`#0B1928` navy telemetry canvas). It imports `workbench_style` only to call
`apply_ttk_theme(self)` once (`App.__init__`, line 1357) for the embedded
Workbench panels' `ttk` widgets — none of `AcquisitionPanel`,
`ModeSelectView`, `UploadMetaView`, or `PostProcessingPanel` use
`workbench_style`'s palette or widget builders. `pendulastic_workbench.py`'s
`TrialLoadPanel`/`WorkbenchView` (embedded in the same `App` root — this is
one application, not separate programs) use `workbench_style.PALETTE`, which
is currently white/bright (`BG #FFFFFF`, accent `#2563EB`) per an earlier
explicit request. Net effect: three different color systems live in one
window today.

Direction chosen for the unified look (compared visually via mockups,
selected by the user): **Modern Clinical Dashboard** — light warm-gray
background, white elevated cards (bordered, since Tkinter has no native
drop-shadow), crisp blue accent. This keeps `workbench_style.py`'s existing
`BTN_ACT #2563EB` accent (already correct from the prior request) and only
adjusts its background/card treatment.

**Information density.** Confirmed with the user: the complaint is
specifically the acquisition/recording screen (`AcquisitionPanel`), not the
Workbench. Root cause is twofold:
1. All four recording sources (OptiTrack, RGB, iPhone IMU, Video File) are
   presented as equal, always-visible checkboxes.
2. The defaults are backwards from actual clinical use: `_src_optitrack`
   defaults `True` (`AcquisitionPanel.__init__`, line 409) while `_src_imu`
   and `_src_rgb` default `False` (lines 410–411). Confirmed with the user:
   routine clinical sessions almost always use **IMU + RGB**; OptiTrack and
   the video-file-upload source are research-only extras used rarely.

**Scope decision:** this covers `pendulastic_app.py` (all its panels) and
the `workbench_style.py` palette only. `pendulastic_workbench.py`'s own
structure (`TrialLoadPanel`/`WorkbenchView` layout, CSV export, metrics
tables) is unaffected beyond inheriting the `BG` color tweak automatically.
`pendulastic_viewer.py` is not touched.

## 2. Architecture

Two changes, no new modules:

- **`workbench_style.py`** — edit `PALETTE["BG"]` from `#FFFFFF` to a soft
  light-gray (`#F4F6F9`). `BORDER` (`#CBD5E1`) and `card_frame()`'s existing
  `highlightbackground=BORDER, highlightthickness=1` treatment already
  produce the "elevated card on gray" look with this one value change — no
  changes to `card_frame`, `primary_button`, `secondary_button`, or
  `apply_ttk_theme`.
- **`pendulastic_app.py`** — add `import workbench_style as ws` at module
  level (promoting it from a Workbench-only dependency to an app-wide one;
  it's a dependency-light module by design, per its own docstring, so this
  has no new transitive cost). Replace the hardcoded `_GREEN`/`_BLUE`
  constants and bare `tk.Frame`/`tk.Button`/`tk.Label` styling in
  `AcquisitionPanel`, `ModeSelectView`, `UploadMetaView`, and
  `PostProcessingPanel` with `ws.PALETTE`/`ws.card_frame`/
  `ws.primary_button`/`ws.secondary_button`. `App.__init__` sets
  `self.configure(bg=ws.PALETTE["BG"])` alongside its existing
  `ws.apply_ttk_theme(self)` call (line 1357).

This is a mechanical restyle pass, not a rewrite: every widget keeps its
existing `command=`/`textvariable=`/grid position; only the styling
arguments and, in `AcquisitionPanel`, the two behavioral changes in Section
3 change.

## 3. Acquisition Screen Changes

In `AcquisitionPanel.__init__` (lines 409–412):

- Flip defaults: `_src_imu = tk.BooleanVar(value=True)`,
  `_src_rgb = tk.BooleanVar(value=True)`, `_src_optitrack =
  tk.BooleanVar(value=False)`. `_src_video_file` stays `False`.
- `lbl_method_status`'s initial text (line 469, `"● OptiTrack (Motive)"`)
  updates to reflect the new default sources (built from
  `get_active_sources()` at init time, same as `_on_source_changed`
  already does for subsequent changes — no new status-text logic needed).

In `_build_widgets`'s `meth_f` block (lines 414–461):

- The IMU and RGB checkbuttons stay always-visible in `chk_row`, at the top
  of the methodology card.
- OptiTrack's checkbutton, the Video File checkbutton, and the existing
  `_video_path_frame` (browse row) move into a new sub-frame,
  `self._research_sources_frame`, initially hidden via `pack_forget()` —
  identical mechanism to the existing `_cam_frame`/`_video_path_frame`
  show/hide pattern, no new widget-visibility mechanism introduced.
- A new toggle label, `"▸ Research sources (OptiTrack, Video File)"`,
  above that sub-frame, flips to `"▾ Research sources"` and calls
  `self._research_sources_frame.pack(...)` / `.pack_forget()` on click.
  Collapsing/expanding only changes visibility — it never mutates
  `_src_optitrack`/`_src_video_file`, so a checked research source stays
  checked (and counted in `get_active_sources()`) even while collapsed.
- The toggle label widget is added to `AcquisitionPanel._lockable` (the
  existing list of widgets disabled during recording, line 510) so it
  can't be toggled mid-recording, consistent with the source checkboxes
  (`chk_opti`, `chk_rgb`, `chk_imu`, `chk_video`) already in that list.

No changes to `get_active_sources()`, `validate_metadata()`,
`get_metadata()`, or any `App` method that consumes them — this is a
visibility/defaults change only, not a data-shape change.

## 4. Testing

- `tests/test_acquisition_panel.py:46-48` currently asserts the *old*
  defaults (`_src_optitrack.get() is True`, `_src_rgb`/`_src_imu.get() is
  False`). This is a deliberate spec change (confirmed with the user), so
  the assertion is updated to the new defaults, not preserved.
- New test: `_research_sources_frame` starts hidden
  (`grid_info() == {}` / `winfo_ismapped()` false, matching this file's
  existing visibility-assertion convention for `_cam_frame`); clicking the
  toggle shows it; clicking again hides it; toggling does not change
  `_src_optitrack.get()`/`_src_video_file.get()`/`get_active_sources()`
  before or after.
- Existing tests at lines 151, 170, 185–193, 320, 338 that call
  `p._src_optitrack.set(...)` directly continue to work unmodified — they
  don't depend on the checkbox's visibility, only the `BooleanVar`.
- No new tests needed for the palette/restyle changes themselves (a color
  value and widget-builder swap); a manual smoke test (`python
  pendulastic_app.py`, click through Mode Select → Acquisition →
  Workbench) confirms no clipping/contrast regressions across all
  restyled panels, since Tkinter color/visual rendering isn't practical to
  assert headlessly (same precedent as the prior viewer-style plan).

## 5. Explicitly Out of Scope

- `pendulastic_workbench.py`'s own layout/structure — untouched beyond the
  inherited `PALETTE["BG"]` value.
- `pendulastic_viewer.py` — not modified.
- Any wizard-style/multi-step restructuring of `AcquisitionPanel` — this
  spec keeps the existing flat-form shape and only changes what's visible
  by default and the color system. A step-by-step redesign was considered
  and explicitly deferred (see brainstorming discussion) as higher-risk
  than this contained change.
- No change to which sources are *allowed* to be selected, validation
  rules (e.g. `video_file`+`rgb` mutual exclusion), or any recording/
  processing logic — defaults and visibility only.
