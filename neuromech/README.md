# neuromech — the knee-spasticity emulator program

Plays programmed pendulum-test swings on the LabVIEW motor rig, then scores every run from the encoder
(truth) and the phone, through the validated production scorer. Plan:
`docs/plans/2026-10-26-001-plan-neuromech-emulator-unified.md`. LabVIEW side:
`neuromech/labview/LABVIEW_PROGRAM_SPEC.md`.

Run everything from the repo root with the project venv (`.venv/Scripts/python.exe`), with
`PYTHONIOENCODING=utf-8` and `OPENBLAS_NUM_THREADS=1`.

## 1. Build the scenario library (once, or after a model change)

```bash
python -m neuromech.build                # 64 scenarios -> neuromech/out/ (manifest + 1 kHz setpoints)
```

| Group | What | Source |
|---|---|---|
| A | PT7-band presets P0–P3 (not MAS settings — plan D5) | De Groote core, calibrated to band centres |
| S | the published severity axis, including where PT7 inverts | De Groote core |
| B | behaviours: flexor/extensor, clasp-knife, clonus, rigidity, contracture, hypotonia, guarding, spasm, habituation | De Groote core + modules |
| C | each of the 7 parameters and the A0 gate pushed out of range | De Groote core + kinematic transforms |
| D | release artefacts: push, wobble, short record, low lift | De Groote core |
| M | severity AND kind of severity: tone ladder, catch-and-hold, no-ringdown fall, end-stop (the catch-release copy of `B-claspknife` was dropped, O5) | De Groote core, via `profiles.py` |
| R | the four mechanisms B9 ported, one at a time: bearing friction, anthropometry, tonic arrest, reflex channel (the force channel is `S-lam1`) | De Groote core |
| H | HESS's four haptic models (Park et al. 2012, Table III), elbow values unscaled: the three-phase catch on a pendulum | De Groote core + `hess_*` module |
| K | HESS HM#1–2 on the knee: H alone refitted so each holds its intended MAS 1 / 1+ (HM#3's MAS 2 and HM#4's MAS 3 exist only in a sliver below the arrest jump) | De Groote core + `hess_*` module |

`out/scenario_manifest.csv` holds, per scenario, the expected 7 parameters, PT7, band or refusal, flags,
the rig range and speed it needs, the peak active muscle torque (for Mode C), and its distance to the nearest
PT7 band edge (`edge_dist_pt7`, `near_edge`).

**Edge check (owner decision O5).** A preset within 0.03 PT7 of a band edge (`build.EDGE_MARGIN_PT7`) is one
rig's tracking error or one scorer revision from the next band. The build prints every such scenario and
**exits 1** if any is not in `scenarios.NEAR_EDGE_ALLOWED` (or an allowlist entry names a scenario that is not
built); the library is still written. Presets meant to sit in a band are refitted to its middle with
`build.fit_param` (the ladder's bisection, on one other parameter) and listed in `scenarios.BAND_MIDDLE`;
scenarios whose purpose is the edge or an out-of-range parameter (S axis, C, R, HESS's unscaled values) are
allowlisted, each with its reason. `--allow-near-edge` builds anyway. On `pt5trough-ref-2026-11-16`
(2026-11-17, 64 scenarios): nothing flagged, 8 allowlisted (`S-lam1`, `S-lam6`, `C-f-low`, `C-f-high`,
`R-friction`, `R-reflex-vel`, `R-reflex-len`, `H-HM3`).

**Duplicate check (owner decision, 2026-11-17).** The build also **exits 1** if two scenarios render the same
swing under different names: identical Params, release and kinematic transforms (`scenarios.definition_key`;
Params alone would not do, since the C and D probes share the default Params on purpose and differ only by
their transform or release). `C-wmax-low` (= `C-f-low`) and `R-reflex-force` (= `S-lam1`, the published
force-feedback channel `severity(1)`) were dropped this way, so the library went from 66 to 64. The probe-group
originals were kept: `C-f-low` now probes both f and omega_max (one 2x-slower swing), and `S-lam1` is the R
group's force channel that `R-reflex-vel` and `R-reflex-len` compare against. Sets that are one swing on
purpose are listed in `scenarios.DUPLICATE_ALLOWED` with the reason, matched as exact sets: `A-P1` =
`B-extensor` = `B-habit1` (B's band-2 rung falls back to the mid rung while band 2 is unreachable),
`B-leadpipe` = `M-fall_no_ringdown` and `B-contracture` = `M-contracture_stop` (the M profiles are defined
independently in `profiles.py`).

**Intent report (owner decision, 2026-11-17; report only).** For every scenario with a severity intent
(`Scenario.intent`, from `profiles.SEVERITY_INTENT`, whole MAS grades) the build prints the intended band and
the band its PT7 lands in, and lists each mismatch. It never changes the exit status. The known mismatches are
**accepted** (`scenarios.INTENT_MISMATCH_ACCEPTED`), and anything else prints as `MISMATCH`. On
`pt5trough-ref-2026-11-16`: `M-tone_L1` band 0 (intent 1, PT7 0.076), `M-tone_L2` band 1 (intent 2, 0.190),
`M-tone_L3` and `M-tone_L4` band 1+ (intents 3 and 4, 0.344 and 0.404). The tone ladder is not refitted: its
lambdas are literal on purpose (`profiles._LADDER_LAMBDA`, a fixed library for testing recalibrations), and
intents 2 to 4 are above the axis's peak (0.4035). `M-catch_and_hold_140` and `_127` (intents 3 and 2) are
refused (no oscillation), which is the refusal path they probe.

### Findings

- **Rig bearing friction alone sits just under MAS 1 (2026-11-17, scorer `pt5trough-ref-2026-11-16`, core
  0.4.0).** `R-friction` (the rig's own dry bearing friction, `rig_params()`, no simulated spasticity) scores
  PT7 0.1144, 0.0056 below the 0/1 edge (0.12); `R-friction-heavy` (0.4 N.m) scores 0.1748, band 1.
  Implication: on a real rig with friction, healthy-ish presets could be pushed toward MAS 1 by the hardware
  alone. To be checked on the real rig. `R-friction` stays in `NEAR_EDGE_ALLOWED`, recorded there as a finding,
  not a preset to refit.

## 2. Plan a session

```bash
python -m neuromech.session plan --name S2026-10-28 --groups A,M --reps 3 --repeat A-P1p:10 --chirp
```

Writes `neuromech/sessions/<name>/`: `runsheet.csv` (what the operator sees: run ids only, R3 blind,
with each run's `peak_deg` and an `encoder_file` column to fill in), `key.csv` (run -> scenario; keep it
from anyone grading), `player/<run>.csv` (what the Pendulum Player VI plays: `t_s, setpoint_deg`, no
header, starting at 0 deg) and `commands/<run>.csv` + `.dbl` (all five columns, for scoring and ILC).
`R000` is the 20 s identification chirp.

## 3. At the bench (per run)

The rig is driven by the **Pendulum Player**, which writes StretchExperiment's position-setpoint
shared variable; StretchExperiment itself is not edited (see `labview/LABVIEW_PROGRAM_SPEC.md`).
The Player does not have to be a VI: `labview/player/play_setpoints.ps1` does the same job from Windows
PowerShell through NI DataSocket (installed with LabVIEW), so the rig PC needs neither Python nor any
LabVIEW building. The analysis PC never touches the rig, so a session travels on a USB stick:

```bash
python -m neuromech.usb_kit --name <S> [--dest E:\]     # scripts + player files + run sheet + README.txt
```

On the rig PC: `PICK_VARIABLE.bat` once, then drag a file onto `DRYRUN.bat` (moves nothing) or
`PLAY.bat`. q/Esc ramps back to 0; each run leaves `sent/<run>_<time>.csv`, what it wrote and when.
Logs and the run sheet come back on the stick. The kit's `README.txt` is the bench procedure.
`key.csv` never goes on the stick.

**Playback safety guard** (owner decisions 2026-11-17). Both players — `play_setpoints.ps1` and the
Pendulum Designer — take their rules from one file, `labview/player/RigGuard.cs` (PLAY loads it with
`Add-Type`, the Designer compiles it in; `neuromech/rig_guard.py` is the Python twin, and
`tests/test_rig_guard.py` requires all three to give the same verdict on the same fixture files):

- **Hard limit ±40° rig angle**, compiled in. `-MaxDeg` / the Designer's *Max |angle|* can only lower it
  (`-MaxDeg 45` is refused; the Designer's control stops at 40).
- **Refused at load, whole, never clipped** (the message names the time and value): a header, a
  non-number, NaN/Inf, a row without exactly two columns (`0,0,99` or a decimal-comma file), time not
  starting at 0 or not increasing, a first angle other than 0, any |angle| over the limit, any change
  over **6° within 10 ms (600 °/s)**, or a **predicted delivered peak** over the limit. The players
  read the file at whole milliseconds of their clock, nominally every 10 ms, but a late tick puts a
  write on any millisecond; so the speed rule compares every pair of milliseconds up to 10 ms apart,
  not just the 0/10/20 ms grid (which a 5° 50 Hz wiggle or a 1 ms 30° spike slips through), and the
  prediction runs on all ten 1 ms phases of the write grid. The library's 5° sync pulses are 5° in
  one write (500 °/s) and pass. A UTF-8 BOM is accepted by all three (both players and `rig_guard`).
- **Predicted delivered peak**: the 10 ms staircase (plus the 2 s end hold) through the identified
  closed loop (section 4 of the LabVIEW spec: 2nd order, 6.35 Hz, ζ 0.23; gain 1.26 at 3 Hz, ~2.0 at
  5 Hz) and five detuned copies (ωn ×0.9 and ×1.1, ζ 0.18); the largest |angle| of the six, worst
  phase. It is a model of the rig as identified on 2026-10-26 with its P/I/D at the identified values,
  not a guarantee: re-identify after any gain change. Saturation and integrator windup (a low
  Controller Output Range) are not modelled.
- **Designer, live designs only**: a design over the limit is offered the largest rig amplitude scale
  (0.01 steps) that passes with the predicted peak ≤ limit − 1°, rescored at that scale; it plays only
  on an explicit Yes, and the score shown and logged is the scaled swing's. The default scale is
  `Knobs.DefaultScale` = **0.70** (owner, 2026-11-17), so the default MAS designs (55° release → 38.5°)
  play without the prompt. An opened `.csv` over the limit is refused, not offered a scale (scaling a
  file would scale its sync pulses and leave its session's intended scores behind).
- **Per-write backstop** in both send paths: NaN/Inf never sent, clamp to ±limit, at most 6° from the
  last value the rig **took** (a write that returned true) and from the last value tried, so a run of
  failed writes followed by a success can never jump the rig. If it ever engages while playing, the run stops and ramps to 0 at 10 °/s (PLAY exits 5,
  the Designer shows "STOPPED by the guard"); a guard row is logged. On a fast file a Windows tick
  late by tens of ms can trip it (the library's swings move at most 7° per write, typically ≤ 3°).
- **Ramp-in**: if the current setpoint cannot be read (or reads NaN or beyond the limit) the player
  refuses to start: no step to 0 from wherever the arm is. PLAY reads it again right after Enter and
  refuses if that fails or it moved more than 0.5° while the prompt waited; it ramps from the fresh
  value. The Designer reads it after its confirm box, when the play starts.
- **Returns to 0**: STOP / q / Esc / Ctrl+C, a guard or watchdog stop, and the end of a run all ramp the
  setpoint to 0 at the return speed (10 °/s; PLAY's `-ReturnDps` takes 1–60). Every ramp is
  write-indexed: each write is one step (return speed × 10 ms) from the last value the rig took, so a
  failed or late write is retried, never jumped over; it runs until the rig has taken the target, for at
  most twice its nominal time + 5 s. After the warm-up write (the value already held) the player waits one
  tick before the first play write. STOP or
  q during the ramp-in cancels the play that would follow. PLAY catches Ctrl+C (as a keystroke and as
  a console signal; the signal path is tested with Ctrl+C-as-input off, the case its handler is for) from
  its Enter prompt to the end and treats it like q; **Ctrl+Break and closing the
  window are not caught**. The write period is fixed at 10 ms (no `-PeriodMs`), and there is no
  `-NoReturn`: PLAY refuses any option it does not know.
- **Errors return to 0 first**: **any** failed write during play or the end hold ends the play at once
  (the rig did not get that value); so does any exception after the first write. Each starts a guarded
  ramp to 0 (through the backstop, from the last value the rig took, tolerating failed writes, bounded
  in time) before the lock is released; the outcome is logged (failed writes carry `write failed: ...`
  in the note, `error` rows, sent log `*_error.csv`, PLAY exit 4). Tested with writes 60–79 failing
  mid-rise of a 35° swing: the largest step between values the rig took stays ≤ 6°. If the writes
  cannot get through, both players say **RIG HOLDING LAST SETPOINT -- USE THE E-STOP** (the Designer in
  a message box) and leave `sent/HOLDING.flag`. The next start of either player shows it and plays only
  after an explicit acknowledgement (PLAY: type `YES`; the Designer: a Yes/No box, default No), which
  deletes it; a dry run ignores it. (The writer lock itself needs no clean-up: Windows releases it when
  the process exits.)
- **Logged**: every refusal leaves `sent/<file>_<time>_refused.csv`; backstop events are `guard` rows
  in the sent log (`t_wall_s,t_file_s,phase,setpoint_deg,note`). `session check-log ... --sent <sent log>`
  fails a run with any refusal, guard or error row. `session plan` writes `guard.csv` (each run's
  verdict and predicted peak) and warns how many runs the players will refuse;
  `python -m neuromech.rig_guard <files|dir> [--index out.csv]` / `--library` does the same anywhere.
- **One writer at a time**: a machine-wide named mutex (`Global\PendulasticSetpointWriter`, never a
  per-session `Local\` fallback, which would let two players write) is taken before the ramp-in and
  released after the return to 0; a second player (Designer or PLAY) refuses to start, and so does a
  player that cannot open the lock (e.g. the other one runs as administrator). Tested in both
  directions. It does **not** cover LabVIEW's own writers: no Manual Control or Stretch profile while
  a player waits or plays.
- **Optional encoder watchdog**: if the operator picks a variable holding the arm's ACTUAL position
  (Designer *Encoder: Pick...*, or `PICK_ENCODER.bat` → `encoder_url.txt`), the player reads it every
  5th write (50 ms, 20 ms read timeout) and stops the content and ramps the setpoint to 0 (PLAY exit 6)
  when |actual| > limit + 3°, when the arm is further from every setpoint sent in the last 150 ms than
  5° + 1.5 × the following error the identified plant (and its detuned copies) predicts for this file
  (the 150 ms window absorbs network/read latency and the plant's lag; 5° covers encoder noise,
  stiction and the 0.4–0.6° tracking RMSE measured on library swings), or after 5 failed reads in a row
  (≈ 250 ms).
  Before starting it refuses if the variable cannot be read, reads more than 5° from the setpoint
  (wrong variable), or is the setpoint variable itself. The +3° is a software stop at 43° actual, not a
  cutoff; it is there to avoid false stops (the mechanical hard stops are the last line). Each look is
  a synchronous read of up to 20 ms inside the write loop, so a slow read delays that tick's write
  (and a fast file then steps further, which the backstop may stop). **Follow-up, not done:** read the
  encoder asynchronously (its own thread) so a slow read never delays a write. Not picked: play proceeds and the
  console / status bar says **"encoder watchdog OFF"**. **Unconfirmed:** nothing in this repo lists an actual-position network variable in
  `variables - network - RT (separate)` (only the setpoint `M1JointAngleDesired`, fallback
  `M1AngleSetPoint`); check in Distributed System Manager whether one exists before relying on it. The
  watchdog runs in the same host process as the player: a second opinion that moves the setpoint, not
  an independent hardware cutoff.
- **Not covered — the E-stop is the answer**: if writes cannot get through, the rig HOLDS the last
  value it received; a player killed mid-play (closed window, Ctrl+Break, crash) also leaves the last
  setpoint held.

**Staged first real playback** (owner, 2026-11-17; the kit's `RIG_CHECKLIST.txt`, from
`designer/RIG_CHECKLIST.txt`, is the operator's copy):

0. Before any playback, note the arm's mechanical hard-stop angles. Confirm that the ±40° limit plus
   the expected overshoot (the predicted delivered peak each player shows; and faults have carried
   the arm to +63°/−81° with the Angle Limits at ±25°) leaves a clear margin to both. If it does not,
   lower the limit for the session (Designer *Max |angle|*, or `-MaxDeg`).
1. Nobody inside the arm's swing envelope; a person with the E-stop in hand; StretchExperiment's
   Controller Output Range set conservatively during playback (it also changes the plant: the
   overshoot prediction does not model saturation); P/I/D at the identified values (the prediction is
   only valid for that loop); arm at 0. No Manual Control or Stretch profile while a player waits or
   plays. Stop with STOP / q / Esc, never by closing the window or Ctrl+Break.
2. 5° smoke file (`smoke_5deg.csv`, DRYRUN first) → a default MAS design at rig amplitude scale 0.3 →
   0.5 → 0.70 (the default). Stop at the first surprise and bring `sent\` back.

At ±40° the built library (64 scenarios) has 3 playable files at 1:1 (C-A0-low, C-A0-small-ok,
D-lowlift): every other scenario releases from 55° or more (the library writes rig angle 1:1).

**Once, before the first session:** play any player file with the log running, then

```bash
python -m neuromech.session check-log --bin <the new .bin> --player sessions/<S>/player/R001.csv [--sent <its sent log>]
```

PASS means the log recorded the Player's setpoint for the whole file (it also reports how often the
setpoint changed, ~10 ms for the Player). FAIL says which: the log never saw the Player's writes, it
logged something else, or it stopped early. Exit status 1 on FAIL.

Check `peak_deg` against the arm's range -> Player `CSV Path` = `player/<run>.csv` -> start the phone ->
start the log -> run the Player (or drag the file onto `PLAY.bat`) -> let the app auto-stop -> stop the phone -> stop the log -> **write the
log's file name in `encoder_file`** (StretchExperiment names its own logs; that column is the pairing).

## 4. Score the session

```bash
python -m neuromech.session score --name S2026-10-28 --encoder-dir <folder of the logs> \
       --phone-manifest <phone session>-manifest.json
```

Per run: encoder-delivered vs intended parameters and band, tracking RMSE, and the phone's parameters
(aligned on the three sync pulses, mounting sign detected, gyro bias taken from the stillness before
release — what the app itself can see). `R000` reports the identified plant, and that plant is what every
run's tracking is judged against (below).
Output: `sessions/<name>/report.csv` and a printed summary.

Each trace is scored from the release of the **delivered** motion, not the command's. The arm leaves the
hold later than the command (Player hold + plant), and the scorer counts a hold left inside the record as
half an oscillation. The delivered release is measured per run, separately on the encoder and on the
phone, by matching the first 2° of the command's fall to the recording, and is written to `report.csv`:
`cmd_release_s`, `enc_release_s`, `enc_release_lag_ms`, `enc_release_resid_deg`, `enc_hold_rms_deg`,
`enc_rival_ratio`, the same for the phone, `ph_minus_enc_lag_ms` (the phone clock's offset at the release)
and `ph_expected_offset_ms`. Design and evidence: `docs/reports/2026-11-11-neuromech-release-alignment.md`
(the section "After review" describes everything below; section B the chirp-derived tracking limit, and
section C what its review changed).

**What gets flagged.** A flagged trace gets no score (there is no fallback to the commanded release), the
reason is in `status` / `phone_status`, and `score` exits with status 1. The printed headline counts
flagged runs in its denominator ("7 of 9 runs (78%); scored 7, release flagged 1, tracking flagged 1").

| `status` | what it means | at the bench |
|---|---|---|
| `release flagged: the onset does not match the command's` | the delivery is not the command (kick-back, slack, wrong file) | look at the log around the release; rerun |
| `release flagged: release uncertain: the hold is not still` | the arm moved more than 0.20° rms in the 0.3 s before the release (sag, ringing, a wobbling hold) | let the arm settle, check the hold gains, rerun |
| `release flagged: release uncertain: a release 10 ms or more away fits ...` | two candidate releases; nothing in the data says which | rerun |
| `release flagged: lag ... from the session median ...: this run, not the rig` | this run's lag is more than 12 ms from the other runs' | this run is the odd one (stiction, a lurch, a late start): rerun it |
| `release flagged: session lag finding` | see below | see below |
| `tracking flagged: tracking error ... times the ... the plant identified from the chirp predicts` | more error than the fitted model predicts for this command: lag-aligned RMSE ÷ the command's rms speed (`tracking_time_err_ms`) is over √((1.15 × `tracking_pred_ms`)² + 4²) ms. That is a statement about the run against the model, not yet evidence about the arm (see the mismatch table below). The scores stay in the report but the run is not a delivery of its scenario. If the sentence says "the margin being the session's median ratio", the session has a MODEL MISMATCH FINDING and this run stands out from it | look at the run; run ILC on it (section 5) or rerun |
| `tracking flagged: the release is ... ms from where the plant identified from the chirp puts it` | the arm left the hold more than 4 ms earlier or later than this rig's plant does with the setpoint the log recorded (`enc_lag_minus_pred_ms`). Late: stiction or a slow start. Early: a lurch, or something pushing the arm. This check sees a fault that is in every run | find the cause at the rig; a rerun will not change it if every run has it |
| `tracking flagged: the arm held X of the setpoint before the release and settled at Y ...` | the hold gain (`enc_hold_gain`) and the gain after the swing (`enc_late_gain`) are more than 2.5 % apart: the arm held somewhere it does not settle, so the plant does not describe this run's hold, and its tracking error and release cannot be trusted either way. Both gains are taken about the encoder's zero measured in the idle log before the command (`enc_idle_offset_deg`) | compare `enc_hold_gain` and `enc_late_gain` across the session's runs. Every run shows it: it is the rig (a slow mode the chirp does not see, or a zero offset the idle log did not catch), and rerunning will not help. One run: it was pushed or pulled at the hold, so rerun it. Either way check the encoder's zero against the setpoint's (`enc_idle_offset_deg`, and idle position vs setpoint by hand) |
| `tracking flagged: tracking error ... is over the limit of 25 ms` | the session has no usable chirp (or `--tracking-limit-ms` was given), so the run's tracking error was held to one fixed number | plan with `--chirp`; see below |
| `scoring error` | the scorer raised on the trace | read the message |

`score` also exits with status 1 when any trace ends in an error and so has no score: an encoder
`scoring error`, or a phone `scoring error` or `release error`. They are listed under "NOT SCORED BECAUSE OF
AN ERROR".

**The tracking limit comes from the session's own chirp.** With `R000` in the session (`plan --chirp`),
`score` identifies the plant from it and, for every run, passes that run's command through the identified
plant: `tracking_pred_ms` is the tracking error the plant itself produces on that command, and the run's
limit (`tracking_limit_ms`) is √((1.15 × predicted)² + 4²) ms. The 4 ms is error the prediction does not
contain (the Player's 100 Hz staircase, encoder noise); it is added in quadrature because it does not go away
when the prediction is large. The model has no gain term, so the prediction is scaled by what the arm held
before the release over what it was told to hold (`enc_hold_gain`), as long as that is within 5 % of 1 and the
run shows the same gain, within 1 %, once the swing has settled (`enc_late_gain`: the arm against the plant driven by
the logged setpoint, from 2.5 s after the release; a hold that relaxes after the release is not a gain). The gain
used is `enc_gain_applied` (1.0 when not applied). A run whose two gains are more than 2.5 % apart held somewhere
it does not settle (a hold pushed 3 to 4 % past the setpoint that relaxes after the release, say). Not scaling
its prediction is not enough: the relaxing hold moves the release and the tracking error on its own, and 5 ms of
stiction on such a run went unflagged in 110 to 121 of 132 runs. The run is tracking flagged for its hold
("the arm held 1.040 of the setpoint before the release and settled at 1.000 …"): 114 of 132 such runs. The
other 18 swing too little after 2.5 s to measure a late gain, and there the hold gain is neither applied nor
judged (which also costs static gains of 0.98 and 0.97 six and eight flagged runs of 132). The cost: a rig with a slow mode
the chirp cannot see does this by itself, and a 0.3 Hz dipole of −3 % has 27 of 66 good runs flagged for it. The
second check is on the release: the identified plant is driven by the setpoint the log recorded and its
release is compared with the arm's (`enc_release_lag_pred_ms`, `enc_lag_minus_pred_ms`, limit 4 ms).

`report.csv` says what a session was judged by, on every row: `tracking_limit_source` (`plant`, `fixed` or
`override`), `tracking_limit_note`, `tracking_margin` and `tracking_margin_source` (`default` or `operator`),
`release_check` (`plant` or `none`), `tracking_session_ratio` and `tracking_session_ratio_runs`,
`model_mismatch_finding`, `chirp_suspect`. `R000`'s row carries the identified plant (`plant_wn_hz`,
`plant_zeta`, `plant_delay_ms`, `plant_r2`), `plant_misfit` (the share of the chirp's own motion the model does
not reproduce), `plant_trusted` (True when the plant is used) and `plant_untrusted_why` (the reason when it is
not).

- **No chirp** (not planned, or `R000` has no log): every run is held to the fixed 25 ms, there is no release
  check against a plant, and the output says `TRACKING LIMIT IS NOT PLANT-DERIVED`. That number belongs to the
  simulated plant: on a slower rig it flags good runs, on a faster or ILC-corrected rig it misses sluggish
  starts, and it cannot see a fault that is in every run. Plan real sessions with `--chirp`.
- **A chirp the model cannot reproduce** (fit R² under 0.95, or `plant_misfit` over 10 %), or one that cannot
  be identified at all: the plant is not used, the runs fall back to the fixed 25 ms, `score` prints a
  `CHIRP FINDING` and exits 1 even if every run is clean. It means coarse stiction or a deadband, or a drive
  that saturates on the chirp (8° at 10 Hz is 500°/s). Look at `R000` first.
- **Two knobs, and neither turns the release check off.** `--tracking-margin M` replaces the 1.15.
  `--tracking-limit-ms N` replaces the plant-derived tracking limit with N for every run (`TRACKING LIMIT
  OVERRIDE USED`). With a trusted chirp the release is checked against the plant either way, so a half-speed
  start or a forward lurch in every run is still flagged. Both are printed and recorded.

**A model mismatch finding.** measured ÷ predicted is 1.0 when the identified plant describes the runs. If the
session's runs agree with each other on a ratio that is not 1 (at least 3 scored runs predicted at 7 ms or
more whose release passed the check, two thirds of them within 15 % of their median, and that median outside
0.92 to 1.08), that is a statement about the model, or about something every run shares, and not about any
one run. `score` prints **one** `MODEL MISMATCH FINDING` in place of a tracking flag per run, holds each run to
the session's ratio (1.15 × the median) so that a run which stands out from the session is still the one
flagged, and exits 1. A ratio that is consistently **low** raises the same finding and tightens each run's
limit: against a prediction that is too large, small faults pass. Do this:

1. Play `R000` again and re-score. A chirp taken at other gains, or before the rig warmed up, is the cheapest
   explanation.
2. Compare `enc_hold_gain` with 1 and the gains with the last session's. Remember that a small fault in every
   run looks the same from here: 5 ms of stiction or a start at 80 % speed in every run gives this finding
   (session ratio about 1.2), not a flag per run.
3. **Do not make the session pass with its own ratio.** The margin a finding prints (1.15 × this session's
   median) is the margin that makes *this* session pass, whatever caused it, and a fault every run shares looks
   exactly like a model mismatch. In simulation, scoring a faulty session with the margin it printed itself let
   5 ms of stiction in every run exit 0 in 58 to 84 % of sessions, a start at 85 % speed in 40 to 100 %, and a
   40 ms Player stall in 25 to 75 % (re-review 2026-11-16, R.3); a margin taken from an earlier good session
   caught all of them. Accept a margin only from a session you trust on other grounds (a fresh chirp with the
   rig cold and warm, a known-good reference session) and carry it forward:
   `python -m neuromech.session score ... --tracking-margin 1.32`. **It is not stored**: pass it on every later
   session. It is recorded in `report.csv`, and a later session that moves away from the accepted ratio in
   either direction gets the finding again.

**What a tracking flag against the plant is worth, so far.** In simulation only, on rigs the fitted model does
not describe. The table is from the independent review of 2026-11-14
(`docs/reports/2026-11-14-neuromech-chirp-tracking-limit-review.md`, branch `docs/chirp-tracking-limit-review`),
re-measured here on the reviewer's rig models; **the magnitudes are the reviewer's guesses about a rig nobody
has measured**. Good runs only, 132 per row, the chirp through the same rig unless stated. "Before" is the
share of good runs flagged when each run was held to max(1.15 × predicted, 4 ms) on its own; "now" is the
share still flagged individually (tracking + release check) in sessions of 20 runs, and how many of those
sessions carry the one finding.

| rig | good runs flagged before | flagged now | sessions with the finding (median ratio) |
|---|---|---|---|
| arm settles at 0.995 / 0.99 / 0.98 / 0.97 of the setpoint | 10 / 22 / 38 / 61 % | 0 / 0 / 5 / 6 % (0, 0, 6 and 8 of 132 runs: the runs that swing too little after 2.5 s for a late gain, where the hold gain is not applied) | none (absorbed by `enc_hold_gain` where the late gain confirms it) |
| slow dipole at 0.3 Hz, mid-band gain +1 / +2 / +3 % | 6 / 65 / 96 % | 0.2 / 5 / 9 % | 70 / 100 / 100 % (1.09 / 1.17 / 1.26) |
| slow dipole at 0.1 Hz, +2 % ; −5 % | 69 % ; 84 % | 15 % ; **53 %** | 99 % ; 41 % (the runs do not agree: not covered) |
| loop 10 / 20 % softer at the 55° hold than at the chirp's 0° | 5 / 88 % | 0.1 % / **42 %** (release check) | 95 / 100 % (1.10 / 1.20) |
| Coulomb friction worth 0.1 / 0.2° of loop error | 4 / 43 % | 3 % / **22 %** (release check) | 10 / 100 % |
| loop deadband 0.05 / 0.1° | 6 / 71 % | 0 / **23 %** (release check) | 63 / 100 % (1.09 / 1.16) |
| encoder noise 0.1° rms: 6.35 Hz ; 8.3 Hz ; 12 Hz ζ 0.7 ; 6.35 Hz ζ 0.5 | 0 ; 10 ; 11 ; 43 % | 0 ; 2 ; **11** ; 8 % | 0 ; 3 ; 0 ; 0 % |
| encoder quantised to 0.36°: 6.35 Hz ; 8.3 Hz ; 12 Hz ζ 0.7 | 1 ; 9 ; 28 % | 2 ; 6 ; **25 %** | 2 ; 27 ; 0 % |
| chirp recorded with bandwidth ×1.10 ; damping ×1.2 | 43 % ; 8 % | 0 ; 0 | 100 % (1.14 ; 1.13) |
| chirp recorded with bandwidth ×0.90 (quiet direction) | 0 | 0 | 100 % (0.88) |
| extra pole at 10 Hz ; 0.2 × mode at 25 Hz (quiet direction) | 0 | 0 | 100 % (0.83 ; 0.78) |
| several mild departures at once (the review's "moderate" composite) | 95 % | 1 % + **11 %** (release check) | 100 % (1.27) |

Apart from its first row, the table predates the late-gain gate and the hold check (2.5 % between
`enc_hold_gain` and `enc_late_gain`). Per run, on 66 scenarios, the hold check adds 27 flagged good runs of 66 on
a 0.3 Hz dipole of −3 % (none before), 9 on a 0.1 Hz dipole of −5 % (53 to 62), 3 on a 1 Hz dipole of −5 %, at
most 2 on each other rig tried, and none on a clean rig or a static gain. Both gains are taken about the
encoder's zero as the idle log before the command shows it (`enc_idle_offset_deg`, the median of position minus
setpoint over the last 0.5 s before the command, taken only if the arm was still there: the medians of
the two halves within 0.1° and the rms about the median within 0.2°. A drifting idle, or one before the controller
has the arm, gives NaN, and the gains are then taken through zero). Without that, a constant encoder offset of 0.2° hold-flagged 10
good runs of 66 and 0.5° hold-flagged 27. With it, none at 0.2, 0.5 or 1.0°.

Not covered, in bold above: nonlinearity strong enough to move the release itself (the release check then
blames good runs: 20 % softening, 0.2° of friction, a 0.1° deadband, a loop gain that rises 20 % with error:
95 %), a rig where the prediction is under 7 ms and noise or quantisation is what is measured, and a mismatch
on which the runs do not agree with each other. **Where predictions are under 7 ms there is no session ratio at
all**: a fault every run shares that the release check misses passes at the per-run rate (on the 12 Hz, ζ 0.7
rig 25 to 100 % of 20-run sessions exit 0, re-review R.3), and encoder noise or resolution can still flag good
runs (0.1° rms noise: 10 % of runs; a 0.36° encoder: 24 %); measure `enc_hold_rms_deg` on the first real log
and, if it is 0.1° or more or the encoder steps are about 0.36°, derive the floor from it. **Measure the zero
too**: on the first real log compare the idle position with the setpoint (`enc_idle_offset_deg`, and by hand). A
constant encoder offset still moves the release and the tracking error, even though the gains no longer see it:
of 66 good runs, 8 are flagged at 0.2°, 43 at 0.5° (8 and 42 before the hold check) and 57 at
1.0°. A log that starts with the command has no idle segment, and there the gains are taken through zero.

What this does not catch, measured in simulation (report, sections B and C): a start at 90 % speed, a 1°
lurch and 2 ms of stiction pass. A small fault in every run that the release check cannot see (5 ms of
stiction, a start at 80 %) is no longer a flag per run but the one model mismatch finding. On a fast,
well-damped rig (11 to 12 Hz) 5 ms of stiction in every run passes entirely in about a quarter of 20-run
sessions, and on a rig with fast dynamics the model lacks (an extra pole at 10 Hz) it cancels the low ratio and
passes in all of them. Faults that are in the **setpoint** are invisible to the release check by construction (the model is
driven by the logged setpoint), so only the tracking ratio sees them: a Player that writes nothing for 20 ms at
the release passes (40 ms does not), a Player clock running 1 % slow lowers the tracking error on most runs
(16 % of runs flagged, the low finding in half of sessions), and a 3° setpoint lurch is flagged on about 60 %
of runs. Relaxing the margin relaxes these.

**If the chirp is the suspect.** A drive that only just fails to follow the chirp is still trusted and
identifies a delay the rig does not have; good runs then read 3 to 5 ms "early" and are flagged as lurches.
In the simulation that window opens at a 275°/s rate limit on the identified plant (8 of 132 good runs
blamed; 58 of 132 at 250°/s; 124 of 132 at 225°/s; refused from 210°/s down), sits at 300°/s on an 8.3 Hz plant and
at 225 to 250°/s on a slower one, where the chirp's misfit is only 5.6 to 7.0 %, so a tighter misfit limit
would not close it. When the session's releases are 3 ms or more early in the median, two thirds of them
within 2.5 ms of that, and `plant_misfit` is over 5 %, `score` prints one `SUSPECT THE CHIRP, NOT THE RUNS`
sentence (`chirp_suspect`). The flags stand and the exit status is 1; the sentence says where to look first.

Phone traces: `sync refused` (the three pulses do not line up), `release error`, `release flagged`
(including "the sync is off": the phone's release is not within 1.5 sample periods of the 0.6 sample
periods early that the pulse alignment leaves), `scoring error`.

**A session lag finding.** The release lag is a property of the rig, so it is judged across the session.
If the runs agree with each other (two thirds within 12 ms of the median, at least 3 runs) but the median
is outside −10…+80 ms, `score` prints **one** `SESSION LAG FINDING` instead of flagging every run, and
scores nothing. It means the rig, not any one run, is somewhere the method was not tested. Do this:

1. `python -m neuromech.session check-log --bin <a log> --player <its player file>`: is the log recording
   the Player's setpoint at all, and at the Player's rate?
2. `python -m neuromech.ilc --identify <R000.bin>`: compare bandwidth, damping and delay with the last
   session's. A lag that grew by d ms with the same bandwidth is d ms of latency somewhere (Player timing,
   a filter, a changed loop rate); a lower bandwidth is the gains or the load.
3. If the cause is found and fixed, rerun the session.
4. The override is for a lag that the chirp identification **explains**: the identified delay and
   bandwidth account for it. Stiction or a lurch in every run is also consistent across a session and is
   not a reason to accept. If the lag is explained and is simply what this rig does, score with it accepted:
   `python -m neuromech.session score ... --accept-session-lag-ms 87` (the session's median, to within
   12 ms). Runs are then held to within 12 ms of the session median, and the accepted value is written to
   `session_lag_override_ms` on every row of `report.csv` and printed. An override that does not match the
   session, or is not needed, is refused or ignored and said so. **An accepted session is still held to
   the tracking gate** and to every other release check: accepting the lag does not make a run a delivery.

The session median is only as good as the majority. **When half or more of a session's runs share a fault
(stiction, a slow start), the median belongs to the fault and the good runs are the ones flagged "this
run, not the rig".** If most runs are flagged this way, suspect the unflagged ones. The same goes for a
fault in every run: comparing runs with each other cannot see it. With a chirp in the session the release
check against the identified plant does (a 6° forward lurch or a half-speed start in every run is flagged,
whatever `--tracking-margin` or `--tracking-limit-ms` says);
without one, a gentle forward lurch in every run is scored without a flag.

All of these limits (0.20° hold, ratio 1.5, 12 ms, −10…+80 ms, the phone's 0.6 ± 1.5 sample periods,
tracking at 1.15 × predicted with a 4 ms floor in quadrature, the hold gain within 5 %, the session ratio's
0.92 to 1.08 / 15 % / two thirds / 7 ms, release within 4 ms of predicted, chirp R² 0.95 and misfit 10 %, the
chirp-suspect 3 ms / 2.5 ms / 5 %, the 25 ms fallback) are **provisional**: they were set on the simulated plant and on constructed faults. No rig
log has been through them, and the first rig session should be read as a test of the limits.

**Do not report scores from a real rig session yet.** The fixed 25 ms tracking limit is known to be
wrong on both sides of the simulated plant; until the chirp-derived limit (branch
`neuromech/chirp-derived-tracking-limit`) is reviewed and merged, a real session's report is a measurement
of the limits, not a judgement of the runs.

## 5. Correct the commands (ILC)

```bash
python -m neuromech.ilc --identify <R000.bin>                     # -> plant JSON
python -m neuromech.ilc commands/R014.csv R014.bin --plant <plant.json>   # -> R014_ilc1.csv
```

A run id is resolved to its scenario through the session's `key.csv`, and every ILC output is also
written as a Player file (`player/R014_ilc1.csv`): play that one next.

Error is always measured against the original scenario, so iterations converge on the scenario, not on
each other. Re-identify after any gain change.

## 6. Dry run without hardware

```bash
python -m neuromech.dryrun
```

Plans a session, plays each command as the Player delivers it (a 100 Hz staircase with Windows
lateness), synthesises LabVIEW `.bin` logs through the identified plant and a phone gyro (59 Hz,
jitter, reversed mount, real-size bias), and scores it like a real session. Result 2026-11-11
(seed 11; the same 9 of 9 at seeds 1, 3, 7 and 42): every run in its intended band on the encoder and on
the phone, median tracking 0.73°, phone |ΔPT7| median 0.001 (max 0.005), delivered release 27.4 ms after
the commanded one (22.7 to 28.4), phone release 8.3 ms before the encoder's (7.1 to 14.6), plant recovered
(6.34 Hz, ζ 0.234, R² 0.998). Before the delivered release was used this run gave 7 of 9: `B-claspknife`
and `M-catch_release_high` (one scenario, intended PT7 0.139) scored 0.116, band 0; they now score 0.128,
band 1, and remain 0.008 from the 0/1 edge. Across all 67 scenarios through the same delivery, 67 are in
band on the encoder (62 before). After the 2026-11-11 review changes the default run is unchanged (9 of 9
at the five seeds, tracking time error 14 ms median, 19 ms at most against the 25 ms limit); of the 67
scenarios 66 are scored and in band on the encoder and the phone at seeds 3, 11 and 42, and `D-wobble`
is flagged at every seed ("the hold is not still"), which is what that scenario is. The 2026-10-26 figures that stood here (median tracking 0.64°, phone
|ΔPT7| max 0.17, ILC converging on every scenario) were not re-measured for this note.

Since 2026-11-14 the dry run's tracking is judged against the plant identified from its own `R000` (section 4):
seed 11 gives tracking 1.00 of predicted (0.98 to 1.01), release +0.2 ms from predicted, per-run limits 16.3 to
22.1 ms in place of 25 (15.9 to 21.8 before the floor went into quadrature, 2026-11-16), and the same 9 of 9 with
no flag and no session finding.

**Current result (2026-11-17, scorer `pt5trough-ref-2026-11-16`, after the O5 refit).** The default session is
`A-P0, A-P1p, M-tone_L4, B-clonus, B-claspknife, C-f-high, M-tone_L2, K-HM1, M-fall_no_ringdown` (`K-HM1` took
the dropped `M-catch_release_high`'s slot). 9 of 9 runs in their intended band on the encoder and the phone at
seeds 11, 1, 3, 7 and 42, none flagged; tracking time error 14.1 to 14.2 ms median, 1.00 to 1.01 of predicted;
delivered release 27.4 to 27.5 ms after the commanded one (median); phone |ΔPT7| median under 0.001 at every
seed, max across seeds 0.016 (seed 3; 0.014 at seed 1, 0.005 or less at seeds 7, 11 and 42). `B-claspknife` intends 0.200 and is delivered at 0.197 to 0.198 (it intended 0.078, band 0, before the
refit); `K-HM1` 0.193 → 0.191 to 0.192. `C-f-high` (allowlisted) intends 0.2818 and is delivered at 0.2801 to
0.2818: band 1+ at every seed, but 0.0001 from the edge, so it is the session's deliberate edge run.

## 7. Does the emulator fall like a real leg?

```bash
python -m neuromech.fall_speed --opti-root <OptiTrack_Recordings> --rec-root <Recordings> [--limit 20]
```

Compares peak fall speed against the first excursion A0 on the real OptiTrack cohort with the
same comparison on the built scenario library (run `neuromech.build` first). The log-log slope is
the number to read: 1.0 means peak speed scales with the swing, 0 means it does not.
2026-10-27: real legs read ~0 per leg and the emulator ladder ~3, because the severity axis's `tone`
torque slows the whole fall. See `docs/reports/2026-10-27-emulator-fall-speed.md`. The cohort half caches per
trial, so `--limit` runs it in chunks on a low-memory machine.

**Mismatch check (plan B13), no recordings needed.** `--pin` writes the real side once, as one
row per leg, to `neuromech/fall_speed_reference.json`: an index grouping a person's legs, A0 and
peak, and nothing else. The file is public, so it carries no study group, clinical grade or anything
ordered by participant ID (`REFERENCE_ROW_FIELDS` in `fall_speed.py`). After that

```bash
python -m neuromech.fall_speed --emulator-only --check K --anchor A-P0
```

judges a profile set against it by a rule fixed on 2026-11-02, before any revised axis was measured
(constants and reasoning at the top of the check in `fall_speed.py`): each profile's change in peak
fall speed from the anchor, beyond what the real legs' slope predicts for its change in A0, must stay
inside a 95 % band of where one real leg lands (bootstrapped over people); and where the set's A0
spans at least x1.2 its slope must sit inside the real slope's 95 % CI. The verdict is MISMATCH or
NO FLAG: a failure to flag, not a match and not a realism certificate, and the report says how large
a change the band lets through. The reference is OptiTrack A0; re-pin it on B11's settled geometry.

## 8. Check the app itself, without a phone

```bash
(cd webapp && npm run build:wasm)                                   # the app core is a local build: make it current
python -m neuromech.phone_trial --sids A-P0,M-tone_L2 --defects all # or --sids pendulum (built in, no build needed)
```

`phone_trial` wraps any scenario's swing in the rest of a real trial (rest, lift, still hold, release,
swing, quiet tail), turns it into what a phone strapped to the shank would record, and writes it in the
app's raw export format with a `.truth.json` beside it: where the release was planted, what the scorer
says about the true motion, and what the app should report. `webapp/scripts/replay-trial.mjs` then replays
the recording through the app's worker and wasm core exactly as the capture screen does, including the
five-second auto-stop, and every check is printed as got / want.

Without `--defects` these are good trials: is the hold accepted, is the release found where it was planted,
does the trial end itself, are the parameters right. Each defect (`fast_lift`, `early_release`,
`moved_in_hold`, `hold_drift`, `lateral_shake`, `short_tail`, `no_tail`) is a good trial with one thing
wrong, and asks whether the app says so. `--ideal` uses a perfect sensor, which separates the app's own
arithmetic from sensor error; `--wasm FILE` replays through another build of the core, to try a fix
before it ships. Output goes to `neuromech/out/phone_trials/`.

The parameters are judged against the true angle ticked and smoothed as the app does it (50 ms hold, EMA
0.3). The scenario manifest's numbers are on a different footing (100 Hz, unsmoothed), and the app's PT
score sits about 0.05 above them on the same motion; that gap is printed, not judged.

## Tests

```bash
python -m pytest neuromech/tests                                    # 321 passed, 2 xfailed (9 skip without a local wasm build)
node neuromech/labview/verify_formula_node.js                       # Formula Node vs Python, 1e-11
```

## Files

| File | Role |
|---|---|
| `pendulum_sim.py` | De Groote et al. 2018, RK4, checked against the authors' MATLAB (reference) |
| `model_rt.py` | the same model at 1 kHz for LabVIEW, plus nine behaviour modules |
| `scenarios.py`, `build.py` | the library and its expected scorer output; `build.score_angle` is the one scoring call |
| `session.py` | plan / score a rig session |
| `ilc.py` | plant identification and trajectory correction |
| `dryrun.py` | the whole program on synthetic hardware |
| `fall_speed.py` | peak fall speed vs A0, real cohort beside the emulator |
| `nm_figs.py`, `pubstyle.py` | figures (deck and publication) |
| `labview/` | Formula Node step, test vectors, verifier, program spec |
| `data/rig_frf_2026-10-26.npz` | measured setpoint→encoder response of the current tune |
