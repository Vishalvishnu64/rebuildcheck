"""Check that the README has setup, run, and test sections."""

from __future__ import annotations

import re
from pathlib import Path

from rebuildcheck.models import CheckResult

from .common import find_readme, read_text, relative_path


NAME = "readme_sections"
PRIORITY = 2
NO_FIX = "No action needed."
MARKDOWN_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$")
SETEXT_RE = re.compile(r"^\s*(?:=+|-+)\s*$")
HTML_HEADING_RE = re.compile(r"<h[1-6][^>]*>(.*?)</h[1-6]>", re.IGNORECASE)


def _heading_texts(text: str) -> list[str]:
    headings: list[str] = []
    lines = text.splitlines()
    in_fence = False
    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue

        markdown = MARKDOWN_HEADING_RE.match(line)
        if markdown:
            headings.append(markdown.group(1))
            continue
        html = HTML_HEADING_RE.search(line)
        if html:
            headings.append(html.group(1))
            continue
        if index + 1 < len(lines) and line.strip() and SETEXT_RE.match(lines[index + 1]):
            headings.append(line.strip())
    return headings


def _normalise(value: str) -> str:
    value = re.sub(r"[`*_\[\]()]", " ", value)
    value = re.sub(r"\s+", " ", value)
    return value.strip().casefold()


def run(repo: Path) -> CheckResult:
    """Return whether the root README contains the required headings."""

    readme = find_readme(repo)
    if readme is None:
        return CheckResult(
            passed=False,
            name=NAME,
            reason="No README was found for documenting repository usage.",
            fix="Add a root README with separate Setup, Run/Usage, and Test/Testing headings.",
            priority=PRIORITY,
            details={"missing": ["setup", "run", "test"]},
        )

    text = read_text(readme)
    relative = relative_path(repo, readme)
    if text is None:
        return CheckResult(
            passed=False,
            name=NAME,
            reason="The README exists but could not be read as text.",
            fix="Make the README readable UTF-8 text and add Setup, Run/Usage, and Test/Testing headings.",
            priority=PRIORITY,
            details={"path": relative, "missing": ["setup", "run", "test"]},
        )

    headings = [_normalise(value) for value in _heading_texts(text)]
    found = {
        "setup": any(
            "setup" in heading
            or "install" in heading
            or "setting up" in heading
            or "getting started" in heading
            for heading in headings
        ),
        "run": any(
            re.search(r"\b(?:run(?:ning)?|usage|start(?:ing)?|launch|execute)\b", heading)
            for heading in headings
        ),
        "test": any(
            re.search(r"\b(?:tests?|testing|verify|verification|check|checks)\b", heading)
            for heading in headings
        ),
    }
    missing = [name for name, present in found.items() if not present]

    if missing:
        return CheckResult(
            passed=False,
            name=NAME,
            reason="The README is missing one or more required documentation headings.",
            fix="Add clearly named Setup, Run (or Usage), and Test (or Testing) headings with the relevant commands and steps.",
            priority=PRIORITY,
            details={"path": relative, "missing": missing},
        )

    return CheckResult(
        passed=True,
        name=NAME,
        reason="The README contains setup, run, and test documentation headings.",
        fix=NO_FIX,
        priority=PRIORITY,
        details={"path": relative, "headings": headings},
    )
