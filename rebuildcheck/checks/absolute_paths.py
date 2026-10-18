"""Check tracked/authored files for machine-specific absolute paths."""

from __future__ import annotations

import re
from pathlib import Path

from rebuildcheck.models import CheckResult

from .common import (
    as_repo_path,
    files_for_scan,
    read_bytes,
    relative_path,
    should_skip_content_scan,
)


NAME = "absolute_paths"
PRIORITY = 2
NO_FIX = "No action needed."

# Build the roots in pieces so this checker does not report its own pattern
# examples as a finding when it is itself committed and scanned.
UNIX_USER_ROOTS = (
    "home",
    "Users",
    "root",
)
UNIX_USER_PATH_RE = re.compile(
    r"(?<![\w/])/(?:" + "|".join(UNIX_USER_ROOTS) + r")[/\\][^\s\"'`<>]+",
    re.IGNORECASE,
)
WINDOWS_USER_PATH_RE = re.compile(
    r"(?<![\w])(?:[A-Za-z]):[/\\]+Users[/\\]+[^\s\"'`<>]+",
    re.IGNORECASE,
)
WSL_USER_PATH_RE = re.compile(
    r"(?<![\w/])/mnt/[A-Za-z][/\\]+Users[/\\]+[^\s\"'`<>]+",
    re.IGNORECASE,
)
HTTP_URL_RE = re.compile(r"(?i)\bhttps?://[^\s\"'`<>]+")


def _match_is_inside_http_url(line: str, start: int) -> bool:
    return any(match.start() <= start < match.end() for match in HTTP_URL_RE.finditer(line))


def _has_match_outside_http_url(
    pattern: re.Pattern[str], line: str
) -> bool:
    return any(
        not _match_is_inside_http_url(line, match.start())
        for match in pattern.finditer(line)
    )


def _finding_kind(line: str) -> str | None:
    if _has_match_outside_http_url(WINDOWS_USER_PATH_RE, line):
        return "windows-user-path"
    if _has_match_outside_http_url(WSL_USER_PATH_RE, line):
        return "wsl-user-path"
    if _has_match_outside_http_url(UNIX_USER_PATH_RE, line):
        return "unix-user-path"
    return None


def run(repo: Path) -> CheckResult:
    """Return whether scanned files avoid obvious user-specific paths."""

    if as_repo_path(repo) is None:
        return CheckResult(
            passed=False,
            name=NAME,
            reason="The inspected repository directory is missing or unreadable, so absolute-path checking could not run.",
            fix="Point rebuildcheck at an existing readable repository directory.",
            priority=PRIORITY,
            details={"tracked_files": False},
        )

    tracked, files = files_for_scan(repo)
    findings: list[dict[str, object]] = []
    unreadable: list[str] = []
    for path in sorted(files, key=lambda item: relative_path(repo, item)):
        if should_skip_content_scan(repo, path):
            continue
        data = read_bytes(path)
        relative = relative_path(repo, path)
        if data is None:
            unreadable.append(relative)
            continue
        if b"\x00" in data:
            continue
        text = data.decode("utf-8", errors="replace")
        for line_number, line in enumerate(text.splitlines(), start=1):
            kind = _finding_kind(line)
            if kind is not None:
                findings.append({"path": relative, "line": line_number, "kind": kind})
                if len(findings) >= 100:
                    break
        if len(findings) >= 100:
            break

    if findings:
        return CheckResult(
            passed=False,
            name=NAME,
            reason="A scanned file contains an obvious machine-specific absolute path.",
            fix="Replace user or workstation paths with repository-relative paths, environment variables, or configuration supplied at runtime.",
            priority=PRIORITY,
            details={"findings": findings, "tracked_files": tracked},
        )
    if unreadable:
        return CheckResult(
            passed=False,
            name=NAME,
            reason="Some scanned files could not be read, so absolute-path checking was incomplete.",
            fix="Make the listed files readable or remove them from the repository before running the rebuild check.",
            priority=PRIORITY,
            details={"unreadable": unreadable[:100], "tracked_files": tracked},
        )

    return CheckResult(
        passed=True,
        name=NAME,
        reason="No obvious user-specific absolute paths were found in scanned files.",
        fix=NO_FIX,
        priority=PRIORITY,
        details={"tracked_files": tracked, "files_scanned": len(files)},
    )
