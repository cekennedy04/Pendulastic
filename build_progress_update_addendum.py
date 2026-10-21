"""Append the bench-rig, test-retest and app-validation sections to the
Pendulastic progress deck.

Style is not invented here -- every font, size, colour and box position below
was read off `Pendulastic Progress Update.pptx` with python-pptx before this
script was written. See docs/reports for the source numbers on each slide.

Writes to a NEW filename; never overwrites the input.
"""
from __future__ import annotations

from pathlib import Path

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import PP_ALIGN
from pptx.util import Inches, Pt

HERE = Path(__file__).resolve().parent
SRC = HERE / "Model_Analysis_Outputs" / "Pendulastic Progress Update.pptx"
OUT = HERE / "Model_Analysis_Outputs" / "Pendulastic Progress Update_v2.pptx"

# ── palette, read off the reference deck ──────────────────────────────────────
BG = RGBColor(0xF0, 0xF5, 0xFA)   # slide background
NAVY = RGBColor(0x0C, 0x1E, 0x34)   # title + card headings
SLATE = RGBColor(0x3A, 0x58, 0x70)   # subtitles + card body
HDR = RGBColor(0x60, 0x62, 0x64)   # content-slide header
HDR2 = RGBColor(0x88, 0x88, 0x88)   # alternate header (Key Findings style)
BODY = RGBColor(0x33, 0x33, 0x33)   # column body text
STATV = RGBColor(0x16, 0x20, 0x2C)   # stat card value
STATC = RGBColor(0x4A, 0x55, 0x68)   # stat card caption
MICRO = RGBColor(0x6B, 0x76, 0x86)   # micro labels + row meta
PALE = RGBColor(0xB8, 0xCD, 0xDE)   # big card numerals
BAR = RGBColor(0x47, 0x56, 0x6E)   # filled bar
TRACK = RGBColor(0xE6, 0xE9, 0xEC)   # bar track
WHITE = RGBColor(0xFF, 0xFF, 0xFF)
PURPLE = RGBColor(0x60, 0x30, 0xA0)   # card rail, accent A
ORANGE = RGBColor(0xB0, 0x60, 0x10)   # card rail, accent B
TEAL = RGBColor(0x0E, 0x6E, 0x66)   # card rail, accent C

SERIF = "Georgia"          # title/divider only -- used sparingly on purpose
TNR = "Times New Roman"  # everything else


# ── primitives ────────────────────────────────────────────────────────────────
def add_slide(prs):
    s = prs.slides.add_slide(prs.slide_layouts[0])  # BLANK
    s.background.fill.solid()
    s.background.fill.fore_color.rgb = BG
    return s


def text(slide, left, top, width, height, runs, *, align=PP_ALIGN.LEFT,
         spacing=None):
    """`runs` is a list of (string, size_pt, bold, color) tuples.

    A run whose string is "\n" starts a new paragraph, so one call can lay out
    a heading and its body without two overlapping text boxes.
    """
    box = slide.shapes.add_textbox(Inches(left), Inches(top),
                                   Inches(width), Inches(height))
    tf = box.text_frame
    tf.word_wrap = True
    para = tf.paragraphs[0]
    para.alignment = align
    if spacing:
        para.line_spacing = spacing
    for chunk, size, bold, color in runs:
        if chunk == "\n":
            para = tf.add_paragraph()
            para.alignment = align
            if spacing:
                para.line_spacing = spacing
            continue
        r = para.add_run()
        r.text = chunk
        r.font.name = TNR
        r.font.size = Pt(size)
        r.font.bold = bold
        r.font.color.rgb = color
    return box


def rect(slide, left, top, width, height, fill):
    sh = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(left), Inches(top),
                                Inches(width), Inches(height))
    sh.fill.solid()
    sh.fill.fore_color.rgb = fill
    sh.line.fill.background()
    sh.shadow.inherit = False
    return sh


def header(slide, title, *, alt=False):
    """The deck's one content-slide header treatment."""
    text(slide, 0.62, 0.62, 12.69, 0.7,
         [(title, 40 if alt else 37.5, False, HDR2 if alt else HDR)])
    return 1.47


def micro_label(slide, left, top, label, width=7.5):
    text(slide, left, top, width, 0.28, [(label, 10.17, True, MICRO)])


def stat_cards(slide, cards, top=1.47):
    """Three big-number cards across, matching the HPE Model Accuracy slide.

    The reference deck places its third card at left 9.76 with width 4.01,
    which runs 0.44in off a 13.33in slide. Re-spaced to sit inside the same
    0.44in margins it uses on the left.
    """
    for i, (value, caption) in enumerate(cards):
        left = [0.44, 4.69, 8.94][i]
        text(slide, left, top, 3.95, 0.6, [(value, 30.2, True, STATV)])
        text(slide, left, top + 0.59, 3.90, 0.8,
             [(caption, 11.23, False, STATC)])


def bar_row(slide, top, label, frac, in_bar, meta):
    """One horizontal bar row, matching the ACCURACY RANKINGS block."""
    text(slide, 0.44, top, 3.21, 0.45, [(label, 10.17, True, STATV)])
    rect(slide, 3.76, top + 0.09, 6.1, 0.28, TRACK)
    if frac > 0:
        rect(slide, 3.76, top + 0.09, max(6.1 * frac, 0.6), 0.28, BAR)
        text(slide, 3.86, top + 0.10, 5.9, 0.26, [(in_bar, 9.64, True, WHITE)])
    # Reference deck uses left 10.03 width 3.75, which overruns the slide edge.
    text(slide, 10.05, top, 2.84, 0.45, [(meta, 9.1, False, MICRO)])


def finding_card(slide, top, number, title, sub, rail):
    """The Key Findings row: white card, coloured rail, pale numeral."""
    rect(slide, 0.73, top, 11.87, 1.09, WHITE)
    rect(slide, 0.73, top, 0.07, 1.09, rail)
    text(slide, 0.93, top + 0.20, 0.5, 0.62, [(number, 29, False, PALE)])
    text(slide, 2.51, top + 0.14, 10.0, 0.42, [(title, 17, False, NAVY)])
    text(slide, 2.51, top + 0.54, 10.0, 0.48, [(sub, 12, False, SLATE)])


def columns(slide, cols, *, top=2.75, left0=0.62, width=2.9, gap=0.23):
    """The Workflow slide's column band: heading over body, evenly spaced."""
    for i, (head, body) in enumerate(cols):
        left = left0 + i * (width + gap)
        text(slide, left, top, width, 0.35, [(head, 20.5, False, NAVY)])
        text(slide, left, top + 0.42, width - 0.1, 3.2,
             [(body, 14.5, False, BODY)], spacing=1.08)


def table(slide, rows, col_w, left, top, *, head_size=10.5, body_size=11.5):
    """A plain table in the deck's serif, no PowerPoint banding."""
    n_r, n_c = len(rows), len(rows[0])
    shape = slide.shapes.add_table(n_r, n_c, Inches(left), Inches(top),
                                   Inches(sum(col_w)), Inches(0.36 * n_r))
    tbl = shape.table
    tbl.first_row = False
    tbl.horz_banding = False
    for j, w in enumerate(col_w):
        tbl.columns[j].width = Inches(w)
    for i, row in enumerate(rows):
        for j, val in enumerate(row):
            cell = tbl.cell(i, j)
            cell.text = ""
            cell.fill.solid()
            cell.fill.fore_color.rgb = TRACK if i == 0 else WHITE
            cell.margin_left = Inches(0.10)
            cell.margin_right = Inches(0.06)
            cell.margin_top = Inches(0.04)
            cell.margin_bottom = Inches(0.04)
            p = cell.text_frame.paragraphs[0]
            r = p.add_run()
            r.text = str(val)
            r.font.name = TNR
            r.font.size = Pt(head_size if i == 0 else body_size)
            r.font.bold = (i == 0)
            r.font.color.rgb = MICRO if i == 0 else STATV
    return tbl


# ── slides ────────────────────────────────────────────────────────────────────
def slide_divider(prs):
    s = add_slide(prs)
    text(s, 0.9, 2.55, 12.0, 0.4,
         [("NEXT PHASE", 13, True, SLATE)])
    box = s.shapes.add_textbox(Inches(0.9), Inches(3.0), Inches(11.6), Inches(1.6))
    tf = box.text_frame
    tf.word_wrap = True
    r = tf.paragraphs[0].add_run()
    r.text = "From Model Characterization to Measurement Validation"
    r.font.name = SERIF
    r.font.size = Pt(44)
    r.font.color.rgb = NAVY
    text(s, 0.92, 4.75, 11.6, 0.5,
         [("Bench reference rig  ·  test–retest repeatability  ·  "
           "the app validation results program", 19, False, SLATE)])
    return s


def slide_why_bench(prs):
    s = add_slide(prs)
    header(s, "Why the Current Evidence Stops Short")
    stat_cards(s, [
        ("100 → 20 Hz",
         "The phone samples at 100 Hz, but the scorer decimates to a 50 ms grid "
         "before scoring. Decimating the optical reference the same way "
         "reproduces 87% of the ω_max_n gap."),
        ("1.36×",
         "Residual knee-angle over-read after the drift correction. Root cause "
         "is single-sensor geometry — the pipeline reports one sensor's "
         "absolute rotation as the knee angle."),
        ("0 of 172",
         "IMU recordings carrying a second, distal sensor. The two-sensor code "
         "path exists and has never received a sample."),
    ])
    micro_label(s, 0.44, 3.55, "WHAT A BENCH SETTLES THAT SUBJECTS CANNOT")
    text(s, 0.44, 3.90, 12.4, 2.2, [
        ("On a human leg, sensor error and algorithm decimation are "
         "confounded — the true angle is never independently known, so "
         "neither source can be attributed.", 16, False, BODY),
        ("\n", 0, False, BODY),
        ("On a bench with a known reference we log the raw 100 Hz stream and "
         "the 20 Hz scored output against the same ground truth, and the "
         "difference between them is the decimation cost, measured rather "
         "than inferred.", 16, False, BODY),
        ("\n", 0, False, BODY),
        ("One caution carries into every result: 61% of the IMU–optical "
         "RMSE is affine, and an affine map is provably invariant under all "
         "seven PT parameters. Halving RMSE moved the parameters by zero. "
         "RMSE is not a proxy for PT accuracy.", 16, False, BODY),
    ], spacing=1.12)
    return s


def slide_rig_options(prs):
    s = add_slide(prs)
    header(s, "Choosing the Bench Reference")
    text(s, 0.62, 1.38, 12.4, 0.55,
         [("Four candidate rigs are already on hand, ranked against what the "
           "pendulum test actually measures: a free decaying swing.",
           14.5, False, BODY)])
    rows = [
        ["Option", "Free decay", "Angle ≤ 0.1°", "≥ 450 °/s",
         "Two segments", "Role"],
        ["Motor + tachometer, driven", "No", "No — velocity only", "Yes",
         "No", "STAGE 1 — rate table"],
        ["Encoder, motor removed", "Yes", "Yes — 0.036°/count",
         "Yes", "No", "STAGE 2 — primary reference"],
        ["Raw signal via DAC", "—", "—", "—", "—",
         "Required on all three, not a rig"],
        ["Bourns pot + printed fixture", "Yes", "Partial — calibratable",
         "Partial", "Yes", "STAGE 2 — second axis"],
    ]
    table(s, rows, [3.05, 1.35, 2.25, 1.45, 1.55, 2.75], 0.62, 2.05)
    micro_label(s, 0.62, 4.55, "WHY NOT ONE RIG")
    text(s, 0.62, 4.88, 12.4, 1.6, [
        ("Every PT parameter is a feature of a free damped oscillation, which "
         "rules the driven motor out of Stage 2. But a driven rate table is "
         "the cleanest possible source of a known angular velocity, which is "
         "exactly what Stage 1 needs — so the motor earns its place first "
         "and comes off second.", 15, False, BODY),
        ("\n", 0, False, BODY),
        ("The potentiometer is the only option that prints into a "
         "two-segment linkage, which is what lets us finally measure the "
         "single-sensor penalty rather than infer it.", 15, False, BODY),
    ], spacing=1.1)
    return s


def slide_rig_build(prs):
    s = add_slide(prs)
    header(s, "Building the Rig: Three Adjustable Terms")
    text(s, 0.62, 1.45, 12.4, 0.85, [
        ("J·θ̈ + B·θ̇ + K·θ + "
         "mglᴄ·sin θ = 0", 17, True, NAVY),
        ("      the model the instrumented pendulum-test literature uses for a "
         "swinging shank. A 70 kg adult with a 43 cm shank gives J = 0.43 "
         "kg·m² and a gravity-only frequency of 0.83 Hz — but our "
         "corpus runs 0.64 to 2.23 Hz, so three of the four terms have to be "
         "adjustable.", 14, False, BODY),
    ], spacing=1.1)
    columns(s, [
        ("Inertia  J",
         "Sliding mass on a slotted 500 mm arm.\n\n"
         "0.43 kg·m² nominal, up to 0.72.\n\n"
         "Sets the baseline period. Reaching 0.64 Hz needs 1.68× the "
         "nominal inertia — more mass, not less."),
        ("Stiffness  K",
         "Interchangeable torsion springs at the pivot: 0, 7, 21, 72 "
         "N·m/rad.\n\n"
         "The term most likely to be skipped, and it cannot be: every "
         "frequency above 0.83 Hz requires real joint stiffness. Geometry "
         "alone cannot reach 2.23 Hz."),
        ("Damping  B",
         "Eddy-current brake — a copper disc between magnets on a "
         "screw-adjustable yoke.\n\n"
         "ζ = 0.054 to 0.271 spans N = 10 cycles down to 2, covering the "
         "corpus. Non-contact, so it adds no friction offset."),
        ("Release  θ₀",
         "Electromagnet detent at 30°, 60°, 90°, 120°, "
         "repeatable to ±1°.\n\n"
         "Spans the excursion gate, and its de-energise edge gives the DAQ and "
         "the phone a shared time zero."),
    ], top=2.62)
    return s


def slide_protocol(prs):
    s = add_slide(prs)
    header(s, "Two-Stage Test Protocol")
    columns(s, [
        ("Stage 1 — Rate table",
         "Motor and tachometer, used as-is. Five blocks, about 45 minutes.\n\n"
         "Axis vertical: gravity stays constant in the sensor frame, isolating "
         "gyro scale factor and bandwidth from the fusion filter.\n\n"
         "Axis horizontal: gravity now rotates through the frame as it does on "
         "a leg, exercising the full fusion path.\n\n"
         "Constant rates 25–600 °/s, plus a 0.1–5 Hz log chirp — "
         "constant rate alone cannot show bandwidth, and the pendulum test is "
         "entirely transient."),
        ("Stage 2 — Free pendulum",
         "Motor decoupled, encoder retained. Four blocks, 105 trials, about 90 "
         "minutes.\n\n"
         "Core agreement: release angle × damping, 12 cells.\n\n"
         "Stiffness sweep: where spastic legs actually live.\n\n"
         "Proximal penalty: imposed 0/10/20° sweep on the printed second "
         "segment — the single-sensor number the project has never had.\n\n"
         "Mounting sensitivity: how much of in-vivo error is placement."),
    ], top=1.55, left0=0.62, width=6.0, gap=0.6)
    micro_label(s, 0.62, 5.35, "PRE-STATED ACCEPTANCE CRITERIA")
    text(s, 0.62, 5.68, 12.4, 1.3, [
        ("Stage 1 — gyro scale-factor error under 2% across 25–600 "
         "°/s; −3 dB bandwidth at or above 10 Hz, clearing the "
         "literature's 5 Hz kinematic cut-off with margin.", 14, False, BODY),
        ("\n", 0, False, BODY),
        ("Stage 2 — ICC ≥ 0.90 against the encoder for each of the "
         "seven parameters, computed separately on the 100 Hz and 20 Hz "
         "streams. Bland–Altman on angle is reported for comparability but "
         "is not the pass condition.", 14, False, BODY),
    ], spacing=1.1)
    return s


def slide_test_retest(prs):
    s = add_slide(prs)
    header(s, "Test–Retest: Ready to Run Today")
    stat_cards(s, [
        ("40", "Participant × leg × session groups with three or more "
               "repeat trials, from the 172 IMU recordings already captured."),
        ("13", "Participants contributing those groups — the full IMU "
               "cohort, not a subset."),
        ("4 – 5", "Repeat trials in almost every group: 29 groups of four "
                      "and 11 of five. Only one group is a singleton."),
    ])
    micro_label(s, 0.44, 3.58, "REPEAT GROUPS BY STUDY ARM")
    bar_row(s, 3.90, "MINT  (MS)", 28 / 40,
            "28 groups  ·  7 participants  ·  121 trials", "70% of groups")
    bar_row(s, 4.41, "CHAT  (stroke)", 8 / 40,
            "8 groups  ·  4 participants", "34 trials")
    bar_row(s, 4.92, "Control", 4 / 40, "4 groups  ·  2 participants",
            "16 trials")
    text(s, 0.44, 5.62, 12.4, 1.4, [
        ("Why this is the strongest result available now.  ", 15, True, NAVY),
        ("Within-session repeatability needs no reference instrument, no MAS "
         "grade and no bench rig — only the same leg measured more than "
         "once in one sitting. Every input already exists.", 15, False, BODY),
        ("\n", 0, False, BODY),
        ("It answers the question a clinician asks first: if you measure the "
         "same leg twice, do you get the same score? The ICC computation is "
         "analysis R8 in the program on the next slides.", 15, False, BODY),
    ], spacing=1.1)
    return s


def slide_bottleneck(prs):
    s = add_slide(prs)
    header(s, "The Bottleneck Is Clinical, Not Technical")
    rows = [
        ["Cohort", "N", "What it supports"],
        ["IMU recordings", "172 trials", "Repeatability, stream-level audits"],
        ["IMU + OptiTrack reference", "13 participants · 79 paired trials",
         "Criterion validity of angle and all seven parameters"],
        ["IMU + clinician-examined MAS", "4 participants",
         "P5, P13, P14, P15 only"],
        ["IMU + any leg at MAS > 0", "3 participants · 5 legs",
         "Underpowered against a 20-leg target"],
        ["Tier-A MAS = 0 legs with IMU", "2 legs", "P5 only — see below"],
    ]
    table(s, rows, [3.6, 3.3, 5.5], 0.62, 1.45)
    micro_label(s, 0.62, 3.85, "THE CONFOUND")
    text(s, 0.62, 4.18, 12.4, 1.0, [
        ("Every clinician-examined MAS = 0 trial comes from P5, and every "
         "MAS > 0 trial from P13, P14 or P15. A discrimination statistic on "
         "that contrast measures whether we can tell P5 apart from three other "
         "people — not whether the app detects spasticity. No analysis "
         "choice fixes this; only more participants do.", 15, False, BODY),
    ], spacing=1.1)
    micro_label(s, 0.62, 5.25, "THE CHEAPEST WAY OUT")
    text(s, 0.62, 5.58, 12.4, 1.3, [
        ("Seven participants — P18 and P20 (MS), P19, P21, P22 and P24 "
         "(stroke), P23 (control) — already have IMU recordings and no MAS "
         "row at all. That is 14 legs, four of them from the population most "
         "likely to grade above zero.", 15, False, BODY),
        ("\n", 0, False, BODY),
        ("Retrospective assessment on those seven closes most of the gap to "
         "the 20-legs-per-group target at zero recording cost, and it is the "
         "one action that unblocks the clinical arm, the HEALTHY_REF "
         "recalibration and the composite score in turn.", 15, False, BODY),
    ], spacing=1.1)
    return s


def slide_program(prs):
    s = add_slide(prs)
    header(s, "App Validation Program", alt=True)
    text(s, 0.73, 1.12, 11.87, 0.4,
         [("Fourteen tasks building one analysis layer, so the paper, the "
           "validation dossier and this deck can never disagree about a "
           "number.", 14, False, SLATE)])
    finding_card(
        s, 1.62, "1", "One analysis layer, three deliverables",
        "Cohort → pre-registered analyses → a single results.json → "
        "figure, table and dossier exporters. No exporter may compute a "
        "statistic: if a number is not in results.json, it cannot appear.",
        PURPLE)
    finding_card(
        s, 2.77, "2", "Eight of the fourteen analyses run on today's data",
        "Angle criterion validity, per-parameter ICCs, the decimation cost, the "
        "filter-convention comparison, a physical plausibility audit and "
        "test–retest all ship without the bench and without new recruitment.",
        TEAL)
    finding_card(
        s, 3.92, "3", "Blocked analyses stay visible instead of vanishing",
        "A skipped record still renders in every output, carrying the reason it "
        "skipped. Silent omission is treated as a bug and is caught by a test.",
        ORANGE)
    finding_card(
        s, 5.07, "4", "A provenance defect found while scoping this",
        "The analysis table carries a MAS value for 19 trials from two "
        "participants who were never examined by a clinician. Grades will now "
        "travel with a provenance tier, and clinical analyses admit tier A only.",
        ORANGE)
    return s


def slide_next_steps(prs):
    s = add_slide(prs)
    header(s, "Sequenced Next Steps", alt=True)
    text(s, 0.73, 1.12, 11.87, 0.4,
         [("Ordered so the cheapest step that can invalidate the next one "
           "always comes first.", 14, False, SLATE)])
    finding_card(
        s, 1.62, "1", "Recover MAS on the seven unassessed participants",
        "14 legs at zero recording cost, four of them stroke. This is the root "
        "of the dependency chain — it releases the clinical arm, then the "
        "HEALTHY_REF recalibration, then the composite score.", PURPLE)
    finding_card(
        s, 2.77, "2", "Build the analysis layer and publish what already holds",
        "Tasks 1–9. Delivers test–retest repeatability and criterion "
        "validity against OptiTrack from data in hand, with the seven "
        "parameters reported separately.", TEAL)
    finding_card(
        s, 3.92, "3", "Characterise the existing rig hardware before fabricating",
        "Encoder type and count, tachometer scale and ripple, the Bourns part "
        "number. Gate: if the encoder sits on the motor's rear shaft, removing "
        "the motor removes the reference and Stage 2 changes shape.", ORANGE)
    finding_card(
        s, 5.07, "4", "Fabricate the pendulum and run both stages",
        "Roughly 150 runs across the two stages. Produces the first error "
        "budget that separates sensor, decimation, filtering and mounting "
        "against a known truth.", ORANGE)
    return s


def main() -> int:
    prs = Presentation(SRC)
    before = len(prs.slides)
    for fn in (slide_divider, slide_why_bench, slide_rig_options,
               slide_rig_build, slide_protocol, slide_test_retest,
               slide_bottleneck, slide_program, slide_next_steps):
        fn(prs)
    prs.save(OUT)
    print(f"{before} slides in -> {len(prs.slides)} slides out")
    print(f"wrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
