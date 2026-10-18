"""Check for a recognized dependency lockfile."""

from __future__ import annotations

from pathlib import Path

from rebuildcheck.models import CheckResult

from .common import iter_files, read_bytes, relative_path


NAME = "dependency_lockfile"
PRIORITY = 2
NO_FIX = "No action needed."

# These names are used by common package managers.  A generic file ending in
# ``.lock`` is intentionally not enough on its own: editor/runtime markers and
# application lock files should not satisfy a dependency reproducibility check.
LOCKFILE_NAMES = frozenset(
    {
        "requirements.lock",
        "requirements-dev.lock",
        "requirements-prod.lock",
        "requirements.txt",
        "requirements-dev.txt",
        "requirements-prod.txt",
        "poetry.lock",
        "pdm.lock",
        "pipfile.lock",
        "uv.lock",
        "package-lock.json",
        "npm-shrinkwrap.json",
        "yarn.lock",
        "pnpm-lock.yaml",
        "pnpm-lock.yml",
        "bun.lock",
        "bun.lockb",
        "cargo.lock",
        "go.sum",
        "composer.lock",
        "gemfile.lock",
        "mix.lock",
        "pubspec.lock",
        "podfile.lock",
        "cartfile.resolved",
        "package.resolved",
        "packages.lock.json",
        "project.assets.json",
        "flake.lock",
        "gradle.lockfile",
        "terraform.lock.hcl",
        "deno.lock",
        "cabal.project.freeze",
        "manifest.toml",
        "renv.lock",
        "paket.lock",
        "conan.lock",
    }
)


def _is_recognized(path: Path) -> bool:
    lowered = path.name.casefold()
    return lowered in LOCKFILE_NAMES


def _is_usable(path: Path, data: bytes) -> bool:
    """Accept a requirements file only when its entries are pinned."""

    if path.name.casefold().endswith(".txt"):
        entries = [
            line.strip()
            for line in data.decode("utf-8", errors="replace").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]
        if not entries:
            return False
        return all(
            "==" in line
            or " @ " in line
            or line.startswith(("git+", "https://", "http://"))
            for line in entries
        )
    return bool(data.strip())


def run(repo: Path) -> CheckResult:
    """Return whether a readable, non-empty dependency lockfile exists."""

    try:
        candidates = sorted(
            (path for path in iter_files(repo) if _is_recognized(path)),
            key=lambda path: relative_path(repo, path),
        )
    except (OSError, TypeError, ValueError):
        candidates = []

    readable: list[str] = []
    unreadable: list[str] = []
    for path in candidates:
        data = read_bytes(path)
        relative = relative_path(repo, path)
        if data is None:
            unreadable.append(relative)
        elif _is_usable(path, data):
            readable.append(relative)

    if readable:
        return CheckResult(
            passed=True,
            name=NAME,
            reason="A recognized, non-empty dependency lockfile is present.",
            fix=NO_FIX,
            priority=PRIORITY,
            details={"lockfiles": readable[:100]},
        )

    if unreadable:
        return CheckResult(
            passed=False,
            name=NAME,
            reason="A recognized dependency lockfile exists but could not be read.",
            fix="Make the lockfile readable and keep a complete dependency lock in the repository.",
            priority=PRIORITY,
            details={"lockfiles": unreadable[:100]},
        )

    return CheckResult(
        passed=False,
        name=NAME,
        reason="No recognized dependency lockfile was found.",
        fix="Generate and commit a lockfile such as requirements.lock, uv.lock, package-lock.json, pubspec.lock, Cargo.lock, or go.sum.",
        priority=PRIORITY,
        details={"lockfiles": []},
    )
