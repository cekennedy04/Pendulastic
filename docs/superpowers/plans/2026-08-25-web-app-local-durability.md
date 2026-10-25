# Local Durability — Implementation Plan (Plan 2 of 4)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the app survive being closed — install it to the Home Screen, run it with no network, store participants and trials on device, and refuse to let a session close until its data has left the phone.

**Architecture:** A service worker caches the shell and wasm keyed to the build, so an offline app can never run maths a stale cache is holding. An install gate blocks data entry until the app is running standalone. IndexedDB holds participants, sessions and trials, with high-rate streams as `ArrayBuffer`s. Export is not a convenience — it is the archive of record, and a session cannot close without it.

**Tech Stack:** Vanilla ES modules, Service Worker API, IndexedDB, Web Share API Level 2, Node 24 `node --test`. No frameworks, no bundler, no dependencies.

**Spec:** `docs/superpowers/specs/2026-08-24-web-app-design.md` (Section 3 in full, plus §3.1a)

## Prerequisite

Plan 1 must be on this branch. Verify:

```bash
git log --oneline | grep -q "raw-log JSONL export" && echo OK || echo "PLAN 1 MISSING"
```

## Global Constraints

Copied from the spec. Every task's requirements implicitly include these.

- **IndexedDB is a volatile cache; the exported file is the archive of record.** Losing the database must cost a convenience, never a record. (§3.1a)
- **A session cannot close or be cleared until it has been exported at least once.** This is the mechanism that makes the line above true in practice rather than in principle. (§3.1a)
- **The composite PT score and severity zone are never persisted** — they are derived at read time, because `HEALTHY_REF` is still moving. (§3.5)
- **`params` field names are exactly the 20 from `PtParams`** — `r2n, n, phi_max_ratio, omega_max_n, omega_min_n, f, area_ratio, omega_peak_deg_s, a0_deg, a1_deg, first_trough_depth, neutral_deg, neutral_deg_raw, pre_release_deg, quality_warn, phi_negated, spasticity_type, p_plus, p_minus, p_total`. Renaming breaks traceability with the desktop corpus. (§3.3)
- **`quality_warn` is strictly `area_ratio > 0.55`** — a scoring-symmetry flag, *not* a capture-quality flag. It must never be merged with `capture_quality`. (§3.3)
- **The export JSONL contract is fixed and already pinned** by `tests/test_web_export_contract.py`: `t` in **seconds**, `v` exactly 3 elements, `sensor` ∈ `accel|gyro|mag`, **accel before gyro** at the same `t`, `phone_ts_ms` required, gyro rad/s, accel m/s², `role` `"distal"`. Every one of these fails *silently* when wrong. (§3.4)
- **Add no npm or Rust dependencies.** No framework, no bundler, no zip library.
- **The "research capture only — not validated" banner stays unconditional.** (§8)
- Do not modify `mobile-imu-core/tests/fixtures/golden.rs` or `tests/pipeline_test.rs`, and never adjust a tolerance to make a test pass.
- Other sessions have in-flight work in `pendulastic_pt_score.py`, `imu_calibration_tuner.py`, `pendulastic_imu_server.py`, `pt_report_common.py`, and `imu_flex_axis.py`. Do not touch them.

## File Structure

```
webapp/
  sw.js                      create — service worker; cache keyed to build id
  src/build-id.js            create — generated; the cache key
  src/install-gate.js        create — standalone detection (pure, testable)
  src/db.js                  create — IndexedDB open/upgrade + CRUD
  src/session-store.js       create — trial/session records; export-gated close
  src/export.js              create — file assembly + Web Share dispatch
  src/app.js                 modify — wire gate, persistence, export lock
  index.html                 modify — install-gate markup, SW registration
  src/app.css                modify — gate styling
  scripts/build-wasm.mjs     modify — emit build-id.js after generating wasm
  tests/install-gate.test.js create
  tests/db.test.js           create
  tests/session-store.test.js create
  tests/export.test.js       create
```

`install-gate.js`, `db.js`, `session-store.js` and `export.js` are separate because each has a different testability boundary: the gate is pure, the db needs a fake IndexedDB, the store is logic over the db, and export is file assembly plus a browser-only dispatch. Splitting them keeps the untestable part down to the `navigator.share` call itself.

---

### Task 1: Build-keyed service worker

A service worker is a cache. This project already removed one stale-artifact class (the committed wasm) precisely so the browser could not run maths that `cargo test` never verified. A cache keyed by URL alone reintroduces that failure *inside the phone*, where it survives reloads and there is no git to diff against. The cache name therefore carries the build id, and activation deletes every cache that is not the current one.

**Files:**
- Create: `webapp/sw.js`
- Create: `webapp/src/build-id.js`
- Modify: `webapp/scripts/build-wasm.mjs`
- Modify: `webapp/index.html`

**Interfaces:**
- Produces: `webapp/src/build-id.js` exporting `export const BUILD_ID = '<hash>';`; `sw.js` responding to a `{type:'SKIP_WAITING'}` message.

- [ ] **Step 1: Emit a build id from the wasm build**

In `webapp/scripts/build-wasm.mjs`, after `wasm-bindgen` writes its output, hash the generated `.wasm` and write the id beside the sources:

```js
import { createHash } from 'node:crypto';
import { writeFileSync, readFileSync } from 'node:fs';

// The cache key is derived from the artifact itself, not a version string a
// human remembers to bump. A rebuilt wasm always produces a new key; an
// unchanged one never does.
const wasmOut = fileURLToPath(new URL('webapp/src/wasm/mobile_imu_core_bg.wasm', repoRoot));
const buildId = createHash('sha256').update(readFileSync(wasmOut)).digest('hex').slice(0, 12);
writeFileSync(
  fileURLToPath(new URL('webapp/src/build-id.js', repoRoot)),
  `// GENERATED by scripts/build-wasm.mjs — do not edit.\nexport const BUILD_ID = '${buildId}';\n`,
);
console.log(`build id ${buildId}`);
```

- [ ] **Step 2: Run the build and confirm the id appears**

Run: `cd webapp && npm run build:wasm`
Expected: prints `build id <12 hex chars>`, and `webapp/src/build-id.js` exists containing that id.

- [ ] **Step 3: Write the service worker**

`webapp/sw.js`:

```js
// Offline shell. The cache name carries the build id, so a new wasm produces
// a new cache and the old one is deleted on activate. Without that, a phone
// could keep serving a cached wasm after the Rust changed -- running maths
// nothing verified, with no way to see it from the outside.
import { BUILD_ID } from './src/build-id.js';

const CACHE = `pendulastic-${BUILD_ID}`;
const SHELL = [
  './',
  './index.html',
  './src/app.js',
  './src/app.css',
  './src/capture.js',
  './src/worker.js',
  './src/install-gate.js',
  './src/db.js',
  './src/session-store.js',
  './src/export.js',
  './src/build-id.js',
  './src/wasm/mobile_imu_core.js',
  './src/wasm/mobile_imu_core_bg.wasm',
];

self.addEventListener('install', (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)));
});

self.addEventListener('activate', (e) => {
  // Delete every cache that is not this build's. This is what makes a rebuild
  // actually reach the device rather than sitting behind a stale entry.
  e.waitUntil(
    caches.keys().then((names) =>
      Promise.all(names.filter((n) => n !== CACHE).map((n) => caches.delete(n))),
    ).then(() => self.clients.claim()),
  );
});

self.addEventListener('fetch', (e) => {
  const url = new URL(e.request.url);
  // Never cache the dev upload route -- it is a POST to the host and must
  // always go to the network.
  if (e.request.method !== 'GET' || url.pathname === '/upload') return;
  e.respondWith(
    caches.match(e.request).then((hit) => hit || fetch(e.request)),
  );
});

self.addEventListener('message', (e) => {
  if (e.data && e.data.type === 'SKIP_WAITING') self.skipWaiting();
});
```

- [ ] **Step 4: Register it from the page**

In `webapp/index.html`, before the closing body, add:

```html
<script>
  // Module service workers need {type:'module'} -- sw.js imports build-id.js.
  if ('serviceWorker' in navigator) {
    window.addEventListener('load', () => {
      navigator.serviceWorker.register('./sw.js', { type: 'module' })
        .catch((e) => console.warn('service worker registration failed', e));
    });
  }
</script>
```

- [ ] **Step 5: Verify the shell list matches reality**

Run:
```bash
cd webapp && node -e "
const {readFileSync,existsSync}=require('fs');
const sw=readFileSync('sw.js','utf8');
const list=[...sw.matchAll(/'\.\/([^']+)'/g)].map(m=>m[1]).filter(p=>p&&!p.endsWith('/'));
const missing=list.filter(p=>!existsSync(p));
if(missing.length){console.error('SHELL lists files that do not exist:',missing);process.exit(1);}
console.log('all',list.length,'shell entries exist');
"
```
Expected: `all 13 shell entries exist`. A shell entry that does not exist makes `addAll` reject and the whole install fail silently — the app then works online and breaks offline, which is the hardest version of this bug to notice.

- [ ] **Step 6: Commit**

```bash
git add webapp/sw.js webapp/src/build-id.js webapp/scripts/build-wasm.mjs webapp/index.html
git commit -m "feat(webapp): add a build-keyed service worker for offline use"
```

---

### Task 2: Standalone install gate

**Files:**
- Create: `webapp/src/install-gate.js`
- Create: `webapp/tests/install-gate.test.js`
- Modify: `webapp/index.html`, `webapp/src/app.css`, `webapp/src/app.js`

**Interfaces:**
- Produces: `installState({ matchMedia, navigatorStandalone, userAgent }) -> 'standalone' | 'needs-install' | 'unsupported-browser'`

- [ ] **Step 1: Write the failing test**

`webapp/tests/install-gate.test.js`:

```js
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { installState } from '../src/install-gate.js';

const IOS = 'Mozilla/5.0 (iPhone; CPU iPhone OS 18_7 like Mac OS X) Safari/604.1';
const ANDROID = 'Mozilla/5.0 (Linux; Android 14) Chrome/120 Mobile Safari/537.36';
const mm = (matches) => () => ({ matches });

test('display-mode standalone is recognised on any platform', () => {
  assert.equal(installState({ matchMedia: mm(true), navigatorStandalone: undefined, userAgent: ANDROID }), 'standalone');
  assert.equal(installState({ matchMedia: mm(true), navigatorStandalone: undefined, userAgent: IOS }), 'standalone');
});

test('iOS reports standalone through its own non-standard flag', () => {
  // Older iOS does not match the display-mode query but does set this.
  assert.equal(installState({ matchMedia: mm(false), navigatorStandalone: true, userAgent: IOS }), 'standalone');
});

test('a browser tab needs installing', () => {
  assert.equal(installState({ matchMedia: mm(false), navigatorStandalone: false, userAgent: IOS }), 'needs-install');
});

test('Android in a tab is needs-install, NOT unsupported', () => {
  // navigator.standalone is undefined on Android. Treating undefined as "not
  // installed AND not iOS" must not lock Android users out permanently.
  assert.equal(installState({ matchMedia: mm(false), navigatorStandalone: undefined, userAgent: ANDROID }), 'needs-install');
});

test('a browser with no service worker support cannot go offline', () => {
  assert.equal(
    installState({ matchMedia: mm(false), navigatorStandalone: undefined, userAgent: ANDROID, hasServiceWorker: false }),
    'unsupported-browser',
  );
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd webapp && node --test tests/install-gate.test.js`
Expected: FAIL — cannot find `../src/install-gate.js`

- [ ] **Step 3: Write the implementation**

`webapp/src/install-gate.js`:

```js
// Whether the app is running installed. Pure so it can be tested without a
// browser: every ambient value is passed in.
//
// Two mechanisms, because neither covers both platforms. `display-mode:
// standalone` is the standard and works on Android and newer iOS.
// `navigator.standalone` is iOS-only and non-standard, and is UNDEFINED on
// Android -- so a check written as `navigator.standalone !== true` would
// declare every Android user un-installed forever, behind a modal telling
// them to use a Safari menu they do not have.
export function installState({
  matchMedia,
  navigatorStandalone,
  userAgent = '',
  hasServiceWorker = true,
} = {}) {
  if (!hasServiceWorker) return 'unsupported-browser';
  const displayMode = typeof matchMedia === 'function'
    ? matchMedia('(display-mode: standalone)').matches
    : false;
  if (displayMode || navigatorStandalone === true) return 'standalone';
  return 'needs-install';
}

/// Instructions differ per platform; iOS has no install prompt API at all,
/// so the user must be walked through the Share menu by hand.
export function installInstructions(userAgent = '') {
  return /iPhone|iPad|iPod/.test(userAgent)
    ? 'Tap the Share button, then "Add to Home Screen", then open Pendulastic from your Home Screen.'
    : 'Open your browser menu and choose "Install app" or "Add to Home Screen", then reopen Pendulastic from there.';
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd webapp && node --test tests/install-gate.test.js`
Expected: PASS, 5 tests

- [ ] **Step 5: Add the blocking gate to the page**

In `webapp/index.html`, immediately after the banner:

```html
<div id="install-gate" hidden>
  <h1>Install before recording</h1>
  <p id="install-why">
    Safari deletes a website's stored data after 7 days of inactivity.
    Adding Pendulastic to your Home Screen is what keeps participant records
    and captured trials from being erased between sessions.
  </p>
  <p id="install-how"></p>
  <p class="install-note">
    Exported files are the permanent record regardless — installing reduces
    how often you need them, it is not what makes your data safe.
  </p>
</div>
```

In `webapp/src/app.css`:

```css
#install-gate {
  position: fixed; inset: 0; z-index: 100;
  background: var(--bg, #111); color: var(--fg, #eee);
  padding: 24px; overflow-y: auto;
  display: flex; flex-direction: column; justify-content: center;
}
#install-gate[hidden] { display: none; }
#install-gate h1 { font-size: 22px; margin: 0 0 12px; }
#install-gate p { font-size: 16px; line-height: 1.5; margin: 0 0 14px; }
.install-note { color: #999; font-size: 14px; }
```

In `webapp/src/app.js`, inside the existing `typeof document !== 'undefined'` block, before any control is wired:

```js
import { installState, installInstructions } from './install-gate.js';

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
```

- [ ] **Step 6: Run the whole webapp suite**

Run: `cd webapp && npm test`
Expected: PASS — 25 pre-existing plus 5 new = 30.

- [ ] **Step 7: Commit**

```bash
git add webapp/src/install-gate.js webapp/tests/install-gate.test.js \
        webapp/index.html webapp/src/app.css webapp/src/app.js
git commit -m "feat(webapp): block recording until the app is installed to the Home Screen"
```

---

### Task 3: IndexedDB store

**Files:**
- Create: `webapp/src/db.js`
- Create: `webapp/tests/db.test.js`

**Interfaces:**
- Produces: `openDb(indexedDB) -> Promise<IDBDatabase>`, `put(db, store, record) -> Promise<void>`, `getAll(db, store, indexName, key) -> Promise<record[]>`, `DB_NAME`, `DB_VERSION`, `STORES`

- [ ] **Step 1: Write the failing test**

`webapp/tests/db.test.js`:

```js
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { openDb, put, getAll, STORES } from '../src/db.js';

// Node 24 has no IndexedDB. Rather than pull in a fake-indexeddb dependency,
// these tests drive the schema logic through the same upgrade callback a real
// browser would, using a minimal stand-in that records what was asked for.
function fakeIndexedDB() {
  const created = [];
  return {
    created,
    open(name, version) {
      const req = {};
      queueMicrotask(() => {
        const db = {
          objectStoreNames: { contains: (n) => created.some((c) => c.name === n) },
          createObjectStore(n, opts) {
            const store = { name: n, opts, indexes: [], createIndex(i, kp) { this.indexes.push({ i, kp }); } };
            created.push(store);
            return store;
          },
        };
        req.result = db;
        req.onupgradeneeded?.({ target: { result: db } });
        req.onsuccess?.({ target: { result: db } });
      });
      return req;
    },
  };
}

test('the schema creates exactly the three stores the spec names', async () => {
  const idb = fakeIndexedDB();
  await openDb(idb);
  assert.deepEqual(idb.created.map((s) => s.name).sort(), ['patients', 'sessions', 'trials']);
});

test('trials are indexed by session so a session view does not scan every trial', async () => {
  const idb = fakeIndexedDB();
  await openDb(idb);
  const trials = idb.created.find((s) => s.name === 'trials');
  assert.ok(trials.indexes.some((x) => x.kp === 'session_id'), 'missing session_id index');
});

test('sessions are indexed by patient', async () => {
  const idb = fakeIndexedDB();
  await openDb(idb);
  const sessions = idb.created.find((s) => s.name === 'sessions');
  assert.ok(sessions.indexes.some((x) => x.kp === 'patient_id'), 'missing patient_id index');
});

test('STORES names match what openDb creates', async () => {
  const idb = fakeIndexedDB();
  await openDb(idb);
  assert.deepEqual(Object.values(STORES).sort(), idb.created.map((s) => s.name).sort());
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd webapp && node --test tests/db.test.js`
Expected: FAIL — cannot find `../src/db.js`

- [ ] **Step 3: Write the implementation**

`webapp/src/db.js`:

```js
// IndexedDB access. Treated as a VOLATILE CACHE, not a record store: Safari
// deletes script-writable storage after 7 days of site inactivity unless the
// app is installed to the Home Screen, and even that exemption is a platform
// behaviour Apple can change. Losing this database must cost a convenience,
// never a record -- see session-store.js's export gate.

export const DB_NAME = 'pendulastic';
export const DB_VERSION = 1;
export const STORES = { patients: 'patients', sessions: 'sessions', trials: 'trials' };

export function openDb(idb) {
  return new Promise((resolve, reject) => {
    const req = idb.open(DB_NAME, DB_VERSION);
    req.onupgradeneeded = (e) => {
      const db = e.target.result;
      if (!db.objectStoreNames.contains(STORES.patients)) {
        db.createObjectStore(STORES.patients, { keyPath: 'id' });
      }
      if (!db.objectStoreNames.contains(STORES.sessions)) {
        const s = db.createObjectStore(STORES.sessions, { keyPath: 'id' });
        s.createIndex('by_patient', 'patient_id');
      }
      if (!db.objectStoreNames.contains(STORES.trials)) {
        const t = db.createObjectStore(STORES.trials, { keyPath: 'id' });
        // Without this, rendering one session's trials means scanning every
        // trial ever recorded on the device.
        t.createIndex('by_session', 'session_id');
      }
    };
    req.onsuccess = (e) => resolve(e.target.result);
    req.onerror = () => reject(req.error || new Error('indexedDB open failed'));
  });
}

export function put(db, storeName, record) {
  return new Promise((resolve, reject) => {
    const tx = db.transaction(storeName, 'readwrite');
    tx.objectStore(storeName).put(record);
    tx.oncomplete = () => resolve();
    tx.onerror = () => reject(tx.error);
  });
}

export function getAll(db, storeName, indexName, key) {
  return new Promise((resolve, reject) => {
    const tx = db.transaction(storeName, 'readonly');
    const src = indexName ? tx.objectStore(storeName).index(indexName) : tx.objectStore(storeName);
    const req = key === undefined ? src.getAll() : src.getAll(key);
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd webapp && node --test tests/db.test.js`
Expected: PASS, 4 tests

- [ ] **Step 5: Commit**

```bash
git add webapp/src/db.js webapp/tests/db.test.js
git commit -m "feat(webapp): add the IndexedDB schema for patients, sessions and trials"
```

---

### Task 4: Trial records and the export gate

**Files:**
- Create: `webapp/src/session-store.js`
- Create: `webapp/tests/session-store.test.js`

**Interfaces:**
- Consumes: `db.js`'s `put`, `getAll`, `STORES`
- Produces: `makeTrialRecord({ sessionId, side, params, trajectory, rawJsonl, algorithmVersion, captureQuality, releaseIdx, releaseOverrideIdx })`, `canCloseSession(session)`, `markExported(session, at)`

- [ ] **Step 1: Write the failing test**

`webapp/tests/session-store.test.js`:

```js
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { makeTrialRecord, canCloseSession, markExported, PARAM_FIELDS } from '../src/session-store.js';

const params = Object.fromEntries(PARAM_FIELDS.map((k, i) => [k, i]));

test('a trial record carries exactly the 20 PtParams fields, no more', () => {
  const r = makeTrialRecord({ sessionId: 's1', side: 'left', params, trajectory: new ArrayBuffer(8), rawJsonl: 'x', algorithmVersion: '0.1.0' });
  assert.deepEqual(Object.keys(r.params).sort(), PARAM_FIELDS.slice().sort());
});

test('the composite score and zone are never stored', () => {
  const r = makeTrialRecord({ sessionId: 's1', side: 'left', params: { ...params, pt_score: 0.4, zone: 'impaired' }, trajectory: new ArrayBuffer(8), rawJsonl: 'x', algorithmVersion: '0.1.0' });
  // HEALTHY_REF is still being recalibrated; a persisted composite would let a
  // trend line silently compare scores from different scorers.
  assert.ok(!('pt_score' in r.params), 'pt_score must not be persisted');
  assert.ok(!('zone' in r.params), 'zone must not be persisted');
  assert.ok(!('pt_score' in r), 'pt_score must not be persisted at the record level either');
});

test('a session cannot close until it has been exported', () => {
  const s = { id: 's1', patient_id: 'p1', timestamp: 0, exported_at: null };
  assert.equal(canCloseSession(s), false);
  assert.equal(canCloseSession(markExported(s, 12345)), true);
});

test('markExported does not mutate the session it was given', () => {
  const s = { id: 's1', patient_id: 'p1', timestamp: 0, exported_at: null };
  markExported(s, 12345);
  assert.equal(s.exported_at, null, 'markExported must return a new record, not mutate');
});

test('a trial keeps its raw log, which is the archive of record', () => {
  const r = makeTrialRecord({ sessionId: 's1', side: 'left', params, trajectory: new ArrayBuffer(8), rawJsonl: 'line\n', algorithmVersion: '0.1.0' });
  assert.equal(r.raw_jsonl, 'line\n');
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd webapp && node --test tests/session-store.test.js`
Expected: FAIL — cannot find `../src/session-store.js`

- [ ] **Step 3: Write the implementation**

`webapp/src/session-store.js`:

```js
// Record shapes and the export gate.
//
// The gate is the whole durability design: IndexedDB can be evicted by the
// platform, so a session that has never been exported has exactly one copy of
// its data, on a device that may erase it. Refusing to close such a session is
// what keeps that from happening quietly.

// Exactly the scalar fields of PtParams, in scoring.rs order. Renaming any of
// them breaks traceability with the desktop corpus and the golden fixtures.
export const PARAM_FIELDS = [
  'r2n', 'n', 'phi_max_ratio', 'omega_max_n', 'omega_min_n', 'f', 'area_ratio',
  'omega_peak_deg_s', 'a0_deg', 'a1_deg', 'first_trough_depth', 'neutral_deg',
  'neutral_deg_raw', 'pre_release_deg', 'quality_warn', 'phi_negated',
  'spasticity_type', 'p_plus', 'p_minus', 'p_total',
];

function uuid() {
  return crypto.randomUUID();
}

export function makeTrialRecord({
  sessionId, side, params, trajectory, rawJsonl, algorithmVersion,
  captureQuality = 'clean', releaseIdx = 0, releaseOverrideIdx = null,
}) {
  // Copy only the known fields. Anything else the caller passes -- notably a
  // composite score -- is dropped rather than persisted.
  const kept = {};
  for (const k of PARAM_FIELDS) kept[k] = params[k];
  return {
    id: uuid(),
    session_id: sessionId,
    side,
    timestamp: Date.now(),
    algorithm_version: algorithmVersion,
    capture_quality: captureQuality,
    release_idx: releaseIdx,
    release_override_idx: releaseOverrideIdx,
    params: kept,
    trajectory,        // ArrayBuffer: [t, angle_deg] on 50ms ticks
    raw_jsonl: rawJsonl,
  };
}

export function makeSessionRecord({ patientId }) {
  return { id: uuid(), patient_id: patientId, timestamp: Date.now(), exported_at: null };
}

export function canCloseSession(session) {
  return Boolean(session && session.exported_at);
}

export function markExported(session, at = Date.now()) {
  return { ...session, exported_at: at };
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd webapp && node --test tests/session-store.test.js`
Expected: PASS, 5 tests

- [ ] **Step 5: Commit**

```bash
git add webapp/src/session-store.js webapp/tests/session-store.test.js
git commit -m "feat(webapp): add trial records and the export-before-close gate"
```

---

### Task 5: Multi-file session export

No zip. `navigator.share()` accepts an array of files, and one contract-pure `.jsonl` per trial stays directly replayable through `replay_trial` without anyone extracting an archive first. Building a zip container by hand would be ~100 lines of central-directory code for the privilege of making the output harder to consume.

**Files:**
- Create: `webapp/src/export.js`
- Create: `webapp/tests/export.test.js`
- Modify: `webapp/src/app.js`

**Interfaces:**
- Consumes: `session-store.js`'s `PARAM_FIELDS`
- Produces: `buildExportFiles({ session, patient, trials }) -> { name, type, text }[]`, `shareFiles(files, { navigatorRef })`

- [ ] **Step 1: Write the failing test**

`webapp/tests/export.test.js`:

```js
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { buildExportFiles } from '../src/export.js';
import { PARAM_FIELDS } from '../src/session-store.js';

const params = Object.fromEntries(PARAM_FIELDS.map((k, i) => [k, i]));
const trial = (id, raw) => ({
  id, session_id: 's1', side: 'left', timestamp: 1, algorithm_version: '0.1.0',
  capture_quality: 'clean', release_idx: 3, release_override_idx: null,
  params, raw_jsonl: raw,
});

test('one jsonl file per trial plus one manifest', () => {
  const files = buildExportFiles({
    session: { id: 's1', patient_id: 'p1', timestamp: 1, exported_at: null },
    patient: { id: 'p1', clinic_patient_id: 'ANON-7', created_at: 1 },
    trials: [trial('t1', 'a\n'), trial('t2', 'b\n')],
  });
  const names = files.map((f) => f.name);
  assert.equal(files.filter((f) => f.name.endsWith('.jsonl')).length, 2);
  assert.equal(files.filter((f) => f.name.endsWith('.json')).length, 1);
  assert.ok(names.every((n) => n.includes('ANON-7')), `names should carry the participant id: ${names}`);
});

test('each trial file is the raw log verbatim, not re-serialised', () => {
  // The raw JSONL was produced by the Rust exporter against a contract pinned
  // in tests/test_web_export_contract.py. Re-encoding it here would put a
  // second, untested implementation of that contract in the path.
  const raw = '{"t":0.1,"role":"distal","sensor":"accel","v":[0,0,9.81],"phone_ts_ms":100}\n';
  const files = buildExportFiles({
    session: { id: 's1', patient_id: 'p1', timestamp: 1, exported_at: null },
    patient: { id: 'p1', clinic_patient_id: 'ANON-7', created_at: 1 },
    trials: [trial('t1', raw)],
  });
  assert.equal(files.find((f) => f.name.endsWith('.jsonl')).text, raw);
});

test('the manifest carries params but no composite score', () => {
  const files = buildExportFiles({
    session: { id: 's1', patient_id: 'p1', timestamp: 1, exported_at: null },
    patient: { id: 'p1', clinic_patient_id: 'ANON-7', created_at: 1 },
    trials: [trial('t1', 'a\n')],
  });
  const manifest = JSON.parse(files.find((f) => f.name.endsWith('.json')).text);
  assert.deepEqual(Object.keys(manifest.trials[0].params).sort(), PARAM_FIELDS.slice().sort());
  assert.ok(!('pt_score' in manifest.trials[0]), 'composite must be derived at read time, never exported as fact');
  assert.equal(manifest.algorithm_version, '0.1.0');
});

test('an empty session produces no files rather than an empty archive', () => {
  const files = buildExportFiles({
    session: { id: 's1', patient_id: 'p1', timestamp: 1, exported_at: null },
    patient: { id: 'p1', clinic_patient_id: 'ANON-7', created_at: 1 },
    trials: [],
  });
  assert.equal(files.length, 0);
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd webapp && node --test tests/export.test.js`
Expected: FAIL — cannot find `../src/export.js`

- [ ] **Step 3: Write the implementation**

`webapp/src/export.js`:

```js
// Session export. These files are the archive of record -- IndexedDB is a
// cache that the platform may erase.
//
// One .jsonl per trial, byte-for-byte as the Rust exporter produced it, plus
// one .json manifest. No zip: navigator.share takes an array of files, and a
// loose .jsonl replays through the desktop pipeline directly, where an archive
// would have to be extracted first.

import { PARAM_FIELDS } from './session-store.js';

function stamp(ms) {
  return new Date(ms).toISOString().replace(/[-:]/g, '').replace(/\..+/, '');
}

export function buildExportFiles({ session, patient, trials }) {
  if (!trials || trials.length === 0) return [];
  const base = `pendulastic-${patient.clinic_patient_id}-${stamp(session.timestamp)}`;

  const files = trials.map((t, i) => ({
    name: `${base}-trial${i + 1}.jsonl`,
    type: 'application/x-ndjson',
    // Verbatim. Re-encoding would introduce a second implementation of a
    // contract whose every failure mode is silent.
    text: t.raw_jsonl,
  }));

  const manifest = {
    schema: 'pendulastic/session-export/v1',
    exported_at: new Date().toISOString(),
    algorithm_version: trials[0].algorithm_version,
    patient: { clinic_patient_id: patient.clinic_patient_id },
    session: { id: session.id, timestamp: session.timestamp },
    trials: trials.map((t, i) => ({
      file: `${base}-trial${i + 1}.jsonl`,
      side: t.side,
      timestamp: t.timestamp,
      capture_quality: t.capture_quality,
      release_idx: t.release_idx,
      release_override_idx: t.release_override_idx,
      // The 20 scalars only. The composite score and zone are derived at read
      // time against the current HEALTHY_REF, which is still being
      // recalibrated -- exporting one would freeze a moving reference.
      params: Object.fromEntries(PARAM_FIELDS.map((k) => [k, t.params[k]])),
    })),
  };
  files.push({ name: `${base}-manifest.json`, type: 'application/json', text: JSON.stringify(manifest, null, 2) });
  return files;
}

export async function shareFiles(files, { navigatorRef = navigator } = {}) {
  const fileObjs = files.map((f) => new File([f.text], f.name, { type: f.type }));
  if (navigatorRef.canShare && navigatorRef.canShare({ files: fileObjs })) {
    await navigatorRef.share({ files: fileObjs, title: 'Pendulastic session' });
    return 'shared';
  }
  // showSaveFilePicker does not exist in Safari; a download anchor is the
  // only fallback that works there.
  for (const f of files) {
    const url = URL.createObjectURL(new Blob([f.text], { type: f.type }));
    const a = document.createElement('a');
    a.href = url; a.download = f.name; a.click();
    URL.revokeObjectURL(url);
  }
  return 'downloaded';
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd webapp && node --test tests/export.test.js`
Expected: PASS, 4 tests

- [ ] **Step 5: Run the whole webapp suite**

Run: `cd webapp && npm test`
Expected: PASS — 30 from Task 2 plus 4 (db) plus 5 (session-store) plus 4 (export) = 43.

- [ ] **Step 6: Update CI's discovery-count guard**

`.github/workflows/ci.yml` asserts the Node test count exactly. Change the expected count from `18` to `43`, and confirm the number matches what `npm test` reports — a stale guard fails every run.

- [ ] **Step 7: Commit**

```bash
git add webapp/src/export.js webapp/tests/export.test.js .github/workflows/ci.yml
git commit -m "feat(webapp): export a session as per-trial JSONL plus a manifest"
```

---

### Task 6: Wire persistence and the export lock into the UI

**Files:**
- Modify: `webapp/src/app.js`, `webapp/index.html`, `webapp/src/app.css`

**Interfaces:**
- Consumes: `db.js`, `session-store.js`, `export.js`

- [ ] **Step 1: Persist each scored trial**

In `webapp/src/app.js`'s result handler, after the waveform renders, store the trial. `exportJsonl()` already exists on the capture handle from Plan 1:

```js
import { openDb, put, getAll, STORES } from './db.js';
import { makeTrialRecord, makeSessionRecord, canCloseSession, markExported } from './session-store.js';
import { buildExportFiles, shareFiles } from './export.js';

let db = null;
let currentSession = null;

async function persistTrial(params, trajectory, rawJsonl) {
  db ??= await openDb(indexedDB);
  const record = makeTrialRecord({
    sessionId: currentSession.id,
    side: currentSide(),
    params,
    trajectory,
    rawJsonl,
    algorithmVersion: BUILD_ID,
  });
  await put(db, STORES.trials, record);
  // A newly recorded trial invalidates any earlier export: the session now
  // holds data that has never left the device.
  currentSession = { ...currentSession, exported_at: null };
  await put(db, STORES.sessions, currentSession);
  refreshExportLock();
}
```

- [ ] **Step 2: Add the lock UI**

In `webapp/index.html`, after the result table:

```html
<div id="session-bar">
  <button id="export-session">Export session</button>
  <button id="close-session" disabled>Close session</button>
  <p id="export-warning">This session has unexported trials. Export before closing.</p>
</div>
```

In `webapp/src/app.js`:

```js
function refreshExportLock() {
  const closable = canCloseSession(currentSession);
  el('close-session').disabled = !closable;
  el('export-warning').hidden = closable;
}

el('export-session').addEventListener('click', async () => {
  db ??= await openDb(indexedDB);
  const trials = await getAll(db, STORES.trials, 'by_session', currentSession.id);
  const patients = await getAll(db, STORES.patients);
  const patient = patients.find((p) => p.id === currentSession.patient_id);
  const files = buildExportFiles({ session: currentSession, patient, trials });
  if (files.length === 0) return;
  await shareFiles(files);
  currentSession = markExported(currentSession);
  await put(db, STORES.sessions, currentSession);
  refreshExportLock();
});
```

- [ ] **Step 3: Verify the lock by hand in Node**

Run:
```bash
cd webapp && node -e "
import('./src/session-store.js').then(({makeSessionRecord,canCloseSession,markExported})=>{
  const s = makeSessionRecord({patientId:'p1'});
  if (canCloseSession(s)) { console.error('FAIL: a fresh session must not be closable'); process.exit(1); }
  if (!canCloseSession(markExported(s))) { console.error('FAIL: an exported session must be closable'); process.exit(1); }
  console.log('export lock behaves correctly');
});
"
```
Expected: `export lock behaves correctly`

- [ ] **Step 4: Run the whole suite**

Run: `cd webapp && npm test`
Expected: PASS, 43 tests.

- [ ] **Step 5: Commit**

```bash
git add webapp/src/app.js webapp/index.html webapp/src/app.css
git commit -m "feat(webapp): persist trials and refuse to close an unexported session"
```

---

### Task 7: The eviction soak test

This cannot be automated and must not be faked. It is the only check on the assumption the whole storage design was written around, and it has never run.

**Files:**
- Create: `webapp/docs/eviction-soak-test.md`

- [ ] **Step 1: Write the protocol**

`webapp/docs/eviction-soak-test.md`:

```markdown
# 7-day storage eviction soak test

## Why

WebKit deletes all script-writable storage — IndexedDB, localStorage, Cache
API, service worker registrations — after 7 days without user interaction with
the site. Apple documents an exemption for sites added to the Home Screen.
**That exemption is the only thing standing between a clinician and losing a
month of participant records, and this project has never verified it.**

The design deliberately does not depend on it (IndexedDB is a cache, the
exported files are the record, and a session cannot close unexported). This
test measures how much the exemption actually buys.

## Protocol

1. On a real iPhone, install Pendulastic to the Home Screen via Share → Add to
   Home Screen. Confirm it opens standalone: the install gate must not appear.
2. Record at least two trials against a test participant id. Export them, so a
   copy exists off-device regardless of the outcome.
3. Note the date, the iOS version, and the participant id here.
4. **Do not open the app for at least 8 days.** Using the phone normally is
   fine and is the point — the clock is site inactivity, not device inactivity.
5. On day 8+, open the app from the Home Screen icon.

## Record the result

- Are the participant and both trials still listed?
- Does the waveform still render for a stored trial (the trajectory
  `ArrayBuffer` survived, not just the metadata)?
- Did the app load offline, i.e. did the service worker cache survive too?

| Date started | iOS version | Date reopened | Records survived | Cache survived |
|---|---|---|---|---|
| | | | | |

## If it fails

The exemption does not hold on this iOS version. That is a finding, not a
defect to fix in this app: raise the export gate from once-per-session to
once-per-trial, and say so in the clinician-facing instructions.
```

- [ ] **Step 2: Commit**

```bash
git add webapp/docs/eviction-soak-test.md
git commit -m "docs(webapp): add the 7-day eviction soak test protocol"
```

---

## Self-Review

**Spec coverage.** §3.1 eviction/install → Tasks 1, 2, 7. §3.1a volatile-cache stance → Tasks 4, 6 (the export gate is the mechanism). §3.2 stores → Task 3. §3.3 params field list → Task 4, enforced by `PARAM_FIELDS` and a test that rejects extra keys. §3.4 export contract → Task 5, which reuses Plan 1's already-pinned Rust emitter verbatim rather than reimplementing it. §3.5 derived-not-persisted → Tasks 4 and 5, both with explicit tests that a composite is dropped. §7 offline → Task 1.

**Deliberately out of scope:** participant *management* UI (creating and picking participants) is U8 and belongs to Plan 3 — Task 6 assumes a `currentSession` and a `currentSide()` exist. If Plan 3 has not landed when this runs, the executor should add the smallest possible stub (a single hard-coded test participant) rather than build U8 early, and say so.

**Type consistency.** `PARAM_FIELDS` is defined once in `session-store.js` and imported by `export.js` and its tests. `STORES` is defined once in `db.js`. Index names `by_session`/`by_patient` are used identically in Task 3's implementation, Task 3's tests, and Task 6's `getAll` call. `BUILD_ID` from Task 1 is the `algorithm_version` written in Task 6 — one identifier for both the cache key and the provenance stamp, so a trial can always be traced to the exact wasm that scored it.

**Known gap, stated rather than hidden.** Task 3's tests drive a hand-written IndexedDB stand-in, not a real implementation, because Node 24 has no IndexedDB and no dependency may be added. They verify the *schema* — store names, key paths, indexes — and cannot verify that a real transaction commits. The first real proof is Task 7's soak test on a device. An executor who finds this insufficient should say so rather than adding `fake-indexeddb`.
