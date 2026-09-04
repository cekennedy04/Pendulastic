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
