# Pendulastic

## Nightly Code Hygiene Reports

A scheduled agent runs weeknights (~1:17am) and writes a categorized cleanup
manifest to `docs/reports/YYYY-MM-DD-hygiene.md`. It never edits or deletes
anything itself - it only proposes.

Each report is a numbered checklist. Every item has a safety tag and a
literal command:

- `[Safe to Delete]` - regenerable cruft (logs, `__pycache__`, merged worktrees).
- `[Gitignore Candidate]` - untracked files that must never be deleted
  (anything under `data/`, `Recordings/`, `OptiTrack_*`, `training_data/`,
  `models/`) but should stop showing as noise.
- `[Needs Review]` - dead-code candidates, stale-but-unmerged worktrees,
  vendored/historical content. Never auto-approved.
- `[Doc Drift Fix]` - a status doc (README, DEPLOYMENT_PLAN, etc.) that no
  longer matches the code.

**Approving items:** when the user says "approve items 1, 3, 4" (referencing
the latest report, or a specific date if they mean an older one), read that
report and execute only the numbered commands named - nothing else, even an
adjacent `[Safe to Delete]` item the user didn't list. Commit with a message
that references the report date and the item numbers applied, e.g.:
`chore: apply hygiene report 2026-08-10 items 1,3,4`.

The nightly agent itself is read-only outside `docs/reports/**` - if asked to
run it manually, don't let it touch anything else.

## Skill routing

When the user's request matches an available skill, invoke it via the Skill tool. When in doubt, invoke the skill.

Key routing rules:
- Product ideas/brainstorming → invoke /office-hours
- Strategy/scope → invoke /plan-ceo-review
- Architecture → invoke /plan-eng-review
- Design system/plan review → invoke /design-consultation or /plan-design-review
- Full review pipeline → invoke /autoplan
- Bugs/errors → invoke /investigate
- QA/testing site behavior → invoke /qa or /qa-only
- Code review/diff check → invoke /review
- Visual polish → invoke /design-review
- Ship/deploy/PR → invoke /ship or /land-and-deploy
- Save progress → invoke /context-save
- Resume context → invoke /context-restore
- Author a backlog-ready spec/issue → invoke /spec
