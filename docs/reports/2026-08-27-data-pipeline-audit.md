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
| **P7 left** | `Recordings/Participant_7/` holds only `Right/`. The 7_28 archive holds `Optitrack recordings/Participant_7_left_control/Position_1/Height_Joint-Level/` — an empty leaf. | Every left-leg OptiTrack trial. No IMU, no video, no `.tak`. |
| **P17 (both legs)** | `OptiTrack_Recordings/Participant_17/{Left,Right}/pre/` both exist and are empty. IMU data IS present for both legs. | Every OptiTrack trial. |

### Recovery attempt, 2026-08-28

The repo holds 146 raw Motive `.tak` takes, which can be re-exported. Coverage
by participant: P5, P13, P14, P15, P16, P18, P19, P20, P21, P22, P23, P24.

* **P7 — not recoverable.** No OptiTrack CSV, no `.tak`, no IMU, no video, and
  the archive leaf is empty. Nothing exists in this repo or the 7_28 archive
  for the left leg. Recovery would need capture-day media held elsewhere.
  P7 is an unaffected control and is still characterised (non-spastic by
  recruitment), so the loss costs parameters, not a label.
* **P17 — RECOVERED via IMU.** No `.tak`, so the OptiTrack export cannot be
  regenerated. But both legs have a complete IMU component set (accel, gyro,
  mag, the raw `.jsonl`) plus video. PT parameters are now computed from IMU
  for both legs and tagged `modality=imu`.
* **P22 left — recoverable, needs Motive.** All four `.tak` takes are present
  (`OptiTrack_Recordings/Participant_22/Left/pre/trial_1..4_optitrack.tak`).
  Re-exporting them with full marker reconstruction (`ReconstructAndExportMarkers.py`)
  is the path to a real optical curve. In the meantime its PT parameters are
  recovered from IMU, but its spasticity label stays UNKNOWN — the IMU
  amplitude has no calibrated threshold (§5).

**IMU-recovered legs are never pooled into the statistical comparison.** They
carry a `modality` tag and the grouped comparison is OptiTrack-only, because
IMU A0 runs a median +20.4° above OptiTrack A0 on the same leg — pooling would
put that offset straight into the between-group difference.

Legs with no PT parameters from any modality: P2 both and P4 right (the duo-take
artefact — Shank and Thigh built from overlapping markers), P7 left, P16 both.

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

### Resolved 2026-08-31: re-exported, and still not recoverable

The four `.tak` files were re-exported with full marker reconstruction. That is
the correct fix for a labelling or coordinate-frame fault, and it did help a
little -- coverage moved from the 27-32% previously on file to 27-37%. It also
gave a precise cause where there had only been a symptom.

**The shank cluster is untracked through the entire pre-release hold on all four
trials**, first appearing at frame 245-356, after release. The decisive figure is
DETECTIONS, not coverage: counting every point the cameras registered, labelled
and unlabelled together, the hold window averages

| trial | detections/frame in hold | shank first seen | shank cov |
|---|---|---|---|
| 1 | 3.08 / 6 | frame 282 | 34.8% |
| 2 | 3.75 / 6 | frame 330 | 29.8% |
| 3 | 3.42 / 6 | frame 245 | 37.3% |
| 4 | 4.08 / 6 | frame 356 | 27.0% |

against the **6 simultaneous detections** needed to reconstruct both clusters.
The maximum in any hold frame is 5.

So the markers were not mislabelled -- they were never detected. Reconstruction
can name detections that exist; it cannot create ones that were never made. **No
re-processing recovers these trials.**

All four are now in `excluded_trials.json` with their own measured numbers,
generated from the data rather than transcribed. Backup at
`excluded_trials.json.bak-2026-08-31-pre-P22-left-exclusion`, matching the
convention used for the P17 pre exclusion. P22's right leg is unaffected and
still characterises the participant; P22 left now reports alongside P7 left as
having no parameters from any modality.

**Capture-side pattern worth acting on.** The shank leaves the camera volume
BEFORE release here, not during the swing. That is distinct from the mid-swing
occlusion documented elsewhere in this file: the leg starts outside coverage.
Together with the collinear thigh bar it is the second concrete thing to fix
before the next session.

**A gap this exposed.** `generate_figures_by_spasticity.py`'s IMU fallback
globbed the trial files directly and never consulted `excluded_trials.json`, so
trials the operator had just excluded still contributed IMU parameters. Fixed.
Because the registry is modality-agnostic, excluding a trial for an OPTICAL
fault now also drops its IMU, which may still have been good -- per-modality
exclusion does not exist. Named here rather than worked around: an operator who
excludes a trial should not have to know which code paths honour it.

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

**P4 — a documented, unverified correction. Investigated 2026-08-28; still
unresolved, but narrowed.**

The two hypotheses — the GRADES were transposed, or the RECORDINGS were —
predict identical data within this repo, so they cannot be separated by
correlation. Four independent attempts to break the tie:

| Approach | Result |
|---|---|
| Marker position in the capture volume (which side of the rig each leg sits on) | **No cohort convention.** No axis shows a consistent Left-vs-Right sign across participants; the rig is repositioned between sessions. P4 is not an outlier on any axis. |
| The `leg` column in the MediaPipe CSVs | **Not independent.** It is written from `leg_side_locked`, which comes from the session config or an operator click — it echoes the folder label. |
| MediaPipe anatomical landmarks (MP left vs right knee motion) | **Too noisy to use.** Motion ratios are only ~1.3x, and the two Right-folder trials disagree with each other (T1 → MP right, T2 → MP left). A supine side view defeats MediaPipe's left/right assignment. |
| Video inspection | Footage is clear and both legs are visible, but a static frame cannot fix handedness without knowing the camera side and the subject's roll. |

**What the video DID establish: the two legs were recorded four days apart, in
separate sessions, and both are filed as `pre`.**

| | Capture date | Trials |
|---|---|---|
| P4 **Right** | 2026-06-25, 10:24–10:26 | 1–5 (video), 1–4 (OptiTrack) |
| P4 **Left** | 2026-06-29, 09:46–09:51 | 2, 3, 5 |

Different clothing, different therapist attire, different room equipment in the
two videos. So a transposition here would be a **session-level labelling error**,
not a within-session mix-up — and the two legs are not a matched within-session
pair for any paired comparison.

The MAS row has `assessed_by=WD` and **no `assessed_date`**, so the grades cannot
be tied to either session from the record. **To resolve: check the clinical notes
for 2026-06-25 and 2026-06-29.** That is the only remaining discriminator.

Original note follows.

 `mas_scores.csv` row 16 records a
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
