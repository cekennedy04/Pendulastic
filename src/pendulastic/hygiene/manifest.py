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
