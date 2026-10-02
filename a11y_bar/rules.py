"""The six rules and the scan that runs them over a set of files."""
from __future__ import annotations

import math
import os
import re
from typing import Callable, Dict, List, NamedTuple, Optional, Set, Tuple

from .colour import contrast_ratio, over, parse_color
from .css import (Rule, background_color, css_rules, font_size_and_weight, is_bold, paints_at_desktop,
                  paints_on_phone, px, root_variables, split_args, strip_comments, substitute_vars)
from .markup import (Element, Markup, could_match, literal, may_match, parse_markup, parse_selector,
                     selector_matches, style_blocks)

# rule id -> (WCAG 2.2 success criterion, what it catches)
RULES: Dict[str, Tuple[str, str]] = {
    "contrast": ("1.4.3", "Text must meet the minimum contrast ratio against its background"),
    "focus-visible": ("2.4.7", "Keyboard focus must stay visible"),
    "touch-target": ("2.5.8", "Buttons and inputs must be tall enough to hit"),
    "reduced-motion": ("2.3.3", "Animated CSS must honor prefers-reduced-motion"),
    "focus-order": ("2.4.3", "Elements should not have a tabindex greater than zero"),
    "alt": ("1.1.1", "Images and videos must have a text alternative"),
}

LARGE_PX = 24.0          # 18pt
LARGE_BOLD_PX = 18.67    # 14pt bold
DEFAULT_MIN_TARGET = 24.0
# Browser default heading sizes in px; every heading is bold by default.
HEADING_PX = {"h1": 32.0, "h2": 24.0, "h3": 18.72, "h4": 16.0, "h5": 13.28, "h6": 10.72}
# Properties whose unknown value could change a contrast verdict.
CONTRAST_PROPS = ("color", "background", "background-color", "font", "font-size", "font-weight")


class Finding(NamedTuple):
    rule: str
    path: str
    line: int
    message: str


AddFn = Callable[[str, str, int, str], None]


def required_ratio(size: float, bold: bool) -> float:
    return 3.0 if size >= LARGE_PX or (size >= LARGE_BOLD_PX and bold) else 4.5


def _contrast_message(ratio: float, size: float, bold: bool, need: float) -> str:
    shown = round(ratio, 2)
    if shown >= need:                     # a failing 4.499:1 is shown as 4.49:1, never 4.50:1
        shown = math.floor(ratio * 100) / 100
    return "%.2f:1 on %.0fpx%s text, needs %.1f:1" % (shown, size, " bold" if bold else "", need)


def check_contrast_pairs(path: str, rules: List[Rule], variables: Dict[str, str],
                         reported: Set[str], add: AddFn) -> None:
    """contrast, first pass: one rule that sets both a colour and its own
    opaque background."""
    for r in rules:
        if not paints_at_desktop(r, variables):
            continue
        raw_bg = background_color(r.decls)
        if "color" not in r.decls or not raw_bg:
            continue
        fg = parse_color(r.decls["color"], variables)
        bg = parse_color(raw_bg, variables)
        if not fg or not bg or bg[3] < 1.0:   # a translucent background has no known backdrop
            continue
        heading = _heading_default(r.selector)
        raw_size = r.decls.get("font-size")
        size = (heading or 16.0) if raw_size is None else px(raw_size, variables)
        if size is None:
            continue                          # 2em, 150%, var(--f), font: menu: unknown, never 16px
        bold = is_bold(r.decls.get("font-weight")) or (heading is not None and "font-weight" not in r.decls)
        need = required_ratio(size, bold)
        ratio = contrast_ratio(over(fg, bg[:3]), bg[:3])
        if ratio < need:
            reported.add(r.selector)
            add("contrast", path, r.line, "%s: %s" % (r.selector, _contrast_message(ratio, size, bold, need)))


def _heading_default(selector: str) -> Optional[float]:
    """The smallest browser default size among the headings a selector
    targets, or None when any part of it targets something else."""
    sizes = []
    for one in selector.split(","):
        tag = re.match(r"(h[1-6])(?![\w-])", re.split(r"[\s>+~]+", one.strip())[-1])
        if not tag:
            return None
        sizes.append(HEADING_PX[tag.group(1)])
    return min(sizes)


def check_contrast_inherited(path: str, markup: Markup, rules: List[Rule], variables: Dict[str, str],
                             reported: Set[str], add: AddFn) -> None:
    """contrast, second pass: each element carrying text, with its colour,
    background, font size and weight resolved through its ancestors.

    Real pages put the background on a wrapper and the colour on the text, so
    the first pass alone sees very few pairs. The cascade here is simplified:
    rules that paint at 1280px are applied in specificity order, later wins
    within equal specificity. Colour inherits; background does not, so the walk
    stops at the nearest ancestor that paints one, and gives up if that one is
    translucent or unreadable.
    """
    prepared = []
    unreadable = []                                  # what a selector we cannot evaluate may match
    for order, r in enumerate(rules):
        if not paints_at_desktop(r, variables):
            continue
        for one in r.selector.split(","):
            compounds = parse_selector(one)
            if compounds:
                spec = (sum(len(c[2]) for c in compounds), sum(len(c[1]) for c in compounds),
                        sum(1 for c in compounds if c[0]))
                prepared.append((spec, order, compounds, one.strip(), r.decls))
            elif any(p in r.decls for p in CONTRAST_PROPS):
                compound = may_match(one)
                if compound is not None:
                    unreadable.append(compound)
    cache: Dict[int, Tuple[Dict[str, str], List[str]]] = {}

    def cascaded(chain: List[Element], upto: int) -> Tuple[Dict[str, str], List[str]]:
        el = chain[upto]
        if id(el) not in cache:
            sub = chain[:upto + 1]
            hits = sorted((h for h in prepared if selector_matches(h[2], sub)), key=lambda h: (h[0], h[1]))
            decls: Dict[str, str] = {}
            for h in hits:
                decls.update(h[4])
            cache[id(el)] = (decls, [h[3] for h in hits])
        return cache[id(el)]

    seen: Set[Tuple[str, str]] = set()
    for el in markup.elements:
        if not el.text.strip():
            continue
        chain: List[Element] = []
        node: Optional[Element] = el
        while node is not None and node.tag != "#document":
            chain.append(node)
            node = node.parent
        chain.reverse()
        levels = range(len(chain) - 1, -1, -1)       # the element first, then outward
        if any(_inline_style_could_paint(n) or any(could_match(c, n) for c in unreadable) for n in chain):
            continue                                  # something this read cannot evaluate may set it

        def nearest(read: Callable[[Dict[str, str]], Optional[str]]) -> Optional[str]:
            """The first value `read` finds on the element or its nearest ancestor."""
            return next((v for v in (read(cascaded(chain, i)[0]) for i in levels) if v is not None), None)

        fg = parse_color(nearest(lambda d: d.get("color")), variables)
        if not fg:
            continue
        bg = backdrop(chain, levels, cascaded, variables)
        if not bg or bg[3] < 1.0:
            continue
        raw_size = next((d.get("font-size") or _heading_size(chain[i]) for d, i in
                         ((cascaded(chain, i)[0], i) for i in levels)
                         if d.get("font-size") or chain[i].tag in HEADING_PX), None)
        size = 16.0 if raw_size is None else px(raw_size, variables)
        if size is None:
            continue                                  # 2em, 150%, runtime: unknown, never 16px
        bold = is_bold(next((d.get("font-weight") or "bold" for d, i in
                             ((cascaded(chain, i)[0], i) for i in levels)
                             if d.get("font-weight") or chain[i].tag in HEADING_PX), None))
        if any(s in reported for s in cascaded(chain, len(chain) - 1)[1]):
            continue                                  # the first pass already reported this rule
        need = required_ratio(size, bold)
        ratio = contrast_ratio(over(fg, bg[:3]), bg[:3])
        if ratio >= need:
            continue
        message = "%s (inherited): %s" % (el.label(), _contrast_message(ratio, size, bold, need))
        if (el.label(), message) in seen:
            continue
        seen.add((el.label(), message))
        add("contrast", path, el.line, message)


NOT_PAINTED = {"none", "hidden", "0", "0px"}
NOT_CLAUSE = re.compile(r":not\([^()]*\)")


def _paints(prop: str, value: str) -> bool:
    """Whether an outline, border or box-shadow value draws anything."""
    v = value.lower().replace("!important", "").strip()
    if not v:
        return False
    if prop == "box-shadow":
        return v != "none"
    return not any(tok in NOT_PAINTED for tok in v.split())


FOCUS_STATE = re.compile(r":focus(-visible)?(?![\w-])")


def draws_focus_ring(rule: Rule) -> bool:
    """A rule that targets :focus or :focus-visible (outside any :not()) and
    draws a visible outline, box-shadow or border there. WCAG 2.4.7 accepts
    either state; :focus-visible only spares pointer users the ring."""
    if not any(FOCUS_STATE.search(NOT_CLAUSE.sub("", one)) and ":not(:focus-visible)" not in one.replace(" ", "")
               for one in rule.selector.split(",")):
        return False                          # :not(:focus-visible) draws for the pointer, never the keyboard
    return any(_paints(prop, value) for prop, value in rule.decls.items()
               if prop in ("outline", "outline-style", "box-shadow") or prop.startswith("border"))


def removed_outline(rule: Rule) -> Optional[str]:
    """The value of an outline this rule removes, or None. A removal scoped
    to :not(:focus-visible) only hides the ring from pointer users and keeps
    it for the keyboard, so it does not count."""
    for prop in ("outline", "outline-style"):
        value = (rule.decls.get(prop) or "").strip().lower()
        if value and not _paints("outline", value):
            if all(":not(:focus-visible)" in one.replace(" ", "") for one in rule.selector.split(",")):
                return None
            return value
    return None


CANVAS = (255, 255, 255, 1.0)


def backdrop(chain: List[Element], levels: range,
             cascaded: Callable[[List[Element], int], Tuple[Dict[str, str], List[str]]],
             variables: Dict[str, str]) -> Optional[Tuple[int, int, int, float]]:
    """The background behind an element's text: the nearest ancestor-or-self
    that paints one, walking past transparent layers, and the white canvas a
    browser shows when nothing paints. None when that layer is an image or a
    colour this read cannot resolve."""
    for i in levels:
        raw = background_color(cascaded(chain, i)[0])
        if raw is None:
            continue
        value = raw.strip().lower()
        colour = parse_color(value, variables)
        if value in ("transparent", "none") or (colour is not None and colour[3] == 0):
            continue
        return colour
    return CANVAS


def _heading_size(el: Element) -> Optional[str]:
    return "%gpx" % HEADING_PX[el.tag] if el.tag in HEADING_PX else None


def _inline_style_could_paint(el: Element) -> bool:
    style = (el.attrs.get("style") or "").lower()
    return any(p in style for p in ("color", "background", "font"))


def check_focus_order(path: str, markup: Markup, add: AddFn) -> None:
    for el in markup.elements:
        value = literal(el.attrs.get("tabindex"))
        if value is not None and re.fullmatch(r"[+-]?\d+", value) and int(value) > 0:
            add("focus-order", path, el.line,
                "<%s tabindex=\"%s\"> moves it ahead of the document order" % (el.tag, value))


def _named(el: Element, *names: str) -> bool:
    """Whether one of these attributes gives a non-empty name. A JSX
    expression counts, since its value only exists at runtime."""
    for name in names:
        raw = el.attrs.get(name)
        if raw is None:
            continue
        value = literal(raw)
        if value is None or value.strip():
            return True
    return False


def check_alt(path: str, markup: Markup, add: AddFn) -> None:
    for el in markup.elements:
        if any(name.startswith("{") for name in el.attrs):
            continue                          # a JSX spread may carry the name
        if el.tag == "img":
            role = (literal(el.attrs.get("role")) or "").lower()
            # alt="" is the standard mark of a decorative image; an empty aria-label names nothing.
            if "alt" in el.attrs or _named(el, "aria-label", "aria-labelledby", "title") \
                    or role in ("presentation", "none"):
                continue
            add("alt", path, el.line, "<img> has no alt text")
        elif el.tag == "input" and (literal(el.attrs.get("type")) or "").lower() == "image":
            if not _named(el, "alt", "aria-label", "aria-labelledby", "title"):
                add("alt", path, el.line, '<input type="image"> has no alt text')
        elif el.tag == "video":
            hidden = (literal(el.attrs.get("aria-hidden")) or "").lower() == "true"
            if hidden or _named(el, "aria-label", "aria-labelledby", "title"):
                continue                      # alt is not a <video> attribute and names nothing
            add("alt", path, el.line, "<video> has no aria-label and is not aria-hidden")


def interactive_elements(markup: Markup) -> List[Element]:
    """Buttons, visible inputs, and links styled as buttons (a class containing
    btn or pill). A plain text link is exempt from the target-size minimum."""
    found = []
    for el in markup.elements:
        if el.tag == "button":
            found.append(el)
        elif el.tag == "input" and (literal(el.attrs.get("type")) or "").lower() != "hidden":
            found.append(el)
        elif el.tag == "a" and any(re.search(r"btn|pill", c, re.I) for c in el.classes):
            found.append(el)
    return found


def _user_agent_sized(el: Element) -> bool:
    """A button or input whose size the page never sets."""
    return el.tag in ("button", "input")


def selector_keys(selector: str) -> Optional[Set[str]]:
    """The tag and .class tokens the last compound of a selector requires, or
    None when it targets a state (:hover), an id, or nothing readable.
    Ancestors are ignored, so a rule can be credited to an element a browser
    would not give it: a missed finding, never a false one."""
    if ":" in selector:
        return None
    last = re.split(r"[\s>+~]+", selector.strip())[-1]
    last = re.sub(r"\[[^\]]*\]", "", last)
    if not last or "#" in last:
        return None
    tokens = re.findall(r"^[a-zA-Z][a-zA-Z0-9]*|\.[A-Za-z0-9_-]+", last)
    return set(tokens) if tokens else None


def target_height(keys: Set[str], rules: List[Rule], variables: Dict[str, str]) -> Optional[float]:
    """The height a phone gives an element, from min-height, height, or
    vertical padding plus the line box; None when nothing sets it.

    Every rule that paints at 375px is merged, so a size raised only inside a
    phone media query counts and one raised only for desktop does not.
    """
    merged: Dict[str, str] = {}
    for r in rules:
        if not paints_on_phone(r, variables):
            continue
        if _rule_targets(r, keys):
            merged.update(r.decls)
    h = px(merged.get("min-height"), variables)
    if h is None:
        h = px(merged.get("height"), variables)
    pad = None
    block = merged.get("padding-block") or merged.get("padding")
    if block:
        first = block if block.startswith(("calc", "clamp", "min(", "max(", "var(")) else block.split()[0]
        one = px(first, variables)
        if one is not None:
            pad = one * 2
    top, bottom = px(merged.get("padding-top"), variables), px(merged.get("padding-bottom"), variables)
    if top is not None and bottom is not None:
        pad = top + bottom
    if pad is None:
        return h
    size, _ = font_size_and_weight(merged, variables)
    raw_line = merged.get("line-height", "").strip()
    line = px(raw_line, variables)
    if line is None and re.fullmatch(r"[\d.]+", raw_line):
        line = float(raw_line) * size
    box = pad + (line or size * 1.2)
    return box if h is None else max(h, box)


def _rule_targets(rule: Rule, keys: Set[str]) -> bool:
    for one in rule.selector.split(","):
        want = selector_keys(one)
        if want and want <= keys:
            return True
    return False


TIME = re.compile(r"(?<![\w.-])(\d*\.?\d+)(ms|s)\b", re.I)
MOTION_PROPS = ("transition", "transition-duration", "animation", "animation-duration")


def animates(rule: Rule, variables: Dict[str, str]) -> bool:
    """Whether a rule sets a transition or animation with a duration above
    zero. In each comma-separated item the first time is the duration, so
    `opacity 0s 1s` (a delay alone) does not animate."""
    for prop in MOTION_PROPS:
        value = rule.decls.get(prop)
        if not value:
            continue
        for item in split_args(substitute_vars(value, variables)):
            times = TIME.findall(item)
            if times and float(times[0][0]) > 0:
                return True
    return False


# ---------- the scan ----------

class Source(NamedTuple):
    """One file to read, and the name it is reported under."""
    path: str
    shown: str


def scan(css_files: List[Source], markup_files: List[Source],
         min_target: float = DEFAULT_MIN_TARGET) -> List[Finding]:
    """Run every rule over the given files and return the findings in a
    stable order (file, line, rule)."""
    findings: List[Finding] = []

    def add(rule: str, path: str, line: int, message: str) -> None:
        findings.append(Finding(rule, path, line, message))

    # Every stylesheet the CSS-backed rules read: files, then <style> blocks.
    sheets: List[Tuple[str, str, int]] = []           # (shown, text, first line)
    for src in css_files:
        sheets.append((src.shown, strip_comments(_read(src.path)), 1))
    parsed: List[Tuple[Source, Markup]] = []
    for src in markup_files:
        markup = parse_markup(src.path, _read(src.path))
        parsed.append((src, markup))
        if os.path.splitext(src.path)[1].lower() in (".html", ".htm"):
            sheets += [(src.shown, strip_comments(css), line) for line, css in style_blocks(markup)]

    sheet_rules = [(shown, css_rules(text, first), text, first) for shown, text, first in sheets]
    variables: Dict[str, str] = {}                    # at the 1280px desktop
    phone_variables: Dict[str, str] = {}              # at the 375px phone
    for _shown, rules, _text, _first in sheet_rules:
        variables.update(root_variables(rules, paints_at_desktop))
        phone_variables.update(root_variables(rules, paints_on_phone))
    file_rules = [r for shown, rules, _t, _f in sheet_rules[:len(css_files)] for r in rules]
    all_rules = [r for _s, rules, _t, _f in sheet_rules for r in rules]

    reported: Set[str] = set()
    for shown, rules, _text, _first in sheet_rules[:len(css_files)]:
        check_contrast_pairs(shown, rules, variables, reported, add)

    interactive: List[Tuple[str, Element]] = []
    for src, markup in parsed:
        check_focus_order(src.shown, markup, add)
        check_alt(src.shown, markup, add)
        interactive += [(src.shown, el) for el in interactive_elements(markup)]
        if os.path.splitext(src.path)[1].lower() in (".html", ".htm"):
            own = [r for _l, css in style_blocks(markup) for r in css_rules(strip_comments(css))]
            check_contrast_inherited(src.shown, markup, file_rules + own, variables, reported, add)

    has_ring = any(draws_focus_ring(r) for _s, rules, _t, _f in sheet_rules for r in rules)
    if not has_ring:
        for shown, rules, _text, _first in sheet_rules:
            for r in rules:
                outline = removed_outline(r)
                if outline and paints_at_desktop(r, variables):
                    add("focus-visible", shown, r.line,
                        "%s sets outline: %s and no :focus or :focus-visible rule draws a ring" % (r.selector, outline))

    for shown, el in interactive:
        keys = {el.tag} | {"." + c for c in el.classes}
        height = target_height(keys, all_rules, phone_variables)
        if height is None and _user_agent_sized(el):
            continue                                  # WCAG 2.5.8 user-agent exception
        if height is None or height < min_target:
            got = "has no height set" if height is None else "is %.0fpx tall" % height
            add("touch-target", shown, el.line,
                "%s %s on a phone, needs %.0fpx" % (el.label(), got, min_target))

    has_fallback = any("prefers-reduced-motion" in r.at for r in all_rules)
    if not has_fallback:
        for shown, rules, _text, _first in sheet_rules:
            moving = next((r for r in rules if animates(r, variables)), None)
            if moving:
                add("reduced-motion", shown, moving.line,
                    "CSS animates and no @media (prefers-reduced-motion: reduce) block exists")
                break

    order = {rule: i for i, rule in enumerate(RULES)}
    findings.sort(key=lambda f: (f.path, f.line, order[f.rule], f.message))
    return findings


def _read(path: str) -> str:
    with open(path, encoding="utf-8", errors="replace") as fh:
        return fh.read()
