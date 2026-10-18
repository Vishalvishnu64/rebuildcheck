"""Check common Windows-to-Linux portability hazards."""

from __future__ import annotations

import re
from pathlib import Path, PurePosixPath

from rebuildcheck.models import CheckResult

from .common import (
    as_repo_path,
    files_for_scan,
    find_readme,
    iter_files,
    read_bytes,
    relative_path,
    run_git,
    run_git_bytes,
)

NAME = "portability"
PRIORITY = 2
NO_FIX = "No action needed."
MAX_FINDINGS = 100

_SHELL_SUFFIXES = frozenset(
    {".ash", ".bash", ".csh", ".dash", ".fish", ".ksh", ".sh", ".tcsh", ".zsh"}
)
_SHELL_INTERPRETERS = frozenset(
    {suffix[1:] for suffix in _SHELL_SUFFIXES} | {"sh"}
)
_MAKEFILE_NAMES = frozenset({"bsdmakefile", "gnumakefile", "makefile"})


def _is_shell_script(path: Path, data: bytes) -> bool:
    """Return whether ``path`` conventionally identifies a shell script."""

    if path.suffix.casefold() in _SHELL_SUFFIXES:
        return True
    first_line = data.split(b"\n", 1)[0]
    if not first_line.startswith(b"#!"):
        return False
    interpreter_parts = first_line[2:].strip().rstrip(b"\r").split()
    if not interpreter_parts:
        return False
    interpreter = interpreter_parts[0]
    interpreter_name = interpreter.rsplit(b"/", 1)[-1].decode(
        "ascii", errors="ignore"
    ).casefold()
    if interpreter_name == "env":
        for part in interpreter_parts[1:]:
            if part in {b"--", b"-S"} or part.startswith(b"-"):
                continue
            interpreter_name = part.rsplit(b"/", 1)[-1].decode(
                "ascii", errors="ignore"
            ).casefold()
            break
    return interpreter_name in _SHELL_INTERPRETERS


def _is_portability_file(path: Path, data: bytes) -> bool:
    """Return whether the file is a shell script, Makefile, or Dockerfile."""

    name = path.name.casefold()
    if name in _MAKEFILE_NAMES or name.startswith("makefile.") or name.endswith(
        ".mk"
    ):
        return True
    if name == "dockerfile" or name.startswith("dockerfile.") or name.endswith(
        ".dockerfile"
    ):
        return True
    return _is_shell_script(path, data)


def _line_numbers_with_crlf(data: bytes) -> list[int]:
    """Return one-based line numbers whose terminator contains CRLF bytes."""

    return [
        line_number
        for line_number, line in enumerate(data.splitlines(keepends=True), start=1)
        if b"\r\n" in line
    ]


def _shell_fix(path: str) -> str:
    return (
        f"Convert `{path}` from CRLF to LF line endings, then save and commit the "
        "normalized file."
    )


def _mode_fix(path: str) -> str:
    return (
        f"Mark `{path}` executable in Git with `git update-index --chmod=+x -- "
        f"{path}`, then commit the mode change."
    )


def _path_fix(mentioned: str, actual: str) -> str:
    return (
        f"Change the README reference `{mentioned}` to the exact repository path "
        f"`{actual}`."
    )


def _finding(
    *,
    path: str,
    line: int,
    kind: str,
    fix: str,
    **extra: object,
) -> dict[str, object]:
    """Build the common JSON-friendly shape used by all findings."""

    return {"path": path, "line": line, "kind": kind, "fix": fix, **extra}


def _group_findings(findings: list[dict[str, object]]) -> list[dict[str, object]]:
    """Group all portability evidence into one finding per file."""

    grouped: dict[str, dict[str, object]] = {}
    for finding in findings:
        path = finding.get("path")
        line = finding.get("line")
        kind = finding.get("kind")
        if not isinstance(path, str) or not isinstance(line, int):
            continue
        entry = grouped.setdefault(path, {"lines": [], "kinds": [], "fixes": []})
        lines = entry["lines"]
        affected_lines = finding.get("affected_lines")
        if isinstance(lines, list):
            candidates = (
                affected_lines
                if isinstance(affected_lines, list)
                else [line]
            )
            for candidate in candidates:
                if isinstance(candidate, int) and candidate not in lines:
                    lines.append(candidate)
        kinds = entry["kinds"]
        if isinstance(kinds, list) and isinstance(kind, str) and kind not in kinds:
            kinds.append(kind)
        fixes = entry["fixes"]
        fix = finding.get("fix")
        if isinstance(fixes, list) and isinstance(fix, str) and fix not in fixes:
            fixes.append(fix)
        for key in ("mentioned", "actual"):
            value = finding.get(key)
            if value is not None:
                entry.setdefault(key, value)

    result: list[dict[str, object]] = []
    for path in sorted(grouped):
        entry = grouped[path]
        lines = sorted(
            line for line in entry.get("lines", []) if isinstance(line, int)
        )
        kinds = sorted(
            kind for kind in entry.get("kinds", []) if isinstance(kind, str)
        )
        fixes = [fix for fix in entry.get("fixes", []) if isinstance(fix, str)]
        if not lines or not kinds or not fixes:
            continue
        finding: dict[str, object] = {
            "path": path,
            "line": lines[0],
            "line_count": len(lines),
            # Keep the first concrete kind for callers that historically
            # filtered on ``kind``; ``kinds`` records every issue in the file.
            "kind": kinds[0],
            "kinds": kinds,
            "fix": " ".join(fixes),
        }
        for key in ("mentioned", "actual"):
            if key in entry:
                finding[key] = entry[key]
        result.append(finding)
    return result


def _index_modes(repo: Path) -> dict[str, set[str]] | None:
    """Return Git index modes keyed by repository-relative POSIX path."""

    returncode, output, _error = run_git(repo, "ls-files", "--stage", "-z", "--")
    if returncode != 0:
        return None

    modes: dict[str, set[str]] = {}
    for record in output.split("\x00"):
        metadata, separator, raw_path = record.partition("\t")
        if not separator or not raw_path:
            continue
        fields = metadata.split()
        if not fields:
            continue
        try:
            relative = PurePosixPath(raw_path)
            if relative.is_absolute() or ".." in relative.parts:
                continue
            normalized = relative.as_posix()
        except (TypeError, ValueError):
            continue
        modes.setdefault(normalized, set()).add(fields[0])
    return modes


def _index_eol(repo: Path) -> dict[str, str] | None:
    """Return Git index EOL states keyed by repository-relative path."""

    returncode, output, _error = run_git(repo, "ls-files", "--eol", "-z", "--")
    if returncode != 0:
        return None

    eol_states: dict[str, str] = {}
    for record in output.split("\x00"):
        metadata, separator, raw_path = record.partition("\t")
        if not separator or not raw_path:
            continue
        fields = metadata.split()
        if not fields:
            continue
        try:
            relative = PurePosixPath(raw_path)
            if relative.is_absolute() or ".." in relative.parts:
                continue
            normalized = relative.as_posix()
        except (TypeError, ValueError):
            continue
        eol_states[normalized] = fields[0]
    return eol_states


def _index_data(repo: Path, relative: str) -> bytes | None:
    """Return a tracked file's index contents when Git can provide them."""

    returncode, output, _error = run_git_bytes(
        repo, "cat-file", "blob", f":{relative}"
    )
    if returncode != 0:
        return None
    return output


def _mode_for_path(path: Path, repo: Path, modes: dict[str, set[str]]) -> set[str]:
    """Look up a file's index mode while keeping path handling defensive."""

    relative = relative_path(repo, path)
    result = modes.get(relative)
    if result is not None:
        return result
    folded = relative.casefold()
    for indexed_path, indexed_modes in modes.items():
        if indexed_path.casefold() == folded:
            return indexed_modes
    return set()


def _existing_paths(repo: Path) -> list[str]:
    """Return existing regular-file paths in stable repository order."""

    paths: set[str] = set()
    for path in iter_files(repo):
        relative = relative_path(repo, path)
        if relative != ".":
            paths.add(relative)
    return sorted(paths, key=lambda value: (value.casefold(), value))


def _path_pattern(relative: str) -> re.Pattern[str] | None:
    """Compile a case-insensitive README matcher for one repository path."""

    normalized = relative.replace("\\", "/")
    components = normalized.split("/")
    if not normalized or any(not component for component in components):
        return None
    escaped = r"[\\/]".join(re.escape(component) for component in components)
    try:
        return re.compile(
            rf"(?<![A-Za-z0-9_.-])(?:\.[\\/])?{escaped}(?![A-Za-z0-9_.-])",
            re.IGNORECASE,
        )
    except (re.error, TypeError, ValueError):
        return None


def _normalise_mentioned_path(value: str) -> str:
    """Normalize optional ``./`` and slash spelling for exact comparison."""

    normalized = value.replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return re.sub(r"/+", "/", normalized)


def _readme_case_findings(
    repo: Path, readme: Path
) -> tuple[list[dict[str, object]], bool]:
    """Find README references whose spelling differs only by case."""

    data = read_bytes(readme)
    if data is None:
        return [], False
    if b"\x00" in data:
        return [], True
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        text = data.decode("utf-8", errors="replace")

    existing = _existing_paths(repo)
    findings: list[dict[str, object]] = []
    seen: set[tuple[int, str, str]] = set()
    for actual in sorted(
        existing, key=lambda value: (-len(value), value.casefold(), value)
    ):
        pattern = _path_pattern(actual)
        if pattern is None:
            continue
        for match in pattern.finditer(text):
            mentioned = match.group(0)
            normalized = _normalise_mentioned_path(mentioned)
            if normalized == actual or normalized.casefold() != actual.casefold():
                continue
            line = text.count("\n", 0, match.start()) + 1
            key = (line, mentioned, actual)
            if key in seen:
                continue
            seen.add(key)
            findings.append(
                _finding(
                    path=relative_path(repo, readme),
                    line=line,
                    kind="case-mismatched-path",
                    fix=_path_fix(mentioned, actual),
                    mentioned=mentioned,
                    actual=actual,
                )
            )
            if len(findings) >= MAX_FINDINGS:
                return findings, False
    return findings, False


def run(repo: Path) -> CheckResult:
    """Return whether common Windows checkout artifacts are absent."""

    root = as_repo_path(repo)
    if root is None:
        return CheckResult(
            passed=False,
            name=NAME,
            reason="The inspected repository directory is missing or unreadable, so portability checking could not run.",
            fix="Point rebuildcheck at an existing readable repository directory.",
            priority=PRIORITY,
            details={"tracked_files": False},
        )

    tracked, files = files_for_scan(root)
    eol_states = _index_eol(root) if tracked else None
    findings: list[dict[str, object]] = []
    unreadable: list[str] = []

    for path in sorted(files, key=lambda item: relative_path(root, item)):
        data = read_bytes(path)
        relative = relative_path(root, path)
        if data is None:
            unreadable.append(relative)
            continue
        if tracked:
            if eol_states is None or eol_states.get(relative) != "i/crlf":
                continue
            indexed_data = _index_data(root, relative)
            if indexed_data is None:
                continue
            scan_data = indexed_data
        else:
            scan_data = data
        # The NUL guard comes before filename classification so an image named
        # Dockerfile or ``.sh`` is never reported as a text file.
        if b"\x00" in scan_data:
            continue
        if not _is_portability_file(path, scan_data):
            continue

        crlf_lines = _line_numbers_with_crlf(scan_data)
        if crlf_lines:
            findings.append(
                _finding(
                    path=relative,
                    line=crlf_lines[0],
                    kind="crlf-line-ending",
                    fix=_shell_fix(relative),
                    line_count=len(crlf_lines),
                    affected_lines=crlf_lines,
                )
            )
        if len(findings) >= MAX_FINDINGS:
            break

    modes = _index_modes(root) if tracked else None
    if modes is not None and len(findings) < MAX_FINDINGS:
        for path in sorted(files, key=lambda item: relative_path(root, item)):
            data = read_bytes(path)
            relative = relative_path(root, path)
            if data is None or b"\x00" in data:
                continue
            first_line = data.split(b"\n", 1)[0]
            if not first_line.startswith(b"#!"):
                continue
            if "100755" in _mode_for_path(path, root, modes):
                continue
            findings.append(
                _finding(
                    path=relative,
                    line=1,
                    kind="shebang-not-executable",
                    fix=_mode_fix(relative),
                )
            )
            if len(findings) >= MAX_FINDINGS:
                break

    readme = find_readme(root)
    binary_readme = False
    if readme is not None and len(findings) < MAX_FINDINGS:
        readme_findings, binary_readme = _readme_case_findings(root, readme)
        findings.extend(readme_findings[: MAX_FINDINGS - len(findings)])

    findings = _group_findings(findings)[:MAX_FINDINGS]

    if findings:
        return CheckResult(
            passed=False,
            name=NAME,
            reason="The repository contains Windows-to-Linux portability hazards.",
            fix="Apply the concrete fix listed with each finding, then rerun the portability check.",
            priority=PRIORITY,
            details={
                "findings": findings,
                "tracked_files": tracked,
                "files_scanned": len(files),
            },
        )

    if unreadable:
        return CheckResult(
            passed=False,
            name=NAME,
            reason="Some scanned files could not be read, so portability checking was incomplete.",
            fix="Make the listed files readable or remove them from the repository before rerunning the portability check.",
            priority=PRIORITY,
            details={
                "unreadable": unreadable[:MAX_FINDINGS],
                "tracked_files": tracked,
                "files_scanned": len(files),
            },
        )

    readme_detail = (
        "binary-ignored" if binary_readme else ("checked" if readme else "missing")
    )
    return CheckResult(
        passed=True,
        name=NAME,
        reason="No Windows-to-Linux portability hazards were found.",
        fix=NO_FIX,
        priority=PRIORITY,
        details={
            "tracked_files": tracked,
            "files_scanned": len(files),
            "readme": readme_detail,
        },
    )


__all__ = ["NAME", "PRIORITY", "run"]
