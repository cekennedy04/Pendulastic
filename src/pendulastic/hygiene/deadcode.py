import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from pendulastic.hygiene.models import Category, Finding

_VULTURE_LINE_RE = re.compile(
    r"^(?P<path>.+):(?P<line>\d+): (?P<message>.+) \((?P<confidence>\d+)% confidence\)$"
)

# Directories that must never be scanned: virtualenvs, dependency caches, VCS
# internals, and (critically) nested git worktrees - each linked worktree is
# a full copy of the repo, so scanning them multiplies findings by however
# many worktrees exist.
DEFAULT_EXCLUDE_PATTERNS = ".venv,venv,node_modules,.git,.worktrees,.claude,build,dist,_deprecated"

# Generous but bounded - a hung/runaway vulture process must not hang the
# whole nightly audit forever.
VULTURE_TIMEOUT_SECONDS = 300

# vulture's own exit codes (see vulture.utils.ExitCode): 0 = no dead code
# found, 3 = dead code found. Both are successful runs. Anything else (1 =
# invalid input, 2 = invalid cmdline arguments) is a real failure.
_VULTURE_SUCCESS_RETURN_CODES = (0, 3)


@dataclass
class DeadCodeResult:
    high_confidence: list[Finding]
    low_confidence: list[Finding]
    whitelist_missing: bool
    failed: bool = False
    error: Optional[str] = None


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
    failure: dict = {}

    if runner is None:
        def runner(cmd: list[str]) -> str:
            try:
                result = subprocess.run(
                    [sys.executable, "-m", "vulture", *cmd[1:]],
                    cwd=repo_root,
                    capture_output=True,
                    text=True,
                    timeout=VULTURE_TIMEOUT_SECONDS,
                )
            except subprocess.TimeoutExpired:
                failure["error"] = f"vulture timed out after {VULTURE_TIMEOUT_SECONDS}s"
                return ""

            if result.returncode not in _VULTURE_SUCCESS_RETURN_CODES:
                failure["error"] = (
                    result.stderr.strip()
                    or f"vulture exited with code {result.returncode}"
                )
                return ""

            return result.stdout

    missing = not whitelist_path(repo_root).exists()
    cmd = ["vulture", ".", "--exclude", DEFAULT_EXCLUDE_PATTERNS]
    if not missing:
        cmd.append(".vulture_whitelist.py")

    output = runner(cmd)

    if "error" in failure:
        return DeadCodeResult(
            high_confidence=[],
            low_confidence=[],
            whitelist_missing=missing,
            failed=True,
            error=failure["error"],
        )

    high, low = parse_vulture_output(output, min_confidence=min_confidence)
    return DeadCodeResult(high_confidence=high, low_confidence=low, whitelist_missing=missing)
