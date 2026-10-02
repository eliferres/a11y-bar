"""reduced-motion (WCAG 2.3.3) and alt (WCAG 1.1.1)."""

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


class TestReducedMotion(unittest.TestCase):
    def test_a_transition_with_no_reduce_block_fails(self) -> None:
        self.assertEqual(check("motion", "animates", "reduced-motion"), [
            ("animates.css", 1, "CSS animates and no @media (prefers-reduced-motion: reduce) block exists")])

    def test_the_same_transition_passes_with_a_reduce_block(self) -> None:
        self.assertEqual(check("motion", "animates-with-fallback", "reduced-motion"), [])

    def test_a_zero_duration_is_not_motion(self) -> None:
        self.assertEqual(check("motion", "zero-duration", "reduced-motion"), [])

    def test_a_duration_set_through_a_custom_property_is_motion(self) -> None:
        self.assertEqual([f[1] for f in check("motion", "var-duration", "reduced-motion")], [2])

    def test_a_delay_alone_is_not_motion(self) -> None:
        self.assertEqual(check("motion", "delay-only", "reduced-motion"), [])

    def test_any_transition_in_a_list_with_a_duration_is_motion(self) -> None:
        self.assertEqual([f[1] for f in check("motion", "mixed-durations", "reduced-motion")], [1])

    def test_the_words_in_a_content_string_are_not_a_fallback(self) -> None:
        self.assertEqual([f[1] for f in check("motion", "content-string", "reduced-motion")], [1])

    def test_the_line_points_at_the_first_animated_declaration(self) -> None:
        self.assertEqual([f[1] for f in check("motion", "keyframes", "reduced-motion")], [3])


class TestAlt(unittest.TestCase):
    def test_a_bare_img_and_a_bare_video_fail(self) -> None:
        self.assertEqual(check("alt", "missing", "alt"), [
            ("missing.html", 2, "<img> has no alt text"),
            ("missing.html", 3, "<video> has no aria-label and is not aria-hidden"),
        ])

    def test_every_accepted_way_to_name_or_hide_media_passes(self) -> None:
        self.assertEqual(check("alt", "named", "alt"), [])

    def test_alt_on_a_video_names_nothing(self) -> None:
        self.assertEqual(check("alt", "video-alt", "alt"),
                         [("video-alt.html", 2, "<video> has no aria-label and is not aria-hidden")])

    def test_a_comparison_before_jsx_does_not_swallow_the_image(self) -> None:
        self.assertEqual(check("alt", "comparison", "alt"), [("comparison.jsx", 3, "<img> has no alt text")])

    def test_markup_inside_js_comments_and_strings_is_not_read(self) -> None:
        self.assertEqual(check("alt", "comments-and-strings", "alt"), [])

    def test_an_apostrophe_in_jsx_text_is_not_a_string(self) -> None:
        self.assertEqual(check("alt", "apostrophe", "alt"), [("apostrophe.jsx", 2, "<img> has no alt text")])

    def test_an_image_with_spread_props_is_skipped(self) -> None:
        self.assertEqual(check("alt", "spread", "alt"), [])

    def test_an_empty_name_is_no_name_and_an_image_button_needs_alt(self) -> None:
        self.assertEqual(check("alt", "empty-names", "alt"), [
            ("empty-names.html", 2, "<img> has no alt text"),
            ("empty-names.html", 3, '<input type="image"> has no alt text'),
            ("empty-names.html", 5, "<img> has no alt text"),
        ])

    def test_jsx_inside_a_map_is_read_and_an_expression_alt_counts(self) -> None:
        self.assertEqual(check("alt", "components", "alt"), [("components.jsx", 3, "<img> has no alt text")])


if __name__ == "__main__":
    unittest.main()
