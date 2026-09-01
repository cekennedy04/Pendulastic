import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  openDb, put, getAll, getOne, legacyPatientPatches,
  STORES, DB_VERSION, MAS_IDENTITY_KEYPATH,
} from '../src/db.js';
import { masIdentity } from '../src/mas-store.js';

// Node 24 has no IndexedDB. Rather than pull in a fake-indexeddb dependency,
// these tests drive the schema logic through the same upgrade callback a real
// browser would, using a minimal stand-in that records what was asked for.
//
// The original fake modeled only a FRESH database (oldVersion 0). v2 adds an
// upgrade path, so the fake now takes the version the device is coming from
// -- the branch that decides whether a store already exists is exactly what
// db.js's header warns is easy to get wrong.
// A store descriptor doubles as the store itself. The migration does not
// merely CREATE stores -- backfillPatientAnchors reads `sessions` and
// `patients` and upserts into `patients` -- so a descriptor carrying only
// createIndex() is not enough: openDb would throw inside the upgrade handler,
// onsuccess would never fire, and every await openDb() would hang forever
// rather than fail. (It did. See the ledger's Ruling F.)
function makeStore(name, opts, rows = []) {
  return {
    name, opts, indexes: [], rows: rows.map((r) => ({ ...r })),
    createIndex(i, kp, o) { this.indexes.push({ name: i, keyPath: kp, options: o || {} }); },
    // Real IDB requests settle asynchronously, and the migration depends on
    // it: it fires two getAll()s and patches only once BOTH have landed.
    // Resolving synchronously here would skip that interleaving entirely.
    getAll() {
      const req = {};
      queueMicrotask(() => { req.result = this.rows.map((r) => ({ ...r })); req.onsuccess?.({ target: req }); });
      return req;
    },
    put(record) {
      const i = this.rows.findIndex((r) => r && r.id === record.id);
      if (i === -1) this.rows.push(record); else this.rows[i] = record;
      return {};
    },
  };
}

// The original fake modeled only a FRESH database (oldVersion 0). v2 adds an
// upgrade path, so the fake now takes the version the device is coming from
// -- the branch that decides whether a store already exists is exactly what
// db.js's header warns is easy to get wrong.
//
// `existing` is either a store name or { name, rows }, so a test can stand up
// a v1 device that already holds real sessions and patients.
function fakeIndexedDBAt(oldVersion, existing = []) {
  const created = existing.map((e) => (
    typeof e === 'string' ? makeStore(e, null) : makeStore(e.name, null, e.rows)
  ));
  return {
    created,
    open(name, version) {
      const req = {};
      (async () => {
        await null; // let the caller attach its handlers first
        const db = {
          objectStoreNames: { contains: (n) => created.some((c) => c.name === n) },
          createObjectStore(n, opts) {
            const store = makeStore(n, opts);
            created.push(store);
            return store;
          },
        };
        req.result = db;
        req.transaction = { objectStore: (n) => created.find((c) => c.name === n) };
        req.onupgradeneeded?.({ target: req, oldVersion, newVersion: version });
        // A real versionchange transaction COMMITS before onsuccess fires, so
        // by then every getAll() the migration issued has already delivered.
        // Draining the queue here reproduces that ordering; firing onsuccess
        // immediately would let a test observe the database "open" with the
        // backfill still in flight, a state a browser never presents.
        await new Promise((r) => setTimeout(r, 0));
        req.onsuccess?.({ target: { result: db } });
      })();
      return req;
    },
  };
}

const storeNames = (idb) => idb.created.map((c) => c.name).sort();
const findStore = (idb, n) => idb.created.find((c) => c.name === n);

test('the schema creates exactly the five stores the spec names', async () => {
  const idb = fakeIndexedDBAt(0);
  await openDb(idb);
  assert.deepEqual(storeNames(idb), ['mas', 'patients', 'sessions', 'settings', 'trials']);
});

test('trials are indexed by session so a session view does not scan every trial', async () => {
  const idb = fakeIndexedDBAt(0);
  await openDb(idb);
  const trials = findStore(idb, 'trials');
  assert.ok(trials.indexes.some((x) => x.keyPath === 'session_id'), 'missing session_id index');
});

test('sessions are indexed by patient', async () => {
  const idb = fakeIndexedDBAt(0);
  await openDb(idb);
  const sessions = findStore(idb, 'sessions');
  assert.ok(sessions.indexes.some((x) => x.keyPath === 'patient_id'), 'missing patient_id index');
});

test('STORES names match what openDb creates', async () => {
  const idb = fakeIndexedDBAt(0);
  await openDb(idb);
  assert.deepEqual(Object.values(STORES).sort(), storeNames(idb));
});

test('DB_VERSION is 2', () => {
  assert.equal(DB_VERSION, 2);
});

test('a fresh database gets all five stores', async () => {
  const idb = fakeIndexedDBAt(0);
  await openDb(idb);
  assert.deepEqual(storeNames(idb), ['mas', 'patients', 'sessions', 'settings', 'trials']);
});

// db.js's header warns that every createIndex sits inside an
// objectStoreNames.contains() branch that is FALSE for a device upgrading
// from v1 -- so the v2 work must hang off oldVersion, not off contains().
test('a v1 device gains settings and mas without recreating v1 stores', async () => {
  const idb = fakeIndexedDBAt(1, ['patients', 'sessions', 'trials']);
  await openDb(idb);
  assert.deepEqual(storeNames(idb), ['mas', 'patients', 'sessions', 'settings', 'trials']);
  // v1 stores were pre-existing, so they must not have been re-created --
  // a re-create would have replaced them and dropped every stored trial.
  assert.equal(findStore(idb, 'trials').opts, null);
  assert.equal(findStore(idb, 'mas').opts.keyPath, 'id');
});

test('settings is keyed by `key`', async () => {
  const idb = fakeIndexedDBAt(0);
  await openDb(idb);
  assert.equal(findStore(idb, 'settings').opts.keyPath, 'key');
});

// The composite identity is enforced by the engine, not by a view. A view
// -level check would be bypassed by any other caller of the store.
test('mas carries a unique compound index over the identity tuple', async () => {
  const idb = fakeIndexedDBAt(0);
  await openDb(idb);
  const idx = findStore(idb, 'mas').indexes.find((i) => i.name === 'by_identity');
  assert.deepEqual(idx.keyPath, ['patient_id', 'leg', 'condition', 'assessed_date']);
  assert.equal(idx.options.unique, true);
});

// masIdentity() builds the lookup key for this index. If the two orders ever
// diverge, every lookup silently misses and the duplicate check stops working
// -- with no error anywhere. This is the test that catches it.
test('masIdentity agrees with the index keyPath', () => {
  const record = {
    patient_id: 'p', leg: 'left', condition: 'rest', assessed_date: '2026-08-31',
  };
  assert.deepEqual(masIdentity(record), MAS_IDENTITY_KEYPATH.map((k) => record[k]));
});

test('mas also carries a non-unique by_patient index', async () => {
  const idb = fakeIndexedDBAt(0);
  await openDb(idb);
  const idx = findStore(idb, 'mas').indexes.find((i) => i.name === 'by_patient');
  assert.equal(idx.keyPath, 'patient_id');
  assert.notEqual(idx.options.unique, true);
});

// ---- legacyPatientPatches ------------------------------------------------
// The invariant: every patient_id a session references resolves to a
// patients row. Trials are never rewritten -- see the spec's migration note.
test('a session pointing at a missing patient gets an anchor row', () => {
  const patches = legacyPatientPatches(
    [{ id: 's1', patient_id: 'ghost-abcdefgh' }], [], { now: 5 });
  assert.equal(patches.length, 1);
  assert.equal(patches[0].id, 'ghost-abcdefgh');
  assert.equal(patches[0].legacy, true);
  assert.match(patches[0].clinic_patient_id, /^UNASSIGNED-/);
});

test('a session whose patient already exists produces no patch', () => {
  const patches = legacyPatientPatches(
    [{ id: 's1', patient_id: 'p1' }], [{ id: 'p1', clinic_patient_id: 'P-1' }], { now: 5 });
  assert.deepEqual(patches, []);
});

test('two sessions sharing one missing patient produce a single anchor', () => {
  const patches = legacyPatientPatches(
    [{ id: 's1', patient_id: 'g' }, { id: 's2', patient_id: 'g' }], [], { now: 5 });
  assert.equal(patches.length, 1);
});

// The hardcoded participant every pre-v2 install already has on disk. Its
// record is NOT deleted -- deleting it would strand every trial recorded
// before this release -- it is flagged so the UI can label and export it.
test('the hardcoded test participant is flagged legacy, not removed', () => {
  const existing = { id: 'fixed-test-participant', clinic_patient_id: 'TEST-PARTICIPANT' };
  const patches = legacyPatientPatches([], [existing], { now: 5 });
  assert.equal(patches.length, 1);
  assert.equal(patches[0].id, 'fixed-test-participant');
  assert.equal(patches[0].legacy, true);
  assert.equal(patches[0].clinic_patient_id, 'TEST-PARTICIPANT');
});

test('an already-flagged legacy participant is not patched again', () => {
  const existing = { id: 'fixed-test-participant', clinic_patient_id: 'TEST-PARTICIPANT', legacy: true };
  assert.deepEqual(legacyPatientPatches([], [existing], { now: 5 }), []);
});

test('a session with no patient_id is skipped rather than anchored to undefined', () => {
  assert.deepEqual(legacyPatientPatches([{ id: 's1' }, null], [], { now: 5 }), []);
});

// --- put()/getAll() promise-wiring stand-in -------------------------------
//
// The tests above drive openDb()'s upgrade callback. put() and getAll() are
// driven through a different fake: a minimal IDBDatabase whose
// transaction()/objectStore()/index() calls are recorded, and whose
// tx.oncomplete / tx.onerror / tx.onabort / request.onsuccess / onerror are
// plain properties the test fires by hand. This is plain callback-and-object
// logic (which event settles the promise, what gets passed to .index()/
// .getAll()), not a simulation of real commit durability, quota errors, or
// key-range semantics -- those genuinely need a browser and stay out of
// scope here.
function fakeDbWithTx() {
  const state = {};
  const db = {
    transaction(storeName, mode) {
      const tx = {};
      state.tx = tx;
      state.storeName = storeName;
      state.mode = mode;
      const store = {
        put(record) {
          state.putRecord = record;
          const req = {};
          state.putReq = req;
          return req;
        },
        getAll(...args) {
          state.indexName = undefined;
          state.getAllTarget = 'store';
          state.getAllArgs = args;
          const req = {};
          state.getAllReq = req;
          return req;
        },
        index(name) {
          state.indexName = name;
          return {
            getAll(...args) {
              state.getAllTarget = 'index';
              state.getAllArgs = args;
              const req = {};
              state.getAllReq = req;
              return req;
            },
          };
        },
      };
      tx.objectStore = (name) => {
        state.objectStoreName = name;
        return store;
      };
      return tx;
    },
  };
  return { db, state };
}

test('put resolves on tx.oncomplete, not merely on the underlying request succeeding', async () => {
  const { db, state } = fakeDbWithTx();
  const p = put(db, STORES.trials, { id: 1 });
  let settled = false;
  p.then(() => {
    settled = true;
  });
  // Real IndexedDB fires the put request's own success before the
  // transaction commits. Firing it here -- even though put() must ignore it
  // -- proves resolution isn't accidentally wired to request success instead
  // of an actual commit.
  state.putReq.onsuccess?.({ target: state.putReq });
  await Promise.resolve();
  await Promise.resolve();
  assert.equal(settled, false, 'resolved before tx.oncomplete fired');
  state.tx.oncomplete();
  await p;
  assert.equal(settled, true);
});

test('put rejects on tx.onerror', async () => {
  const { db, state } = fakeDbWithTx();
  const p = put(db, STORES.trials, { id: 1 });
  const err = new Error('put failed');
  state.tx.error = err;
  state.tx.onerror();
  await assert.rejects(p, (e) => e === err);
});

test('put rejects on tx.onabort (an aborted transaction must not hang forever)', async () => {
  const { db, state } = fakeDbWithTx();
  const p = put(db, STORES.trials, { id: 1 });
  assert.equal(
    typeof state.tx.onabort,
    'function',
    'put() never wired tx.onabort -- an abort (thrown exception in a request '
      + 'success handler, or a QuotaExceededError that some engines route '
      + 'straight to abort) would leave the promise pending forever',
  );
  const err = new Error('put aborted');
  state.tx.error = err;
  state.tx.onabort();
  await assert.rejects(p, (e) => e === err);
});

test('put rejects with a constructed error when tx.onabort fires and tx.error is null', async () => {
  const { db, state } = fakeDbWithTx();
  const p = put(db, STORES.trials, { id: 1 });
  state.tx.error = null;
  state.tx.onabort();
  await assert.rejects(p, (e) => e instanceof Error);
});

test('getAll with an index name reaches .index(name).getAll(key)', async () => {
  const { db, state } = fakeDbWithTx();
  const p = getAll(db, STORES.trials, 'by_session', 'session-1');
  state.getAllReq.result = ['trial-a'];
  state.getAllReq.onsuccess();
  const result = await p;
  assert.equal(state.getAllTarget, 'index');
  assert.equal(state.indexName, 'by_session');
  assert.deepEqual(state.getAllArgs, ['session-1']);
  assert.deepEqual(result, ['trial-a']);
});

test('getAll without an index name reaches the store directly', async () => {
  const { db, state } = fakeDbWithTx();
  const p = getAll(db, STORES.patients, undefined, 'patient-1');
  state.getAllReq.result = ['patient-x'];
  state.getAllReq.onsuccess();
  await p;
  assert.equal(state.getAllTarget, 'store');
  assert.equal(state.indexName, undefined);
});

test('getAll with key undefined calls getAll() with no argument, not getAll(undefined)', async () => {
  const { db, state } = fakeDbWithTx();
  const p = getAll(db, STORES.patients, undefined, undefined);
  state.getAllReq.result = [];
  state.getAllReq.onsuccess();
  await p;
  assert.deepEqual(state.getAllArgs, [], 'called getAll(undefined) instead of getAll()');
});

// ---- the migration, driven end to end -----------------------------------
// legacyPatientPatches is covered as a pure function above. These drive the
// real upgrade path -- openDb -> onupgradeneeded -> backfillPatientAnchors ->
// patientsStore.put -- because this migration runs over the only on-device
// copy of clinical data, and a pure-function test cannot show that the
// plumbing around it actually wires up. (The plan left this untested; the
// repaired fake makes it cheap. See Ruling F.)

test('a v1 device with a dangling session gets the anchor WRITTEN to patients', async () => {
  const idb = fakeIndexedDBAt(1, [
    { name: 'patients', rows: [] },
    { name: 'sessions', rows: [{ id: 's1', patient_id: 'ghost-abcdefgh' }] },
    { name: 'trials', rows: [{ id: 't1', session_id: 's1' }] },
  ]);
  await openDb(idb);
  const patients = findStore(idb, 'patients').rows;
  assert.equal(patients.length, 1);
  assert.equal(patients[0].id, 'ghost-abcdefgh');
  assert.equal(patients[0].legacy, true);
  assert.match(patients[0].clinic_patient_id, /^UNASSIGNED-/);
});

test('the migration leaves trials untouched', async () => {
  const trial = { id: 't1', session_id: 's1', params: { A0: 42 } };
  const idb = fakeIndexedDBAt(1, [
    { name: 'patients', rows: [] },
    { name: 'sessions', rows: [{ id: 's1', patient_id: 'ghost' }] },
    { name: 'trials', rows: [trial] },
  ]);
  await openDb(idb);
  assert.deepEqual(findStore(idb, 'trials').rows, [trial]);
});

test('a v1 device carrying the hardcoded participant has it flagged in place', async () => {
  const idb = fakeIndexedDBAt(1, [
    { name: 'patients', rows: [{ id: 'fixed-test-participant', clinic_patient_id: 'TEST-PARTICIPANT' }] },
    { name: 'sessions', rows: [{ id: 's1', patient_id: 'fixed-test-participant' }] },
    { name: 'trials', rows: [] },
  ]);
  await openDb(idb);
  const patients = findStore(idb, 'patients').rows;
  // Flagged, NOT duplicated and NOT deleted -- deleting it would strand every
  // trial recorded before this release.
  assert.equal(patients.length, 1);
  assert.equal(patients[0].id, 'fixed-test-participant');
  assert.equal(patients[0].clinic_patient_id, 'TEST-PARTICIPANT');
  assert.equal(patients[0].legacy, true);
});

test('a fresh v0 install has nothing to backfill', async () => {
  const idb = fakeIndexedDBAt(0);
  await openDb(idb);
  assert.deepEqual(findStore(idb, 'patients').rows, []);
});

// The spec states this as the post-migration invariant to assert, in these
// words: "every patient_id referenced by a sessions row resolves to a
// patients row". The tests above check the two mechanisms that produce it;
// this one checks the property itself, over a device messy enough that a
// mechanism could pass while the invariant still failed -- several sessions,
// some anchored, some dangling, one duplicate, one with no patient_id.
test('post-migration, every session patient_id resolves to a patients row', async () => {
  const idb = fakeIndexedDBAt(1, [
    {
      name: 'patients',
      rows: [
        { id: 'real-1', clinic_patient_id: 'P-001' },
        { id: 'fixed-test-participant', clinic_patient_id: 'TEST-PARTICIPANT' },
      ],
    },
    {
      name: 'sessions',
      rows: [
        { id: 's1', patient_id: 'real-1' },
        { id: 's2', patient_id: 'fixed-test-participant' },
        { id: 's3', patient_id: 'ghost-one' },
        { id: 's4', patient_id: 'ghost-one' },
        { id: 's5', patient_id: 'ghost-two' },
        { id: 's6' },
      ],
    },
    { name: 'trials', rows: [] },
  ]);
  await openDb(idb);

  const sessions = findStore(idb, 'sessions').rows;
  const ids = new Set(findStore(idb, 'patients').rows.map((p) => p.id));
  for (const s of sessions) {
    if (s.patient_id == null) continue;
    assert.ok(ids.has(s.patient_id), `session ${s.id} references unanchored ${s.patient_id}`);
  }
  // And the anchoring did not multiply rows: 2 pre-existing + 2 distinct ghosts.
  assert.equal(findStore(idb, 'patients').rows.length, 4);
});
