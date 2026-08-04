# Longitudinal Dashboard — Design Spec

**Status:** Approved
**Date:** 2026-08-04

---

## 1. Goal

Add a longitudinal dashboard to the Pendulastic Workbench that lets a researcher save
trial-by-trial evaluation data per participant, track progress across multiple
timepoints (e.g. Initial, Post-Training, Follow-up), compare left vs right leg metrics,
and render a 3-panel comparative figure:

1. **Waveform overlays** — synchronized knee-angle trajectories ($t_0$-aligned) across
   sessions, with each session's PT score in the legend.
2. **Parameter bar charts** — grouped comparison of the 7 Popović sub-metrics against
   healthy reference baselines.
3. **Longitudinal PT score trend** — timeline of the composite PT score with
   session-to-session $\Delta\%$ annotations and color-coded clinical threshold bands.

## 2. Prerequisite

This design depends on `docs/superpowers/plans/2026-08-04-workbench-pt-score-panel.md`
landing first. That plan adds `pt_score`/`mas` keys to
`WorkbenchView.get_metrics_snapshot()["per_trace"][label]`; today that snapshot only has
the 7 raw `windowed_pt_params` keys. Everything below assumes `pt_score`/`mas` are
already present on each per-trace snapshot.

As landed (branch `worktree-workbench-pt-score-panel`, commit `542e659`, revised after
its own review from the plan doc's original text), `pt_score`/`mas` are computed from
`pendulastic_pt_score.compute_pt_params(t, y)` — a separate function from the 7 raw
`windowed_pt_params` keys already in `per_trace[label]` — and **are both `None`
together** when `compute_pt_params` reports insufficient signal for that trace. This
design accounts for that explicitly:

- **Save action** (§7): refuses to save if the *reference* trace's `pt_score` is
  `None` — a session without a usable PT score for its ground-truth trace isn't useful
  to a longitudinal PT-score dashboard. Other, non-reference visible traces may still
  be saved with `pt_score: null`/`mas: null` in their metrics; the block applies only
  to the reference trace.
- **Rendering** (§6): a `None` `pt_score` for the panel's selected `trace_label` is not
  a crash. The waveform legend renders `"{label} (PT=n/a)"` instead of a formatted
  float. The PT trend panel excludes any session whose selected trace has
  `pt_score: null` from both the plotted line and the $\Delta\%$ calculation against
  its neighbors (equivalent to that session not existing for trend purposes; it can
  still appear in the waveform overlay and, independently, in the bar chart, since the
  bar chart never reads `pt_score`).

## 3. Scope

- Purely local to the Tkinter Workbench desktop app (`pendulastic_workbench.py`). No
  interaction with `web/api/`'s separate in-memory `participant_db`/`trial_db` — that's
  a different app (mobile/web) with its own persistence; this feature does not unify
  with it.
- `participants/` is added to `.gitignore` — history files hold real clinical data
  (participant IDs, dates, kinematic scores) and must never be committed, the same way
  `models/` and `Recordings/` are excluded today.

## 4. File Impact Matrix

| File | Nature of change |
|---|---|
| `pendulastic_storage.py` (new) | `load_history`, `save_trial`, `list_participant_ids` — see §5. |
| `longitudinal_dashboard.py` (new) | `render_dashboard(history, leg, trace_label) -> matplotlib.figure.Figure` — see §6. |
| `pendulastic_workbench.py` | `WorkbenchView` gains a "Save Trial to Dashboard" button + save dialog; `TrialLoadPanel` gains a "View Participant Dashboard" button; new `DashboardView` panel; `App` wires the new panel into its panel-swap navigation. See §7. |
| `tests/test_pendulastic_storage.py` (new) | Round-trip save/load, non-overwrite across legs/dates, atomic write, date validation, defensive-load skip behavior. |
| `tests/test_longitudinal_dashboard.py` (new) | Headless render checks (Agg backend, no Tk). |
| `tests/test_pendulastic_workbench.py` | New tests for the save dialog and `DashboardView` navigation. |

## 5. Data Schema & `pendulastic_storage.py`

`participants/{participant_id}/history.json`:

```json
{
  "participant_id": "P5",
  "legs": {
    "left": {
      "sessions": [
        {
          "label": "Initial",
          "date": "2026-07-07",
          "reference_trace": "optitrack",
          "traces": {
            "imu": {
              "t": [...], "angle": [...],
              "metrics": {
                "R2n": 0.95, "N": 6.0, "phi_max_ratio": 0.79,
                "omega_max_n": 7.17, "omega_min_n": 0.01, "f": 1.0,
                "area_ratio": 0.13, "pt_score": 0.115, "mas": "0"
              }
            },
            "optitrack": { "t": [...], "angle": [...], "metrics": {"...": "..."} }
          }
        }
      ]
    },
    "right": { "sessions": [] }
  }
}
```

Metric keys match `workbench_engine.windowed_pt_params`'s 7 keys exactly (`R2n`, `N`,
`phi_max_ratio`, `omega_max_n`, `omega_min_n`, `f`, `area_ratio`) plus `pt_score`/`mas`
— no renaming to the plan's original `R_20`/`q_ratio`/`area` names. Reference baselines
and PT zone thresholds are imported, not redefined:
`pendulastic_pt_score.HEALTHY_REF`, `PT_HEALTHY_MAX`, `PT_BORDERLINE_MAX`.

**API:**

- `normalize_participant_id(participant_id: str) -> str` — `participant_id.strip().upper()`. `" p5 "`, `"p5"`, and `"P5"` must all resolve to the same `participants/P5/history.json`, or a typo'd case/whitespace variant silently creates a duplicate participant folder. Applied consistently by `load_history`, `save_trial`, and `list_participant_ids` — normalization happens once, at the storage boundary, not separately in each caller.
- `load_history(participant_id) -> dict` — defensive read (see below). Normalizes `participant_id` before resolving the path.
- `save_trial(participant_id, leg, session_label, date, traces, metrics_by_label, reference_trace) -> None` — normalizes `participant_id`; validates `date` matches `YYYY-MM-DD` via `datetime.strptime(date, "%Y-%m-%d")`, raising `ValueError` on mismatch (fail fast at write time rather than tolerating bad dates downstream). **Upserts** on `(session_label, date)` within `legs[leg]["sessions"]`: if a session with the same `session_label` *and* `date` already exists for that leg, it is replaced in place (same list position, new `traces`/`reference_trace`) rather than appended as a duplicate — guards against a researcher double-clicking "Save Trial to Dashboard" or re-saving after reprocessing the same trial. A different `session_label` on the same `date`, or the same `session_label` on a different `date`, is a distinct session and is appended normally. Writes atomically (write to a temp file, then `os.replace`) so a crash mid-write can't corrupt the file; never touches other legs' or other (label, date) sessions.
- `list_participant_ids() -> list[str]` — scans `participants/*/history.json`; returned IDs are already normalized (they're the directory names `save_trial` created).

**Defensive loading:** `load_history()` never crashes the caller on a malformed file:

- Missing or unreadable `history.json` → empty skeleton (`{"participant_id": ..., "legs": {"left": {"sessions": []}, "right": {"sessions": []}}}`).
- `json.JSONDecodeError` → treated the same as missing.
- Missing `legs`, `legs.<leg>`, or `legs.<leg>.sessions` keys → defaulted via `.get(...)` chains, never `KeyError`.
- An individual session dict missing `traces`, `metrics`, or `reference_trace`, or whose
  `date` fails `datetime.fromisoformat()`, is **skipped, not silently dropped**:
  `load_history()` logs a `logging.warning(...)` for each skipped session (e.g.
  `"skipped malformed session for P5/left: missing 'traces'"`) and returns the skip
  reasons alongside the history (e.g. `history["_skipped"]: list[str]`) so callers can
  surface them. This avoids the failure mode where a researcher sees a shorter history
  than expected and assumes a trial was never recorded, rather than realizing a file was
  corrupted.

## 6. `longitudinal_dashboard.py`

```python
def render_dashboard(history: dict, leg: str, trace_label: str) -> matplotlib.figure.Figure
```

One `Figure`, 3 stacked subplots. Sessions for `leg` are **sorted by `date`** (parsed via
`datetime.fromisoformat`, never by JSON insertion/append order) before any panel is
built, so a backfilled earlier-dated session saved after a later one still renders in
the correct chronological position across all three panels. A session whose `date`
can't be parsed is excluded from the render (already flagged by `load_history`'s
`_skipped` diagnostics, not re-raised here). Sessions missing `trace_label` entirely are
also skipped, not errored.

1. **Waveform overlay** — each session's chosen trace, $t - t_0$ aligned, legend
   `"{label} (PT={pt_score:.3f})"`, or `"{label} (PT=n/a)"` when that trace's
   `pt_score` is `None` (§2).
2. **Parameter bar chart** — grouped bars over the 7 params × sessions, horizontal
   reference lines from `HEALTHY_REF`. **Strict single-trace filtering**: every bar in
   a session's group is read from `traces[trace_label]["metrics"]` only — never from
   `reference_trace` or any other trace present in that session, even as a fallback for
   a missing param. If `traces[trace_label]["metrics"]` is missing any of the 7 params,
   that whole session is dropped from the bar chart (not partially filled from another
   trace), so a single grouped bar never silently mixes readings from two different
   sensors. `pt_score` is never read here, so a `None` `pt_score` has no effect on this
   panel.
3. **PT score trend** — line/scatter over sessions in date order, $\Delta\%$
   annotations between consecutive points, background bands from `PT_HEALTHY_MAX`/
   `PT_BORDERLINE_MAX` (green/yellow/red). A session whose selected trace has
   `pt_score: None` is excluded from this panel entirely (from the plotted line and
   from $\Delta\%$ against its neighbors), per §2 — as if it lacked `trace_label`
   altogether, for trend purposes only.

## 7. UI Additions (`pendulastic_workbench.py`)

- **`WorkbenchView`**: new "Save Trial to Dashboard" button opens a `Toplevel` dialog
  collecting `participant_id`, `leg` (left/right), `session_label`, `date` (defaults to
  today, editable, validated against `YYYY-MM-DD` before calling storage). `participant_id`
  is not separately normalized in the dialog — it's passed through as typed, since
  `pendulastic_storage.normalize_participant_id` is the single source of truth and
  already runs inside `save_trial`; duplicating the normalization in the UI would just
  be a second place for the rule to drift out of sync. **Before saving, the dialog
  checks `get_metrics_snapshot()["per_trace"][reference_trace]["pt_score"]`; if it is
  `None`, the save is refused with an explicit error** (a session without a usable PT
  score for its reference trace isn't useful to a longitudinal PT-score dashboard) —
  other, non-reference visible traces may still carry `pt_score: null` into storage
  unblocked (§2, §6). On confirm, calls `pendulastic_storage.save_trial(...)` with
  every currently *visible* trace, each one's metrics from `get_metrics_snapshot()`,
  and the current `_reference_var` value as `reference_trace`. If the (label, date)
  pair already exists for that participant/leg, the dialog surfaces that the save will
  overwrite the existing session (upsert, per §5) before committing, so a re-save is a
  visible choice, not a silent replace.
- **`TrialLoadPanel`**: new "View Participant Dashboard" button.
- **New `DashboardView(tk.Frame)`**: participant picker (dropdown from
  `list_participant_ids()`, already-normalized, + manual entry normalized on lookup by
  `load_history`), leg selector, trace-label selector (union of
  labels found across that leg's sessions), "Load" renders the 3-panel `Figure` via
  `FigureCanvasTkAgg` (same embedding pattern as `WorkbenchView`'s existing plot canvas).
  If `load_history()` returned any `_skipped` entries for this participant, `DashboardView`
  shows a non-fatal status line (e.g. `"Skipped 1 corrupted session for participant P5"`)
  rather than silently rendering a shorter-than-expected history. Back button returns to
  `TrialLoadPanel`.
- **`App`**: owns `_dashboard_view`; wires `on_view_dashboard`/back navigation alongside
  the existing `on_back_to_mode_select`/`on_workbench_load_another`.

## 8. Testing Plan

- **Storage**: round-trip save/load; saving one leg/date doesn't clobber other
  legs/dates; atomic-write safety; `save_trial` rejects a malformed date string;
  `load_history` returns an empty skeleton for a missing file, tolerates a truncated/
  invalid-JSON file, and skips (with a logged/reported reason) a session missing
  required keys or with an unparseable date, without raising; `" p5 "`, `"p5"`, and
  `"P5"` all resolve to the same `participants/P5/history.json` (no duplicate folders);
  saving a trial with a (label, date) pair that already exists **replaces** that session
  in place rather than appending a duplicate, while a different label or a different
  date on the same leg still appends normally.
- **Dashboard rendering**: headless (Agg backend, no Tk) — a synthetic 2–3 session
  history renders a 3-axes figure; sessions saved out of chronological order render in
  date order; legend contains PT scores; bar chart covers all 7 params; PT zone bands
  are present; a session missing the selected trace label is skipped without raising; a
  session whose `traces[trace_label]["metrics"]` is missing one of the 7 params is
  dropped from the bar chart entirely rather than partially rendered or backfilled from
  `reference_trace`; a session whose selected trace has `pt_score: None` renders
  `"PT=n/a"` in the waveform legend without raising, and is excluded from the PT trend
  panel's line and $\Delta\%$ annotations (while a *different* session in the same
  history with a real `pt_score` still renders normally in all three panels).
- **Workbench UI**: save-dialog wiring (`tests/test_pendulastic_workbench.py`),
  `DashboardView` navigation and skipped-session status display, and the save-blocked
  case where the reference trace's `pt_score` is `None`.
