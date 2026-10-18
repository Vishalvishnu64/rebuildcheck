"""Materialize an example source tree as a temporary standalone Git repo."""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


FIXTURES = ("passing", "broken")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run rebuildcheck against a standalone materialized example."
    )
    parser.add_argument("fixture", choices=FIXTURES)
    parser.add_argument("--no-run", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    project_root = Path(__file__).resolve().parents[1]
    source = project_root / "examples" / args.fixture
    if not source.is_dir():
        print(f"Example fixture is missing: {args.fixture}", file=sys.stderr)
        return 2

    with tempfile.TemporaryDirectory(prefix="rebuildcheck-example-") as directory:
        destination = Path(directory) / args.fixture
        shutil.copytree(
            source,
            destination,
            ignore=shutil.ignore_patterns(".rebuildcheck-fixture"),
        )
        _run_git(destination, "init", "-q")
        _run_git(destination, "config", "user.name", "rebuildcheck example")
        _run_git(destination, "config", "user.email", "example@example.invalid")
        _run_git(destination, "add", ".")
        _run_git(destination, "commit", "-qm", f"example: {args.fixture}")

        command = [sys.executable, "-m", "rebuildcheck", str(destination)]
        if args.no_run:
            command.append("--no-run")
        if args.json:
            command.append("--json")
        completed = subprocess.run(command, cwd=project_root, check=False)
        return completed.returncode


def _run_git(cwd: Path, *arguments: str) -> None:
    subprocess.run(
        ["git", *arguments],
        cwd=cwd,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


if __name__ == "__main__":
    raise SystemExit(main())
