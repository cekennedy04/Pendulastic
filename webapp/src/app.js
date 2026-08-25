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
    return { latched: false, action: { kind: 'result', params: event.params } };
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

  function resetToIdle() {
    el('start').hidden = false;
    el('stop').hidden = true;
  }

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

  function onResult(p) {
    const { latched, action } = nextOutcome(faulted, { type: 'result', params: p });
    faulted = latched;
    // Nulled on every terminal outcome (result, error, and the Stop
    // handler below) so a fresh Start never reuses a finished session.
    session = null;
    if (!action) return; // a fault already latched this trial -- ignore the bounce
    el('guide').className = '';
    el('guide').textContent = 'scored';
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
