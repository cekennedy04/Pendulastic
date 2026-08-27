# Data pipeline audit — 2026-08-27

Structural gaps, exclusions and data-quality discrepancies found while
characterising every participant by spasticity. Everything here is a statement
about the DATA, not about the analysis code. Each entry says what is missing,
how it was verified, and what the pipeline does about it now.

The governing rule: **code flags, the operator decides.** Nothing in this list
causes a silent drop. A participant or leg that cannot be characterised is
reported as `unknown`, and `excluded_trials.json` remains the only mechanism
that removes a trial from an analysis.

---

## 1. Missing OptiTrack — empty directory scaffolds

Two participants have OptiTrack directory trees with **no CSV files in them**.
The folders exist, so a check for "does the directory exist" passes; only a
check for files reveals the gap.

| Participant | What exists | What is missing |
|---|---|---|
| **P7 left** | `Recordings/Participant_7/` holds only `Right/`. The 7_28 archive holds `Optitrack recordings/Participant_7_left_control/Position_1/Height_Joint-Level/` — an empty leaf. | Every left-leg OptiTrack trial. No IMU either. |
| **P17 (both legs)** | `OptiTrack_Recordings/Participant_17/{Left,Right}/pre/` both exist and are empty. IMU data IS present for both legs. | Every OptiTrack trial. |

`Model_Analysis_Outputs/Figures/Fig_A_goniogram_Participant_7_left_control_T1..T4`
exist as PNG/PDF, so P7 left was analysed at some point. The source data is no
longer in either the live tree or the archive. **Not recoverable from this
repo** — check the capture-day media if the trials are wanted.

**Pipeline behaviour.** Legs are now enumerated from the participant roster and
a fixed `LEGS` tuple, not from whatever data exists. Before this, a leg with no
data did not appear in the output at all: a table listing 36 legs while
silently omitting 2 reads as full coverage. P7 left and both P16 legs were
invisible this way.

P7 is an unaffected control, so it is still characterised (non-spastic by
recruitment) despite having no left-leg recording at all.

---

## 2. Missing OptiTrack — data present, unreconstructable

**P22 left: 4 trials exist and all 4 fail.**

```
Participant_22/Left/pre/trial_1..4_optitrack.csv
  -> ValueError: Fewer than 5 fully-tracked frames in the hold window
     — cannot establish an anatomical reference.
```

The knee axis is seeded from the thigh→shank centroid vector measured during
the hold, when the leg is extended. That requires at least a few frames in
which **all six** Shank and Thigh markers are simultaneously tracked. On P22
left there are fewer than five such frames in the entire hold window.

For scale, P22 **right** loads but at 46–57% coverage; the left leg is worse.
This is the most severe case of the corpus-wide coverage collapse (73% of the
215 trials sit below 90% coverage).

**This is absence of data, not poor data.** It is reported as `unreadable` with
that message, distinct from a low-coverage trial that still produces a curve.

**Deliberately NOT worked around.** P22 left has usable IMU recordings, and it
is tempting to derive its amplitude from those. That is rejected — see §5.

---

## 3. Non-participant folders in the recordings tree

`Recordings/Participant_test/` is capture scaffolding: `participant_id: "test"`,
`weight_kg: "20"`, `diagnosis: "MS"`. It was being picked up by
`generate_paper_results_analysis.load_group_and_demo` and **classified as an MS
participant**. Now excluded by name (`NON_PARTICIPANT_IDS = {"test", "0",
"demo"}`).

Anything added to `Recordings/` is treated as a participant by default, so a
future scratch folder will be counted unless it matches that set. Worth a
naming convention (a `_scratch` prefix, or a flag in `metadata.json`) if this
recurs.

---

## 4. MAS coverage and internal discrepancies

**Coverage.** 14 of 22 participants have a clinical MAS score. **All four
post-stroke participants (P19, P21, P22, P24) have none.** Grades present in
the corpus span only `0`, `1` and `1+` — there is no `2`, `3` or `4`.

**P17 — overall grade pending, components scored.**

```
17,right,pre,,-1,,2026-08-19,,Overall MAS Grade not yet assessed,1,0
17,left ,pre,,-1,,2026-08-19,,Overall MAS Grade not yet assessed,0,0
```

`-1` is `mas_validation.PENDING_MAS_GRADE`. The overall grade is not yet given,
but flexion and extension WERE scored. That is real clinical evidence, and it
was being discarded — P17 came back `unknown`. Components now fill in for a
pending overall grade, under their own source (`clinical-mas-components`) so a
component-only verdict is never read as a completed assessment.

**P15 left — overall grade contradicts its own components.**

```
15,left,Pre,...,overall=0, flexion=1+, extension=0
```

The overall grade says non-spastic; the flexion component says 1+. **Unresolved.**
The clinician's overall grade is treated as authoritative and the leg is
labelled non-spastic. Components never override an overall grade that exists.
Flagged here because it is a genuine contradiction in the source record, not a
parsing artefact — worth confirming against the clinical notes.

**P4 — a documented, unverified correction.** `mas_scores.csv` row 16 records a
2026-08-24 left/right transposition fix, applied "per operator judgment,
supported by biomechanics" and explicitly **"NOT verified against source
clinical records"**. The note itself says the finding is equally consistent
with the RECORDINGS being transposed rather than the grades. Any analysis
grouping P4 by leg inherits that assumption. Pre-edit backup:
`mas_scores.csv.bak-2026-08-24-pre-P4-swap`.

---

## 5. Why IMU amplitude is not used to fill OptiTrack gaps

P22 left and both P17 legs have IMU data and no usable OptiTrack. Deriving
their spasticity label from IMU-measured swing amplitude would close every
remaining gap. It is rejected on two measurements:

* **Modality offset.** IMU `A0_deg` runs systematically higher than OptiTrack
  `A0_deg` on the same leg: **median +20.4°, mean +20.3°, sd 8.4°** across the
  16 legs where both exist. The OptiTrack-derived threshold cannot be reused on
  IMU amplitudes.
* **No calibration set.** An IMU-specific threshold cannot be fitted here.
  Clinical MAS covers P2–P15; the IMU recordings cover P14–P24. Exactly **one**
  known-MAS-0 leg has IMU data (P15 left, 63.2°) and it sits **inside** the
  range of the three known-MAS≥1 legs that do (56.8–75.5°). There is no
  separation to fit to.

Any IMU threshold would therefore be invented rather than derived. Once MAS is
collected for the P17–P24 range this becomes tractable and should be revisited.

---

## 6. Directory reorganisations have broken tests twice

`Recordings/` has been restructured at least twice:

1. `Recordings/Participant_13/Session_post/...`
2. → `Recordings/Participant_13/Participant_13_left_post/Session_post/...`
3. → `Recordings/Participant_13/Left/post/Session_post/...` (current)

`test_contaminated_trial_no_longer_has_extreme_bias` hardcoded layout (2) and
failed on a missing file, which surfaced as `no OptiTrack match found for ...`
— indistinguishable at a glance from a real bug in `find_optitrack_match`. The
matcher was fine throughout.

Tests now locate trials by participant/leg/condition rather than spelling out
the tree. **Any new test that hardcodes a path under `Recordings/` will break
on the next reorganisation.**

---

## 7. Undeclared dependency

`statsmodels` is imported by `generate_paper_results_analysis.py` and the three
scripts that import from it, but was absent from `requirements.txt`. None of
the four could run from a clean install. Now declared.

---

## Current characterisation coverage

All 22 real participants carry a spasticity characterisation; **none are
unknown at participant level**. 16 non-spastic, 6 spastic (P4, P13, P14, P15,
P17, P21).

At LEG level, 44 legs are enumerated: 34 non-spastic, 9 spastic, **1 unknown**
(P22 left, §2).

Label provenance across the 44 legs:

| Source | Legs |
|---|---|
| `clinical-mas` (overall grade) | 23 |
| `a0-derived` (OptiTrack swing amplitude) | 13 |
| `control-by-recruitment` | 5 |
| `clinical-mas-components` | 2 |
| `no-data` | 1 |

---

## Open items

- [ ] Collect clinical MAS for P17–P24, especially the four stroke participants. This removes the derived-label caveat AND makes an IMU threshold calibratable.
- [ ] Confirm P15 left: overall MAS 0 vs flexion component 1+ (§4).
- [ ] Verify the P4 left/right transposition against source clinical records (§4).
- [ ] Decide whether P7 left and P17 OptiTrack are recoverable from capture-day media, or should be recorded as permanently absent (§1).
- [ ] Adopt a naming convention for non-participant folders under `Recordings/` (§3).
