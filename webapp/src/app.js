// Main-thread UI glue: wires the four-state capture state machine and the
// scored-result table onto startCapture's three callbacks. No DOM framework,
// no build step -- plain ES module loaded directly by the browser.
import { startCapture } from './capture.js';

const el = (id) => document.getElementById(id);

// code 0..3 from onState, per capture.js's doc comment: 0 Moving, 1 Holding,
// 2 Ready, 3 Released. Both arrays are indexed by that same code, matching
// mobile-imu-core's HoldState (see progress.md conflict #5).
const STATES = ['MOVING\nhold still', 'HOLDING', 'READY\nrelease now', 'RELEASED\nlet it settle'];
const CLASSES = ['moving', 'holding', 'ready', 'fired'];

// Every field name PtParams' JSON serialiser emits (mobile-imu-core's
// params_json.rs), in that exact order, so the table always renders all 20
// -- several are only meaningful read together, and no clinical
// prioritisation has been established for this app (task-6 dispatch,
// requirement 4).
const PARAM_ORDER = [
  'r2n', 'n', 'phi_max_ratio', 'omega_max_n', 'omega_min_n', 'f', 'area_ratio',
  'omega_peak_deg_s', 'a0_deg', 'a1_deg', 'first_trough_depth', 'neutral_deg',
  'neutral_deg_raw', 'pre_release_deg', 'quality_warn', 'phi_negated',
  'spasticity_type', 'p_plus', 'p_minus', 'p_total',
];

// Pure reducer over the onResult/onError message stream for ONE trial --
// exported so the fault-latch behaviour below can be driven by plain
// objects under `node --test` with no DOM, the same split `worker.js` uses
// for `createWorkerHandler` and `capture.js` uses for `encodeSample`.
//
// Why a latch is needed at all: `onError`'s call to `session.stop()` posts
// a second `{type:'finish'}` to the worker. `WasmSession::finish` is
// idempotent, so that second `finish` succeeds and the worker replies with
// a real `result` or `'unscorable'` one tick later. Read literally, that
// reply would silently overwrite the fault just shown with what looks like
// a completed trial -- exactly the failure mode the "not validated" banner
// exists to prevent (fix-round-1 finding). `'unscorable'` is a legitimate
// clinical outcome, not a fault, so it must never itself set the latch --
// but once a genuine fault HAS latched, nothing after it for that trial
// (a real result or an 'unscorable' bounce alike) may display.
//
// `latched`: whether a genuine fault has already been shown this trial.
// `event`: `{type:'result', params}` or `{type:'error', reason}`.
// Returns `{latched, action}`, where `action` is `null` (ignore -- a fault
// already latched) or `{kind:'result'|'unscorable'|'fault', ...}` for the
// caller to render.
export function nextOutcome(latched, event) {
  if (latched) return { latched: true, action: null };
  if (event.type === 'result') {
    const action = { kind: 'result', params: event.params };
    // Only set when the caller actually passed one, so a test (or any
    // caller) that builds a bare `{type:'result', params}` event -- the
    // shape worker.js's `finish` message kept working -- gets back exactly
    // `{kind:'result', params}`, with no stray `trajectory: undefined` key.
    if ('trajectory' in event) action.trajectory = event.trajectory;
    return { latched: false, action };
  }
  if (event.reason === 'unscorable') {
    return { latched: false, action: { kind: 'unscorable' } };
  }
  return { latched: true, action: { kind: 'fault', reason: event.reason } };
}

// Renders a single scored value. quality_warn/phi_negated are booleans,
// spasticity_type is a string, and any of the numeric fields may arrive as
// JSON null -- a non-finite f64 serialises to null rather than dropping the
// whole trial (mobile-imu-core's params_json.rs, Ruling H). None of those
// should be forced through toFixed.
function formatValue(v) {
  if (v === null || v === undefined) return '—';
  if (typeof v === 'number') return v.toFixed(4);
  return String(v);
}

// Everything below touches the DOM, so it is guarded to run only in a real
// browser: importing this module (e.g. from webapp/tests/app.test.js, to
// reach the pure `nextOutcome` above) must not throw under `node --test`,
// which has no `document`.
if (typeof document !== 'undefined') {
  let session = null;
  // Set once a genuine fault has been displayed for the CURRENT trial;
  // reset at the start of every new trial. See `nextOutcome` above.
  let faulted = false;
  // Kept so a viewport resize/orientation change can redraw the last
  // trajectory at the new canvas size instead of leaving a stretched bitmap
  // on screen until the next trial.
  let lastTrajectory = null;

  function resetToIdle() {
    el('start').hidden = false;
    el('stop').hidden = true;
  }

  // Draws `trajectory` (mobile-imu-core's finish_trajectory() payload, via
  // worker.js) onto the result canvas: the whole tick series -- pre-release
  // hold included -- angle (deg) against time (s), with the release point,
  // every accepted peak/trough, and the neutral line marked. This is the
  // waveform design spec §5 called for and the app never built: without it,
  // a scorer bug (e.g. finding a release but no return peak) is invisible --
  // the result screen showed 20 numbers and nothing an operator could check
  // them against.
  //
  // Plain canvas 2D, no charting library (binding constraint). Favours one
  // large, high-contrast plot over a dense one: this is read at arm's length
  // by someone who just performed the swing and wants to confirm the trace
  // matches it, not study it.
  function drawWaveform(trajectory) {
    const wrap = el('waveform-wrap');
    const canvas = el('waveform');
    if (!trajectory || !trajectory.t || trajectory.t.length === 0) {
      wrap.hidden = true;
      return;
    }
    lastTrajectory = trajectory;
    wrap.hidden = false;

    const { t, angle_deg: ang, release_idx, peak_idx, trough_idx, neutral_deg } = trajectory;

    // Size the backing bitmap to the element's CSS box at device pixel
    // density -- otherwise the plot is blurry on any real phone screen.
    const dpr = window.devicePixelRatio || 1;
    const cssW = canvas.clientWidth || 300;
    const cssH = canvas.clientHeight || 220;
    canvas.width = Math.round(cssW * dpr);
    canvas.height = Math.round(cssH * dpr);
    const ctx = canvas.getContext('2d');
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, cssW, cssH);

    const finiteAng = ang.filter((v) => typeof v === 'number' && Number.isFinite(v));
    if (finiteAng.length === 0) return;
    let yLo = Math.min(...finiteAng);
    let yHi = Math.max(...finiteAng);
    if (typeof neutral_deg === 'number') {
      yLo = Math.min(yLo, neutral_deg);
      yHi = Math.max(yHi, neutral_deg);
    }
    el('waveform-range').textContent =
      `angle range shown: ${yLo.toFixed(1)}° – ${yHi.toFixed(1)}°`;
    const pad = Math.max(2, (yHi - yLo) * 0.12) || 5;
    yLo -= pad;
    yHi += pad;

    const tLo = t[0];
    const tHi = t[t.length - 1];
    const padL = 46, padR = 12, padT = 10, padB = 26;
    const plotW = Math.max(1, cssW - padL - padR);
    const plotH = Math.max(1, cssH - padT - padB);
    const xOf = (tt) => padL + ((tt - tLo) / Math.max(1e-9, tHi - tLo)) * plotW;
    const yOf = (a) => padT + (1 - (a - yLo) / Math.max(1e-9, yHi - yLo)) * plotH;

    ctx.font = '11px -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif';
    ctx.strokeStyle = '#5a6169';
    ctx.fillStyle = '#101317';
    ctx.lineWidth = 1;

    // Axes.
    ctx.beginPath();
    ctx.moveTo(padL, padT);
    ctx.lineTo(padL, padT + plotH);
    ctx.lineTo(padL + plotW, padT + plotH);
    ctx.stroke();

    // Y ticks: low/mid/high of the range actually covered.
    ctx.textAlign = 'right';
    ctx.textBaseline = 'middle';
    for (const a of [yLo + pad, (yLo + yHi) / 2, yHi - pad]) {
      const y = yOf(a);
      ctx.strokeStyle = '#e3e6ea';
      ctx.beginPath();
      ctx.moveTo(padL, y);
      ctx.lineTo(padL + plotW, y);
      ctx.stroke();
      ctx.fillStyle = '#5a6169';
      ctx.fillText(`${a.toFixed(0)}°`, padL - 6, y);
    }
    // X ticks: whole seconds across the trial span.
    ctx.textAlign = 'center';
    ctx.textBaseline = 'top';
    const span = Math.max(1e-9, tHi - tLo);
    const step = Math.max(1, Math.ceil(span / 6));
    for (let s = 0; s <= tHi; s += step) {
      if (s < tLo) continue;
      const x = xOf(s);
      ctx.fillStyle = '#5a6169';
      ctx.fillText(`${s}s`, x, padT + plotH + 4);
    }
    ctx.textAlign = 'left';
    ctx.textBaseline = 'alphabetic';
    ctx.fillStyle = '#5a6169';
    ctx.fillText('deg', 4, padT + 10);

    // Neutral line.
    if (typeof neutral_deg === 'number' && Number.isFinite(neutral_deg)) {
      const y = yOf(neutral_deg);
      ctx.save();
      ctx.strokeStyle = '#5a6169';
      ctx.setLineDash([5, 4]);
      ctx.lineWidth = 1.5;
      ctx.beginPath();
      ctx.moveTo(padL, y);
      ctx.lineTo(padL + plotW, y);
      ctx.stroke();
      ctx.restore();
    }

    // Release marker: a vertical dashed line, so it reads even where the
    // trace happens to cross the neutral line at the same instant.
    if (Number.isInteger(release_idx) && t[release_idx] !== undefined) {
      const x = xOf(t[release_idx]);
      ctx.save();
      ctx.strokeStyle = '#1d4ed8';
      ctx.setLineDash([3, 3]);
      ctx.lineWidth = 2;
      ctx.beginPath();
      ctx.moveTo(x, padT);
      ctx.lineTo(x, padT + plotH);
      ctx.stroke();
      ctx.restore();
    }

    // Angle trace. Breaks the line at every non-finite (null) sample --
    // tick 0 always, and any mid-trial sensor dropout -- rather than
    // interpolating through zero, which would draw a motion that never
    // happened.
    ctx.strokeStyle = '#101317';
    ctx.lineWidth = 2.5;
    ctx.beginPath();
    let drawing = false;
    for (let i = 0; i < t.length; i++) {
      const a = ang[i];
      if (typeof a !== 'number' || !Number.isFinite(a)) {
        drawing = false;
        continue;
      }
      const x = xOf(t[i]);
      const y = yOf(a);
      if (!drawing) {
        ctx.moveTo(x, y);
        drawing = true;
      } else {
        ctx.lineTo(x, y);
      }
    }
    ctx.stroke();

    // Accepted peaks/troughs -- what the scorer actually counted, distinct
    // markers so they read as different even without color (upward triangle
    // vs. downward triangle).
    const marker = (idx, color, up) => {
      for (const i of idx || []) {
        const a = ang[i];
        if (typeof a !== 'number' || !Number.isFinite(a)) continue;
        const x = xOf(t[i]);
        const y = yOf(a);
        ctx.fillStyle = color;
        ctx.beginPath();
        if (up) {
          ctx.moveTo(x, y - 7);
          ctx.lineTo(x - 6, y + 5);
          ctx.lineTo(x + 6, y + 5);
        } else {
          ctx.moveTo(x, y + 7);
          ctx.lineTo(x - 6, y - 5);
          ctx.lineTo(x + 6, y - 5);
        }
        ctx.closePath();
        ctx.fill();
      }
    };
    marker(peak_idx, '#0f7a37', true);
    marker(trough_idx, '#7a0d0d', false);
  }

  window.addEventListener('resize', () => {
    if (!el('waveform-wrap').hidden && lastTrajectory) drawWaveform(lastTrajectory);
  });

  function onState({ code, calm_s, drift_deg }) {
    const g = el('guide');
    g.className = CLASSES[code];
    g.textContent = code === 1 ? `HOLDING ${calm_s.toFixed(1)}s` : STATES[code];
    // Both gates are surfaced separately: the corrective action for motion
    // and for drift differ, so "it failed" is not enough for the clinician
    // (task-6 dispatch, requirement 2).
    el('calm').textContent = `${calm_s.toFixed(2)} s / 0.95 s`;
    el('drift').textContent = `${drift_deg.toFixed(2)}° / 5.00°`;
  }

  function onResult(p, trajectory) {
    const { latched, action } = nextOutcome(faulted, { type: 'result', params: p, trajectory });
    faulted = latched;
    // Nulled on every terminal outcome (result, error, and the Stop
    // handler below) so a fresh Start never reuses a finished session.
    session = null;
    if (!action) return; // a fault already latched this trial -- ignore the bounce
    el('guide').className = '';
    el('guide').textContent = 'scored';
    drawWaveform(action.trajectory);
    el('result').hidden = false;
    el('result').innerHTML = PARAM_ORDER
      .map((k) => `<tr><td>${k}</td><td>${formatValue(p[k])}</td></tr>`)
      .join('');
    resetToIdle();
  }

  // The two onError cases read differently on purpose (task-6 dispatch,
  // requirement 3): 'unscorable' is an expected clinical outcome -- the
  // trial genuinely had no usable swing -- and should read as such, not as
  // a fault. Any other reason means something broke and is presented as an
  // error. Either way capture is no longer running: a fault can arrive
  // while the batch/flush loop is still live (e.g. the worker threw
  // mid-trial), so stop it rather than leaving devicemotion samples
  // flowing into a dead worker. `session` is nulled synchronously below,
  // before the `finish` this triggers can reply -- see `nextOutcome`'s doc
  // for why that reply must still be caught by the latch, not relied on
  // to arrive too late to matter.
  function onError(reason) {
    const { latched, action } = nextOutcome(faulted, { type: 'error', reason });
    faulted = latched;
    session?.stop();
    session = null;
    if (!action) return; // a fault already latched this trial -- ignore the bounce
    const g = el('guide');
    if (action.kind === 'unscorable') {
      g.className = 'unscorable';
      g.textContent = 'NOT SCORABLE\nno usable swing -- try again';
    } else {
      g.className = 'fault';
      g.textContent = `ERROR\n${action.reason}`;
    }
    resetToIdle();
  }

  el('start').addEventListener('click', async () => {
    faulted = false; // fresh trial: the previous trial's latch must not carry over
    el('start').hidden = true;
    el('stop').hidden = false;
    el('result').hidden = true;
    el('waveform-wrap').hidden = true;
    lastTrajectory = null;
    el('calm').textContent = '—';
    el('drift').textContent = '—';
    session = await startCapture({ onState, onResult, onError });
  });

  el('stop').addEventListener('click', () => {
    session?.stop();
    session = null;
    el('stop').hidden = true;
    el('start').hidden = false;
  });
}
