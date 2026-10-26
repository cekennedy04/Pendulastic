# Task: persist unscorable trials instead of discarding their raw data

## The problem, in one sentence
When the Pendulastic PWA cannot score a recording (no usable pendulum swing — e.g. a
steady-state step-response / staircase, or any non-oscillating capture), it throws the
**raw IMU recording away**: the trial is never written to IndexedDB, never appears in the
trials list, and is **not** included in `Export session`. The only way to save that raw
data today is the transient per-trial **"Export raw log"** button, one file at a time,
and only before the operator taps Start again.

This violates the project's established scoring philosophy — **"measure everything, flag
out-of-range, never exclude"** — at the most basic level: an unscorable trial should be
*persisted and flagged*, not deleted. It also blocks the SSR (steady-state step-response)
sensor-accuracy validation, whose recordings are deliberately non-pendulum and therefore
always "unscorable," yet whose raw accel+gyro is exactly the data we need.

## Where the code is (branch + worktree)
- Branch: **`feat/webapp-workbench-restyle`** (this is the branch the current preview
  deploy is built from). Worktree: `C:/Users/cladi/Pendulastic/.claude/worktrees/webapp-restyle`.
- This is a **JS-only** change (`webapp/src/`); **no Rust/wasm rebuild is required** — do
  not touch `mobile-imu-core` or the scoring math. The unscorable *decision* stays exactly
  as it is; we only change what happens to the raw data afterward.

## Root cause (verified — file references are in `webapp/src/`)
1. The Rust `finish()` returns `undefined` when a trial is unscorable; `worker.js`
   (~L40–48) turns that into a posted `{type:'error', reason:'unscorable'}`.
2. `capture.js` (~L93) routes `type:'result'` → `onResult` and everything else →
   `onError`. So an unscorable trial goes to **`onError`**, never `onResult`.
3. `persistTrial(...)` (`app.js` ~L677) — the only thing that writes a trial to IndexedDB
   and its raw JSONL — is **called only from `onResult`** (`app.js` ~L1856, inside the
   `if (capture) { … exportJsonl().then(persistTrial) … }` chain).
4. `onError`'s `unscorable` branch (`app.js` ~L1895) shows "NOT SCORABLE — no usable
   swing" and calls `showExportControls()` (retaining `exportSession`), but **never
   persists**. Hence the raw recording is dropped once the operator starts the next trial
   (`exportSession = null` in the Start handler).

## What to build
Persist unscorable trials as **unscored recordings**: same raw JSONL, `params = null` /
`ptScore = null`, plus an explicit flag (e.g. `scorable: false` / `unscored: true`) on the
stored trial record. They must appear in the session, be included in `Export session`
(both the raw JSONL and a manifest entry), and render in the trials list marked "not
scorable" rather than showing zeros (zeros would misread as "scored healthy" — the code
already warns about this in `renderPtScore`).

### Scope (each must be handled, TDD — write the failing test first)
1. **`onError` unscorable branch (`app.js`)** — run the same export+persist chain
   `onResult` uses, with null params/ptScore. **Preserve the busy-counter invariant
   exactly**: `onResult` has a long comment (~L1808–1868) explaining there must be exactly
   ONE `sessionBusyCount++` and ONE decrement in a single `.finally()` on the whole
   `exportJsonl().then(persistTrial)` chain, with `refreshExportLock()` after. Mirror that
   discipline; do not introduce a second increment/decrement path. Only persist the
   `unscorable` kind — genuine faults (`kind:'fault'`) must still NOT persist.
2. **`persistTrial` (`app.js` ~L677)** — accept `params === null` / `ptScore === null`
   without crashing; write the raw JSONL and the flag. Check `makeTrialRecord` (see the
   comment at `app.js` ~L72) and make every params-derived field null-safe.
3. **Trial record schema / `makeTrialRecord`** — add the `scorable:false`/`unscored`
   flag; keep existing scored trials unchanged (flag defaults to scored).
4. **Trials list view (`webapp/src/views/trials.js`) and `renderStoredTrial`
   (`app.js` ~L1030)** — render an unscored trial as "not scorable" (no metric rows, no
   zone), never crash on null params.
5. **Export (`webapp/src/export.js`, `buildExportFiles` ~L31, manifest `params` ~L101)**
   — include unscored trials: raw JSONL always; manifest entry with `params: null` (or an
   explicit `"unscored": true`) rather than a zero-filled param block. Keep the manifest
   shape backward-compatible so downstream `bench_rescore.py` / `ssr_extract.py` (which
   read the raw JSONL) are unaffected, and so a null-params entry does not break any
   manifest consumer.
6. **Session lock/counts** — `sessionLockState` (`app.js` ~L174), `exportLockState`
   (~L213), and `storedCount` must count unscored trials as stored trials (so the
   "unexported trials" warning and Close gating behave correctly).

### Success criteria
- A non-swing / staircase recording (unscorable) is **persisted**: it shows in the trials
  list marked "not scorable," survives a page reload, and `Export session` includes its
  **raw JSONL** plus a manifest entry with null params.
- Scored trials behave exactly as before (no regression to the persist/export/lock paths
  or the busy counter).
- Genuine faults (`kind:'fault'`) still do **not** persist.
- Unit tests (Node `--test`, the project's existing harness) cover: unscorable → persisted
  with `scorable:false` + raw JSONL; scored → unchanged; fault → not persisted; export
  includes an unscored trial; trials view renders one; `sessionLockState`/`storedCount`
  count it.

### Do NOT
- Change scoring math, the excursion gate, `finish()`'s unscorable decision, or any Rust.
- Rebuild wasm (JS-only). Do not alter the release/frequency scoring fix already on this
  branch.

## Verify + deploy (preview only — NOT prod)
From `webapp/`:
```
node scripts/build-dist.mjs
npx vercel deploy --yes --project pendulastic-app dist        # PREVIEW, never --prod
```
On device: run a deliberately non-pendulum recording → confirm it says "not scorable",
appears in the trials list as unscored, survives reload, and `Export session` contains its
raw JSONL. Report the preview URL.

## Context
This came out of SSR (steady-state step-response) bench validation: the staircase profile
(`0→+A→−A→+A→0`, 3 s dwells, 6 s final auto-stop) is intentionally non-pendulum, so the app
always calls it unscorable — but the raw accel+gyro is the whole point. `ssr_extract.py`
(repo root) already consumes the raw JSONL; this change just lets the operator collect it
via the normal session export instead of the fragile one-at-a-time "Export raw log".
