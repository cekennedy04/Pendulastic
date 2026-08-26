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
