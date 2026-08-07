# Trial Exclusion UI — Design Spec

## 1. Goal

Give the operator a way to mark a recorded trial non-viable (e.g. the participant used their own
muscles to actively stop the pendulum swing instead of a passive release) directly from
`pendulastic_app.py`'s existing `AnalysisPanel`, instead of hand-editing `excluded_trials.json`.

## 2. Background

`excluded_trials.json` (repo root) is a `{trial_key: reason_string}` registry, already wired into
`pt_report_common.discover_all_trials()` via `load_excluded_trials()`, and already used in
production (5 entries excluding non-viable Participant 15 trials, added 2026-08-07). The only
existing way to add an entry is hand-editing the JSON file directly. This spec adds a UI for it.

**Revision note:** this design went through a Codex-arbitrated review round after the initial
draft. That review found `trial_key` (the 4-field `(participant, leg, condition, trial_number)`
string already used by the shipped registry) is not guaranteed to identify a single physical
recording — `discover_all_trials()` dedupes by real file path, not by `trial_key`, so two distinct
recordings could theoretically collide on the same key. Decided: accept this as existing,
already-shipped behavior (no registry-format migration) — see §4's duplicate-detection UI instead.
The review also caught a queue-reuse bug, a layout problem, an incomplete atomic-write API, and
several test gaps, all folded into the sections below.

## 3. Data Layer (`pt_report_common.py`)

- `discover_all_trials(include_archive=True, include_excluded=False)` — new `include_excluded`
  parameter, keyword-only in practice but added at the end of the positional signature so every
  existing caller (which never passes it) is unaffected. Default `False` preserves current
  behavior exactly. When `True`, excluded trials are included in the result, and **every**
  returned record gets an `excluded: bool` field (computed via `trial_key()` lookup against
  `load_excluded_trials()`), so callers never re-derive the key themselves.
- `duplicate_trial_keys() -> dict[str, list[str]]` — for the current `discover_all_trials()`
  result, `{trial_key: [real_path, ...]}` for every key with more than one real path. Empty in the
  common case; the UI (§4) uses this to warn before a toggle would affect more than one recording.
- `set_trials_excluded(keys: list[str], excluded: bool) -> None` — the single entry point the UI
  calls, replacing a raw dict setter so the batch semantics can't be gotten wrong from the caller
  side: loads the registry once, for each key in `keys` either sets it to a fixed placeholder
  reason (`"excluded via Analysis panel"`) when `excluded=True`, or **removes the key entirely**
  when `excluded=False` (a falsy/blank value would still satisfy `key in excluded`, corrupting the
  gate), then saves once. Atomic write: temp file via `tempfile.mkstemp(dir=..., prefix=...)` in
  the same directory (not a fixed `.tmp` suffix, which would collide if two writers ever ran at
  once) + `os.replace`. On any write failure, raises — the caller (§4) must not update UI state
  until this returns successfully.

## 4. UI (`pendulastic_app.py`'s `AnalysisPanel`)

**Layout:** the 260px left sidebar (participant list, figure-type radios, Generate button) is
unchanged. The trial table replaces the figure viewer's content area (right side, the space that
currently holds `_viewer_canvas`) when exactly one participant is selected in the list — a second
"view mode" for that same space, not a new cramped column. Selecting 0 or 2+ participants reverts
the right side to the existing figure-viewer placeholder/last-generated-figure state and disables
the trial table entirely (multi-select stays reserved for the existing 2-participant comparison
flow).

**Table:** `ttk.Treeview(show="headings")` with columns Leg | Condition | Trial # | N |
phi_max_ratio | area_ratio, plus a `⚠` indicator column populated only for rows whose `trial_key`
appears in `duplicate_trial_keys()`. Explicit column widths/stretch, a vertical scrollbar. No real
checkbox widget (Tkinter doesn't have one for Treeview) — excluded rows get a distinct row tag
(greyed foreground) instead.

**Population (background thread, own queue):** selecting a single participant enqueues a table-load
job on a **separate** `self._table_queue` (not `self._result_queue`, which stays Generate-only —
reusing one queue was the concurrency bug the design review caught, since a stale table-load result
could otherwise be decoded as a figure result or vice versa). Each job carries a monotonically
increasing `request_id`; `_poll_table_queue()` drops any result whose `request_id` doesn't match
the latest one issued (handles rapid re-selection). For each trial: `pt_score.load_optitrack(path)`
then `pt.compute_pt_params(t, angle)`; a load exception and a `compute_pt_params -> None` result
are both caught and shown as `N/A` in the param columns, distinguished only in a debug log line,
not in the UI (the operator doesn't need to know which failure mode, just that the params aren't
available).

**Toggling:** multi-select rows + a "Toggle Excluded" button. Before saving, if any selected row's
`trial_key` is in `duplicate_trial_keys()`, a single confirmation dialog covering the whole batch
lists every affected key from the selection and how many real recordings each maps to (e.g. "2 of
your 5 selected rows affect more than one recording: key A -> 2 files, key B -> 3 files. Continue?"),
not one dialog per row. Declining cancels the entire toggle, not just the duplicate rows. On
confirm: collect the selected keys, flip them to the opposite of
their current state (mixed-state selections are rejected with a message asking the user to select
rows in the same current state), call `set_trials_excluded(keys, excluded)`. UI row tags update
**only after** that call returns successfully; on exception, rows are left unchanged and a status
message shows the error. The Toggle button (and the whole table) is disabled while a Generate run
is in flight (mirrors the existing `btn_generate` disabled-during-run state), so a report can't be
generated against a registry that's mid-write.

**Post-toggle refresh:** after a successful toggle, re-run `_refresh_participants()` (existing
method) so the participant list's trial counts stay consistent with what the table now shows.

## 5. Data Flow

1. User selects a single participant → table-load job enqueued with a fresh `request_id` →
   background thread calls `discover_all_trials(include_excluded=True)` filtered to that
   participant, scores each trial's params, computes `duplicate_trial_keys()` → posts to
   `_table_queue` → `_poll_table_queue()` (only if `request_id` still current) populates the
   Treeview.
2. User multi-selects rows, clicks Toggle Excluded → (duplicate-key confirmation if needed) →
   `set_trials_excluded(keys, excluded)` → on success, row tags update + `_refresh_participants()`.
3. Next "Generate" click uses `collect_participant()` → `discover_all_trials()` (default
   `include_excluded=False`), which already reflects the just-saved registry.

## 6. Error Handling

- Per-trial load/score failure: caught, shown as `N/A`, never aborts the table population.
- `set_trials_excluded` failure: caught by the caller, rows left in their pre-toggle state, error
  surfaced in the panel's existing status label.
- Malformed `excluded_trials.json`: already handled by `load_excluded_trials()`'s existing
  defensive pattern (missing/malformed → `{}`); `set_trials_excluded` reads through the same
  function, so it inherits that behavior — a hand-corrupted file resets to empty rather than
  raising, on both the read and the subsequent write-back.

## 7. Testing

**Data layer** (`tests/test_pt_report_common.py`, plain functions, `monkeypatch`/`tmp_path`, no
test classes):
- `discover_all_trials(include_excluded=True)` returns excluded trials tagged `excluded=True`,
  non-excluded tagged `False`; default (`include_excluded=False`) behavior is byte-for-byte
  unchanged from today.
- `duplicate_trial_keys()` returns entries only for keys with >1 real path; empty for the common
  case.
- `set_trials_excluded(["k1"], True)` then `load_excluded_trials()` contains `"k1"`;
  `set_trials_excluded(["k1"], False)` removes it entirely (not a falsy value).
- `set_trials_excluded` preserves unrelated existing entries.
- Atomic write: the temp file is created in the same directory as the target (not `/tmp` or a
  fixed name), and a simulated write failure (e.g. monkeypatched `os.replace` to raise) leaves the
  original registry file's content untouched.
- Malformed existing registry: `set_trials_excluded` still succeeds, ending with only the keys
  passed in (treats malformed-as-empty, matching `load_excluded_trials()`).

**UI** (`tests/test_analysis_panel.py`, extending the existing `_FakeReport`-stand-in + real
withdrawn `tk.Tk()` + `root.update()` polling convention):
- `_FakeReport` gains `discover_all_trials`, `duplicate_trial_keys`, `set_trials_excluded` methods
  that record calls, matching the pattern already used for `make_report_figure` etc.
- Selecting a single participant populates the table with the fake's trials.
- Selecting 0 or 2 participants clears/disables the table.
- Rapid re-selection: an in-flight table-load whose `request_id` is stale is dropped, never
  overwrites a newer selection's rows (simulate via a fake that delays the first call and returns
  the second call's result first).
- A trial whose fake `load_optitrack`/`compute_pt_params` raises or returns `None` shows `N/A`
  instead of crashing the poll loop.
- Multi-row toggle calls `set_trials_excluded` exactly once with all selected keys, not once per
  row.
- `set_trials_excluded` raising leaves row tags unchanged and shows an error, not a silent success.
- Toggle button is disabled while `btn_generate` is disabled (Generate in flight).
- A duplicate-key row triggers the confirmation path (mock the dialog, assert it was shown with
  the right count) before `set_trials_excluded` is called.
