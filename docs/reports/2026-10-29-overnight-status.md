# Overnight status, 2026-10-29

**Branch:** `claude/mobile-app-validation-plan-u94fbs`, merged to `main` at `611dc42`.

Everything below is on `main`. Nothing is waiting on a decision from me.

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

- **1133** Python tests passing, **426** webapp, **16** Rust suites, clippy clean.
- CI green on `main` at `611dc42` (run 267).
- **16 pre-existing Python failures**, confirmed identical to clean
  `origin/main` by diffing both failure lists from a worktree. I introduced
  none. They are 12 in `test_capture_coverage_hpe_session` (a worker consuming
  0 frames), 3 in `test_capture_quality_guard`
  (`ModuleNotFoundError: cv2` — environment), and **1 real logic failure**:
  `test_imu_calibration_tuner.py::test_score_waveform_plateau_during_active_swing_fails`.
  That last one is worth your attention because **CI does not run that file**,
  so it stays green while the test is broken.

---

## 4. Still open, in the order I would take them

1. **The RMSE run** above. Blocks the webapp deploy.
2. **The cohort recalibration**, on current `main`. One run now reports the
   per-arm attrition breakdown, the `omega_max_n` decomposition into
   `omega_peak_deg_s` / `A0_deg`, and the `f`-boundary census that answers the
   first of the two questions in `docs/reports/2026-10-27-dropping-f.md`.
3. **Zone classification is still off** and the thresholds are still
   `0.1709 / 0.3528` from `pt7-2026-08-24`, against a current formula id of
   `pt6rescaled-2026-10-27`. The app reports numbers, not grades. Turning that
   on depends on (2).
4. **`omega_max_n`'s reference** (`9.5672`, 1.85x above physics) — decided by
   (2)'s decomposition.
5. **The validation on real recordings.** The 2026-10-25 AUCs are synthetic and
   should not be quoted.
6. **Rig:** whose geometry the emulator represents (Control 71° vs CHAT/MINT
   65–66°, pending B10), opening the angle range to ±65°, identifying
   `coulomb_nm` from a passive drop.
7. **The Drive copy of the `f` report** still carries two errors I corrected in
   the repo copy — the monotone column and the share range. The Docs connector
   has no access to that file, so it needs enabling for this chat before I can
   fix it in place.
