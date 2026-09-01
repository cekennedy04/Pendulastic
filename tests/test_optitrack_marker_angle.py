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


def _bar(centre, long_axis, tilt_deg, size=0.06):
    """3 markers that are nearly collinear, matching the real thigh cluster.

    The real thigh sits 1.5 mm out of line over a 92 mm span, so its roll is
    unobservable. `tilt_deg` offsets the bar from the limb axis the way a
    strapped cluster does (measured median 14.8 deg on 40 trials).
    """
    long_axis = np.asarray(long_axis, float)
    long_axis = long_axis / np.linalg.norm(long_axis)
    perp = np.cross(long_axis, [0.0, 0.0, 1.0])
    if np.linalg.norm(perp) < 1e-6:
        perp = np.cross(long_axis, [0.0, 1.0, 0.0])
    perp = perp / np.linalg.norm(perp)
    spin = np.cross(long_axis, perp)
    bar_dir = _rot(spin, tilt_deg) @ long_axis
    return np.array([
        centre + bar_dir * size,
        centre - bar_dir * size,
        centre + spin * 0.0012,        # 1.2 mm out of line: a bar, not a plate
    ])


def _build_trial(n=240, hold=60, flex_deg=40.0, thigh_tilt=22.0, shank_tilt=30.0,
                 drop_from=None, drop_to=None, sign=-1.0, start_state="held",
                 hold_drift_deg=0.0, out_of_plane_deg=0.0, swap_frame=None,
                 thigh_as_bar=True):
    """Ground-truth trial: thigh fixed, shank flexes by `flex_deg` after release.

    start_state:
      "held"       - the leg is extended and stationary through the hold. This
                     is the ONLY state the old generator could produce, which
                     is why the seed bug was invisible to the suite.
      "rest"       - the leg already hangs flexed before the recording starts,
                     as in P8 Left trial_2 where nobody is holding it.
      "mid_motion" - the recording starts partway through the swing, as in
                     P9 Left trial_3.

    hold_drift_deg drifts the hold linearly (patient shifting).
    out_of_plane_deg rotates the flexion axis out of the sagittal plane.
    swap_frame permutes marker indices on one frame (Motive re-solve).
    thigh_as_bar emits a near-collinear thigh, which is what 239/254 real
    trials actually have, and is therefore the DEFAULT. Pass False only to
    exercise the plate-plate rig deliberately -- it occurs in 0 of the 65 real
    trials sampled, and optitrack_knee_axis refuses it.
    """
    hip = np.array([0.0, 0.40, 1.50])
    knee = np.array([0.0, 0.00, 1.50])
    thigh_axis = hip - knee
    thigh_axis = thigh_axis / np.linalg.norm(thigh_axis)
    flex_axis = _rot(np.array([0.0, 1.0, 0.0]), out_of_plane_deg) @ np.array([1.0, 0.0, 0.0])

    start_offset = {"held": 0.0, "rest": flex_deg, "mid_motion": flex_deg * 0.45}[start_state]

    truth = np.empty(n)
    rows = []
    for i in range(n):
        if start_state == "held":
            f = 0.0 if i < hold else flex_deg * (1.0 - math.exp(-(i - hold) / 25.0))
        elif start_state == "rest":
            f = start_offset                      # never moves
        else:
            f = start_offset + (flex_deg - start_offset) * (1.0 - math.exp(-i / 25.0))
        if i < hold:
            f += hold_drift_deg * (i / max(1, hold))

        shank_axis = _rot(flex_axis, sign * f) @ (-thigh_axis)
        truth[i] = math.degrees(
            math.acos(np.clip(np.dot(thigh_axis, shank_axis), -1.0, 1.0)))

        t_c = knee + thigh_axis * 0.18
        s_c = knee + shank_axis * 0.20
        T = (_bar(t_c, thigh_axis, thigh_tilt) if thigh_as_bar
             else _plate(t_c, thigh_axis, thigh_tilt))
        S = _plate(s_c, shank_axis, shank_tilt)
        if swap_frame is not None and i == swap_frame:
            T = T[[1, 0, 2]]                       # Motive permutes Marker1/2/3

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
    # The baseline is NOT asserted to be 180: this curve is relative, because
    # no absolute zero can be earned from an arbitrary hinge sign. The tilted
    # plates must not distort the SHAPE, which is what the old PC1-as-long-axis
    # assumption got wrong -- it put a 20-26 deg error into the curve itself,
    # not merely into its zero.
    assert _shape_err(ang, truth) < 3.0, (
        "max shape deviation from ground truth %.1f deg" % _shape_err(ang, truth))


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


def test_the_curve_polarity_survives_a_micron_of_rounding(tmp_path):
    """The specific instability the sign pin exists to remove.

    The hinge axis is an eigenvector, and `eigh`'s sign is arbitrary: before
    the pin, the SAME trial reconstructed as 0 -> -40 deg in memory and
    0 -> +40 deg after a CSV round trip, which perturbs the coordinates by
    5e-7 m. Sign is observable, so it must not depend on rounding."""
    import optitrack_knee_axis as ka
    for sign in (-1.0, +1.0):
        rows, _truth = _build_trial(n=400, hold=60, flex_deg=40.0, sign=sign)
        shank = np.stack([r[2] for r in rows], axis=1)
        thigh = np.stack([r[3] for r in rows], axis=1)
        direct = ka.knee_angle_from_clusters(shank, thigh, 120.0).get_relative_angles()

        p = _write_csv(str(tmp_path / ("rt%s.csv" % sign)), rows)
        _t, viacsv = pts.load_optitrack(p)

        # the round trip really does perturb the data, or this proves nothing
        raw = np.stack([r[2] for r in rows], axis=1)
        df, nr, cr, tr, _new = pts._parse_optitrack_header(p)
        cols = pts._find_labeled_marker_cols(nr, cr or [], "Shank", type_row=tr)
        got = np.stack([df.iloc[:, c].values.astype(float) for c in cols[:3]])
        delta = float(np.nanmax(np.abs(got - raw)))
        assert 0.0 < delta < 1e-5, f"expected a sub-micron perturbation, got {delta}"

        assert np.sign(direct[-1] - direct[0]) == np.sign(viacsv[-1] - viacsv[0]), (
            "sign=%s: polarity flipped on %.1e m of rounding (%.2f vs %.2f)"
            % (sign, delta, direct[-1] - direct[0], viacsv[-1] - viacsv[0]))


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
    # Every optical trial now carries the uncalibrated notice, by design: the
    # zero is never invented. That one is expected; anything else is not.
    other = [w for w in q.warnings if "RELATIVE, not absolute" not in w]
    assert other == [], other


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


# ─────────────────────────────────────────────────────────────────────────────
# Motive can export the rigid-body marker block in the body's LOCAL frame.
# Those are constant offsets, not measurements. The world positions are still
# in the file under the unlabeled block.
# ─────────────────────────────────────────────────────────────────────────────

def test_static_labeled_markers_are_recognised_as_local_coordinates():
    import pandas as pd
    # Three markers that never move: a constant offset from a body origin.
    df = pd.DataFrame({i: [v] * 50 for i, v in enumerate(
        [-0.049, -0.007, 0.023, 0.051, 0.009, -0.024, -0.001, -0.002, 0.001])})
    trips = [[0, 1, 2], [3, 4, 5], [6, 7, 8]]
    assert pts._labeled_markers_are_local(df, trips)


def test_real_moving_markers_are_not_mistaken_for_local_coordinates():
    import pandas as pd
    n = 50
    swing = np.linspace(0.0, 0.30, n)          # 30 cm of travel
    cols = {}
    for m in range(3):
        cols[m*3+0] = 0.4 + swing
        cols[m*3+1] = 0.4 + swing * 0.5
        cols[m*3+2] = 2.0 - swing
    df = pd.DataFrame(cols)
    trips = [[0, 1, 2], [3, 4, 5], [6, 7, 8]]
    assert not pts._labeled_markers_are_local(df, trips)


def test_unlabeled_markers_split_into_a_still_thigh_and_a_swinging_shank():
    """In a pendulum test the thigh is held and the shank swings, so total
    excursion separates them by an order of magnitude -- 8-28 mm against
    137-311 mm on P4 right."""
    import pandas as pd
    n = 60
    still = np.linspace(0.0, 0.01, n)          # 1 cm
    moving = np.linspace(0.0, 0.30, n)         # 30 cm
    cols, trips, c = {}, [], 0
    for series in (still, still, still, moving, moving, moving):
        idx = []
        for axis in range(3):
            cols[c] = 0.4 + series * (1 + axis * 0.1)
            idx.append(c); c += 1
        trips.append(idx)
    df = pd.DataFrame(cols)
    shank, thigh = pts._split_unlabeled_by_motion(df, trips)
    assert shank is not None and thigh is not None
    assert sorted(t[0] for t in thigh) == [0, 3, 6]      # the still trio
    assert sorted(s[0] for s in shank) == [9, 12, 15]    # the swinging trio


def test_split_refuses_when_the_two_clusters_are_not_separable():
    """Six markers all moving alike carry no thigh/shank distinction. Refuse
    rather than invent a segmentation."""
    import pandas as pd
    n = 60
    same = np.linspace(0.0, 0.20, n)
    cols, trips, c = {}, [], 0
    for _ in range(6):
        idx = []
        for _axis in range(3):
            cols[c] = 0.4 + same
            idx.append(c); c += 1
        trips.append(idx)
    df = pd.DataFrame(cols)
    assert pts._split_unlabeled_by_motion(df, trips) == (None, None)


def test_split_refuses_with_fewer_than_six_markers():
    import pandas as pd
    n = 20
    cols, trips, c = {}, [], 0
    for _ in range(5):
        idx = []
        for _axis in range(3):
            cols[c] = np.linspace(0, 0.1, n); idx.append(c); c += 1
        trips.append(idx)
    assert pts._split_unlabeled_by_motion(pd.DataFrame(cols), trips) == (None, None)


# ─────────────────────────────────────────────────────────────────────────────
# Trials that do NOT start at an extended hold.
#
# _build_trial above always opens with the leg held straight, which is the one
# case the seed assumption gets right. Real recordings are often started before
# the examiner has lifted the leg, or after the swing has already begun, and
# those are the shapes that broke the reconstruction in the field:
#   * P8 Left trial_2  -- video frame 5 and frame 370 both show the leg hanging
#     flexed with nobody holding it; the recording brackets the whole procedure.
#   * P9 Left trial_3  -- opens mid-motion, seed window moving at 2.0 mm/frame.
# ─────────────────────────────────────────────────────────────────────────────

def _build_protocol(rest_flex=50.0, lift_frames=90, hold_frames=90,
                    swing_frames=300, lead_in=120, start_at=0,
                    thigh_tilt=22.0, shank_tilt=30.0, fps=120.0,
                    thigh_as_bar=True):
    """A whole pendulum procedure: rest -> lift -> hold -> release -> rest.

    `lead_in` frames of the leg sitting at rest BEFORE the examiner lifts it,
    then the lift, the held extension, the damped swing, and rest again.
    `start_at` drops the first N frames, so the recording can be made to begin
    mid-lift or mid-swing. Returns (rows, truth) with truth the true interior
    knee angle, 180 = straight.
    """
    hip = np.array([0.0, 0.40, 1.50])
    knee = np.array([0.0, 0.00, 1.50])
    thigh_axis = (hip - knee) / np.linalg.norm(hip - knee)
    flex_axis = np.array([1.0, 0.0, 0.0])

    flex = []
    flex += [rest_flex] * lead_in                                  # sitting at rest
    for i in range(lift_frames):                                   # examiner lifts
        flex.append(rest_flex * (1.0 - (i + 1) / lift_frames))
    flex += [0.0] * hold_frames                                    # held extended
    for i in range(swing_frames):                                  # released, swings
        t = i / fps
        flex.append(rest_flex * (1.0 - math.exp(-2.2 * t) * math.cos(2 * math.pi * 0.85 * t)))
    flex += [rest_flex] * lead_in                                  # settles at rest

    # Build each plate ONCE at the reference pose, then carry it rigidly. The
    # earlier helper rebuilt the plate from world-z every frame, which lets it
    # twist about its own long axis as the segment swings -- a marker plate
    # screwed to a limb does not do that, and the twist put a 7 mm bias into
    # any joint-centre estimate fitted to the trajectories.
    ref_shank = -thigh_axis
    S_ref = _plate(knee + ref_shank * 0.20, ref_shank, shank_tilt) - (knee + ref_shank * 0.20)
    # The thigh is a near-collinear BAR by default, because that is the rig:
    # 239/254 real trials have one, and a plate-plate pair occurs in 0 of the
    # 65 real trials sampled -- a plate thigh models a rig that does not exist.
    _thigh_cluster = _bar if thigh_as_bar else _plate
    T_ref = (_thigh_cluster(knee + thigh_axis * 0.18, thigh_axis, thigh_tilt)
             - (knee + thigh_axis * 0.18))

    rows, truth = [], []
    for i, f in enumerate(flex[start_at:]):
        R = _rot(flex_axis, -f)
        shank_axis = R @ ref_shank
        truth.append(math.degrees(math.acos(np.clip(np.dot(thigh_axis, shank_axis), -1.0, 1.0))))
        T = T_ref + (knee + thigh_axis * 0.18)          # thigh is stationary
        S = (R @ S_ref.T).T + (knee + shank_axis * 0.20)
        rows.append((i, i / fps, S, T, False))
    return rows, np.asarray(truth)


def _shape_err(ang, truth):
    """Max deviation from ground truth after quotienting out offset and sign.

    Since the 2026-09-01 ruling the optical curve is RELATIVE, and its polarity
    comes from an eigenvector sign, so neither its zero nor its sign carries
    information (see optitrack_knee_axis.anchor_to_extension). What must still
    be exact is the SHAPE. This is not a weaker test of the defect these cases
    were written for: the old seeded reconstruction FOLDED the curve at 180
    (unsigned arccos), and a fold is a shape error that no offset and no sign
    flip can absorb -- see test_a_folded_curve_is_still_caught_by_shape_error.
    """
    ok = np.isfinite(ang) & np.isfinite(truth)
    a, b = ang[ok], truth[ok]
    return min(float(np.max(np.abs((sgn * a - np.mean(sgn * a))
                                   - (b - np.mean(b)))))
               for sgn in (+1.0, -1.0))


def _err(path, truth, monkeypatch=None):
    """Shape error against ground truth.

    `monkeypatch` is accepted and ignored. It used to force the joint-centre
    path on via USE_FUNCTIONAL_KNEE_CENTRE, but that flag was only ever read by
    _angle_from_labeled_markers, which was deleted on 2026-09-01; setting it
    now would be a no-op dressed up as configuration.
    """
    t, ang = pts.load_optitrack(path)
    return _shape_err(ang, truth), float(np.nanmedian(ang[:60]))


def test_a_folded_curve_is_still_caught_by_shape_error():
    """_shape_err quotients out offset and sign, so it must be shown to still
    catch the defect the absolute check used to catch. It does, because the old
    bug was never a pure offset: it anchored the seed pose to 180 and then an
    UNSIGNED arccos folded everything past 180 back down. A fold is a shape
    change, and no offset or mirror can undo it."""
    _rows, truth = _build_protocol()          # starts at rest, lifts through extension
    # What the old reconstruction produced: seed pose forced to 180, then folded.
    x = truth - truth[0] + 180.0
    folded = 180.0 - np.abs(x - 180.0)
    assert _shape_err(folded, truth) > 20.0, (
        "a folded curve must be caught: got %.2f deg" % _shape_err(folded, truth))
    # ...while the two things the ruling says carry no information are absorbed.
    assert _shape_err(truth - 37.0, truth) < 1e-9, "a constant offset must not count"
    assert _shape_err(-truth, truth) < 1e-9, "a mirror must not count"


def test_trial_that_starts_at_rest_is_not_zeroed_on_the_resting_pose(tmp_path, monkeypatch):
    """The failure confirmed on video for P8 Left trial_2. The recording opens
    with the leg hanging flexed; seeding on frames 0-59 makes that pose 180 deg
    by construction, so the baseline reads a convincing 179.9 while every angle
    after it is wrong."""
    rows, truth = _build_protocol()
    p = _write_csv(str(tmp_path / "rest_start.csv"), rows)
    max_err, base = _err(p, truth, monkeypatch)
    assert max_err < 2.0, (
        f"max error {max_err:.1f} deg -- the zero is anchored to the wrong pose "
        f"(reported baseline {base:.1f}, true opening angle {truth[0]:.1f})")


def test_trial_that_starts_mid_swing_is_not_zeroed_on_a_moving_pose(tmp_path, monkeypatch):
    """P9 Left trial_3's shape: the recording begins after release, so the seed
    window is not calm and not extended. It reported A0 = 418.1 deg."""
    rows, truth = _build_protocol(start_at=340)
    p = _write_csv(str(tmp_path / "mid_swing.csv"), rows)
    # This recording never contains the held extension, so there is no pose of
    # known angle to anchor the zero on. The honest outcome is a refusal --
    # anchoring on the largest angle present would invent 46 deg of extension
    # the leg never reached.
    t, ang, q = pts.load_optitrack_detailed(p)
    assert len(ang) == len(rows), "the trial must still be returned, not dropped"
    # No held extension is in frame, so the zero cannot be recovered. The shape
    # is still right: the swing's peak-to-peak excursion survives even though
    # its absolute position does not.
    fin = ang[np.isfinite(ang)]
    assert (fin.max() - fin.min()) == pytest.approx(truth.max() - truth.min(), abs=3.0)


def test_the_ordinary_hold_first_trial_still_works(tmp_path, monkeypatch):
    """The case that already worked must not regress: recording starts with the
    leg already held extended, which is what the current seed assumes."""
    rows, truth = _build_protocol(lead_in=0, lift_frames=0)
    p = _write_csv(str(tmp_path / "hold_first.csv"), rows)
    max_err, _base = _err(p, truth, monkeypatch)
    assert max_err < 2.0, f"regression on the easy case: {max_err:.1f} deg"


# ─────────────────────────────────────────────────────────────────────────────
# The seed window has to actually BE a hold. Nothing checked that before.
# ─────────────────────────────────────────────────────────────────────────────

def test_a_moving_seed_window_is_flagged(tmp_path):
    """P9 Left trial_3's shape: the recording opens mid-swing, so the pose the
    zero is taken from is moving. It reported A0 = 418 deg at 97.3% coverage,
    where neither the coverage gate nor the plausibility checks saw anything."""
    rows, _truth = _build_protocol(start_at=340)
    p = _write_csv(str(tmp_path / "moving_seed.csv"), rows)
    _t, _ang, q = pts.load_optitrack_detailed(p)
    assert any("reference window is not a hold" in w for w in q.warnings), q.warnings


def test_a_genuine_hold_is_not_flagged(tmp_path):
    rows, _truth = _build_protocol(lead_in=0, lift_frames=0)
    p = _write_csv(str(tmp_path / "real_hold.csv"), rows)
    _t, _ang, q = pts.load_optitrack_detailed(p)
    assert not any("reference window is not a hold" in w for w in q.warnings), q.warnings


def test_a_trial_that_opens_at_rest_is_still_stationary_and_so_not_flagged(tmp_path):
    """Documents the limit of this detector rather than overselling it. A
    recording that opens with the leg resting IS still, so a stillness check
    cannot see that the pose is flexed rather than extended. Under the pose-free
    reconstruction that no longer matters: no zero is taken from any pose."""
    rows, _truth = _build_protocol()
    p = _write_csv(str(tmp_path / "rest_open.csv"), rows)
    _t, _ang, q = pts.load_optitrack_detailed(p)
    assert not any("reference window is not a hold" in w for w in q.warnings)


def test_seed_window_speed_is_measured_without_any_reference_pose(tmp_path):
    """It must not depend on the seed it is checking."""
    import pandas as pd
    n = 80
    moving = np.linspace(0.0, 0.20, n)          # 200 mm over 80 frames
    cols = {}
    for m in range(3):
        for axis in range(3):
            cols[m * 3 + axis] = 0.4 + moving
    df = pd.DataFrame(cols)
    trips = [[0, 1, 2], [3, 4, 5], [6, 7, 8]]
    speed = pts._seed_window_speed_mm(df, trips, n_frames=60)
    # 200 mm / 79 steps, projected on three equal axes
    assert speed == pytest.approx(200.0 / (n - 1) * math.sqrt(3), rel=0.05)


def test_seed_window_speed_is_nan_without_markers():
    import pandas as pd
    assert not np.isfinite(pts._seed_window_speed_mm(pd.DataFrame(), []))


# ─────────────────────────────────────────────────────────────────────────────
# Synthetic generator: near-collinear bar clusters and non-held trial states.
# ─────────────────────────────────────────────────────────────────────────────

def test_bar_cluster_is_near_collinear_like_the_real_thigh():
    """Real thigh clusters are 3 markers 1.5 mm out of line over a 92 mm span.
    A bar built here must land in that regime, or every geometry test is
    exercising a triangle and proving nothing."""
    import numpy as np
    from pendulastic_pt_score import MIN_CLUSTER_PLANAR_EXTENT_M
    pts = _bar(np.array([0.0, 0.0, 1.0]), np.array([0.0, 1.0, 0.0]), 15.0)
    centred = pts - pts.mean(axis=0)
    sv = np.linalg.svd(centred, compute_uv=False)
    assert sv[1] < MIN_CLUSTER_PLANAR_EXTENT_M, sv
    assert sv[0] > 0.03, "bar must still have a real span"


def test_generator_can_start_at_rest_and_mid_motion():
    """The two states that break the seed. `held` is the old behaviour."""
    rows_h, truth_h = _build_trial(start_state="held")
    rows_r, truth_r = _build_trial(start_state="rest")
    rows_m, truth_m = _build_trial(start_state="mid_motion")
    assert truth_h[0] == pytest.approx(180.0, abs=0.5)
    assert truth_r[0] < 150.0, "a resting leg is not extended"
    assert 150.0 < truth_m[0] < 179.0, "mid-motion starts partway through"


def test_generator_can_emit_out_of_plane_swing_and_a_marker_swap():
    """Both fixtures must be verified by their EFFECT, not their shape.

    An out-of-plane swing has to actually leave the sagittal plane, and a
    marker swap has to actually reorder markers at exactly one frame.
    """
    # Out of plane: the shank centroid gains an x-component it does not have
    # in a purely sagittal swing.
    rows_flat, _ = _build_trial(out_of_plane_deg=0.0)
    rows_oop, _ = _build_trial(out_of_plane_deg=18.0)
    x_flat = max(abs(r[2][:, 0].mean()) for r in rows_flat[120:])
    x_oop = max(abs(r[2][:, 0].mean()) for r in rows_oop[120:])
    assert x_flat < 1e-6, f"a sagittal swing should stay at x=0, got {x_flat}"
    assert x_oop > 0.01, f"18 deg out of plane should move x, got {x_oop}"

    # Marker swap: the SAME three points in a different order, at exactly the
    # one frame, and nowhere else.
    rows_plain, _ = _build_trial()
    rows_swap, _ = _build_trial(swap_frame=140)
    assert np.allclose(rows_swap[140][3], rows_plain[140][3][[1, 0, 2]]), \
        "swap_frame did not reorder the thigh markers"
    assert not np.allclose(rows_swap[140][3], rows_plain[140][3]), \
        "assertion would pass even with no swap"
    assert np.allclose(rows_swap[139][3], rows_plain[139][3]), \
        "the swap leaked into a neighbouring frame"


# ─────────────────────────────────────────────────────────────────────────────
# Loader integration: the pose-free reconstruction reaches the call site, and
# what it knows about the trial reaches the operator.
# ─────────────────────────────────────────────────────────────────────────────

def test_loader_reports_knee_axis_flags_in_trial_quality(tmp_path):
    """The loader's tuple contract does not change; the flags ride in
    TrialQuality, which is where every other quality signal already lives.

    This trial never passes through extension, so no absolute zero can be
    earned and the curve comes back relative -- which the operator is told."""
    import pendulastic_pt_score as pt
    rows, _truth = _build_trial(n=400, hold=0, flex_deg=40.0,
                                start_state="mid_motion")
    path = tmp_path / "trial_mid_motion_optitrack.csv"
    _write_csv(str(path), rows)
    _t, _ang, quality = pt.load_optitrack_detailed(str(path))
    joined = " ".join(quality.warnings).lower()
    assert "relative, not absolute" in joined, quality.warnings


def test_a_leg_that_never_moves_is_refused_not_reconstructed(tmp_path):
    """P8 Left trial_2's shape taken to its limit: the leg hangs flexed and
    nobody ever moves it.

    A hinge axis is recovered FROM the rotation, so a recording with no
    rotation contains no axis -- there is nothing to measure the angle about.
    The old code answered anyway, seeding on the first 60 frames and reporting
    a confident 179.9 for a leg that was flexed the whole time. Refusing with a
    named reason is the honest answer, and the operator's fix is capture-side."""
    import pendulastic_pt_score as pt
    rows, _truth = _build_trial(n=400, hold=0, flex_deg=40.0, start_state="rest")
    path = tmp_path / "trial_rest_optitrack.csv"
    _write_csv(str(path), rows)
    with pytest.raises(ValueError) as exc:
        pt.load_optitrack_detailed(str(path))
    assert "no rotation" in str(exc.value).lower(), str(exc.value)
    # and the refusal must be the module's, not an incidental crash
    import optitrack_knee_axis as ka
    shank = np.stack([r[2] for r in rows], axis=1)
    thigh = np.stack([r[3] for r in rows], axis=1)
    with pytest.raises(ka.GeometryError):
        ka.knee_angle_from_clusters(shank, thigh, fps=120.0)


def test_the_loader_never_hands_out_an_absolute_optical_angle(tmp_path):
    """The seam the 2026-09-01 ruling closed.

    knee_angle_from_clusters can no longer report is_calibrated, because the
    hinge axis's sign comes from an eigenvector and is therefore arbitrary: a
    5e-7 m rounding difference flipped one synthetic trial from
    (uncalibrated, 0 -> -40) to (calibrated, 180 -> +220) against a truth of
    180 -> 140. An absolute zero taken from that is the 179.9-on-a-flexed-leg
    bug wearing a different hat."""
    import optitrack_knee_axis as ka
    rows, _truth = _build_trial(n=400, hold=80, flex_deg=45.0)
    shank = np.stack([r[2] for r in rows], axis=1)
    thigh = np.stack([r[3] for r in rows], axis=1)
    res = ka.knee_angle_from_clusters(shank, thigh, fps=120.0)
    assert res.is_calibrated is False
    assert res.offset_deg is None
    assert "uncalibrated_offset" in res.flags, res.flags
    with pytest.raises(ka.UncalibratedOffsetError):
        res.get_absolute_angles()

    # and the loader's curve is that relative curve, not an anchored one
    p = _write_csv(str(tmp_path / "abs.csv"), rows)
    _t, ang, q = pts.load_optitrack_detailed(p)
    assert np.nanmedian(ang[:60]) == pytest.approx(0.0, abs=1e-6), (
        "the loader anchored the curve; it must stay relative")
    assert any("RELATIVE, not absolute" in w for w in q.warnings), q.warnings


def test_an_accumulating_hinge_axis_is_flagged_in_both_conventions():
    """A curve spanning more degrees than the knee can travel is a mis-derived
    hinge axis that accumulated instead of oscillating, and nothing downstream
    catches it: compute_pt_params happily returns an A0 of several hundred
    degrees, compute_pt_score scores it, and pt_to_mas grades it.

    Measured on the OptiTrack corpus (2026-09-01) before this check existed:
    sampled curves spanned 362 to 2724 deg with an empty warning list, and only
    8% of scored trials carried an A0 inside the 25-120 deg interpretable band.

    The check must be convention-free -- a span is invariant to the arbitrary
    zero and arbitrary polarity of a relative curve, which is the form the
    knee-axis reconstruction returns and therefore the form that needs it."""
    t = np.linspace(0.0, 12.0, 1440)
    # a swing that never reverses: the angle winds on and on
    accumulating = -220.0 * t + 40.0 * np.sin(2 * np.pi * 0.9 * t)
    for relative in (True, False):
        warns = pts._curve_quality_warnings(accumulating, relative=relative)
        assert any("more than the joint can travel" in w for w in warns), (relative, warns)

    # a real pendulum swing is NOT flagged, in either convention
    genuine = 180.0 - 90.0 * (1.0 - np.cos(2 * np.pi * 0.9 * t)) * np.exp(-t / 4.0)
    assert genuine.max() - genuine.min() < pts.MAX_PLAUSIBLE_CURVE_SPAN_DEG
    for relative in (True, False):
        warns = pts._curve_quality_warnings(genuine, relative=relative)
        assert not any("more than the joint can travel" in w for w in warns), (relative, warns)

    # flagged, never dropped -- the curve still comes back intact
    assert np.isfinite(accumulating).all()


def test_an_inverted_relative_curve_still_trips_the_p21_tripwire():
    """The check that must NOT be convention-suppressed.

    "rises after release" compares the post-release median against the
    pre-release median of the SAME curve, so it is offset-invariant by
    construction and depends only on polarity. It was briefly skipped in
    relative mode, which retired the P21 tripwire on the optical path -- the
    one path P21's inversion actually happened on. It is back on."""
    n = 600
    t = np.linspace(0.0, 5.0, n)
    swing = 40.0 * (1.0 - np.exp(-2.0 * np.maximum(t - 0.5, 0.0)))
    correct = -swing                    # relative curve: falls after release
    inverted = +swing                   # the P21 signature, zero still at 0

    for relative in (True, False):
        assert any("inverted" in w for w in
                   pts._curve_quality_warnings(inverted, relative=relative)), relative
        assert not any("inverted" in w for w in
                       pts._curve_quality_warnings(correct, relative=relative)), relative

    # and it really is offset-invariant, so re-enabling it cannot fire on a
    # relative curve merely for having an arbitrary zero
    for off in (-137.0, 0.0, 88.0):
        assert not any("inverted" in w for w in
                       pts._curve_quality_warnings(correct + off, relative=True)), off


def test_the_sign_pin_guard_can_actually_fire():
    """A safeguard whose threshold sits where it can never be reached is not a
    safeguard. The previous absolute cut (1e-9 on a rad*deg*frames quantity of
    order tens to thousands) could not fire, so the documented "leave the sign
    rather than flip on noise" behaviour was dead code -- the same defect this
    project already removed once in _merge_close_extrema.

    Measured: clean swings agree 0.98-1.00, pure marker noise agrees 0.08-0.12,
    and 12 of a 64-trial real sample fall below the 0.20 cut."""
    import optitrack_knee_axis as ka

    def agreement(tri, bar):
        rv = ka._rotation_increments(tri)
        w, V = np.linalg.eigh(rv.T @ rv)
        axis = V[:, np.argsort(w)[::-1]][:, 0]
        idx = np.where(np.isfinite(tri).all(axis=(0, 2)))[0]
        d = np.diff(ka._proxy_extension_angle(tri, bar, idx))
        turn = rv @ axis
        g = np.isfinite(d) & np.isfinite(turn)
        wt = turn[g] * d[g]
        return abs(float(np.sum(wt))) / float(np.sum(np.abs(wt)))

    # A clean swing is decisive and must NOT reach the guard.
    rows, _truth = _build_trial(n=400, hold=60, flex_deg=40.0)
    tri, bar, _w = ka.classify_clusters(np.stack([r[2] for r in rows], axis=1),
                                        np.stack([r[3] for r in rows], axis=1))
    clean = agreement(tri, bar)
    assert clean > ka.MIN_SIGN_PIN_AGREEMENT, clean
    assert clean > 0.9, f"a clean swing should be near-unanimous, got {clean:.3f}"

    # Marker noise with no real hinge carries no flexion information and MUST
    # reach the guard, rather than flipping the sign on a coin toss.
    rng = np.random.default_rng(3)
    tri_n = (rng.normal(scale=0.002, size=(3, 400, 3))
             + np.array([[0.06, 0, 0], [-0.06, 0, 0], [0, 0.021, 0]])[:, None, :])
    bar_n = (rng.normal(scale=0.002, size=(3, 400, 3))
             + np.array([[0.046, 0, 0], [-0.046, 0, 0], [0, 0.0012, 0]])[:, None, :])
    noisy = agreement(tri_n, bar_n)
    assert noisy < ka.MIN_SIGN_PIN_AGREEMENT, (
        f"noise agreed {noisy:.3f}, at or above the {ka.MIN_SIGN_PIN_AGREEMENT} "
        "cut -- the guard cannot distinguish evidence from a coin toss")

    # and the guard, reached, leaves the axis exactly as it was
    rv = ka._rotation_increments(tri_n)
    w, V = np.linalg.eigh(rv.T @ rv)
    axis = V[:, np.argsort(w)[::-1]][:, 0]
    idx = np.where(np.isfinite(tri_n).all(axis=(0, 2)))[0]
    kept = ka._pin_axis_sign(axis, rv, tri_n, bar_n, idx)
    assert np.allclose(kept, axis), "the guard fired but the axis still moved"


def test_relative_mode_suppresses_only_the_check_that_needs_the_zero(tmp_path):
    """Exactly one of the two 180-convention checks may be suppressed.

    "above full extension" reads an ABSOLUTE value, so it needs the zero and is
    meaningless on a relative curve -- it fired on 18/26 real trials.
    "rises after release" compares two medians of the SAME curve, so it needs
    only the POLARITY, which the hinge sign pin now fixes. Suppressing that one
    too, as this code briefly did, retired the P21 tripwire on the optical
    path -- the one path P21's inversion actually happened on."""
    # 190, not 205: still above the 180.5 full-extension guard this test is
    # about, but inside MAX_PLAUSIBLE_CURVE_SPAN_DEG so the span check (a
    # separate, convention-free guard) stays out of this assertion.
    ang = np.concatenate([np.zeros(60), np.linspace(0.0, 190.0, 180)])
    absolute = pts._curve_quality_warnings(ang, relative=False)
    relative = pts._curve_quality_warnings(ang, relative=True)
    assert any("above full extension" in w for w in absolute), absolute
    assert not any("above full extension" in w for w in relative), relative
    # the P21 tripwire fires in BOTH modes
    assert any("inverted" in w for w in absolute), absolute
    assert any("inverted" in w for w in relative), relative
    # the convention-free checks survive in BOTH modes
    flat = np.zeros(240)
    assert any("never varies" in w for w in pts._curve_quality_warnings(flat, relative=True))
    assert any("entirely NaN" in w
               for w in pts._curve_quality_warnings(np.full(240, np.nan), relative=True))
