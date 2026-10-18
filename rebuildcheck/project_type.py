"""Detect the supported project type from root-level marker files.

The detector intentionally uses file names only.  It does not execute a
package manager or parse a manifest, which keeps detection cheap and safe for
incomplete repositories.  A project is considered ambiguous when markers for
more than one supported ecosystem are present; in that case the neutral
``"unknown"`` result is returned instead of guessing.
"""

from __future__ import annotations

from pathlib import Path
from typing import Final, Literal


ProjectType = Literal["python", "node", "go", "rust", "unknown"]
UNKNOWN_PROJECT_TYPE: Final[ProjectType] = "unknown"

# The tuple order is part of the detector's contract.  It makes candidate
# inspection and any future presentation deterministic while ambiguity still
# remains neutral rather than being resolved by an arbitrary winner.
PROJECT_TYPE_PRECEDENCE: Final[tuple[str, ...]] = ("python", "node", "go", "rust")

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
        markers = PROJECT_TYPE_MARKERS[project_type]
        if any(marker.casefold() in present for marker in markers):
            candidates.append(project_type)
    return tuple(candidates)


def detect_project_type(repo: Path) -> ProjectType:
    """Return ``python``, ``node``, ``go``, ``rust``, or ``unknown``.

    Exactly one ecosystem must have at least one marker file.  Empty,
    unreadable, invalid, and multi-ecosystem repositories all return the
    neutral ``"unknown"`` result.
    """

    candidates = project_type_candidates(repo)
    if len(candidates) == 1:
        return candidates[0]  # type: ignore[return-value]
    return UNKNOWN_PROJECT_TYPE


__all__ = [
    "PROJECT_TYPE_MARKERS",
    "PROJECT_TYPE_PRECEDENCE",
    "ProjectType",
    "UNKNOWN_PROJECT_TYPE",
    "detect_project_type",
    "project_type_candidates",
]
