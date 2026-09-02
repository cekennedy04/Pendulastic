# Regenerating the analysis outputs

Written 2026-09-02, after the reconstruction fixes on `fix/optitrack-trial-quality`
made every output on disk stale.

## Why this needs a runbook

A single pass over the corpus loads each trial at **roughly 22 seconds**, and
`generate_paper_results_analysis.py` needs the better part of an hour end to end.
That is longer than some agent/CI environments allow a process to live, and
attempts there were repeatedly killed mid-corpus.

Both expensive stages are now cached, so a killed run resumes instead of starting
over. **Run these from an ordinary terminal, not from a time-limited session.**

## Order, and why it is not arbitrary

```
1. generate_paper_results_analysis.py     writes paper_results_analysis_trials.csv
2. generate_multimetric_analysis.py       writes multimetric_trials.csv
3. generate_longitudinal_and_score_figures.py
4. generate_group_condition_figures.py
5. generate_pt_score_critique.py
6. generate_paper_figures.py              reads (1)
7. generate_all_figures_comprehensive.py  reads (1) AND (2) -- must be last
8. python -c "import pt_cohort_common as p; p.run_cohort_comparison()"
```

`generate_all_figures_comprehensive.py` makes **no corpus calls at all** -- it
renders from the two cached tables. Run it before its producers and it will
happily draw today's figures from yesterday's numbers and exit 0.

`generate_figures_by_spasticity.py` is independent of the above and already
current as of 2026-09-02 11:45.

## The caches

`Model_Analysis_Outputs/_cache/` holds two, both keyed on each source file's
size and mtime plus `_CACHE_VERSION` in `generate_paper_results_analysis.py`:

| file | covers |
|---|---|
| `paper_a0_by_trial.json` | the A0 walk in `a0_by_leg_from_optitrack` |
| `paper_trial_params.json` | the per-trial IMU/OptiTrack PT parameters |

Both are needed. The A0 walk runs inside `load_group_and_demo`, i.e. **before**
the per-trial loop, so caching only the loop achieves nothing -- every killed run
died upstream of the checkpoint having banked no results. That was the actual
bug behind "the cache file does not exist even after several attempts".

**Bump `_CACHE_VERSION` whenever anything feeding `imu_pt_params_for_trial`
changes** -- the loaders, `windowed_pt_params`, or the recorded quality fields.
A stale cache here does not crash, it publishes wrong numbers. A needless full
recompute costs an hour; a silently stale one costs a result.

To force a clean rebuild, delete `Model_Analysis_Outputs/_cache/`.

## Optional: fill the A0 cache faster

`generate_paper_results_analysis.py` fills it serially. Three worker processes
are about three times quicker and write the identical keys and values; more than
three OOM'd scipy on this machine ("paging file is too small"). See the warming
script referenced in the session notes, or simply let the serial run proceed.

## Archive before regenerating

These tables are **untracked**, so regenerating destroys the ability to compare
the same trials before and after. That already cost us once: the ICC drop below
could only be inferred, because `paper_results_analysis_trials.csv` was
overwritten before its trial list could be diffed against the 2026-08-19 run.

Copy the numeric outputs first -- they are small (91 CSVs, ~0.6 MB against
239 MB for the directory as a whole). One archive already exists at
`Model_Analysis_Outputs/_archive/2026-09-02_1253_pre-regen_99f0e05`.

## Open question this regeneration exists to answer

IMU-vs-OptiTrack ICC(2,1) fell between 2026-08-19 and 2026-09-02:

| parameter | 08-19 (n=61) | 09-02 (n=119) |
|---|---|---|
| R2n | 0.226 | 0.037 |
| N | 0.458 | 0.071 |
| omega_min_n | 0.214 | -0.012 |

The trial count nearly doubled because `bccb44c` stopped *refusing*
ill-conditioned trials -- correct in itself, since analysis flags rather than
drops, but nothing downstream read the flag, so every readmitted trial entered
the agreement statistic unweighted.

Each row now carries `optitrack_method`, `opti_coverage` and
`opti_conditioning`. Recomputing ICC on the well-conditioned subset and
comparing against all trials is what decides whether this is a data-quality
stratification or a genuine negative result. **It is not yet answered.**
