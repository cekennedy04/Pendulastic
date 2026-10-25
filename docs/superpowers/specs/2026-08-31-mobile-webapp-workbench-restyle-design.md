# Mobile webapp: workbench restyle + participant, side, trial history, MAS entry

Date: 2026-08-31
Status: awaiting review
Scope: `webapp/`

## Problem

`webapp/` is a one-screen capture app. `pendulastic_app.py` is a five-panel
desktop workbench. They are the same product and do not look like it, and the
phone is missing capabilities the desktop has had for months.

Concretely:

| desktop | mobile today |
| --- | --- |
| landing screen: brand mark, wordmark, tagline, five action tiles | drops straight into capture |
| participant metadata | `FIXED_PATIENT_ID = 'fixed-test-participant'`, hardcoded |
| left/right leg | `TRIAL_SIDE = null`; the comment beside it already says "U8 replaces this with a real side selector" |
| `MasEntryPanel`, 11 fields | none |
| session/trial review | only the most recent trial is ever on screen |

Visually the two share a family but not a system: `workbench_style.py` is a
slate/blue design (`#F4F6F9` ground, `#2563EB` accent, layered
surface/panel/border, 14px rounded elevated tiles, a circular pendulum
monogram); `app.css` is near-black on flat white with one blue button and no
brand at all.

## Goals

1. Adopt `workbench_style.PALETTE` and its card/tile/brand vocabulary on the
   phone, adapted to a one-handed arm's-length screen.
2. Replace the hardcoded participant with real participant entry.
3. Add the left/right side selector the code already anticipates.
4. Add in-session trial history.
5. Port the MAS entry form at full field parity with the desktop.
6. Export MAS both as a `mas_scores.csv` the desktop appends without changes,
   and as a block in the session manifest.

## Non-goals

- The validation dashboard half of `MasEntryPanel` (boxplot/heatmap/ROC). It
  needs matplotlib and a corpus; the phone has neither.
- Upload & Analyze, Multi-Modal Comparison, Analysis & Reports. These are
  desktop-corpus tools, not capture-time tools.
- Any change to scoring, to `mobile-imu-core`, or to what a trial means.

## Binding constraints inherited from the existing app

These are not preferences. Each is documented in the file it governs and has
already cost a defect at least once.

- **No framework, no CSS/JS build step.** `package.json` has no bundler; the
  only build is wasm. (task-6 binding constraint)
- **The banner is unconditional.** `#banner` is sticky at `z-index: 200`, has
  no dismiss control, and must be visible on every view. The install gate is
  explicitly required not to cover it. (spec §8)
- **`hidden` is fragile here.** `app.css` carries a long comment on
  author-origin `display` silently defeating the `hidden` attribute; it has
  caused a double-capture bug. See "View toggling" below for how this design
  avoids re-entering that trap rather than restating the workaround.
- **The export gate is the durability design.** IndexedDB is a volatile cache;
  a session cannot close until its data has left the device, and any new
  unexported data re-arms the lock.
- **Semantic state colors are safety-critical.** See "Colors that do not
  change".

## Design

### Views

```
index.html
  #banner            unchanged: sticky, z-index 200, present on every view
  #install-gate      unchanged: fixed overlay, still below the banner
  <main>
    section.view#view-home      brand mark, wordmark, tiles
    section.view#view-capture   guide, gates, start/stop, waveform, score, result
    section.view#view-trials    session trial list -> tap for detail
    section.view#view-mas       11-field MAS form
    section.view#view-session   participant, side, export, close
```

Home carries one hero tile (Record Trial, showing the active participant and
side) and three secondary tiles (Trials with a count, MAS Entry, Session).
This mirrors `ModeSelectView`'s one-primary/four-secondary arrangement.

Every non-home view opens with a `<- Back` header, matching
`UploadMetaView` and `MasEntryPanel`.

### View toggling

Views use an explicit class, **not** the `hidden` attribute:

```css
.view { display: none; }
.view.active { display: flex; flex-direction: column; flex: 1; }
```

`app.css` already documents `hidden` being defeated by author-origin
`display` rules, and the fix there was to restate `[hidden]` at author origin
with sufficient specificity. That fix works but is a standing obligation:
every future author rule that sets `display` on a toggled element has to
remember it. Views are new code, so they avoid the class of bug by
construction instead of inheriting the obligation. Existing intra-view
toggles (`#start`, `#stop`, `#result`, `#waveform-wrap`, `#pt-score`,
`#export-actions`, `#send-to-laptop`) keep `hidden` and keep their existing
author-origin rule unchanged.

### Router lifecycle (addresses review point 1)

`src/router.js` exposes pure transition logic plus explicit lifecycle hooks.
A view registers `onEnter(params)` and `onLeave()`; `navigate(name)` runs the
outgoing view's `onLeave` to completion before the incoming view's `onEnter`.

This is required, not decorative. `capture.js` holds four live resources
while a trial runs:

| resource | site |
| --- | --- |
| `devicemotion` listener | `capture.js:125` |
| `setInterval(flush, BATCH_MS)` | `capture.js:126` |
| screen wake lock | `capture.js:78-79` |
| `window` `resize` -> `drawWaveform` | `app.js:979` |

Leaving `view-capture` without disarming these produces, in order of
severity: a capture that keeps consuming `devicemotion` and flushing batches
with no UI attached; a wake lock held indefinitely on a screen the clinician
thinks is idle; and a `resize` handler that calls `drawWaveform` against a
canvas inside a `display: none` subtree, where `getBoundingClientRect()`
returns zeros and the canvas is resized to 0x0 -- so returning to the capture
view shows a blank plot with no error anywhere.

Rules:

- **A recording in progress blocks navigation.** `view-capture`'s `onLeave`
  returns false while a capture is live; the router refuses the transition and
  the view shows why. Navigating away mid-trial cannot silently discard a
  capture, and cannot leave one running headless either.
- **`onLeave` on an idle capture view** releases the wake lock, detaches the
  `resize` handler, and clears any pending redraw.
- **`onEnter` on the capture view** re-attaches the `resize` handler and
  redraws the last trajectory if one exists.
- **The install gate is orthogonal.** It stays a fixed overlay and is not a
  view; it can be up over any view and must keep the banner visible.

### Data model

`DB_VERSION` 1 -> 2.

`db.js`'s header warns that the existing `createIndex` calls are nested
inside `objectStoreNames.contains()` branches that never re-run for a user
who already has the store, and prescribes branching on `e.oldVersion`
instead. The migration does exactly that.

New and changed stores:

- **`patients`** (exists) -- already keyed by UUID with free-text
  `clinic_patient_id`. No shape change. `FIXED_PATIENT_ID` /
  `FIXED_PATIENT_LABEL` are removed from `app.js` and a real participant is
  entered in `view-session`.
- **`settings`** (new) -- `{key, value}`, `keyPath: 'key'`. Holds the active
  participant id, the last-used side, and MAS form drafts.
- **`mas`** (new) -- `keyPath: 'id'` (UUID). Fields are exactly
  `mas_validation.DEFAULT_MAS_FIELDS` plus `id`, `patient_id`, `updated_at`.
  Two indexes:

  ```js
  m.createIndex('by_patient', 'patient_id');
  m.createIndex('by_identity',
    ['patient_id', 'leg', 'condition', 'assessed_date'], { unique: true });
  ```

  The logical identity is the tuple (participant, leg, condition,
  assessed_date), and it is enforced **by the database engine**, not by the
  view. IndexedDB indexes accept an array `keyPath` and honour `unique: true`
  over it, so a compound unique index expresses this natively. (An earlier
  draft of this spec claimed IndexedDB could not express a composite unique
  constraint over non-key paths and fell back to a view-layer check. That was
  wrong; a duplicate must be rejected regardless of which view calls the
  store.)

  `assessed_date` is deliberately part of the identity: correcting today's
  entry updates it, but re-assessing the same leg on a later date creates a
  new record. `append_mas_score` only ever appends and has no notion of
  superseding a previous row, so collapsing two assessment dates into one
  would make the phone lose a longitudinal observation the desktop keeps.

  **`id` stays a UUID and is never derived from the tuple.** A deterministic
  synthetic key -- `${participant}_${leg}_${condition}_${assessed_date}` --
  was considered and rejected for two reasons. It is ambiguous under
  delimiter collision, because `participant` and `condition` are free text:
  participant `P_1` + leg `left` and participant `P` + leg `1_left` produce
  the same string. More seriously, the tuple's components are **mutable** --
  correcting a typo in `condition` changes the derived key, so `put()` writes
  a *second* record under the new key and strands the original, which is
  precisely the duplicate the scheme exists to prevent, now unreachable by
  the view that caused it. A stable UUID plus a unique index gets
  engine-level enforcement while keeping an edit an update.

  A rejected write surfaces as a `ConstraintError` on the transaction, which
  the view reports as "an assessment already exists for this leg on this
  date -- open it?" rather than as a failure.
- **`trials`** (exists) -- no shape change. `side` is already a field and
  simply stops being `null`.

### Migration and the legacy participant (addresses review point 2)

The review asked that no `trials` or `mas` row ever exist without a valid
foreign-key anchor in `patients`. That invariant is adopted. The mechanism is
narrower than "retroactively map legacy trials", for two reasons.

First, an accurate statement of the risk. Deleting the `FIXED_PATIENT_ID`
*constant* from `app.js` does not delete the *record*: nothing in `db.js` or
`app.js` ever removes a patients row, so an existing installed user keeps
`{id: 'fixed-test-participant', clinic_patient_id: 'TEST-PARTICIPANT'}` in
IndexedDB, and their sessions keep pointing at it. The trials are not
orphaned. The real failure is adjacent and quieter: a participant-scoped UI
that only knows about participants the user typed will never surface that
record, so those sessions become unreachable and unexportable through the new
UI -- data loss by omission rather than by dangling key.

Second, the remedy. A bulk read-and-rewrite of `trials` inside
`onupgradeneeded` runs over the only on-device copy of clinical data inside a
versionchange transaction that can abort part-way. That is a poor trade
against a problem a one-row upsert solves.

So, in the `e.oldVersion < 2` branch:

1. Create `settings` and `mas`.
2. Scan `sessions` for distinct `patient_id` values. For any that has no
   `patients` row, insert one with `clinic_patient_id` set to
   `"UNASSIGNED-<short id>"` and `legacy: true`.
3. If a `fixed-test-participant` row exists, leave its data untouched and set
   `legacy: true` on it so the UI can label it.

Trial records themselves are never rewritten.

The UI consequences: `view-session`'s participant picker lists legacy
participants alongside real ones, marked "legacy", and they export normally.
On first launch after upgrade, the active participant is the legacy one if
exactly one exists, so a clinician mid-study is not silently switched to a
blank participant.

Post-migration invariant, asserted in tests: every `patient_id` referenced by
a `sessions` row resolves to a `patients` row.

### MAS form drafts (addresses review point 3)

The 11-field form is entered mid-examination. If iOS evicts or terminates the
standalone app while it is part-filled, retyping it means re-examining the
patient -- the input is a clinical observation, not a re-derivable value.

The form persists to a draft on every `input`/`change` event, debounced, and
`view-mas`'s `onEnter` hydrates from that draft. Saving the record clears it.

The draft is stored in the IndexedDB **`settings`** store under
`mas-draft:<patient_id>:<leg>`, **not** `sessionStorage`. The review proposed
"`sessionStorage` or local settings"; `sessionStorage` is scoped to the
browsing session and is cleared when the standalone app is terminated, which
is exactly the eviction-then-relaunch case the requirement exists to survive.
It would leave the stated failure unfixed. `localStorage` would survive
termination but is synchronous and outside the 7-day-eviction reasoning the
rest of the app's durability design is built around; `settings` keeps one
storage story.

A draft is a convenience, never a record. It is not exported, and it does not
arm the export lock.

### Export

Two additions.

**1. `pendulastic-<pid>-<stamp>-mas.csv`**, alongside the per-trial `.jsonl`
files. Columns are exactly `mas_validation.DEFAULT_MAS_FIELDS`, in that
order:

```
participant, leg, condition, diagnosis, mas_grade, assessed_by,
assessed_date, stronger_leg, notes, mas_flexion, mas_extension
```

so `append_mas_score()` ingests it with no desktop-side change. `notes` is
free text, so the writer is RFC4180: fields containing `,`, `"`, CR or LF are
double-quoted with `"` doubled inside.

**2. Manifest `pendulastic/session-export/v1` -> `v2`**, adding a `mas`
block carrying the same rows. Bumped rather than silently widened: a v1
consumer must not be handed a different shape under an unchanged version
string.

The CSV is the source of truth for the desktop pipeline; the manifest block
makes the exported directory self-describing. The duplication is deliberate
and one-directional -- both are generated from the same `mas` records in one
pass, so they cannot disagree.

MAS edits count as unexported session data and re-arm the close lock exactly
as recording a trial does.

### CSV null and sentinel representation (addresses review point 4)

The review asked that pending/unassessed fields render as empty strings
rather than a `-1` sentinel, to avoid desktop ingestion changes. Verified
against `mas_validation.py`, the opposite is true for one column and true for
the rest, so the rule is per-field.

`append_mas_score()` at `mas_validation.py:260-263`:

```python
grade = row.get("mas_grade", "")
if not (_valid_grade(grade) or grade == PENDING_MAS_GRADE):
    raise ValueError(...)
```

`_valid_grade("")` is `False` and `"" != "-1"`, so an empty `mas_grade` is
rejected outright and no write is attempted. `-1` is not a stray sentinel: it
is `PENDING_MAS_GRADE`, deliberately kept out of `MAS_RANK` so that
`pair_pt_and_mas` skips such a row from every statistic with a
`_skip_reason` (`mas_validation.py:63-70`). Emitting `""` there would make
every pending row fail ingestion.

Conversely `mas_flexion`, `mas_extension` and `stronger_leg` validate as
"blank, or a member of the enum", so `-1` in any of them raises too.

The binding rule:

| column | permitted | never |
| --- | --- | --- |
| `mas_grade` | `0` `1` `1+` `2` `3` `4`, or `-1` | `""` |
| `mas_flexion`, `mas_extension` | `""`, or a grade | `-1` |
| `stronger_leg` | `""`, `left`, `right`, `equal` | `-1` |
| `participant`, `leg`, `condition`, `diagnosis`, `assessed_by`, `notes` | free text; `""` fine | -- |

No column ever renders a JavaScript `null`/`undefined` as the strings
`"null"`/`"undefined"`. `buildMasCsv` coerces absent values to `""` and then
applies the table above.

`assessed_date` defaults to today in ISO `YYYY-MM-DD`, matching
`MasEntryPanel`'s `datetime.date.today().isoformat()`.

**The grades are strings, and `1+` is one of them.** `MAS_ORDER` is
`["0", "1", "1+", "2", "3", "4"]` (`pendulastic_pt_score.py:531`). The third
grade is the literal two-character string `1+`, never `1.5` and never a
number: `_valid_grade` is a dictionary membership test against exactly these
strings, so any numeric coercion raises on ingestion. `mas-store.js`
transcribes the list verbatim, and a test asserts it character-for-character
against the Python source's ordering.

### Pending grades are permitted, but never accidental

A record may carry `mas_grade = "-1"`. This preserves the workflow
`PENDING_MAS_GRADE` was defined for -- flexion and extension assessed at the
bedside, the overall grade filled in afterwards -- which is precisely the
bedside case this app exists to serve. Forbidding it would leave a clinician
who has examined both directions unable to save anything at all.

The risk it carries is a silent hole, not a corrupt statistic: `-1` is kept
out of `MAS_RANK` specifically so `_valid_grade` rejects it and
`pair_pt_and_mas` skips the row from every downstream statistic and
threshold-fit with a `_skip_reason` (`mas_validation.py:63-70`). It cannot
reach an ordinal computation. So the mitigation targets visibility, not
validity:

- `-1` is selectable only as an explicit **"not yet assessed"** option in the
  grade picker. It is never the default and never what an untouched field
  yields; the picker opens with no selection and the form refuses to save
  until the clinician chooses either a grade or that option.
- Records with `mas_grade = "-1"` are badged **pending** wherever MAS is
  listed (`view-mas`, `view-trials`, `view-session`).
- `view-session` shows a pending count beside the export controls, so a
  session is never exported with unresolved assessments the clinician has
  forgotten about. This is a notice, not a block -- exporting a pending row
  is legitimate and the desktop accepts it.

Pending records export normally, as `-1`, in both the CSV and the manifest
block.

### Styling

`app.css`'s `:root` adopts `workbench_style.PALETTE`:

| token | value | from |
| --- | --- | --- |
| `--bg` | `#F4F6F9` | `PALETTE["BG"]` |
| `--surface` | `#FFFFFF` | `PALETTE["SURFACE"]` |
| `--panel` | `#F5F8FC` | `PALETTE["PANEL"]` |
| `--accent` | `#2563EB` | `PALETTE["BTN_ACT"]` |
| `--accent-soft` | `#DCEAFE` | `PALETTE["BTN"]` |
| `--fg` | `#0F172A` | `PALETTE["FG"]` |
| `--fg2` | `#475569` | `PALETTE["FG2"]` |
| `--fg3` | `#64748B` | `PALETTE["FG3"]` |
| `--border` | `#CBD5E1` | `PALETTE["BORDER"]` |
| `--mono` | `Consolas, ui-monospace, monospace` | `PALETTE["MONO"]` |

`--mono` goes on the numeric readouts that already use
`font-variant-numeric: tabular-nums` (gates, PT score, result table).

**Tiles** port `workbench_style.Tile`: 14px radius, `--surface` fill,
`--border` at rest, `--accent` at 2px when pressed, icon + title + subtitle.
`.tile--primary` is the filled-accent hero. Icons are inline SVG reusing
`Tile._draw_icon`'s vocabulary (record, upload, compare, chart, checklist).
The desktop's hover states become `:active`/`:focus-visible`, since a phone
has no hover.

**Cards** port `card_frame`: `--panel` fill, 1px `--border`, an uppercase
`--fg3` section label. `#gates`, `#pt-score` and the trial-history rows all
become cards, replacing the ad-hoc `#f4f5f7` literal currently repeated in
five places.

**Brand mark** ports `brand_mark()` to inline SVG at identical proportions:
circle outline, pivot dot, swing line, weighted bob, all `--accent`.

**Buttons** port `primary_button`/`secondary_button` as `.btn--primary`
(filled `--accent`, white) and `.btn--secondary` (`--accent-soft` fill,
`--fg` text). Existing sizing is kept: `#start`/`#stop` stay at their current
`clamp(20px, 6vw, 26px)`, 18px padding. Tap targets stay >= 44px.

**Fonts** keep the existing `-apple-system, BlinkMacSystemFont, "Segoe UI",
Roboto, sans-serif` stack. Segoe UI is the native Windows UI face, and the
design intent is "the platform's own UI typeface"; that stack already
resolves to SF on iOS. Hard-coding Segoe would land on an arbitrary fallback
on every device the app actually runs on.

### Colors that do not change

The `#guide` state colors -- `moving` `#6b7280`, `holding` `#b45309`,
`ready` `#0f7a37`, `fired` `#1d4ed8`, `unscorable` `#4b5563`, `fault`
`#9f1239` -- and the banner's `#7a0d0d` keep their exact current values.

`app.css` documents these as chosen for reading at arm's length under outdoor
glare by someone whose hands are occupied, each backed by an independent
glyph and text label so color is never the sole cue; `ready` is additionally
the only pulsing state. The banner color is mandated by spec §8. Restyling
these to fit a palette would trade a documented safety property for visual
consistency. Only the chrome around them changes.

The `--pt-*-bg` zone colors are likewise unchanged, and zone classification
stays suppressed (`ZONE_CLASSIFICATION_CALIBRATED = false`).

### Module layout

`app.js` is 1183 lines and gains four views' worth of wiring. It is split,
keeping every currently-exported pure function exported from the same module
so `tests/app.test.js` continues to import from `../src/app.js` unchanged.

| file | holds |
| --- | --- |
| `src/router.js` | `resolveView`, `canLeave`, transition reducer -- pure |
| `src/mas-store.js` | `MAS_ORDER`, `MAS_FIELDS`, `makeMasRecord`, `validateMasForm` |
| `src/mas-csv.js` | `buildMasCsv`, `csvField` -- pure |
| `src/views/capture.js` | capture wiring + lifecycle hooks |
| `src/views/trials.js` | history list and detail |
| `src/views/mas.js` | form, draft hydrate/persist |
| `src/views/session.js` | participant, side, export, close |

`scripts/shell-list.mjs` walks `src/` for `.js`/`.css`, so the service-worker
shell, `BUILD_ID`, and `dist/` pick all of these up with no list to maintain.
`tests/sw-shell.test.js` and `tests/dist-build.test.js` verify that.

## Testing

Pure functions under `node --test`, no DOM, following the existing hand-rolled
fakes in `tests/db.test.js`.

- **`mas-csv`** -- column order and count identical to `DEFAULT_MAS_FIELDS`;
  RFC4180 quoting for comma, quote, CR, LF and combinations in `notes`; the
  per-field sentinel table above enforced column by column; `null`/`undefined`
  render as empty, never `"null"`.
- **Round-trip** -- generated rows re-validated against a JS transcription of
  `append_mas_score`'s three checks, so a change to either side that breaks
  ingestion fails here rather than on the desktop.
- **`mas-store`** -- `MAS_ORDER` matches `pendulastic_pt_score.py:531`
  character for character, including `1+` and the ordering; `validateMasForm`
  accepts every grade plus `-1`, rejects `""` for `mas_grade`, rejects `-1`
  for the optional grades, enforces `STRONGER_LEG_OPTIONS`, and rejects an
  untouched form (no grade selected) so `-1` can never arrive by default.
- **`db`** -- extend the existing fake so the v2 migration is driven through
  `oldVersion` 0 (fresh) and 1 (upgrade); assert `settings` and `mas` are
  created in both, that `by_identity` is created with `unique: true` over the
  four-element array keyPath, that v1 stores are not recreated on upgrade,
  and the post-migration anchor invariant.
- **`mas` identity** -- a second save of the same (patient, leg, condition,
  date) updates in place rather than inserting; a save differing only in
  `assessed_date` inserts a second record; editing `condition` on an existing
  record updates that record and does not strand the original (the failure
  the synthetic-key scheme would have introduced).
- **`router`** -- transitions, `onLeave` ordering before `onEnter`, and that a
  live capture refuses the transition.
- **`export`** -- manifest is `v2`, carries the `mas` block, and the CSV and
  the block agree row for row.
- **`sw-shell` / `dist-build`** -- existing tests, confirming the new modules
  are shipped.

## Risks

- **Split of `app.js`.** The riskiest change here, because the file's session
  bookkeeping (`sessionBusyCount`, `sessionReadyPromise`, the export lock) is
  subtle and heavily commented. Mitigation: the split is mechanical, the pure
  functions keep their current module and their current tests, and the session
  bookkeeping moves as one unit rather than being distributed across views.
- **A schema bump reaches installed phones only via the service worker.** A
  device on the old shell keeps `DB_VERSION` 1 until it takes the update.
  `updateViaCache: 'none'` and the content-derived `BUILD_ID` already exist to
  make that reliable, and the migration is additive, so an old shell against a
  new DB is not a corrupting combination.
- **The MAS form is long on a phone.** Full parity was chosen deliberately:
  a missing column means re-examining a patient. Drafts plus a single-column
  layout with `--panel` cards per group are the mitigation.

## Resolved during review

- **`-1` versus `""` for `mas_grade`.** Review asked for `""`; the quoted
  `append_mas_score` check rejects it. Resolved as the per-field table above,
  with pending permitted but never accidental.
- **Composite identity enforcement.** An earlier draft claimed IndexedDB
  could not express this and used a view-layer check. It can, via a compound
  unique index; enforcement moved to the engine. The deterministic synthetic
  key proposed in review was rejected on delimiter-collision and
  mutable-component grounds, both recorded beside the `mas` store.
- **Grade set.** `1+`, not `1.5`; strings, not numbers.

## Open questions

None.
