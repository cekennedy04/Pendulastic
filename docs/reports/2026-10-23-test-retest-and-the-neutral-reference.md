# Test-retest: the neutral reference is the error budget

**Answer: within a single sitting, the phone cannot reproduce its own
parameters. On ~153 trials across 39 leg/condition groups and 13 participants,
A0 — the parameter KD2 nominates to carry severity — has a standard error of
measurement of 17.5° on a mean of 61.7°, an MDC95 near 48°. Seven of nine
parameters are in Koo & Li's "poor" band. The cause is localised: within a leg
the release angle is stable to 2.7° while the NEUTRAL estimate moves by 12.9°
(worst group 39.7°), and A0 = release − neutral inherits it. The four
parameters that divide by A0 inherit it twice. Reverting `61d5723` recovers a
large part of it — six of the seven Popović parameters improve on a matched
trial set, `phi_max_ratio` by 72% — but A0's SEM only falls to 13.1°, so the
neutral reference remains the dominant error term and is not a sensor problem.**

Tooling: `test_retest_reliability.py`, `retest_compare.py` on
`bench/retest-diagnostics`. Simulation: `a0_variance_probe.py`,
`a0_startangle_probe.py`, `a0_neutral_probe.py` (session scratch, reproduced
below). Corpus numbers measured on the operator's machine; simulation on a
container against the same scorer.

---

## 1. What was measured

Within-session test-retest: the same leg, swung several times in one sitting,
should score the same each time. No reference instrument, no MAS grade and no
rig is required, and every input was already on disk — which is why this was
available to run before any of the rig work.

Scored on the production path: `rmse_pipeline_common.reconstruct_trial` →
`imu_calibration_tuner.replay_trial` at `ANALYSIS_TICK_S` (100 Hz) →
`pendulastic_pt_score.compute_pt_params`. Not on raw `prox_pitch`, which
bypasses the AHRS and the EMA and is not what the app computes.

ICC(1,1), one-way random: repeat trials are not distinguishable raters, so
"trial 2" carries no meaning "trial 1" does not. Groups need ≥3 repeats.

**On reading ICC here.** ICC is between-group variance over total variance, so
in a cohort of 13 participants all graded MAS 0–1 it is suppressed by the
narrow spread *between* legs even where precision is adequate. That makes it a
misleading headline for this corpus. **SEM and MDC95 are the honest figures** —
they are in each parameter's own units and do not depend on how different the
participants happen to be. They fail on their own terms regardless.

## 2. The result

Signed extraction (`fix/swing-angle-signed-abs-fold` head), 161 trials scored,
153 in 39 groups with ≥3 repeats, 13 participants:

| parameter | ICC(1,1) | SEM | MDC95 | mean | CV% |
|---|---|---|---|---|---|
| `R2n` | 0.034 | 0.942 | 2.611 | 0.972 | 96.9 |
| `N` | 0.727 | 1.146 | 3.178 | 4.595 | 24.9 |
| `phi_max_ratio` | 0.192 | 0.437 | 1.212 | 0.380 | 115.1 |
| `omega_max_n` | 0.025 | 5.196 | 14.403 | 5.176 | 100.4 |
| `omega_min_n` | 0.222 | 0.004 | 0.011 | 0.003 | 134.6 |
| `f` | 0.207 | 0.290 | 0.804 | 0.815 | 35.6 |
| `area_ratio` | 0.483 | 0.226 | 0.626 | 0.381 | 59.2 |
| `A0_deg` | 0.053 | 17.495 | 48.493 | 61.727 | 28.3 |
| `omega_peak_deg_s` | 0.538 | 43.051 | 119.332 | 276.822 | 15.6 |

Pre-release hold across kept trials: 141 steady, 8 drifting, 4 unassessable.
So hold drift is not the story.

**A0's MDC95 is 48° on a swing whose whole range is about 62°.** No change a
clinician could induce is detectable in it.

## 3. The pattern: everything divided by A0

Checked against each parameter's definition in `pendulastic_pt_score.py`:

| divides by A0 | parameter | definition | ICC |
|---|---|---|---|
| yes | `R2n` | `A1 / (1.6 * A0)` | 0.034 |
| yes | `omega_max_n` | `omega_peak_dps / A0` | 0.025 |
| yes | `phi_max_ratio` | `max(ret_pk) / A0` | 0.192 |
| yes | `omega_min_n` | `min(omega_abs) / A0` | 0.222 |
| no | `area_ratio` | `|P+ − P−| / P_total` | 0.483 |
| no | `omega_peak_deg_s` | peak angular velocity | 0.538 |
| no | `N` | counted extrema | 0.727 |

`omega_peak_deg_s` and `omega_max_n` are the same quantity, one divided by A0.
Reliability falls from 0.538 to 0.025 across that division alone. `f` is the
one exception to the pattern — 0.207 without dividing by A0 — and has its own
period-estimation history (see `2026-09-14-release-detection.md`).

Normalising by the drop amplitude exists to *remove* start-angle dependence, so
if A0 were measured accurately those ratios should be **more** repeatable than
their own numerators. The measured ordering is backwards, which requires an
explanation rather than a report.

## 4. Four mechanisms eliminated by simulation

Identical physics through the same production scorer, one nuisance factor at a
time. A0's coefficient of variation:

| arm | what varied | A0 CV% | note |
|---|---|---|---|
| A | nothing | **0.0** | scorer is deterministic; a non-zero CV here would be a bug |
| B | stop time, 8–15 s | 0.4 | `neutral_deg` 0.2%; `area_ratio` 34.8%, `N` 5.0% |
| C | hold length, 1.5–6 s | 0.0 | release detection stable on clean onsets |
| D | angle noise, 0.3° RMS | 0.6 | `area_ratio` 74.1%, `omega_min_n` 63.1% |
| G | linear tail drift, ±1.5°/s | 0.1 | `neutral_deg` moved 3.7° and A0 did **not** — the baseline-drift correction absorbed it |

None approaches the measured 28.3%. Two incidental findings: `area_ratio` and
`omega_min_n` are fragile *by construction* — 19–74% CV from stop time or
modest noise on identical physics — which independently supports
`compute_pt_score_simple()` already dropping `area_ratio`.

## 5. The arm that discriminates

If the examiner simply released the leg from a different extension each repeat,
the A0-normalised ratios should absorb it. Arm E injected genuine
release-angle variation producing A0 CV 28.7%, almost exactly the measured
28.3%:

| | simulated genuine release variation | measured corpus |
|---|---|---|
| `A0_deg` | CV 28.7% | CV 28.3% — matches |
| the four A0-normalised ratios | **CV 0.0%** | ICC 0.025–0.222, **worst in table** |
| `omega_peak_deg_s` | CV 28.7% — noisiest | CV 15.6% — **best in table** |

**The ordering is exactly inverted.** Genuine release-angle variation is
therefore ruled out, and so is any mechanism that moves A0 *in proportion to
the real swing*, because the ratios would absorb that too. What remains is A0
carrying error that does not correspond to the swing.

## 6. What the corpus diagnostic found

A0 = release − neutral, so per-trial `pre_release_deg` and `neutral_deg` were
recorded and their within-group scatter compared (mean over 40 groups of the
within-group SD, degrees):

| term | mean SD | median | worst group |
|---|---|---|---|
| `pre_release_deg` | **2.66** | 2.79 | 6.19 |
| `neutral_deg` | **12.89** | 9.46 | **39.69** |
| `A0_deg` | 10.43 | 7.65 | 41.38 |

**The examiner is consistent to 2.7°. The neutral estimate moves by 12.9°.**
A0 tracks the neutral, not the release.

`neutral_deg` is the median of the settled tail, so a trial that has not
settled estimates it badly. Restricting to the longer-tailed half (post-release
≥ 10.4 s, 79 trials) improves SEM on every parameter checked:

| parameter | SEM all | SEM longer tail | |
|---|---|---|---|
| `R2n` | 0.796 | **0.251** | 3.2× better |
| `omega_max_n` | 4.275 | **0.941** | 4.5× better |
| `omega_peak_deg_s` | 36.949 | 29.037 | |
| `N` | 1.048 | 0.912 | |
| `A0_deg` | 13.979 | 13.709 | |

Read SEM, not ICC, in that block: halving the sample leaves ~2 trials per group
and makes ICC unstable (A0's goes slightly negative while its SEM improves).

**This was the first hypothesis, and the simulation had said it was wrong.**
The simulation was wrong, not the hypothesis: its synthetic traces settle
cleanly to a well-defined neutral within a couple of seconds, so the tail
median had nothing to get wrong. Real traces do not settle like that. The
honest reading is that arms B and G bound how much a *clean* trace's neutral
can move, not how much a real one does.

## 7. The signed extraction, on a matched trial set

Restricted to the 153 trials in 39 groups **both** variants scored, so this
compares two algorithms rather than two populations:

| parameter | ICC signed | ICC abs | SEM signed | SEM abs | SEM change |
|---|---|---|---|---|---|
| `phi_max_ratio` | 0.192 | 0.658 | 0.437 | 0.123 | **−71.9%** |
| `omega_max_n` | 0.025 | 0.147 | 5.196 | 1.777 | **−65.8%** |
| `R2n` | 0.034 | 0.346 | 0.942 | 0.330 | **−65.0%** |
| `A0_deg` | 0.053 | 0.228 | 17.495 | 13.105 | −25.1% |
| `omega_peak_deg_s` | 0.538 | 0.616 | 43.051 | 37.276 | −13.4% |
| `f` | 0.207 | 0.292 | 0.290 | 0.263 | −9.3% |
| `N` | 0.727 | 0.771 | 1.146 | 1.052 | −8.3% |
| `area_ratio` | 0.483 | 0.521 | 0.226 | 0.215 | −4.9% |
| `omega_min_n` | 0.222 | 0.021 | 0.004 | 0.009 | +141.1% |

Six of seven Popović parameters improve. The abs path is a strict superset on
coverage — every trial the signed path scored, plus `19_right_pre_T3`,
`23_left_pre_T1` and `4_left_pre_T1`.

The lone regression, `omega_min_n`, is below its own noise floor under either
variant: SEM 0.004–0.009 against a mean of 0.003–0.004, CV 135–227%.

An earlier unmatched comparison understated this. The three trials unique to
the abs run were *masking* the improvement: matched, `R2n` improves 65.0%
rather than 15.5% and `omega_max_n` 65.8% rather than 17.7%.

Acted on in `fix/revert-signed-swing-angle`. The change's original
justification came from the horizontal SSR bench, which
`TILTED_RIG_ANGLE_VALIDATION_PROTOCOL.md` states cannot grade the fused angle
at all — so it was judged on a configuration unable to measure it. The revert
does not settle the question; it returns it to the tilted rig.

## 8. What this supports, and what it does not

Supported:

- Within-session repeatability is poor for most parameters, and the cause is
  the **settled-tail neutral reference**, not the sensor. `omega_peak_deg_s`
  never touches the neutral and is the most repeatable parameter in the set
  (CV 14.0%) — the control the claim rests on.
- `61d5723` degrades repeatability and should not ship.
- The remedy is partly protocol and already built: settle-termination, and the
  `clean`/`short`/`unsettled` capture-quality classification.

Not supported, and not claimed:

- That the instrument is unfit. The error is concentrated in a derived
  reference, which is a definition and protocol problem before it is a
  hardware one.
- Any statement about absolute accuracy. This measures repeatability only;
  a perfectly repeatable instrument can be repeatably wrong. Accuracy needs
  the tilted rig against the 1 kHz encoder, and the optical corpus.
- Any severity claim. R16 and R25 stand untouched.

Open:

- Whether a more robust neutral estimator reduces A0's SEM below 13.1°.
- What a full settled tail buys when stratified by the app's own
  `capture_quality` rather than a median split on tail length.
- Whether re-mount repeatability (the phone re-strapped between repeats) is
  worse than the within-mount case measured here. A clinic re-straps; this
  corpus is the optimistic bound.

## 9. Reproducing it

```
# signed
git checkout fix/swing-angle-signed-abs-fold
python test_retest_reliability.py

# abs
git checkout -b bench/retest-abs fix/swing-angle-signed-abs-fold
git revert --no-commit --no-edit 61d5723
python test_retest_reliability.py

# matched
python retest_compare.py <signed cache> <abs cache>
```

Each run prints its angle-extraction label, tree state and an algorithm
fingerprint, and writes a cache and CSV keyed by that fingerprint, so two
variants cannot share a cache. The abs run reported here is fingerprint
`115e959bc4b9`; the signed run predates fingerprinting and used the legacy
`test_retest_params.json`.

**A caution that cost a run.** Before fingerprinting, the cache was one fixed
filename keyed on trial key alone with `if key in cache: continue`, so
re-running after reverting the angle path reused the previous variant's
parameters and reported the two variants as identical. An uncommitted
`git revert` also follows a checkout, so a tree on the signed branch can be
running abs code — which mislabelled two runs in both directions. Both hazards
are closed, but any result predating 2026-10-23 that compared two variants
should be re-checked for them.

## 10. Surfaced along the way

- **`tests/fixtures/pendulum_sim.py` has never been committed to any branch**,
  though the validation plan and two reports cite it as the basis for PT7's
  non-monotonicity — the finding KD2 rests on. That result is not currently
  reproducible from the repository.
- **16 of 21 exclusions were optical-only**, dropping Participant 17's pre
  timepoint and Participant 22's left leg from a phone-only analysis because
  OptiTrack failed. Addressed by scoping exclusions (`EXCLUSION_SCOPES`).
- **`tests/conftest.py` imported tkinter at module scope**, making all 78 test
  files uncollectable on a Tk-less Python. Fixed on `fix/ci-green-2026-10`.
- **CI had failed 79 consecutive runs**, including `main`'s only run, from
  three structural causes. Fixed in PR #67.
- **`test_the_caveat_travels_with_a_grade_served_over_the_wire` fails** on both
  variants. It is R26 — a phone-derived grade must carry its measured
  disagreement wherever it reaches a person, including over an API — and is a
  requirement-level failure deserving its own issue.
