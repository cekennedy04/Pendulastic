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

let session = null;

function resetToIdle() {
  el('start').hidden = false;
  el('stop').hidden = true;
}

function onState({ code, calm_s, drift_deg }) {
  const g = el('guide');
  g.className = CLASSES[code];
  g.textContent = code === 1 ? `HOLDING ${calm_s.toFixed(1)}s` : STATES[code];
  // Both gates are surfaced separately: the corrective action for motion and
  // for drift differ, so "it failed" is not enough for the clinician
  // (task-6 dispatch, requirement 2).
  el('calm').textContent = `${calm_s.toFixed(2)} s / 0.95 s`;
  el('drift').textContent = `${drift_deg.toFixed(2)}° / 5.00°`;
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

function onResult(p) {
  el('guide').className = '';
  el('guide').textContent = 'scored';
  el('result').hidden = false;
  el('result').innerHTML = PARAM_ORDER
    .map((k) => `<tr><td>${k}</td><td>${formatValue(p[k])}</td></tr>`)
    .join('');
  resetToIdle();
}

// The two onError cases read differently on purpose (task-6 dispatch,
// requirement 3): 'unscorable' is an expected clinical outcome -- the trial
// genuinely had no usable swing -- and should read as such, not as a fault.
// Any other reason means something broke and is presented as an error.
// Either way capture is no longer running: a fault can arrive while the
// batch/flush loop is still live (e.g. the worker threw mid-trial), so stop
// it rather than leaving devicemotion samples flowing into a dead worker.
function onError(reason) {
  session?.stop();
  session = null;
  const g = el('guide');
  if (reason === 'unscorable') {
    g.className = 'unscorable';
    g.textContent = 'NOT SCORABLE\nno usable swing -- try again';
  } else {
    g.className = 'fault';
    g.textContent = `ERROR\n${reason}`;
  }
  resetToIdle();
}

el('start').addEventListener('click', async () => {
  el('start').hidden = true;
  el('stop').hidden = false;
  el('result').hidden = true;
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
