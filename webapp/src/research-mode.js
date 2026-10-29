// Research recording mode: a gated raw recorder for bench/SSR validation,
// separate from the scored-capture flow. `?research=1` turns it on and the
// choice is remembered so it survives the query param dropping off; `?research=0`
// turns it off. When off, nothing about the clinical/scored flow changes.

export const RESEARCH_KEY = 'pendulastic_research';

// Pure but for the two injected side-channels (the URL query and storage), both
// defaulted so callers pass nothing in the browser and fakes in tests.
export function isResearchMode(search = location.search, storage = safeLocalStorage()) {
  const params = new URLSearchParams(search);
  if (params.has('research')) {
    const v = params.get('research');
    const on = v !== '0' && v !== '';
    // Persist the explicit choice. A storage that throws (private mode, blocked
    // site data) must not break the toggle -- the flag still holds for THIS
    // load, it just will not be remembered.
    try { on ? storage.setItem(RESEARCH_KEY, '1') : storage.removeItem(RESEARCH_KEY); } catch { /* not persisted */ }
    return on;
  }
  try { return storage.getItem(RESEARCH_KEY) === '1'; } catch { return false; }
}

// localStorage access can throw before the first call (a sandboxed frame), so
// even reaching for it is guarded.
function safeLocalStorage() {
  try { return globalThis.localStorage; } catch { return null; }
}
