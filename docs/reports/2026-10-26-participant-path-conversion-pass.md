# Analysis pass over the participant-path conversion

**Date:** 2026-10-26
**Trigger:** `126a117` landed on main (`aeb4c3c`) carrying 21 converted analysis
scripts, none of which has coverage in CI's gated list.
**Scope:** the 25 files `126a117` touches outside `tests/`.

The corpus is not in this container, so a true end-to-end run is impossible.
Three checks were run instead, and the third is the one that matters.

## 1. Import check -- 21 of 25 clean, 4 environmental

Every converted module imports. The four that do not are missing optional
dependencies, not conversion damage: `master_app.py`, `pendulastic_app.py` and
`pendulastic_viewer.py` need `tkinter`, and `spike_metrabs_rmse_v2.py` needs
`tensorflow`. Six more failed on `statsmodels` until it was installed, then
passed. No `NameError` or `AttributeError` of the kind a half-finished
refactor produces.

## 2. Hardcoded folder names -- no violations inside the converted set

CLAUDE.md's rule is "never spell a participant folder name out in code". Every
literal `"Participant_"` / `"Control_"` / `"CHAT_"` / `"MINT_"` / `"DOSA_"` in
the converted files was checked in context and all are legitimate:

- `calibrate.py` strips prefixes for display labels and handles **all five**.
- `evaluate_all_participants.py` and `pendulastic_pt_score.py` build paths under
  `Model_Analysis_Outputs/`, a derived directory that was never renamed.
- `analysis_pipeline.py` calls `find_participant_dir` first and uses the legacy
  name only as a fallback when the resolver returns None.

## 3. A study-prefixed corpus, and the counterfactual

A synthetic corpus was built with folders named `Control_8`, `CHAT_12`,
`MINT_15`, `DOSA_16` and legacy `Participant_9` -- 8 legs, 16 trials, waveforms
from `rig_neuromuscular_model`, with a matching `mas_scores.csv`.

| consumer | result |
|---|---|
| `discover_all_trials` | **16 trials**, 5 participants, condition parsed as `pre` |
| `list_participants` | 8, 9, 12, 15, 16 |
| `classify_participant_legs` | every leg labelled from clinical MAS, all five prefixes |
| `collect_participant` | legs and timepoints resolved per participant |
| `severity_grading_test --cohort` | full funnel, discrimination table, frequency and noise blocks |

**The counterfactual is the finding.** Swapping
`participant_paths.pids_in_path` back for the pre-conversion
`re.compile(r"Participant_(\d+)")` on the same corpus:

```
converted (participant_paths.pids_in_path):  16 trials
legacy-only regex r'Participant_(\d+)':       2 trials
```

Fourteen of sixteen trials, across four of five participants, become
**invisible to the entire reporting pipeline** -- silently, with no error
raised. The conversion is load-bearing, and `126a117` sitting unmerged for
nearly two months was a live data-loss bug, not a tidy-up.

## Two scripts were still exposed, and are now fixed

Both were outside `126a117`'s 25 files, so the conversion never reached them.
Both extracted the participant id from a path component with a legacy-only
regex, returned None for every renamed folder, and left `pid` as None -- which
drops the trial with no error, exactly the failure mode above.

- `pendulastic_pipeline.py:356` -- `_PID_RE` in the DISCOVERY section, used by
  `_path_meta`.
- `run_movenet_all.py:36` -- the same pattern over `Recordings/`.

Both now call `participant_paths.pid_from_dir_name`, which accepts all five
prefixes. Verified behaviour-preserving on legacy names
(`Participant_9_left_post` -> `9_left_post`, as before) and newly working on
study-prefixed ones.

A third, `spike_metrabs_rmse_v2.py:69`, keeps a legacy-only regex but applies
it to a TRIAL NAME rather than a folder, and resolves the folder itself through
`participant_paths.participant_dir`. Left alone: whether it breaks depends on
how trial names are generated, and it is a prototype spike script.

## What this pass could not check

Nothing here ran against real recordings. The figure-generating scripts
(`generate_*`) were import-checked only -- they need the corpus and were not
exercised on it. `master_app.py`, `pendulastic_app.py` and
`pendulastic_viewer.py` were not checked at all beyond the conversion audit,
because `tkinter` is unavailable here, and they are the three with operator-
facing path handling. Those are the remaining gap.
