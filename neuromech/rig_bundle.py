"""Build the rig-PC bundle: everything the motor rig's Windows PC needs, in one folder and one zip.

    python -m neuromech.rig_bundle                    # rebuild the library + the Designer, then bundle
    python -m neuromech.rig_bundle --skip-library     # reuse neuromech/out/ (refused if it is stale)

Writes (both gitignored, under neuromech/out/):
    rig_bundle/                         the folder to copy onto a USB stick
    rig_bundle_<YYYYMMDD>_<sha7>.zip    the same folder, zipped, top folder named like the zip

The rig PC (the LabVIEW StretchExperiment PC) needs no Python and no developer tools: the Designer is a
.NET Framework WinForms exe and the plain player is Windows PowerShell; both reach the motor only through
StretchExperiment's setpoint shared variable via NI DataSocket, which LabVIEW installs.

What goes in, and why:
  PendulumDesigner.exe / PendulumDesigner64.exe (+ .exe.config)  built fresh from this checkout with the
      .NET 3.5 compiler (CLR 2.0). The .config lets the same exe run on CLR 4 too, so it starts on a stock
      Windows 7 (3.5 built in) and on Windows 8/10/11 where only 4.x is present.
  PLAY.bat DRYRUN.bat PICK_VARIABLE.bat _run.bat play_setpoints.ps1   the plain player (CRLF, ASCII)
  library/<sid>.csv     every built scenario that passes the playback guard (neuromech.rig_guard, when it is
                        importable) or else the raw check (finite, |angle| <= 40 deg, <= 600 deg/s per 10 ms
                        write), as player files (t_s, setpoint_deg, no header, starting at 0), plus R-chirp
  library/INDEX.csv     per file: group, title, duration, peak, fastest per-write rate; scenarios left out are
                        listed with the reason. Expected scores stay on the analysis PC (the bench grades blind).
  smoke_5deg.csv        a slow 5 deg sine for first contact and the check-log test
  bench_log.csv         a blank run log for library files played with PLAY.bat
  logs/                 where StretchExperiment's .bin logs go
  START_HERE.txt        the bench procedure, the safety limits and what is not validated (CRLF for Notepad)
  MANIFEST.txt / MANIFEST.json   every file with SHA256, the source commit, build date, scorer/model version

The scenario library is a build output: rerun this after any change to the scenarios or the scorer.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from datetime import datetime, timezone

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from neuromech import build as B  # noqa: E402
from neuromech import ilc  # noqa: E402
from neuromech import usb_kit as UK  # noqa: E402

NM = os.path.dirname(os.path.abspath(__file__))
DEFAULT_OUT = os.path.join(NM, "out", "rig_bundle")
LIMIT_DEG = 40.0               # owner decision 2026-11-17: hard limit +/-40 deg rig angle
RATE_DPS = 600.0               # owner decision 2026-11-17: at most 600 deg/s per write
WRITE_MS = 10                  # both players write every 10 ms
GUARD_FUNCS = ("check_trajectory", "check", "check_player")
EXE_CONFIG = """<?xml version="1.0" encoding="utf-8"?>
<configuration>
  <startup>
    <supportedRuntime version="v4.0"/>
    <supportedRuntime version="v2.0.50727"/>
  </startup>
</configuration>
"""
BENCH_LOG_COLS = ["order", "file", "label", "pc_time", "peak_deg", "encoder_file", "phone_trial",
                  "phone_started", "app_auto_stopped", "notes"]
# Text that must never ship: personal paths, participant folder names, credentials.
FORBIDDEN = [r"[A-Za-z]:\\Users\\", r"/Users/", r"/home/", r"\b(Control|CHAT|MINT|DOSA|Participant)_\d",
             r"(?i)api[_-]?key", r"(?i)password", r"(?i)secret", r"(?i)token\s*[:=]", r"@[A-Za-z0-9-]+\.(com|edu|org)"]


def git(*args):
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True).stdout.strip()


def source_state():
    """The commit the bundle is built from, and whether the files that go into it differ from that commit."""
    sha = git("rev-parse", "HEAD")
    dirty = git("status", "--porcelain", "--untracked-files=no", "--", "neuromech", "motor_reference.py",
                "pendulastic_pt_score.py")
    return sha, [ln[3:] for ln in dirty.splitlines() if ln.strip()]


def build_library(allow_near_edge=False):
    cmd = [sys.executable, "-m", "neuromech.build"] + (["--allow-near-edge"] if allow_near_edge else [])
    env = dict(os.environ, PYTHONIOENCODING="utf-8", OPENBLAS_NUM_THREADS="1")
    r = subprocess.run(cmd, cwd=ROOT, env=env)
    if r.returncode != 0:
        raise SystemExit(f"python -m neuromech.build exited {r.returncode}; fix the library before bundling it")


def check_library_current():
    rec, cur = B.library_provenance(), B.provenance()
    bad = B.provenance_mismatch(rec, cur)
    if bad:
        raise SystemExit(f"neuromech/out/ was built under different {', '.join(bad)} than this code; "
                         "run without --skip-library")
    return rec


def crlf_copy(src, dst):
    """Batch files and the .ps1 must be CRLF and ASCII whatever the checkout's line endings are."""
    b = open(src, "rb").read().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
    if any(c > 127 for c in b):
        raise SystemExit(f"{src} is not plain ASCII; Windows PowerShell 2-5 would misread it")
    with open(dst, "wb") as fh:
        fh.write(b)


def guard_status():
    """The playback guard (neuromech.rig_guard, owner decision 2026-11-17: +/-40 deg, 600 deg/s per write)
    -> (check function or None, a one-line status for MANIFEST and START_HERE)."""
    try:
        from neuromech import rig_guard as G
    except ImportError:
        return None, ("ABSENT: built before the playback guard merged. NOT FOR MOTOR USE; "
                      f"library filtered by raw peak <= {LIMIT_DEG:g} deg and {RATE_DPS:g} deg/s per write only")
    for name in GUARD_FUNCS:
        fn = getattr(G, name, None)
        if callable(fn):
            return fn, f"present: neuromech.rig_guard.{name} checked every library file"
    raise SystemExit("neuromech.rig_guard exists but exposes none of " + ", ".join(GUARD_FUNCS)
                     + "; update rig_bundle.GUARD_FUNCS to its check function")


def guard_verdict(fn, t, sp):
    """None if the guard passes the file, else its reason. Accepts the shapes a check function might return:
    None/True/'' (pass), a string (reason), False, a dict or an object with ok/reason(s)."""
    r = fn(t, sp)
    if r is None or r is True or r == "":
        return None
    if r is False:
        return "refused by rig_guard"
    if isinstance(r, str):
        return r
    ok = r.get("ok") if isinstance(r, dict) else getattr(r, "ok", None)
    why = (r.get("reason") or r.get("reasons")) if isinstance(r, dict) else (getattr(r, "reason", None) or getattr(r, "reasons", None))
    if ok is None:
        raise SystemExit(f"cannot read rig_guard's verdict {r!r}; update rig_bundle.guard_verdict")
    if ok:
        return None
    return "; ".join(why) if isinstance(why, (list, tuple)) else str(why or "refused by rig_guard")


def raw_verdict(t, sp, max_deg=None):
    """The fallback when there is no guard: finite, within the angle limit, within the per-write rate."""
    max_deg = LIMIT_DEG if max_deg is None else max_deg
    if not np.all(np.isfinite(sp)):
        return "not finite (NaN/Inf)"
    peak = float(np.max(np.abs(sp)))
    if peak > max_deg + 1e-9:
        return f"peak {peak:.1f} deg over {max_deg:g}"
    rate = per_write_rate(t, sp)
    if rate > RATE_DPS + 1e-9:
        return f"{rate:.0f} deg/s per {WRITE_MS} ms write over {RATE_DPS:g}"
    return None


def per_write_rate(t, sp):
    """The fastest change between two consecutive player writes (every WRITE_MS, clock-indexed), in deg/s."""
    tw = np.arange(0.0, float(t[-1]) + 1e-9, WRITE_MS / 1000.0)
    return float(np.max(np.abs(np.diff(np.interp(tw, t, sp))))) / (WRITE_MS / 1000.0) if len(tw) > 1 else 0.0


def export_library(out_lib, guard=None):
    """Player files for every built scenario the guard (or the raw fallback) passes, and an index of all."""
    import pandas as pd
    from neuromech import session as SS
    man = pd.read_csv(os.path.join(B.OUT, "scenario_manifest.csv"))
    os.makedirs(out_lib)
    rows = []

    def add(sid, group, title, t, sp, phase):
        peak = float(np.max(np.abs(sp)))
        why = raw_verdict(t, sp)
        if why is None and guard is not None:
            why = guard_verdict(guard, t, sp)
        if why is None:
            ilc.write_player(os.path.join(out_lib, sid + ".csv"), t, sp)
        rows.append(dict(file="" if why else sid + ".csv", sid=sid, group=group, title=title,
                         duration_s=round(float(t[-1]), 1), peak_deg=round(peak, 1),
                         max_rate_per_write_dps=round(per_write_rate(t, sp), 0),
                         included="yes" if why is None else "NO: " + why))

    t, sp, _, _, ph = SS.chirp_command()
    add("R-chirp", "R0", "identification chirp 0.2-10 Hz, 8 deg (play first in a session)", t, sp, ph)
    for _, r in man.iterrows():
        a = np.genfromtxt(os.path.join(B.OUT, "setpoints", f"{r.sid}.csv"), delimiter=",", names=True)
        add(r.sid, r.group, r.title, a["t_s"], a["setpoint_deg"], a["phase"].astype(int))
    with open(os.path.join(out_lib, "INDEX.csv"), "w", newline="", encoding="ascii", errors="replace") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]), lineterminator="\r\n")
        w.writeheader(); w.writerows(rows)
    return rows


def compile_designer(out_dir):
    from neuromech.designer.build_designer import CSC, build as build_designer
    with tempfile.TemporaryDirectory() as tmp:
        exes = build_designer(tmp)
        for e in exes:
            shutil.copy(e, out_dir)
            with open(os.path.join(out_dir, os.path.basename(e) + ".config"), "w", newline="\r\n", encoding="utf-8") as fh:
                fh.write(EXE_CONFIG)
    ver = subprocess.run([CSC, "-help"], capture_output=True, text=True).stdout.splitlines()
    return [os.path.basename(e) for e in exes], next((v.strip() for v in ver if "version" in v.lower()), "csc v3.5")


def cs_score_formula_id():
    src = open(os.path.join(NM, "designer", "PtScore.cs"), encoding="utf-8").read()
    return re.search(r'SCORE_FORMULA_ID\s*=\s*"([^"]+)"', src).group(1)


def privacy_scan(folder):
    """Refuse to ship personal paths, participant folder names or anything that looks like a credential."""
    pats = [re.compile(p) for p in FORBIDDEN]
    user = os.path.basename(os.path.expanduser("~"))
    hits = []
    for dp, _, fs in os.walk(folder):
        for f in fs:
            p = os.path.join(dp, f)
            b = open(p, "rb").read()
            texts = [b.decode("latin-1")]
            if f.lower().endswith(".exe"):
                texts.append(b.decode("utf-16-le", errors="ignore"))   # .NET user strings are UTF-16
            for s in texts:
                if user and len(user) > 2 and user.lower() in s.lower():
                    hits.append((p, "user name"))
                for pat in pats:
                    if f.lower().endswith(".exe") and pat.pattern.startswith("(?i)"):
                        continue        # framework metadata names (e.g. Password chars) are not secrets
                    if pat.search(s):
                        hits.append((p, pat.pattern))
    if hits:
        raise SystemExit("privacy scan failed:\n" + "\n".join(f"  {os.path.relpath(p, folder)}: {w}" for p, w in hits))


def sha256(p):
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def write_manifest(folder, meta):
    files = []
    for dp, _, fs in os.walk(folder):
        for f in fs:
            p = os.path.join(dp, f); rel = os.path.relpath(p, folder).replace(os.sep, "/")
            if rel in ("MANIFEST.txt", "MANIFEST.json"):
                continue
            files.append(dict(path=rel, bytes=os.path.getsize(p), sha256=sha256(p)))
    files.sort(key=lambda d: d["path"].lower())
    meta = dict(meta, files=files)
    with open(os.path.join(folder, "MANIFEST.json"), "w", newline="\n", encoding="utf-8") as fh:
        json.dump(meta, fh, indent=1)
    lines = ["PENDULUM RIG BUNDLE - MANIFEST", ""]
    for k, v in meta.items():
        if k != "files":
            lines.append(f"{k:24s} {v if not isinstance(v, list) else ', '.join(v) or '(none)'}")
    lines += ["", f"{len(files)} files (MANIFEST.txt and MANIFEST.json not listed). Check one on the rig PC with:",
              "    certutil -hashfile <file> SHA256", "", f"{'sha256':64s}  {'bytes':>9s}  path"]
    lines += [f"{d['sha256']}  {d['bytes']:9d}  {d['path']}" for d in files]
    with open(os.path.join(folder, "MANIFEST.txt"), "w", newline="\r\n", encoding="ascii") as fh:
        fh.write("\n".join(lines) + "\n")
    return files


GUARD_TEXT = {
    False: ("""!!!  DO NOT PLAY ANYTHING ON THE MOTOR WITH THIS BUNDLE  !!!
It was built BEFORE the playback guard (hard +/-40 deg limit, 600 deg/s per write, NaN refusal) was
merged. Its players check only the whole file's peak once before starting (Max |angle|, default 45,
which the Designer lets an operator raise to 90); they do not clamp each value sent, have no speed
limit, and accept a file containing NaN. Use DRY RUN only, or get a bundle whose MANIFEST says
"playback_guard  present".""",
            """   * THIS BUNDLE HAS NO PLAYBACK GUARD (see the top of this file): no per-write clamp, no speed or
     acceleration limit, NaN passes the peak check. Dry run only.
"""),
    True: ("""The guard is REQUIRED for motor use and is in this build. Its limits: +/-40 deg rig angle, at
most 600 deg/s per write. READ THE SAFETY SECTION (3) BEFORE THE ARM MOVES.""",
           """   * The playback guard is new: its first real-motor use is tomorrow's session. Watch the first play
     of every file with a hand on the E-stop.
"""),
}

START_HERE = r"""PENDULUM RIG BUNDLE  -  START HERE
==================================
Built {date} from commit {sha7} ({sha}).
Scorer {score_id}, model {model_hash}. {n_lib} scenario files in library\ ({n_out} left out; see
library\INDEX.csv for each one and why).
Everything here was made on the analysis PC; this PC needs no Python and nothing installed.

PLAYBACK GUARD: {guard}
{guard_banner}
StretchExperiment's Angle Limit fields do NOT bound these players: the arm has gone to +63 / -81 deg
with those fields at +/-25. The E-stop / amplifier fault is the real protection; keep it within reach.
Results from this rig are NOT validated yet: do not report scores from a real rig session.


0. WHAT IS IN THIS FOLDER
   PendulumDesigner.exe     the main tool (32-bit; LabVIEW 2011's DataSocket is 32-bit)
   PendulumDesigner64.exe   the same program at 64-bit, if the 32-bit one says DataSocket is not registered
   *.exe.config             keep each beside its exe: lets it run on .NET 3.5 or .NET 4.x
   library\                 ready-made scenario files; library\INDEX.csv lists them (peak, length, the
                            fastest change between two 10 ms writes: the 5 deg sync pulses alone are 500)
   smoke_5deg.csv           a slow +/-5 deg sine, 20 s: the FIRST thing to play
   PLAY.bat, DRYRUN.bat, PICK_VARIABLE.bat, _run.bat, play_setpoints.ps1
                            the plain player: drag a .csv onto PLAY.bat (no Designer needed)
   bench_log.csv            blank run log for files played with PLAY.bat (open in Excel or Notepad)
   logs\                    copy StretchExperiment's .bin logs here
   MANIFEST.txt             every file with its SHA256 and the commit it came from


1. PREREQUISITES ON THE RIG PC
   * Windows 7 or later. Nothing to install: the exe runs on the .NET that Windows already has
     (3.5 on Windows 7, 4.x on Windows 8 and later). If it will not start, see TROUBLESHOOTING.
   * LabVIEW (it installs NI DataSocket, which is how the program reaches the setpoint variable) and
     StretchExperiment running on the RT target as usual.
   * NI Distributed System Manager, to look at / test the setpoint variable.
   * Nothing else. No internet needed.


2. COPY
   Copy this WHOLE folder to the rig PC's desktop (do not run it from the stick: it writes trials.csv,
   trial_files\ and sent\ beside the program). Optional: compare a file with MANIFEST.txt using
       certutil -hashfile PendulumDesigner.exe SHA256


3. SAFETY SET-UP IN STRETCHEXPERIMENT (every session, before anything plays)
   Working settings measured on this rig (horizontal arm):
   * Integration Term = 0. P term sets the swing frequency (P=1 gives about 1 Hz).
   * Derivative Term below 0.01 (0.01 already kills the swing). PID FIR Filter ON only when D > 0
     (D without the filter screeches; the filter with D = 0 made a swing GROW, to -81 deg).
   * Controller Output Range: +/-20 clips and damps, +/-50 does not.
   * Known trips (amplifier fault, SET lamp flashing): speed 300 (overcurrent); P 4.5 with I 0.5 and
     D 0.1; D 0.1 without the filter; moving within 5 deg of an angle limit.
   * Recover from a trip: release the E-Stop, press SET on the hardware panel, Manual Control ->
     Speed 10 -> Goto Position 0.
   * Angle Limit CCW/CW: set them, but they only bound StretchExperiment's own Stretch profile. They do
     NOT stop this program. Keep the E-stop within reach of whoever presses PLAY.
   * The owner's limits (2026-11-17): at most +/-40 deg rig angle and 600 deg/s per write. Do not raise
     the Designer's Max |angle| above 40 and do not pass a bigger -MaxDeg to PLAY.bat.
   * The arm resonates near 6.3 Hz and over-travels fast content (about 1.3x at 3 Hz, 2x at 5 Hz).
     Fast scenarios (clonus, the chirp) can move the arm further than the file says. Start with the
     slow ones and watch the first play of every new file.


4. ONCE, BEFORE THE FIRST REAL PLAY
   a. Pick the variable: open PendulumDesigner.exe, press Pick..., expand the RT target ->
      "variables - network - RT (separate)" -> M1JointAngleDesired, OK. (Or double-click
      PICK_VARIABLE.bat. Both save variable_url.txt.) If picking fails, type the address, e.g.
      psp://<rig IP>/variables - network - RT (separate)/M1JointAngleDesired
   b. In NI Distributed System Manager, with the Angle Limits at +/-10, type 5 into that variable.
      The arm should go to 5 deg and STAY. If it snaps back, something on the target owns the variable;
      try M1AngleSetPoint instead. Put it back to 0.
   c. Smoke test: arm at 0 (Manual Control -> Speed 10 -> Goto Position 0). Start StretchExperiment's
      log. Drag smoke_5deg.csv onto PLAY.bat, press Enter. The arm sways +/-5 deg slowly for 20 s.
      Stop the log, copy the .bin into logs\. (Back on the analysis PC this is checked with
      `python -m neuromech.session check-log --bin <that .bin> --player smoke_5deg.csv`.)
      If the log only records inside a Stretch trial, start a long Stretch-mode hold and play over it.


5. THE DESIGNER  (PendulumDesigner.exe)
   Double-click it. If Windows says "Windows protected your PC", choose More info -> Run anyway (it is
   not signed). If it says DataSocket is not registered for its width, use PendulumDesigner64.exe.
   a. Design a movement:  "Aim at a MAS grade" (pick 0 / 1 / 1+ / 2 / 3 / 4) -> Design it.  Or tick
      parameters and type targets, or move the knobs by hand. The right side shows the PT7 score, zone,
      a rough MAS, the seven parameters, the rig's motion (top plot) and the knee angle the phone should
      show (bottom plot). Check "Rig: peak ... deg" in the text: it must be under 40.
   b. Or load a scenario: Open file... -> library\<name>.csv (see library\INDEX.csv for what each is).
      The Designer re-scores it with its own copy of the scorer and shows both plots.
   c. DRY RUN FIRST, every new movement: tick "Dry run: no motion, no log", press PLAY. Nothing is sent
      to the rig; the red cursor runs along the plots and the status line reports the timing
      ("updates every 10 ms ..."). If the 95th percentile is over 25 ms, close other programs.
   d. Real play: untick Dry run, type a trial label, arm at 0, start the phone, start StretchExperiment's
      log, press PLAY and confirm. STOP ramps the arm back to 0 at 10 deg/s. At the end it holds the
      last angle 2 s, then ramps to 0.
   e. The trial log at the bottom fills itself in and saves trials.csv beside the program after every
      change. Type each StretchExperiment log's file name into encoder_file, and the phone's trial number
      into phone_trial if you can read it. The movement played is kept in trial_files\T###.csv.
   Do not close the Designer or switch the PC off while it plays: the rig keeps the last value it was
   sent. Use STOP and wait for "back at 0".


6. THE PLAIN PLAYER  (no Designer)
   Drag a library\ file onto DRYRUN.bat first (checks the file and the PC's timing, moves nothing).
   Then: arm at 0 -> start the phone -> start the log -> drag the file onto PLAY.bat -> Enter.
   q or Esc stops and ramps back to 0. Write the file name and the log's name in bench_log.csv.
   PLAY.bat refuses a file over its angle limit, with a header row, or not starting at 0.


7. WHAT TO EXPECT ON THIS (HORIZONTAL) RIG
   The arm swings in a horizontal plane, so the phone's accelerometer sees no in-plane gravity: the
   app's angle reads near 0 / inverted / drifting whatever is played. Only timing-type results are
   comparable with the phone here: f (frequency), N (number of swings), the decay (tau) and whether
   the app captures and auto-stops the trial. A0, R2n, area_ratio and PT7 from the phone are NOT
   meaningful on this rig. The Designer's "flat-rig phone" column says which are comparable.


8. WHAT IS NOT VALIDATED YET  (tell anyone who sees a result)
{unvalidated_guard}   * The run-to-run scoring (release alignment, tracking limits from the chirp) is checked in
     SIMULATION ONLY. Do not report scores from a real rig session yet.
   * The scenario expectations are simulation results on the current scorer ({score_id}).
   * The library's scenarios are not MAS settings: grades 2-4 are PT7-band presets, not clinical MAS.


9. BRING BACK  (on the stick, in this folder)
   * trials.csv, trial_files\ and sent\  (written beside PendulumDesigner.exe)
   * logs\  with every StretchExperiment .bin you recorded
   * bench_log.csv if you used PLAY.bat
   * the phone's export (and its -manifest.json)
   * a note of the StretchExperiment settings used (P, I, D, filter, output range, speeds, limits)
   On the analysis PC:
       python -m neuromech.match_trials --kit <this folder> --phone-manifest <export>-manifest.json --encoder-dir <this folder>\logs
   pairs each trial with its phone recording and encoder log.


TROUBLESHOOTING
   * Exe does not start / ".NET Framework" message: keep the .exe.config beside the exe (it lets the
     exe use .NET 4.x, built into Windows 8 and later, or .NET 3.5, built into Windows 7). Nothing needs
     installing. If it still will not start, use PLAY.bat with library\ files instead (PowerShell only).
   * "NI DataSocket is not registered for this program's width": use the other exe (32 vs 64-bit).
   * "cannot connect to psp://...": check the RT target's IP and the variable name in Distributed
     System Manager; Pick... again.
   * "20 writes in a row failed": the rig holds the last value it received. E-stop if the arm is not
     where it should be, then Manual Control -> Goto Position 0.
   * PLAY.bat window flashes and closes: right-click play_setpoints.ps1 -> Properties -> Unblock.
"""


def main(argv=None):
    a = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    a.add_argument("--out", default=DEFAULT_OUT, help="bundle folder (default neuromech/out/rig_bundle)")
    a.add_argument("--zip-dir", default=None, help="where the zip goes (default: beside --out)")
    a.add_argument("--skip-library", action="store_true", help="reuse neuromech/out/ instead of rebuilding it")
    a.add_argument("--allow-near-edge", action="store_true", help="passed to neuromech.build")
    a.add_argument("--allow-dirty", action="store_true", help="bundle uncommitted changes (recorded in MANIFEST)")
    x = a.parse_args(argv)

    sha, dirty = source_state()
    if dirty and not x.allow_dirty:
        raise SystemExit("uncommitted changes in files that go into the bundle:\n  " + "\n  ".join(dirty)
                         + "\ncommit them or pass --allow-dirty")
    if not x.skip_library:
        build_library(x.allow_near_edge)
    prov = check_library_current()

    out = os.path.abspath(x.out)
    if os.path.exists(out):
        shutil.rmtree(out)
    os.makedirs(out)
    exes, csc_ver = compile_designer(out)
    for f in UK.SCRIPTS:
        crlf_copy(os.path.join(UK.PLAYER, f), os.path.join(out, f))
    UK.smoke_file(os.path.join(out, "smoke_5deg.csv"))
    guard, guard_line = guard_status()
    rows = export_library(os.path.join(out, "library"), guard)
    os.makedirs(os.path.join(out, "logs"))
    with open(os.path.join(out, "logs", "PUT_STRETCHEXPERIMENT_LOGS_HERE.txt"), "w", newline="\r\n") as fh:
        fh.write("Copy every StretchExperiment .bin log from this session into this folder.\n")
    with open(os.path.join(out, "bench_log.csv"), "w", newline="", encoding="ascii") as fh:
        csv.writer(fh, lineterminator="\r\n").writerow(BENCH_LOG_COLS)

    now = datetime.now(timezone.utc)
    n_lib = sum(r["included"] == "yes" for r in rows)
    with open(os.path.join(out, "START_HERE.txt"), "w", newline="\r\n", encoding="ascii") as fh:
        fh.write(START_HERE.format(date=now.strftime("%Y-%m-%d %H:%M UTC"), sha=sha, sha7=sha[:7],
                                   score_id=prov["score_formula_id"], model_hash=prov["model_hash"],
                                   n_lib=n_lib, n_out=len(rows) - n_lib, guard=guard_line,
                                   guard_banner=GUARD_TEXT[guard is not None][0],
                                   unvalidated_guard=GUARD_TEXT[guard is not None][1]))
    privacy_scan(out)
    meta = dict(bundle="Pendulum rig bundle (neuromech emulator)", source_commit=sha,
                source_dirty_files=dirty, build_date_utc=now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                score_formula_id=prov["score_formula_id"], designer_score_formula_id=cs_score_formula_id(),
                model_hash=prov["model_hash"], designer_exes=exes,
                dotnet=".NET Framework 3.5 build (CLR 2.0); .exe.config also allows CLR 4.x",
                compiler=csc_ver, python=platform.python_version(), playback_guard=guard_line,
                motor_use=("allowed after the guard's own bench checks" if guard is not None
                           else "NO: built before the playback guard merged"),
                limit_deg=LIMIT_DEG, rate_limit_dps_per_write=RATE_DPS,
                library_files=n_lib, library_left_out=len(rows) - n_lib)
    if meta["designer_score_formula_id"] != meta["score_formula_id"]:
        raise SystemExit(f"the Designer's scorer ({meta['designer_score_formula_id']}) is not the library's "
                         f"({meta['score_formula_id']}); port the scorer change to PtScore.cs first")
    files = write_manifest(out, meta)

    stem = f"rig_bundle_{now.strftime('%Y%m%d')}_{sha[:7]}"
    zdir = os.path.abspath(x.zip_dir or os.path.dirname(out))
    zpath = os.path.join(zdir, stem + ".zip")
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
        for dp, dns, fs in os.walk(out):
            dns.sort()
            for f in sorted(fs):
                p = os.path.join(dp, f)
                z.write(p, stem + "/" + os.path.relpath(p, out).replace(os.sep, "/"))
    print(f"bundle -> {out}  ({len(files) + 2} files, {n_lib} library files, {len(rows) - n_lib} left out)")
    print(f"guard  -> {guard_line}")
    print(f"zip    -> {zpath}  ({os.path.getsize(zpath) / 1e6:.1f} MB)")
    if dirty:
        print("WARNING: built with uncommitted changes:", ", ".join(dirty))
    return out, zpath


if __name__ == "__main__":
    main()
