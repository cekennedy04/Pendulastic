# Pendulum Test Literature Benchmarks

**Compiled:** 2026-08-24
**Why:** our own PT-score calibration cohort is too small to fit thresholds on
(29 participant-legs, only 6 at MAS>=1). This collects published reference
values from larger cohorts so our parameters can be sanity-checked against
them, and records several findings that bear directly on how
`pendulastic_pt_score.compute_pt_score` is built.

Search performed via the `mcpmarket-me:biomedical-search` skill (Valyu
semantic search over PubMed/bioRxiv/medRxiv/ClinicalTrials).

---

## 1. Primary benchmark study

**Whelan A, Sexton A, Jones M, O'Connell C, McGibbon CA (2018).**
*Predictive value of the pendulum test for assessing knee extensor spasticity.*
J NeuroEng Rehabil. https://pubmed.ncbi.nlm.nih.gov/PMC6052641 — 24 citations

**Cohort: 131 knees from 93 patients** — 4.5x ours, with full clinician MAS.

| group | patients | knees |
|---|---|---|
| ABI (incl. stroke) | 45 | 56 |
| **MS** | **14** | **23** |
| CP | 12 | 18 |
| SCI | 22 | 34 |

Knee-extensor MAS distribution across all 131 knees:
`MAS 0: 53 | 1: 33 | 1+: 14 | 2: 16 | 3: 11`

Instrumentation: BioTone(TM) fibre-optic goniometer + EMG (EMG used to confirm
relaxation before each trial — see §5).

---

## 2. Metric discrimination (their Table 6, ROC AUC)

**Model 1 = detect ANY spasticity (MAS 0 vs MAS>0), knee extensors.**
AUC > 0.7 is their acceptability bar.

| metric | AUC (95% CI) | verdict |
|---|---|---|
| E1amp (1st extension amplitude) | 0.808 (0.726–0.890) | best, but flexor-related |
| **F1amp (1st flexion amplitude)** | **0.807 (0.729–0.885)** | **best extensor metric** |
| RI (relaxation index) | 0.784 (0.698–0.869) | acceptable |
| ERI (extension relaxation index) | 0.762 (0.672–0.852) | acceptable |
| Plateau angle (resting angle) | 0.691 (0.599–0.783) | borderline |
| **Ncyc (number of cycles)** | **0.665 (0.567–0.762)** | **WORST** |

**Model 2 = discriminate severity (MAS 1,1+ vs 2,3).** Every metric fell in
0.59–0.71; none acceptable. Ncyc worst again at 0.590.

> "the pendulum test is a valid tool to distinguish knee extensors with
> spasticity (MAS > 0), from those without spasticity (MAS = 0), but ...
> none of the metrics we analyzed were able to discriminate between knees
> with low/moderate (MAS = [1,1+]) and high/severe (MAS = [2,3]) spasticity."

---

## 3. Best published classifier (their Table 7)

Detecting presence of extensor spasticity (MAS >= 1):

| model | accuracy | sens | spec | PPV | NPV |
|---|---|---|---|---|---|
| **F1amp + Plateau angle** | **77.9%** | .81 | .70 | .86 | .62 |
| RI alone | 74.0% | .77 | .66 | .86 | .51 |
| F1amp alone | 73.3% | .77 | .63 | .84 | .53 |
| Plateau alone | 68.7% | .71 | .58 | .87 | .33 |

Fitted logistic model (directly reusable as an external cross-check):

```
logit = 3.258 - 0.073*F1amp + 0.045*Plateau
        Beta(F1amp) p < .001 ;  Beta(Plat) p = .082
```

Single-term models:
```
RI      : logit = 5.153 - 3.646*RI       (p < .001)
F1amp   : logit = 4.236 - 0.051*F1amp    (p < .001)
Plateau : logit = 3.383 - 0.049*Plateau  (p < .001)
```

---

## 4. Findings that affect OUR score

### 4a. `N` (number of cycles) is the weakest metric in the literature

> "number of cycles had the **poorest classification performance of all
> metrics** for both Model 1 and 2 analyses ... the number of cycles did not
> correspond to MAS score."

Their ANOVA found a significant extensor-spasticity effect for every metric
at p < .001 **except** Ncyc, at **p = 0.594**.

Our `compute_pt_score` weights `N` as 1 of 7 equal terms (`_N_PARAMS = 7`,
each contributing `1/(7*ref)`). On this evidence `N` contributes mostly
noise to the total.

This does NOT retroactively justify the pre-2026-08-24 `N` bugs — `N` was
measurably wrong (tail noise inflating counts; see that day's fixes) and it
also gates `imu_calibration_tuner.score_waveform`, so fixing it mattered
regardless. But it argues against `N` carrying 1/7 of the clinical score.

### 4b. We omit the most linear metric

**Plateau angle** (final resting angle) had the most linear relationship with
MAS of any metric they tested, and is half of their best classifier.

We compute it (`neutral_deg` / `pre_release_deg` in `compute_pt_params`) but
it is **not one of the 7 scored parameters** — it is only a diagnostic.
So our score currently includes the literature's worst metric at full weight
and excludes one of its two best.

### 4c. RI / R2n is non-monotonic in severity

> "RI and F1amp decreased in magnitude until extensor MAS = 2 then increase
> for MAS = 3 to levels similar as MAS = 1+."

A U-shape. Any monotonic threshold on an RI-like term will score the most
severe cases as *milder* than moderate ones. Our `R2n` (`A1 / (1.6*A0)`) is
an RI-family term and may inherit this. Worth checking directly before any
future severity-graded (as opposed to binary) calibration.

Note their RI = `F1amp / Plateau`, which is **not** our `R2n` formula. Do not
compare our absolute `R2n` values to published RI values without converting
(see §6).

### 4d. Our 3-zone model may be over-ambitious

`PT_HEALTHY_MAX` / `PT_BORDERLINE_MAX` imply a severity gradient. The
literature's finding is that pendulum metrics support a **binary**
spastic/not-spastic split and do not reliably grade severity. Our
2026-08-24 MAS-0-vs-MAS>=1 recalibration is the supported shape; the
borderline band is not independently validated.

### 4e. Flexor spasticity does NOT confound the extensor metrics

Useful negative result: F1amp and RI were unaffected by flexor MAS, despite
flexor and extensor MAS correlating (r = .525, p < .001). Supports treating
knee-extensor spasticity in isolation, as we do.

---

## 5. Methodological gap in our setup

Both the benchmark study and the systematic review (§6) confirm relaxation
before release, via **EMG** or a **phase-plane (angle vs angular velocity)
check**. The review is blunt that pendulum results are "completely affected
by the level of relaxation and by the form of sitting."

We have neither check. Our nearest equivalent is the manual trial-exclusion
list (`load_excluded_trials`), which currently holds 5 P15 trials excluded as
"participant used own muscles to stop the leg" — i.e. we are catching
non-relaxation by operator observation after the fact rather than by
instrument. A phase-plane check is computable from data we already record and
needs no new hardware.

---

## 6. Cross-study comparability warning

**Rahimi F, Eyvazpour R, Salahshour N, Azghani MR (2020).**
*Objective assessment of spasticity by pendulum test: a systematic review on
methods of implementation and outcome measures.*
https://pubmed.ncbi.nlm.nih.gov/PMC7653760 — 24 citations

Exists specifically because different groups compute the same quantity under
different names and definitions. Read before comparing our absolute parameter
values to any published number. Also catalogs sensor technologies; on IMUs
specifically it notes "issues concerning the validity and reliability of the
measurements" — relevant to our IMU-vs-OptiTrack agreement work.

---

## 7. Other cohorts located

| study | cohort | MAS? | note |
|---|---|---|---|
| He 1997 | **46 MS** | no clinical scale | largest MS pendulum cohort found |
| Leslie 1992 | 14 MS | Ashworth | electro-goniometer |
| Huang 2021 (PMC8292373) | 40 chronic stroke | yes | FSE + RI vs MAS, mobility correlations |
| Greenan-Fowler 2000 | 30 CP + 10 healthy | MAS | |
| Nordmark 2002 | 20 CP (SDR) | MAS | goniometer + EMG |
| Stillman 1995 | 77 healthy (young/mid/elderly) | n/a | healthy normative reference |

---

## 8. Data availability — no public raw dataset found

**No downloadable dataset pairing pendulum kinematics with MAS grades was
located.** These are published studies; raw data would require contacting
authors. Searches for open repositories (Zenodo/figshare/PhysioNet) surfaced
only general gait/EMG/IMU datasets with no spasticity grading.

What is usable *without* contacting anyone:
- Whelan Table 2 (MAS distributions per cohort) — compare our distribution
- Whelan Table 6 (AUCs) — benchmark our per-parameter discrimination
- Whelan Table 7 (logistic coefficients) — run their published classifier on
  our data as an external cross-check

Worth contacting McGibbon (UNB) for the 131-knee dataset if a real external
validation is wanted — that is the single highest-value dataset found, and
its MS arm (23 knees) alone exceeds our entire MAS>=1 group.

---

## 9. Suggested follow-ups

1. Compute plateau/resting angle as a **scored** parameter, not just a
   diagnostic (§4b).
2. Test whether our `R2n` shows the U-shape (§4c) before trusting any
   severity-graded threshold.
3. Reconsider `N`'s 1/7 weight in `compute_pt_score` (§4a).
4. Add a phase-plane relaxation check (§5) — no new hardware needed.
5. Run Whelan's published logistic classifier against our cohort as an
   external check that does not depend on our own small-n calibration.
6. Consider whether `PT_BORDERLINE_MAX` should exist at all (§4d).

None of these are done. Recorded here so the reasoning is not lost.
