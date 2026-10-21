# Dynamic Characterization Run Sheet — horizontal rig, LabVIEW Stretch Mode

**Goal.** Characterize the phone's **sensor channel dynamics** against the 1 kHz encoder,
using the **Stretch Mode** profile (no hand release). Everything here is gyro-observable,
so it is valid on the horizontal rig: **oscillation frequency, ring-down count N, decay,
and step-response dynamics.** Absolute amplitude / A0 stays deferred to the tilted rig.

Phone angle = **raw gyro-y integration** (the validated horizontal estimator), NOT the
app's fused/`abs()` output. Analyze with `python bench_dynamics.py` (below).

---

## One-time setup (same as the SOP)
- [ ] Rig powered, arm mounted, path clear; phone rigidly clamped to the arm end.
- [ ] App in **raw-record mode** (`?research=1` → *Record (raw)*) so nothing is score-gated.
- [ ] Zero the arm (power OFF → hand-move to swing centre → ZeroAngle → confirm `M1 ≈ 0`
      → Set Zero T&V → power ON → SET).
- [ ] Angle Limits `CCW = 45`, `CW = −45`. E-Stop within reach.
- [ ] Log the **app build (commit)** and phone model on the session row.

## Capture order — EVERY recording (the sync tap is new; it's what makes timing absolute)
1. Arm at zero, dead still. **Start the phone raw recording.**
2. **One sharp sync tap** on the arm (a single knuckle rap) — a spike both the phone gyro
   and the encoder resolve. This bootstraps time alignment; without it, alignment is
   cross-correlation only (shape, not absolute latency).
3. **Start the LabVIEW encoder log — note the filename** (that's the join).
4. Press **OK** to launch the Stretch trial; let it run to the auto-stop.
5. **Stop the phone, then stop the encoder log.**
6. Export the phone JSONL; write the tracker row (Rec #, encoder filename, block, config).

> ⚠️ Watch the **SET lamp**. If it flashes / the arm goes dead, the amp tripped — release
> the E-Stop, press SET, and drop the P term (Block R) or step speed (Block S) one notch.

---

## Block R — Ring-down: frequency, N, decay  (UNDERDAMPED config)
The Stretch **Fast Trial** lifts the arm gently, then commands a fast return to 0; with the
loop underdamped (D = 0, filter OFF) the arm **rings about zero** — a motor-driven pendulum.
Sweep **P** to move the frequency (f ∝ √P); read f / N / decay at each.

### Config — ringing, not settling
<table>

| Setting | Value | Note |
|---|---|---|
| P term | **sweep (see below)** | sets frequency, f ∝ √P |
| Integration Term | **0** | I suppresses the oscillation |
| Derivative Term | **0** | any D damps the ring |
| PID FIR Filter | **OFF** | D = 0 **with** filter ON grows the swing — must be OFF |
| Output Range | ±50 | |
| Angle Limits | ±45 | |
| Trial Type | **Fast** | decouples lift and return |
| Fast Stretch Speed (lift) | **50 °/s** | gentle — below the phone's ~57 °/s release gate |
| Initial Start & Final Return Speed (release) | **200 °/s** | fast return → the ring |
| Stretch Angle Range | mid value | amplitude is currently fixed (~±37.5°); fine — dynamics don't need it to vary |
| Prepare Period | 3 s | baseline, under the 5 s auto-stop |
| Stretch and Hold Period | 5 s | sit still at the top before release |
| Neutral and Hold Period | 6 s | rings down, then 5 s-still auto-stops the app |
| Total Trial Number | 1 | |

</table>

### Run list — sweep P for frequency, 3 reps each = 12 recordings
<table>

| Rec | P term | Expected f (approx) | Encoder file | Capture rank | Notes |
|---|---|---|---|---|---|
| R1–R3 | **0.25** | low (~0.5 Hz) | | | |
| R4–R6 | **0.5** | | | | |
| R7–R9 | **1.0** | mid | | | |
| R10–R12 | **2.0** | high (~2–3 Hz) | | | |

</table>

Notes: the app scorer may **refuse** the fast (high-P) rings — that's fine, we score the
**raw gyro**, not the scorer (a refusal is itself a logged bandwidth data point). Keep P ≤ 2
to stay clear of amp trips; back off if the SET lamp flashes.

## Block S — Step dynamics: rise, overshoot, settle  (SETTLING config)
The SSR staircase read for its **transients**. Sweep **step speed** — overshoot tracks
speed, not the gains.

### Config — clean settling
<table>

| Setting | Value | Setting | Value |
|---|---|---|---|
| P term | **4** | PID FIR Filter | **ON** (D screeches without it) |
| Integration Term | **0.3** | Output Range | ±50 |
| Derivative Term | **0.05** | Angle Limits | ±45 |

</table>

Staircase `0 → +A → −A → +A → 0`, dwells **3 s / 3 s / 3 s / 6 s** (6 s final auto-stops).

<table>

| Rec | Step speed (°/s) | Encoder file | Capture rank | Notes |
|---|---|---|---|---|
| S1–S3 | **30** | | | expect ~0.1% overshoot |
| S4–S6 | **50** | | | |
| S7–S9 | **100** | | | |
| S10–S12 | **200** | | | expect ~5% overshoot |

</table>

> If Block S was already run as the tracker's "Step-Speed Sweep," reuse those recordings —
> the analysis below is the new part; no need to re-capture.

---

## After the bench — analysis
Decode encoder + score gyro dynamics against it, both with the **same** estimator:

```
# Ring-down block
python bench_dynamics.py --mode ringdown \
  --manifest <phone-session>-manifest.json --encoder-dir <encoder_folder> \
  --out ringdown_dynamics.csv --plot ringdown_dynamics.png

# Step block
python bench_dynamics.py --mode step \
  --manifest <phone-session>-manifest.json --encoder-dir <encoder_folder> \
  --out step_dynamics.csv --plot step_dynamics.png
```

Reports, per trial, phone-vs-encoder:
- **Ring-down:** frequency f (Hz), swing count N, decay τ (s) / damping ζ — plus gain & R²
  of phone-f vs encoder-f across the P sweep.
- **Step:** rise time (10–90%), overshoot (%), settle time (±2% band), per step.

## Pass / target
- Phone **f within ~2%** of the encoder across the P sweep (matches the earlier f ∝ √P result).
- Phone **N and decay τ** match the encoder envelope within tolerance.
- Step **overshoot / rise / settle** track the encoder and rise monotonically with step speed.
- All are **sensor-channel** claims — the fused/app angle stays deferred to the tilted rig.
