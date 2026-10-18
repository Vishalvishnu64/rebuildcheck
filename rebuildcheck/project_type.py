"""Detect supported project types from root-level marker files.

The detector does not execute a package manager.  Dart/Flutter manifests are
read only far enough to distinguish a Flutter manifest from an ordinary Dart
manifest.  A project is considered ambiguous when markers for more than one
supported ecosystem are present; in that case the neutral ``"unknown"``
result is returned instead of guessing.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Final, Literal


ProjectType = Literal[
    "python",
    "node",
    "go",
    "rust",
    "dart",
    "flutter",
    "unknown",
]
UNKNOWN_PROJECT_TYPE: Final[ProjectType] = "unknown"

_MAX_MANIFEST_READ = 4 * 1024 * 1024
_FLUTTER_DEPENDENCY_NAMES = frozenset({"flutter", "flutter_test"})
_FLUTTER_LIST_ITEM_RE = re.compile(
    r"^[-]\s*(?:flutter|flutter_test)\s*$", re.IGNORECASE
)
_FLUTTER_DEPENDENCY_RE = re.compile(
    r"(?:^|[,{]\s*)['\"]?(?:flutter|flutter_test)['\"]?\s*:",
    re.IGNORECASE,
)
_FLUTTER_SDK_RE = re.compile(
    r"(?:^|[,{]\s*)['\"]?sdk['\"]?\s*:\s*['\"]?flutter(?![A-Za-z0-9_])",
    re.IGNORECASE,
)

# The tuple order is part of the detector's contract.  It makes candidate
# inspection and any future presentation deterministic while ambiguity still
# remains neutral rather than being resolved by an arbitrary winner.
PROJECT_TYPE_PRECEDENCE: Final[tuple[str, ...]] = (
    "python",
    "node",
    "go",
    "rust",
    "dart",
    "flutter",
)

# Strong manifest/lock/runtime markers are listed before weaker version-only
# markers.  The detector currently exposes only the type, but keeping this
# order explicit prevents filesystem iteration order from affecting results.
PROJECT_TYPE_MARKERS: Final[dict[str, tuple[str, ...]]] = {
    "python": (
        "pyproject.toml",
        "requirements.lock",
        "requirements.txt",
        "poetry.lock",
        "uv.lock",
        ".python-version",
        "pytest.ini",
        ".pytest.ini",
        "tox.ini",
        "setup.py",
        "setup.cfg",
        "Pipfile",
        "Pipfile.lock",
    ),
    "node": (
        "package.json",
        "package-lock.json",
        "yarn.lock",
        "pnpm-lock.yaml",
        ".nvmrc",
    ),
    "go": (
        "go.mod",
        "go.sum",
        ".go-version",
    ),
    "rust": (
        "Cargo.toml",
        "Cargo.lock",
        "rust-toolchain.toml",
        "rust-toolchain",
        ".rust-version",
    ),
    # Both ecosystems use the same manifest and lockfile markers.  The
    # manifest contents decide whether the public candidate is ``dart`` or
    # ``flutter``; keeping both marker sets explicit documents the supported
    # files for callers that inspect this constant.
    "dart": (
        "pubspec.yaml",
        "pubspec.lock",
    ),
    "flutter": (
        "pubspec.yaml",
        "pubspec.lock",
    ),
}


def _root_file_names(repo: Path) -> frozenset[str]:
    """Return regular root-file names case-insensitively, or an empty set."""

    try:
        if not repo.exists() or not repo.is_dir():
            return frozenset()
        return frozenset(
            child.name.casefold()
            for child in repo.iterdir()
            if not child.is_symlink() and child.is_file()
        )
    except (OSError, RuntimeError, TypeError, ValueError):
        return frozenset()


def _root_file(repo: Path, name: str) -> Path | None:
    """Return a regular root file by exact or case-insensitive name."""

    try:
        candidate = repo / name
        if not candidate.is_symlink() and candidate.is_file():
            return candidate
    except (OSError, RuntimeError, TypeError, ValueError):
        return None

    try:
        children = sorted(repo.iterdir(), key=lambda item: item.name.casefold())
    except (OSError, RuntimeError, TypeError, ValueError):
        return None
    wanted = name.casefold()
    for child in children:
        if child.name.casefold() != wanted:
            continue
        try:
            if not child.is_symlink() and child.is_file():
                return child
        except (OSError, RuntimeError):
            continue
    return None


def _read_manifest(path: Path) -> str | None:
    """Read a bounded UTF-8 manifest without allowing filesystem errors out."""

    try:
        with path.open("rb") as handle:
            data = handle.read(_MAX_MANIFEST_READ + 1)
        if len(data) > _MAX_MANIFEST_READ or b"\x00" in data:
            return None
        return data.decode("utf-8", errors="replace")
    except (OSError, RuntimeError, TypeError, ValueError):
        return None


def _pubspec_uses_flutter(text: str | None) -> bool:
    """Return whether a pubspec clearly names a Flutter dependency.

    This deliberately checks YAML-looking dependency keys rather than any
    occurrence of the word ``flutter``.  That keeps comments, descriptions,
    and package names such as ``flutter_lints`` from turning a Dart project
    into a Flutter project by accident.
    """

    if text is None:
        return False
    for raw_line in text.splitlines():
        # A pubspec comment begins at ``#`` outside the simple dependency
        # values relevant here.  Removing it also avoids matching prose in a
        # comment that mentions Flutter.
        line = raw_line.split("#", 1)[0].strip()
        if not line:
            continue
        if _FLUTTER_LIST_ITEM_RE.fullmatch(line):
            return True
        if _FLUTTER_DEPENDENCY_RE.search(line) or _FLUTTER_SDK_RE.search(line):
            return True
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip().strip("'\"").casefold()
        value = value.strip().strip("'\"").casefold()
        if key in _FLUTTER_DEPENDENCY_NAMES:
            return True
        if key == "sdk" and value == "flutter":
            return True
    return False


def _dart_or_flutter_type(repo: Path) -> str:
    """Classify a repository with a pubspec marker."""

    manifest = _root_file(repo, "pubspec.yaml")
    return (
        "flutter"
        if _pubspec_uses_flutter(_read_manifest(manifest) if manifest else None)
        else "dart"
    )


def project_type_candidates(repo: Path) -> tuple[str, ...]:
    """Return supported project types represented by root marker files.

    Results follow :data:`PROJECT_TYPE_PRECEDENCE`, never directory iteration
    order.  This helper is useful to callers that want to explain why a
    project was classified as ambiguous without changing the neutral public
    result of :func:`detect_project_type`.
    """

    try:
        root = Path(repo)
    except (TypeError, ValueError, OSError):
        return ()

    present = _root_file_names(root)
    candidates: list[str] = []
    for project_type in PROJECT_TYPE_PRECEDENCE:
        if project_type in {"dart", "flutter"}:
            continue
        markers = PROJECT_TYPE_MARKERS[project_type]
        if any(marker.casefold() in present for marker in markers):
            candidates.append(project_type)
    if "pubspec.yaml" in present or "pubspec.lock" in present:
        candidates.append(_dart_or_flutter_type(root))
    return tuple(candidates)


def detect_project_type(repo: Path) -> ProjectType:
    """Return a supported project type or ``unknown``.

    Exactly one ecosystem must have at least one marker file.  Empty,
    unreadable, invalid, and multi-ecosystem repositories all return the
    neutral ``"unknown"`` result.
    """

    candidates = project_type_candidates(repo)
    if len(candidates) == 1:
        return candidates[0]  # type: ignore[return-value]
    return UNKNOWN_PROJECT_TYPE


def detect_project_types(repo: Path) -> list[dict[str, str]]:
    """Report recognised projects in immediate subdirectories.

    The scan is used only when the supplied root has no recognised project
    markers.  Each result contains a repository-relative ``path`` and a
    concrete ``type``.  Ambiguous, unreadable, symlinked, and nested project
    roots are skipped rather than guessed or recursively traversed.
    """

    try:
        root = Path(repo)
    except (TypeError, ValueError, OSError):
        return []

    if project_type_candidates(root):
        return []

    try:
        children = sorted(
            root.iterdir(), key=lambda item: (item.name.casefold(), item.name)
        )
    except (OSError, RuntimeError, TypeError, ValueError):
        return []

    projects: list[dict[str, str]] = []
    for child in children:
        try:
            if child.is_symlink() or not child.is_dir():
                continue
            candidates = project_type_candidates(child)
        except (OSError, RuntimeError, TypeError, ValueError):
            continue
        if len(candidates) != 1:
            continue
        projects.append({"path": child.name, "type": candidates[0]})
    return projects


# This descriptive alias keeps callers that think of the results as
# subprojects readable without changing the canonical report-oriented helper.
detect_subprojects = detect_project_types


__all__ = [
    "PROJECT_TYPE_MARKERS",
    "PROJECT_TYPE_PRECEDENCE",
    "ProjectType",
    "UNKNOWN_PROJECT_TYPE",
    "detect_project_type",
    "detect_project_types",
    "detect_subprojects",
    "project_type_candidates",
]
