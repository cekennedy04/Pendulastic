# Simplified Recording Folder Structure Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reduce the recording save path from `Session_<pre|post>/Position_<1-3>/Height_<Low|Joint-Level|High>` to `<Right|Left>/<free-typed characterization>`, and add an explicit Leg field to `master_app.py` so operators stop encoding leg into the Participant ID box.

**Architecture:** Single-file change to `master_app.py` (GUI fields, validation, path building, metadata, IMU meta, and the UDP START packet), plus documentation-only updates to `motive_sync.py` (its folder-mirroring logic is already generic over whatever relative path it receives, so no logic changes there).

**Tech Stack:** Python 3, Tkinter/ttk (GUI), stdlib `os`/`json` (paths + metadata), no new dependencies.

## Global Constraints

- New folder structure: `Recordings/Participant_<ID>/<Leg>/<Characterization>/Trial_<N>.avi`, where `<Leg>` is exactly `Right` or `Left` and `<Characterization>` is free-typed text (e.g. `pre`, `post`, `6wk-followup`).
- `Position_X` and `Height_Y` folder levels, and the `var_pos`/`var_height`/`drop_pos`/`drop_height` GUI fields, are removed entirely — not deprecated, not kept as optional.
- Characterization has no default text and no dropdown presets (plain `tk.Entry`), matching the approved spec.
- Leg is a readonly `ttk.Combobox` with exactly two values, `["Right", "Left"]`, defaulting to `"Right"`.
- `analysis_pipeline.py`, `batch_pendulastic.py`, `compute_metrics.py`, `evaluate_all_participants.py`, and any `plot_*.py` script are **out of scope** — do not modify them, even where they reference `position`/`height`. This includes the "RUN BATCH EVALUATION" button's result-summary text in `master_app.py` (`start_batch_evaluation`, around line 963), which calls into the unchanged `analysis_pipeline.py` and must keep describing that pipeline's still-unchanged `Participant_/Position_/Height_` expectations verbatim.
- The 5 existing participant folders already on disk under `Recordings/` are not touched or migrated.
- No automated test suite exists for `master_app.py` or `motive_sync.py` (confirmed: no `tests/test_master_app.py` or `tests/test_motive_sync.py` in the repo). Verification is manual, per the approved spec — every task's "run it" step is a manual check, not `pytest`.

Spec: `docs/superpowers/specs/2026-08-04-simplified-recording-folder-structure-design.md`

---

### Task 1: Replace Session/Position/Height GUI fields with Leg + Characterization

**Files:**
- Modify: `master_app.py:155` (var_session init)
- Modify: `master_app.py:211-216` (Session row)
- Modify: `master_app.py:244-266` (Camera Position / Camera Height / Trial Number rows)
- Modify: `master_app.py:268-330` (all rows below Trial Number — row numbers shift up by 1)
- Modify: `master_app.py:1054-1068` (`_lock_inputs`)

**Interfaces:**
- Produces: `self.entry_characterization` (a `tk.Entry`, read via `.get().strip()`), `self.var_leg` (a `tk.StringVar`, values `"Right"`/`"Left"`, read via `.get()`), `self.drop_leg` (the Leg `ttk.Combobox`, for `_lock_inputs`). Tasks 2 and 3 consume these exact names.
- Removes: `self.var_session`, `self.drop_session`, `self.var_pos`, `self.drop_pos`, `self.var_height`, `self.drop_height` — Tasks 2 and 3 must not reference these.

- [ ] **Step 1: Replace the Session row (line 155, 211-216) with a Characterization entry**

Current (`master_app.py:155`):
```python
        self.var_record_imu = tk.BooleanVar(value=_IMU_AVAIL)
        self.var_session    = tk.StringVar(value="pre")
        self._imu_recording = False
```
New:
```python
        self.var_record_imu = tk.BooleanVar(value=_IMU_AVAIL)
        self._imu_recording = False
```

Current (`master_app.py:211-216`):
```python
        # --- Session tag (pre / post intervention) ---
        tk.Label(self.root, text="Session:").grid(row=6, column=0, sticky="e", **pad)
        self.drop_session = ttk.Combobox(self.root, textvariable=self.var_session,
                                          width=25, state="readonly",
                                          values=["pre", "post"])
        self.drop_session.grid(row=6, column=1, sticky="w", **pad)
```
New:
```python
        # --- Characterization (free-typed: pre / post / anything else) ---
        tk.Label(self.root, text="Characterization:").grid(row=6, column=0, sticky="e", **pad)
        self.entry_characterization = tk.Entry(self.root, width=28)
        self.entry_characterization.grid(row=6, column=1, sticky="w", **pad)
```

- [ ] **Step 2: Replace Camera Position with Leg, remove Camera Height, renumber Trial Number**

Current (`master_app.py:244-266`):
```python
        # --- Camera Position 1-3 ---
        tk.Label(self.root, text="Camera Position:").grid(row=11, column=0, sticky="e", **pad)
        self.var_pos = tk.StringVar(value="1")
        self.drop_pos = ttk.Combobox(self.root, textvariable=self.var_pos, width=25,
                                     state="readonly",
                                     values=["1", "2", "3"])
        self.drop_pos.grid(row=11, column=1, sticky="w", **pad)

        # --- Camera Height ---
        tk.Label(self.root, text="Camera Height:").grid(row=12, column=0, sticky="e", **pad)
        self.var_height = tk.StringVar(value="Joint-Level")
        self.drop_height = ttk.Combobox(self.root, textvariable=self.var_height, width=25,
                                        state="readonly",
                                        values=["Low", "Joint-Level", "High"])
        self.drop_height.grid(row=12, column=1, sticky="w", **pad)

        # --- Trial Number ---
        tk.Label(self.root, text="Trial Number:").grid(row=13, column=0, sticky="e", **pad)
        self.var_trial = tk.StringVar(value="1")
        self.drop_trial = ttk.Combobox(self.root, textvariable=self.var_trial, width=25,
                                       state="readonly",
                                       values=["1", "2", "3", "4", "5"])
        self.drop_trial.grid(row=13, column=1, sticky="w", **pad)
```
New:
```python
        # --- Leg ---
        tk.Label(self.root, text="Leg:").grid(row=11, column=0, sticky="e", **pad)
        self.var_leg = tk.StringVar(value="Right")
        self.drop_leg = ttk.Combobox(self.root, textvariable=self.var_leg, width=25,
                                     state="readonly",
                                     values=["Right", "Left"])
        self.drop_leg.grid(row=11, column=1, sticky="w", **pad)

        # --- Trial Number ---
        tk.Label(self.root, text="Trial Number:").grid(row=12, column=0, sticky="e", **pad)
        self.var_trial = tk.StringVar(value="1")
        self.drop_trial = ttk.Combobox(self.root, textvariable=self.var_trial, width=25,
                                       state="readonly",
                                       values=["1", "2", "3", "4", "5"])
        self.drop_trial.grid(row=12, column=1, sticky="w", **pad)
```

- [ ] **Step 3: Shift every row from the old row 14 onward up by 1**

These widgets keep the same code, only their `grid(row=...)` value changes. Apply each of these 8 substitutions in `master_app.py:268-325`:

| Widget | Old row | New row |
|---|---|---|
| Separator after Trial Number (`master_app.py:268-269`) | 14 | 13 |
| `btn_start` (`master_app.py:276`) | 15 | 14 |
| `btn_stop` (`master_app.py:283`) | 15 | 14 |
| `chk_delayed` (`master_app.py:291`) | 16 | 15 |
| `imu_frame` (`master_app.py:296-297`) | 20 | 19 |
| Separator after countdown checkbox (`master_app.py:309-310`) | 17 | 16 |
| `btn_evaluate` (`master_app.py:317-318`) | 18 | 17 |
| `lbl_status` (`master_app.py:324-325`) | 19 | 18 |

For example, `master_app.py:276`:
```python
        self.btn_start.grid(row=15, column=0, padx=10, pady=12)
```
becomes:
```python
        self.btn_start.grid(row=14, column=0, padx=10, pady=12)
```
Apply the same `row=N` → `row=N-1` edit for each row in the table above, at its own widget's `.grid(...)` call. Leave every other argument (`column`, `padx`, `pady`, `sticky`, `columnspan`) unchanged.

- [ ] **Step 4: Update `_lock_inputs` to lock/unlock the new widgets**

Current (`master_app.py:1054-1062`):
```python
    def _lock_inputs(self, locked):
        state = "disabled" if locked else "normal"
        ro_state = "disabled" if locked else "readonly"
        for w in (self.entry_id, self.entry_age, self.entry_weight):
            w.config(state=state)
        for w in (self.drop_sex, self.drop_diag, self.drop_pos,
                  self.drop_height, self.drop_trial, self.drop_cam,
                  self.drop_session):
            w.config(state=ro_state)
```
New:
```python
    def _lock_inputs(self, locked):
        state = "disabled" if locked else "normal"
        ro_state = "disabled" if locked else "readonly"
        for w in (self.entry_id, self.entry_age, self.entry_weight,
                  self.entry_characterization):
            w.config(state=state)
        for w in (self.drop_sex, self.drop_diag, self.drop_leg,
                  self.drop_trial, self.drop_cam):
            w.config(state=ro_state)
```

- [ ] **Step 5: Syntax-check the file**

Run: `python -m py_compile master_app.py`
Expected: no output, exit code 0. (The file still references `self.var_pos`/`self.var_height`/`self.var_session` elsewhere at this point — Task 3 removes those — so a `grep` count, not a live launch, is the right check here.)

Run: `grep -n "var_pos\|var_height\|var_session\|drop_pos\|drop_height\|drop_session" master_app.py`
Expected: only the leftover reads in `_start_imu`, `_build_paths`, `_write_metadata`, and `start_recording` (lines ~394-401, 542-546, 577-579, 686-687) — fixed in Task 3. No more matches inside `_build_ui` or `_lock_inputs`.

- [ ] **Step 6: Commit**

```bash
git add master_app.py
git commit -m "Replace Session/Position/Height fields with Leg + Characterization"
```

---

### Task 2: Validate the Characterization field

**Files:**
- Modify: `master_app.py:515-535` (`_validate_inputs`)

**Interfaces:**
- Consumes: `self.entry_characterization` (from Task 1).
- Produces: no new names — `_validate_inputs()` keeps its existing signature and return value (`pid`); it now also raises `ValueError` for a bad characterization, which every existing caller (`_start_countdown`, `start_recording`) already catches.

- [ ] **Step 1: Add the characterization check**

Current (`master_app.py:515-535`):
```python
    def _validate_inputs(self):
        """Return a sanitized participant ID or raise ValueError."""
        pid = self.entry_id.get().strip()
        if not pid:
            raise ValueError("Participant ID cannot be empty.")
        # Block characters that are illegal in Windows folder names.
        illegal = set('<>:"/\\|?*')
        if any(ch in illegal for ch in pid):
            raise ValueError('Participant ID contains illegal characters: < > : " / \\ | ? *')

        age = self.entry_age.get().strip()
        if age and not age.isdigit():
            raise ValueError("Age must be a whole number.")

        weight = self.entry_weight.get().strip()
        if weight:
            try:
                float(weight)
            except ValueError:
                raise ValueError("Weight must be a number (e.g. 72.5).")
        return pid
```
New:
```python
    def _validate_inputs(self):
        """Return a sanitized participant ID or raise ValueError."""
        pid = self.entry_id.get().strip()
        if not pid:
            raise ValueError("Participant ID cannot be empty.")
        # Block characters that are illegal in Windows folder names.
        illegal = set('<>:"/\\|?*')
        if any(ch in illegal for ch in pid):
            raise ValueError('Participant ID contains illegal characters: < > : " / \\ | ? *')

        age = self.entry_age.get().strip()
        if age and not age.isdigit():
            raise ValueError("Age must be a whole number.")

        weight = self.entry_weight.get().strip()
        if weight:
            try:
                float(weight)
            except ValueError:
                raise ValueError("Weight must be a number (e.g. 72.5).")

        characterization = self.entry_characterization.get().strip()
        if not characterization:
            raise ValueError("Characterization cannot be empty.")
        if any(ch in illegal for ch in characterization):
            raise ValueError('Characterization contains illegal characters: < > : " / \\ | ? *')
        return pid
```

- [ ] **Step 2: Manually verify the validation**

Run `python master_app.py`, leave "Characterization" blank, click "START RECORDING". Expected: an error dialog reading "Characterization cannot be empty." and no folder created under `Recordings/`. Then type `pre/post` (containing `/`) into Characterization and click START again. Expected: an error dialog reading `Characterization contains illegal characters: < > : " / \ | ? *` and still no folder created. Close the app.

- [ ] **Step 3: Commit**

```bash
git add master_app.py
git commit -m "Validate the Characterization field before recording starts"
```

---

### Task 3: Wire Leg + Characterization into paths, metadata, IMU meta, and the START packet

**Files:**
- Modify: `master_app.py:387-410` (`_start_imu`)
- Modify: `master_app.py:540-566` (`_build_paths`)
- Modify: `master_app.py:568-587` (`_write_metadata`)
- Modify: `master_app.py:642-732` (`start_recording`, specifically the `start_msg` build at 685-689)

**Interfaces:**
- Consumes: `self.var_leg`, `self.entry_characterization` (Task 1); `_validate_inputs()` (Task 2, unchanged signature).
- Produces: `_build_paths(pid)` keeps its existing return shape `(participant_dir, video_path, rel_path)` — Task 6's manual verification and any future caller rely on that tuple order being unchanged.

- [ ] **Step 1: Update `_build_paths`**

Current (`master_app.py:540-566`):
```python
    def _build_paths(self, pid):
        """Build and create the directory tree. Returns (participant_dir, video_path, rel_path)."""
        position = self.var_pos.get()
        height = self.var_height.get()
        trial = self.var_trial.get()

        session = self.var_session.get()

        # Pre- and post-intervention takes are kept in separate subtrees so a
        # repeated Position/Height/Trial combination cannot overwrite the other
        # session's data.
        participant_dir = os.path.join(ROOT_DIR, f"Participant_{pid}")
        trial_dir = os.path.join(participant_dir,
                                 f"Session_{session}",
                                 f"Position_{position}",
                                 f"Height_{height}")
        # exist_ok=True makes this safe to call repeatedly.
        os.makedirs(trial_dir, exist_ok=True)

        video_path = os.path.join(trial_dir, f"Trial_{trial}.avi")

        # Relative path is what the slave machine recreates under its own root.
        rel_path = os.path.join(f"Participant_{pid}",
                                f"Session_{session}",
                                f"Position_{position}",
                                f"Height_{height}")
        return participant_dir, video_path, rel_path
```
New:
```python
    def _build_paths(self, pid):
        """Build and create the directory tree. Returns (participant_dir, video_path, rel_path)."""
        leg = self.var_leg.get()
        characterization = self.entry_characterization.get().strip()
        trial = self.var_trial.get()

        # Leg and characterization are kept in separate subtrees so a repeated
        # Trial combination under a different leg/characterization cannot
        # overwrite the other one's data.
        participant_dir = os.path.join(ROOT_DIR, f"Participant_{pid}")
        trial_dir = os.path.join(participant_dir, leg, characterization)
        # exist_ok=True makes this safe to call repeatedly.
        os.makedirs(trial_dir, exist_ok=True)

        video_path = os.path.join(trial_dir, f"Trial_{trial}.avi")

        # Relative path is what the slave machine recreates under its own root.
        rel_path = os.path.join(f"Participant_{pid}", leg, characterization)
        return participant_dir, video_path, rel_path
```

- [ ] **Step 2: Update `_write_metadata`**

Current (`master_app.py:568-587`):
```python
    def _write_metadata(self, participant_dir, pid):
        """Write/refresh metadata.json in the participant folder."""
        metadata = {
            "participant_id": pid,
            "age": self.entry_age.get().strip(),
            "weight_kg": self.entry_weight.get().strip(),
            "sex": self.var_sex.get(),
            "diagnosis": self.var_diag.get(),
            "last_trial": {
                "session": self.var_session.get(),
                "camera_position": self.var_pos.get(),
                "camera_height": self.var_height.get(),
                "trial_number": self.var_trial.get(),
                "imu_recorded": bool(_IMU_AVAIL and self.var_record_imu.get()),
            },
            "last_updated": datetime.now().isoformat(timespec="seconds"),
        }
        meta_path = os.path.join(participant_dir, "metadata.json")
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(metadata, f, indent=4)
```
New:
```python
    def _write_metadata(self, participant_dir, pid):
        """Write/refresh metadata.json in the participant folder."""
        metadata = {
            "participant_id": pid,
            "age": self.entry_age.get().strip(),
            "weight_kg": self.entry_weight.get().strip(),
            "sex": self.var_sex.get(),
            "diagnosis": self.var_diag.get(),
            "last_trial": {
                "leg": self.var_leg.get(),
                "characterization": self.entry_characterization.get().strip(),
                "trial_number": self.var_trial.get(),
                "imu_recorded": bool(_IMU_AVAIL and self.var_record_imu.get()),
            },
            "last_updated": datetime.now().isoformat(timespec="seconds"),
        }
        meta_path = os.path.join(participant_dir, "metadata.json")
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(metadata, f, indent=4)
```

- [ ] **Step 3: Update `_start_imu`'s meta dict**

Current (`master_app.py:387-401`):
```python
    def _start_imu(self, trial_dir, pid, trial):
        """Open the IMU CSV for this trial. Never fatal to the main capture."""
        if not (_IMU_AVAIL and self.var_record_imu.get()):
            return
        path = os.path.join(trial_dir, f"Trial_{trial}_imu.csv")
        meta = {
            "participant": pid,
            "session":     self.var_session.get(),
            "position":    self.var_pos.get(),
            "height":      self.var_height.get(),
            "trial":       trial,
            "t0_epoch":    f"{time.time():.4f}",
            "video":       f"Trial_{trial}.avi",
            "video_fps":   f"{TARGET_FPS:.3f}",
        }
```
New:
```python
    def _start_imu(self, trial_dir, pid, trial):
        """Open the IMU CSV for this trial. Never fatal to the main capture."""
        if not (_IMU_AVAIL and self.var_record_imu.get()):
            return
        path = os.path.join(trial_dir, f"Trial_{trial}_imu.csv")
        meta = {
            "participant":     pid,
            "leg":             self.var_leg.get(),
            "characterization": self.entry_characterization.get().strip(),
            "trial":           trial,
            "t0_epoch":        f"{time.time():.4f}",
            "video":           f"Trial_{trial}.avi",
            "video_fps":       f"{TARGET_FPS:.3f}",
        }
```

- [ ] **Step 4: Update the START packet in `start_recording`**

Current (`master_app.py:664, 685-689`):
```python
            participant_dir, video_path, rel_path = self._build_paths(pid)
```
```python
            start_msg = (
                f"START|id={pid}|position={self.var_pos.get()}|"
                f"height={self.var_height.get()}|trial={self.var_trial.get()}|"
                f"relpath={rel_path}"
            )
```
New (add the two local reads right after `_build_paths`, then use them in `start_msg`):
```python
            participant_dir, video_path, rel_path = self._build_paths(pid)
            leg = self.var_leg.get()
            characterization = self.entry_characterization.get().strip()
```
```python
            start_msg = (
                f"START|id={pid}|leg={leg}|characterization={characterization}|"
                f"trial={self.var_trial.get()}|relpath={rel_path}"
            )
```

- [ ] **Step 5: Syntax-check and confirm no stale references remain**

Run: `python -m py_compile master_app.py`
Expected: no output, exit code 0.

Run: `grep -n "var_pos\|var_height\|var_session\|drop_pos\|drop_height\|drop_session" master_app.py`
Expected: no matches at all.

- [ ] **Step 6: Manually verify end-to-end path/metadata/packet behavior**

Run `python master_app.py`. Enter a participant ID (e.g. `TEST1`), leave Leg at its default `Right`, type `pre` into Characterization, click "START RECORDING", wait ~2 seconds, click "STOP".

Confirm on disk:
- `Recordings/Participant_TEST1/Right/pre/Trial_1.avi` exists and is a non-empty video file.
- If the IMU checkbox was available and checked, `Recordings/Participant_TEST1/Right/pre/Trial_1_imu.csv` also exists.
- `Recordings/Participant_TEST1/metadata.json` contains `"leg": "Right"` and `"characterization": "pre"` under `"last_trial"`, with no `camera_position`/`camera_height`/`session` keys anywhere in the file.

- [ ] **Step 7: Commit**

```bash
git add master_app.py
git commit -m "Wire Leg + Characterization into paths, metadata, IMU meta, and START packet"
```

---

### Task 4: Update the module docstring

**Files:**
- Modify: `master_app.py:1-21` (top-of-file docstring)

**Interfaces:**
- None — comment-only change, no code behavior affected.

- [ ] **Step 1: Update the save-path description**

Current (`master_app.py:9-12`):
```
   * Records a 30 fps webcam video on a dedicated background thread so the
     GUI never freezes.
   * Saves video to:  [Root]/Participant_[ID]/Position_[X]/Height_[Y]/Trial_[Z].avi
   * Writes metadata.json into the participant folder.
```
New:
```
   * Records a 30 fps webcam video on a dedicated background thread so the
     GUI never freezes.
   * Saves video to:  [Root]/Participant_[ID]/[Right|Left]/[Characterization]/Trial_[Z].avi
   * Writes metadata.json into the participant folder.
```

Do **not** touch `master_app.py` around line 963-965 (the "RUN BATCH EVALUATION" result summary's "Participant_/Position_/Height_ folders" hint text) — that describes `analysis_pipeline.py`'s still-unchanged expectations (see Global Constraints) and must stay as-is.

- [ ] **Step 2: Commit**

```bash
git add master_app.py
git commit -m "Update module docstring for the new save path"
```

---

### Task 5: Update `motive_sync.py` documentation

**Files:**
- Modify: `motive_sync.py:14-19` (module docstring, start_local_motive summary)
- Modify: `motive_sync.py:86-88` (`parse_start_packet` docstring example)
- Modify: `motive_sync.py:109-116` (`build_take_name` docstring comment)
- Modify: `motive_sync.py:134-138` (`mirror_relpath` docstring example)
- Modify: `motive_sync.py:201-203` (`start_local_motive` docstring example)
- Modify: `motive_sync.py:302-309` (`__main__` demo block)

**Interfaces:**
- None — every change in this task is a comment/docstring/print-string edit. `parse_start_packet`, `mirror_relpath`, and `build_take_name` are unchanged functions; they already work on whatever `relpath`/fields they're handed, from Task 3's new packet format.

- [ ] **Step 1: Update the module docstring**

Current (`motive_sync.py:14-19`):
```
 start_local_motive(packet_string):
   1. Parse the START packet (id / position / height / trial / relpath).
   2. Mirror the folder tree under OptiTrack_Recordings/ using 'relpath' and
      SANITIZE that path to forward slashes — Motive hard-freezes on backslashes.
   3. Build the take name:  trial_{N}_optitrack
      (matches the CSV filename the evaluation pipeline expects).
```
New:
```
 start_local_motive(packet_string):
   1. Parse the START packet (id / leg / characterization / trial / relpath).
   2. Mirror the folder tree under OptiTrack_Recordings/ using 'relpath' and
      SANITIZE that path to forward slashes — Motive hard-freezes on backslashes.
   3. Build the take name:  trial_{N}_optitrack
      (matches the CSV filename the evaluation pipeline expects).
```

- [ ] **Step 2: Update `parse_start_packet`'s docstring example**

Current (`motive_sync.py:84-96`):
```python
def parse_start_packet(packet_string):
    """
    Parse a packet string of the form:
        START|id=001|position=1|height=Joint-Level|trial=1|relpath=Participant_001\\...
    into a dict of key=value fields. Tokens without '=' (e.g. the leading
    'START') are ignored, so a string with or without the prefix both work.
    """
```
New:
```python
def parse_start_packet(packet_string):
    """
    Parse a packet string of the form:
        START|id=001|leg=Right|characterization=pre|trial=1|relpath=Participant_001\\...
    into a dict of key=value fields. Tokens without '=' (e.g. the leading
    'START') are ignored, so a string with or without the prefix both work.
    """
```

- [ ] **Step 3: Update `build_take_name`'s comment**

Current (`motive_sync.py:109-119`):
```python
def build_take_name(fields):
    """
    Build the Motive take name: trial_{N}_optitrack

    Matches the CSV filename the evaluation pipeline expects
    (trial_N_optitrack.csv) so no manual renaming is needed after export.
    Participant / position / height context lives in the session folder path
    (SetCurrentSession), not in the take name.
    """
    trial = _sanitize(fields.get("trial", "1"))
    return f"trial_{trial}_optitrack"
```
New:
```python
def build_take_name(fields):
    """
    Build the Motive take name: trial_{N}_optitrack

    Matches the CSV filename the evaluation pipeline expects
    (trial_N_optitrack.csv) so no manual renaming is needed after export.
    Participant / leg / characterization context lives in the session folder
    path (SetCurrentSession), not in the take name.
    """
    trial = _sanitize(fields.get("trial", "1"))
    return f"trial_{trial}_optitrack"
```

- [ ] **Step 4: Update `mirror_relpath`'s docstring example**

Current (`motive_sync.py:134-138`):
```python
def mirror_relpath(fields):
    """
    Recreate the laptop's folder tree under LOCAL_ROOT using the 'relpath' field
    (e.g. Participant_001\\Position_1\\Height_Joint-Level). Returns the created
    absolute path, or None if no relpath was supplied. Errors are logged, not raised.
    """
```
New:
```python
def mirror_relpath(fields):
    """
    Recreate the laptop's folder tree under LOCAL_ROOT using the 'relpath' field
    (e.g. Participant_001\\Right\\pre). Returns the created absolute path, or
    None if no relpath was supplied. Errors are logged, not raised.
    """
```

- [ ] **Step 5: Update `start_local_motive`'s docstring example**

Current (`motive_sync.py:193-207`):
```python
def start_local_motive(packet_string):
    """
    Mirror the folder tree, point Motive at it, name the take, and start recording
    via the NatNet remote-command state machine.

    Sequence (strict): LiveMode -> SetCurrentSession,<unix_path> ->
                        SetRecordTakeName,<take> -> StartRecording.

    Args:
        packet_string: the START packet, e.g.
            "START|id=001|position=1|height=Joint-Level|trial=1|relpath=Participant_001\\Position_1\\Height_Joint-Level"

    Returns:
        The take name that was set (e.g. "P_001_Pos_1_H_Joint-Level_T_1").
    """
```
New:
```python
def start_local_motive(packet_string):
    """
    Mirror the folder tree, point Motive at it, name the take, and start recording
    via the NatNet remote-command state machine.

    Sequence (strict): LiveMode -> SetCurrentSession,<unix_path> ->
                        SetRecordTakeName,<take> -> StartRecording.

    Args:
        packet_string: the START packet, e.g.
            "START|id=001|leg=Right|characterization=pre|trial=1|relpath=Participant_001\\Right\\pre"

    Returns:
        The take name that was set (e.g. "trial_1_optitrack").
    """
```

- [ ] **Step 6: Update the `__main__` demo block**

Current (`motive_sync.py:302-309`):
```python
if __name__ == "__main__":
    # Not a runnable app - this is a module imported by the master webcam script.
    print(__doc__)
    demo = "START|id=001|position=1|height=Joint-Level|trial=1|relpath=Participant_001\\Position_1\\Height_Joint-Level"
    f = parse_start_packet(demo)
    print("example take name   :", build_take_name(f))
    print("example session path:", sanitize_session_path(os.path.join(LOCAL_ROOT,
          "Participant_001", "Position_1", "Height_Joint-Level")))
```
New:
```python
if __name__ == "__main__":
    # Not a runnable app - this is a module imported by the master webcam script.
    print(__doc__)
    demo = "START|id=001|leg=Right|characterization=pre|trial=1|relpath=Participant_001\\Right\\pre"
    f = parse_start_packet(demo)
    print("example take name   :", build_take_name(f))
    print("example session path:", sanitize_session_path(os.path.join(LOCAL_ROOT,
          "Participant_001", "Right", "pre")))
```

- [ ] **Step 7: Run the updated demo block and confirm the mirrored structure**

Run: `python motive_sync.py`
Expected output includes:
```
example take name   : trial_1_optitrack
example session path: .../OptiTrack_Recordings/Participant_001/Right/pre
```
(Path separators will be OS-native; the key check is that `Position_1`/`Height_Joint-Level` are gone and `Right`/`pre` appear in their place, with no `.tak`/`.csv` files or folders actually created — this only builds the string.)

- [ ] **Step 8: Commit**

```bash
git add motive_sync.py
git commit -m "Update motive_sync.py docs/examples for Leg + Characterization"
```

---

### Task 6: Full end-to-end manual verification

**Files:** none (verification only).

**Interfaces:** none.

- [ ] **Step 1: Fresh full-flow run**

Run `python master_app.py`. Enter participant ID `VERIFY1`, set Leg to `Left`, type `post` into Characterization, set Trial Number to `2`, start and stop a short recording.

Confirm:
- `Recordings/Participant_VERIFY1/Left/post/Trial_2.avi` exists.
- `Recordings/Participant_VERIFY1/metadata.json` has `"leg": "Left"`, `"characterization": "post"`, `"trial_number": "2"` under `"last_trial"`.
- The GUI shows no "Camera Position" or "Camera Height" fields anywhere.

- [ ] **Step 2: Confirm the OptiTrack mirror (if Motive is available)**

If Motive is running and configured per `motive_sync.py`'s header comment, repeat Step 1 and confirm `OptiTrack_Recordings/Participant_VERIFY1/Left/post/` is created (mirroring the laptop-side structure) and contains `trial_2_optitrack.tak`/`.csv` after the take is stopped. If Motive is not available in this environment, skip this step and note it as unverified in the task's completion notes — it does not block the rest of this plan, since Task 5's demo-block run in Task 5 Step 7 already confirms the path-building logic in isolation.

- [ ] **Step 3: Confirm existing recordings are untouched**

Run: `git status --short Recordings/` (or list the 5 existing `Participant_13_*`/`Participant_P001_*` folders)
Expected: no changes reported — those folders and their old `Session_/Position_/Height_` contents are exactly as they were before this plan started.

- [ ] **Step 4: Final review**

Read back `docs/superpowers/specs/2026-08-04-simplified-recording-folder-structure-design.md` section by section and confirm every requirement has a corresponding change: new folder structure (Tasks 1, 3), Leg field (Task 1), free-text Characterization (Tasks 1, 2), removed Position/Height (Task 1), updated metadata/IMU meta/START packet (Task 3), motive_sync docs (Task 5), analysis-pipeline scope boundary respected (Global Constraints, Task 4). No further commit needed for this step — it's a review checkpoint.
