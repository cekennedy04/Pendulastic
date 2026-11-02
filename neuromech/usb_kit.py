"""Everything the rig PC needs for one session, in one folder to copy onto a USB stick.

    python -m neuromech.usb_kit --name S1 [--dest D:\\]      # S1 = a session made by `session plan`

The rig PC needs no Python and no LabVIEW work: the kit plays files with play_setpoints.ps1 (Windows
PowerShell + NI DataSocket, which LabVIEW installs). Logs come back on the same stick and are scored here.

    <dest>/<name>/
      README.txt              the bench steps, in order
      PendulumDesigner.exe    design a movement (MAS grade / target parameters / by hand), see its scores
      PendulumDesigner64.exe  and the waveform, play it, keep the trial log (trials.csv) -- the main tool
      PLAY.bat  DRYRUN.bat  PICK_VARIABLE.bat  _run.bat  play_setpoints.ps1   play a ready-made file
      smoke_5deg.csv          a slow 5 deg sine: first contact with the rig, and the check-log file
      player/<run>.csv        the session's runs
      runsheet.csv            fill in encoder_file at the bench, bring it back
      logs/                   copy StretchExperiment's .bin logs here
key.csv stays on this machine: it maps runs to scenarios, and the bench grades blind (R3).
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from neuromech import ilc  # noqa: E402
from neuromech import session  # noqa: E402

PLAYER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "labview", "player")
SCRIPTS = ("PLAY.bat", "DRYRUN.bat", "PICK_VARIABLE.bat", "_run.bat", "play_setpoints.ps1")
DEFAULT_DEST = os.path.join(os.path.dirname(os.path.abspath(__file__)), "usb")

README = """\
PENDULUM PLAYER KIT  -  session {name}
====================================

THE DESIGNER (PendulumDesigner.exe)  -  start here
  Copy this folder to the rig PC's desktop and double-click PendulumDesigner.exe (if it says DataSocket is
  not registered for its width, use PendulumDesigner64.exe). Nothing to install on Windows 7.
  1  What movement?  Aim at a MAS grade, or tick parameters and type target values (R2n, N, f, ...), or
     turn the knobs by hand. Press "Design it". It shows the PT7 score, the zone, a rough MAS, all seven
     parameters, the rig's motion and the knee angle the phone should show.
  2  Type a trial label (e.g. "MAS 3 slow"), Pick... the setpoint variable once, set up the arm, phone and
     log, press PLAY. The trial number and label show in big letters while it plays, with a red cursor
     moving along both curves. STOP returns the arm to 0.
  3  The trial log at the bottom fills itself in. Type each StretchExperiment log's file name into
     encoder_file, and (if you can read it off the phone) the phone file's trial number into phone_trial.
     It saves trials.csv beside the program on every change; each played movement is kept in trial_files\\.
  On this flat rig, compare only f and N with the phone: its angle reads inverted and drifts in this plane.
  Back on the analysis PC:
      python -m neuromech.match_trials --kit <this folder> --phone-manifest <export>-manifest.json --encoder-dir <this folder>\\logs
  pairs every trial with its phone recording (by capture order, skipping false starts; phone_trial wins)
  and its encoder log, and writes trials_matched.csv: designed vs delivered vs phone, side by side.

THE PLAIN PLAYER (PLAY.bat) plays the session's ready-made files in player\\.
Plays the emulator's angle files into the rig through StretchExperiment's own setpoint variable.
Nothing in StretchExperiment is changed and nothing is built in LabVIEW. Copy this whole folder
to the rig PC's desktop first (do not run it from the stick).

ONCE, on the rig PC
  1. Double-click PICK_VARIABLE.bat. In the window, expand the RT target ->
     variables - network - RT (separate) -> M1JointAngleDesired, then OK.
     (Saved to variable_url.txt. If it fails, type the address into that file yourself, e.g.
      psp://192.168.0.x/variables - network - RT (separate)/M1JointAngleDesired )
  2. Test the variable in NI Distributed System Manager, limits at +/-10: type 5 into it.
     The arm should move to 5 and STAY. If it snaps back, something on the target owns it; try
     M1AngleSetPoint instead (step 1 again).
  3. Smoke test: arm at 0 (Manual Control -> Speed 10 -> Goto Position 0). Start StretchExperiment's
     log, drag smoke_5deg.csv onto PLAY.bat, press Enter. The arm should sway +/-5 deg slowly for
     20 s. Stop the log and copy the .bin into logs\\. Back on the analysis PC:
         python -m neuromech.session check-log --bin <that .bin> --player smoke_5deg.csv
     PASS means the log records what the player sends. If logging only happens inside a Stretch
     trial, start a long Stretch-mode hold, play over it, and check again.

EVERY RUN (in runsheet.csv order)
  * New file? Drag it onto DRYRUN.bat first: checks the file and the PC's timing, moves nothing.
  * Arm at 0 -> start the phone -> start the log -> drag player\\<run>.csv onto PLAY.bat ->
    press Enter -> let the app auto-stop -> stop the phone -> stop the log.
  * Write the log's file name into runsheet.csv's encoder_file column. That is the only thing that
    pairs a log with its run. Copy the .bin into logs\\.

SAFETY
  * PLAY refuses any file that goes beyond 45 deg, has a header, or does not start at 0.
    (Change the limit only after the arm's mechanical range is confirmed: PLAY.bat file -MaxDeg 60)
  * q or Esc during a run stops it and ramps the setpoint back to 0 at 10 deg/s.
  * At the end it holds the last angle 2 s, then ramps to 0. The E-stop is still the real guard;
    the Angle Limit fields on the front panel do NOT bound this player.
  * "Updates ... 95th pct" over 25 ms: close other programs and play the run again.

BRING BACK on the stick: runsheet.csv, logs\\, and sent\\ (what the player wrote, for checking).
Then on the analysis PC:
    python -m neuromech.session score --name {name} --encoder-dir <stick>\\{name}\\logs --phone-manifest <phone export>-manifest.json
(copy the filled runsheet.csv over neuromech\\sessions\\{name}\\runsheet.csv first)
"""


def smoke_file(path, amp=5.0, f_hz=0.2, dur=20.0, fs=1000.0):
    """A slow sine that starts and ends at 0 with zero slope: gentle enough for first contact."""
    t = np.arange(0, dur + 0.5 / fs, 1 / fs)
    sp = amp * np.sin(2 * np.pi * f_hz * t) * np.sin(np.pi * t / dur) ** 2
    ilc.write_player(path, t, sp)
    return path


def build(name, dest=DEFAULT_DEST):
    src = os.path.join(session.SESS, name)          # read at call time, so tests can redirect it
    if not os.path.isdir(os.path.join(src, "player")):
        raise SystemExit(f"no session {name} with player files under {session.SESS}; run `python -m neuromech.session plan --name {name} ...` first")
    out = os.path.join(dest, name)
    if os.path.exists(out):
        raise SystemExit(f"{out} exists; delete it or pick another --dest")
    os.makedirs(os.path.join(out, "logs"))
    for f in SCRIPTS:
        shutil.copy(os.path.join(PLAYER, f), out)
    from neuromech.designer.build_designer import build as build_designer
    build_designer(out)
    shutil.copytree(os.path.join(src, "player"), os.path.join(out, "player"))
    shutil.copy(os.path.join(src, "runsheet.csv"), out)
    smoke_file(os.path.join(out, "smoke_5deg.csv"))
    with open(os.path.join(out, "README.txt"), "w", newline="\r\n") as fh:
        fh.write(README.format(name=name))
    assert not os.path.exists(os.path.join(out, "key.csv"))
    return out


def main():
    a = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    a.add_argument("--name", required=True); a.add_argument("--dest", default=DEFAULT_DEST)
    x = a.parse_args()
    out = build(x.name, x.dest)
    print(f"kit -> {out}")
    for f in sorted(os.listdir(out)):
        print("  ", f)


if __name__ == "__main__":
    main()
