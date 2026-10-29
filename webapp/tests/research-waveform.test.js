import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  advanceAngleDeg, foldGyroBatch, capSeries, RAD2DEG, FLOATS_PER_SAMPLE,
} from '../src/research-waveform.js';

test('advanceAngleDeg integrates rate (rad/s) over dt (s) into degrees', () => {
  // 1 rad/s for 1 s == 57.2958 deg, added onto the previous angle.
  assert.ok(Math.abs(advanceAngleDeg(0, 1, 1) - RAD2DEG) < 1e-9);
  assert.ok(Math.abs(advanceAngleDeg(10, 1, 1) - (10 + RAD2DEG)) < 1e-9);
  assert.equal(advanceAngleDeg(10, 0, 1), 10);
});

test('advanceAngleDeg ignores a non-positive or non-finite dt rather than producing NaN', () => {
  assert.equal(advanceAngleDeg(5, 3, 0), 5);
  assert.equal(advanceAngleDeg(5, 3, -1), 5);
  assert.equal(advanceAngleDeg(5, 3, NaN), 5);
});

test('foldGyroBatch turns a raw wire batch into an angle + rate trace off gyro-y', () => {
  const S = FLOATS_PER_SAMPLE;
  const buf = new Float64Array(2 * S);
  buf[0] = 1000; buf[5] = 1;        // t=1000 ms, gy = 1 rad/s (index 5)
  buf[S + 0] = 1100; buf[S + 5] = 1; // t=1100 ms, gy = 1 rad/s
  const { state, points } = foldGyroBatch(buf, { lastTMs: null, angleDeg: 0 });
  assert.equal(points.length, 2);
  assert.equal(points[0].angleDeg, 0, 'the first-ever sample has no dt, so no angle change');
  assert.ok(Math.abs(points[1].rateDegS - RAD2DEG) < 1e-9);
  assert.ok(Math.abs(points[1].angleDeg - RAD2DEG * 0.1) < 1e-9, '1 rad/s over 0.1 s == 5.7296 deg');
  assert.equal(state.lastTMs, 1100);
});

test('foldGyroBatch continues the angle across batches from prior state', () => {
  const S = FLOATS_PER_SAMPLE;
  const b1 = new Float64Array(S); b1[0] = 0; b1[5] = 1;
  const r1 = foldGyroBatch(b1, { lastTMs: null, angleDeg: 0 });
  const b2 = new Float64Array(S); b2[0] = 1000; b2[5] = 1; // 1 s later, 1 rad/s
  const r2 = foldGyroBatch(b2, r1.state);
  assert.equal(r2.state.lastTMs, 1000);
  assert.ok(Math.abs(r2.points[0].angleDeg - RAD2DEG) < 1e-9, 'integrates across the batch boundary');
});

test('capSeries keeps only the last maxLen points', () => {
  assert.deepEqual(capSeries([1, 2, 3, 4, 5], 3), [3, 4, 5]);
  assert.deepEqual(capSeries([1, 2], 3), [1, 2]);
  assert.deepEqual(capSeries([], 3), []);
});
