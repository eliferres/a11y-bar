"""The pure building blocks: colour math, length resolution, the CSS walk,
media-query reading and the JSX preparation for the HTML parser."""
from __future__ import annotations

import unittest

import a11y_bar as a


class TestColour(unittest.TestCase):
    def test_black_on_white_is_twenty_one_to_one(self) -> None:
        self.assertAlmostEqual(a.contrast_ratio((0, 0, 0), (255, 255, 255)), 21.0, places=2)

    def test_grey_999_on_white_is_below_the_body_text_minimum(self) -> None:
        self.assertAlmostEqual(a.contrast_ratio((0x99, 0x99, 0x99), (255, 255, 255)), 2.85, places=2)

    def test_hex_shorthand_rgb_and_alpha_forms_parse(self) -> None:
        self.assertEqual(a.parse_color("#abc", {}), (0xaa, 0xbb, 0xcc, 1.0))
        self.assertEqual(a.parse_color("rgb(10, 20, 30)", {}), (10, 20, 30, 1.0))
        self.assertEqual(a.parse_color("rgb(10 20 30 / 50%)", {}), (10, 20, 30, 0.5))
        self.assertEqual(a.parse_color("#00000080", {})[3], 128 / 255)

    def test_custom_properties_and_fallbacks_resolve(self) -> None:
        self.assertEqual(a.parse_color("var(--ink)", {"--ink": "#111111"}), (17, 17, 17, 1.0))
        self.assertEqual(a.parse_color("var(--missing, #fff)", {}), (255, 255, 255, 1.0))

    def test_an_unreadable_colour_is_unknown_rather_than_guessed(self) -> None:
        self.assertIsNone(a.parse_color("color-mix(in srgb, red, blue)", {}))
        self.assertIsNone(a.parse_color("currentColor", {}))

    def test_a_translucent_colour_is_composited_onto_its_background(self) -> None:
        self.assertEqual(a.over((0, 0, 0, 0.5), (255, 255, 255)), (128, 128, 128))


class TestLengths(unittest.TestCase):
    def test_px_rem_and_vw_at_the_desktop_viewport(self) -> None:
        self.assertEqual(a.px("44px"), 44.0)
        self.assertEqual(a.px("2rem"), 32.0)
        self.assertEqual(a.px("3.125vw"), 40.0)

    def test_em_resolves_only_where_it_is_root_relative(self) -> None:
        self.assertIsNone(a.px("2em"))
        self.assertEqual(a.px("30em", allow_em=True), 480.0)

    def test_percent_and_unitless_values_are_not_lengths(self) -> None:
        self.assertIsNone(a.px("150%"))
        self.assertIsNone(a.px("1.5"))

    def test_clamp_min_max_and_two_term_calc_resolve(self) -> None:
        self.assertEqual(a.px("clamp(32px, 3.125vw, 40px)"), 40.0)
        self.assertEqual(a.px("min(10px, 2rem)"), 10.0)
        self.assertEqual(a.px("calc(var(--spacing) * 3)", {"--spacing": "0.25rem"}), 12.0)
        self.assertEqual(a.px("calc(1rem + 4px)"), 20.0)

    def test_a_hyphen_inside_a_variable_name_is_not_subtraction(self) -> None:
        self.assertEqual(a.px("calc(var(--tap-size) * 1)", {"--tap-size": "44px"}), 44.0)


class TestCssWalk(unittest.TestCase):
    def test_a_charset_line_does_not_swallow_the_rule_after_it(self) -> None:
        rules = a.css_rules('@charset "utf-8";\n.q{color:#999}')
        self.assertEqual([(r.selector, r.at, r.line) for r in rules], [(".q", "", 2)])

    def test_root_variables_survive_an_import_line(self) -> None:
        rules = a.css_rules('@import "tailwindcss";\n:root{--a:#999999;--b:#ffffff}')
        self.assertEqual(a.root_variables(rules), {"--a": "#999999", "--b": "#ffffff"})

    def test_a_semicolon_inside_supports_parentheses_is_kept(self) -> None:
        rules = a.css_rules("@supports (background: url(a;b)){ .q{color:#999} }")
        self.assertEqual([(r.selector, r.at) for r in rules], [(".q", "@supports (background: url(a;b))")])

    def test_a_semicolon_inside_a_selector_is_kept(self) -> None:
        rules = a.css_rules('a[href*=";"]{color:#999}')
        self.assertEqual([r.selector for r in rules], ['a[href*=";"]'])

    def test_comments_are_removed_without_moving_line_numbers(self) -> None:
        rules = a.css_rules(a.strip_comments("/* one\ntwo */\n.q{color:#999}"))
        self.assertEqual(rules[0].line, 3)


class TestDesktopMedia(unittest.TestCase):
    def test_width_conditions_are_judged_at_1280(self) -> None:
        self.assertTrue(a.media_applies_at_desktop("@media (min-width: 900px)"))
        self.assertFalse(a.media_applies_at_desktop("@media (max-width: 359px)"))
        self.assertTrue(a.media_applies_at_desktop("@media (min-width: 64em)"))
        self.assertTrue(a.media_applies_at_desktop("@media (width >= 1024px)"))

    def test_a_non_width_feature_is_not_the_default_rendering(self) -> None:
        self.assertFalse(a.media_applies_at_desktop("@media (prefers-color-scheme: dark)"))


class TestPhoneMedia(unittest.TestCase):
    CASES = {
        "(max-width: 599px)": True,
        "(max-width: 374px)": False,
        "(min-width: 376px)": False,
        "(max-width: 30em)": True,
        "(width <= 599px)": True,
        "(320px <= width <= 599px)": True,
        "(width >= 376px)": False,
        "(pointer: coarse)": True,
        "(any-pointer: coarse)": True,
        "(pointer: coarse) and (min-width: 600px) and (max-width: 900px)": True,
        "(pointer: coarse) and (orientation: portrait)": True,
        "(min-resolution: 2dppx) and (max-width: 599px)": True,
        "(hover: hover)": False,
        "not all and (max-width: 599px)": False,
        "print": False,
    }

    def test_each_query_shape_is_read_as_a_375px_phone_renders_it(self) -> None:
        for query, expected in self.CASES.items():
            with self.subTest(query=query):
                self.assertEqual(a.media_covers_phone("@media " + query), expected)


class TestMarkupPreparation(unittest.TestCase):
    def test_an_arrow_inside_an_attribute_does_not_end_the_tag(self) -> None:
        src = '<button onClick={() => go(a > b)} tabIndex={2}>x</button>'
        m = a.parse_markup("x.tsx", src)
        self.assertEqual(a.literal(m.elements[0].attrs["tabindex"]), "2")

    def test_masking_keeps_length_and_line_numbers(self) -> None:
        src = '<div\n  onClick={() => {\n    go();\n  }}\n/>\n<img src="a.png">'
        masked = a.mask_jsx(src)
        self.assertEqual(len(masked), len(src))
        self.assertEqual(masked.count("\n"), src.count("\n"))
        self.assertEqual(a.parse_markup("x.jsx", src).elements[1].line, 6)

    def test_an_image_inside_a_children_expression_is_still_seen(self) -> None:
        m = a.parse_markup("x.jsx", "<ul>{items.map(i => <img src={i.src} />)}</ul>")
        self.assertEqual([e.tag for e in m.elements], ["ul", "img"])

    def test_literal_reads_quoted_and_numeric_expressions_only(self) -> None:
        self.assertEqual(a.literal('{"3"}'), "3")
        self.assertEqual(a.literal("{3}"), "3")
        self.assertIsNone(a.literal("{order}"))
        self.assertEqual(a.literal("5"), "5")


if __name__ == "__main__":
    unittest.main()
