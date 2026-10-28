// Pure helpers for the research recorder's live strip chart. No DOM, no
// globals -- the canvas drawing that consumes these lives in views/research.js.
//
// The display angle is a plain gyro integration (deg), reset at Start. It
// DRIFTS over long holds and is display-only -- the SAVED raw JSONL is the real
// data. Integrated off gyro-y (the horizontal bench's swing axis), which is
// what this recorder is for; a different mounting would need a different axis.

export const RAD2DEG = 180 / Math.PI;
export const FLOATS_PER_SAMPLE = 7;   // [t_ms, ax, ay, az, gx, gy, gz], matches capture.js
const GYRO_Y_OFFSET = 5;              // gy within one sample

// One integration step: previous angle (deg) + rate (rad/s) over dt (s). A
// non-positive or non-finite dt (the first-ever sample, a stalled clock, an
// out-of-order batch) contributes nothing rather than poisoning the angle.
export function advanceAngleDeg(prevDeg, rateRadS, dtS) {
  if (!Number.isFinite(dtS) || dtS <= 0) return prevDeg;
  return prevDeg + rateRadS * RAD2DEG * dtS;
}

// Fold one raw wire batch (Float64Array, 7 floats/sample) into a running trace.
// `state` is { lastTMs, angleDeg }; returns the advanced state plus one point
// per sample: { tMs, rateDegS, angleDeg }. Rectangular integration against the
// previous sample's timestamp, so a batch boundary is not a special case.
export function foldGyroBatch(buf, state) {
  let { lastTMs, angleDeg } = state;
  const points = [];
  const n = Math.floor(buf.length / FLOATS_PER_SAMPLE);
  for (let i = 0; i < n; i += 1) {
    const base = i * FLOATS_PER_SAMPLE;
    const tMs = buf[base];
    const rateRadS = buf[base + GYRO_Y_OFFSET];
    const dtS = lastTMs == null ? 0 : (tMs - lastTMs) / 1000;
    angleDeg = advanceAngleDeg(angleDeg, rateRadS, dtS);
    lastTMs = tMs;
    points.push({ tMs, rateDegS: rateRadS * RAD2DEG, angleDeg });
  }
  return { state: { lastTMs, angleDeg }, points };
}

// The rolling window the canvas draws: keep only the last `maxLen` points.
export function capSeries(series, maxLen) {
  return series.length > maxLen ? series.slice(series.length - maxLen) : series;
}
