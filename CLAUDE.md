# Pendulastic

## Coordination (read first)

`STATUS.md` at the repo root is the single source of truth for what is being
worked on, which branch is canonical for each workstream, and which decisions
are waiting on the owner. Many sessions work in this repo at once.

- Read `STATUS.md` before starting. Update it in the same PR that changes a
  workstream's state (PR opened or merged, branch superseded, decision made).
- One branch per workstream, with its own upstream, merged into `main` by PR.
  Do not merge or push directly to `main`. Check `git rev-parse --abbrev-ref @{u}`
  before pushing; a branch tracking `origin/main` pushes to main.
- Do not start work on a workstream `STATUS.md` marks FROZEN or TO ARCHIVE.
- `webapp/src/build-id.js` is generated but tracked, and CI checks it.
  `BUILD_ID` is a hash of the shell files plus the Rust source in
  `mobile-imu-core/src/`, so a PR that changes either must restamp it
  (`npm run build:shell` in `webapp/`) or `sw-shell.test.js` fails. A PR that
  changes neither must not commit a restamp: `npm run build:wasm` rewrites the
  file as a side effect, so restore it (`git checkout -- webapp/src/build-id.js`).
  This Windows checkout has CRLF working copies of some hashed files and
  computes a different `BUILD_ID` than CI; use the value CI's failure message
  expects, or stamp from an LF checkout.
- Run `cargo clippy --all-targets -- -D warnings` as well as `cargo test`
  before pushing Rust changes; CI fails on any clippy warning.
- `webapp/src/wasm/` is a local build. Run `npm run build:wasm` in `webapp/`
  before webapp tests or `neuromech/phone_trial.py` replays.
- The PT scorer exists in Python (`pendulastic_pt_score.py`), Rust
  (`mobile-imu-core/src/pt_score.rs`) and C# (`neuromech/designer/PtScore.cs`).
  A scorer change touches all three in one PR and runs all three parity checks:
  `tests/test_pt_zone_parity.py`, `neuromech/designer/parity/run_parity.py`, and
  `mobile-imu-core/tests/fixtures/gen_fixtures.py` followed by `cargo test`.
- A new plan in `docs/plans/` names the plan it supersedes, and the superseded
  plan gets a banner pointing forward.
- Dated notes in `docs/reports/` are history. Do not treat them, or agent
  memory, as current status when they disagree with `STATUS.md`.

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
- `[Doc Drift Fix]` - a status doc (README, STATUS.md, etc.) that no
  longer matches the code.

**Approving items:** when the user says "approve items 1, 3, 4" (referencing
the latest report, or a specific date if they mean an older one), read that
report and execute only the numbered commands named - nothing else, even an
adjacent `[Safe to Delete]` item the user didn't list. Commit with a message
that references the report date and the item numbers applied, e.g.:
`chore: apply hygiene report 2026-08-10 items 1,3,4`.

The nightly agent itself is read-only outside `docs/reports/**` - if asked to
run it manually, don't let it touch anything else.

## Participant folder naming

`Recordings/` and `OptiTrack_Recordings/` folders carry the parent study:
`Control_{pid}` (unaffected control), `CHAT_{pid}` (stroke), `MINT_{pid}` (MS),
and `Participant_{pid}` for legacy/ungrouped folders. Renamed 2026-09-01 (the MS
study's folders were `DOSA_{pid}` for part of that day; `DOSA_` still resolves).

**Never spell a participant folder name out in code** - both schemes are live
(the archive was never renamed). Resolve through `participant_paths.py`:
`participant_dir(root, pid)`, `iter_participant_dirs(root)`,
`glob_participants(root, *tail)`, `pid_from_dir_name(name)`,
`pids_in_path(rel)`. See `docs/reference/participant-folder-naming.md`.

Trial keys in `excluded_trials.json` / `trial_quality_tags.json` are built from
the participant ID, never the folder name, so a rename cannot invalidate an
exclusion. Keep it that way.

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
