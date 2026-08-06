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
