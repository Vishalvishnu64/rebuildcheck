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
MAX_FINDINGS = 100

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


def _group_findings(findings: list[dict[str, object]]) -> list[dict[str, object]]:
    """Group absolute-path evidence by file with bounded line numbers."""

    grouped: dict[str, dict[str, object]] = {}
    for finding in findings:
        path = finding.get("path")
        line = finding.get("line")
        kind = finding.get("kind")
        if not isinstance(path, str) or not isinstance(line, int):
            continue
        entry = grouped.setdefault(path, {"lines": [], "kinds": []})
        lines = entry["lines"]
        if isinstance(lines, list) and line not in lines:
            lines.append(line)
        kinds = entry["kinds"]
        if isinstance(kinds, list) and isinstance(kind, str) and kind not in kinds:
            kinds.append(kind)

    result: list[dict[str, object]] = []
    for path in sorted(grouped):
        entry = grouped[path]
        lines = sorted(
            line for line in entry.get("lines", []) if isinstance(line, int)
        )
        kinds = sorted(
            kind for kind in entry.get("kinds", []) if isinstance(kind, str)
        )
        if not lines or not kinds:
            continue
        result.append(
            {
                "path": path,
                # Keep the first line for existing human evidence rendering;
                # ``lines`` and ``more`` carry the complete bounded evidence.
                "line": lines[0],
                "lines": lines[:3],
                "more": max(0, len(lines) - 3),
                "kind": kinds[0] if len(kinds) == 1 else "multiple",
                "kinds": kinds,
            }
        )
    return result


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
        if len({finding.get("path") for finding in findings}) >= MAX_FINDINGS:
            break

    findings = _group_findings(findings)[:MAX_FINDINGS]

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
