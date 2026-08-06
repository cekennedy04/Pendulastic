#!/usr/bin/env python
"""Nightly hygiene audit - Phases 1-2 (mechanical, deterministic).

Phase 3 (spec-to-code drift) is NOT performed by this script - it requires
semantic reasoning and is done by the calling agent, which appends its own
[Doc Drift Fix] items starting at the NEXT_ITEM_NUMBER this script reports.
"""

import subprocess
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


def detect_default_branch(repo_root: Path) -> str:
    """Best-effort detection of the repo's default branch.

    Tries the origin remote's HEAD symref first (the authoritative source
    when a remote is configured), then falls back to checking for a local
    "main" or "master" branch, and finally defaults to "main" if neither
    check is conclusive. Never raises.
    """
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_root), "symbolic-ref", "refs/remotes/origin/HEAD"],
            capture_output=True,
            text=True,
            check=True,
        )
        ref = result.stdout.strip()
        prefix = "refs/remotes/origin/"
        if ref.startswith(prefix) and ref != prefix:
            return ref[len(prefix):]
    except (subprocess.CalledProcessError, OSError):
        pass

    for candidate in ("main", "master"):
        try:
            result = subprocess.run(
                ["git", "-C", str(repo_root), "show-ref", "--verify", "--quiet", f"refs/heads/{candidate}"],
                capture_output=True,
                text=True,
            )
        except OSError:
            break
        if result.returncode == 0:
            return candidate

    return "main"


def build_mechanical_report(
    repo_root: Path,
    vulture_runner: Optional[Callable[[list[str]], str]] = None,
) -> str:
    findings = []
    incomplete_notes: list[str] = []

    try:
        main_branch = detect_default_branch(repo_root)
        findings.extend(classify_worktrees(repo_root, main_branch=main_branch))
    except subprocess.CalledProcessError as exc:
        incomplete_notes.append(f"Phase 1 INCOMPLETE: worktree detection failed - {exc}")

    try:
        findings.extend(classify_untracked(repo_root))
    except subprocess.CalledProcessError as exc:
        incomplete_notes.append(f"Phase 1 INCOMPLETE: untracked-file scan failed - {exc}")

    deadcode = run_vulture(repo_root, runner=vulture_runner)
    if deadcode.failed:
        incomplete_notes.append(f"Phase 2 INCOMPLETE: {deadcode.error}")
    findings.extend(deadcode.high_confidence)

    report_date = date.today().isoformat()
    rendered = render_manifest(findings, report_date, whitelist_missing=deadcode.whitelist_missing)

    findings_body = rendered.markdown
    if incomplete_notes:
        findings_body = "\n".join(incomplete_notes) + "\n\n" + findings_body

    sections = [
        "=== MECHANICAL FINDINGS (Phases 1-2) ===",
        findings_body,
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
