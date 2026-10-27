"""Render the emulator research report (HTML) with the scenario appendix filled from the manifest.

    python neuromech/report/build_report.py   -> neuromech/report/out/index.html (+ fig/*.png)
"""
import html
import os
import shutil

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
NM = os.path.dirname(HERE)
OUT = os.path.join(HERE, "out"); os.makedirs(os.path.join(OUT, "fig"), exist_ok=True)
# A figure that nm_figs could not build is skipped, not fatal. nm_rig_ilc
# needs a measured rig response that a clean clone cannot have (see
# nm_figs.RIG_FRF); nm_dryrun needs a dry-run session that also carries an
# ilc_demo.csv, which `python -m neuromech.dryrun` does NOT write -- only
# dryrun.ilc_demo() does. Losing the whole report because one of six is absent
# wastes more than it protects. The missing ones are named on the way out and
# the HTML simply has no <img> for them.
_FIGS = ("nm_ladder", "nm_behaviours", "nm_matrix", "nm_rig_ilc", "nm_range", "nm_dryrun")
missing_figs = []
for f in _FIGS:
    src = os.path.join(NM, "out", "figures", f + ".png")
    if os.path.exists(src):
        shutil.copy(src, os.path.join(OUT, "fig", f + ".png"))
    else:
        missing_figs.append(f)

M = pd.read_csv(os.path.join(NM, "out", "scenario_manifest.csv"))
# `.get`, not `[]`. The R group was added to scenarios.py on 2026-10-26 and
# this map was not, so the whole report died on KeyError: 'R' -- after every
# figure had been built. A scenario group the report has no label for should
# show its letter, not take the report down.
GROUP = {"A": "PT7-band preset", "S": "Severity axis", "B": "Behaviour", "C": "Out of range",
         "D": "Artefact", "M": "Mechanism profile", "R": "Ported mechanism"}
rows = []
for _, r in M.iterrows():
    mas = r.mas if isinstance(r.mas, str) else "refused"
    cls = "refused" if mas == "refused" else ("m" + mas.replace("+", "p"))
    flags = " ".join(x for x, on in (("f&gt;2 Hz", r.flag_f_out_of_range), ("area&gt;0.55", r.flag_area_warn), ("range", r.exceeds_rig_limit)) if on)
    def n(v, d=2):
        return "" if pd.isna(v) else f"{v:.{d}f}"
    rows.append(
        f"<tr><td class=mono>{html.escape(r.sid)}</td><td>{GROUP.get(r.group, r.group)}</td><td>{html.escape(r.title)}</td>"
        f"<td class=num>{n(r.A0_deg, 1)}</td><td class=num>{n(r.R2n)}</td><td class=num>{n(r.N, 1)}</td>"
        f"<td class=num>{n(r.phi_max_ratio)}</td><td class=num>{n(r.f)}</td><td class=num>{n(r.area_ratio)}</td>"
        f"<td class=num>{n(r.pt7)}</td><td><span class='g {cls}'>{mas}</span></td>"
        f"<td class=num>{n(r.peak_active_torque_nm, 1)}</td><td class=small>{flags}</td></tr>")
tpl = open(os.path.join(HERE, "report_template.html"), encoding="utf-8").read()
page = tpl.replace("<!--SCENARIO_ROWS-->", "\n".join(rows)).replace("<!--N_SCEN-->", str(len(M)))
open(os.path.join(OUT, "index.html"), "w", encoding="utf-8").write(page)
print("wrote", os.path.join(OUT, "index.html"), f"({len(page) // 1024} KB)")
if missing_figs:
    print(f"  {len(missing_figs)} figure(s) absent, so the report has no image for them: "
          f"{', '.join(missing_figs)}")
    print("  nm_rig_ilc needs neuromech/data/rig_frf_*.npz (a measured chirp run); "
          "nm_dryrun needs a dry-run session carrying ilc_demo.csv (dryrun.ilc_demo)")
