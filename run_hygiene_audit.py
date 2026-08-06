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
