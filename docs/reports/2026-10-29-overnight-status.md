# Overnight status, 2026-10-29

**Branch:** `claude/mobile-app-validation-plan-u94fbs`, merged to `main`.

Everything below is on `main` and verified there. Two things need you: the
RMSE run in section 1, which needs the machine with the recordings, and the
decision in section 4 item 3, which I did not want to make on your behalf.

---

## 1. The one thing that needs you

**Run the OptiTrack RMSE comparison.** It is the accuracy gate on the AHRS
change that landed yesterday (`791b0ae`), and it is the only open item that
blocks deploying the webapp.

```
git checkout main && git pull origin main
.venv\Scripts\python.exe batch_imu_vs_optitrack_rmse.py --compare-floor
```

Add `--rec-root` / `--opti-root` if `Recordings` and `OptiTrack_Recordings`
are not beside the repo. It runs the corpus twice over the same trials, floor
off and floor on, and prints a paired per-trial comparison with an explicit
verdict. It writes only the gitignored
`Model_Analysis_Outputs/imu_vs_optitrack_rmse.csv` and refuses to write at all
if no trial evaluates.

**If it says accuracy is worse, the floor should be reverted** — the
reproducibility it buys is not worth real accuracy. Paste me the
`PAIRED COMPARISON` block and I will act on it either way.

### Why I could not run it myself

Three routes, all closed, and I stopped rather than keep trying:

1. `SendMessage` cannot see your sessions — Remote Control is not connected to
   this session.
2. Firing a task into one of your PC sessions is refused for this session. The
   approval card returned "the owner's device approved the request without
   recording which question it answered" on both attempts. It has to be
   answered in Claude **on the web or the desktop app**; the phone apps cannot
   answer it yet, and an approval tapped there produces exactly that result.
3. The computer-folder route appeared overnight, but `Pendulastic` is listed
   under `cannot_be_used`: that computer runs **Windows**, which cloud sessions
   cannot attach to. `folders` is empty, so there is nothing to request.

If you approve route 2 from web or desktop, say so and I will fire the run into
your idle "Neuromech" session. Note that approving it limits this session to
your trusted devices permanently.

---

## 2. What landed overnight

Since the corpus was unreachable, I measured what the floor costs at the
**filter level**, where no recordings are needed
(`tests/test_ahrs_residual_floor.py`). Accel held at a known gravity direction,
gyro at zero, quaternion started deliberately wrong:

| | floor off | floor on |
|---|---|---|
| updates to reach 1 deg (from 30 / 10 / 2 deg) | 646 / 192 / 21 | **646 / 192 / 21** |
| steady-state error, mean of last 500 | 0.023 deg | **0.000** |
| chatter, peak-to-peak | 7e-3 to 4e-2 deg | **0** |
| steady state under accel noise 5e-4 to 3e-2 | — | equal or better at every level |

So: no convergence penalty, the chatter is gone, and it does not under-correct
against noise. The teeth were verified by reverting the filter to
`step / s_norm` — the chatter tests fail and the replay-reproducibility tests
revert to xfail.

Also added `tests/test_rmse_compare_floor.py`, which exercises
`--compare-floor`'s plumbing without a corpus, so your run cannot waste itself
on a crash in the reporting after evaluating everything twice. It checks that
both passes genuinely run at different floors, that the filter global is
restored even when a pass raises, that trials pair by identity rather than list
position, and that each verdict branch reads correctly.

### A sensor freeze mid-swing scored a clean pass

Separate from the floor work, and the more consequential of the two.

`score_waveform`'s continuity check exists to catch staircase sensor
artifacts, and it was blind to them. Both edges of its active-swing window
are derived from the same signal it is checking, so a dropout had two ways to
move the window off itself:

| | what the freeze did |
|---|---|
| left edge | `_lift_hold_end` read it as a lift-and-hold — its hold window is exactly 0.5 s — and reported release at the freeze's **end**, putting the artifact before the window began |
| right edge | a freeze of 2 s or more also destroyed extrema detection, leaving one lone extremum 0.15 s after release, which collapsed the window to **four samples**, all of them before the freeze |

Measured on the 20 Hz synthetic, a freeze starting two samples after release:

| freeze | before | after |
|---|---|---|
| 0.30 s | pass, penalty 0 | **reject, 1** |
| 0.50 s | pass, penalty 0 | **reject, 5** |
| 1.00 s | pass, penalty 0 | **reject, 15** |
| 2.00 s | pass, penalty 0 | **reject, 35** |
| 3.00 s | pass, penalty 0 | **reject, 55** |

The clean trial still passes and its release still lands at 1.00 s. Penalty
now rises monotonically with freeze length, so a longer dropout cannot score
better than a shorter one.

Two guards did it, in both languages:

- **A lift's hold must sit at least 24 % of the signal range from rest**
  (`_LIFT_MIN_HOLD_DEV`). This one is geometric rather than tuned: if a still
  window really were the release point, the swing could span at most about
  twice its deviation from rest, so a genuine hold is 50–100 % of the range.
  A still window at 24 % or less is therefore not a release point. The 4.9 deg
  step into the synthetic's freeze is 8 % of its range.
- **One extremum is not an oscillation**, for the continuity window's
  purposes. Deliberately *not* changed inside
  `_active_oscillation_window_end`, which also bounds extremum counting for
  `compute_pt_params`: there a lone extremum is an accepted bounded error
  (N = 0.5 on a tail bump) and changing it would move N, A1, R2n and f on
  every stored trial.

**What you should check, because I could not.** The first guard is in
`_detect_release`, which is on the clinical scoring path, so it can in
principle move release detection on real trials. The geometric argument says
it cannot misfire on a genuine lift, and the lift-and-hold tests in
`test_pt_score.py` (including the jitter and held-from-frame-zero cases) all
pass — but the trials it was written for are the 15 Control_6/7/8 baseline
trials from 2026-10-27, and those are on your machine. Re-running the cohort
recalibration is what would confirm it: if those 15 trials still score, the
guard is clear.

Verification: 16 Rust suites pass, clippy clean under `-D warnings`.
Regenerating `golden.rs` from Python moved **nothing behavioural** — only the
numpy/scipy version string and last-bit float noise from this container's
newer numpy (2.5.3 / 1.18.1 against the recorded 2.4.6 / 1.17.1) — so the
fixtures were left untouched and both ports still agree on all six pinned
trials. `ALGORITHM_VERSION` restamped, wasm hash `a475ddb32342` →
`adbe5db62afa`. Teeth checked by reverting each guard in turn: the
deviation floor alone accounts for the 3.00 s case, the extremum rule for
2.00 s and 3.00 s.

`tests/test_imu_calibration_tuner.py` is now in CI's gated file list.

### Found alongside it: the two ports' continuity windows are different code

Not fixed, and worth your attention more than the freeze was.

Python's `score_waveform` bounds its continuity window with an inline
two-branch rule: last extremum, capped at 4 s after release, with a
settle-based fallback. Rust's `score_waveform` bounds it with
`active_oscillation_window_end` — a different rule, which stops where extrema
stop arriving on schedule and has **no time cap at all**. The Python helper's
own docstring says it was modelled on `score_waveform`'s inline logic; the
helper then evolved, Rust adopted the helper, and Python's `score_waveform`
kept the original.

So the desktop tuner and the shipped app apply different quality gates to the
same waveform. They agree on the six golden trials, which is exactly why
nothing noticed: the parity tests pin those six and nothing else. Unifying
them means deciding which rule is right — the 4 s cap is the live question,
since a 12 s trial with real motion at 5 s is outside the Python window
entirely — and that is a decision I did not want to make for you overnight.

### Two things I tried and threw away

Neither is in the repo, and neither should be cited:

- **RMSE against the E2E fixture's analytic truth.** Came out 22.4 deg under
  *both* settings on a trace spanning 0–45 deg. That fixture is a port-parity
  construction, not a physically faithful trace, so my truth mapping does not
  apply to it. Deltas of 0.009 deg on a 22 deg baseline are noise and would
  have been a fabricated validation.
- **A slow-rotation tracking test.** Its error tracked the *total rotation
  angle* almost exactly (20 deg of rotation produced 19.9 deg of "error",
  identically under both settings), so the filter was not following my
  synthetic gravity at all. My truth model, not the filter. Removed rather than
  narrowed to the range where it looked sane.

**Still untested as a result:** tracking across real motion, which is the
regime a pendulum swing lives in and the one the accel correction gates itself
off for. That is exactly what the corpus run covers.

---

## 3. Verified state

- **1347** Python tests passing (3.13, the interpreter here that has cv2 and
  mediapipe), **426** webapp, **16** Rust suites, clippy clean under
  `-D warnings`.
- CI green on `main` at `611dc42` (run 267); the tuner work above landed after
  it.
- **12 pre-existing Python failures**, all in
  `test_capture_coverage_hpe_session` (a worker consuming 0 frames), confirmed
  identical to clean `origin/main` by diffing both full-suite failure lists
  from a worktree: 12 against 13, the one difference being the tuner test the
  fix above repaired, and nothing new on my side. I introduced none and fixed
  one.
- Earlier in the session the same comparison showed **16**, on an interpreter
  without cv2. The extra three were `test_capture_quality_guard`'s
  `ModuleNotFoundError: cv2`, which is the environment, not the code, and the
  fourth was:
  `test_imu_calibration_tuner.py::test_score_waveform_plateau_during_active_swing_fails`.
  **That one is now fixed** — see section 2 — and the file is gated in CI, so
  it cannot go quiet again. It had been failing on main while CI stayed green
  for exactly one reason: the file was not in CI's explicit list, and
  `pytest tests/` is not what CI runs.
- One more thing the comparison turned up: `test_camera_utils.py::`
  `test_stale_frame_from_abandoned_thread_does_not_leak_into_newer_session`
  is **flaky**, failing about 2 runs in 5 on current `main` *and* on a clean
  worktree of it. Not in CI's list, so it gates nothing, but while it flaps a
  real regression in camera frame handling is indistinguishable from noise.

---

## 4. Still open, in the order I would take them

1. **The RMSE run** above. Blocks the webapp deploy.
2. **The cohort recalibration**, on current `main`. One run now reports the
   per-arm attrition breakdown, the `omega_max_n` decomposition into
   `omega_peak_deg_s` / `A0_deg`, and the `f`-boundary census that answers the
   first of the two questions in `docs/reports/2026-10-27-dropping-f.md`. It
   also confirms the release-detection guard from section 2 against the 15
   Control_6/7/8 lift-and-hold trials, which is the one check for it I could
   not run.
3. **Unify the two `score_waveform` continuity windows** (section 2). The
   desktop tuner and the shipped app currently gate the same waveform by
   different rules. Needs a decision on the 4 s cap before it can be fixed,
   which is why I left it.
4. **Zone classification is still off** and the thresholds are still
   `0.1709 / 0.3528` from `pt7-2026-08-24`, against a current formula id of
   `pt6rescaled-2026-10-27`. The app reports numbers, not grades. Turning that
   on depends on (2).
5. **`omega_max_n`'s reference** (`9.5672`, 1.85x above physics) — decided by
   (2)'s decomposition.
6. **The validation on real recordings.** The 2026-10-25 AUCs are synthetic and
   should not be quoted.
7. **Rig:** whose geometry the emulator represents (Control 71° vs CHAT/MINT
   65–66°, pending B10), opening the angle range to ±65°, identifying
   `coulomb_nm` from a passive drop.
8. **The Drive copy of the `f` report** still carries two errors I corrected in
   the repo copy. I retried tonight: Drive can *read* the file
   ("Frequency as a scored parameter in the pendulum test score"), but both
   `read_doc` and a targeted append through the Docs API come back
   `Permission denied` for that document, so I changed nothing. Enable the
   Docs connector for this chat and I will fix it in place — or paste these
   two paragraphs at the end yourself, which is all it needs:

   > Corrections, 27 October 2026. Two figures above were wrong when this memo
   > was first circulated. Both were found by committing the computation behind
   > them, rather than by re-reading the argument.
   >
   > The table under "Removing it does not blunt the instrument" reported the
   > scored set as non-monotone across the tone ladder with frequency included.
   > It is monotone. Every rung of the ladder has an oscillation count of at
   > least two, and the most severe rung sits at exactly 2.00, so no rung
   > crosses the boundary where the frequency term is dropped. The ladder is
   > ordered either way. The monotonicity argument therefore rests entirely on
   > the sweep under "Frequency carries the only discontinuity in the score",
   > which was built to cross that boundary, and not on the ladder at all. What
   > the ladder shows is the weaker claim, and that one still holds: removing
   > frequency costs no discrimination and gains a little.
   >
   > The share of the composite attributed to frequency, between 2.4 and 9.6
   > per cent, holds only across the profiles where the frequency was actually
   > estimated. Across all nine graded profiles the range starts at 0.0 per
   > cent, because one profile has its frequency skipped and so contributes
   > nothing at all.
9. **The flaky camera test** from section 3, whenever it is convenient. It
   gates nothing today, which is also why it has been able to stay broken.
