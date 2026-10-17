import io
import json
import shutil
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from rebuildcheck.cli import inspect_repository, main
from rebuildcheck.verification import verify_clean_room


@unittest.skipUnless(shutil.which("git"), "git is required for repository tests")
class RepositoryBehaviorTests(unittest.TestCase):
    def make_repo(self, *, slow_test: bool = False) -> Path:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name) / "fixture"
        root.mkdir()

        make_test = (
            'test:\n\tpython -c "import time; time.sleep(2)"\n'
            if slow_test
            else "test:\n\tpython -m unittest discover -s tests -v\n"
        )
        files = {
            "README.md": (
                "# Fixture\n\n"
                "## Setup\nUse Python 3.12.\n\n"
                "## Run\nRun the checker.\n\n"
                "## Test\nRun `make test`.\n"
            ),
            ".python-version": "3.12\n",
            "requirements.lock": "# no third-party dependencies\n",
            "Dockerfile": "FROM python:3.12-slim\n",
            ".env.example": "API" + "_KEY=" + "$" + "{API_KEY}\n",
            "Makefile": make_test,
            "tests/test_fixture.py": (
                "import unittest\n\n"
                "class FixtureTests(unittest.TestCase):\n"
                "    def test_fixture(self):\n"
                "        self.assertTrue(True)\n"
            ),
        }
        for relative, content in files.items():
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")

        self.git(root, "init", "-q")
        self.git(root, "config", "user.name", "rebuildcheck tests")
        self.git(root, "config", "user.email", "rebuildcheck@example.invalid")
        self.git(root, "add", ".")
        self.git(root, "commit", "-qm", "fixture: initial commit")
        return root

    @staticmethod
    def git(root: Path, *arguments: str) -> None:
        subprocess.run(
            ["git", *arguments],
            cwd=root,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

    def test_clean_room_report_passes(self) -> None:
        root = self.make_repo()

        report = inspect_repository(root)

        self.assertTrue(report.passed)
        self.assertEqual(report.score, 100)
        self.assertEqual(report.verification.test.status, "passed")
        self.assertEqual(report.verification.test.command, "make test")

    def test_no_run_skips_execution_but_runs_static_checks(self) -> None:
        root = self.make_repo()

        report = inspect_repository(root, no_run=True)

        self.assertTrue(report.passed)
        self.assertEqual(report.verification.test.status, "skipped")
        self.assertEqual(report.verification.docker.status, "skipped")
        self.assertEqual(report.score, 100)

    def test_json_cli_output_is_machine_readable(self) -> None:
        root = self.make_repo()
        output = io.StringIO()

        with redirect_stdout(output):
            exit_code = main([str(root), "--json", "--no-run"])

        document = json.loads(output.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertTrue(document["passed"])
        self.assertEqual(document["score"], 100)
        self.assertEqual(document["repository"], "fixture")

    def test_missing_repository_returns_failures_instead_of_crashing(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)

        report = inspect_repository(Path(temporary.name) / "missing", no_run=True)

        self.assertFalse(report.passed)
        self.assertEqual(len(report.checks), 9)
        self.assertTrue(report.fixes)

    def test_secret_and_absolute_path_checks_fail_with_concrete_findings(self) -> None:
        root = self.make_repo()
        fake_key = "AKIA" + "A" * 16
        machine_path = "/" + "home/" + "judge/" + "repo"
        key_name = "TO" + "KEN"
        (root / "notes.txt").write_text(
            f"{key_name}={fake_key}\nPATH={machine_path}\n",
            encoding="utf-8",
        )
        self.git(root, "add", "notes.txt")
        self.git(root, "commit", "-qm", "fixture: add bad values")

        report = inspect_repository(root, no_run=True)
        results = {check.name: check for check in report.checks}

        self.assertFalse(results["absolute_paths"].passed)
        self.assertFalse(results["committed_secrets"].passed)
        self.assertIn("Replace", results["absolute_paths"].fix)
        self.assertIn("Revoke", results["committed_secrets"].fix)

    def test_timeout_is_reported_as_a_failed_clean_room_test(self) -> None:
        root = self.make_repo(slow_test=True)

        report = verify_clean_room(
            root,
            'python -c "import time; time.sleep(2)"',
            timeout_seconds=1,
        )

        self.assertEqual(report.test.status, "failed")
        self.assertIn("timed out", report.test.message.casefold())

    def test_requested_docker_is_skipped_when_docker_is_unavailable(self) -> None:
        root = self.make_repo()

        with patch("rebuildcheck.verification.shutil.which", return_value=None):
            report = verify_clean_room(root, "make test", run_docker=True)

        self.assertEqual(report.test.status, "passed")
        self.assertEqual(report.docker.status, "skipped")
        self.assertIn("unavailable", report.docker.message)


if __name__ == "__main__":
    unittest.main()
