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
