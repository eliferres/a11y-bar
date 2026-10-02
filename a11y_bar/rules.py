"""The six rules and the scan that runs them over a set of files."""
from __future__ import annotations

import os
import re
from typing import Callable, Dict, List, NamedTuple, Optional, Set, Tuple

from .colour import contrast_ratio, over, parse_color
from .css import (Rule, background_color, css_rules, font_size_and_weight, is_bold, paints_at_desktop,
                  paints_on_phone, px, root_variables, strip_comments)
from .markup import Element, Markup, literal, parse_markup, parse_selector, selector_matches, style_blocks

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


class Finding(NamedTuple):
    rule: str
    path: str
    line: int
    message: str


AddFn = Callable[[str, str, int, str], None]


def required_ratio(size: float, bold: bool) -> float:
    return 3.0 if size >= LARGE_PX or (size >= LARGE_BOLD_PX and bold) else 4.5


def _contrast_message(ratio: float, size: float, bold: bool, need: float) -> str:
    return "%.2f:1 on %.0fpx%s text, needs %.1f:1" % (ratio, size, " bold" if bold else "", need)


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
        size, bold = font_size_and_weight(r.decls, variables)
        need = required_ratio(size, bold)
        ratio = contrast_ratio(over(fg, bg[:3]), bg[:3])
        if ratio + 0.005 < need:
            reported.add(r.selector)
            add("contrast", path, r.line, "%s: %s" % (r.selector, _contrast_message(ratio, size, bold, need)))


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
    for order, r in enumerate(rules):
        if not paints_at_desktop(r, variables):
            continue
        for one in r.selector.split(","):
            compounds = parse_selector(one)
            if compounds:
                spec = (sum(len(c[2]) for c in compounds), sum(len(c[1]) for c in compounds),
                        sum(1 for c in compounds if c[0]))
                prepared.append((spec, order, compounds, one.strip(), r.decls))
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

        def nearest(read: Callable[[Dict[str, str]], Optional[str]]) -> Optional[str]:
            """The first value `read` finds on the element or its nearest ancestor."""
            return next((v for v in (read(cascaded(chain, i)[0]) for i in levels) if v is not None), None)

        fg = parse_color(nearest(lambda d: d.get("color")), variables)
        if not fg:
            continue
        bg = parse_color(nearest(background_color), variables)
        if not bg or bg[3] < 1.0:
            continue
        raw_size = nearest(lambda d: d.get("font-size"))
        size = 16.0 if raw_size is None else px(raw_size, variables)
        if size is None:
            continue                                  # 2em, 150%, runtime: unknown, never 16px
        bold = is_bold(nearest(lambda d: d.get("font-weight")))
        if any(s in reported for s in cascaded(chain, len(chain) - 1)[1]):
            continue                                  # the first pass already reported this rule
        need = required_ratio(size, bold)
        ratio = contrast_ratio(over(fg, bg[:3]), bg[:3])
        if ratio + 0.005 >= need:
            continue
        message = "%s (inherited): %s" % (el.label(), _contrast_message(ratio, size, bold, need))
        if (el.label(), message) in seen:
            continue
        seen.add((el.label(), message))
        add("contrast", path, el.line, message)


def check_focus_order(path: str, markup: Markup, add: AddFn) -> None:
    for el in markup.elements:
        value = literal(el.attrs.get("tabindex"))
        if value is not None and re.fullmatch(r"-?\d+", value) and int(value) > 0:
            add("focus-order", path, el.line,
                "<%s tabindex=\"%s\"> moves it ahead of the document order" % (el.tag, value))


def _has_attr(el: Element, *names: str) -> bool:
    return any(name in el.attrs for name in names)


def check_alt(path: str, markup: Markup, add: AddFn) -> None:
    for el in markup.elements:
        if el.tag == "img":
            role = (literal(el.attrs.get("role")) or "").lower()
            if _has_attr(el, "alt", "aria-label", "aria-labelledby", "title") or role in ("presentation", "none"):
                continue
            add("alt", path, el.line, "<img> has no alt text")
        elif el.tag == "video":
            hidden = (literal(el.attrs.get("aria-hidden")) or "").lower() == "true"
            if hidden or _has_attr(el, "aria-label", "aria-labelledby", "title"):
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


MOTION = re.compile(r"(transition|animation)(-duration)?\s*:[^;}]*?(\d*\.?\d+)\s*(ms|s)\b")


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
    variables: Dict[str, str] = {}
    for _shown, rules, _text, _first in sheet_rules:
        variables.update(root_variables(rules))
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

    has_ring = any(":focus-visible" in text for _s, _r, text, _f in sheet_rules)
    if not has_ring:
        if interactive:
            shown, el = interactive[0]
            add("focus-visible", shown, el.line,
                "%d interactive element(s) and no :focus-visible rule in the CSS" % len(interactive))
        for shown, rules, _text, _first in sheet_rules:
            for r in rules:
                outline = (r.decls.get("outline") or "").strip().lower()
                if outline in ("none", "0") and paints_at_desktop(r, variables):
                    add("focus-visible", shown, r.line,
                        "%s sets outline: %s and no :focus-visible rule replaces it" % (r.selector, outline))

    for shown, el in interactive:
        keys = {el.tag} | {"." + c for c in el.classes}
        height = target_height(keys, all_rules, variables)
        if height is None or height < min_target:
            got = "has no height set" if height is None else "is %.0fpx tall" % height
            add("touch-target", shown, el.line,
                "%s %s on a phone, needs %.0fpx" % (el.label(), got, min_target))

    if not any("prefers-reduced-motion" in text for _s, _r, text, _f in sheet_rules):
        for shown, _rules, text, first in sheet_rules:
            m = next((m for m in MOTION.finditer(text) if float(m.group(3)) > 0), None)
            if m:
                add("reduced-motion", shown, first + text.count("\n", 0, m.start()),
                    "CSS animates and no @media (prefers-reduced-motion: reduce) block exists")
                break

    order = {rule: i for i, rule in enumerate(RULES)}
    findings.sort(key=lambda f: (f.path, f.line, order[f.rule], f.message))
    return findings


def _read(path: str) -> str:
    with open(path, encoding="utf-8", errors="replace") as fh:
        return fh.read()
