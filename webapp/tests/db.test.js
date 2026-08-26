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
