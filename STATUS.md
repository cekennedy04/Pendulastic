# Pendulastic status

**This is the single source of truth for what is being worked on.** Read it before
starting; update it in the same PR that changes a workstream's state. Dated notes in
`docs/reports/` are history, not status. Last full review: 2026-11-11; merges and owner decisions recorded 2026-11-16; merges, 0.4.0 live and hosting decision recorded 2026-11-17; #112, #115, #116, #117 merged and build 522923b0ba75 deployed 2026-11-17.

## Priorities (owner, 2026-11-10)

1. **Neuromech emulator / rig** is the top priority.
2. The PT scorer must be settled before any rig result quotes a PT score.
3. Legacy video / desktop code is to be archived, not developed.
4. Webapp deployment and paper/thesis results follow, in that order.

## Quality bar (owner, 2026-11-11)

This is clinical-measurement software. A change to the scorer, the AHRS filter or rig
scoring lands alone, with: corpus before/after and every changed trial explained; the
Rust core run on real trials, not only read; parity checks across Python, Rust and C#;
an independent review by someone who did not write it; and a version bump when reported
scores move. "Not verified" is stated, never implied.

## Decisions waiting on the owner

1. **Contacting GitHub to purge `refs/pull/90`**, which still holds commits with clinical
   fields. The owner will decide later; both branches that carried them are deleted.
2. **How to get a graded lambda 1.4 swing on the rig** (2026-11-18). At the 0.70 scale the guard
   needs, the swing is under the scorer's 30° excursion floor, and the app misses the release at
   either scale. Options (re-zero the rig command so the 1:1 swing is centred inside ±40°, a
   shallower limb geometry, or tracking probe only) and the numbers are in
   `docs/reports/2026-11-18-neuromech-lambda-1p4.md`. Nothing was changed in the guard, the scorer
   or the app.

## Decisions taken (owner, 2026-11-16)

- **AHRS seed fix (#98):** merged with a merge commit (algorithm version 0.2.0). Every
  phone-IMU number measured before it is stale. The live app was redeployed from main
  (8aa1055) the same day; #102 stops Vercel's git integration overwriting it.
- **Drift bound and `HEALTHY_REF`:** finish #94 and re-derive `HEALTHY_REF` with the bound,
  as one versioned change. Merged 2026-11-17 as #105 (`pt6rescaled-ref-2026-11-16`,
  supersedes #94).
- **No refusals from the drift or A0 bounds** (owner: refusals must not affect severe
  legs; the app scores every kind of swing; take the most robust option). In #105, all
  ports that have the logic (Python and Rust; C# has neither bound): A0 is bounded by
  raw span + |applied slope| × duration + 2% of span, replacing span × 1.27. A drift
  estimate out of bounds is skipped and the curve scored without it; an A0 past the
  bound drops the correction and scores the uncorrected curve with a flag; a correction
  over 25% of the raw span is applied and flagged. `AmplitudeExceedsCurveError` is
  removed. Every score records which correction it used and its flags.
- **Scoring footing:** move the app to the Python reference footing, together with
  counting oscillations from the first trough and dropping `omega_max_n` (#76 / #83), under
  one score-formula version bump. Merged 2026-11-17 as #110 after independent review
  (core 0.4.0, `pt5trough-ref-2026-11-16`); the live app was redeployed at 0.4.0 (aeb3f76)
  the same day. Step 2 change approved: the
  first-trough drop runs after the active-oscillation window bound.
- **Release detector:** deferred; no problems seen in use. Revisit only if a problem
  turns up that is too big to ignore.
- **Clinical-field branches:** `neuromech/fall-speed-b13` and `neuromech/fall-speed-check`
  deleted.
- **Cleanup:** the 43 daily-report branches deleted; archive pass, September doc rescue and
  `archive/<branch>` tags approved (in progress); branch and worktree deletions are shown to
  the owner before they happen.
- **Nightly hygiene agent:** retired.
- **Footing plan Q1–Q6:** the recommended options (tracked reference-footing constant;
  trends break at the footing change, 0.4.0 since #105 took 0.3.0; keep a lone early peak; `lateral_motion` later; detrend
  unification after #105; this change may move `HEALTHY_REF["f"]` by about 0.001).
- **Hosting and domain (2026-11-17):** `pendulastic.com` was not bought and will not be; the
  owner is leaving Vercel. The app stays at `pendulastic-app.vercel.app` through the
  2026-11-18 meeting; choosing the new host is a follow-up.
- **Scorable-length gate (footing plan Q7):** the 40-sample minimum becomes 2.0 s of finite
  signal in all three ports, after checking it drops no `HEALTHY_REF` control curve.
- **Rig O1–O5** (literature review, second opinion and a contrarian review; memo in the
  session notes, to be added to `docs/plans/`):
  - *Before any session:* build the Player's own `Max |setpoint|` clamp (the Angle Limit
    fields do not bound the Player). **Built 2026-11-17 (simulation/code-tested, not yet run
    on the rig):** both players share `labview/player/RigGuard.cs` — ±40° hard limit (operator
    can only lower), load-time refusal incl. 600 °/s and a predicted-delivered-peak check on the
    identified plant, per-write backstop that stops the run, no start without a setpoint
    reading; one writer at a time (named mutex); an optional same-process encoder watchdog (the
    actual-position variable is unconfirmed, so it reports OFF until one is picked); a staged
    first-run checklist with a hard-stop margin step (`designer/RIG_CHECKLIST.txt`);
    Designer default scale 0.70 and a scale-down offer for bigger live designs. At ±40°
    3 of 66 library scenarios are playable at 1:1 (the rest release from ≥ 55°). After the PR #117
    review: speed and predicted peak checked on every 1 ms phase (not just the 10 ms grid), Ctrl+C and
    error paths return to 0, PLAY re-reads the setpoint after Enter, no `-PeriodMs`/`-NoReturn`,
    return speed 1–60 °/s, no `Local\` lock fallback, 2-column rows only. Re-review: the backstop
    and every ramp step from the last value the rig actually took, any failed write ends the play,
    a RIG HOLDING end leaves `sent/HOLDING.flag` that the next start must acknowledge, watchdog stops
    after 5 missed reads. Follow-up: asynchronous encoder reads. See
    `neuromech/README.md` "Playback safety guard". The dry run is 9/9 in band on main (the 7/9 reading
    came from code without #96; see O5).
  - *O1 arm range:* widen to the scenarios' range only after the clamp exists, with
    asymmetric limits and a margin to the hard stops at least the fault overshoot seen
    (+63°/−81° at ±25° limits). 0.75× renders only as a labelled invariance probe.
  - *O3 orientation:* horizontal Mode A now, for the gyro channel and the scorer against
    the encoder only. The app's angle counts rotation across gravity, so a horizontal swing
    reads near zero in the app and a 30–45° tilt reads 0.50–0.71×. Next: the existing light
    arm in a fully vertical plane (≥75°), Mode A, phone at the shank position, with on-edge
    and tilted mounts. The owner confirmed the current motor holds the arm against gravity.
  - *O2 actuator:* no purchase. A mass-matched shank or Mode C would be sized per scenario
    from inverse dynamics (inertia + gravity + active torque), with a backdrivability spec.
  - *O5 clasp-knife:* identical replays with a fixed rest between drops; adaptation in its
    own labelled series; add one multi-drop session file with realistic gaps. Revised by the
    owner after the dry-run bisect. Corrected by #105's review: 7/9 reproduces only on code
    without #96 (bcb6a71); `main` and #105 deliver 9/9 at seed 11, with the clasp-knife
    pair at PT7 0.127 and 0.128, about 0.007 above the 0.12 band edge, so the pair is in
    band but marginal. The decision stands: once the footing change lands, refit the
    clasp-knife presets to the middle of their band on the new scorer, drop `M-catch_release_high` (identical to
    `B-claspknife` since af8a20a), and add a build check that flags any preset within about
    0.03 PT7 of a band edge.
  - *O4 clinicians:* not used for grade calibration (MAS needs a hands-on rater; the clinical
    corpus serves). Optional later: intended-band agreement on the vertical rig.
  - *Added checks:* phone–encoder sync and axis alignment, iOS vs Android sample rates,
    gyro drift over a session, cable drag, and clonus (6 Hz) on the arm's 6.34 Hz resonance.

## Workstreams

### 1. Neuromech knee-spasticity emulator and rig — ACTIVE, top priority
- **Goal:** a motor rig that plays back knee swings of known severity so the phone app
  can be checked against known truth and clinicians can rate the same swing.
- **Canonical branch:** `main`. `feat/neuromech-emulator` and PR #69 are superseded by
  #70. The two late commits on `feat/neuromech-player` are already on main via #80.
- **Entry points:** `python -m neuromech.build`; `neuromech/session.py` (plan, score,
  check-log); `neuromech/dryrun.py`; Pendulum Player in `neuromech/labview/player/`;
  plan in `docs/plans/2026-10-26-001-plan-neuromech-emulator-unified.md`.
- **Merged 2026-11-11:** #87 (provenance, B14), #86 (plan review). #82 closed as
  contained in #90.
- **Merged 2026-11-16 (squash):** #96 (release alignment) and #90 (fall-speed check, B13;
  reference file holds only person, A0 and peak; see decision 6).
- **Current numbers (2026-11-17, core 0.4.0 / `pt5trough-ref-2026-11-16`, with #112):**
  `python -m neuromech.build` writes 66 scenarios (64 until 2026-11-18; `M-catch_release_high`
  dropped by #112; `C-wmax-low` and `R-reflex-force` dropped as duplicates, decision c, #115
  merged 2026-11-17). The A-P2 and A-P3 presets are unreachable on the current scorer. The
  simulated dry run delivers 9 of 9 runs in their intended band at seeds 11, 1, 3, 7
  and 42 (7 of 9 before #96).
- **O5 preset refit (#112, merged 2026-11-17, reviewed and re-reviewed):** presets refitted to their band middles on
  the five-term scorer (`B-claspknife` 0.078 band 0 -> 0.200 band 1; `K-HM1` 0.149 ->
  0.193; `B-spasm`, `B-flexor`, `B-clonus` off their edges), `M-catch_release_high`
  dropped, and the build now exits 1 when a scenario not on the allowlist
  (`scenarios.NEAR_EDGE_ALLOWED`, 10 probes with reasons; 8 after decision c) sits within 0.03 PT7 of a
  band edge. The `M-tone_L*` ladder is deliberately not moved (see the PR).
  Owner merged it as is (B-claspknife in band 1, release 25 -> 36 deg; the
  `catch_release_high` profile now uses 36 deg). **Review decisions a-c: decided (owner,
  2026-11-17, "go with whatever you recommend"), implemented in #115 (merged 2026-11-17, reviewed):**
  (a) the `M-tone_L*` ladder stays as is (accepted: L1 band 0, L2 band 1, L3/L4 band 1+);
  `python -m neuromech.build` now prints an intent-mismatch report (report only, exit status
  unchanged) with the known mismatches marked accepted (`scenarios.INTENT_MISMATCH_ACCEPTED`);
  (b) `R-friction` stays allowlisted and is recorded as a finding (README, Findings): bearing
  friction alone scores PT7 0.1144, 0.0056 below the MAS 1 edge, so a real rig's friction could
  push healthy-ish presets toward MAS 1; to be checked on the real rig; (c) `C-wmax-low`
  (= `C-f-low`) and `R-reflex-force` (= `S-lam1`) dropped, 66 -> 64 scenarios, and the build
  exits 1 on any scenario pair that renders the same swing unless the set is in
  `scenarios.DUPLICATE_ALLOWED` (three sets listed with reasons: `A-P1`/`B-extensor`/`B-habit1`,
  `B-leadpipe`/`M-fall_no_ringdown`, `B-contracture`/`M-contracture_stop`; dropping those is
  open for the owner). Smaller follow-ups: the edge check does not stop `session.plan` from
  playing a flagged library.
- **After the 2026-11-18 meeting (from the #115 review):** `B-mixed` renders the same swing as
  `A-P1p` (its flexor reflex never fires: return swing 0.57 rad/s < `flex_vth` 0.8): refit or
  drop it, and make the duplicate check also compare rendered setpoints/traces; owner to keep or
  drop the allowlisted look-alike sets; the `A-P1`/`B-extensor`/`B-habit1` allowlist entry will
  fail the build once band 2 becomes reachable after a recalibration (say so in its reason);
  stale detection for `INTENT_MISMATCH_ACCEPTED`; seed synthetic session logs by scenario, not
  slot; `test_model_rt_ports::test_importing_profiles_does_not_build_them` is a wall-time test
  that flakes under load (also on main).
- **Motor playback safety guard (#117, merged 2026-11-17):** three review rounds plus Codex
  and Gemini contrarian passes; details under Rig O1–O5 above. **First motor use 2026-11-18,
  staged per `neuromech/designer/RIG_CHECKLIST.txt`.** Not covered by software: closing the
  window, Ctrl+Break or a crash leaves the rig holding; no independent hardware cutoff.
- **USB bundle for the rig PC (#114, merged 2026-11-17, reviewed):** `python -m neuromech.rig_bundle` builds the Designer
  (.NET 2/4 dual config, no install), the guarded kit and `library/` (1:1, 3 of 66 + chirp) and
  `library_x0.70/` (labelled 0.70× render, 57 of 66), each file re-checked by the guard;
  `R-chirp` left out (owner, 2026-11-17: predicted 22° and ~970°/s at the arm).
- **Release alignment (#96, merged):** rig sessions are scored from the delivered release
  (the arm leaves the hold 18 to 30 ms after the command) and runs where it cannot be
  found are flagged. Reviewed twice. Simulation only. **Do not report scores from a real
  rig session yet:** the fixed 25 ms tracking limit suits only the simulated plant.
- **Severity axis past the PT7 peak (branch `claude/neuromech-lambda-1p4-b7244a`, PR open, not reviewed):**
  adds `S-lam1.2` and `S-lam1.4` (falling branch; PT7 0.224 and 0.170, band 1, played 1:1) and a lambda mode
  in the Designer (`designer/DeGroote.cs`, a C# port of `model_rt` held to Python by `test_designer.py`).
  Simulation only. Findings: lambda 1.4 passes the guard only at 0.70, where its 25.3° excursion is under
  the scorer's 30° floor (refused); and the app does not find the release at lambda 1.4 at either scale
  (early catch at 55°/s, under the 1.0 rad/s capture threshold). Getting a graded lambda 1.4 swing on the
  rig is an owner decision (re-zero the rig command, change the limb geometry, or use it as a tracking
  probe only): `docs/reports/2026-11-18-neuromech-lambda-1p4.md`.
- **Open PRs:** the lambda 1.4 PR above.
- **Chirp-derived tracking limit (#107, merged 2026-11-17):** each
  run is held to what the plant identified from the session's chirp (R000) predicts,
  with a release check against that plant, a session-level MODEL MISMATCH FINDING, a hold
  gain gated by the gain after the swing, and a flag for a hold the arm does not settle
  at. Reviewed and re-reviewed (`docs/chirp-tracking-limit-review`); 286 tests, dry run
  9 of 9. **Simulation only, not verified on a real rig.** The 250 deg/s chirp test already
  saw 4 early releases on main before #112 (A-P0 sits 0.002 ms past the 4 ms limit, so
  3 vs 4 is rounding); #112 made the pin accept either. Known costs (README and report
  section C): slow rig modes the chirp cannot see flag good runs; a constant encoder
  offset still moves release and tracking; no session ratio where predictions are under
  7 ms; a margin must never be accepted from the session being judged.
- **Another session's work, not part of this plan:** `docs/neuromech-elbow-plan`
  (worktree `Pendulastic-wt-elbow`).
- **Rig path versus app path** (simulation only;
  `docs/reports/2026-11-10-rig-phone-angle-vs-app.md` on `eval/rig-phone-angle-vs-app`):
  `session.py`'s gyro integration tracks truth within about 1 degree when the swing is
  on gyro-y, but silently accepts an off-axis mount; the app's score is 0.06 to 0.15
  PT7 higher than the 100 Hz score. Rig scores must not be described as what the app
  would report. The generator simulates a vertical swing; a horizontal rig with the
  phone flat is untested.
- **Next actions:** the first rig session, read as a
  measurement of the limits. On its first log measure `enc_hold_rms_deg`, the idle position against the
  setpoint (`enc_idle_offset_deg`) and `enc_hold_gain` / `enc_late_gain` on runs believed good.

### 2. Synthetic phone-trial generator — ACTIVE (part of neuromech)
- **Goal:** check the app end to end without a phone, against known truth.
- **Canonical branch:** `main` (#99 merged 2026-11-16: everything from #92 except the
  AHRS seed fix, which is #98; #92 closed). Replay tests need a local wasm build and do not
  run in CI.
- **Entry points:** `neuromech/phone_trial.py`, `webapp/scripts/replay-trial.mjs`,
  `neuromech/README.md` §8.
- **Known app defects it exposes** (strict-xfail tests in
  `neuromech/tests/test_phone_trial.py`): a brisk lift fires the release detector, and a
  steady 9 degree sag through the hold is never caught. On rig-format recordings the
  app's offline scorer places the release inside the lift on some scenarios and takes
  the first sync pulse as the release when given a whole recording.

### 3. PT scorer — ACTIVE, gates workstream 1
- **Goal:** one validated scorer, identical in every language.
- **Implementations:** Python `pendulastic_pt_score.py` (reference),
  Rust `mobile-imu-core/src/` (`scoring.rs`, `pt_score.rs`; ships in the webapp),
  C# `neuromech/designer/PtScore.cs` (given-release, `detrend=False` path only; it has
  no release detector and no drift correction). `run_statistical_engine.py` held a
  fourth `HEALTHY_REF` copy; with #105 it imports the scorer's, and
  `tests/test_pt_zone_parity.py` pins every copy to Python.
- **Conventions:** on screen the app scores with `detrend=False`; the record the app
  STORES uses `detrend=True` (`finish_detrended` in `wasm.rs`, `persistTrial` in
  `webapp/src/app.js`). `neuromech.build.score_angle` is 100 Hz, release given,
  `detrend=False`. `pt_report_common.score_trial` uses the `compute_pt_params` defaults
  (`detrend=True`, auto release). These are not interchangeable.
- **Lineage comparison** (`docs/scorer-lineage-comparison`): test-retest under
  `detrend=False` is a tie between `main` and the September lineage. `9a555de` is
  already on main. `61d5723` (signed swing angle) must stay out.
- **Drift-correction bound + re-derived `HEALTHY_REF` (#105, merged 2026-11-17; supersedes #94, which is still open and conflicting: close it):** the bound in Python and Rust, never a refusal (see decisions
  taken), with the correction used and any quality flags shown in the viewer, workbench
  and live view and stored on the app record (`scoring_quality`), and
  `HEALTHY_REF` re-derived with the bound (phi 0.6407→0.6264, omega_max_n
  6.4839→6.6702, area 0.1766→0.1678, f unchanged; 37 trials, 11 legs, 7 people) in
  Python, Rust, C# and `run_statistical_engine.py`. `SCORE_FORMULA_ID`
  `pt6rescaled-ref-2026-11-16`, core 0.3.0. Bound alone: `detrend=False` bit-identical;
  `detrend=True` moves 2 phone and 20 optical trials. Rust matches Python on 463 real
  curves (both conventions), including the correction used and the flags. neuromech:
  no band changes, dry run 9 of 9. Corpus: no trial is refused by either bound at
  either setting, the no-refusal rule moves no score, and 7 trials (6 phone, 1 optical,
  `detrend=True`) carry the large-correction flag. A stored app record that falls back
  to the live convention says why (`analysis_refusal`).
- **`compare_pair` orientation (#95, merged 2026-11-16):** reworked in its own PR after the first
  port was found wrong. Marker-path references are mirrored only on release-side
  evidence; correlation alone can only confirm "as given" under a verified lag;
  anything else is reported undetermined and scored as given. Corpus: 66 as given, 55
  mirrored, 24 undetermined; median 14.67 degrees (range 13.86 to 15.30) against 21.80
  on main. Independent review (`docs/compare-pair-orientation-review`): rule right on
  every pair it could check against the markers (114 of 114); required fixes applied
  and confirmed in a re-review. The thresholds were set on the same 145 pairs; do not
  quote them as validated on another corpus.
- **Found by that review, open:** four corpus trials where the optical and the phone
  curve are both inverted (their agreement is not meaningful; phone side to look at);
  the IMU sweeps score raw RMSE against a relative reference (median 143 degrees);
  `pt_report_common._lag_align_candidate` and `generate_paper_figures` align mirrored
  pairs at a different lag than the oriented comparison.
- **Onset release detector (`d2e2cee`, `35d3192`, `05b5a4e`):** REJECTED as a port
  (`docs/release-detector-port-findings`). It fixes 12 corpus trials and breaks 2, fails
  main's lift-and-hold tests, and cannot handle curves whose hold is the minimum, which
  includes most optical control curves. Release detector redesign deferred by the owner.
- **Release-timing sensitivity** (`docs/oscillation-count-sensitivity`): 40 of 162
  scorable corpus trials count a start-of-swing shoulder as half an oscillation; a 10 ms
  release shift changes N on 34 trials. Not what limits test-retest reliability.
  `area_ratio` has a separate, larger release sensitivity. The owner chose to count from
  the first trough, bundled with the app footing change.
- **Held until a bench session can back them:** frequency cap and `f_out_of_range`
  (`c2dfe67`), Rust flex-projection tier (`f429356`).
- **App reference footing (#110, merged 2026-11-17; core 0.4.0,
  `pt5trough-ref-2026-11-16`):** five terms (`omega_max_n` shown, not scored), N counted
  from the first trough, `HEALTHY_REF["f"]` 0.9511 to 0.9501, a 2.0 s scorable span in all
  three ports, and the app scored on the reference footing (`ReplayConfig::reference()`:
  10 ms ticks, EMA 0.5 rescaled, flex projection). Rust matches Python on 179 of 179
  corpus trials (traces within 2e-13 deg, verdicts identical, both conventions); the wasm
  matches the native core on the app-clock feed. App before to after: 59 on-screen and 62
  stored verdicts change, PT a median 0.074 lower; the app and the reference now agree on
  every trial. Reference before to after: 17 verdicts, N -0.5 on 59 trials. neuromech: 11
  intended bands move, no preset refitted; 14 scenarios sit within 0.03 PT7 of an edge.
  #76 and #83 closed as superseded. `derive_healthy_ref.py` runs without
  `--allow-unloadable` since #108 (seven unloadable optical baseline takes keyed in
  `excluded_trials.json`).
- **Do not:** remove `abs()` from clinical swing-angle extraction.

### 4. Rust AHRS / phone replay — SEED FIX MERGED
- **Canonical branch:** `main` (#78 merged; #91, a negative result, merged 2026-11-11).
- **Gravity-seed fix (#98, merged 2026-11-16 with a merge commit; core 0.2.0):** the seed quaternion had the tilt sign mirrored in
  Python and Rust. On real trials the phone starts about 100 degrees from flat, so the
  old filter started about 145 degrees wrong about gravity and did not converge within a
  trial. The fix changes every scored trial (A0 by a median 4.4 degrees; 24 verdicts at
  the reference settings, 36 on screen at the app's settings), improves resting-tail
  drift and A0 test-retest, and leaves IMU-versus-OptiTrack RMSE unchanged. Reports:
  `docs/seed-fix-corpus-check`, `docs/seed-fix-rust-confirmation`.
- **Rust port fidelity:** against Python at the app's settings on the same stream,
  traces are identical and verdicts agree on 179 of 179.
- **Consequence:** every phone-IMU number measured before #98 used the mirrored seed and
  is stale. Records made after the live app is redeployed carry `ALGORITHM_VERSION`
  `0.2.0+...`. Independent review of the split: `docs/seed-fix-split-review`.

### 5. OptiTrack / RMSE validation — STABLE
- **Canonical branch:** `main` (#81, #88 merged).
- **Entry points:** `batch_imu_vs_optitrack_rmse.py`, `rmse_pipeline_common.py`.
- **Gaps:** the RMSE gate compares only the active swing window, so it could not see the seed bug;
  do not treat it as a sufficient gate for filter changes. The result "AHRS floor has zero effect on
  144 paired corpus trials" is now recorded in `docs/reports/2026-11-02-ahrs-floor-verdict.md`
  (measured before the seed fix).

### 6. Bench / test-retest (September lineage) — FROZEN, to be harvested
- **Branches:** `bench/retest-abs`, `bench/retest-diagnostics`,
  `fix/swing-angle-signed-abs-fold`, `feat/rust-abs-flex-projection`,
  `fix/optitrack-trial-quality` (local only, not on origin). About 110 to 130 commits
  that never reached `main`. #68 closed as moot for main.
- **Rescued 2026-11-16 (#101):** the instrument-validation plan, tilted-rig protocol and run
  sheets and dynamic-characterization run sheet (now in `docs/bench/`), `regenerating-figures.md`
  and the September reports. Redacted for the public repo; each carries a banner saying it may
  predate the seed fix. Not rescued: the three September `docs/superpowers/` plans and specs and
  the root-level `DEPLOYMENT_PLAN`-era notes (still on the branches and the `archive/*` tags).
- **Next action:** each branch is tagged `archive/<name>`; retire the branches once the owner
  approves. Do not start new work on them.
- **Bench session kit (#109):** `docs/bench/2026-11-18-app-on-motor-session.md`, rig-log
  template and `bench_session_report.py` (phone gyro vs encoder: f, N, tau, step response;
  pairs trials from the rig log). First app-on-motor session 2026-11-18 on the horizontal
  rig: grades the sensor channel and capture, not the app's angle or PT score.
- **Canonical bench number:** phone static accuracy 0.58° (the 0.92° in the Notion SOP
  is stale). Measured before the seed fix.

### 7. Webapp (phone IMU app) — LIVE, NOT VALIDATED
- **Canonical branch:** `main`. `integrate-webapp` (local only, not on origin) and
  `feat/webapp-workbench-restyle` are stale; do not build or deploy from them.
- **Entry points:** `webapp/` (vanilla ES modules, wasm from `mobile-imu-core`);
  deploy with `npm run deploy` in `webapp/` (scripted Vercel upload to project `pendulastic-app`).
- **Gap to the reference:** closed by the reference-footing PR (workstream 3): from core
  0.4.0 the app scores on the reference footing and agrees with Python on every corpus
  trial. Records carry `score_formula_id` and `scoring_footing`; the trends view breaks a
  series where the footing changes. Before it merged, the app read a median 0.057 higher
  than the reference and disagreed on 56 of 179 on-screen verdicts. Live at 0.4.0 since
  2026-11-17.
- **Offline (#104):** the primary
  offline route is the existing install-once PWA. It is the only route on iOS, and was
  checked offline in desktop Chromium. `npm run build:usb` adds a single-file
  `webapp/dist-usb/` copy for Android and laptops, not yet tried on a real phone. See
  `docs/reference/offline-usb.md`. The custom-domain plan in `docs/reference/custom-domain.md`
  is dropped (domain not bought; leaving Vercel). **Owner:** try the USB file on one
  Android phone.
- **Bench-readiness release (#116, merged and deployed 2026-11-17, build `522923b0ba75`,
  algorithm `0.4.0+777e57ce0933`; scorer unchanged):** research raw recorder no longer discards
  unscorable recordings; worker errors are routed to the request they answer; crash/timeout
  shows NOT saved instead of hanging; iOS motion permission requested inside the tap; Stop
  pinned during capture; parameter panel opens on the scored five (owner); scripted deploy
  `npm run deploy` (clean origin/main only, tests, `--project pendulastic-app`, verifies the live
  BUILD_ID). Local `.vercel/` link to the wrong project deleted (owner, 2026-11-17). After a
  deploy, iPhones must close and reopen the app (cache-first service worker).
- **Backlog:** eviction soak test; zone recalibration (blocked: only 2
  legs with MAS ≥ 1); app bugs in `TODOS.md`.

### 8. Video / pose estimation, desktop apps, `web/`, `mobile/` — TO ARCHIVE
- Idle since August. Owner decision: archive.
- **First pass (#103, merged 2026-11-16):** `web/`, `mobile/`, `Evaluate Models Py/` and 13 root
  scripts with no importers and no CI test moved to `archive/` (see `archive/README.md`).
  Root scripts with no importers that were NOT moved because they feed current IMU or paper
  work: `compute_metrics.py`, `evaluate_settled_tail_gyro_bias.py`,
  `evaluate_tuning_grid_methodology.py`, `evaluate_pt7_discrimination.py`,
  `model_vs_optitrack_eval.py`, `run_statistical_engine.py`, the `generate_*` figure scripts
  (blocked on "name one canonical figure pipeline"), and the Motive scripts
  `ExporterScript-CSV-fixed.py`, `ReconstructAndExportMarkers.py`.
- **Frozen in place for now** (still imported by tests or other code):
  `workbench_engine.py`, `pendulastic_app.py`, `analysis_pipeline.py`,
  `batch_mediapipe.py`, `pendulastic_viewer.py`. Do not develop them, except
  `workbench_engine.compare_pair`, which the RMSE pipeline uses.

### 9. Hygiene and reporting — STALLED
- **Merged 2026-11-16:** #66 (repo cleanup; daily and Monday reports archived under
  `docs/`). The 43 draft `claude/daily-report-*` PRs are closed and their branches deleted;
  each report is on main. Nine older daily-report branches remain.
- The nightly hygiene agent is retired (owner, 2026-11-16).
- Run `git worktree list` for the current worktrees rather than trusting a list here.
- Disk: drive C: was full on 2026-11-11 and is at about 4 GB free after removing
  finished worktrees and regenerable build output. Builds and tests fail with "No space
  left on device" when it fills; `mobile-imu-core/target` and `neuromech/out` in any
  worktree are safe to delete.
- No `.gitattributes`: this Windows checkout can write CRLF into files that are LF on
  main. Compare `git diff --stat` with `git diff --stat --ignore-cr-at-eol` before
  committing.

## Paper / thesis backlog
- Collect the outstanding MAS grades; resolve the two open MAS queries in `mas_scores.csv` (one overall-vs-component disagreement, one suspected left/right transposition).
- Re-derive IMU results after the seed fix lands.
- Name one canonical figure pipeline.
- Gate the Python test files that do not need Tk in CI (61 of 74 are ungated).
