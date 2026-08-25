import { test } from 'node:test';
import assert from 'node:assert/strict';
import { encodeSample } from '../src/capture.js';

// encodeSample is the pure arithmetic core of capture.js: axis mapping and
// deg/s->rad/s conversion. It is exercised here with plain objects standing
// in for a DeviceMotionEvent -- no DOM, no worker, no globals required.

test('axis mapping: beta->x, gamma->y, alpha->z land in the right slots', () => {
  const out = new Float64Array(7);
  const event = {
    timeStamp: 123.456,
    accelerationIncludingGravity: { x: 1, y: 2, z: 3 },
    rotationRate: { alpha: 30, beta: 10, gamma: 20 },
  };
  encodeSample(out, 0, event);

  assert.equal(out[1], 1, 'ax');
  assert.equal(out[2], 2, 'ay');
  assert.equal(out[3], 3, 'az');
  // gx must come from beta, gy from gamma, gz from alpha -- not positional.
  assert.equal(out[4], 10 * (Math.PI / 180), 'gx must be beta (10 deg/s), not gamma or alpha');
  assert.equal(out[5], 20 * (Math.PI / 180), 'gy must be gamma (20 deg/s), not beta or alpha');
  assert.equal(out[6], 30 * (Math.PI / 180), 'gz must be alpha (30 deg/s), not beta or gamma');
});

test('gyro conversion: 180 deg/s converts to exactly pi rad/s', () => {
  const out = new Float64Array(7);
  const event = {
    timeStamp: 0,
    accelerationIncludingGravity: { x: 0, y: 0, z: 0 },
    rotationRate: { alpha: 180, beta: 180, gamma: 180 },
  };
  encodeSample(out, 0, event);

  assert.equal(out[4], Math.PI, 'beta (gx): 180 deg/s must equal pi rad/s exactly');
  assert.equal(out[5], Math.PI, 'gamma (gy): 180 deg/s must equal pi rad/s exactly');
  assert.equal(out[6], Math.PI, 'alpha (gz): 180 deg/s must equal pi rad/s exactly');
});

test('timeStamp passes through unchanged, in milliseconds, at a nonzero offset', () => {
  const out = new Float64Array(14);
  const event = {
    timeStamp: 98765.4321,
    accelerationIncludingGravity: { x: 0, y: 0, z: 9.81 },
    rotationRate: { alpha: 0, beta: 0, gamma: 0 },
  };
  // Write into the second sample slot to also confirm `offset` is honored
  // and the first slot is left untouched.
  encodeSample(out, 7, event);

  assert.equal(out[0], 0, 'first sample slot must be untouched');
  assert.equal(out[7], 98765.4321, 'timeStamp must pass through unchanged (no unit conversion)');
});
