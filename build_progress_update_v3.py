"""Rebuild the Pendulastic progress deck around one narrative:
where we are, where we want to be, how we get there.

Keeps only the title and Project Aims slides from the original; every other
slide is dropped as stale. Figures are the freshly generated status set --
everything under Model_Analysis_Outputs/Figures/ dates from 22 July, before
the 2026-08-28 drift fix and the 2026-09-01 OptiTrack coverage fix.

Style constants were read off the original deck with python-pptx, not guessed.
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import PP_ALIGN
from pptx.util import Inches, Pt

HERE = Path(__file__).resolve().parent
SRC = HERE / "Model_Analysis_Outputs" / "Pendulastic Progress Update.pptx"
OUT = HERE / "Model_Analysis_Outputs" / "Pendulastic Progress Update_v4.pptx"
FIG = HERE / "Model_Analysis_Outputs" / "status_figures"

KEEP = {0, 1}          # title, Project Aims
SLIDE_W, SLIDE_H = 13.333, 7.5

# ── palette, read off the reference deck ──────────────────────────────────────
BG = RGBColor(0xF0, 0xF5, 0xFA)
NAVY = RGBColor(0x0C, 0x1E, 0x34)
SLATE = RGBColor(0x3A, 0x58, 0x70)
HDR = RGBColor(0x60, 0x62, 0x64)
HDR2 = RGBColor(0x88, 0x88, 0x88)
BODY = RGBColor(0x33, 0x33, 0x33)
STATV = RGBColor(0x16, 0x20, 0x2C)
STATC = RGBColor(0x4A, 0x55, 0x68)
MICRO = RGBColor(0x6B, 0x76, 0x86)
PALE = RGBColor(0xB8, 0xCD, 0xDE)
TRACK = RGBColor(0xE6, 0xE9, 0xEC)
WHITE = RGBColor(0xFF, 0xFF, 0xFF)
PURPLE = RGBColor(0x60, 0x30, 0xA0)
ORANGE = RGBColor(0xB0, 0x60, 0x10)
TEAL = RGBColor(0x0E, 0x6E, 0x66)
BAD = RGBColor(0xA3, 0x33, 0x27)

SERIF = "Georgia"
TNR = "Times New Roman"

_SIZES: dict[str, tuple[int, int]] = {}


def img_size(path):
    p = str(path)
    if p not in _SIZES:
        _SIZES[p] = Image.open(p).size
    return _SIZES[p]


# ── primitives ────────────────────────────────────────────────────────────────
def add_slide(prs):
    s = prs.slides.add_slide(prs.slide_layouts[0])
    s.background.fill.solid()
    s.background.fill.fore_color.rgb = BG
    return s


def text(slide, left, top, width, height, runs, *, align=PP_ALIGN.LEFT,
         spacing=None, font=TNR):
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
        r.font.name = font
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


def place_in_box(slide, path, left, top, width, height):
    """Contain-fit and centre an image inside a box.

    Never sets width alone: figure aspect ratios here run 0.86 to 2.01, and
    auto-scaling height from width silently overflows the slide for the tall
    ones and misaligns any two placed side by side.
    """
    pw, ph = img_size(path)
    scale = min(width / pw, height / ph)
    w, h = pw * scale, ph * scale
    return slide.shapes.add_picture(
        str(path), Inches(left + (width - w) / 2), Inches(top + (height - h) / 2),
        Inches(w), Inches(h))


def header(slide, title, *, alt=False):
    text(slide, 0.62, 0.55, 12.4, 0.7,
         [(title, 38 if alt else 35, False, HDR2 if alt else HDR)])
    return 1.35


def micro_label(slide, left, top, label, width=8.0):
    text(slide, left, top, width, 0.28, [(label, 10.17, True, MICRO)])


def stat_cards(slide, cards, top=1.35):
    for i, (value, caption) in enumerate(cards):
        left = [0.44, 4.69, 8.94][i]
        text(slide, left, top, 3.95, 0.6, [(value, 28, True, STATV)])
        text(slide, left, top + 0.56, 3.90, 0.8,
             [(caption, 11.23, False, STATC)])


def finding_card(slide, top, number, title, sub, rail, *, h=1.09):
    rect(slide, 0.73, top, 11.87, h, WHITE)
    rect(slide, 0.73, top, 0.07, h, rail)
    text(slide, 0.93, top + 0.18, 0.5, 0.62, [(number, 29, False, PALE)])
    text(slide, 2.51, top + 0.12, 10.0, 0.42, [(title, 17, False, NAVY)])
    text(slide, 2.51, top + 0.52, 10.0, 0.5, [(sub, 12, False, SLATE)])


def columns(slide, cols, *, top, left0=0.62, width=2.9, gap=0.23,
            head_size=19, body_size=14):
    for i, (head, body) in enumerate(cols):
        left = left0 + i * (width + gap)
        text(slide, left, top, width, 0.35, [(head, head_size, False, NAVY)])
        text(slide, left, top + 0.42, width - 0.1, 3.4,
             [(body, body_size, False, BODY)], spacing=1.08)


def table(slide, rows, col_w, left, top, *, head_size=10.5, body_size=11.5,
          row_h=0.34, accent_col=None):
    tbl = slide.shapes.add_table(len(rows), len(rows[0]), Inches(left),
                                 Inches(top), Inches(sum(col_w)),
                                 Inches(row_h * len(rows))).table
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
            cell.margin_top = Inches(0.03)
            cell.margin_bottom = Inches(0.03)
            p = cell.text_frame.paragraphs[0]
            r = p.add_run()
            r.text = str(val)
            r.font.name = TNR
            r.font.size = Pt(head_size if i == 0 else body_size)
            r.font.bold = (i == 0)
            if i == 0:
                r.font.color.rgb = MICRO
            elif accent_col is not None and j == accent_col:
                r.font.color.rgb = TEAL
            else:
                r.font.color.rgb = STATV
    return tbl


def divider(prs, kicker, title, sub):
    s = add_slide(prs)
    text(s, 0.9, 2.62, 12.0, 0.4, [(kicker, 13, True, SLATE)])
    box = s.shapes.add_textbox(Inches(0.9), Inches(3.05), Inches(11.6),
                               Inches(1.5))
    tf = box.text_frame
    tf.word_wrap = True
    r = tf.paragraphs[0].add_run()
    r.text = title
    r.font.name = SERIF
    r.font.size = Pt(46)
    r.font.color.rgb = NAVY
    text(s, 0.92, 4.70, 11.6, 0.5, [(sub, 19, False, SLATE)])
    return s


def drop_slides(prs, keep):
    """python-pptx has no delete API; drop the sldId entries and their rels."""
    RID = ("{http://schemas.openxmlformats.org/officeDocument/2006/"
           "relationships}id")
    lst = prs.slides._sldIdLst
    for i, el in reversed(list(enumerate(lst))):
        if i not in keep:
            prs.part.drop_rel(el.get(RID))
            lst.remove(el)


# ══ WHERE WE ARE ══════════════════════════════════════════════════════════════
def s_collected(prs):
    s = add_slide(prs)
    header(s, "What We Have Collected")
    place_in_box(s, FIG / "wa_cohort.png", 0.55, 1.20, 5.5, 5.85)
    text(s, 6.35, 1.35, 6.5, 5.4, [
        ("22 participants, 172 IMU recordings.  ", 16, True, NAVY),
        ("Every participant has video and OptiTrack. Thirteen also carry phone "
         "IMU, and those thirteen are the whole basis of the app validation.",
         16, False, BODY),
        ("\n", 0, False, BODY),
        ("79 paired trials.  ", 16, True, NAVY),
        ("Trials where a phone recording and an optical reference exist for the "
         "same swing, across 12 participants. This is what criterion validity "
         "is computed on.", 16, False, BODY),
        ("\n", 0, False, BODY),
        ("Only four participants have a clinician MAS.  ", 16, True, BAD),
        ("Seventeen of the 39 MAS rows are assumed controls who were never "
         "actually examined. The clinical column is the thin one, and it is "
         "the one that gates the clinical claim.", 16, False, BODY),
        ("\n", 0, False, BODY),
        ("Figures regenerated 3 Sep 2026. The previous figure set predates the "
         "August drift fix and the September OptiTrack coverage fix.",
         12.5, False, MICRO),
    ], spacing=1.12)
    return s


def s_trajectory(prs):
    s = add_slide(prs)
    header(s, "What the App Captures")
    place_in_box(s, FIG / "wa_trajectory.png", 0.55, 1.20, 8.1, 5.5)
    text(s, 8.95, 1.55, 3.95, 5.0, [
        ("One trial, one phone.", 17, True, NAVY),
        ("\n", 0, False, BODY),
        ("The leg is released from near-horizontal and swings freely. "
         "Everything the score uses is read off this curve: the first "
         "excursion, how fast it moves, how quickly it decays, and where it "
         "comes to rest.", 14.5, False, BODY),
        ("\n", 0, False, BODY),
        ("The phone samples at 100 Hz. The scorer then decimates to a 50 ms "
         "grid before computing anything — a choice that turns out to "
         "matter a great deal.", 14.5, False, BODY),
        ("\n", 0, False, BODY),
        ("No reference instrument is present in this recording. That is the "
         "point of the product: a phone on the shank and nothing else.",
         14.5, False, SLATE),
    ], spacing=1.12)
    return s


def s_app_today(prs):
    s = add_slide(prs)
    header(s, "The Mobile App Today")
    stat_cards(s, [
        ("3,985", "Lines of Rust in the scoring engine, across 15 modules — "
                  "the same maths as the desktop reference."),
        ("200 KB", "The engine compiled to WebAssembly and rebuilt 3 Sep. "
                   "It runs in a worker, on the phone, offline."),
        ("3,008", "Lines of JavaScript around it: capture, storage, trends, "
                  "export, and an installable offline shell."),
    ])
    micro_label(s, 0.62, 3.05, "WHAT EXISTS ON EACH SURFACE")
    rows = [
        ["Surface", "State", "Notes"],
        ["Web app (iOS Safari, installable)", "Built and running",
         "Service worker, IndexedDB sessions, CSV/JSONL export, trends view"],
        ["Rust core compiled to WebAssembly", "Built and current",
         "Rebuilt today; must be rebuilt after any change under mobile-imu-core/src/"],
        ["Android capture harness", "Written, never built",
         "No JDK or Android SDK on this machine. Explicitly throwaway, capture only"],
        ["iOS capture harness", "Written, never built",
         "SwiftUI sources and an xcodegen project file; same status as Android"],
    ]
    table(s, rows, [4.05, 2.55, 5.7], 0.62, 3.40, row_h=0.42, body_size=12,
          accent_col=1)
    text(s, 0.62, 5.75, 12.3, 1.3, [
        ("The product is the web app.  ", 15, True, NAVY),
        ("The two native harnesses were built for a capture study and are not "
         "the shipping path — the plan that created them says so explicitly. "
         "Anything claimed about the app on a phone today rests on the "
         "browser build.", 15, False, BODY),
    ], spacing=1.1)
    return s


def s_app_quality(prs):
    s = add_slide(prs)
    header(s, "How Solid the App Is")
    place_in_box(s, FIG / "app_modules.png", 0.50, 1.15, 7.9, 5.05)
    text(s, 8.70, 1.30, 4.25, 5.0, [
        ("296 tests, all green.", 16, True, NAVY),
        ("\n", 0, False, BODY),
        ("125 Rust tests and 171 JavaScript tests, run 3 Sep 2026. Zero "
         "failures, zero compiler warnings.", 14, False, BODY),
        ("\n", 0, False, BODY),
        ("The scoring maths is exact.  ", 14, True, NAVY),
        ("Golden fixtures regenerate byte-identical against the Python "
         "reference: all seven parameter formulas, both thresholds and every "
         "guard constant match.", 14, False, BODY),
        ("\n", 0, False, BODY),
        ("But coverage is uneven.  ", 14, True, BAD),
        ("538 lines sit in modules with no tests at all — including "
         "wasm.rs, which is the entire boundary between the engine and the "
         "browser, and calibration.rs.", 14, False, BODY),
    ], spacing=1.1)
    return s


def s_app_gaps(prs):
    s = add_slide(prs)
    header(s, "What the App Cannot Yet Claim", alt=True)
    text(s, 0.73, 1.05, 11.87, 0.4,
         [("Four of these are known and recorded in the repo. None is a "
           "surprise; all four are unshipped work.", 14, False, SLATE)])
    finding_card(s, 1.55, "1", "It still carries a “not validated” banner, by design",
                 "The webapp spec requires the banner and gives it no dismiss "
                 "control. That is the correct state today, and it is exactly "
                 "what the bench rig and the analysis layer exist to change.",
                 ORANGE)
    finding_card(s, 2.70, "2", "Two scoring conventions are live at once",
                 "Of 41 call sites, exactly two pass detrend=False; the other "
                 "39 take the True default. Measured on 197 real trials, 63 of "
                 "them (32%) land on a different MAS grade — so the same "
                 "recording scored live and analysed later can disagree.", BAD)
    finding_card(s, 3.85, "3", "The majority convention is untested in both languages",
                 "No Rust test exercises detrend=true, and the Python side has "
                 "no golden fixture for it either. The branch those 39 call "
                 "sites actually use is the one neither suite covers.", BAD)
    finding_card(s, 5.00, "4", "Deployment is manual and fails quietly",
                 "Git builds cannot work because the wasm must be compiled, so "
                 "release is a CLI folder upload. A wasm-bindgen version "
                 "mismatch emits bindings the .wasm disagrees with without "
                 "erroring — the build script guards it, nothing else does.",
                 ORANGE)
    return s


def s_agreement(prs):
    s = add_slide(prs)
    header(s, "Where the Measurement Actually Stands")
    place_in_box(s, FIG / "wa_agreement.png", 0.55, 1.15, 8.5, 4.6)
    text(s, 9.25, 1.35, 3.7, 4.4, [
        ("Read the blue bars against the orange.", 15, True, NAVY),
        ("\n", 0, False, BODY),
        ("Blue is rank agreement: does the phone put the trials in the right "
         "order? Orange is value agreement: does it return the right number?",
         13.5, False, BODY),
        ("\n", 0, False, BODY),
        ("Blue exceeds orange on every parameter. The phone is tracking "
         "something real and reporting it on the wrong scale.",
         13.5, False, BODY),
    ], spacing=1.1)
    micro_label(s, 0.62, 5.95, "WHAT THIS MEANS")
    text(s, 0.62, 6.28, 12.3, 1.0, [
        ("Only N clears an ICC of 0.5, and it is the literature's weakest "
         "discriminator. The two velocity terms read 0.39× and 0.44× the "
         "reference — the signature of the 20 Hz decimation, not of a bad "
         "sensor. φ max ratio is negatively correlated and is a defect to "
         "investigate in its own right.", 14.5, False, BODY),
    ], spacing=1.1)
    return s


def s_testretest(prs):
    s = add_slide(prs)
    header(s, "One Result Is Ready Today")
    stat_cards(s, [
        ("40", "Participant × leg × session groups with three or more repeat "
               "trials, already captured."),
        ("13", "Participants contributing them — the full IMU cohort, not "
               "a subset."),
        ("4 – 5", "Repeat trials in almost every group. Only one group is a "
                      "singleton."),
    ])
    # ar 2.01: at the full 12.2in width this figure would need 6.07in of
    # height, so it is height-constrained. Give it the left two-thirds and put
    # the reading beside it rather than centring it in dead space.
    place_in_box(s, FIG / "wa_testretest.png", 0.50, 2.95, 8.3, 4.15)
    text(s, 9.05, 3.15, 3.85, 3.9, [
        ("No dependencies.", 15, True, NAVY),
        ("\n", 0, False, BODY),
        ("Repeatability needs no reference instrument, no MAS grade and no "
         "bench rig — only the same leg measured more than once in one "
         "sitting, which we already have.", 13.5, False, BODY),
        ("\n", 0, False, BODY),
        ("It answers the question a clinician asks first: measure the same leg "
         "twice, do you get the same score?", 13.5, False, BODY),
        ("\n", 0, False, BODY),
        ("The ICC computation itself is one task in the analysis layer.",
         13.5, False, SLATE),
    ], spacing=1.1)
    return s


def s_bottleneck(prs):
    s = add_slide(prs)
    header(s, "What Is Actually Blocking Us")
    place_in_box(s, FIG / "wa_bottleneck.png", 0.55, 1.15, 7.9, 4.3)
    text(s, 8.75, 1.30, 4.2, 4.3, [
        ("The contrast is confounded.", 15, True, BAD),
        ("\n", 0, False, BODY),
        ("Every clinician-examined MAS = 0 trial comes from P5, and every "
         "MAS > 0 trial from P13, P14 or P15.", 13.5, False, BODY),
        ("\n", 0, False, BODY),
        ("A discrimination statistic on that measures whether we can tell P5 "
         "from three other people — not whether the app detects "
         "spasticity. No analysis choice repairs it.", 13.5, False, BODY),
    ], spacing=1.1)
    micro_label(s, 0.62, 5.68, "THE CHEAPEST WAY OUT")
    text(s, 0.62, 6.02, 12.3, 1.2, [
        ("Seven participants — P18 and P20 (MS), P19, P21, P22 and P24 "
         "(stroke), P23 (control) — already have IMU recordings and no MAS "
         "row at all. Fourteen legs, four of them from the population most "
         "likely to grade above zero. Retrospective assessment closes most of "
         "the gap without a single new recording session.", 14.5, False, BODY),
    ], spacing=1.1)
    return s


# ══ WHERE WE WANT TO BE ═══════════════════════════════════════════════════════
def s_target(prs):
    s = add_slide(prs)
    header(s, "Where We Want To Be")
    rows = [
        ["", "Where we are (3 Sep 2026)", "Where we want to be"],
        ["Value agreement",
         "ICC ≈ 0 on five of seven parameters",
         "ICC ≥ 0.90 on all seven, against known truth"],
        ["Velocity fidelity",
         "ω terms read 0.39× and 0.44× the reference",
         "Within 2% of a known angular rate to 600 °/s"],
        ["Sensor configuration",
         "Single sensor; 0 of 172 recordings have a second",
         "Two-sensor, or a stated tolerance for proximal motion"],
        ["Repeatability",
         "40 repeat groups captured, ICC not yet computed",
         "Published within-session ICC for every parameter"],
        ["Clinical evidence",
         "5 spastic and 2 control legs, confounded",
         "20 legs per group, unconfounded, Tier-A provenance"],
        ["Score calibration",
         "HEALTHY_REF invalid; composite score unusable",
         "Recalibrated on genuinely examined controls"],
        ["Reference standard",
         "OptiTrack only, itself degraded on many trials",
         "Benchtop known truth, with OptiTrack as the in-vivo check"],
    ]
    table(s, rows, [2.55, 4.65, 5.1], 0.62, 1.35, row_h=0.42,
          body_size=12.5, accent_col=2)
    text(s, 0.62, 5.35, 12.3, 1.6, [
        ("The gap is not primarily a sensor problem.", 16, True, NAVY),
        ("\n", 0, False, BODY),
        ("The phone samples at 100 Hz and its rank agreement is real. What is "
         "missing is a reference we can trust absolutely, a second segment to "
         "resolve the geometry, and enough examined legs to say anything "
         "clinical. Two of those three are cheap.", 15, False, BODY),
    ], spacing=1.12)
    return s


# ══ HOW WE GET THERE ══════════════════════════════════════════════════════════
def s_rig_choice(prs):
    s = add_slide(prs)
    header(s, "A Bench Reference, Built From Parts We Have")
    text(s, 0.62, 1.28, 12.3, 0.5,
         [("On a human leg, sensor error and algorithm decimation are "
           "confounded — the true angle is never independently known. A "
           "bench with known truth is the only way to separate them.",
           14.5, False, BODY)])
    rows = [
        ["Option on hand", "Free decay", "Angle ≤ 0.1°", "≥ 450 °/s",
         "Two segments", "Role"],
        ["Motor + tachometer, driven", "No", "No — velocity only", "Yes",
         "No", "STAGE 1 — rate table"],
        ["Encoder, motor removed", "Yes", "Yes — 0.036°/count", "Yes", "No",
         "STAGE 2 — primary reference"],
        ["Raw signal via DAC", "—", "—", "—", "—",
         "Required on all, not a rig"],
        ["Bourns pot + printed fixture", "Yes", "Partial — calibratable",
         "Partial", "Yes", "STAGE 2 — second axis"],
    ]
    table(s, rows, [3.05, 1.35, 2.25, 1.45, 1.55, 2.65], 0.62, 1.95,
          row_h=0.40, accent_col=5)
    micro_label(s, 0.62, 4.30, "WHY TWO RIGS AND NOT ONE")
    text(s, 0.62, 4.64, 12.3, 2.2, [
        ("Every scored parameter is a feature of a free decaying swing, which "
         "rules the driven motor out of the pendulum stage. But a driven rate "
         "table is the cleanest possible source of a known angular velocity, "
         "which is exactly what the sensor stage needs — so the motor earns "
         "its place first and comes off second.", 14.5, False, BODY),
        ("\n", 0, False, BODY),
        ("The potentiometer is the only option that prints into a two-segment "
         "linkage. That is what finally lets us measure the single-sensor "
         "penalty instead of inferring it.", 14.5, False, BODY),
    ], spacing=1.1)
    return s


def s_rig_build(prs):
    s = add_slide(prs)
    header(s, "Building It: Three Adjustable Terms")
    text(s, 0.62, 1.30, 12.4, 0.9, [
        ("J·θ̈ + B·θ̇ + K·θ + mglᴄ·sin θ = 0", 17, True, NAVY),
        ("      the model the instrumented pendulum literature uses for a "
         "swinging shank. A 70 kg adult with a 43 cm shank gives a "
         "gravity-only frequency of 0.83 Hz — but our recordings run 0.64 "
         "to 2.23 Hz, so three of the four terms must be adjustable.",
         14, False, BODY),
    ], spacing=1.1)
    columns(s, [
        ("Inertia  J",
         "Sliding mass on a slotted 500 mm arm.\n\n"
         "0.43 kg·m² nominal, up to 0.72.\n\n"
         "Sets the baseline period. Reaching 0.64 Hz needs 1.68× the nominal "
         "inertia — more mass, not less."),
        ("Stiffness  K",
         "Interchangeable torsion springs: 0, 7, 21, 72 N·m/rad.\n\n"
         "The term most likely to be skipped, and it cannot be: every "
         "frequency above 0.83 Hz requires real joint stiffness. Geometry "
         "alone cannot reach 2.23 Hz."),
        ("Damping  B",
         "Eddy-current brake — a copper disc between magnets on an "
         "adjustable yoke.\n\n"
         "ζ from 0.054 to 0.271 spans N = 10 cycles down to 2, covering the "
         "range we see. Non-contact, so no friction offset."),
        ("Release  θ₀",
         "Electromagnet detent at 30°, 60°, 90°, 120°, repeatable to ±1°.\n\n"
         "Spans the excursion gate, and its de-energise edge gives the "
         "logger and the phone a shared time zero."),
    ], top=2.55)
    return s


def s_protocol(prs):
    s = add_slide(prs)
    header(s, "Two Stages, About Three Hours of Capture")
    columns(s, [
        ("Stage 1 — Rate table",
         "Motor and tachometer, used as-is. Five blocks, about 45 minutes.\n\n"
         "Axis vertical: gravity stays fixed in the sensor frame, isolating "
         "gyro scale factor and bandwidth from the fusion filter.\n\n"
         "Axis horizontal: gravity now rotates through the frame as it does "
         "on a leg, exercising the full fusion path.\n\n"
         "Constant rates 25–600 °/s plus a 0.1–5 Hz chirp. Constant rate "
         "alone cannot show bandwidth, and the pendulum test is entirely "
         "transient."),
        ("Stage 2 — Free pendulum",
         "Motor decoupled, encoder retained. Four blocks, 105 trials, about "
         "90 minutes.\n\n"
         "Core agreement: release angle × damping, twelve cells.\n\n"
         "Stiffness sweep: the stiff end, where spastic legs live.\n\n"
         "Proximal penalty: an imposed 0/10/20° sweep on the printed second "
         "segment — the single-sensor number we have never had.\n\n"
         "Mounting sensitivity: how much of in-vivo error is placement."),
    ], top=1.35, left0=0.62, width=6.0, gap=0.6, body_size=14)
    micro_label(s, 0.62, 5.45, "PRE-STATED ACCEPTANCE CRITERIA")
    text(s, 0.62, 5.79, 12.3, 1.3, [
        ("Stage 1 — gyro scale-factor error under 2% across 25–600 °/s; "
         "bandwidth at or above 10 Hz, clearing the literature's 5 Hz "
         "kinematic cut-off with margin.", 14, False, BODY),
        ("\n", 0, False, BODY),
        ("Stage 2 — ICC ≥ 0.90 against the encoder for each parameter, "
         "computed separately on the 100 Hz and the 20 Hz streams. The gap "
         "between those two columns is the decimation cost, measured rather "
         "than inferred.", 14, False, BODY),
    ], spacing=1.1)
    return s


def s_program(prs):
    s = add_slide(prs)
    header(s, "And One Analysis Layer Behind It", alt=True)
    text(s, 0.73, 1.05, 11.87, 0.4,
         [("Fourteen tasks, so the paper, the validation dossier and this deck "
           "can never disagree about a number.", 14, False, SLATE)])
    finding_card(s, 1.55, "1", "One analysis layer, three deliverables",
                 "Cohort → pre-registered analyses → a single results.json "
                 "→ figure, table and dossier exporters. No exporter may "
                 "compute a statistic: if a number is not in results.json, it "
                 "cannot appear on a slide.", PURPLE)
    finding_card(s, 2.70, "2", "Eight of the fourteen run on today's data",
                 "Angle agreement, per-parameter ICCs, the decimation cost, "
                 "the filter-convention comparison, a physical plausibility "
                 "audit and repeatability all ship without the bench and "
                 "without new recruitment.", TEAL)
    finding_card(s, 3.85, "3", "Blocked analyses stay visible",
                 "A skipped result still renders, carrying the reason it "
                 "skipped. Silent omission is treated as a bug and is caught "
                 "by a test.", ORANGE)
    finding_card(s, 5.00, "4", "A provenance defect found while scoping this",
                 "The analysis table carries a MAS value for 19 trials from "
                 "two participants never examined by a clinician. Grades will "
                 "now travel with a provenance tier, and clinical analyses "
                 "admit examined data only.", ORANGE)
    return s


def s_next(prs):
    s = add_slide(prs)
    header(s, "How We Get There", alt=True)
    text(s, 0.73, 1.05, 11.87, 0.4,
         [("Ordered so the cheapest step that can invalidate the next one "
           "always comes first.", 14, False, SLATE)])
    finding_card(s, 1.55, "1", "Recover MAS on the seven unassessed participants",
                 "Fourteen legs at zero recording cost, four of them stroke. "
                 "This is the root of the chain: it releases the clinical arm, "
                 "then the HEALTHY_REF recalibration, then the composite "
                 "score.", PURPLE)
    finding_card(s, 2.70, "2", "Build the analysis layer and publish what holds",
                 "Delivers within-session repeatability and criterion validity "
                 "against OptiTrack from data already in hand, with the seven "
                 "parameters reported separately.", TEAL)
    finding_card(s, 3.85, "3", "Characterise the rig hardware before fabricating",
                 "Encoder type and count, tachometer scale and ripple, the "
                 "Bourns part number. Gate: if the encoder sits on the motor's "
                 "rear shaft, removing the motor removes the reference and "
                 "Stage 2 changes shape.", ORANGE)
    finding_card(s, 5.00, "4", "Fabricate the pendulum and run both stages",
                 "About 150 runs. Produces the first error budget that "
                 "separates sensor, decimation, filtering and mounting against "
                 "a known truth — and the first real measurement of the "
                 "single-sensor penalty.", ORANGE)
    return s


def main() -> int:
    prs = Presentation(SRC)
    before = len(prs.slides)
    drop_slides(prs, KEEP)

    divider(prs, "PART ONE", "Where we are",
            "What has been collected, and what it currently supports")
    s_collected(prs)
    s_trajectory(prs)
    s_app_today(prs)
    s_app_quality(prs)
    s_app_gaps(prs)
    s_agreement(prs)
    s_testretest(prs)
    s_bottleneck(prs)

    divider(prs, "PART TWO", "Where we want to be",
            "The measurement claim we intend to be able to defend")
    s_target(prs)

    divider(prs, "PART THREE", "How we get there",
            "A bench reference, an analysis layer, and fourteen legs of "
            "clinical data")
    s_rig_choice(prs)
    s_rig_build(prs)
    s_protocol(prs)
    s_program(prs)
    s_next(prs)

    prs.save(OUT)
    print(f"{before} slides in -> {len(prs.slides)} out "
          f"(kept {sorted(KEEP)}, appended {len(prs.slides) - len(KEEP)})")
    print(f"wrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
