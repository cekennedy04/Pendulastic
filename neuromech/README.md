# neuromech — the knee-spasticity emulator program

Plays programmed pendulum-test swings on the LabVIEW motor rig, then scores every run from the encoder
(truth) and the phone, through the validated production scorer. Plan:
`docs/plans/2026-10-26-001-plan-neuromech-emulator-unified.md`. LabVIEW side:
`neuromech/labview/LABVIEW_PROGRAM_SPEC.md`.

Run everything from the repo root with the project venv (`.venv/Scripts/python.exe`), with
`PYTHONIOENCODING=utf-8` and `OPENBLAS_NUM_THREADS=1`.

## 1. Build the scenario library (once, or after a model change)

```bash
python -m neuromech.build                # 67 scenarios -> neuromech/out/ (manifest + 1 kHz setpoints)
```

| Group | What | Source |
|---|---|---|
| A | PT7-band presets P0–P3 (not MAS settings — plan D5) | De Groote core, calibrated to band centres |
| S | the published severity axis, including where PT7 inverts | De Groote core |
| B | behaviours: flexor/extensor, clasp-knife, clonus, rigidity, contracture, hypotonia, guarding, spasm, habituation | De Groote core + modules |
| C | each of the 7 parameters and the A0 gate pushed out of range | De Groote core + kinematic transforms |
| D | release artefacts: push, wobble, short record, low lift | De Groote core |
| M | severity AND kind of severity: tone ladder, catch-release, catch-and-hold, no-ringdown fall, end-stop | De Groote core, via `profiles.py` |
| R | the four mechanisms B9 ported, one at a time: bearing friction, anthropometry, tonic arrest, reflex channel | De Groote core |
| H | HESS's four haptic models (Park et al. 2012, Table III), elbow values unscaled: the three-phase catch on a pendulum | De Groote core + `hess_*` module |
| K | HESS HM#1–2 on the knee: H alone refitted so each holds its intended MAS 1 / 1+ (HM#3's MAS 2 and HM#4's MAS 3 exist only in a sliver below the arrest jump) | De Groote core + `hess_*` module |

`out/scenario_manifest.csv` holds, per scenario, the expected 7 parameters, PT7, band or refusal, flags,
the rig range and speed it needs, and the peak active muscle torque (for Mode C).

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
`PLAY.bat`. It refuses files over 45 deg, with a header, or not starting at 0; q/Esc ramps back to 0;
each run leaves `sent/<run>_<time>.csv`, what it wrote and when. Logs and the run sheet come back on
the stick. The kit's `README.txt` is the bench procedure. `key.csv` never goes on the stick.

**Once, before the first session:** play any player file with the log running, then

```bash
python -m neuromech.session check-log --bin <the new .bin> --player sessions/<S>/player/R001.csv
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
(the section "After review" describes everything below; section B the chirp-derived tracking limit).

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
| `tracking flagged: tracking error ... times the ... the plant identified from the chirp predicts` | the arm followed the command more loosely than this rig's own plant does: lag-aligned RMSE ÷ the command's rms speed (`tracking_time_err_ms`) is over max(1.15 × `tracking_pred_ms`, 4 ms). The scores stay in the report but the run is not a delivery of its scenario | look at the run; run ILC on it (section 5) or rerun |
| `tracking flagged: the release is ... ms from where the plant identified from the chirp puts it` | the arm left the hold more than 4 ms earlier or later than this rig's plant does with the setpoint the log recorded (`enc_lag_minus_pred_ms`). Late: stiction or a slow start. Early: a lurch, or something pushing the arm. This check sees a fault that is in every run | find the cause at the rig; a rerun will not change it if every run has it |
| `tracking flagged: tracking error ... is over the limit of 25 ms` | the session has no usable chirp (or `--tracking-limit-ms` was given), so the run was held to one fixed number | plan with `--chirp`; see below |
| `scoring error` | the scorer raised on the trace | read the message |

`score` also exits with status 1 when any trace ends in an error and so has no score: an encoder
`scoring error`, or a phone `scoring error` or `release error`. They are listed under "NOT SCORED BECAUSE OF
AN ERROR".

**The tracking limit comes from the session's own chirp.** With `R000` in the session (`plan --chirp`),
`score` identifies the plant from it and, for every run, passes that run's command through the identified
plant: `tracking_pred_ms` is the tracking error the plant itself produces on that command, and the run's
limit (`tracking_limit_ms`) is max(1.15 × predicted, 4 ms). The 4 ms floor is for a rig that tracks almost
perfectly, where what is left is the Player's 100 Hz staircase and encoder noise, which the model does not
contain. The second check is on the release: the identified plant is driven by the setpoint the log
recorded and its release is compared with the arm's (`enc_release_lag_pred_ms`, `enc_lag_minus_pred_ms`,
limit 4 ms). `report.csv` carries `tracking_limit_source` (`plant`, `fixed` or `override`) and
`tracking_limit_note` on every row, and the summary prints which it was.

- **No chirp** (not planned, or `R000` has no log): every run is held to the fixed 25 ms and the output
  says `TRACKING LIMIT IS NOT PLANT-DERIVED`. That number belongs to the simulated plant: on a slower rig
  it flags good runs, on a faster or ILC-corrected rig it misses sluggish starts, and it cannot see a fault
  that is in every run. Plan real sessions with `--chirp`.
- **A chirp the model cannot reproduce** (fit R² under 0.95, or the identified model leaves more than 10 %
  of the chirp's motion unexplained: `plant_misfit`): the plant is not used, the runs fall back to the fixed
  25 ms, `score` prints a `CHIRP FINDING` and exits 1. It means coarse stiction or a deadband, or a drive
  that saturates on the chirp (8° at 10 Hz is 500°/s). Look at `R000` first.
- **`--tracking-limit-ms N`** still judges every run by N alone, prints `TRACKING LIMIT OVERRIDE USED` and
  is recorded as `override`; the plant's predictions stay in the report.

What this does not catch, measured in simulation (report, section B): a start at 90 % speed, a 1° lurch
and 2 ms of stiction pass. A drive that saturates only slightly on the chirp (limited to 225 to 250°/s in
the simulation) is still trusted and makes good runs look 4 to 5 ms "early": if every run is flagged
"early" by about the same amount and `plant_misfit` is over 5 %, suspect the chirp before the runs.

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
check against the identified plant does (a 6° forward lurch or a half-speed start in every run is flagged);
without one, a gentle forward lurch in every run is scored without a flag.

All of these limits (0.20° hold, ratio 1.5, 12 ms, −10…+80 ms, the phone's 0.6 ± 1.5 sample periods,
tracking at 1.15 × predicted with a 4 ms floor, release within 4 ms of predicted, chirp R² 0.95 and misfit
10 %, the 25 ms fallback) are **provisional**: they were set on the simulated plant and on constructed faults. No rig
log has been through them, and the first rig session should be read as a test of the limits.

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
seed 11 gives tracking 1.00 of predicted (0.98 to 1.01), release +0.2 ms from predicted, per-run limits 15.9 to
21.8 ms in place of 25, and the same 9 of 9 with no flag.

## Tests

```bash
python -m pytest neuromech/tests                                    # 228 tests
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
| `nm_figs.py`, `pubstyle.py` | figures (deck and publication) |
| `labview/` | Formula Node step, test vectors, verifier, program spec |
| `data/rig_frf_2026-10-26.npz` | measured setpoint→encoder response of the current tune |
