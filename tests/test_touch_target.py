"""touch-target (WCAG 2.5.8): judged as a 375px phone renders it."""

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


def target(case: str, min_target: float = a.DEFAULT_MIN_TARGET) -> list:
    return check("target", case, "touch-target", min_target)


class TestHeight(unittest.TestCase):
    def test_a_20px_button_fails_the_24px_minimum(self) -> None:
        self.assertEqual(target("short"), [("short.html", 1, "button.pill is 20px tall on a phone, needs 24px")])

    def test_the_minimum_is_configurable(self) -> None:
        self.assertEqual(target("thirty-two"), [])
        self.assertEqual(target("thirty-two", 44.0),
                         [("thirty-two.html", 1, "button.pill is 32px tall on a phone, needs 44px")])

    def test_padding_plus_the_line_box_counts(self) -> None:
        self.assertEqual(target("padded-link", 44.0), [])

    def test_a_button_styled_link_with_no_height_fails(self) -> None:
        self.assertEqual(target("unsized-link"),
                         [("unsized-link.html", 1, "a.btn has no height set on a phone, needs 24px")])

    def test_a_plain_text_link_and_a_hidden_input_are_not_targets(self) -> None:
        self.assertEqual(target("plain-link"), [])
        self.assertEqual(target("hidden-input"), [])

    def test_a_hover_state_does_not_size_the_resting_box(self) -> None:
        self.assertEqual(len(target("hover-state")), 1)

    def test_a_custom_property_in_a_style_block_resolves(self) -> None:
        self.assertEqual(target("style-variable", 44.0), [])


class TestUserAgentException(unittest.TestCase):
    def test_unstyled_checkboxes_radios_and_buttons_are_exempt(self) -> None:
        self.assertEqual(target("native-controls"), [])


class TestPhoneViewport(unittest.TestCase):
    """A size raised only for phones counts; one raised only where a phone
    never renders does not."""

    def test_a_root_variable_raised_for_phones_applies(self) -> None:
        self.assertEqual(target("root-variable-phone"), [])

    def test_a_phone_width_raise_passes(self) -> None:
        self.assertEqual(target("phone-raise"), [])

    def test_a_coarse_pointer_raise_passes(self) -> None:
        self.assertEqual(target("coarse-raise"), [])

    def test_a_phone_padding_raise_passes(self) -> None:
        self.assertEqual(target("padding-raise"), [])

    def test_a_desktop_only_raise_fails(self) -> None:
        self.assertEqual(target("desktop-raise"),
                         [("desktop-raise.html", 1, "button.btn-small is 20px tall on a phone, needs 24px")])

    def test_a_raise_below_375px_fails(self) -> None:
        self.assertEqual(target("narrow-raise"),
                         [("narrow-raise.html", 1, "button.btn-small is 20px tall on a phone, needs 24px")])


if __name__ == "__main__":
    unittest.main()
