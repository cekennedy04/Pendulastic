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
release — what the app itself can see). `R000` reports the identified plant.
Output: `sessions/<name>/report.csv` and a printed summary.

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
jitter, reversed mount, real-size bias), and scores it like a real session. Result re-run 2026-11-10
on main: 7 of 9 runs delivered in their intended band, median tracking 0.73°, phone vs encoder |ΔPT7|
median 0.000 (max 0.015), phone/encoder band agreement 89%, plant recovered (6.34 Hz, ζ 0.234, R² 0.998).
The two misses are `B-claspknife` and `M-catch_release_high`: both intend PT7 0.139 and deliver about
0.115, which falls on the other side of the 0 / 1 band edge. (The 2026-10-26 run recorded every run in
band; that no longer reproduces.)

## 7. Check the app itself, without a phone

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
python -m pytest neuromech/tests                                    # 90 tests
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
