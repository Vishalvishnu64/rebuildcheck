import io
import json
import shutil
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from rebuildcheck.checks import (
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
from rebuildcheck.cli import inspect_repository, main, run_static_checks
from rebuildcheck.verification import verify_clean_room


ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which("git"), "git is required for repository fixtures")
class CheckFixtureTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="rebuildcheck-tests-")
        self.root = Path(self.temporary.name) / "repo"
        self.root.mkdir()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def write(self, relative: str, content: str | bytes) -> Path:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            path.write_bytes(content)
        else:
            path.write_text(content, encoding="utf-8")
        return path

    def git(self, *arguments: str) -> None:
        subprocess.run(
            ["git", *arguments],
            cwd=self.root,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

    def commit(self) -> None:
        self.git("init", "-q")
        self.git("config", "user.name", "rebuildcheck tests")
        self.git("config", "user.email", "rebuildcheck@example.invalid")
        self.git("add", ".")
        self.git("commit", "-qm", "fixture: initial commit")

    def make_complete_repo(self) -> Path:
        self.write(
            "README.md",
            "# Fixture\n\n"
            "## Setup\nInstall Python.\n\n"
            "## Run\nRun the application.\n\n"
            "## Test\nRun `make test`.\n",
        )
        self.write(".python-version", "3.12\n")
        self.write("requirements.lock", "# standard library only\n")
        self.write("Dockerfile", "FROM python:3.12.8-slim-bookworm\n")
        self.write(".env.example", "API" + "_KEY=${API_KEY}\n")
        self.write("Makefile", "test:\n\tpython -m unittest discover -s tests -v\n")
        self.write(
            "tests/test_fixture.py",
            "import unittest\n\n"
            "class FixtureTests(unittest.TestCase):\n"
            "    def test_ok(self):\n"
            "        self.assertTrue(True)\n",
        )
        self.commit()
        return self.root

    def test_empty_repository_is_safe_and_reports_missing_inputs(self) -> None:
        results = run_static_checks(self.root)

        self.assertEqual(len(results), 9)
        self.assertFalse(next(item for item in results if item.name == "git_repository").passed)
        self.assertFalse(next(item for item in results if item.name == "dockerfile").passed)

    def test_non_git_folder_fails_git_check(self) -> None:
        self.write("note.txt", "not a Git repository\n")

        result = git_repository.run(self.root)

        self.assertFalse(result.passed)
        self.assertIn("not a Git worktree", result.reason)

    def test_git_repository_with_no_commits_fails(self) -> None:
        self.git("init", "-q")

        result = git_repository.run(self.root)

        self.assertFalse(result.passed)
        self.assertIn("no commit", result.reason)

    def test_clean_git_repository_passes(self) -> None:
        self.write("README.md", "clean\n")
        self.commit()

        result = git_repository.run(self.root)

        self.assertTrue(result.passed)

    def test_dirty_working_tree_fails(self) -> None:
        self.write("README.md", "clean\n")
        self.commit()
        self.write("README.md", "changed\n")

        result = git_repository.run(self.root)

        self.assertFalse(result.passed)
        self.assertIn("uncommitted", result.reason)

    def test_nested_directory_is_not_mistaken_for_a_repository_root(self) -> None:
        self.make_complete_repo()
        nested = self.root / "nested"
        nested.mkdir()

        result = git_repository.run(nested)

        self.assertFalse(result.passed)
        self.assertIn("inside another Git worktree", result.reason)

    def test_dependency_lockfile_passes(self) -> None:
        self.write("requirements.lock", "# pinned standard-library project\n")

        result = dependency_lockfile.run(self.root)

        self.assertTrue(result.passed)

    def test_missing_dependency_lockfile_fails(self) -> None:
        self.write("pyproject.toml", "[project]\nname = 'fixture'\n")

        result = dependency_lockfile.run(self.root)

        self.assertFalse(result.passed)
        self.assertIn("No recognized", result.reason)

    def test_unpinned_requirements_file_fails(self) -> None:
        self.write("requirements.txt", "requests\n")

        result = dependency_lockfile.run(self.root)

        self.assertFalse(result.passed)

    def test_runtime_version_passes_for_python_version_file(self) -> None:
        self.write(".python-version", "3.12.8\n")

        result = runtime_version.run(self.root)

        self.assertTrue(result.passed)
        self.assertEqual(result.details["pins"][0]["version"], "3.12.8")

    def test_runtime_version_fails_for_unpinned_range(self) -> None:
        self.write("pyproject.toml", "[project]\nrequires-python = '>=3.12'\n")

        result = runtime_version.run(self.root)

        self.assertFalse(result.passed)

    def test_dockerfile_check_passes_with_from(self) -> None:
        self.write("Dockerfile", "FROM python:3.12.8-slim-bookworm\n")

        result = dockerfile.run(self.root)

        self.assertTrue(result.passed)

    def test_dockerfile_check_fails_without_from(self) -> None:
        self.write("Dockerfile", "RUN echo no-base-image\n")

        result = dockerfile.run(self.root)

        self.assertFalse(result.passed)
        self.assertIn("no usable FROM", result.reason)

    def test_readme_sections_pass_with_markdown_headings(self) -> None:
        self.write(
            "README.md",
            "# Fixture\n## Setup\n## Usage\n## Testing\n",
        )

        result = readme_sections.run(self.root)

        self.assertTrue(result.passed)

    def test_readme_sections_fail_when_test_heading_is_missing(self) -> None:
        self.write("README.md", "# Fixture\n## Setup\n## Run\n")

        result = readme_sections.run(self.root)

        self.assertFalse(result.passed)
        self.assertEqual(result.details["missing"], ["test"])

    def test_test_command_passes_for_documented_make_command(self) -> None:
        self.write("README.md", "## Test\nRun `make test`.\n")

        result = test_command.run(self.root)

        self.assertTrue(result.passed)
        self.assertEqual(result.details["command"], "make test")

    def test_test_command_fails_for_undocumented_tests(self) -> None:
        self.write("README.md", "## Test\nTests are important.\n")

        result = test_command.run(self.root)

        self.assertFalse(result.passed)

    def test_environment_example_passes_with_placeholders(self) -> None:
        self.write(
            ".env.example",
            "API" + "_KEY=${API_KEY}\n"
            "SERVICE_URL=https://localhost:8000\n",
        )

        result = environment_example.run(self.root)

        self.assertTrue(result.passed)

    def test_environment_example_fails_with_real_sensitive_value(self) -> None:
        self.write(".env.example", "API" + "_KEY=real-not-a-placeholder\n")

        result = environment_example.run(self.root)

        self.assertFalse(result.passed)
        self.assertIn("non-placeholder", result.reason)

    def test_absolute_path_check_passes_for_relative_documentation(self) -> None:
        self.write("README.md", "Use `<repo_path>` supplied at runtime.\n")

        result = absolute_paths.run(self.root)

        self.assertTrue(result.passed)

    def test_absolute_path_check_finds_unix_home_path(self) -> None:
        unix_path = "/" + "home/" + "user/" + "project"
        self.write("config.txt", f"PROJECT_PATH={unix_path}\n")

        result = absolute_paths.run(self.root)

        self.assertFalse(result.passed)
        self.assertEqual(result.details["findings"][0]["kind"], "unix-user-path")

    def test_absolute_path_check_finds_windows_users_path(self) -> None:
        windows_path = "C:" + "\\" + "Users" + "\\" + "judge" + "\\" + "repo"
        self.write("config.txt", f"PROJECT_PATH={windows_path}\n")

        result = absolute_paths.run(self.root)

        self.assertFalse(result.passed)
        self.assertEqual(result.details["findings"][0]["kind"], "windows-user-path")

    def test_secret_check_passes_for_environment_placeholder_and_readme_example(self) -> None:
        self.write(".env.example", "API" + "_KEY=${API_KEY}\n")
        self.write("README.md", "The token is loaded from the environment.\n")

        result = committed_secrets.run(self.root)

        self.assertTrue(result.passed)

    def test_secret_check_finds_aws_access_key(self) -> None:
        aws_key = "AKIA" + "A" * 16
        self.write("config.txt", f"ACCESS_VALUE={aws_key}\n")

        result = committed_secrets.run(self.root)

        self.assertFalse(result.passed)
        self.assertEqual(result.details["findings"][0]["kind"], "aws-access-key")

    def test_secret_check_finds_github_token(self) -> None:
        github_value = "ghp_" + "a" * 24
        token_key = "TOKEN" + "_VALUE"
        self.write("config.txt", f"{token_key}={github_value}\n")

        result = committed_secrets.run(self.root)

        self.assertFalse(result.passed)
        self.assertEqual(result.details["findings"][0]["kind"], "github-token")

    def test_binary_file_is_skipped_by_text_scanners(self) -> None:
        aws_key = "AKIA" + "B" * 16
        self.write("image.bin", b"\x00" + aws_key.encode("ascii") + b"\x00")

        self.assertTrue(absolute_paths.run(self.root).passed)
        self.assertTrue(committed_secrets.run(self.root).passed)

    def test_very_large_file_is_reported_as_unreadable(self) -> None:
        self.write("large.txt", b"x" * (4 * 1024 * 1024 + 1))

        result = absolute_paths.run(self.root)

        self.assertFalse(result.passed)
        self.assertIn("could not be read", result.reason)

    def test_unreadable_file_is_reported_without_crashing(self) -> None:
        path = self.write("notes.txt", "not readable for this check\n")

        with patch("rebuildcheck.checks.absolute_paths.read_bytes", return_value=None):
            result = absolute_paths.run(self.root)

        self.assertFalse(result.passed)
        self.assertIn(path.name, result.details["unreadable"])

    def test_marked_fixture_is_not_scanned_when_parent_project_scans_itself(self) -> None:
        self.write("README.md", "parent project\n")
        self.commit()
        fixture = self.root / "examples" / "broken"
        fixture.mkdir(parents=True)
        (fixture / ".rebuildcheck-fixture").write_text("fixture\n", encoding="utf-8")
        (fixture / "bad.txt").write_text(
            "/" + "home/user/repo\n" + "TOKEN=" + "AKIA" + "C" * 16 + "\n",
            encoding="utf-8",
        )
        self.git("add", ".")
        self.git("commit", "-qm", "fixture: add marked sample")

        self.assertTrue(absolute_paths.run(self.root).passed)
        self.assertTrue(committed_secrets.run(self.root).passed)

        self.assertFalse(absolute_paths.run(fixture).passed)
        self.assertFalse(committed_secrets.run(fixture).passed)

    def test_current_project_examples_and_documentation_do_not_trigger_detectors(self) -> None:
        self.assertTrue(absolute_paths.run(ROOT).passed)
        self.assertTrue(committed_secrets.run(ROOT).passed)

    def test_dockerfile_has_pinned_real_tag_and_runtime_layout(self) -> None:
        dockerfile_text = (ROOT / "Dockerfile").read_text(encoding="utf-8")
        self.assertIn(
            "python:3.12.8-slim-bookworm@sha256:2199a62885a12290dc9c5be3ca0681d367576ab7bf037da120e564723292a2f0",
            dockerfile_text,
        )
        self.assertIn("WORKDIR /app", dockerfile_text)
        self.assertIn("COPY . .", dockerfile_text)
        self.assertIn("python -m unittest discover -s tests -v", dockerfile_text)
        self.assertIn('ENTRYPOINT ["python", "-m", "rebuildcheck"]', dockerfile_text)
        self.assertIn('CMD ["--help"]', dockerfile_text)
        self.assertNotIn("C:" + "\\" + "Users", dockerfile_text)

    def test_docker_documentation_has_cli_and_container_test_commands(self) -> None:
        readme = (ROOT / "README.md").read_text(encoding="utf-8")

        self.assertIn("docker run --rm rebuildcheck --help", readme)
        self.assertIn(
            "docker run --rm --entrypoint python rebuildcheck -m unittest",
            readme,
        )

    def test_timeout_is_reported_for_hanging_test_command(self) -> None:
        self.make_complete_repo()

        result = verify_clean_room(
            self.root,
            'python -c "import time; time.sleep(2)"',
            timeout_seconds=1,
        )

        self.assertEqual(result.test.status, "failed")
        self.assertIn("timed out", result.test.message.casefold())

    def test_json_cli_output_is_valid_for_complete_fixture(self) -> None:
        self.make_complete_repo()
        output = io.StringIO()

        with redirect_stdout(output):
            exit_code = main([str(self.root), "--json", "--no-run"])

        document = json.loads(output.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertTrue(document["passed"])

    def test_broken_cli_report_is_nonzero_and_fixes_are_priority_ordered(self) -> None:
        self.make_complete_repo()
        self.write("requirements.lock", "")
        self.write(
            "bad.txt",
            "/" + "home/user/repo\n" + "TOKEN=" + "AKIA" + "D" * 16 + "\n",
        )
        self.git("add", ".")
        self.git("commit", "-qm", "fixture: deliberate failures")

        report = inspect_repository(self.root, no_run=True)

        self.assertFalse(report.passed)
        self.assertEqual(report.fixes, sorted(report.fixes, key=lambda item: (item["priority"], item["check"])))

    def test_broken_example_cli_is_nonzero_and_json_is_valid(self) -> None:
        output = io.StringIO()

        with redirect_stdout(output):
            exit_code = main(
                [str(ROOT / "examples" / "broken"), "--json"]
            )

        document = json.loads(output.getvalue())
        names = {item["check"] for item in document["fixes"]}
        self.assertNotEqual(exit_code, 0)
        self.assertFalse(document["passed"])
        self.assertEqual(document["score"], 50)
        self.assertTrue({"dependency_lockfile", "absolute_paths", "committed_secrets"} <= names)
        self.assertEqual(
            document["fixes"],
            sorted(document["fixes"], key=lambda item: (item["priority"], item["check"])),
        )

    def test_passing_example_materializes_as_a_clean_standalone_repo(self) -> None:
        import sys

        completed = subprocess.run(
            [sys.executable, "examples/materialize.py", "passing"],
            cwd=ROOT,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("Score: 100/100", completed.stdout)
