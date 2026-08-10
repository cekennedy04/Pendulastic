# Pendulastic — Weekly Update (Aug 4–10, 2026)

Plain-language version, organized by: what was done, what was fixed, what's still open, what was asked of Claude, and what's next.

---

## What was done (new stuff built)

- **New screen for entering MAS scores** in the app — type in a clinician's MAS grade, which leg is stronger, and notes, then hit Save/Export. Before this, scores had to be typed directly into a CSV file by hand. (`61694ec`…`8d4a4aa`)
- **Automatic MS-vs-Control comparison.** Every analysis run now also compares MS patients against Control participants and produces a chart + statistics, using the same scoring method as the individual reports. (`f0952e0`, `965453c`, `7a41b4b`)
- **MAS-vs-PT-score validation tool.** Checks how well the app's own score agrees with the clinician's MAS grade, and can fit thresholds between them. (`4673cb6`)
- **Nightly automatic code cleanup checker** went live and ran for the first time. It scans the project and writes a list of suggested cleanups (old files, clutter, etc.) — it never deletes or changes anything itself, only proposes. (`1c83c3c`→`63b7839`)
- **Recovered 18 old scripts** (e.g. `align_and_calibrate.py`, `train_angle_regressor.py`, `validate_tracking.py`) that were sitting only on a local computer — now safely stored in the project history. (`4f45334`)

## What was fixed

- **Some sessions never got a report.** The system used to wait for a minimum trial count that some short sessions never reached. Fixed by generating a report 30 minutes after the first recording instead, using whatever trials exist by then. (`7500c4e`)
- **Bad trials were skewing scores.** 5 trials from participant P15 were invalid — the participant used their own muscles instead of letting the leg swing freely, which throws off the math. Fixed by adding an `excluded_trials.json` list so these are automatically skipped everywhere. (`e716c8a`)
- **A save could corrupt the score file** if the MAS score CSV's first line was blank. Fixed same day. (`4c7c54d`)
- **Score file safety was too loose** — tightened which columns are allowed to be auto-added to the CSV. (`28fa3e7`)
- **The nightly cleanup checker had two early bugs** — it once suggested deleting the main project folder itself, and it crashed when its code-checking tool ran without a properly set-up environment. Both caught and fixed the same day. (`03f5cbf`, `67313a7`)

## What issues persist (still open)

- **Marker-tracking warning on almost every trial** for the reference participant used for comparison — a data-quality flag keeps showing up, root cause not found yet.
- **No new charts actually got produced this week.** The two new chart tools (MAS validation chart, MS-vs-Control chart) haven't been run against real recordings yet — needs to happen on the lab machine, not in the environment this report was written in.
- **No screenshot yet** of the new MAS entry screen.
- **Automated tests could not be run** in the environment this report was written in (missing test tool) — still needs confirming on your machine.
- **4 planned features have a spec but no code yet:** multi-person disambiguation ("Pick Person"), a phone-sensor pairing helper, a tracking-accuracy validation pipeline, and an in-app button to mark bad trials instead of editing a file by hand.

## What you asked Claude to do

Reconstructed from notes left in the project's own planning documents — not a transcript, so flag anything that doesn't match what you actually asked for:

- After trying the new MAS entry screen yourself, you asked for two more fields: which leg is stronger, and a free-text notes box.
- You confirmed the real MAS score data is one score per participant per leg, not per session — this changed how the validation tool matches scores to trials.
- You chose to have the score file update itself automatically the first time a new field is saved, instead of a separate one-time migration script.
- You reported that the video-upload tool had no way to say which person in frame is the patient, or fix a bad tracking frame — a "Pick Person" spec was written in response (not built yet).
- You asked for the nightly cleanup checker to run unattended overnight and only ever suggest changes, never make them, based on past experience of unattended tools quietly introducing bugs.

## What needs to be done next

- Run the two new chart tools against real recordings on the lab machine and check the output.
- Take a screenshot of the new MAS entry screen.
- Decide whether to root-cause the marker-tracking warning now, or keep it as a caveat.
- Pick which of the 4 unbuilt features to do next.
- Confirm the automated tests pass on your machine.

---

*The four PNGs already in the repo from earlier calibration work (not new this week) are viewable in the repo root: `lateral_impact_presentation.png`, `oneoff_lateral_check.png`, and their `_P1_Pos2_T1` variants.*
