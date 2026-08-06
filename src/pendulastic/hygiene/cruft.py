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
