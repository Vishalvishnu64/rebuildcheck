"""Check that a supported runtime has a concrete version pin."""

from __future__ import annotations

import json
import re
from pathlib import Path

from rebuildcheck.models import CheckResult

from .common import read_text, relative_path, root_file


NAME = "runtime_version"
PRIORITY = 2
NO_FIX = "No action needed."

# A major and minor component are enough for files such as .python-version and
# .nvmrc.  A lone major version or a range is deliberately not a pin.
PIN_RE = re.compile(r"^(?:v|python-|node-)?(\d+)\.(\d+)(?:\.(\d+))?(?:[-+][0-9A-Za-z.-]+)?$")

RUNTIME_FILES = (
    ".python-version",
    ".nvmrc",
    ".node-version",
    ".ruby-version",
    ".java-version",
    ".go-version",
    ".elixir-version",
    ".erlang-version",
    ".terraform-version",
    ".rust-version",
    "runtime.txt",
    "rust-toolchain",
    "rust-toolchain.toml",
    "Dockerfile",
    ".tool-versions",
    "go.mod",
    "pyproject.toml",
    "package.json",
)


def _pin_from_token(value: str) -> str | None:
    token = value.strip().strip('"\'`')
    if not token or any(character.isspace() for character in token):
        return None
    match = PIN_RE.fullmatch(token)
    if match is None:
        return None
    return match.group(0)


def _first_value(text: str) -> str:
    for line in text.splitlines():
        candidate = line.strip()
        if candidate and not candidate.startswith("#"):
            return candidate
    return ""


def _parse_runtime_file(name: str, text: str) -> tuple[str, str] | None:
    lowered = name.casefold()

    if lowered == ".tool-versions":
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            parts = stripped.split()
            if len(parts) < 2 or parts[0].casefold() not in {
                "python",
                "nodejs",
                "node",
                "ruby",
                "java",
                "golang",
                "go",
                "rust",
                "terraform",
            }:
                continue
            pin = _pin_from_token(parts[1])
            if pin is not None:
                return pin, parts[0]
        return None

    if lowered == "go.mod":
        match = re.search(r"(?im)^\s*go\s+([^\s]+)", text)
        if match:
            pin = _pin_from_token(match.group(1))
            if pin is not None:
                return pin, "go"
        return None

    if lowered == "rust-toolchain.toml":
        match = re.search(r"(?im)^\s*channel\s*=\s*[\"']([^\"']+)[\"']", text)
        if match:
            pin = _pin_from_token(match.group(1))
            if pin is not None:
                return pin, "channel"
        return None

    if lowered == "dockerfile":
        match = re.search(
            r"(?im)^\s*FROM\s+(?:python|node|ruby|golang|go|rust|java):([^\s@]+)",
            text,
        )
        if match:
            pin = _pin_from_token(match.group(1))
            if pin is not None:
                return pin, "FROM"
        return None

    if lowered == "pyproject.toml":
        # Only an exact requires-python value is a pin; inequalities describe a
        # range and should not make an otherwise unpinned project pass.
        match = re.search(
            r"(?im)^\s*requires-python\s*=\s*[\"']\s*(?:==\s*)?([^\"']+)\s*[\"']",
            text,
        )
        if match and not re.search(r"[<>=!~,*|]", match.group(1).strip().replace("==", "")):
            pin = _pin_from_token(match.group(1))
            if pin is not None:
                return pin, "requires-python"
        return None

    if lowered == "package.json":
        try:
            document = json.loads(text)
            engines = document.get("engines", {}) if isinstance(document, dict) else {}
            node = engines.get("node") if isinstance(engines, dict) else None
            if isinstance(node, str):
                pin = _pin_from_token(node)
                if pin is not None:
                    return pin, "engines.node"
        except (TypeError, ValueError):
            return None
        return None

    value = _first_value(text)
    # runtime.txt commonly uses python-3.12.4; the generic parser accepts that
    # form as well as a plain version.
    pin = _pin_from_token(value)
    if pin is not None:
        return pin, "version"
    return None


def run(repo: Path) -> CheckResult:
    """Return whether at least one conventional runtime file pins a version."""

    valid: list[dict[str, str]] = []
    invalid: list[str] = []

    for name in RUNTIME_FILES:
        path = root_file(repo, name)
        if path is None:
            continue
        text = read_text(path)
        relative = relative_path(repo, path)
        if text is None:
            invalid.append(relative)
            continue
        parsed = _parse_runtime_file(path.name, text)
        if parsed is None:
            invalid.append(relative)
            continue
        pin, source = parsed
        valid.append({"path": relative, "version": pin, "source": source})

    if valid:
        return CheckResult(
            passed=True,
            name=NAME,
            reason="A conventional runtime-version file contains a concrete version pin.",
            fix=NO_FIX,
            priority=PRIORITY,
            details={"pins": valid},
        )

    if invalid:
        reason = "The runtime-version file(s) found do not contain a concrete version pin."
        fix = "Set a concrete major/minor (and preferably patch) version in .python-version, .tool-versions, .nvmrc, or the equivalent runtime file."
    else:
        reason = "No supported runtime-version file was found."
        fix = "Add a concrete runtime pin such as `3.12.4` in .python-version, .nvmrc, .tool-versions, or the equivalent file."

    return CheckResult(
        passed=False,
        name=NAME,
        reason=reason,
        fix=fix,
        priority=PRIORITY,
        details={"files": invalid},
    )
