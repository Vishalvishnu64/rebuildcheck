"""Check that the repository root has a usable Dockerfile base image."""

from __future__ import annotations

import re
from pathlib import Path

from rebuildcheck.models import CheckResult

from .common import read_text, relative_path, root_file


NAME = "dockerfile"
PRIORITY = 2
NO_FIX = "No action needed."
FROM_RE = re.compile(r"^\s*FROM\s+\S+", re.IGNORECASE)


def run(repo: Path) -> CheckResult:
    """Return whether a root Dockerfile contains at least one FROM line."""

    path = root_file(repo, "Dockerfile")
    if path is None:
        return CheckResult(
            passed=False,
            name=NAME,
            reason="The repository root does not contain a Dockerfile.",
            fix="Add a root Dockerfile with a pinned or otherwise explicit base image using a `FROM` instruction.",
            priority=PRIORITY,
            details={"path": "Dockerfile"},
        )

    text = read_text(path)
    relative = relative_path(repo, path)
    if text is None:
        return CheckResult(
            passed=False,
            name=NAME,
            reason="The root Dockerfile exists but could not be read as text.",
            fix="Make Dockerfile readable and store its build instructions as UTF-8 text.",
            priority=PRIORITY,
            details={"path": relative},
        )

    from_lines: list[int] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        without_comment = line.split("#", 1)[0]
        if FROM_RE.match(without_comment):
            from_lines.append(line_number)

    if not from_lines:
        return CheckResult(
            passed=False,
            name=NAME,
            reason="The root Dockerfile has no usable FROM instruction.",
            fix="Add a base image line such as `FROM python:3.12` near the start of Dockerfile.",
            priority=PRIORITY,
            details={"path": relative},
        )

    return CheckResult(
        passed=True,
        name=NAME,
        reason="The root Dockerfile contains a FROM instruction for a container base image.",
        fix=NO_FIX,
        priority=PRIORITY,
        details={"path": relative, "from_lines": from_lines},
    )
