"""Clean-room verification helpers.

The verifier deliberately treats commands as argument vectors rather than
shell snippets.  This keeps a command supplied by a caller composable with a
``--no-run`` option and avoids making the verifier responsible for CLI policy.
"""

from __future__ import annotations

import json
import pathlib
import re
import shlex
import shutil
import subprocess
import tempfile
import time
from dataclasses import asdict, dataclass
from typing import Literal


Status = Literal["passed", "failed", "skipped"]

_VALID_STATUSES = frozenset({"passed", "failed", "skipped"})
_MAX_OUTPUT_CHARS = 4_000
_TEST_TARGET_RE = re.compile(r"^\s*test\s*:", re.MULTILINE)
_SHELL_CONTROL_TOKENS = frozenset({";", "&&", "||", "|", ">", ">>", "<", "&"})
_README_COMMAND_PATTERNS = (
    re.compile(r"(?<![\w-])make\s+(?:test|check)(?![\w-])", re.IGNORECASE),
    re.compile(
        r"(?<![\w-])(?:python(?:3(?:\.\d+)?)?|pypy3?)\s+-m\s+"
        r"(?:unittest|pytest)\b[^`\n;&|]*",
        re.IGNORECASE,
    ),
    re.compile(r"(?<![\w-])pytest(?:\s+-[^`\n;&|]+)?", re.IGNORECASE),
    re.compile(
        r"(?<![\w-])(?:npm|yarn|pnpm|bun)\s+(?:run\s+)?test\b",
        re.IGNORECASE,
    ),
    re.compile(r"(?<![\w-])cargo\s+test\b[^`\n;&|]*", re.IGNORECASE),
    re.compile(r"(?<![\w-])go\s+test\b[^`\n;&|]*", re.IGNORECASE),
)


@dataclass(slots=True)
class CleanRoomTestResult:
    """The result of running a repository's test command in a clone."""

    status: Status
    command: str | None = None
    returncode: int | None = None
    output: str = ""
    message: str = ""
    duration_seconds: float | None = None

    def __post_init__(self) -> None:
        _validate_status(self.status)

    @property
    def error(self) -> str | None:
        """Compatibility-friendly access to a failure or skip explanation."""

        return self.message or None

    def to_dict(self) -> dict[str, object]:
        """Return only JSON-compatible values."""

        return asdict(self)


@dataclass(slots=True)
class DockerResult:
    """The result of an optional Docker build in the clean-room clone."""

    status: Status
    command: str | None = None
    returncode: int | None = None
    output: str = ""
    message: str = ""
    duration_seconds: float | None = None

    def __post_init__(self) -> None:
        _validate_status(self.status)

    @property
    def error(self) -> str | None:
        """Compatibility-friendly access to a failure or skip explanation."""

        return self.message or None

    def to_dict(self) -> dict[str, object]:
        """Return only JSON-compatible values."""

        return asdict(self)


@dataclass(slots=True)
class VerificationReport:
    """Combined clean-room test and optional Docker verification result."""

    status: Status
    repository: str
    test: CleanRoomTestResult
    docker: DockerResult
    summary: str
    clone_error: str | None = None

    def __post_init__(self) -> None:
        _validate_status(self.status)

    @property
    def clean_room_test(self) -> CleanRoomTestResult:
        """Descriptive alias for callers that prefer the longer name."""

        return self.test

    @property
    def clean_room(self) -> CleanRoomTestResult:
        """Descriptive alias for the clean-room test result."""

        return self.test

    @property
    def docker_result(self) -> DockerResult:
        """Descriptive alias for callers that prefer the longer name."""

        return self.docker

    def to_dict(self) -> dict[str, object]:
        """Return a recursively JSON-compatible report."""

        return asdict(self)

    def to_json(self) -> str:
        """Serialize the report using the standard library JSON encoder."""

        return json.dumps(self.to_dict(), sort_keys=True)


# Short aliases make the result types convenient without changing the more
# explicit public names above.
CleanRoomResult = CleanRoomTestResult
Report = VerificationReport


@dataclass(slots=True)
class _CommandOutcome:
    returncode: int | None
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False
    error: str | None = None
    duration_seconds: float | None = None

    @property
    def output(self) -> str:
        return _compact_output(self.stdout, self.stderr)


def verify_clean_room(
    repo: pathlib.Path,
    command: str | None,
    run_docker: bool = False,
    timeout_seconds: int = 120,
) -> VerificationReport:
    """Clone ``repo`` and verify it without using the caller's work tree.

    ``command`` may be supplied by a caller or left as ``None`` to use the
    small set of repository conventions understood by this module.  A command
    is parsed with :func:`shlex.split` and executed without a shell, so callers
    can safely compose this function with a CLI's ``--no-run`` handling.

    Every expected operational failure is represented in the returned report;
    this function does not let subprocess, path, or temporary-directory errors
    escape to the CLI.
    """

    repository_display = _display_repository(repo)
    requested_command = _display_requested_command(command)

    timeout_error = _validate_timeout(timeout_seconds)
    if timeout_error is not None:
        return _failure_report(
            repository=repository_display,
            command=requested_command,
            message=timeout_error,
            clone_error=timeout_error,
            run_docker=run_docker,
        )

    try:
        repo_path = pathlib.Path(repo).expanduser().resolve()
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        message = f"Invalid repository path: {_exception_text(exc)}"
        return _failure_report(
            repository=repository_display,
            command=requested_command,
            message=message,
            clone_error=message,
            run_docker=run_docker,
        )

    if not repo_path.exists():
        message = f"Repository does not exist: {repository_display}"
        return _failure_report(
            repository=repository_display,
            command=requested_command,
            message=message,
            clone_error=message,
            run_docker=run_docker,
        )
    if not repo_path.is_dir():
        message = f"Repository path is not a directory: {repository_display}"
        return _failure_report(
            repository=repository_display,
            command=requested_command,
            message=message,
            clone_error=message,
            run_docker=run_docker,
        )

    git_check = _run_command(
        ["git", "-C", str(repo_path), "rev-parse", "--show-toplevel"],
        timeout_seconds=timeout_seconds,
    )
    if git_check.returncode != 0:
        message = _failure_message(
            "Repository is not a Git working tree", git_check
        )
        return _failure_report(
            repository=repository_display,
            command=requested_command,
            message=message,
            clone_error=message,
            output=git_check.output,
            run_docker=run_docker,
            duration_seconds=git_check.duration_seconds,
        )

    git_root_text = git_check.stdout.strip().splitlines()
    if not git_root_text:
        message = "Git did not report a repository root."
        return _failure_report(
            repository=repository_display,
            command=requested_command,
            message=message,
            clone_error=message,
            output=git_check.output,
            run_docker=run_docker,
            duration_seconds=git_check.duration_seconds,
        )
    git_root = pathlib.Path(git_root_text[0]).expanduser()

    try:
        with tempfile.TemporaryDirectory(prefix="rebuildcheck-") as temporary_dir:
            clone_dir = pathlib.Path(temporary_dir) / "repo"
            clone = _run_command(
                [
                    "git",
                    "clone",
                    "--quiet",
                    "--",
                    str(git_root),
                    str(clone_dir),
                ],
                timeout_seconds=timeout_seconds,
            )
            if clone.returncode != 0:
                message = _failure_message("Git clone failed", clone)
                return _failure_report(
                    repository=repository_display,
                    command=requested_command,
                    message=message,
                    clone_error=message,
                    output=clone.output,
                    run_docker=run_docker,
                    duration_seconds=clone.duration_seconds,
                )

            detected_command, detection_message = _select_command(
                clone_dir, command
            )
            if detection_message is not None:
                test_result = CleanRoomTestResult(
                    status="failed",
                    command=requested_command,
                    message=detection_message,
                )
            elif detected_command is None:
                test_result = CleanRoomTestResult(
                    status="skipped",
                    message="No test command was detected; clean-room test skipped.",
                )
            else:
                test_result = _run_test_command(
                    detected_command, clone_dir, timeout_seconds
                )

            docker_result = _run_docker_build(
                clone_dir=clone_dir,
                run_docker=run_docker,
                timeout_seconds=timeout_seconds,
            )
            return _make_report(
                repository=repository_display,
                test=test_result,
                docker=docker_result,
            )
    except (OSError, RuntimeError, ValueError) as exc:
        message = f"Unable to create or use a clean-room directory: {_exception_text(exc)}"
        return _failure_report(
            repository=repository_display,
            command=requested_command,
            message=message,
            clone_error=message,
            run_docker=run_docker,
        )
    except Exception as exc:
        # A filesystem or subprocess implementation can still raise an
        # unexpected exception (for example from a platform-specific backend).
        # Keep the verifier useful to a CLI by converting it to a result.
        message = f"Clean-room verification failed unexpectedly: {_exception_text(exc)}"
        return _failure_report(
            repository=repository_display,
            command=requested_command,
            message=message,
            clone_error=message,
            run_docker=run_docker,
        )


def _run_test_command(
    command: str, clone_dir: pathlib.Path, timeout_seconds: int
) -> CleanRoomTestResult:
    arguments, normalized, error = _parse_command(command)
    if error is not None or arguments is None:
        return CleanRoomTestResult(
            status="failed",
            command=normalized,
            message=error or "Invalid test command.",
        )

    outcome = _run_command(
        arguments,
        cwd=clone_dir,
        timeout_seconds=timeout_seconds,
    )
    if outcome.returncode == 0:
        status: Status = "passed"
        message = "Test command passed."
    else:
        status = "failed"
        message = _failure_message("Test command failed", outcome)
    return CleanRoomTestResult(
        status=status,
        command=normalized,
        returncode=outcome.returncode,
        output=outcome.output,
        message=message,
        duration_seconds=outcome.duration_seconds,
    )


def _run_docker_build(
    clone_dir: pathlib.Path,
    run_docker: bool,
    timeout_seconds: int,
) -> DockerResult:
    command = "docker build ."
    if not run_docker:
        return DockerResult(
            status="skipped",
            command=None,
            message="Docker build was not requested; skipped.",
        )

    try:
        docker_executable = shutil.which("docker")
    except Exception as exc:
        docker_executable = None
        which_error = _exception_text(exc)
    else:
        which_error = ""

    if not docker_executable:
        message = "Docker executable is unavailable; Docker build skipped."
        if which_error:
            message = f"Docker executable check failed ({which_error}); Docker build skipped."
        return DockerResult(status="skipped", command=command, message=message)

    outcome = _run_command(
        [docker_executable, "build", "."],
        cwd=clone_dir,
        timeout_seconds=timeout_seconds,
    )
    if outcome.returncode == 0:
        status: Status = "passed"
        message = "Docker build passed."
    else:
        status = "failed"
        message = _failure_message("Docker build failed", outcome)
    return DockerResult(
        status=status,
        command=command,
        returncode=outcome.returncode,
        output=outcome.output,
        message=message,
        duration_seconds=outcome.duration_seconds,
    )


def _select_command(
    clone_dir: pathlib.Path, command: str | None
) -> tuple[str | None, str | None]:
    """Choose an explicit command or detect one from common project files."""

    if command is not None:
        if not isinstance(command, str):
            return None, "Test command must be a string or None."
        normalized = command.strip()
        if not normalized:
            return None, "Test command is empty."
        _, _, error = _parse_command(normalized)
        return (normalized, None) if error is None else (None, error)

    documented_command = _command_from_readme(clone_dir)
    if documented_command is not None:
        return documented_command, None

    makefile = clone_dir / "Makefile"
    if makefile.is_file():
        try:
            makefile_text = makefile.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            return None, f"Unable to inspect Makefile: {_exception_text(exc)}"
        if _TEST_TARGET_RE.search(makefile_text):
            return "make test", None

    package_json = clone_dir / "package.json"
    if package_json.is_file():
        try:
            package = json.loads(
                package_json.read_text(encoding="utf-8", errors="replace")
            )
        except (OSError, json.JSONDecodeError) as exc:
            return None, f"Unable to inspect package.json: {_exception_text(exc)}"
        scripts = package.get("scripts") if isinstance(package, dict) else None
        if (
            isinstance(scripts, dict)
            and isinstance(scripts.get("test"), str)
            and scripts["test"].strip()
        ):
            return "npm test", None

    if (clone_dir / "Cargo.toml").is_file():
        return "cargo test", None
    if (clone_dir / "go.mod").is_file():
        return "go test ./...", None

    test_directories = (
        clone_dir / "tests",
        clone_dir / "test",
    )
    if any(directory.is_dir() for directory in test_directories):
        return "python -m unittest discover -s tests -v", None

    if any(
        (clone_dir / filename).is_file()
        for filename in ("pytest.ini", "tox.ini", ".pytest.ini")
    ):
        return "python -m pytest", None

    pyproject = clone_dir / "pyproject.toml"
    if pyproject.is_file():
        try:
            import tomllib

            pyproject_data = tomllib.loads(
                pyproject.read_text(encoding="utf-8", errors="replace")
            )
        except (OSError, ValueError, UnicodeError) as exc:
            return None, f"Unable to inspect pyproject.toml: {_exception_text(exc)}"
        if isinstance(pyproject_data, dict) and isinstance(
            pyproject_data.get("tool"), dict
        ):
            tools = pyproject_data["tool"]
            if isinstance(tools.get("pytest"), dict):
                return "python -m pytest", None

    return None, None


def _command_from_readme(clone_dir: pathlib.Path) -> str | None:
    for name in ("README.md", "README.rst", "README.txt", "README"):
        readme = clone_dir / name
        if not readme.is_file():
            continue
        try:
            text = readme.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for pattern in _README_COMMAND_PATTERNS:
            match = pattern.search(text)
            if match is None:
                continue
            candidate = match.group(0).strip().strip("`\"'")
            candidate = re.split(
                r"\s+#|\s+which\s+", candidate, maxsplit=1, flags=re.IGNORECASE
            )[0]
            candidate = candidate.rstrip(".,:;)]\"").strip()
            if candidate:
                return candidate
    return None


def _parse_command(
    command: str,
) -> tuple[list[str] | None, str | None, str | None]:
    if not isinstance(command, str):
        return None, None, "Test command must be a string."
    normalized = command.strip()
    if not normalized:
        return None, normalized, "Test command is empty."
    try:
        arguments = shlex.split(normalized, posix=True)
    except ValueError as exc:
        return None, normalized, f"Invalid test command quoting: {_exception_text(exc)}"
    if not arguments:
        return None, normalized, "Test command is empty."
    if any("\x00" in argument for argument in arguments):
        return None, normalized, "Invalid test command: NUL bytes are not allowed."
    if any(argument in _SHELL_CONTROL_TOKENS for argument in arguments):
        return (
            None,
            normalized,
            "Invalid test command: shell control operators are not supported.",
        )
    return arguments, normalized, None


def _run_command(
    arguments: list[str],
    *,
    cwd: pathlib.Path | None = None,
    timeout_seconds: int,
) -> _CommandOutcome:
    started = time.monotonic()
    try:
        completed = subprocess.run(
            arguments,
            cwd=str(cwd) if cwd is not None else None,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        return _CommandOutcome(
            returncode=None,
            stdout=_as_text(exc.stdout),
            stderr=_as_text(exc.stderr),
            timed_out=True,
            error=f"Command timed out after {timeout_seconds} seconds.",
            duration_seconds=_duration(started),
        )
    except (OSError, ValueError, TypeError) as exc:
        return _CommandOutcome(
            returncode=None,
            error=f"Unable to run command: {_exception_text(exc)}",
            duration_seconds=_duration(started),
        )
    except Exception as exc:
        return _CommandOutcome(
            returncode=None,
            error=f"Unable to run command: {_exception_text(exc)}",
            duration_seconds=_duration(started),
        )

    return _CommandOutcome(
        returncode=completed.returncode,
        stdout=_as_text(completed.stdout),
        stderr=_as_text(completed.stderr),
        duration_seconds=_duration(started),
    )


def _make_report(
    repository: str,
    test: CleanRoomTestResult,
    docker: DockerResult,
) -> VerificationReport:
    if test.status == "failed" or docker.status == "failed":
        status: Status = "failed"
    elif test.status == "passed" and docker.status in {"passed", "skipped"}:
        status = "passed"
    else:
        status = "skipped"

    summary = f"Clean-room test: {test.status}; Docker build: {docker.status}."
    return VerificationReport(
        status=status,
        repository=repository,
        test=test,
        docker=docker,
        summary=summary,
    )


def _failure_report(
    *,
    repository: str,
    command: str | None,
    message: str,
    clone_error: str,
    run_docker: bool,
    output: str = "",
    duration_seconds: float | None = None,
) -> VerificationReport:
    test = CleanRoomTestResult(
        status="failed",
        command=command,
        output=output,
        message=message,
        duration_seconds=duration_seconds,
    )
    docker = DockerResult(
        status="skipped",
        command="docker build ." if run_docker else None,
        message="Docker build skipped because the clean-room clone was not available.",
    )
    report = _make_report(repository=repository, test=test, docker=docker)
    report.clone_error = clone_error
    return report


def _validate_status(status: str) -> None:
    if status not in _VALID_STATUSES:
        raise ValueError(
            f"status must be one of {sorted(_VALID_STATUSES)}, got {status!r}"
        )


def _validate_timeout(timeout_seconds: int) -> str | None:
    if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, int):
        return "timeout_seconds must be a positive integer."
    if timeout_seconds <= 0:
        return "timeout_seconds must be greater than zero."
    return None


def _failure_message(prefix: str, outcome: _CommandOutcome) -> str:
    if outcome.error:
        return f"{prefix}: {outcome.error}"
    if outcome.returncode is None:
        return f"{prefix}: command did not return a status."
    return f"{prefix}: command exited with status {outcome.returncode}."


def _compact_output(stdout: str, stderr: str) -> str:
    sections: list[str] = []
    clean_stdout = _as_text(stdout).strip()
    clean_stderr = _as_text(stderr).strip()
    if clean_stdout:
        sections.append(f"stdout:\n{clean_stdout}")
    if clean_stderr:
        sections.append(f"stderr:\n{clean_stderr}")
    text = "\n\n".join(sections)
    if len(text) <= _MAX_OUTPUT_CHARS:
        return text
    marker = "\n...[output truncated]...\n"
    remaining = max(0, _MAX_OUTPUT_CHARS - len(marker))
    head_size = remaining // 2
    tail_size = remaining - head_size
    return f"{text[:head_size]}{marker}{text[-tail_size:]}"


def _duration(started: float) -> float:
    return round(max(0.0, time.monotonic() - started), 3)


def _as_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _exception_text(exc: BaseException) -> str:
    text = str(exc).strip()
    return text or exc.__class__.__name__


def _display_repository(repo: object) -> str:
    try:
        return str(repo)
    except Exception:
        return repr(repo)


def _display_requested_command(command: object) -> str | None:
    if command is None:
        return None
    if isinstance(command, str):
        return command.strip()
    return _as_text(command)


__all__ = [
    "CleanRoomResult",
    "CleanRoomTestResult",
    "DockerResult",
    "Report",
    "Status",
    "VerificationReport",
    "verify_clean_room",
]
