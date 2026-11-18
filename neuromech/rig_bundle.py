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
  PLAY.bat DRYRUN.bat PICK_VARIABLE.bat PICK_ENCODER.bat _run.bat play_setpoints.ps1 RigGuard.cs
                        the plain player and its safety rules (usb_kit's own list; CRLF, ASCII; PLAY refuses
                        to run without RigGuard.cs)
  RIG_CHECKLIST.txt     the operator procedure (neuromech/designer/RIG_CHECKLIST.txt, CRLF)
  library/<sid>.csv     every built scenario that passes neuromech.rig_guard.check_trajectory(limit_deg=40), the
                        same rules the players apply (40 deg, 6 deg per 10 ms write, predicted delivered peak,
                        NaN), as player files (t_s, setpoint_deg, no header, starting at 0), plus R-chirp.
                        Every file is read back from the bundle with rig_guard.check_file before it ships.
                        Without the guard: a raw 40 deg / 600 deg/s check and a NOT FOR MOTOR USE stamp.
  library/INDEX.csv     per file: peak, step per write, predicted delivered peak, scale_needed, and the reason
                        for anything left out. Expected scores stay on the analysis PC (the bench grades blind).
  library_x0.70/        the labelled scaled render (owner, 2026-11-17): everything after the 5 deg sync pulses
                        x0.70, so a 55 deg release becomes 38.5. An invariance probe only: its expected scores
                        differ from the 1:1 library. Same guard check, own INDEX.csv and README.txt.
  smoke_5deg.csv        a slow 5 deg sine for first contact and the check-log test
  bench_log.csv         a blank run log for library files played with PLAY.bat
  logs/                 where StretchExperiment's .bin logs go
  START_HERE.txt        what is here, the limits, what is not validated; points to RIG_CHECKLIST (CRLF)
  MANIFEST.txt / MANIFEST.json   every file with SHA256, the source commit, build date, scorer/model version,
                        the guard status, and motor_use (YES only if the guard checked every library file)

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
LIB_SCALE = 0.70               # owner decision 2026-11-17: the labelled scaled library set
SCALED_LABEL = ("SCALED RENDER ({scale:.2f}x) - INVARIANCE PROBE ONLY; expected scores differ from the 1:1 library.\n"
                "Everything after the 5 deg sync pulses is multiplied by {scale:.2f} (pulses unchanged). Never compare a\n"
                "score from these files with the 1:1 library's expected values; INDEX.csv lists what was left out and why.")
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
    # git() strips the output, which eats the first line's leading status column: split on the path instead
    return sha, [ln.split(None, 1)[1] for ln in dirty.splitlines() if ln.strip()]


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
            if name == "check_trajectory":
                return (lambda t, sp: G.check_trajectory(t, sp, limit_deg=LIMIT_DEG)), \
                    f"present: neuromech.rig_guard.check_trajectory(limit_deg={LIMIT_DEG:g}) on every library file"
            return fn, f"present: neuromech.rig_guard.{name} on every library file"
    raise SystemExit("neuromech.rig_guard exists but exposes none of " + ", ".join(GUARD_FUNCS)
                     + "; update rig_bundle.GUARD_FUNCS to its check function")


def guard_verdict(fn, t, sp, info=None):
    """None if the guard passes the file, else its reason. Accepts the shapes a check function might return:
    None/True/'' (pass), a string (reason), False, a dict or an object with ok/reason(s)."""
    r = fn(t, sp)
    if r is None or r is True or r == "":
        return None
    if r is False:
        return "refused by rig_guard"
    if isinstance(r, str):
        return r
    if info is not None:
        for k in ("predicted_peak_deg", "raw_peak_deg", "max_step_deg", "scale_needed"):
            v = r.get(k) if isinstance(r, dict) else getattr(r, k, None)
            if v is not None and np.isfinite(float(v)):
                info[k] = round(float(v), 2) if k == "scale_needed" else round(float(v), 1)
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


def sync_end_index(fs=1000.0):
    """First sample after motor_reference's square sync pulses (lead, then PULSE_COUNT x (pulse + gap))."""
    import motor_reference as mr
    return int(round(mr.LEAD_S * fs)) + mr.PULSE_COUNT * (int(round(mr.PULSE_WIDTH_S * fs)) + int(round(mr.PULSE_GAP_S * fs)))


def scaled_render(t, sp, scale):
    """The file with everything after the sync pulses multiplied by scale; the 5 deg pulses stay 5 deg so the
    alignment edge is unchanged. The signal is 0 where the scaling starts, so nothing steps."""
    fs = 1.0 / float(t[1] - t[0])
    i = sync_end_index(fs)
    if abs(float(sp[i - 1])) > 1e-9 or abs(float(sp[i])) > 1e-9:
        raise ValueError("the file is not at 0 after the sync pulses; not a motor_reference preamble")
    out = np.array(sp, dtype=float, copy=True)
    out[i:] *= scale
    return out


def export_library(out_lib, guard=None, scale=1.0):
    """Player files for every built scenario the guard (or the raw fallback) passes, and an index of all.
    scale != 1: the labelled scaled render (an invariance probe), file names <sid>_x<scale>.csv, no chirp."""
    import pandas as pd
    from neuromech import session as SS
    man = pd.read_csv(os.path.join(B.OUT, "scenario_manifest.csv"))
    os.makedirs(out_lib)
    tag = "" if scale == 1.0 else f"_x{scale:.2f}"
    rows = []

    def add(sid, group, title, t, sp):
        if scale != 1.0:
            sp = scaled_render(t, sp, scale)
        peak = float(np.max(np.abs(sp)))
        info = {}
        why = guard_verdict(guard, t, sp, info) if guard is not None else raw_verdict(t, sp)
        name = sid + tag + ".csv"
        if why is None:
            ilc.write_player(os.path.join(out_lib, name), t, sp)
        rows.append(dict(file="" if why else name, sid=sid, scale=f"{scale:.2f}", group=group, title=title,
                         duration_s=round(float(t[-1]), 1), peak_deg=round(peak, 1),
                         max_step_per_write_deg=round(per_write_rate(t, sp) * WRITE_MS / 1000.0, 2),
                         predicted_peak_deg=info.get("predicted_peak_deg", ""),
                         scale_needed=info.get("scale_needed", ""),
                         checked_by="rig_guard" if guard is not None else "raw fallback",
                         included="yes" if why is None else "NO: " + why))

    if scale == 1.0:
        t, sp, _, _, _ = SS.chirp_command()
        add("R-chirp", "R0", "identification chirp 0.2-10 Hz, 8 deg (play first in a session)", t, sp)
    for _, r in man.iterrows():
        a = np.genfromtxt(os.path.join(B.OUT, "setpoints", f"{r.sid}.csv"), delimiter=",", names=True)
        add(r.sid, r.group, r.title, a["t_s"], a["setpoint_deg"])
    with open(os.path.join(out_lib, "INDEX.csv"), "w", newline="", encoding="ascii", errors="replace") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]), lineterminator="\r\n")
        w.writeheader(); w.writerows(rows)
    if scale != 1.0:
        with open(os.path.join(out_lib, "README.txt"), "w", newline="\r\n", encoding="ascii") as fh:
            fh.write(SCALED_LABEL.format(scale=scale) + "\n")
    return rows


def verify_library(folder):
    """Read every library file back from the built bundle the way the players do (rig_guard.check_file) and
    refuse the bundle if any fails. -> how many files were checked."""
    from neuromech import rig_guard as G
    n = 0
    for sub in sorted(os.listdir(folder)):
        if not sub.startswith("library"):
            continue
        for f in sorted(os.listdir(os.path.join(folder, sub))):
            if f.endswith(".csv") and f != "INDEX.csv":
                r = G.check_file(os.path.join(folder, sub, f), limit_deg=LIMIT_DEG)
                if not r["ok"]:
                    raise SystemExit(f"{sub}/{f} fails rig_guard when read back: {r['reason']}")
                n += 1
    return n


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
It was built WITHOUT the playback guard (neuromech.rig_guard was not importable), so its library was not
checked by the rules the players apply. Use DRY RUN only, or get a bundle whose MANIFEST says
"motor_use  YES".""",
            """   * THIS BUNDLE WAS NOT CHECKED BY THE PLAYBACK GUARD (see the top of this file). Dry run only.
"""),
    True: ("""The players refuse anything over the 40 deg hard limit, any change over 6 deg in one 10 ms write
(600 deg/s), anything the identified rig response predicts would overshoot past the limit, and NaN.
They re-check every write and stop + ramp to 0 if that check fires. Every file in library\\ and
library_x{scale:.2f}\\ passed that check when this bundle was built. It is software in one Windows
process: THE E-STOP IS THE PROTECTION. Stop at the first surprise.""",
           """   * The playback guard is new; this is its first use on the real motor. Watch the first play of every
     file with a hand on the E-stop.
"""),
}

START_HERE = r"""PENDULUM RIG BUNDLE  -  START HERE
==================================
Built {date} from commit {sha7} ({sha}).
Scorer {score_id}, model {model_hash}.
{n_lib} files in library\ (1:1; {n_out} left out) and {n_sc} in library_x{scale:.2f}\ ({n_sc_out} left out).
library_x{scale:.2f}\ is a SCALED RENDER ({scale:.2f}x) - INVARIANCE PROBE ONLY; expected scores differ from the
1:1 library. Each folder's INDEX.csv lists every scenario and why any was left out.
Everything here was made on the analysis PC; this PC needs no Python and nothing installed.

PLAYBACK GUARD: {guard}
{guard_banner}
StretchExperiment's Angle Limit fields do NOT bound these players: the arm has gone to +63 / -81 deg
with those fields at +/-25.
Results from this rig are NOT validated: do not report PT scores from a rig session.

>>> THE PROCEDURE IS RIG_CHECKLIST.txt. Print it or keep it open, and tick it off in order. <<<
This file says what is here and what the limits are; the checklist says what to do.


0. WHAT IS IN THIS FOLDER
   RIG_CHECKLIST.txt        THE PROCEDURE: hard stops and margin, the staged first run, during a run
   PendulumDesigner.exe     the main tool (32-bit; LabVIEW 2011's DataSocket is 32-bit)
   PendulumDesigner64.exe   the same program at 64-bit, if the 32-bit one says DataSocket is not registered
   *.exe.config             keep each beside its exe: lets it run on .NET 3.5 (Windows 7) or 4.x (Windows 8+)
   RigGuard.cs              the safety rules both players use. PLAY.bat refuses to run without it.
                            Never delete or edit it.
   PLAY.bat, DRYRUN.bat, PICK_VARIABLE.bat, PICK_ENCODER.bat, _run.bat, play_setpoints.ps1
                            the plain player: drag a .csv onto DRYRUN.bat / PLAY.bat (no Designer needed)
   smoke_5deg.csv           a slow +/-5 deg sine, 20 s: the first thing that moves the arm (stage b)
   library\                 the scenarios at 1:1 rig angle that fit the limits (most do not: the
                            release is at about 55 deg)
   library_x{scale:.2f}\            the scenarios with everything after the 5 deg sync pulses scaled by
                            {scale:.2f}. SCALED RENDER: an invariance probe only. Label such trials "x{scale:.2f}".
                            INDEX.csv columns: peak, largest step per 10 ms write, predicted delivered
                            peak, and the reason for anything left out.
   bench_log.csv            blank run log for files played with PLAY.bat
   logs\                    copy StretchExperiment's .bin logs here
   MANIFEST.txt             every file with its SHA256, the commit, and the guard status


1. PREREQUISITES ON THE RIG PC
   * Windows 7 or later. Nothing to install: the exe runs on the .NET that Windows already has.
   * LabVIEW / StretchExperiment as usual (LabVIEW installs NI DataSocket, which the players use to
     write the setpoint variable), and NI Distributed System Manager.
   * No internet, no admin rights needed.


2. COPY
   Copy this WHOLE folder to the rig PC's desktop and run it from there, not from the stick (it writes
   trials.csv, trial_files\ and sent\ beside the programs). Optional check against MANIFEST.txt:
       certutil -hashfile PendulumDesigner.exe SHA256


3. SAFETY - the short version (RIG_CHECKLIST.txt has the full list)
   * Before anything plays: note the arm's hard-stop angles and check there is a clear margin from
     40 deg plus overshoot to BOTH. If not, LOWER the limit for the session (Designer "Max |angle|" only
     goes down; PLAY.bat file -MaxDeg 25). The limit cannot be raised above 40.
   * Nobody in the arm's swing envelope. One person whose only job is the E-stop.
   * Staged first run, do not skip a stage, stop at the first surprise:
       a. DRYRUN.bat smoke_5deg.csv (moves nothing)    b. PLAY.bat smoke_5deg.csv (+/-5 deg, 20 s)
       c. Designer default MAS design at "Rig amplitude scale" 0.30    d. the same at 0.50
       e. the same at 0.70 (the default)
   * STOP (Designer) or q / Esc (PLAY.bat) ramps the setpoint to 0 at 10 deg/s. NEVER stop by closing the
     window or with Ctrl+Break: the rig would hold the last setpoint. If writes fail or the program
     crashes, the rig HOLDS the last setpoint: use the E-stop.
   * Arm at 0 with Manual Control BEFORE starting, then leave Manual Control alone and run no Stretch
     profile while a player waits or plays (the players cannot lock out LabVIEW's own writers; PLAY
     re-reads the setpoint after Enter and refuses if it moved).
   * "RIG HOLDING LAST SETPOINT -- USE THE E-STOP": E-stop. The players leave sent\HOLDING.flag and the next
     start asks you to acknowledge it (PLAY: type YES; Designer: Yes). Do that only once the arm is at
     rest and the link is checked.
   * "STOPPED BY THE GUARD" / "STOPPED BY THE ENCODER WATCHDOG": do not repeat that run; bring sent\ back.
   * Only one player can drive the rig at a time.
   StretchExperiment settings that worked on this rig: Integration Term 0; Derivative Term below 0.01
   (0.01 kills the swing); PID FIR Filter ON only when D > 0 (D without it screeches; the filter with
   D = 0 made a swing grow, to -81 deg); Controller Output Range low during playback. Known trips
   (amplifier fault, SET lamp flashing): speed 300 (overcurrent); P 4.5 with I 0.5 and D 0.1; D 0.1
   without the filter; moving within 5 deg of an angle limit. Recover: release the E-Stop, press SET,
   Manual Control -> Speed 10 -> Goto Position 0. The predicted peak assumes the identified P/I/D: if
   the gains change, fast content is no longer covered by the prediction.


4. ONCE: THE VARIABLES
   * Setpoint: PICK_VARIABLE.bat (or Pick... in the Designer) -> RT target -> "variables - network - RT
     (separate)" -> M1JointAngleDesired. Test it in Distributed System Manager with the Angle Limits at
     +/-10: type 5, the arm goes to 5 and STAYS (if it snaps back, try M1AngleSetPoint). Back to 0.
   * Encoder watchdog (OPTIONAL): the variable that publishes the arm's ACTUAL position is not yet
     confirmed. Look for it in Distributed System Manager (it changes when the arm is moved by hand
     or by Manual Control). If you find it, PICK_ENCODER.bat; otherwise every run says "encoder
     watchdog OFF", which is expected.


5. THE DESIGNER  (PendulumDesigner.exe)
   If Windows says "Windows protected your PC": More info -> Run anyway (it is not signed).
   a. "Aim at a MAS grade" -> Design it (or targets, or the knobs). The text shows the PT7 score, the
      seven parameters, the commanded peak AND the predicted delivered peak. Over the limit, it offers
      to scale the movement down: say yes or change the design.
   b. Or Open file... -> library\<name>.csv or library_x{scale:.2f}\<name>_x{scale:.2f}.csv.
   c. Dry run first ("Dry run: no motion, no log"), then the real play: label, arm at 0, phone, log, PLAY.
   d. Fill encoder_file (and phone_trial) in the trial log; it saves trials.csv on every change.


6. WHAT TO EXPECT ON THIS (HORIZONTAL) RIG
   The arm swings in a horizontal plane, so the phone's accelerometer sees no in-plane gravity: the
   app's angle reads near 0 / inverted / drifting. Only f, N, the decay (tau) and whether the app
   captures and auto-stops are comparable with the phone. Do NOT report PT7, A0, R2n or area_ratio
   from this rig.


7. WHAT IS NOT VALIDATED
{unvalidated_guard}   * The overshoot prediction uses the rig response measured from earlier logs (resonance about 6.35 Hz,
     with a spread of variants); saturation, windup and a change of gains are not in it.
   * Release alignment and tracking limits used to score rig runs are checked in SIMULATION ONLY.
   * Scenario expectations are simulation results on {score_id}. Grades 2-4 are PT7-band presets,
     not clinical MAS. library_x{scale:.2f}\ files do not have the 1:1 library's expected scores.


8. BRING BACK  (on the stick, in this folder)
   sent\ (every write, refusal, guard event and HOLDING.flag), trials.csv, trial_files\, logs\ with every
   StretchExperiment .bin, bench_log.csv if PLAY.bat was used, the phone export and its -manifest.json,
   the ticked RIG_CHECKLIST (hard-stop angles, limit used), and the StretchExperiment settings
   (P, I, D, filter, output range, speeds, limits). On the analysis PC:
       python -m neuromech.match_trials --kit <this folder> --phone-manifest <export>-manifest.json --encoder-dir <this folder>\logs


TROUBLESHOOTING
   * Exe does not start: keep the .exe.config beside it. If it still will not, use PLAY.bat.
   * "NI DataSocket is not registered for this program's width": use the other exe (32 vs 64-bit).
   * PLAY.bat says RigGuard.cs is missing: copy the whole folder again; never run PLAY.bat alone.
   * "cannot connect to psp://...": check the RT target's IP and the variable in Distributed System Manager.
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
    # the kit's own file list (usb_kit), so a script or guard file added there reaches this bundle too
    for f in UK.SCRIPTS + (UK.GUARD_SRC,):
        crlf_copy(os.path.join(UK.PLAYER, f), os.path.join(out, f))
    crlf_copy(UK.CHECKLIST, os.path.join(out, "RIG_CHECKLIST.txt"))
    UK.smoke_file(os.path.join(out, "smoke_5deg.csv"))
    guard, guard_line = guard_status()
    rows = export_library(os.path.join(out, "library"), guard)
    srows = export_library(os.path.join(out, f"library_x{LIB_SCALE:.2f}"), guard, LIB_SCALE)
    n_sc = sum(r["included"] == "yes" for r in srows)
    rechecked = verify_library(out) if guard is not None else 0
    motor_ok = (guard is not None and all(r["checked_by"] == "rig_guard" for r in rows + srows)
                and rechecked == sum(r["included"] == "yes" for r in rows + srows))
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
                                   n_sc=n_sc, n_sc_out=len(srows) - n_sc, scale=LIB_SCALE,
                                   guard_banner=GUARD_TEXT[motor_ok][0].format(scale=LIB_SCALE),
                                   unvalidated_guard=GUARD_TEXT[motor_ok][1]))
    privacy_scan(out)
    meta = dict(bundle="Pendulum rig bundle (neuromech emulator)", source_commit=sha,
                source_dirty_files=dirty, build_date_utc=now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                score_formula_id=prov["score_formula_id"], designer_score_formula_id=cs_score_formula_id(),
                model_hash=prov["model_hash"], designer_exes=exes,
                dotnet=".NET Framework 3.5 build (CLR 2.0); .exe.config also allows CLR 4.x",
                compiler=csc_ver, python=platform.python_version(), playback_guard=guard_line,
                motor_use=("YES: every included library file passed the guard, and again when read back from "
                           "the bundle (follow RIG_CHECKLIST.txt)" if motor_ok
                           else "NO: the playback guard did not check every library file"),
                guard_rechecked_files=rechecked,
                limit_deg=LIMIT_DEG, rate_limit_dps_per_write=RATE_DPS,
                library_files=n_lib, library_left_out=len(rows) - n_lib,
                scaled_library=f"library_x{LIB_SCALE:.2f}: scaled render {LIB_SCALE:.2f}x after the sync pulses, invariance probe only",
                scaled_library_files=n_sc, scaled_library_left_out=len(srows) - n_sc)
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
    print(f"bundle -> {out}  ({len(files) + 2} files, {n_lib} library files, {len(rows) - n_lib} left out; "
          f"library_x{LIB_SCALE:.2f}: {n_sc} files, {len(srows) - n_sc} left out)")
    print(f"guard  -> {guard_line}")
    print(f"zip    -> {zpath}  ({os.path.getsize(zpath) / 1e6:.1f} MB)")
    if dirty:
        print("WARNING: built with uncommitted changes:", ", ".join(dirty))
    return out, zpath


if __name__ == "__main__":
    main()
