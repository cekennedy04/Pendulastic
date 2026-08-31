# Mobile Webapp Workbench Restyle Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bring `webapp/` in line with `pendulastic_app.py`'s workbench design language, and add the four capabilities the phone lacks: participant entry, the left/right side selector, in-session trial history, and MAS entry at full desktop field parity.

**Architecture:** The single-screen page becomes five `<section class="view">` elements toggled by an `.active` class, driven by a small pure router with `onEnter`/`onLeave` lifecycle hooks. `app.js` keeps its session bookkeeping and becomes the shell controller; each view's DOM wiring moves to `src/views/*.js`. Two new IndexedDB stores (`settings`, `mas`) arrive in a `DB_VERSION` 2 migration branched on `oldVersion`. MAS leaves the device as a `mas_scores.csv` the desktop appends unchanged, plus a block in a `v2` manifest.

**Tech Stack:** Plain ES modules, plain CSS, no framework and no CSS/JS build step (the only build is wasm). Tests are `node --test` over pure functions with hand-rolled fakes.

**Spec:** `docs/superpowers/specs/2026-08-31-mobile-webapp-workbench-restyle-design.md`

## Global Constraints

Every task's requirements implicitly include this section.

- **No framework, no bundler, no CSS/JS build step.** `webapp/package.json` has no build tooling beyond `build:wasm`. Do not add a dependency.
- **`#banner` is unconditional.** Sticky, `z-index: 200`, no dismiss control, visible on every view. Nothing may cover it — including `#install-gate`.
- **Never use the `hidden` attribute for views.** Views use `.view` / `.view.active`. Existing intra-view `hidden` toggles and their author-origin `[hidden]` rule stay exactly as they are.
- **These colors do not change:** `--moving-bg #6b7280`, `--holding-bg #b45309`, `--ready-bg #0f7a37`, `--fired-bg #1d4ed8`, `--unscorable-bg #4b5563`, `--fault-bg #9f1239`, `--banner-bg #7a0d0d`, and all `--pt-*-bg`. They are documented safety properties, not decoration.
- **DOM-touching code must not run on import.** `src/app.js` guards its DOM block with `if (typeof document !== 'undefined')`. View modules export factory functions that only touch the DOM when called; module top level must stay import-safe under `node --test`.
- **MAS grades are strings.** `MAS_ORDER = ['0', '1', '1+', '2', '3', '4']`. The third grade is the literal `1+` — never `1.5`, never a number.
- **`PENDING_MAS_GRADE = '-1'`** is valid for `mas_grade` only. `''` is invalid for `mas_grade` and valid for `mas_flexion`, `mas_extension`, `stronger_leg`.
- **`DEFAULT_MAS_FIELDS` order is fixed:** `participant, leg, condition, diagnosis, mas_grade, assessed_by, assessed_date, stronger_leg, notes, mas_flexion, mas_extension`.
- **`ZONE_CLASSIFICATION_CALIBRATED` stays `false`.** Zone classification remains suppressed.
- Run tests with `cd webapp && npm test`. `npm run build:wasm` must have been run once in the checkout first.

---

## File Structure

**Created:**
- `webapp/src/router.js` — pure view-transition logic and the router factory
- `webapp/src/mas-store.js` — MAS constants, form validation, record construction
- `webapp/src/mas-csv.js` — RFC4180 CSV writer for `mas_scores.csv`
- `webapp/src/views/home.js` — landing tiles
- `webapp/src/views/capture.js` — capture wiring + lifecycle hooks
- `webapp/src/views/trials.js` — in-session trial history
- `webapp/src/views/mas.js` — MAS form + draft persistence
- `webapp/src/views/session.js` — participant, side, export, close
- `webapp/tests/router.test.js`, `mas-store.test.js`, `mas-csv.test.js`

**Modified:**
- `webapp/index.html` — view sections, home markup, brand mark, MAS form
- `webapp/src/app.css` — palette tokens, `.view`, `.tile`, `.card`, `.btn`
- `webapp/src/db.js` — `DB_VERSION` 2, `settings` + `mas` stores, anchor helper
- `webapp/src/app.js` — shell controller; view wiring extracted out
- `webapp/src/export.js` — MAS CSV file, manifest `v2`
- `webapp/tests/db.test.js`, `app.test.js`, `export.test.js`

`scripts/shell-list.mjs` walks `src/` for `.js`/`.css`, so new modules reach the service-worker shell, `BUILD_ID`, and `dist/` automatically. `tests/sw-shell.test.js` and `tests/dist-build.test.js` verify that with no edit.

---

### Task 1: Design tokens

Swap the palette to `workbench_style.PALETTE` while leaving every semantic state color untouched. No logic changes, no test changes.

**Files:**
- Modify: `webapp/src/app.css:5-22` (the `:root` block), plus the rules named in Step 2
- Modify: `webapp/src/app.js:857,858,873,916,975,976` (canvas literals in `drawWaveform`)

**Interfaces:**
- Consumes: nothing
- Produces: CSS custom properties `--bg --surface --panel --accent --accent-soft --fg --fg2 --fg3 --border --mono`, used by every later task

- [ ] **Step 1: Replace the `:root` block**

Replace `webapp/src/app.css` lines 5-22 with:

```css
:root {
  /* Workbench palette -- mirrors workbench_style.PALETTE so the phone and
     the desktop app read as one product. Names match the desktop's roles
     (BG/SURFACE/PANEL/BTN_ACT/FG/FG2/FG3/BORDER), not the old
     ink/paper/muted/line vocabulary, so a rule's intent is legible against
     the Python side. */
  --bg:          #F4F6F9;
  --surface:     #FFFFFF;
  --panel:       #F5F8FC;
  --accent:      #2563EB;
  --accent-soft: #DCEAFE;
  --fg:          #0F172A;
  --fg2:         #475569;
  --fg3:         #64748B;
  --border:      #CBD5E1;
  --mono: Consolas, ui-monospace, SFMono-Regular, Menlo, monospace;

  /* Back-compat aliases. The rules below still reference these names; they
     are retired as each rule is migrated, and this block goes away in the
     styling pass. Kept so this task is a pure value change with no chance
     of a missed selector silently losing its color. */
  --ink:   var(--fg);
  --paper: var(--surface);
  --muted: var(--fg3);
  --line:  var(--border);

  /* SAFETY-CRITICAL -- DO NOT RETUNE. Each is documented below at the rule
     that uses it: read at arm's length under outdoor glare, always paired
     with an independent glyph and text label. */
  --banner-bg: #7a0d0d;
  --banner-fg: #ffffff;
  --moving-bg: #6b7280;
  --holding-bg: #b45309;
  --ready-bg: #0f7a37;
  --fired-bg: #1d4ed8;
  --unscorable-bg: #4b5563;
  --fault-bg: #9f1239;
  --pt-healthy-bg: #0f7a37;
  --pt-borderline-bg: #b45309;
  --pt-impaired-bg: #9f1239;
  --pt-unknown-bg: #4b5563;
}
```

- [ ] **Step 2: Point the page ground at `--bg` and surfaces at `--panel`**

In `webapp/src/app.css`, make exactly these substitutions:

- `html, body { background: var(--paper); }` → `background: var(--bg);`
- `#install-gate { background: var(--paper); }` → `background: var(--surface);` (the gate is a sheet above the ground, so it keeps pure white)
- Every literal `background: #f4f5f7;` → `background: var(--panel);` — five occurrences: `#waveform`, `.gate`, `#pt-score`, `#export-btn, #send-to-laptop`, `#export-session, #close-session`
- `#start, #stop { background: var(--fired-bg); }` → `background: var(--accent);` (this is the Start *button*, not the `fired` state; the `#guide.fired` rule keeps `--fired-bg`)
- `#send-to-laptop` and `#export-session`: `border-color: var(--fired-bg); color: var(--fired-bg);` → `var(--accent)` in both

- [ ] **Step 3: Update the canvas literals in `drawWaveform`**

`drawWaveform` paints on a 2D canvas and cannot read CSS variables, so these six hexes duplicate the palette by hand. Three are semantic and stay; three are chrome and move.

In `webapp/src/app.js`:

| line | current | new | why |
| --- | --- | --- | --- |
| 857 | `#5a6169` | `#64748B` | neutral line — chrome, follows `--fg3` |
| 858 | `#101317` | `#0F172A` | angle trace — chrome, follows `--fg` |
| 873 | `#e3e6ea` | `#CBD5E1` | grid — chrome, follows `--border` |
| 916 | `#1d4ed8` | *unchanged* | release marker — pairs with `#guide.fired` |
| 975 | `#0f7a37` | *unchanged* | peak marker — pairs with `#guide.ready` |
| 976 | `#7a0d0d` | *unchanged* | trough marker — pairs with the banner |

Add above the first of them:

```js
  // These duplicate app.css's palette by hand because a 2D canvas cannot
  // read CSS custom properties. The three chrome colors track --fg3/--fg/
  // --border; the three marker colors are the SAME hues as #guide's release/
  // ready/banner states on purpose, so a plotted trial reads consistently
  // with the state screen the operator just watched. Do not "harmonise"
  // the markers into the palette -- see app.css's state-color note.
```

- [ ] **Step 4: Verify nothing else referenced the old tokens**

Run: `cd webapp && grep -rn "getPropertyValue\|setProperty" src/`
Expected: no output. No JavaScript reads these variables, so this task cannot change behavior.

- [ ] **Step 5: Run the test suite**

Run: `cd webapp && npm test`
Expected: PASS, unchanged count. This task touches no logic; a failure here means a stray edit.

- [ ] **Step 6: Commit**

```bash
git add webapp/src/app.css webapp/src/app.js
git commit -m "style: adopt workbench palette tokens in the mobile webapp"
```

---

### Task 2: `mas-store.js` — MAS constants and form validation

**Files:**
- Create: `webapp/src/mas-store.js`
- Test: `webapp/tests/mas-store.test.js`

**Interfaces:**
- Consumes: nothing
- Produces:
  - `MAS_ORDER: string[]`, `PENDING_MAS_GRADE: string`, `STRONGER_LEG_OPTIONS: string[]`, `LEG_OPTIONS: string[]`, `MAS_FIELDS: string[]`
  - `validateMasForm(form) -> { ok: boolean, errors: string[] }`
  - `makeMasRecord({ patientId, form, now }) -> record` with `id`, `patient_id`, `updated_at`, and the 11 `MAS_FIELDS`
  - `masIdentity(record) -> [patient_id, leg, condition, assessed_date]`
  - `isPending(record) -> boolean`

- [ ] **Step 1: Write the failing test**

Create `webapp/tests/mas-store.test.js`:

```js
import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  MAS_ORDER, PENDING_MAS_GRADE, STRONGER_LEG_OPTIONS, MAS_FIELDS,
  validateMasForm, makeMasRecord, masIdentity, isPending,
} from '../src/mas-store.js';

// These four constants are transcriptions of the desktop's own definitions.
// A drift here does not fail loudly -- it produces a CSV the desktop's
// append_mas_score() rejects at ingestion time, on the clinician's machine,
// after the phone has already reported a successful export. Pin them exactly.
test('MAS_ORDER matches pendulastic_pt_score.py:531 character for character', () => {
  assert.deepEqual(MAS_ORDER, ['0', '1', '1+', '2', '3', '4']);
});

test('the third grade is the string "1+", never a number', () => {
  assert.equal(MAS_ORDER[2], '1+');
  assert.equal(typeof MAS_ORDER[2], 'string');
  assert.ok(!MAS_ORDER.includes(1.5));
  assert.ok(!MAS_ORDER.includes('1.5'));
});

test('MAS_FIELDS matches mas_validation.DEFAULT_MAS_FIELDS in order', () => {
  assert.deepEqual(MAS_FIELDS, [
    'participant', 'leg', 'condition', 'diagnosis', 'mas_grade',
    'assessed_by', 'assessed_date', 'stronger_leg', 'notes',
    'mas_flexion', 'mas_extension',
  ]);
});

test('STRONGER_LEG_OPTIONS keeps the leading blank meaning "not assessed"', () => {
  assert.deepEqual(STRONGER_LEG_OPTIONS, ['', 'left', 'right', 'equal']);
});

const valid = {
  participant: 'P-014', leg: 'left', condition: '', diagnosis: '',
  mas_grade: '1+', assessed_by: 'CK', assessed_date: '2026-08-31',
  stronger_leg: '', notes: '', mas_flexion: '', mas_extension: '',
};

test('a complete form validates', () => {
  assert.deepEqual(validateMasForm(valid), { ok: true, errors: [] });
});

test('every MAS_ORDER grade is accepted', () => {
  for (const g of MAS_ORDER) {
    assert.equal(validateMasForm({ ...valid, mas_grade: g }).ok, true, g);
  }
});

// The pending sentinel is a supported desktop workflow (flexion/extension
// now, overall grade later) -- see mas_validation.py:63-70.
test('the pending sentinel is accepted for mas_grade', () => {
  assert.equal(validateMasForm({ ...valid, mas_grade: PENDING_MAS_GRADE }).ok, true);
});

// append_mas_score() raises on an empty mas_grade before writing anything,
// so an untouched picker must never reach an export.
test('an unset mas_grade is rejected so -1 can never arrive by default', () => {
  const r = validateMasForm({ ...valid, mas_grade: '' });
  assert.equal(r.ok, false);
  assert.match(r.errors.join(' '), /not yet assessed/);
});

test('a nonsense mas_grade is rejected', () => {
  assert.equal(validateMasForm({ ...valid, mas_grade: '1.5' }).ok, false);
  assert.equal(validateMasForm({ ...valid, mas_grade: '5' }).ok, false);
});

// The inverse rule: blank IS "not assessed" for the optional grades, and the
// sentinel is invalid there -- append_mas_score() raises on both counts.
test('optional grades accept blank and reject the pending sentinel', () => {
  assert.equal(validateMasForm({ ...valid, mas_flexion: '' }).ok, true);
  assert.equal(validateMasForm({ ...valid, mas_flexion: '2' }).ok, true);
  assert.equal(validateMasForm({ ...valid, mas_flexion: PENDING_MAS_GRADE }).ok, false);
  assert.equal(validateMasForm({ ...valid, mas_extension: PENDING_MAS_GRADE }).ok, false);
  assert.equal(validateMasForm({ ...valid, mas_extension: 'x' }).ok, false);
});

test('stronger_leg is a closed enum with blank permitted', () => {
  for (const v of STRONGER_LEG_OPTIONS) {
    assert.equal(validateMasForm({ ...valid, stronger_leg: v }).ok, true, JSON.stringify(v));
  }
  assert.equal(validateMasForm({ ...valid, stronger_leg: 'both' }).ok, false);
  assert.equal(validateMasForm({ ...valid, stronger_leg: PENDING_MAS_GRADE }).ok, false);
});

test('participant and leg are required', () => {
  assert.equal(validateMasForm({ ...valid, participant: '' }).ok, false);
  assert.equal(validateMasForm({ ...valid, leg: '' }).ok, false);
  assert.equal(validateMasForm({ ...valid, leg: 'middle' }).ok, false);
});

test('assessed_date must be ISO yyyy-mm-dd', () => {
  assert.equal(validateMasForm({ ...valid, assessed_date: '31/08/2026' }).ok, false);
  assert.equal(validateMasForm({ ...valid, assessed_date: '' }).ok, false);
});

test('makeMasRecord carries all 11 fields plus its own keys', () => {
  const r = makeMasRecord({ patientId: 'pat-1', form: valid, now: 1700000000000 });
  assert.equal(typeof r.id, 'string');
  assert.equal(r.patient_id, 'pat-1');
  assert.equal(r.updated_at, 1700000000000);
  for (const f of MAS_FIELDS) assert.ok(f in r, `missing ${f}`);
  assert.equal(r.mas_grade, '1+');
});

// A missing key must become '' and never the strings "undefined"/"null",
// which would reach the CSV verbatim.
test('makeMasRecord normalises absent fields to empty strings', () => {
  const r = makeMasRecord({ patientId: 'pat-1', form: { ...valid, notes: undefined, diagnosis: null }, now: 1 });
  assert.equal(r.notes, '');
  assert.equal(r.diagnosis, '');
});

test('makeMasRecord drops keys outside MAS_FIELDS', () => {
  const r = makeMasRecord({ patientId: 'p', form: { ...valid, sneaky: 'x' }, now: 1 });
  assert.equal('sneaky' in r, false);
});

test('masIdentity is the four-part tuple the unique index uses', () => {
  const r = makeMasRecord({ patientId: 'pat-1', form: { ...valid, condition: 'rest' }, now: 1 });
  assert.deepEqual(masIdentity(r), ['pat-1', 'left', 'rest', '2026-08-31']);
});

test('isPending is true only for the sentinel', () => {
  assert.equal(isPending({ mas_grade: PENDING_MAS_GRADE }), true);
  assert.equal(isPending({ mas_grade: '0' }), false);
  assert.equal(isPending({ mas_grade: '' }), false);
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd webapp && node --test tests/mas-store.test.js`
Expected: FAIL — `Cannot find module '../src/mas-store.js'`

- [ ] **Step 3: Write the implementation**

Create `webapp/src/mas-store.js`:

```js
// MAS assessment records, mirroring the desktop's MasEntryPanel form and
// mas_validation.py's schema exactly.
//
// The four constants below are TRANSCRIPTIONS of Python definitions, not
// independent choices. Drift does not fail here -- it produces a CSV that
// append_mas_score() rejects on the clinician's machine, after the phone has
// already reported the export as successful. tests/mas-store.test.js pins
// each one; update both sides together or not at all.

// pendulastic_pt_score.py:531. Strings, and the third grade is the literal
// two-character "1+" -- _valid_grade() is a dict membership test, so any
// numeric form (1.5, 1) raises on ingestion.
export const MAS_ORDER = ['0', '1', '1+', '2', '3', '4'];

// mas_validation.py:71. "Overall grade not yet assessed" -- a supported
// workflow (flexion/extension at the bedside, grade later), deliberately
// kept out of MAS_RANK so pair_pt_and_mas() skips such a row from every
// statistic instead of coding it as an ordinal value.
export const PENDING_MAS_GRADE = '-1';

// mas_validation.py:75. The leading blank is "not assessed" and is valid.
export const STRONGER_LEG_OPTIONS = ['', 'left', 'right', 'equal'];

export const LEG_OPTIONS = ['left', 'right'];

// mas_validation.py:DEFAULT_MAS_FIELDS, in order. This order is the CSV
// column order; see mas-csv.js.
export const MAS_FIELDS = [
  'participant', 'leg', 'condition', 'diagnosis', 'mas_grade',
  'assessed_by', 'assessed_date', 'stronger_leg', 'notes',
  'mas_flexion', 'mas_extension',
];

const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/;

function str(v) {
  return v === null || v === undefined ? '' : String(v);
}

// Mirrors append_mas_score()'s three validation checks (mas_validation.py:
// 260-277) plus the two fields it takes on trust (participant, leg) and the
// date format MasEntryPanel produces. Returns every error rather than the
// first, so a long form reports all its problems in one pass.
export function validateMasForm(form = {}) {
  const errors = [];

  if (!str(form.participant).trim()) errors.push('Participant ID is required.');

  const leg = str(form.leg);
  if (!LEG_OPTIONS.includes(leg)) errors.push('Choose a leg (left or right).');

  // The asymmetry with the optional grades below is deliberate and load-
  // bearing: append_mas_score() rejects '' here outright, and accepts the
  // sentinel. Requiring an explicit choice is what stops '-1' from being
  // what an untouched picker yields.
  const grade = str(form.mas_grade);
  if (grade === '') {
    errors.push('Choose a MAS grade, or "not yet assessed".');
  } else if (!MAS_ORDER.includes(grade) && grade !== PENDING_MAS_GRADE) {
    errors.push(`MAS grade must be one of ${MAS_ORDER.join(', ')} (or "not yet assessed").`);
  }

  // Inverse rule: blank means "not assessed" and is always valid; the
  // sentinel is NOT valid here -- append_mas_score() raises on any non-blank
  // value that is not a real grade, and '-1' is non-blank.
  for (const f of ['mas_flexion', 'mas_extension']) {
    const v = str(form[f]);
    if (v !== '' && !MAS_ORDER.includes(v)) {
      errors.push(`${f.replace('_', ' ')} must be blank or one of ${MAS_ORDER.join(', ')}.`);
    }
  }

  if (!STRONGER_LEG_OPTIONS.includes(str(form.stronger_leg))) {
    errors.push('Stronger leg must be blank, left, right, or equal.');
  }

  if (!ISO_DATE.test(str(form.assessed_date))) {
    errors.push('Assessed date must be yyyy-mm-dd.');
  }

  return { ok: errors.length === 0, errors };
}

// Copies only MAS_FIELDS, coercing absent values to ''. Anything else the
// caller passes is dropped rather than persisted -- the same discipline
// makeTrialRecord applies, and the reason no field can reach the CSV as the
// string "undefined".
export function makeMasRecord({ patientId, form, now = Date.now() }) {
  const kept = {};
  for (const f of MAS_FIELDS) kept[f] = str(form[f]);
  return {
    id: crypto.randomUUID(),
    patient_id: patientId,
    updated_at: now,
    ...kept,
  };
}

// The tuple the `by_identity` unique index is built over, in index order.
// db.js owns the keyPath itself (MAS_IDENTITY_KEYPATH) because it owns the
// schema; this must produce values in that same order or a lookup silently
// misses. tests/db.test.js cross-checks the two -- see "masIdentity agrees
// with the index keyPath" there.
export function masIdentity(record) {
  return [record.patient_id, record.leg, record.condition, record.assessed_date];
}

export function isPending(record) {
  return str(record && record.mas_grade) === PENDING_MAS_GRADE;
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd webapp && node --test tests/mas-store.test.js`
Expected: PASS, 17 tests

- [ ] **Step 5: Commit**

```bash
git add webapp/src/mas-store.js webapp/tests/mas-store.test.js
git commit -m "feat: add MAS record store with desktop-parity validation"
```

---

### Task 3: `mas-csv.js` — RFC4180 writer

**Files:**
- Create: `webapp/src/mas-csv.js`
- Test: `webapp/tests/mas-csv.test.js`

**Interfaces:**
- Consumes: `MAS_FIELDS`, `MAS_ORDER`, `PENDING_MAS_GRADE`, `STRONGER_LEG_OPTIONS` from `./mas-store.js`
- Produces: `csvField(value) -> string`, `buildMasCsv(records) -> string`

- [ ] **Step 1: Write the failing test**

Create `webapp/tests/mas-csv.test.js`:

```js
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { csvField, buildMasCsv } from '../src/mas-csv.js';
import {
  MAS_FIELDS, MAS_ORDER, PENDING_MAS_GRADE, STRONGER_LEG_OPTIONS,
} from '../src/mas-store.js';

const row = {
  participant: 'P-014', leg: 'left', condition: 'rest', diagnosis: 'stroke',
  mas_grade: '1+', assessed_by: 'CK', assessed_date: '2026-08-31',
  stronger_leg: 'right', notes: 'none', mas_flexion: '2', mas_extension: '',
};

test('the header is DEFAULT_MAS_FIELDS in order', () => {
  const [header] = buildMasCsv([row]).split('\r\n');
  assert.equal(header,
    'participant,leg,condition,diagnosis,mas_grade,assessed_by,' +
    'assessed_date,stronger_leg,notes,mas_flexion,mas_extension');
  assert.equal(header.split(',').length, MAS_FIELDS.length);
});

test('a row emits its columns in header order', () => {
  const [, first] = buildMasCsv([row]).split('\r\n');
  assert.equal(first, 'P-014,left,rest,stroke,1+,CK,2026-08-31,right,none,2,');
});

test('the file ends with a terminating CRLF', () => {
  assert.ok(buildMasCsv([row]).endsWith('\r\n'));
});

test('an empty record list still emits the header', () => {
  assert.equal(buildMasCsv([]), MAS_FIELDS.join(',') + '\r\n');
});

// RFC4180. `notes` is free text typed at the bedside, so all four of these
// are reachable in practice.
test('fields containing a comma are quoted', () => {
  assert.equal(csvField('a,b'), '"a,b"');
});

test('embedded double quotes are doubled inside a quoted field', () => {
  assert.equal(csvField('he said "hi"'), '"he said ""hi"""');
});

test('newlines and carriage returns force quoting', () => {
  assert.equal(csvField('a\nb'), '"a\nb"');
  assert.equal(csvField('a\r\nb'), '"a\r\nb"');
});

test('ordinary values are not quoted', () => {
  assert.equal(csvField('plain'), 'plain');
  assert.equal(csvField('1+'), '1+');
  assert.equal(csvField(''), '');
});

test('null and undefined become empty, never the strings null/undefined', () => {
  assert.equal(csvField(null), '');
  assert.equal(csvField(undefined), '');
  const line = buildMasCsv([{ ...row, notes: undefined, diagnosis: null }]).split('\r\n')[1];
  assert.ok(!line.includes('undefined'));
  assert.ok(!line.includes('null'));
});

test('a notes field with every hostile character round-trips through one row', () => {
  const nasty = 'quote " comma , newline \n done';
  const line = buildMasCsv([{ ...row, notes: nasty }]).split('\r\n').slice(1).join('\r\n');
  assert.ok(line.includes('"quote "" comma , newline \n done"'));
});

// ---- Round-trip against append_mas_score()'s own rules -------------------
// A JS transcription of mas_validation.py:260-277. Its purpose is to fail
// HERE, in CI, rather than on a clinician's laptop after the phone has
// already reported a successful export.
function appendMasScoreWouldAccept(r) {
  const grade = r.mas_grade ?? '';
  if (!(MAS_ORDER.includes(grade) || grade === PENDING_MAS_GRADE)) return false;
  if (!STRONGER_LEG_OPTIONS.includes(r.stronger_leg ?? '')) return false;
  for (const f of ['mas_flexion', 'mas_extension']) {
    const v = r[f] ?? '';
    if (v && !MAS_ORDER.includes(v)) return false;
  }
  return true;
}

test('every grade this app can emit is one append_mas_score accepts', () => {
  for (const g of [...MAS_ORDER, PENDING_MAS_GRADE]) {
    assert.equal(appendMasScoreWouldAccept({ ...row, mas_grade: g }), true, g);
  }
});

test('an empty mas_grade would be rejected by the desktop', () => {
  assert.equal(appendMasScoreWouldAccept({ ...row, mas_grade: '' }), false);
});

test('the pending sentinel in an optional grade would be rejected', () => {
  assert.equal(appendMasScoreWouldAccept({ ...row, mas_flexion: PENDING_MAS_GRADE }), false);
  assert.equal(appendMasScoreWouldAccept({ ...row, stronger_leg: PENDING_MAS_GRADE }), false);
});

test('a pending row survives the whole pipeline as -1, not as blank', () => {
  const line = buildMasCsv([{ ...row, mas_grade: PENDING_MAS_GRADE }]).split('\r\n')[1];
  assert.equal(line.split(',')[MAS_FIELDS.indexOf('mas_grade')], '-1');
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd webapp && node --test tests/mas-csv.test.js`
Expected: FAIL — `Cannot find module '../src/mas-csv.js'`

- [ ] **Step 3: Write the implementation**

Create `webapp/src/mas-csv.js`:

```js
// Writes mas_scores.csv in exactly the shape mas_validation.append_mas_score()
// ingests: DEFAULT_MAS_FIELDS as the header, in order, RFC4180-quoted.
//
// The desktop reads this file with Python's csv module and appends rows to
// the clinician's existing mas_scores.csv. Getting the quoting wrong does not
// throw -- it shifts columns, so a `notes` field containing a comma silently
// becomes a `notes` value plus a bogus `mas_flexion`. That is why `notes` is
// the field most of tests/mas-csv.test.js is about.

import { MAS_FIELDS } from './mas-store.js';

// RFC4180 section 2: a field is quoted if it contains a comma, a double
// quote, CR or LF; inside a quoted field a double quote is escaped by
// doubling it.
export function csvField(value) {
  const s = value === null || value === undefined ? '' : String(value);
  return /[",\r\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}

// CRLF line endings, per RFC4180. Python's csv.DictReader accepts both, but
// the spec's ending is the one to emit. The trailing terminator matters:
// without it, appending to this file concatenates the last row onto the
// first appended one.
export function buildMasCsv(records = []) {
  const lines = [MAS_FIELDS.join(',')];
  for (const r of records) lines.push(MAS_FIELDS.map((f) => csvField(r[f])).join(','));
  return lines.join('\r\n') + '\r\n';
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd webapp && node --test tests/mas-csv.test.js`
Expected: PASS, 14 tests

- [ ] **Step 5: Commit**

```bash
git add webapp/src/mas-csv.js webapp/tests/mas-csv.test.js
git commit -m "feat: add RFC4180 mas_scores.csv writer"
```

---

### Task 4: `DB_VERSION` 2 — `settings`, `mas`, and the legacy anchor

**Files:**
- Modify: `webapp/src/db.js`
- Test: `webapp/tests/db.test.js`

**Interfaces:**
- Consumes: nothing
- Produces: `STORES.settings`, `STORES.mas`, `DB_VERSION === 2`, `legacyPatientPatches(sessions, patients, { now }) -> patientRecord[]`, `getOne(db, storeName, key) -> Promise<record|undefined>`

- [ ] **Step 1: Write the failing test**

Append to `webapp/tests/db.test.js`. Extend the existing `fakeIndexedDB()` to take an `oldVersion` and to record indexes with their options:

```js
// The existing fake models a FRESH database (oldVersion 0). v2 adds an
// upgrade path, so the fake now takes the version the device is coming from
// -- the branch that decides whether a store already exists is exactly what
// db.js's header warns is easy to get wrong.
function fakeIndexedDBAt(oldVersion, existingStores = []) {
  const created = existingStores.map((name) => ({ name, opts: null, indexes: [] }));
  return {
    created,
    open(name, version) {
      const req = {};
      queueMicrotask(() => {
        const db = {
          objectStoreNames: { contains: (n) => created.some((c) => c.name === n) },
          createObjectStore(n, opts) {
            const store = {
              name: n, opts, indexes: [],
              createIndex(i, kp, o) { this.indexes.push({ name: i, keyPath: kp, options: o || {} }); },
            };
            created.push(store);
            return store;
          },
        };
        req.result = db;
        req.transaction = { objectStore: (n) => created.find((c) => c.name === n) };
        req.onupgradeneeded?.({ target: req, oldVersion, newVersion: version });
        req.onsuccess?.({ target: { result: db } });
      });
      return req;
    },
  };
}

const storeNames = (idb) => idb.created.map((c) => c.name).sort();
const findStore = (idb, n) => idb.created.find((c) => c.name === n);

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
```

Update that file's import line to:

```js
import {
  openDb, put, getAll, getOne, legacyPatientPatches,
  STORES, DB_VERSION, MAS_IDENTITY_KEYPATH,
} from '../src/db.js';
import { masIdentity } from '../src/mas-store.js';
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd webapp && node --test tests/db.test.js`
Expected: FAIL — `legacyPatientPatches is not a function`, and `DB_VERSION` is 1

- [ ] **Step 3: Write the implementation**

In `webapp/src/db.js`, set `DB_VERSION = 2`, extend `STORES`, and replace `onupgradeneeded`:

```js
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
```

```js
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
```

Add below `openDb`:

```js
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd webapp && node --test tests/db.test.js`
Expected: PASS. `backfillPatientAnchors` is browser plumbing and stays untested, matching how `capture.js` leaves its worker/permission plumbing untested.

- [ ] **Step 5: Run the full suite**

Run: `cd webapp && npm test`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add webapp/src/db.js webapp/tests/db.test.js
git commit -m "feat: DB_VERSION 2 with settings, mas, and legacy patient anchors"
```

---

### Task 5: `router.js` — view transitions with lifecycle hooks

**Files:**
- Create: `webapp/src/router.js`
- Test: `webapp/tests/router.test.js`

**Interfaces:**
- Consumes: nothing
- Produces:
  - `VIEWS: string[]` — `['home', 'capture', 'trials', 'mas', 'session']`
  - `resolveView(name) -> string`
  - `planTransition(current, next, { canLeave }) -> {kind: 'noop'|'blocked'|'switch', ...}`
  - `createRouter({ onShow }) -> { register(name, hooks), navigate(name), current() }`

- [ ] **Step 1: Write the failing test**

Create `webapp/tests/router.test.js`:

```js
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { VIEWS, resolveView, planTransition, createRouter } from '../src/router.js';

test('the five views are the ones index.html defines', () => {
  assert.deepEqual(VIEWS, ['home', 'capture', 'trials', 'mas', 'session']);
});

test('an unknown view falls back to home rather than a blank screen', () => {
  assert.equal(resolveView('home'), 'home');
  assert.equal(resolveView('nope'), 'home');
  assert.equal(resolveView(undefined), 'home');
});

const always = () => true;

test('navigating to the current view is a no-op', () => {
  assert.deepEqual(planTransition('home', 'home', { canLeave: always }),
    { kind: 'noop', view: 'home' });
});

test('a permitted transition switches', () => {
  assert.deepEqual(planTransition('home', 'capture', { canLeave: always }),
    { kind: 'switch', from: 'home', to: 'capture' });
});

// The whole reason this is a reducer and not a class method: a live capture
// holds a devicemotion listener, a flush interval and a wake lock, and
// leaving the view without stopping them orphans all three.
test('a view that refuses to leave blocks the transition and keeps the view', () => {
  const canLeave = () => 'Stop the trial first.';
  assert.deepEqual(planTransition('capture', 'home', { canLeave }),
    { kind: 'blocked', view: 'capture', reason: 'Stop the trial first.' });
});

test('a blocked transition is still blocked when the target is unknown', () => {
  const r = planTransition('capture', 'garbage', { canLeave: () => 'busy' });
  assert.equal(r.kind, 'blocked');
});

test('onLeave runs to completion before onEnter', () => {
  const order = [];
  const router = createRouter({ onShow: (n) => order.push(`show:${n}`) });
  router.register('home', { onLeave: () => { order.push('home:leave'); return true; } });
  router.register('capture', { onEnter: () => order.push('capture:enter') });
  router.navigate('capture');
  assert.deepEqual(order, ['home:leave', 'show:capture', 'capture:enter']);
});

test('a blocked navigate leaves current() unchanged and never shows the target', () => {
  const shown = [];
  const router = createRouter({ onShow: (n) => shown.push(n) });
  router.register('capture', { onLeave: () => 'recording' });
  router.register('home', {});
  router.navigate('capture');
  shown.length = 0;
  const result = router.navigate('home');
  assert.equal(result.kind, 'blocked');
  assert.equal(result.reason, 'recording');
  assert.equal(router.current(), 'capture');
  assert.deepEqual(shown, []);
});

test('a view with no hooks navigates freely', () => {
  const router = createRouter({ onShow: () => {} });
  router.register('trials', {});
  assert.equal(router.navigate('trials').kind, 'switch');
  assert.equal(router.current(), 'trials');
});

test('onEnter receives the params passed to navigate', () => {
  let got = null;
  const router = createRouter({ onShow: () => {} });
  router.register('trials', { onEnter: (p) => { got = p; } });
  router.navigate('trials', { trialId: 't-9' });
  assert.deepEqual(got, { trialId: 't-9' });
});

test('the router starts on home', () => {
  assert.equal(createRouter({ onShow: () => {} }).current(), 'home');
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd webapp && node --test tests/router.test.js`
Expected: FAIL — `Cannot find module '../src/router.js'`

- [ ] **Step 3: Write the implementation**

Create `webapp/src/router.js`:

```js
// View transitions for the five-section shell.
//
// The transition RULE is a pure reducer (planTransition) so it can be tested
// without a DOM; createRouter is the thin stateful wrapper the page uses.
//
// Why onLeave can refuse. A live capture holds four resources that outlive
// any DOM node: a `devicemotion` listener, a `setInterval(flush)`, a screen
// wake lock (all capture.js), and a window `resize` handler that redraws the
// waveform (app.js). Navigating away without stopping them leaves a capture
// running headless -- still consuming sensor events and flushing batches with
// no UI attached, still holding the screen awake -- and points the resize
// handler at a canvas inside a `display: none` subtree, where
// getBoundingClientRect() returns zeros and the canvas is silently resized to
// 0x0. Refusing the transition is the only outcome that neither discards a
// trial in progress nor orphans one.

export const VIEWS = ['home', 'capture', 'trials', 'mas', 'session'];

// An unknown name lands on home rather than hiding every section and
// leaving a blank page under the banner.
export function resolveView(name) {
  return VIEWS.includes(name) ? name : 'home';
}

// `canLeave(current)` returns `true` to permit, or a human-readable string
// explaining the refusal. A string is used rather than `false` so the view
// that blocks owns the wording -- the router has no idea why a capture is
// busy.
export function planTransition(current, next, { canLeave }) {
  const target = resolveView(next);
  if (target === current) return { kind: 'noop', view: current };
  const verdict = canLeave(current);
  if (verdict !== true) return { kind: 'blocked', view: current, reason: verdict };
  return { kind: 'switch', from: current, to: target };
}

export function createRouter({ onShow }) {
  const hooks = new Map();
  let current = 'home';

  return {
    register(name, { onEnter, onLeave } = {}) {
      hooks.set(name, { onEnter, onLeave });
    },

    current() {
      return current;
    },

    navigate(next, params = {}) {
      const plan = planTransition(current, next, {
        canLeave: (from) => hooks.get(from)?.onLeave?.() ?? true,
      });
      if (plan.kind !== 'switch') return plan;
      // Order is load-bearing: the outgoing view's teardown completes before
      // the incoming view is shown, so the incoming onEnter can measure
      // layout (drawWaveform needs a laid-out canvas) and never races a
      // teardown still holding the same DOM.
      current = plan.to;
      onShow(plan.to);
      hooks.get(plan.to)?.onEnter?.(params);
      return plan;
    },
  };
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd webapp && node --test tests/router.test.js`
Expected: PASS, 11 tests

- [ ] **Step 5: Commit**

```bash
git add webapp/src/router.js webapp/tests/router.test.js
git commit -m "feat: add view router with onEnter/onLeave lifecycle hooks"
```

---

### Task 6: View shell and the home screen

Restructure `index.html` into five sections, add the brand mark and tiles, wire the router. After this task the app navigates; capture still works from its own view.

**Files:**
- Modify: `webapp/index.html`
- Modify: `webapp/src/app.css`
- Modify: `webapp/src/app.js`
- Create: `webapp/src/views/home.js`

**Interfaces:**
- Consumes: `createRouter`, `VIEWS` from `../router.js`
- Produces: `createHomeView({ router, el }) -> { onEnter() }`; the DOM ids `view-home`, `view-capture`, `view-trials`, `view-mas`, `view-session`, `home-participant`, `home-trial-count`

- [ ] **Step 1: Wrap the existing controls in view sections**

In `webapp/index.html`, insert `<main>` immediately after the `#install-gate` div and before `<button id="start">`. Move **every** element from `#start` through `#session-bar` inside it, distributed as below. Do not change any element's id or attributes; only their parentage changes.

```html
<main>
  <section class="view active" id="view-home">
    <header class="brand">
      <svg class="brand-mark" viewBox="0 0 52 52" aria-hidden="true">
        <!-- workbench_style.brand_mark() at identical proportions: pivot dot,
             swing line, weighted bob, enclosing ring. -->
        <circle cx="26" cy="26" r="21.8" fill="none" stroke="currentColor" stroke-width="2.5"/>
        <line x1="26" y1="11.4" x2="36.4" y2="35.4" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"/>
        <circle cx="36.4" cy="35.4" r="4.7" fill="currentColor"/>
        <circle cx="26" cy="11.4" r="2.3" fill="currentColor"/>
      </svg>
      <div class="brand-text">
        <h1>Pendulastic</h1>
        <p>Clinical Pendulum Test Platform</p>
      </div>
    </header>

    <button class="tile tile--primary" data-nav="capture">
      <svg class="tile-icon" viewBox="0 0 24 24" aria-hidden="true">
        <circle cx="12" cy="12" r="9" fill="none" stroke="currentColor" stroke-width="2"/>
        <circle cx="12" cy="12" r="4" fill="currentColor"/>
      </svg>
      <span class="tile-text">
        <span class="tile-title">Record Trial</span>
        <span class="tile-sub" id="home-participant">no participant set</span>
      </span>
    </button>

    <div class="tile-grid">
      <button class="tile" data-nav="trials">
        <svg class="tile-icon" viewBox="0 0 24 24" aria-hidden="true">
          <rect x="3" y="5" width="8" height="14" fill="none" stroke="currentColor" stroke-width="2"/>
          <rect x="13" y="5" width="8" height="14" fill="none" stroke="currentColor" stroke-width="2"/>
        </svg>
        <span class="tile-text">
          <span class="tile-title">Trials</span>
          <span class="tile-sub" id="home-trial-count">none yet</span>
        </span>
      </button>
      <button class="tile" data-nav="mas">
        <svg class="tile-icon" viewBox="0 0 24 24" aria-hidden="true">
          <rect x="4" y="4" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2"/>
          <path d="M8 12l3 3 5-6" fill="none" stroke="currentColor" stroke-width="2"
                stroke-linecap="round" stroke-linejoin="round"/>
        </svg>
        <span class="tile-text">
          <span class="tile-title">MAS Entry</span>
          <span class="tile-sub">Enter &amp; validate</span>
        </span>
      </button>
      <button class="tile" data-nav="session">
        <svg class="tile-icon" viewBox="0 0 24 24" aria-hidden="true">
          <path d="M12 20V5M6 11l6-6 6 6" fill="none" stroke="currentColor" stroke-width="2"
                stroke-linecap="round" stroke-linejoin="round"/>
        </svg>
        <span class="tile-text">
          <span class="tile-title">Session</span>
          <span class="tile-sub">Participant &amp; export</span>
        </span>
      </button>
    </div>
  </section>

  <section class="view" id="view-capture">
    <div class="view-head"><button class="btn btn--secondary" data-nav="home">&larr; Back</button>
      <h2>Record Trial</h2></div>
    <p id="nav-blocked" class="nav-blocked" hidden></p>
    <!-- MOVE HERE, unchanged: #start, #guide, #gates, #stop, #waveform-wrap,
         #pt-score, #export-actions, #result -->
  </section>

  <section class="view" id="view-trials">
    <div class="view-head"><button class="btn btn--secondary" data-nav="home">&larr; Back</button>
      <h2>Trials</h2></div>
    <div id="trial-list"></div>
  </section>

  <section class="view" id="view-mas">
    <div class="view-head"><button class="btn btn--secondary" data-nav="home">&larr; Back</button>
      <h2>MAS Score Entry</h2></div>
    <!-- Task 10 fills this in -->
  </section>

  <section class="view" id="view-session">
    <div class="view-head"><button class="btn btn--secondary" data-nav="home">&larr; Back</button>
      <h2>Session</h2></div>
    <!-- MOVE HERE, unchanged: #session-bar. Task 8 adds participant + side. -->
  </section>
</main>
```

- [ ] **Step 2: Add the view, brand, and tile CSS**

Append to `webapp/src/app.css`:

```css
/* ---- Views ---------------------------------------------------------------
   Views deliberately do NOT use the `hidden` attribute. app.css already
   carries a long note (see the #start/#stop rule) on author-origin `display`
   silently defeating `hidden`; that fix works but obliges every future author
   rule to remember it. A dedicated class cannot be defeated the same way, so
   new code avoids the bug class by construction. The existing intra-view
   `hidden` toggles are unchanged. */
main { display: flex; flex-direction: column; flex: 1; min-height: 0; }
.view { display: none; }
.view.active { display: flex; flex-direction: column; flex: 1; min-height: 0; }

.view-head {
  display: flex; align-items: center; gap: 12px;
  margin: 16px 16px 8px;
}
.view-head h2 {
  margin: 0;
  font-size: clamp(17px, 4.8vw, 21px);
  font-weight: 700;
  color: var(--fg);
}

/* Why a transition was refused -- shown in place rather than as an alert(),
   which would block the extension AND the capture it is reporting on. */
.nav-blocked {
  margin: 0 16px 8px;
  padding: 10px 12px;
  border-left: 4px solid var(--holding-bg);
  background: rgba(180, 83, 9, 0.12);
  font-size: clamp(13px, 3.6vw, 15px);
  font-weight: 600;
  color: var(--fg);
}
.nav-blocked[hidden] { display: none; }

/* ---- Brand (workbench_style.brand_mark + the ModeSelectView header) ------ */
.brand { display: flex; align-items: center; gap: 14px; margin: 24px 16px 28px; }
.brand-mark { width: 52px; height: 52px; flex: none; color: var(--accent); }
.brand-text h1 {
  margin: 0; font-size: clamp(22px, 7vw, 28px); font-weight: 700; color: var(--fg);
}
.brand-text p {
  margin: 2px 0 0; font-size: clamp(12px, 3.6vw, 15px); color: var(--fg3);
}

/* ---- Tiles (workbench_style.Tile) ---------------------------------------
   The desktop tile's hover state becomes :active/:focus-visible -- a phone
   has no hover, and a hover rule there sticks after a tap. */
.tile {
  display: flex; align-items: center; gap: 14px; width: 100%;
  padding: 16px; margin: 0;
  font: inherit; text-align: left; cursor: pointer;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 14px;
  color: var(--fg);
}
.tile:active, .tile:focus-visible { border-color: var(--accent); border-width: 2px; padding: 15px; }
.tile-icon { width: 26px; height: 26px; flex: none; color: var(--accent); }
.tile-text { display: flex; flex-direction: column; min-width: 0; }
.tile-title { font-size: clamp(15px, 4.4vw, 18px); font-weight: 700; }
.tile-sub { font-size: clamp(12px, 3.4vw, 14px); color: var(--fg3); margin-top: 2px; }

.tile--primary { background: var(--accent); border-color: var(--accent); color: #fff; margin: 0 0 18px; }
.tile--primary .tile-icon { color: #fff; }
.tile--primary .tile-sub { color: #EAF2FF; }
.tile--primary:active, .tile--primary:focus-visible { background: #1D4ED8; border-color: #1D4ED8; }

#view-home { padding: 0 16px 24px; }
.tile-grid { display: flex; flex-direction: column; gap: 12px; }

/* ---- Buttons (workbench_style.primary_button / secondary_button) --------- */
.btn {
  padding: 12px 18px; font: inherit; font-weight: 700;
  border-radius: 12px; border: 1px solid var(--border); cursor: pointer;
  min-height: 44px;
}
.btn--primary { background: var(--accent); border-color: var(--accent); color: #fff; }
.btn--secondary { background: var(--accent-soft); color: var(--fg); }
```

- [ ] **Step 3: Write the home view module**

Create `webapp/src/views/home.js`:

```js
// The landing screen. Mirrors pendulastic_app.py's ModeSelectView: one
// filled-accent hero tile for the routine path, secondary tiles for the rest.
//
// No DOM work at module scope -- app.js calls this from inside its
// `typeof document !== 'undefined'` guard, so importing the module under
// `node --test` stays safe.

export function createHomeView({ el }) {
  return {
    // Both subtitles are live state, so they are refreshed on every entry
    // rather than written once at startup.
    onEnter({ participantLabel, side, trialCount } = {}) {
      const p = el('home-participant');
      if (p) {
        p.textContent = participantLabel
          ? `${participantLabel}${side ? ` · ${side} leg` : ''}`
          : 'no participant set';
      }
      const c = el('home-trial-count');
      if (c) {
        c.textContent = trialCount ? `${trialCount} this session` : 'none yet';
      }
    },
  };
}
```

- [ ] **Step 4: Wire the router in `app.js`**

Inside `app.js`'s `if (typeof document !== 'undefined') {` block, after the install-gate section, add:

```js
  // ---- View routing -------------------------------------------------------
  const router = createRouter({
    onShow: (name) => {
      for (const v of VIEWS) {
        el(`view-${v}`).classList.toggle('active', v === name);
      }
      // A view switch scrolls to the top: the banner is sticky, but a page
      // scrolled halfway down the previous view otherwise opens the new one
      // mid-content.
      window.scrollTo(0, 0);
    },
  });

  router.register('home', createHomeView({ el }));

  // One delegated listener rather than a listener per control: Task 9's trial
  // rows and Task 10's form are rendered after this runs, and a per-element
  // binding would miss every one of them.
  document.addEventListener('click', (e) => {
    const nav = e.target.closest?.('[data-nav]');
    if (!nav) return;
    const result = router.navigate(nav.dataset.nav, navParams(nav.dataset.nav));
    const blocked = el('nav-blocked');
    if (result.kind === 'blocked') {
      blocked.textContent = result.reason;
      blocked.hidden = false;
    } else {
      blocked.hidden = true;
    }
  });

  // Assembled here rather than inside each view so a view module never needs
  // a reference to the session bookkeeping this file owns.
  function navParams(name) {
    if (name === 'home') {
      return {
        participantLabel: currentPatient?.clinic_patient_id ?? '',
        side: currentSide,
        trialCount: currentTrialCount,
      };
    }
    return {};
  }
```

Add to the imports at the top of `app.js`:

```js
import { createRouter, VIEWS } from './router.js';
import { createHomeView } from './views/home.js';
```

Declare beside the existing `currentSession` / `currentTrialCount`:

```js
  // Set by Task 8's session view; read here only to label the home tiles.
  let currentPatient = null;
  let currentSide = null;
```

- [ ] **Step 5: Verify the shell and the suite**

Run: `cd webapp && npm test`
Expected: PASS. `tests/sw-shell.test.js` and `tests/dist-build.test.js` must pass **without edits** — `shell-list.mjs` walks `src/` for `.js`, so `router.js` and `views/home.js` are picked up automatically. If either fails, the walk is not reaching `src/views/`; fix `shell-list.mjs`, not the test.

- [ ] **Step 6: Check it in a browser**

Run: `cd webapp && python dev_server.py` and open `http://localhost:8900`.
Expected: the home screen shows the brand mark and four tiles; each tile opens its view; Back returns home; the red banner stays fixed at the top on every view.

- [ ] **Step 7: Commit**

```bash
git add webapp/index.html webapp/src/app.css webapp/src/app.js webapp/src/views/home.js
git commit -m "feat: add view shell, home screen, and brand mark"
```

---

### Task 7: Capture view lifecycle

Move the capture wiring behind lifecycle hooks so leaving the view cannot orphan a running trial.

**Files:**
- Create: `webapp/src/views/capture.js`
- Modify: `webapp/src/app.js:979` (the `resize` listener) and the capture wiring
- Test: `webapp/tests/app.test.js`

**Interfaces:**
- Consumes: `createRouter` from Task 5
- Produces: `createCaptureView({ el, isCapturing, redraw }) -> { onEnter(), onLeave() }`

- [ ] **Step 1: Write the failing test**

Append to `webapp/tests/app.test.js`:

```js
import { createCaptureView } from '../src/views/capture.js';

// A stub `el` -- the view only ever reads/writes properties these objects
// have, so no DOM is needed.
function fakeEl(map) {
  return (id) => map[id] ?? null;
}

test('a live capture refuses to leave the view', () => {
  const view = createCaptureView({
    el: fakeEl({}), isCapturing: () => true, redraw: () => {},
  });
  const verdict = view.onLeave();
  assert.notEqual(verdict, true);
  assert.match(String(verdict), /trial/i);
});

test('an idle capture view leaves freely', () => {
  const view = createCaptureView({
    el: fakeEl({}), isCapturing: () => false, redraw: () => {},
  });
  assert.equal(view.onLeave(), true);
});

// The canvas lives inside a `display: none` subtree while another view is
// active, where getBoundingClientRect() is all zeros -- redrawing then
// resizes it to 0x0 and the plot comes back blank with no error anywhere.
test('leaving detaches the resize redraw and entering re-attaches it', () => {
  let redraws = 0;
  const view = createCaptureView({
    el: fakeEl({}), isCapturing: () => false, redraw: () => { redraws += 1; },
  });
  view.onEnter();
  view.handleResize();
  assert.equal(redraws, 1);
  view.onLeave();
  view.handleResize();
  assert.equal(redraws, 1, 'a resize while the view is inactive must not redraw');
  view.onEnter();
  view.handleResize();
  assert.equal(redraws, 2);
});

test('entering redraws once so a returning view is not blank', () => {
  let redraws = 0;
  const view = createCaptureView({
    el: fakeEl({}), isCapturing: () => false, redraw: () => { redraws += 1; },
  });
  view.onEnter();
  assert.equal(redraws, 1);
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd webapp && node --test tests/app.test.js`
Expected: FAIL — `Cannot find module '../src/views/capture.js'`

- [ ] **Step 3: Write the implementation**

Create `webapp/src/views/capture.js`:

```js
// Lifecycle for the capture view.
//
// Four resources outlive any DOM node while a trial runs: capture.js's
// `devicemotion` listener, its `setInterval(flush)`, its screen wake lock,
// and app.js's window `resize` -> drawWaveform handler. The first three are
// owned by the capture handle and released by its stop(); this module owns
// the fourth and the refusal that keeps the first three from being abandoned.
//
// `isCapturing` and `redraw` are injected rather than imported so the whole
// module is testable with plain objects under `node --test`.

export function createCaptureView({ el, isCapturing, redraw }) {
  let active = false;

  return {
    onEnter() {
      active = true;
      // The canvas was inside a `display: none` subtree until this instant,
      // so any redraw attempted while away measured 0x0. Redraw now that it
      // has a real box, or a returning operator sees a blank plot.
      redraw();
    },

    // Returning a string rather than `false` lets this view own the wording;
    // the router only distinguishes `true` from everything else.
    onLeave() {
      if (isCapturing()) {
        return 'A trial is recording. Tap Stop before leaving this screen.';
      }
      active = false;
      const blocked = el('nav-blocked');
      if (blocked) blocked.hidden = true;
      return true;
    },

    // Called by app.js's single window `resize` listener. Gating on `active`
    // rather than adding and removing the listener keeps one registration
    // for the page's whole life -- there is no path that can leak a second.
    handleResize() {
      if (active) redraw();
    },
  };
}
```

- [ ] **Step 4: Rewire `app.js`**

Replace the `resize` listener at `app.js:979`:

```js
  // Was: an unconditional redraw gated only on #waveform-wrap.hidden. That
  // check does not see the VIEW's visibility, so once capture lived inside a
  // toggled section a resize on any other view redrew a canvas whose
  // getBoundingClientRect() is all zeros -- silently resizing it to 0x0.
  window.addEventListener('resize', () => captureView.handleResize());
```

Register the view beside `home`:

```js
  const captureView = createCaptureView({
    el,
    // `session` is app.js's live capture handle -- non-null exactly while a
    // trial is running (set by the Start handler, cleared by resetToIdle).
    isCapturing: () => session !== null,
    redraw: () => {
      if (!el('waveform-wrap').hidden && lastTrajectory) drawWaveform(lastTrajectory);
    },
  });
  router.register('capture', captureView);
```

Add the import:

```js
import { createCaptureView } from './views/capture.js';
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd webapp && node --test tests/app.test.js`
Expected: PASS

- [ ] **Step 6: Verify the refusal on a device or in the browser**

Run: `cd webapp && python dev_server.py`, open the capture view, tap Start, then tap Back.
Expected: navigation is refused and the amber `#nav-blocked` notice reads "A trial is recording. Tap Stop before leaving this screen." Tap Stop, then Back — it navigates.

- [ ] **Step 7: Commit**

```bash
git add webapp/src/views/capture.js webapp/src/app.js webapp/tests/app.test.js
git commit -m "feat: gate view exit on a live capture and scope the resize redraw"
```

---

### Task 8: Participant entry and the side selector

Remove `FIXED_PATIENT_ID`; the session view owns participant and side.

**Files:**
- Create: `webapp/src/views/session.js`
- Modify: `webapp/index.html` (`#view-session`), `webapp/src/app.js:31-33,470-486`
- Test: `webapp/tests/app.test.js`

**Interfaces:**
- Consumes: `getOne`, `put`, `getAll`, `STORES` from `../db.js`; `LEG_OPTIONS` from `../mas-store.js`
- Produces: `SETTING_KEYS = { activePatient: 'active-patient', side: 'trial-side' }`; `patientLabel(patient)`; `nextParticipantState(state, action)`; `createSessionView({ el, context, listPatients, addPatient, selectPatient, selectSide }) -> { onEnter() }`

- [ ] **Step 1: Write the failing test**

Append to `webapp/tests/app.test.js`:

```js
import { patientLabel, nextParticipantState, SETTING_KEYS } from '../src/views/session.js';

test('setting keys are stable strings', () => {
  assert.equal(SETTING_KEYS.activePatient, 'active-patient');
  assert.equal(SETTING_KEYS.side, 'trial-side');
});

test('a normal participant shows its clinic id', () => {
  assert.equal(patientLabel({ clinic_patient_id: 'P-014' }), 'P-014');
});

// Legacy rows must stay visible and exportable -- they anchor every trial
// recorded before participant entry existed.
test('a legacy participant is labelled as such rather than hidden', () => {
  assert.equal(patientLabel({ clinic_patient_id: 'TEST-PARTICIPANT', legacy: true }),
    'TEST-PARTICIPANT (legacy)');
});

test('a missing participant reads as unset, never as undefined', () => {
  assert.equal(patientLabel(null), 'no participant set');
  assert.equal(patientLabel({}), 'no participant set');
});

const base = { patient: null, side: null, trialCount: 0 };

test('choosing a participant sets it and clears nothing else', () => {
  const s = nextParticipantState(base, { type: 'select', patient: { id: 'p1', clinic_patient_id: 'P-1' } });
  assert.equal(s.patient.id, 'p1');
  assert.equal(s.side, null);
});

test('choosing a side records it', () => {
  assert.equal(nextParticipantState(base, { type: 'side', side: 'left' }).side, 'left');
});

test('an invalid side is ignored rather than stored', () => {
  assert.equal(nextParticipantState(base, { type: 'side', side: 'middle' }).side, null);
});

// Switching participant mid-session would silently attach the next trial to
// a different person than the ones already recorded.
test('switching participant is refused while the session holds trials', () => {
  const withTrials = { ...base, patient: { id: 'p1' }, trialCount: 2 };
  const s = nextParticipantState(withTrials, { type: 'select', patient: { id: 'p2' } });
  assert.equal(s.patient.id, 'p1');
  assert.match(s.error, /export and close/i);
});

test('switching participant is allowed once the session is empty', () => {
  const empty = { ...base, patient: { id: 'p1' }, trialCount: 0 };
  assert.equal(nextParticipantState(empty, { type: 'select', patient: { id: 'p2' } }).patient.id, 'p2');
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd webapp && node --test tests/app.test.js`
Expected: FAIL — `Cannot find module '../src/views/session.js'`

- [ ] **Step 3: Add the session view markup**

In `webapp/index.html`, inside `#view-session` above the moved `#session-bar`:

```html
    <div class="card">
      <p class="card-label">Participant</p>
      <select id="participant-select"></select>
      <div class="field-row">
        <input id="participant-new" type="text" inputmode="text" autocomplete="off"
               placeholder="New participant ID">
        <button class="btn btn--secondary" id="participant-add">Add</button>
      </div>
      <p id="participant-error" class="field-error" hidden></p>
    </div>

    <div class="card">
      <p class="card-label">Leg</p>
      <div class="seg" id="side-select" role="group" aria-label="Leg">
        <button class="seg-btn" data-side="left">Left</button>
        <button class="seg-btn" data-side="right">Right</button>
      </div>
    </div>
```

- [ ] **Step 4: Write the implementation**

Create `webapp/src/views/session.js`:

```js
// Participant identity, the trial side, and the export/close controls.
//
// Replaces app.js's FIXED_PATIENT_ID/FIXED_PATIENT_LABEL, which hardcoded
// every trial on every device to one synthetic participant. The state
// transitions are a pure reducer so the "cannot switch mid-session" rule is
// testable without a DOM.

import { LEG_OPTIONS } from '../mas-store.js';

export const SETTING_KEYS = {
  activePatient: 'active-patient',
  side: 'trial-side',
};

// Legacy rows anchor trials recorded before participant entry existed (see
// db.js's legacyPatientPatches). They stay listed and exportable; the suffix
// is so a clinician can tell them apart from one they typed.
export function patientLabel(patient) {
  const id = patient && patient.clinic_patient_id;
  if (!id) return 'no participant set';
  return patient.legacy === true ? `${id} (legacy)` : id;
}

// Pure. `state` is `{patient, side, trialCount}`; the returned state carries
// an `error` string when an action was refused.
export function nextParticipantState(state, action) {
  if (action.type === 'side') {
    if (!LEG_OPTIONS.includes(action.side)) return { ...state, error: undefined };
    return { ...state, side: action.side, error: undefined };
  }

  if (action.type === 'select') {
    // A session's trials all belong to one participant -- makeSessionRecord
    // stores patient_id on the SESSION, not the trial. Switching now would
    // silently file the next trial under a different person than the ones
    // already recorded, and the export manifest would name only one of them.
    if (state.trialCount > 0 && state.patient && state.patient.id !== action.patient.id) {
      return {
        ...state,
        error: 'This session already has trials. Export and close it before switching participant.',
      };
    }
    return { ...state, patient: action.patient, error: undefined };
  }

  return state;
}

// The view itself. Everything it needs is injected, so this module stays
// import-safe under `node --test` and the pure reducer above can be tested
// without a DOM.
export function createSessionView({ el, context, listPatients, addPatient, selectPatient, selectSide }) {
  let ready = false;

  function initOnce() {
    if (ready) return;

    el('participant-select').addEventListener('change', async (e) => {
      const { patients } = await listPatients();
      const chosen = patients.find((p) => p.id === e.target.value) || null;
      if (chosen) await selectPatient(chosen);
      await render();
    });

    el('participant-add').addEventListener('click', async () => {
      const raw = el('participant-new').value.trim();
      const errEl = el('participant-error');
      if (!raw) {
        errEl.textContent = 'Enter a participant ID first.';
        errEl.hidden = false;
        return;
      }
      errEl.hidden = true;
      await addPatient(raw);
      el('participant-new').value = '';
      await render();
    });

    el('side-select').addEventListener('click', async (e) => {
      const btn = e.target.closest('[data-side]');
      if (!btn) return;
      await selectSide(btn.dataset.side);
      await render();
    });

    ready = true;
  }

  async function render() {
    const { patients } = await listPatients();
    const { patient, side, error } = context();

    const select = el('participant-select');
    select.textContent = '';
    const none = document.createElement('option');
    none.value = '';
    none.textContent = 'no participant set';
    select.append(none);
    // createElement + textContent, not innerHTML: clinic_patient_id is free
    // text a clinician types.
    for (const p of patients) {
      const opt = document.createElement('option');
      opt.value = p.id;
      opt.textContent = patientLabel(p);
      select.append(opt);
    }
    select.value = patient ? patient.id : '';

    for (const btn of el('side-select').querySelectorAll('[data-side]')) {
      btn.setAttribute('aria-pressed', String(btn.dataset.side === side));
    }

    const errEl = el('participant-error');
    errEl.textContent = error || '';
    errEl.hidden = !error;
  }

  return {
    async onEnter() {
      initOnce();
      await render();
    },
  };
}
```

- [ ] **Step 5: Replace `ensurePatient` in `app.js`**

Delete `FIXED_PATIENT_ID` and `FIXED_PATIENT_LABEL` (`app.js:32-33`) and replace `ensurePatient` (`app.js:470-476`) with:

```js
  // Was: a hardcoded synthetic participant every device shared. Now the
  // active participant is whatever the session view last stored, resolved
  // through `settings` so it survives a reload and an app relaunch.
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

Then replace `initSession` so a device with no participant yet is a valid resting state rather than a crash:

```js
  async function initSession() {
    db ??= await openDb(indexedDB);
    currentPatient = await ensurePatient();
    const stored = await getOne(db, STORES.settings, SETTING_KEYS.side);
    currentSide = stored?.value ?? null;
    // No participant is a legitimate first-run state, not an error. Leaving
    // currentSession null keeps both session buttons disabled through
    // exportLockState's existing `session: null` branch -- no new lock
    // condition is introduced, so the export gate's reasoning is unchanged.
    if (!currentPatient) {
      currentSession = null;
      currentTrialCount = 0;
      refreshExportLock();
      return;
    }
    const sessions = await getAll(db, STORES.sessions, 'by_patient', currentPatient.id);
    currentSession = resumeOrCreateSession(sessions, currentPatient.id);
    await put(db, STORES.sessions, currentSession);
    const trials = await getAll(db, STORES.trials, 'by_session', currentSession.id);
    currentTrialCount = trials.length;
    refreshExportLock();
  }
```

Gate recording on the same state, as the first lines of the existing `#start` click handler:

```js
    // Checked before any capture starts rather than at persist time: a trial
    // recorded with nowhere to file it would be scored, shown, and then
    // silently dropped by persistTrial.
    if (!currentPatient || !currentSide) {
      el('guide').className = 'fault';
      el('guide').textContent = 'Set a participant and leg\nin Session first';
      return;
    }
```

Replace `TRIAL_SIDE` at the `makeTrialRecord` call (`app.js:31`, used at ~`app.js:537`) with `currentSide`.

Register the view, wiring the reducer to the stores:

```js
  let participantError = null;

  router.register('session', createSessionView({
    el,
    context: () => ({ patient: currentPatient, side: currentSide, error: participantError }),
    listPatients: async () => {
      await ensureSessionReady();
      return { patients: await getAll(db, STORES.patients) };
    },
    addPatient: async (clinicId) => {
      const patient = { id: crypto.randomUUID(), clinic_patient_id: clinicId, created_at: Date.now() };
      await put(db, STORES.patients, patient);
      await applyParticipantAction({ type: 'select', patient });
    },
    selectPatient: (patient) => applyParticipantAction({ type: 'select', patient }),
    selectSide: (side) => applyParticipantAction({ type: 'side', side }),
  }));

  // One funnel for both actions so the "cannot switch mid-session" rule lives
  // in the tested reducer rather than being re-decided at each call site.
  async function applyParticipantAction(action) {
    const next = nextParticipantState(
      { patient: currentPatient, side: currentSide, trialCount: currentTrialCount },
      action,
    );
    participantError = next.error ?? null;
    if (next.patient !== currentPatient) {
      currentPatient = next.patient;
      await put(db, STORES.settings, { key: SETTING_KEYS.activePatient, value: currentPatient.id });
      // A different participant means a different session; force the next
      // ensureSessionReady() to resolve one rather than reusing the old
      // patient's.
      sessionReadyPromise = null;
      await ensureSessionReady();
    }
    if (next.side !== currentSide) {
      currentSide = next.side;
      await put(db, STORES.settings, { key: SETTING_KEYS.side, value: currentSide });
    }
  }
```

Add the imports:

```js
import { patientLabel, nextParticipantState, createSessionView, SETTING_KEYS } from './views/session.js';
import { getOne } from './db.js';
```

- [ ] **Step 6: Run test to verify it passes**

Run: `cd webapp && npm test`
Expected: PASS

- [ ] **Step 7: Verify in the browser**

Run: `cd webapp && python dev_server.py`. Add a participant, pick a leg, record a trial, and confirm the home hero tile reads `P-014 · left leg`. Reload — the participant and leg persist. Try switching participant with a trial recorded: refused with the export-and-close message.

- [ ] **Step 8: Commit**

```bash
git add webapp/src/views/session.js webapp/src/app.js webapp/index.html webapp/tests/app.test.js
git commit -m "feat: real participant entry and left/right side selector"
```

---

### Task 9: Trial history

**Files:**
- Create: `webapp/src/views/trials.js`
- Modify: `webapp/index.html` (`#view-trials`), `webapp/src/app.js`
- Test: `webapp/tests/app.test.js`

**Interfaces:**
- Consumes: `getAll`, `STORES` from `../db.js`; `formatValue` behavior from `app.js`
- Produces: `createTrialsView({ el, loadTrials, showTrial }) -> { onEnter() }`, `trialSummary(trial, index) -> {label, meta, id}`

- [ ] **Step 1: Write the failing test**

Append to `webapp/tests/app.test.js`:

```js
import { trialSummary } from '../src/views/trials.js';

const trial = {
  id: 't-1', side: 'left', timestamp: Date.UTC(2026, 7, 31, 14, 5, 0),
  capture_quality: 'clean', unmeasured: [], params: { n: 4.25, a0_deg: 41.2 },
};

test('a trial summary is numbered from one, not zero', () => {
  assert.equal(trialSummary(trial, 0).label, 'Trial 1');
});

test('the summary carries side and the two headline parameters', () => {
  const s = trialSummary(trial, 0);
  assert.match(s.meta, /left/);
  assert.match(s.meta, /4\.25/);
  assert.match(s.meta, /41\.2/);
});

test('the summary carries the trial id so a row can open its detail', () => {
  assert.equal(trialSummary(trial, 0).id, 't-1');
});

// An unscorable trial is an expected clinical outcome, not a fault -- it must
// still be listed, or the operator cannot tell it was recorded at all.
test('a trial with no params still lists', () => {
  const s = trialSummary({ ...trial, params: {} }, 2);
  assert.equal(s.label, 'Trial 3');
  assert.match(s.meta, /left/);
});

test('a trial with unmeasured parameters is flagged in the summary', () => {
  const s = trialSummary({ ...trial, unmeasured: ['r2n', 'f'] }, 0);
  assert.match(s.meta, /2 unmeasured/);
});

test('a null side reads as unset rather than "null"', () => {
  const s = trialSummary({ ...trial, side: null }, 0);
  assert.ok(!s.meta.includes('null'));
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd webapp && node --test tests/app.test.js`
Expected: FAIL — `Cannot find module '../src/views/trials.js'`

- [ ] **Step 3: Write the implementation**

Create `webapp/src/views/trials.js`:

```js
// The trials recorded in the current session. Before this view the page only
// ever showed the most recent trial, so an operator could not confirm that
// trial 3 of 5 had actually been captured without exporting.

// Pure. One row's display text.
export function trialSummary(trial, index) {
  const bits = [];
  bits.push(trial.side ? String(trial.side) : 'side not set');
  const n = trial.params && trial.params.n;
  if (typeof n === 'number') bits.push(`N ${n.toFixed(2)}`);
  const a0 = trial.params && trial.params.a0_deg;
  if (typeof a0 === 'number') bits.push(`A0 ${a0.toFixed(1)}°`);
  const unmeasured = (trial.unmeasured || []).length;
  if (unmeasured) bits.push(`${unmeasured} unmeasured`);
  return { id: trial.id, label: `Trial ${index + 1}`, meta: bits.join(' · ') };
}

export function createTrialsView({ el, loadTrials, showTrial }) {
  return {
    async onEnter() {
      const list = el('trial-list');
      list.textContent = '';
      const trials = await loadTrials();
      if (trials.length === 0) {
        const p = document.createElement('p');
        p.className = 'empty';
        p.textContent = 'No trials recorded in this session yet.';
        list.append(p);
        return;
      }
      // Rows are built with createElement rather than innerHTML:
      // clinic_patient_id is free text a clinician types, and it reaches this
      // list through the trial's own record.
      for (const [i, t] of trials.entries()) {
        const s = trialSummary(t, i);
        const row = document.createElement('button');
        row.className = 'tile trial-row';
        row.dataset.trialId = s.id;
        const text = document.createElement('span');
        text.className = 'tile-text';
        const title = document.createElement('span');
        title.className = 'tile-title';
        title.textContent = s.label;
        const sub = document.createElement('span');
        sub.className = 'tile-sub';
        sub.textContent = s.meta;
        text.append(title, sub);
        row.append(text);
        row.addEventListener('click', () => showTrial(t));
        list.append(row);
      }
    },
  };
}
```

- [ ] **Step 4: Register it in `app.js`**

```js
  router.register('trials', createTrialsView({
    el,
    loadTrials: async () => {
      await ensureSessionReady();
      if (!currentSession) return [];
      const trials = await getAll(db, STORES.trials, 'by_session', currentSession.id);
      return trials.sort((a, b) => a.timestamp - b.timestamp);
    },
    // Re-uses the capture view's own renderers rather than a second copy --
    // a divergent second waveform/score renderer is exactly the kind of
    // duplicate this codebase has been bitten by before.
    showTrial: (t) => {
      lastTrajectory = t.trajectory;
      renderResult(t.params);
      drawWaveform(t.trajectory);
      router.navigate('capture');
    },
  }));
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd webapp && npm test`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add webapp/src/views/trials.js webapp/src/app.js webapp/tests/app.test.js
git commit -m "feat: add in-session trial history"
```

---

### Task 10: MAS entry form with drafts

**Files:**
- Create: `webapp/src/views/mas.js`
- Modify: `webapp/index.html` (`#view-mas`), `webapp/src/app.css`, `webapp/src/app.js`
- Test: `webapp/tests/mas-store.test.js`

**Interfaces:**
- Consumes: `MAS_ORDER`, `PENDING_MAS_GRADE`, `STRONGER_LEG_OPTIONS`, `LEG_OPTIONS`, `MAS_FIELDS`, `validateMasForm`, `makeMasRecord`, `isPending` from `../mas-store.js`; `put`, `getOne`, `getAll`, `STORES` from `../db.js`
- Produces: `draftKey(patientId, leg) -> string`, `createMasView({...}) -> { onEnter() }`

- [ ] **Step 1: Write the failing test**

Append to `webapp/tests/mas-store.test.js`:

```js
import { draftKey } from '../src/views/mas.js';

test('a draft key is scoped to participant and leg', () => {
  assert.equal(draftKey('pat-1', 'left'), 'mas-draft:pat-1:left');
});

test('two legs of one participant keep separate drafts', () => {
  assert.notEqual(draftKey('pat-1', 'left'), draftKey('pat-1', 'right'));
});

test('an unset leg still yields a usable key rather than "undefined"', () => {
  assert.equal(draftKey('pat-1', null), 'mas-draft:pat-1:');
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd webapp && node --test tests/mas-store.test.js`
Expected: FAIL — `Cannot find module '../src/views/mas.js'`

- [ ] **Step 3: Add the form markup**

In `webapp/index.html`, inside `#view-mas` below its `.view-head`:

```html
    <form id="mas-form" class="form">
      <div class="card">
        <p class="card-label">Who</p>
        <label class="field"><span>Participant ID</span>
          <input name="participant" type="text" autocomplete="off"></label>
        <label class="field"><span>Leg</span>
          <select name="leg">
            <option value="">--</option>
            <option value="left">Left</option>
            <option value="right">Right</option>
          </select></label>
        <label class="field"><span>Stronger leg</span>
          <select name="stronger_leg"></select></label>
      </div>

      <div class="card">
        <p class="card-label">Context</p>
        <label class="field"><span>Condition</span>
          <input name="condition" type="text" autocomplete="off"></label>
        <label class="field"><span>Diagnosis</span>
          <input name="diagnosis" type="text" autocomplete="off"></label>
      </div>

      <div class="card">
        <p class="card-label">Grades</p>
        <label class="field"><span>MAS grade</span>
          <select name="mas_grade"></select></label>
        <label class="field"><span>MAS flexion</span>
          <select name="mas_flexion"></select></label>
        <label class="field"><span>MAS extension</span>
          <select name="mas_extension"></select></label>
      </div>

      <div class="card">
        <p class="card-label">Assessment</p>
        <label class="field"><span>Assessed by</span>
          <input name="assessed_by" type="text" autocomplete="off"></label>
        <label class="field"><span>Assessed date</span>
          <input name="assessed_date" type="date"></label>
        <label class="field"><span>Notes</span>
          <textarea name="notes" rows="3"></textarea></label>
      </div>

      <p id="mas-errors" class="field-error" hidden></p>
      <p id="mas-status" class="field-status"></p>
      <button class="btn btn--primary" id="mas-save" type="submit">Save assessment</button>
    </form>
    <div id="mas-list"></div>
```

- [ ] **Step 4: Write the implementation**

Create `webapp/src/views/mas.js`:

```js
// The MAS entry form: all 11 of the desktop MasEntryPanel's fields, because
// a column missing here means re-examining a patient, not re-deriving a
// number.

import {
  MAS_ORDER, PENDING_MAS_GRADE, STRONGER_LEG_OPTIONS, LEG_OPTIONS, MAS_FIELDS,
  validateMasForm, makeMasRecord, isPending,
} from '../mas-store.js';

// Drafts live in IndexedDB `settings`, NOT sessionStorage: sessionStorage is
// cleared when the standalone app is terminated, which is precisely the
// eviction-then-relaunch case a draft exists to survive. A half-filled MAS
// form is a clinical observation -- losing it means re-examining the patient.
export function draftKey(patientId, leg) {
  return `mas-draft:${patientId ?? ''}:${leg ?? ''}`;
}

const GRADE_OPTIONS = [
  { value: '', label: '--' },
  ...MAS_ORDER.map((g) => ({ value: g, label: g })),
  // Explicit, and never the default. append_mas_score() rejects an empty
  // mas_grade outright, so requiring a deliberate choice here is what keeps
  // '-1' from being what an untouched picker yields.
  { value: PENDING_MAS_GRADE, label: 'not yet assessed' },
];

// Optional grades take the inverse rule: blank IS "not assessed", and the
// pending sentinel is invalid -- so it is not offered.
const OPTIONAL_GRADE_OPTIONS = [
  { value: '', label: '-- not assessed' },
  ...MAS_ORDER.map((g) => ({ value: g, label: g })),
];

function fill(select, options) {
  select.textContent = '';
  for (const o of options) {
    const opt = document.createElement('option');
    opt.value = o.value;
    opt.textContent = o.label;
    select.append(opt);
  }
}

function readForm(form) {
  const out = {};
  for (const f of MAS_FIELDS) out[f] = form.elements[f] ? form.elements[f].value : '';
  return out;
}

function writeForm(form, values) {
  for (const f of MAS_FIELDS) {
    if (form.elements[f]) form.elements[f].value = values[f] ?? '';
  }
}

export function createMasView({ el, saveRecord, loadRecords, loadDraft, saveDraft, clearDraft, context }) {
  const form = el('mas-form');
  let ready = false;

  function initOnce() {
    if (ready) return;
    fill(form.elements.mas_grade, GRADE_OPTIONS);
    fill(form.elements.mas_flexion, OPTIONAL_GRADE_OPTIONS);
    fill(form.elements.mas_extension, OPTIONAL_GRADE_OPTIONS);
    fill(form.elements.stronger_leg, STRONGER_LEG_OPTIONS.map(
      (v) => ({ value: v, label: v === '' ? '-- not assessed' : v })));

    // Debounced so a fast typist does not queue one IndexedDB write per
    // keystroke; 400ms is short enough that a termination loses at most a
    // few characters.
    let timer = null;
    const persist = () => {
      clearTimeout(timer);
      timer = setTimeout(() => {
        const { patientId } = context();
        saveDraft(draftKey(patientId, form.elements.leg.value), readForm(form));
      }, 400);
    };
    form.addEventListener('input', persist);
    form.addEventListener('change', persist);

    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      const { patientId } = context();
      const values = readForm(form);
      const { ok, errors } = validateMasForm(values);
      const errEl = el('mas-errors');
      if (!ok) {
        errEl.textContent = errors.join(' ');
        errEl.hidden = false;
        return;
      }
      errEl.hidden = true;
      try {
        await saveRecord(makeMasRecord({ patientId, form: values }));
        await clearDraft(draftKey(patientId, values.leg));
        el('mas-status').textContent = isPending(values)
          ? 'Saved as pending -- add the overall grade before the study closes.'
          : 'Saved.';
        await renderList();
      } catch (err) {
        // A ConstraintError is the `by_identity` unique index rejecting a
        // duplicate (same participant, leg, condition and date). That is a
        // recoverable situation, not a failure -- say so.
        el('mas-status').textContent = err && err.name === 'ConstraintError'
          ? 'An assessment already exists for this leg on this date. Change the date or edit the existing one.'
          : `Save failed: ${err instanceof Error ? err.message : String(err)}`;
      }
    });

    ready = true;
  }

  async function renderList() {
    const list = el('mas-list');
    list.textContent = '';
    for (const r of await loadRecords()) {
      const row = document.createElement('div');
      row.className = 'card mas-row';
      const t = document.createElement('p');
      t.className = 'tile-title';
      t.textContent = `${r.leg} · ${isPending(r) ? 'pending' : r.mas_grade}`;
      const m = document.createElement('p');
      m.className = 'tile-sub';
      m.textContent = `${r.assessed_date}${r.condition ? ` · ${r.condition}` : ''}`;
      row.append(t, m);
      if (isPending(r)) row.classList.add('is-pending');
      list.append(row);
    }
  }

  return {
    async onEnter() {
      initOnce();
      const { patientId, participantLabel, side } = context();
      const draft = await loadDraft(draftKey(patientId, side));
      if (draft) {
        writeForm(form, draft);
      } else {
        // Prefill rather than leave blank: the participant and leg are
        // already known from the session, and re-typing them is where a
        // transcription error enters.
        writeForm(form, {
          participant: participantLabel,
          leg: LEG_OPTIONS.includes(side) ? side : '',
          assessed_date: new Date().toISOString().slice(0, 10),
        });
      }
      el('mas-status').textContent = '';
      el('mas-errors').hidden = true;
      await renderList();
    },
  };
}
```

- [ ] **Step 5: Add form and card CSS**

Append to `webapp/src/app.css`:

```css
/* ---- Cards (workbench_style.card_frame) ---------------------------------- */
.card {
  background: var(--panel);
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 12px 14px;
  margin: 0 16px 12px;
}
.card-label {
  margin: 0 0 8px;
  font-size: clamp(11px, 3vw, 13px); font-weight: 600;
  text-transform: uppercase; letter-spacing: 0.04em; color: var(--fg3);
}

.field { display: flex; flex-direction: column; gap: 4px; margin-bottom: 10px; }
.field > span { font-size: clamp(12px, 3.4vw, 14px); font-weight: 600; color: var(--fg2); }
.field input, .field select, .field textarea, #participant-select, #participant-new {
  font: inherit;
  padding: 10px 12px;
  min-height: 44px;
  border: 1px solid var(--border);
  border-radius: 10px;
  background: var(--surface);
  color: var(--fg);
}
.field input:focus, .field select:focus, .field textarea:focus { outline: 2px solid var(--accent); }
.field-row { display: flex; gap: 8px; margin-top: 8px; }
.field-row #participant-new { flex: 1; min-width: 0; }
#participant-select { width: 100%; }

.field-error {
  margin: 0 16px 8px; font-weight: 700;
  font-size: clamp(12px, 3.4vw, 14px); color: var(--banner-bg);
}
.field-error[hidden] { display: none; }
.field-status { margin: 0 16px 8px; font-size: clamp(12px, 3.2vw, 14px); color: var(--fg3); }

#mas-save { margin: 0 16px 16px; width: calc(100% - 32px); }

/* A pending assessment is incomplete, not wrong -- the same amber the
   unmeasured-parameter notice uses, never the fault red. */
.mas-row.is-pending { border-left: 4px solid var(--holding-bg); }
.empty { margin: 16px; color: var(--fg3); }

/* Segmented left/right control. Both options are always visible, so the
   choice is legible without opening a picker. */
.seg { display: flex; gap: 8px; }
.seg-btn {
  flex: 1; min-height: 44px; font: inherit; font-weight: 700;
  border: 1px solid var(--border); border-radius: 10px;
  background: var(--surface); color: var(--fg); cursor: pointer;
}
.seg-btn[aria-pressed="true"] { background: var(--accent); border-color: var(--accent); color: #fff; }
```

- [ ] **Step 6: Register the view in `app.js`**

```js
  router.register('mas', createMasView({
    el,
    context: () => ({
      patientId: currentPatient?.id ?? null,
      participantLabel: currentPatient?.clinic_patient_id ?? '',
      side: currentSide,
    }),
    saveRecord: async (record) => {
      await ensureSessionReady();
      await put(db, STORES.mas, record);
      // A new assessment is unexported session data, so it re-arms the close
      // lock exactly as a new trial does.
      currentSession = invalidateExport(currentSession);
      await put(db, STORES.sessions, currentSession);
      refreshExportLock();
    },
    loadRecords: async () => (currentPatient
      ? getAll(db, STORES.mas, 'by_patient', currentPatient.id)
      : []),
    loadDraft: async (key) => (await getOne(db, STORES.settings, key))?.value ?? null,
    saveDraft: (key, value) => put(db, STORES.settings, { key, value }),
    clearDraft: (key) => put(db, STORES.settings, { key, value: null }),
  }));
```

- [ ] **Step 7: Run test to verify it passes**

Run: `cd webapp && npm test`
Expected: PASS

- [ ] **Step 8: Verify draft survival in the browser**

Run: `cd webapp && python dev_server.py`. Fill three MAS fields, force-reload the tab, reopen MAS Entry.
Expected: the three values are still there. Save once, then save again with the same participant/leg/condition/date: the duplicate message appears rather than a second row.

- [ ] **Step 9: Commit**

```bash
git add webapp/src/views/mas.js webapp/index.html webapp/src/app.css webapp/src/app.js webapp/tests/mas-store.test.js
git commit -m "feat: add MAS entry form with desktop parity and durable drafts"
```

---

### Task 11: Export the MAS CSV and bump the manifest to v2

**Files:**
- Modify: `webapp/src/export.js`, `webapp/src/app.js`
- Test: `webapp/tests/export.test.js`

**Interfaces:**
- Consumes: `buildMasCsv` from `./mas-csv.js`; `MAS_FIELDS` from `./mas-store.js`
- Produces: `buildExportFiles({ session, patient, trials, masRecords })` — now emits `<base>-mas.csv` and a `v2` manifest

- [ ] **Step 1: Write the failing test**

Append to `webapp/tests/export.test.js`:

```js
import { MAS_FIELDS } from '../src/mas-store.js';

const session = { id: 's-1', timestamp: Date.UTC(2026, 7, 31, 12, 0, 0) };
const patient = { clinic_patient_id: 'P-014' };
const trials = [{
  raw_jsonl: '{}\n', side: 'left', timestamp: 1, algorithm_version: '0.1.0',
  capture_quality: 'clean', release_idx: 0, unmeasured: [],
  release_override_idx: null, params: {},
}];
const masRecords = [{
  participant: 'P-014', leg: 'left', condition: 'rest', diagnosis: '',
  mas_grade: '1+', assessed_by: 'CK', assessed_date: '2026-08-31',
  stronger_leg: '', notes: 'settled, no catch', mas_flexion: '2', mas_extension: '',
}];

test('the manifest schema is v2', () => {
  const files = buildExportFiles({ session, patient, trials, masRecords });
  const manifest = JSON.parse(files.find((f) => f.name.endsWith('-manifest.json')).text);
  assert.equal(manifest.schema, 'pendulastic/session-export/v2');
});

test('a mas csv is emitted beside the trials', () => {
  const files = buildExportFiles({ session, patient, trials, masRecords });
  const csv = files.find((f) => f.name.endsWith('-mas.csv'));
  assert.ok(csv, 'expected a -mas.csv file');
  assert.equal(csv.type, 'text/csv');
  assert.equal(csv.text.split('\r\n')[0], MAS_FIELDS.join(','));
});

test('the csv and the manifest block agree row for row', () => {
  const files = buildExportFiles({ session, patient, trials, masRecords });
  const manifest = JSON.parse(files.find((f) => f.name.endsWith('-manifest.json')).text);
  const csvRows = files.find((f) => f.name.endsWith('-mas.csv')).text
    .trim().split('\r\n').slice(1);
  assert.equal(manifest.mas.length, csvRows.length);
  assert.equal(manifest.mas[0].mas_grade, '1+');
});

// No MAS entered is the common case for a capture-only session; it must not
// produce a header-only file the desktop would append nothing from.
test('no mas records means no mas csv at all', () => {
  const files = buildExportFiles({ session, patient, trials, masRecords: [] });
  assert.equal(files.find((f) => f.name.endsWith('-mas.csv')), undefined);
});

test('an omitted masRecords argument behaves like an empty one', () => {
  const files = buildExportFiles({ session, patient, trials });
  assert.equal(files.find((f) => f.name.endsWith('-mas.csv')), undefined);
  const manifest = JSON.parse(files.find((f) => f.name.endsWith('-manifest.json')).text);
  assert.deepEqual(manifest.mas, []);
});

test('the mas csv shares the trial files stem', () => {
  const files = buildExportFiles({ session, patient, trials, masRecords });
  const csv = files.find((f) => f.name.endsWith('-mas.csv'));
  assert.ok(csv.name.startsWith('pendulastic-P-014-'));
});

test('a notes field with a comma does not shift the csv columns', () => {
  const files = buildExportFiles({
    session, patient, trials,
    masRecords: [{ ...masRecords[0], notes: 'catch, then release' }],
  });
  const row = files.find((f) => f.name.endsWith('-mas.csv')).text.trim().split('\r\n')[1];
  assert.ok(row.includes('"catch, then release"'));
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd webapp && node --test tests/export.test.js`
Expected: FAIL — schema is `v1`, no `-mas.csv`

- [ ] **Step 3: Write the implementation**

In `webapp/src/export.js`, add the import and extend `buildExportFiles`:

```js
import { buildMasCsv } from './mas-csv.js';
import { MAS_FIELDS } from './mas-store.js';
```

```js
export function buildExportFiles({ session, patient, trials, masRecords = [] }) {
  if (!trials || trials.length === 0) return [];
  // ... existing patientPart / base / files ...

  // Emitted only when there is at least one assessment. A header-only file
  // is not harmless: append_mas_score() would read it, find no rows, and the
  // clinician would have an empty artifact suggesting MAS was collected.
  if (masRecords.length > 0) {
    files.push({
      name: `${base}-mas.csv`,
      type: 'text/csv',
      text: buildMasCsv(masRecords),
    });
  }

  const manifest = {
    // v2 adds `mas`. Bumped rather than widened in place: a v1 consumer must
    // not be handed a different shape under an unchanged version string.
    schema: 'pendulastic/session-export/v2',
    // ... existing exported_at / algorithm_version / patient / session / trials ...
    // The same rows as the CSV, projected through MAS_FIELDS so the two are
    // generated from one source in one pass and cannot disagree.
    mas: masRecords.map((r) => Object.fromEntries(MAS_FIELDS.map((k) => [k, r[k] ?? '']))),
  };
  files.push({ name: `${base}-manifest.json`, type: 'application/json', text: JSON.stringify(manifest, null, 2) });
  return files;
}
```

In `app.js`'s export-session handler, load the records and pass them through:

```js
      const masRecords = currentPatient
        ? await getAll(db, STORES.mas, 'by_patient', currentPatient.id)
        : [];
      const files = buildExportFiles({ session: currentSession, patient, trials, masRecords });
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd webapp && npm test`
Expected: PASS

- [ ] **Step 5: Show the pending-assessment count beside the export controls**

A `-1` row exports fine and the desktop accepts it, but it is an assessment the clinician still owes. Surface it where the session is about to leave the device.

Add to `webapp/index.html`, inside `#session-bar` above `#session-status`:

```html
    <p id="mas-pending-count" class="field-status" hidden></p>
```

Extend Task 8's `createSessionView` `render()` — add `countPending` to its injected dependencies and append:

```js
    // A notice, never a block. A pending row is legitimate and the desktop
    // ingests it; the failure this guards against is forgetting one, not
    // exporting one.
    const pending = await countPending();
    const pendEl = el('mas-pending-count');
    pendEl.textContent = pending
      ? `${pending} MAS assessment${pending === 1 ? '' : 's'} still marked "not yet assessed".`
      : '';
    pendEl.hidden = pending === 0;
```

Wire it where the view is registered in `app.js`:

```js
    countPending: async () => (currentPatient
      ? (await getAll(db, STORES.mas, 'by_patient', currentPatient.id)).filter(isPending).length
      : 0),
```

Add `isPending` to `app.js`'s `mas-store.js` import.

- [ ] **Step 6: Verify the CSV against the desktop**

Export a session with one MAS row, then from the repo root:

```bash
python -c "
import mas_validation as m, csv, sys
rows = list(csv.DictReader(open(sys.argv[1], newline='', encoding='utf-8')))
print('columns match:', list(rows[0]) == m.DEFAULT_MAS_FIELDS)
m.append_mas_score(rows[0], csv_path='/tmp/mas_roundtrip.csv')
print('append_mas_score accepted the row')
" <path-to-exported-mas.csv>
```

Expected: `columns match: True` and `append_mas_score accepted the row`, with no `ValueError`.

- [ ] **Step 7: Commit**

```bash
git add webapp/src/export.js webapp/src/app.js webapp/index.html webapp/src/views/session.js webapp/tests/export.test.js
git commit -m "feat: export mas_scores.csv and bump the manifest to v2"
```

---

### Task 12: Styling pass

Retire the back-compat aliases and bring the pre-existing controls onto the card/tile vocabulary.

**Files:**
- Modify: `webapp/src/app.css`

**Interfaces:**
- Consumes: the tokens from Task 1
- Produces: no `--ink` / `--paper` / `--muted` / `--line` references remain

- [ ] **Step 1: Migrate the alias references**

In `webapp/src/app.css`, replace every remaining `var(--ink)` with `var(--fg)`, `var(--muted)` with `var(--fg3)`, `var(--line)` with `var(--border)`, and `var(--paper)` with `var(--surface)`. Then delete the back-compat alias block from `:root`.

- [ ] **Step 2: Bring `#gates` and `#pt-score` onto `.card`**

Replace the bespoke background/border/radius declarations in the `.gate` and `#pt-score` rules with the card treatment, keeping their existing layout and type rules:

```css
.gate {
  flex: 1;
  background: var(--panel);
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 10px 12px;
  text-align: center;
}
```

Apply `font-family: var(--mono);` to `#gates dd`, `#pt-score-value`, `#pt-score-breakdown td`, and `#result td` — these already carry `font-variant-numeric: tabular-nums`, and `--mono` is `workbench_style.PALETTE["MONO"]`.

- [ ] **Step 3: Verify no alias survives**

Run: `cd webapp && grep -n -- "--ink\|--paper\|--muted\|--line" src/app.css`
Expected: no output.

- [ ] **Step 4: Confirm the state colors were not touched**

Run: `cd webapp && grep -n "6b7280\|b45309\|0f7a37\|1d4ed8\|4b5563\|9f1239\|7a0d0d" src/app.css`
Expected: exactly the seven `:root` declarations from Task 1, unchanged. Any other hit means a safety-critical color was edited.

- [ ] **Step 5: Run the suite**

Run: `cd webapp && npm test`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add webapp/src/app.css
git commit -m "style: retire palette aliases and unify cards on the workbench vocabulary"
```

---

### Task 13: Build, shell verification, and device smoke test

**Files:**
- Modify: `webapp/README.md`

- [ ] **Step 1: Rebuild the shell and dist**

Run: `cd webapp && npm run build:dist`
Expected: succeeds. `src/build-id.js`'s `SHELL` now lists `./src/router.js`, `./src/mas-store.js`, `./src/mas-csv.js`, and the four `./src/views/*.js`, and `BUILD_ID` has changed.

- [ ] **Step 2: Confirm the shell picked up every new module**

Run: `cd webapp && node -e "import('./src/build-id.js').then(m => console.log(m.SHELL.filter(s => s.includes('views') || s.includes('router') || s.includes('mas')).join('\n')))"`
Expected: seven paths. A missing one means `shell-list.mjs`'s walk is not recursing into `src/views/` — fix the walk, because a module absent from `SHELL` is not cached and the app breaks offline only.

- [ ] **Step 3: Run the whole suite once more**

Run: `cd webapp && npm test`
Expected: PASS

- [ ] **Step 4: Smoke test on a phone**

Serve `dist/` over HTTPS or via `python dev_server.py` on the LAN, install to the Home Screen, then walk: home → set participant and leg in Session → record a trial → check it appears in Trials → enter a MAS assessment → export the session.

Expected: the share sheet offers `<base>-trial1.jsonl`, `<base>-mas.csv`, and `<base>-manifest.json`; Close Session is disabled until the export completes; the banner is visible on all five views.

- [ ] **Step 5: Document the new views in the README**

Add to `webapp/README.md` after the intro, replacing the description of a single-screen app:

```markdown
## Views

The app is five sections toggled by `src/router.js`: **home** (tiles),
**capture**, **trials** (this session's history), **mas** (assessment entry),
and **session** (participant, leg, export, close).

Views use `.view` / `.view.active`, never the `hidden` attribute -- see the
note above the `.view` rule in `src/app.css` for why.

Leaving the capture view is refused while a trial is recording: a live
capture owns a `devicemotion` listener, a flush interval, and a screen wake
lock, and navigating away would orphan all three.

## MAS export

A session with assessments exports `<base>-mas.csv` alongside the trial
`.jsonl` files, with exactly `mas_validation.DEFAULT_MAS_FIELDS` as its
header, so `append_mas_score()` ingests it unchanged. `mas_grade` may be `-1`
("not yet assessed"); it may never be blank. The optional grades take the
inverse rule -- blank is valid, `-1` is not. See
`docs/superpowers/specs/2026-08-31-mobile-webapp-workbench-restyle-design.md`.
```

- [ ] **Step 6: Commit**

```bash
git add webapp/README.md webapp/src/build-id.js
git commit -m "docs: describe the view shell and MAS export in the webapp README"
```

---

## Deployment

Per `pendulastic_webapp_vercel_deploy`, the live app at `pendulastic-app.vercel.app` is deployed by **CLI folder upload of `webapp/dist/`**, not a git build — the host has no Rust toolchain for `build:wasm`. Do not connect a git integration, and do not let the dashboard select the Python preset. Deploy only after Task 13's smoke test passes.
