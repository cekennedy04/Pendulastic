import { test } from 'node:test';
import assert from 'node:assert/strict';
import { buildExportFiles, shareFiles, downloadViaAnchor } from '../src/export.js';
import { PARAM_FIELDS } from '../src/session-store.js';
import { MAS_FIELDS } from '../src/mas-store.js';

const params = Object.fromEntries(PARAM_FIELDS.map((k, i) => [k, i]));
const trial = (id, raw) => ({
  id, session_id: 's1', side: 'left', timestamp: 1, algorithm_version: '0.1.0',
  capture_quality: 'clean', release_idx: 3, release_override_idx: null,
  params, raw_jsonl: raw,
});

test('one jsonl file per trial plus one manifest', () => {
  const files = buildExportFiles({
    session: { id: 's1', patient_id: 'p1', timestamp: 1, exported_at: null },
    patient: { id: 'p1', clinic_patient_id: 'ANON-7', created_at: 1 },
    trials: [trial('t1', 'a\n'), trial('t2', 'b\n')],
  });
  const names = files.map((f) => f.name);
  assert.equal(files.filter((f) => f.name.endsWith('.jsonl')).length, 2);
  assert.equal(files.filter((f) => f.name.endsWith('.json')).length, 1);
  assert.ok(names.every((n) => n.includes('ANON-7')), `names should carry the participant id: ${names}`);
});

test('each trial file is the raw log verbatim, not re-serialised', () => {
  // The raw JSONL was produced by the Rust exporter against a contract pinned
  // in tests/test_web_export_contract.py. Re-encoding it here would put a
  // second, untested implementation of that contract in the path.
  const raw = '{"t":0.1,"role":"distal","sensor":"accel","v":[0,0,9.81],"phone_ts_ms":100}\n';
  const files = buildExportFiles({
    session: { id: 's1', patient_id: 'p1', timestamp: 1, exported_at: null },
    patient: { id: 'p1', clinic_patient_id: 'ANON-7', created_at: 1 },
    trials: [trial('t1', raw)],
  });
  assert.equal(files.find((f) => f.name.endsWith('.jsonl')).text, raw);
});

test('the manifest carries params but no composite score', () => {
  const files = buildExportFiles({
    session: { id: 's1', patient_id: 'p1', timestamp: 1, exported_at: null },
    patient: { id: 'p1', clinic_patient_id: 'ANON-7', created_at: 1 },
    trials: [trial('t1', 'a\n')],
  });
  const manifest = JSON.parse(files.find((f) => f.name.endsWith('.json')).text);
  assert.deepEqual(Object.keys(manifest.trials[0].params).sort(), PARAM_FIELDS.slice().sort());
  assert.ok(!('pt_score' in manifest.trials[0]), 'composite must be derived at read time, never exported as fact');
  assert.equal(manifest.algorithm_version, '0.1.0');
});

test('an empty session produces no files rather than an empty archive', () => {
  const files = buildExportFiles({
    session: { id: 's1', patient_id: 'p1', timestamp: 1, exported_at: null },
    patient: { id: 'p1', clinic_patient_id: 'ANON-7', created_at: 1 },
    trials: [],
  });
  assert.equal(files.length, 0);
});

test('a hostile clinic_patient_id cannot inject path separators, a leading dot, or non-ASCII into a filename', () => {
  // clinic_patient_id is unconstrained free text: db.js keys patients by a
  // UUID and never validates it, and there is no app.js form yet that could
  // add its own constraint. This is the one place that turns it into a
  // filename that reaches new File(...), an <a download> attribute, and an
  // iOS share sheet.
  const hostile = '.hidden/../é evil name';
  const files = buildExportFiles({
    session: { id: 's1', patient_id: 'p1', timestamp: 1, exported_at: null },
    patient: { id: 'p1', clinic_patient_id: hostile, created_at: 1 },
    trials: [trial('t1', 'a\n'), trial('t2', 'b\n')],
  });
  const names = files.map((f) => f.name);
  for (const n of names) {
    assert.match(n, /^[A-Za-z0-9_-]+\.(jsonl|json)$/, `unsafe filename: ${n}`);
  }
  assert.equal(new Set(names).size, names.length, 'names must stay unique per trial');
});

test('a clinic_patient_id that sanitises away to nothing still produces a stable, non-empty filename stem', () => {
  const files = buildExportFiles({
    session: { id: 's1', patient_id: 'p1', timestamp: 1, exported_at: null },
    patient: { id: 'p1', clinic_patient_id: '.../ é', created_at: 1 },
    trials: [trial('t1', 'a\n')],
  });
  const names = files.map((f) => f.name);
  for (const n of names) {
    assert.match(n, /^[A-Za-z0-9_-]+\.(jsonl|json)$/, `unsafe filename: ${n}`);
  }
});

test('the manifest records each trial\'s own algorithm_version, not just the first trial\'s', () => {
  const t1 = trial('t1', 'a\n');
  const t2 = { ...trial('t2', 'b\n'), algorithm_version: '0.2.0' };
  const files = buildExportFiles({
    session: { id: 's1', patient_id: 'p1', timestamp: 1, exported_at: null },
    patient: { id: 'p1', clinic_patient_id: 'ANON-7', created_at: 1 },
    trials: [t1, t2],
  });
  const manifest = JSON.parse(files.find((f) => f.name.endsWith('.json')).text);
  assert.equal(manifest.trials[0].algorithm_version, '0.1.0');
  assert.equal(manifest.trials[1].algorithm_version, '0.2.0');
});

test('shareFiles rejects on an empty file list rather than reporting a successful no-op', async () => {
  // Task 6 gates session-close on export succeeding. A no-op that returned
  // 'downloaded' would let a session be marked exported with no byte ever
  // having left the device -- this must fail loudly, not silently succeed.
  await assert.rejects(() => shareFiles([]));
  await assert.rejects(() => shareFiles(undefined));
});

// The download fallback is the only path on this branch that can lose an
// export SILENTLY. shareFiles returns 'downloaded' unconditionally once it
// takes this branch, the caller's compare-and-swap then passes, and the
// session is marked exported -- so if the anchor never actually downloads
// anything, a session is recorded as archived with nothing having left the
// device. Two mistakes cause exactly that, and both were present:
//
//   - the anchor was never inserted into the document (some browsers ignore
//     a click on a detached anchor outright)
//   - the object URL was revoked in the SAME synchronous tick as the click,
//     which can pull the blob out from under a download that has been queued
//     but not yet started -- in a loop over N files, N times over
//
// Neither raises an error. The ordering is therefore pinned here directly.

// A DOM stand-in that records the call order. Only what downloadViaAnchor
// touches is implemented; anything else it started using would throw rather
// than quietly no-op.
function fakeDom() {
  const calls = [];
  const anchors = [];
  const doc = {
    createElement(tag) {
      assert.equal(tag, 'a');
      const a = {
        href: null,
        download: null,
        click: () => calls.push(`click:${a.download}`),
        remove: () => calls.push(`remove:${a.download}`),
      };
      anchors.push(a);
      return a;
    },
    body: {
      appendChild(a) { calls.push(`append:${a.download}`); },
    },
  };
  const urlRef = {
    created: [],
    createObjectURL(blob) {
      const url = `blob:fake/${urlRef.created.length}`;
      urlRef.created.push(url);
      calls.push(`create:${url}`);
      return url;
    },
    revokeObjectURL(url) { calls.push(`revoke:${url}`); },
  };
  return { calls, anchors, doc, urlRef };
}

const tick = () => new Promise((resolve) => setTimeout(resolve, 0));

test('the download anchor is attached before it is clicked and removed after', async () => {
  const { calls, anchors, doc, urlRef } = fakeDom();
  downloadViaAnchor({ name: 'a.jsonl', type: 'application/x-ndjson', text: 'x\n' }, { documentRef: doc, urlRef });

  const append = calls.indexOf('append:a.jsonl');
  const click = calls.indexOf('click:a.jsonl');
  const remove = calls.indexOf('remove:a.jsonl');
  assert.ok(append !== -1, 'a detached anchor may be ignored outright -- it must be appended');
  assert.ok(append < click, 'the anchor must be in the document BEFORE the click');
  assert.ok(click < remove, 'the anchor must only be removed after the click');
  assert.equal(anchors[0].download, 'a.jsonl');
  assert.equal(anchors[0].href, urlRef.created[0]);
  await tick();
});

test('the object URL is revoked on a later tick, never in the same one as the click', async () => {
  const { calls, doc, urlRef } = fakeDom();
  downloadViaAnchor({ name: 'a.jsonl', type: 'application/x-ndjson', text: 'x\n' }, { documentRef: doc, urlRef });

  assert.ok(
    !calls.some((c) => c.startsWith('revoke:')),
    'revoking in the click\'s own tick can pull the blob out from under a queued download -- silently',
  );
  await tick();
  assert.deepEqual(calls.filter((c) => c.startsWith('revoke:')), [`revoke:${urlRef.created[0]}`]);
});

test('shareFiles downloads every file, each with its own anchor, when sharing is unavailable', async () => {
  // A trial that is downloaded but whose manifest is not (or vice versa) is
  // an incomplete archive that still reports 'downloaded'.
  const { calls, doc, urlRef } = fakeDom();
  const files = [
    { name: 't1.jsonl', type: 'application/x-ndjson', text: 'a\n' },
    { name: 't2.jsonl', type: 'application/x-ndjson', text: 'b\n' },
    { name: 'm.json', type: 'application/json', text: '{}' },
  ];
  const result = await shareFiles(files, { navigatorRef: {}, documentRef: doc, urlRef });

  assert.equal(result, 'downloaded');
  for (const f of files) {
    const append = calls.indexOf(`append:${f.name}`);
    const click = calls.indexOf(`click:${f.name}`);
    const remove = calls.indexOf(`remove:${f.name}`);
    assert.ok(append !== -1 && append < click && click < remove, `bad anchor lifecycle for ${f.name}: ${calls}`);
  }
  assert.equal(urlRef.created.length, 3, 'one object URL per file');
  await tick();
  assert.equal(calls.filter((c) => c.startsWith('revoke:')).length, 3, 'every object URL must eventually be revoked');
});

test('shareFiles prefers the share sheet and never touches the download path when it works', async () => {
  const { calls, doc, urlRef } = fakeDom();
  let shared = null;
  const navigatorRef = {
    canShare: () => true,
    share: async (payload) => { shared = payload; },
  };
  const result = await shareFiles(
    [{ name: 't1.jsonl', type: 'application/x-ndjson', text: 'a\n' }],
    { navigatorRef, documentRef: doc, urlRef },
  );
  assert.equal(result, 'shared');
  assert.equal(shared.files.length, 1);
  assert.deepEqual(calls, [], 'the anchor fallback must not run when the share sheet handled it');
});

// ---- MAS export (task 11) -------------------------------------------------
const masSession = { id: 's-1', timestamp: Date.UTC(2026, 7, 31, 12, 0, 0) };
const masPatient = { clinic_patient_id: 'P-014' };
const masTrials = [{
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
  const files = buildExportFiles({ session: masSession, patient: masPatient, trials: masTrials, masRecords });
  const manifest = JSON.parse(files.find((f) => f.name.endsWith('-manifest.json')).text);
  assert.equal(manifest.schema, 'pendulastic/session-export/v2');
});

test('a mas csv is emitted beside the trials', () => {
  const files = buildExportFiles({ session: masSession, patient: masPatient, trials: masTrials, masRecords });
  const csv = files.find((f) => f.name.endsWith('-mas.csv'));
  assert.ok(csv, 'expected a -mas.csv file');
  assert.equal(csv.type, 'text/csv');
  assert.equal(csv.text.split('\r\n')[0], MAS_FIELDS.join(','));
});

test('the csv and the manifest block agree row for row', () => {
  const files = buildExportFiles({ session: masSession, patient: masPatient, trials: masTrials, masRecords });
  const manifest = JSON.parse(files.find((f) => f.name.endsWith('-manifest.json')).text);
  const csvRows = files.find((f) => f.name.endsWith('-mas.csv')).text
    .trim().split('\r\n').slice(1);
  assert.equal(manifest.mas.length, csvRows.length);
  assert.equal(manifest.mas[0].mas_grade, '1+');
});

// No MAS entered is the common case for a capture-only session; it must not
// produce a header-only file the desktop would append nothing from.
test('no mas records means no mas csv at all', () => {
  const files = buildExportFiles({ session: masSession, patient: masPatient, trials: masTrials, masRecords: [] });
  assert.equal(files.find((f) => f.name.endsWith('-mas.csv')), undefined);
});

test('an omitted masRecords argument behaves like an empty one', () => {
  const files = buildExportFiles({ session: masSession, patient: masPatient, trials: masTrials });
  assert.equal(files.find((f) => f.name.endsWith('-mas.csv')), undefined);
  const manifest = JSON.parse(files.find((f) => f.name.endsWith('-manifest.json')).text);
  assert.deepEqual(manifest.mas, []);
});

test('the mas csv shares the trial files stem', () => {
  const files = buildExportFiles({ session: masSession, patient: masPatient, trials: masTrials, masRecords });
  const csv = files.find((f) => f.name.endsWith('-mas.csv'));
  assert.ok(csv.name.startsWith('pendulastic-P-014-'));
});

test('a notes field with a comma does not shift the csv columns', () => {
  const files = buildExportFiles({
    session: masSession, patient: masPatient, trials: masTrials,
    masRecords: [{ ...masRecords[0], notes: 'catch, then release' }],
  });
  const row = files.find((f) => f.name.endsWith('-mas.csv')).text.trim().split('\r\n')[1];
  assert.ok(row.includes('"catch, then release"'));
});
