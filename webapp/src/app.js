// Main-thread UI glue: wires the four-state capture state machine and the
// scored-result table onto startCapture's three callbacks. No DOM framework,
// no build step -- plain ES module loaded directly by the browser.
import { startCapture } from './capture.js';
import { installState, installInstructions } from './install-gate.js';
import { openDb, put, getAll, STORES } from './db.js';
import { makeTrialRecord, makeSessionRecord, canCloseSession, markExported } from './session-store.js';
import { buildExportFiles, shareFiles } from './export.js';
import { BUILD_ID } from './build-id.js';

const el = (id) => document.getElementById(id);

// Participant management (unit U8) does not exist yet -- every trial is
// attributed to a single fixed side and a single fixed on-device test
// participant until a real picker replaces both (task-6 dispatch,
// correction 2).
const TRIAL_SIDE = 'left'; // U8 replaces this with a real side selector
const FIXED_PATIENT_ID = 'fixed-test-participant';
const FIXED_PATIENT_LABEL = 'TEST-PARTICIPANT'; // a literal we control, not free text -- see export.js's clinic_patient_id note

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
    // `{kind:'result', params}`, with no stray `trajectory: undefined` /
    // `ptScore: undefined` key.
    if ('trajectory' in event) action.trajectory = event.trajectory;
    if ('ptScore' in event) action.ptScore = event.ptScore;
    return { latched: false, action };
  }
  if (event.reason === 'unscorable') {
    return { latched: false, action: { kind: 'unscorable' } };
  }
  return { latched: true, action: { kind: 'fault', reason: event.reason } };
}

// Pure: decides which session a fresh page load should continue recording
// into, given every session already on file for this patient. `sessions` is
// whatever `getAll(db, STORES.sessions, 'by_patient', patientId)` returns.
//
// An unexported session (`exported_at == null`) is still "open" -- a reload
// mid-session (tab crash, accidental refresh, iOS evicting a backgrounded
// tab) must resume it rather than starting a new one, or the trials already
// recorded into it become invisible to the export flow even though they are
// still sitting in IndexedDB under a session id nothing on screen references
// any more. Exported-and-closed sessions are filtered out on purpose: once a
// session's data has left the device, its slot is done, and closing it
// (task-6 dispatch) simply forgets it so the next trial starts a new one --
// see the close-session handler below, which relies on exactly this
// filtering to "close" a session without ever touching its stored record.
//
// DESIGN DECISION, not an implementation detail: this filter is the ENTIRE
// mechanism behind "Close session". Nothing is ever written to mark a
// session closed -- `exported_at` already means "safe to leave behind," and
// filtering on it does double duty as the close flag. The direct
// consequence: an exported-but-never-explicitly-closed session (the operator
// tapped Export but the tab was killed before they tapped Close) is
// INDISTINGUISHABLE, on the next load, from one that was properly closed --
// both are just "an exported session for this patient" and both get
// filtered out identically. `close-session`'s click handler is therefore a
// pure in-memory reset; it performs no IndexedDB write of its own. If a
// future unit needs a real closed/not-closed distinction (e.g. to list past
// sessions), this filter is not enough on its own and a dedicated
// `closed_at` field should be added instead of overloading `exported_at`.
//
// If more than one open session exists (should not happen through this UI,
// but a future participant picker or a manual DB edit could produce it), the
// most recently created one wins -- an operator mid-visit wants the session
// they were just working in, not an old abandoned one.
export function resumeOrCreateSession(sessions, patientId) {
  const open = (sessions || [])
    .filter((s) => s.patient_id === patientId && s.exported_at == null)
    .sort((a, b) => b.timestamp - a.timestamp)[0];
  return open || makeSessionRecord({ patientId });
}

// Pure: what the export-lock UI should show for a given session and how
// many trials it holds. Extracted from the DOM-binding `refreshExportLock`
// below so the decision itself -- not just the two-line el().disabled
// plumbing -- can be driven with plain objects under `node --test` (per
// task-6 dispatch's testing note: this project has repeatedly found
// untested browser-only decision logic to be where silent errors live).
//
// `trialCount` is folded in deliberately, beyond what `canCloseSession`
// alone considers: a brand-new session with zero trials is technically
// "not closable" by `canCloseSession` (its `exported_at` is null), but
// warning an operator who hasn't recorded anything yet that they have
// "unexported trials" is actively misleading. The warning should appear
// only once there is something on the session that could actually be lost.
export function sessionLockState(session, trialCount) {
  if (!session || !trialCount) return { closable: false, warningVisible: false };
  const closable = canCloseSession(session);
  return { closable, warningVisible: !closable };
}

// Pure: the other half of the export gate, alongside session-store.js's
// markExported. This is the single rule that makes the gate mean anything --
// without it, a clinician exports once, records five more trials, and closes
// the session believing all of it was archived, when only the first trial
// ever left the device. `persistTrial` below calls this on every trial save;
// it is pulled out on its own so that one-line invariant can be pinned by a
// test independent of IndexedDB, the worker, or any other DOM-bound state.
//
// Rejects a null/undefined session explicitly rather than spreading it: a
// review finding (fix round 1) traced a real data-loss path back to exactly
// this -- `{...null}` silently produces `{}`, which drops `id` and turns a
// programming error (calling this after `currentSession` was cleared out
// from under an in-flight persist) into a key-less IndexedDB put() that
// fails with a misleading error, rather than a loud, attributable one here.
export function invalidateExport(session) {
  if (!session) throw new Error('invalidateExport requires a session, got ' + String(session));
  return { ...session, exported_at: null };
}

// Pure: the compare-and-swap check behind marking a session exported (fix
// round 2). `exported` is the snapshot taken when buildExportFiles ran --
// `{sessionId, trialIds}`. `live` is a fresh snapshot taken again
// immediately before the decision to call markExported: `live.sessionId` is
// whatever the app's live currentSession.id is AT THAT LATER MOMENT, and
// `live.trialIds` is a fresh read of `exported.sessionId`'s own trials (not
// live.sessionId's -- see the export-session handler, which is careful to
// re-query by the id captured at export time, not whatever session happens
// to be current later).
//
// Why this exists at all: `shareFiles` hands control to the OS share sheet,
// which is user-paced -- it can stay open for seconds to minutes, not the
// couple of IndexedDB transactions round 1 dealt with. `onResult` is a
// sensor-driven callback, not a click, so a trial can land via `persistTrial`
// at any point during that whole window regardless of what the session-bar
// buttons show -- disabling buttons (round 1's fix) cannot prevent this,
// because nothing here is gated behind a button. Calling `markExported` on
// a stale snapshot would mark data that never actually left the device as
// safely archived -- worse than round 1's finding, which merely orphaned an
// already-saved trial: this one unlocks Close on a session whose only copy
// of new data is still sitting in storage the platform may evict.
//
// Both a changed trial set AND a swapped-out session fail the check, and
// either failure means the same thing to the caller: don't mark exported,
// tell the clinician to export again. Erring toward "export again" costs a
// few seconds; erring the other way costs a trial.
export function canMarkExported(exported, live) {
  if (!exported || !live) return false;
  if (exported.sessionId !== live.sessionId) return false;
  if (exported.trialIds.length !== live.trialIds.length) return false;
  const a = [...exported.trialIds].sort();
  const b = [...live.trialIds].sort();
  return a.every((id, i) => id === b[i]);
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
  // Blocks recording until the app is opened from the Home Screen icon, not
  // a browser tab (task-2 dispatch). See install-gate.js's doc comment for
  // why both `matchMedia` and `navigator.standalone` are checked. This must
  // run before any control below is wired -- Start is disabled synchronously
  // rather than left clickable until some later check catches up.
  const gateState = installState({
    matchMedia: window.matchMedia.bind(window),
    navigatorStandalone: window.navigator.standalone,
    userAgent: navigator.userAgent,
    hasServiceWorker: 'serviceWorker' in navigator,
  });
  if (gateState !== 'standalone') {
    el('install-how').textContent = gateState === 'unsupported-browser'
      ? 'This browser cannot run Pendulastic offline. Use Safari on iOS or Chrome on Android.'
      : installInstructions(navigator.userAgent);
    el('install-gate').hidden = false;
    el('start').hidden = true;
  }

  // ---- Local persistence + export lock (task-6) --------------------------
  // A scored trial is worthless the moment it is forgotten, and IndexedDB is
  // a volatile cache the platform may erase -- see db.js's and
  // session-store.js's doc comments. Everything below makes each trial
  // durable to that cache immediately, and refuses to let a session close
  // until its data has actually left the device via export.js.
  let db = null;
  let currentSession = null;
  // Kept in memory alongside `currentSession` purely so `sessionLockState`
  // can tell "a fresh session with nothing recorded yet" apart from "a
  // session with unexported data" without an extra IndexedDB round trip on
  // every render (see that function's doc comment for why the distinction
  // matters).
  let currentTrialCount = 0;
  // Memoises the one-time-per-session init below so every call site
  // (persistTrial, the two session-bar buttons) can simply `await` it
  // instead of racing each other to create a duplicate session record.
  // Reset to null by the close-session handler so the NEXT await forces a
  // fresh resumeOrCreateSession() pass, which is also what makes "close"
  // work without ever mutating the closed session's own record.
  let sessionReadyPromise = null;
  // True for the entire duration of a persistTrial() call (fix round 1).
  // `persistTrial` awaits two separate IndexedDB writes before it clears
  // exported_at; refreshExportLock() only ran at the very end, so for that
  // whole window the session bar kept showing whatever state was true
  // BEFORE this trial. If the session was already exported, Close stayed
  // enabled and a tap during that window nulled `currentSession` out from
  // under the in-flight persist -- `invalidateExport(null)` would then
  // spread into `{}`, silently dropping `id`, and the session `put()` would
  // fail with no key while the trial itself had already committed under the
  // OLD (now-exported, filtered-out-on-resume) session id: a trial recorded
  // after export, invisibly unreachable on the next load. This flag closes
  // that window by making both buttons reflect "a write is in progress",
  // not just the last-known stored state.
  let persisting = false;

  async function ensurePatient() {
    const patients = await getAll(db, STORES.patients);
    const existing = patients.find((p) => p.id === FIXED_PATIENT_ID);
    if (existing) return existing;
    const patient = { id: FIXED_PATIENT_ID, clinic_patient_id: FIXED_PATIENT_LABEL, created_at: Date.now() };
    await put(db, STORES.patients, patient);
    return patient;
  }

  async function initSession() {
    db ??= await openDb(indexedDB);
    const patient = await ensurePatient();
    const sessions = await getAll(db, STORES.sessions, 'by_patient', patient.id);
    currentSession = resumeOrCreateSession(sessions, patient.id);
    await put(db, STORES.sessions, currentSession); // no-op if resumed and already stored; creates it otherwise
    const trials = await getAll(db, STORES.trials, 'by_session', currentSession.id);
    currentTrialCount = trials.length;
    refreshExportLock();
  }

  function ensureSessionReady() {
    sessionReadyPromise ??= initSession();
    return sessionReadyPromise;
  }

  // Fired at load time rather than lazily on the first trial: initialising
  // early means a slow first IndexedDB open does not add latency to the
  // moment a trial finishes and needs to be saved, and a failure here (e.g.
  // IndexedDB unavailable) is surfaced immediately rather than silently at
  // the worst possible time.
  ensureSessionReady().catch((err) => {
    console.error('session init failed', err);
    el('session-status').textContent =
      `Could not open local storage: ${err instanceof Error ? err.message : String(err)}. Trials will not be saved.`;
  });

  function refreshExportLock() {
    // While a trial is being persisted, both buttons are locked regardless
    // of what `currentSession`/`currentTrialCount` currently say -- see
    // `persisting`'s doc comment above for the race this closes.
    if (persisting) {
      el('export-session').disabled = true;
      el('close-session').disabled = true;
      return;
    }
    el('export-session').disabled = false;
    const { closable, warningVisible } = sessionLockState(currentSession, currentTrialCount);
    el('close-session').disabled = !closable;
    el('export-warning').hidden = !warningVisible;
  }

  // `trajectory` here is the plain object worker.js's `result` message
  // carries (the same one drawWaveform renders) -- see session-store.js's
  // updated doc comment on makeTrialRecord for its exact shape.
  async function persistTrial(params, trajectory, rawJsonl) {
    await ensureSessionReady();
    // Set before the first write and cleared in `finally` (fix round 1):
    // a rejected write must not strand the session bar disabled forever,
    // and refreshExportLock() must run on both transitions so the bar never
    // shows stale state while a write is actually in flight.
    persisting = true;
    refreshExportLock();
    try {
      const record = makeTrialRecord({
        sessionId: currentSession.id,
        side: TRIAL_SIDE,
        params,
        trajectory,
        rawJsonl,
        algorithmVersion: BUILD_ID,
      });
      await put(db, STORES.trials, record);
      currentTrialCount += 1;
      // A newly recorded trial invalidates any earlier export: the session
      // now holds data that has never left the device. See
      // invalidateExport's doc comment above for why this one line is the
      // whole point of the gate.
      currentSession = invalidateExport(currentSession);
      await put(db, STORES.sessions, currentSession);
    } finally {
      persisting = false;
      refreshExportLock();
    }
  }

  el('export-session').addEventListener('click', async () => {
    el('session-status').textContent = '';
    await ensureSessionReady();
    try {
      const sessionIdAtExport = currentSession.id;
      const trials = await getAll(db, STORES.trials, 'by_session', sessionIdAtExport);
      const patients = await getAll(db, STORES.patients);
      const patient = patients.find((p) => p.id === currentSession.patient_id);
      const files = buildExportFiles({ session: currentSession, patient, trials });
      // Belt-and-suspenders with shareFiles' own guard (task-6 dispatch,
      // correction 3): a session with no trials must never reach the share
      // sheet, and must never be marked exported.
      if (files.length === 0) {
        el('session-status').textContent = 'Nothing to export yet -- record a trial first.';
        return;
      }
      const exportedSnapshot = { sessionId: sessionIdAtExport, trialIds: trials.map((t) => t.id) };

      await shareFiles(files); // user-paced -- the share sheet can stay open for a long time

      // Compare-and-swap (fix round 2): re-read as late as possible,
      // immediately before the decision, so the still-unavoidable final gap
      // (this read plus one put()) is as narrow as it can be made without an
      // atomic transaction spanning both -- which db.js does not expose.
      // See canMarkExported's doc comment for why this check exists at all.
      const trialsNow = await getAll(db, STORES.trials, 'by_session', sessionIdAtExport);
      const liveSnapshot = { sessionId: currentSession.id, trialIds: trialsNow.map((t) => t.id) };
      if (!canMarkExported(exportedSnapshot, liveSnapshot)) {
        el('session-status').textContent =
          'A trial was recorded while exporting. This session has NOT been marked exported -- export again to include it.';
        refreshExportLock();
        return;
      }

      currentSession = markExported(currentSession);
      await put(db, STORES.sessions, currentSession);
      refreshExportLock();
      el('session-status').textContent = 'Session exported.';
    } catch (err) {
      el('session-status').textContent = `export failed: ${err instanceof Error ? err.message : String(err)}`;
    }
  });

  el('close-session').addEventListener('click', async () => {
    await ensureSessionReady();
    if (!canCloseSession(currentSession)) return; // the button is disabled for this case; guard it anyway
    // No IndexedDB write happens here, deliberately -- see
    // resumeOrCreateSession's doc comment above for why. The session already
    // carries the `exported_at` that makes it closable; "closing" is just
    // forgetting it locally and forcing the next ensureSessionReady() to run
    // resumeOrCreateSession() again, which skips any session with a set
    // `exported_at` and creates a fresh one. This means an exported session
    // that never had Close tapped on it is indistinguishable from one that
    // did -- accepted for this task, called out explicitly rather than left
    // implicit in the filter predicate.
    currentSession = null;
    currentTrialCount = 0;
    sessionReadyPromise = null;
    el('result').hidden = true;
    el('waveform-wrap').hidden = true;
    el('pt-score').hidden = true;
    hideExportControls();
    el('session-status').textContent = 'Session closed.';
    await ensureSessionReady();
  });

  let session = null;
  // Set once a genuine fault has been displayed for the CURRENT trial;
  // reset at the start of every new trial. See `nextOutcome` above.
  let faulted = false;
  // Kept so a viewport resize/orientation change can redraw the last
  // trajectory at the new canvas size instead of leaving a stretched bitmap
  // on screen until the next trial.
  let lastTrajectory = null;
  // The just-finished capture handle, kept alive for Export after `session`
  // itself is nulled below (onResult/onError null `session` unconditionally
  // -- that is what makes tapping Start immediately after a result safe).
  // `exportSession.exportJsonl()` still works after that: it is the same
  // object, its worker is never terminated, and `TrialSession::finish` takes
  // `&self`, so the wasm session's raw log survives scoring either way.
  let exportSession = null;

  // Raw-log export, KTD4: turns a captured trial into a laptop-replayable
  // file. This is the ONLY way an on-device trial can be diagnosed at all --
  // every prior analysis of the peak-at-release / missed-oscillation /
  // post-release-drift symptoms was done on simulated data because the real
  // capture could not leave the phone.
  function timestampedFilename() {
    const d = new Date();
    const p = (n) => String(n).padStart(2, '0');
    return `pendulastic-trial-${d.getFullYear()}${p(d.getMonth() + 1)}${p(d.getDate())}` +
      `-${p(d.getHours())}${p(d.getMinutes())}${p(d.getSeconds())}.jsonl`;
  }

  function downloadViaAnchor(blob, filename) {
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  }

  function showExportControls() {
    el('export-actions').hidden = false;
    // `showSaveFilePicker` does not exist in Safari; Web Share API Level 2
    // (`navigator.share({files})`) is the iOS mechanism, so it is the only
    // control offered unconditionally. `Send to laptop` only makes sense
    // when this very page was served by the dev server -- a production
    // deploy has no `/upload` endpoint to receive it.
    el('send-to-laptop').hidden = location.port !== '8900';
    el('export-status').textContent = '';
  }

  function hideExportControls() {
    el('export-actions').hidden = true;
    el('export-status').textContent = '';
  }

  el('export-btn').addEventListener('click', async () => {
    if (!exportSession) return;
    el('export-status').textContent = 'exporting…';
    try {
      const jsonl = await exportSession.exportJsonl();
      const filename = timestampedFilename();
      const blob = new Blob([jsonl], { type: 'application/x-ndjson' });

      let shared = false;
      if (typeof navigator.canShare === 'function') {
        const file = new File([blob], filename, { type: 'application/x-ndjson' });
        let shareable = false;
        try { shareable = navigator.canShare({ files: [file] }); } catch { shareable = false; }
        if (shareable) {
          try {
            await navigator.share({ files: [file] });
            shared = true;
          } catch (err) {
            // A user-cancelled share sheet is not a failure -- respect the
            // cancellation rather than surprising them with an immediate
            // download anyway. Any other share failure falls through to the
            // plain-anchor fallback below.
            if (err && err.name === 'AbortError') shared = true;
          }
        }
      }
      if (!shared) downloadViaAnchor(blob, filename);
      el('export-status').textContent = `exported ${filename}`;
    } catch (err) {
      el('export-status').textContent = `export failed: ${err instanceof Error ? err.message : String(err)}`;
    }
  });

  el('send-to-laptop').addEventListener('click', async () => {
    if (!exportSession) return;
    el('export-status').textContent = 'sending…';
    try {
      const jsonl = await exportSession.exportJsonl();
      const filename = timestampedFilename();
      const res = await fetch(`/upload?filename=${encodeURIComponent(filename)}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/x-ndjson' },
        body: jsonl,
      });
      if (!res.ok) throw new Error(`server returned ${res.status}`);
      el('export-status').textContent = `sent to laptop as ${filename}`;
    } catch (err) {
      el('export-status').textContent = `send failed: ${err instanceof Error ? err.message : String(err)}`;
    }
  });

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

  // Human-readable zone label. Every label carries "(provisional)" -- this
  // instrument has not passed its validation gate (trajectory RMSE 14.84°
  // against a <=10° target; leave-one-participant-out AUC 0.21, below chance)
  // and the zone thresholds themselves are a working calibration on 29
  // participant-legs, not a validated clinical cutoff (see
  // mobile-imu-core/src/pt_score.rs). The word choice deliberately avoids
  // anything that reads as a diagnosis.
  const ZONE_LABEL = {
    healthy: 'healthy range (provisional)',
    borderline: 'borderline (provisional)',
    impaired: 'impaired range (provisional)',
    unknown: 'zone unknown',
  };

  // Renders the composite PT score panel: `ptScore` is
  // `{score, zone, breakdown}` from mobile-imu-core's finish_pt_score()
  // (worker.js's `finishPtScore`), or null/undefined when the trial produced
  // no score -- hide the whole panel rather than show a blank or zero, which
  // would misleadingly read as "scored healthy."
  //
  // `breakdown` arrives pre-sorted by descending contribution (Rust's
  // `PtScoreBreakdown::ordered`), so the largest driver is simply first --
  // no re-sorting here.
  function renderPtScore(ptScore) {
    const wrap = el('pt-score');
    if (!ptScore || typeof ptScore.score !== 'number') {
      wrap.hidden = true;
      return;
    }
    wrap.hidden = false;
    el('pt-score-value').textContent = ptScore.score.toFixed(4);
    const zoneEl = el('pt-score-zone');
    const zone = typeof ptScore.zone === 'string' ? ptScore.zone : 'unknown';
    zoneEl.textContent = ZONE_LABEL[zone] || ZONE_LABEL.unknown;
    zoneEl.className = `zone-${zone}`;
    el('pt-score-breakdown').innerHTML = (ptScore.breakdown || [])
      .map(({ key, value }) => `<tr><td>${key}</td><td>${formatValue(value)}</td></tr>`)
      .join('');
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

  function onResult(p, trajectory, ptScore) {
    const { latched, action } = nextOutcome(faulted, { type: 'result', params: p, trajectory, ptScore });
    faulted = latched;
    // Captured before `session` is nulled below: the worker (and its wasm
    // session's raw log) is never terminated, so Export keeps working after
    // the active-capture handle is gone.
    exportSession = session;
    // Nulled on every terminal outcome (result, error, and the Stop
    // handler below) so a fresh Start never reuses a finished session.
    session = null;
    if (!action) return; // a fault already latched this trial -- ignore the bounce
    el('guide').className = '';
    el('guide').textContent = 'scored';
    drawWaveform(action.trajectory);
    renderPtScore(action.ptScore);
    el('result').hidden = false;
    el('result').innerHTML = PARAM_ORDER
      .map((k) => `<tr><td>${k}</td><td>${formatValue(p[k])}</td></tr>`)
      .join('');
    showExportControls();
    resetToIdle();

    // Persist the scored trial (task-6): fire-and-forget from the render
    // path's point of view -- the operator must not wait on an IndexedDB
    // round trip to see their result -- but every failure is still surfaced
    // rather than swallowed, since a trial that silently fails to save is
    // exactly the durability gap this task closes. `capture` is pinned to a
    // local before any later trial's onResult can reassign `exportSession`.
    const capture = exportSession;
    if (capture) {
      capture.exportJsonl()
        .then((rawJsonl) => persistTrial(p, action.trajectory, rawJsonl))
        .catch((err) => {
          el('session-status').textContent =
            `trial was scored but NOT saved: ${err instanceof Error ? err.message : String(err)}`;
        });
    }
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
    // Captured before `session.stop()`/nulling below, same reasoning as
    // onResult -- the raw log is most valuable to export precisely when the
    // trial did NOT score cleanly.
    exportSession = session;
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
    showExportControls();
    resetToIdle();
  }

  el('start').addEventListener('click', async () => {
    faulted = false; // fresh trial: the previous trial's latch must not carry over
    el('start').hidden = true;
    el('stop').hidden = false;
    el('result').hidden = true;
    el('waveform-wrap').hidden = true;
    el('pt-score').hidden = true;
    hideExportControls();
    exportSession = null; // the previous trial's export must not survive into a new one
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
