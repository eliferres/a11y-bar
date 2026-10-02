"""contrast (WCAG 1.4.3): the rule pair, the inherited walk, and what
each one leaves unread on purpose."""
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


def contrast(case: str) -> list:
    return check("contrast", case, "contrast")


class TestRulePair(unittest.TestCase):
    def test_grey_body_text_on_white_fails(self) -> None:
        self.assertEqual(contrast("pair-fail"),
                         [("pair-fail.css", 1, ".q: 2.85:1 on 16px text, needs 4.5:1")])

    def test_dark_grey_on_white_passes(self) -> None:
        self.assertEqual(contrast("pair-pass"), [])

    def test_large_text_answers_to_three_to_one(self) -> None:
        self.assertEqual(contrast("large-text"), [])
        self.assertEqual(contrast("small-text"),
                         [("small-text.css", 1, ".q: 3.03:1 on 16px text, needs 4.5:1")])

    def test_bold_text_from_19px_counts_as_large(self) -> None:
        self.assertEqual(contrast("bold-text"), [])

    def test_a_root_custom_property_is_resolved(self) -> None:
        self.assertEqual(contrast("root-variable"),
                         [("root-variable.css", 2, ".q: 1.92:1 on 16px text, needs 4.5:1")])


class TestInheritedPair(unittest.TestCase):
    def test_background_on_a_wrapper_and_colour_on_the_text_fails(self) -> None:
        self.assertEqual(contrast("inherited-fail"),
                         [("inherited-fail.html", 2, "p.t (inherited): 2.85:1 on 16px text, needs 4.5:1")])

    def test_the_same_markup_in_dark_grey_passes(self) -> None:
        self.assertEqual(contrast("inherited-pass"), [])

    def test_a_background_on_body_reaches_a_grandchild(self) -> None:
        self.assertEqual(contrast("inherited-deep"),
                         [("inherited-deep.html", 2, "span.lede (inherited): 2.32:1 on 16px text, needs 4.5:1")])

    def test_text_a_browser_never_paints_is_never_judged(self) -> None:
        self.assertEqual(contrast("script-text"), [])

    def test_fluid_type_resolves_at_the_desktop_viewport(self) -> None:
        self.assertEqual(contrast("fluid-type"),
                         [("fluid-type.html", 2, "p.t (inherited): 2.32:1 on 40px text, needs 3.0:1")])

    def test_a_font_size_in_em_or_percent_skips_the_element(self) -> None:
        self.assertEqual(contrast("em-font-size"), [])
        self.assertEqual(contrast("percent-font-size"), [])

    def test_a_translucent_or_gradient_background_is_not_guessed(self) -> None:
        self.assertEqual(contrast("translucent-background"), [])
        self.assertEqual(contrast("gradient-background"), [])


class TestDesktopViewport(unittest.TestCase):
    """Contrast is judged at a 1280px desktop: a rule inside a @media that
    does not hold there never decides the verdict."""

    def test_a_phone_only_font_size_does_not_decide_the_desktop_verdict(self) -> None:
        self.assertEqual(contrast("media-narrow"), [])

    def test_a_desktop_media_rule_does(self) -> None:
        self.assertEqual(contrast("media-desktop"),
                         [("media-desktop.html", 2, "p.t (inherited): 3.03:1 on 16px text, needs 4.5:1")])

    def test_an_em_breakpoint_that_holds_at_1280_is_read(self) -> None:
        self.assertEqual(contrast("media-em-desktop"),
                         [("media-em-desktop.html", 2, "p.t (inherited): 2.85:1 on 20px text, needs 4.5:1")])

    def test_a_colour_scheme_query_is_not_the_default_rendering(self) -> None:
        self.assertEqual(contrast("color-scheme"), [])

    def test_the_rule_pair_skips_a_phone_only_rule_too(self) -> None:
        self.assertEqual(contrast("media-pair"), [])


class TestAtRuleParsing(unittest.TestCase):
    def test_a_charset_line_does_not_hide_the_rule_after_it(self) -> None:
        self.assertEqual(contrast("charset"), [("charset.css", 2, ".q: 2.85:1 on 16px text, needs 4.5:1")])

    def test_a_statement_layer_does_not_leak_onto_the_next_media_block(self) -> None:
        self.assertEqual(contrast("layer-statement"), [])

    def test_a_semicolon_inside_supports_parentheses_keeps_the_rule(self) -> None:
        self.assertEqual(contrast("supports-semicolon"),
                         [("supports-semicolon.css", 1, ".q: 2.85:1 on 16px text, needs 4.5:1")])

    def test_a_semicolon_inside_a_selector_keeps_the_rule(self) -> None:
        self.assertEqual(contrast("selector-semicolon"),
                         [("selector-semicolon.css", 1, 'a[href*=";"]: 2.85:1 on 16px text, needs 4.5:1')])


if __name__ == "__main__":
    unittest.main()
