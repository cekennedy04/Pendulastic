"""
capture_coverage.py
===================
Tell the operator that a trial is losing marker tracking WHILE the participant
is still on the plinth, instead of an analyst finding out a week later.

Why this exists
---------------
Measured across 219 trials with a meaningful dropout (2026-08-31): when the
labelled markers go missing, the cameras are seeing **0.21 of the 6 markers per
frame**, and unlabelled detections are 0.02 per frame. The markers are not
mislabelled, they are unseen -- so nothing downstream recovers them. Re-exporting
the .tak does not, re-labelling does not, and relaxing the reconstruction to 2
markers of 3 does not (per-marker tracking is 74.1% against 72.6% for all six:
they drop together, not independently).

That makes this a capture-time problem with no analysis-time cure, and it is
widespread rather than exceptional. The longest run of consecutive frames with
all six markers tracked is a median of **33 frames -- 0.3 seconds** -- across
253 trials, and **150 of them never manage half a second**. P22's left leg lost
the shank for its entire pre-release hold and had to be excluded outright.

The one thing ruled IN by the evidence is line-of-sight occlusion by the
assessor, who stands over the limb holding the ankle. Dropouts happen with the
shank NEARER the middle of the tracked volume than average (0.290 m against
0.348 m), so it is not the edge of the capture volume, and all six markers go at
once, so it is not marker-specific. That is worth knowing because it says what
fixes it: where the assessor stands and where the cameras look, not the tape.

What this module is
-------------------
Pure decision logic over a stream of per-frame tracking flags. It does no I/O
and knows nothing about NatNet, so it can be tested without a Motive server --
which matters, because the failure it guards against is only reproducible with
real hardware in the room.

Two uses, one core:

* `preflight` -- watch for a few seconds with the leg held in the start position
  and the assessor in their real holding posture, then say whether this setup
  can record a usable trial. Run it before the first trial of a session.
* `live` -- the same statistics over a rolling window during a recording, so a
  trial that goes bad is visible immediately and can be repeated.
"""
from __future__ import annotations

from typing import NamedTuple, Optional

# A usable trial needs the markers held continuously through the pre-release
# hold, because that is where the anatomical reference comes from. One second
# is deliberately modest: it is well under a real hold, and the corpus median
# longest run is 0.3 s, so this is a low bar that most existing trials would
# still have failed. It is set to catch a broken SETUP, not to grade technique.
PREFLIGHT_MIN_CONTINUOUS_S = 1.0

# Over the whole watch window, how much of the time both clusters must be seen.
# A leg held still inside a working volume should be at or near 100%; 0.95
# leaves room for a dropped frame without excusing a flicker.
PREFLIGHT_MIN_COVERAGE = 0.95

# How long to watch. Long enough for the assessor to settle into the posture
# they will actually hold, short enough that nobody skips the check.
PREFLIGHT_WATCH_S = 5.0

# Rolling window for the live indicator during a recording.
LIVE_WINDOW_S = 3.0

PASS = "pass"
FAIL = "fail"
NO_DATA = "no_data"


class CoverageStats(NamedTuple):
    """What was seen over a window of frames.

    `longest_continuous_s` is the load-bearing number, not `coverage`. A trial
    can be 70% covered and still useless if that 70% arrives as isolated frames:
    every reconstruction step that carries continuity -- marker-permutation
    matching, axis-step limits, the sign of a collinear cluster's line -- needs
    consecutive frames, and compares against a previous frame that may be far
    away when tracking flickers.
    """
    n_frames: int
    duration_s: float
    coverage: float               # fraction of frames with BOTH clusters tracked
    longest_continuous_s: float
    thigh_coverage: float
    shank_coverage: float


class Verdict(NamedTuple):
    status: str                   # PASS / FAIL / NO_DATA
    headline: str                 # one line, for a status bar
    detail: str                   # what to change, for a dialog
    stats: Optional[CoverageStats]


class CoverageMonitor:
    """Accumulates per-frame tracking flags and reports coverage statistics.

    Frames are pushed in by whatever is reading the mocap stream; this class
    never reads a socket itself. `window_s` keeps only the most recent frames,
    for the live indicator; leave it None to accumulate everything, for a
    pre-flight check over a fixed watch.
    """

    def __init__(self, window_s: Optional[float] = None):
        self.window_s = window_s
        self._samples: list = []          # (t, thigh_ok, shank_ok)

    def reset(self) -> None:
        self._samples.clear()

    def feed(self, t: float, thigh_tracked: bool, shank_tracked: bool) -> None:
        """Record one frame. `t` is seconds, monotonic within a session."""
        self._samples.append((float(t), bool(thigh_tracked), bool(shank_tracked)))
        if self.window_s is not None:
            cutoff = self._samples[-1][0] - self.window_s
            drop = 0
            for sample in self._samples:
                if sample[0] >= cutoff:
                    break
                drop += 1
            if drop:
                del self._samples[:drop]

    def stats(self) -> Optional[CoverageStats]:
        if len(self._samples) < 2:
            return None
        times = [s[0] for s in self._samples]
        duration = times[-1] - times[0]
        both = [s[1] and s[2] for s in self._samples]

        # Longest continuous stretch, measured in TIME rather than frames, so
        # the threshold means the same thing if the rig is ever run at another
        # rate.
        longest = 0.0
        run_start = None
        for i, ok in enumerate(both):
            if ok and run_start is None:
                run_start = times[i]
            elif not ok and run_start is not None:
                longest = max(longest, times[i - 1] - run_start)
                run_start = None
        if run_start is not None:
            longest = max(longest, times[-1] - run_start)

        n = len(self._samples)
        return CoverageStats(
            n_frames=n,
            duration_s=duration,
            coverage=sum(both) / n,
            longest_continuous_s=longest,
            thigh_coverage=sum(1 for s in self._samples if s[1]) / n,
            shank_coverage=sum(1 for s in self._samples if s[2]) / n,
        )


def _worst_segment(stats: CoverageStats) -> str:
    """Which cluster is being lost, so the operator knows where to look.

    Named explicitly because the fix differs: a shank lost through the swing is
    usually the assessor's arm, while a thigh lost while the leg is still is
    usually the assessor's torso.
    """
    if stats.shank_coverage < stats.thigh_coverage - 0.05:
        return "shank"
    if stats.thigh_coverage < stats.shank_coverage - 0.05:
        return "thigh"
    return "both"


def verdict(stats: Optional[CoverageStats],
            min_continuous_s: float = PREFLIGHT_MIN_CONTINUOUS_S,
            min_coverage: float = PREFLIGHT_MIN_COVERAGE) -> Verdict:
    """Turn coverage statistics into a pass/fail and something to do about it."""
    if stats is None or stats.n_frames < 2:
        return Verdict(
            NO_DATA,
            "No mocap frames received",
            "Nothing arrived from Motive. Check that Motive is streaming "
            "(View > Data Streaming, Broadcast Frame Data on) and that the "
            "Thigh and Shank rigid bodies exist in the current asset list.",
            stats)

    seg = _worst_segment(stats)
    if stats.longest_continuous_s < min_continuous_s:
        return Verdict(
            FAIL,
            f"Tracking is breaking up ({stats.longest_continuous_s:.2f}s "
            f"unbroken, need {min_continuous_s:.1f}s)",
            f"The {seg} markers are visible only in short bursts, so no part of "
            f"the hold can be reconstructed. Longest unbroken stretch was "
            f"{stats.longest_continuous_s:.2f}s out of {stats.duration_s:.1f}s "
            f"watched (thigh seen {stats.thigh_coverage * 100:.0f}% of frames, "
            f"shank {stats.shank_coverage * 100:.0f}%).\n\n"
            "This is almost always the assessor standing between the cameras "
            "and the leg. Step to the side of the limb rather than over it, and "
            "check no camera's view of the markers passes through your torso or "
            "arms. Recording now will produce a trial that cannot be scored.",
            stats)

    if stats.coverage < min_coverage:
        return Verdict(
            FAIL,
            f"Marker coverage {stats.coverage * 100:.0f}% "
            f"(need {min_coverage * 100:.0f}%)",
            f"Tracking holds for {stats.longest_continuous_s:.2f}s at a time but "
            f"drops repeatedly: both clusters were seen in only "
            f"{stats.coverage * 100:.0f}% of frames over {stats.duration_s:.1f}s "
            f"(thigh {stats.thigh_coverage * 100:.0f}%, shank "
            f"{stats.shank_coverage * 100:.0f}%). The {seg} markers are the ones "
            "being lost. Adjust the assessor's position or add a camera "
            "covering the swing arc before recording.",
            stats)

    return Verdict(
        PASS,
        f"Coverage OK ({stats.coverage * 100:.0f}%, "
        f"{stats.longest_continuous_s:.1f}s unbroken)",
        f"Both clusters tracked in {stats.coverage * 100:.0f}% of "
        f"{stats.n_frames} frames over {stats.duration_s:.1f}s, with an unbroken "
        f"stretch of {stats.longest_continuous_s:.1f}s. Safe to record.",
        stats)


def rigid_bodies_tracked(frame, thigh_id: int, shank_id: int):
    """(thigh_tracked, shank_tracked) from a natnet_client.MocapFrame.

    A rigid body absent from the frame counts as not tracked, which is what
    Motive means by omitting it.
    """
    bodies = getattr(frame, "rigid_bodies", None) or {}
    thigh = bodies.get(thigh_id)
    shank = bodies.get(shank_id)
    return (bool(thigh is not None and thigh.tracking_valid),
            bool(shank is not None and shank.tracking_valid))
