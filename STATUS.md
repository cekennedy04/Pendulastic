# Pendulastic status

**This is the single source of truth for what is being worked on.** Read it before
starting; update it in the same PR that changes a workstream's state. Dated notes in
`docs/reports/` are history, not status. Last full review: 2026-11-11; merges and owner decisions recorded 2026-11-16.

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

## Decisions taken (owner, 2026-11-16)

- **AHRS seed fix (#98):** merged with a merge commit (algorithm version 0.2.0). Every
  phone-IMU number measured before it is stale. The live app was redeployed from main
  (8aa1055) the same day; #102 stops Vercel's git integration overwriting it.
- **Drift bound and `HEALTHY_REF`:** finish #94 and re-derive `HEALTHY_REF` with the bound,
  as one versioned change (in progress).
- **Scoring footing:** move the app to the Python reference footing, together with
  counting oscillations from the first trough and dropping `omega_max_n` (#76 / #83), under
  one score-formula version bump (plan in progress on `feat/app-reference-footing`).
- **Release detector:** deferred; no problems seen in use. Revisit only if a problem
  turns up that is too big to ignore.
- **Clinical-field branches:** `neuromech/fall-speed-b13` and `neuromech/fall-speed-check`
  deleted.
- **Cleanup:** the 43 daily-report branches deleted; archive pass, September doc rescue and
  `archive/<branch>` tags approved (in progress); branch and worktree deletions are shown to
  the owner before they happen.
- **Nightly hygiene agent:** retired.
- **Footing plan Q1–Q6:** the recommended options (tracked reference-footing constant;
  trends break at 0.3.0; keep a lone early peak; `lateral_motion` later; detrend
  unification after #94; this change may move `HEALTHY_REF["f"]` by about 0.001).
- **Custom domain:** `pendulastic.com`, bought by the owner, pointed at the Vercel project.
- **Scorable-length gate (footing plan Q7):** the 40-sample minimum becomes 2.0 s of finite
  signal in all three ports, after checking it drops no `HEALTHY_REF` control curve.
- **Rig O1–O5** (literature review, second opinion and a contrarian review; memo in the
  session notes, to be added to `docs/plans/`):
  - *Before any session:* build the Player's own `Max |setpoint|` clamp (the Angle Limit
    fields do not bound the Player) and find why the dry run fell from 9/9 to 7/9 in band.
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
    owner after the dry-run bisect (7/9 is not a regression: the documented scorer fixes
    5058c9a and #79 moved the preset's intended PT7 to 0.139, and rig tracking lag costs a
    steady 0.021–0.024): once the footing change lands, refit the clasp-knife presets to the
    middle of their band on the new scorer, drop `M-catch_release_high` (identical to
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
- **Current numbers (main, 2026-11-16):** `python -m neuromech.build` writes 67
  scenarios. The A-P2 and A-P3 presets are unreachable on the current scorer. The
  simulated dry run delivers 9 of 9 runs in their intended band (7 of 9 before #96).
- **Release alignment (#96, merged):** rig sessions are scored from the delivered release
  (the arm leaves the hold 18 to 30 ms after the command) and runs where it cannot be
  found are flagged. Reviewed twice. Simulation only. **Do not report scores from a real
  rig session yet:** the fixed 25 ms tracking limit suits only the simulated plant.
- **Chirp-derived tracking limit (in progress, no PR):**
  `neuromech/chirp-derived-tracking-limit` judges each run against the plant identified
  from the session's chirp. Its review (`docs/chirp-tracking-limit-review`) found the
  release check robust under model mismatch but the tracking ratio fitted to the
  simulator's plant family; fixes are being applied, then a re-review. The branch was
  cut from #96 before it was squash-merged, so it must be rebased onto `main`.
- **Another session's work, not part of this plan:** `docs/neuromech-elbow-plan`
  (worktree `Pendulastic-wt-elbow`).
- **Rig path versus app path** (simulation only;
  `docs/reports/2026-11-10-rig-phone-angle-vs-app.md` on `eval/rig-phone-angle-vs-app`):
  `session.py`'s gyro integration tracks truth within about 1 degree when the swing is
  on gyro-y, but silently accepts an off-axis mount; the app's score is 0.06 to 0.15
  PT7 higher than the 100 Hz score. Rig scores must not be described as what the app
  would report. The generator simulates a vertical swing; a horizontal rig with the
  phone flat is untested.
- **Next actions:** finish, re-review and land the chirp-derived tracking limit; then
  the first rig session, read as a measurement of the limits.

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
  no release detector and no drift correction). `run_statistical_engine.py` holds a
  fourth `HEALTHY_REF` copy.
- **Conventions:** on screen the app scores with `detrend=False`; the record the app
  STORES uses `detrend=True` (`finish_detrended` in `wasm.rs`, `persistTrial` in
  `webapp/src/app.js`). `neuromech.build.score_angle` is 100 Hz, release given,
  `detrend=False`. `pt_report_common.score_trial` uses the `compute_pt_params` defaults
  (`detrend=True`, auto release). These are not interchangeable.
- **Lineage comparison** (`docs/scorer-lineage-comparison`): test-retest under
  `detrend=False` is a tie between `main` and the September lineage. `9a555de` is
  already on main. `61d5723` (signed swing angle) must stay out.
- **Drift-correction bound (PR #94, draft):** ported to Python and Rust with caller
  handling of the new `AmplitudeExceedsCurveError`; `detrend=False` output is
  bit-identical on the corpus. Independent review: code correct. Owner decided to finish it
  and re-derive `HEALTHY_REF` with the bound (in progress on `fix/drift-bound-healthy-ref`).
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
- **Open PRs:** #76 (drop `omega_max_n`) and #83 (desktop live view; contains #76).
  Both conflict with `main`. The owner chose to drop `omega_max_n`; it is being carried
  into the app footing change, after which these two are superseded.
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
- **Rescued 2026-11-16 (draft PR):** the instrument-validation plan, tilted-rig protocol and run
  sheets and dynamic-characterization run sheet (now in `docs/bench/`), `regenerating-figures.md`
  and the September reports. Redacted for the public repo; each carries a banner saying it may
  predate the seed fix. Not rescued: the three September `docs/superpowers/` plans and specs and
  the root-level `DEPLOYMENT_PLAN`-era notes (still on the branches and the `archive/*` tags).
- **Next action:** each branch is tagged `archive/<name>`; retire the branches once the owner
  approves. Do not start new work on them.
- **Canonical bench number:** phone static accuracy 0.58° (the 0.92° in the Notion SOP
  is stale). Measured before the seed fix.

### 7. Webapp (phone IMU app) — LIVE, NOT VALIDATED
- **Canonical branch:** `main`. `integrate-webapp` (local only, not on origin) and
  `feat/webapp-workbench-restyle` are stale; do not build or deploy from them.
- **Entry points:** `webapp/` (vanilla ES modules, wasm from `mobile-imu-core`);
  deploy is a manual Vercel upload of `webapp/dist` to project `pendulastic-app`.
- **Known gap to the reference:** see decision 2. The app's PT score is a median 0.058
  higher than the reference configuration and reads borderline where the reference
  reads healthy on 32 of 179 corpus trials. Zone thresholds and `HEALTHY_REF` were
  derived on the reference footing; whether they suit the app's has not been tested.
- **Offline + custom domain (draft PR #104, `feat/offline-usb-build`):** the primary
  offline route is the existing install-once PWA. It is the only route on iOS, and was
  checked offline in desktop Chromium. `npm run build:usb` adds a single-file
  `webapp/dist-usb/` copy for Android and laptops, not yet tried on a real phone. See
  `docs/reference/offline-usb.md`. `docs/reference/custom-domain.md` covers moving to
  `pendulastic.com` (GoDaddy registrar, Vercel host). **Owner:** buy the domain, set
  the DNS records, try the USB file on one Android phone.
- **Backlog:** scripted deploy; eviction soak test; zone recalibration (blocked: only 2
  legs with MAS ≥ 1); app bugs in `TODOS.md`.

### 8. Video / pose estimation, desktop apps, `web/`, `mobile/` — TO ARCHIVE
- Idle since August. Owner decision: archive.
- **First pass (draft PR, pending merge):** `web/`, `mobile/`, `Evaluate Models Py/` and 13 root
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
