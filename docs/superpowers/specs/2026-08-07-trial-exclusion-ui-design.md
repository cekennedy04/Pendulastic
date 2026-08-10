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

**Revision note:** this design went through three Codex-arbitrated review rounds (round 3 verdict:
"needs another round" — its findings are folded in below, marked round 3). Round 1 found
`trial_key` (the 4-field `(participant, leg, condition, trial_number)` string already used by the
shipped registry) is not guaranteed to identify a single physical recording — `discover_all_trials()`
dedupes by real file path, not by `trial_key`, so two distinct recordings could theoretically
collide on the same key. Decided: accept this as existing, already-shipped behavior (no
registry-format migration) — see §4's duplicate-detection UI instead. Round 1 also caught a
queue-reuse bug, a layout problem, and an incomplete atomic-write API, folded into the first
revision. Round 2 (reviewing the written round-1 spec against the actual `pendulastic_app.py`/
`pt_report_common.py` code) found the round-1 fixes didn't fully hold up: a contradiction in
`discover_all_trials()`'s contract, a missing `trial_key` field on returned records (the UI can't
call `set_trials_excluded` without it), a genuine data-loss path where a malformed registry would
be silently overwritten, an incomplete Generate-in-flight lock, wrong import names against the
real file, an unspecified `<<ListboxSelect>>` binding (none currently exists on this panel), and
several other gaps. Round 3 (checking round 2's fixes against the real code again) found several
of them still didn't hold: per-record discovery-failure isolation is unimplementable as written
because the failure point is inside `discover_all_trials()`'s own internals, stale table jobs could
still repopulate the table after the user cleared their selection, fully-excluded participants
would silently vanish from the sidebar with no way to undo, the corruption check only covered
unparseable JSON (not valid-but-wrong-shape JSON), the UI test approach targeted the wrong test
double, the viewer's scrollbars weren't accounted for in the view-switch, `_REPORT_AVAIL` (a
separate guard from `_PT_AVAIL`) wasn't addressed, and total discovery failure had no terminal
error path. All corrected in place below.

## 3. Data Layer (`pt_report_common.py`)

- `discover_all_trials(include_archive=True, *, include_excluded=False)` — new keyword-only
  `include_excluded` parameter (the `*` makes it impossible to pass positionally, so no existing
  call site can be affected by accident). **Contract, precisely stated to resolve round-1's
  self-contradiction:** when `include_excluded=False` (the default), every returned record is
  byte-for-byte identical to today — no new fields, excluded trials still silently dropped. When
  `include_excluded=True`, excluded trials are included, and **only in that branch** every record
  additionally carries `trial_key: str` (the exact string `pt_report_common.trial_key(...)`
  produces for that record — the UI needs this to call `set_trials_excluded`, round-1's spec text
  claimed callers "never re-derive the key" but never actually added the field) and
  `excluded: bool`. The two new fields exist together, only on the `include_excluded=True` path.
- **Per-record discovery-failure isolation (round 3 fix — round 2's version was unimplementable):**
  `discover_all_trials()` calls `_parse_trial_path()` internally, which calls `os.path.getmtime()`
  uncaught today — a single deleted/inaccessible file currently aborts the *entire* discovery call,
  not just that one record, so the table-load worker (§4) would never even receive a partial
  result to isolate a failure from. Fix belongs in `discover_all_trials()` itself: wrap
  `_parse_trial_path()`'s call site in a `try/except`, and skip (not abort) any path that raises,
  the same way an unparseable path already returns `None` and gets skipped today.
- `list_participants(include_archive=True, *, include_excluded=False)` — **new parameter, same
  pattern as `discover_all_trials()` (round 3 fix):** default unchanged for every existing caller
  (`run_pt_analysis.py`, `pt_cohort_common.py`, etc., which must keep seeing only participants with
  at least one non-excluded trial). When `True`, a participant whose *every* trial has been
  excluded still appears (with `n_trials` reflecting only non-excluded trials, i.e. `0`, so the UI
  can visually flag it). Without this, excluding a participant's last remaining trial makes them
  vanish from `AnalysisPanel`'s own participant list on the very next refresh — with no way to
  re-select them and undo it. `AnalysisPanel` (§4) is the only caller that passes `include_excluded=True`.
- `duplicate_trial_keys(records: list[dict]) -> dict[str, list[str]]` — a **pure function over an
  already-fetched record list**, not a second discovery call. `{trial_key: [path, ...]}` for every
  key with more than one path among `records`. The caller (§4) always passes it the exact same
  `discover_all_trials(include_excluded=True)` result the table is built from, in the same
  request — never a fresh call — so duplicate detection can't disagree with what's on screen, and
  duplicates among already-excluded siblings are still caught (round-1's version would have missed
  those by silently re-querying with the default filter).
- `set_trials_excluded(keys: list[str], excluded: bool) -> None` — the single entry point the UI
  calls, replacing a raw dict setter so the batch semantics can't be gotten wrong from the caller
  side. `keys` is deduplicated internally (a caller passing the same key twice, e.g. from two
  colliding rows, must not double-toggle or corrupt the count shown in the confirmation dialog).
  For each unique key: sets a fixed placeholder reason (`"excluded via Analysis panel"`) when
  `excluded=True`, or **removes the key entirely** when `excluded=False` (a falsy/blank value would
  still satisfy `key in excluded`, corrupting the gate).
  - **Malformed on-disk registry (data-loss fix, round 2; scope widened round 3):** if
    `excluded_trials.json` exists but either (a) fails to parse as JSON, or (b) parses to anything
    other than a `dict` with all-`str` keys and all-`str` values — round 3 caught that round 2's
    check only covered case (a); valid JSON like `[]`, `null`, or a list would still have been
    silently treated as `{}` and overwritten, exactly the same data-loss path for a different
    input — `set_trials_excluded` **raises `RegistryCorruptError` and refuses to write** in either
    case. It must never treat a parse failure *or* a wrong-shape result as "empty" and then save,
    since both would silently discard every exclusion the file actually contained. This is
    deliberately stricter than `load_excluded_trials()`'s own read-time behavior (which treats
    either case as `{}` for report generation, where failing open is worse than temporarily
    un-filtering) — a *write* path must never destroy data it can't fully account for. The UI (§4)
    surfaces this as a hard error telling the operator to fix or restore the file by hand.
  - **Atomic write, scope stated explicitly (round 2):** temp file via `tempfile.mkstemp(dir=...,
    prefix=...)` in the same directory + `os.replace`, wrapped in `try/finally` so the temp file is
    always removed even if `os.replace` itself raises. This protects against a crash mid-write
    leaving a half-written file. **It is not a substitute for cross-process concurrency control** —
    two instances of the app (or the app plus a hand-edit) writing at the same moment can still lose
    an update, last-writer-wins. Accepted as a scope limitation: this tool assumes one operator, one
    running app instance at a time, matching how every other JSON-registry writer in this repo
    (`imu_calibration_config.py`, `rmse_best_config.json`) already works with no locking.

## 4. UI (`pendulastic_app.py`'s `AnalysisPanel`)

**Layout:** the 260px left sidebar (participant list, figure-type radios, Generate button) is
unchanged. The trial table replaces the figure viewer's content area (right side, the space that
currently holds `_viewer_canvas`) when exactly one participant is selected in the list — a second
"view mode" for that same space, not a new cramped column. Selecting 0 or 2+ participants reverts
the right side to the existing figure-viewer placeholder/last-generated-figure state and disables
the trial table entirely (multi-select stays reserved for the existing 2-participant comparison
flow). **Widget lifecycle (round 2; scrollbars added round 3):** switching views never destroys
`_viewer_canvas` or its current Matplotlib figure — both are `grid_remove()`d, and the table frame
`grid()`d in the same cell; switching back re-`grid()`s the canvas. Round 3 caught that
`_build_widgets` currently creates the viewer's `vbar`/`hbar` scrollbars as **local variables**,
never stored on `self` — left as-is, `grid_remove()`ing only the canvas leaves two orphaned
scrollbars gridded next to the table. Fix: promote them to `self._viewer_vbar`/`self._viewer_hbar`
when created, and `grid_remove()`/`grid()` them in lockstep with the canvas.

**Participant list uses `include_excluded=True` (round 3 fix):** `_refresh_participants()` now
calls `common.list_participants(include_excluded=True)` (§3) instead of the default-filtered call,
so a participant whose every trial has been excluded still appears — labeled distinctly (e.g. a
trailing "(all excluded)" suffix when `n_trials == 0` after exclusion) rather than vanishing with
no way to re-select and undo it. This was round 2's blind spot: its "if still present" clause for
re-selecting after a toggle silently assumed the participant would always still be present, which
round 3 showed is false in exactly the case this feature exists to support (excluding all of
someone's bad trials).

**New selection binding (round 2 — none exists today):** `AnalysisPanel` currently only reads
`self._participant_list.curselection()` when the Generate button is clicked; there is no live
reaction to selection changes. This spec adds one: `self._participant_list.bind("<<ListboxSelect>>",
self._on_participant_selection_changed)`. That handler is what decides single/zero/multi and
drives the view switch above and the table-load job below. **Request-id discipline (round 3
fix):** the handler increments `self._table_request_id` on **every** call, regardless of outcome —
zero/multi selection, a busy-rejected change, or a valid single selection all bump it. Round 2's
version only issued a new id when actually starting a load job, so a slow, still-in-flight
single-participant job could complete *after* the user cleared their selection, find its id still
matched "latest" (since nothing newer had been issued), and incorrectly repopulate an already-cleared
table.

**Table:** `ttk.Treeview(show="headings")` with columns Leg | Condition | Trial # | N |
phi_max_ratio | area_ratio, plus a `⚠` indicator column populated only for rows whose `trial_key`
appears in `duplicate_trial_keys()`. Explicit column widths/stretch, a vertical scrollbar. No real
checkbox widget (Tkinter doesn't have one for Treeview) — excluded rows get a distinct row tag
(greyed foreground) instead.

**Formatting rules (round 2 — previously only "N/A" for exceptions):** `N` displayed to 1 decimal;
`phi_max_ratio`/`area_ratio` to 3 decimals. Any of `None`, `NaN`, or infinite → `N/A`. A discovery-
or scoring-level exception for that specific trial → `N/A`, logged at debug level (which of the two
failure classes isn't shown to the operator — see next paragraph). If `compute_pt_params`/
`load_optitrack` are unavailable at all (the existing `_PT_AVAIL` guard in this file is `False`),
the whole table shows one row of explanatory text instead of per-trial `N/A` spam.

**Population (background thread, own queue):** the selection handler enqueues a table-load job on a
**separate** `self._table_queue` (not `self._result_queue`, which stays Generate-only — reusing one
queue was the concurrency bug round 1 caught, since a stale table-load result could otherwise be
decoded as a figure result or vice versa). Each job carries a monotonically increasing `request_id`;
`_poll_table_queue()` drops any result whose `request_id` doesn't match the latest one issued
(handles rapid re-selection, and doubles as job cancellation — a superseded job's result is simply
discarded when it eventually arrives).

The worker calls `common.discover_all_trials(include_excluded=True)` **once**, filters to the
selected participant, computes `common.duplicate_trial_keys(records)` against that **same** list
(never a second discovery call — round 2's consistency fix), then per trial calls the module-level
`load_optitrack(path)` then `compute_pt_params(t, angle)` — matching this file's actual existing
import style (`from pendulastic_pt_score import ..., compute_pt_params, load_optitrack, ...` behind
the existing `_PT_AVAIL` guard, not a `pt_score.`/`pt.`-qualified reference, which round 1's
pseudocode used incorrectly and doesn't exist in this file). Three independent failure points are
each caught separately and never abort the whole job: (1) a discovery-level failure per record
(`_parse_trial_path`'s `os.path.getmtime()` can raise for a deleted/inaccessible file — that record
is skipped, not fatal to the job); (2) `load_optitrack` raising; (3) `compute_pt_params` raising or
returning `None`. (2) and (3) both render as `N/A` per the formatting rule above.

**Toggling:** multi-select rows + a "Toggle Excluded" button. Selected keys are **deduplicated
first** (round 2 — a colliding `trial_key` can appear on more than one selected row; without
dedup, the confirmation count and `set_trials_excluded` call would double-count it). Before saving,
if any deduplicated key is in `duplicate_trial_keys()`, a single confirmation dialog covering the
whole batch lists every affected key **and the real file paths it maps to** (round 2 — a bare count
doesn't let the operator judge whether proceeding is safe; e.g. "key A -> 2 files:
`Recordings/Participant_15/Right/pre/Trial_2.avi`, `Recordings/Participant_15/Right/pre_dup/Trial_2.avi`.
Continue?"), not one dialog per row. Declining cancels the entire toggle. On confirm: flip the
deduplicated keys to the opposite of their currently *displayed* state (mixed-state selections are
rejected with a message asking the user to select rows in the same current state), call
`set_trials_excluded(keys, excluded)`.

**Accepted limitation (round 2):** the flip target is computed from what the table currently shows,
not re-verified against the file at write time — if `excluded_trials.json` changed externally
between table load and this click (another app instance, a hand-edit), the flip could act on stale
information. Given the single-operator/single-instance assumption already stated in §3, this is
accepted rather than engineered around (would require optimistic-concurrency version checks this
tool's scope doesn't warrant); the operator can always re-select the participant to refresh the
table before toggling if they suspect the file changed underneath them.

UI row tags update **only after** `set_trials_excluded` returns successfully; on exception —
including `RegistryCorruptError` — rows are left unchanged and the panel's status label shows the
error (for `RegistryCorruptError`, specifically telling the operator to fix or restore
`excluded_trials.json` by hand before trying again).

**Busy-state lock, widened (round 2 — round 1's version only disabled the Generate button):** a
single `self._busy` flag, set for the duration of either a Generate run or a toggle-save, gates
three things: the Toggle button's enabled state, the `<<ListboxSelect>>` handler (a selection change
while busy is ignored, not queued), and the Generate button itself. This prevents the scenario round
2 flagged — changing participant selection mid-Generate, spawning an unrelated table job, and ending
up with a generated figure that no longer matches the visible selection.

**Post-toggle refresh (round 2 — `_refresh_participants()` clears the listbox selection):** calling
the existing `_refresh_participants()` after a toggle would deselect the participant the operator is
actively reviewing, snapping the view back to the placeholder state. Instead: capture the selected
participant's id before refreshing, rebuild the participant list via the existing method, then
re-select that same id if it's still present, and re-run the table-load job for it (not a full
`_on_participant_selection_changed` re-entry, just the load) so the table reflects the just-saved
state without losing the operator's place.

## 5. Data Flow

1. `<<ListboxSelect>>` fires → handler determines single/zero/multi. Zero or multi: revert to
   figure view, clear/disable table. Single, not busy: table-load job enqueued with a fresh
   `request_id` → background thread calls `discover_all_trials(include_excluded=True)` once,
   filters to the participant, computes `duplicate_trial_keys(records)` against that same list,
   scores each trial's params → posts to `_table_queue` → `_poll_table_queue()` (only if
   `request_id` still current) populates the Treeview.
2. User multi-selects rows, clicks Toggle Excluded → keys deduplicated → (duplicate-key
   confirmation with real paths, if needed) → `set_trials_excluded(keys, excluded)` → on success:
   row tags update, participant re-selected after `_refresh_participants()`, table reloaded for
   that participant. On `RegistryCorruptError` or any other exception: rows unchanged, error shown.
3. Next "Generate" click uses `collect_participant()` → `discover_all_trials()` (default
   `include_excluded=False`), which already reflects the just-saved registry.

## 6. Error Handling

- Per-trial load/score failure: caught, shown as `N/A`, never aborts the table population.
- Per-record discovery failure (e.g. `os.path.getmtime()` on a deleted file): that record is
  skipped, never aborts the whole table job.
- `set_trials_excluded` failure (including `RegistryCorruptError`): caught by the caller, rows left
  in their pre-toggle state, error surfaced in the panel's existing status label — for
  `RegistryCorruptError` specifically, telling the operator to fix or restore
  `excluded_trials.json` by hand first.
- Malformed `excluded_trials.json` on **read**: `load_excluded_trials()`'s existing defensive
  pattern (missing/malformed → `{}`) is unchanged — report generation still degrades to
  "un-filtered" rather than crashing. On **write** (`set_trials_excluded`), a malformed file is a
  hard stop (§3) — the asymmetry is deliberate: reading a corrupt file as empty is a display
  degradation, writing through it would be data loss.

## 7. Testing

**Data layer** (`tests/test_pt_report_common.py`, plain functions, `monkeypatch`/`tmp_path`, no
test classes):
- `discover_all_trials(include_excluded=False)` (the default) returns records with no `trial_key`/
  `excluded` fields at all — byte-for-byte identical to today's shape. `include_excluded=True`
  returns excluded trials too, each with both fields (`excluded=True`/`False` correctly split, and
  `trial_key` matching what `pt_report_common.trial_key(...)` independently produces for that
  record).
- `duplicate_trial_keys(records)` operates purely on the list it's given (no internal discovery
  call — verify via a records list crafted by hand, not routed through `discover_all_trials` at
  all): returns entries only for keys with >1 path among those records; empty for the common case;
  includes a collision between one excluded and one non-excluded record (round 1's version would
  have missed this).
- `set_trials_excluded(["k1", "k1"], True)` (duplicate input) results in exactly one entry for
  `"k1"`, not an error or a double-write.
- `set_trials_excluded(["k1"], True)` then `load_excluded_trials()` contains `"k1"`;
  `set_trials_excluded(["k1"], False)` removes it entirely (not a falsy value).
- `set_trials_excluded` preserves unrelated existing entries.
- Atomic write: the temp file is created in the same directory as the target (not `/tmp` or a
  fixed name); a simulated `os.replace` failure leaves the original registry file's content
  untouched AND the temp file is not left behind (monkeypatch `os.replace` to raise, then assert no
  stray `*.tmp*` file remains in the directory).
- **Malformed existing registry (data-loss fix): `set_trials_excluded` raises
  `RegistryCorruptError` and does not touch the file at all** — assert the on-disk bytes are
  byte-for-byte unchanged after the call, proving no silent overwrite-as-empty occurred. Separately
  confirm `load_excluded_trials()` (the read path) still returns `{}` for the same malformed file,
  per its existing unchanged behavior — the two paths intentionally diverge.

**UI** (`tests/test_analysis_panel.py`, extending the existing `_FakeReport`-stand-in + real
withdrawn `tk.Tk()` + `root.update()` polling convention):
- `_FakeReport` gains `discover_all_trials`, `duplicate_trial_keys`, `set_trials_excluded`, plus
  fake `load_optitrack`/`compute_pt_params` call-recording hooks matching whatever interface the
  worker actually calls (not just the three data-layer methods — round 2 flagged that the original
  test-double list didn't cover the scoring calls the worker makes per trial).
- `<<ListboxSelect>>` on a single participant populates the table; on 0 or 2 participants, table
  clears/disables and the view reverts to the figure placeholder.
- Rapid re-selection: an in-flight table-load whose `request_id` is stale is dropped, never
  overwrites a newer selection's rows (simulate via a fake that delays the first call and returns
  the second call's result first).
- A trial whose fake `load_optitrack` raises, or whose `compute_pt_params` raises or returns
  `None`, shows `N/A` instead of crashing the poll loop; a discovery-level failure (fake raises from
  the per-record path, not the scoring call) skips that record without aborting the job.
- Duplicate `trial_key`s among the fake's records are detected and drive the confirmation dialog
  (mocked) with the correct paths and counts, deduplicated when the same key appears via multiple
  selected rows.
- Multi-row toggle with a repeated colliding key calls `set_trials_excluded` exactly once with the
  deduplicated key list, not once per row and not with duplicates in the list.
- `set_trials_excluded` raising (including a fake `RegistryCorruptError`) leaves row tags unchanged
  and shows the specific error, not a silent success or a generic message.
- `self._busy` gates both the Toggle button and the `<<ListboxSelect>>` handler — a selection
  change fired while a fake Generate call is still "in flight" (delayed fake) is ignored, not
  queued or acted on late.
- After a successful toggle, the previously-selected participant remains selected (not cleared by
  the participant-list refresh) and the table reloads to reflect the new state.
- Switching from table view back to figure view does not destroy or regenerate the previously
  shown figure — assert the same figure/canvas object is still present after a selection round-trip
  to a different participant count and back.
