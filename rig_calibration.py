"""Benchtop rig calibration: profiles, blind clinician grades, and the anchors.

The instrument has no severity reference. Only 10 legs across 5 participants
were examined by a clinician and every one of those grades sits at MAS 0 or 1,
so the range where PT7 is known to invert has never been measured on a human.
The rig exists to produce that range on demand.

This module is the software half. It holds the record a calibration session
produces, derives the stiffness-to-grade mapping from clinician assessments
ALONE, and turns the result into the two anchors the severity index needs. It
does not model the rig's mechanics and does not decide when to trust it.

The load-bearing rule, R3: instrument output is never an input to the mapping
it will later be validated against. `stiffness_to_grade` cannot see an
instrument reading -- not because a caller would be careless, but because a
mapping fitted partly on the instrument would agree with the instrument by
construction, and that agreement would look like validation.

Nothing here reads live data. Until a real calibration session exists,
`load_session` returns an empty record and `derive_anchors` returns None, which
is the state R16 requires the app to display: excursion and kinematics, no
index, no zone.
"""
from __future__ import annotations

import json
import os
import statistics
from typing import Optional

#: Ordered MAS grades a rig profile may be assessed as. Mirrors
#: pendulastic_pt_score.MAS_ORDER; "4" is excluded deliberately -- the rig is
#: specified to span 0 through 3 (R1), and a grade outside what it can produce
#: would be an assessor error rather than a reading.
RIG_GRADES = ("0", "1", "1+", "2", "3")

#: Rank for ordinal work. "1+" sits between 1 and 2, which is the whole reason
#: the scale cannot be held in a float.
RIG_GRADE_RANK = {g: i for i, g in enumerate(RIG_GRADES)}

#: Minimum assessors per profile before a mapping is worth reporting (R4). Two
#: gives an agreement number at all; one gives a mapping with no way to tell a
#: reading from an opinion.
MIN_ASSESSORS = 2

#: Minimum instrument trials at one setting before its spread is publishable
#: (R7). Below this the "spread" is a gap between two numbers.
MIN_TRIALS_PER_PROFILE = 3

DEFAULT_SESSION_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "rig_calibration.json")


class BlindnessError(ValueError):
    """Raised when a mapping would be built from instrument-tainted input.

    R3 exists because the failure is silent: a stiffness-to-grade mapping
    fitted with the instrument's own readings in it will agree with the
    instrument afterwards, and that agreement reads as validation to anyone who
    was not there when it was built.
    """


def load_session(path: str = None) -> dict:
    """The recorded calibration session, or an empty record.

    Returns {"profiles": [...], "assessments": [...], "readings": [...]}.
    A missing file is the normal state today, not an error: the rig does not
    exist yet, and every consumer must already handle "no anchors".
    """
    path = path or DEFAULT_SESSION_PATH
    empty = {"profiles": [], "assessments": [], "readings": []}
    if not os.path.exists(path):
        return empty
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return empty
    if not isinstance(data, dict):
        return empty
    for key in empty:
        data.setdefault(key, [])
    return data


def assessments_for(session: dict, setting_id) -> list:
    """Every clinician assessment of one dialled setting."""
    return [a for a in session.get("assessments", [])
            if str(a.get("setting_id")) == str(setting_id)
            and a.get("grade") in RIG_GRADE_RANK]


def readings_for(session: dict, setting_id) -> list:
    """Every instrument reading at one dialled setting, as A0 in degrees."""
    out = []
    for r in session.get("readings", []):
        if str(r.get("setting_id")) != str(setting_id):
            continue
        v = r.get("a0_deg")
        try:
            v = float(v)
        except (TypeError, ValueError):
            continue
        if v == v:  # not NaN
            out.append(v)
    return out


def inter_rater_agreement(session: dict, setting_id) -> dict:
    """How much the assessors of one profile agreed (R4).

    Reports exact agreement, agreement within one grade, and the spread in
    ranks. Not a kappa: with a handful of assessors on an ordinal scale of
    five, a chance-corrected coefficient is more precision than the data can
    carry, and the raw disagreement is what a reader needs.
    """
    grades = [a["grade"] for a in assessments_for(session, setting_id)]
    n = len(grades)
    out = {"n_assessors": n, "grades": grades, "exact": None,
           "within_one": None, "rank_spread": None, "sufficient": n >= MIN_ASSESSORS}
    if n < 2:
        return out
    ranks = [RIG_GRADE_RANK[g] for g in grades]
    pairs = [(i, j) for i in range(n) for j in range(i + 1, n)]
    out["exact"] = sum(ranks[i] == ranks[j] for i, j in pairs) / len(pairs)
    out["within_one"] = sum(abs(ranks[i] - ranks[j]) <= 1 for i, j in pairs) / len(pairs)
    out["rank_spread"] = max(ranks) - min(ranks)
    return out


def stiffness_to_grade(session: dict) -> dict:
    """{setting_id: consensus grade} from blind clinician assessment alone.

    R3: this function is given the session and reads ONLY its assessments. It
    raises rather than silently tolerating a caller that tries to pass
    instrument readings in as assessments, because the resulting mapping would
    be circular in a way nothing downstream could detect.

    Consensus is the median rank, which on an ordinal scale is the assessor in
    the middle rather than an average of grades that do not add.
    """
    out = {}
    for a in session.get("assessments", []):
        if "a0_deg" in a or "pt7" in a or "instrument" in a:
            raise BlindnessError(
                "an assessment carries an instrument-derived field "
                f"({sorted(set(a) & {'a0_deg', 'pt7', 'instrument'})}); the "
                "stiffness-to-grade mapping must come from blind clinical "
                "assessment alone (R3)")
    for setting in {str(a.get("setting_id")) for a in session.get("assessments", [])}:
        grades = [a["grade"] for a in assessments_for(session, setting)]
        if len(grades) < MIN_ASSESSORS:
            continue
        ranks = sorted(RIG_GRADE_RANK[g] for g in grades)
        mid = ranks[len(ranks) // 2] if len(ranks) % 2 else \
            ranks[len(ranks) // 2 - 1]  # lower median: do not invent a grade
        out[setting] = RIG_GRADES[mid]
    return out


def derive_anchors(session: dict = None, path: str = None) -> Optional[dict]:
    """The two A0 anchors for the severity index, or None.

    Returns {"healthy_a0_deg", "boundary_a0_deg", "healthy_spread",
    "boundary_spread", "healthy_setting", "boundary_setting", "n_healthy",
    "n_boundary"}.

    The healthy anchor is the instrument's A0 at the profile clinicians graded
    MAS 0; the boundary anchor is its A0 at the lowest profile they graded
    above 0. Index 0 sits at the first, 1 at the second (KD3).

    Returns None -- no index -- unless BOTH anchors are backed by enough
    assessors and enough trials. That is R16's state and it is the default,
    because a partially calibrated index is worse than none: it looks like a
    measurement.
    """
    session = session if session is not None else load_session(path)
    mapping = stiffness_to_grade(session)
    if not mapping:
        return None

    healthy = [s for s, g in mapping.items() if g == "0"]
    affected = sorted((RIG_GRADE_RANK[g], s) for s, g in mapping.items()
                      if RIG_GRADE_RANK[g] > 0)
    if not healthy or not affected:
        return None

    h_setting = healthy[0]
    b_setting = affected[0][1]
    h_trials = readings_for(session, h_setting)
    b_trials = readings_for(session, b_setting)
    if len(h_trials) < MIN_TRIALS_PER_PROFILE or len(b_trials) < MIN_TRIALS_PER_PROFILE:
        return None

    h_a0 = statistics.median(h_trials)
    b_a0 = statistics.median(b_trials)
    if not (h_a0 > b_a0):
        # A0 falls as a limb becomes more affected. If the healthy profile does
        # not swing further than the affected one, the rig or the mapping is
        # wrong, and an index built on it would run backwards.
        return None

    return {
        "healthy_a0_deg": float(h_a0),
        "boundary_a0_deg": float(b_a0),
        # Published with the anchors, never separately (R7): an anchor whose
        # repeat trials scatter is a different object from one that does not.
        "healthy_spread": float(max(h_trials) - min(h_trials)),
        "boundary_spread": float(max(b_trials) - min(b_trials)),
        "healthy_setting": h_setting,
        "boundary_setting": b_setting,
        "n_healthy": len(h_trials),
        "n_boundary": len(b_trials),
    }


# ── R15: the rig's passive profile ───────────────────────────────────────────
#
# Before any active spasticity torque is applied, the rig runs unactuated at the
# passive properties taken as the healthy human standard, so its swing envelope
# can be compared against human controls. If the rig mimics tone without
# mimicking a limb, every later grade validates the instrument against the
# wrong thing -- and nothing else in the plan would catch it.
#
# PROVENANCE. Popović & Bajd (2018), Serbian Journal of Electrical Engineering:
# "The computational result for the healthy subjects outputs the values for the
# viscous damping B = 0.5 Nms/rad and elasticity K = 0.5 Nm/rad. These values
# were used for the determination of the reflexive torque T_h in patients."
# They attribute the baseline to the linearised model of Bajd & Bowman (1982),
# Journal of Biomedical Engineering, fitted to healthy control goniograms.
#
# The model, for a relaxed limb dropping under gravity with T_h = 0:
#
#     J*phi'' + B*phi' + K*phi + (1/2)*m*g*l*phi = 0
#
# with the shank-foot complex as a single rod, m = 5 kg, l = 0.6 m,
# J = (1/3)*m*l^2.

PASSIVE_DAMPING_NMS_PER_RAD = 0.5   #: B
PASSIVE_STIFFNESS_NM_PER_RAD = 0.5  #: K
SHANK_MASS_KG = 5.0                 #: m
SHANK_LENGTH_M = 0.6                #: l
GRAVITY_M_S2 = 9.81


def passive_envelope(B=PASSIVE_DAMPING_NMS_PER_RAD,
                     K=PASSIVE_STIFFNESS_NM_PER_RAD,
                     m=SHANK_MASS_KG, l=SHANK_LENGTH_M, g=GRAVITY_M_S2,
                     start_deg=45.0, stop_deg=3.0):
    """What the linearised healthy model predicts the rig should do.

    Returns inertia, damping factor, natural and damped frequency, damping
    ratio, and the number of cycles before the swing falls below `stop_deg` --
    the field's own stop criterion (Fowler et al.), not this codebase's window.

    This is the target R15 dials the rig to and the prediction its measured
    envelope is checked against. It is a MODEL, not a measurement: m and l are
    population averages for a shank-foot complex, so a real limb differing from
    them moves the frequency without anything being wrong.
    """
    J = (1.0 / 3.0) * m * l ** 2
    beta = B / (2.0 * J)
    gravity_term = 0.5 * m * g * l
    w0_sq = (K + gravity_term) / J
    w0 = w0_sq ** 0.5
    wd = max(w0_sq - beta ** 2, 0.0) ** 0.5
    zeta = beta / w0 if w0 else float("nan")
    import math as _m
    cycles = (_m.log(start_deg / stop_deg) / (2 * _m.pi * zeta)
              if zeta > 0 and start_deg > stop_deg > 0 else float("inf"))
    return {
        "inertia_kg_m2": J,
        "damping_factor_per_s": beta,
        "gravity_term_nm_per_rad": gravity_term,
        "natural_freq_hz": w0 / (2 * 3.141592653589793),
        "damped_freq_hz": wd / (2 * 3.141592653589793),
        "damping_ratio": zeta,
        "cycles_to_stop": cycles,
    }
