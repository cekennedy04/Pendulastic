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
            prefix = "refs/heads/"
            current_branch = ref[len(prefix):] if ref.startswith(prefix) else ref
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
        if wt.branch == main_branch:
            continue
        if not wt.branch:
            continue
        if Path(wt.path).resolve() == repo_root.resolve():
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
