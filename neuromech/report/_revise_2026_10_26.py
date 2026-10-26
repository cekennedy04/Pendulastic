"""One-off: revise the report template for the merged program (kept for provenance)."""
import os

HERE = os.path.dirname(os.path.abspath(__file__))
p = os.path.join(HERE, "report_template.html")
s = open(p, encoding="utf-8").read()


def rep(old, new):
    global s
    assert s.count(old) == 1, old[:70]
    s = s.replace(old, new)


rep('<div class="eyebrow">Pendulastic · Aim 3 · research report · 26 Oct 2026</div>',
    '<div class="eyebrow">Pendulastic · Aim 3 · research report · 26 Oct 2026 · revised after merging the vertical-rig work</div>')
rep('<p class="meta">Built on the team\'s Notion design',
    '<p class="meta">Revised to merge the separate vertical-rig neuromuscular-model work (branch <code>claude/mobile-app-validation-plan</code>) into one program on <code>feat/neuromech-emulator</code>. Built on the team\'s Notion design')
rep('<div class="fact"><b>45 scenarios</b><span>MAS 0–3 ladder, 11 behaviours, a habituation series, out-of-range cases for each parameter, release artefacts</span></div>',
    '<div class="fact"><b>55 scenarios</b><span>PT7-band presets, 11 behaviours, out-of-range cases, release artefacts, and 10 mechanism profiles from the vertical-rig model</span></div>')
rep('<div class="fact"><b>8 blind spots</b><span>behaviours the production scorer grades wrongly, found before touching hardware</span></div>',
    '<div class="fact"><b>9 of 9 runs</b><span>in their intended band in a full dry-run session on synthetic hardware; phone |ΔPT7| median 0.009</span></div>')
rep("""<section>
<h2>1. What the emulator has to do</h2>""", """<section>
<h2>Revision: two efforts, one program</h2>
<div class="col stack">
  <p>A second line of work had built a mechanism-based neuromuscular model for a vertical rig, a severity-grading test, and a clinician-calibration framework. Both efforts are now on one branch, and every test passes there (61, plus the Formula Node check and that model's self-test). Merging them settled four things.</p>
</div>
<div class="tbl"><table>
<thead><tr><th>Question</th><th>What the merge showed</th><th>Decision</th></tr></thead>
<tbody>
<tr><td>Which scorer?</td><td>The two analyses had used different scorers: the vertical-rig work used <code>main</code>'s older constants. Re-run on the validated scorer, its findings hold and one gets stronger. Dialled from healthy to an intended MAS 3, the tone ladder grades <b>0, 0, 0, 0</b>, then refused. At one intended severity, changing only the mechanism spreads PT7 <b>13.8×</b> (grades 4, 2 and 1). A healthy limb that falls without oscillating still grades <b>4</b>.</td><td>Validated scorer only, production convention (<code>detrend=False</code>), through one call</td></tr>
<tr><td>Which physics?</td><td>The published De Groote model swings at 0.915 Hz against our controls' 0.914 Hz, inside the vertical-rig work's own physical bound (0.80–0.97 Hz). Its nonlinear gravity also answers the literature's objection to linear pendulum models.</td><td>De Groote core; the vertical-rig model's mechanisms play as profiles until ported</td></tr>
<tr><td>Where does the leg rest?</td><td>Three sources give three answers: 118°, 125° and 142°.</td><td>A measured input: the rig's unactuated rest, plus one defined corpus method</td></tr>
<tr><td>What does the rig claim?</td><td>The ladder is calibrated to the scorer's own bands, which is circular for validating grades. A programmable knee simulator parameterised from pendulum drops is prior art (Riener &amp; Burgkart, expired US8108190B2).</td><td>Presets renamed P0–P3, not MAS. Grades come only from blind clinicians. The novelty claim is narrowed to emulating the pendulum test itself</td></tr>
</tbody></table></div>
</section>

<section>
<h2>1. What the emulator has to do</h2>""")
rep("  <p>The MAS ladder is calibrated by bisection",
    "  <p>Group M adds the vertical-rig model's ten profiles: a tone ladder, catch-and-release, two catch-and-holds, a fall without ring-down, and an end-stop. Severity and the kind of impairment can then be tested on separate axes. The PT7-band presets (formerly the \"MAS ladder\") are calibrated by bisection")
rep('  <div class="finding"><span class="tag">inversion</span>',
    """  <div class="finding"><span class="tag">compression</span><p>On the vertical-rig tone ladder, five dialled severities from healthy to intended MAS 3 get one grade (band 0), then a refusal. The scorer does not rank severity across most of the range it is meant to cover.</p></div>
  <div class="finding"><span class="tag">mechanism</span><p>At one intended severity, catch-and-hold, catch-and-release and raised tone grade 4, 2 and 1. PT7 moves about four times further when the <i>kind</i> of impairment changes than when its amount goes from none to severe. The quantities that do track severity are unnormalised: peak velocity, resting angle and A0. Every ratio parameter is confound-dominated.</p></div>
  <div class="finding"><span class="tag">inversion</span>""")
rep('<h3 class="col">A session</h3>', """<h3 class="col">The program around LabVIEW</h3>
<div class="col stack">
  <p>Everything except the VIs is built. <code>python -m neuromech.session plan</code> writes a blind run sheet, a key kept away from graders, and one command file per run in CSV and in LabVIEW's binary format. <code>session score</code> finds each run inside its log, scores the encoder (the truth) and the phone, and reports delivered against intended. <code>python -m neuromech.ilc</code> identifies the plant from a chirp and corrects any command from its own run.</p>
  <p>A dry run exercises all of it on synthetic hardware: LabVIEW-format logs through the identified plant, and a phone gyro at 59 Hz, mounted backwards, with real-size bias. It caught three bugs that would have broken the first bench session, all now fixed and tested. The result: every run lands in its intended band, the plant is recovered to 0.1 %, and the correction loop converges on every scenario. Phone agreement is limited by gyro bias. Using only the stillness the app can see before release, residual bias moves PT7 by up to about 0.15. The concrete fix for the app is to re-estimate bias once the leg has settled.</p>
</div>
<figure><img src="fig/nm_dryrun.png" alt="Left: intended, encoder-delivered and phone PT7 for nine dry-run scenarios, closely matching. Right: tracking error falling over three correction iterations for four scenarios.">
<figcaption><b>A full session, dry-run.</b> The bars are intended, delivered and phone PT7 per run. On the right, tracking error falls over one rig run per correction pass, computed through the real tool code.</figcaption></figure>
<h3 class="col">A session</h3>""")
old = s[s.index("<h2>7. Decisions and gaps</h2>"):s.index("<h2>8. What the literature supports</h2>")]
new = """<h2>7. Decisions for the owner</h2>
<div class="tbl"><table>
<thead><tr><th>#</th><th>Decision</th><th>Options</th></tr></thead>
<tbody>
<tr><td class=num>O1</td><td>Arm range</td><td>±65° at full scale (measure the mechanical limit), or render at 0.75× amplitude (A0 ≈ 41°; every ratio parameter is unchanged)</td></tr>
<tr><td class=num>O2</td><td>Actuator for torque mode</td><td>This model needs 0.8–3.7 N·m for presets P0–P3, 15 for severe and 26 for real-size clonus. The vertical-rig model's tone ladder needs up to 18 N·m, and its hard catch 62 N·m at 1,854 N·m/s. A gearbox giving 20 N·m or more covers everything except that hard catch</td></tr>
<tr><td class=num>O3</td><td>Vertical rig timing</td><td>Playback runs now on the horizontal rig, which tests the phone's gyro channel. Validating the fused angle waits for the vertical rig</td></tr>
<tr><td class=num>O4</td><td>Clinician access</td><td>At least two blind assessors per profile, to calibrate grades without circularity</td></tr>
<tr><td class=num>O5</td><td>Clasp-knife adaptation</td><td>Model it faithfully (traces drift across drops) or rest between drops. A habituation series is in the library either way</td></tr>
</tbody></table></div>
<p class="col" style="margin-top:14px">Also needed on the LabVIEW side: a setpoint-array input, feedforward, and a re-tune for tracking. The full specification is in <code>neuromech/labview/LABVIEW_PROGRAM_SPEC.md</code>.</p>
</section>

<section>
"""
s = s.replace(old, new)
rep('<tr><td>Figures (deck and publication)</td>', """<tr><td>Session planner and scorer</td><td class=mono>python -m neuromech.session plan | score</td><td>built; dry-run verified</td></tr>
<tr><td>Dry run on synthetic hardware</td><td class=mono>python -m neuromech.dryrun</td><td>9/9 runs in band</td></tr>
<tr><td>Vertical-rig mechanism model and severity test</td><td class=mono>rig_neuromuscular_model.py, severity_grading_test.py</td><td>merged; self-test passes; on the production convention</td></tr>
<tr><td>Figures (deck and publication)</td>""")
open(p, "w", encoding="utf-8").write(s)

b = os.path.join(HERE, "build_report.py")
t = open(b, encoding="utf-8").read()
t = t.replace('for f in ("nm_ladder", "nm_behaviours", "nm_matrix", "nm_rig_ilc", "nm_range"):',
              'for f in ("nm_ladder", "nm_behaviours", "nm_matrix", "nm_rig_ilc", "nm_range", "nm_dryrun"):')
t = t.replace('GROUP = {"A": "MAS ladder", "S": "Severity axis", "B": "Behaviour", "C": "Out of range", "D": "Artefact"}',
              'GROUP = {"A": "PT7-band preset", "S": "Severity axis", "B": "Behaviour", "C": "Out of range", "D": "Artefact", "M": "Mechanism profile"}')
open(b, "w", encoding="utf-8").write(t)
print("report template + builder updated")
