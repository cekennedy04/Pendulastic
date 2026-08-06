# Nightly Hygiene Audit Runbook

Run this against the Pendulastic repo at `C:\Users\cladi\Pendulastic`.

**Hard constraints (do not violate):**
- No `Edit`/`Write` outside `docs/reports/**`.
- No `git` command except `git add docs/reports/<file> && git commit` on the
  one new report file.
- Every finding is a proposal, never an executed action.

**Steps:**

1. Ensure `vulture` is installed in the repo's venv (idempotent):
   `.venv/Scripts/python.exe -m pip install vulture`
2. Run the mechanical audit (Phases 1-2):
   `.venv/Scripts/python.exe run_hygiene_audit.py`
   This prints a markdown checklist plus a line `=== NEXT_ITEM_NUMBER: N ===`.
3. Phase 3 (spec-to-code drift, done by you, not the script): read
   `README.md`, `DEPLOYMENT_PLAN.md`, `GOAL_ACHIEVEMENT_ASSESSMENT.md`,
   `ALGORITHM_LAUNCH_CONFIRMATION.md`, `IMPACT_MEASUREMENT_REPORT.md`.
   Compare each concrete claim (entry points, module names, described
   capabilities) against what's actually on disk. For every mismatch, write
   one more numbered item continuing from N, tagged `` `[Doc Drift Fix]` ``,
   in the same "N. `[Category]` description" + fenced command-block format
   the script used for its own items.
4. Assemble the final report: the script's "MECHANICAL FINDINGS" section,
   then your Phase 3 items continuing the numbering, then (if present) the
   script's "LOW-CONFIDENCE DEAD-CODE" section verbatim at the bottom. The
   `=== MECHANICAL FINDINGS ===`, `=== LOW-CONFIDENCE DEAD-CODE ===`, and
   `=== NEXT_ITEM_NUMBER: N ===` lines are section delimiters for your own
   assembly process only - strip all of them before writing the final report
   file; none of them are report content and they must never appear in the
   file committed to `docs/reports/`.
5. Write it to `docs/reports/<YYYY-MM-DD>-hygiene.md` (today's date) using
   UTF-8 encoding explicitly. Use only plain ASCII punctuation (hyphens, not
   em dashes) in anything you write, to avoid encoding mismatches when this
   file is later viewed on GitHub or in an editor defaulting to a different
   codepage.
6. `git add docs/reports/<file> && git commit -m "chore: nightly hygiene report <date>"`.
7. Send a push notification: one line with counts per category and the
   report path.

If step 1 or step 2 fails (no network, vulture install fails, vulture times
out or crashes), still complete steps 3-7, but mark the Phase 2 section
"INCOMPLETE: <reason>" instead of silently omitting it. This same rule
applies to Phase 1 (worktree and untracked-file detection): if either of
those git-backed checks fails (e.g. an unexpected git error), the script
still produces a report with a "Phase 1 INCOMPLETE: <reason>" line rather
than crashing and producing no report at all. Never treat a Phase 1 or
Phase 2 failure as a reason to skip steps 3-7 or withhold the report.
