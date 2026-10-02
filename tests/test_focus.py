"""focus-order (WCAG 2.4.3) and focus-visible (WCAG 2.4.7)."""

from __future__ import annotations

import os
import unittest
from pathlib import Path

import a11y_bar as a

FIXTURES = Path(__file__).parent / "fixtures" / "rules"


def check(group: str, case: str, rule: str, min_target: float = a.DEFAULT_MIN_TARGET) -> list:
    """(file name, line, message) for each finding of one rule on one case,
    where a case is every fixture file sharing the name."""
    paths = sorted(str(p) for p in (FIXTURES / group).glob(case + ".*"))
    css, markup = a.collect(paths)
    return [(os.path.basename(f.path), f.line, f.message)
            for f in a.scan(css, markup, min_target) if f.rule == rule]


class TestFocusOrder(unittest.TestCase):
    def test_a_positive_tabindex_fails(self) -> None:
        self.assertEqual(check("focus", "tabindex-positive", "focus-order"),
                         [("tabindex-positive.html", 2,
                           '<button tabindex="3"> moves it ahead of the document order')])

    def test_zero_and_minus_one_pass(self) -> None:
        self.assertEqual(check("focus", "tabindex-zero-and-negative", "focus-order"), [])

    def test_jsx_literal_fails_and_a_runtime_expression_is_skipped(self) -> None:
        self.assertEqual(check("focus", "tabindex", "focus-order"),
                         [("tabindex.tsx", 1, '<div tabindex="2"> moves it ahead of the document order')])


class TestFocusVisible(unittest.TestCase):
    def test_no_ring_and_a_removed_outline_both_fail(self) -> None:
        self.assertEqual(check("focus", "no-ring", "focus-visible"), [
            ("no-ring.css", 2, ".x sets outline: none and no :focus-visible rule replaces it"),
            ("no-ring.html", 1, "1 interactive element(s) and no :focus-visible rule in the CSS"),
        ])

    def test_a_removed_outline_passes_when_a_focus_visible_rule_exists(self) -> None:
        self.assertEqual(check("focus", "ring", "focus-visible"), [])

    def test_a_ring_in_a_style_block_counts_beside_a_stylesheet(self) -> None:
        self.assertEqual(check("focus", "ring-in-style-block", "focus-visible"), [])

    def test_an_outline_removed_only_on_a_narrow_phone_is_not_a_desktop_finding(self) -> None:
        self.assertEqual(check("focus", "outline-narrow", "focus-visible"), [])

    def test_an_outline_removed_at_desktop_widths_is(self) -> None:
        self.assertEqual(check("focus", "outline-desktop", "focus-visible"),
                         [("outline-desktop.css", 2, ".x sets outline: none and no :focus-visible rule replaces it")])


if __name__ == "__main__":
    unittest.main()
