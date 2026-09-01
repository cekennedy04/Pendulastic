// IndexedDB access. Treated as a VOLATILE CACHE, not a record store: Safari
// deletes script-writable storage after 7 days of site inactivity unless the
// app is installed to the Home Screen, and even that exemption is a platform
// behaviour Apple can change. Losing this database must cost a convenience,
// never a record -- see session-store.js's export gate.

export const DB_NAME = 'pendulastic';
export const DB_VERSION = 2;

// The logical identity of a MAS assessment, as an index keyPath. Defined
// here because this module owns the schema; mas-store.js's masIdentity()
// must produce values in this same order, and tests/db.test.js cross-checks
// the two rather than leaving it to a comment.
export const MAS_IDENTITY_KEYPATH = ['patient_id', 'leg', 'condition', 'assessed_date'];

export const STORES = {
  patients: 'patients',
  sessions: 'sessions',
  trials: 'trials',
  settings: 'settings',
  mas: 'mas',
};

export function openDb(idb) {
  return new Promise((resolve, reject) => {
    const req = idb.open(DB_NAME, DB_VERSION);
    // Branched on oldVersion, NOT on objectStoreNames.contains(). The v1
    // block below only runs for a database that has never existed; a device
    // upgrading from v1 already contains those three stores, so a
    // contains()-guarded block would silently never run and the v2 work
    // would be skipped on exactly the installs that need it. This is the
    // trap the note that used to live here warned about -- it is now
    // structural rather than advisory.
    req.onupgradeneeded = (e) => {
      const db = e.target.result;
      const tx = e.target.transaction;

      if (e.oldVersion < 1) {
        db.createObjectStore(STORES.patients, { keyPath: 'id' });
        const s = db.createObjectStore(STORES.sessions, { keyPath: 'id' });
        s.createIndex('by_patient', 'patient_id');
        const t = db.createObjectStore(STORES.trials, { keyPath: 'id' });
        // Without this, rendering one session's trials means scanning every
        // trial ever recorded on the device.
        t.createIndex('by_session', 'session_id');
      }

      if (e.oldVersion < 2) {
        if (!db.objectStoreNames.contains(STORES.settings)) {
          // Active participant, last-used side, and MAS form drafts.
          db.createObjectStore(STORES.settings, { keyPath: 'key' });
        }
        if (!db.objectStoreNames.contains(STORES.mas)) {
          const m = db.createObjectStore(STORES.mas, { keyPath: 'id' });
          m.createIndex('by_patient', 'patient_id');
          // The logical identity of an assessment. Enforced by the engine so
          // a duplicate is impossible regardless of which view writes -- a
          // view-layer check only binds the view that remembers to run it.
          // `id` stays a UUID rather than a value derived from this tuple:
          // the components are free text and mutable, so a derived key would
          // both collide on delimiters (participant "P_1" + leg "left" vs
          // "P" + "1_left") and, on an edit, write a SECOND record under the
          // new key while stranding the original.
          m.createIndex('by_identity', MAS_IDENTITY_KEYPATH, { unique: true });
        }
        backfillPatientAnchors(tx);
      }
    };
    req.onsuccess = (e) => resolve(e.target.result);
    req.onerror = () => reject(req.error || new Error('indexedDB open failed'));
    // A versionchange upgrade cannot start while another context -- a second
    // tab, or the browser tab alongside the installed Home Screen app -- still
    // holds this database open at the old version. IndexedDB fires `blocked`
    // and then just waits, indefinitely. Before v2 there was no second version
    // to upgrade TO, so this could never fire; the moment DB_VERSION moved to
    // 2 it became reachable, and with no handler openDb would neither resolve
    // nor reject -- a blank screen with nothing to tell the operator why.
    // Rejecting is strictly better than hanging: the message names the fix,
    // and if the other context does close, the upgrade still completes (this
    // promise has already settled, so the later resolve is a no-op) and the
    // next load opens cleanly.
    req.onblocked = () => reject(new Error(
      'Pendulastic is open in another tab or window. Close it, then reload this page to finish updating.',
    ));
  });
}

// Every patient_id a session references must resolve to a patients row.
// Pure, so the rule is testable without a database; backfillPatientAnchors
// below is the thin IndexedDB plumbing around it.
//
// Two cases, both produced by the release that removed app.js's hardcoded
// FIXED_PATIENT_ID:
//
//  - A session references a patient with no row. Not reachable through any
//    shipped code path, but the invariant is cheap to guarantee and a
//    dangling reference would make those trials invisible AND unexportable.
//  - The row IS there and is the hardcoded 'fixed-test-participant' every
//    pre-v2 install has. Deleting it would strand every trial recorded
//    before this release, so it is flagged `legacy` instead and the
//    participant picker lists and exports it like any other.
export function legacyPatientPatches(sessions = [], patients = [], { now = Date.now() } = {}) {
  const byId = new Map(patients.filter(Boolean).map((p) => [p.id, p]));
  const patches = [];
  const seen = new Set();

  for (const s of sessions) {
    if (!s || s.patient_id == null) continue;
    if (byId.has(s.patient_id) || seen.has(s.patient_id)) continue;
    seen.add(s.patient_id);
    patches.push({
      id: s.patient_id,
      clinic_patient_id: `UNASSIGNED-${String(s.patient_id).slice(0, 8)}`,
      created_at: now,
      legacy: true,
    });
  }

  const fixed = byId.get('fixed-test-participant');
  if (fixed && fixed.legacy !== true) patches.push({ ...fixed, legacy: true });

  return patches;
}

// Runs inside the versionchange transaction. Deliberately touches only the
// `patients` store: rewriting `trials` here would put the only on-device
// copy of clinical data inside a transaction that can abort part-way, to
// solve a problem a handful of upserts already solves.
function backfillPatientAnchors(tx) {
  if (!tx) return;
  const sessionsReq = tx.objectStore(STORES.sessions).getAll();
  const patientsStore = tx.objectStore(STORES.patients);
  const patientsReq = patientsStore.getAll();
  let sessions = null;
  let patients = null;
  const apply = () => {
    if (sessions === null || patients === null) return;
    for (const p of legacyPatientPatches(sessions, patients)) patientsStore.put(p);
  };
  sessionsReq.onsuccess = () => { sessions = sessionsReq.result || []; apply(); };
  patientsReq.onsuccess = () => { patients = patientsReq.result || []; apply(); };
}

// Single-record read. getAll already covers the list cases; the settings
// store is looked up one key at a time.
export function getOne(db, storeName, key) {
  return new Promise((resolve, reject) => {
    const tx = db.transaction(storeName, 'readonly');
    const req = tx.objectStore(storeName).get(key);
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

export function put(db, storeName, record) {
  return new Promise((resolve, reject) => {
    const tx = db.transaction(storeName, 'readwrite');
    tx.objectStore(storeName).put(record);
    tx.oncomplete = () => resolve();
    tx.onerror = () => reject(tx.error);
    // abort is handled separately from error: IndexedDB does not guarantee a
    // transaction's error event fires before it aborts -- an exception thrown
    // inside a request's own onsuccess, or (inconsistently across engines,
    // notably WebKit/Safari) a QuotaExceededError, can go straight to abort
    // with the transaction's error event never firing at all. Without this,
    // those paths leave the promise neither resolved nor rejected forever,
    // which for this module's "never a silent record loss" stance is worse
    // than a clean rejection: the caller gets no signal and cannot retry.
    tx.onabort = () => reject(tx.error || new Error('indexedDB transaction aborted'));
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
