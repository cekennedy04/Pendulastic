"""
spasticity_grouping.py
======================
Group participant LEGS by spasticity rather than by diagnosis.

Why this exists
---------------
Analyses were grouping by condition (MS / Stroke / Control). Diagnosis is not
the physiological variable the pendulum test measures -- two people with the
same diagnosis can have very different tone, and tone is what the swing
responds to. So the grouping unit here is the (participant, leg) pair, because
MAS is assessed per leg and PT parameters are computed per leg. P4 is the
worked example: MAS 0 on the left, 1+ on the right.

Where a label comes from, in strict precedence order
----------------------------------------------------
1. CLINICAL MAS overall grade for that leg, when one has been recorded.
   Always wins.
2. CLINICAL MAS COMPONENTS (flexion / extension) when the overall grade is
   still pending but the components were scored. Real clinical evidence, so it
   outranks anything derived -- but it never overrides an overall grade that
   exists. P15 left is graded 0 overall with flexion 1+; the clinician's
   summary judgement stands, and the disagreement is left visible rather than
   averaged away.
3. CONTROL by diagnosis -> non-spastic. Unaffected controls are not assessed
   with MAS, but they are non-spastic by recruitment.
4. A0-DERIVED from the OptiTrack pendulum swing, for a diagnosed participant
   with no MAS yet. This is the stopgap until MAS is collected -- currently
   7 of 21 participants have no overall MAS, including all four post-stroke
   participants.
5. UNKNOWN. Never silently dropped, never guessed. An unknown leg is reported
   as unknown so it stays visible; see the project rule that code flags data
   and the operator decides.

Why A0 and NOT the PT7 score
----------------------------
PT7 cannot rank spasticity severity. It is U-shaped in tone: validated against
simulator ground truth, a nearly rigid leg (tone 8 N.m, A0 4.2 deg) scores
0.091 -- inside the HEALTHY band -- while a mild-moderate leg (tone 1.5) scores
1.093. Every scored PT7 parameter is a ratio normalised on the swing itself, so
when the swing collapses the ratios renormalise on a small, clean motion and a
barely-moving leg looks pristine. Deriving spasticity from PT7 would therefore
label the MOST affected legs non-spastic, and would do it most often to exactly
the participants who lack MAS.

A0_deg (peak swing amplitude) is the one quantity that IS monotonic in severity
(46.4 deg at tone 0 falling to 4.2 deg at tone 8). It is already computed by
compute_pt_params; it is simply not one of the seven scored parameters.

What the A0 threshold can and cannot do
---------------------------------------
A0_SPASTIC_MAX_DEG is set from this cohort's own known-MAS-0 legs: mean 52.5,
sd 7.2, so mean - 2sd = 38.1 deg. Measured against the legs with clinical MAS:

    flags 2 of 5 known-spastic legs, 0 of 15 known-normal legs

So it is specific but not sensitive. It detects MARKED amplitude loss (the P13
legs, at 29.5 and 21.5 deg) and cannot separate mild spasticity from normal --
three MAS-1 legs sit at 45-50 deg, inside the healthy range. A derived label of
"non-spastic" therefore means "no marked amplitude loss detected", NOT "MAS 0".
Only a clinical MAS can say the latter, which is why source is returned
alongside every label and why clinical MAS always wins.

Two further caveats worth carrying:
  * A0 is the initial extension above neutral, so it also reflects how far the
    examiner lifted the leg. Passive range limitation is part of the signal,
    but examiner variability is a confound.
  * Most OptiTrack trials have poor marker coverage. A0 taken from a trial the
    cameras half-missed is unreliable, so callers should pass a coverage-
    filtered amplitude or accept the label as provisional.

Why the derived label is OptiTrack-only
---------------------------------------
IMU-derived A0 is NOT accepted as a source, even though it exists for legs
OptiTrack cannot reconstruct (P22 left is the live case). Two reasons, measured
2026-08-27:

  * IMU A0 runs systematically HIGHER than OptiTrack A0 on the same leg --
    median +20.4 deg, mean +20.3, sd 8.4 across the 16 legs where both exist.
    The OptiTrack threshold cannot be reused on IMU amplitudes.
  * An IMU-specific threshold cannot be calibrated on this cohort. Clinical MAS
    covers P2-P15; the IMU recordings cover P14-P24. Only ONE known-MAS-0 leg
    has IMU data (P15 left, 63.2 deg), and it sits INSIDE the range of the three
    known-MAS>=1 legs that do (56.8-75.5). There is no separation to fit to, so
    any IMU threshold would be invented rather than derived.

The consequence is deliberate: a leg with only IMU data is UNKNOWN rather than
labelled from a number that has no calibrated meaning. Participant-level
characterisation still succeeds through the other leg. Once MAS is collected
for the P17-P24 range, an IMU threshold becomes derivable and this can change.
"""
from __future__ import annotations

import csv
import os
from typing import NamedTuple, Optional

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MAS_CSV = os.path.join(BASE_DIR, "mas_scores.csv")

# Levels. Deliberately binary: this cohort's clinical MAS values only span
# 0, 1 and 1+, and the A0 proxy cannot resolve grades within "spastic".
NON_SPASTIC = "non-spastic"
SPASTIC = "spastic"
UNKNOWN = "unknown"

# Where a label came from. Always reported, so an analysis can restrict itself
# to clinically-labelled legs, and so a derived label is never mistaken for an
# assessed one.
SRC_CLINICAL = "clinical-mas"
# Overall grade still pending, but the flexion/extension components were
# scored. Kept distinct from SRC_CLINICAL so nobody reads a component-only
# verdict as a completed assessment.
SRC_CLINICAL_COMPONENT = "clinical-mas-components"
SRC_CONTROL = "control-by-recruitment"
SRC_A0 = "a0-derived"
SRC_NONE = "no-data"

# mas_validation.PENDING_MAS_GRADE -- "assessed_date set, grade not yet given".
PENDING_MAS_GRADE = "-1"

# Peak swing amplitude (degrees) at or below which a leg with no clinical MAS
# is called spastic. See the module docstring for the derivation and for what
# this threshold provably does and does not detect.
A0_SPASTIC_MAX_DEG = 38.1


class SpasticityLabel(NamedTuple):
    """A leg's spasticity level plus the provenance of that level.

    `detail` is a short human-readable note (the MAS grade, or the amplitude
    and threshold) so a reader can see why the label came out as it did.
    """
    level: str
    source: str
    detail: str


def mas_level(grade: Optional[str]) -> Optional[str]:
    """Map a clinical MAS grade to a level, or None if it carries no verdict.

    A blank grade and the PENDING_MAS_GRADE sentinel both mean "not assessed
    yet" -- neither is evidence of absent spasticity, so neither returns
    NON_SPASTIC.
    """
    g = (grade or "").strip()
    if not g or g == PENDING_MAS_GRADE:
        return None
    return NON_SPASTIC if g == "0" else SPASTIC


def load_mas_by_leg(path: str = None, condition_prefix: str = "pre") -> dict:
    """{(participant, leg): grade} from mas_scores.csv for one timepoint.

    Keys are normalised to (str pid, lowercase leg). Rows whose grade carries
    no verdict are skipped, so a caller that finds no key knows the leg is
    genuinely unassessed rather than assessed-as-blank.
    """
    path = path or MAS_CSV
    out = {}
    if not os.path.exists(path):
        return out
    with open(path, encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            cond = (row.get("condition") or "").strip().lower()
            if condition_prefix and not cond.startswith(condition_prefix):
                continue
            if mas_level(row.get("mas_grade")) is None:
                continue
            pid = (row.get("participant") or "").strip()
            leg = (row.get("leg") or "").strip().lower()
            if pid and leg:
                out[(pid, leg)] = (row.get("mas_grade") or "").strip()
    return out


def load_mas_components_by_leg(path: str = None,
                               condition_prefix: str = "pre") -> dict:
    """{(participant, leg): (flexion, extension)} from mas_scores.csv.

    Some legs have the flexion and extension components scored while the
    OVERALL grade is still PENDING_MAS_GRADE. That is real clinical evidence,
    and discarding it leaves a participant uncharacterised for no good reason
    -- P17 is the live case, right leg flexion 1 with the overall pending.
    """
    path = path or MAS_CSV
    out = {}
    if not os.path.exists(path):
        return out
    with open(path, encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            cond = (row.get("condition") or "").strip().lower()
            if condition_prefix and not cond.startswith(condition_prefix):
                continue
            flex = (row.get("mas_flexion") or "").strip()
            ext = (row.get("mas_extension") or "").strip()
            if not flex and not ext:
                continue
            pid = (row.get("participant") or "").strip()
            leg = (row.get("leg") or "").strip().lower()
            if pid and leg:
                out[(pid, leg)] = (flex, ext)
    return out


def component_level(components) -> Optional[str]:
    """Verdict from a (flexion, extension) component pair, or None.

    Spastic if EITHER component is graded above 0 -- tone in one direction is
    tone. Non-spastic only when at least one component was scored and none is
    positive.
    """
    if not components:
        return None
    seen = [mas_level(c) for c in components if (c or "").strip()]
    seen = [lv for lv in seen if lv is not None]
    if not seen:
        return None
    return SPASTIC if SPASTIC in seen else NON_SPASTIC


def classify_leg(pid: str, leg: str, *, arm: Optional[str] = None,
                 mas_by_leg: Optional[dict] = None,
                 mas_components: Optional[dict] = None,
                 a0_deg: Optional[float] = None) -> SpasticityLabel:
    """Label one (participant, leg) by spasticity, with its provenance.

    `arm` is the diagnosis-derived arm ("MS" / "Stroke" / "Control" / ...) as
    produced by pt_cohort_common.classify_participant; only "Control" is
    consulted, and only to supply the non-spastic label that recruitment
    already implies. `a0_deg` is the leg's peak swing amplitude, typically a
    median across that leg's trials.

    Precedence is strict and documented in the module docstring. Clinical MAS
    always wins; a missing input never becomes a guess.
    """
    pid = str(pid).strip()
    leg = str(leg).strip().lower()

    mas_by_leg = mas_by_leg if mas_by_leg is not None else load_mas_by_leg()
    grade = mas_by_leg.get((pid, leg))
    level = mas_level(grade)
    if level is not None:
        return SpasticityLabel(level, SRC_CLINICAL, f"MAS {grade}")

    # Overall grade pending, components scored. Never overrides an overall
    # grade that exists: P15 left is graded 0 overall with flexion 1+, and the
    # clinician's summary judgement is the one to keep -- but the disagreement
    # is worth knowing about, so it is not quietly averaged away either.
    comp_level = component_level((mas_components or {}).get((pid, leg)))
    if comp_level is not None:
        flex, ext = (mas_components or {})[(pid, leg)]
        return SpasticityLabel(comp_level, SRC_CLINICAL_COMPONENT,
                               f"overall grade pending; flexion {flex or '-'}, "
                               f"extension {ext or '-'}")

    if (arm or "").strip().lower() == "control":
        return SpasticityLabel(NON_SPASTIC, SRC_CONTROL,
                               "unaffected control, not MAS-assessed")

    if a0_deg is not None and a0_deg == a0_deg:      # not NaN
        a0 = float(a0_deg)
        if a0 <= A0_SPASTIC_MAX_DEG:
            return SpasticityLabel(
                SPASTIC, SRC_A0,
                f"A0 {a0:.1f} deg <= {A0_SPASTIC_MAX_DEG:.1f} threshold")
        # Says only that no MARKED amplitude loss was found. The threshold
        # misses mild spasticity by construction -- see the module docstring.
        return SpasticityLabel(
            NON_SPASTIC, SRC_A0,
            f"A0 {a0:.1f} deg > {A0_SPASTIC_MAX_DEG:.1f} threshold; "
            "mild spasticity is not detectable this way")

    return SpasticityLabel(UNKNOWN, SRC_NONE,
                           "no clinical MAS and no usable swing amplitude")


def summarise(labels: dict) -> dict:
    """{level: count} over a {(pid, leg): SpasticityLabel} mapping.

    Every level key is always present, including UNKNOWN at zero, so a caller
    rendering a table never silently omits the unknown row.
    """
    counts = {NON_SPASTIC: 0, SPASTIC: 0, UNKNOWN: 0}
    for lab in labels.values():
        counts[lab.level] = counts.get(lab.level, 0) + 1
    return counts


LEGS = ("left", "right")


def classify_participant_legs(pid: str, *, arm: Optional[str] = None,
                              mas_by_leg: Optional[dict] = None,
                              mas_components: Optional[dict] = None,
                              a0_by_leg: Optional[dict] = None) -> dict:
    """{leg: SpasticityLabel} for BOTH legs of one participant, always.

    Enumerates from LEGS rather than from whatever data happens to exist. A leg
    with no recordings at all must appear as UNKNOWN, not vanish: P7 left and
    both P16 legs were invisible while the leg list was built from the data,
    which reads as full coverage when it is not.
    """
    a0_by_leg = a0_by_leg or {}
    return {leg: classify_leg(pid, leg, arm=arm, mas_by_leg=mas_by_leg,
                              mas_components=mas_components,
                              a0_deg=a0_by_leg_value(a0_by_leg, leg))
            for leg in LEGS}


def a0_by_leg_value(a0_by_leg: dict, leg: str):
    """Amplitude for `leg` from a {leg: A0} mapping, tolerating either a bare
    leg key or a (pid, leg) key so callers can pass whichever they hold."""
    if leg in a0_by_leg:
        return a0_by_leg[leg]
    for key, val in a0_by_leg.items():
        if isinstance(key, tuple) and len(key) == 2 and str(key[1]).lower() == leg:
            return val
    return None


def participant_level(leg_labels: dict) -> SpasticityLabel:
    """Roll per-leg labels up into one characterisation for the participant.

    SPASTIC if ANY leg is spastic. Spasticity is unilateral in hemiparesis --
    P21 aside, a stroke participant can easily be spastic on one side only --
    so requiring both legs would erase exactly the participants this grouping
    exists to identify. NON_SPASTIC only when at least one leg is known and no
    known leg is spastic. UNKNOWN only when NO leg could be labelled.
    """
    levels = [lab.level for lab in leg_labels.values()]
    known = [lv for lv in levels if lv != UNKNOWN]
    if SPASTIC in levels:
        spastic_legs = sorted(lg for lg, lab in leg_labels.items()
                              if lab.level == SPASTIC)
        src = ", ".join(sorted({leg_labels[lg].source for lg in spastic_legs}))
        return SpasticityLabel(SPASTIC, src, f"spastic on: {', '.join(spastic_legs)}")
    if known:
        srcs = ", ".join(sorted({lab.source for lab in leg_labels.values()
                                 if lab.level != UNKNOWN}))
        unknown_legs = sorted(lg for lg, lab in leg_labels.items()
                              if lab.level == UNKNOWN)
        note = "no spastic leg found"
        if unknown_legs:
            note += f"; {', '.join(unknown_legs)} not assessable"
        return SpasticityLabel(NON_SPASTIC, srcs, note)
    return SpasticityLabel(UNKNOWN, SRC_NONE, "no leg could be labelled")
