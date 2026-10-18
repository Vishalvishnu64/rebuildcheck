"""Command-line orchestration and report formatting for rebuildcheck."""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from rebuildcheck.checks import CHECKS
from rebuildcheck.models import CheckResult
from rebuildcheck.project_type import UNKNOWN_PROJECT_TYPE, detect_project_type
from rebuildcheck.verification import (
    CleanRoomTestResult,
    DockerResult,
    VerificationReport,
    verify_clean_room,
)


@dataclass
class InspectionReport:
    """All results produced for one repository inspection."""

    repository: str
    checks: list[CheckResult]
    verification: VerificationReport
    score: int
    fixes: list[dict[str, Any]]
    project_type: str = UNKNOWN_PROJECT_TYPE

    @property
    def passed(self) -> bool:
        """Whether every required check that ran passed."""

        if any(not check.passed for check in self.checks):
            return False
        if self.verification.test.status == "failed":
            return False
        if self.verification.docker.status == "failed":
            return False
        return True

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-friendly report."""

        return {
            "repository": self.repository,
            "project_type": self.project_type,
            "passed": self.passed,
            "score": self.score,
            "checks": [check.to_dict() for check in self.checks],
            "verification": self.verification.to_dict(),
            "fixes": self.fixes,
        }


def build_parser() -> argparse.ArgumentParser:
    """Build the public command-line parser."""

    parser = argparse.ArgumentParser(
        prog="rebuildcheck",
        description="Simulate a clean rebuild and report what would fail.",
    )
    parser.add_argument("repo_path", help="Repository directory to inspect")
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print the report as JSON instead of human-readable text",
    )
    parser.add_argument(
        "--docker",
        action="store_true",
        help="Also build the repository Dockerfile when Docker is available",
    )
    parser.add_argument(
        "--no-run",
        action="store_true",
        help="Run static checks only; skip the clean-room test and Docker build",
    )
    return parser


def run_static_checks(repo: Path) -> list[CheckResult]:
    """Run every registered static check without allowing one to abort the report."""

    results: list[CheckResult] = []
    for check in CHECKS:
        name = getattr(check, "NAME", check.__name__.rsplit(".", 1)[-1])
        priority = getattr(check, "PRIORITY", 9)
        try:
            result = check.run(repo)
        except Exception as exc:  # pragma: no cover - defensive boundary
            result = CheckResult(
                passed=False,
                name=name,
                reason=f"The check could not complete: {type(exc).__name__}.",
                fix="Inspect the repository and rerun rebuildcheck after correcting the reported filesystem or configuration problem.",
                priority=priority,
                details={"error": type(exc).__name__},
            )
        results.append(result)
    return results


def inspect_repository(
    repo_path: str | Path,
    *,
    docker: bool = False,
    no_run: bool = False,
) -> InspectionReport:
    """Inspect ``repo_path`` and return a complete report."""

    repo = _as_path(repo_path)
    repository = _repository_label(repo_path)
    checks = run_static_checks(repo)
    command = _command_from_checks(checks)

    if no_run:
        verification = _skipped_verification(repository, docker)
    else:
        verification = verify_clean_room(repo, command, run_docker=docker)
        # Keep reports portable even when the caller supplied an absolute path.
        verification.repository = repository

    score = _score(checks, verification)
    fixes = _fixes(checks, verification)
    return InspectionReport(
        repository=repository,
        checks=checks,
        verification=verification,
        score=score,
        fixes=fixes,
        project_type=detect_project_type(repo),
    )


def render_json(report: InspectionReport) -> str:
    """Render a report as stable, indented JSON."""

    return json.dumps(report.to_dict(), indent=2, sort_keys=True)


def render_human(report: InspectionReport) -> str:
    """Render a concise report with all fixes at the end."""

    lines = [
        f"rebuildcheck report: {report.repository}",
        f"Project type: {report.project_type.title()}",
        f"Score: {report.score}/100",
        "",
        "Static checks:",
    ]
    for check in report.checks:
        status = "PASS" if check.passed else "FAIL"
        label = check.name.replace("_", " ").title()
        lines.append(f"  {status} {label} — {check.reason}")

    test = report.verification.test
    lines.extend(
        [
            "",
            f"Clean-room test: {test.status.upper()} — {test.message or 'No additional details.'}",
        ]
    )
    if test.command:
        lines.append(f"  Command: {test.command}")

    docker = report.verification.docker
    lines.append(
        f"Docker build: {docker.status.upper()} — {docker.message or 'No additional details.'}"
    )
    if docker.command:
        lines.append(f"  Command: {docker.command}")

    lines.extend(["", "Fixes (priority order):"])
    if report.fixes:
        for index, fix in enumerate(report.fixes, start=1):
            lines.append(
                f"  {index}. P{fix['priority']} {fix['check']}: {fix['fix']}"
            )
    else:
        lines.append("  None — the repository is ready for a clean rebuild.")
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    """Run the CLI and return a process exit status."""

    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        report = inspect_repository(
            args.repo_path,
            docker=args.docker,
            no_run=args.no_run,
        )
    except Exception as exc:  # pragma: no cover - final safety net
        message = f"rebuildcheck could not inspect the repository: {type(exc).__name__}."
        if args.json:
            print(json.dumps({"passed": False, "error": message}, indent=2))
        else:
            print(message)
        return 1

    print(render_json(report) if args.json else render_human(report))
    return 0 if report.passed else 1


def _as_path(repo_path: str | Path) -> Path:
    try:
        return Path(repo_path)
    except (TypeError, ValueError, OSError):
        # Path-like operations in the individual checks will turn this into
        # ordinary failed results rather than making the CLI crash.
        return Path(".") / "__invalid_rebuildcheck_path__"


def _repository_label(repo_path: object) -> str:
    """Return a relative, non-sensitive label for the inspected path."""

    try:
        text = str(repo_path)
        path = Path(text)
        if text in {"", "."}:
            return "."
        return path.name or "."
    except (OSError, RuntimeError, TypeError, ValueError):
        return "<invalid path>"


def _command_from_checks(checks: list[CheckResult]) -> str | None:
    for check in checks:
        if check.name != "test_command":
            continue
        command = check.details.get("command")
        return command if isinstance(command, str) and command.strip() else None
    return None


def _skipped_verification(repository: str, docker_requested: bool) -> VerificationReport:
    """Represent the explicit ``--no-run`` choice without executing anything."""

    test = CleanRoomTestResult(
        status="skipped",
        message="Clean-room test skipped because --no-run was requested.",
    )
    docker = DockerResult(
        status="skipped",
        command="docker build ." if docker_requested else None,
        message="Docker build skipped because --no-run was requested.",
    )
    return VerificationReport(
        status="skipped",
        repository=repository,
        test=test,
        docker=docker,
        summary="Static checks only; execution checks were skipped.",
    )


def _score(checks: list[CheckResult], verification: VerificationReport) -> int:
    """Score evaluated checks while excluding optional skipped checks."""

    passed = sum(check.passed for check in checks)
    evaluated = len(checks)
    for result in (verification.test, verification.docker):
        if result.status == "passed":
            passed += 1
            evaluated += 1
        elif result.status == "failed":
            evaluated += 1
    return int((passed / evaluated) * 100) if evaluated else 0


def _fixes(
    checks: list[CheckResult], verification: VerificationReport
) -> list[dict[str, Any]]:
    fixes: list[dict[str, Any]] = []
    for check in checks:
        if not check.passed:
            fixes.append(
                {"priority": check.priority, "check": check.name, "fix": check.fix}
            )
    if verification.test.status == "failed":
        fixes.append(
            {
                "priority": 3,
                "check": "clean_room_test",
                "fix": "Run the documented test command in a fresh clone and fix the first reported failure or timeout.",
            }
        )
    if verification.docker.status == "failed":
        fixes.append(
            {
                "priority": 4,
                "check": "docker_build",
                "fix": "Run `docker build .` locally and fix the first Dockerfile or build-context error.",
            }
        )
    return sorted(fixes, key=lambda item: (item["priority"], item["check"]))


__all__ = [
    "InspectionReport",
    "build_parser",
    "inspect_repository",
    "main",
    "render_human",
    "render_json",
    "run_static_checks",
]
