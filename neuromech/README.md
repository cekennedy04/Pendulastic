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
before the release over what it was told to hold (`enc_hold_gain`), as long as that is within 5 % of 1. The
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
3. If it is simply what the rig does, accept it with the margin the finding printed:
   `python -m neuromech.session score ... --tracking-margin 1.32`. It is recorded, and a later session that
   moves away from the accepted ratio in either direction gets the finding again.

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
| arm settles at 0.995 / 0.99 / 0.98 / 0.97 of the setpoint | 10 / 22 / 38 / 61 % | 0 | none (absorbed by `enc_hold_gain`) |
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

Not covered, in bold above: nonlinearity strong enough to move the release itself (the release check then
blames good runs: 20 % softening, 0.2° of friction, a 0.1° deadband, a loop gain that rises 20 % with error:
95 %), a rig where the prediction is under 7 ms and noise or quantisation is what is measured, and a mismatch
on which the runs do not agree with each other.

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

## Tests

```bash
python -m pytest neuromech/tests                                    # 264 tests
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
