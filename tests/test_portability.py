import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

from rebuildcheck.checks import portability


class PortabilityFixtureTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="rebuildcheck-portability-")
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
        self.git("config", "user.name", "rebuildcheck portability tests")
        self.git("config", "user.email", "portability@example.invalid")
        self.git("config", "core.autocrlf", "false")
        self.git("add", ".")
        self.git("commit", "-qm", "fixture: portability")

    def test_crlf_is_found_in_shell_makefile_and_dockerfile(self) -> None:
        self.write("script.sh", b"#!/bin/sh\r\necho ok\r\n")
        self.write("Makefile", b"test:\r\n\techo ok\r\n")
        self.write("Dockerfile", b"FROM python:3.12\r\n")
        self.commit()

        eol = subprocess.run(
            ["git", "ls-files", "--eol", "--", "script.sh"],
            cwd=self.root,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        ).stdout
        self.assertIn("i/crlf", eol)

        result = portability.run(self.root)

        self.assertFalse(result.passed)
        findings = result.details["findings"]
        crlf_paths = {item["path"] for item in findings if item["kind"] == "crlf-line-ending"}
        self.assertEqual(crlf_paths, {"script.sh", "Makefile", "Dockerfile"})
        self.assertTrue(all(item["fix"] for item in findings))
        self.assertTrue(all("LF" in item["fix"] for item in findings if item["kind"] == "crlf-line-ending"))

    def test_index_crlf_is_grouped_into_one_finding_with_line_count(self) -> None:
        self.write("script.sh", b"echo ok\r\n" * 100)
        self.commit()

        result = portability.run(self.root)

        self.assertFalse(result.passed)
        findings = result.details["findings"]
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["kind"], "crlf-line-ending")
        self.assertEqual(findings[0]["path"], "script.sh")
        self.assertEqual(findings[0]["line_count"], 100)
        self.assertTrue(findings[0]["fix"])

    def test_working_crlf_with_lf_index_is_not_a_crlf_finding(self) -> None:
        self.write("Makefile", b"test:\n\techo ok\n")
        self.commit()
        (self.root / "Makefile").write_bytes(b"test:\r\n\techo ok\r\n")

        eol = subprocess.run(
            ["git", "ls-files", "--eol", "--", "Makefile"],
            cwd=self.root,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        ).stdout
        self.assertIn("i/lf", eol)
        self.assertIn("w/crlf", eol)

        result = portability.run(self.root)

        findings = result.details.get("findings", [])
        self.assertFalse(
            any(item.get("kind") == "crlf-line-ending" for item in findings)
        )

    def test_non_git_folder_falls_back_to_working_file_bytes(self) -> None:
        self.write("Makefile", b"test:\r\n\techo ok\r\n")

        result = portability.run(self.root)

        self.assertFalse(result.passed)
        findings = [
            item
            for item in result.details["findings"]
            if item["kind"] == "crlf-line-ending"
        ]
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["line_count"], 2)

    def test_lf_files_do_not_trigger_line_ending_check(self) -> None:
        script = self.write("script.sh", "#!/bin/sh\necho ok\n")
        script.chmod(script.stat().st_mode | stat.S_IXUSR)
        self.write("Makefile", "test:\n\techo ok\n")
        self.write("Dockerfile", "FROM python:3.12\n")
        self.commit()

        result = portability.run(self.root)

        self.assertTrue(result.passed)

    def test_non_executable_tracked_shebang_is_found(self) -> None:
        script = self.write("tools/run", "#!/bin/sh\necho ok\n")
        self.commit()

        result = portability.run(self.root)

        self.assertFalse(result.passed)
        finding = next(item for item in result.details["findings"] if item["kind"] == "shebang-not-executable")
        self.assertEqual(finding["path"], "tools/run")
        self.assertIn("git update-index --chmod=+x", finding["fix"])

    def test_executable_tracked_shebang_passes(self) -> None:
        script = self.write("tools/run", "#!/bin/sh\necho ok\n")
        script.chmod(script.stat().st_mode | stat.S_IXUSR)
        self.commit()

        result = portability.run(self.root)

        self.assertTrue(result.passed)

    def test_case_mismatched_readme_path_is_found(self) -> None:
        self.write("src/Config.txt", "value\n")
        self.write("README.md", "Read `src/config.txt` before running.\n")
        self.commit()

        result = portability.run(self.root)

        self.assertFalse(result.passed)
        finding = next(item for item in result.details["findings"] if item["kind"] == "case-mismatched-path")
        self.assertEqual(finding["mentioned"], "src/config.txt")
        self.assertEqual(finding["actual"], "src/Config.txt")
        self.assertIn("src/Config.txt", finding["fix"])

    def test_exact_readme_path_passes(self) -> None:
        self.write("src/Config.txt", "value\n")
        self.write("README.md", "Read `./src/Config.txt` before running.\n")
        self.commit()

        result = portability.run(self.root)

        self.assertTrue(result.passed)

    def test_nul_containing_files_are_ignored_even_with_portability_names(self) -> None:
        self.write("binary.sh", b"#!/bin/sh\r\n\x00not text\r\n")
        self.write("Dockerfile.backup", b"FROM nowhere\r\n\x00binary\r\n")
        self.write("README.md", b"\x00src/config.txt\r\n")
        self.write("src/Config.txt", "value\n")
        self.commit()

        result = portability.run(self.root)

        self.assertTrue(result.passed)
        self.assertEqual(result.details["readme"], "binary-ignored")


if __name__ == "__main__":
    unittest.main()
