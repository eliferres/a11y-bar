"""a11y-bar: a static, browser-free accessibility check for a web project's files.

Six WCAG 2.2 rules can be decided from the files alone, without rendering
anything: text contrast, a visible focus indicator, touch-target size, a
reduced-motion fallback, positive tabindex, and text alternatives for images.
The CSS-backed rules read stylesheets (built CSS plus <style> blocks); the
markup-backed rules read .html, .htm, .jsx and .tsx through an HTML parser.

Every rule leans the same way when it cannot read something: a selector,
length or colour it does not understand is skipped, never guessed at, so the
cost of a gap is a missed finding rather than a false one.
"""
from __future__ import annotations

__version__ = "0.1.0"

from .colour import contrast_ratio, luminance, over, parse_color  # noqa: E402
from .css import (DESKTOP_PX, PHONE_PX, Rule, css_rules, media_applies_at_desktop,  # noqa: E402
                  media_covers_phone, px, root_variables, strip_comments, width_edges)
from .markup import Element, Markup, literal, mask_jsx, parse_markup  # noqa: E402
from .rules import DEFAULT_MIN_TARGET, RULES, Finding, Source, scan  # noqa: E402
from .cli import UsageError, collect, main  # noqa: E402

__all__ = [
    "DEFAULT_MIN_TARGET", "DESKTOP_PX", "Element", "Finding", "Markup", "PHONE_PX", "RULES", "Rule",
    "Source", "UsageError", "collect", "contrast_ratio", "css_rules", "literal", "luminance", "main",
    "mask_jsx", "media_applies_at_desktop", "media_covers_phone", "over", "parse_color", "parse_markup",
    "px", "root_variables", "scan", "strip_comments", "width_edges",
]
