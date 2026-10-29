import { test } from 'node:test';
import assert from 'node:assert/strict';
import { isResearchMode, RESEARCH_KEY } from '../src/research-mode.js';

function fakeStorage(initial = {}) {
  const m = new Map(Object.entries(initial));
  return {
    getItem: (k) => (m.has(k) ? m.get(k) : null),
    setItem: (k, v) => m.set(k, String(v)),
    removeItem: (k) => m.delete(k),
  };
}

test('?research=1 enables research mode and persists the flag', () => {
  const s = fakeStorage();
  assert.equal(isResearchMode('?research=1', s), true);
  assert.equal(s.getItem(RESEARCH_KEY), '1', 'the flag must stick after the query param drops');
});

test('?research=0 disables research mode and clears the stored flag', () => {
  const s = fakeStorage({ [RESEARCH_KEY]: '1' });
  assert.equal(isResearchMode('?research=0', s), false);
  assert.equal(s.getItem(RESEARCH_KEY), null);
});

test('with no query param, the stored flag decides', () => {
  assert.equal(isResearchMode('', fakeStorage({ [RESEARCH_KEY]: '1' })), true);
  assert.equal(isResearchMode('', fakeStorage()), false);
});

test('a storage that throws (private mode) never crashes and stays off by default', () => {
  const throwing = {
    getItem() { throw new Error('blocked'); },
    setItem() { throw new Error('blocked'); },
    removeItem() { throw new Error('blocked'); },
  };
  assert.equal(isResearchMode('', throwing), false);
  // The query param still turns it on for this load even if it cannot persist.
  assert.equal(isResearchMode('?research=1', throwing), true);
});
