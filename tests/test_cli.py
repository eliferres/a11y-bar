"""The command line: what it reads, how it reports, and its exit codes."""
from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures"


def run(*args: str, cwd: Path = FIXTURES) -> subprocess.CompletedProcess:
    """Run the tool from the fixtures folder so reported paths are short."""
    return subprocess.run([sys.executable, "-m", "a11y_bar", *args], cwd=str(cwd),
                          capture_output=True, text=True,
                          env={"PYTHONPATH": str(FIXTURES.parent.parent), "PATH": ""})


class TestExitCodes(unittest.TestCase):
    def test_a_clean_page_passes_with_exit_0(self) -> None:
        r = run("clean")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(r.stdout, "PASS: 0 finding(s), 2 file(s) read, 6 rules\n")

    def test_findings_exit_1_with_one_line_each_then_the_verdict(self) -> None:
        r = run("project")
        self.assertEqual(r.returncode, 1)
        self.assertEqual(r.stdout.splitlines(), [
            "project/src/page.tsx:1: [focus-order] <button tabindex=\"5\"> moves it ahead of the document order",
            "project/src/page.tsx:2: [alt] <img> has no alt text",
            "FAIL: 2 finding(s), 3 file(s) read, 6 rules",
        ])

    def test_a_missing_path_is_one_line_on_stderr_and_exit_2(self) -> None:
        r = run("no-such-dir")
        self.assertEqual((r.returncode, r.stdout), (2, ""))
        self.assertEqual(r.stderr, "a11y-bar: no such file or directory: no-such-dir\n")

    def test_an_unsupported_file_is_a_usage_error(self) -> None:
        r = run("unsupported/notes.txt")
        self.assertEqual(r.returncode, 2)
        self.assertIn("not a .css, .html, .htm, .jsx or .tsx file", r.stderr)

    def test_a_bad_min_target_is_one_line_and_exit_2(self) -> None:
        for value in ("abc", "0", "-4"):
            with self.subTest(value=value):
                r = run("clean", "--min-target", value)
                self.assertEqual(r.returncode, 2)
                self.assertEqual(len(r.stderr.splitlines()), 1, r.stderr)

    def test_version_prints_the_command_name_and_version(self) -> None:
        r = run("--version")
        self.assertEqual((r.returncode, r.stdout), (0, "a11y-bar 0.1.0\n"))


class TestNothingToRead(unittest.TestCase):
    """A scan that read nothing must not report a pass."""

    def test_css_without_markup_is_a_usage_error(self) -> None:
        r = run("css-only")
        self.assertEqual(r.returncode, 2)
        self.assertEqual(r.stderr, "a11y-bar: no .html, .htm, .jsx or .tsx files found in css-only\n")

    def test_markup_without_css_is_a_usage_error(self) -> None:
        r = run("markup-only")
        self.assertEqual(r.returncode, 2)
        self.assertEqual(r.stderr,
                         "a11y-bar: no CSS found in markup-only (no .css file and no <style> block)\n")

    def test_a_style_block_with_no_rule_in_it_is_not_css(self) -> None:
        for name in ("comment-style", "import-style"):
            with self.subTest(name=name):
                r = run(name)
                self.assertEqual(r.returncode, 2)
                self.assertIn("no CSS found", r.stderr)

    def test_a_style_tag_quoted_inside_a_tsx_string_is_not_css(self) -> None:
        r = run("tsx-style-string")
        self.assertEqual(r.returncode, 2)
        self.assertIn("no CSS found", r.stderr)


class TestWhatIsRead(unittest.TestCase):
    def test_a_project_root_reads_out_and_the_source_folders(self) -> None:
        files = json.loads(run("project", "--json").stdout)["files"]
        self.assertEqual(files, ["project/out/style.css", "project/out/index.html", "project/src/page.tsx"])

    def test_node_modules_is_never_read(self) -> None:
        self.assertNotIn("vendored", run("project").stdout)

    def test_a_clean_project_root_counts_every_file_from_both_trees(self) -> None:
        r = run("project-clean")
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertEqual(r.stdout, "PASS: 0 finding(s), 3 file(s) read, 6 rules\n")

    def test_with_no_path_the_current_directory_is_checked(self) -> None:
        r = run(cwd=FIXTURES / "clean")
        self.assertEqual((r.returncode, r.stdout), (0, "PASS: 0 finding(s), 2 file(s) read, 6 rules\n"))

    def test_explicit_files_are_read_once_each(self) -> None:
        r = run("clean/index.html", "clean/style.css", "clean")
        self.assertEqual(r.stdout, "PASS: 0 finding(s), 2 file(s) read, 6 rules\n")

    def test_a_page_whose_only_css_is_a_style_block_is_judged(self) -> None:
        self.assertEqual(run("inline-only").returncode, 0)
        r = run("inline-nofocus")
        self.assertEqual(r.returncode, 1)
        self.assertEqual(r.stdout.splitlines()[0],
                         "inline-nofocus/index.html:1: [focus-visible] button sets outline: none "
                         "and no :focus-visible rule replaces it")
        self.assertEqual(len(r.stdout.splitlines()), 2)


class TestJson(unittest.TestCase):
    def test_each_finding_carries_its_rule_criterion_file_and_line(self) -> None:
        r = run("project", "--json")
        self.assertEqual(r.returncode, 1)
        report = json.loads(r.stdout)
        self.assertEqual(report["version"], "0.1.0")
        self.assertEqual(report["findings"][1], {
            "rule": "alt", "wcag": "1.1.1", "file": "project/src/page.tsx", "line": 2,
            "message": "<img> has no alt text",
        })

    def test_a_clean_run_is_valid_json_with_no_findings(self) -> None:
        r = run("clean", "--json")
        self.assertEqual((r.returncode, json.loads(r.stdout)["findings"]), (0, []))


if __name__ == "__main__":
    unittest.main()
