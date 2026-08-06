# Nightly Code Hygiene Routine — Design Spec

**Status:** Approved (brainstorming complete, pending user review of this document)
**Date:** 2026-08-06

## 1. Problem

Pendulastic is a sprawling, actively-developed research + app repo: 14 git
worktrees (some certainly merged/stale), 74+ top-level Python scripts with no
lint/dead-code tooling configured, a large amount of untracked root-level
clutter (logs, one-off outputs, a 31MB zip, a `_deprecated/` folder), and a
growing pile of status docs (`README.md`, `DEPLOYMENT_PLAN.md`,
`GOAL_ACHIEVEMENT_ASSESSMENT.md`, etc.) that can silently drift from what the
code actually does. None of this gets cleaned up because doing it by hand
competes with feature work.

The user wants this handled automatically, overnight, without spending
attention on it — but their own research (summarized in the prompt that
started this session) flags the real failure mode: unattended agents that
*edit* code convert money into technical debt via subtle regressions. The
routine must therefore separate "find and propose" from "execute," and never
blur the two.

## 2. Goals / Non-Goals

**Goals:**
- Every weeknight, produce a single categorized, actionable report of hygiene
  issues in the Pendulastic repo: stale worktrees/branches, untracked-file
  cruft, dead code, and doc-vs-code drift.
- Zero risk to the user's actual data or code from the nightly run itself —
  it only ever writes one new file.
- Let the user clear a backlog of proposed cleanups in seconds the next
  morning by replying "approve items N, M, ..." in a normal chat turn.

**Non-Goals:**
- The nightly run does not fix, delete, or edit anything itself.
- Not a general CI/lint gate — this is a periodic sweep, not a pre-commit
  check.
- Not scoped to the `mobile`/`web`/app code beyond doc-drift comparison; deep
  static analysis targets the Python codebase (where the dead-code and script
  sprawl problem actually lives).

## 3. Trigger & Safety Envelope

- Scheduled via `/schedule` (durable cloud cron — survives the laptop being
  closed), weeknights only, off-minute time: `17 1 * * 1-5` (~1:17am
  Mon–Fri).
- The nightly agent operates under a hard, explicit constraint stated in its
  own prompt: **no `Edit`/`Write` outside `docs/reports/**`, no `git` command
  besides `add`/`commit` scoped to that one new report file.** Every other
  finding becomes a line item in the report, never an executed action.
- Exception: the agent may `pip install vulture` into the repo's existing
  `.venv` to run Phase 2's static analysis. This changes local tooling state,
  not the repo — it is not persisted to `requirements.txt` unless the user
  later approves that separately.
- If any phase fails (e.g. `vulture` install fails, no network at 1am), the
  agent still writes the report with the failed phase clearly marked
  incomplete rather than skipping the whole run silently.

## 4. The Four Phases

Single agent, one context, phases run in sequence (not fanned out — see
§8 Alternatives Considered for why).

### Phase 1 — Cruft & Worktree Deep-Dive

For each of the repo's git worktrees:
- `git log main..<branch> --oneline` — empty means fully merged →
  `[Safe to Delete]` (worktree + branch).
- Non-empty (real unmerged work) + no commits in the last 14 days → stale but
  not merged → `[Needs Review]`.
- Non-empty + recent commits → active, skip entirely (not listed).

Same merged/stale logic applies to any local branch without a worktree.

For untracked root-level files/dirs, classify by pattern:
- Regenerable junk (`*_out.txt`, `*_err.txt`, `app_pid.txt`, `__pycache__/`,
  `.pytest_cache/`, other clearly-regenerable logs) → `[Safe to Delete]`.
- `STCFormer-main.zip` (31MB, checked into history) and `_deprecated/`
  contents → `[Needs Review]` — worth a human look before purging vendored
  or historical code.
- **Hard rule, not a judgment call:** anything under `data/`, `Recordings/`,
  `OptiTrack_*`, `training_data/`, or `models/` is never proposed for
  deletion. At most it gets flagged `[Gitignore Candidate]` if it should stop
  showing up as untracked noise. This exists because those directories hold
  irreplaceable sensor/video recordings, not disposable build output — the
  cost of a false positive there is unacceptable regardless of how confident
  the agent is.

### Phase 2 — Static Dead-Code Analysis

Runs `vulture` (Python's standard unused-code detector — same class of tool
gstack's `/health` skill wraps per-language) across the repo's Python files,
plus a manual cross-check against `_deprecated/` for scripts that duplicate
or have been superseded by current ones.

All findings default to `[Needs Review]`, never `[Safe to Delete]` — deleting
code is inherently riskier than deleting a log file, even at high detector
confidence. `vulture`'s own confidence score (0–100%) is included per finding
so the user can prioritize.

**Whitelist & false-positive handling.** The root-level scripts are mostly
exploratory research/one-off analysis code — exactly the shape that trips
vulture's known false-positive patterns (argparse `Namespace` attributes,
functions only reachable from an `if __name__ == "__main__":` block,
notebook-style top-level assignments read only by eyeballing output). Left
unchecked this would flood the morning report with noise on the first run,
which trains the user to stop reading it. Mitigation, in order:

1. Before running `vulture`, check for `.vulture_whitelist.py` at repo root
   (vulture's standard whitelist mechanism — a file of dummy references like
   `_.some_name` that suppresses specific known-fine findings). If present,
   pass it: `vulture . .vulture_whitelist.py`.
2. If absent, run with `--min-confidence 80` as the default threshold rather
   than vulture's default of 60 — this alone removes most of the argparse/
   dynamic-dispatch noise. Findings between 60–79% confidence are still
   collected but placed in a separate "low-confidence, likely noise"
   subsection at the bottom of the report rather than mixed into the main
   numbered checklist, so they never accidentally get swept up in an
   "approve items 1–10" reply.
3. On the **first run only** (no `.vulture_whitelist.py` exists yet), the
   report's Phase 2 section opens with a single `[Needs Review]` item
   proposing the user generate one: `vulture . --make-whitelist >
   .vulture_whitelist.py`, reviewed and committed by hand. The nightly agent
   never creates this file itself — it lives outside `docs/reports/**`, so
   creating it is a normal approved action like everything else, not an
   exception to the read-only rule.

### Phase 3 — Spec-to-Code Drift Audit

Cross-references `README.md`, `DEPLOYMENT_PLAN.md`,
`GOAL_ACHIEVEMENT_ASSESSMENT.md`, `ALGORITHM_LAUNCH_CONFIRMATION.md`, and
`IMPACT_MEASUREMENT_REPORT.md` against what's actually implemented (entry
points referenced, module/script names, described capabilities vs. what
exists on disk). Findings tagged `[Doc Drift Fix]` with the specific claim
and the specific contradicting/missing code.

### Phase 4 — Manifest Compilation

Synthesizes all findings from Phases 1–3 into
`docs/reports/YYYY-MM-DD-hygiene.md`: a single numbered checklist. Each item
has:
- Its safety category (`[Safe to Delete]` / `[Gitignore Candidate]` /
  `[Needs Review]` / `[Doc Drift Fix]`)
- A one-line description
- A literal, copy-pasteable command block (or diff) that fully executes it

Item numbers are stable within a report so "approve items 1, 3, 4" is
unambiguous. The agent commits only this new file (`git add
docs/reports/YYYY-MM-DD-hygiene.md && git commit`).

## 5. Approval & Apply Flow

Execution never happens automatically. Whenever the user next opens a live
session and says something like "approve items 1, 3, 4":

1. Claude reads the most recent (or explicitly referenced) report under
   `docs/reports/`.
2. Executes only the numbered commands the user approved — nothing else in
   the report, even adjacent `[Safe to Delete]` items.
3. Commits the result with a message referencing which report and which item
   numbers were applied (e.g. `chore: apply hygiene report 2026-08-10 items
   1,3,4`).

This rides entirely on a normal chat turn — no separate scheduled component,
no auto-execution. To make this convention discoverable by *any* future
session (not just one with this conversation's context), a new root
`CLAUDE.md` is added to Pendulastic with a short section explaining the
report format and the "approve items N" convention.

## 6. Notification

On completion, a push notification fires with a one-line summary (counts per
category) and a pointer to the report path, so the user sees it's ready
without having to open the repo first.

## 7. File/Directory Changes

- New: `docs/reports/` (created on first run if absent)
- New: root `CLAUDE.md` documenting the manifest format and approval
  convention (written once, as part of implementation — not regenerated
  nightly)
- New: one `/schedule` cron job

## 8. Alternatives Considered

- **Multi-agent fan-out (3 parallel specialists + synthesis):** would likely
  surface more findings on a repo this size, but costs more tokens/run and
  adds a synthesis-agent failure mode (merge conflicts between agents'
  numbering). Rejected for v1 — single-agent phased approach chosen instead,
  with the note that if it visibly under-covers the repo after a few weeks,
  swapping the `/schedule` prompt for a Workflow call is a small, isolated
  change.
- **Composing gstack's `/health` skill directly for Phase 2:** `/health` is
  tuned for a quality-score dashboard with trend tracking, not a one-shot
  categorized manifest feeding an approval flow. Its dead-code detection
  approach (per-language tool wrapping) is reused directly (`vulture` for
  Python) without taking a dependency on `/health`'s own output format.

## 9. Testing / Validation

- First run is manually triggered (not waiting for the next scheduled
  weeknight) to confirm: report format is correct, safety categorization
  matches the hard rules in §4 (especially the data-directory exclusion),
  commit succeeds, notification fires.
- Manually verify the approval flow once by approving a small, low-stakes
  `[Safe to Delete]` item (e.g. a stale `*_out.txt` file) end-to-end.
- On that first run, confirm Phase 2 correctly detects the missing
  `.vulture_whitelist.py` and proposes generating it rather than silently
  dumping unfiltered findings; after the whitelist is created and committed,
  run a second manual pass to confirm the noise drops and low-confidence
  findings land in the separate subsection, not the main checklist.
