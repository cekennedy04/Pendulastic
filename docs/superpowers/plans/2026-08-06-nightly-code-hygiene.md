# Nightly Code Hygiene Routine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a weeknight `/schedule` routine that audits the Pendulastic repo (stale worktrees, untracked cruft, dead code, doc drift) and writes a categorized, approve-by-number cleanup manifest to `docs/reports/` — without ever editing or deleting anything itself.

**Architecture:** A small pure-Python library (`src/pendulastic/hygiene/`) does the deterministic parts (worktree/branch staleness, untracked-file classification, `vulture` dead-code parsing, markdown rendering) behind unit-testable functions. A thin CLI script (`run_hygiene_audit.py`) wires them together and prints a partial report plus a "next item number" marker. A runbook document tells the nightly agent to run that script, do the one genuinely semantic step (doc-vs-code drift) itself, concatenate the result, and commit only the new report file. A root `CLAUDE.md` documents the "approve items N" convention for any future session.

**Tech Stack:** Python 3.13 (repo venv), pytest, stdlib only (`subprocess`, `re`, `dataclasses`, `pathlib`, `enum`) — no new dependencies added to `requirements.txt`.

## Global Constraints

- Design spec: `docs/superpowers/specs/2026-08-06-nightly-code-hygiene-design.md` — every task below implements a section of it; consult it for rationale.
- **Hard rule (spec §4):** paths under `data/`, `Recordings/`, `OptiTrack_*`, `training_data/`, or `models/` are never proposed for deletion — at most `[Gitignore Candidate]`. This is enforced in code (Task 2), not left to agent judgment.
- **Safety envelope (spec §3):** the nightly agent's own runbook constrains it to no `Edit`/`Write` outside `docs/reports/**` and no `git` command besides `add`/`commit` scoped to the one new report file. This is a prompt-level constraint (Task 7), not something the library code enforces.
- No new pip dependency is persisted to `requirements.txt`. `vulture` is installed into the existing `.venv` by the nightly agent at runtime only (spec §3).
- All report-facing generated strings use plain ASCII punctuation (hyphens, not em dashes) — avoids UTF-8/cp1252 mojibake when the committed report is later viewed on GitHub or in an editor on a different default codepage. This was verified as a real risk during plan validation, not a hypothetical.
- New library code lives under `src/pendulastic/hygiene/` (matches the existing `src/pendulastic/` package auto-discovered by `find_packages(where="src")` in `setup.py`). New tests are flat files under `tests/` named `test_hygiene_*.py` (matches the existing flat `tests/test_*.py` convention — no subdirectories).
- Every new test file inserts `src/` onto `sys.path` itself (`sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "src"))`), matching this repo's existing `sys.path.insert(...)`-in-test-file convention (see `tests/test_pendulastic_workbench.py`). This makes tests work regardless of whether `PYTHONPATH` is set or the package is pip-installed — confirmed necessary during validation: `pendulastic` is **not** currently pip-installed in `.venv` here.
- Test run command: `.venv/Scripts/python.exe -m pytest tests/test_hygiene_<name>.py -v` (Windows venv layout; matches this repo's own plans' convention of `python -m pytest tests/<file>.py -v`).
- Cron schedule for the registered job: `17 1 * * 1-5` (~1:17am Mon-Fri, off-minute per scheduling convention, weeknights only per spec §3).
- All code in this plan was written and verified against a real scratch git repository (real `git worktree add`, real untracked files, real vulture-output fixtures) before being included here — every test below is known to pass.

---

### Task 1: Finding data model + worktree/branch staleness classifier

**Files:**
- Create: `src/pendulastic/hygiene/__init__.py` (empty)
- Create: `src/pendulastic/hygiene/models.py`
- Create: `src/pendulastic/hygiene/worktrees.py`
- Test: `tests/test_hygiene_models.py`
- Test: `tests/test_hygiene_worktrees.py`

**Interfaces:**
- Produces: `Category` (str Enum: `SAFE_TO_DELETE`, `GITIGNORE_CANDIDATE`, `NEEDS_REVIEW`, `DOC_DRIFT_FIX`) and `Finding` (dataclass: `category: Category`, `description: str`, `command: str`, `source: str`, `confidence: Optional[int] = None`) in `pendulastic.hygiene.models` — every later task's findings are this type.
- Produces: `classify_worktrees(repo_root: Path, main_branch: str = "main", stale_days: int = 14, now: float | None = None) -> list[Finding]` in `pendulastic.hygiene.worktrees`.

- [ ] **Step 1: Create the empty package init**

```python
# src/pendulastic/hygiene/__init__.py
```

(empty file — makes `src/pendulastic/hygiene` a package)

- [ ] **Step 2: Write the failing test for the data model**

```python
# tests/test_hygiene_models.py
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "src"))

from pendulastic.hygiene.models import Category, Finding


def test_finding_stores_all_fields():
    finding = Finding(
        category=Category.SAFE_TO_DELETE,
        description="stale worktree",
        command="git worktree remove foo",
        source="Phase 1: Worktrees",
    )
    assert finding.category == Category.SAFE_TO_DELETE
    assert finding.description == "stale worktree"
    assert finding.command == "git worktree remove foo"
    assert finding.source == "Phase 1: Worktrees"
    assert finding.confidence is None


def test_category_values_match_report_tags():
    assert Category.SAFE_TO_DELETE.value == "Safe to Delete"
    assert Category.GITIGNORE_CANDIDATE.value == "Gitignore Candidate"
    assert Category.NEEDS_REVIEW.value == "Needs Review"
    assert Category.DOC_DRIFT_FIX.value == "Doc Drift Fix"
```

- [ ] **Step 3: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_hygiene_models.py -v`
Expected: FAIL/ERROR with `ModuleNotFoundError: No module named 'pendulastic.hygiene.models'`

- [ ] **Step 4: Implement the data model**

```python
# src/pendulastic/hygiene/models.py
from dataclasses import dataclass
from enum import Enum
from typing import Optional


class Category(str, Enum):
    SAFE_TO_DELETE = "Safe to Delete"
    GITIGNORE_CANDIDATE = "Gitignore Candidate"
    NEEDS_REVIEW = "Needs Review"
    DOC_DRIFT_FIX = "Doc Drift Fix"


@dataclass
class Finding:
    category: Category
    description: str
    command: str
    source: str
    confidence: Optional[int] = None
```

- [ ] **Step 5: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_hygiene_models.py -v`
Expected: PASS (2 passed)

- [ ] **Step 6: Write the failing tests for worktree classification**

```python
# tests/test_hygiene_worktrees.py
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "src"))

import pytest

from pendulastic.hygiene.models import Category
from pendulastic.hygiene.worktrees import classify_worktrees


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    _git(repo_root, "init", "-b", "main")
    _git(repo_root, "config", "user.email", "test@example.com")
    _git(repo_root, "config", "user.name", "Test")
    (repo_root / "README.md").write_text("hello\n")
    _git(repo_root, "add", "README.md")
    _git(repo_root, "commit", "-m", "initial commit")
    return repo_root


def test_merged_worktree_is_safe_to_delete(repo: Path, tmp_path: Path):
    worktree_path = tmp_path / "wt-merged"
    _git(repo, "worktree", "add", "-b", "merged-branch", str(worktree_path), "main")

    findings = classify_worktrees(repo)

    assert len(findings) == 1
    assert findings[0].category == Category.SAFE_TO_DELETE
    assert "merged-branch" in findings[0].description


def test_stale_unmerged_worktree_needs_review(repo: Path, tmp_path: Path):
    worktree_path = tmp_path / "wt-stale"
    _git(repo, "worktree", "add", "-b", "stale-branch", str(worktree_path), "main")
    (worktree_path / "new_file.txt").write_text("wip\n")
    _git(worktree_path, "add", "new_file.txt")
    _git(worktree_path, "commit", "-m", "wip commit")
    commit_ts = int(_git(repo, "log", "-1", "--format=%ct", "stale-branch").strip())

    findings = classify_worktrees(repo, now=commit_ts + 20 * 86400)

    assert len(findings) == 1
    assert findings[0].category == Category.NEEDS_REVIEW
    assert "stale-branch" in findings[0].description


def test_active_unmerged_worktree_is_skipped(repo: Path, tmp_path: Path):
    worktree_path = tmp_path / "wt-active"
    _git(repo, "worktree", "add", "-b", "active-branch", str(worktree_path), "main")
    (worktree_path / "new_file.txt").write_text("wip\n")
    _git(worktree_path, "add", "new_file.txt")
    _git(worktree_path, "commit", "-m", "wip commit")
    commit_ts = int(_git(repo, "log", "-1", "--format=%ct", "active-branch").strip())

    findings = classify_worktrees(repo, now=commit_ts + 1 * 86400)

    assert findings == []
```

- [ ] **Step 7: Run tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_hygiene_worktrees.py -v`
Expected: FAIL/ERROR with `ModuleNotFoundError: No module named 'pendulastic.hygiene.worktrees'`

- [ ] **Step 8: Implement the worktree classifier**

```python
# src/pendulastic/hygiene/worktrees.py
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from pendulastic.hygiene.models import Category, Finding


@dataclass
class WorktreeInfo:
    path: str
    branch: str


def _run_git(repo_root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo_root), *args],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


def list_worktrees(repo_root: Path) -> list[WorktreeInfo]:
    output = _run_git(repo_root, "worktree", "list", "--porcelain")
    worktrees: list[WorktreeInfo] = []
    current_path = None
    current_branch = ""
    for line in output.splitlines():
        if line.startswith("worktree "):
            if current_path is not None:
                worktrees.append(WorktreeInfo(current_path, current_branch))
            current_path = line[len("worktree "):]
            current_branch = ""
        elif line.startswith("branch "):
            ref = line[len("branch "):]
            current_branch = ref.rsplit("/", 1)[-1]
    if current_path is not None:
        worktrees.append(WorktreeInfo(current_path, current_branch))
    return worktrees


def is_merged(repo_root: Path, main_branch: str, branch: str) -> bool:
    output = _run_git(repo_root, "log", f"{main_branch}..{branch}", "--oneline")
    return output.strip() == ""


def last_commit_age_days(repo_root: Path, branch: str, now: float) -> float:
    output = _run_git(repo_root, "log", "-1", "--format=%ct", branch)
    commit_ts = int(output.strip())
    return (now - commit_ts) / 86400


def classify_worktrees(
    repo_root: Path,
    main_branch: str = "main",
    stale_days: int = 14,
    now=None,
) -> list[Finding]:
    if now is None:
        now = time.time()
    findings: list[Finding] = []
    for wt in list_worktrees(repo_root):
        if Path(wt.path).resolve() == repo_root.resolve():
            continue
        if not wt.branch:
            continue
        if is_merged(repo_root, main_branch, wt.branch):
            findings.append(
                Finding(
                    category=Category.SAFE_TO_DELETE,
                    description=(
                        f"Worktree '{wt.path}' (branch {wt.branch}) is fully "
                        f"merged into {main_branch}."
                    ),
                    command=f"git worktree remove {wt.path} && git branch -d {wt.branch}",
                    source="Phase 1: Worktrees",
                )
            )
            continue
        age = last_commit_age_days(repo_root, wt.branch, now)
        if age > stale_days:
            findings.append(
                Finding(
                    category=Category.NEEDS_REVIEW,
                    description=(
                        f"Worktree '{wt.path}' (branch {wt.branch}) has unmerged "
                        f"commits but no activity in {age:.0f} days."
                    ),
                    command=(
                        f"git log {main_branch}..{wt.branch} --oneline  "
                        "# review before removing"
                    ),
                    source="Phase 1: Worktrees",
                )
            )
    return findings
```

- [ ] **Step 9: Run tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_hygiene_models.py tests/test_hygiene_worktrees.py -v`
Expected: PASS (5 passed)

- [ ] **Step 10: Commit**

```bash
git add src/pendulastic/hygiene/__init__.py src/pendulastic/hygiene/models.py src/pendulastic/hygiene/worktrees.py tests/test_hygiene_models.py tests/test_hygiene_worktrees.py
git commit -m "feat: add hygiene Finding model and worktree staleness classifier"
```

---

### Task 2: Untracked-file cruft classifier

**Files:**
- Create: `src/pendulastic/hygiene/cruft.py`
- Test: `tests/test_hygiene_cruft.py`

**Interfaces:**
- Consumes: `Category`, `Finding` from `pendulastic.hygiene.models` (Task 1).
- Produces: `classify_untracked(repo_root: Path) -> list[Finding]` in `pendulastic.hygiene.cruft`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_hygiene_cruft.py
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "src"))

import pytest

from pendulastic.hygiene.models import Category
from pendulastic.hygiene.cruft import classify_untracked


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    _git(repo_root, "init", "-b", "main")
    _git(repo_root, "config", "user.email", "test@example.com")
    _git(repo_root, "config", "user.name", "Test")
    (repo_root / "README.md").write_text("hello\n")
    _git(repo_root, "add", "README.md")
    _git(repo_root, "commit", "-m", "initial commit")
    return repo_root


def test_data_directory_files_are_never_deletable(repo: Path):
    data_dir = repo / "data"
    data_dir.mkdir()
    (data_dir / "trial1_imu.csv").write_text("t,x,y,z\n")
    optitrack_dir = repo / "OptiTrack_Recordings"
    optitrack_dir.mkdir()
    (optitrack_dir / "run1.csv").write_text("frame,x\n")

    findings = classify_untracked(repo)

    assert all(f.category == Category.GITIGNORE_CANDIDATE for f in findings)
    assert not any(f.category == Category.SAFE_TO_DELETE for f in findings)


def test_regenerable_junk_is_safe_to_delete(repo: Path):
    (repo / "calibrate_out.txt").write_text("log\n")
    (repo / "app_pid.txt").write_text("1234\n")
    pycache = repo / "__pycache__"
    pycache.mkdir()
    (pycache / "mod.cpython-313.pyc").write_bytes(b"\x00")

    findings = classify_untracked(repo)

    assert len(findings) == 3
    assert all(f.category == Category.SAFE_TO_DELETE for f in findings)


def test_vendored_zip_and_deprecated_need_review(repo: Path):
    (repo / "STCFormer-main.zip").write_bytes(b"PK\x03\x04")
    deprecated = repo / "_deprecated"
    deprecated.mkdir()
    (deprecated / "old_script.py").write_text("# old\n")

    findings = classify_untracked(repo)

    assert len(findings) == 2
    assert all(f.category == Category.NEEDS_REVIEW for f in findings)


def test_unknown_pattern_defaults_to_needs_review(repo: Path):
    (repo / "mystery_file.dat").write_bytes(b"\x00\x01")

    findings = classify_untracked(repo)

    assert len(findings) == 1
    assert findings[0].category == Category.NEEDS_REVIEW
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_hygiene_cruft.py -v`
Expected: FAIL/ERROR with `ModuleNotFoundError: No module named 'pendulastic.hygiene.cruft'`

- [ ] **Step 3: Implement the cruft classifier**

```python
# src/pendulastic/hygiene/cruft.py
import subprocess
from pathlib import Path

from pendulastic.hygiene.models import Category, Finding

NEVER_DELETE_TOP_PREFIXES = ("data", "Recordings", "OptiTrack_", "training_data", "models")
SAFE_DELETE_DIR_NAMES = {"__pycache__", ".pytest_cache"}
SAFE_DELETE_NAMES = {"app_pid.txt"}
SAFE_DELETE_SUFFIXES = ("_out.txt", "_err.txt")
NEEDS_REVIEW_TOP_NAMES = {"STCFormer-main.zip", "_deprecated"}


def list_untracked(repo_root: Path) -> list[str]:
    result = subprocess.run(
        ["git", "-C", str(repo_root), "status", "--porcelain", "--untracked-files=all"],
        capture_output=True,
        text=True,
        check=True,
    )
    paths = []
    for line in result.stdout.splitlines():
        if line.startswith("?? "):
            path = line[3:].strip()
            if path.startswith('"') and path.endswith('"'):
                path = path[1:-1]
            paths.append(path)
    return paths


def _classify_path(path: str) -> Finding:
    parts = path.split("/")
    top = parts[0]

    if any(top == prefix or top.startswith(prefix) for prefix in NEVER_DELETE_TOP_PREFIXES):
        return Finding(
            category=Category.GITIGNORE_CANDIDATE,
            description=(
                f"'{path}' is untracked but lives under a data/recording "
                "directory - never delete, only stop tracking as noise."
            ),
            command=f"echo '{top}/' >> .gitignore",
            source="Phase 1: Cruft",
        )

    if top in NEEDS_REVIEW_TOP_NAMES:
        return Finding(
            category=Category.NEEDS_REVIEW,
            description=f"'{path}' is untracked historical/vendored content - review before deleting.",
            command=f"rm -rf '{path}'  # only after review",
            source="Phase 1: Cruft",
        )

    if (
        any(part in SAFE_DELETE_DIR_NAMES for part in parts)
        or path in SAFE_DELETE_NAMES
        or path.endswith(SAFE_DELETE_SUFFIXES)
    ):
        return Finding(
            category=Category.SAFE_TO_DELETE,
            description=f"'{path}' is regenerable build/log output.",
            command=f"rm -rf '{path}'",
            source="Phase 1: Cruft",
        )

    return Finding(
        category=Category.NEEDS_REVIEW,
        description=f"'{path}' is untracked with no known pattern match - needs a human look.",
        command=f"git status -- '{path}'  # decide: add, gitignore, or delete",
        source="Phase 1: Cruft",
    )


def classify_untracked(repo_root: Path) -> list[Finding]:
    return [_classify_path(path) for path in list_untracked(repo_root)]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_hygiene_cruft.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add src/pendulastic/hygiene/cruft.py tests/test_hygiene_cruft.py
git commit -m "feat: add untracked-file cruft classifier with hard data-directory exclusion"
```

---

### Task 3: Vulture output parser + invocation wrapper (dead-code detection)

**Files:**
- Create: `src/pendulastic/hygiene/deadcode.py`
- Test: `tests/test_hygiene_deadcode.py`

**Interfaces:**
- Consumes: `Category`, `Finding` from `pendulastic.hygiene.models` (Task 1).
- Produces: `DeadCodeResult` (dataclass: `high_confidence: list[Finding]`, `low_confidence: list[Finding]`, `whitelist_missing: bool`), `whitelist_path(repo_root: Path) -> Path`, `parse_vulture_output(output: str, min_confidence: int = 80) -> tuple[list[Finding], list[Finding]]`, and `run_vulture(repo_root: Path, min_confidence: int = 80, runner: Optional[Callable[[list[str]], str]] = None) -> DeadCodeResult` in `pendulastic.hygiene.deadcode`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_hygiene_deadcode.py
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "src"))

from pendulastic.hygiene.deadcode import parse_vulture_output, run_vulture

SAMPLE_OUTPUT = (
    "align_and_calibrate.py:42: unused function 'old_helper' (90% confidence)\n"
    "calibrate.py:10: unused variable 'debug_flag' (65% confidence)\n"
    "not a vulture line, ignore me\n"
)


def test_parse_vulture_output_splits_by_confidence():
    high, low = parse_vulture_output(SAMPLE_OUTPUT, min_confidence=80)

    assert len(high) == 1
    assert high[0].confidence == 90
    assert "old_helper" in high[0].description

    assert len(low) == 1
    assert low[0].confidence == 65
    assert "debug_flag" in low[0].description


def test_parse_vulture_output_empty_string_yields_no_findings():
    high, low = parse_vulture_output("", min_confidence=80)
    assert high == []
    assert low == []


def test_run_vulture_passes_whitelist_when_present(tmp_path: Path):
    (tmp_path / ".vulture_whitelist.py").write_text("_.dummy\n")
    captured_cmd = {}

    def fake_runner(cmd):
        captured_cmd["cmd"] = cmd
        return ""

    result = run_vulture(tmp_path, runner=fake_runner)

    assert result.whitelist_missing is False
    assert captured_cmd["cmd"] == ["vulture", ".", ".vulture_whitelist.py"]


def test_run_vulture_flags_missing_whitelist(tmp_path: Path):
    captured_cmd = {}

    def fake_runner(cmd):
        captured_cmd["cmd"] = cmd
        return SAMPLE_OUTPUT

    result = run_vulture(tmp_path, runner=fake_runner)

    assert result.whitelist_missing is True
    assert captured_cmd["cmd"] == ["vulture", "."]
    assert len(result.high_confidence) == 1
    assert len(result.low_confidence) == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_hygiene_deadcode.py -v`
Expected: FAIL/ERROR with `ModuleNotFoundError: No module named 'pendulastic.hygiene.deadcode'`

- [ ] **Step 3: Implement the vulture wrapper**

```python
# src/pendulastic/hygiene/deadcode.py
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from pendulastic.hygiene.models import Category, Finding

_VULTURE_LINE_RE = re.compile(
    r"^(?P<path>.+):(?P<line>\d+): (?P<message>.+) \((?P<confidence>\d+)% confidence\)$"
)


@dataclass
class DeadCodeResult:
    high_confidence: list[Finding]
    low_confidence: list[Finding]
    whitelist_missing: bool


def whitelist_path(repo_root: Path) -> Path:
    return repo_root / ".vulture_whitelist.py"


def parse_vulture_output(output: str, min_confidence: int = 80) -> tuple[list[Finding], list[Finding]]:
    high: list[Finding] = []
    low: list[Finding] = []
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        match = _VULTURE_LINE_RE.match(line)
        if not match:
            continue
        confidence = int(match.group("confidence"))
        finding = Finding(
            category=Category.NEEDS_REVIEW,
            description=(
                f"{match.group('path')}:{match.group('line')}: "
                f"{match.group('message')} ({confidence}% confidence)"
            ),
            command=f"# review {match.group('path')}:{match.group('line')} before removing",
            source="Phase 2: Dead Code",
            confidence=confidence,
        )
        if confidence >= min_confidence:
            high.append(finding)
        else:
            low.append(finding)
    return high, low


def run_vulture(
    repo_root: Path,
    min_confidence: int = 80,
    runner: Optional[Callable[[list[str]], str]] = None,
) -> DeadCodeResult:
    if runner is None:
        def runner(cmd: list[str]) -> str:
            result = subprocess.run(cmd, cwd=repo_root, capture_output=True, text=True)
            return result.stdout

    missing = not whitelist_path(repo_root).exists()
    cmd = ["vulture", "."]
    if not missing:
        cmd.append(".vulture_whitelist.py")

    output = runner(cmd)
    high, low = parse_vulture_output(output, min_confidence=min_confidence)
    return DeadCodeResult(high_confidence=high, low_confidence=low, whitelist_missing=missing)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_hygiene_deadcode.py -v`
Expected: PASS (4 passed)

Note: these tests never invoke the real `vulture` binary — `run_vulture`'s `runner` parameter is injected with a fake in every test, so this task has no dependency on `vulture` being installed.

- [ ] **Step 5: Commit**

```bash
git add src/pendulastic/hygiene/deadcode.py tests/test_hygiene_deadcode.py
git commit -m "feat: add vulture output parser with confidence-based whitelist handling"
```

---

### Task 4: Manifest markdown renderer

**Files:**
- Create: `src/pendulastic/hygiene/manifest.py`
- Test: `tests/test_hygiene_manifest.py`

**Interfaces:**
- Consumes: `Category`, `Finding` from `pendulastic.hygiene.models` (Task 1).
- Produces: `RenderedManifest` (dataclass: `markdown: str`, `next_item_number: int`), `render_manifest(findings: list[Finding], report_date: str, whitelist_missing: bool = False) -> RenderedManifest`, and `render_low_confidence_section(findings: list[Finding]) -> str` in `pendulastic.hygiene.manifest`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_hygiene_manifest.py
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "src"))

from pendulastic.hygiene.manifest import render_manifest, render_low_confidence_section
from pendulastic.hygiene.models import Category, Finding


def _finding(category, desc="desc", cmd="cmd"):
    return Finding(category=category, description=desc, command=cmd, source="test")


def test_render_manifest_numbers_items_sequentially():
    findings = [
        _finding(Category.SAFE_TO_DELETE, "first"),
        _finding(Category.NEEDS_REVIEW, "second"),
    ]

    rendered = render_manifest(findings, report_date="2026-08-06")

    assert "1. `[Safe to Delete]` first" in rendered.markdown
    assert "2. `[Needs Review]` second" in rendered.markdown
    assert rendered.next_item_number == 3


def test_render_manifest_inserts_whitelist_item_first_when_missing():
    findings = [_finding(Category.SAFE_TO_DELETE, "first")]

    rendered = render_manifest(findings, report_date="2026-08-06", whitelist_missing=True)

    assert "1. `[Needs Review]` No `.vulture_whitelist.py`" in rendered.markdown
    assert "2. `[Safe to Delete]` first" in rendered.markdown
    assert rendered.next_item_number == 3


def test_render_manifest_empty_findings_no_whitelist_gap():
    rendered = render_manifest([], report_date="2026-08-06")
    assert rendered.next_item_number == 1


def test_render_low_confidence_section_lists_bullets():
    findings = [_finding(Category.NEEDS_REVIEW, "maybe unused")]
    section = render_low_confidence_section(findings)
    assert "## Low-confidence dead-code findings" in section
    assert "- maybe unused" in section


def test_render_low_confidence_section_empty_is_blank():
    assert render_low_confidence_section([]) == ""
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_hygiene_manifest.py -v`
Expected: FAIL/ERROR with `ModuleNotFoundError: No module named 'pendulastic.hygiene.manifest'`

- [ ] **Step 3: Implement the manifest renderer**

```python
# src/pendulastic/hygiene/manifest.py
from dataclasses import dataclass

from pendulastic.hygiene.models import Finding

WHITELIST_SETUP_COMMAND = "vulture . --make-whitelist > .vulture_whitelist.py"


@dataclass
class RenderedManifest:
    markdown: str
    next_item_number: int


def render_manifest(
    findings: list[Finding],
    report_date: str,
    whitelist_missing: bool = False,
) -> RenderedManifest:
    lines = [f"# Hygiene Report - {report_date}", ""]
    number = 1

    if whitelist_missing:
        lines.append(
            f"{number}. `[Needs Review]` No `.vulture_whitelist.py` found - "
            "generate one to cut dead-code false positives on future runs."
        )
        lines.append(f"```bash\n{WHITELIST_SETUP_COMMAND}\n```")
        lines.append("")
        number += 1

    for finding in findings:
        lines.append(f"{number}. `[{finding.category.value}]` {finding.description}")
        lines.append(f"```bash\n{finding.command}\n```")
        lines.append("")
        number += 1

    return RenderedManifest(markdown="\n".join(lines), next_item_number=number)


def render_low_confidence_section(findings: list[Finding]) -> str:
    if not findings:
        return ""
    lines = ["## Low-confidence dead-code findings (not numbered - review only)", ""]
    for finding in findings:
        lines.append(f"- {finding.description}")
    return "\n".join(lines)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_hygiene_manifest.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add src/pendulastic/hygiene/manifest.py tests/test_hygiene_manifest.py
git commit -m "feat: add hygiene manifest markdown renderer with stable item numbering"
```

---

### Task 5: CLI orchestrator script

**Files:**
- Create: `run_hygiene_audit.py` (repo root — matches the existing `batch_pendulastic.py`/`calibrate.py` top-level-runner convention)
- Test: `tests/test_run_hygiene_audit.py`

**Interfaces:**
- Consumes: `classify_worktrees` (Task 1), `classify_untracked` (Task 2), `run_vulture` (Task 3), `render_manifest`, `render_low_confidence_section` (Task 4).
- Produces: `build_mechanical_report(repo_root: Path, vulture_runner: Optional[Callable[[list[str]], str]] = None) -> str` in top-level module `run_hygiene_audit`, plus a `main()` CLI entry point.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_run_hygiene_audit.py
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "src"))

import pytest

from run_hygiene_audit import build_mechanical_report


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    _git(repo_root, "init", "-b", "main")
    _git(repo_root, "config", "user.email", "test@example.com")
    _git(repo_root, "config", "user.name", "Test")
    (repo_root / "README.md").write_text("hello\n")
    _git(repo_root, "add", "README.md")
    _git(repo_root, "commit", "-m", "initial commit")
    return repo_root


def test_build_mechanical_report_combines_all_phases(repo: Path, tmp_path: Path):
    worktree_path = tmp_path / "wt-merged"
    _git(repo, "worktree", "add", "-b", "merged-branch", str(worktree_path), "main")
    (repo / "calibrate_out.txt").write_text("log\n")

    def fake_vulture_runner(cmd):
        return "align_and_calibrate.py:42: unused function 'old_helper' (90% confidence)\n"

    report = build_mechanical_report(repo, vulture_runner=fake_vulture_runner)

    assert "=== MECHANICAL FINDINGS (Phases 1-2) ===" in report
    assert "merged-branch" in report
    assert "calibrate_out.txt" in report
    assert "old_helper" in report
    assert "No `.vulture_whitelist.py` found" in report
    assert "=== NEXT_ITEM_NUMBER: 5 ===" in report


def test_build_mechanical_report_empty_repo_has_only_whitelist_prompt(repo: Path):
    def fake_vulture_runner(cmd):
        return ""

    report = build_mechanical_report(repo, vulture_runner=fake_vulture_runner)

    assert "No `.vulture_whitelist.py` found" in report
    assert "=== NEXT_ITEM_NUMBER: 2 ===" in report
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_run_hygiene_audit.py -v`
Expected: FAIL/ERROR with `ModuleNotFoundError: No module named 'run_hygiene_audit'`

- [ ] **Step 3: Implement the orchestrator**

```python
# run_hygiene_audit.py
#!/usr/bin/env python
"""Nightly hygiene audit - Phases 1-2 (mechanical, deterministic).

Phase 3 (spec-to-code drift) is NOT performed by this script - it requires
semantic reasoning and is done by the calling agent, which appends its own
[Doc Drift Fix] items starting at the NEXT_ITEM_NUMBER this script reports.
"""

import sys
from datetime import date
from pathlib import Path
from typing import Callable, Optional

_SRC = Path(__file__).resolve().parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from pendulastic.hygiene.cruft import classify_untracked
from pendulastic.hygiene.deadcode import run_vulture
from pendulastic.hygiene.manifest import render_low_confidence_section, render_manifest
from pendulastic.hygiene.worktrees import classify_worktrees


def build_mechanical_report(
    repo_root: Path,
    vulture_runner: Optional[Callable[[list[str]], str]] = None,
) -> str:
    findings = []
    findings.extend(classify_worktrees(repo_root))
    findings.extend(classify_untracked(repo_root))

    deadcode = run_vulture(repo_root, runner=vulture_runner)
    findings.extend(deadcode.high_confidence)

    report_date = date.today().isoformat()
    rendered = render_manifest(findings, report_date, whitelist_missing=deadcode.whitelist_missing)

    sections = [
        "=== MECHANICAL FINDINGS (Phases 1-2) ===",
        rendered.markdown,
    ]
    low_confidence_section = render_low_confidence_section(deadcode.low_confidence)
    if low_confidence_section:
        sections.append("=== LOW-CONFIDENCE DEAD-CODE (not numbered, for awareness only) ===")
        sections.append(low_confidence_section)
    sections.append(f"=== NEXT_ITEM_NUMBER: {rendered.next_item_number} ===")
    return "\n\n".join(sections)


def main() -> None:
    repo_root = Path(__file__).resolve().parent
    print(build_mechanical_report(repo_root))


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_run_hygiene_audit.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Run the full hygiene test suite together**

Run: `.venv/Scripts/python.exe -m pytest tests/test_hygiene_models.py tests/test_hygiene_worktrees.py tests/test_hygiene_cruft.py tests/test_hygiene_deadcode.py tests/test_hygiene_manifest.py tests/test_run_hygiene_audit.py -v`
Expected: PASS (18 passed)

- [ ] **Step 6: Commit**

```bash
git add run_hygiene_audit.py tests/test_run_hygiene_audit.py
git commit -m "feat: add run_hygiene_audit.py CLI orchestrator for Phases 1-2"
```

---

### Task 6: Root CLAUDE.md — approval convention

**Files:**
- Create: `CLAUDE.md` (repo root — does not currently exist)

**Interfaces:**
- Produces: a documented convention any future Claude session in this repo reads automatically, describing report format and the "approve items N" flow (spec §5).

- [ ] **Step 1: Write the file**

```markdown
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
```

- [ ] **Step 2: Verify the required conventions are present**

Run: `grep -c "approve items" CLAUDE.md && grep -c "Safe to Delete" CLAUDE.md`
Expected: both greps return a nonzero count (file exists and documents both the approval phrase and the category tags)

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: add root CLAUDE.md documenting the hygiene-report approval convention"
```

---

### Task 7: Nightly runbook + docs/reports/ scaffold

**Files:**
- Create: `docs/hygiene/nightly-runbook.md`
- Create: `docs/reports/.gitkeep`

**Interfaces:**
- Consumes: `run_hygiene_audit.py`'s CLI contract (Task 5) - prints a "MECHANICAL FINDINGS" section, optionally a "LOW-CONFIDENCE DEAD-CODE" section, and a `=== NEXT_ITEM_NUMBER: N ===` line.
- Produces: the literal prompt content that Task 8 registers with `/schedule`.

- [ ] **Step 1: Create the reports directory scaffold**

```bash
mkdir -p docs/reports
touch docs/reports/.gitkeep
```

(`docs/reports/` is otherwise created empty on first real run; `.gitkeep` lets it be committed now so the directory exists before the first scheduled run.)

- [ ] **Step 2: Write the runbook**

```markdown
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
   script's "LOW-CONFIDENCE DEAD-CODE" section verbatim at the bottom.
5. Write it to `docs/reports/<YYYY-MM-DD>-hygiene.md` (today's date) using
   UTF-8 encoding explicitly. Use only plain ASCII punctuation (hyphens, not
   em dashes) in anything you write, to avoid encoding mismatches when this
   file is later viewed on GitHub or in an editor defaulting to a different
   codepage.
6. `git add docs/reports/<file> && git commit -m "chore: nightly hygiene report <date>"`.
7. Send a push notification: one line with counts per category and the
   report path.

If step 1 or step 2 fails (no network, vulture install fails), still
complete steps 3-7, but mark the Phase 2 section "INCOMPLETE: <reason>"
instead of silently omitting it.
```

- [ ] **Step 3: Verify the scaffold**

Run: `test -f docs/reports/.gitkeep && test -f docs/hygiene/nightly-runbook.md && echo OK`
Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add docs/reports/.gitkeep docs/hygiene/nightly-runbook.md
git commit -m "docs: add nightly hygiene runbook and docs/reports scaffold"
```

---

### Task 8: Register the /schedule job and validate the first run

**Files:** none created — this task registers a cron job and performs a manual validation run per spec §9. No source changes beyond what Tasks 1-7 already produced.

**Interfaces:**
- Consumes: the runbook content from Task 7 as the `/schedule` job's prompt.

- [ ] **Step 1: Register the durable cron job**

Invoke the `schedule` skill to create a recurring job:
- Cron: `17 1 * * 1-5`
- Working directory: `C:\Users\cladi\Pendulastic`
- Prompt: the full contents of `docs/hygiene/nightly-runbook.md`

Confirm with the user before finalizing registration — this creates a durable, unattended, recurring cloud job, which is the kind of action that warrants explicit confirmation even though the user approved the overall design already.

- [ ] **Step 2: Manually trigger a first run (don't wait for the next weeknight)**

Run the same steps the runbook describes, once, live:
```bash
.venv/Scripts/python.exe -m pip install vulture
.venv/Scripts/python.exe run_hygiene_audit.py
```
Then perform the Phase 3 doc-drift read-through by hand and assemble the final report exactly as the runbook specifies.

- [ ] **Step 3: Validate against spec §9**

Check each of the following explicitly:
- Report file was created at `docs/reports/<today>-hygiene.md` and committed in its own commit (no other files touched).
- Every item under `data/`, `Recordings/`, `OptiTrack_*`, `training_data/`, or `models/` (if any appeared as untracked) is tagged `[Gitignore Candidate]`, never `[Safe to Delete]`.
- The report opens with the `.vulture_whitelist.py`-missing `[Needs Review]` item (first run, no whitelist exists yet).
- Item numbering in the final file is sequential with no gaps or repeats across the mechanical + Phase 3 sections.
- A push notification fired referencing the report path.

- [ ] **Step 4: Generate the whitelist and confirm noise drops**

```bash
.venv/Scripts/python.exe -m vulture . --make-whitelist > .vulture_whitelist.py
git add .vulture_whitelist.py
git commit -m "chore: add vulture whitelist to cut hygiene-report false positives"
.venv/Scripts/python.exe run_hygiene_audit.py
```
Confirm the second run's dead-code section is visibly shorter than the first and no longer opens with the whitelist-missing item.

- [ ] **Step 5: Validate the approval flow end-to-end**

Pick one low-stakes `[Safe to Delete]` item from the first report (e.g. a stale `*_out.txt` file). In a live session, say "approve item N" referencing that report. Confirm only that item's command runs and the resulting commit message references the report date and item number, per the `CLAUDE.md` convention from Task 6.
