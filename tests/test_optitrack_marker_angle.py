# tests/test_optitrack_marker_angle.py
"""
Regression tests for the OptiTrack labeled-marker knee-angle path.

Written 2026-08-26 after P21's right leg produced an anatomically impossible
202 deg post-release curve. Three separate defects were found; each gets a
test here, plus the coverage gate that stops the loader silently fabricating
motion the cameras never captured.
"""
import os, sys, math
import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import pendulastic_pt_score as pts


# ─────────────────────────────────────────────────────────────────────────────
# Synthetic Motive export builder
# ─────────────────────────────────────────────────────────────────────────────

def _rot(axis, deg):
    axis = np.asarray(axis, float); axis = axis / np.linalg.norm(axis)
    th = math.radians(deg); c, s = math.cos(th), math.sin(th)
    K = np.array([[0, -axis[2], axis[1]],
                  [axis[2], 0, -axis[0]],
                  [-axis[1], axis[0], 0]])
    return np.eye(3) * c + s * K + (1 - c) * np.outer(axis, axis)


def _plate(centre, long_axis, tilt_deg, size=0.06):
    """3 markers forming a plate whose PC1 is `tilt_deg` off `long_axis`.

    This is the geometry that broke the old PCA implementation: a real marker
    plate does NOT have its longest extent along the limb.
    """
    long_axis = np.asarray(long_axis, float)
    long_axis = long_axis / np.linalg.norm(long_axis)
    perp = np.cross(long_axis, [0.0, 0.0, 1.0])
    if np.linalg.norm(perp) < 1e-6:
        perp = np.cross(long_axis, [0.0, 1.0, 0.0])
    perp = perp / np.linalg.norm(perp)
    spin = np.cross(long_axis, perp)
    plate_dir = _rot(spin, tilt_deg) @ long_axis
    return np.array([
        centre + plate_dir * size,
        centre - plate_dir * size,
        centre + spin * size * 0.35,
    ])


def _build_trial(n=240, hold=60, flex_deg=40.0, thigh_tilt=22.0, shank_tilt=30.0,
                 drop_from=None, drop_to=None, sign=-1.0):
    """Ground-truth trial: thigh fixed, shank flexes by `flex_deg` after release.

    Returns (rows, truth) where truth[i] is the true interior knee angle.
    `drop_from`/`drop_to` blank out the labeled markers to simulate occlusion.
    """
    hip = np.array([0.0, 0.40, 1.50])
    knee = np.array([0.0, 0.00, 1.50])
    thigh_axis = hip - knee                      # knee -> hip
    thigh_axis = thigh_axis / np.linalg.norm(thigh_axis)
    flex_axis = np.array([1.0, 0.0, 0.0])        # sagittal flexion

    truth = np.empty(n)
    rows = []
    for i in range(n):
        f = 0.0 if i < hold else flex_deg * (1.0 - math.exp(-(i - hold) / 25.0))
        # shank points knee -> ankle, i.e. opposite the thigh axis when extended
        shank_axis = _rot(flex_axis, sign * f) @ (-thigh_axis)
        truth[i] = math.degrees(
            math.acos(np.clip(np.dot(thigh_axis, shank_axis), -1.0, 1.0)))

        t_c = knee + thigh_axis * 0.18
        s_c = knee + shank_axis * 0.20
        T = _plate(t_c, thigh_axis, thigh_tilt)
        S = _plate(s_c, shank_axis, shank_tilt)

        occluded = drop_from is not None and drop_from <= i < drop_to
        rows.append((i, i / 120.0, S, T, occluded))
    return rows, truth


def _write_csv(path, rows, include_solved=True):
    """Write a Motive 1.22-style export with BOTH the solved rigid-body marker
    block and the measured marker block, as real exports contain."""
    type_row = ["", ""]
    name_row = ["", ""]
    comp_row = ["", ""]
    axis_row = ["Frame", "Time (Seconds)"]

    if include_solved:
        for seg in ("Shank", "Thigh"):
            for m in (1, 2, 3):
                for ax in ("X", "Y", "Z"):
                    type_row.append("Rigid Body Marker")
                    name_row.append('"%s:Marker%d"' % (seg, m))
                    comp_row.append("Position")
                    axis_row.append(ax)
    for seg in ("Shank", "Thigh"):
        for m in (1, 2, 3):
            for ax in ("X", "Y", "Z"):
                type_row.append("Marker")
                name_row.append("%s:Marker%d" % (seg, m))
                comp_row.append("Position")
                axis_row.append(ax)

    lines = [
        "Format Version,1.22,Take Name,synthetic,Capture Frame Rate,120.000000,"
        "Total Frames in Take,%d,Rotation Type,Quaternion,Length Units,Meters" % len(rows),
        "",
        ",".join(type_row),
        ",".join(name_row),
        ",".join(comp_row),
        ",".join(axis_row),
    ]
    for (fr, t, S, T, occ) in rows:
        cells = [str(fr), "%.6f" % t]
        blocks = [(S, T), (S, T)] if include_solved else [(S, T)]
        for (Sb, Tb) in blocks:
            for seg in (Sb, Tb):
                for m in range(3):
                    for ax in range(3):
                        cells.append("" if occ else "%.6f" % seg[m][ax])
        lines.append(",".join(cells))
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    return path


# ─────────────────────────────────────────────────────────────────────────────
# Defect 1: the loader must read MEASURED markers, not Motive's solved
#           rigid-body reprojection.
# ─────────────────────────────────────────────────────────────────────────────

def test_marker_cols_prefer_measured_block_over_solved_reprojection():
    """Each marker appears twice in a Motive export: once as a solved
    'Rigid Body Marker' (a reprojection of the rigid-body pose, which carries
    that body's tracking failures) and once as a measured 'Marker'. Taking
    cols[:3] silently picked the reprojection."""
    type_row = ["", ""] + ["Rigid Body Marker"] * 3 + ["Marker"] * 3
    name_row = ["", ""] + ['"Shank:Marker1"'] * 3 + ["Shank:Marker1"] * 3
    comp_row = ["", ""] + ["Position"] * 6

    cols = pts._find_labeled_marker_cols(name_row, comp_row, "Shank",
                                         type_row=type_row)
    assert cols == [[5, 6, 7]], (
        "expected the measured block at cols 5-7, got %r "
        "(cols 2-4 are the solved rigid-body reprojection)" % (cols,))


def test_marker_cols_fall_back_to_solved_when_no_measured_block():
    """Older exports carry only the rigid-body marker block; still usable."""
    type_row = ["", ""] + ["Rigid Body Marker"] * 3
    name_row = ["", ""] + ['"Shank:Marker1"'] * 3
    comp_row = ["", ""] + ["Position"] * 3
    assert pts._find_labeled_marker_cols(name_row, comp_row, "Shank",
                                         type_row=type_row) == [[2, 3, 4]]


def test_marker_cols_without_type_row_is_backward_compatible():
    """Callers that predate the type_row argument must keep working."""
    name_row = ["", ""] + ['"Shank:Marker1"'] * 3
    comp_row = ["", ""] + ["Position"] * 3
    assert pts._find_labeled_marker_cols(name_row, comp_row, "Shank") == [[2, 3, 4]]


# ─────────────────────────────────────────────────────────────────────────────
# Defect 2: the segment axis must be anatomically seeded, not cluster PC1.
# ─────────────────────────────────────────────────────────────────────────────

def test_angle_recovers_ground_truth_despite_tilted_marker_plates(tmp_path):
    """The plates sit 22 deg (thigh) and 30 deg (shank) off the limb axis --
    the geometry measured on real P21 data. The old PC1-as-long-axis
    assumption produced a ~20-26 deg baseline error from exactly this."""
    rows, truth = _build_trial()
    p = _write_csv(str(tmp_path / "t.csv"), rows)
    t, ang = pts.load_optitrack(p)

    assert len(ang) == len(truth)
    assert abs(np.median(ang[:50]) - 180.0) < 2.0, (
        "hold baseline should read ~180 deg, got %.1f" % np.median(ang[:50]))
    assert np.nanmax(np.abs(ang - truth)) < 3.0, (
        "max deviation from ground truth %.1f deg" % np.nanmax(np.abs(ang - truth)))


def test_flexion_decreases_the_angle_for_both_flexion_directions(tmp_path):
    """P21's right leg inverted because the shank plate's PC1 fell on the far
    side of the thigh axis, so flexion INCREASED the computed angle. The
    interior angle must decrease under flexion regardless of which way the
    shank swings relative to the plate geometry."""
    for sign in (-1.0, +1.0):
        rows, truth = _build_trial(sign=sign)
        p = _write_csv(str(tmp_path / ("t%s.csv" % sign)), rows)
        _t, ang = pts.load_optitrack(p)
        baseline = np.median(ang[:50])
        settled = np.median(ang[-30:])
        assert settled < baseline - 20.0, (
            "sign=%s: angle went %.1f -> %.1f; flexion must reduce the "
            "interior angle" % (sign, baseline, settled))
        assert np.nanmax(ang) <= 180.5, (
            "sign=%s: produced %.1f deg -- above anatomical full extension"
            % (sign, np.nanmax(ang)))


def test_no_curve_exceeds_180_degrees(tmp_path):
    """A knee cannot open past 180 deg. The P21 report plotted 202 deg."""
    rows, _ = _build_trial(sign=+1.0)
    p = _write_csv(str(tmp_path / "t.csv"), rows)
    _t, ang = pts.load_optitrack(p)
    assert np.nanmax(ang) <= 180.5


# ─────────────────────────────────────────────────────────────────────────────
# Defect 3: occlusion must not be silently filled in.
#
# 2026-08-27 policy change: a low-coverage trial is FLAGGED, not dropped. The
# operator decides which trials are bad (excluded_trials.json is the only
# exclusion mechanism); the loader's job is to hand over an honest curve and
# say what is wrong with it. "Honest" still means the gaps stay NaN — the
# original ffill/bfill fabrication must never come back.
# ─────────────────────────────────────────────────────────────────────────────

def test_occluded_swing_is_flagged_but_still_returned(tmp_path):
    """73% of the corpus loses marker tracking at release. The loader must
    return the curve anyway and warn, so the operator can judge it."""
    rows, _ = _build_trial(drop_from=70, drop_to=200)   # ~54% of frames blank
    p = _write_csv(str(tmp_path / "t.csv"), rows)
    t, ang, q = pts.load_optitrack_detailed(p)
    assert len(ang) == len(rows), "the trial must not be dropped"
    assert q.coverage < pts.LOW_OPTICAL_COVERAGE
    assert any("coverage" in w for w in q.warnings), q.warnings


def test_occluded_frames_stay_nan_and_are_never_fabricated(tmp_path):
    """The whole point of the 2026-08-26 work: dropped frames must remain NaN
    rather than being frozen forward across the gap."""
    rows, _ = _build_trial(drop_from=70, drop_to=200)
    p = _write_csv(str(tmp_path / "t.csv"), rows)
    _t, ang, _q = pts.load_optitrack_detailed(p)
    assert not np.isfinite(ang[80:190]).any(), (
        "occluded frames came back finite — the loader is fabricating motion")


def test_low_coverage_trial_does_not_raise_from_plain_load_optitrack(tmp_path):
    """The 2-tuple entry point keeps working and likewise must not reject."""
    rows, _ = _build_trial(drop_from=70, drop_to=200)
    p = _write_csv(str(tmp_path / "t.csv"), rows)
    _t, ang = pts.load_optitrack(p)              # must not raise
    assert len(ang) == len(rows)


def test_well_tracked_trial_carries_no_warnings(tmp_path):
    """A clean trial must come back with an empty warning list, so a warning
    means something."""
    rows, _ = _build_trial(drop_from=100, drop_to=105)   # ~2% of frames
    p = _write_csv(str(tmp_path / "t.csv"), rows)
    _t, ang, q = pts.load_optitrack_detailed(p)
    assert np.isfinite(ang).mean() > 0.9
    assert q.coverage > pts.LOW_OPTICAL_COVERAGE
    assert q.warnings == (), q.warnings


# ─────────────────────────────────────────────────────────────────────────────
# The plausibility guard: reports, never rejects.
# ─────────────────────────────────────────────────────────────────────────────

def _warn_text(ang):
    return " | ".join(pts._curve_quality_warnings(ang))


def test_guard_flags_angle_above_full_extension():
    ang = np.concatenate([np.full(60, 180.0), np.full(120, 202.0)])
    assert "above full extension" in _warn_text(ang)


def test_guard_flags_curve_that_opens_after_release():
    """P21 Right's original shape: baseline low, then rising past it. Fixed at
    source in 2026-08-26; the warning stays as a regression tripwire."""
    ang = np.concatenate([np.full(60, 154.0), np.full(120, 177.0)])
    assert "rises after release" in _warn_text(ang)


def test_guard_is_silent_on_a_normal_pendulum_curve():
    ang = np.concatenate([np.full(60, 180.0), np.full(120, 150.0)])
    assert pts._curve_quality_warnings(ang) == []


def test_guard_flags_a_curve_with_no_swing_at_all():
    """P2's duo takes built Shank and Thigh from overlapping markers, making
    the relative angle constant by construction."""
    assert "never varies" in _warn_text(np.full(180, 180.0))


def test_guard_keeps_a_small_but_real_swing_unflagged():
    """A rigid spastic limb barely swings — that is signal, not an error."""
    ang = np.concatenate([np.full(60, 180.0), np.full(120, 176.0)])
    assert pts._curve_quality_warnings(ang) == []


def test_guard_flags_all_nan_curve():
    assert "entirely NaN" in _warn_text(np.full(180, np.nan))


def test_guard_never_raises_on_any_of_these():
    """The guard is a reporter now. Nothing it sees may abort a load."""
    for ang in (np.full(180, np.nan),
                np.full(180, 180.0),
                np.concatenate([np.full(60, 154.0), np.full(120, 177.0)]),
                np.concatenate([np.full(60, 180.0), np.full(120, 202.0)]),
                np.array([]),
                np.array([np.nan, 180.0])):
        pts._curve_quality_warnings(ang)         # must not raise


def test_optical_coverage_reports_real_fraction(tmp_path):
    rows, _ = _build_trial(drop_from=60, drop_to=120)    # exactly 60 of 240
    p = _write_csv(str(tmp_path / "t.csv"), rows)
    cov = pts.optical_coverage(p)
    assert abs(cov - 0.75) < 0.02, "expected ~0.75 coverage, got %.3f" % cov


# ─────────────────────────────────────────────────────────────────────────────
# The legacy ffill paths must not launder their own gaps into "full coverage".
# ─────────────────────────────────────────────────────────────────────────────

def test_raw_column_coverage_measures_before_any_fill():
    """The quaternion and unlabeled-marker paths ffill because their maths has
    no NaN handling. Coverage must therefore be taken from the raw frame: after
    the fill every row looks tracked, and reporting that would tell the
    operator the exact opposite of the truth."""
    import pandas as pd
    df = pd.DataFrame({"a": [1.0, np.nan, 3.0, 4.0],
                       "b": [1.0, 2.0, 3.0, 4.0]})
    assert pts._raw_column_coverage(df, [0, 1]) == 0.75
    # What the filled frame would have claimed, had we measured it too late.
    assert pts._raw_column_coverage(df.ffill().bfill(), [0, 1]) == 1.0


def test_raw_column_coverage_treats_sentinels_as_untracked():
    """Motive writes out-of-range placeholders rather than blanks in some
    exports; those are absent data, not a position."""
    import pandas as pd
    df = pd.DataFrame({"a": [1.0, 1e9, 3.0, 4.0], "b": [1.0, 2.0, 3.0, 4.0]})
    assert pts._raw_column_coverage(df, [0, 1]) == 0.75


def test_raw_column_coverage_handles_empty_input():
    import pandas as pd
    assert pts._raw_column_coverage(pd.DataFrame(), [0]) == 0.0
    assert pts._raw_column_coverage(pd.DataFrame({"a": [1.0]}), []) == 0.0
