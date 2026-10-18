import json
import tempfile
import unittest
from pathlib import Path

from rebuildcheck.checks import absolute_paths, committed_secrets
from rebuildcheck.cli import inspect_repository, render_human, render_json


class EvidenceAndFalsePositiveTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="rebuildcheck-evidence-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / "repo"
        self.root.mkdir()

    def write(self, relative: str, content: str | bytes) -> Path:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            path.write_bytes(content)
        else:
            path.write_text(content, encoding="utf-8")
        return path

    @staticmethod
    def aws_value() -> str:
        return "AKIA" + "A" * 16

    @staticmethod
    def github_value() -> str:
        return "ghp_" + "a" * 24

    @staticmethod
    def slack_value() -> str:
        return "xoxb-" + "b" * 20

    @staticmethod
    def stripe_value() -> str:
        return "sk_live_" + "s" * 16

    @staticmethod
    def home_path() -> str:
        return "/" + "home/" + "judge/" + "repo"

    def test_findings_keep_relative_line_and_redacted_secret_preview(self) -> None:
        secret = self.aws_value()
        key = "ACCESS" + "_KEY"
        self.write("src/config.txt", "header\n" + key + "=" + secret + "\n")

        result = committed_secrets.run(self.root)

        self.assertFalse(result.passed)
        finding = next(
            item
            for item in result.details["findings"]
            if item["kind"] == "aws-access-key"
        )
        self.assertEqual(finding["path"], "src/config.txt")
        self.assertEqual(finding["line"], 2)
        self.assertEqual(finding["preview"], "AKIA****")
        self.assertNotIn(secret, json.dumps(result.to_dict()))
        self.assertLessEqual(len(finding["preview"].split("****", 1)[0]), 4)

    def test_absolute_path_finding_keeps_relative_line(self) -> None:
        self.write("src/settings.txt", "first\nPATH=" + self.home_path() + "\n")

        result = absolute_paths.run(self.root)

        self.assertFalse(result.passed)
        finding = result.details["findings"][0]
        self.assertEqual(finding["path"], "src/settings.txt")
        self.assertEqual(finding["line"], 2)
        self.assertEqual(finding["lines"], [2])
        self.assertEqual(finding["more"], 0)
        self.assertEqual(finding["kinds"], ["unix-user-path"])

    def test_lockfiles_build_outputs_and_binary_extensions_are_skipped(self) -> None:
        secret = self.aws_value()
        machine_path = self.home_path()
        token_key = "TO" + "KEN"
        content = token_key + "=" + secret + "\nPATH=" + machine_path + "\n"

        for name in (
            "package-lock.json",
            "npm-shrinkwrap.json",
            "requirements.lock",
            "uv.lock",
            "pubspec.lock",
            "pnpm-lock.yaml",
            "yarn.lock",
            "poetry.lock",
            "Cargo.lock",
            "go.sum",
        ):
            self.write(name, content)
        for directory in ("build", "dist", "out", "target"):
            self.write(directory + "/generated.txt", content)
        for extension in (".jar", ".png", ".jpg", ".ico"):
            self.write("artifacts/blob" + extension, content)

        self.assertTrue(committed_secrets.run(self.root).passed)
        self.assertTrue(absolute_paths.run(self.root).passed)

    def test_http_urls_are_not_windows_or_unix_absolute_paths(self) -> None:
        url = "https://example.test/" + "C:" + "/" + "Users/demo/repo"
        self.write("links.txt", "Docs: " + url + "\n")

        result = absolute_paths.run(self.root)

        self.assertTrue(result.passed)

    def test_dummy_assignment_bearer_and_url_values_are_safe(self) -> None:
        dummy_values = (
            "example",
            "dummy-value",
            "fake_token",
            "changeme-value",
            "test-value",
            "xxxx-value",
            "AKIA" + "x" * 16,
            "ghp_" + "x" * 24,
        )
        api_key = "API" + "_KEY"
        lines = [api_key + "=" + value for value in dummy_values]
        lines.append("Authorization: Bearer " + "xoxb-" + "x" * 20)
        lines.append(
            "SERVICE_URL=https://user:" + "fake-" + "x" * 16 + "@example.test"
        )
        self.write("placeholders.env", "\n".join(lines) + "\n")

        result = committed_secrets.run(self.root)

        self.assertTrue(result.passed)

    def test_real_provider_tokens_and_private_key_headers_still_flag(self) -> None:
        private_key_header = "-----BEGIN " + "RSA PRIVATE KEY-----"
        self.write(
            "credentials.txt",
            "AWS=" + self.aws_value() + "\n"
            + "GITHUB=" + self.github_value() + "\n"
            + "SLACK=" + self.slack_value() + "\n"
            + "STRIPE=" + self.stripe_value() + "\n"
            + private_key_header
            + "\n",
        )

        result = committed_secrets.run(self.root)
        kinds = {
            kind
            for item in result.details["findings"]
            for kind in item["kinds"]
        }

        self.assertFalse(result.passed)
        self.assertTrue(
            {
                "aws-access-key",
                "github-token",
                "slack-token",
                "stripe-key",
                "private-key",
            }
            <= kinds
        )

    def test_evidence_groups_files_and_truncates_line_numbers(self) -> None:
        machine_path = self.home_path()
        self.write(
            "paths.txt",
            "\n".join("PATH=" + machine_path for _ in range(5)) + "\n",
        )
        self.write(
            "tokens.txt",
            "\n".join("AWS=" + self.aws_value() for _ in range(5)) + "\n",
        )

        path_result = absolute_paths.run(self.root)
        path_findings = path_result.details["findings"]
        self.assertEqual(len(path_findings), 1)
        self.assertEqual(path_findings[0]["path"], "paths.txt")
        self.assertEqual(path_findings[0]["lines"], [1, 2, 3])
        self.assertEqual(path_findings[0]["more"], 2)

        secret_result = committed_secrets.run(self.root)
        secret_findings = secret_result.details["findings"]
        self.assertEqual(len(secret_findings), 1)
        self.assertEqual(secret_findings[0]["path"], "tokens.txt")
        self.assertEqual(secret_findings[0]["lines"], [1, 2, 3])
        self.assertEqual(secret_findings[0]["more"], 2)
        self.assertEqual(secret_findings[0]["kinds"], ["aws-access-key"])

    def test_sensitive_assignments_only_flag_substantial_literal_values(self) -> None:
        self.write(
            "assignments.py",
            "\n".join(
                (
                    'TOKEN = config.token',
                    'TOKEN = "req.headers.authorization"',
                    'TOKEN = "jwt.decode(value)"',
                    'TOKEN = "process.env.TOKEN"',
                    'TOKEN = "os.environ[TOKEN]"',
                    'TOKEN = "bytes(value)"',
                    'TOKEN = "str(value)"',
                    'TOKEN = "long.value"',
                    'TOKEN = "long[value]"',
                    'TOKEN = "long(value)"',
                    'TOKEN = "abcdefgh".strip()',
                    'TOKEN = "1234567"',
                    'TOKEN = "write"',
                    'TOKEN = "read"',
                    'TOKEN = "true"',
                    'TOKEN = "false"',
                    'TOKEN = "null"',
                    'TOKEN = "short"',
                    'TOKEN = "a real secret value"',
                )
            )
            + "\n",
        )

        result = committed_secrets.run(self.root)

        self.assertFalse(result.passed)
        findings = result.details["findings"]
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["kind"], "sensitive-assignment")
        self.assertEqual(findings[0]["lines"], [19])

    def test_named_directories_and_key_extensions_are_skipped(self) -> None:
        private_key = "-----BEGIN RSA PRIVATE KEY-----\n"
        machine_path = "PATH=" + self.home_path() + "\n"
        for directory in (
            "tests",
            "TEST",
            "test",
            "__TESTS__",
            "Docs",
            "examples",
            "FIXTURES",
        ):
            for extension in (".pem", ".key", ".crt"):
                self.write(directory + "/ignored" + extension, private_key + machine_path)

        self.assertTrue(committed_secrets.run(self.root).passed)
        self.assertTrue(absolute_paths.run(self.root).passed)

    def test_provider_tokens_and_private_keys_are_kept_outside_skips(self) -> None:
        self.write(
            "credentials.pem",
            "AWS=" + self.aws_value() + "\n"
            + "GITHUB=" + self.github_value() + "\n"
            + "SLACK=" + self.slack_value() + "\n"
            + "STRIPE=" + self.stripe_value() + "\n"
            + "-----BEGIN PRIVATE KEY-----\n",
        )

        result = committed_secrets.run(self.root)
        finding = result.details["findings"][0]

        self.assertFalse(result.passed)
        self.assertEqual(finding["path"], "credentials.pem")
        self.assertEqual(
            set(finding["kinds"]),
            {"aws-access-key", "github-token", "slack-token", "stripe-key", "private-key"},
        )

    def test_human_and_json_reports_include_bounded_evidence(self) -> None:
        secret = self.aws_value()
        self.write("credentials.txt", "safe\nAWS_ACCESS_KEY=" + secret + "\n")
        self.write("settings.txt", "safe\nPATH=" + self.home_path() + "\n")
        self.write("src/Config.txt", "content\n")
        self.write("README.md", "See `src/config.txt` for details.\n")
        self.write("script.sh", b"#!/bin/sh\r\necho ok\r\n")

        report = inspect_repository(self.root, no_run=True)
        human = render_human(report)
        document = json.loads(render_json(report))

        self.assertIn("credentials.txt:2", human)
        self.assertIn("settings.txt:2", human)
        self.assertIn("README.md:1", human)
        self.assertIn("script.sh:1", human)
        self.assertIn("AKIA****", human)
        self.assertNotIn(secret, human)

        checks = {item["name"]: item for item in document["checks"]}
        for name in ("committed_secrets", "absolute_paths", "portability"):
            findings = checks[name]["details"]["findings"]
            self.assertTrue(findings)
            self.assertTrue(all("path" in item and "line" in item for item in findings))
        secret_json = json.dumps(checks["committed_secrets"])
        self.assertNotIn(secret, secret_json)


if __name__ == "__main__":
    unittest.main()
