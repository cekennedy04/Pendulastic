# Weekly Progress Report — Pendulastic

**To:** Dr. Perez
**From:** Claire Kennedy
**Date:** Monday, 2026-08-31 (covering 2026-08-24 through 2026-08-30)

---

## 1. Greeting & Overview

Hi Dr. Perez — here's last week's summary. The week had two very different
halves. Monday through Thursday (08-24 to 08-27) was the busiest stretch in
recent memory: we closed out the live-phone IMU fixes from the prior week's
PR, then built almost an entire self-contained phone-capture web app —
capture UI, IndexedDB persistence, offline service worker, session export,
and a first real Vercel deployment — on top of the `mobile-imu-core` Rust
engine started the week before. Friday through Sunday (08-28 to 08-30) had
zero commits in either repository; the nightly hygiene agent also missed
every scheduled run this week (11 straight stalled nights as of Sunday).

The headline number to flag before anything else: **70 commits landed this
week, but only 12 of them are on `main`.** The other 58 — the entire web
app build — are sitting on `webapp-static-deploy`, unreviewed, alongside 5
older stranded commits on `fix/app-teardown-imu-port-release`. I did not
open PRs for either branch mid-week as I should have; that's on me and it's
the first thing I'm fixing this week (see Section 6).

## 2. Key Work & Development Done

**Live-phone IMU fixes, merged to `main` (08-24–08-25, PR #32):**
- Three angle-integration defects found from real recorded phone trials:
  `on_gyro()` was silently substituting a 10 ms fallback `dt` for a sensor
  streaming gyro at 1.0 Hz (measured: a ~90° physical rotation was reported
  as 4.91° instead of 175.80°); a bias-calibration routine was applying
  gyro-bias estimates that were mostly their own sensor noise (now gated to
  axes ≥3 standard errors from zero); and a stillness gate compared raw
  accelerometer readings against a threshold calibrated in g-units against a
  stream that can arrive in either g or m/s², so two phones in the same
  clinic could disagree about what "still" means.
- Added a per-trial IMU quality sidecar (`<trial>_imu_quality.json`) that
  flags a trial whose gyro sample rate degrades mid-recording — a 1 Hz
  capture scores 63.76° RMSE against OptiTrack vs. 13.35° at full rate, so
  this isn't cosmetic. Recording now also refuses to *start* below the
  minimum usable rate instead of only warning.
- App-teardown hardening: exception-safe teardown, IMU server port release,
  Tk timer cancellation via Tcl, and a fix so `start()` no longer reports a
  healthy IMU server that's actually refusing connections.
- Added a fully-pinned `requirements-lock.txt` for reproducible environment
  setup.

**PT7 zone recalibration (08-24, `278b6f1` → `ea24843`):** see Roadblocks —
this one's worth reading in detail, it changed the diagnostic axis we
calibrate against.

**`mobile-imu-core` Rust engine + web capture app (08-24–08-27, all on
`webapp-static-deploy`):**
- Ported the U2 angle math and Popovic PT scoring to Rust with an
  end-to-end parity check against the Python reference.
- Built a live capture session in Rust (cumulative hold-drift gate, release
  detection with an override-wins-over-auto-detect rule) and a
  wasm-bindgen veneer over it, so the same scoring code that runs on the
  desktop app now runs in-browser via WebAssembly.
- Built the browser side on top of it: a capture worker with a replay seam
  for automated testing, main-thread `devicemotion` capture, a capture UI
  with separate motion/drift-gate readouts, an install-to-Home-Screen gate
  (recording is blocked until the app is installed, since iOS only grants
  motion-sensor permission in that context), a build-keyed offline service
  worker, and an IndexedDB schema for patients/sessions/trials.
- Added a persistence layer that closed two ways an export could be lost
  (a session-close race and a compare-and-swap gap before marking a session
  exported), then locked the session at the decision point rather than
  inside the write path.
- Added a static-deploy build pipeline and got a first real Vercel
  deployment working, which surfaced two real hosting defects (below).
- Stood up the repo's first CI pipeline — Rust tests, the export contract,
  webapp tests, and a wasm-drift check — and deleted the manual wasm-drift
  check that predated it (the binary itself is no longer committed, for
  reasons documented in `webapp/README.md`: it isn't reproducible
  byte-for-byte across build machines).
- 110 webapp tests pass as of the last commit on the branch (`1abf50c`,
  Thursday 08-27, 15:17).

**Performance work on the stranded `fix/app-teardown-imu-port-release`
branch (08-25–08-26):**
- Deferred `mediapipe` import from app startup: measured 59.78 s eager vs.
  17.95 s deferred module import (roughly a 21 s vs. 2 s window-open delay
  on a quiet machine). Deferred `scikit-learn`/`scipy.stats` similarly
  (5.86 s → 4.42 s). Removed a dead scoring safeguard
  (`_merge_close_extrema()`) after verifying it never fires against all 28
  archived knee-angle traces — `find_peaks`'s own distance parameter already
  guarantees wider separation than the threshold it was checking.
- One open thread from this work: an unexplained ~13 s gap between the app
  window closing and the process actually exiting (mediapipe teardown only
  accounts for 1.8 s of it).

## 3. Visuals, Figures & UI State

Straight answer, same as last week's report: **no new figure images were
generated this week.** The four `.png` files in the repo root
(`lateral_impact_presentation*.png`, `oneoff_lateral_check*.png`) predate
this window and aren't new work, so I'm not linking them here as if they
were this week's output.

On the UI dashboard: the new phone-capture web app (`webapp/index.html`,
`webapp/src/app.js`) exists and has a real screen flow — an install gate
("Install before recording"), a Start/Stop capture screen, and a results
screen with Export raw log / Send to laptop / Export session / Close
session controls — but I can't hand you a screenshot of it honestly. It
needs the Rust core cross-compiled to WebAssembly (`npm run build:wasm`)
before it will render past the install gate, and that requires a
`wasm32-unknown-unknown` Rust target and a version-matched `wasm-bindgen`
CLI that aren't set up in this reporting environment. I'd rather tell you
that than paste a broken or fabricated image. Getting a real screenshot
(and ideally the live Vercel URL) into next week's report is now on the
action list.

## 4. Roadblocks, Errors, & Solutions

- **PT7 zone thresholds recalibrated twice, second pass changed the
  diagnostic axis (08-24, resolved with an open clinical question).**
  First pass (`278b6f1`) recomputed `PT_HEALTHY_MAX`/`PT_BORDERLINE_MAX`
  against the documented n=4-control/n=3-MS cohort — the numbers barely
  moved (<1%) despite real upstream fixes, because both distributions
  shifted together. Validating that pass surfaced a real problem: on every
  metadata-classifiable participant (n=8 control, n=8 MS) the separation
  collapsed, and the headline significance (p=0.0000078) evaporated to
  p=0.11 once trials were aggregated per participant instead of treated as
  independent. Second pass (`ea24843`) recalibrated on the MAS-grade axis
  instead of MS-vs-Control — PT7 measures spasticity *severity*, and this
  cohort's MS arm spans the full severity range including MAS-0
  participants who should score like controls. Regrouped that way,
  MAS-0 (n=23 legs) vs. MAS≥1 (n=6 legs) gives Mann-Whitney p=0.00196 and
  is stable across several sensitivity checks. It also caught and fixed a
  transposed left/right MAS entry for one participant. **Still flagged as
  provisional**, not closed: the MAS≥1 arm is only 6 legs from 4
  participants, which is a data-collection gap, not an arithmetic one —
  see Question 3 below.

- **Two hosting defects found on the first real Vercel deploy (08-27,
  resolved).** Deploy attempt #1 failed outright ("No python entrypoint
  found") — Vercel's framework auto-detection read the surrounding repo
  (overwhelmingly `.py` files) and a stale dashboard preset persisted past
  the upload; fixed by an explicit `vercel.json` with `framework: null`
  and no build/install commands. A second, more latent defect: the dist
  build only emitted a `_headers` file, a Netlify/Cloudflare convention
  Vercel ignores — `sw.js` and the build-id file (which carry the cache
  key that tells an installed phone a new build exists) would have been
  served with default caching indefinitely. Fixed by emitting an
  equivalent `vercel.json` and adding tests that assert the two host
  configs agree on every shared path. **Not yet verified end-to-end**: the
  fix landed but no successful live deploy has been confirmed since.

- **Trial-report layout defects on P24's full report (08-27, resolved).**
  The source-agreement table clipped its headers because column widths
  were computed from character count rather than rendered width (fixed
  with iterative shrink/redraw/measure — font metrics turned out not to be
  linear in point size; two different point sizes rasterized to the exact
  same pixel width). A zone label ("Impaired") floated above the visible
  axes when its band started above P24's data range; fixed by having
  `visible_zone_bands()` decide the visible range once for both the shaded
  spans and their labels.

- **Open, not a bug: the review found real design debt in the web app
  that isn't fixed yet.** A whole-branch review on 08-25 (recorded in
  `TODOS.md` since it lived only in a gitignored scratch directory) found
  that hold-drift is currently coaching-only — a trial released after
  large accumulated drift scores identically to a clean one, because the
  scorer only sees the rate gate. It also found the capture pipeline
  silently substitutes/drops out-of-spec samples instead of surfacing a
  count, and that gate thresholds (0.95 s, 5.00°) are duplicated as UI
  literals in three places instead of crossing the wasm boundary once.
  None of these are wrong today, but none are instrumented either.

## 5. Questions & Request for Feedback

1. **Process fix on my end, flagging so you know it's being addressed:**
   58 commits of the web capture app and 5 perf-fix commits are sitting
   unreviewed off `main`. I'm opening PRs for both this week rather than
   letting the branches grow further — is there anything about the web
   app specifically you want a heads-up on before that review lands, given
   it's a bigger surface area than a typical PR?
2. On the PT7 recalibration: are you comfortable treating MAS grade
   (rather than MS-vs-Control diagnosis) as the calibration axis going
   forward? The severity argument seems right to me, but it also means the
   "clean separation" we reported before this fix was partly an artifact
   of which participants happened to be in the n=7 cohort.
3. The MAS≥1 arm supporting that recalibration is 6 legs from 4
   participants. Is recruiting/scheduling more clinician-assessed MAS≥1
   participants something we should prioritize now, or continue on the
   current cohort while the web app work stabilizes?
4. The hold-drift-is-coaching-only gap means a trial can currently look
   clean and score normally despite drift the app already detected and
   displayed live. Do you want that blocking (refuse to score / flag the
   result) before the app sees any real participant data, or is
   coaching-only acceptable for an initial shadow study?
5. The nightly hygiene agent has now missed 11 consecutive scheduled runs
   (last report 2026-08-18). Tonight (Monday) is the next real test
   window since it's weeknights-only — should I dig into the schedule
   config directly if it doesn't fire, or is someone else already looking
   at it?

## 6. Next Week's Action Plan

- **Open PRs for `webapp-static-deploy` (58 commits) and
  `fix/app-teardown-imu-port-release` (5 commits) immediately** — both are
  currently invisible to review, and the web app branch in particular is
  large enough that further silent growth makes review harder, not easier.
- Get a real screenshot (and the live Vercel URL) into next week's report:
  install the `wasm32-unknown-unknown` Rust target and a matched
  `wasm-bindgen` CLI, build the app, and verify the deploy actually went
  live after last week's `vercel.json` fix.
- Instrument the two open web-app gaps from Section 4: surface
  clamped-dt/dropped-sample counters from the capture worker, and decide
  whether hold-drift should block scoring rather than stay coaching-only
  (pending your answer to Question 4).
- Chase the unexplained ~13 s app-exit delay flagged in last week's perf
  work.
- Pending your answer to Question 3: either begin MAS≥1 participant
  recruitment, or continue hardening the current pipeline on the existing
  cohort.
- Check the hygiene-agent schedule config Monday night if it doesn't
  recover on its own (11th missed night as of this report).

---

*Prepared from repository commit history (2026-08-24 through 2026-08-30)
across `main`, `webapp-static-deploy`, and `fix/app-teardown-imu-port-release`,
plus the daily progress reports and `TODOS.md`. No figures or screenshots
were fabricated for this report — Section 3 reflects an honest gap rather
than a placeholder image.*
