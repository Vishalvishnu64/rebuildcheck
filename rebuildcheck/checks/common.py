"""Defensive helpers shared by the repository checks.

The checks are intentionally useful on repositories that are incomplete or
not Git repositories yet.  These helpers therefore treat filesystem and Git
errors as data (usually ``None`` or an empty collection) instead of allowing
them to escape from a check.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Iterator


DEFAULT_READ_LIMIT = 4 * 1024 * 1024
GIT_TIMEOUT_SECONDS = 10

# Generated/dependency directories can contain a very large number of files
# and are not part of a repository's authored source for these checks.
SKIP_DIRECTORIES = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".venv",
        "venv",
        "node_modules",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
    }
)

README_NAMES = ("README.md", "README.rst", "README.txt", "README")


def as_repo_path(repo: os.PathLike[str] | str) -> Path | None:
    """Return ``repo`` as a directory path, or ``None`` when it is unusable."""

    try:
        path = Path(repo)
        if not path.exists() or not path.is_dir():
            return None
        return path
    except (OSError, RuntimeError, TypeError, ValueError):
        return None


def relative_path(repo: os.PathLike[str] | str, path: os.PathLike[str] | str) -> str:
    """Return a stable POSIX path relative to ``repo``.

    A malicious or unusual path should never cause a check result to expose an
    absolute machine path.  If a path cannot be made relative, its basename is
    used as a conservative fallback.
    """

    try:
        root = os.path.abspath(os.fspath(repo))
        target = os.path.abspath(os.fspath(path))
        candidate = os.path.relpath(target, root)
        if candidate == ".":
            return "."
        if candidate == ".." or candidate.startswith(".." + os.sep):
            return Path(os.fspath(path)).name or "."
        return Path(candidate).as_posix()
    except (OSError, RuntimeError, TypeError, ValueError):
        try:
            return Path(path).name or "."
        except (TypeError, ValueError):
            return "."


def iter_files(repo: os.PathLike[str] | str) -> Iterator[Path]:
    """Yield regular files below ``repo`` without following symlinks.

    ``os.walk``'s ``onerror`` callback is intentionally a no-op: an unreadable
    directory should not make a static check crash.  Individual checks can
    still report an unreadable file when it is one they specifically need.
    """

    root = as_repo_path(repo)
    if root is None:
        return

    def ignore_walk_error(_error: OSError) -> None:
        return None

    try:
        walker = os.walk(
            root,
            topdown=True,
            followlinks=False,
            onerror=ignore_walk_error,
        )
        for directory, directories, filenames in walker:
            directories[:] = [
                name
                for name in directories
                if name.casefold() not in SKIP_DIRECTORIES
            ]
            for filename in filenames:
                path = Path(directory) / filename
                try:
                    if path.is_symlink() or not path.is_file():
                        continue
                except (OSError, RuntimeError):
                    continue
                yield path
    except (OSError, RuntimeError, TypeError, ValueError):
        return


def read_bytes(
    path: os.PathLike[str] | str,
    *,
    limit: int = DEFAULT_READ_LIMIT,
) -> bytes | None:
    """Read a reasonably sized file, returning ``None`` on I/O failure.

    A size limit prevents a surprising repository from consuming unbounded
    memory.  An over-limit file is treated like an unreadable file by callers
    that need complete inspection.
    """

    try:
        with Path(path).open("rb") as handle:
            data = handle.read(limit + 1)
        if len(data) > limit:
            return None
        return data
    except (OSError, RuntimeError, TypeError, ValueError):
        return None


def read_text(
    path: os.PathLike[str] | str,
    *,
    limit: int = DEFAULT_READ_LIMIT,
    reject_binary: bool = True,
) -> str | None:
    """Read UTF-8 text defensively.

    Invalid UTF-8 is decoded with replacement so a single odd byte does not
    crash a check.  Files containing NUL bytes are considered binary when
    ``reject_binary`` is true.
    """

    data = read_bytes(path, limit=limit)
    if data is None:
        return None
    if reject_binary and b"\x00" in data:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("utf-8", errors="replace")


def root_file(repo: os.PathLike[str] | str, *names: str) -> Path | None:
    """Find the first named regular file directly below ``repo``.

    Exact names are preferred.  A case-insensitive fallback is useful when a
    checkout came from a case-insensitive filesystem while remaining harmless
    on Linux.
    """

    root = as_repo_path(repo)
    if root is None:
        return None

    for name in names:
        candidate = root / name
        try:
            if not candidate.is_symlink() and candidate.is_file():
                return candidate
        except (OSError, RuntimeError):
            continue

    wanted = {name.casefold() for name in names}
    try:
        children = sorted(root.iterdir(), key=lambda item: item.name.casefold())
    except (OSError, RuntimeError):
        return None
    for child in children:
        if child.name.casefold() not in wanted:
            continue
        try:
            if not child.is_symlink() and child.is_file():
                return child
        except (OSError, RuntimeError):
            continue
    return None


def find_readme(repo: os.PathLike[str] | str) -> Path | None:
    """Find a conventional README in the repository root."""

    return root_file(repo, *README_NAMES)


def is_environment_filename(name: str) -> bool:
    """Return whether ``name`` conventionally denotes a dotenv file."""

    lowered = name.casefold()
    if lowered == ".env" or lowered.endswith(".env"):
        return True
    if lowered.startswith(".env."):
        return True
    return lowered in {
        "env.example",
        "env.sample",
        "env.template",
        "environment.example",
        "environment.sample",
        "environment.template",
    }


def iter_environment_files(repo: os.PathLike[str] | str) -> Iterator[Path]:
    """Yield dotenv-like files below ``repo``."""

    for path in iter_files(repo):
        try:
            if is_environment_filename(path.name):
                yield path
        except (OSError, RuntimeError, TypeError, ValueError):
            continue


def run_git(
    repo: os.PathLike[str] | str,
    *arguments: str,
    timeout: int = GIT_TIMEOUT_SECONDS,
) -> tuple[int | None, str, str]:
    """Run a Git command without a shell.

    The return code is ``None`` when the process could not be started or was
    interrupted.  Standard output and error are decoded with replacement so
    unusual filenames cannot cause an exception.
    """

    root = as_repo_path(repo)
    if root is None:
        return None, "", "repository is not a directory"
    try:
        completed = subprocess.run(
            ["git", *arguments],
            cwd=os.fspath(root),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError, ValueError):
        return None, "", "git could not be executed"

    def decode(value: bytes) -> str:
        return value.decode("utf-8", errors="replace")

    return completed.returncode, decode(completed.stdout), decode(completed.stderr)


def git_output(
    repo: os.PathLike[str] | str,
    *arguments: str,
    timeout: int = GIT_TIMEOUT_SECONDS,
) -> str | None:
    """Return successful Git stdout, or ``None`` when Git cannot answer."""

    returncode, stdout, _stderr = run_git(repo, *arguments, timeout=timeout)
    if returncode != 0:
        return None
    return stdout


def tracked_files(repo: os.PathLike[str] | str) -> tuple[bool, list[Path]]:
    """Return ``(is_git, files)`` for files tracked by Git.

    Git paths are validated before joining them to the inspected repository so
    odd repository entries cannot make result paths escape the repository.
    """

    root = as_repo_path(repo)
    if root is None:
        return False, []
    output = git_output(root, "ls-files", "-z")
    if output is None:
        return False, []

    files: list[Path] = []
    for raw_path in output.split("\x00"):
        if not raw_path:
            continue
        try:
            relative = Path(raw_path)
            if relative.is_absolute() or ".." in relative.parts:
                continue
            candidate = root / relative
            if candidate.is_symlink() or not candidate.is_file():
                continue
            files.append(candidate)
        except (OSError, RuntimeError, TypeError, ValueError):
            continue
    return True, files


def files_for_scan(repo: os.PathLike[str] | str) -> tuple[bool, list[Path]]:
    """Return tracked files, falling back to authored files for non-Git trees."""

    is_git, files = tracked_files(repo)
    if is_git:
        return True, files
    return False, list(iter_files(repo))


def status_paths(repo: os.PathLike[str] | str) -> list[str] | None:
    """Return relative paths reported by ``git status``, or ``None`` on error."""

    output = git_output(
        repo,
        "status",
        "--porcelain=v1",
        "--untracked-files=all",
        "-z",
    )
    if output is None:
        return None

    paths: list[str] = []
    pieces = output.split("\x00")
    index = 0
    while index < len(pieces):
        entry = pieces[index]
        index += 1
        if not entry:
            continue
        status = entry[:2]
        raw_path = entry[3:] if len(entry) >= 3 else ""
        # Porcelain -z uses a second NUL-separated path for renames/copies.
        if status[:1] in {"R", "C"} and index < len(pieces):
            new_path = pieces[index]
            index += 1
            if new_path:
                raw_path = new_path
        try:
            candidate = Path(raw_path.strip('"'))
            if candidate.is_absolute() or ".." in candidate.parts:
                paths.append(Path(raw_path).name or "?")
            else:
                paths.append(candidate.as_posix())
        except (TypeError, ValueError):
            paths.append("?")
    return paths
