"""Check that the README documents a command for running tests."""

from __future__ import annotations

import json
import re
from pathlib import Path

from rebuildcheck.models import CheckResult
from rebuildcheck.project_type import detect_project_type

from .common import find_readme, read_text, relative_path, root_file


NAME = "test_command"
PRIORITY = 2
NO_FIX = "No action needed."

# Keep command matching deliberately conservative.  It is better to return a
# short, executable command than to treat a random mention of the word "test"
# as the repository's test command.
COMMAND_PATTERNS: tuple[tuple[re.Pattern[str], str | None], ...] = (
    (re.compile(r"(?<![\w-])make\s+(?:test|check)(?![\w-])", re.IGNORECASE), None),
    (re.compile(r"(?<![\w-])just\s+test(?![\w-])", re.IGNORECASE), None),
    (re.compile(r"(?<![\w-])(?:python(?:3(?:\.\d+)?)?|pypy3?)\s+-m\s+unittest\b[^`\n;&|]*", re.IGNORECASE), None),
    (re.compile(r"(?<![\w-])(?:python(?:3(?:\.\d+)?)?|pypy3?)\s+-m\s+pytest\b[^`\n;&|]*", re.IGNORECASE), None),
    (re.compile(r"(?<![\w-])pytest(?:\s+-[^`\n;&|]+)?", re.IGNORECASE), None),
    (re.compile(r"(?<![\w-])(?:npm|yarn|pnpm|bun)\s+(?:run\s+)?test\b", re.IGNORECASE), None),
    (re.compile(r"(?<![\w-])cargo\s+test\b[^`\n;&|]*", re.IGNORECASE), None),
    (re.compile(r"(?<![\w-])go\s+test\b[^`\n;&|]*", re.IGNORECASE), None),
    (re.compile(r"(?<![\w-])flutter\s+test\b[^`\n;&|]*", re.IGNORECASE), None),
    (re.compile(r"(?<![\w-])dart\s+test\b[^`\n;&|]*", re.IGNORECASE), None),
    (re.compile(r"(?<![\w-])(?:dotnet\s+test|mvn\s+test|gradle\s+test|mix\s+test)\b[^`\n;&|]*", re.IGNORECASE), None),
    (re.compile(r"(?<![\w-])(?:\.\/)?gradlew\s+test\b[^`\n;&|]*", re.IGNORECASE), None),
    (re.compile(r"(?<![\w-])bundle\s+exec\s+(?:rspec|rake\s+test)\b[^`\n;&|]*", re.IGNORECASE), None),
    (re.compile(r"(?<![\w-])(?:tox|nox)(?:\s+[^`\n;&|]+)?", re.IGNORECASE), None),
)


def _clean_command(candidate: str) -> str | None:
    command = candidate.strip().strip("`\"'")
    command = re.sub(r"^(?:\$|>)\s*", "", command)
    command = re.sub(r"^PS>\s*", "", command, flags=re.IGNORECASE)
    command = command.strip().rstrip(".,:;)]\"").strip()
    if not command:
        return None
    # A prose line can contain a matched command followed by explanatory text.
    # Keep shell-style commands intact while trimming common Markdown prose.
    command = re.split(r"\s+#|\s+which\s+", command, maxsplit=1, flags=re.IGNORECASE)[0].strip()
    return command or None


def _detect_in_text(text: str) -> str | None:
    in_fence = False
    lines = text.splitlines()
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            in_fence = not in_fence
            continue
        # Commands in code fences and inline-code/prose are both useful.  The
        # fence state is kept so a heading containing a word like "test" is
        # never enough by itself.
        for pattern, _fixed in COMMAND_PATTERNS:
            match = pattern.search(line)
            if match is None:
                continue
            candidate = _clean_command(match.group(0))
            if candidate is not None:
                return candidate
        if in_fence:
            continue
    return None


def _detect_from_project_files(repo: Path) -> str | None:
    """Find a conventional command when no README command is available."""

    makefile = root_file(repo, "Makefile", "makefile")
    if makefile is not None:
        make_text = read_text(makefile)
        if make_text is not None and re.search(r"(?m)^\s*test\s*:", make_text):
            return "make test"

    package_json = root_file(repo, "package.json")
    if package_json is not None:
        package_text = read_text(package_json)
        if package_text is not None:
            try:
                package = json.loads(package_text)
                scripts = package.get("scripts", {}) if isinstance(package, dict) else {}
                if isinstance(scripts, dict) and isinstance(scripts.get("test"), str):
                    if scripts["test"].strip():
                        return "npm test"
            except (TypeError, ValueError):
                pass

    cargo = root_file(repo, "Cargo.toml")
    if cargo is not None and read_text(cargo) is not None:
        return "cargo test"

    go_mod = root_file(repo, "go.mod")
    if go_mod is not None and read_text(go_mod) is not None:
        return "go test ./..."

    if root_file(repo, "pubspec.yaml") is not None or root_file(repo, "pubspec.lock") is not None:
        project_type = detect_project_type(repo)
        if project_type == "flutter":
            return "flutter test"
        if project_type == "dart":
            return "dart test"

    return None


def detect_test_command(repo: Path) -> str | None:
    """Return a documented or conventional test command for ``repo``.

    README documentation is preferred.  The small project-file fallback keeps
    this helper useful to verification code, while :func:`run` specifically
    requires the command to be present in the README as requested.
    """

    readme = find_readme(repo)
    if readme is not None:
        text = read_text(readme)
        if text is not None:
            command = _detect_in_text(text)
            if command is not None:
                return command
    try:
        return _detect_from_project_files(repo)
    except (OSError, TypeError, ValueError):
        return None


def run(repo: Path) -> CheckResult:
    """Return whether a README documents a recognizable test command."""

    readme = find_readme(repo)
    if readme is None:
        return CheckResult(
            passed=False,
            name=NAME,
            reason="No README was found from which to identify a test command.",
            fix="Add a root README and show the exact command used to run the test suite, such as `make test`.",
            priority=PRIORITY,
            details={"path": "README.md"},
        )

    text = read_text(readme)
    relative = relative_path(repo, readme)
    if text is None:
        return CheckResult(
            passed=False,
            name=NAME,
            reason="The README exists but could not be read as text.",
            fix="Make the README readable UTF-8 text and document the test command in it.",
            priority=PRIORITY,
            details={"path": relative},
        )

    command = _detect_in_text(text)
    if command is None:
        return CheckResult(
            passed=False,
            name=NAME,
            reason="The README does not document a recognizable test command.",
            fix="Add a Test section with the exact command to run, for example `make test` or `python -m unittest discover -s tests -v`.",
            priority=PRIORITY,
            details={"path": relative},
        )

    return CheckResult(
        passed=True,
        name=NAME,
        reason="The README documents a recognizable command for running tests.",
        fix=NO_FIX,
        priority=PRIORITY,
        details={"path": relative, "command": command},
    )
