import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  nextOutcome, resumeOrCreateSession, sessionLockState, invalidateExport, canMarkExported,
  exportLockState, retainExportHandle, zoneDisplay, unmeasuredNotice, excursionNotice,
} from '../src/app.js';
import { canCloseSession, markExported } from '../src/session-store.js';
import { createCaptureView } from '../src/views/capture.js';

// nextOutcome is app.js's pure fault-latch reducer over the onResult/onError
// message stream for one trial -- no DOM, no worker, no globals required
// (mirrors the createWorkerHandler / encodeSample split used in earlier
// tasks). It exists because `onError`'s call to `session.stop()` posts a
// second `finish` to the worker, whose idempotent reply must not be allowed
// to silently overwrite a fault the clinician already saw (fix-round-1
// finding).

test('a normal result displays and does not latch', () => {
  const { latched, action } = nextOutcome(false, { type: 'result', params: { f: 1 } });
  assert.equal(latched, false);
  assert.deepEqual(action, { kind: 'result', params: { f: 1 } });
});

test('an unscorable outcome displays and does not latch', () => {
  const { latched, action } = nextOutcome(false, { type: 'error', reason: 'unscorable' });
  assert.equal(latched, false, "'unscorable' is an expected clinical outcome, not a fault");
  assert.deepEqual(action, { kind: 'unscorable' });
});

test('a genuine fault displays and latches', () => {
  const { latched, action } = nextOutcome(false, { type: 'error', reason: 'worker crashed' });
  assert.equal(latched, true);
  assert.deepEqual(action, { kind: 'fault', reason: 'worker crashed' });
});

test('once latched, a bounced real result is ignored rather than overwriting the fault', () => {
  const { latched, action } = nextOutcome(true, { type: 'result', params: { f: 1 } });
  assert.equal(latched, true, 'the latch must stay set');
  assert.equal(action, null, 'no display action once a fault has latched this trial');
});

test('once latched, a bounced unscorable is ignored rather than overwriting the fault', () => {
  const { latched, action } = nextOutcome(true, { type: 'error', reason: 'unscorable' });
  assert.equal(latched, true);
  assert.equal(action, null);
});

test('an unscorable outcome does not itself latch out a later genuine result', () => {
  // 'unscorable' must never swallow anything that follows it in the same
  // trial -- only a real fault is allowed to latch.
  const first = nextOutcome(false, { type: 'error', reason: 'unscorable' });
  assert.equal(first.latched, false);
  const second = nextOutcome(first.latched, { type: 'result', params: { f: 2 } });
  assert.equal(second.latched, false);
  assert.deepEqual(second.action, { kind: 'result', params: { f: 2 } });
});

test('a real fault always wins over a subsequent unscorable bounce', () => {
  const first = nextOutcome(false, { type: 'error', reason: 'worker crashed' });
  assert.equal(first.latched, true);
  const second = nextOutcome(first.latched, { type: 'error', reason: 'unscorable' });
  assert.equal(second.latched, true);
  assert.equal(second.action, null, 'the fault must not be overwritten by the bounce');
});

test('a result event carrying a trajectory passes it through on the action', () => {
  const trajectory = { t: [0, 0.05], angle_deg: [null, 180], release_idx: 0, peak_idx: [], trough_idx: [], neutral_deg: 180 };
  const { latched, action } = nextOutcome(false, { type: 'result', params: { f: 1 }, trajectory });
  assert.equal(latched, false);
  assert.deepEqual(action, { kind: 'result', params: { f: 1 }, trajectory });
});

test('a result event with no trajectory key omits it from the action rather than adding undefined', () => {
  // worker.js's message always carries a trajectory (falling back to null
  // when finish_trajectory() itself returns nothing), but nextOutcome must
  // not silently invent the key for any caller that omits it -- see the
  // very first test in this file, which relies on exactly this shape.
  const { action } = nextOutcome(false, { type: 'result', params: { f: 1 } });
  assert.deepEqual(action, { kind: 'result', params: { f: 1 } });
  assert.ok(!('trajectory' in action), 'no trajectory key should appear when the event carried none');
});

test('a result event carrying a ptScore passes it through on the action', () => {
  const ptScore = { score: 0.42, zone: 'borderline', breakdown: [{ key: 'area_ratio', value: 0.3 }] };
  const { latched, action } = nextOutcome(false, { type: 'result', params: { f: 1 }, ptScore });
  assert.equal(latched, false);
  assert.deepEqual(action, { kind: 'result', params: { f: 1 }, ptScore });
});

test('a result event with no ptScore key omits it from the action rather than adding undefined', () => {
  const { action } = nextOutcome(false, { type: 'result', params: { f: 1 } });
  assert.ok(!('ptScore' in action), 'no ptScore key should appear when the event carried none');
});

test('a result event can carry both a trajectory and a ptScore together', () => {
  const trajectory = { t: [0, 0.05], angle_deg: [null, 180], release_idx: 0, peak_idx: [], trough_idx: [], neutral_deg: 180 };
  const ptScore = { score: 0.05, zone: 'healthy', breakdown: [] };
  const { action } = nextOutcome(false, { type: 'result', params: { f: 1 }, trajectory, ptScore });
  assert.deepEqual(action, { kind: 'result', params: { f: 1 }, trajectory, ptScore });
});

test('a real fault always wins over a subsequent bounced result', () => {
  const first = nextOutcome(false, { type: 'error', reason: 'worker crashed' });
  assert.equal(first.latched, true);
  const second = nextOutcome(first.latched, { type: 'result', params: { f: 3 } });
  assert.equal(second.latched, true);
  assert.equal(second.action, null, 'the fault must not be overwritten by a bounced result');
});

// resumeOrCreateSession decides, on a fresh page load, which session a
// trial should be attributed to -- resume the patient's still-open (never
// exported) session, or start a brand new one. This is the mechanism that
// keeps a reload mid-session from orphaning already-recorded trials.

test('with no sessions on file, a fresh session is created for the patient', () => {
  const s = resumeOrCreateSession([], 'p1');
  assert.equal(s.patient_id, 'p1');
  assert.equal(s.exported_at, null);
});

test('an existing unexported session for the patient is resumed, not duplicated', () => {
  const open = { id: 'existing', patient_id: 'p1', timestamp: 5, exported_at: null };
  const s = resumeOrCreateSession([open], 'p1');
  assert.equal(s.id, 'existing');
});

test('an exported session is treated as closed and is never resumed', () => {
  const closed = markExported({ id: 'old', patient_id: 'p1', timestamp: 5, exported_at: null }, 999);
  const s = resumeOrCreateSession([closed], 'p1');
  assert.notEqual(s.id, 'old', 'a closed session must not be handed back as the one to keep recording into');
  assert.equal(s.exported_at, null, 'the new session must start unexported');
});

test('a session belonging to a different patient is never resumed', () => {
  const other = { id: 'theirs', patient_id: 'p2', timestamp: 5, exported_at: null };
  const s = resumeOrCreateSession([other], 'p1');
  assert.notEqual(s.id, 'theirs');
  assert.equal(s.patient_id, 'p1');
});

test('when multiple open sessions exist for the patient, the most recently created one is resumed', () => {
  const older = { id: 'older', patient_id: 'p1', timestamp: 10, exported_at: null };
  const newer = { id: 'newer', patient_id: 'p1', timestamp: 20, exported_at: null };
  const s = resumeOrCreateSession([older, newer], 'p1');
  assert.equal(s.id, 'newer');
});

// sessionLockState is the pure decision behind the session-bar UI: whether
// Close is enabled, and whether the unexported-trials warning shows.

test('a brand-new session with zero trials is not closable and shows no warning', () => {
  const s = { id: 's1', patient_id: 'p1', timestamp: 0, exported_at: null };
  assert.deepEqual(sessionLockState(s, 0), { closable: false, warningVisible: false });
});

test('with no session at all, the lock state is inert', () => {
  assert.deepEqual(sessionLockState(null, 0), { closable: false, warningVisible: false });
});

test('a session with recorded, unexported trials is not closable and shows the warning', () => {
  const s = { id: 's1', patient_id: 'p1', timestamp: 0, exported_at: null };
  assert.deepEqual(sessionLockState(s, 3), { closable: false, warningVisible: true });
});

test('a session with recorded trials that has been exported is closable and shows no warning', () => {
  const s = markExported({ id: 's1', patient_id: 'p1', timestamp: 0, exported_at: null }, 12345);
  assert.deepEqual(sessionLockState(s, 3), { closable: true, warningVisible: false });
});

// invalidateExport is the other half of the export gate: the rule that a
// newly recorded trial must clear exported_at, so a session that gained
// data since its last export can no longer be closed. Without this, the
// gate is cosmetic -- see the doc comment on invalidateExport in app.js.

test('invalidateExport clears exported_at without mutating the session it was given', () => {
  const exported = markExported({ id: 's1', patient_id: 'p1', timestamp: 0, exported_at: null }, 500);
  const invalidated = invalidateExport(exported);
  assert.equal(invalidated.exported_at, null);
  assert.equal(exported.exported_at, 500, 'invalidateExport must return a new record, not mutate the one it was given');
});

test('invalidateExport rejects a null or undefined session rather than silently spreading it', () => {
  // Fix round 1: `{...null}` is `{}`, which drops `id` and turns a
  // programming error into a key-less IndexedDB put() that fails with a
  // misleading error far from its actual cause. This must fail loudly here.
  assert.throws(() => invalidateExport(null));
  assert.throws(() => invalidateExport(undefined));
});

test('recording a trial after export re-locks the session end to end', () => {
  // The exact sequence persistTrial drives: export a session (closable),
  // then record one more trial (must become un-closable again).
  const fresh = { id: 's1', patient_id: 'p1', timestamp: 0, exported_at: null };
  assert.equal(canCloseSession(fresh), false);
  const exported = markExported(fresh, 100);
  assert.equal(canCloseSession(exported), true, 'sanity check: exporting must make the session closable');
  const afterNewTrial = invalidateExport(exported);
  assert.equal(canCloseSession(afterNewTrial), false, 'a session that gained data since its last export must not be closable');
  assert.deepEqual(sessionLockState(afterNewTrial, 1), { closable: false, warningVisible: true });
});

// canMarkExported is the compare-and-swap check behind marking a session
// exported (fix round 2): shareFiles() hands control to a user-paced OS
// share sheet, and a sensor-driven trial (onResult -> persistTrial) can land
// during that window regardless of what the session-bar buttons show.
// Marking exported on a stale snapshot would archive-by-claim data that
// never left the device -- this predicate is what stops that.

test('canMarkExported is true when nothing changed between export and the live check', () => {
  const exported = { sessionId: 's1', trialIds: ['t1', 't2'] };
  const live = { sessionId: 's1', trialIds: ['t1', 't2'] };
  assert.equal(canMarkExported(exported, live), true);
});

test('canMarkExported is false when a trial landed during the export window', () => {
  const exported = { sessionId: 's1', trialIds: ['t1'] };
  const live = { sessionId: 's1', trialIds: ['t1', 't2'] };
  assert.equal(canMarkExported(exported, live), false, 'a trial recorded mid-export must block marking the session exported');
});

test('canMarkExported is false when the session itself was swapped out underneath the export', () => {
  const exported = { sessionId: 's1', trialIds: ['t1'] };
  const live = { sessionId: 's2', trialIds: ['t1'] };
  assert.equal(canMarkExported(exported, live), false, 'marking a different live session exported on an old snapshot is the same bug wearing a different hat');
});

test('canMarkExported does not depend on trial id ordering', () => {
  const exported = { sessionId: 's1', trialIds: ['t2', 't1'] };
  const live = { sessionId: 's1', trialIds: ['t1', 't2'] };
  assert.equal(canMarkExported(exported, live), true);
});

test('canMarkExported is false, not throwing, on a missing snapshot', () => {
  const live = { sessionId: 's1', trialIds: ['t1'] };
  assert.equal(canMarkExported(null, live), false);
  assert.equal(canMarkExported(undefined, live), false);
  assert.equal(canMarkExported(live, null), false);
});

// retainExportHandle is fix C1 stated as a rule. The bug: onResult did an
// unconditional `exportSession = session`, but Stop -- the normal and only
// completion path -- nulls `session` synchronously, one task before the
// worker's `result` reply arrives. So exportSession was overwritten with null
// on every single trial, the persist chain's `if (capture)` guard never fired,
// and NOTHING was ever written to IndexedDB: trial count stuck at 0, "Export
// session" permanently reporting "Nothing to export yet", the raw-log export
// silently a no-op, and -- worst -- an earlier export's `exported_at` left
// standing, so Close remained enabled while a scored, unpersisted trial
// existed. That last one is a data-loss path through the gate that exists to
// prevent data loss.

const handleA = { id: 'trial-just-finished' };
const handleB = { id: 'trial-in-progress' };

test('a terminal outcome arriving after Stop nulled the session keeps the handle Stop captured', () => {
  // The exact C1 sequence: Stop captures the live handle, nulls `session`,
  // then the worker's result lands with `session === null`.
  const afterStop = retainExportHandle(null, handleA);
  assert.equal(afterStop, handleA, 'Stop must capture the handle before nulling');
  assert.equal(
    retainExportHandle(afterStop, null),
    handleA,
    'the result reply arrives with session===null and must NOT erase what Stop captured -- this is the whole of C1',
  );
});

test('a live session replaces whatever handle is held', () => {
  // A fault raised mid-capture: `session` is still live, and is the trial the
  // operator is looking at.
  assert.equal(retainExportHandle(handleA, handleB), handleB);
  assert.equal(retainExportHandle(null, handleB), handleB);
});

test('with nothing held and nothing live, there is still nothing to export', () => {
  assert.equal(retainExportHandle(null, null), null);
});

test('a fault bouncing in after Stop does not erase the handle either', () => {
  // onError also nulls `session`; its own retain call must be as safe as
  // onResult's, since a fault can arrive after Stop just as a result can.
  const held = retainExportHandle(null, handleA); // Stop
  const afterFault = retainExportHandle(held, null); // onError, session already null
  const afterBouncedResult = retainExportHandle(afterFault, null); // the idempotent finish reply
  assert.equal(afterBouncedResult, handleA);
});

// exportLockState is the full session-bar decision, busy-lock included.
// `busyCount` is a reference count three independent entry points share, and
// a single missed decrement leaves it above zero forever -- at which point
// BOTH buttons are dead and the session can never be exported. Since the
// export files are the archive of record, "export unreachable" is the most
// expensive UI state on this branch, so the busy rule is pinned here rather
// than left implicit in el().disabled plumbing.

const exportedSession = markExported({ id: 's1', patient_id: 'p1', timestamp: 0, exported_at: null }, 100);
const openSession = { id: 's1', patient_id: 'p1', timestamp: 0, exported_at: null };

test('while an entry point is mid-mutation, both session buttons are locked', () => {
  const state = exportLockState({ busyCount: 1, session: exportedSession, trialCount: 3 });
  assert.equal(state.exportDisabled, true);
  assert.equal(state.closeDisabled, true, 'Close must not be tappable while something is already mutating session state');
});

test('a leaked busy count wedges export permanently -- the failure I1 exists to prevent', () => {
  // Not a hypothetical: before I1's fix, `capture.exportJsonl()` on a handle
  // from a denied permission prompt threw synchronously, past the .finally()
  // that owns the decrement. This asserts the consequence, so the property is
  // stated somewhere a reader can find it.
  const stuck = exportLockState({ busyCount: 1, session: exportedSession, trialCount: 3 });
  assert.equal(stuck.exportDisabled, true);
  assert.equal(
    exportLockState({ busyCount: 0, session: exportedSession, trialCount: 3 }).exportDisabled,
    false,
    'and it only clears when the count comes back to zero -- there is no other recovery but a reload',
  );
});

test('a negative busy count is treated as locked, not silently normalised to idle', () => {
  // A count below zero can only mean the increment/decrement pairing is
  // broken. Erring toward locked surfaces that; erring toward idle hides it.
  assert.equal(exportLockState({ busyCount: -1, session: exportedSession, trialCount: 3 }).exportDisabled, true);
});

test('idle with an exported session: export offered, close allowed, no warning', () => {
  assert.deepEqual(
    exportLockState({ busyCount: 0, session: exportedSession, trialCount: 3 }),
    { exportDisabled: false, closeDisabled: false, warningVisible: false },
  );
});

test('idle with unexported trials: export offered, close refused, warning shown', () => {
  assert.deepEqual(
    exportLockState({ busyCount: 0, session: openSession, trialCount: 3 }),
    { exportDisabled: false, closeDisabled: true, warningVisible: true },
  );
});

test('a fresh session with no trials warns about nothing', () => {
  assert.deepEqual(
    exportLockState({ busyCount: 0, session: openSession, trialCount: 0 }),
    { exportDisabled: false, closeDisabled: true, warningVisible: false },
  );
});

test('the unexported-trials warning is a fact about the session, not blanked while busy', () => {
  // An export is not finished until markExported runs, so the session really
  // does still hold unexported data mid-flight. Blanking the warning would
  // also make it flicker off and back on around every trial save.
  assert.equal(exportLockState({ busyCount: 1, session: openSession, trialCount: 3 }).warningVisible, true);
});

test('exportLockState with no arguments is inert rather than throwing', () => {
  assert.deepEqual(
    exportLockState(),
    { exportDisabled: false, closeDisabled: true, warningVisible: false },
  );
});


// -- Zone classification suppression --------------------------------------
// The score and breakdown still render; the healthy/borderline/impaired
// verdict does not. Two reasons, both current (an earlier justification --
// that the control cohort scored worse than the MS cohort -- was true of the
// tree on 2026-08-28 and was superseded by commits 9abe37c and a658c3f;
// controls now score 0.155 against MS 0.475, the right way round):
//   1. HEALTHY_REF does not reproduce from its stated provenance -- "control
//      median n=4" over P2/P8/P9/P12 recomputes area_ratio 0.1390 against a
//      stated 0.0768, and no deriving script exists in any commit.
//   2. Leave-one-participant-out AUC is 0.21, below chance. A band is an
//      individual classification; group separation does not license one.

test('no zone is shown while the reference is uncalibrated', () => {
  for (const zone of ['healthy', 'borderline', 'impaired', 'unknown']) {
    const badge = zoneDisplay(zone, false);
    assert.equal(badge.className, 'zone-uncalibrated');
    assert.ok(/recalibration/i.test(badge.text), badge.text);
  }
});

test('the impaired verdict cannot reach the badge while uncalibrated', () => {
  // The specific harm: a healthy participant being told "impaired".
  const badge = zoneDisplay('impaired', false);
  assert.ok(!/impaired/i.test(badge.text), `leaked the verdict: ${badge.text}`);
  assert.ok(!/healthy|borderline/i.test(badge.text), `leaked a verdict: ${badge.text}`);
});

test('the uncalibrated badge is styled neutrally, not as a zone', () => {
  // Reusing zone-impaired's red would reintroduce the verdict visually even
  // with neutral wording.
  const badge = zoneDisplay('impaired', false);
  assert.ok(!['zone-healthy', 'zone-borderline', 'zone-impaired'].includes(badge.className));
});

test('the app ships with classification OFF by default', () => {
  // zoneDisplay's default argument is the module-level flag, so calling it
  // with one argument is what the renderer actually does.
  const badge = zoneDisplay('impaired');
  assert.equal(badge.className, 'zone-uncalibrated');
});

test('zones render normally once the reference is recalibrated', () => {
  // The suppression is a flag, not a deletion -- recalibration re-enables it.
  assert.deepEqual(zoneDisplay('healthy', true),
    { text: 'healthy range (provisional)', className: 'zone-healthy' });
  assert.deepEqual(zoneDisplay('impaired', true),
    { text: 'impaired range (provisional)', className: 'zone-impaired' });
});

test('an unrecognised zone falls back to unknown rather than throwing', () => {
  const badge = zoneDisplay('nonsense', true);
  assert.equal(badge.className, 'zone-unknown');
  assert.equal(badge.text, 'zone unknown');
});

test('every re-enabled label still carries the provisional qualifier', () => {
  for (const zone of ['healthy', 'borderline', 'impaired']) {
    assert.ok(/provisional/.test(zoneDisplay(zone, true).text), zone);
  }
});


// -- Unmeasured-parameter notice -------------------------------------------
// An unmeasured parameter sits at 0.0, and the score penalises r2n,
// phi_max_ratio, n and omega_max_n for being BELOW the healthy reference --
// so "could not be computed" contributes the LARGEST penalty that parameter
// can produce. The score reads more impaired the less was measurable. The
// notice exists to say so; without it the number is actively misleading.

test('a fully measured trial gets no notice', () => {
  assert.equal(unmeasuredNotice([]), null);
  assert.equal(unmeasuredNotice(undefined), null);
  assert.equal(unmeasuredNotice(null), null);
});

test('the no-return-swing case names the physical cause, not a capture fault', () => {
  const n = unmeasuredNotice(['r2n', 'phi_max_ratio']);
  assert.ok(/did not swing back past neutral/.test(n.text), n.text);
  // Must NOT tell the operator to retake: a limb with high tone legitimately
  // does this, and the trial is a real finding that is kept and scored.
  assert.ok(!/retake|re-record|discard|invalid/i.test(n.text), n.text);
});

test('the notice quantifies how much the score is overstated', () => {
  // Two of seven parameters unmeasured = up to 29% of the scale.
  const n = unmeasuredNotice(['r2n', 'phi_max_ratio']);
  assert.ok(/29% of the scale/.test(n.text), n.text);
  assert.ok(/overstates impairment/.test(n.text), n.text);
});

test('a single unmeasured parameter reads in the singular', () => {
  const n = unmeasuredNotice(['f']);
  assert.equal(n.count, 1);
  assert.ok(/It counts/.test(n.text), n.text);
  assert.ok(!/They count/.test(n.text), n.text);
});

test('three unmeasured parameters list correctly and scale correctly', () => {
  const n = unmeasuredNotice(['r2n', 'phi_max_ratio', 'f']);
  assert.equal(n.count, 3);
  assert.ok(/They count/.test(n.text), n.text);
  assert.ok(/43% of the scale/.test(n.text), n.text);
  assert.ok(/, .* and /.test(n.text), `should use a serial list: ${n.text}`);
});

test('parameters are named in plain language, not raw keys alone', () => {
  const n = unmeasuredNotice(['r2n']);
  assert.ok(/first-swing return/.test(n.text), n.text);
  assert.ok(/R2n/.test(n.text), 'the raw key stays, to match the breakdown table');
});

test('an unrecognised key degrades to the key itself rather than throwing', () => {
  const n = unmeasuredNotice(['some_new_param']);
  assert.ok(n.text.includes('some_new_param'), n.text);
});


// -- Excursion gate on the phone (ported from Python, 2026-08-31) ----------
// Python refuses to grade a swing outside [25, 120] deg; the phone had no such
// gate and rendered a band regardless. A0 = 418.1 deg -- a real reconstruction
// failure on P9 at 97.3% coverage -- was being graded MAS "3".

test('a normal swing produces no refusal', () => {
  assert.equal(excursionNotice(null), null);
  assert.equal(excursionNotice(undefined), null);
  assert.equal(excursionNotice(''), null);
  assert.equal(excursionNotice('   '), null);
});

test('a collapsed swing is refused and says so in measurement terms', () => {
  const n = excursionNotice('Insufficient excursion: the leg moved 9.0 deg, below the 25 deg floor.');
  assert.ok(n);
  assert.equal(n.impossible, false);
  assert.ok(/9\.0 deg/.test(n.text));
});

test('an impossible swing is marked distinctly from a collapsed one', () => {
  // They need different treatment: one is a patient/positioning issue, the
  // other means the reconstruction failed and nothing should be read at all.
  const n = excursionNotice('Impossible excursion: the leg moved 418.1 deg, above the 120 deg ceiling.');
  assert.ok(n);
  assert.equal(n.impossible, true);
});

test('the refusal is independent of zone suppression', () => {
  // zoneDisplay withholds the band because the REFERENCE is unreproducible.
  // The excursion refusal is about the MEASUREMENT and must survive that --
  // otherwise re-enabling zones would be the only way to see it again.
  const n = excursionNotice('Insufficient excursion: the leg moved 9.0 deg, below the 25 deg floor.');
  assert.ok(n, 'refusal must not depend on ZONE_CLASSIFICATION_CALIBRATED');
  assert.equal(zoneDisplay('impaired').className, 'zone-uncalibrated');
});


// -- Dual drift-correction conventions (2026-08-31) ------------------------
// The live screen scores with detrend=false, matching pendulastic_app.py's live
// view. The STORED record scores with detrend=true, matching pt_report_common
// -> run_pt_analysis, which the cohort reports are built from. The two disagree
// on the MAS grade for 63 of 197 real trials.

test('a result carries both conventions through the outcome reducer', () => {
  const { action } = nextOutcome(false, {
    type: 'result', params: { f: 1 }, ptScore: { score: 0.4 },
    paramsDetrended: { f: 2 }, ptScoreDetrended: { score: 0.3 },
  });
  assert.deepEqual(action.paramsDetrended, { f: 2 });
  assert.deepEqual(action.ptScoreDetrended, { score: 0.3 });
});

test('a result without the analysis pair omits the keys rather than adding undefined', () => {
  const { action } = nextOutcome(false, { type: 'result', params: { f: 1 } });
  assert.ok(!('paramsDetrended' in action));
  assert.ok(!('ptScoreDetrended' in action));
});

// ---- capture view lifecycle (task 7) -------------------------------------
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
  // onEnter() itself redraws (see the next test), so every count here is
  // "one for the enter, plus one per resize that was allowed through". The
  // plan's original counts omitted the enter's own redraw and contradicted
  // the next test; the property under test is unchanged -- see Ruling I.
  view.onEnter();
  assert.equal(redraws, 1, 'onEnter redraws');
  view.handleResize();
  assert.equal(redraws, 2, 'a resize while active redraws');
  view.onLeave();
  view.handleResize();
  assert.equal(redraws, 2, 'a resize while the view is inactive must not redraw');
  view.onEnter();
  assert.equal(redraws, 3, 're-entering redraws again');
  view.handleResize();
  assert.equal(redraws, 4, 'the resize redraw is live again after re-entry');
});

test('entering redraws once so a returning view is not blank', () => {
  let redraws = 0;
  const view = createCaptureView({
    el: fakeEl({}), isCapturing: () => false, redraw: () => { redraws += 1; },
  });
  view.onEnter();
  assert.equal(redraws, 1);
});

// Ruling B, pinned as a test rather than left as a comment. app.js nulls
// `session` BEFORE `await startCapture(...)` (deliberately -- see the comment
// there), so `session !== null` reads FALSE for the whole iOS permission
// prompt window while a capture is in fact being started. Deriving from the
// Stop button instead brackets that window, because #stop is un-hidden
// synchronously before the await and re-hidden by resetToIdle and Stop.
test('the capture-in-flight window is bracketed by #stop, not by a session handle', () => {
  const stop = { hidden: true };
  let session = null;
  const byStop = createCaptureView({
    el: fakeEl({ stop }), isCapturing: () => !stop.hidden, redraw: () => {},
  });
  const bySession = createCaptureView({
    el: fakeEl({ stop }), isCapturing: () => session !== null, redraw: () => {},
  });

  // The Start handler, up to and including the line before the await.
  stop.hidden = false;
  session = null;

  assert.notEqual(byStop.onLeave(), true, '#stop derivation must refuse here');
  assert.equal(bySession.onLeave(), true,
    'the session derivation permits leaving mid-start -- this is the orphan bug');
});
