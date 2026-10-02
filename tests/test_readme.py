"""The README's rule table and the code name the same rules and criteria."""
from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

import a11y_bar as a

ROOT = Path(__file__).resolve().parent.parent
README = ROOT / "README.md"


class TestReadme(unittest.TestCase):
    def test_the_rule_table_matches_the_rules_in_the_code(self) -> None:
        rows = re.findall(r"^\| `([a-z-]+)` \| .* \| (\d\.\d\.\d+) ", README.read_text(encoding="utf-8"), re.M)
        self.assertEqual(dict(rows), {rule: wcag for rule, (wcag, _) in a.RULES.items()})
        self.assertEqual([r for r, _ in rows], list(a.RULES))

    def test_the_documented_default_target_is_the_real_one(self) -> None:
        self.assertIn("The default is %d, the WCAG 2.5.8 minimum" % a.DEFAULT_MIN_TARGET,
                      README.read_text(encoding="utf-8"))

    def test_the_first_run_output_is_the_recorded_demo_output(self) -> None:
        shown = re.search(r"python3 -m a11y_bar demo/broken\n```\n\n```text\n(.*?)\n```",
                          README.read_text(encoding="utf-8"), re.S).group(1)
        recorded = json.loads((ROOT / "demo" / "transcript.json").read_text(encoding="utf-8"))[0]["out"]
        self.assertEqual(shown, recorded.rsplit("\nexit ", 1)[0])


if __name__ == "__main__":
    unittest.main()
