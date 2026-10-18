import json
import tempfile
import unittest
from pathlib import Path

from rebuildcheck.checks import (
    dependency_lockfile,
    environment_example,
    runtime_version,
    test_command,
)
from rebuildcheck.cli import inspect_repository, render_human, render_json
from rebuildcheck.project_type import (
    detect_project_type,
    detect_project_types,
    project_type_candidates,
)


class MultiLanguageFixtureTests(unittest.TestCase):
    """Exercise supported ecosystem markers with disposable repositories."""

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="rebuildcheck-multilang-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / "fixture"
        self.root.mkdir()

    def write(self, relative: str, content: str) -> None:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def test_python_lock_runtime_and_test_markers(self) -> None:
        for lockfile in ("requirements.lock", "poetry.lock", "uv.lock"):
            for command in (
                "python -m pytest",
                "python -m unittest discover -s tests",
            ):
                with self.subTest(lockfile=lockfile, command=command):
                    self.write(lockfile, "lock data\n")
                    self.write(".python-version", "3.12.4\n")
                    self.write("README.md", f"## Test\nRun `{command}`.\n")

                    self.assertTrue(dependency_lockfile.run(self.root).passed)
                    self.assertTrue(runtime_version.run(self.root).passed)
                    self.assertTrue(test_command.run(self.root).passed)

                    (self.root / lockfile).unlink()
                    (self.root / ".python-version").unlink()
                    (self.root / "README.md").unlink()

    def test_node_markers(self) -> None:
        for lockfile in ("package-lock.json", "yarn.lock", "pnpm-lock.yaml"):
            with self.subTest(lockfile=lockfile):
                self.write(lockfile, "lock data\n")
                self.write(".nvmrc", "20.11.1\n")
                self.write("README.md", "## Test\nRun `npm test`.\n")

                self.assertTrue(dependency_lockfile.run(self.root).passed)
                self.assertTrue(runtime_version.run(self.root).passed)
                self.assertTrue(test_command.run(self.root).passed)
                self.assertEqual(detect_project_type(self.root), "node")

                (self.root / lockfile).unlink()
                (self.root / ".nvmrc").unlink()
                (self.root / "README.md").unlink()

    def test_go_markers_and_project_file_test_fallback(self) -> None:
        self.write("go.sum", "module.example.test v1.2.3 h1:checksum\n")
        self.write("go.mod", "module module.example.test\n\ngo 1.22\n")
        self.write("README.md", "## Test\nRun `go test ./...`.\n")

        self.assertTrue(dependency_lockfile.run(self.root).passed)
        self.assertTrue(runtime_version.run(self.root).passed)
        self.assertTrue(test_command.run(self.root).passed)
        (self.root / "README.md").unlink()
        self.assertEqual(test_command.detect_test_command(self.root), "go test ./...")
        self.assertEqual(detect_project_type(self.root), "go")

    def test_rust_markers(self) -> None:
        self.write("Cargo.lock", "version = 3\n")
        self.write("rust-toolchain.toml", '[toolchain]\nchannel = "1.78.0"\n')
        self.write("README.md", "## Test\nRun `cargo test`.\n")

        self.assertTrue(dependency_lockfile.run(self.root).passed)
        self.assertTrue(runtime_version.run(self.root).passed)
        self.assertTrue(test_command.run(self.root).passed)
        self.assertEqual(detect_project_type(self.root), "rust")

    def test_dart_and_flutter_markers_and_test_fallbacks(self) -> None:
        self.write(
            "pubspec.yaml",
            "name: sample_dart\n"
            "dependencies:\n"
            "  collection: ^1.18.0\n",
        )
        self.write("pubspec.lock", "packages:\n  collection:\n    dependency: direct main\n")

        self.assertEqual(project_type_candidates(self.root), ("dart",))
        self.assertEqual(detect_project_type(self.root), "dart")
        self.assertTrue(dependency_lockfile.run(self.root).passed)
        self.assertEqual(test_command.detect_test_command(self.root), "dart test")

        self.write(
            "pubspec.yaml",
            "name: sample_flutter\n"
            "dependencies:\n"
            "  flutter:\n"
            "    sdk: flutter\n"
            "dev_dependencies:\n"
            "  flutter_test:\n"
            "    sdk: flutter\n",
        )
        self.assertEqual(project_type_candidates(self.root), ("flutter",))
        self.assertEqual(detect_project_type(self.root), "flutter")
        self.assertEqual(test_command.detect_test_command(self.root), "flutter test")

        self.write("README.md", "## Test\nRun `flutter test --coverage`.\n")
        result = test_command.run(self.root)
        self.assertTrue(result.passed)
        self.assertEqual(result.details["command"], "flutter test --coverage")

    def test_unrecognised_root_reports_immediate_projects_in_cli_data(self) -> None:
        self.write("python-app/pyproject.toml", "[project]\nname = 'sample'\n")
        self.write("flutter-app/pubspec.yaml", "name: sample\ndependencies:\n  flutter:\n    sdk: flutter\n")
        self.write("ambiguous/package.json", "{}\n")
        self.write("ambiguous/go.mod", "module example.test\ngo 1.22\n")
        self.write("nested/hidden/package.json", "{}\n")

        self.assertEqual(detect_project_type(self.root), "unknown")
        self.assertEqual(
            detect_project_types(self.root),
            [
                {"path": "flutter-app", "type": "flutter"},
                {"path": "python-app", "type": "python"},
            ],
        )

        report = inspect_repository(self.root, no_run=True)
        document = json.loads(render_json(report))
        self.assertEqual(document["project_type"], "unknown")
        self.assertEqual(document["projects"], detect_project_types(self.root))
        human = render_human(report)
        self.assertIn("python-app: Python", human)
        self.assertIn("flutter-app: Flutter", human)

    def test_missing_environment_example_priority_depends_on_source_references(self) -> None:
        without_reference = environment_example.run(self.root)
        self.assertFalse(without_reference.passed)
        self.assertEqual(without_reference.priority, 3)
        self.assertEqual(without_reference.details["references"], [])

        self.write("app.py", "import os\nservice_url = os.getenv('SERVICE_URL')\n")
        with_reference = environment_example.run(self.root)
        self.assertFalse(with_reference.passed)
        self.assertEqual(with_reference.priority, 1)
        self.assertEqual(with_reference.details["references"][0]["path"], "app.py")

    def test_missing_lockfile_fix_precedes_no_environment_example_fix(self) -> None:
        self.write("pyproject.toml", "[project]\nname = 'sample'\n")
        report = inspect_repository(self.root, no_run=True)
        priorities = {
            item["check"]: item["priority"]
            for item in report.fixes
        }
        self.assertLess(
            priorities["dependency_lockfile"],
            priorities["environment_example"],
        )

    def test_detector_is_deterministic_and_neutral_for_unknown_or_ambiguous(self) -> None:
        self.assertEqual(detect_project_type(self.root), "unknown")
        self.assertEqual(project_type_candidates(self.root), ())

        self.write("go.mod", "module module.example.test\n\ngo 1.22\n")
        self.write("package.json", '{"scripts": {"test": "node test.js"}}\n')

        self.assertEqual(project_type_candidates(self.root), ("node", "go"))
        self.assertEqual(detect_project_type(self.root), "unknown")

    def test_report_exposes_project_type_in_human_and_json_forms(self) -> None:
        self.write("package.json", '{"scripts": {"test": "node test.js"}}\n')

        report = inspect_repository(self.root, no_run=True)
        document = json.loads(render_json(report))

        self.assertEqual(report.project_type, "node")
        self.assertEqual(document["project_type"], "node")
        self.assertIn("Project type: Node", render_human(report))

    def test_unpinned_or_missing_ecosystem_markers_fail_in_disposable_fixtures(self) -> None:
        fixtures = (
            {
                "name": "node",
                "files": {".nvmrc": "20\n", "README.md": "## Test\nRun the tests.\n"},
            },
            {
                "name": "go",
                "files": {
                    "go.mod": "module module.example.test\n\ngo 1\n",
                    "README.md": "## Test\nRun the checks.\n",
                },
            },
            {
                "name": "rust",
                "files": {
                    "rust-toolchain.toml": '[toolchain]\nchannel = "stable"\n',
                    "README.md": "## Test\nRun the checks.\n",
                },
            },
        )

        for fixture in fixtures:
            with self.subTest(project_type=fixture["name"]):
                for relative, content in fixture["files"].items():
                    self.write(relative, content)

                self.assertFalse(dependency_lockfile.run(self.root).passed)
                self.assertFalse(runtime_version.run(self.root).passed)
                self.assertFalse(test_command.run(self.root).passed)
                self.assertEqual(detect_project_type(self.root), fixture["name"])

                for relative in fixture["files"]:
                    (self.root / relative).unlink()


if __name__ == "__main__":
    unittest.main()
