import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { createAudioCues } from '../src/audio-cues.js';
import { captureQualityOf, SETTLE_TARGET_S, HOLD_TARGET_S, progressOf, beepsDue, wholeSeconds, lateralNote } from '../src/capture-feedback.js';

const T = 5.0;

test('a self-terminated trial is clean', () => {
  assert.equal(captureQualityOf({ settleS: 5.0, settleTargetS: T, endedManually: false }), 'clean');
});

// Stopped after settling began: a partial settled tail, so neutral_deg has
// something to work with but less than the protocol asks for.
test('stopped mid-settle is short', () => {
  assert.equal(captureQualityOf({ settleS: 2.4, settleTargetS: T, endedManually: true }), 'short');
});

// Stopped with no settling at all: no settled tail, so the neutral estimate is
// the weakest of the three. Distinct from short on purpose.
test('stopped with no settling at all is unsettled', () => {
  assert.equal(captureQualityOf({ settleS: 0, settleTargetS: T, endedManually: true }), 'unsettled');
});

// The data is what the protocol asks for regardless of who ended the trial.
test('a manual stop at or past the target is still clean', () => {
  assert.equal(captureQualityOf({ settleS: 5.0, settleTargetS: T, endedManually: true }), 'clean');
});

// Defaults must not flatter a trial nothing is known about.
test('defaults are pessimistic rather than optimistic', () => {
  assert.equal(captureQualityOf({}), 'unsettled');
  assert.equal(captureQualityOf(), 'unsettled');
});

test('the boundary is inclusive', () => {
  assert.equal(captureQualityOf({ settleS: 4.999, settleTargetS: T }), 'short');
  assert.equal(captureQualityOf({ settleS: 5.0, settleTargetS: T }), 'clean');
});

// The JS constant mirrors mobile-imu-core's. Nothing else would catch a drift
// between them: the app would keep classifying against a stale target while
// the core terminated on the real one, so every trial would silently be
// labelled 'short'.
test('the settle target mirrors the Rust constant', async () => {
  const rs = await readFile(new URL('../../mobile-imu-core/src/session.rs', import.meta.url), 'utf8');
  const m = rs.match(/pub const SETTLE_TARGET_S: f64 = ([0-9.]+);/);
  assert.ok(m, 'SETTLE_TARGET_S not found in session.rs');
  assert.equal(Number(m[1]), SETTLE_TARGET_S);
});

// ---- progress ------------------------------------------------------------
// The bar answers "how much longer", which a state label alone cannot.
test('holding fills toward the hold target', () => {
  const p = progressOf({ stateCode: 1, calmS: HOLD_TARGET_S / 2, settleS: 0 });
  assert.ok(Math.abs(p.fraction - 0.5) < 0.02, JSON.stringify(p));
  assert.match(p.label, /hold/i);
});

test('released fills toward the settle target', () => {
  const p = progressOf({ stateCode: 3, calmS: 0, settleS: 2.5 });
  assert.ok(Math.abs(p.fraction - 0.5) < 0.01, JSON.stringify(p));
  assert.match(p.label, /settl/i);
});

// A reset must be visible as an empty bar, not a paused one -- the operator
// needs to see WHY they are still waiting.
test('a reset settle shows an empty bar', () => {
  assert.equal(progressOf({ stateCode: 3, calmS: 0, settleS: 0 }).fraction, 0);
});

// An empty bar in MOVING would imply progress that is not happening.
test('states with nothing to count toward show no bar', () => {
  assert.equal(progressOf({ stateCode: 0, calmS: 0, settleS: 0 }), null);
  assert.equal(progressOf({ stateCode: 2, calmS: 0, settleS: 0 }), null);
});

test('a settled trial shows a full bar', () => {
  assert.equal(progressOf({ stateCode: 4, calmS: 0, settleS: 5 }).fraction, 1);
});

test('fraction never exceeds one', () => {
  assert.equal(progressOf({ stateCode: 3, calmS: 0, settleS: 99 }).fraction, 1);
  assert.equal(progressOf({ stateCode: 1, calmS: 99, settleS: 0 }).fraction, 1);
});

test('fraction is never negative', () => {
  assert.equal(progressOf({ stateCode: 3, calmS: 0, settleS: -1 }).fraction, 0);
});

// The hold target mirrors session.rs's Ready threshold, which is
// 0.95 * GYRO_BIAS_WINDOW_S. A drift would leave the bar full before the core
// armed, or still filling after it had.
test('the hold target mirrors the Rust ready threshold', async () => {
  const rs = await readFile(new URL('../../mobile-imu-core/src/stillness.rs', import.meta.url), 'utf8');
  const m = rs.match(/pub const GYRO_BIAS_WINDOW_S: f64 = ([0-9.]+);/);
  assert.ok(m, 'GYRO_BIAS_WINDOW_S not found');
  assert.ok(Math.abs(HOLD_TARGET_S - 0.95 * Number(m[1])) < 1e-9);
});

// ---- beep scheduling ------------------------------------------------------
// One beep per completed second of stability, during hold and settle alike.
test('crossing a second boundary is due one beep', () => {
  assert.equal(beepsDue(0, 1.02), 1);
});

test('no boundary crossed is due none', () => {
  assert.equal(beepsDue(1, 1.9), 0);
});

// Sample batches arrive every 50ms but can jump; the count must not swallow
// seconds when it does.
test('a jump across two boundaries is due two beeps', () => {
  assert.equal(beepsDue(1, 3.05), 2);
});

// A reset must not fire a beep and must not go negative -- the audio has to
// carry the same reset the bar shows, by falling silent rather than chirping.
test('a reset to zero is due no beeps', () => {
  assert.equal(beepsDue(3, 0), 0);
});

test('the whole-second counter tracks the value it was given', () => {
  assert.equal(wholeSeconds(0), 0);
  assert.equal(wholeSeconds(0.99), 0);
  assert.equal(wholeSeconds(1.0), 1);
  assert.equal(wholeSeconds(4.999), 4);
  assert.equal(wholeSeconds(-1), 0);
});

// ---- audio is additive, never load-bearing --------------------------------
test('a failing AudioContext does not throw out of unlock', () => {
  const cues = createAudioCues({ ctxFactory: () => { throw new Error('blocked'); } });
  assert.doesNotThrow(() => cues.unlock());
  assert.equal(cues.available, false);
});

// Every cue must be a no-op when audio never started, so no caller can be
// written that depends on it having worked.
test('cues are silent no-ops when audio is unavailable', () => {
  const cues = createAudioCues({ ctxFactory: () => { throw new Error('blocked'); } });
  cues.unlock();
  assert.doesNotThrow(() => { cues.tick(); cues.complete(); });
});

test('cues do not throw before unlock is ever called', () => {
  const cues = createAudioCues({ ctxFactory: () => { throw new Error('unused'); } });
  assert.doesNotThrow(() => { cues.tick(); cues.complete(); });
});

test('a suspended context is resumed, since iOS starts them suspended', () => {
  let resumed = false;
  const fake = { state: 'suspended', resume: () => { resumed = true; }, currentTime: 0 };
  const cues = createAudioCues({ ctxFactory: () => fake });
  cues.unlock();
  assert.equal(resumed, true);
});

// ---- the hold cue must not depend on a whole second ------------------------
// REGRESSION. The first version beeped once per completed second during the
// hold as well as the settle. calm_s tops out at 0.95 before the state turns
// Ready, so Math.floor(calm_s) is always 0 and the hold could never fire a
// single beep -- the operator heard nothing until settling. This pins the
// arithmetic that made it impossible.
test('the hold never completes a whole second, so it cannot be beeped per second', () => {
  // Every value calm_s can actually take, up to and including its ceiling.
  for (const calmS of [0, 0.2, 0.5, 0.94, HOLD_TARGET_S]) {
    assert.equal(beepsDue(0, calmS), 0,
      `calm_s=${calmS} would need a whole second to beep, and cannot reach one`);
  }
});

// Settling is longer than a second, which is why it CAN be counted.
test('settling does complete whole seconds', () => {
  assert.ok(SETTLE_TARGET_S >= 1, 'a per-second count needs at least one second');
  assert.equal(beepsDue(0, 1.0), 1);
  assert.equal(beepsDue(0, SETTLE_TARGET_S), 5);
});

test('lateral note reports the value and asserts no verdict', () => {
  const s = lateralNote({ lateral_fraction: 0.183, lateral_peak_deg_s: 34.2 });
  assert.match(s, /18% of the swing/);
  assert.match(s, /peak 34°\/s/);
  // No classification language: there is no calibrated cutoff to back one.
  assert.doesNotMatch(s, /poor|bad|fail|good|clean|acceptable/i);
});

test('not measured is said out loud, never shown as zero', () => {
  // 0 means "measured, and perfectly planar". Borrowing that number for
  // "could not assess" would report an unassessable trial as a clean one.
  const s = lateralNote(null);
  assert.match(s, /not measured/i);
  assert.doesNotMatch(s, /0%/);
});

test('a trial with no lateral field at all renders nothing', () => {
  assert.equal(lateralNote(undefined), null);
});

test('a planar trial is reported as zero, not hidden', () => {
  assert.match(lateralNote({ lateral_fraction: 0, lateral_peak_deg_s: 0 }), /0% of the swing/);
});

test('a non-finite fraction renders nothing rather than NaN%', () => {
  assert.equal(lateralNote({ lateral_fraction: NaN, lateral_peak_deg_s: 1 }), null);
});
