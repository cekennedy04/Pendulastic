import shlex
import subprocess
from pathlib import Path
from typing import Optional

from pendulastic.hygiene.models import Category, Finding

# Exact-match protected top-level names (no accidental over-matching of things
# like "database/" or "models_v2_notes.md").
NEVER_DELETE_EXACT_NAMES = {"data", "Recordings", "training_data", "models"}
# Genuine prefix-match protected names - covers both "OptiTrack_Recordings"
# and "OptiTrack_Tracking_Data_...".
NEVER_DELETE_PREFIXES = ("OptiTrack_",)

SAFE_DELETE_DIR_NAMES = {"__pycache__", ".pytest_cache"}
SAFE_DELETE_NAMES = {"app_pid.txt"}
SAFE_DELETE_SUFFIXES = ("_out.txt", "_err.txt")
NEEDS_REVIEW_TOP_NAMES = {"STCFormer-main.zip", "_deprecated"}


def list_untracked(repo_root: Path) -> list[str]:
    # --untracked-files=normal (not "all") is critical: for a wholly-untracked
    # directory (e.g. training_data/ with 19,840 files), git reports ONE line
    # ("?? training_data/") instead of one line per contained file. Individual
    # untracked files inside otherwise-tracked directories are still reported
    # normally (e.g. "?? calibrate_out.txt").
    result = subprocess.run(
        ["git", "-C", str(repo_root), "status", "--porcelain", "--untracked-files=normal"],
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


def _matches_protected_name(part: str) -> bool:
    return part in NEVER_DELETE_EXACT_NAMES or any(
        part.startswith(prefix) for prefix in NEVER_DELETE_PREFIXES
    )


def _classify_path(path: str, repo_root: Optional[Path] = None) -> Finding:
    # A path reported by `git status --untracked-files=normal` ends with "/"
    # when the entry itself is a wholly-untracked directory. Strip it before
    # splitting so we don't get a spurious empty trailing segment.
    is_dir_hint = path.endswith("/")
    trimmed = path[:-1] if is_dir_hint else path
    parts = trimmed.split("/")
    top = parts[0]

    matched_index = next(
        (i for i, part in enumerate(parts) if _matches_protected_name(part)), None
    )

    if matched_index is not None:
        matched_prefix = "/".join(parts[: matched_index + 1])

        if matched_index < len(parts) - 1:
            # The matched segment has path components after it, so it must be
            # a directory (you can't have a path component "inside" a file).
            matched_is_dir = True
        elif repo_root is not None:
            # The matched segment is the last component of the reported
            # entry - check the filesystem to be sure whether it's a file
            # or a directory (e.g. a top-level file that happens to match a
            # protected prefix, like "OptiTrack_summary.csv").
            matched_is_dir = (repo_root / matched_prefix).is_dir()
        else:
            matched_is_dir = is_dir_hint

        suffix = "/" if matched_is_dir else ""
        return Finding(
            category=Category.GITIGNORE_CANDIDATE,
            description=(
                f"'{path}' is untracked but lives under a data/recording "
                "directory - never delete, only stop tracking as noise."
            ),
            command=f"echo {shlex.quote(matched_prefix + suffix)} >> .gitignore",
            source="Phase 1: Cruft",
        )

    if top in NEEDS_REVIEW_TOP_NAMES:
        return Finding(
            category=Category.NEEDS_REVIEW,
            description=f"'{path}' is untracked historical/vendored content - review before deleting.",
            command=f"rm -rf {shlex.quote(path)}  # only after review",
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
            command=f"rm -rf {shlex.quote(path)}",
            source="Phase 1: Cruft",
        )

    return Finding(
        category=Category.NEEDS_REVIEW,
        description=f"'{path}' is untracked with no known pattern match - needs a human look.",
        command=f"git status -- {shlex.quote(path)}  # decide: add, gitignore, or delete",
        source="Phase 1: Cruft",
    )


def classify_untracked(repo_root: Path) -> list[Finding]:
    return [_classify_path(path, repo_root) for path in list_untracked(repo_root)]
