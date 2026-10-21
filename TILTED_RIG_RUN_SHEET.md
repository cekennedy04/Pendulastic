# Tilted-Rig Run Sheet — app angle-accuracy validation

Goal: grade **the app's fused angle** (what the scorer consumes), which the
horizontal rig structurally cannot do (there the app reads inverted + washed out —
see `ssr_wave_app_vs_encoder.png`). Tilt the plane so gravity anchors the fusion.

## One-time setup
- [ ] Incline the swing plane **30–45°** from horizontal. Measure with an inclinometer
      / phone level and **write the exact tilt here: ______°**
- [ ] Phone rigidly clamped to arm end; app open; on charge; `?research=1` (raw recorder).
- [ ] Re-zero in the tilted plane: power OFF → hand-move to tilted rest → ZeroAngle →
      confirm `M1 ≈ 0` → Set Zero T&V → power ON → SET.
- [ ] Independent encoder sanity check + rigid-mount tap test (one time).

## Per-recording capture order (every row)
1. Arm at tilted zero, still → **Record (raw)** in the app.
2. **One sharp sync tap** on the arm (spike both encoder + gyro resolve).
3. Start LabVIEW encoder log → **note filename**.
4. Run the trial → stop phone → stop encoder (that order).
5. Log the row below.

## Block S — STATIC angle accuracy  (config: P4 / I0.3 / D0.03, filter ON, ±50)
Single-signed staircase `0→10→20→30→40→30→20→10→0`, 3–5 s dwells, step speed ~30°/s.

| Rec | Tilt° | Encoder file | Capture rank | Notes |
|-----|-------|--------------|--------------|-------|
| S1  |       |              | 1            |       |
| S2  |       |              | 2            |       |
| S3  |       |              | 3            |       |

## Block D — DYNAMIC swing accuracy  (config: P1 / I0 / D0, filter OFF, ±50; lift ≤50°/s, return 200°/s)
Hold-and-release from start angle; let it ring down to rest; wait 5 s.

| Rec | Tilt° | Start angle | Encoder file | Capture rank | Notes |
|-----|-------|-------------|--------------|--------------|-------|
| D1  |       | 10          |              | 4            |       |
| D2  |       | 20          |              | 5            |       |
| D3  |       | 30          |              | 6            |       |
| D4  |       | 30 (repeat) |              | 7            |       |
| D5  |       | 40          |              | 8            |       |

## After the bench — analysis (three traces vs encoder truth)
- Encoder: `labview_bin_reader.py` → 1 kHz truth.
- App fused+projected: replay raw JSONL through the shipping pipeline.
- Raw gyro: `ssr_extract.py` (sensor reference).
- Align by sync tap. Report **for the FUSED angle**:
  - Block S: gain (ideal 1.0), bias, R², per-step |err|, drift °/s across amplitudes.
  - Block D: swing **RMSE**, peak/A0 error, N, f, decay vs encoder.
  - Fused-vs-gyro delta → does the horizontal ~50–62° gap close once gravity anchors?

## Pass criteria
- App **fused** angle tracks encoder **≤ 5°** (clinical goal), ideally near the 0.9° gyro floor.
- Fused ≈ gyro ≈ encoder → proves the horizontal miss was geometry, not the app.
