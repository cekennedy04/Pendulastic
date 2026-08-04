# Simplified Recording Folder Structure — Design Spec
**Date:** 2026-08-04
**Status:** Approved

---

## 1. Goal

The current recording save convention nests four levels deep under each participant —
`Session_<pre|post>/Position_<1-3>/Height_<Low|Joint-Level|High>/Trial_<N>.avi` — and has no field
for which leg was recorded. In practice, operators have been working around the missing leg field
by typing it into the Participant ID box itself (e.g. `13_right_post`, `13_left_pre`), producing
folders like `Recordings/Participant_13_right_post/Session_post/Position_1/Height_Joint-Level/`.
Position and Height are camera-rig setup details that are no longer used for anything downstream.

This spec reduces the recording tree to two levels — leg, then a free-typed characterization label
(pre/post/anything else) — and adds an explicit Leg field so the Participant ID field goes back to
holding only the participant ID.

**New structure:**
```
Recordings/
  Participant_<ID>/
    Right/  (or Left/)
      <characterization>/        e.g. "pre", "post", "6wk-followup"
        Trial_1.avi
        Trial_1_imu.csv
        Trial_2.avi
        ...
  metadata.json                  (in Participant_<ID>/, unchanged location)
```

---

## 2. Scope

**In scope:** the acquisition-side path/UI/metadata logic in `master_app.py`, plus documentation
updates in `motive_sync.py` (its OptiTrack mirror logic is generic over whatever relative path it's
given, so no functional change is needed there — only its docstrings/examples, which currently show
the old `position=/height=` packet format).

**Out of scope:** the downstream analysis scripts (`analysis_pipeline.py`, `batch_pendulastic.py`,
`compute_metrics.py`, `evaluate_all_participants.py`, and several `plot_*.py` scripts) explicitly
parse `Position_X`/`Height_Y` path segments to identify trials and match videos to OptiTrack CSVs.
Those are **not** updated by this task — newly recorded trials under the new structure will not be
picked up by those scripts until a separate follow-up task updates them. This is a deliberate,
confirmed scope boundary, not an oversight.

**Not migrated:** the 5 existing participant folders already on disk under `Recordings/`
(`Participant_13_left_post`, `Participant_13_left_pre`, `Participant_13_right_post`,
`Participant_13_right_pre`, `Participant_P001_msparticipant2`) are left exactly as they are.

---

## 3. GUI Changes (`master_app.py`)

- **Remove** the "Camera Position" dropdown (`var_pos`/`drop_pos`, values `1`/`2`/`3`) and the
  "Camera Height" dropdown (`var_height`/`drop_height`, values `Low`/`Joint-Level`/`High`) —
  currently `master_app.py:245-258`.
- **Add** a "Leg" field: `var_leg = tk.StringVar(value="Right")`, a readonly `ttk.Combobox` with
  values `["Right", "Left"]`. Readonly because there are exactly two valid values — a free-text
  field here would just invite typos with no upside.
- **Replace** the "Session" dropdown (currently `master_app.py:212-216`, readonly, locked to
  `pre`/`post`) with a plain `tk.Entry` labeled "Characterization", no default text and no presets —
  the operator types `pre`, `post`, or any other label for that recording.
- Row numbers in `_build_ui`'s `grid()` calls are renumbered sequentially to close the gap left by
  the two removed rows and one added row.

---

## 4. Validation (`_validate_inputs`)

Extend the existing method (`master_app.py:515-535`) with the same illegal-character check already
applied to the Participant ID, applied to the characterization text:

```python
illegal = set('<>:"/\\|?*')
characterization = self.entry_characterization.get().strip()
if not characterization:
    raise ValueError("Characterization cannot be empty.")
if any(ch in illegal for ch in characterization):
    raise ValueError('Characterization contains illegal characters: < > : " / \\ | ? *')
```

Both checks run before any folder is created or recording starts, consistent with how the Participant
ID is already validated.

---

## 5. Path + Metadata Changes

### `_build_paths(self, pid)` (`master_app.py:540-566`)

```python
def _build_paths(self, pid):
    """Build and create the directory tree. Returns (participant_dir, video_path, rel_path)."""
    leg = self.var_leg.get()
    characterization = self.entry_characterization.get().strip()
    trial = self.var_trial.get()

    participant_dir = os.path.join(ROOT_DIR, f"Participant_{pid}")
    trial_dir = os.path.join(participant_dir, leg, characterization)
    os.makedirs(trial_dir, exist_ok=True)

    video_path = os.path.join(trial_dir, f"Trial_{trial}.avi")

    rel_path = os.path.join(f"Participant_{pid}", leg, characterization)
    return participant_dir, video_path, rel_path
```

### `_write_metadata` (`master_app.py:568-587`)

The `last_trial` dict's `"camera_position"`/`"camera_height"` keys are replaced with `"leg"` and
`"characterization"`; the separate `"session"` key (redundant with characterization) is dropped:

```python
"last_trial": {
    "leg": self.var_leg.get(),
    "characterization": self.entry_characterization.get().strip(),
    "trial_number": self.var_trial.get(),
    "imu_recorded": bool(_IMU_AVAIL and self.var_record_imu.get()),
},
```

### `_start_imu` (`master_app.py:387-410`)

The IMU CSV's `meta` dict drops `"session"`/`"position"`/`"height"` in favor of `"leg"`/
`"characterization"`:

```python
meta = {
    "participant": pid,
    "leg": self.var_leg.get(),
    "characterization": self.entry_characterization.get().strip(),
    "trial": trial,
    "t0_epoch": f"{time.time():.4f}",
    "video": f"Trial_{trial}.avi",
    "video_fps": f"{TARGET_FPS:.3f}",
}
```

### `start_recording` (`master_app.py:642-732`)

The UDP START packet sent to `motive_sync` drops `position=`/`height=` in favor of `leg=`/
`characterization=`:

```python
start_msg = (
    f"START|id={pid}|leg={leg}|characterization={characterization}|trial={trial}|"
    f"relpath={rel_path}"
)
```

(`leg`/`characterization` are read once, right after `_build_paths`, alongside the existing `pid`.)

### Module docstring and status text

The top-of-file docstring (`master_app.py:11`, "Saves video to: ...") and the status/help string
around `master_app.py:965` ("...Participant_/Position_/Height_ folders.") are updated to describe
the new two-level structure.

---

## 6. `motive_sync.py`

No functional change: `mirror_relpath` (`motive_sync.py:134-152`) recreates whatever `relpath`
string it's given, so it already mirrors the new two-level structure automatically once
`master_app.py` sends a shorter `relpath`. `build_take_name` (`motive_sync.py:109-119`) already only
uses `trial` — untouched.

Documentation-only updates:
- Module docstring's example packet (`motive_sync.py:15, 87, 203`) changes from
  `position=1|height=Joint-Level|...|relpath=Participant_001\Position_1\Height_Joint-Level` to a
  `leg=Right|characterization=pre|...|relpath=Participant_001\Right\pre`-style example.
- `build_take_name`'s comment ("Participant / position / height context lives in the session folder
  path") is updated to say "Participant / leg / characterization context".
- The `__main__` demo block (`motive_sync.py:302-309`) is updated to use the new example packet and
  path.

---

## 7. Testing Plan

No automated test suite exists for this Tkinter app or for `motive_sync.py`. Verification is manual:

1. Run `master_app.py`; confirm the Leg dropdown and Characterization text field are present, and
   the old Camera Position / Camera Height dropdowns are gone.
2. Fill in a participant ID, pick a leg, type a characterization label, start and stop a short
   recording. Confirm on disk:
   - `Recordings/Participant_<ID>/<Leg>/<characterization>/Trial_1.avi` exists.
   - `Trial_1_imu.csv` exists in the same folder (if IMU recording is enabled).
   - `Recordings/Participant_<ID>/metadata.json` has `leg`/`characterization` under `last_trial`,
     with no leftover `camera_position`/`camera_height`/`session` keys.
3. Trigger validation errors deliberately: leave characterization blank, and type an illegal
   character into it; confirm both are rejected with a clear message before any folder is created.
4. Dry-run `motive_sync.parse_start_packet` and `motive_sync.mirror_relpath` against a synthetic
   packet string built the new way (no live Motive connection needed) and confirm the mirrored
   folder under `OptiTrack_Recordings/` matches the new two-level structure.

---

## 8. Out of Scope

- Updating `analysis_pipeline.py`, `batch_pendulastic.py`, `compute_metrics.py`,
  `evaluate_all_participants.py`, or any `plot_*.py` script that parses `Position_X`/`Height_Y` path
  segments — confirmed as a separate follow-up task.
- Migrating the 5 existing participant folders already on disk to the new structure.
- Any change to the IMU CSV internal format, the OptiTrack CSV export mechanics, or camera
  capture/codec behavior.
