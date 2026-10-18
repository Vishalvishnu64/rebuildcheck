import json
import tempfile
import unittest
from pathlib import Path

from rebuildcheck.checks import dependency_lockfile, runtime_version, test_command
from rebuildcheck.cli import inspect_repository, render_human, render_json
from rebuildcheck.project_type import (
    detect_project_type,
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

        self.assertTrue(dependency_lockfile.run(self.root).passed)
        self.assertTrue(runtime_version.run(self.root).passed)
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


if __name__ == "__main__":
    unittest.main()
