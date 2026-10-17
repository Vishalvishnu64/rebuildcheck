"""Ordered registry of rebuildcheck's static checks."""

from __future__ import annotations

from . import (
    absolute_paths,
    committed_secrets,
    dependency_lockfile,
    dockerfile,
    environment_example,
    git_repository,
    readme_sections,
    runtime_version,
    test_command,
)

# Modules are kept in dependency-independent presentation order so a CLI can
# call ``check.run(repo)`` and report results consistently.
CHECKS = (
    git_repository,
    dependency_lockfile,
    runtime_version,
    dockerfile,
    test_command,
    readme_sections,
    environment_example,
    absolute_paths,
    committed_secrets,
)

# A callable view is convenient for consumers that do not want to know that
# registry entries are modules.  CHECKS remains the public ordered registry.
CHECK_RUNNERS = tuple(check.run for check in CHECKS)

__all__ = ["CHECKS", "CHECK_RUNNERS"]
