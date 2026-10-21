"""Pendulastic progress deck, 8 September 2026.

Three emphases, per the brief: the mobile app, the IMU-vs-MediaPipe figure
set generated today, and the motor-driven bench rig that is about to carry
test-retest and construct validity.

Style constants are the ones read off the original deck with python-pptx for
build_progress_update_v3.py, not re-guessed. Keeps only the title and Project
Aims slides from the source; everything else is written fresh.

Every number on a slide was measured in the session that wrote this file:
  cohort        data_purpose.py + a direct walk of Recordings/ (2026-09-08)
  app           cargo test (128) + node --test (179), wc -l, wasm mtime
  MediaPipe     Model_Analysis_Outputs/Methodology_Comparison/, regenerated
                2026-09-08 13:0x by generate_methodology_comparison_figures.py,
                after P4's new session entered the corpus (296 trials)
  test-retest   build_status_figures.py, regenerated 2026-09-08
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
OUT = HERE / "Model_Analysis_Outputs" / "Pendulastic Progress Update_v5.pptx"
FIG = HERE / "Model_Analysis_Outputs" / "status_figures"
MC = HERE / "Model_Analysis_Outputs" / "Methodology_Comparison"

KEEP = {0, 1}          # title, Project Aims
SLIDE_W, SLIDE_H = 13.333, 7.5

# -- palette, read off the reference deck --------------------------------------
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


# -- primitives ----------------------------------------------------------------
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

    Never sets width alone: the figures used here run 0.86 to 2.41 in aspect
    ratio, and auto-scaling height from width silently overflows the slide for
    the tall ones and misaligns any two placed side by side.
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


def caption(slide, top, runs, *, left=0.62, width=12.3, height=1.0):
    text(slide, left, top, width, height, runs, spacing=1.1)


def drop_slides(prs, keep):
    """python-pptx has no delete API; drop the sldId entries and their rels."""
    RID = ("{http://schemas.openxmlformats.org/officeDocument/2006/"
           "relationships}id")
    lst = prs.slides._sldIdLst
    for i, el in reversed(list(enumerate(lst))):
        if i not in keep:
            prs.part.drop_rel(el.get(RID))
            lst.remove(el)


# == PART ONE - THE COHORT =====================================================
def s_cohort(prs):
    s = add_slide(prs)
    header(s, "Who We Have Recorded")
    place_in_box(s, FIG / "wa_cohort.png", 0.50, 1.15, 5.3, 5.95)
    text(s, 6.15, 1.30, 6.7, 5.6, [
        ("22 participants, three study arms.  ", 16, True, NAVY),
        ("Nine MS (MINT), four stroke (CHAT), nine unaffected controls. "
         "P2 and P4 through P24, with no P3. Every one of them has an "
         "OptiTrack session.", 16, False, BODY),
        ("\n", 0, False, BODY),
        ("181 phone-IMU recordings across 14 of them, and 606 videos.  ",
         16, True, NAVY),
        ("The 14 with phone IMU are the whole basis of the app validation; "
         "the other eight are an RGB-plus-optical set and stay results-grade "
         "for the model work.", 16, False, BODY),
        ("\n", 0, False, BODY),
        ("P4 was re-recorded this morning.  ", 16, True, TEAL),
        ("Ten IMU trials across both legs, filed as a new pre session beside "
         "the June one. P4 had no phone IMU at all until today - and P4 is "
         "clinician-examined, which is why this one session matters more than "
         "its size suggests.", 16, False, BODY),
        ("\n", 0, False, BODY),
        ("Five participants have a clinician MAS.  ", 16, True, BAD),
        ("P4, P5, P13, P14 and P15 - ten legs, and as of today all ten have "
         "phone IMU. Seventeen of the 39 MAS rows are assumed controls who "
         "were never examined, and four more carry no assessor at all. The "
         "clinical column is still the thin one, and it is the one that gates "
         "the clinical claim.", 16, False, BODY),
    ], spacing=1.10)
    return s


def s_cohort_note(prs):
    s = add_slide(prs)
    header(s, "What Today's P4 Session Changes", alt=True)
    text(s, 0.73, 1.18, 11.87, 0.4,
         [("One re-recording, and it lands on the one participant whose legs "
           "the corpus most needed.", 14, False, SLATE)])
    finding_card(s, 1.65, "1",
                 "P4 is the first examined participant with a split within one person",
                 "P4 left graded MAS 0 and P4 right graded 1+ - the highest "
                 "examined grade anywhere in the corpus - and both legs now "
                 "carry phone IMU. Every previous contrast was between "
                 "people, so a discrimination statistic measured whether we "
                 "could tell P5 apart from three others. This is the first "
                 "one that does not.", TEAL, h=1.35)
    finding_card(s, 3.20, "2",
                 "But the grade was assessed in June, and these trials are September",
                 "The MAS row sits on condition june_pre; the new trials are "
                 "a fresh pre session. Tone changes over an intervention, "
                 "which is the point of measuring it more than once, so this "
                 "is a carry across conditions and has to be stated wherever "
                 "it is used - not presented as a matched observation. A "
                 "re-assessment would settle it.", ORANGE, h=1.35)
    micro_label(s, 0.73, 4.95, "AND THE GAP THAT DID NOT MOVE")
    caption(s, 5.30, [
        ("Seven participants still have IMU recordings and no MAS row at all "
         "- P18 and P20 (MS), P19, P21, P22 and P24 (stroke), P23 (control). "
         "Fourteen legs, four of them from the population most likely to "
         "grade above zero. Retrospective assessment closes most of what "
         "remains without a single new recording session.",
         15, False, BODY),
    ], left=0.73, width=11.87, height=1.4)
    return s


# == PART TWO - THE MOBILE APP =================================================
def s_app_today(prs):
    s = add_slide(prs)
    header(s, "The Mobile App Today")
    stat_cards(s, [
        ("4,374", "Lines of Rust in the scoring engine, across 15 modules - "
                  "the same maths as the desktop reference, now on the same "
                  "grid."),
        ("219 KB", "The engine compiled to WebAssembly, rebuilt this morning. "
                   "It runs in a worker, on the phone, offline."),
        ("307", "Tests green today: 128 Rust and 179 JavaScript. Zero "
                "failures, zero compiler warnings."),
    ])
    micro_label(s, 0.62, 3.05, "WHAT EXISTS ON EACH SURFACE")
    rows = [
        ["Surface", "State", "Notes"],
        ["Web app (iOS Safari, installable)", "Built and running",
         "Service worker, IndexedDB sessions, CSV/JSONL export, trends view mounted"],
        ["Rust core compiled to WebAssembly", "Built and current",
         "Rebuilt 8 Sep; must be rebuilt after any change under mobile-imu-core/src/"],
        ["Longitudinal trends view", "Shipped since the last update",
         "Per-parameter series with honest provenance, and which reference each point used"],
        ["Android capture harness", "Written, never built",
         "No JDK or Android SDK on this machine. Explicitly throwaway, capture only"],
        ["iOS capture harness", "Written, never built",
         "SwiftUI sources and an xcodegen project file; same status as Android"],
    ]
    table(s, rows, [4.05, 2.75, 5.5], 0.62, 3.40, row_h=0.42, body_size=12,
          accent_col=1)
    caption(s, 6.10, [
        ("The product is the web app.  ", 15, True, NAVY),
        ("The two native harnesses were built for a capture study and are not "
         "the shipping path. Everything claimed about the app on a phone today "
         "rests on the browser build.", 15, False, BODY),
    ], height=1.2)
    return s


def s_app_grid(prs):
    s = add_slide(prs)
    header(s, "The Biggest App Change: One Analysis Grid")
    text(s, 0.62, 1.28, 12.4, 0.9, [
        ("The phone scored at 20 Hz - its display poll. The desktop had "
         "already moved to 100 Hz, the sensor's own cadence. That single "
         "divergence was the whole of the phone/desktop disagreement: R2n "
         "differed by 9.5e-2 on identical input.", 15.5, False, BODY),
    ], spacing=1.1)
    micro_label(s, 0.62, 2.30, "WHAT MOVING TO 100 Hz BOUGHT, MEASURED")
    rows = [
        ["", "Before", "After", "How it was measured"],
        ["omega_peak error vs OptiTrack", "29.1%", "19.6%", "46 paired trials"],
        ["omega_max_n error vs OptiTrack", "41.9%", "32.6%", "46 paired trials"],
        ["A0 error vs OptiTrack", "25.8%", "20.9%", "46 paired trials"],
        ["Oscillation window", "4 s (a cap)", "10 s",
         "N read 3.5 for true counts of 4, 6 and 8 alike - it was reading "
         "frequency x window, not damping"],
        ["Angular velocity",
         "finite difference + smoother", "Savitzky-Golay analytic derivative",
         "old route over-read the peak +14.67% at 2 deg of noise; new holds "
         "0.50-1.37% across 0.6-1.8 Hz"],
        ["HEALTHY_REF[\"N\"]", "3.5", "5.50",
         "3.5 was the 4 s cap itself, not a physiological value"],
    ]
    table(s, rows, [2.95, 2.15, 2.55, 4.65], 0.62, 2.65, row_h=0.44,
          body_size=11.5, accent_col=2)
    caption(s, 5.85, [
        ("Scoring at 20 Hz also pushed the 0.10 s smoothing window below "
         "savgol's polyorder floor, so the port's fixed 9-sample window and "
         "the reference's duration-based one disagreed by roughly 2x in time "
         "at the very rate scoring ran on. Both are duration-based now, which "
         "is only safe because scoring left 20 Hz.", 14, False, BODY),
    ], height=1.3)
    return s


def s_app_quality(prs):
    s = add_slide(prs)
    header(s, "How Solid The Engine Is")
    place_in_box(s, FIG / "app_modules.png", 0.50, 1.15, 7.9, 5.15)
    text(s, 8.70, 1.30, 4.25, 5.2, [
        ("307 tests, all green today.", 16, True, NAVY),
        ("\n", 0, False, BODY),
        ("128 Rust and 179 JavaScript, run 8 Sep 2026. Zero failures, zero "
         "compiler warnings.", 14, False, BODY),
        ("\n", 0, False, BODY),
        ("The scoring maths is pinned across the language boundary.  ",
         14, True, NAVY),
        ("Golden fixtures regenerate against the Python reference, and the "
         "four gate and zone constants are now asserted equal in both "
         "languages rather than merely written twice.", 14, False, BODY),
        ("\n", 0, False, BODY),
        ("Coverage is still uneven.  ", 14, True, BAD),
        ("538 lines - 12% - sit in modules with no tests at all, and the "
         "largest of them is wasm.rs, the entire boundary between the engine "
         "and the browser. calibration.rs, stillness.rs and lib.rs are the "
         "others.",
         14, False, BODY),
    ], spacing=1.1)
    return s


def s_app_gaps(prs):
    s = add_slide(prs)
    header(s, "What The App Still Cannot Claim", alt=True)
    text(s, 0.73, 1.18, 11.87, 0.4,
         [("All four are recorded in the repo. None is a surprise; all four "
           "are unshipped work, and two of them are what the rig exists to "
           "close.", 14, False, SLATE)])
    finding_card(s, 1.55, "1",
                 "It carries a “not validated” banner, by design",
                 "The spec requires the banner and gives it no dismiss "
                 "control. That is the correct state today, and it is exactly "
                 "what the bench rig and the analysis layer exist to change.",
                 ORANGE)
    finding_card(s, 2.70, "2", "No severity index and no zone label is shown",
                 "PT7 is non-monotonic in severity and HEALTHY_REF is not "
                 "reproducible, so classification stays suppressed. The A0 "
                 "index that replaces it is specified but needs anchors only "
                 "the rig can supply.", BAD)
    finding_card(s, 3.85, "3",
                 "Phone A0 and optical A0 are not on one scale",
                 "Measured on 100 paired trials: IMU median 54.72 deg against "
                 "optical 34.04, ratio 1.489, Pearson +0.024 and Spearman "
                 "+0.053 at p 0.60. A gain would preserve rank; this does "
                 "not. Against the 25 deg gate the two verdicts differ on 34 "
                 "of 100 trials.", BAD)
    finding_card(s, 5.00, "4", "Deployment is manual and fails quietly",
                 "Git builds cannot work because the wasm must be compiled, so "
                 "release is a CLI folder upload. A wasm-bindgen version "
                 "mismatch emits bindings the .wasm disagrees with without "
                 "erroring - the build script guards it, nothing else does.",
                 ORANGE)
    return s


# == PART THREE - IMU vs MEDIAPIPE =============================================
def s_mp_question(prs):
    s = add_slide(prs)
    header(s, "The Question, And Why It Is Not Obvious")
    text(s, 0.62, 1.30, 12.4, 1.0, [
        ("The app is IMU-only. That was decided before it was measured, so "
         "this figure set went back and asked the question properly: on the "
         "same trials, against the same OptiTrack reference, does a phone on "
         "the shank beat a camera pointed at the leg?", 16, False, BODY),
    ], spacing=1.12)
    columns(s, [
        ("What was measured",
         "296 MediaPipe trials scored end to end, 186 of them against a "
         "usable OptiTrack reference and 116 with a phone IMU alongside.\n\n"
         "Four channels on every trial: OptiTrack (the reference), phone IMU, "
         "MediaPipe 2D pixel angle, MediaPipe world-landmark angle."),
        ("At two levels",
         "The angle itself - offset-removed RMSE against OptiTrack, which is "
         "the question of whether the channel tracks the limb.\n\n"
         "And the derived PT score - whether the seven parameters computed "
         "off that angle land in the same place. These turn out to be "
         "different questions with different answers."),
        ("The honest caveat",
         "Median 2D detection is 72.1%, and 100 of 296 trials fall under 50%. "
         "MediaPipe is not being scored on its best day everywhere.\n\n"
         "So the comparison reports both: how it does where the camera "
         "cooperates, and what happens where it does not."),
        ("One reference correction",
         "The OptiTrack loader was fixed on 1 Sep and pinned against "
         "synthetic ground truth. It changed the reference SHAPE, not just "
         "its datum.\n\n"
         "Every figure here uses the corrected reference. Pre-September "
         "accuracy numbers are not comparable with these."),
    ], top=2.75, body_size=13.5)
    return s


def s_mp_angle(prs):
    s = add_slide(prs)
    header(s, "At The Angle Level, The Two Channels Tie")
    place_in_box(s, MC / "rmse_by_participant.png", 0.45, 1.15, 8.1, 5.6)
    text(s, 8.85, 1.45, 4.05, 5.2, [
        ("13.7 deg against 17.6 deg.", 16, True, NAVY),
        ("\n", 0, False, BODY),
        ("Median offset-removed knee-angle RMSE against OptiTrack: phone IMU "
         "13.7, MediaPipe 17.6. The IMU has the lower median on 9 of 14 "
         "participants.", 14, False, BODY),
        ("\n", 0, False, BODY),
        ("But look at the spread.  ", 14, True, NAVY),
        ("The within-participant scatter dwarfs the between-channel "
         "difference. Neither channel is consistently better at recovering "
         "the knee angle.", 14, False, BODY),
        ("\n", 0, False, BODY),
        ("This is the result that narrowed the decision.  ", 14, True, ORANGE),
        ("MediaPipe was not dropped because it tracks the limb badly. It "
         "tracks it about as well as the phone. The separation is somewhere "
         "else.", 14, False, BODY),
    ], spacing=1.1)
    return s


def s_mp_pt(prs):
    s = add_slide(prs)
    header(s, "At The Score Level, It Does Not")
    place_in_box(s, MC / "pt_agreement_by_participant.png", 0.45, 1.26, 12.45, 4.74)
    micro_label(s, 0.62, 6.06, "THE ANSWER DEPENDS ON ONE BOOLEAN, SO BOTH PANELS ARE SHOWN")
    caption(s, 6.36, [
        ("In production the phone scores undetrended and OptiTrack detrended, "
         "so panel A compares two conventions as well as two sensors - and "
         "the channels come out tied, 6 of 13 to the IMU. Score both the same "
         "way (panel B) and the IMU separates: better on 12 of 13 "
         "participants, pooled |ΔPT7| 0.505 → 0.262 and correlation with "
         "OptiTrack +0.388 → +0.614, while MediaPipe stays at 0.532 / "
         "+0.370 under either. This is a measurement, not a recommendation to change the "
         "convention.", 14.5, False, BODY),
    ], height=1.1)
    return s


def s_mp_convention(prs):
    s = add_slide(prs)
    header(s, "Why That Boolean Is The Largest Single Term")
    place_in_box(s, MC / "detrend_convention.png", 0.45, 1.26, 12.45, 4.74)
    caption(s, 6.02, [
        ("OptiTrack does not drift, so detrending barely moves it - 0.043 "
         "median |ΔPT7| on the same curve. The phone does drift, and the "
         "production convention leaves that drift in the score: 0.368, more "
         "than eight times larger, and larger than the difference between "
         "the two sensors.", 15, False, BODY),
        ("\n", 0, False, BODY),
        ("The consequence for reporting:  ", 15, True, NAVY),
        ("any IMU-versus-optical agreement figure has to say which convention "
         "pairing it used, because the two answers differ by more than the "
         "instruments do. That is now a stated requirement on every "
         "cross-modality figure we publish.", 15, False, BODY),
    ], height=1.42)
    return s


def s_mp_icc(prs):
    s = add_slide(prs)
    header(s, "Per Parameter, So A Failure Can Be Located")
    # ar 2.00 across the full 12.45in width needs 6.22in of height, which does
    # not fit under a header. Height-constrain it and keep the reading short:
    # this is an eight-panel figure and squeezing it two-up would make it
    # unreadable, which is a content failure, not a styling one.
    place_in_box(s, MC / "per_metric_icc.png", 0.45, 1.26, 12.45, 4.74)
    caption(s, 6.10, [
        ("ICC(2,1) against OptiTrack, phone IMU (blue) against MediaPipe 2D "
         "(orange). The IMU leads on seven of eight: N +0.62 against +0.33, "
         "φ max ratio +0.57 against +0.14, and R2n, ω max and A0 are "
         "outright negative for MediaPipe while the IMU is at or above "
         "zero. Area ratio is the exception and it is a tie, +0.15 against "
         "+0.16. The derived score, not the tracked angle, is where the "
         "optical route fails.",
         14.5, False, BODY),
    ], height=1.1)
    return s


def s_mp_bias(prs):
    s = add_slide(prs)
    header(s, "Every Derived Channel Over-Scores. MediaPipe Most.")
    place_in_box(s, MC / "pt7_by_methodology.png", 0.45, 1.26, 12.45, 4.58)
    caption(s, 5.95, [
        ("PT7 medians on the same trials: OptiTrack 0.35, phone IMU 0.73, "
         "MediaPipe 2D 0.95, MediaPipe world 0.85. MediaPipe sits near the top "
         "of the usable scale on the median trial, so when those scores are "
         "mapped to MAS it grades roughly 60% of trials at the ceiling - "
         "against 31% for OptiTrack. A bias in the derived composite, not in "
         "the tracked angle.", 15, False, BODY),
        ("\n", 0, False, BODY),
        ("Note also how few trials each channel can grade at all: 163 of 229 "
         "clear the excursion gate optically, 129 on the phone, 159 on "
         "MediaPipe.", 14, False, SLATE),
    ], height=1.48)
    return s


def s_mp_framing(prs):
    s = add_slide(prs)
    header(s, "Robustness Is The Stronger Argument")
    place_in_box(s, MC / "waveforms_framing_contrast.png", 0.42, 1.26, 9.20, 5.75)
    text(s, 9.75, 1.55, 3.20, 5.3, [
        ("Same three curves. Two camera setups.", 15.5, True, NAVY),
        ("\n", 0, False, BODY),
        ("Top: Control_8, lateral view with the whole supine body in frame. "
         "99.6% median detection - MediaPipe tracks the swing.",
         13.5, False, BODY),
        ("\n", 0, False, BODY),
        ("Bottom: MINT_18, the patient's head and shoulders cropped out and a "
         "standing assessor fully in frame. 5.5% median detection - the "
         "optical curve collapses into a handful of samples.",
         13.5, False, BODY),
        ("\n", 0, False, BODY),
        ("The IMU curve does not depend on where the camera was pointed.",
         13.5, True, TEAL),
        ("\n", 0, False, BODY),
        ("Framing is not a tuning parameter we control in a clinic. It is "
         "whoever set the tripod up that day.", 13.5, False, SLATE),
    ], spacing=1.08)
    return s


def s_mp_verdict(prs):
    s = add_slide(prs)
    header(s, "What This Settles, And What It Does Not", alt=True)
    text(s, 0.73, 1.18, 11.87, 0.4,
         [("The decision to keep the app IMU-only stands. Its stated reason "
           "has been narrowed by measurement, which is a different thing.",
           14, False, SLATE)])
    finding_card(s, 1.65, "1", "MediaPipe recovers the knee angle about as well as the phone",
                 "13.7 deg against 17.6 deg offset-removed, with "
                 "within-participant spread far larger than the gap. Filling "
                 "MediaPipe's undetected frames to make a continuous trace "
                 "costs 0.00 median, so the parity is not an artefact of "
                 "scoring it only where it succeeds.", TEAL, h=1.30)
    finding_card(s, 3.10, "2", "What fails is the derivation, not the tracking",
                 "PT7 built on that angle correlates +0.370 with the "
                 "reference - and agreed with a clinician exactly on 31.5% of "
                 "gradeable trials when KD6 was settled. Better tracking is "
                 "therefore not a route back to a claim path: the score built "
                 "on the curve is the problem, not the curve.", BAD,
                 h=1.30)
    finding_card(s, 4.55, "3", "So the app stays IMU-based, and MediaPipe stays advisory",
                 "It remains valuable as a second opinion and as the only "
                 "channel that can be run retrospectively on 606 archived "
                 "videos. It is pinned as advisory on the clinician-facing "
                 "surface, and it is not a claim path.", PURPLE, h=1.30)
    micro_label(s, 0.73, 6.25, "AND ONE THING THIS FIGURE SET DOES NOT SETTLE")
    caption(s, 6.58, [
        ("Whether the phone's own numbers are right. Every comparison here is "
         "against OptiTrack, which is itself an in-vivo reference with no "
         "independently known angle. That is the question the bench rig "
         "answers, and it is Part Four.", 14, False, BODY),
    ], left=0.73, width=11.87, height=0.8)
    return s


# == PART FOUR - WHAT IS COMING ================================================
def s_testretest(prs):
    s = add_slide(prs)
    header(s, "Test-Retest Is Ready To Run Today")
    stat_cards(s, [
        ("42", "Participant x leg x session groups with three or more repeat "
               "trials, already captured. Up from 40 last week."),
        ("14", "Participants contributing them - every participant who has "
               "phone IMU, not a subset."),
        ("181", "Repeat trials in total: 131 MS, 34 stroke, 16 control."),
    ])
    place_in_box(s, FIG / "wa_testretest.png", 0.50, 2.95, 8.3, 4.15)
    text(s, 9.05, 3.15, 3.85, 3.9, [
        ("No dependencies.", 15, True, NAVY),
        ("\n", 0, False, BODY),
        ("Within-session repeatability needs no reference instrument, no MAS "
         "grade and no bench rig - only the same leg measured more than once "
         "in one sitting, which we already have.", 13.5, False, BODY),
        ("\n", 0, False, BODY),
        ("It answers the question a clinician asks first: measure the same "
         "leg twice, do you get the same score?", 13.5, False, BODY),
        ("\n", 0, False, BODY),
        ("Between-session reliability is a separate and harder claim, and "
         "that is the one the motor rig is being built to carry.",
         13.5, False, SLATE),
    ], spacing=1.1)
    return s


def s_rig_why(prs):
    s = add_slide(prs)
    header(s, "Why A Motor, And Not More Participants")
    place_in_box(s, FIG / "wa_bottleneck.png", 0.50, 1.15, 7.7, 4.2)
    text(s, 8.55, 1.30, 4.4, 4.3, [
        ("The corpus has nothing above MAS 1+.", 15, True, BAD),
        ("\n", 0, False, BODY),
        ("Ten clinician-examined legs - six graded above zero, four at zero. "
         "The range where the composite score is known to invert, MAS 2 and "
         "3, has never been measured on a human.", 13.5, False, BODY),
        ("\n", 0, False, BODY),
        ("And on a human leg the true angle is never independently known, so "
         "sensor error and algorithm error stay confounded. A bench with known "
         "truth is the only way to separate them.", 13.5, False, BODY),
    ], spacing=1.1)
    micro_label(s, 0.62, 5.60, "WHAT THE RIG SUPPLIES THAT RECRUITMENT CANNOT")
    caption(s, 5.94, [
        ("MAS 2 and 3 on demand, repeatable from a recorded setting; a known "
         "angle and a known angular rate to compare against; and a limb that "
         "several clinicians can grade blind, in randomised order, so the "
         "stiffness-to-grade mapping comes from clinicians alone and never "
         "from the instrument it will be used to validate.", 14.5, False, BODY),
    ], height=1.3)
    return s


def s_rig_hardware(prs):
    s = add_slide(prs)
    header(s, "The Motor, And How It Is Driven")
    columns(s, [
        ("The motor",
         "Kollmorgen ServoDisc JR 14 MC4-1.\n\n"
         "A 3D-printed frame holds it perpendicular to the ground so the "
         "swing plane is the sagittal one.\n\n"
         "The Biodex carries a motor too - unscrewing it and side-mounting "
         "gives a second route to getting a prosthetic leg onto a shaft."),
        ("The control loop",
         "LabVIEW on a real-time OS follows a trapezoidal position path.\n\n"
         "Timing comes from a PCI timing card; the DAQ deck is the output "
         "stage that drives the motor, not the thing closing the loop.\n\n"
         "Two computers, nested loops: one runs the encoder, one runs "
         "LabVIEW."),
        ("What changed",
         "The earlier approach parked the motor at a zero point and let it "
         "fight its way back to it.\n\n"
         "The new one commands positions at set speeds instead - which is "
         "what a pendulum drop actually is, and what the seven scored "
         "parameters are features of."),
        ("Open hardware questions",
         "A joint attaches to the motor shaft but cannot currently be encoded "
         "for. That is the gating unknown.\n\n"
         "A pulley or an angled mount with a gearbox would shift weight "
         "downward; a 3D hinge joint adds the lateral axis."),
    ], top=1.35, body_size=13.5)
    micro_label(s, 0.62, 5.55, "THE OTHER HALF OF THE RIG")
    caption(s, 5.89, [
        ("A prosthetic leg on an encoder, so the swing is a real limb with a "
         "known angle. Adding weights shows how mass changes the pendulum "
         "output; a mechanical stopper mimics clonus; and the encoder answers "
         "the direct question - can the phone measure the drop as accurately "
         "as a goniometer can?", 14.5, False, BODY),
    ], height=1.3)
    return s


def s_rig_mapping(prs):
    s = add_slide(prs)
    header(s, "Mapping The Seven Scored Parameters Onto Motor Terms")
    text(s, 0.62, 1.22, 12.4, 0.4,
         [("Each parameter the app reports has a control-side knob that moves "
           "it. This is the specification the motor code is written against.",
           14, False, SLATE)])
    rows = [
        ["Metric to mimic", "How it is coded in the motor controller"],
        ["1.  Relaxation index",
         "Raise active extensor gain and short-range stiffness - an immediate, "
         "sharp resistance spike at the onset of the drop"],
        ["2.  Number of swings  N",
         "Raise passive joint damping and program a prolonged reflex decay "
         "constant, keeping the simulated muscles active across swings"],
        ["3.  First extension maximum",
         "Trigger active flexor torque on a velocity-threshold gate, as soon "
         "as the rate goes negative"],
        ["4.  Peak positive velocity",
         "Governed directly by the short-range stiffness constant; higher "
         "pre-movement tone scales it up and brakes the swing"],
        ["5.  Peak negative velocity",
         "Program the extensor reflex delay - a shorter delay engages "
         "resistance sooner and suppresses the rebound peak"],
        ["6.  Oscillation frequency  f",
         "Raise passive stiffness relative to the lower-leg moment of "
         "inertia; high joint stiffness raises the swing frequency"],
        ["7.  Area ratio asymmetry",
         "Asymmetric thresholds and gains per channel. For extension-type "
         "spasticity, extensor gain much higher than flexor gain"],
        ["8.  Lateral motion",
         "On a single-axis knee motor, inject a low-amplitude high-frequency "
         "torque disturbance correlated with the sagittal velocity peaks"],
    ]
    table(s, rows, [3.45, 8.95], 0.62, 1.72, row_h=0.53, body_size=12.5)
    caption(s, 6.55, [
        ("Item 8 is not one of the seven, and it is on the list because the "
         "IMU can see it and a single camera cannot. Deriving and plotting "
         "lateral motion from the hip roll channel is already a named unit of "
         "work - the signal is written into every trial CSV today and nothing "
         "downstream reads it.", 13.5, False, BODY),
    ], height=0.9)
    return s


def s_rig_protocol(prs):
    s = add_slide(prs)
    header(s, "How The Rig Gets Calibrated")
    columns(s, [
        ("Step 1 - Source the waveforms",
         "Ask Monica for her recorded waveforms.\n\n"
         "Run them through the same PT-score analysis the app uses, so the "
         "target is a measured curve rather than an assumed one.\n\n"
         "Map the resulting parameter values onto the motor terms opposite."),
        ("Step 2 - Tune and verify",
         "Tune the arm to mimic spasticity, stiffness and clonus "
         "independently, and confirm each shows up in the parameter it is "
         "supposed to move.\n\n"
         "Before any active torque is layered on, run the rig unactuated at "
         "the passive setting and compare its swing envelope against human "
         "healthy controls. Report that comparison whatever it shows - "
         "otherwise the rig could mimic tone without mimicking a limb."),
        ("Step 3 - Blind clinician assessment",
         "Dial profiles spanning MAS 0, 1, 1+, 2 and 3, each reproducible "
         "from a recorded setting.\n\n"
         "More than one clinician grades each profile, blind to the setting, "
         "in an order randomised per assessor. Inter-rater agreement is "
         "reported alongside the mapping.\n\n"
         "No instrument-derived quantity feeds the mapping it will be "
         "validated against."),
    ], top=1.40, left0=0.62, width=3.90, gap=0.35, body_size=13.5)
    micro_label(s, 0.62, 5.75, "WHAT THAT UNLOCKS, IN ORDER")
    caption(s, 6.09, [
        ("The two anchors for the A0 severity index, which is specified and "
         "will stay invisible until they exist; a recalibrated reference to "
         "replace HEALTHY_REF; the first error budget that separates sensor, "
         "grid, filtering and mounting against a known truth; and "
         "between-session reliability on a limb that does not change between "
         "sessions.", 14.5, False, BODY),
    ], height=1.2)
    return s


def s_next(prs):
    s = add_slide(prs)
    header(s, "What Is Still To Come", alt=True)
    text(s, 0.73, 1.18, 11.87, 0.4,
         [("Five units of the instrument-validation plan remain, ordered so "
           "the cheapest step that can invalidate the next one comes first.",
           14, False, SLATE)])
    rows = [
        ["Unit", "What it does", "Needs the rig?"],
        ["U5  Refuse a drifting hold",
         "A trial whose pre-release hold cannot be trusted is tagged with its "
         "measured drift instead of silently scoring anyway. Code flags, "
         "never drops.", "No"],
        ["U6  Derive and plot lateral motion",
         "hip_roll_deg is written into every trial CSV and read by nothing; "
         "OptiTrack's out-of-plane rotation is computed and discarded. "
         "Surface both per trial.", "No"],
        ["U8  Two-tier MAS reporting",
         "Construct validity claimed on the ten clinician-examined legs only; "
         "any wider figure labelled exploratory with its composition named.",
         "No"],
        ["U9  State the dual-variable model",
         "A refused PT7 grade reads as a suppressed classification, not a "
         "failed measurement, and names A0 as the severity variable.", "No"],
        ["U7  The A0 severity index",
         "Built, tested against its anchors, and displaying nothing until "
         "anchors exist. Floored at zero, unbounded above, one marks the "
         "normative boundary.", "Yes - for the anchors"],
    ]
    table(s, rows, [3.15, 7.05, 2.20], 0.73, 1.62, row_h=0.62, body_size=12,
          accent_col=2)
    micro_label(s, 0.73, 5.60, "AND THE TWO CLINICAL-SIDE MOVES THAT COST NO RECORDING TIME")
    caption(s, 5.94, [
        ("Re-assess P4 so today's session has a condition-matched grade "
         "instead of a June carry - two legs, and they are the only "
         "within-person MAS split we have. Then recover MAS on the seven "
         "participants who have IMU and no grade: fourteen legs, four of them "
         "stroke. That is the root of the chain - it releases the clinical "
         "arm, then the reference recalibration, then the score.",
         14.5, False, BODY),
    ], left=0.73, width=11.87, height=1.3)
    return s


def main() -> int:
    prs = Presentation(SRC)
    before = len(prs.slides)
    drop_slides(prs, KEEP)

    divider(prs, "PART ONE", "Who we have recorded",
            "The cohort as it stands on 8 September 2026")
    s_cohort(prs)
    s_cohort_note(prs)

    divider(prs, "PART TWO", "The mobile app",
            "What shipped, how solid it is, and what it still cannot say")
    s_app_today(prs)
    s_app_grid(prs)
    s_app_quality(prs)
    s_app_gaps(prs)

    divider(prs, "PART THREE", "IMU against MediaPipe",
            "Six figures, regenerated today against the corrected optical "
            "reference")
    s_mp_question(prs)
    s_mp_angle(prs)
    s_mp_pt(prs)
    s_mp_convention(prs)
    s_mp_icc(prs)
    s_mp_bias(prs)
    s_mp_framing(prs)
    s_mp_verdict(prs)

    divider(prs, "PART FOUR", "Test-retest, and the motor",
            "One result ready today, and the bench rig that carries the rest")
    s_testretest(prs)
    s_rig_why(prs)
    s_rig_hardware(prs)
    s_rig_mapping(prs)
    s_rig_protocol(prs)
    s_next(prs)

    prs.save(OUT)
    print(f"{before} slides in -> {len(prs.slides)} out "
          f"(kept {sorted(KEEP)}, appended {len(prs.slides) - len(KEEP)})")
    print(f"wrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
