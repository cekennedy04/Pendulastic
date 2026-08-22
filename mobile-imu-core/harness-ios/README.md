# Pendulastic Capture Harness — iOS (U0)

Throwaway capture-only app for the KTD3 shadow study. See U0 in
`docs/plans/2026-08-21-001-feat-phone-imu-pendulum-app-plan.md` for why this
exists and why it is explicitly not reused by U3+.

Written on a machine with no Xcode available — not generated, built, or run
yet. `project.yml` is an [XcodeGen](https://github.com/yonaskolb/XcodeGen)
spec rather than a hand-written `.xcodeproj`, since the `.pbxproj` format
isn't practical to author without Xcode itself.

## First-time setup (once, on a Mac)

```bash
brew install xcodegen   # if not already installed
cd mobile-imu-core/harness-ios
xcodegen generate
open PendulasticHarness.xcodeproj
```

## Build and run

Run the `PendulasticHarness` scheme on a physical iOS device — the Simulator
has no real accelerometer/gyroscope/magnetometer (see the plan's
Dependencies/Assumptions).

## Recording a trial

1. Launch the app, tap **Start**, perform the pendulum swing, tap **Stop**.
2. The trial JSON lands in the app's Documents/trials directory. Retrieve it
   via Xcode's Devices & Simulators window (Download Container) or the
   Files app if file sharing is enabled.
3. Feed the file into KTD3's shadow-study comparison (offline against U1+U2,
   and against the existing `imu_calibration_tuner.py` pipeline).

## Sample shape

Each trial file is a JSON array of `{t, role, sensor, v, phone_ts_ms}` objects,
matching `imu_calibration_tuner.py`'s `replay_trial()` input contract exactly
(see `SampleRecorder.swift`'s doc comment for why `role` is fixed to
`"proximal"`). Accelerometer values are in g's (Core Motion's convention) —
U1 handles the g's-vs-m/s² unit normalization against Android's output
(KTD10), not this harness.
