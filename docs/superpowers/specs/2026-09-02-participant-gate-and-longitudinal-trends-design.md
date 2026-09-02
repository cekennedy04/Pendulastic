# Participant Gate, Longitudinal Trends, and Figure Export — Design

**Date:** 2026-09-02
**Status:** approved in brainstorming; awaiting spec review
**Applies to:** `webapp/` (the phone capture app), branch `feat/webapp-workbench-restyle`
**Predecessor:** `docs/superpowers/specs/2026-08-31-mobile-webapp-workbench-restyle-design.md`
(Tasks 1–13 of that plan are complete as of commit `d0e010e`.)

---

## 1. Context

The workbench restyle landed five views, real participant entry, in-session
trial history, MAS entry at desktop field parity, and a `mas_scores.csv`
export the desktop's `append_mas_score()` ingests unchanged.

Three follow-on requests came out of the first real device test:

- **A.** The operator should be prompted to select or add a participant
  *before* anything else, rather than landing on the home tiles.
- **B.** A place to see a participant's spasticity trend over time.
- **C.** Generate and export figures.

These are three units with different shapes. A is a routing rule over a flow
that already exists. B introduces the app's first cross-session read. C
depends on B. They are specified together here because B and C are one
feature, but **A is independently implementable and ships first.**

---

## 2. Decisions taken, with provenance

Recorded so a later reader does not have to reconstruct them.

| Decision | Choice | Notes |
| --- | --- | --- |
| What the trend plots | MAS grade, A0, **and** the PT7 composite | See §2.1 — the composite was included over a stated objection |
| Data source | Device history **plus** import of exported bundles | The phone only holds what it recorded |
| Figure output | PNG at print DPI only | No SVG, no clipboard |
| Participant prompt trigger | Only when no participant is set | Not every launch |
| Aggregation unit | Per **session** (median of its trials) | See §2.2 |

### 2.1 The PT7 composite on a time axis — objection raised and overruled

This is recorded in full because it is a clinical-safety decision, not a
preference.

The objection put to the user before any code was designed:

- **PT7 is non-monotonic in severity.** It is U-shaped; a near-rigid leg
  scores *mild*. A patient who genuinely worsens can move the line the wrong
  way. (Recorded in the project's own findings; the excursion gate shipped
  2026-08-30 does not fix the non-monotonic middle.)
- **`ZONE_CLASSIFICATION_CALIBRATED = false`** (`webapp/src/app.js:288`). The
  app deliberately suppresses healthy/borderline/impaired labels because
  `HEALTHY_REF` was calibrated on artifacts.
- **The shipped disclaimer** in `webapp/index.html` states this instrument's
  ability to tell one person's trial from another's "has been measured at
  worse than chance," and that group separation "does not license classifying
  a single trial." A trend line is a sequence of single trials.

The user was offered three alternatives that avoid the problem (MAS+A0;
MAS+A0+raw parameters; MAS only) and **chose to include the composite.** That
is their call and it is implemented as asked. The mitigations below are not
negotiable parts of that implementation:

1. The PT7 chart carries a **permanent on-chart caption** — not a tooltip,
   not a footnote — reading that the score is non-monotonic in severity and
   that a worsening leg can trend downward.
2. The chart also states that PT7 is **recomputed at read time** against the
   current `HEALTHY_REF`, so recalibrating that reference retroactively moves
   the entire historical curve.
3. No zone colouring, no band shading, no "improving/worsening" arrow, and no
   trend line fit. The series is drawn as points joined by straight segments
   and nothing more. Any of those additions would assert exactly the
   interpretation the app suppresses elsewhere.

### 2.2 Why per-session and not per-trial

Single-trial discrimination is documented as worse than chance. A per-trial
scatter would plot mostly noise while looking like signal. The median of a
session's trials is the smallest unit that is not primarily noise, and the
median (not the mean) because a single unscorable or artifact-laden trial
must not drag the point.

A session contributing fewer than two scorable trials is still plotted, but
its point is rendered hollow (see §5.4) so a thin session is visible as thin.

---

## 3. Unit A — Participant-first gate

**Independently shippable. No schema change. No dependency on B or C.**

### 3.1 Behaviour

- On startup, if no participant is set, the app opens on **`session`**
  instead of `home`, with the participant card at the top of the view.
- Once a participant is set it persists across relaunches (already true —
  `SETTING_KEYS.activePatient` in the `settings` store), so a clinician
  mid-visit is never re-asked.
- **Close Session also clears the active participant.** The next launch
  therefore re-prompts. This is the change that makes "prompted first" true
  for the second patient of the day, not just the first.
- When no participant is set, the session view shows one line: *"Select or
  add a participant to begin."*

### 3.2 Interfaces

```js
// src/views/session.js
export function initialView({ patient }) // -> 'session' | 'home'
```

Pure, so the rule is tested without a DOM.

### 3.3 Files

- Modify `webapp/src/app.js` — navigate to `initialView(...)` at startup
  rather than relying on `#view-home` carrying `.active` in the markup;
  clear `SETTING_KEYS.activePatient` in the close-session handler.
- Modify `webapp/src/views/session.js` — export `initialView`; render the
  prompt line.
- Modify `webapp/index.html` — the prompt line element.
- Test `webapp/tests/app.test.js`.

### 3.4 Acceptance criteria

1. A device with no participant opens on the session view, not home.
2. A device with a participant opens on home.
3. After Close Session, the next launch opens on the session view.
4. Recording remains impossible without a participant *and* a leg — the
   existing gate in the Start handler is unchanged and still tested.
5. The banner remains visible; the install gate still takes precedence over
   everything (it is `position: fixed; inset: 0; z-index: 100`).

---

## 4. Unit B — Longitudinal store and trends view

### 4.1 Data access: no schema change

`sessions` is already indexed `by_patient`; `trials` is indexed `by_session`.
**Trial records carry no `patient_id`** (`makeTrialRecord` in
`webapp/src/session-store.js` stores `session_id` only).

The join is therefore done in memory:

```
getAll(sessions, 'by_patient', pid)  ->  for each session: getAll(trials, 'by_session', sid)
```

This is an N+1, and that is fine: N is the number of visits for one
participant — a handful. The alternative (DB_VERSION 3 adding `patient_id`
to trials plus a `by_patient` index, backfilled) is rejected. IndexedDB
migrations are one-way — `open()` at a lower version than stored fails with
`VersionError` — and the v2 migration already spent the rollback option once.
A second one buys nothing here that an in-memory join does not.

### 4.2 The composite is not stored

`makeTrialRecord` deliberately drops the composite score: it is derived at
read time because `HEALTHY_REF` moves. The trends view therefore **recomputes
PT7 from the stored 20 parameters** through the same path the capture view
uses. No second scoring implementation is introduced. This is also the
mechanism behind the §2.1 caption about recalibration moving the curve.

### 4.3 Import

A file picker on the trends view accepts the artifacts the app already
exports: `<base>-manifest.json` and `<base>-mas.csv`.

- **Additive only.** Import never deletes or overwrites a local record.
- **Trials dedupe on `id`.** A re-import of the same bundle is a no-op.
- **MAS rows dedupe on the `by_identity` tuple** (`patient_id`, `leg`,
  `condition`, `assessed_date`). The unique index already enforces this at
  the engine level, so a duplicate raises `ConstraintError` and is counted
  and skipped rather than surfacing as a failure.
- **Participant matching is by `clinic_patient_id`**, not by UUID: a bundle
  exported from a different device carries a different `patients.id` for the
  same person. An imported bundle whose `clinic_patient_id` matches no local
  participant creates one, flagged `imported: true`.
- The importer reports a summary: *N trials added, M skipped as duplicates,
  K MAS rows added, J skipped.* Silence after an import is not acceptable —
  the operator must be able to tell an import that did nothing from one that
  worked.

Manifest `schema` is checked. `pendulastic/session-export/v2` is accepted;
`v1` is accepted for trials but carries no `mas` block; anything else is
refused with the version named.

### 4.4 The view

A sixth view, `#view-trends`, reached from a new home tile. `VIEWS` in
`webapp/src/router.js` grows to six.

Three stacked charts share one x-axis (session date) and one legend
(left leg / right leg):

| Chart | y-axis | Notes |
| --- | --- | --- |
| MAS grade | ordinal `0, 1, 1+, 2, 3, 4` | step chart; a pending `-1` is a **gap**, never a zero |
| A0 | degrees | excursion amplitude |
| PT7 composite | unitless | permanent caption per §2.1 |

Empty states are distinct and explicit: "no participant selected", "no
sessions recorded for this participant", and "no MAS assessments recorded"
are three different messages, because they have three different remedies.

### 4.5 Interfaces

```js
// src/views/trends.js
export function sessionSeries(sessions, trialsBySession)  // -> [{ sessionId, date, leg, a0, pt7, n }]
export function masSeries(masRecords)                     // -> [{ date, leg, grade, pending }]
export function median(values)                            // -> number | null
export function chartScale(values, { height })            // -> { min, max, toY }
export function importSummary(result)                     // -> string

export function createTrendsView({
  el,            // (id) => Element
  context,       // () => ({ patientId, participantLabel })
  loadHistory,   // () => Promise<{ sessions, trialsBySession, masRecords }>
  importBundle,  // (files)  => Promise<{ trialsAdded, trialsSkipped, masAdded, masSkipped }>
  exportFigure,  // (canvas, name) => Promise<void>
})                                                        // -> { onEnter() }
```

Everything except `createTrendsView` is pure and DOM-free. `createTrendsView`
takes every dependency by injection, as the other view modules do, so it stays
import-safe under `node --test` and touches the DOM only when called.

---

## 5. Unit C — Figure export

- The same canvas renderer used on screen, re-run into an **offscreen canvas
  at 3× device pixel ratio**, then `canvas.toBlob()` → the existing
  `shareFiles()` in `webapp/src/export.js`.
- One PNG per chart. Filenames share the session-export stem:
  `pendulastic-<clinic_patient_id>-<stamp>-trend-mas.png`, `-trend-a0.png`,
  `-trend-pt7.png`.
- The on-chart captions from §2.1 are **rendered into the PNG**, not drawn
  only in the DOM around it. An exported figure travels without its page; a
  caveat that lives outside the image does not survive being pasted into a
  slide.
- No SVG, no clipboard path. Both were considered and declined.

---

## 6. Quality validation strategy

The bar set by Tasks 1–13 on this branch, and applied here unchanged.

1. **Pure-function unit tests**, `node --test`, no DOM, no new dependency.
   Every function in §4.5 plus `initialView`.
2. **Mutation sweep on every new pure function.** A green suite is not
   evidence a test discriminates. Each mutant must be confirmed to have
   actually applied before its result is believed — two mutants silently
   failed to apply during Task 9 and looked exactly like uncaught ones.
3. **Test-count check before and after every append.** A test file that was
   never written reports the file's *original* count and looks like success;
   this happened in Task 11.
4. **Headless browser verification** of every interactive path, driven from a
   single `browse eval` script (the page drops to `about:blank` between
   separate CLI calls, and `eval` does not await a returned promise).
5. **Screenshots at 390×844** for each new view.
6. **Import round-trip**: export a session, import the bundle into a clean
   database, and assert the trend renders the same points. This is the
   equivalent of Task 11's `mas_validation.py` parity check and is the single
   most valuable test in Unit B.
7. **Ruling E discipline**: any change under `webapp/src/` runs
   `npm run build:shell` before commit, because `BUILD_ID` is the service
   worker's cache key and a stale one leaves installed phones on old code
   forever.

---

## 7. Deployment readiness

A unit is deployment-ready when **all** of the following hold. This list is
the gate, not a wish list.

1. Full suite green (`cd webapp && npm test`), with the total test count
   having moved by the expected amount.
2. `npm run build:dist` succeeds and every new module appears in `SHELL`.
3. Headless browser walk clean, console free of errors, against the built
   `dist/`, not the source tree.
4. **Physical-device smoke test performed by the user** on a preview
   deployment. This cannot be delegated to CI or to a headless browser: the
   paths that matter most — `devicemotion`, the wake lock, iOS terminating a
   backgrounded standalone app — do not exist off-device.
5. Production (`pendulastic-app.vercel.app`) is updated **only after** step 4
   passes, and only by CLI folder upload of `webapp/dist/`. Git integration
   cannot work: Vercel's build image has no Rust toolchain for `build:wasm`.

### 7.1 Known gaps that are not blockers but should be fixed

- **No in-app build identifier.** There is currently no way to tell which
  build a phone is running from inside the app. This already cost one full
  smoke-test pass, in which production's old UI was tested and reported as
  the new build failing. Surfacing the short `BUILD_ID` in the session view
  is a small, contained change and should land before the next device test.
- **No icons.** `manifest.json` has no `icons` and `index.html` no
  `apple-touch-icon`, so iOS Add-to-Home-Screen uses a page screenshot. With
  two builds installed side by side this is actively confusing, because both
  are named "Pendulastic".
- **`ALGORITHM_VERSION` names the wrong commit.** `build-wasm.mjs` stamps
  `HEAD` *at build time*, which is always the commit before the one
  containing the change it describes. Keying off the tree hash of
  `mobile-imu-core/src` would close it.

---

## 8. Risks

| Risk | Mitigation |
| --- | --- |
| The PT7 trend is read as a clinical trajectory | §2.1 mitigations 1–3; no fitted line, no zones, no arrows |
| A partial history reads as a complete one | The view labels its source: N sessions on this device, M imported |
| Import creates duplicate participants | Match on `clinic_patient_id`, not UUID; imported participants flagged |
| Chart code grows into a charting library | Three fixed chart types, hand-rolled, no generic abstraction until a fourth is needed |
| Recalibrating `HEALTHY_REF` silently changes history | Stated on the chart and in the exported PNG |

---

## 9. Out of scope

- Any change to how PT7 is computed. This spec renders it; it does not touch
  the scoring path.
- Cross-participant or cohort views. One participant at a time.
- Editing or deleting stored trials and assessments.
- Sync between devices. Import is manual and file-based by design.
- SVG figures and clipboard export, both explicitly declined.

---

## 10. Implementation order

1. **Unit A** — own plan, implemented immediately. Small, unblocks daily use.
2. **Unit B** — own plan, written after A lands.
3. **Unit C** — folded into B's plan as its final tasks, since it reuses B's
   renderer and has no independent value.
