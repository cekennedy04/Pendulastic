# Task: fix pendulum-test release detection so real swings score reliably and repeatably

## The problem, in one sentence
The app reports `f = 0`, `N = 0`, and refuses `R2n` on real, clearly-oscillating pendulum swings — inconsistently — because its release detector fires on pre-release motion instead of the actual release, and everything downstream is measured from the wrong instant.

## Evidence (measured, not assumed)
A 15-trial bench session was recorded with a phone rigidly mounted on a motor-driven arm (raw IMU exports in `Pendulastic_Mobile_App_Trial/Session_3/*.jsonl`, app scores in that folder's `*-manifest.json`).

- The app **refused 10 of 15 trials** (`unmeasured = ["r2n","phi_max_ratio","f"]`, and `f = 0`, `n = 0`).
- **Every refused trial had a real oscillating swing** — 9 to 30 physical direction reversals, counted drift-free from the raw gyro rate. The refused trials are indistinguishable from the scored ones in swing count and span.
- **Near-identical swings got opposite verdicts:** trials 1 and 11 (10 reversals, 2.0 s span) scored; trial 15 (10 reversals, 2.0 s span) was refused. Same swing, opposite result — the scoring is unstable.
- The app **always** produced `a0_deg` (the first drop); it only failed on the metrics that need it to lock onto the oscillation after release.
- A recovery re-score that supplied a **correct** release index recovered `f` and `N` on **10 of 10** refused trials. The swings are fully scorable; the release detector is the sole point of failure.
- On all 35 pilot exports the app's `release_idx` was `0` on every single trial — it never located a release.

Root cause: the release detector is a **displacement** detector (baseline = median of the first ~0.6 s, threshold = ~8% of signal range, fire on first crossing). A multi-second pre-release hold — the correct clinical gesture — has tremor and drift that trip it early, so "release" lands in the hold or the lift, and the oscillation falls outside the analysis window.

### Second, distinct defect: a frequency ceiling in peak detection
A later 18-trial manual session (`Session_4/`, `Manual_9_14`) refused 6 of 18. FFT on the raw gyro shows the refused trials are genuine, clean oscillations at **2.7–3.1 Hz** (17–19 swings each); every trial the app *scored* was **0.9–1.2 Hz**. The refusal is **frequency-dependent** — the app scores slow swings and refuses fast ones. The peak detector enforces a minimum inter-peak gap sized for a "~3.5 Hz max" pendulum; at ~3 Hz the real peaks fall inside that gap, too few are found, and it returns `f=0, N=0` on a perfectly good swing. A recovery re-score with a correct release and no artificial gap recovered all 6 (f≈2.9, N≈18), matching the FFT. (Confirmed earlier in `Session_2`, where ~3.7 Hz trials were also refused.)

**Design decision from the project owner — measure everything, exclude nothing, flag what's unusual:**
The app must not assume any frequency band. Remove the frequency gate as a *refusal/exclusion* mechanism entirely — always measure and report `f`, `N`, and the dependent metrics from whatever peaks are actually present, at any frequency. Do **not** substitute `0` or `unmeasured` because a value falls outside an expected range. Instead, when a measured value is outside a physiologically plausible range (e.g. `f` above some soft bound), **set a flag** (a boolean/enum alongside the value, in the same spirit as the existing `quality_warn`) and still return the real number. The flag is advisory metadata for the clinician; it never zeroes, hides, or drops the trial. The peak-detector's minimum-inter-peak-gap must be loosened enough that real oscillations at ~3 Hz (and faster) are counted; the only remaining guard against counting noise as peaks is amplitude/prominence, not a frequency cap.

## Where the code is (fix BOTH; keep them identical in behavior)
1. **Production engine (Rust → wasm, this is what the shipping PWA runs):**
   - `mobile-imu-core/src/scoring.rs` — `detect_release(t, ang, baseline_sec)` at ~line 313; `compute_pt_params(...)` takes `release_idx: Option<usize>` at ~line 498 and applies it at ~line 522.
   - Rebuilding the wasm and redeploying is part of "done" — a Rust-only change does not reach the app. (`webapp/scripts/build-wasm.mjs`; the app is deployed by CLI folder upload of `webapp/dist/`, not a git build.)
2. **Python reference (analysis + tests):**
   - `pendulastic_pt_score.py` — `_detect_release(t, ang, ...)` at line 2305; `compute_pt_params(..., release_idx=None, detrend=True)` at line 2695.
3. **JS shell (already plumbed — likely no change needed):** `webapp/src/app.js`, `webapp/src/session-store.js` already carry `release_idx` and a `release_override_idx` hook through the pipeline.

## What to build
A release detector that works for **hold → release → decaying swing**, replacing the displacement-threshold approach in both `detect_release` (Rust) and `_detect_release` (Python). Requirements:

1. **Find the true release** — the onset of the sustained, large, oscillatory motion after the pre-release hold. A departure must be **sustained** (≈100 ms), not a single sample, or a tremor/handling twitch counts as release. (A first-pass detector without the sustain requirement locked onto a 5° blip 9 s early — the sustain requirement fixed it to ~150 ms worst-case.)
2. **Be robust to a long, non-quiet hold** — 3–14 s of holding with hand tremor and gyro drift must not move the detected release.
3. **Be deterministic** — identical input must always give identical output (no dependence on array length, sample count, or floating-point accumulation order that varies across platforms).
4. **Keep the explicit-override path working** — `release_idx` / `release_override_idx` must still bypass detection when supplied (the bench supplies a known release; keep that contract).

## Success criteria — must meet ALL
- **Recovery:** all 10 refused `Session_3` trials AND all 6 refused `Session_4` trials produce a non-zero `f` and `N` (the recovery re-score got 10/10 and 6/6). No real oscillating swing is refused, including the ~3 Hz ones — validate the recovered `f` against an FFT of the raw gyro (Session_4's refused trials must land at 2.7–3.1 Hz, not the ~1 Hz a naive estimate suggests).
- **Flag, never exclude:** no metric is ever replaced by `0` or moved to `unmeasured` because it is out of range. A trial with an unusual value returns the real value plus an advisory flag. Confirm on `Session_4` that trials 3, 4, 7, 11, 12, 14 come back with their true ~3 Hz `f` and full `N`, each carrying an "out-of-range" flag rather than a zero. (`unmeasured` stays reserved for genuinely unmeasurable cases — e.g. no detectable swing at all — not for out-of-range values.)
- **High repeatability (the user's explicit ask):** on a set of near-identical swings (e.g. `Session_3` trials 1, 11, 15, and any repeatability block), the scored `f`, `N`, `A0`, `R2n` must agree — compute SEM and MDC95 per metric and show they shrink versus the current detector. Same swing → same score.
- **No corpus regression (hard constraint):** re-run the participant corpus test–retest and confirm it is **not worse** on any of the 7 Popović parameters. A prior naive "trim to release" change made corpus test–retest *worse on 7 of 8 parameters* (A0 MDC95 29.8 → 33.6 deg) — see `docs/reports/2026-09-11-preamble-sensitivity.md`. The bench and the clinical corpus can pull in opposite directions; validate against both, not just the bench.
- **Python and Rust agree** — score the same trials through both and confirm the metrics match to within tolerance.
- **Tests added** — unit tests for: a long-hold-then-release trial (release found correctly, not at index 0); the sustain requirement (a single-sample blip is rejected); an explicit `release_idx` overriding detection; a genuinely still/no-swing trial still refused. TDD: write the failing test first.

## How to work
- This is a behavior change to scoring; follow TDD and keep the change scoped to release detection — do not refactor unrelated scoring.
- `pendulastic_pt_score.py` and `mobile-imu-core/` are actively edited by other sessions. Check `git log`/status before starting and keep the diff tight.
- Data to test against: `Pendulastic_Mobile_App_Trial/Session_3/` (15 bench trials, 10 currently refused). Replay a raw JSONL through the production pipeline via `imu_calibration_tuner.replay_trial(samples, {"beta":0.10,"ema_alpha":0.30,"flex_axis_capture":True,"gravity_seed":True}, tick_s=imu_calibration_tuner.ANALYSIS_TICK_S)`, then score with `compute_pt_params`.
- Use the project `.venv`, not `freemocap-env`.
- Do NOT change scoring math (A0/R2n/N/area formulas), reference values (`HEALTHY_REF`), or the detrend convention — only how the release point is found.

## Also fix: export trial numbering is scrambled
The phone numbers exported trials in an order unrelated to capture order. In `Session_4` (Manual_9_14), sorting the manifest by `timestamp` gives capture order `11, 4, 7, 2, 13, 18, …` — the filename `trialN` number does not track when the trial was recorded. This silently misaligns every downstream join (a per-trial condition label attached by filename number lands on the wrong swing; it was caught only because the physics `f ∝ √P` disagreed until the mapping was redone by timestamp). Fix: **number exports in capture order**, so `trial1` is the first-recorded trial. The manifest already carries a per-trial `timestamp`; the export just needs to sort by it before assigning the index (and/or write an explicit `capture_index`). Add a test that exported indices are monotonic in `timestamp`.

## Out of scope
The phone↔encoder accuracy comparison, the LabVIEW rig, and the neuromechanical model. This task is release detection and its repeatability only.
