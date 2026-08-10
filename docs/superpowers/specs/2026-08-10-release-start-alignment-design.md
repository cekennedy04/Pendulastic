# Per-Trace Release-Start Alignment — Design Spec
**Date:** 2026-08-10
**Status:** Approved (rev. 2 — see Section 2a)

---

## 1. Goal

There is a delay in the release mark between the OptiTrack and the phone IMU knee-angle CSV
traces loaded into `pendulastic_workbench.py`: each source's release moment lands at a different
point on its own clock, and today the workbench has no way to align them at that shared physical
event. Goal: let the user select each trace's own release moment and use it to align that trace
against the chosen reference trace.

## 2. Relationship to Existing Work

**`detect_release_t0()` / `align_to_release()` already exist** in `pendulastic_pt_score.py`
(commit `6776a73`, "pure adaptive-threshold t0 release detection + trial time-axis alignment"),
with their own tests in `tests/test_pt_score.py`. They are not wired into `pendulastic_workbench.py`,
`workbench_engine.py`, or `pendulastic_app.py` today — dead code outside their own tests.
`detect_release_t0(t, signal, baseline_sec=0.6)` Savitzky-Golay filters the signal, then runs
`_detect_release`, which walks forward from a `baseline_sec` baseline window and returns the first
index whose value crosses `0.08 * signal_range` past the baseline median — or, **if no sample ever
crosses it, silently returns the baseline window's own boundary index** (verified directly against
the code: for a constant/flat signal, `signal_range` is `0`, so the crossing condition is never
true, and the loop falls through). `detect_release_t0` currently has no way to signal "no release
found" — every call either raises (only on `<4` finite samples) or returns *some* time, including a
bogus baseline-boundary time for degenerate input.

That per-trace threshold crossing is very likely the source of the delay this spec exists to fix:
IMU and OptiTrack signals have different noise floors and different shapes right at the release
event, so each trace's independently-detected threshold crossing lands at a systematically
different point relative to the true physical release — auto-detecting each trace's t0 in
isolation does not make them agree with each other. This spec does not change `_detect_release`'s
threshold algorithm. It does add a small, scoped correctness fix to `detect_release_t0` itself
(Section 3.2) and reuses it as a **starting suggestion only**, with the user-driven
correction/override loop that was missing.

The workbench already has two related, unconnected mechanisms:
- A global "Release Start" milestone (one timestamp per session, tied to the video scrubber,
  drawn as a vertical dashed line, exported via `annotations_to_csv_rows`/`export_session`).
- A per-trace manual `lag(s)` override `Entry` next to each trace's visibility checkbox, which
  shifts that trace's time array before `engine.compare_pair` computes RMSE/MAE/timing-offset
  against the reference trace selected in the "Reference" dropdown.

### 2a. Correction from rev. 1 of this spec

**Rev. 1 proposed marking each trace's release by dragging the shared video scrubber, then
clicking that trace's "Mark Release" button, reading `current_time_sec()`.** An independent review
(Codex) found this fundamentally broken and it was verified directly against the code before
accepting the correction:

- `current_time_sec()` is `current_frame_index() / self._fps` — the **video's own** frame-index/fps
  clock, not any individual trace's own `t` array. It has no defined relationship to a CSV trace's
  time values at all.
- The scrubber (`ttk.Scale(from_=0, to=0, ...)`) is range-`[0,0]` — **cannot move** — until
  `load_video()` runs. A trial with only OptiTrack and IMU CSVs and no video has no way to operate
  this mechanism at all, which is exactly the comparison this feature exists for.
- Mixing an auto-seeded mark (in a trace's own native time) with a manual mark (in video time) in
  the same field made `ref_t - test_t` meaningless whenever both existed.

Rev. 2 (this document) replaces the marking mechanism with direct click-to-mark on the plot itself,
per trace, using `event.xdata` — which is genuinely in that trace's own plotted time coordinates,
since each trace's line was plotted as `self._ax.plot(t, angle)` with its own `t` array. This
resolves the clock-domain problem at the source rather than working around it. Section 3 below is a
full rewrite, not a patch, of rev. 1's Section 3. Rev. 1 also under-scoped several other issues the
same review raised (export schema, provenance, stale-lag handling, a pre-existing marker-redraw
leak, trial-scoped reset) — all folded into Section 3 below rather than deferred.

## 3. Design

### 3.1 Dedicated per-trace release-mark storage

New `WorkbenchView.__init__` state, entirely separate from the existing `self._annotations` dict
(which keeps its original meaning: the 3 remaining global milestones only —
`MILESTONE_LABELS` drops `"Release Start"`, leaving `First Peak Extension`, `Maximum Flexion`,
`Rest/Settled`, marked via the existing scrubber-driven "Mark Here" flow, unchanged):

```python
self._release_marks: dict = {}       # {label: {"t_trace": float, "source": "auto"|"manual"}}
self._lag_provenance: dict = {}      # {label: "auto"|"manual"}
```

Reusing `_annotations`' `(frame_index, t_sec)` shape (rev. 1's approach) was wrong: a CSV-only
trace has no meaningful `frame_index`, and encoding trace identity into a formatted string key
(`"Release Start (<label>)"`) forced every consumer to parse display text back out. The dedicated
dict is keyed by the real trace label and carries **provenance** (`"auto"` vs `"manual"`), which
Section 3.4 depends on.

### 3.2 `detect_release_t0` correctness fix (scoped)

`detect_release_t0` gains:
- Validation that `t` and `signal` have matching, non-empty, finite, non-decreasing `t` (raises
  `ValueError` on violation, same failure mode it already has for `<4` finite samples).
- A real "no release detected" signal: if `_detect_release`'s returned index equals the baseline
  window boundary (`bi`) — meaning the forward scan never crossed the threshold — raise
  `ValueError("no release detected")` instead of returning the baseline index as if it were a real
  release time.

`_detect_release`'s threshold math (`0.08 * signal_range`, the baseline computation) is untouched —
this only makes an already-latent "did it actually find something" distinction explicit at the
boundary the workbench needs it at.

### 3.3 Marking mechanism

Each trace's existing chip row (checkbox + `lag(s):` field, from `set_traces()`) gains:
- A **"Mark Release"** button. Clicking it arms that specific trace as the click target
  (`self._armed_release_label = label`) and changes the plot cursor/cue (e.g. button text flips to
  "Click plot to mark…") until the next click on `self._ax` or until re-armed for a different
  trace.
- A small numeric `Entry` (release time, that trace's own units) as a non-click fallback for
  traces that are hidden, sparse, or overlap too tightly to click precisely.
- A **"Clear"** action that removes any mark (auto or manual) for that trace.

`_on_plot_click` (already wired to `self._fig.canvas.mpl_connect("button_press_event", ...)`,
currently only used for video seeking) gains a branch: if `self._armed_release_label` is set and
the click is inside `self._ax`, read `event.xdata`, snap it to that trace's **nearest actual sample
timestamp** (not the raw click position — avoids off-grid values and makes repeated clicks near the
same point idempotent), store
`self._release_marks[label] = {"t_trace": snapped_t, "source": "manual"}`, clear
`self._armed_release_label`, redraw that trace's marker, and call
`self._recompute_release_lags()`. The existing "click seeks video" behavior is preserved when no
release mark is armed.

Each trace's marker is drawn (`_draw_release_artist(label, t_trace)`) as a short vertical line at
`t_trace` in **that trace's own line color** (`self._trace_lines[label].get_color()`), not a fixed
red, plus a small text tag — resolving rev. 1's "all identical full-height red lines,
indistinguishable per trace" gap. Both the line and text artists for a label are tracked together
(`self._release_artists[label] = (line_artist, text_artist)`) and both removed before redrawing —
fixing a **pre-existing bug** in `_draw_milestone_artist` (used for the 3 remaining global
milestones) that only ever removed the text artist, silently leaking a stray `axvline` on every
re-mark within a session; the same tracked-pair pattern is applied there too as a one-line fix,
since this feature's frequent re-marking would otherwise make the leak obvious immediately.

### 3.4 Auto-seed via `detect_release_t0`

When `set_traces()` sees a label with no existing entry in `self._release_marks` (see Section 3.5
for what "existing" means across trial vs. same-trial reloads), it calls
`pendulastic_pt_score.detect_release_t0(t, angle)`. On success:
`self._release_marks[label] = {"t_trace": t0, "source": "auto"}`, marker drawn immediately — no
clicking required for traces where the auto-guess is good enough. On the (now real, per Section 3.2)
`ValueError` — insufficient data or no confident release found — the trace is simply left unmarked,
falling back fully to manual click/entry marking.

### 3.5 Auto-align: release marks drive the existing lag override, with provenance

`_lag_override_vars[label]` entries already exist per trace (the `lag(s)` `Entry`). Its existing
`<Return>`/`<FocusOut>` bindings — the only places a *user* edit is committed — now also set
`self._lag_provenance[label] = "manual"`. Programmatic writes (below) never go through those
bindings, so this cleanly distinguishes hand-typed values from computed ones without new plumbing.

New `_recompute_release_lags()`: reads the reference trace's own release mark (if any). For every
other trace:
- If both it and the reference have a release mark **and** `self._lag_provenance.get(label) !=
  "manual"`: compute `lag = engine.release_lag_sec(ref_t, test_t)` (new pure function in
  `workbench_engine.py`: `ref_t - test_t`, with a finite-value check on both inputs) and write it
  into the `lag(s)` `Entry`, setting provenance to `"auto"`.
- If the trace itself has no release mark, or the reference has none, **and** the field's
  provenance is `"auto"` (i.e., it was release-derived, not hand-typed): **clear** the field rather
  than leaving a stale value in place. This is the fix for rev. 1's "known limitation," which the
  review correctly identified as silent data corruption (metrics displayed against a new reference,
  computed from an offset that applied to the old one) rather than a benign gap.
- A `"manual"` field is never touched by this method — the researcher's hand-tuned value survives
  reference changes and other traces' marks changing, exactly as rev. 1 intended, but now actually
  enforced via provenance instead of accidentally relying on nothing else happening to call
  `lag_var.set()`.

Triggered by: any click-to-mark or Clear action (on any trace, including the reference trace
itself), any auto-seed in `set_traces()`, and the Reference dropdown changing (replaces that
`trace_add` callback's direct call to `_recompute_metrics()` — `_recompute_release_lags()` always
calls `_recompute_metrics()` itself at the end).

### 3.6 Trial-scoped reset

`App.on_load_trial()` (`pendulastic_workbench.py:1101`) is the only place a *genuinely new* trial
is loaded — it calls `WorkbenchView.set_traces()` for the first time. Its later async-HPE-results
call to `set_traces()` (`_load_video_models_async`'s `apply()`) is the same trial's results merging
in, not a new trial, and must keep preserving marks exactly as it does today for visibility/lag.

New `WorkbenchView.reset_for_new_trial()`, called from `on_load_trial()` immediately before its
first `set_traces(traces)` call, clears `self._release_marks`, `self._lag_provenance`, and every
`lag(s)` `Entry`'s text (all lag values, regardless of provenance — a lag tuned for the previous
trial's data is not meaningfully valid for a new one either). This is scoped narrowly to the state
this feature introduces or directly interacts with. **Not fixed here** (pre-existing, out of
scope): `_visible_vars` and the global `_annotations` milestones already persist by label across a
literal new-trial load with the same labels (`imu`, `optitrack`) today, independent of this
feature — a real gap, but not one this spec's mechanism created or is positioned to fix cleanly.

### 3.7 Export

New "Release Marks..." entry alongside the existing Export CSV menu (`Traces...`,
`Per-Trace Metrics...`, `Comparison Metrics...`, `Annotations...`), backed by a new
`engine.release_marks_to_csv_rows(release_marks, participant_id, session_date)`: one row per
trace with `label, t_trace, source`. The existing `Annotations...` export is unaffected — it goes
back to meaning only the 3 remaining global milestones, its original scope before rev. 1's reuse.

## 4. Out of Scope

- Changing `_detect_release`'s threshold algorithm or `align_to_release`'s behavior.
- Shifting the underlying trace time arrays, on-disk CSVs, or PT-score windowing — this is
  strictly a faster, better-seeded way to populate the existing `lag(s)` override, which already
  feeds every downstream comparison computation unchanged.
- Supporting anything beyond one constant time offset per trace (`compare_pair`'s existing
  `lag_override_sec` contract) — clock drift, differing sample rates, or dropped-sample correction
  are pre-existing limitations of that mechanism, not introduced or fixable by this spec.
- `_visible_vars` / global `_annotations` staleness across a same-label new-trial load (Section
  3.6) and async-HPE-results race against a trial change already in progress
  (`_load_video_models_async` has no trial-generation guard today) — pre-existing gaps, noted but
  not fixed here.

## 5. Testing Plan

1. `engine.release_lag_sec(ref_t, test_t)` — pure function unit test, including a non-finite-input
   rejection case.
2. `detect_release_t0` — mismatched-length / non-monotonic `t` raises; a flat/constant signal now
   raises `ValueError` instead of returning the baseline index (regression test directly on
   rev. 1's incorrect claim).
3. `_on_plot_click`'s release-marking branch — click while armed for trace X snaps to X's nearest
   sample and records `source="manual"`; click while unarmed falls through to the existing
   video-seek behavior unchanged.
4. `_recompute_release_lags()`: reference + one trace both marked → that trace's `lag(s)` set with
   `source="auto"`; a `"manual"`-provenance field is never overwritten by a subsequent mark/reference
   change; removing a trace's mark (or switching reference to an unmarked trace) **clears** any
   `"auto"` field that depended on it rather than leaving it stale.
5. `set_traces()` auto-seed: a trace with a clean synthetic release signal gets an auto-populated
   `"auto"`-source mark; a trace with degenerate/insufficient/flat data is left unmarked, no
   exception propagates out of `set_traces()`.
6. `reset_for_new_trial()`: called before a new trial's first `set_traces()`, clears
   `_release_marks`/`_lag_provenance`/lag text; **not** called on the async-HPE-merge `set_traces()`
   re-call for the same trial (existing marks survive that path, matching today's visibility/lag
   preservation behavior).
7. Redraw-leak fix: marking the same trace twice within one session leaves exactly one marker
   artist pair for that trace, not an accumulating stack of `axvline`s (also verified for the
   existing global-milestone path, which had the same latent bug).
8. `release_marks_to_csv_rows` — pure formatter unit test.
9. Full regression: existing test suite (including `test_pt_score.py`'s existing
   `detect_release_t0`/`align_to_release` tests, which must still pass unchanged except where
   Section 3.2's new validation is directly exercised) stays green.
