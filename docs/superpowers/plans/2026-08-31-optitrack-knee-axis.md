# Pose-Free Knee Angle Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the assumed-pose seed in `_angle_from_labeled_markers` with a reconstruction that measures what the rig observes and refuses what it cannot.

**Architecture:** A new module `optitrack_knee_axis.py` derives the thigh direction directly from the bar cluster's line (no pose assumption), estimates the knee hinge axis functionally from the triangle cluster's rotation increments, and produces a *signed* angle about that hinge so it cannot fold at 180°. Every scored PT parameter is offset-invariant, so the unknown constant offset does not block scoring; `180° = extended` becomes a cosmetic offset applied only when a verified hold exists.

**Tech Stack:** Python 3.13, numpy, scipy (`scipy.signal.welch`), pandas, pytest. Run everything with `.venv/Scripts/python.exe`.

**Spec:** `docs/superpowers/specs/2026-08-31-optitrack-knee-axis-design.md`

## Global Constants

Copy these verbatim into `optitrack_knee_axis.py`. Values and their justification come from the spec.

```python
MIN_HINGE_CONDITIONING = 0.90          # PROVISIONAL: splits observed set 21/9
LOW_FREQ_CUTOFF_HZ = 6.0               # PC2 is differenced, energy shifts up
OUT_OF_PLANE_MIN_LF_RATIO = 0.50       # PROVISIONAL: rests on 2 positives
MIN_SPECTRAL_FRAMES = 240              # 2 s at 120 Hz; leakage floor
MAX_HOLD_SPEED_MM_PER_FRAME = 0.5      # P9 mid-motion start measured 2.0
MAX_HOLD_COLLINEARITY_DEG = 25.0       # clear of the 14.8 deg bar offset
MAX_HOLD_SD_DEG = 2.0                  # drifting hold -> withhold offset
```

Reuse from `pendulastic_pt_score`, do not re-implement: `MIN_CLUSTER_PLANAR_EXTENT_M`, `_reference_shape`, `_kabsch_rotations`, `_kabsch_rotation`, `_shortest_arc_rotation`, `MAX_CLUSTER_RMSD_M`, `_MARKER_PERMUTATIONS`.

## Global Constraints

- **Another agent is editing this repo concurrently.** Never `git add -A`. Never plain `git commit`. Commit only with pathspecs: `git commit -F - -- <paths>`. Before each commit run `git diff --cached --name-only` and confirm it lists nothing you did not write.
- Marker arrays are `(3, n, 3)`: marker index, frame, xyz. Positions are **metres**. Values with `abs > 1e5` are Motive's "untracked" sentinel and become NaN.
- Sample rate is 120 Hz across all 254 trials, but read it from column 1 rather than hardcoding.
- Never fill a gap. An untracked frame is NaN; it is never interpolated.
- Every refusal names its reason in prose an operator can act on.

---

### Task 1: Synthetic generator for the failure modes the suite cannot reach

`_build_trial` always starts with a held, extended leg, which is exactly why this bug survived. Everything downstream needs these fixtures, so they come first.

**Files:**
- Modify: `tests/test_optitrack_marker_angle.py:31-81` (add `_bar`, extend `_build_trial`)

**Interfaces:**
- Produces: `_bar(centre, long_axis, tilt_deg, size=0.06)` returning `(3,3)` near-collinear markers; `_build_trial(..., start_state="held", hold_drift_deg=0.0, out_of_plane_deg=0.0, swap_frame=None, thigh_as_bar=False)` returning `(rows, truth)`.

- [ ] **Step 1: Write the failing test**

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_optitrack_marker_angle.py::test_bar_cluster_is_near_collinear_like_the_real_thigh -v`
Expected: FAIL, `NameError: name '_bar' is not defined`

- [ ] **Step 3: Write minimal implementation**

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: same command as Step 2. Expected: PASS

- [ ] **Step 5: Write the failing tests for the new trial states**

```python
def test_generator_can_start_at_rest_and_mid_motion():
    """The two states that break the seed. `held` is the old behaviour."""
    rows_h, truth_h = _build_trial(start_state="held")
    rows_r, truth_r = _build_trial(start_state="rest")
    rows_m, truth_m = _build_trial(start_state="mid_motion")
    assert truth_h[0] == pytest.approx(180.0, abs=0.5)
    assert truth_r[0] < 150.0, "a resting leg is not extended"
    assert 150.0 < truth_m[0] < 179.0, "mid-motion starts partway through"


def test_generator_can_emit_out_of_plane_swing_and_a_marker_swap():
    rows, truth = _build_trial(out_of_plane_deg=18.0)
    assert len(rows) == len(truth)
    rows2, _ = _build_trial(swap_frame=140)
    assert rows2[140][2].shape == (3, 3)
```

- [ ] **Step 6: Run to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_optitrack_marker_angle.py -k "start_at_rest or out_of_plane_swing" -v`
Expected: FAIL, `TypeError: _build_trial() got an unexpected keyword argument`

- [ ] **Step 7: Extend `_build_trial`**

Replace the signature and body of `_build_trial` with:

```python
def _build_trial(n=240, hold=60, flex_deg=40.0, thigh_tilt=22.0, shank_tilt=30.0,
                 drop_from=None, drop_to=None, sign=-1.0, start_state="held",
                 hold_drift_deg=0.0, out_of_plane_deg=0.0, swap_frame=None,
                 thigh_as_bar=False):
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
    trials actually have.
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
```

- [ ] **Step 8: Run the whole file to verify nothing regressed**

Run: `.venv/Scripts/python.exe -m pytest tests/test_optitrack_marker_angle.py -q`
Expected: all pass, including the pre-existing tests that call `_build_trial()` with no new arguments.

- [ ] **Step 9: Commit**

```bash
git commit -F - -- tests/test_optitrack_marker_angle.py <<'EOF'
test(optitrack): generate the trial states the suite could never reach

_build_trial always started with a held, extended leg, which is exactly why
the seed-window bug survived every test. Adds start_state (held/rest/
mid_motion), hold drift, out-of-plane swing, a 1-frame marker index swap, and
_bar() for the near-collinear thigh that 239 of 254 real trials actually have.

Existing callers are unchanged: every new parameter defaults to the old
behaviour.
EOF
```

---

### Task 2: Cluster geometry classification

**Files:**
- Create: `optitrack_knee_axis.py`
- Test: `tests/test_optitrack_knee_axis.py`

**Interfaces:**
- Produces: `classify_clusters(a, b) -> tuple[np.ndarray, np.ndarray, str]` returning `(triangle, bar, which)` where `which` is `"a_is_triangle"` or `"b_is_triangle"`; raises `GeometryError` when both or neither are collinear.

- [ ] **Step 1: Write the failing test**

```python
import numpy as np
import pytest
import optitrack_knee_axis as ka


def _tri(n=100):
    """(3, n, 3) triangle cluster: real out-of-plane extent."""
    base = np.array([[0.06, 0.0, 0.0], [-0.06, 0.0, 0.0], [0.0, 0.021, 0.0]])
    return np.repeat(base[:, None, :], n, axis=1)


def _bar(n=100):
    """(3, n, 3) near-collinear cluster, 1.2 mm out of line."""
    base = np.array([[0.046, 0.0, 0.0], [-0.046, 0.0, 0.0], [0.0, 0.0012, 0.0]])
    return np.repeat(base[:, None, :], n, axis=1)


def test_classify_detects_by_planar_extent_not_marker_count():
    """Both clusters have THREE markers. Counting them would misclassify every
    real trial, because the thigh bar is a 3-marker cluster 1.5 mm out of line."""
    tri, bar, which = ka.classify_clusters(_tri(), _bar())
    assert which == "a_is_triangle"
    assert tri.shape == bar.shape == (3, 100, 3)


def test_classify_handles_the_reversed_rig_automatically():
    """15 of 254 trials are shank-bar / thigh-triangle. No caller should have
    to know that."""
    _tri_out, _bar_out, which = ka.classify_clusters(_bar(), _tri())
    assert which == "b_is_triangle"


def test_classify_refuses_when_neither_cluster_is_a_triangle():
    with pytest.raises(ka.GeometryError) as exc:
        ka.classify_clusters(_bar(), _bar())
    assert "collinear" in str(exc.value).lower()
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_optitrack_knee_axis.py -q`
Expected: FAIL, `ModuleNotFoundError: No module named 'optitrack_knee_axis'`

- [ ] **Step 3: Write minimal implementation**

```python
"""
optitrack_knee_axis.py
======================
Knee angle from labeled marker clusters, without assuming any pose.

Replaces the seeded reconstruction, which anchored the zero to the first 60
frames and set axis_thigh = -axis_shank, making the seed frame read exactly
180 deg BY CONSTRUCTION. A trial starting at rest or mid-motion therefore
anchored "straight" to a flexed pose and still reported a convincing 179.9.

See docs/superpowers/specs/2026-08-31-optitrack-knee-axis-design.md.
"""
from __future__ import annotations

import numpy as np

from pendulastic_pt_score import MIN_CLUSTER_PLANAR_EXTENT_M, _reference_shape


class GeometryError(ValueError):
    """The two clusters are not a triangle-and-bar pair."""


def _planar_extent(mk: np.ndarray) -> float:
    """Second singular value of the cluster's reference shape, in metres.

    This is how a bar is told from a plate. Marker COUNT cannot do it: the
    real thigh bar is a 3-marker cluster only 1.5 mm out of line over a 92 mm
    span, so counting markers classifies every trial identically.
    """
    tracked = np.isfinite(mk).all(axis=(0, 2))
    idx = np.where(tracked)[0]
    if len(idx) < 3:
        raise GeometryError("Cluster is never fully tracked; no shape to measure.")
    ref = _reference_shape(mk, idx)
    return float(np.linalg.svd(ref, compute_uv=False)[1])


def classify_clusters(a: np.ndarray, b: np.ndarray):
    """(triangle, bar, which) for a triangle-and-bar pair, in either order."""
    ea, eb = _planar_extent(a), _planar_extent(b)
    a_tri = ea >= MIN_CLUSTER_PLANAR_EXTENT_M
    b_tri = eb >= MIN_CLUSTER_PLANAR_EXTENT_M
    if a_tri and not b_tri:
        return a, b, "a_is_triangle"
    if b_tri and not a_tri:
        return b, a, "b_is_triangle"
    if not a_tri and not b_tri:
        raise GeometryError(
            f"Both clusters are collinear (out-of-line extent {ea*1000:.1f} mm "
            f"and {eb*1000:.1f} mm): neither can supply a hinge axis.")
    raise GeometryError(
        f"Both clusters are triangles ({ea*1000:.1f} mm and {eb*1000:.1f} mm "
        f"out of line): this rig geometry is unsupported.")
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_optitrack_knee_axis.py -q`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git commit -F - -- optitrack_knee_axis.py tests/test_optitrack_knee_axis.py <<'EOF'
feat(optitrack): classify cluster geometry by planar extent, not marker count

First piece of the pose-free knee angle. Detection has to be on out-of-line
extent: the real thigh bar is a 3-marker cluster 1.5 mm out of line over a
92 mm span, so counting markers classifies all 254 trials identically.

Handles the 15 reversed (shank-bar) trials automatically, so no caller has to
know which segment carries which geometry.
EOF
```

---

### Task 3: Thigh direction with mandatory sign continuity

**Files:**
- Modify: `optitrack_knee_axis.py`
- Test: `tests/test_optitrack_knee_axis.py`

**Interfaces:**
- Consumes: nothing from Task 2 at runtime.
- Produces: `segment_line_direction(bar) -> np.ndarray` of shape `(n, 3)`, NaN rows where untracked.

- [ ] **Step 1: Write the failing test**

```python
def test_line_direction_is_sign_continuous_through_an_index_swap():
    """SVD returns +/-v arbitrarily per frame, and Motive permutes marker
    indices on re-solve. Without continuity the direction flips 180 deg and
    the angle spikes. This is the transient that must NOT spike."""
    n = 120
    mk = np.zeros((3, n, 3))
    for i in range(n):
        mk[0, i] = [0.046, 0.0, 0.0]
        mk[1, i] = [-0.046, 0.0, 0.0]
        mk[2, i] = [0.0, 0.0012, 0.0]
    mk[[0, 1], 60] = mk[[1, 0], 60]          # 1-frame index swap
    dirs = ka.segment_line_direction(mk)
    steps = np.degrees(np.arccos(np.clip(
        np.sum(dirs[1:] * dirs[:-1], axis=1), -1, 1)))
    assert np.nanmax(steps) < 5.0, f"direction flipped: max step {np.nanmax(steps)}"


def test_line_direction_is_nan_where_untracked():
    mk = np.zeros((3, 50, 3))
    mk[0, :] = [0.046, 0.0, 0.0]; mk[1, :] = [-0.046, 0.0, 0.0]
    mk[2, :] = [0.0, 0.0012, 0.0]
    mk[:, 20:25] = np.nan
    dirs = ka.segment_line_direction(mk)
    assert np.isnan(dirs[20:25]).all()
    assert np.isfinite(dirs[30]).all()
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_optitrack_knee_axis.py -k line_direction -v`
Expected: FAIL, `AttributeError: module 'optitrack_knee_axis' has no attribute 'segment_line_direction'`

- [ ] **Step 3: Write minimal implementation**

```python
def segment_line_direction(bar: np.ndarray) -> np.ndarray:
    """Per-frame unit direction of a collinear cluster, sign-continuous.

    A bar observes its LINE but not its sign: SVD returns +/-v arbitrarily,
    and Motive permutes Marker1/2/3 when it re-solves the cluster. Continuity
    is therefore mandatory, not defensive, and it is enforced here on the 3-D
    vector before any scalar reduction -- unwrapping a scalar afterwards
    cannot undo a 180 deg vector flip.
    """
    n = bar.shape[1]
    out = np.full((n, 3), np.nan)
    prev = None
    for i in range(n):
        pts = bar[:, i, :]
        if not np.isfinite(pts).all():
            continue
        centred = pts - pts.mean(axis=0)
        try:
            line = np.linalg.svd(centred, full_matrices=False)[2][0]
        except np.linalg.LinAlgError:
            continue
        if prev is not None and float(np.dot(line, prev)) < 0.0:
            line = -line
        out[i] = line
        prev = line
    return out
```

- [ ] **Step 4: Run to verify it passes**

Run: same as Step 2. Expected: 2 passed

- [ ] **Step 5: Commit**

```bash
git commit -F - -- optitrack_knee_axis.py tests/test_optitrack_knee_axis.py <<'EOF'
feat(optitrack): sign-continuous line direction for the bar cluster

The thigh direction comes straight off the bar's line, with no pose
assumption -- this is the half of the seed problem that stops being an
assumption at all.

Sign continuity is mandatory rather than defensive. SVD returns +/-v
arbitrarily per frame and Motive permutes Marker1/2/3 on re-solve, so without
it the direction flips 180 deg on a transient. Enforced on the 3-D vector
before any scalar reduction, because unwrapping a scalar afterwards cannot
undo a vector flip.
EOF
```

---

### Task 4: Functional hinge axis and its conditioning

**Files:**
- Modify: `optitrack_knee_axis.py`
- Test: `tests/test_optitrack_knee_axis.py`

**Interfaces:**
- Produces: `hinge_axis(triangle) -> tuple[np.ndarray, float, np.ndarray]` returning `(axis, conditioning, pc2_series)`.

- [ ] **Step 1: Write the failing test**

```python
def _rotating_triangle(n, axis, deg_per_frame, wobble_deg=0.0, wobble_hz=0.0, fps=120.0):
    """Triangle rotating about `axis`, optionally wobbling about a perpendicular."""
    from pendulastic_pt_score import _shortest_arc_rotation
    base = np.array([[0.06, 0.0, 0.0], [-0.06, 0.0, 0.0], [0.0, 0.021, 0.0]])
    axis = np.asarray(axis, float); axis = axis / np.linalg.norm(axis)
    perp = np.cross(axis, [0.0, 0.0, 1.0])
    perp = perp / np.linalg.norm(perp)
    out = np.empty((3, n, 3))
    for i in range(n):
        R = _rot(axis, deg_per_frame * i)
        if wobble_deg:
            R = _rot(perp, wobble_deg * np.sin(2 * np.pi * wobble_hz * i / fps)) @ R
        out[:, i, :] = base @ R.T
    return out


def test_hinge_axis_recovers_a_known_rotation_axis():
    mk = _rotating_triangle(300, [0.0, 0.0, 1.0], 0.4)
    axis, cond, _pc2 = ka.hinge_axis(mk)
    assert abs(abs(float(np.dot(axis, [0, 0, 1]))) - 1.0) < 0.02, axis
    assert cond > 0.95, cond


def test_hinge_conditioning_falls_when_the_plate_tumbles():
    """Rotation spread across axes is not a hinge, and conditioning must say so."""
    mk = _rotating_triangle(300, [0.0, 0.0, 1.0], 0.4, wobble_deg=12.0, wobble_hz=2.0)
    _axis, cond, _pc2 = ka.hinge_axis(mk)
    assert cond < 0.95, cond
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_optitrack_knee_axis.py -k hinge -v`
Expected: FAIL, `AttributeError: ... has no attribute 'hinge_axis'`

- [ ] **Step 3: Write minimal implementation**

```python
from pendulastic_pt_score import _kabsch_rotations


def _rotation_increments(triangle: np.ndarray) -> np.ndarray:
    """Frame-to-frame rotation vectors of a triangle cluster, (m, 3) radians.

    For a hinge these all lie along the hinge, so their principal direction IS
    the axis -- recovered without any pose assumption, which is the whole
    point.
    """
    tracked = np.isfinite(triangle).all(axis=(0, 2))
    idx = np.where(tracked)[0]
    if len(idx) < 3:
        return np.zeros((0, 3))
    ref = _reference_shape(triangle, idx)
    cur = np.transpose(triangle[:, idx, :], (1, 0, 2))
    cur = cur - cur.mean(axis=1, keepdims=True)
    try:
        rots = _kabsch_rotations(ref, cur)
    except np.linalg.LinAlgError:
        return np.zeros((0, 3))
    out = []
    for a, b in zip(rots[:-1], rots[1:]):
        r = b @ a.T
        ang = float(np.arccos(np.clip((np.trace(r) - 1.0) / 2.0, -1.0, 1.0)))
        v = np.array([r[2, 1] - r[1, 2], r[0, 2] - r[2, 0], r[1, 0] - r[0, 1]])
        nv = float(np.linalg.norm(v))
        out.append(v / nv * ang if (nv > 1e-12 and ang > 1e-9) else np.zeros(3))
    return np.asarray(out)


def hinge_axis(triangle: np.ndarray):
    """(axis, conditioning, pc2_series) from the plate's own rotation.

    `conditioning` is the dominant eigenvalue's share of the total. 1.0 is a
    perfect hinge; a tumbling plate tends toward 1/3. `pc2_series` is the
    projection onto the SECOND axis, which the caller classifies as real
    out-of-plane motion or as jitter.
    """
    rv = _rotation_increments(triangle)
    if len(rv) < 8:
        raise GeometryError(
            f"Only {len(rv)} usable rotation increments: the cluster is not "
            f"tracked long enough to estimate a hinge axis.")
    w, V = np.linalg.eigh(rv.T @ rv)
    order = np.argsort(w)[::-1]
    w, V = w[order], V[:, order]
    total = float(w.sum())
    if not np.isfinite(total) or total <= 0:
        raise GeometryError("Cluster shows no rotation; no hinge axis exists.")
    return V[:, 0], float(w[0] / total), rv @ V[:, 1]
```

- [ ] **Step 4: Run to verify it passes**

Run: same as Step 2. Expected: 2 passed

- [ ] **Step 5: Commit**

```bash
git commit -F - -- optitrack_knee_axis.py tests/test_optitrack_knee_axis.py <<'EOF'
feat(optitrack): estimate the knee hinge axis from plate rotation, pose-free

For a hinge, every frame-to-frame rotation increment lies along the axis, so
their principal direction recovers it with no pose assumption -- the same
trick imu_flex_axis uses on gyro vectors.

Returns the conditioning (dominant eigenvalue share) and the PC2 series, so
the caller can tell a poorly-conditioned axis from genuine out-of-plane limb
motion instead of refusing both alike.
EOF
```

---

### Task 5: Three-way conditioning verdict

**Files:**
- Modify: `optitrack_knee_axis.py`
- Test: `tests/test_optitrack_knee_axis.py`

**Interfaces:**
- Consumes: `hinge_axis()`'s `conditioning` and `pc2_series`.
- Produces: `low_freq_ratio(pc2, fps) -> float`; `conditioning_verdict(conditioning, pc2, fps) -> str` returning `"ok"`, `"ill_conditioned_axis"` or `"out_of_plane_motion"`.

- [ ] **Step 1: Write the failing test**

```python
def test_low_freq_ratio_separates_slow_motion_from_jitter():
    fps, n = 120.0, 600
    t = np.arange(n) / fps
    slow = np.sin(2 * np.pi * 1.0 * t)
    fast = np.random.default_rng(0).normal(size=n)
    assert ka.low_freq_ratio(slow, fps) > 0.9
    assert ka.low_freq_ratio(fast, fps) < 0.4


def test_verdict_refuses_jitter_but_keeps_real_out_of_plane_motion():
    """A single conditioning cut would refuse 9 of 30 measured trials, and 2 of
    those are real limb motion -- biased toward unusual movement, which is
    where spasticity lives."""
    fps, n = 120.0, 600
    t = np.arange(n) / fps
    slow = np.sin(2 * np.pi * 1.0 * t)
    fast = np.random.default_rng(0).normal(size=n)
    assert ka.conditioning_verdict(0.97, fast, fps) == "ok"
    assert ka.conditioning_verdict(0.70, fast, fps) == "ill_conditioned_axis"
    assert ka.conditioning_verdict(0.70, slow, fps) == "out_of_plane_motion"


def test_verdict_refuses_a_series_too_short_to_have_a_spectrum():
    short = np.sin(np.arange(50) / 5.0)
    assert ka.conditioning_verdict(0.70, short, 120.0) == "ill_conditioned_axis"
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_optitrack_knee_axis.py -k "low_freq or verdict" -v`
Expected: FAIL, `AttributeError: ... has no attribute 'low_freq_ratio'`

- [ ] **Step 3: Write minimal implementation**

```python
from scipy.signal import welch

MIN_HINGE_CONDITIONING = 0.90
LOW_FREQ_CUTOFF_HZ = 6.0
OUT_OF_PLANE_MIN_LF_RATIO = 0.50
MIN_SPECTRAL_FRAMES = 240


def low_freq_ratio(pc2: np.ndarray, fps: float) -> float:
    """Share of PC2's power below LOW_FREQ_CUTOFF_HZ.

    Welch with a Hann window, because short series suffer spectral LEAKAGE and
    poor frequency resolution (aliasing happens at acquisition, not here) and
    windowing is the mitigation.

    The cutoff sits at 6 Hz, above the 0.5-1.5 Hz swing, because PC2 is a
    DIFFERENCED series and differencing shifts energy upward. Measured: at
    6 Hz the two genuine out-of-plane trials read 0.71 and 0.54 against
    0.08-0.41 for jitter. A 2.5 Hz cutoff was rejected -- nothing there
    reaches 0.60, so the branch would be dead code.
    """
    pc2 = np.asarray(pc2, dtype=float)
    pc2 = pc2[np.isfinite(pc2)]
    if len(pc2) < MIN_SPECTRAL_FRAMES:
        return float("nan")
    f, p = welch(pc2 - pc2.mean(), fs=fps,
                 nperseg=min(256, len(pc2)), window="hann")
    total = float(p.sum())
    if total <= 0:
        return float("nan")
    return float(p[f < LOW_FREQ_CUTOFF_HZ].sum() / total)


def conditioning_verdict(conditioning: float, pc2: np.ndarray, fps: float) -> str:
    """"ok" | "ill_conditioned_axis" | "out_of_plane_motion".

    Three outcomes, not two, deliberately. Refusing every poorly-conditioned
    trial would throw away real non-sagittal limb motion along with the noise,
    and would do it in the direction that erases group separation.
    """
    if conditioning >= MIN_HINGE_CONDITIONING:
        return "ok"
    ratio = low_freq_ratio(pc2, fps)
    if np.isfinite(ratio) and ratio >= OUT_OF_PLANE_MIN_LF_RATIO:
        return "out_of_plane_motion"
    return "ill_conditioned_axis"
```

- [ ] **Step 4: Run to verify it passes**

Run: same as Step 2. Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git commit -F - -- optitrack_knee_axis.py tests/test_optitrack_knee_axis.py <<'EOF'
feat(optitrack): three conditioning outcomes, so real motion is not refused

A single conditioning cut would refuse 9 of 30 measured trials, and 2 of those
are genuine out-of-plane limb motion rather than noise -- biased toward trials
with unusual movement, which is where spasticity lives, and the same one-sided
bias the seed bug already inflicted.

The spectral discriminator is fixed on measured data: Welch/Hann PSD of the
PC2 increment series, low-frequency ratio below 6.0 Hz, threshold 0.50. 6 Hz
rather than the swing frequency because PC2 is DIFFERENCED, which shifts
energy up. A 2.5 Hz / 0.60 rule was rejected: nothing reaches 0.60 there, so
the branch would be dead code.

Both thresholds are PROVISIONAL -- the spectral one rests on 2 positives, and
0.90 is not derived at all. Task 10 pins branch reachability with a synthetic
so 2 examples cannot leave it unexercised.
EOF
```

---

### Task 6: Signed angle that cannot fold

**Files:**
- Modify: `optitrack_knee_axis.py`
- Test: `tests/test_optitrack_knee_axis.py`

**Interfaces:**
- Produces: `segment_axis_from_plate(triangle, hinge) -> np.ndarray` shape `(n,3)`; `signed_knee_angle(thigh_dirs, shank_dirs, hinge) -> np.ndarray` shape `(n,)`.

- [ ] **Step 1: Write the failing test**

```python
def test_signed_angle_does_not_fold_past_180():
    """The defect this replaces: an unsigned arccos mirrors at 180, so an
    angle continuing past it reads as coming back down."""
    n = 200
    hinge = np.array([0.0, 0.0, 1.0])
    thigh = np.repeat(np.array([[1.0, 0.0, 0.0]]), n, axis=0)
    sweep = np.linspace(170.0, 200.0, n)
    shank = np.stack([_rot(hinge, a) @ np.array([1.0, 0.0, 0.0]) for a in sweep])
    ang = ka.signed_knee_angle(thigh, shank, hinge)
    assert np.all(np.diff(ang) > 0), "angle folded instead of continuing"
    assert ang[-1] - ang[0] == pytest.approx(30.0, abs=1.0)


def test_signed_angle_is_nan_where_either_direction_is_missing():
    n = 40
    hinge = np.array([0.0, 0.0, 1.0])
    thigh = np.repeat(np.array([[1.0, 0.0, 0.0]]), n, axis=0)
    shank = np.repeat(np.array([[0.0, 1.0, 0.0]]), n, axis=0)
    shank[10:15] = np.nan
    ang = ka.signed_knee_angle(thigh, shank, hinge)
    assert np.isnan(ang[10:15]).all()
    assert np.isfinite(ang[20])
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_optitrack_knee_axis.py -k signed_angle -v`
Expected: FAIL, `AttributeError: ... has no attribute 'signed_knee_angle'`

- [ ] **Step 3: Write minimal implementation**

```python
def signed_knee_angle(thigh_dirs: np.ndarray, shank_dirs: np.ndarray,
                      hinge: np.ndarray) -> np.ndarray:
    """Continuous signed angle between two directions, about `hinge`.

    atan2 rather than arccos. The old code used an unsigned arccos, which
    FOLDS at 180: an angle continuing past it reads as coming back down, so a
    wrong anchor produced a plausible curve instead of an obvious error. A
    signed angle plus unwrapping cannot fold, so an anchoring error shows up
    as an out-of-range value rather than hiding as a mirrored one.
    """
    hinge = np.asarray(hinge, float)
    hinge = hinge / np.linalg.norm(hinge)
    n = len(thigh_dirs)
    ang = np.full(n, np.nan)
    ok = (np.isfinite(thigh_dirs).all(axis=1) & np.isfinite(shank_dirs).all(axis=1))
    if not ok.any():
        return ang
    t = thigh_dirs[ok] - np.outer(thigh_dirs[ok] @ hinge, hinge)
    s = shank_dirs[ok] - np.outer(shank_dirs[ok] @ hinge, hinge)
    tn = np.linalg.norm(t, axis=1, keepdims=True)
    sn = np.linalg.norm(s, axis=1, keepdims=True)
    good = (tn[:, 0] > 1e-9) & (sn[:, 0] > 1e-9)
    t = np.divide(t, np.where(tn > 1e-9, tn, 1.0))
    s = np.divide(s, np.where(sn > 1e-9, sn, 1.0))
    cross = np.cross(t, s) @ hinge
    dot = np.sum(t * s, axis=1)
    vals = np.degrees(np.arctan2(cross, dot))
    vals[~good] = np.nan
    finite = np.isfinite(vals)
    if finite.any():
        vals[finite] = np.degrees(np.unwrap(np.radians(vals[finite])))
    ang[np.where(ok)[0]] = vals
    return ang


def segment_axis_from_plate(triangle: np.ndarray, hinge: np.ndarray) -> np.ndarray:
    """Per-frame direction of the triangle's segment, carried by its rotation.

    The reference direction is any unit vector perpendicular to the hinge; its
    absolute phase is arbitrary, which is exactly the constant offset the
    scored parameters are invariant to.
    """
    hinge = np.asarray(hinge, float)
    hinge = hinge / np.linalg.norm(hinge)
    seed = np.cross(hinge, [0.0, 0.0, 1.0])
    if np.linalg.norm(seed) < 1e-6:
        seed = np.cross(hinge, [0.0, 1.0, 0.0])
    seed = seed / np.linalg.norm(seed)

    tracked = np.isfinite(triangle).all(axis=(0, 2))
    idx = np.where(tracked)[0]
    n = triangle.shape[1]
    out = np.full((n, 3), np.nan)
    if len(idx) < 3:
        return out
    ref = _reference_shape(triangle, idx)
    cur = np.transpose(triangle[:, idx, :], (1, 0, 2))
    cur = cur - cur.mean(axis=1, keepdims=True)
    try:
        rots = _kabsch_rotations(ref, cur)
    except np.linalg.LinAlgError:
        return out
    out[idx] = np.einsum("mij,j->mi", rots, seed)
    return out
```

- [ ] **Step 4: Run to verify it passes**

Run: same as Step 2. Expected: 2 passed

- [ ] **Step 5: Commit**

```bash
git commit -F - -- optitrack_knee_axis.py tests/test_optitrack_knee_axis.py <<'EOF'
feat(optitrack): signed knee angle that cannot fold at 180

The old reconstruction took an unsigned arccos, which MIRRORS at 180: an angle
continuing past it reads as coming back down, so a wrong anchor produced a
plausible curve rather than an obvious error. That is why a leg hanging flexed
all trial still reported head 179.9.

atan2 about the hinge, then unwrapped. An anchoring error now shows up as an
out-of-range value instead of hiding as a mirrored one.

segment_axis_from_plate's reference direction is any vector perpendicular to
the hinge. Its phase is arbitrary, which is precisely the constant offset the
scored PT parameters are invariant to.
EOF
```

---

### Task 7: KneeAngleResult and the uncalibrated guard

**Files:**
- Modify: `optitrack_knee_axis.py`
- Test: `tests/test_optitrack_knee_axis.py`

**Interfaces:**
- Produces: `UncalibratedOffsetError`; `KneeAngleResult` with fields `is_calibrated: bool`, `offset_deg: float | None`, `conditioning: float`, `low_freq_ratio: float`, `flags: tuple[str, ...]`, `raw_angles: np.ndarray`, and methods `get_relative_angles()`, `get_absolute_angles()`.

- [ ] **Step 1: Write the failing test**

```python
def test_absolute_angles_raise_when_the_offset_was_never_established():
    r = ka.KneeAngleResult(raw_angles=np.array([10.0, 20.0, 30.0]),
                           is_calibrated=False, offset_deg=None,
                           conditioning=0.97, low_freq_ratio=0.1,
                           flags=("uncalibrated_offset",))
    with pytest.raises(ka.UncalibratedOffsetError):
        r.get_absolute_angles()
    rel = r.get_relative_angles()
    assert rel[0] == 0.0 and rel[2] == 20.0


def test_absolute_angles_also_refuse_a_low_confidence_hold():
    r = ka.KneeAngleResult(raw_angles=np.array([1.0, 2.0]), is_calibrated=True,
                           offset_deg=5.0, conditioning=0.97, low_freq_ratio=0.1,
                           flags=("low_confidence_hold",))
    with pytest.raises(ka.UncalibratedOffsetError):
        r.get_absolute_angles()


def test_result_has_no_innocuous_angles_attribute():
    """A plain .angles would let a consumer reach an absolute curve without
    saying so. The escape hatch is named raw_angles, so its use is visible in
    a diff."""
    r = ka.KneeAngleResult(raw_angles=np.array([1.0]), is_calibrated=True,
                           offset_deg=0.0, conditioning=0.97,
                           low_freq_ratio=0.1, flags=())
    assert not hasattr(r, "angles")
    assert r.get_absolute_angles()[0] == 1.0
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_optitrack_knee_axis.py -k "absolute_angles or innocuous" -v`
Expected: FAIL, `AttributeError: ... has no attribute 'KneeAngleResult'`

- [ ] **Step 3: Write minimal implementation**

```python
from dataclasses import dataclass, field


class UncalibratedOffsetError(RuntimeError):
    """Absolute angles were requested for a curve with no trustworthy zero."""


@dataclass(frozen=True)
class KneeAngleResult:
    """A knee curve plus what is known about how far it can be trusted.

    There is deliberately NO `.angles` attribute. A plain, innocuous-looking
    name is how an uncalibrated curve reaches an absolute-angle consumer
    without anyone deciding it should. Access is by named accessor instead.

    Magic-method overrides (__getitem__, __array__) are deliberately NOT used:
    they break slicing and iteration in surprising ways, and they cannot
    protect the real seam anyway, since load_optitrack_detailed must keep
    returning a plain array for its existing consumers.
    """
    raw_angles: np.ndarray
    is_calibrated: bool
    offset_deg: float | None
    conditioning: float
    low_freq_ratio: float
    flags: tuple = field(default_factory=tuple)

    def get_relative_angles(self) -> np.ndarray:
        """Baseline-subtracted. Always valid, calibrated or not.

        This is what scoring uses: every scored PT parameter is a difference,
        a ratio of differences, a derivative, a frequency or a count, so a
        constant offset cancels.
        """
        a = np.asarray(self.raw_angles, dtype=float)
        finite = np.isfinite(a)
        if not finite.any():
            return a.copy()
        return a - a[np.argmax(finite)]

    def get_absolute_angles(self) -> np.ndarray:
        """Angles on the 180-is-extended convention. Raises when unearned."""
        if not self.is_calibrated or "low_confidence_hold" in self.flags:
            raise UncalibratedOffsetError(
                "No trustworthy zero was established for this trial "
                f"(flags: {', '.join(self.flags) or 'none'}). Use "
                "get_relative_angles(); every scored PT parameter is "
                "offset-invariant and does not need an absolute zero.")
        return np.asarray(self.raw_angles, dtype=float)
```

- [ ] **Step 4: Run to verify it passes**

Run: same as Step 2. Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git commit -F - -- optitrack_knee_axis.py tests/test_optitrack_knee_axis.py <<'EOF'
feat(optitrack): make an uncalibrated curve impossible to use as absolute

KneeAngleResult has deliberately no .angles attribute. An innocuous-looking
name is how an uncalibrated curve reaches an absolute-angle consumer without
anyone deciding it should. get_relative_angles() always succeeds and is what
scoring uses; get_absolute_angles() raises UncalibratedOffsetError; raw_angles
is a visibly-named escape hatch.

Magic-method overrides were considered and rejected: they break slicing and
iteration surprisingly, and cannot protect the real seam anyway, because
load_optitrack_detailed must keep returning a plain array.
EOF
```

---

### Task 8: Hold detection and the cosmetic offset

**Files:**
- Modify: `optitrack_knee_axis.py`
- Test: `tests/test_optitrack_knee_axis.py`

**Interfaces:**
- Produces: `find_hold(triangle, bar, angles) -> tuple[slice | None, tuple[str, ...]]`; `anchor_to_extension(angles, hold) -> tuple[float | None, tuple[str, ...]]`.

- [ ] **Step 1: Write the failing test**

```python
def test_a_drifting_hold_withholds_the_offset():
    """Patients shift during the hold, which drifts the reference rather than
    stepping it. A drifting hold must not be used to set an absolute zero."""
    ang = np.concatenate([np.linspace(0.0, 9.0, 80), np.linspace(9.0, 60.0, 220)])
    offset, flags = ka.anchor_to_extension(ang, slice(0, 80))
    assert offset is None
    assert "low_confidence_hold" in flags


def test_a_steady_hold_sets_the_offset_so_extension_reads_180():
    ang = np.concatenate([np.full(80, 4.0), np.linspace(4.0, 60.0, 220)])
    offset, flags = ka.anchor_to_extension(ang, slice(0, 80))
    assert offset == pytest.approx(176.0, abs=0.5)
    assert flags == ()


def test_no_hold_at_all_is_reported_not_guessed():
    offset, flags = ka.anchor_to_extension(np.linspace(0, 60, 300), None)
    assert offset is None
    assert "uncalibrated_offset" in flags
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_optitrack_knee_axis.py -k "hold or anchor" -v`
Expected: FAIL, `AttributeError: ... has no attribute 'anchor_to_extension'`

- [ ] **Step 3: Write minimal implementation**

```python
MAX_HOLD_SPEED_MM_PER_FRAME = 0.5
MAX_HOLD_COLLINEARITY_DEG = 25.0
MAX_HOLD_SD_DEG = 2.0
EXTENDED_ANGLE_DEG = 180.0


def anchor_to_extension(angles: np.ndarray, hold):
    """(offset_deg, flags). Cosmetic only -- scoring never depends on it.

    A hold that DRIFTS is not a reference. Patients shift during the hold, so
    the failure is a slow ramp rather than a clean step, and averaging over it
    would silently bake the drift into the zero.
    """
    if hold is None:
        return None, ("uncalibrated_offset",)
    seg = np.asarray(angles, dtype=float)[hold]
    seg = seg[np.isfinite(seg)]
    if len(seg) < 5:
        return None, ("uncalibrated_offset",)
    if float(np.std(seg)) > MAX_HOLD_SD_DEG:
        return None, ("low_confidence_hold",)
    return float(EXTENDED_ANGLE_DEG - np.median(seg)), ()


def find_hold(triangle: np.ndarray, bar: np.ndarray, angles: np.ndarray):
    """(slice | None, flags) for the pre-release extended hold.

    Calm AND geometrically extended. Calm alone is not enough: a leg resting
    flexed is perfectly calm, and calling that "extended" is precisely the bug
    being fixed.
    """
    n = triangle.shape[1]
    cen = np.nanmean(np.concatenate([triangle, bar], axis=0), axis=0)   # (n,3)
    speed_mm = np.full(n, np.inf)
    step = np.linalg.norm(np.diff(cen, axis=0), axis=1) * 1000.0
    speed_mm[1:] = step
    ang = np.asarray(angles, dtype=float)
    extended = np.abs(ang - EXTENDED_ANGLE_DEG) <= MAX_HOLD_COLLINEARITY_DEG
    ok = (speed_mm <= MAX_HOLD_SPEED_MM_PER_FRAME) & extended & np.isfinite(ang)
    if not ok.any():
        return None, ("uncalibrated_offset",)
    idx = np.where(ok)[0]
    breaks = np.where(np.diff(idx) > 1)[0]
    runs = np.split(idx, breaks + 1)
    best = max(runs, key=len)
    if len(best) < 5:
        return None, ("uncalibrated_offset",)
    return slice(int(best[0]), int(best[-1]) + 1), ()
```

- [ ] **Step 4: Run to verify it passes**

Run: same as Step 2. Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git commit -F - -- optitrack_knee_axis.py tests/test_optitrack_knee_axis.py <<'EOF'
feat(optitrack): detect the hold, and withhold the offset when it drifts

The offset is cosmetic here -- scoring never depends on it -- which is exactly
what makes it safe to refuse. A hold must be calm AND geometrically extended:
calm alone is not enough, because a leg resting flexed is perfectly calm, and
calling that "extended" is the bug being fixed.

A drifting hold sets low_confidence_hold and withholds the offset rather than
averaging the drift into the zero. Patients shift during the hold, so the
failure is a slow ramp, not a clean step.
EOF
```

---

### Task 9: Orchestrator

**Files:**
- Modify: `optitrack_knee_axis.py`
- Test: `tests/test_optitrack_knee_axis.py`

**Interfaces:**
- Consumes: everything from Tasks 2-8.
- Produces: `knee_angle_from_clusters(cluster_a, cluster_b, fps) -> KneeAngleResult`.

- [ ] **Step 1: Write the failing test**

```python
def test_orchestrator_recovers_a_known_flexion_from_a_bar_and_triangle():
    from tests.test_optitrack_marker_angle import _build_trial
    rows, truth = _build_trial(n=400, hold=80, flex_deg=45.0, thigh_as_bar=True)
    shank = np.stack([r[2] for r in rows], axis=1)     # (3, n, 3)
    thigh = np.stack([r[3] for r in rows], axis=1)
    res = ka.knee_angle_from_clusters(shank, thigh, fps=120.0)
    rel = res.get_relative_angles()
    swept_true = abs(truth[-1] - truth[0])
    swept_got = abs(np.nanmax(rel) - np.nanmin(rel))
    assert swept_got == pytest.approx(swept_true, rel=0.15), (swept_got, swept_true)


def test_orchestrator_refuses_an_ill_conditioned_trial_with_a_named_reason():
    n = 400
    rng = np.random.default_rng(1)
    tumbling = rng.normal(scale=0.05, size=(3, n, 3))
    bar = np.repeat(np.array([[0.046, 0, 0], [-0.046, 0, 0], [0, 0.0012, 0]])[:, None, :],
                    n, axis=1)
    with pytest.raises(ka.GeometryError) as exc:
        ka.knee_angle_from_clusters(tumbling, bar, fps=120.0)
    assert "conditioned" in str(exc.value).lower() or "hinge" in str(exc.value).lower()
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_optitrack_knee_axis.py -k orchestrator -v`
Expected: FAIL, `AttributeError: ... has no attribute 'knee_angle_from_clusters'`

- [ ] **Step 3: Write minimal implementation**

```python
def knee_angle_from_clusters(cluster_a: np.ndarray, cluster_b: np.ndarray,
                             fps: float) -> KneeAngleResult:
    """Knee angle from two marker clusters, in either role order.

    Raises GeometryError when no trustworthy angle exists. Returns a
    KneeAngleResult otherwise, flagged with everything known about it.
    """
    triangle, bar, _which = classify_clusters(cluster_a, cluster_b)
    axis, conditioning, pc2 = hinge_axis(triangle)
    verdict = conditioning_verdict(conditioning, pc2, fps)
    if verdict == "ill_conditioned_axis":
        raise GeometryError(
            f"The hinge axis is not recoverable: only "
            f"{conditioning * 100:.0f}% of the segment's rotation lies on a "
            f"single axis, and the remainder is high-frequency, i.e. marker "
            f"noise rather than limb motion. Check marker placement and "
            f"tracking quality for this trial.")

    flags = () if verdict == "ok" else (verdict, "OUT_OF_PLANE_AMPLITUDE_UNDERREPORTED")
    thigh_dirs = segment_line_direction(bar)
    shank_dirs = segment_axis_from_plate(triangle, axis)
    angles = signed_knee_angle(thigh_dirs, shank_dirs, axis)

    hold, hold_flags = find_hold(triangle, bar, angles)
    offset, offset_flags = anchor_to_extension(angles, hold)
    flags = flags + hold_flags + offset_flags
    if offset is not None:
        angles = angles + offset
    return KneeAngleResult(raw_angles=angles, is_calibrated=offset is not None,
                           offset_deg=offset, conditioning=conditioning,
                           low_freq_ratio=low_freq_ratio(pc2, fps),
                           flags=tuple(dict.fromkeys(flags)))
```

- [ ] **Step 4: Run to verify it passes**

Run: same as Step 2. Expected: 2 passed

- [ ] **Step 5: Run the whole module suite**

Run: `.venv/Scripts/python.exe -m pytest tests/test_optitrack_knee_axis.py -q`
Expected: all pass

- [ ] **Step 6: Commit**

```bash
git commit -F - -- optitrack_knee_axis.py tests/test_optitrack_knee_axis.py <<'EOF'
feat(optitrack): assemble the pose-free knee angle end to end

knee_angle_from_clusters takes two clusters in either role order and returns a
KneeAngleResult, or raises with a reason an operator can act on. An
ill-conditioned axis names what is wrong (rotation not on one axis, remainder
high-frequency) and what to check, rather than reporting a frame count.
EOF
```

---

### Task 10: Properties the design rests on

**Files:**
- Modify: `tests/test_optitrack_knee_axis.py`

- [ ] **Step 1: Write the property tests**

```python
def test_offset_invariance_of_every_scored_parameter():
    """The claim the whole design rests on, tested rather than argued.

    If this fails, an unknown offset DOES reach the score and the decision to
    demote 180-is-extended to presentation was wrong."""
    import pendulastic_pt_score as pt
    t = np.arange(1200) / 120.0
    ts = np.maximum(t - 1.0, 0.0)
    ang = 130.0 + 50.0 * np.exp(-ts / 3.0) * np.cos(2 * np.pi * 0.9 * ts)
    base = pt.compute_pt_params(t, ang)
    for off in (-37.0, -5.0, 12.5, 88.0):
        shifted = pt.compute_pt_params(t, ang + off)
        assert shifted is not None
        for k in pt._PARAM_KEYS:
            assert shifted[k] == pytest.approx(base[k], rel=1e-6), (k, off)
        assert shifted["A0_deg"] == pytest.approx(base["A0_deg"], rel=1e-6)


def test_out_of_plane_branch_is_reachable():
    """Two real positives cannot keep a branch honest. A dialled-in
    out-of-plane trial must actually reach out_of_plane_motion, or the branch
    is dead code the way the quadriceps-catch merge was."""
    fps, n = 120.0, 600
    t = np.arange(n) / fps
    slow_pc2 = np.sin(2 * np.pi * 1.0 * t)
    assert ka.conditioning_verdict(0.80, slow_pc2, fps) == "out_of_plane_motion"
    assert ka.low_freq_ratio(slow_pc2, fps) >= ka.OUT_OF_PLANE_MIN_LF_RATIO


def test_a_blackout_at_release_does_not_spike_the_angle():
    from tests.test_optitrack_marker_angle import _build_trial
    rows, _truth = _build_trial(n=400, hold=80, thigh_as_bar=True,
                                drop_from=80, drop_to=90)
    shank = np.stack([r[2] if not r[4] else np.full((3, 3), np.nan)
                      for r in rows], axis=1)
    thigh = np.stack([r[3] if not r[4] else np.full((3, 3), np.nan)
                      for r in rows], axis=1)
    res = ka.knee_angle_from_clusters(shank, thigh, fps=120.0)
    rel = res.get_relative_angles()
    steps = np.abs(np.diff(rel[np.isfinite(rel)]))
    assert np.nanmax(steps) < 20.0, f"spiked {np.nanmax(steps)} deg across the gap"


def test_a_marker_index_swap_does_not_spike_the_angle():
    from tests.test_optitrack_marker_angle import _build_trial
    rows, _truth = _build_trial(n=400, hold=80, thigh_as_bar=True, swap_frame=200)
    shank = np.stack([r[2] for r in rows], axis=1)
    thigh = np.stack([r[3] for r in rows], axis=1)
    res = ka.knee_angle_from_clusters(shank, thigh, fps=120.0)
    rel = res.get_relative_angles()
    steps = np.abs(np.diff(rel[np.isfinite(rel)]))
    assert np.nanmax(steps) < 20.0, f"index swap spiked {np.nanmax(steps)} deg"
```

- [ ] **Step 2: Run them**

Run: `.venv/Scripts/python.exe -m pytest tests/test_optitrack_knee_axis.py -k "invariance or reachable or spike" -v`
Expected: PASS. If offset invariance fails, STOP and report — the design's central claim is wrong and the spec needs revisiting before any integration.

- [ ] **Step 3: Commit**

```bash
git commit -F - -- tests/test_optitrack_knee_axis.py <<'EOF'
test(optitrack): pin the claims the pose-free design rests on

Offset invariance is tested rather than argued: if an injected constant offset
moved any scored parameter, demoting 180-is-extended to presentation would be
wrong and the whole approach would need revisiting.

Branch reachability is pinned because the out-of-plane threshold rests on two
real positives, and two examples cannot keep a branch honest -- that is how the
quadriceps-catch merge sat unreachable for months.

Transient tests cover a 10-frame blackout at release and a 1-frame marker index
swap at peak velocity, which is what the vector-stage sign continuity exists
for.
EOF
```

---

### Task 11: Loader integration

**Files:**
- Modify: `pendulastic_pt_score.py` (the `_angle_from_labeled_markers` call site inside `load_optitrack_detailed`, near line 1425)
- Test: `tests/test_optitrack_marker_angle.py`

**Interfaces:**
- Consumes: `knee_angle_from_clusters`, `GeometryError`, `UncalibratedOffsetError`.
- Produces: unchanged `load_optitrack_detailed(path) -> (t, angle, TrialQuality)`.

- [ ] **Step 1: Write the failing test**

```python
def test_loader_reports_knee_axis_flags_in_trial_quality(tmp_path):
    """The loader's tuple contract does not change; the flags ride in
    TrialQuality, which is where every other quality signal already lives."""
    import pendulastic_pt_score as pt
    rows, _truth = _build_trial(n=400, hold=0, flex_deg=40.0,
                                start_state="rest", thigh_as_bar=True)
    path = tmp_path / "trial_rest_optitrack.csv"
    _write_csv(str(path), rows)
    _t, _ang, quality = pt.load_optitrack_detailed(str(path))
    joined = " ".join(quality.warnings).lower()
    assert "uncalibrated" in joined or "hold" in joined, quality.warnings
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_optitrack_marker_angle.py -k loader_reports -v`
Expected: FAIL — no such warning is produced today.

- [ ] **Step 3: Replace the call site**

In `load_optitrack_detailed`, replace the `angles = _angle_from_labeled_markers(df, _shank_mks, _thigh_mks)` block with:

```python
            import optitrack_knee_axis as _ka

            def _cluster(cols_list):
                arr = np.stack([df.iloc[:, c].values.astype(float)
                                for c in cols_list[:3]])
                arr[np.abs(arr) > 1e5] = np.nan
                return arr

            _fps = 1.0 / max(float(np.median(np.diff(t))), 1e-9)
            try:
                _res = _ka.knee_angle_from_clusters(_cluster(_shank_mks),
                                                    _cluster(_thigh_mks), _fps)
            except _ka.GeometryError as exc:
                raise ValueError(f"{exc} (optical coverage {cov*100:.1f}%)") from exc
            # Scoring uses the RELATIVE curve: every scored PT parameter is
            # offset-invariant, so an unearned absolute zero buys nothing and
            # risks everything. The absolute convention is applied only when
            # anchor_to_extension actually established it.
            angles = (_res.get_absolute_angles() if _res.is_calibrated
                      and "low_confidence_hold" not in _res.flags
                      else _res.get_relative_angles())
            warns = list(_curve_quality_warnings(angles))
            for _flag in _res.flags:
                warns.append(_KNEE_AXIS_WARNINGS.get(
                    _flag, f"Knee-axis reconstruction flag: {_flag}."))
```

Add above `load_optitrack_detailed`:

```python
_KNEE_AXIS_WARNINGS = {
    "uncalibrated_offset":
        "No extended hold was recorded, so this curve has no absolute zero. "
        "The shape and every scored PT parameter are still valid -- they are "
        "offset-invariant -- but the angle values themselves are relative.",
    "low_confidence_hold":
        "The pre-release hold drifted more than 2 deg, so it was not used to "
        "set the zero. Scored parameters are unaffected; absolute angles are "
        "withheld.",
    "out_of_plane_motion":
        "The leg did not swing in a single plane, so the reported amplitude "
        "is a LOWER BOUND: a non-sagittal swing projected onto one axis reads "
        "short by roughly cos(out-of-plane angle).",
    "OUT_OF_PLANE_AMPLITUDE_UNDERREPORTED":
        "Treat phi_max and R2n as minimum bounds for this trial.",
}
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_optitrack_marker_angle.py -q`
Expected: all pass

- [ ] **Step 5: Run every suite that consumes optical angles**

Run each separately (memory: run one file at a time):
```
.venv/Scripts/python.exe -m pytest tests/test_pt_score.py -q
.venv/Scripts/python.exe -m pytest tests/test_optitrack_marker_angle.py -q
.venv/Scripts/python.exe -m pytest tests/test_pt_report_common.py -q
.venv/Scripts/python.exe -m pytest tests/test_workbench_engine.py -q
```
Expected: all pass. Any failure here is a real behaviour change — read it before assuming it is a stale expectation.

- [ ] **Step 6: Commit**

```bash
git commit -F - -- pendulastic_pt_score.py tests/test_optitrack_marker_angle.py <<'EOF'
fix(optitrack): reconstruct the knee angle without assuming a pose

Replaces the seeded reconstruction at the loader's call site. The old code
seeded the anatomical axis from the FIRST 60 frames and set
axis_thigh = -axis_shank, so the seed frame read exactly 180 by construction
and an unsigned arccos folded there instead of running past. A trial starting
at rest or mid-motion anchored "straight" to a flexed pose and still reported a
convincing 179.9 baseline.

load_optitrack_detailed keeps its (t, angle, TrialQuality) contract; the new
flags ride in TrialQuality, where every other quality signal already lives.

Scoring consumes the RELATIVE curve. Every scored PT parameter is
offset-invariant, and so is _detect_release, so an unearned absolute zero buys
nothing and risks everything. The absolute convention is applied only when a
hold actually established it.
EOF
```

---

### Task 12: Real-corpus regression and acceptance

**Files:**
- Create: `docs/reports/2026-08-31-knee-axis-acceptance.md`

- [ ] **Step 1: Write the regression test**

Append to `tests/test_optitrack_marker_angle.py`:

```python
import os
import pytest

_P8 = os.path.join("OptiTrack_Recordings", "Participant_8", "Left", "trial_2_optitrack.csv")
_P9 = os.path.join("OptiTrack_Recordings", "Participant_9", "Left", "trial_3_optitrack.csv")


@pytest.mark.skipif(not os.path.exists(_P8), reason="real corpus not present")
def test_p8_left_trial2_no_longer_claims_a_fully_extended_resting_leg():
    """Video shows the leg hanging flexed for the whole recording with nobody
    holding it, yet the old code reported head 179.9 / tail 179.6."""
    import numpy as np, pendulastic_pt_score as pt
    _t, ang, quality = pt.load_optitrack_detailed(_P8)
    a = np.asarray(ang, float); a = a[np.isfinite(a)]
    head = float(np.median(a[:len(a) // 10]))
    assert head < 170.0 or "uncalibrated" in " ".join(quality.warnings).lower()


@pytest.mark.skipif(not os.path.exists(_P9), reason="real corpus not present")
def test_p9_left_trial3_no_longer_reports_an_impossible_excursion():
    """A0 418 deg at 97.3% coverage: arithmetically impossible for an interior
    knee angle, and invisible to every existing filter."""
    import numpy as np, pendulastic_pt_score as pt
    try:
        _t, ang, _q = pt.load_optitrack_detailed(_P9)
    except ValueError:
        return                    # a named refusal is an acceptable outcome
    p = pt.compute_pt_params(np.asarray(_t), np.asarray(ang))
    if p and p.get("A0_deg") is not None:
        assert p["A0_deg"] < 120.0, p["A0_deg"]
```

- [ ] **Step 2: Run it**

Run: `.venv/Scripts/python.exe -m pytest tests/test_optitrack_marker_angle.py -k "p8_left or p9_left" -v`
Expected: PASS (or skip if the corpus is absent on this machine).

- [ ] **Step 3: Measure the acceptance criteria**

Write and run `scratch_acceptance.py` (do NOT commit it) that walks every `OptiTrack_Recordings/**/*optitrack*.csv`, loads via `pt.load_optitrack_detailed`, and reports:
- count of trials whose settled tail median exceeds 170°, out of the total scored
- count of degenerate legs (`R2n` exactly 0 or `area_ratio >= 0.9`), and their MAS labels
- count of trials newly refused, each with its reason

Acceptance: trials above 170° falls from 20/214 toward the pre-bug 3/214; the 6 degenerate MAS-0 legs resolve; no trial that currently scores sanely becomes unscoreable without a named reason.

- [ ] **Step 4: Write the acceptance report**

Create `docs/reports/2026-08-31-knee-axis-acceptance.md` recording the measured before/after for each criterion, the list of newly-refused trials with reasons, and — if any criterion is not met — say so plainly rather than reframing the target. A partial result honestly reported is the deliverable; a met target claimed loosely is not.

- [ ] **Step 5: Commit**

```bash
git commit -F - -- tests/test_optitrack_marker_angle.py docs/reports/2026-08-31-knee-axis-acceptance.md <<'EOF'
test(optitrack): pin the two real trials that exposed the seed bug, and record acceptance

P8 Left trial_2 must stop claiming a fully extended resting leg; P9 Left
trial_3 must stop reporting an arithmetically impossible 418 deg excursion.
Both skip cleanly when the corpus is absent, so the suite still runs on a
checkout without data.

The acceptance report records the measured before/after on the full corpus:
trials settling above 170 deg, degenerate legs and their MAS labels, and every
newly-refused trial with its reason.
EOF
```

---

## Self-Review

**Spec coverage.** Geometry auto-detection by planar extent → Task 2. Five primitives → Tasks 3, 4, 6, 8. Three conditioning outcomes with the Welch metric → Task 5. `KneeAngleResult` with no `.angles` → Task 7. Loader keeps its contract, flags in `TrialQuality` → Task 11. Synthetic generator cases → Task 1. Properties (invariance, non-folding, transients, reachability, real-corpus) → Tasks 6, 10, 12. Acceptance criteria → Task 12. No spec section is unimplemented.

**Type consistency.** `hinge_axis` returns a 3-tuple everywhere it is used (Tasks 4, 9). `conditioning_verdict(conditioning, pc2, fps)` has one signature (Tasks 5, 9, 10). `KneeAngleResult` field names match between definition (Task 7) and construction (Task 9). `classify_clusters` returns `(triangle, bar, which)` in Tasks 2 and 9.

**Known risk, stated rather than hidden.** Task 9's orchestrator test asserts swept angle within 15% of truth. If the bar's 14.8° mounting offset interacts with the hinge-plane projection more than expected, that tolerance may need widening — but widen it only with a measured reason recorded in the test, never to make a red test green.

## Out of scope

Re-deriving the excursion threshold, revisiting PT7's zones, and recruiting for severity all depend on these angles and come after this lands on validated data.
