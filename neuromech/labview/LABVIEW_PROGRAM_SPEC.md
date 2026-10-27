# Knee-spasticity emulator — LabVIEW program specification

**Constraint that shapes everything here:** the rig runs `StretchExperiment.lvproj` (LabVIEW RT 11.0,
target "LabviewTargetMach…", network shared-variable library `variables - network - RT (separate)`),
which we do not own and will not edit — everything is done through its front panel. So nothing below
adds a block to the RT target. The emulator is a **separate host VI**, the *Pendulum Player*, that
writes the rig's existing position-setpoint shared variable; StretchExperiment's own position loop,
logging and amplifier protection do the rest.

Build guide for the Player (front panel, 15 blocks, dry run, first live run):
https://claude.ai/code/artifact/c4bb3c6a-d73d-410c-b512-40967ce0aae0 · rig controls and trip limits:
https://claude.ai/code/artifact/b78f4da2-7b15-4f51-b806-f994be21b9c0

Companion code: `neuromech/` (Python twin, scenario library, session planner/scorer, ILC, dry run).

---

## 1. Modes, and what the constraint allows

| Mode | Where it runs | What it can emulate | Needs | Status |
|---|---|---|---|---|
| **A — Scenario player** | **Host**: `PendulumPlayer.vi` reads a 1 kHz file and writes the interpolated angle to `M1JointAngleDesired` every 10 ms (DataSocket, clock-indexed); the RT target's existing PID tracks it | Everything kinematic: all 55 scenarios | Nothing on the target. Confirm the variable in NI Distributed System Manager first (guide, step 1) | **Build now** — the critical path |
| **B — Live model** | **Host**, inside the Player's loop: the Formula Node stepped 10 × per 10 ms tick, its angle written instead of a file sample | The same scenarios, parameters on the front panel instead of in a file | Mode A working | Optional: it renders nothing a file cannot. Kept because it is the step toward interaction |
| **B′ — Interactive (admittance)** | Must close the loop on the measured torque at 1 kHz — **inside the RT loop** | A PT pushes the virtual leg | An edit to StretchExperiment (or our own RT VI). A 10 ms, jittery host loop is too slow and late to render a stiff virtual leg stably | **Blocked** by the no-edit constraint; owner decision |
| **C — Torque mode on a real shank** | RT torque loop | Real pendulum + emulated muscle torques; phone sees true gravity | Vertical mount, shank matched to J and mgl, gearbox, RT access | With the tilted/vertical rig and RT access |

There is no feedforward path: the Player can only set the target. **ILC is the substitute** — the
command file itself is pre-distorted from the previous run's encoder log (`neuromech.ilc`), which is
feedforward computed offline, and needs no change to the rig.

## 2. Files

`python -m neuromech.session plan` writes, per run:

| File | For | Format |
|---|---|---|
| `player/<run>.csv` | **the Player** | two columns, **no header**: `t_s, setpoint_deg`, 1 kHz, starting at t = 0 and **0 deg** (the arm is parked at zero; a non-zero start would be a step, so the planner refuses it). A header row would read as zeros, make the Player's dt = 0, and end playback immediately |
| `commands/<run>.csv` | scoring and ILC | `t_s, setpoint_deg, vel_dps, acc_dps2, phase` with header (`phase` 0 = sync pulses + lift, 1 = hold at the release angle, 2 = released swing, 3 = identification) |
| `commands/<run>.dbl` | a future RT-side player | the same five columns, big-endian float64, interleaved |
| `runsheet.csv` | the operator | run ids only (R3 blind), `peak_deg` per run, and an `encoder_file` column to fill in |

`setpoint_deg`: rig angle, **0 = the rig's zero (where the passive leg would hang)**, + = toward extension.
Every file starts with the shared preamble from `motor_reference.py` (3 × 5° sync pulses, 1 s still,
4 s minimum-jerk lift at ≤ 42 °/s — below the phone's 57 °/s release gate — then a 3 s hold). The
expected scorer output for every scenario is in `out/scenario_manifest.csv`.

## 3. What reaches the motor, and the guards

The Player writes every ~10 ms; the shared variable holds each value, so the RT loop receives a
**100 Hz staircase**, and Windows lateness of a few ms shifts a step, never the timeline (the Player
indexes the file by the clock). The dry run models exactly this (`dryrun.player_hold`): every run still
lands in its intended band, median tracking 0.64° (0.57° with an ideal 1 kHz command), the plant is
still identified from the chirp (6.34 Hz, ζ 0.234), and ILC still converges (to ~0.1°).

Guards, in the order they act:

1. **`peak_deg` on the run sheet** — the furthest the run will command. Compare it with the arm's
   range before pressing run. The `Angle Limit CCW/CW` fields **do not bound the Player** (they bound
   the Stretch profile only; the arm has reached 63° and −81° with them at ±25).
2. **In the Player** (add to the guide's build): a `Max |setpoint| (deg)` control; refuse to start if
   the file's peak exceeds it, and clamp every write to it.
3. **Dry Run** switch on the Player: the whole file plays with nothing written. Use it for every new file.
4. **The amplifier fault** (SET lamp flashing; E-Stop release → SET) — the only protection that has
   actually stopped the arm. Trips seen at speed 300, P 4.5 with I 0.5, D 0.1 unfiltered.

**Logging** is StretchExperiment's own 7-channel `.bin` (position, velocity, position_setpoint, torque,
two EMG, velocity_setpoint), so `position_setpoint` records the staircase the target received and
`neuromech.ilc.locate` finds each run in its log from it. StretchExperiment names the file itself:
write the real name in the run sheet's `encoder_file` column; `session score` pairs by that column.
**Bench check before the first session:** confirm the log records while the Player drives the
setpoint. If it only logs inside a Stretch trial, start a long Stretch-mode hold and play over it —
test this with a 10° file first.

## 4. Tracking: the rig cannot render the swing faithfully as tuned today

Measured 2026-10-26 from the logged setpoint and encoder of 23 SSR recordings (P4 / I0.3 / D0.03):
|encoder/setpoint| = 1.01 at 1 Hz, **1.10 at 2 Hz, 1.26 at 3 Hz, ~2.0 at 5 Hz, resonance 6.35 Hz**
(coherence > 0.9 to 6.8 Hz). The pendulum fundamental (0.6–1.5 Hz) is fine; the spastic catch and
clonus (3–8 Hz) are over-rendered up to 2×. Two fixes, both needed:

1. **Re-tune for tracking, not settling** (front-panel gains; the guide's first run uses P 2 / I 0.1 / D 0).
2. **Iterative learning control** — because every scenario is repeated, correct the command from the
   encoder log of the previous run: `python -m neuromech.ilc CMD.csv RUN.bin` writes `<sid>_ilc1.csv`.
   On the identified plant it cuts swing tracking error 0.37° → 0.07° in four runs (self-test).
   Re-identify the plant after any gain change.

Regardless of tracking quality, **score the encoder, not the command**: the encoder trace is the
truth the phone is compared against; the command is only the target. Report *intended vs delivered*
parameters for every run.

## 5. Mode B — the model in the loop (host)

Paste `model_step_formula_node.txt` into a Formula Node inside the Player's loop and step it ten times
per 10 ms iteration (it advances 1 ms per call), writing the last angle. Outside the node:

- shift registers for the 6 states (`th w ts tr tf tc`) and 3 flags (`srs_on rev clon_t0`), initialised
  at release to `th = th0, w = omega0`, others 0, `srs_on = 1, rev = 0, clon_t0 = -1`;
- **three** ring buffers of length **52** (omega, T_SRS, theta). Each tick read
  `idx = (head-50) mod 52` → `wd, sd, thd` and `(head-51) mod 52` → `wd_prev, sd_prev`; after the
  node write `w_o, ts_o, th_o` at `head+1`. Initialise the theta buffer to `th0`, not 0, so the
  length drive reads zero stretch before release. (Length 52, not 51: with 51 the "one older" index
  wraps onto the newest sample and the reflex runs away — the bug found while building the Python
  twin. The theta buffer was added 2026-10-26 for the length drive `k_l`; a rig that never sets
  `k_l` can leave `thd` wired to `th0` and skip the third buffer.)
- host-computed constants from `Params`: `J = 0.446 s⁴`, `mgl = 4.96 s² · 9.81 · 0.256 s`
  (or the explicit `lc_m / m_kg / j_kgm2` triple when the limb comes from anthropometry —
  `model_rt.limb_from_anthropometry`), `inc = rad(incline)`,
  `clon_nm = rad(clonus_deg) · J · (2π clonus_hz)²`, `k_l_thr = rad(k_l_thr_deg)`,
  `k_f · gain_scale`, `k_v · gain_scale`, `k_l · gain_scale`,
  the four guard phases (printed in each test-vector header).
- setpoint = `rad2deg(th_o) − rest_deg`, written exactly where Mode A writes the file sample.

**Verify before use:** feed `test_vector_MAS2.csv` and `test_vector_all_modules.csv` (inputs per tick)
through the node and compare its outputs with the files' `*_o` columns. The same text, executed as
JavaScript by `verify_formula_node.js`, matches the Python twin to 1e-11 on every state for all 3 000
ticks, so any LabVIEW mismatch is a wiring error, not a model error.

Regenerate both vectors with `python neuromech/labview/make_test_vectors.py` after ANY change to
`model_rt.simulate`, then re-run the verifier. Their parameter sets are pinned in that script rather
than read from the calibrated ladder, so a parity failure can never be confused with a
recalibration. `test_vector_all_modules` exercises every branch including `coulomb_nm`, `k_v`, `k_l`
and `reflex_max_nm`.

Interactive variant (B′, PT training): add the measured interaction torque to `tau` (calibrated torque
channel, sign: + extends). This is admittance rendering, and it has to run in the 1 kHz RT loop: through
the host's 10 ms jittery loop the torque arrives 10–20 ms late, which destabilises any stiff virtual leg.
It therefore needs RT access the current arrangement does not give (§1).

## 6. Run order for a session

1. Power, zero (power OFF → hand to centre → ZeroAngle → Set Zero T&V → power ON → SET), limits.
2. **Chirp identification run** (`player/R000.csv`, 0.2 → 10 Hz, ±8°, 20 s): fit the 2nd-order plant
   (`python -m neuromech.ilc --identify`); refuse to continue if the resonance moved > 10 % from the last session.
3. **Plan it:** `python -m neuromech.session plan --name <S> --groups A,M --reps 3 --repeat A-P2:10 --chirp`
   writes a blind run sheet and one `player/<run>.csv` per run (R000 = chirp, then the P2 × 10 repeatability block, then the rest randomised).
4. **Per run:** check `peak_deg` → Player `CSV Path` = `player/<run>.csv` → start phone → start the
   log → run the Player → let the app auto-stop (≥ 6 s still) → stop phone → stop log → **write the
   log's file name in the run sheet's `encoder_file` column.**
5. `python -m neuromech.session score --name <S> --encoder-dir <logs> --phone-manifest <…>`.
6. After the session: `python -m neuromech.ilc` for any scenario whose delivered PT7 left its band;
   re-run those.

Acceptance per scenario (encoder-scored): delivered PT7 inside the intended MAS band, each of the 7
parameters within the bench MDC95, tracking RMSE < 1°. Phone validation then compares phone-scored
parameters against the **encoder-scored** ones, run by run.

## 7. Prerequisites and open decisions

| # | Item | Why it blocks | Owner action |
|---|---|---|---|
| 1 | **Angle range.** Clinical geometry releases at +55° (rest depth) and swings to −35°…−52° below rest; 52 of 55 scenarios exceed today's ±45° software limits | Without it the ladder cannot be played at full scale | Confirm the arm's *mechanical* range; set limits to ±65° if safe. Fallback: render at 0.75× amplitude (A0 ≈ 41°, still above the 25° gate; every ratio parameter is amplitude-invariant) |
| 2 | **Build the Pendulum Player** (host VI, no StretchExperiment edit) | Mode A does not exist without it | Follow the build guide; add the `Max abs setpoint` guard (§3); confirm the shared variable in DSM |
| 3 | **Logging under the Player** | No encoder log, no score | One bench check (§3, last paragraph) |
| 3b | **Re-tune + ILC** | 2× over-rendering above 4 Hz (§4); no feedforward path | Chirp run, gains on the front panel, ILC-corrected files |
| 4 | **Motor torque rating and gearbox** | Mode C needs ≥ 4 N·m for MAS 0–3, 15 N·m for severe, 26 N·m for real-size clonus | Read the ServoDisc model's continuous/peak torque; size a gearbox |
| 5 | **Horizontal plane** | Modes A/B validate the phone's gyro channel only; the app's fused angle needs gravity in-plane | Tilted/vertical rig (already planned) — Mode C is the end state |
| 6 | **Patient-derived presets** | The ladder is calibrated to the scorer's own MAS bands — fine for testing the phone against the encoder, *circular* for validating the grade map | Fit (tone, k_f, k_df, B, s) to recorded clinic trials with clinician MAS (MINT/CHAT + Monica's waveforms); use per-grade medians as presets |

## 8. Status of the companion code (2026-10-26)

| Piece | File | State |
|---|---|---|
| Published model (De Groote 2018), RK4, checked vs the authors' MATLAB | `neuromech/pendulum_sim.py` | 21 tests pass |
| LabVIEW-portable model + 9 behaviour modules | `neuromech/model_rt.py` | matches the reference within 0.34° (euler4) / 0.30° (rk4); 8 tests pass |
| Formula Node port | `labview/model_step_formula_node.txt` | verified to 1e-11 vs Python on 2 × 3 000 ticks |
| Scenario library (45) + expected scorer output | `scenarios.py`, `build.py` → `out/` | built; manifest + 1 kHz setpoint files |
| ILC tool | `ilc.py` | self-test converges; untested on hardware |
| Figures | `nm_figs.py` → `out/figures`, publication set | done |
| Session planner (Player files, blind run sheet) and scorer (pairs logs by run-sheet name) | `session.py` | dry-run verified with the Player's 100 Hz staircase modelled |
| Pendulum Player VI | build guide artifact | **to build** on the host (≈ 15 blocks); nothing to add on the RT target |
