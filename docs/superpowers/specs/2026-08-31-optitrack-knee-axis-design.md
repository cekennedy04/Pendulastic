# Pose-free knee angle from labeled markers

**2026-08-31.** Replaces the seeded reconstruction in
`pendulastic_pt_score._angle_from_labeled_markers`, which anchors the zero to an
assumed pose and cannot be contradicted by the data.

## The defect

Two failures, and the second is why the first stayed invisible.

**D1 — the seed window is assumed, not detected.** The anatomical axis is seeded
from the *first 60 frames*, assumed to be the extended hold.

**D2 — 180 degrees is unfalsifiable.** The seed sets `axis_thigh = -axis_shank`,
so the seed frame reads exactly 180 by construction, and the angle is an
unsigned `arccos`, which folds at 180 instead of running past it. A trial that
starts at rest or mid-motion therefore anchors "straight" to a flexed pose and
still reports a convincing 179.9 baseline.

Confirmed on video: P8 Left trial_2 has the leg hanging flexed for the whole
recording with nobody holding it, and reports head 179.9 / tail 179.6 / A0 8.5.
P9 Left trial_3 starts mid-motion and reports A0 418.1 deg at 97.3% coverage.

Blast radius: 20/214 trials settle above 170 deg against 3/214 before, and 6
legs go degenerate against 1 — all six MAS-0, i.e. biased in the direction that
erases group separation.

## Why the obvious fixes were rejected

**Detect a better seed window.** Tried and rejected: seeding from the calm
window of maximum thigh-to-shank centroid separation does not discriminate
(P8: 310.4 mm at frames 0-59 against a 313.6 mm whole-trial maximum, where a
180->130 flexion should move roughly 30 mm). It also leaves D2 untouched — a
better-chosen seed is still unfalsifiable.

**A functional (SARA / axis-of-rotation) knee axis.** Infeasible on this data.
It needs full 3-DOF orientation of *both* segments, and measured across 254
trials:

| geometry | trials |
|---|---|
| shank triangle / thigh bar | 239 |
| shank bar / thigh triangle | 15 |
| **both non-collinear** | **0** |

Thigh out-of-line extent is a median 1.5 mm against the shank's 24.5 mm. In
every trial at least one segment is a bar whose roll is unobservable, so the
relative orientation the method needs never exists.

## The approach

Take what the rig actually observes, and stop asserting the rest.

**A bar still observes its line direction.** Measured on 40 bar-thigh trials,
that line sits a median 14.8 deg off the limb axis, 39/40 within 30 deg. Because
the bar is strapped rigidly, that offset is *constant per trial*.

**Every scored PT parameter is offset-invariant.** `A0` and `A1` are
differences, `R2n` and `phi_max_ratio` are ratios of differences, `omega_*` are
derivatives, `f` is a frequency, `N` is a count, `area_ratio` integrates phi. So
a constant, unknown offset **does not block scoring**.

That is the load-bearing property of this design. It means `180 = extended`
demotes from an assumption the reconstruction depends on to a *presentation*
offset applied only when evidence supports it — and it lets trials with no clean
hold be scored rather than refused, which matters on a corpus this thin.

## Module

New module `optitrack_knee_axis.py`. `pendulastic_pt_score.py` is already about
3000 lines; this is a self-contained geometric problem with a testable
interface.

### Geometry auto-detection

Roles are detected inside the module, never passed in. Detection is on **planar
extent**, not marker count: the bars here are 3-marker clusters that are nearly
collinear (1.5 mm out-of-line over a 92 mm span), so counting markers would
misclassify every trial. The cluster whose second singular value clears
`MIN_CLUSTER_PLANAR_EXTENT_M` is the triangle; the other is the bar. This covers
the 15 reversed trials automatically.

### Primitives

1. **`segment_line_direction(bar)`** -> per-frame unit vector.
   SVD gives a line direction whose sign is arbitrary per frame, so sign
   continuity is **mandatory**: `if dot(cur, prev) < 0: cur = -cur`, carried
   from the previous accepted frame. This is done at the 3-D vector stage,
   before any scalar reduction.

2. **`hinge_axis(triangle)`** -> `(axis, conditioning)`.
   Principal direction of the frame-to-frame rotation increments. Pose-free.
   `conditioning` is the dominant eigenvalue's share of the total,
   `w[0] / sum(w)`.

3. **`segment_axis_from_plate(triangle, hinge)`** -> per-frame unit vector in
   the plane perpendicular to `hinge`, carried by the plate's measured rotation.

4. **`signed_knee_angle(thigh_dir, shank_dir, hinge)`** -> continuous angle.
   `atan2` about the hinge axis, then unwrapped. **Cannot fold at 180.** Sign
   continuity is already established on the input vectors (1), so the 1-D
   unwrap is belt-and-braces rather than the primary defence.

5. **`anchor_to_extension(angles, hold_window)`** -> offset, or `None`.
   Cosmetic only. Applied when a hold is detected *and* passes the confidence
   bound below.

### Conditioning: three outcomes, not two

A single conditioning cut would refuse 9 of 30 measured trials, and **2 of
those 9 are genuine out-of-plane limb motion, not noise** — biased toward
trials with unusual movement, which is where spasticity lives. That is the same
one-sided bias the seed bug already inflicted, so it gets its own branch.

| conditioning | PC2 character | outcome |
|---|---|---|
| >= 0.90 | — | `ok` |
| < 0.90 | high-frequency | refuse: `ill_conditioned_axis` |
| < 0.90 | low-frequency | emit + flag `out_of_plane_motion` |

**The spectral metric, defined exactly.** Welch PSD of the PC2 increment series,
Hann window, `nperseg = min(256, len(pc2))`. Short series suffer *spectral
leakage and poor frequency resolution* — not aliasing, which happens at
acquisition — so windowing plus a minimum-duration floor of 240 tracked frames
(2 s at the measured 120 Hz) is the mitigation.

```
low_freq_ratio = sum(P[f < LOW_FREQ_CUTOFF_HZ]) / sum(P)
```

`LOW_FREQ_CUTOFF_HZ = 6.0`, `OUT_OF_PLANE_MIN_LF_RATIO = 0.50`.

**Why 6 Hz and not the swing frequency.** PC2 is a *differenced* series
(frame-to-frame increments), and differencing shifts energy upward, so the
informative band sits above the 0.5-1.5 Hz swing. Measured, on the 9
poorly-conditioned trials:

| cutoff | poorly-conditioned range | well-conditioned median |
|---|---|---|
| 1.5 Hz | 0.02 - 0.11 | 0.03 |
| 2.5 Hz | 0.04 - 0.34 | 0.06 |
| 4.0 Hz | 0.05 - 0.48 | 0.12 |
| **6.0 Hz** | **0.08 - 0.71** | 0.17 |

At 6 Hz the two genuine out-of-plane trials (0.71, 0.54) clear the rest
(0.08-0.41). **A cutoff of 2.5 Hz with a 0.60 threshold was considered and
rejected: nothing reaches 0.60 there, so the branch would be dead code** — the
same defect as a merge window sitting inside an already-guaranteed separation.

**The threshold is PROVISIONAL.** It rests on 2 positive examples. It must not
be quoted as validated, and it must be re-derived on a larger set. Because 2
examples cannot keep a branch honest, a synthetic test with dialled-in
out-of-plane motion is **required**, so the branch is provably reachable
independently of how many real trials happen to trip it.

**Amplitude is under-reported when this fires.** Projecting a non-sagittal
rotation onto a single hinge axis scales excursion by roughly
`cos(theta_out_of_plane)`. `TrialQuality` therefore carries
`OUT_OF_PLANE_AMPLITUDE_UNDERREPORTED`, and `phi_max` / `R2n` are to be read as
lower bounds. The out-of-plane angle is *estimated and reported* from the
PC2/PC1 energy ratio, but the score is **not** auto-corrected by it: correcting
against an unvalidated model is how a fabricated number becomes a trusted one.

### Result object

`KneeAngleResult` is returned by the module and carries `is_calibrated`,
`offset_deg`, `conditioning`, `low_freq_ratio`, and `flags`.

There is deliberately **no `.angles` attribute**. Access is through named
accessors, so a consumer cannot reach an absolute angle without saying so:

- `get_relative_angles()` — always succeeds. Baseline-subtracted; this is what
  scoring uses, and it is valid regardless of calibration.
- `get_absolute_angles()` — raises `UncalibratedOffsetError` when
  `is_calibrated` is false or `low_confidence_hold` is set.
- `raw_angles` — explicit escape hatch, named so its use is visible in a diff.

Magic-method overrides (`__getitem__`, `__array__`) are deliberately **not**
used. They break slicing and iteration in surprising ways, and they cannot
protect the real boundary anyway: `load_optitrack_detailed` must keep returning
`(t, angle, TrialQuality)` for its existing consumers, so at that seam the
protection is the `TrialQuality` flag. Removing the innocuous-looking `.angles`
name achieves the intent without the surprises.

**Who actually needs absolute angles.** PT7 and the MAS mapping are
offset-invariant and therefore safe. The real absolute consumers are display,
`pre_release_deg`, and range-of-motion reporting.

### Hold detection and confidence

A candidate hold is a window that is calm *and* geometrically extended (thigh
line near-parallel to shank axis). Patient shift during the hold produces slow
drift rather than a clean step, so the window carries a variance bound: if the
angle's standard deviation across the candidate hold exceeds
`MAX_HOLD_SD_DEG = 2.0`, it is flagged `low_confidence_hold` and the offset is
**not** applied. The relative path is unaffected — which is cheap precisely
because the offset is not load-bearing.

## Error handling

Every refusal is named, in the shape of the existing coverage gate: an
explanatory `ValueError` for unreadable, or NaN plus a `TrialQuality` flag.

| flag | meaning | angles emitted |
|---|---|---|
| `ill_conditioned_axis` | hinge unrecoverable, jitter-dominated | no |
| `out_of_plane_motion` | real non-sagittal motion, amplitude under-read | yes, flagged |
| `uncalibrated_offset` | no qualifying hold; relative only | yes, relative |
| `low_confidence_hold` | hold found but drifting; offset withheld | yes, relative |

## Testing

The existing suite cannot catch this bug: `_build_trial` always starts with a
held, extended leg. The synthetic generator is extended to emit trials with
known ground truth for each failure mode.

**Generator cases:** starts at rest; starts mid-motion; drifting hold;
sign-flipping bar; out-of-plane swing; near-zero excursion; **10-frame total
marker blackout at swing release**; **1-frame marker index swap on the bar at
peak velocity** (Motive is documented to permute Marker1/2/3 between frames, so
this is a real transient, not a hypothetical).

**Properties pinned:**

1. **Offset invariance.** Inject an arbitrary constant offset; every scored PT
   parameter must be unchanged. The whole design rests on this claim, so it is
   tested rather than argued.
2. **No folding.** A trial crossing 180 must produce a continuous curve where
   today's `arccos` mirrors.
3. **No spike on transients.** The blackout and index-swap cases must not
   produce a 180-degree spike — this is what the vector-stage continuity check
   exists for.
4. **Branch reachability.** The dialled-in out-of-plane case must actually set
   `out_of_plane_motion`, so the branch cannot be dead code.
5. **Real-corpus regression.** P8 Left trial_2 must stop reporting head 179.9;
   P9 Left trial_3 must stop reporting A0 418.

## Acceptance criteria

Re-run the 254-trial corpus and compare against the current reconstruction:

- trials settling above 170 deg falls from 20/214 toward the pre-bug 3/214
- the 6 degenerate MAS-0 legs resolve
- no trial that currently scores sanely becomes unscoreable without a named
  reason

## Explicitly out of scope

Re-deriving the excursion threshold and revisiting PT7's zones both depend on
these angles and come **after** this lands on validated data. Recruiting for
severity is downstream of both.
