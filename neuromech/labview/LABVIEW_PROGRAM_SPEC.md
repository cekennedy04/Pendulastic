# Knee-spasticity emulator — LabVIEW program specification

Builds on the existing **StretchExperiment** project (host PC + NI real-time target, 1 kHz loop,
position PID on the ServoDisc motor, 1 kHz encoder, 7-channel `.bin` logs). Everything here is an
*addition* to that program; nothing in the current Stretch / Fast-trial modes changes.

Companion code: `neuromech/` (Python twin, scenario library, ILC tool, figures). Status of each piece
is at the end of this document.

---

## 1. Three modes, built in order

| Mode | What runs on the RT target | What it can emulate | Needs | Build when |
|---|---|---|---|---|
| **A — Scenario player** | Plays a precomputed 1 kHz setpoint file through the existing position PID (+ feedforward) | Everything kinematic: all 45 scenarios, MAS ladder, behaviours, out-of-range | One new capability: a setpoint **array** input (the "set point input that can accept a sequence of values" already on the Notion wish-list) | **Now** — critical path |
| **B — Real-time model** | The Formula Node model (`model_step_formula_node.txt`) integrated every tick; its angle is the setpoint | Same as A, but parameters live on the front panel; with the torque sensor added to the model it becomes interactive (a PT can push the virtual leg — admittance rendering) | Mode A's feedforward; torque-sensor calibration for the interactive variant | After A is validated |
| **C — Torque mode on a real shank** | Motor in torque control supplies **only the active muscle torques** (tone, SRS, reflex, modules); gravity and inertia are physical | The most faithful: real pendulum + emulated muscle; phone sees true gravity, so the app's **fused** angle is gradable | Vertical mount, prosthetic shank + weights matched to J and mgl, gearbox, motor torque ≥ ~5 N·m continuous (MAS 0–3 needs 0.8–3.7 N·m peak; severe cases 15 N·m; real-size clonus 26 N·m) | With the tilted/vertical rig |

Mode A first because it is the smallest LabVIEW change, it is exactly reproducible (the command is a
file), and the encoder log makes every run self-scoring.

## 2. Scenario file (Mode A input)

`neuromech/out/setpoints/<sid>.csv`, one row per 1 ms tick:

| column | unit | meaning |
|---|---|---|
| `t_s` | s | time from file start |
| `setpoint_deg` | deg | rig angle command; **0 = where a passive leg hangs**, + = toward extension |
| `vel_dps` | deg/s | derivative of the command — velocity feedforward |
| `acc_dps2` | deg/s² | second derivative — acceleration feedforward |
| `phase` | 0/1/2 | 0 = sync pulses + lift, 1 = still hold at the release angle, 2 = released swing |

Every file starts with the shared preamble from `motor_reference.py` (3 × 5° sync pulses, 1 s still,
4 s minimum-jerk lift at ≤ 42 °/s — below the phone's 57 °/s release gate — then a 3 s hold), so the
phone↔encoder alignment and the app's release detection behave exactly as on the bench today.
The expected scorer output for every file is in `scenario_manifest.csv`.

## 3. RT target — Mode A block diagram

```
[before the Timed Loop]  read <sid>.csv (FTP'd to the target like the logs) -> 3 DBL arrays: sp, vel, acc
                         check: max|sp| <= soft limit, max|vel| <= 500 deg/s, length <= 60 000
[Timed Loop, 1 kHz]      k = loop iteration (clamped to last sample)
    e     = sp[k] - encoder_deg
    u_pid = existing PID(e)                          (unchanged block)
    u_ff  = Kv*vel[k] + Ka*acc[k]                    (NEW: feedforward, in the same units as u_pid)
    u     = clamp(u_pid + u_ff, Output Range)
    trips (any -> motor off, latch E-stop):
        |e| > 10 deg for > 50 ms                     tracking-error trip
        |encoder_deg| > scenario soft limit + 5 deg  range trip
        |encoder velocity| > 600 deg/s               speed trip
        sp[k] or u is NaN                            data trip
    log: time, position, velocity, position_setpoint = sp[k], torque,
         ch6 (was emg_fds) = phase[k], ch7 (was emg_edc) = scenario index
```

- **Feedforward gains.** `Ka ≈ J_arm` and `Kv ≈ b_arm` in controller-output units. Identify them from
  the chirp scenario (§6). With feedforward the PID only corrects the residual, which is what removes
  most of the 6.35 Hz resonance problem below.
- **Logging stays 7 channels** so `labview_bin_reader.py` reads it unchanged; the two EMG channels
  (unused on the bench) carry phase and scenario index. Name the file `<sid>_rep<n>.bin` — this alone
  removes the capture-order pairing problem that has cost the most time on the bench.

## 4. Tracking: the rig cannot render the swing faithfully as tuned today

Measured 2026-10-26 from the logged setpoint and encoder of 23 SSR recordings (P4 / I0.3 / D0.03):
|encoder/setpoint| = 1.01 at 1 Hz, **1.10 at 2 Hz, 1.26 at 3 Hz, ~2.0 at 5 Hz, resonance 6.35 Hz**
(coherence > 0.9 to 6.8 Hz). The pendulum fundamental (0.6–1.5 Hz) is fine; the spastic catch and
clonus (3–8 Hz) are over-rendered up to 2×. Two fixes, both needed:

1. **Re-tune for tracking, not settling**, then feedforward (§3).
2. **Iterative learning control** — because every scenario is repeated, correct the command from the
   encoder log of the previous run: `python -m neuromech.ilc CMD.csv RUN.bin` writes `<sid>_ilc1.csv`.
   On the identified plant it cuts swing tracking error 0.37° → 0.07° in four runs (self-test).
   Re-identify the plant after any gain change.

Regardless of tracking quality, **score the encoder, not the command**: the encoder trace is the
truth the phone is compared against; the command is only the target. Report *intended vs delivered*
parameters for every run.

## 5. Mode B — the model in the loop

Paste `model_step_formula_node.txt` into a Formula Node inside the Timed Loop. Outside the node:

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
- setpoint = `rad2deg(th_o) − rest_deg` into the Mode A path (feedforward from the model's own
  `w_o` and `dw`).

**Verify before use:** feed `test_vector_MAS2.csv` and `test_vector_all_modules.csv` (inputs per tick)
through the node and compare its outputs with the files' `*_o` columns. The same text, executed as
JavaScript by `verify_formula_node.js`, matches the Python twin to 1e-11 on every state for all 3 000
ticks, so any LabVIEW mismatch is a wiring error, not a model error.

Regenerate both vectors with `python neuromech/labview/make_test_vectors.py` after ANY change to
`model_rt.simulate`, then re-run the verifier. Their parameter sets are pinned in that script rather
than read from the calibrated ladder, so a parity failure can never be confused with a
recalibration. `test_vector_all_modules` exercises every branch including `coulomb_nm`, `k_v`, `k_l`
and `reflex_max_nm`.

Interactive variant (PT training): add the measured interaction torque to `tau` (calibrated torque
channel, sign: + extends). This is admittance rendering; it is stable while the virtual inertia
(0.26 kg·m² for the nominal leg) is large relative to the arm's own.

## 6. Run order for a session

1. Power, zero (power OFF → hand to centre → ZeroAngle → Set Zero T&V → power ON → SET), limits.
2. **Chirp identification run** (0.2 → 10 Hz, ±8°, 20 s): fit the 2nd-order plant and the feedforward
   gains; refuse to continue if the resonance moved > 10 % from the last session.
3. **Repeatability block:** `A-MAS2` × 10.
4. **Ladder:** `A-MAS0 … A-MAS3`, randomised, × 3. Then behaviours, out-of-range, artefacts.
5. Per run: start phone → start log → play file → let the app auto-stop (≥ 6 s still) → stop phone →
   stop log. The file name carries the scenario; no manual pairing.
6. After the session: `python -m neuromech.ilc` for any scenario whose delivered PT7 left its band;
   re-run those.

Acceptance per scenario (encoder-scored): delivered PT7 inside the intended MAS band, each of the 7
parameters within the bench MDC95, tracking RMSE < 1°. Phone validation then compares phone-scored
parameters against the **encoder-scored** ones, run by run.

## 7. Prerequisites and open decisions

| # | Item | Why it blocks | Owner action |
|---|---|---|---|
| 1 | **Angle range.** Clinical geometry releases at +55° (rest depth) and swings to −35°…−52° below rest; 42 of 45 scenarios exceed today's ±45° software limits | Without it the ladder cannot be played at full scale | Confirm the arm's *mechanical* range; set limits to ±65° if safe. Fallback: render at 0.75× amplitude (A0 ≈ 41°, still above the 25° gate; every ratio parameter is amplitude-invariant) |
| 2 | **Setpoint-array input** on the RT target | Mode A does not exist without it | Add a file-read + array-index path beside the Stretch profile generator |
| 3 | **Re-tune + feedforward** | 2× over-rendering above 4 Hz (§4) | Chirp run, then gains |
| 4 | **Motor torque rating and gearbox** | Mode C needs ≥ 4 N·m for MAS 0–3, 15 N·m for severe, 26 N·m for real-size clonus | Read the ServoDisc model's continuous/peak torque; size a gearbox |
| 5 | **Horizontal plane** | Modes A/B validate the phone's gyro channel only; the app's fused angle needs gravity in-plane | Tilted/vertical rig (already planned) — Mode C is the end state |
| 6 | **Patient-derived presets** | The ladder is calibrated to the scorer's own MAS bands — fine for testing the phone against the encoder, *circular* for validating the grade map | Fit (tone, k_f, k_df, B, s) to recorded clinic trials with clinician MAS (MINT/CHAT + Monica's waveforms); use per-grade medians as presets |

## 8. Status of the companion code (2026-10-26)

| Piece | File | State |
|---|---|---|
| Published model (De Groote 2018), RK4, checked vs the authors' MATLAB | `neuromech/pendulum_sim.py` | 21 tests pass |
| LabVIEW-portable model + 11 behaviour modules | `neuromech/model_rt.py` | matches the reference within 0.34° (euler4) / 0.30° (rk4); bit-identical to the pre-2026-10-26 model at every parameter set that predates it |
| Formula Node port | `labview/model_step_formula_node.txt` | verified to 1e-11 vs Python on 2 × 3 000 ticks |
| Test-vector generator | `labview/make_test_vectors.py` | added 2026-10-26; the vectors had no generator before |
| Scenario library + expected scorer output | `scenarios.py`, `build.py` → `out/` | built; manifest + 1 kHz setpoint files. `out/` is gitignored — regenerate, never trust a committed copy |
| ILC tool | `ilc.py` | self-test converges; untested on hardware |
| Figures | `nm_figs.py` → `out/figures`, publication set | needs an untracked `bench_figures/pubstyle`; not exercised by the build |
| LabVIEW VIs | — | **not built** (this spec) |
