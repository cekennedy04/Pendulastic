# TODOS

## Design debt

- **Run `/design-consultation` for the phone IMU pendulum app** before UI implementation locks in a default look.
  - **Why:** `docs/plans/2026-08-21-001-feat-phone-imu-pendulum-app-plan.md`'s design review (KTD6) deferred the visual system (color, typography, iconography) — no `DESIGN.md` exists yet for this new app.
  - **Context:** The existing `mobile/` RN app already committed to large typography/touch targets for clinical use; the new app's a11y minimums (R9) match that bar, but its visual identity is still unset.
  - **Depends on:** Nothing blocking — can run any time before or during U4/U5 implementation.

## Eng debt (from /plan-eng-review + outside-voice review, 2026-08-21)

- **Strengthen U7's cross-platform parity test beyond same-Rust-code equivalence.**
  - **What:** U7 currently only proves the Rust core produces the same score via Swift and Kotlin bindings — it doesn't test real device sensor capture, cross-stream ordering, timestamp handling, or coordinate/unit normalization on-device.
  - **Why:** Outside-voice review (Codex) flagged this as "weak to the point of being misleading" — a green U7 could hide real on-device bugs.
  - **Pros:** Closes the gap between "the port is correct" and "the app on a real phone is correct."
  - **Cons:** Requires actual on-device instrumentation/logging to compare against a fixture, more effort than the current pure-binding test.
  - **Depends on:** U1-U5 implemented.

- **Add a trial-history/browse/recovery model.**
  - **What:** No specified way to browse past trials, reopen one, re-export it, delete it, or recover after the app is killed mid-trial.
  - **Why:** Outside-voice review flagged that "export" currently has no defined entry point beyond the single just-completed trial.
  - **Pros:** Matches how a clinician would actually use this across a full clinic day (multiple participants, multiple trials).
  - **Cons:** Adds a new screen/data model beyond this plan's v1 scope.
  - **Depends on:** U8 (participant model), U6 (export).

- **Specify data handling for locally-stored participant + movement data.**
  - **What:** Beyond "local only" (already scoped), decide on device-backup inclusion/exclusion, behavior on a shared/multi-user device, retention/deletion policy, and share-sheet disclosure risk when exporting.
  - **Why:** Outside-voice review noted participant names + raw movement recordings are research/health-adjacent data even with IRB explicitly out of scope for this plan.
  - **Pros:** Cheap to decide now, before real participant data exists on real devices.
  - **Cons:** Slightly expands scope beyond pure engineering into data-governance territory.
  - **Depends on:** U8, U6.

- **Nail down real distribution accounts/credentials for KTD8.**
  - **What:** KTD8 names the distribution mechanism (TestFlight, Firebase App Distribution/APK) but not the actual prerequisites: an Apple Developer account/team for signing/provisioning, a Firebase project, and a macOS/Xcode build environment.
  - **Why:** Outside-voice review noted "no blockers" in the plan ignores these real-world prerequisites.
  - **Pros:** Surfaces account/access lead time before it blocks a build.
  - **Cons:** Depends on organizational access (Apple Developer Program membership, Firebase project ownership) outside pure engineering scope.
  - **Depends on:** Nothing blocking — can be resolved any time before U4/U5 are ready to distribute.

- **Define a hardware-capability fallback policy.**
  - **What:** No policy for Android devices lacking a usable gyroscope/magnetometer, or with heavily throttled/OEM-limited sensor rates.
  - **Why:** Outside-voice review flagged this as unaddressed; KTD5 assumes standard listener rates are available without a floor for degraded hardware.
  - **Pros:** Prevents a confusing silent-failure or bad-score experience on unsupported devices.
  - **Cons:** Requires device-capability testing across a range of Android hardware, which isn't feasible to fully scope now.
  - **Depends on:** U5.

## CEO debt (from /plan-ceo-review via /autoplan + Codex outside voice, 2026-08-21)

- ~~**Add explicit capture-acceptance criteria — a "do not score this trial" quality gate.**~~ **RESOLVED 2026-08-21** — specified in the plan as KTD11 + R14 + U13 (`capture_quality` field on `ScoreResult`: sensor-stream completeness, attachment-stability residual, swing-range plausibility). Exact numeric thresholds for the latter two remain an Outstanding Question, deferred to the KTD3 shadow study's real trial data — not resolved here, only the checks and their data source are.

- **Decide participant-record disambiguation policy.**
  - **What:** U8's participant list has no defined behavior for two participants with the same display name (no uniqueness constraint beyond R7's tagging requirement).
  - **Why:** CEO review flagged this as a real but low-stakes edge case — not worth gating v1 on, but undecided.
  - **Pros:** Cheap to decide once (e.g. show a disambiguating ID/date-added alongside name).
  - **Cons:** None significant — small UI decision.
  - **Depends on:** U8.

## Web capture app debt (from the `webapp-core-capture` whole-branch review, 2026-08-25)

These are rulings the branch made and lived with. They were recorded only under
`.superpowers/`, which is gitignored and does not survive the merge, so they are
restated here with enough context to act on without that scratch space.

- **Hold-drift is coaching-only; it does not reach the score.**
  - **What:** `session.rs`'s drift gate resets the live UI to `Moving`, but `TrialSession::finish` hands the log to `replay`, whose `ReleaseDetector` knows only the rate gate. A trial released after large accumulated drift therefore scores 20 parameters indistinguishable from a clean one.
  - **Why:** The design spec's answer is `capture_quality: {low_confidence: 'hold_drift'}`, deferred to the persistence plan. Until that lands there is no in-app signal at all — the clinician sees a normal-looking result.
  - **Depends on:** The `capture_quality` / persistence work (KTD11 + R14 + U13).

- **The capture capability floor has no owner.**
  - **What:** The spec binds ≥50 Hz, zero `dt == 0`, and zero `dt` outside `(0, 500) ms`, but nothing implements or measures any of it. `replay.rs` silently substitutes `dt = 0.01` for out-of-range intervals, and `capture.js` silently drops samples once its `CAP`-sized accumulator fills.
  - **Why:** Both are the documented 2026-08-17 defect class: they produce a plausible, clean-looking wrong answer, and the direction of the error makes a spastic limb look healthier. Surfacing `clamped_dt` / `dropped_samples` counters in the `state` message would convert the silence into a number the UI (and later the export) can act on.
  - **Depends on:** Nothing blocking — the counters are additive to the existing worker `state` message.

- **Gate thresholds are duplicated as UI literals.**
  - **What:** `app.js` renders `0.95 s` and `5.00°` as hardcoded text, `wasm.rs` returns a hardcoded `0.95`, and `MAX_HOLD_DRIFT_DEG` is documented as uncalibrated and expected to move once shadow-study data exists.
  - **Why:** When the threshold changes, the clinician is shown a target that is not the one being enforced, and no test fails. The gate values should cross the wasm boundary once and be rendered from there.
  - **Depends on:** The KTD3 shadow study, which is what will actually move `MAX_HOLD_DRIFT_DEG`.

- **The retroactive-release scrub UI is blocked at the wasm boundary.**
  - **What:** `wasm.rs` collapses `TrialError::InsufficientSamples` and `TrialError::ReleaseNeverDetected` into a single `undefined`.
  - **Why:** The KTD9 release-override recovery only makes sense for `ReleaseNeverDetected` — a too-short log cannot be recovered by scrubbing. The scrub UI cannot be built until the two are distinguishable across the boundary.
  - **Depends on:** Nothing blocking — a discriminated return from `finish()`, then the UI.

- **`cargo clippy` in CI cannot fail.**
  - **What:** `.github/workflows/ci.yml`'s rust job runs `cargo clippy --all-targets` with no `-D warnings`, so the step is decorative: it currently emits two warnings (`needless_range_loop` on `q_dot` at `src/ahrs.rs:232` and on `omega` at `src/session.rs:105`) and still passes.
  - **Why:** A check that cannot fail is worse than no check, because the pipeline reads as enforcing a lint standard it does not enforce. Either arm it with `-D warnings` and fix those two, or drop the step.
  - **Depends on:** Nothing.

- ~~**Two tests assert nothing.**~~ **RESOLVED 2026-08-25** — `mobile-imu-core/tests/replay_test.rs`'s `the_method_selects_between_relative_and_ockendon` asserted a struct field equalled what it had just been set to; it now runs `replay` under both methods and pins the Ockendon angles against `ockendon_deg` itself (mutation-checked). `webapp/tests/worker.test.js`'s malformed-cfg test accepted either of two message types; it now pins the one outcome that actually occurs (undefined gains coerce to NaN and a normal opening `state` is posted). That NaN coercion is itself worth a decision — a session built with NaN gains is accepted silently — but it is now pinned rather than absorbed by an either/or.

## Local durability (Plan 2, `webapp-local-durability`) — open items, 2026-08-26

Recorded from the plan's review ledger, which is gitignored and does not survive the merge.
Ordered roughly by how much they matter.

- **The Critical fix is unguarded.** Trial persistence was completely dead in the browser until
  `786ad30` — `onResult` read a capture handle the Stop button had already nulled, so nothing
  ever reached IndexedDB while the UI rendered normally. The fix is two coupled halves (Stop
  captures before nulling; `onResult`/`onError` retain rather than assign). Neither works alone.
  **Delete either half and all 104 tests still pass** — the DOM-guarded block in `app.js` has no
  coverage of any kind. The pure `retainExportHandle` tests pin the rule, not the wiring.
- **CI cannot catch a stale `BUILD_ID`.** CI runs `cargo build` + `wasm-bindgen` directly, never
  `build-wasm.mjs`, so `BUILD_ID`'s *value* is unchecked (only its 12-hex shape). This already
  bit once: `786ad30` shipped a `build-id.js` describing no real shell state, with
  `ALGORITHM_VERSION` naming the commit *before* persistence worked. Fix: assert in CI that
  `BUILD_ID` differs from the merge base whenever any `SHELL` file changed in the diff.
- **`cache.addAll` uses the default HTTP cache.** `updateViaCache: 'none'` only covers `sw.js`
  and its imports; the shell's own fetches can still come from a stale HTTP cache, which would
  defeat the shell-wide key on a production host with a long `max-age`. Invisible today because
  `dev_server.py` sends `no-store`. One line: `SHELL.map((u) => new Request(u, {cache: 'reload'}))`.
- **Cross-trial contamination.** Tapping Start in the one-task window between Stop and the
  worker's `result` reply attributes the previous trial's params to the new handle. Pre-existing.
- **A Stop tap during the iOS permission prompt is a no-op**, leaving a capture running with the
  Start button showing and no way to stop it.
- **`TRIAL_SIDE` is `null` deliberately** (a fabricated laterality in the archive of record is
  worse than an absent one — it violates spec §3.2's `'left' | 'right'` union on purpose). It is
  not exported, so nothing would notice a silent revert to `'left'`. Unit U8 replaces it.
- **Parked: the export CAS spans two IndexedDB transactions.** Safe only because no `await` sits
  between the re-read and the `put()`; `app.js` carries a banner comment saying so. A real fix
  needs an atomic read-then-write `db.js` does not expose.
- **`capture_quality` is unconditionally `'clean'`** and exported as such — the manifest asserts a
  quality judgement nothing evaluated. KTD11's gate is not in this plan.
- **`release_quat` (spec §3.2, §4.3) is not in the trial record.** Recoverable by replaying
  `raw_jsonl` with the stored `release_idx`, so redundancy loss rather than data loss.
- **`navigator.storage.persist()` (spec §3.1) is never called.** Free on Chrome/Android.
- **`gitRevision()` has no `-dirty` marker**, so a wasm built from uncommitted Rust stamps the
  last clean SHA as the source of record.
- Smaller: `persistTrial`'s two `put()`s are not in one transaction (fails safe); the busy-count
  logic has no automated coverage; `getAll`'s `onerror` path and `sw.js`'s own handlers are
  untested; `manifest.json` has no icons so Android may create a shortcut rather than a WebAPK
  (iOS unaffected); closing a session writes nothing to storage, so an exported-but-never-closed
  session is indistinguishable from a closed one and a reload mid-visit splits it into two records.

**Unverified premise, unchanged:** the 7-day eviction exemption for Home-Screen PWAs has still
never been tested. `webapp/docs/eviction-soak-test.md` is the protocol; its results table is empty.
