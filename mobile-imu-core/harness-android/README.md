# Pendulastic Capture Harness — Android (U0)

Throwaway capture-only app for the KTD3 shadow study. See U0 in
`docs/plans/2026-08-21-001-feat-phone-imu-pendulum-app-plan.md` for why this
exists and why it is explicitly not reused by U3+.

Not built or run yet on this machine — no JDK/Android SDK is installed here.

## First-time setup (once, on a machine with the toolchain)

1. Install a JDK 17 and the Android SDK (easiest path: install Android Studio,
   which bundles both).
2. Generate the Gradle wrapper so `./gradlew` works without a separate Gradle
   install: `gradle wrapper --gradle-version 8.7` from this directory (needs a
   one-time standalone `gradle` on PATH, or open the project in Android
   Studio, which generates the wrapper automatically on first sync).

## Build and run

```bash
./gradlew installDebug
```

or open this directory in Android Studio and run the `app` configuration on a
physical device (the Simulator/emulator has no real IMU — see the plan's
Dependencies/Assumptions).

## Recording a trial

1. Launch the app, tap **Start**, perform the pendulum swing, tap **Stop**.
2. The trial JSON lands in the app's external files dir:
   `/sdcard/Android/data/com.pendulastic.harness/files/trials/trial-<timestamp>.json`
   — pull it with `adb pull` or a file manager.
3. Feed the file into KTD3's shadow-study comparison (offline against U1+U2,
   and against the existing `imu_calibration_tuner.py` pipeline).

## Sample shape

Each trial file is a JSON array of `{t, role, sensor, v, phone_ts_ms}` objects,
matching `imu_calibration_tuner.py`'s `replay_trial()` input contract exactly
(see `SampleRecorder.kt`'s doc comment for why `role` is fixed to `"proximal"`).
