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
    // NOTE for a future DB_VERSION bump: every createIndex() call below sits
    // nested inside the createObjectStore() branch that only runs the first
    // time a store is created. A v2 migration that wants to add an index to
    // a store someone already has from v1 must NOT extend these blocks --
    // objectStoreNames.contains() will be true for them and the block will
    // silently never run. Branch on event.oldVersion instead (e.g.
    // `if (e.oldVersion < 2) { db.transaction(...).objectStore(...).createIndex(...) }`
    // using the versionchange transaction IDBOpenDBRequest exposes), or the
    // missing index surfaces later as an unrelated runtime failure.
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
