"""Check for a safe dotenv example and safe dotenv-like files."""

from __future__ import annotations

import re
from pathlib import Path

from rebuildcheck.models import CheckResult

from .common import (
    iter_environment_files,
    read_text,
    relative_path,
    root_file,
)


NAME = "environment_example"
PRIORITY = 1
NO_FIX = "No action needed."

ENV_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
SENSITIVE_KEY_RE = re.compile(
    r"(?:api[_-]?key|secret|token|password|passwd|private[_-]?key|"
    r"access[_-]?key|client[_-]?secret|credential)",
    re.IGNORECASE,
)
SECRET_PREFIX_RE = re.compile(
    r"^(?:sk_(?:live|test)_|rk_(?:live|test)_|gh[pousr]_\w+|"
    r"xox[baprs]-|AIza[0-9A-Za-z_-]{10,}|AKIA[0-9A-Z]{12,})",
    re.IGNORECASE,
)
JWT_RE = re.compile(r"^eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$")
URL_WITH_CREDENTIALS_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*://[^/\s:@]+:[^@\s]+@", re.IGNORECASE)

PLACEHOLDER_WORDS = frozenset(
    {
        "change-me",
        "changeme",
        "example",
        "example-value",
        "dummy",
        "insert-value",
        "placeholder",
        "replace-me",
        "set-me",
        "todo",
        "your-value",
        "your_api_key",
        "your-api-key",
        "your-token",
        "your-secret",
        "your-password",
        "your_value",
        "your-value-here",
    }
)
SAFE_LITERAL_VALUES = frozenset(
    {
        "0",
        "1",
        "false",
        "true",
        "null",
        "none",
        "development",
        "test",
        "testing",
        "production",
        "localhost",
        "127.0.0.1",
        "::1",
    }
)


def _unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def _is_placeholder(value: str) -> bool:
    stripped = _unquote(value).strip()
    lowered = stripped.casefold()
    if not stripped or lowered in PLACEHOLDER_WORDS:
        return True
    if (stripped.startswith("${") and stripped.endswith("}")) or (
        stripped.startswith("{{") and stripped.endswith("}}")
    ):
        return True
    if (stripped.startswith("<") and stripped.endswith(">")) or (
        stripped.startswith("[") and stripped.endswith("]")
    ):
        return True
    if lowered.startswith(("your-", "your_", "replace-", "replace_", "change-", "change_")):
        return True
    if lowered.startswith(
        ("placeholder-", "placeholder_", "example-", "example_")
    ):
        return True
    return False


def _is_safe_value(key: str, value: str) -> bool:
    stripped = _unquote(value).strip()
    lowered = stripped.casefold()
    if _is_placeholder(stripped) or lowered in SAFE_LITERAL_VALUES:
        return True
    if re.fullmatch(r"\d+(?:\.\d+)?", stripped):
        return True
    if lowered.startswith(("http://localhost", "https://localhost", "http://127.0.0.1", "https://127.0.0.1")):
        return True
    if ".example" in lowered or lowered.endswith(".test"):
        return True
    if SENSITIVE_KEY_RE.search(key):
        return False
    if SECRET_PREFIX_RE.search(stripped) or JWT_RE.fullmatch(stripped):
        return False
    if URL_WITH_CREDENTIALS_RE.match(stripped):
        return False
    # Long, punctuation-free random values are unlikely to be intentional
    # documentation.  This catches many copied tokens without rejecting normal
    # examples such as service names or local URLs.
    if len(stripped) >= 24 and " " not in stripped:
        classes = sum(
            (
                any(character.islower() for character in stripped),
                any(character.isupper() for character in stripped),
                any(character.isdigit() for character in stripped),
                any(not character.isalnum() for character in stripped),
            )
        )
        if classes >= 3:
            return False
    return True


def _parse_environment_file(text: str) -> list[dict[str, object]]:
    unsafe: list[dict[str, object]] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("export "):
            stripped = stripped[7:].lstrip()
        if "=" not in stripped:
            unsafe.append({"line": line_number, "kind": "malformed-entry"})
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        if not ENV_KEY_RE.fullmatch(key):
            unsafe.append({"line": line_number, "kind": "invalid-key"})
            continue
        if not _is_safe_value(key, value):
            unsafe.append({"line": line_number, "key": key, "kind": "non-placeholder-value"})
    return unsafe


def run(repo: Path) -> CheckResult:
    """Return whether a root .env.example exists and dotenv files are safe."""

    example = root_file(repo, ".env.example")
    if example is None:
        return CheckResult(
            passed=False,
            name=NAME,
            reason="The repository root does not contain .env.example.",
            fix="Add a root .env.example containing only blank or clearly marked placeholder values, and keep real credentials out of the repository.",
            priority=PRIORITY,
            details={"path": ".env.example"},
        )

    files = sorted(
        set(iter_environment_files(repo)),
        key=lambda path: relative_path(repo, path),
    )
    if example not in files:
        files.insert(0, example)

    unreadable: list[str] = []
    unsafe: list[dict[str, object]] = []
    checked: list[str] = []
    for path in files:
        text = read_text(path)
        relative = relative_path(repo, path)
        if text is None:
            unreadable.append(relative)
            continue
        checked.append(relative)
        for finding in _parse_environment_file(text):
            unsafe.append({"path": relative, **finding})

    if unreadable:
        return CheckResult(
            passed=False,
            name=NAME,
            reason="One or more dotenv-like files could not be read completely.",
            fix="Make every environment example readable and keep credentials in local, ignored files rather than committed files.",
            priority=PRIORITY,
            details={"files": unreadable[:100], "checked": checked[:100]},
        )

    if unsafe:
        return CheckResult(
            passed=False,
            name=NAME,
            reason="An environment file contains a non-placeholder or malformed entry.",
            fix="Replace credential values with blanks or placeholders such as ${YOUR_VARIABLE}, and remove any real secrets from environment files.",
            priority=PRIORITY,
            details={"findings": unsafe[:100], "checked": checked[:100]},
        )

    return CheckResult(
        passed=True,
        name=NAME,
        reason=".env.example exists and all dotenv-like files contain safe placeholder or non-secret values.",
        fix=NO_FIX,
        priority=PRIORITY,
        details={"files": checked[:100]},
    )
