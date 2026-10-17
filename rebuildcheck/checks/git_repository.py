"""Check that the inspected directory is a committed, clean Git worktree."""

from __future__ import annotations

from pathlib import Path

from rebuildcheck.models import CheckResult

from .common import (
    as_repo_path,
    git_output,
    relative_path,
    status_paths,
)


NAME = "git_repository"
PRIORITY = 1
NO_FIX = "No action needed."


def run(repo: Path) -> CheckResult:
    """Return whether ``repo`` is a clean Git worktree with a commit."""

    root = as_repo_path(repo)
    if root is None:
        return CheckResult(
            passed=False,
            name=NAME,
            reason="The inspected repository directory is missing or unreadable.",
            fix="Point rebuildcheck at an existing readable repository directory.",
            priority=PRIORITY,
            details={"repository": "."},
        )

    worktree = git_output(root, "rev-parse", "--is-inside-work-tree")
    if worktree is None or worktree.strip().casefold() != "true":
        return CheckResult(
            passed=False,
            name=NAME,
            reason="The inspected directory is not a Git worktree.",
            fix="Run `git init` in the repository, add the intended files, and create an initial commit.",
            priority=PRIORITY,
            details={"repository": "."},
        )

    commit = git_output(root, "rev-parse", "--verify", "HEAD")
    if commit is None or not commit.strip():
        return CheckResult(
            passed=False,
            name=NAME,
            reason="The Git repository has no commit that can be checked out.",
            fix="Stage the repository files and create at least one commit before rebuilding.",
            priority=PRIORITY,
            details={"repository": "."},
        )

    changed = status_paths(root)
    if changed is None:
        return CheckResult(
            passed=False,
            name=NAME,
            reason="Git reported the repository, but its working-tree status could not be read.",
            fix="Make the repository readable and run `git status` successfully, then retry the check.",
            priority=PRIORITY,
            details={"repository": "."},
        )
    if changed:
        return CheckResult(
            passed=False,
            name=NAME,
            reason="The Git working tree has uncommitted changes or untracked files.",
            fix="Review the listed files, commit the intended changes, or remove generated/unwanted files before rebuilding.",
            priority=PRIORITY,
            details={
                "repository": ".",
                "commit": commit.strip()[:12],
                "changed_paths": changed[:100],
            },
        )

    return CheckResult(
        passed=True,
        name=NAME,
        reason="The directory is a Git worktree with at least one commit and a clean working tree.",
        fix=NO_FIX,
        priority=PRIORITY,
        details={"repository": ".", "commit": commit.strip()[:12]},
    )
