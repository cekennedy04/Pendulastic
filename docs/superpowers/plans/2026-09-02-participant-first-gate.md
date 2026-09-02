# Participant-First Gate Implementation Plan (Unit A)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Open the app on participant selection whenever no participant is set — on first launch and after Close Session — so no trial can be recorded against an unchosen participant.

**Architecture:** Two pure functions carry the whole decision. `initialView({patient})` says which view a launch lands on; `resolveActivePatient({activeSetting, patients})` says whether a stored setting, a deliberate clear, or a lone legacy participant wins. `app.js` becomes thin plumbing around both: it navigates to `initialView(...)` once session init resolves, and its `ensurePatient()` delegates to `resolveActivePatient(...)`. Close Session writes an explicit cleared-sentinel so the next launch re-prompts.

**Tech Stack:** Plain ES modules, plain CSS, no framework, no bundler, no new dependency. Tests are `node --test` over pure functions.

**Spec:** `docs/superpowers/specs/2026-09-02-participant-gate-and-longitudinal-trends-design.md` (Unit A, §3)

## Global Constraints

Every task's requirements implicitly include this section.

- **No framework, no bundler, no CSS/JS build step.** Do not add a dependency.
- **`#banner` is unconditional** and nothing may cover it. `#install-gate` takes precedence over all view routing — it is `position: fixed; inset: 0; z-index: 100` and is shown before any control is wired.
- **Views use `.view` / `.view.active`**, never the `hidden` attribute.
- **DOM-touching code must not run on import.** View modules export factory functions; module top level stays import-safe under `node --test`.
- **Any task that adds, removes, or MODIFIES a file under `webapp/src/` must run `npm run build:shell` before committing.** `BUILD_ID` is a content hash over the shell file list *and their bytes*, and `sw.js` keys its offline cache on it — a stale key leaves an installed phone serving old code forever. Never hand-edit `src/build-id.js`. Never run `build:wasm` just to refresh it (that recomputes `ALGORITHM_VERSION`, which must track the wasm alone).
- **Recording stays gated on participant AND leg.** The existing check at the top of the `#start` handler is not touched by this plan and its behaviour must not change.
- Run tests with `cd webapp && npm test`. Baseline is **239 passing** at commit `da98d85`.
- **Count tests before and after every append.** A heredoc that never ran reports the file's original count and looks like success.

---

## File Structure

**Modified:**
- `webapp/src/views/session.js` — add `initialView`, `resolveActivePatient`; render the prompt line
- `webapp/src/app.js` — navigate to `initialView(...)` at startup; `ensurePatient()` delegates to `resolveActivePatient(...)`; Close Session clears the active participant
- `webapp/index.html` — the prompt line element
- `webapp/tests/app.test.js` — tests for both pure functions

**Created:** none.

**Schema:** unchanged. `DB_VERSION` stays 2. No migration.

---

## Spec refinement resolved here

The spec's §3.1 says Close Session should cause a re-prompt. The existing
`ensurePatient()` defeats that: after clearing the setting it falls through to

```js
if (patients.length === 1) return patients[0];
```

so on a device with exactly one participant — the common case — the cleared
participant is immediately re-adopted and the operator is never prompted.

That single-participant adoption is not a bug; it exists so a v1 install
carrying one legacy participant is not forced to make a choice mid-study. It
must be kept for that case and skipped for a deliberate clear.

**Resolution:** distinguish *never set* from *deliberately cleared* by whether
the `settings` row EXISTS:

| `settings` row | Meaning | Result |
| --- | --- | --- |
| absent | never chosen (v1 install) | adopt a lone participant, else `null` |
| present, `value` set | a chosen participant | that participant |
| present, `value` null | deliberately cleared by Close Session | `null` — re-prompt |

---

### Task 1: `initialView` and startup routing

Land the launch rule. No change to `ensurePatient` yet, so on a device with a
participant nothing observable changes; on a fresh device the app opens on
session instead of home.

**Files:**
- Modify: `webapp/src/views/session.js`
- Modify: `webapp/src/app.js`
- Modify: `webapp/index.html`
- Test: `webapp/tests/app.test.js`

**Interfaces:**
- Consumes: `createRouter` (`src/router.js`), `SETTING_KEYS` (`src/views/session.js`)
- Produces: `initialView({ patient }) -> 'session' | 'home'`

- [ ] **Step 1: Write the failing test**

Append to `webapp/tests/app.test.js`:

```js
// ---- participant-first gate (unit A) -------------------------------------
// A trial recorded against an unchosen participant is unattributable after
// the fact, so the app opens on selection rather than on the tiles.
test('a device with no participant opens on the session view', () => {
  assert.equal(initialView({ patient: null }), 'session');
});

test('a device with a participant opens on home', () => {
  assert.equal(initialView({ patient: { id: 'p1', clinic_patient_id: 'P-1' } }), 'home');
});

test('a missing or empty argument is treated as no participant', () => {
  assert.equal(initialView(), 'session');
  assert.equal(initialView({}), 'session');
});
```

Extend that file's session-view import line to:

```js
import {
  patientLabel, nextParticipantState, SETTING_KEYS, initialView,
} from '../src/views/session.js';
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd webapp && node --test tests/app.test.js`
Expected: FAIL — `initialView is not a function`.

Record the test count before and after this append; it must rise by exactly 3.

- [ ] **Step 3: Write the implementation**

Add to `webapp/src/views/session.js`, below `patientLabel`:

```js
// Which view a launch lands on. Pure so the launch rule is testable without a
// DOM or a database.
//
// Opening on the tiles when no participant is set invites the operator to tap
// Record Trial first; the Start handler would then refuse, which teaches the
// gate by failure. Opening on selection teaches it by layout instead.
export function initialView({ patient } = {}) {
  return patient ? 'home' : 'session';
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd webapp && node --test tests/app.test.js`
Expected: PASS, count up by exactly 3.

- [ ] **Step 5: Add the prompt line to the markup**

In `webapp/index.html`, inside `#view-session`, immediately above the
Participant `<div class="card">`:

```html
      <p id="participant-required" class="field-status" hidden>
        Select or add a participant to begin.
      </p>
```

- [ ] **Step 6: Render the prompt line**

In `webapp/src/views/session.js`, inside `render()`, immediately after the
`errEl` block and before the pending-count block:

```js
    // Shown only when there is nothing selected. This is guidance, not an
    // error, so it uses .field-status rather than .field-error.
    const needEl = el('participant-required');
    if (needEl) needEl.hidden = Boolean(patient);
```

- [ ] **Step 7: Navigate there at startup**

In `webapp/src/app.js`, replace the startup kickoff:

```js
  ensureSessionReady().catch((err) => {
    console.error('session init failed', err);
    el('session-status').textContent =
      `Could not open local storage: ${err instanceof Error ? err.message : String(err)}. Trials will not be saved.`;
  });
```

with:

```js
  // Routing waits on session init because `currentPatient` is not known until
  // it resolves. `initialView` returning 'home' is a no-op -- the markup
  // already has #view-home active -- so the only observable effect is landing
  // on selection when nothing is set.
  ensureSessionReady()
    .then(() => {
      router.navigate(initialView({ patient: currentPatient }));
    })
    .catch((err) => {
      console.error('session init failed', err);
      el('session-status').textContent =
        `Could not open local storage: ${err instanceof Error ? err.message : String(err)}. Trials will not be saved.`;
    });
```

Extend `app.js`'s session-view import to include `initialView`:

```js
import { patientLabel, nextParticipantState, createSessionView, SETTING_KEYS, initialView } from './views/session.js';
```

- [ ] **Step 8: Regenerate the shell and run the suite**

Run: `cd webapp && npm run build:shell && npm test`
Expected: PASS, 242 tests (239 baseline + 3).

- [ ] **Step 9: Verify in a browser**

Serve `webapp/` over plain HTTP on localhost (not `dev_server.py` — its
self-signed cert is rejected by headless Chromium) and drive the whole check
from ONE `browse eval` script, stashing the result on `window` because `eval`
does not await a returned promise:

```js
window.__a = 'running';
(async () => {
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  document.getElementById('install-gate').hidden = true;
  await sleep(1200); // let ensureSessionReady resolve and route
  const active = (document.querySelector('.view.active') || { id: 'NONE' }).id;
  window.__a = 'landed on: ' + active
    + ' | prompt shown: ' + !document.getElementById('participant-required').hidden;
})();
'started'
```

Expected on a clean database: `landed on: view-session | prompt shown: true`.

- [ ] **Step 10: Commit**

```bash
git add webapp/src/views/session.js webapp/src/app.js webapp/index.html webapp/tests/app.test.js webapp/src/build-id.js
git commit -m "feat: open on participant selection when none is set"
```

---

### Task 2: Close Session clears the active participant

Make the re-prompt true for the second patient of the day, not just the first.

**Files:**
- Modify: `webapp/src/views/session.js`
- Modify: `webapp/src/app.js`
- Test: `webapp/tests/app.test.js`

**Interfaces:**
- Consumes: `SETTING_KEYS` (`src/views/session.js`)
- Produces: `resolveActivePatient({ activeSetting, patients }) -> patient | null`

- [ ] **Step 1: Write the failing test**

Append to `webapp/tests/app.test.js`:

```js
// The three-way distinction that makes Close Session re-prompt without
// breaking a v1 install that never chose a participant. See the plan's
// "Spec refinement resolved here".
const pats = [{ id: 'p1', clinic_patient_id: 'P-1' }, { id: 'p2', clinic_patient_id: 'P-2' }];

test('a stored participant id resolves to that participant', () => {
  const got = resolveActivePatient({ activeSetting: { value: 'p2' }, patients: pats });
  assert.equal(got.id, 'p2');
});

// A row that EXISTS carrying no value is Close Session's deliberate clear.
test('a deliberately cleared setting re-prompts instead of re-adopting', () => {
  assert.equal(
    resolveActivePatient({ activeSetting: { key: 'active-patient', value: null }, patients: [pats[0]] }),
    null,
  );
});

// No row at all is a pre-v2 install that never chose. Adopting its single
// legacy participant is what keeps a clinician mid-study from being asked a
// question they never had to answer before.
test('no setting at all adopts a lone participant', () => {
  const got = resolveActivePatient({ activeSetting: undefined, patients: [pats[0]] });
  assert.equal(got.id, 'p1');
});

test('no setting and several participants forces a choice', () => {
  assert.equal(resolveActivePatient({ activeSetting: undefined, patients: pats }), null);
});

test('no setting and no participants resolves to nothing', () => {
  assert.equal(resolveActivePatient({ activeSetting: undefined, patients: [] }), null);
  assert.equal(resolveActivePatient({}), null);
});

// A stored id whose participant is gone must not crash or silently adopt.
test('a stored id that matches nothing resolves to nothing', () => {
  assert.equal(resolveActivePatient({ activeSetting: { value: 'ghost' }, patients: pats }), null);
});
```

Extend the session-view import line to add `resolveActivePatient`.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd webapp && node --test tests/app.test.js`
Expected: FAIL — `resolveActivePatient is not a function`. Count must rise by exactly 6.

- [ ] **Step 3: Write the implementation**

Add to `webapp/src/views/session.js`, below `initialView`:

```js
// Which participant a launch should resume, given the stored setting and every
// participant on the device. Pure, because the three-way distinction below is
// the whole rule and it must not live inside an IndexedDB callback.
//
// Whether the `settings` row EXISTS is load-bearing, not incidental:
//
//   absent              -- never chosen. A pre-v2 install carrying one legacy
//                          participant adopts it rather than forcing a choice
//                          a clinician mid-study never had to make.
//   present, value set  -- a chosen participant.
//   present, value null -- deliberately cleared by Close Session. Re-prompt.
//
// Collapsing the last two into "no value" would re-adopt the participant the
// operator just closed, on exactly the single-participant device where that is
// most likely, and the prompt would never appear.
export function resolveActivePatient({ activeSetting, patients = [] } = {}) {
  if (activeSetting && activeSetting.value) {
    return patients.find((p) => p.id === activeSetting.value) ?? null;
  }
  if (activeSetting) return null;
  if (patients.length === 1) return patients[0];
  return null;
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd webapp && node --test tests/app.test.js`
Expected: PASS, count up by exactly 6.

- [ ] **Step 5: Delegate `ensurePatient` to it**

In `webapp/src/app.js`, replace the body of `ensurePatient`:

```js
  async function ensurePatient() {
    const active = await getOne(db, STORES.settings, SETTING_KEYS.activePatient);
    const patients = await getAll(db, STORES.patients);
    if (active && active.value) {
      const found = patients.find((p) => p.id === active.value);
      if (found) return found;
    }
    // Exactly one participant on the device -- typically the legacy row a
    // v1 install is carrying -- is adopted rather than forcing a choice a
    // clinician mid-study did not ask to make.
    if (patients.length === 1) return patients[0];
    return null;
  }
```

with:

```js
  // Thin plumbing: the rule itself lives in resolveActivePatient, where it is
  // tested against plain objects.
  async function ensurePatient() {
    return resolveActivePatient({
      activeSetting: await getOne(db, STORES.settings, SETTING_KEYS.activePatient),
      patients: await getAll(db, STORES.patients),
    });
  }
```

Extend `app.js`'s session-view import to include `resolveActivePatient`.

- [ ] **Step 6: Clear the participant on Close Session**

In `webapp/src/app.js`'s `close-session` click handler, immediately after:

```js
      currentSession = null;
      currentTrialCount = 0;
      sessionReadyPromise = null;
```

insert:

```js
      // Written as an EXISTING row with a null value, not deleted: a deleted
      // row is indistinguishable from "never chosen", which on a
      // single-participant device would re-adopt the participant just closed
      // and the next launch would never prompt. See resolveActivePatient.
      //
      // The leg is cleared in BOTH places for the same reason the participant
      // is. Clearing only the in-memory copy would leave initSession restoring
      // the previous patient's leg from `settings` on the next launch, so a
      // freshly prompted participant would arrive with someone else's leg
      // already selected -- and the Start gate would happily pass.
      currentPatient = null;
      currentSide = null;
      await put(db, STORES.settings, { key: SETTING_KEYS.activePatient, value: null });
      await put(db, STORES.settings, { key: SETTING_KEYS.side, value: null });
```

- [ ] **Step 7: Run the suite**

Run: `cd webapp && npm run build:shell && npm test`
Expected: PASS, 248 tests (242 + 6).

- [ ] **Step 8: Mutation-sweep the new rule**

Confirm each mutant ACTUALLY APPLIES before believing its result — a mutant
that failed to apply looks exactly like one that was not caught.

| Mutant | Expected |
| --- | --- |
| delete `if (activeSetting) return null;` | fail — cleared setting re-adopts |
| change `patients.length === 1` to `>= 1` | fail — several participants auto-adopt |
| make `initialView` always return `'home'` | fail — no-participant device skips the prompt |

Restore the file and confirm it is byte-identical (`cmp`) after each.

- [ ] **Step 9: Verify the full loop in a browser**

One `browse eval` script, result stashed on `window`: add a participant, pick
a leg, confirm the app is on home; then reload and confirm it stays on home;
then Close Session and reload, and confirm it lands on `view-session` with the
prompt shown. Close Session requires an exported session, so if the button is
disabled, drive the state directly by writing the cleared sentinel and
reloading — and say so in the report rather than implying the button was used.

- [ ] **Step 10: Commit**

```bash
git add webapp/src/views/session.js webapp/src/app.js webapp/tests/app.test.js webapp/src/build-id.js
git commit -m "feat: re-prompt for a participant after Close Session"
```

---

## Deployment

Per the spec's §7, this unit is deployment-ready only after the user's
physical-device smoke test on a preview deployment. `pendulastic-app.vercel.app`
is not updated by this plan.

Before that device test, consider landing the in-app build identifier noted in
spec §7.1 — without it, a tester cannot tell which build they are running, which
has already invalidated one full smoke-test pass.
