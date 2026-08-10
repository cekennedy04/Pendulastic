# Multi-Trial Recording Mode — Design Spec

## 1. Goal

Let a clinician record trial after trial in quick succession from `pendulastic_app.py`'s
`AcquisitionPanel`, without being forced into the full analysis/review screen (`PostProcessingPanel`)
after every single trial. The live camera preview and knee-angle telemetry (`WebcamViewerWindow` /
the telemetry sparkline) must keep working exactly as they do today — this feature only changes what
happens *after* STOP, not what's visible *during* recording. Clinicians also get the ability to
delete a just-recorded trial immediately, without leaving the acquisition screen.

## 2. Background

Today, `AcquisitionPanel.on_stop()` → `App.on_stop()` always ends the same way regardless of source
mix: background processing runs if needed (`_run_rgb_processing` for MediaPipe tracking,
`_run_imu_tuning` for IMU calibration tuning, or neither for a plain OptiTrack trial), and every one
of those three paths converges on `App._transition_to_review()`, which force-navigates to
`PostProcessingPanel` and — when the trial came from a live recording — pops a modal "Recording
Saved" confirmation dialog. The only way back to recording another trial is `PostProcessingPanel`'s
"← New Trial" button, which increments the trial spinner and returns to `AcquisitionPanel.enter_idle()`.
There is no concept of a per-session trial list or a way to delete a trial's saved files from the UI.

## 3. Scope

Applies to live recording sources only: IMU, RGB, OptiTrack (and any combination of them). The
standalone `video_file` research source is out of scope — it already bypasses the live-recording
loop and countdown entirely (`App.on_start()` routes it through `_start_video_file_processing()` and
returns early), so "trial after trial" live capture doesn't apply to it.

## 4. User Flow

A new **"Record multiple trials"** checkbox is added to `AcquisitionPanel`, near the existing
"5-second countdown" checkbox. Off by default — with it off, behavior is unchanged byte-for-byte
from today.

With it checked:

1. Clinician fills in the form, hits **START**, records, hits **STOP** — same as today.
2. A placeholder row ("Trial N — Processing…") appears immediately in a new **trial list** panel on
   the same acquisition screen. Background processing (if any) runs as it does today.
3. Once processing finishes, the row updates in place to "Trial N — `<sources>` — Saved". The app
   does **not** navigate to `PostProcessingPanel` and does **not** show the "Recording Saved" popup —
   the row itself is the confirmation. The trial spinner auto-increments and the screen returns to
   idle, ready for **START** again.
4. Each row has a **✕ delete** button, disabled while that trial's `status == "processing"`.
   Clicking it (after a confirm dialog) deletes that trial's saved files from disk immediately and
   removes the row. Deleted trial numbers are never reused — deleting Trial 2 leaves 1, 3, 4…
5. Clicking a row's text (not the ✕) opens `PostProcessingPanel` for that trial, with a
   "← Back to Trials" button (instead of "← New Trial") that returns to the acquisition screen with
   the list intact and the trial spinner untouched.
6. Leaving the acquisition screen (e.g. "← Mode Select") clears the trial list. Unchecking the
   toggle only hides the list panel — its in-memory data survives until the screen is left, so
   re-checking the box brings it back.

## 5. UI (`AcquisitionPanel`)

- **Toggle**: `tk.BooleanVar` `_multi_trial_var`, checkbox labeled "Record multiple trials",
  positioned near `countdown_chk`. Added to the existing `_lockable` list so it locks during
  recording/countdown like other form fields.
- **Trial list panel**: a new card (styled like the existing "RECORDING SOURCE" `ws.card_frame`)
  shown only when `_multi_trial_var.get()` is true and at least one trial exists this session.
  Plain packed rows, no scrollbar (clinical sessions run on the order of 5-15 trials per sitting;
  add a scrollbar later if this becomes a real problem — not needed for v1).
- **Row anatomy**: `Trial {n} · {sources joined, e.g. "IMU + RGB"} · {Processing…|Saved}` with a
  right-aligned `✕` button, disabled/greyed while processing. Clicking the row's label opens
  `PostProcessingPanel`; clicking `✕` deletes.
- The panel is absent entirely (not just empty) until the toggle is on and the first trial exists.

## 6. State & Data Flow (`App`)

- New `self._session_trials: list[dict]`, entries shaped
  `{trial_num, sources, status: "processing"|"saved", source_angles, fps, meta, base_filename, file_paths}`.
  `file_paths` collects every file this app itself wrote for that trial (IMU/RGB angle CSVs, RGB
  video, RGB phone-camera timestamps file where applicable) — the same set of files
  `_show_recording_saved_confirmation()` already enumerates today, just captured into the trial
  record instead of only used for a one-off message.
- `App.on_stop()`: when `_multi_trial_var.get()` is true, a placeholder entry is appended to
  `_session_trials` right away, with `trial_num`, `sources`, and `meta` already known (from
  `self._acq.get_metadata()`, read at the top of `on_stop()` as it is today) and `status="processing"`;
  `source_angles`/`file_paths` are filled in later, at finalization. This happens before background
  processing starts (all three processing paths already converge on `_transition_to_review()`, so
  finalization is the single choke point). `enter_processing()` already disables START/STOP until
  processing completes, so at most one trial is ever `"processing"` at a time — no concurrent-
  placeholder reconciliation needed.
- `_transition_to_review()`: when called with `from_recording=True` and multi-trial mode is on, it
  does not navigate to `PostProcessingPanel` and does not call
  `_show_recording_saved_confirmation()`. Instead a new `_finish_trial_multi_mode(source_angles,
  meta, base_fn)` updates the matching placeholder entry in place — `status="saved"`,
  `source_angles` and `file_paths` filled in — refreshes the trial-list widget, calls
  `self._acq.enter_idle()`, and increments the trial spinner (reusing
  `AcquisitionPanel.increment_trial()`). When multi-trial mode is off, `_transition_to_review()`
  behaves exactly as it does today.

## 7. Delete Behavior

- `✕` on a `"saved"` row triggers a confirm dialog: *"Delete Trial 2? This removes its saved files
  and can't be undone."*
- On confirm, `App.on_delete_trial(trial_num)`: `os.remove()`s every path in that trial's
  `file_paths` (each wrapped in `try/except` so an already-missing file doesn't raise), removes the
  entry from `_session_trials`, and refreshes the trial-list widget.
- `✕` is disabled while `status == "processing"` — no cancellation support is added for the
  background processing threads (`_run_rgb_processing` / `_run_imu_tuning` don't support it today,
  and this feature doesn't require adding it).
- Deleting a trial never renumbers or reuses trial numbers; the next new recording continues from
  the highest trial number ever used in this session, plus one.

## 8. Review / Back-Navigation (`PostProcessingPanel`)

- `App` sets a context flag before showing `PostProcessingPanel`, indicating whether it was opened
  from the multi-trial list or the traditional single-trial flow.
- **From the trial list**: back button reads "← Back to Trials"; click calls
  `self._acq.enter_idle()` directly — no trial-spinner increment (already done at save time), list
  stays intact.
- **From the traditional flow** (toggle off): unchanged from today — "← New Trial" increments the
  spinner and returns to idle.
- No changes to `PostProcessingPanel`'s plotting or PT-metrics logic — only the back button's label
  and click target become context-aware.

## 9. Out of Scope

- Cancelling in-flight background processing on delete.
- A scrollable trial list (revisit if session trial counts grow large in practice).
- Persisting the trial list across navigating away from the acquisition screen, or across app
  restarts.
- Renumbering trials after a delete.
- Any change to the `video_file` standalone research path.

## 10. Testing

- `App._finish_trial_multi_mode()` (or equivalent): appends a placeholder on STOP, updates it to
  `"saved"` once processing completes, for each of the three processing paths (RGB tracking, IMU
  tuning, plain OptiTrack with neither).
- Multi-trial mode on: STOP does not call `_show_recording_saved_confirmation()` and does not pack
  `PostProcessingPanel`; screen returns to idle with the trial spinner incremented.
- Multi-trial mode off: behavior byte-for-byte matches current tests for `on_stop()` /
  `_transition_to_review()`.
- Delete: removes the correct files from disk (verify via `tmp_path`-backed `DataManager.DATA_DIR`),
  removes the entry from `_session_trials`, leaves other trials' files untouched; delete is a no-op
  (button disabled) while `status == "processing"`.
- Trial numbering: deleting a middle trial leaves a gap; the next recorded trial uses
  `max(trial_num for all entries ever created) + 1`, not `len(_session_trials) + 1`.
- Row click opens `PostProcessingPanel` with that trial's stored `source_angles`/`meta`, and its
  back button returns to the acquisition screen with the list still populated.
- Leaving the acquisition screen (mode select) clears `_session_trials`; re-entering multi-trial mode
  starts with an empty list.
