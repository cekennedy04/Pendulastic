# IMU Countdown Auto-Tare — Design Spec
**Date:** 2026-07-31
**Status:** Approved

---

## 1. Goal

Every IMU trial's angle convention depends on a zero-reference (`pendulastic_imu_server.zero()`)
being set at least once, ever, in the running server session — without it, `swing_angle_deg()`
returns `NaN` forever and the whole trial CSV fills with the literal string `nan`. Today that
reference is set by clicking a manual "Zero Sensor" button, and because nothing ever calls
`clear_zero()` automatically, it silently persists across every subsequent trial in the same
session — including trials where the phone was remounted or the clinician forgot to re-zero. This
was confirmed directly: a trial recorded without clicking "Zero Sensor" still produced plausible
angles, because it silently inherited an unrelated earlier trial's calibration.

Replace the manual button with automatic calibration during the existing 5-second pre-recording
countdown: continuously watch for a stable hold, tare the instant one is detected, and keep
re-taring to the latest stable window for as long as the countdown runs — so whatever the
clinician is holding right before recording actually starts is what "180°" gets calibrated against,
with no manual step and no silent staleness across trials.

---

## 2. File Impact Matrix

| File | Nature of change |
|---|---|
| `pendulastic_app.py` | New `App.on_countdown_start()` controller hook; new stability buffer + edge-triggered `_imu.zero()` calls inside `App._tick()`; new `App.is_imu_calibrated()` query; `AcquisitionPanel._tick_countdown()` gains the extend-then-confirm fallback; removes `btn_zero`/`_zero_frame`/`_on_zero_sensor`/`btn_clear_zero`/`_on_clear_zero` and their UI row; countdown checkbox forced on when `imu` is an active source |
| `pendulastic_imu_server.py` | No changes — `zero()` is called more often and from a different place, but its own implementation is untouched |

---

## 3. Continuous Re-Tare, Not Fire-Once

Taring only the *first* time a stable window is seen and then no longer watching would leave a gap:
the clinician could still shift the limb between that first stable moment and when recording
actually begins seconds later. Instead, stability is monitored for the *entire* countdown, and
every time the reading re-settles after having drifted, `zero()` fires again. Whatever the last
stable window was, right before recording starts, is what the trial is calibrated against.

**Where it runs:** the countdown (`AcquisitionPanel._start_countdown`/`_tick_countdown`) and the
50ms poll loop (`App._tick`) live in different classes, but `App._state` already stays `"idle"` for
the entire countdown today (it only flips to `"recording"` once the countdown finishes) — no new
state value is needed. `App._tick()` detects an active countdown via
`self._acq._countdown_id is not None` (already exists, already reliably `None` outside a countdown).
`AcquisitionPanel` gains one new controller callback, `on_countdown_start()`, invoked at the top of
`_start_countdown()` — matching the existing `on_start`/`on_stop`/`on_source_changed` callback
pattern — so `App` knows exactly when to reset its stability buffer. No new thread and no changes
to `pendulastic_imu_server.py` — this is purely additive logic inside the app's existing poll loop.

---

## 4. Stability Detection Algorithm

- **Signal:** both `pitch` and `roll` from `_imu.get_state()["angles"]`. Both, not just pitch —
  a limb held steady in flexion/extension but wobbling side to side should not count as stable.
- **Buffer:** `App._calib_buffer: list[tuple[float, float]]`, the trailing ~1 second of
  `(pitch, roll)` samples at the existing 50ms `_tick()` cadence (20 samples). Reset to empty in
  `on_countdown_start()`. Stability cannot be evaluated until the buffer has 20 samples — nothing
  can trigger in roughly the first second of the countdown.
- **Threshold:** `_CALIB_STABILITY_RANGE_DEG = 2.0`. Stable this tick iff
  `max(pitches) - min(pitches) < 2.0` **and** `max(rolls) - min(rolls) < 2.0` over the current buffer.
- **Edge-triggered:** `App._calib_was_stable: bool`, reset to `False` in `on_countdown_start()`.
  `_imu.zero()` fires only on the tick where stability transitions `False → True`, not on every tick
  while already stable — `zero()` also re-arms flex-axis capture
  (`_flex_axis = None; _flex_axis_armed = True`) each call, and there is no reason to re-arm 20
  times a second during one sustained hold. If the reading later drifts back out of tolerance,
  `_calib_was_stable` resets to `False`; the next new stable window re-tares again. `App` also sets
  `self._calib_ever_stable = True` the first time this fires, which is what
  `is_imu_calibrated()` (Section 6) reports.
- **Display:** the countdown's existing `status_var` ("Starting in {n}…") gains a calibration
  suffix — `"stabilizing…"` before the first tare this countdown, `"✓ calibrated"` once at least
  one has fired.

---

## 5. Fallback When It Never Stabilizes

If the countdown reaches 0 with `_calib_ever_stable` still `False`, `_tick_countdown` does not
transition to recording. Instead it extends in 1-second increments — same `after(1000, ...)`
mechanism, just re-entering the check rather than starting the trial — showing `"Hold steady…"`,
up to a hard cap of `_MAX_CALIB_EXTENSION_S = 5` additional seconds (10s total). `App._tick()`'s
stability polling keeps running unchanged throughout this extension: it triggers on
`self._acq._countdown_id is not None`, and the extension reuses that same identifier/scheduling
mechanism rather than clearing it, so the extra seconds are exactly as capable of producing a
stable window as the original five. If it is *still*
never stable at the cap, a confirmation dialog appears (`messagebox.askyesno`, matching this file's
existing dialog usage elsewhere): *"Sensor hasn't stabilized — start anyway?"* Accepting proceeds to
`on_start()` regardless; declining cancels exactly like the existing `_cancel_countdown` path. This
makes the risk explicit and puts the decision in the clinician's hands rather than silently
proceeding on an unverified reference, and rather than blocking indefinitely.

This whole extension/fallback path is skipped entirely when `imu` is not an active source —
`is_imu_calibrated()` (Section 6) returns `True` trivially in that case, so RGB/OptiTrack-only
trials behave exactly as they do today, with no extension and no forced countdown.

---

## 6. Removed UI, Forced Countdown, and the New Query Method

**Removed entirely:** `btn_zero`, `_zero_frame`, `_on_zero_sensor`, `btn_clear_zero`,
`_on_clear_zero`, and their grid row. "Clear Zero"'s original purpose — invalidating a stale
calibration, e.g. after remounting the phone mid-session — is superseded once every trial
re-calibrates automatically during its own countdown: the next trial's countdown tares fresh
regardless of what an earlier trial left behind, so there is no remaining scenario where a
clinician would want to manually invalidate a calibration without also just starting a new trial
(which re-calibrates anyway).

**Countdown forced on for IMU trials:** when `imu` is an active source, `_on_source_changed` (already
runs on every source toggle) calls `self.countdown_var.set(True)` and
`countdown_chk.config(state="disabled")` — there is no other calibration path left once the manual
button is gone. When `imu` is not (or is toggled back off), the checkbox is re-enabled
(`state="normal"`) and left at whatever value it holds; nothing forces it back to unchecked. Source
checkboxes are already unreachable mid-countdown regardless (`_start_countdown` already calls
`_lock_form(True)`), so there is no case where `imu` gets toggled while a countdown is in flight.

**New query method**, `App.is_imu_calibrated() -> bool`:
```python
def is_imu_calibrated(self) -> bool:
    """True if calibration isn't required (imu not an active source) or has
    already succeeded at least once this countdown."""
    if "imu" not in self._active_sources:
        return True
    return self._calib_ever_stable
```
`AcquisitionPanel._tick_countdown` calls this at `n == 0` to decide whether to proceed, extend, or
(at the cap) show the confirmation dialog.

---

## 7. Testing Plan

1. **Stability algorithm** — direct tests on the buffer/peak-to-peak logic: stays under threshold →
   `zero()` fires once (edge-triggered, not repeatedly); drifts out then re-stabilizes → fires
   again; never stabilizes → never fires. Both pitch and roll must independently be under threshold
   for the window to count as stable.
2. **Countdown extension** — never-stable case extends in 1s increments to the 5s cap, then shows
   the confirmation dialog; accepting proceeds to `on_start()`, declining cancels.
3. **Non-IMU trials unaffected** — `is_imu_calibrated()` returns `True` trivially when `imu` isn't
   active; RGB/OptiTrack-only trials never hit the extension logic and the countdown checkbox stays
   optional.
4. **Removed UI** — `btn_zero`, `_zero_frame`, `btn_clear_zero` and their handlers no longer exist;
   the countdown checkbox is disabled/locked-checked whenever `imu` is active.
5. **Full regression** — existing suite stays green, including the pre-existing countdown/`on_start`
   flow for non-IMU sources.

---

## 8. Out of Scope

- Any change to `pendulastic_imu_server.py`'s `zero()`/`clear_zero()`/flex-axis-capture
  implementation — reused exactly as-is, just called from a new place.
- Stability detection using full quaternion angular distance instead of Euler pitch/roll — Euler
  angles are already exposed by `get_state()` with no new server-side API needed; revisit only if
  pitch+roll proves insufficient in practice.
- Configurable stability tolerance/buffer duration — `2.0°` / ~1s are fixed constants for now, not
  exposed as user-facing settings.
