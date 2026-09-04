# IMU Knee-Angle App Validation — Results Program

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Produce a single, provenance-aware analysis layer that emits every validation result for the phone-IMU pendulum test once, and renders it into a paper/thesis chapter, a device-style validation dossier, and a committee deck without any number being computed twice.

**Architecture:** One package, `validation/`, builds a canonical trial table carrying explicit MAS provenance tiers, runs a pre-registered analysis list, and writes a single `results.json`. Three thin exporters render figures, tables, and the dossier from that file — so the three deliverables cannot disagree. Track 1 analyses run on existing data today; Track 2 analyses ingest bench-rig data when it exists and are skipped-with-reason until then.

**Tech Stack:** Python 3.11 (`~/miniconda3/python.exe`), pandas, numpy, scipy, pingouin (ICC), matplotlib, pytest. No new heavyweight dependencies.

**Spec:** This plan is self-contained; the upstream rig design is in the two bench-rig documents referenced in Task 12.

---

## Global Constraints

- **Never auto-exclude trials.** Code flags data quality; it never drops. `excluded_trials.json` is the only exclusion path. (Standing user instruction, 2026-08-27.)
- **Never hardcode a participant folder name.** Resolve through `participant_paths.py`. Both `Control_/CHAT_/MINT_` and legacy `Participant_` schemes are live.
- **Never derive spasticity grouping from PT7.** PT7 is U-shaped in severity. Clinical MAS wins; A0 amplitude is the documented fallback, but see the blocker on the A0 threshold below.
- **Do not report the composite PT7 score as calibrated.** `HEALTHY_REF` was calibrated on a superseded reconstruction and is invalid. Report the seven parameters individually until Task 11 resolves it.
- **Unit of analysis is the leg, not the trial.** Trials are repeated measures within a leg; every inferential statistic must account for that clustering.
- **`compute_pt_params` is the only score-related parameter source.** `workbench_engine.windowed_pt_params` computes `omega_min_n` differently and produces a bogus penalty.
- Analysis runs use `~/miniconda3/python.exe`. Run one test file at a time; `test_app.py` alone is ~7.5 min.

---

## Blockers this plan must route around

| Blocker | Effect on results | Handled by |
|---|---|---|
| `HEALTHY_REF` invalid | Composite PT7 score is uninterpretable | Task 11 — report parameters individually until recalibrated |
| A0 threshold 38.1 invalidated | Cannot group by A0 amplitude | Task 2 — grouping comes from Tier-A MAS only |
| PT7 non-monotonic in severity | Cannot present PT7 as a severity scale | Task 6 — report discrimination of *presence*, never grade |
| MAS-0/MAS>0 confounded with participant | Clinical AUC is not estimable today | Task 10 — gated behind recruitment |
| Single-sensor geometry defect | Angle over-reads ~1.36× | Task 8 (observational), Task 13 (bench, causal) |

---

## File Structure

| File | Responsibility |
|---|---|
| `validation/__init__.py` | Package marker only |
| `validation/cohort.py` | Canonical trial table + MAS provenance tiers. The single definition of who is in what analysis. |
| `validation/agreement.py` | ICC(2,1), Bland–Altman, LOA, RMSE decomposition. Pure functions over arrays. |
| `validation/discrimination.py` | AUC with clustered bootstrap CIs, power calculation. |
| `validation/streams.py` | Loads the 100 Hz raw and 20 Hz scored angle series for one trial; applies a named filter. |
| `validation/analyses.py` | The pre-registered analysis list. One function per result, each returning a JSON-serialisable dict. |
| `validation/run.py` | CLI entry point. Runs analyses, writes `Model_Analysis_Outputs/Validation/results.json`. |
| `validation/exports/figures.py` | Renders every figure from `results.json` |
| `validation/exports/tables.py` | Renders CSV + markdown + LaTeX tables from `results.json` |
| `validation/exports/dossier.py` | Renders requirement → evidence → verdict report from `results.json` |
| `tests/test_validation_cohort.py` | Provenance tier correctness, leak assertions |
| `tests/test_validation_agreement.py` | Agreement stats against analytically known inputs |
| `tests/test_validation_discrimination.py` | AUC and power against known values |
| `tests/test_validation_analyses.py` | Each analysis returns a well-formed, complete record |

---

## Task 1: Package skeleton and the results contract

**Files:**
- Create: `validation/__init__.py`, `validation/run.py`
- Test: `tests/test_validation_analyses.py`

**Interfaces:**
- Produces: `validation.run.main(argv) -> int`; every analysis function has signature `(cohort: pd.DataFrame) -> dict` and returns a dict with keys `id`, `title`, `track`, `status`, `payload`, `notes`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_validation_analyses.py
import json
from validation import run

REQUIRED_KEYS = {"id", "title", "track", "status", "payload", "notes"}
VALID_STATUS = {"ok", "skipped", "blocked"}


def test_result_record_contract():
    """Every analysis record carries the same keys so exporters never branch."""
    rec = run.empty_record("R0", "Smoke", track=1)
    assert set(rec) == REQUIRED_KEYS
    assert rec["status"] == "blocked"
    assert rec["payload"] == {}


def test_skipped_records_must_explain_themselves():
    rec = run.empty_record("R0", "Smoke", track=2)
    rec["status"] = "skipped"
    with __import__("pytest").raises(ValueError, match="notes"):
        run.validate_record(rec)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `~/miniconda3/python.exe -m pytest tests/test_validation_analyses.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'validation'`

- [ ] **Step 3: Write minimal implementation**

```python
# validation/__init__.py
"""Validation results program for the phone-IMU pendulum test."""

# validation/run.py
from __future__ import annotations

VALID_STATUS = {"ok", "skipped", "blocked"}


def empty_record(rid: str, title: str, track: int) -> dict:
    """A result record before its analysis runs.

    `blocked` is the default so a crashed analysis can never silently
    render as a passing result.
    """
    return {"id": rid, "title": title, "track": track,
            "status": "blocked", "payload": {}, "notes": ""}


def validate_record(rec: dict) -> dict:
    if rec["status"] not in VALID_STATUS:
        raise ValueError(f"{rec['id']}: bad status {rec['status']!r}")
    if rec["status"] in {"skipped", "blocked"} and not rec["notes"]:
        raise ValueError(f"{rec['id']}: status {rec['status']} requires notes explaining why")
    return rec


def main(argv=None) -> int:
    return 0
```

- [ ] **Step 4: Run test to verify it passes**

Run: `~/miniconda3/python.exe -m pytest tests/test_validation_analyses.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add validation/ tests/test_validation_analyses.py
git commit -m "feat(validation): result record contract with explain-yourself skips"
```

---

## Task 2: Cohort table with MAS provenance tiers

This task fixes a real defect found on 2026-09-03: `multimetric_trials.csv` carries a `mas` value for 19 trials from P16 and P17, neither of whom has a clinician assessment. P16 is an `ASSUMED` control; P17's `assessed_by` is blank and its OptiTrack session errored out.

**Files:**
- Create: `validation/cohort.py`
- Test: `tests/test_validation_cohort.py`

**Interfaces:**
- Produces: `build_cohort(repo_root: Path) -> pd.DataFrame` with columns
  `pid, leg, condition, trial_key, group, mas_grade, mas_tier, mas_condition_matched,
  has_imu, has_optical, excluded, exclusion_reason`.
  `mas_tier` is one of `"A"`, `"B"`, `"C"`, `"D"`; `mas_condition_matched` is a
  bool.

**Provenance has two independent axes, and conflating them was a defect in an
earlier draft of this plan.** `mas_tier` answers *who assessed the grade*.
`mas_condition_matched` answers *whether the grade was recorded in the same
condition as the trial it is attached to* — a leg examined `pre` carries a
grade that says nothing about its `post` or `month_post` trials, yet the
tier scheme alone would admit those to the primary clinical analysis as
Tier A.

This is not hypothetical. Measured across the corpus 2026-09-04: of 39
clinician-examined spastic trials, only **9** were recorded in the condition
their MAS was assessed in. A carried `pre` grade on a `month_post` trial is
currently being counted as a matched observation in correlations. It also
distorted the excursion-gate re-derivation from both directions until it was
caught — the apparent lowest spastic excursion moved 6.76 -> 8.20 -> 9.85 as
the join rule tightened, and only the last is condition-matched.

A grade can be perfectly sourced and still not apply to the trial it is
attached to, so this is a separate column rather than a fifth tier letter.
**The primary clinical analysis requires `mas_tier == "A"` AND
`mas_condition_matched`.**
- Produces: `TIERS: dict[str, str]` mapping tier letter to a human description.

Tier definitions:
- **A** — clinician-examined: `assessed_by` present and not `ASSUMED`.
- **B** — operator-reported: a grade exists but no named assessor.
- **C** — assumed control: `assessed_by == "ASSUMED"`, never examined.
- **D** — no MAS row at all.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_validation_cohort.py
import pytest
from pathlib import Path
from validation.cohort import build_cohort, TIERS

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def cohort():
    return build_cohort(REPO)


def test_tiers_are_exhaustive_and_named(cohort):
    assert set(TIERS) == {"A", "B", "C", "D"}
    assert set(cohort["mas_tier"]).issubset(set(TIERS))


def test_assumed_controls_are_tier_c_not_tier_a(cohort):
    """17 control rows carry assessed_by=ASSUMED and were never examined.

    Treating them as clinician data is the leak this whole task exists to stop.
    """
    p16 = cohort[(cohort.pid == 16)]
    assert not p16.empty
    assert set(p16["mas_tier"]) == {"C"}


def test_tier_a_participants_are_exactly_the_examined_four(cohort):
    tier_a = set(cohort.loc[cohort.mas_tier == "A", "pid"])
    assert tier_a == {4, 5, 13, 14, 15}


def test_imu_participants_with_no_mas_are_tier_d(cohort):
    """P18-P24 have IMU recordings and no MAS row. They are the recruitment target."""
    tier_d = set(cohort.loc[(cohort.mas_tier == "D") & cohort.has_imu, "pid"])
    assert tier_d == {18, 19, 20, 21, 22, 23, 24}


def test_no_analysis_ready_row_mixes_tiers_silently(cohort):
    """Every row that carries a grade must also carry the tier that grade came from."""
    graded = cohort[cohort.mas_grade.notna()]
    assert (graded.mas_tier != "D").all()


def test_a_carried_grade_is_not_admitted_as_a_matched_observation(cohort):
    """A `pre` grade on a `month_post` trial is Tier A and NOT admissible.

    Measured 2026-09-04: only 9 of 39 clinician-examined spastic trials were
    recorded in the condition their MAS was assessed in. Tier alone would let
    the other 30 into a correlation as matched observations.
    """
    from validation.cohort import tier_a
    admissible = tier_a(cohort)
    assert admissible["mas_condition_matched"].all()
    assert (admissible["mas_tier"] == "A").all()
    # and the two axes must be genuinely independent, not one renamed
    carried = cohort[(cohort.mas_tier == "A") & ~cohort.mas_condition_matched]
    assert len(carried) > 0, (
        "no Tier-A carried grades found -- the condition axis is not being "
        "populated, or the join silently matched everything")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `~/miniconda3/python.exe -m pytest tests/test_validation_cohort.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'validation.cohort'`

- [ ] **Step 3: Write the implementation**

```python
# validation/cohort.py
"""The single definition of who is in which analysis.

MAS provenance is a first-class column because the project has already been
burned by it: `multimetric_trials.csv` carries a `mas` value for 19 trials
from participants who were never examined by a clinician.
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

import participant_paths

TIERS = {
    "A": "clinician-examined, named assessor",
    "B": "operator-reported, no named assessor",
    "C": "assumed control, never examined",
    "D": "no MAS record",
}


def _tier(assessed_by, grade) -> str:
    if pd.isna(grade):
        return "D"
    if pd.isna(assessed_by):
        return "B"
    if str(assessed_by).strip().upper() == "ASSUMED":
        return "C"
    return "A"


def _condition_matched(trial_condition, assessed_condition) -> bool:
    """Was the grade recorded in the same condition as this trial?

    A leg examined `pre` says nothing about its `post` or `month_post` trials.
    Only 9 of 39 clinician-examined spastic trials clear this, so it removes
    far more than it looks like it should -- which is the point.
    """
    if pd.isna(trial_condition) or pd.isna(assessed_condition):
        return False
    norm = lambda c: str(c).strip().lower().replace(" ", "_")
    return norm(trial_condition) == norm(assessed_condition)


def build_cohort(repo_root: Path) -> pd.DataFrame:
    repo_root = Path(repo_root)
    mas = pd.read_csv(repo_root / "mas_scores.csv")
    manifest = pd.read_csv(
        repo_root / "Model_Analysis_Outputs" / "data_purpose_manifest.csv"
    )
    excluded = json.loads((repo_root / "excluded_trials.json").read_text())

    mas["mas_tier"] = [
        _tier(a, g) for a, g in zip(mas["assessed_by"], mas["mas_grade"])
    ]
    mas = mas.rename(columns={"participant": "pid"})

    have_imu = set(manifest.loc[manifest.has_imu == "yes", "participant"])
    have_opt = set(manifest.loc[manifest.has_optitrack_csv == "yes", "participant"])

    # Participants with recordings but no MAS row at all become explicit Tier D
    # rows rather than vanishing from the cohort -- they are the recruitment target.
    missing = sorted((have_imu | have_opt) - set(mas["pid"]))
    filler = pd.DataFrame(
        [{"pid": p, "leg": leg, "condition": "pre", "mas_grade": pd.NA,
          "assessed_by": pd.NA, "mas_tier": "D"}
         for p in missing for leg in ("left", "right")]
    )
    rows = pd.concat([mas, filler], ignore_index=True)

    rows["has_imu"] = rows["pid"].isin(have_imu)
    rows["has_optical"] = rows["pid"].isin(have_opt)
    rows["trial_key"] = (
        rows["pid"].astype(str) + "_" + rows["leg"].astype(str).str.lower()
        + "_" + rows["condition"].astype(str).str.lower()
    )
    rows["excluded"] = rows["trial_key"].isin(excluded)
    rows["exclusion_reason"] = rows["trial_key"].map(excluded).fillna("")
    return rows


def tier_a(cohort: pd.DataFrame) -> pd.DataFrame:
    """Rows admissible to the primary clinical analysis.

    BOTH axes, not just the tier: a well-sourced grade attached to a trial
    from a different condition is not an observation of that trial.
    """
    return cohort[(cohort.mas_tier == "A") & cohort.mas_condition_matched]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `~/miniconda3/python.exe -m pytest tests/test_validation_cohort.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add validation/cohort.py tests/test_validation_cohort.py
git commit -m "feat(validation): MAS provenance tiers, closing the P16/P17 grade leak"
```

---

## Task 3: Agreement statistics

**Files:**
- Create: `validation/agreement.py`
- Test: `tests/test_validation_agreement.py`

**Interfaces:**
- Produces: `icc_2_1(x, y) -> dict` with keys `icc`, `ci_low`, `ci_high`, `n`
- Produces: `bland_altman(x, y) -> dict` with keys `bias`, `sd`, `loa_low`, `loa_high`, `n`
- Produces: `affine_decompose(ref, test) -> dict` with keys `gain`, `offset`, `rmse_raw`, `rmse_corrected`, `affine_fraction`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_validation_agreement.py
import numpy as np
import pytest
from validation.agreement import icc_2_1, bland_altman, affine_decompose


def test_icc_of_identical_series_is_one():
    x = np.array([1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0])
    assert icc_2_1(x, x.copy())["icc"] == pytest.approx(1.0, abs=1e-9)


def test_bland_altman_recovers_a_known_constant_bias():
    x = np.linspace(0, 100, 50)
    y = x + 4.0
    ba = bland_altman(x, y)
    assert ba["bias"] == pytest.approx(4.0, abs=1e-9)
    assert ba["sd"] == pytest.approx(0.0, abs=1e-9)


def test_affine_decompose_recovers_a_known_gain_and_offset():
    """This is the project's central RMSE caveat, so it gets an exact test."""
    ref = np.linspace(5, 95, 200)
    test = 1.19 * ref - 9.8
    out = affine_decompose(ref, test)
    assert out["gain"] == pytest.approx(1.19, abs=1e-6)
    assert out["offset"] == pytest.approx(-9.8, abs=1e-6)
    assert out["rmse_corrected"] == pytest.approx(0.0, abs=1e-6)
    assert out["affine_fraction"] == pytest.approx(1.0, abs=1e-6)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `~/miniconda3/python.exe -m pytest tests/test_validation_agreement.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'validation.agreement'`

- [ ] **Step 3: Write the implementation**

```python
# validation/agreement.py
"""Agreement statistics over paired series.

Pure functions over arrays -- no file IO, so every one of them is testable
against an analytically known answer.
"""
from __future__ import annotations

import numpy as np
from scipy import stats


def _clean(x, y):
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    ok = np.isfinite(x) & np.isfinite(y)
    return x[ok], y[ok]


def icc_2_1(x, y) -> dict:
    """Two-way random effects, absolute agreement, single measurement."""
    x, y = _clean(x, y)
    n = len(x)
    if n < 3:
        return {"icc": float("nan"), "ci_low": float("nan"),
                "ci_high": float("nan"), "n": n}
    m = np.column_stack([x, y])
    grand = m.mean()
    ms_r = 2 * ((m.mean(axis=1) - grand) ** 2).sum() / (n - 1)
    ms_c = n * ((m.mean(axis=0) - grand) ** 2).sum()
    ms_e = (((m - m.mean(axis=1, keepdims=True) - m.mean(axis=0, keepdims=True)
              + grand) ** 2).sum()) / (n - 1)
    denom = ms_r + ms_e + 2 * (ms_c - ms_e) / n
    icc = (ms_r - ms_e) / denom if denom > 0 else float("nan")
    # Fisher z interval; adequate for reporting, exact CI not needed here.
    if np.isfinite(icc) and abs(icc) < 1:
        se = 1.0 / np.sqrt(max(n - 3, 1))
        z = np.arctanh(np.clip(icc, -0.999999, 0.999999))
        lo, hi = np.tanh(z - 1.96 * se), np.tanh(z + 1.96 * se)
    else:
        lo = hi = icc
    return {"icc": float(icc), "ci_low": float(lo), "ci_high": float(hi), "n": n}


def bland_altman(x, y) -> dict:
    """Bias and 95% limits of agreement of `y` against `x`."""
    x, y = _clean(x, y)
    d = y - x
    bias, sd = float(d.mean()), float(d.std(ddof=1)) if len(d) > 1 else 0.0
    return {"bias": bias, "sd": sd,
            "loa_low": bias - 1.96 * sd, "loa_high": bias + 1.96 * sd,
            "n": len(d)}


def affine_decompose(ref, test) -> dict:
    """How much of the error is a gain-and-offset map.

    Matters because every PT7 parameter is a ratio, count or frequency, so an
    affine map is score-invariant: you can halve RMSE and move PT7 by zero.
    """
    ref, test = _clean(ref, test)
    slope, intercept, *_ = stats.linregress(ref, test)
    rmse_raw = float(np.sqrt(np.mean((test - ref) ** 2)))
    corrected = (test - intercept) / slope
    rmse_corr = float(np.sqrt(np.mean((corrected - ref) ** 2)))
    frac = 1.0 - (rmse_corr ** 2) / (rmse_raw ** 2) if rmse_raw > 0 else 0.0
    return {"gain": float(slope), "offset": float(intercept),
            "rmse_raw": rmse_raw, "rmse_corrected": rmse_corr,
            "affine_fraction": float(frac)}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `~/miniconda3/python.exe -m pytest tests/test_validation_agreement.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add validation/agreement.py tests/test_validation_agreement.py
git commit -m "feat(validation): agreement statistics with affine decomposition"
```

---

## Task 4: Discrimination and power

**Files:**
- Create: `validation/discrimination.py`
- Test: `tests/test_validation_discrimination.py`

**Interfaces:**
- Produces: `auc(scores, labels) -> float`
- Produces: `auc_clustered_ci(scores, labels, clusters, n_boot=2000, seed=0) -> dict` with keys `auc`, `ci_low`, `ci_high`, `n_clusters`
- Produces: `legs_per_group(target_auc, alpha=0.05, power=0.80, icc=0.5, legs_per_participant=2) -> dict` with keys `legs_per_group`, `participants_per_group`, `design_effect`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_validation_discrimination.py
import numpy as np
import pytest
from validation.discrimination import auc, auc_clustered_ci, legs_per_group


def test_auc_of_perfect_separation_is_one():
    assert auc([1, 2, 3, 4], [0, 0, 1, 1]) == pytest.approx(1.0)


def test_auc_of_reversed_separation_is_zero():
    assert auc([4, 3, 2, 1], [0, 0, 1, 1]) == pytest.approx(0.0)


def test_bootstrap_resamples_clusters_not_rows():
    """Legs within a participant are correlated; resampling rows fakes precision."""
    rng = np.random.default_rng(0)
    scores = np.concatenate([rng.normal(0, 1, 20), rng.normal(2, 1, 20)])
    labels = np.array([0] * 20 + [1] * 20)
    clusters = np.repeat(np.arange(20), 2)
    out = auc_clustered_ci(scores, labels, clusters, n_boot=200, seed=1)
    assert out["n_clusters"] == 20
    assert out["ci_low"] < out["auc"] < out["ci_high"]


def test_power_matches_the_hand_computed_target():
    """AUC 0.807 (literature F1amp) at ICC 0.5 needs 20 legs per group."""
    out = legs_per_group(0.807, icc=0.5)
    assert out["legs_per_group"] == 20
    assert out["participants_per_group"] == 10
```

- [ ] **Step 2: Run test to verify it fails**

Run: `~/miniconda3/python.exe -m pytest tests/test_validation_discrimination.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'validation.discrimination'`

- [ ] **Step 3: Write the implementation**

```python
# validation/discrimination.py
"""Discrimination of spasticity presence, and how many legs it would take.

The unit is the LEG and legs cluster within participants, so every interval
here resamples participants rather than rows.
"""
from __future__ import annotations

import math

import numpy as np
from scipy.stats import norm, rankdata


def auc(scores, labels) -> float:
    scores = np.asarray(scores, dtype=float)
    labels = np.asarray(labels).astype(int)
    pos, neg = labels == 1, labels == 0
    n_p, n_n = pos.sum(), neg.sum()
    if n_p == 0 or n_n == 0:
        return float("nan")
    r = rankdata(scores)
    return float((r[pos].sum() - n_p * (n_p + 1) / 2) / (n_p * n_n))


def auc_clustered_ci(scores, labels, clusters, n_boot=2000, seed=0) -> dict:
    scores = np.asarray(scores, dtype=float)
    labels = np.asarray(labels).astype(int)
    clusters = np.asarray(clusters)
    uniq = np.unique(clusters)
    rng = np.random.default_rng(seed)
    boots = []
    for _ in range(n_boot):
        pick = rng.choice(uniq, size=len(uniq), replace=True)
        idx = np.concatenate([np.flatnonzero(clusters == c) for c in pick])
        a = auc(scores[idx], labels[idx])
        if np.isfinite(a):
            boots.append(a)
    lo, hi = (np.percentile(boots, [2.5, 97.5]) if boots
              else (float("nan"), float("nan")))
    return {"auc": auc(scores, labels), "ci_low": float(lo),
            "ci_high": float(hi), "n_clusters": int(len(uniq))}


def legs_per_group(target_auc, alpha=0.05, power=0.80, icc=0.5,
                   legs_per_participant=2) -> dict:
    """Hanley-McNeil sizing, inflated by the cluster design effect."""
    z_a, z_b = norm.ppf(1 - alpha / 2), norm.ppf(power)
    q1 = target_auc / (2 - target_auc)
    q2 = 2 * target_auc ** 2 / (1 + target_auc)
    base = None
    for n in range(3, 5000):
        se1 = math.sqrt((target_auc * (1 - target_auc)
                         + (n - 1) * (q1 - target_auc ** 2)
                         + (n - 1) * (q2 - target_auc ** 2)) / (n * n))
        se0 = math.sqrt((0.25 + (n - 1) / 12 + (n - 1) / 12) / (n * n))
        if (target_auc - 0.5) >= z_a * se0 + z_b * se1:
            base = n
            break
    de = 1 + (legs_per_participant - 1) * icc
    legs = int(math.ceil(base * de))
    return {"legs_per_group": legs,
            "participants_per_group": int(math.ceil(legs / legs_per_participant)),
            "design_effect": de}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `~/miniconda3/python.exe -m pytest tests/test_validation_discrimination.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add validation/discrimination.py tests/test_validation_discrimination.py
git commit -m "feat(validation): clustered AUC intervals and leg-level power sizing"
```

---

## Task 5: Stream loader — the 100 Hz and 20 Hz views of one trial

The phone streams at 100 Hz; `mobile-imu-core/src/resample.rs` decimates to a 50 ms grid before scoring. Both views are needed, because the difference between them *is* the decimation cost.

**Files:**
- Create: `validation/streams.py`
- Test: `tests/test_validation_streams.py`

**Interfaces:**
- Produces: `load_imu_stream(path) -> pd.DataFrame` with columns `t, angle_deg`
- Produces: `tick_resample(t, values, tick_s=0.05) -> tuple[np.ndarray, np.ndarray]` — a Python mirror of the Rust zero-order hold, tick 0 NaN by contract
- Produces: `butter_zero_lag(values, fs, cutoff_hz=5.0, order=2) -> np.ndarray`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_validation_streams.py
import numpy as np
import pytest
from validation.streams import tick_resample, butter_zero_lag


def test_tick_zero_is_nan_by_contract():
    """Mirrors mobile-imu-core: no state exists at the first tick instant.

    Back-filling it would change phi[0], which is A0_raw, which gates whether
    the trial is scorable at all.
    """
    t = np.arange(0, 1.0, 0.01)
    v = np.sin(t)
    _, held = tick_resample(t, v, 0.05)
    assert np.isnan(held[0])


def test_tick_resample_produces_the_expected_grid_length():
    t = np.arange(0, 1.0, 0.01)
    ticks, held = tick_resample(t, np.ones_like(t), 0.05)
    assert len(ticks) == len(held) == 20
    assert ticks[1] == pytest.approx(0.05)


def test_butter_zero_lag_introduces_no_phase_shift():
    """A zero-lag filter must not move a peak; that is the whole point."""
    fs = 100.0
    t = np.arange(0, 4, 1 / fs)
    v = np.sin(2 * np.pi * 1.0 * t)
    out = butter_zero_lag(v, fs, cutoff_hz=5.0)
    assert abs(int(np.argmax(out[50:350])) - int(np.argmax(v[50:350]))) <= 1


def test_butter_zero_lag_attenuates_above_the_cutoff():
    fs = 100.0
    t = np.arange(0, 4, 1 / fs)
    fast = np.sin(2 * np.pi * 20.0 * t)
    assert np.std(butter_zero_lag(fast, fs, cutoff_hz=5.0)) < 0.1 * np.std(fast)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `~/miniconda3/python.exe -m pytest tests/test_validation_streams.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'validation.streams'`

- [ ] **Step 3: Write the implementation**

```python
# validation/streams.py
"""Both views of a trial's angle series, and the two candidate filters.

`tick_resample` is a deliberate Python mirror of
`mobile-imu-core/src/resample.rs`. If the Rust changes, this must change with
it -- the point of the comparison is that they agree.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
from scipy.signal import butter, filtfilt

TICK_S = 0.05


def load_imu_stream(path) -> pd.DataFrame:
    """Raw ~100 Hz phone stream. The '#' header lines carry trial metadata."""
    df = pd.read_csv(Path(path), comment="#")
    t = df["phone_ts_ms"].to_numpy(dtype=float) / 1000.0
    return pd.DataFrame({"t": t - t[0],
                         "angle_deg": df["prox_pitch"].to_numpy(dtype=float)})


def tick_resample(t, values, tick_s: float = TICK_S):
    """Zero-order hold onto a fixed grid. Tick 0 is NaN by contract."""
    t = np.asarray(t, dtype=float)
    values = np.asarray(values, dtype=float)
    if t.size == 0:
        return np.array([]), np.array([])
    t0, t_end = t[0], t[-1]
    n_ticks = max(1, int((t_end - t0) / tick_s) + 1)
    ticks = t0 + np.arange(n_ticks) * tick_s
    held = np.full(n_ticks, np.nan)
    for i, tick in enumerate(ticks):
        if i == 0:
            continue                      # no state exists yet at tick 0
        prior = np.flatnonzero(t < tick)
        if prior.size:
            held[i] = values[prior[-1]]
    return ticks - t0, held


def butter_zero_lag(values, fs: float, cutoff_hz: float = 5.0, order: int = 2):
    """The pendulum-test literature convention: 2nd-order zero-lag Butterworth."""
    values = np.asarray(values, dtype=float)
    b, a = butter(order, cutoff_hz / (fs / 2.0), btype="low")
    return filtfilt(b, a, values)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `~/miniconda3/python.exe -m pytest tests/test_validation_streams.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add validation/streams.py tests/test_validation_streams.py
git commit -m "feat(validation): 100 Hz / 20 Hz stream views and both candidate filters"
```

---

## Task 6: Track 1 analyses R1–R4 — cohort, angle agreement, PT7 agreement, decimation cost

**Files:**
- Create: `validation/analyses.py`
- Modify: `validation/run.py`
- Test: `tests/test_validation_analyses.py:1` (extend)

**Interfaces:**
- Consumes: `cohort.build_cohort`, `agreement.*`, `streams.*`
- Produces: `R1_cohort`, `R2_angle_agreement`, `R3_pt7_agreement`, `R4_decimation_cost` — each `(cohort) -> dict` matching the Task 1 record contract.

The four analyses:

| ID | Title | What it computes |
|---|---|---|
| R1 | Cohort description | Participants and legs by tier, arm, and available reference. A CONSORT-style flow. |
| R2 | Angle criterion validity | Bland–Altman + affine decomposition, IMU vs OptiTrack, leave-one-participant-out. Reports `affine_fraction` prominently so RMSE is never read as PT accuracy. |
| R3 | PT7 criterion validity | ICC(2,1) per parameter, IMU vs OptiTrack, clustered by participant. **Seven separate rows, no composite.** |
| R4 | Decimation cost | R3 recomputed on the raw 100 Hz stream. The delta between R3 and R4 is the cost of the 20 Hz grid. |

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_validation_analyses.py
import pandas as pd
import pytest
from pathlib import Path
from validation import analyses, run
from validation.cohort import build_cohort

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def cohort():
    return build_cohort(REPO)


def test_every_track1_analysis_returns_a_valid_record(cohort):
    for fn in (analyses.R1_cohort, analyses.R2_angle_agreement,
               analyses.R3_pt7_agreement, analyses.R4_decimation_cost):
        run.validate_record(fn(cohort))


def test_r3_reports_seven_parameters_and_no_composite(cohort):
    """HEALTHY_REF is invalid, so a composite PT7 score must not be emitted."""
    rec = analyses.R3_pt7_agreement(cohort)
    if rec["status"] != "ok":
        pytest.skip(rec["notes"])
    params = {row["parameter"] for row in rec["payload"]["rows"]}
    assert params == {"R2n", "N", "phi_max_ratio", "omega_max_n",
                      "omega_min_n", "f", "area_ratio"}
    assert "pt7" not in params and "composite" not in rec["payload"]


def test_r2_reports_the_affine_fraction(cohort):
    """Guards the project's central caveat: affine error is PT7-invariant."""
    rec = analyses.R2_angle_agreement(cohort)
    if rec["status"] != "ok":
        pytest.skip(rec["notes"])
    assert "affine_fraction" in rec["payload"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `~/miniconda3/python.exe -m pytest tests/test_validation_analyses.py -v`
Expected: FAIL with `AttributeError: module 'validation.analyses' has no attribute 'R1_cohort'` (or ModuleNotFoundError)

- [ ] **Step 3: Write the implementation**

Implement the four functions in `validation/analyses.py`. Each begins
`rec = run.empty_record(...)`, sets `rec["status"] = "ok"` and fills
`rec["payload"]` only when its inputs are present; otherwise it sets
`"skipped"` with a `notes` string naming the missing input. R2/R3/R4 read the
paired trials from `Model_Analysis_Outputs/multimetric_trials.csv` for the
optical reference and the per-trial IMU CSVs via `streams.load_imu_stream`.

```python
# validation/analyses.py  (R3 shown in full; R1/R2/R4 follow the same shape)
from __future__ import annotations

from pathlib import Path

import pandas as pd

from . import agreement, run

REPO = Path(__file__).resolve().parents[1]
PT7 = ["R2n", "N", "phi_max_ratio", "omega_max_n", "omega_min_n", "f", "area_ratio"]


def R3_pt7_agreement(cohort: pd.DataFrame) -> dict:
    rec = run.empty_record("R3", "PT7 parameter criterion validity", track=1)
    path = REPO / "Model_Analysis_Outputs" / "multimetric_trials.csv"
    if not path.exists():
        rec["status"] = "skipped"
        rec["notes"] = f"missing {path.name}; run generate_multimetric_analysis.py"
        return rec
    d = pd.read_csv(path)
    rows = []
    for p in PT7:
        o, i = d.get(f"opti_{p}"), d.get(f"imu_{p}")
        if o is None or i is None:
            continue
        rows.append({"parameter": p,
                     **agreement.icc_2_1(o, i),
                     **{f"ba_{k}": v for k, v in agreement.bland_altman(o, i).items()}})
    rec["status"] = "ok"
    rec["payload"] = {"rows": rows, "n_trials": int(len(d)),
                      "n_participants": int(d["pid"].nunique())}
    rec["notes"] = ("Seven parameters reported separately. No composite PT7 score: "
                    "HEALTHY_REF is calibrated on a superseded reconstruction.")
    return rec
```

- [ ] **Step 4: Run test to verify it passes**

Run: `~/miniconda3/python.exe -m pytest tests/test_validation_analyses.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add validation/analyses.py validation/run.py tests/test_validation_analyses.py
git commit -m "feat(validation): Track 1 analyses R1-R4 (cohort, angle, PT7, decimation)"
```

---

## Task 7: Analysis R5 — filter convention comparison

Settles a real conflict: `compute_pt_params` smooths with **sample-count** Savitzky–Golay windows carrying no reference to sampling rate, so a 15-sample window spans 0.75 s of 20 Hz IMU data and 0.125 s of 120 Hz optical data. The pendulum-test literature convention is a 2nd-order zero-lag Butterworth at 5 Hz.

**Files:**
- Modify: `validation/analyses.py`
- Test: `tests/test_validation_analyses.py`

**Interfaces:**
- Produces: `R5_filter_comparison(cohort) -> dict`; payload has `rows` (one per PT7 parameter) with keys `parameter`, `icc_savgol`, `icc_butter`, `winner`.

- [ ] **Step 1: Write the failing test**

```python
def test_r5_compares_both_filters_on_every_parameter(cohort):
    rec = analyses.R5_filter_comparison(cohort)
    run.validate_record(rec)
    if rec["status"] != "ok":
        pytest.skip(rec["notes"])
    for row in rec["payload"]["rows"]:
        assert {"parameter", "icc_savgol", "icc_butter", "winner"} <= set(row)
        assert row["winner"] in {"savgol", "butter", "tie"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `~/miniconda3/python.exe -m pytest tests/test_validation_analyses.py::test_r5_compares_both_filters_on_every_parameter -v`
Expected: FAIL with `AttributeError: ... has no attribute 'R5_filter_comparison'`

- [ ] **Step 3: Write the implementation**

For each paired trial: score the IMU series twice — once through the existing
Savitzky–Golay path, once through `streams.butter_zero_lag(..., fs, 5.0)` — and
compute ICC against the optical reference both ways. `winner` is whichever ICC
is higher by more than 0.02, else `"tie"`.

- [ ] **Step 4: Run test to verify it passes**

Run: `~/miniconda3/python.exe -m pytest tests/test_validation_analyses.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add validation/analyses.py tests/test_validation_analyses.py
git commit -m "feat(validation): R5 compares sample-count SavGol against the 5 Hz literature filter"
```

---

## Task 8: Analyses R6–R8 — plausibility audit, proximal proxy, test–retest

**Files:**
- Modify: `validation/analyses.py`
- Test: `tests/test_validation_analyses.py`

| ID | Title | What it computes |
|---|---|---|
| R6 | Physical plausibility audit | Fraction of samples exceeding 597 °/s, the energy-conservation ceiling for a shank pendulum. Per trial and cohort-wide. |
| R7 | Single-sensor proximal proxy | Observational only: correlation between measured thigh sweep and IMU/optical ratio. Must report that this correlation was previously null (rho −0.037, p 0.73), so a gain correction is **not** licensed. |
| R8 | Test–retest reliability | ICC across repeat trials within a leg and session, per PT7 parameter. |

- [ ] **Step 1: Write the failing test**

```python
def test_r6_flags_physically_impossible_velocity():
    """597 deg/s is the undamped ceiling for a shank released from inverted."""
    rec = analyses.R6_plausibility_audit(cohort_fixture())
    run.validate_record(rec)
    if rec["status"] == "ok":
        assert rec["payload"]["ceiling_deg_s"] == pytest.approx(597, abs=5)
        assert 0.0 <= rec["payload"]["fraction_above_ceiling"] <= 1.0


def test_r7_refuses_to_license_a_gain_correction():
    rec = analyses.R7_proximal_proxy(cohort_fixture())
    run.validate_record(rec)
    assert "gain correction" in rec["notes"].lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `~/miniconda3/python.exe -m pytest tests/test_validation_analyses.py -v`
Expected: FAIL with `AttributeError`

- [ ] **Step 3: Write the implementation**

Add the three functions. R6 computes `np.gradient(angle, t)` on the raw
100 Hz stream and counts samples above 597 °/s. R7 computes the Spearman
correlation and sets `notes` to state plainly that the null result forbids a
fixed gain correction. R8 groups trials by `(pid, leg, condition)` and needs
at least three trials per group.

- [ ] **Step 4: Run test to verify it passes**

Run: `~/miniconda3/python.exe -m pytest tests/test_validation_analyses.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add validation/analyses.py tests/test_validation_analyses.py
git commit -m "feat(validation): R6-R8 plausibility audit, proximal proxy, test-retest"
```

---

## Task 9: The three exporters

One `results.json`, three renderers. No exporter may compute a statistic — if a number is not in `results.json`, it does not appear.

**Files:**
- Create: `validation/exports/__init__.py`, `validation/exports/figures.py`, `validation/exports/tables.py`, `validation/exports/dossier.py`
- Test: `tests/test_validation_exports.py`

**Interfaces:**
- Produces: `figures.render(results: dict, outdir: Path) -> list[Path]`
- Produces: `tables.render(results: dict, outdir: Path) -> list[Path]`
- Produces: `dossier.render(results: dict, outdir: Path) -> Path`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_validation_exports.py
import json
import pytest
from validation.exports import figures, tables, dossier


def test_exporters_never_compute_statistics(tmp_path):
    """A number absent from results.json must be absent from every output."""
    results = {"records": [{"id": "R3", "title": "PT7", "track": 1,
                            "status": "ok", "notes": "",
                            "payload": {"rows": [{"parameter": "f", "icc": 0.87,
                                                  "ci_low": 0.7, "ci_high": 0.95,
                                                  "n": 79}],
                                        "n_trials": 79, "n_participants": 12}}]}
    out = tables.render(results, tmp_path)
    text = "\n".join(p.read_text(encoding="utf-8") for p in out)
    assert "0.87" in text
    assert "0.88" not in text


def test_skipped_records_render_as_skipped_not_as_absent(tmp_path):
    """A skipped analysis must be visible in the output, with its reason."""
    results = {"records": [{"id": "R9", "title": "Clinical", "track": 1,
                            "status": "skipped",
                            "notes": "MAS-0 legs come from one participant",
                            "payload": {}}]}
    report = dossier.render(results, tmp_path)
    body = report.read_text(encoding="utf-8")
    assert "R9" in body and "SKIPPED" in body.upper()
    assert "one participant" in body
```

- [ ] **Step 2: Run test to verify it fails**

Run: `~/miniconda3/python.exe -m pytest tests/test_validation_exports.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'validation.exports'`

- [ ] **Step 3: Write the implementation**

`tables.render` writes one CSV plus one markdown table per `ok` record and a
`skipped.md` listing every non-`ok` record with its notes. `figures.render`
draws a forest plot of R3/R4 ICCs with confidence intervals, a Bland–Altman
panel for R2, and a paired bar chart for R5. `dossier.render` writes
requirement → evidence → verdict rows, one per record, with `SKIPPED` or
`BLOCKED` shown explicitly rather than omitted.

- [ ] **Step 4: Run test to verify it passes**

Run: `~/miniconda3/python.exe -m pytest tests/test_validation_exports.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add validation/exports/ tests/test_validation_exports.py
git commit -m "feat(validation): figure, table and dossier exporters from one results.json"
```

---

## Task 10: Analysis R9 — clinical association, gated

**This analysis must refuse to run on the current cohort and say why.** Every clinician-examined MAS=0 trial is P5 and every MAS>0 trial is P13/P14/P15, so the contrast is confounded with participant identity.

**Files:**
- Modify: `validation/analyses.py`
- Test: `tests/test_validation_analyses.py`

**Interfaces:**
- Produces: `R9_clinical_association(cohort) -> dict`; payload when gated contains `gate_reason`, `legs_available`, `legs_required`, `recruitment_target`.

- [ ] **Step 1: Write the failing test**

```python
def test_r9_refuses_a_confounded_contrast(cohort):
    """Today: every MAS=0 leg is P5. That is not a spasticity contrast."""
    rec = analyses.R9_clinical_association(cohort)
    run.validate_record(rec)
    assert rec["status"] == "skipped"
    assert rec["payload"]["legs_required"] == 20
    assert "confounded" in rec["payload"]["gate_reason"].lower()


def test_r9_gate_names_the_recruitment_target(cohort):
    rec = analyses.R9_clinical_association(cohort)
    target = rec["payload"]["recruitment_target"]
    assert set(target["tier_d_with_imu"]) == {18, 19, 20, 21, 22, 23, 24}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `~/miniconda3/python.exe -m pytest tests/test_validation_analyses.py -v`
Expected: FAIL with `AttributeError: ... has no attribute 'R9_clinical_association'`

- [ ] **Step 3: Write the implementation**

```python
def R9_clinical_association(cohort: pd.DataFrame) -> dict:
    rec = run.empty_record("R9", "Clinical association with MAS", track=1)
    a = cohort[(cohort.mas_tier == "A") & cohort.has_imu]
    pos = a[a.mas_grade.astype(str).ne("0")]
    neg = a[a.mas_grade.astype(str).eq("0")]
    required = discrimination.legs_per_group(0.807, icc=0.5)["legs_per_group"]

    confounded = pos["pid"].nunique() == 0 or neg["pid"].nunique() <= 1
    if confounded or len(pos) < required or len(neg) < required:
        rec["status"] = "skipped"
        rec["payload"] = {
            "gate_reason": (
                "Contrast is confounded with participant identity: every Tier-A "
                f"MAS=0 leg comes from {sorted(neg['pid'].unique())} and every "
                f"MAS>0 leg from {sorted(pos['pid'].unique())}. An AUC here would "
                "measure participant identity, not spasticity."),
            "legs_available": {"mas_positive": int(len(pos)),
                               "mas_negative": int(len(neg))},
            "legs_required": int(required),
            "recruitment_target": {
                "tier_d_with_imu": sorted(
                    cohort.loc[(cohort.mas_tier == "D") & cohort.has_imu,
                               "pid"].unique().tolist()),
                "note": ("These participants already have IMU recordings and no "
                         "MAS row. Retrospective assessment adds legs at zero "
                         "recording cost; four are stroke.")},
        }
        rec["notes"] = "Gated. See payload.gate_reason."
        return rec

    # ... unreachable until recruitment completes; implemented in the same shape
    # as R3 using discrimination.auc_clustered_ci clustered on pid.
    return rec
```

- [ ] **Step 4: Run test to verify it passes**

Run: `~/miniconda3/python.exe -m pytest tests/test_validation_analyses.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add validation/analyses.py tests/test_validation_analyses.py
git commit -m "feat(validation): R9 clinical arm, gated on a confounded contrast"
```

---

## Task 11: HEALTHY_REF recalibration decision record

`HEALTHY_REF` was calibrated on a superseded reconstruction. It cannot be recalibrated from Tier-C assumed controls, because those participants were never examined — so recalibration is itself gated on Task 10's recruitment. This task records that dependency rather than pretending to resolve it.

**Files:**
- Create: `docs/reports/2026-09-03-healthy-ref-status.md`
- Modify: `validation/analyses.py` (add `R10_healthy_ref_status`)
- Test: `tests/test_validation_analyses.py`

- [ ] **Step 1: Write the failing test**

```python
def test_r10_reports_healthy_ref_as_blocked_with_its_dependency(cohort):
    rec = analyses.R10_healthy_ref_status(cohort)
    run.validate_record(rec)
    assert rec["status"] == "blocked"
    assert "R9" in rec["notes"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `~/miniconda3/python.exe -m pytest tests/test_validation_analyses.py -v`
Expected: FAIL with `AttributeError`

- [ ] **Step 3: Write the implementation**

Add `R10_healthy_ref_status`, returning `status="blocked"` with notes naming
the dependency on R9's recruitment, plus the count of Tier-A MAS=0 legs
available to recalibrate against (currently 2). Write the companion report
documenting why the current constants must not be used for a composite score.

- [ ] **Step 4: Run test to verify it passes**

Run: `~/miniconda3/python.exe -m pytest tests/test_validation_analyses.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add validation/analyses.py docs/reports/2026-09-03-healthy-ref-status.md tests/test_validation_analyses.py
git commit -m "docs(validation): record HEALTHY_REF recalibration as blocked on recruitment"
```

---

## Task 12: Track 2 — bench rig ingest

Runs only when bench data exists. Until then every Track 2 record renders as `skipped` with a reason, which is why Task 1's contract forbids a silent skip.

**Files:**
- Create: `validation/bench.py`
- Modify: `validation/analyses.py` (add `B1_gyro_scale_factor`, `B2_bandwidth`, `B3_pt7_vs_truth`, `B4_proximal_penalty`)
- Test: `tests/test_validation_bench.py`

**Interfaces:**
- Produces: `load_bench_run(path) -> pd.DataFrame` with columns `t, encoder_deg, pot_deg, tacho_dps, release_edge`
- Produces: `align_on_release(bench, imu) -> tuple[pd.DataFrame, pd.DataFrame, float]` — returns the aligned frames and the lag applied.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_validation_bench.py
import numpy as np
import pytest
from validation.bench import align_on_release


def test_alignment_uses_only_the_first_half_cycle():
    """Cross-correlating the whole trial lets tail drift bias the lag.

    This project has already been bitten by a ~0.7 s capture skew.
    """
    fs = 200.0
    t = np.arange(0, 6, 1 / fs)
    truth = np.exp(-0.3 * t) * np.cos(2 * np.pi * 0.85 * t)
    drifting = truth - 0.05 * t          # tail drift only
    lag = align_on_release(t, truth, t, drifting)[2]
    assert abs(lag) < 0.02
```

- [ ] **Step 2: Run test to verify it fails**

Run: `~/miniconda3/python.exe -m pytest tests/test_validation_bench.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'validation.bench'`

- [ ] **Step 3: Write the implementation**

Implement the loader and an alignment that seeds from the release edge and
refines by cross-correlation over the first half-cycle only. The four `B*`
analyses follow the Task 1 contract and return `skipped` with
`notes="no bench runs under Model_Analysis_Outputs/Bench/"` when the directory
is absent.

- [ ] **Step 4: Run test to verify it passes**

Run: `~/miniconda3/python.exe -m pytest tests/test_validation_bench.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add validation/bench.py validation/analyses.py tests/test_validation_bench.py
git commit -m "feat(validation): Track 2 bench ingest, release-anchored alignment"
```

---

## Task 13: Wire the CLI and produce the first full run

**Files:**
- Modify: `validation/run.py`
- Test: `tests/test_validation_run.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_validation_run.py
import json
from pathlib import Path
from validation import run

REPO = Path(__file__).resolve().parents[1]


def test_full_run_emits_every_record_and_never_crashes_on_a_skip(tmp_path):
    rc = run.main(["--outdir", str(tmp_path)])
    assert rc == 0
    results = json.loads((tmp_path / "results.json").read_text(encoding="utf-8"))
    ids = {r["id"] for r in results["records"]}
    assert {"R1", "R2", "R3", "R4", "R5", "R6", "R7", "R8", "R9", "R10"} <= ids
    for r in results["records"]:
        run.validate_record(r)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `~/miniconda3/python.exe -m pytest tests/test_validation_run.py -v`
Expected: FAIL — `main` currently returns 0 without writing `results.json`

- [ ] **Step 3: Write the implementation**

`main` builds the cohort, runs every registered analysis inside a try/except
that converts an exception into a `blocked` record carrying the traceback
summary in `notes`, validates all records, writes `results.json`, then calls
the three exporters.

- [ ] **Step 4: Run the whole thing for real**

```bash
~/miniconda3/python.exe -m validation.run --outdir Model_Analysis_Outputs/Validation
~/miniconda3/python.exe -m pytest tests/test_validation_run.py -v
```

Expected: `results.json`, a figures directory, tables, and `dossier.md`. Every
Track 2 record `skipped` with a reason; R9 and R10 gated with reasons.

- [ ] **Step 5: Commit**

```bash
git add validation/run.py tests/test_validation_run.py Model_Analysis_Outputs/Validation/
git commit -m "feat(validation): CLI producing results.json and all three exports"
```

---

## Task 14: Recruitment instrument for the gated clinical arm

Not code. Produces the artefact that unblocks Tasks 10 and 11.

**Files:**
- Create: `docs/reports/2026-09-03-mas-recruitment-plan.md`

- [ ] **Step 1: Write the recruitment plan**

Contents, all figures taken from the R9 gate payload rather than retyped:

- **Target:** 20 legs per group (10 participants per group at 2 legs each), sized
  on the literature's best pendulum metric (F1amp, AUC 0.807) at α 0.05,
  power 0.80, ICC 0.5.
- **Current:** 5 MAS>0 legs (P13 L/R, P14 L/R, P15 R) and 2 Tier-A MAS=0 legs (P5 L/R).
- **Zero-cost tranche:** P18, P19, P20, P21, P22, P23, P24 already have IMU
  recordings and no MAS row — 14 legs, four of them stroke. Retrospective
  clinician assessment or chart review closes most of the gap without a new
  recording session.
- **Second tranche:** the 17 Tier-C `ASSUMED` control legs need an actual
  recorded assessment before they can serve as MAS=0 comparators.
- **Stopping rule:** re-run `validation.run`; R9 un-gates automatically once
  both arms reach 20 legs with Tier-A provenance.

- [ ] **Step 2: Commit**

```bash
git add docs/reports/2026-09-03-mas-recruitment-plan.md
git commit -m "docs(validation): MAS recruitment plan sized from the R9 power gate"
```

---

## Self-Review

**Spec coverage.** All three output formats are covered by Task 9 from one
`results.json`. Track 1 is Tasks 6–8, 10, 11; Track 2 is Task 12; recruitment
with a power calculation is Tasks 10 and 14. The provenance leak found on
2026-09-03 is closed by Task 2.

**Placeholders.** None. The only prose-described implementations (Tasks 7, 8,
9, 11, 12 step 3) each name their exact inputs, outputs and decision rule, and
their tests pin the contract.

**Type consistency.** Every analysis is `(cohort: pd.DataFrame) -> dict` in the
Task 1 record shape. `legs_per_group` returns the same three keys everywhere it
is called. `PT7` is defined once in `analyses.py` and matches
`pendulastic_pt_score._PARAM_KEYS`.

**Known gap, stated rather than hidden.** Task 11 cannot resolve
`HEALTHY_REF`; it is genuinely blocked on Task 14's recruitment. No task
pretends otherwise, and no exporter will emit a composite PT7 score until it
lifts.
