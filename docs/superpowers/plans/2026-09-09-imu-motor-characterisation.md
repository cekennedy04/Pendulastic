# IMU Motor Characterisation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Measure the phone IMU pipeline against a motor-driven encoder reference, so A0 accuracy, tail drift, the excursion floor and the analysis grid are settled against known truth instead of against OptiTrack.

**Architecture:** A shared analytic trajectory definition produces both what LabVIEW commands and what the analysis expects, so any disagreement is measurement rather than transcription. Three sync pulses precede each swing and are the only alignment mechanism — no cross-correlation. Analysis joins the encoder log to a normally-recorded phone trial and scores both grids and both detrend conventions against truth.

**Tech Stack:** Python 3, numpy, pytest. Reuses `pendulastic_pt_score.compute_pt_params`, `imu_calibration_tuner.replay_trial`, `rmse_pipeline_common.reconstruct_trial`. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-09-09-imu-motor-characterisation-design.md`

## Global Constraints

- Position control only. Nothing in this plan emulates physics; the neuromechanical model is separate later work.
- The phone records through the **unmodified** production path. No bench-only build.
- Alignment is by sync-pulse edges only. Cross-correlation is forbidden: the existing IMU/OptiTrack path carries a ~0.7 s NatNet skew and correlation on a near-periodic signal aliases by nearly one cycle.
- Angles in **degrees** at every public boundary; radians only inside a function.
- Encoder is truth. Where a comparison has a direction, it is `imu - truth`.
- Every trial is scored four ways: {20 Hz, 100 Hz} x {detrend False, detrend True}.
- New modules live at repo root, matching this codebase's existing convention for analysis modules.
- Run Python as `/c/Users/cladi/miniconda3/python.exe` (not on PATH).

---

### Task 1: The analytic trajectory

**Files:**
- Create: `motor_reference.py`
- Test: `tests/test_motor_reference.py`

**Interfaces:**
- Consumes: nothing
- Produces: `swing(a0_deg, f_hz, tau_s, duration_s, fs_hz) -> (t, theta_deg)`; constants `PULSE_COUNT = 3`, `PULSE_DEG = 5.0`, `PULSE_WIDTH_S = 0.08`, `PULSE_GAP_S = 0.25`, `PRE_ROLL_S = 1.0`, `LEAD_S = 0.2`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_motor_reference.py
import os, sys
import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import motor_reference as mr


def test_the_swing_starts_at_its_commanded_amplitude():
    t, th = mr.swing(a0_deg=45.0, f_hz=0.9, tau_s=1.6, duration_s=10.0, fs_hz=1000.0)
    assert th[0] == pytest.approx(45.0, abs=1e-9)
    assert len(t) == len(th)
    assert t[0] == 0.0


def test_the_swing_decays_toward_zero():
    _t, th = mr.swing(a0_deg=45.0, f_hz=0.9, tau_s=1.6, duration_s=10.0, fs_hz=1000.0)
    assert abs(th[-1]) < 1.0, "a 10 s trace at tau=1.6 should have settled"
    assert np.max(np.abs(th[:1000])) > np.max(np.abs(th[-1000:]))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/c/Users/cladi/miniconda3/python.exe -m pytest tests/test_motor_reference.py -q`
Expected: FAIL, `ModuleNotFoundError: No module named 'motor_reference'`

- [ ] **Step 3: Write minimal implementation**

```python
# motor_reference.py
"""The trajectory the motor commands and the analysis expects.

ONE definition, shared. LabVIEW exports from here and the analysis compares
against here, so a disagreement between them is a measurement difference
rather than a transcription error -- which is the failure mode that has cost
this project the most time.
"""
from __future__ import annotations

import numpy as np

#: Sync preamble. Three pulses, because two cannot distinguish a missed
#: leading edge from a late start and one cannot be checked at all.
PULSE_COUNT = 3
PULSE_DEG = 5.0
PULSE_WIDTH_S = 0.08
PULSE_GAP_S = 0.25
PRE_ROLL_S = 1.0
#: Still time BEFORE the first pulse. Without it the trace begins at full
#: pulse height, so pulse one has no rising edge to detect -- an edge counter
#: sees two pulses, not three. It is also what the motor physically does.
LEAD_S = 0.2


def swing(a0_deg, f_hz, tau_s, duration_s=12.0, fs_hz=1000.0):
    """A decaying cosine: theta(t) = a0 * exp(-t/tau) * cos(2*pi*f*t).

    Returns (t_s, theta_deg) with t starting at 0.
    """
    n = int(round(duration_s * fs_hz))
    t = np.arange(n, dtype=float) / fs_hz
    theta = a0_deg * np.exp(-t / tau_s) * np.cos(2.0 * np.pi * f_hz * t)
    return t, theta
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/c/Users/cladi/miniconda3/python.exe -m pytest tests/test_motor_reference.py -q`
Expected: PASS, 2 passed

- [ ] **Step 5: Commit**

```bash
git add motor_reference.py tests/test_motor_reference.py
git commit -m "feat(bench): the analytic trajectory the motor and the analysis share"
```

---

### Task 2: The sync preamble and the full commanded trace

**Files:**
- Modify: `motor_reference.py`
- Test: `tests/test_motor_reference.py`

**Interfaces:**
- Consumes: `swing` from Task 1
- Produces: `preamble(fs_hz) -> (t, theta_deg)`; `full_trace(a0_deg, f_hz, tau_s, duration_s, fs_hz) -> (t, theta_deg, t_release_s)`

- [ ] **Step 1: Write the failing test**

```python
def test_the_preamble_has_exactly_three_pulses():
    t, th = mr.preamble(fs_hz=1000.0)
    # A pulse is a contiguous run above half height.
    above = th > (mr.PULSE_DEG / 2.0)
    edges = np.diff(above.astype(int))
    assert (edges == 1).sum() == mr.PULSE_COUNT
    assert (edges == -1).sum() == mr.PULSE_COUNT


def test_the_full_trace_reports_where_the_swing_begins():
    t, th, t_rel = mr.full_trace(a0_deg=45.0, f_hz=0.9, tau_s=1.6,
                                 duration_s=10.0, fs_hz=1000.0)
    i = int(round(t_rel * 1000.0))
    assert th[i] == pytest.approx(45.0, abs=1e-6), (
        "the swing must start at full amplitude at the reported release time")
    assert np.all(np.abs(th[:i - 1]) <= mr.PULSE_DEG + 1e-9), (
        "nothing before release may exceed the pulse height")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/c/Users/cladi/miniconda3/python.exe -m pytest tests/test_motor_reference.py -q`
Expected: FAIL, `AttributeError: module 'motor_reference' has no attribute 'preamble'`

- [ ] **Step 3: Write minimal implementation**

```python
def preamble(fs_hz=1000.0):
    """PULSE_COUNT square pulses, then a still pre-roll.

    Square rather than smooth on purpose: the alignment is taken from the
    edge, and a sharp edge is what both the encoder and the gyro resolve
    identically. The motor is in position control, so the pulse is small
    enough not to need current the drive cannot deliver.
    """
    seg = [np.zeros(int(round(LEAD_S * fs_hz)))]
    for _ in range(PULSE_COUNT):
        seg.append(np.full(int(round(PULSE_WIDTH_S * fs_hz)), PULSE_DEG))
        seg.append(np.zeros(int(round(PULSE_GAP_S * fs_hz))))
    seg.append(np.zeros(int(round(PRE_ROLL_S * fs_hz))))
    theta = np.concatenate(seg)
    t = np.arange(len(theta), dtype=float) / fs_hz
    return t, theta


def full_trace(a0_deg, f_hz, tau_s, duration_s=12.0, fs_hz=1000.0):
    """Preamble then swing, as one trace. Returns (t, theta_deg, t_release_s)."""
    tp, thp = preamble(fs_hz)
    ts, ths = swing(a0_deg, f_hz, tau_s, duration_s, fs_hz)
    theta = np.concatenate([thp, ths])
    t = np.arange(len(theta), dtype=float) / fs_hz
    return t, theta, float(len(thp) / fs_hz)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/c/Users/cladi/miniconda3/python.exe -m pytest tests/test_motor_reference.py -q`
Expected: PASS, 4 passed

- [ ] **Step 5: Commit**

```bash
git add motor_reference.py tests/test_motor_reference.py
git commit -m "feat(bench): sync preamble and the full commanded trace"
```

---

### Task 3: The sweep matrix and the LabVIEW export

**Files:**
- Modify: `motor_reference.py`
- Test: `tests/test_motor_reference.py`

**Interfaces:**
- Consumes: `full_trace` from Task 2
- Produces: `sweep_matrix() -> list[dict]` with keys `run_id, a0_deg, f_hz, tau_s, repeat`; `export_trace(run, out_dir, fs_hz, duration_s=None) -> str`; `run_duration_s(run) -> float`

- [ ] **Step 1: Write the failing test**

```python
def test_the_sweep_is_dense_around_the_excursion_floor():
    """The floor is the point. 22, 25 and 28 must all be present, because the
    question is where extraction breaks relative to MIN_INTERPRETABLE_A0_DEG."""
    a0s = sorted({r["a0_deg"] for r in mr.sweep_matrix()})
    for v in (22.0, 25.0, 28.0):
        assert v in a0s, f"{v} deg missing from the sweep"


def test_every_run_is_repeated_and_uniquely_identified():
    runs = mr.sweep_matrix()
    ids = [r["run_id"] for r in runs]
    assert len(ids) == len(set(ids)), "run_id must be unique"
    assert {r["repeat"] for r in runs} == {1, 2, 3}
    assert 60 <= len(runs) <= 120, f"{len(runs)} runs is outside a bench day"


def test_the_export_round_trips_through_a_file(tmp_path):
    """The exported file must carry BOTH the sync pulse and the swing.

    Checked separately on purpose: one max over the whole file conflates them,
    and the swing is 18x the pulse height on the first run, so a dropped
    preamble would be invisible.
    """
    run = mr.sweep_matrix()[0]
    path = mr.export_trace(run, str(tmp_path), fs_hz=1000.0)
    rows = [l.split(",") for l in open(path, encoding="utf-8").read().splitlines()]
    assert rows[0] == ["t_s", "theta_deg"]

    t0, th0 = float(rows[1][0]), float(rows[1][1])
    assert t0 == 0.0 and th0 == pytest.approx(0.0), "the trace must start still"

    t_rel = mr.full_trace(run["a0_deg"], run["f_hz"], run["tau_s"], fs_hz=1000.0)[2]
    pre = [float(r[1]) for r in rows[1:] if float(r[0]) < t_rel]
    assert max(pre) == pytest.approx(mr.PULSE_DEG, abs=1e-6), (
        "the sync pulse must survive into the exported file")

    peak = max(abs(float(r[1])) for r in rows[1:])
    assert peak == pytest.approx(run["a0_deg"], abs=1e-6), (
        "the swing amplitude must survive into the exported file")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/c/Users/cladi/miniconda3/python.exe -m pytest tests/test_motor_reference.py -q`
Expected: FAIL, `AttributeError: module 'motor_reference' has no attribute 'sweep_matrix'`

- [ ] **Step 3: Write minimal implementation**

```python
import os

#: Amplitudes, dense around MIN_INTERPRETABLE_A0_DEG = 25.0. Finding where
#: extraction breaks relative to that floor is the experiment's main purpose.
SWEEP_A0_DEG = (90.0, 70.0, 50.0, 40.0, 30.0, 28.0, 25.0, 22.0, 20.0, 15.0, 10.0, 5.0)
#: Brackets HEALTHY_REF['f'] = 0.9137 Hz.
SWEEP_F_HZ = (0.6, 0.9, 1.2)
#: Roughly 2, 5 and 9 cycles to 3 deg from 45 -- spastic through healthy.
SWEEP_TAU_S = (0.5, 1.6, 3.2)
NOMINAL_F_HZ = 0.9
NOMINAL_TAU_S = 1.6
#: The three amplitudes at which f and tau are also varied.
CROSS_A0_DEG = (50.0, 25.0, 10.0)
REPEATS = 3


def sweep_matrix():
    """Every run of the bench session, in the order to run them.

    Full amplitude sweep at nominal f and tau, then f and tau varied at three
    amplitudes. Repeats are consecutive so a drifting bench shows up as
    within-run spread rather than as a trend across the whole session.
    """
    combos = [(a0, NOMINAL_F_HZ, NOMINAL_TAU_S) for a0 in SWEEP_A0_DEG]
    for a0 in CROSS_A0_DEG:
        for f in SWEEP_F_HZ:
            if f != NOMINAL_F_HZ:
                combos.append((a0, f, NOMINAL_TAU_S))
        for tau in SWEEP_TAU_S:
            if tau != NOMINAL_TAU_S:
                combos.append((a0, NOMINAL_F_HZ, tau))

    runs = []
    for a0, f, tau in combos:
        for rep in range(1, REPEATS + 1):
            runs.append({
                "run_id": f"a{a0:g}_f{f:g}_t{tau:g}_r{rep}",
                "a0_deg": a0, "f_hz": f, "tau_s": tau, "repeat": rep,
            })
    return runs


def run_duration_s(run):
    """How long this run's swing must last to be measurable.

    Long enough for the tail to settle (5 tau) plus two full periods to fit a
    drift slope across, with margin. A 12 s trial at tau=3.2 is only 3.75 time
    constants: the "settled" tail still carries live oscillation, and fitting
    it reports half a degree per second of drift that is not there.
    """
    return max(12.0, 5.0 * float(run["tau_s"]) + 4.0 / float(run["f_hz"]) + 2.0)


def export_trace(run, out_dir, fs_hz=1000.0, duration_s=None):
    """Write one run's commanded trace as CSV for LabVIEW to stream."""
    if duration_s is None:
        duration_s = run_duration_s(run)
    os.makedirs(out_dir, exist_ok=True)
    t, theta, _rel = full_trace(run["a0_deg"], run["f_hz"], run["tau_s"],
                                duration_s=duration_s, fs_hz=fs_hz)
    path = os.path.join(out_dir, f"{run['run_id']}.csv")
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write("t_s,theta_deg\n")
        for a, b in zip(t, theta):
            fh.write(f"{a:.6f},{b:.6f}\n")
    return path
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/c/Users/cladi/miniconda3/python.exe -m pytest tests/test_motor_reference.py -q`
Expected: PASS, 7 passed

- [ ] **Step 5: Commit**

```bash
git add motor_reference.py tests/test_motor_reference.py
git commit -m "feat(bench): the sweep matrix and its LabVIEW export"
```

---

### Task 4: Finding the sync pulses in a log

**Files:**
- Create: `motor_bench.py`
- Modify: `motor_reference.py` (add `pulse_search_window_s()`)
- Test: `tests/test_motor_bench.py`

**Interfaces:**
- Consumes: `motor_reference.PULSE_COUNT`, `PULSE_DEG`
- Produces: `find_pulse_edges(t, signal, expected=PULSE_COUNT, search_s=None) -> list[float]`; `SyncError`. Also adds `motor_reference.pulse_search_window_s()`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_motor_bench.py
import os, sys
import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import motor_bench as mb
import motor_reference as mr


def test_the_three_pulse_edges_are_found_in_a_clean_preamble():
    t, th = mr.preamble(fs_hz=1000.0)
    edges = mb.find_pulse_edges(t, th)
    assert len(edges) == 3
    assert edges[0] == pytest.approx(mr.LEAD_S, abs=0.005)
    gap = mr.PULSE_WIDTH_S + mr.PULSE_GAP_S
    assert edges[1] - edges[0] == pytest.approx(gap, abs=0.01)


def test_the_edges_survive_noise():
    """The gyro sees these through sensor noise, not as a clean square."""
    rng = np.random.default_rng(0)
    t, th = mr.preamble(fs_hz=1000.0)
    noisy = th + rng.normal(0.0, 0.3, size=len(th))
    assert len(mb.find_pulse_edges(t, noisy)) == 3


def test_too_few_pulses_raises_rather_than_guessing():
    """A missed pulse means the alignment is unknown. Guessing it is how a
    whole session becomes silently wrong.

    The pulse to erase is derived from the constants, not from a sample index:
    LEAD_S puts all three pulses inside the first 0.94 s of a 2.19 s preamble,
    so anything that zeroes "the back half" erases only stillness and the test
    would pass without ever exercising the refusal.
    """
    fs = 1000.0
    t, th = mr.preamble(fs_hz=fs)
    maimed = th.copy()
    start = int(mr.LEAD_S * fs)
    maimed[start:start + int(mr.PULSE_WIDTH_S * fs)] = 0.0
    assert (maimed > mr.PULSE_DEG / 2).sum() < (th > mr.PULSE_DEG / 2).sum(), (
        "the test must actually remove a pulse, or it proves nothing")
    with pytest.raises(mb.SyncError, match="expected 3"):
        mb.find_pulse_edges(t, maimed)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/c/Users/cladi/miniconda3/python.exe -m pytest tests/test_motor_bench.py -q`
Expected: FAIL, `ModuleNotFoundError: No module named 'motor_bench'`

- [ ] **Step 3: Write minimal implementation**

```python
# motor_bench.py
"""Join a motor encoder log to a phone trial and score the phone against truth.

The encoder is truth. Every comparison here is `imu - truth`, and alignment
comes from sync-pulse edges only -- never cross-correlation, which on a
near-periodic signal aliases onto a wrong-by-nearly-one-cycle lag.
"""
from __future__ import annotations

import numpy as np

import motor_reference as mr


class SyncError(ValueError):
    """Alignment could not be established. Never downgraded to a warning: an
    unaligned trial produces numbers that look fine and are not."""


def find_pulse_edges(t, signal, expected=mr.PULSE_COUNT, search_s=None):
    """Rising-edge times of the sync pulses, in seconds.

    Threshold at half the pulse height above the pre-pulse baseline, with
    hysteresis, so noise near the threshold cannot produce doubled edges.
    """
    t = np.asarray(t, dtype=float)
    x = np.asarray(signal, dtype=float)
    ok = np.isfinite(t) & np.isfinite(x)
    t, x = t[ok], x[ok]
    if len(t) < 10:
        raise SyncError(f"only {len(t)} finite samples; expected {expected} pulses")

    # Bound the search to the preamble BEFORE measuring anything.
    #
    # Without this the thresholds are derived from the whole trace, whose
    # largest excursion is the swing, not the pulse. On a real run that means
    # span = 45 deg, hi = 27 deg, and the 5 deg pulses never cross it: the
    # function found ONE edge (the swing) on every full trial and refused.
    # Isolated-preamble tests could not see it, because there the swing is
    # absent and span IS the pulse height.
    #
    # The window comes from the shared constants rather than from a magic
    # number, and baseline and span are computed INSIDE it -- which keeps the
    # detection scale-free, so it works on the encoder's degrees and on the
    # phone's reconstructed angle without assuming the two agree. Assuming they
    # agree is precisely what this harness exists to test.
    if search_s is None:
        search_s = mr.pulse_search_window_s()
    win = t <= (t[0] + search_s)
    if win.sum() < 10:
        raise SyncError(
            f"only {int(win.sum())} samples inside the {search_s:.2f} s preamble "
            f"window; expected {expected} pulses")
    tw, xw = t[win], x[win]

    baseline = float(np.median(xw[: max(1, int(len(xw) * 0.05))]))
    span = float(np.max(xw) - baseline)
    if span <= 0:
        raise SyncError(f"signal never rises; expected {expected} pulses")
    hi = baseline + 0.6 * span
    lo = baseline + 0.4 * span

    # armed starts False when the trace is ALREADY high at its first sample:
    # that is not a rising edge, it is a trace that began mid-pulse, and
    # recording t[0] as an edge would return the right COUNT with one bogus
    # member -- the one failure mode here that fails open instead of closed.
    edges, armed = [], not (xw[0] >= hi)
    for i in range(len(xw)):
        if armed and xw[i] >= hi:
            edges.append(float(tw[i]))
            armed = False
        elif not armed and xw[i] <= lo:
            armed = True
    if len(edges) != expected:
        raise SyncError(f"found {len(edges)} pulse edges, expected {expected}")
    return edges
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/c/Users/cladi/miniconda3/python.exe -m pytest tests/test_motor_bench.py -q`
Expected: PASS, 3 passed

Also add to `motor_reference.py`, beside `preamble()`:

```python
def pulse_search_window_s():
    """How long to look for sync pulses: past the last one, short of the swing.

    NOT the preamble's full length. That was tried and it is fs-dependent in a
    way that reproduces the bug it was meant to fix: this returns raw seconds
    while preamble() rounds each segment's sample count per fs, so at rates
    where the roundings do not cancel the real preamble is SHORTER than the
    nominal figure. Measured: exact at 1000/500/200/100 Hz, but -0.006 s at
    250 Hz and -0.012765 s at 104.72 Hz. There the window overran into the
    swing, span reinflated to the swing amplitude, and detection failed with
    "found 1 pulse edges". A phone samples near 100-200 Hz and irregularly, so
    this was going to bite on real data while every 1 kHz test passed.

    Ending halfway through PRE_ROLL_S instead puts the boundary in the middle
    of guaranteed stillness: the last pulse ends at 0.94 s, the swing starts at
    2.19 s, and this returns 1.69 s. Roughly 0.7 s of margin on each side, so
    rounding jitter of a few milliseconds cannot reach either edge at any
    plausible rate -- and it needs no assumption that the samples are evenly
    spaced, which a phone's are not.
    """
    return (LEAD_S
            + PULSE_COUNT * (PULSE_WIDTH_S + PULSE_GAP_S)
            + PRE_ROLL_S / 2.0)
```

And add these two regression tests to `tests/test_motor_bench.py`:

```python
def test_edges_are_found_on_a_full_trial_not_just_an_isolated_preamble():
    """The input align() actually passes. Deriving thresholds from the whole
    trace scales them to the 45 deg swing, so hi lands at 27 deg, the 5 deg
    pulses never cross it, and the function finds ONE edge and refuses every
    real trial. Isolated-preamble tests cannot see this: with no swing present,
    span IS the pulse height."""
    t, th, _rel = mr.full_trace(45.0, 0.9, 1.6, duration_s=12.0, fs_hz=1000.0)
    assert len(mb.find_pulse_edges(t, th)) == 3


def test_a_trace_that_starts_already_high_does_not_count_a_bogus_first_edge():
    """The one failure mode here that fails OPEN: recording t[0] as an edge
    returns the right COUNT with one bogus member, which no downstream check
    can catch.

    Constructed so it reaches the armed-flag guard. An earlier version simply
    cut into pulse 1, which made the first 5% of the window all pulse-height,
    so baseline == max, span == 0, and it exited through "signal never rises"
    -- a different path entirely. It passed while proving nothing.
    """
    fs = 1000.0
    _t, th = mr.preamble(fs_hz=fs)
    lead = np.full(30, mr.PULSE_DEG)            # starts high, but only briefly
    sig = np.concatenate([lead, th])
    t = np.arange(len(sig)) / fs
    # Baseline must still resolve low, or this exercises the wrong branch.
    win = t <= mr.pulse_search_window_s()
    assert np.median(sig[win][: int(win.sum() * 0.05)]) == 0.0, (
        "test setup is wrong: baseline is not low, so the armed guard is not reached")
    assert len(mb.find_pulse_edges(t, sig)) == 3, (
        "the leading high block must not be counted as a fourth edge")


@pytest.mark.parametrize("fs", [1000.0, 500.0, 250.0, 200.0, 104.72, 100.0, 60.0])
def test_detection_survives_sample_rates_that_are_not_1_khz(fs):
    """A phone samples near 100-200 Hz and irregularly, not at 1 kHz.

    Deriving the search window from the preamble's nominal length failed here:
    preamble() rounds each segment per fs, so the real preamble runs short at
    250 Hz (-0.006 s) and 104.72 Hz (-0.012765 s), the window overran into the
    swing, and detection failed with "found 1 pulse edges".
    """
    t, th, _rel = mr.full_trace(90.0, 0.9, 1.6, duration_s=12.0, fs_hz=fs)
    assert len(mb.find_pulse_edges(t, th)) == 3
```

- [ ] **Step 5: Commit**

```bash
git add motor_bench.py motor_reference.py tests/test_motor_bench.py
git commit -m "feat(bench): sync-pulse edge detection, which refuses rather than guesses"
```

---

### Task 5: Aligning the phone to the encoder

**Files:**
- Modify: `motor_bench.py`
- Test: `tests/test_motor_bench.py`

**Interfaces:**
- Consumes: `find_pulse_edges` from Task 4
- Produces: `align(enc_t, enc_theta, imu_t, imu_theta) -> dict` with keys `lag_s, pulse_offset_spread_s, clock_rate_error, enc_t, enc_theta, imu_t, imu_theta, t_release_s`

- [ ] **Step 1: Write the failing test**

```python
def _rig_pair(lag_s=0.37, a0=45.0, fs=1000.0):
    """An encoder trace and a phone trace of the same motion, offset in time."""
    t, th, rel = mr.full_trace(a0_deg=a0, f_hz=0.9, tau_s=1.6,
                               duration_s=8.0, fs_hz=fs)
    return t, th, t + lag_s, th.copy(), rel


def test_alignment_recovers_a_known_clock_offset():
    et, eth, it, ith, _rel = _rig_pair(lag_s=0.37)
    out = mb.align(et, eth, it, ith)
    assert out["lag_s"] == pytest.approx(0.37, abs=0.01)


def test_alignment_puts_the_two_traces_on_one_clock():
    et, eth, it, ith, _rel = _rig_pair(lag_s=-0.22)
    out = mb.align(et, eth, it, ith)
    n = min(len(out["enc_theta"]), len(out["imu_theta"]))
    diff = out["imu_theta"][:n] - out["enc_theta"][:n]
    assert np.nanmax(np.abs(diff)) < 0.5, "aligned traces should nearly coincide"


def test_the_clock_rate_error_is_reported_even_when_it_passes():
    """The spread bound only catches a 7.6% rate error over this lever arm, so
    a 2% clock -- 240 ms of drift across a 12 s trial -- passes. That is
    acceptable for a crystal-based phone clock, but the number must be VISIBLE
    rather than merely under a threshold, or nobody can tell a good sync from a
    barely-passing one."""
    et, eth, it, ith, _rel = _rig_pair(lag_s=0.0)
    out = mb.align(et, eth, it * 1.02 + 0.3, ith)
    assert out["clock_rate_error"] == pytest.approx(0.02, abs=0.005)
    assert out["pulse_offset_spread_s"] > 0.0


def test_release_time_is_reported_on_the_common_clock():
    et, eth, it, ith, rel = _rig_pair(lag_s=0.1)
    out = mb.align(et, eth, it, ith)
    assert out["t_release_s"] == pytest.approx(rel, abs=0.02)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/c/Users/cladi/miniconda3/python.exe -m pytest tests/test_motor_bench.py -q`
Expected: FAIL, `AttributeError: module 'motor_bench' has no attribute 'align'`

- [ ] **Step 3: Write minimal implementation**

```python
def align(enc_t, enc_theta, imu_t, imu_theta):
    """Put both traces on the encoder's clock using the pulse edges.

    Lag is the MEDIAN of the per-pulse offsets rather than the first: three
    edges give a spread, and a spread is the only evidence available that the
    two clocks are running at the same rate. A single edge cannot show that.
    """
    e_edges = find_pulse_edges(enc_t, enc_theta)
    i_edges = find_pulse_edges(imu_t, imu_theta)
    offsets = [i - e for i, e in zip(i_edges, e_edges)]
    lag = float(np.median(offsets))
    spread = float(np.max(offsets) - np.min(offsets))

    # Rate error, reported rather than merely bounded.
    #
    # KNOW WHAT THIS CAN AND CANNOT SEE. The pulses span 0.66 s, so a clock
    # running fast by r shows up here as spread r*0.66. The 50 ms bound below
    # therefore only catches r > 7.6%. Measured: a clock 2% fast passes, having
    # drifted 240 ms by the end of a 12 s trial. Three edges over two thirds of
    # a second are barely evidence about RATE at all.
    #
    # That is tolerable because a phone clock is crystal-based at 50-100 ppm,
    # which drifts 0.6-1.2 ms across a trial -- negligible against everything
    # else measured here -- and the realistic threat is a fixed OFFSET, which
    # this handles exactly. But the number is reported so a clock that does
    # misbehave is visible instead of silently passing. If it ever matters, the
    # fix is a second pulse burst at the END of the trial: a 12 s lever arm
    # makes the same three-edge test roughly 18x more sensitive.
    lever = float(e_edges[-1] - e_edges[0])
    rate_error = (spread / lever) if lever > 0 else float("nan")

    if spread > 0.05:
        raise SyncError(
            f"pulse offsets disagree by {spread:.3f} s over a {lever:.2f} s "
            f"lever arm ({100 * rate_error:.2f}% rate error); the two clocks "
            "are not running at the same rate")

    # Release is one preamble after the encoder's first edge.
    t_release = e_edges[0] + (mr.PULSE_COUNT * (mr.PULSE_WIDTH_S + mr.PULSE_GAP_S)
                              + mr.PRE_ROLL_S)
    return {
        "lag_s": lag,
        "pulse_offset_spread_s": spread,
        "clock_rate_error": rate_error,
        "enc_t": np.asarray(enc_t, dtype=float),
        "enc_theta": np.asarray(enc_theta, dtype=float),
        "imu_t": np.asarray(imu_t, dtype=float) - lag,
        "imu_theta": np.asarray(imu_theta, dtype=float),
        "t_release_s": float(t_release),
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/c/Users/cladi/miniconda3/python.exe -m pytest tests/test_motor_bench.py -q`
Expected: PASS, 6 passed

- [ ] **Step 5: Commit**

```bash
git add motor_bench.py tests/test_motor_bench.py
git commit -m "feat(bench): align phone to encoder on pulse edges, and check clock rate"
```

---

### Task 6: Scoring one trial against truth

**Files:**
- Modify: `motor_bench.py`
- Test: `tests/test_motor_bench.py`

**Interfaces:**
- Consumes: `align` from Task 5; `pendulastic_pt_score.compute_pt_params`
- Produces: `score_trial(aligned, run) -> dict` with keys `a0_true, a0_enc, a0_imu, a0_ratio, a0_error_deg, tail_drift_deg_s, rmse_deg`. Trims to `t >= aligned["t_release_s"]` first.

- [ ] **Step 1: Write the failing test**

```python
def test_a_perfect_phone_recovers_the_commanded_amplitude():
    """The ceiling case. If A0 is wrong when the phone trace IS the encoder
    trace, the defect is in extraction, not in the sensor."""
    et, eth, it, ith, _rel = _rig_pair(lag_s=0.0, a0=45.0)
    out = mb.score_trial(mb.align(et, eth, it, ith),
                         {"a0_deg": 45.0, "f_hz": 0.9, "tau_s": 1.6})
    assert out["a0_true"] == 45.0
    assert out["a0_ratio"] == pytest.approx(1.0, abs=0.15)
    assert abs(out["rmse_deg"]) < 0.5


def test_a_phone_reading_high_is_reported_as_a_ratio_above_one():
    et, eth, it, ith, _rel = _rig_pair(lag_s=0.0, a0=45.0)
    out = mb.score_trial(mb.align(et, eth, it * 1.0, ith * 1.5),
                         {"a0_deg": 45.0, "f_hz": 0.9, "tau_s": 1.6})
    assert out["a0_ratio"] > 1.2
    assert out["a0_error_deg"] > 0


def test_tail_drift_is_measured_not_assumed():
    """The corpus estimate is -0.735 deg/s from indirect evidence. Here it is
    a fitted slope on a trace whose true drift is known to be zero."""
    et, eth, it, ith, _rel = _rig_pair(lag_s=0.0, a0=45.0)
    drifted = ith + 0.4 * (it - it[0])
    out = mb.score_trial(mb.align(et, eth, it, drifted),
                         {"a0_deg": 45.0, "f_hz": 0.9, "tau_s": 1.6})
    assert out["tail_drift_deg_s"] == pytest.approx(0.4, abs=0.15)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/c/Users/cladi/miniconda3/python.exe -m pytest tests/test_motor_bench.py -q`
Expected: FAIL, `AttributeError: module 'motor_bench' has no attribute 'score_trial'`

- [ ] **Step 3: Write minimal implementation**

```python
import pendulastic_pt_score as pts


def _a0_of(t, theta):
    """A0 through the production extractor, or NaN.

    release_idx=0 is not optional. The caller has already trimmed to the
    release, and compute_pt_params runs its OWN release detection which needs
    the pre-release hold to find a baseline. Trimmed without being told, it
    mis-fires about 0.58 s late at the first trough and truncates A0 --
    measured at ratio 0.683 instead of 1.002, and varying with frequency and
    damping in a way that looks exactly like a real instrument defect. It is
    not one: told where release is, the extractor recovers a known amplitude to
    0.2% at every amplitude from 5 to 90 deg and at every waveform shape tested.
    """
    p = pts.compute_pt_params(np.asarray(t, float), np.asarray(theta, float),
                              release_idx=0)
    if not p:
        return float("nan")
    v = p.get("A0_deg")
    return float(v) if v is not None else float("nan")


def score_trial(aligned, run):
    """One trial against truth. Direction is always imu - truth.

    Both traces are trimmed to post-release BEFORE anything is extracted.
    compute_pt_params runs its own release detection, and a 5 deg square sync
    pulse is a sharper transient than the swing -- left in, it can anchor A0 to
    the preamble and return a plausible-looking wrong number.
    """
    rel = float(aligned["t_release_s"])
    et_all, eth_all = aligned["enc_t"], aligned["enc_theta"]
    it_all, ith_all = aligned["imu_t"], aligned["imu_theta"]
    e_keep, i_keep = et_all >= rel, it_all >= rel
    et, eth = et_all[e_keep], eth_all[e_keep]
    it, ith = it_all[i_keep], ith_all[i_keep]

    a0_true = float(run["a0_deg"])
    a0_enc = _a0_of(et, eth)
    a0_imu = _a0_of(it, ith)

    # RMSE on the encoder's grid, over the overlap only.
    lo, hi = max(et.min(), it.min()), min(et.max(), it.max())
    grid = et[(et >= lo) & (et <= hi)]
    ref = np.interp(grid, et, eth)
    test = np.interp(grid, it, ith)
    rmse = float(np.sqrt(np.mean((test - ref) ** 2))) if len(grid) > 2 else float("nan")

    # Tail drift: slope of the settled tail, or NaN when the trial is too
    # short to have one.
    #
    # The estimator is fine; the GATE is what matters. Measured on traces with
    # known drift: at tau=1.6 a last-quarter fit reports 0.301 deg/s on an 8 s
    # trace whose true drift is ZERO, and 0.005 on the same trace at 12 s. At
    # tau=3.2 it reports 0.557 at 12 s and -0.006 at 24 s. What it is fitting
    # in the short cases is residual oscillation, not drift -- roughly 1.8
    # cycles still alive inside the window -- and the sign flips with phase, so
    # no fixed window fraction is stable.
    #
    # A trial that cannot support the measurement reports nothing rather than a
    # phantom, which is the whole point: -0.735 deg/s is the corpus estimate
    # this bench exists to check, and a spurious 0.3 would sit right on top of it.
    settle_s = 5.0 * float(run["tau_s"])
    min_span_s = 2.0 / float(run["f_hz"])
    tail_mask = it >= (it[0] + settle_s)
    if tail_mask.sum() >= 10 and (it[-1] - it[0] - settle_s) >= min_span_s:
        drift = float(np.polyfit(it[tail_mask], ith[tail_mask], 1)[0])
    else:
        drift = float("nan")

    return {
        "a0_true": a0_true,
        "a0_enc": a0_enc,
        "a0_imu": a0_imu,
        "a0_ratio": (a0_imu / a0_true) if a0_true else float("nan"),
        "a0_error_deg": a0_imu - a0_true,
        "tail_drift_deg_s": drift,
        "rmse_deg": rmse,
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/c/Users/cladi/miniconda3/python.exe -m pytest tests/test_motor_bench.py -q`
Expected: PASS, 9 passed

- [ ] **Step 5: Commit**

```bash
git add motor_bench.py tests/test_motor_bench.py
git commit -m "feat(bench): score one trial against encoder truth"
```

---

### Task 7: The four-way grid and detrend comparison

**Files:**
- Modify: `motor_bench.py`
- Test: `tests/test_motor_bench.py`

**Interfaces:**
- Consumes: `score_trial` from Task 6; `imu_calibration_tuner.TICK_S`, `ANALYSIS_TICK_S`
- Produces: `score_four_ways(samples, aligned, run) -> dict` keyed `"20hz_detrend_false"`, `"20hz_detrend_true"`, `"100hz_detrend_false"`, `"100hz_detrend_true"`

- [ ] **Step 1: Write the failing test**

```python
def test_all_four_conventions_are_scored():
    """The phone ships detrend=False on a 100 Hz grid; analysis uses True. The
    flag moves phone PT7 by 0.363 against optical's 0.050, and nothing has ever
    said which is closer to TRUTH. This is where that gets answered."""
    et, eth, it, ith, _rel = _rig_pair(lag_s=0.0, a0=45.0)
    aligned = mb.align(et, eth, it, ith)
    out = mb.score_four_ways(None, aligned, {"a0_deg": 45.0, "f_hz": 0.9, "tau_s": 1.6})
    assert set(out) == {"20hz_detrend_false", "20hz_detrend_true",
                        "100hz_detrend_false", "100hz_detrend_true"}
    for key, res in out.items():
        # Asserting the KEY exists is not a test. An earlier version did
        # exactly that and passed while all four ratios were NaN.
        r = res["a0_ratio"]
        assert np.isfinite(r), f"{key} returned {r}"
        assert 0.9 < r < 1.1, f"{key} recovered {r:.3f} of a clean known amplitude"


def test_the_detrend_flag_changes_the_answer_on_a_drifting_trace():
    """On a clean trace the two conventions should nearly agree; on a drifting
    one they must not, or the comparison could not detect the defect it exists
    for."""
    et, eth, it, ith, _rel = _rig_pair(lag_s=0.0, a0=45.0)
    drifted = ith + 0.6 * (it - it[0])
    out = mb.score_four_ways(None, mb.align(et, eth, it, drifted),
                             {"a0_deg": 45.0, "f_hz": 0.9, "tau_s": 1.6})
    a = out["100hz_detrend_false"]["a0_imu"]
    b = out["100hz_detrend_true"]["a0_imu"]
    assert abs(a - b) > 1.0, "detrend should matter on a drifting trace"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/c/Users/cladi/miniconda3/python.exe -m pytest tests/test_motor_bench.py -q`
Expected: FAIL, `AttributeError: module 'motor_bench' has no attribute 'score_four_ways'`

- [ ] **Step 3: Write minimal implementation**

```python
def _a0_of_detrended(t, theta, detrend):
    """A0 under one detrend convention. Same release_idx=0 contract as _a0_of:
    the CALLER must have trimmed to the release already."""
    p = pts.compute_pt_params(np.asarray(t, float), np.asarray(theta, float),
                              release_idx=0, detrend=detrend)
    if not p:
        return float("nan")
    v = p.get("A0_deg")
    return float(v) if v is not None else float("nan")


def score_four_ways(samples, aligned, run):
    """Score the trial on both grids and under both detrend conventions.

    `samples` is the raw component reconstruction when a real phone log is
    available, in which case each grid is replayed from raw. When it is None
    (a synthetic trial) the aligned trace is resampled instead, which exercises
    the same comparison without needing an AHRS replay.
    """
    import imu_calibration_tuner as tuner

    out = {}
    for grid_name, tick in (("20hz", tuner.TICK_S), ("100hz", tuner.ANALYSIS_TICK_S)):
        if samples is not None:
            t_g, th_g = tuner.replay_trial(samples, _REPLAY_CONFIG(), tick_s=tick)
            t_g = np.asarray(t_g, float) - aligned["lag_s"]
            th_g = np.asarray(th_g, float)
        else:
            t_src, th_src = aligned["imu_t"], aligned["imu_theta"]
            t_g = np.arange(t_src.min(), t_src.max(), tick)
            th_g = np.interp(t_g, t_src, th_src)

        # Trim to the release BEFORE re-extracting. score_trial does this
        # internally, but the line below OVERWRITES its a0_imu, so the trim has
        # to happen here too. Without it _a0_of_detrended gets an untrimmed
        # array while passing release_idx=0 -- which asserts index 0 IS the
        # release -- and every convention returns NaN. Same defect as the one
        # already fixed in _a0_of, reappearing at a second call site.
        rel = float(aligned["t_release_s"])
        keep = t_g >= rel
        t_rel, th_rel = t_g[keep] - rel, th_g[keep]

        for flag in (False, True):
            sub = dict(aligned)
            sub["imu_t"], sub["imu_theta"] = t_g, th_g
            res = score_trial(sub, run)
            res["a0_imu"] = _a0_of_detrended(t_rel, th_rel, flag)
            res["a0_ratio"] = (res["a0_imu"] / res["a0_true"]) if res["a0_true"] else float("nan")
            res["a0_error_deg"] = res["a0_imu"] - res["a0_true"]
            out[f"{grid_name}_detrend_{str(flag).lower()}"] = res
    return out


def _REPLAY_CONFIG():
    """The production replay settings, so the bench measures what ships."""
    return {"beta": 0.041, "ema_alpha": 0.5, "flex_axis_capture": True,
            "gravity_seed": True, "method": "relative"}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/c/Users/cladi/miniconda3/python.exe -m pytest tests/test_motor_bench.py -q`
Expected: PASS, 11 passed

- [ ] **Step 5: Commit**

```bash
git add motor_bench.py tests/test_motor_bench.py
git commit -m "feat(bench): score every trial on both grids and both detrend conventions"
```

---

### Task 8: The session report

**Files:**
- Modify: `motor_bench.py`
- Test: `tests/test_motor_bench.py`

**Interfaces:**
- Consumes: `score_four_ways` from Task 7
- Produces: `summarise(rows) -> dict` with keys `n, a0_ratio_by_amplitude, breakdown_a0_deg, drift_median_deg_s, best_convention`

- [ ] **Step 1: Write the failing test**

```python
def _row(a0_true, ratio, drift=0.0):
    return {"run": {"a0_deg": a0_true},
            "scores": {k: {"a0_true": a0_true, "a0_imu": a0_true * ratio,
                           "a0_ratio": ratio, "a0_error_deg": a0_true * (ratio - 1),
                           "tail_drift_deg_s": drift, "rmse_deg": 0.1}
                       for k in ("20hz_detrend_false", "20hz_detrend_true",
                                 "100hz_detrend_false", "100hz_detrend_true")}}


def test_the_summary_finds_where_extraction_breaks():
    """Good above 25 deg, bad below: the breakdown point must be reported, and
    it is the number the excursion floor has never had."""
    rows = [_row(a0, 1.0) for a0 in (90, 50, 30, 28, 25)] + \
           [_row(a0, 2.5) for a0 in (22, 20, 15, 10, 5)]
    s = mb.summarise(rows)
    assert s["n"] == 10
    assert 22.0 <= s["breakdown_a0_deg"] <= 25.0


def test_no_breakdown_is_reported_as_none_not_as_zero():
    rows = [_row(a0, 1.0) for a0 in (90, 50, 25, 10, 5)]
    assert mb.summarise(rows)["breakdown_a0_deg"] is None


def test_the_summary_names_the_convention_closest_to_truth():
    """One convention is made clearly best and the other three clearly worse,
    so the assertion can only pass for the right reason."""
    rows = [_row(a0, 1.0) for a0 in (90, 50, 25)]
    for r in rows:
        r["scores"]["100hz_detrend_true"]["a0_ratio"] = 1.02   # nearly exact
        r["scores"]["100hz_detrend_false"]["a0_ratio"] = 1.40
        r["scores"]["20hz_detrend_true"]["a0_ratio"] = 1.55
        r["scores"]["20hz_detrend_false"]["a0_ratio"] = 1.80
    assert mb.summarise(rows)["best_convention"] == "100hz_detrend_true"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/c/Users/cladi/miniconda3/python.exe -m pytest tests/test_motor_bench.py -q`
Expected: FAIL, `AttributeError: module 'motor_bench' has no attribute 'summarise'`

- [ ] **Step 3: Write minimal implementation**

```python
#: A trial counts as recovered when A0 is within this fraction of truth.
RECOVERY_TOLERANCE = 0.20


def summarise(rows, convention="100hz_detrend_false"):
    """Session-level answers to the four questions the bench exists for.

    `breakdown_a0_deg` is the largest commanded amplitude at which extraction
    stopped recovering truth. None means it never broke down over the sweep,
    which is itself an answer -- and one the 25 deg floor would then have to
    justify some other way.
    """
    by_a0 = {}
    drifts = []
    for r in rows:
        s = r["scores"][convention]
        by_a0.setdefault(float(r["run"]["a0_deg"]), []).append(float(s["a0_ratio"]))
        d = s.get("tail_drift_deg_s")
        if d is not None and np.isfinite(d):
            drifts.append(float(d))

    ratio_by_a0 = {a0: float(np.median(v)) for a0, v in sorted(by_a0.items())}
    breakdown = None
    for a0 in sorted(ratio_by_a0, reverse=True):
        if abs(ratio_by_a0[a0] - 1.0) > RECOVERY_TOLERANCE:
            breakdown = a0
            break

    best, best_err = None, float("inf")
    for key in ("20hz_detrend_false", "20hz_detrend_true",
                "100hz_detrend_false", "100hz_detrend_true"):
        errs = [abs(r["scores"][key]["a0_ratio"] - 1.0) for r in rows
                if np.isfinite(r["scores"][key]["a0_ratio"])]
        if errs and float(np.median(errs)) < best_err:
            best, best_err = key, float(np.median(errs))

    return {
        "n": len(rows),
        "a0_ratio_by_amplitude": ratio_by_a0,
        "breakdown_a0_deg": breakdown,
        "drift_median_deg_s": float(np.median(drifts)) if drifts else float("nan"),
        "best_convention": best,
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/c/Users/cladi/miniconda3/python.exe -m pytest tests/test_motor_bench.py -q`
Expected: PASS, 14 passed

- [ ] **Step 5: Commit**

```bash
git add motor_bench.py tests/test_motor_bench.py
git commit -m "feat(bench): session summary -- breakdown point, drift, best convention"
```

---

## Self-review notes

**Spec coverage.** Trajectory (Tasks 1-2), sweep and export (Task 3),
pulse-edge sync with no cross-correlation (Tasks 4-5), A0 / drift / RMSE
against truth (Task 6), both grids and both detrend conventions (Task 7), and
the session-level answers (Task 8). The spec's "Reading the result" section is
interpretation, not code, and correctly has no task.

**Not covered, deliberately.** The LabVIEW RT side is outside this repository.
Task 3 exports the CSV it consumes, which is the whole interface between them.

**Open from the spec, unchanged by this plan.** Bench session length,
the encoder log's on-disk format (Task 5 takes arrays, so the loader is a thin
adapter once the format is known), and the motor model number.
