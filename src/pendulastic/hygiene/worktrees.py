import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from pendulastic.hygiene.models import Category, Finding


@dataclass
class WorktreeInfo:
    path: str
    branch: str
    locked: bool = False
    lock_reason: Optional[str] = None


def _run_git(repo_root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo_root), *args],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


def list_worktrees(repo_root: Path) -> list[WorktreeInfo]:
    # `git worktree list --porcelain` always lists the main/primary worktree
    # FIRST, followed by linked worktrees - callers rely on this ordering to
    # structurally protect the main worktree regardless of what branch it's
    # checked out to.
    output = _run_git(repo_root, "worktree", "list", "--porcelain")
    worktrees: list[WorktreeInfo] = []
    current_path = None
    current_branch = ""
    current_locked = False
    current_lock_reason: Optional[str] = None

    for line in output.splitlines():
        if line.startswith("worktree "):
            if current_path is not None:
                worktrees.append(
                    WorktreeInfo(current_path, current_branch, current_locked, current_lock_reason)
                )
            current_path = line[len("worktree "):]
            current_branch = ""
            current_locked = False
            current_lock_reason = None
        elif line.startswith("branch "):
            ref = line[len("branch "):]
            prefix = "refs/heads/"
            current_branch = ref[len(prefix):] if ref.startswith(prefix) else ref
        elif line == "locked" or line.startswith("locked "):
            current_locked = True
            reason = line[len("locked "):].strip() if line.startswith("locked ") else ""
            current_lock_reason = reason or None
    if current_path is not None:
        worktrees.append(
            WorktreeInfo(current_path, current_branch, current_locked, current_lock_reason)
        )
    return worktrees


def is_merged(repo_root: Path, main_branch: str, branch: str) -> bool:
    output = _run_git(repo_root, "log", f"{main_branch}..{branch}", "--oneline", "--")
    return output.strip() == ""


def last_commit_age_days(repo_root: Path, branch: str, now: float) -> float:
    # "--" goes AFTER the revision here (not before it) to tell git "there
    # are no pathspecs following" while still letting `branch` be resolved
    # as a revision - putting "--" before `branch` would instead make git
    # treat `branch` itself as a pathspec, which is wrong.
    output = _run_git(repo_root, "log", "-1", "--format=%ct", branch, "--")
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

    all_worktrees = list_worktrees(repo_root)
    # Structural protection: the FIRST worktree in the list is always the
    # main/primary checkout, regardless of what branch it happens to be on.
    # This holds even when the primary checkout is on some branch other than
    # main_branch (e.g. mid-feature-work) and the tool is invoked from a
    # different linked worktree entirely.
    primary_worktree_path = Path(all_worktrees[0].path).resolve() if all_worktrees else None

    for wt in all_worktrees:
        if primary_worktree_path is not None and Path(wt.path).resolve() == primary_worktree_path:
            continue
        if wt.branch == main_branch:
            continue
        if not wt.branch:
            continue
        if Path(wt.path).resolve() == repo_root.resolve():
            continue

        if wt.locked:
            reason_suffix = f" (reason: {wt.lock_reason})" if wt.lock_reason else " (no reason given)"
            findings.append(
                Finding(
                    category=Category.NEEDS_REVIEW,
                    description=(
                        f"Worktree '{wt.path}' (branch {wt.branch}) is locked{reason_suffix} - "
                        "it is in active use; confirm with whoever locked it before removing."
                    ),
                    command=(
                        f"git worktree list --porcelain  "
                        "# locked worktree - do not remove without review"
                    ),
                    source="Phase 1: Worktrees",
                )
            )
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
