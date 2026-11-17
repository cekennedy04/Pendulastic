# 2026-11-18 bench session: phone app on the motor rig

**What this session shows.** (a) The app captures, exports, and does not crash or
refuse on rig swings. (b) The phone gyro channel matches the encoder for frequency,
swing count N, decay tau, and step dynamics. The rig swings in a horizontal plane, so
the app's fused angle and PT score cannot be assessed here (gravity lies on the
rotation axis). Do not quote a PT score from this session as an accuracy number.

Background: `DYNAMIC_CHARACTERIZATION_RUN_SHEET.md` (same folder).

## Bring
- [ ] iPhone, charged, with Low Power Mode off, Auto-Lock set to Never, and Do Not Disturb on.
- [ ] The clamp that holds the phone flat, screen up, on the arm end. Use the same position for every trial.
- [ ] Laptop with this branch checked out and `.venv` working (`python -m pytest tests/test_bench_dynamics.py` passes).
- [ ] A way to move files: AirDrop or a cable for the phone, and the usual FTP or USB route for the LabVIEW `.bin` files.
- [ ] A printout of `2026-11-18-rig-log-template.csv`, or a copy open on the laptop.

## Setup (10 min)
- [ ] Rig powered, path clear, phone rigidly clamped. **E-Stop within reach.**
- [ ] Trip reset: if the SET lamp flashes or the arm goes dead, the amplifier has tripped. Release the E-Stop, press SET, then back off (lower P, or lower step speed).
- [ ] Known trips: speed 300, and D 0.1 without the filter. Angle Limits ±45 bound the profile only and are **not** a servo guard.
- [ ] Zero the arm: power OFF, move the arm by hand to the swing centre, press ZeroAngle, confirm `M1 ≈ 0`, press Set Zero T&V, power ON, press SET.
- [ ] LabVIEW file prefix: `bench1118`. Trial numbers continue across the day. Do not reuse a number.
- [ ] **App and version check.** In iPhone **Safari** (not the home-screen icon), open
      `https://pendulastic-app.vercel.app/?research=1`. Doing everything in Safari keeps the
      scored captures and the raw recordings in one store, so a single export holds both.
      Open a session and read the footer `build … · algorithm …`. Write it on the log.
      On 2026-11-17 the live site served `algorithm 0.2.0+8aa1055a6e88…`, which is the
      #98 seed fix. `main` is at `0.3.0+…` (re-derived HEALTHY_REF). That changes the
      score reference, not capture or export, so either version works for today. Record
      which one you used.

## Per-trial routine
1. Arm at zero and still. Start the phone (see the block for which mode).
2. Start the LabVIEW encoder log and note the trial number.
3. Block S only: one sharp knuckle tap on the arm, now that both are recording.
4. Press **OK** in LabVIEW. Wait for the app to auto-stop (scored mode) or for the profile to end (raw mode).
5. Stop the phone (raw mode). Stop the encoder log **last**, after the phone has stopped.
   The analyzer scores only the stretch both recorded.
6. Fill one log row: encoder trial number, block (`R` or `S`), P/I/D, speeds, and
   `app_result` (`scored` / `NOT SCORABLE` / `ERROR: <text>`).

`ERROR` trials are not saved or exported, so this log is the only record of them.
**Keep a row for every trial, including bad ones.** The analyzer pairs trials from the
log. It drops the `ERROR` rows and matches the remaining rows, in trial order, to the
exported phone trials in capture order. Cross-correlation then only checks each pair.
An unmarked ERROR or a missing row shows up as a COUNT MISMATCH in the report.

## Block R: ring-down P sweep, scored app capture (~25 min, 12 trials)
Phone mode: **Quick Test → Start** (the normal scored capture). **Do not tap the arm**
in this block. A sharp tap exceeds the app's release gate and can end the trial early.
The rig log does the pairing.

| Setting | Value |
|---|---|
| Trial Type | **Fast** |
| Integration / Derivative | **0 / 0** |
| PID FIR Filter | **OFF** |
| Output Range | ±50 |
| Fast Stretch Speed (lift) | **50** (must stay under 57.3 °/s) |
| Initial Start and Final Return Speed (release) | **200** |
| Prepare / Stretch-and-Hold / Neutral-and-Hold | 3 s / 5 s / **12 s** (covers the full ring at low P) |

| P | Trials | Expect |
|---|---|---|
| 0.25 | 3 | ~0.5 Hz |
| 0.5 | 3 | ~0.7 Hz |
| 1 | 3 | ~1 Hz |
| 2 | 3 | ~1.4 Hz |

Stay at P ≤ 2. If the SET lamp flashes, drop one notch. A `NOT SCORABLE` result is
a data point, not a failure: log it and move on.

## Block S: step response, raw recorder (~15 min, 5 trials)
Phone mode: home tile **Record (raw)** → Start. Then start the encoder log, then **one
sharp knuckle tap on the arm**, then press OK. Tap only once both are recording, so
the tap shows up in both traces.

| Setting | Value |
|---|---|
| P / I / D | **4 / 0.3 / 0.03** |
| PID FIR Filter | **ON** (D without the filter screeches) |
| Step speed | **30** × 5 trials |
| Staircase | 0 → +A → −A → +A → 0, dwells 3/3/3/6 s |

## If time remains (optional, ~10 min)
- Repeat P = 1 × 3 to check repeatability.
- Step speed 100 × 3. Overshoot should rise with speed.

## Save
```
Pendulastic_Mobile_App_Trial/2026-11-18/phone/     <- the app export: *-manifest.json + *.jsonl
Pendulastic_Mobile_App_Trial/2026-11-18/encoder/   <- bench1118*.bin
Pendulastic_Mobile_App_Trial/2026-11-18/rig_log.csv
```
To export from the app: Session → Export, then AirDrop or save every file, including
the manifest. That folder is gitignored, so nothing there is committed.

## Analyze (one command, ~10 s)
Run in PowerShell from the repo root, with `feat/bench-session-kit` (or main, once
merged) checked out:
```powershell
.venv\Scripts\python.exe bench_session_report.py --phone Pendulastic_Mobile_App_Trial\2026-11-18\phone --encoder Pendulastic_Mobile_App_Trial\2026-11-18\encoder --glob "bench1118*Stretch*.bin" --rig-log Pendulastic_Mobile_App_Trial\2026-11-18\rig_log.csv --since 2026-11-18 --out Pendulastic_Mobile_App_Trial\2026-11-18\report
```
- `--glob` keeps only the stretch trials. LabVIEW also writes GT/MVC/SP `.bin` files.
- `--since` drops any older Quick Test trials that come along in the same export.
- Output: `trials.csv`, `steps.csv`, `summary.md`, and figures `fig_ringdown_overlay.png`,
  `fig_f_scatter.png`, `fig_decay_scatter.png`, `fig_step_overlay.png`. The summary has
  slide-ready tables, app exported/scored counts, ERROR and NOT SCORABLE counts per P
  from the log, and ALGORITHM_VERSION.
- **Check the Pairing section first.** It must not say COUNT MISMATCH. Any flagged row
  (`match_q < 0.5`, or phone f more than 10 % off the encoder, which usually means a
  mis-pair) needs a look: usually an ERROR not marked in the log, or a missing row.
  Fix the log and rerun.

## Pass targets
- Phone f within ~2 % of the encoder across P. N equal within ±1. Tau within ~10 %.
- Step overshoot within ~1.5 points. Rise and settle within ~0.02 s.
- App: every trial exported, no `ERROR`. Report the `NOT SCORABLE` count per P.

On 2026-09-15 data, the same analyzer gave a median |Δf| of 0.6 %, equal N, and a
median |Δtau| of 0.24 s on 20 end-of-staircase rings. On steps it gave a median
|Δovershoot| of 1.0 point and |Δrise| of 0.006 s over 176 scored steps (out of 201
detected), with one polarity across all 46 trials.
