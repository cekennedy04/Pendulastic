// The storage seam: two-query traversal, and the wasm scorer adapter.
//
// Node 24 has no IndexedDB and `init()` fetches a URL that means nothing here,
// so this follows db.test.js's approach -- a minimal stand-in that honours the
// contract getAll() actually uses, rather than pulling in a dependency.

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { loadPatientTrials } from '../src/trends-loader.js';
import { STORES } from '../src/db.js';

/**
 * Enough of IDBDatabase for `getAll(db, store, index, key)`: a transaction,
 * an object store, an index, and a `getAll(key)` returning a request whose
 * `onsuccess` fires on a later tick.
 */
function fakeDb(data) {
  const asked = [];
  return {
    asked,
    transaction(storeName) {
      return {
        objectStore(name) {
          assert.equal(name, storeName);
          return {
            index(indexName) {
              return {
                getAll(key) {
                  asked.push({ store: name, index: indexName, key });
                  const req = {};
                  queueMicrotask(() => {
                    req.result = (data[name] || []).filter(
                      (r) => r[indexKey(indexName)] === key);
                    req.onsuccess?.();
                  });
                  return req;
                },
              };
            },
          };
        },
      };
    },
  };
}

function indexKey(indexName) {
  return { by_patient: 'patient_id', by_session: 'session_id' }[indexName];
}

test('a patient trend walks sessions first, then that session\'s trials', () => {
  // The traversal exists because STORES.trials is indexed by_session only --
  // a trial carries session_id, never a patient id. Getting this wrong would
  // mean either an empty chart or a schema migration on a device holding the
  // only copy of a session.
  const db = fakeDb({
    [STORES.sessions]: [
      { id: 's1', patient_id: 'p1' },
      { id: 's2', patient_id: 'p1' },
      { id: 's3', patient_id: 'other' },
    ],
    [STORES.trials]: [
      { id: 't1', session_id: 's1' },
      { id: 't2', session_id: 's2' },
      { id: 't3', session_id: 's3' },
    ],
  });
  return loadPatientTrials(db, 'p1').then((trials) => {
    assert.deepEqual(trials.map((t) => t.id).sort(), ['t1', 't2']);
    // s3 belongs to another patient and must never be queried for.
    assert.deepEqual(db.asked[0], { store: STORES.sessions, index: 'by_patient', key: 'p1' });
    const sessionKeys = db.asked.slice(1).map((a) => a.key).sort();
    assert.deepEqual(sessionKeys, ['s1', 's2']);
  });
});

test('a patient with no sessions yields no trials rather than every trial', () => {
  // A missing index arg to getAll returns the WHOLE store. If the traversal
  // ever degraded to that, one patient's chart would silently show the
  // device's entire history.
  const db = fakeDb({
    [STORES.sessions]: [{ id: 's1', patient_id: 'someone-else' }],
    [STORES.trials]: [{ id: 't1', session_id: 's1' }],
  });
  return loadPatientTrials(db, 'nobody').then((trials) => {
    assert.deepEqual(trials, []);
  });
});

test('trials from several sessions are flattened into one list', () => {
  const db = fakeDb({
    [STORES.sessions]: [{ id: 's1', patient_id: 'p1' }, { id: 's2', patient_id: 'p1' }],
    [STORES.trials]: [
      { id: 'a', session_id: 's1' },
      { id: 'b', session_id: 's1' },
      { id: 'c', session_id: 's2' },
    ],
  });
  return loadPatientTrials(db, 'p1').then((trials) => {
    assert.equal(trials.length, 3);
    assert.deepEqual(trials.map((t) => t.id).sort(), ['a', 'b', 'c']);
  });
});

test('a session with no trials contributes nothing and does not throw', () => {
  const db = fakeDb({
    [STORES.sessions]: [{ id: 's1', patient_id: 'p1' }, { id: 'empty', patient_id: 'p1' }],
    [STORES.trials]: [{ id: 'a', session_id: 's1' }],
  });
  return loadPatientTrials(db, 'p1').then((trials) => {
    assert.deepEqual(trials.map((t) => t.id), ['a']);
  });
});
