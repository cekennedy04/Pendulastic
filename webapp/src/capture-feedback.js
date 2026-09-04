// How a completed trial's tail quality is labelled, and what the guidance
// layer should show while one is running. Pure: no DOM, no timers, no wasm.

// Mirrors mobile-imu-core session.rs SETTLE_TARGET_S. Duplicated rather than
// read across the wasm boundary because it is needed to CLASSIFY a finished
// trial, after the session handle has already been nulled -- and a constant
// that must agree across two languages is pinned by a test rather than by a
// comment (see the mirror test).
export const SETTLE_TARGET_S = 5.0;

// Flags, never rejects. Code marks data quality and never drops a capture --
// a trial from a patient who cannot easily repeat it is not the app's to
// discard.
//
// The three values fail differently and must stay distinct, because
// neutral_deg is the settled-tail median and each gives it a different amount
// to work with:
//   clean     -- the full settled tail the protocol asks for
//   short     -- a partial tail; neutral is weaker but present
//   unsettled -- no settled tail at all; neutral is weakest
export function captureQualityOf({ settleS = 0, settleTargetS = 5.0, endedManually = true } = {}) {
  if (settleS >= settleTargetS) return 'clean';
  if (settleS > 0) return 'short';
  return 'unsettled';
}

// Mirrors session.rs's Ready threshold: calm_s >= 0.95 * GYRO_BIAS_WINDOW_S,
// with GYRO_BIAS_WINDOW_S = 1.0. Same reasoning as SETTLE_TARGET_S above --
// needed on the JS side to draw a bar, pinned by a test rather than a comment.
export const HOLD_TARGET_S = 0.95;

// What the progress bar should show, or null when there is nothing to count
// toward. Pure, so the arithmetic is tested without a DOM.
//
// Returns null rather than a zero-length bar for MOVING and READY: an empty
// bar in MOVING would imply progress that is not happening, and READY is an
// arrival, not a countdown.
export function progressOf({ stateCode, calmS = 0, settleS = 0 } = {}) {
  const clamp = (x) => Math.max(0, Math.min(1, x));
  if (stateCode === 1) return { fraction: clamp(calmS / HOLD_TARGET_S), label: 'hold steady' };
  if (stateCode === 3) return { fraction: clamp(settleS / SETTLE_TARGET_S), label: 'let it settle' };
  if (stateCode === 4) return { fraction: 1, label: 'trial complete' };
  return null;
}

// Whole completed seconds of a stability counter. Floors at zero so a reset
// or a stray negative cannot produce a negative beep count.
export function wholeSeconds(value) {
  return Math.floor(Math.max(0, value));
}

// How many beeps are owed since the last tick: one per completed second of
// stability. Never negative -- when the counter resets the audio simply falls
// silent, carrying the same reset the progress bar shows rather than chirping
// at the operator for losing the hold.
export function beepsDue(prevWholeSeconds, nextValue) {
  return Math.max(0, wholeSeconds(nextValue) - prevWholeSeconds);
}
