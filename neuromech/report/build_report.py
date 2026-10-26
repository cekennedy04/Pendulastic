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
for f in ("nm_ladder", "nm_behaviours", "nm_matrix", "nm_rig_ilc", "nm_range", "nm_dryrun"):
    shutil.copy(os.path.join(NM, "out", "figures", f + ".png"), os.path.join(OUT, "fig", f + ".png"))

M = pd.read_csv(os.path.join(NM, "out", "scenario_manifest.csv"))
GROUP = {"A": "PT7-band preset", "S": "Severity axis", "B": "Behaviour", "C": "Out of range", "D": "Artefact", "M": "Mechanism profile"}
rows = []
for _, r in M.iterrows():
    mas = r.mas if isinstance(r.mas, str) else "refused"
    cls = "refused" if mas == "refused" else ("m" + mas.replace("+", "p"))
    flags = " ".join(x for x, on in (("f&gt;2 Hz", r.flag_f_out_of_range), ("area&gt;0.55", r.flag_area_warn), ("range", r.exceeds_rig_limit)) if on)
    def n(v, d=2):
        return "" if pd.isna(v) else f"{v:.{d}f}"
    rows.append(
        f"<tr><td class=mono>{html.escape(r.sid)}</td><td>{GROUP[r.group]}</td><td>{html.escape(r.title)}</td>"
        f"<td class=num>{n(r.A0_deg, 1)}</td><td class=num>{n(r.R2n)}</td><td class=num>{n(r.N, 1)}</td>"
        f"<td class=num>{n(r.phi_max_ratio)}</td><td class=num>{n(r.f)}</td><td class=num>{n(r.area_ratio)}</td>"
        f"<td class=num>{n(r.pt7)}</td><td><span class='g {cls}'>{mas}</span></td>"
        f"<td class=num>{n(r.peak_active_torque_nm, 1)}</td><td class=small>{flags}</td></tr>")
tpl = open(os.path.join(HERE, "report_template.html"), encoding="utf-8").read()
page = tpl.replace("<!--SCENARIO_ROWS-->", "\n".join(rows)).replace("<!--N_SCEN-->", str(len(M)))
open(os.path.join(OUT, "index.html"), "w", encoding="utf-8").write(page)
print("wrote", os.path.join(OUT, "index.html"), f"({len(page) // 1024} KB)")
