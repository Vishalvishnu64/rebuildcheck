"""Check tracked/authored files for high-signal committed secrets."""

from __future__ import annotations

import re
from pathlib import Path

from rebuildcheck.models import CheckResult

from .common import as_repo_path, files_for_scan, read_bytes, relative_path


NAME = "committed_secrets"
PRIORITY = 1
NO_FIX = "No action needed."

PRIVATE_KEY_RE = re.compile(r"-----BEGIN [A-Z0-9 _-]*PRIVATE KEY-----")
PROVIDER_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("aws-access-key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("api-key", re.compile(r"\bsk-" + r"[A-Za-z0-9]{20,}\b")),
    ("github-token", re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{20,}\b")),
    ("slack-token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{16,}\b")),
    ("google-api-key", re.compile(r"\bAIza[0-9A-Za-z_-]{30,}\b")),
    ("stripe-key", re.compile(r"\b(?:sk|rk)_(?:live|test)_[A-Za-z0-9]{12,}\b")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b")),
)
SENSITIVE_ASSIGNMENT_RE = re.compile(
    r"(?ix)\b(?:api[_-]?key|secret(?:[_-]?key)?|token|password|passwd|"
    r"private[_-]?key|access[_-]?key|client[_-]?secret|credentials?)\b"
    r"\s*(?:=|:)\s*(?:[\"'](?P<quoted>[^\"']+)[\"']|(?P<bare>[^\s,;]+))"
)
BEARER_RE = re.compile(r"(?i)\bBearer\s+([A-Za-z0-9_./+=-]{16,})")
URL_CREDENTIAL_RE = re.compile(r"(?i)\b[A-Za-z][A-Za-z0-9+.-]*://[^/\s:@]+:([^@\s]+)@")

PLACEHOLDER_WORDS = frozenset(
    {
        "",
        "example",
        "dummy",
        "placeholder",
        "changeme",
        "change-me",
        "replace-me",
        "your-token",
        "your-secret",
        "your-password",
        "your-api-key",
        "secret",
        "password",
        "token",
        "value",
        "null",
        "none",
        "false",
        "true",
    }
)


def _looks_placeholder(value: str) -> bool:
    stripped = value.strip().strip('"\'`')
    lowered = stripped.casefold()
    return (
        not stripped
        or lowered in PLACEHOLDER_WORDS
        or (stripped.startswith("${") and stripped.endswith("}"))
        or (stripped.startswith("{{") and stripped.endswith("}}"))
        or (stripped.startswith("<") and stripped.endswith(">"))
        or "example" in lowered
        or lowered.startswith(("your-", "your_", "change-", "replace-"))
    )


def _assignment_is_secret(match: re.Match[str], key: str) -> bool:
    value = match.group("quoted") or match.group("bare") or ""
    if _looks_placeholder(value):
        return False
    # Code often assigns a variable or calls a loader, which is not a literal
    # secret.  Quoted values and sufficiently substantial bare values are the
    # useful high-signal cases here.
    if match.group("quoted") is None and re.fullmatch(
        r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*\s*\(.*\)",
        value,
    ):
        return False
    if match.group("quoted") is None and value.casefold() in {key.casefold(), "env", "os.environ", "getenv"}:
        return False
    if len(value) < 4 and not re.search(r"private|password|secret", key, re.IGNORECASE):
        return False
    return True


def _scan_text(text: str, relative: str) -> list[dict[str, object]]:
    findings: list[dict[str, object]] = []
    seen: set[tuple[int, str]] = set()
    for line_number, line in enumerate(text.splitlines(), start=1):
        if PRIVATE_KEY_RE.search(line):
            seen.add((line_number, "private-key"))
        for kind, pattern in PROVIDER_PATTERNS:
            if pattern.search(line):
                seen.add((line_number, kind))
        bearer = BEARER_RE.search(line)
        if bearer is not None and not _looks_placeholder(bearer.group(1)):
            seen.add((line_number, "bearer-token"))
        url_credentials = URL_CREDENTIAL_RE.search(line)
        if url_credentials is not None and not _looks_placeholder(url_credentials.group(1)):
            seen.add((line_number, "url-credential"))
        for match in SENSITIVE_ASSIGNMENT_RE.finditer(line):
            key_match = re.search(
                r"(?i)(api[_-]?key|secret(?:[_-]?key)?|token|password|passwd|private[_-]?key|access[_-]?key|client[_-]?secret|credentials?)",
                match.group(0),
            )
            key = key_match.group(1) if key_match else "secret"
            if _assignment_is_secret(match, key):
                seen.add((line_number, "sensitive-assignment"))
    for line_number, kind in sorted(seen):
        findings.append({"path": relative, "line": line_number, "kind": kind})
    return findings


def run(repo: Path) -> CheckResult:
    """Return whether tracked/authored text contains obvious secret material."""

    if as_repo_path(repo) is None:
        return CheckResult(
            passed=False,
            name=NAME,
            reason="The inspected repository directory is missing or unreadable, so secret scanning could not run.",
            fix="Point rebuildcheck at an existing readable repository directory.",
            priority=PRIORITY,
            details={"tracked_files": False},
        )

    tracked, files = files_for_scan(repo)
    findings: list[dict[str, object]] = []
    unreadable: list[str] = []
    for path in sorted(files, key=lambda item: relative_path(repo, item)):
        data = read_bytes(path)
        relative = relative_path(repo, path)
        if data is None:
            unreadable.append(relative)
            continue
        if b"\x00" in data:
            continue
        text = data.decode("utf-8", errors="replace")
        findings.extend(_scan_text(text, relative))
        if len(findings) >= 100:
            findings = findings[:100]
            break

    if findings:
        return CheckResult(
            passed=False,
            name=NAME,
            reason="A scanned file contains a value matching an obvious secret pattern.",
            fix="Revoke and remove the credential, replace it with an environment-variable placeholder, and commit only the sanitized file.",
            priority=PRIORITY,
            details={"findings": findings, "tracked_files": tracked},
        )
    if unreadable:
        return CheckResult(
            passed=False,
            name=NAME,
            reason="Some scanned files could not be read, so secret scanning was incomplete.",
            fix="Make the listed files readable or remove them from the repository before rerunning the check.",
            priority=PRIORITY,
            details={"unreadable": unreadable[:100], "tracked_files": tracked},
        )

    return CheckResult(
        passed=True,
        name=NAME,
        reason="No obvious secrets were found in scanned files.",
        fix=NO_FIX,
        priority=PRIORITY,
        details={"tracked_files": tracked, "files_scanned": len(files)},
    )
