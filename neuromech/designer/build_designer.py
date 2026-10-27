"""Compile PendulumDesigner.exe for the rig PC (Windows 7, stock .NET 3.5: nothing to install there).

    python neuromech/designer/build_designer.py [--out DIR]

Uses the .NET Framework 3.5 C# compiler that ships with Windows, so the exe targets the CLR 2.0 runtime
Windows 7 has out of the box. Sources: every .cs in this folder (the parity/ console harness excluded).
"""
from __future__ import annotations

import argparse
import glob
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CSC = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "Microsoft.NET", "Framework", "v3.5", "csc.exe")
EXE = "PendulumDesigner.exe"


def build(out_dir: str = HERE) -> list[str]:
    """Two builds of the same program. PendulumDesigner.exe is 32-bit: LabVIEW 2011 is 32-bit, so the rig PC's
    NI DataSocket is registered 32-bit. PendulumDesigner64.exe runs at the OS's native width, for a PC whose
    DataSocket came with 64-bit LabVIEW (this one has both). Either says so if it cannot load DataSocket."""
    if not os.path.exists(CSC):
        raise SystemExit(f"no .NET 3.5 compiler at {CSC} (Windows Features -> .NET Framework 3.5)")
    src = sorted(glob.glob(os.path.join(HERE, "*.cs")))
    os.makedirs(out_dir, exist_ok=True)
    made = []
    for name, plat in ((EXE, "x86"), (EXE.replace(".exe", "64.exe"), "anycpu")):
        exe = os.path.join(out_dir, name)
        cmd = [CSC, "-nologo", "-optimize+", "-target:winexe", f"-platform:{plat}",
               f"-out:{exe}", "-r:System.Windows.Forms.dll", "-r:System.Drawing.dll", "-r:System.Core.dll"] + src
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            raise SystemExit(r.stdout + r.stderr)
        made.append(exe)
    return made


if __name__ == "__main__":
    a = argparse.ArgumentParser(); a.add_argument("--out", default=HERE)
    for exe in build(a.parse_args().out):
        print(exe)
    sys.exit(0)
