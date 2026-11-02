# Participant folder naming

*Renamed 2026-09-01. Applies to `Recordings/` and `OptiTrack_Recordings/`.*

*The MS study's prefix was `DOSA_` earlier the same day and is now `MINT_`.
`DOSA_` is still resolved as a legacy name, so an un-renamed archive — and
reports written before the change, such as
`docs/reports/2026-09-01-knee-axis-acceptance.md` — still line up with the data.*

Participant folders carry the parent study, so a folder can be identified at a
glance without opening its `metadata.json`:

| Prefix | Group | Participants |
| --- | --- | --- |
| `Control_{pid}` | Unaffected control | 2, 6, 7, 8, 9, 10, 12, 16, 23 |
| `CHAT_{pid}` | Stroke | 19, 21, 22, 24 |
| `MINT_{pid}` | MS | 4, 5, 11, 13, 14, 15, 17, 18, 20 |
| `DOSA_{pid}` | MS — the prefix's former spelling | resolved, never written for a new folder |
| `Participant_{pid}` | Legacy / no study | `Participant_test`, `Participant_P001_msparticipant2`, and any diagnosis that maps to no study ("Other Motor Impairment") |

The prefix is derived from `Recordings/<folder>/metadata.json`'s `diagnosis`
field, which stays the primary record — the folder name is a cache of a fact
stored inside it. OptiTrack folders have no metadata of their own and take the
prefix decided for the same participant in `Recordings/`.

## Rules for code

**Never spell a participant folder name out.** Both schemes are live: the
archive was never renamed, the legacy `Participant_13_left_post` sub-layout is
still on disk, and a participant with no study keeps `Participant_`. Use
`participant_paths.py`:

| Need | Call |
| --- | --- |
| Path to one participant | `participant_dir(root, pid)` — add `create=True` to make it |
| Existing folder, or None | `find_participant_dir(root, pid)` |
| Name to write for a NEW participant | `participant_dir_name(pid, root, diagnosis=...)` |
| Every participant under a root | `iter_participant_dirs(root)` → `(pid, name, path)` |
| Glob across all participants | `glob_participants(root, *tail)` |
| Glob within one participant | `participant_glob_for(root, pid, *tail)` |
| Folder name → id | `pid_from_dir_name(name)` |
| A value that may carry a prefix → id | `strip_prefix(value)` |
| Id out of a whole relative path | `pids_in_path(rel)` |
| Diagnosis → prefix | `group_for_diagnosis(diagnosis)` |

Two invariants the pipeline leans on, both pinned by
`tests/test_participant_paths.py`:

1. **Both schemes resolve.** A half-migrated tree, or an archive that was never
   renamed, still finds its data.
2. **The participant id does not move when the prefix does.**
   `excluded_trials.json` and `trial_quality_tags.json` are keyed
   `{pid}_{leg}_{condition}_T{trial}` — built from the id, never from the folder
   — so `pt_report_common._parse_trial_path` and
   `rmse_pipeline_common` read the id off the path with `pids_in_path()` rather
   than off any one folder's prefix. Renaming a folder must never invalidate an
   exclusion.

`find_participant_dir` matches the id as a whole segment: participant 2 can
never resolve to participant 20's folder.

## Writers

`master_app.py`, `pendulastic_app.py` and `pendulastic_viewer.py` all create
folders through `participant_paths`. An **existing folder always wins** over the
diagnosis field, so a participant's data is never split across two folders when
their recorded diagnosis is later corrected.

## Verification

The rename was proved inert by snapshotting pipeline discovery before and after:
participant ids, `data_purpose.classify_all()`, `pt_report_common.list_participants()`,
and all 153 discovered trials with their 152 trial keys came back byte-identical.

The `DOSA_` → `MINT_` follow-up (9 participants, 18 folders across both roots)
was checked the same way and came back identical too: 24 `Recordings/` folders
and 23 OptiTrack ones with the same ids, `classify_all()` unchanged, 22
participants and 248 trials from `list_participants()`, and 977 discovered
trial CSVs — the only difference anywhere was the prefix itself.
