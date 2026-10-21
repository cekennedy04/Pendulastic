# Tilted-Rig Protocol — validating the app's FUSED angle (not just the sensor)

**Purpose.** The horizontal rig can only validate the *raw gyro* (0.9° vs the 1 kHz
encoder), **not** the app's fused angle. Tilting the swing plane 30–45° gives the
accelerometer a gravity reference in-plane, so the app's real shipping angle path
(AHRS fusion → flex-axis projection) is actually exercised and can be validated
against the encoder — without building a full vertical rig.

## Why tilt (the degeneracy this removes)
- **Horizontal:** gravity sits on the phone's z-axis; rotating in-plane barely
  changes the accelerometer. Madgwick has no reference, so the "fused" angle
  **collapses to gyro integration**. That is why horizontal SSR validated the
  *sensor* (0.9°) but showed the *fused* angle ~50–62° off — a **geometry artifact,
  not an app defect**.
- **Direct evidence (`ssr_wave_app_vs_encoder.png`, 2026-09-18):** on the horizontal
  rig the app's fused angle is **inverted AND washed out** vs the encoder — it reads
  ≈ −30° while the encoder holds +38°, ≈ +60° while the encoder holds −38°, and every
  sustained hold **decays back toward zero**. This is the fusion getting no in-plane
  gravity reference (sign-reversed flex-axis projection + Madgwick washout), NOT the
  `abs()` fold (a fold flips negatives *up*; here the sign is genuinely reversed).
  **Conclusion: the horizontal rig structurally cannot grade the app's angle** —
  it only ever validated the raw sensor. The app's accuracy must be tested where
  gravity anchors the plane.
- **Clinical:** the leg swings in the **vertical/sagittal** plane, where gravity
  anchors the fusion.
- **Tilt 30–45°:** projects a component of gravity onto the in-plane axes → the
  accelerometer senses the angle → the fusion **and** the flex-axis projection
  are exercised. This is the cheapest way to test the shipping angle path.

## What this newly validates
| Signal | Horizontal SSR | Tilted rig |
|---|---|---|
| Raw-gyro sensor accuracy | ✅ 0.9° | ✅ (re-confirm) |
| App **fused + projected** angle (what the scorer consumes) | ❌ (~62°, geometry) | ✅ **the point** |
| Does the fusion help once gravity is available? | n/a | fused vs gyro delta |

## Rig setup (one-time)
1. **Incline the swing plane 30–45°** from horizontal (tilt the whole mount / arm base).
   Measure the tilt with an inclinometer or a phone level; **log the exact tilt angle**.
2. Phone rigidly clamped to the arm end, as before; app open, on charge.
3. **Re-zero in the tilted plane:** power OFF → hand-move to the tilted rest → ZeroAngle →
   confirm `M1 ≈ 0` → Set Zero T&V → power ON → SET. Encoder zero = tilted rest.
4. Everything else stays at the established configs.

## Protocol A — SSR steps on the tilt (static angle accuracy)
Static "does the app report the correct held angle?" test, fusion-anchored.
- Config: P=4, I=0.3, D=0.03, PID filter ON, Output/Limits ±50 (the SSR settling config).
- **Single-signed staircase (all one side of zero):** `0 → 10 → 20 → 30 → 40 → 30 →
  20 → 10 → 0`, dwells **3–5 s** per step, step speed **~30 °/s** (gentle slew = clean
  steps; speed dominates overshoot, not the gains). **Staying on one side keeps the
  `abs()` fold out of the measurement** so the residual is pure static accuracy.
- 3 repeats = 3 recordings; every step gives a fused-vs-encoder held-angle pair.
- (Optional, only to reproduce the horizontal artifact on the tilt for contrast: the
  old through-zero `0 → +A → −A → +A → 0` staircase — expect the fold to reappear.)

## Protocol B — pendulum swings on the tilt (dynamic, clinically representative)
The clinically-honest test of the fused angle (single-signed, like a real leg):
- Config: P=1, I=0, D=0 (filter off), Output ±50; lift ≤ 50 °/s, return 200 °/s (the
  pendulum config).
- Hold-and-release from the start angle; let it swing to rest, wait 5 s.
- Start angles **30, 40, 30, 20, 10** (repeat 30° for repeatability); ~6–8 recordings.

## Capture (per recording)
1. Arm at the tilted zero, still. **Start the raw recorder** (app `?research=1` →
   *Record (raw)*, which keeps every recording — no scoring gate).
2. **Sharp sync tap** on the arm once (a spike both the encoder and the phone gyro
   resolve — bootstraps time alignment; `motor_reference.py` notes the sharp edge).
3. Start the LabVIEW encoder log; **note its filename**.
4. Run the trial (staircase or release). Stop phone, stop encoder — in that order.
5. Log the row (tilt angle, config, encoder filename, capture rank).

## Analysis — three signals against the encoder truth
1. **Encoder angle** — `labview_bin_reader.py` → 1 kHz CSV (truth).
2. **App fused+projected angle** — replay the raw JSONL through the **shipping**
   pipeline (`imu_calibration_tuner.replay_trial`; and the Rust core once the
   Rust↔Python reconciliation is done — validate the *canonical* path).
3. **Raw-gyro angle** — `ssr_extract.py` (sensor reference).

Align by the sync tap (or cross-correlate gyro-y vs encoder rate). Compute:
- **Fused vs encoder:** gain (SLOPE, ideal 1.0), intercept, R², per-step Δ error,
  mean/max |error|, drift °/s — and for Protocol B, angle **RMSE over the whole
  swing**, peak/amplitude error, N and f vs the encoder.
- **Fused vs gyro:** confirm the fusion now tracks (the ~62° horizontal gap closes).

## Success criteria
- App **fused** angle tracks the encoder to **≤ 5°** (clinical goal), ideally near the
  0.9° sensor floor once gravity anchors the fusion.
- **Fused ≈ gyro ≈ encoder** — proves the horizontal ~62° miss was geometry, not the app.
- SSR gain ≈ 1.0, R² ≈ 1.0 for the **fused** angle (not just gyro).

## Caveats
- Tilt is a proxy for the sagittal plane, not identical; absolute A0 / area_ratio still
  ideally want near-vertical, but 30–45° already breaks the degeneracy and exercises the
  fusion.
- The `abs()` fold still folds the SSR staircase's through-zero steps (Protocol A), but
  **Protocol B swings are single-signed like the clinic** and do not fold — so **Protocol
  B is the clinically-representative fused-angle test**.
- Validate the angle path **that ships**. Until the Rust core matches the Python reference
  (reconciliation in progress), replay through the reconciled/canonical implementation.
