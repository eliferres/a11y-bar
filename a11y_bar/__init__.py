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

import os
import re
from html.parser import HTMLParser
from typing import Callable, Dict, List, NamedTuple, Optional, Set, Tuple

__version__ = "0.1.0"

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
DESKTOP_PX = 1280.0      # the viewport the contrast and focus rules are judged at
PHONE_PX = 375.0         # the viewport the touch-target rule is judged at


class Finding(NamedTuple):
    rule: str
    path: str
    line: int
    message: str


AddFn = Callable[[str, str, int, str], None]


# ---------- colour ----------

def _channel(c: float) -> float:
    c = c / 255.0
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def luminance(rgb: Tuple[int, int, int]) -> float:
    """WCAG relative luminance."""
    r, g, b = (_channel(v) for v in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_ratio(fg: Tuple[int, int, int], bg: Tuple[int, int, int]) -> float:
    hi, lo = sorted((luminance(fg), luminance(bg)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


NAMED_COLOURS = {"white": (255, 255, 255, 1.0), "black": (0, 0, 0, 1.0)}


def parse_color(value: Optional[str], variables: Dict[str, str]) -> Optional[Tuple[int, int, int, float]]:
    """(r, g, b, alpha) for a hex, rgb() or rgba() literal, following var()
    references and their fallbacks; None for anything else."""
    if value is None:
        return None
    v = value.strip().lower()
    hops = 0
    while v.startswith("var(") and hops < 5:
        inner = v[4:v.rfind(")")]
        name, _, fallback = inner.partition(",")
        v = (variables.get(name.strip()) or fallback).strip().lower()
        hops += 1
    m = re.fullmatch(r"#([0-9a-f]{3,8})", v)
    if m:
        h = m.group(1)
        if len(h) in (3, 4):
            h = "".join(c * 2 for c in h)
        if len(h) not in (6, 8):
            return None
        alpha = int(h[6:8], 16) / 255.0 if len(h) == 8 else 1.0
        return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), alpha)
    m = re.fullmatch(r"rgba?\(([^)]*)\)", v)
    if m:
        parts = [p for p in re.split(r"[,\s/]+", m.group(1).strip()) if p]
        if len(parts) < 3:
            return None
        try:
            rgb = [round(float(p.rstrip("%")) * (2.55 if p.endswith("%") else 1)) for p in parts[:3]]
            alpha = 1.0
            if len(parts) > 3:
                alpha = float(parts[3].rstrip("%")) / (100.0 if parts[3].endswith("%") else 1.0)
        except ValueError:
            return None
        r, g, b = (max(0, min(255, c)) for c in rgb)
        return (r, g, b, alpha)
    return NAMED_COLOURS.get(v)


def over(fg: Tuple[int, int, int, float], bg: Tuple[int, int, int]) -> Tuple[int, int, int]:
    """A translucent foreground composited onto an opaque background."""
    a = fg[3]
    return (round(fg[0] * a + bg[0] * (1 - a)),
            round(fg[1] * a + bg[1] * (1 - a)),
            round(fg[2] * a + bg[2] * (1 - a)))


# ---------- CSS ----------

class Rule(NamedTuple):
    selector: str
    decls: Dict[str, str]
    line: int
    at: str              # enclosing at-rule preludes joined by " | ", "" at top level


def strip_comments(text: str) -> str:
    """Remove /* */ comments, keeping their newlines so line numbers hold."""
    return re.sub(r"/\*.*?\*/", lambda m: "\n" * m.group(0).count("\n"), text, flags=re.S)


def css_rules(text: str, first_line: int = 1) -> List[Rule]:
    """Every declaration block in a stylesheet, with its enclosing at-rules.

    A brace-depth walk rather than one regex, because the at-rule a rule sits
    in decides whether it paints at a given width: a regex over `sel { ... }`
    loses that, and a rule meant only for a 359px phone ends up deciding a
    desktop verdict. A statement at-rule (@charset, @import, `@layer a, b;`)
    ends at its own semicolon; without that, it merges with the selector after
    it and swallows that rule. Only `@`-led text ends at a `;`: a semicolon
    inside a selector (a[href*=";"]) or inside a prelude's own parentheses
    (@supports (background: url(a;b))) is ordinary text.
    """
    out: List[Rule] = []
    stack: List[str] = []
    buf: List[str] = []
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch == ";":
            pending = "".join(buf)
            if pending.lstrip().startswith("@") and pending.count("(") <= pending.count(")"):
                buf = []
                i += 1
                continue
        if ch == "{":
            prelude = "".join(buf).strip()
            buf = []
            if prelude.startswith("@"):
                stack.append(prelude)
                i += 1
                continue
            end = text.find("}", i + 1)
            if end < 0:
                end = n
            decls: Dict[str, str] = {}
            for d in text[i + 1:end].split(";"):
                if ":" in d:
                    k, _, v = d.partition(":")
                    decls[k.strip().lower()] = v.strip()
            if prelude:
                line = first_line + text.count("\n", 0, i)
                out.append(Rule(prelude, decls, line, " | ".join(stack)))
            i = end + 1
            continue
        if ch == "}":
            if stack:
                stack.pop()
            buf = []
            i += 1
            continue
        buf.append(ch)
        i += 1
    return out


def root_variables(rules: List[Rule]) -> Dict[str, str]:
    """Custom properties declared on :root or html."""
    found: Dict[str, str] = {}
    for r in rules:
        if ":root" in r.selector or r.selector == "html":
            found.update({k: v for k, v in r.decls.items() if k.startswith("--")})
    return found


def _split_args(expr: str) -> List[str]:
    """The comma-separated arguments of a function body, split at depth zero."""
    args, depth, start = [], 0, 0
    for i, ch in enumerate(expr):
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif ch == "," and depth == 0:
            args.append(expr[start:i])
            start = i + 1
    args.append(expr[start:])
    return [a.strip() for a in args]


def _split_calc(expr: str) -> Optional[Tuple[str, str, str]]:
    """(left, operator, right) for a two-term calc(). + and - count only with
    the surrounding spaces CSS requires, so var(--a-b) is not a subtraction."""
    depth = 0
    for i, ch in enumerate(expr):
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif depth == 0 and ch in "*+-":
            spaced = 0 < i < len(expr) - 1 and expr[i - 1].isspace() and expr[i + 1].isspace()
            if ch == "*" or spaced:
                return expr[:i].strip(), ch, expr[i + 1:].strip()
    return None


def px(value: Optional[str], variables: Optional[Dict[str, str]] = None,
       allow_em: bool = False, _depth: int = 0) -> Optional[float]:
    """A length in pixels, or None when it cannot be known from the file.

    rem is 16px and vw is read at the 1280px desktop viewport. em resolves only
    when allow_em is set, which is for media-query widths, where em is relative
    to the root by spec; on a font-size em is relative to the parent, which a
    file-level scan does not know, so it stays unresolved and the element is
    skipped. calc() of two terms, clamp(), min() and max() resolve when every
    term does: utility CSS writes padding as calc(var(--spacing) * 3) and fluid
    type as clamp(), so a scanner that skipped them would read almost nothing.
    """
    if value is None or _depth > 4:
        return None
    v = value.strip().lower()
    if variables is not None and v.startswith("var("):
        name = v[4:v.rfind(")")].split(",")[0].strip()
        return px(variables.get(name), variables, allow_em, _depth + 1)
    m = re.fullmatch(r"(clamp|min|max)\((.*)\)", v)
    if m:
        terms = [px(a, variables, allow_em, _depth + 1) for a in _split_args(m.group(2))]
        if any(t is None for t in terms):
            return None
        if m.group(1) == "clamp":
            return min(max(terms[1], terms[0]), terms[2]) if len(terms) == 3 else None
        if len(terms) != 2:
            return None
        return min(terms) if m.group(1) == "min" else max(terms)
    m = re.fullmatch(r"calc\((.*)\)", v)
    if m:
        split = _split_calc(m.group(1).strip())
        if not split:
            return None
        left, op, right = split
        if op == "*":
            if re.fullmatch(r"-?[\d.]+", right):
                a = px(left, variables, allow_em, _depth + 1)
                return None if a is None else a * float(right)
            if re.fullmatch(r"-?[\d.]+", left):
                b = px(right, variables, allow_em, _depth + 1)
                return None if b is None else b * float(left)
            return None
        a = px(left, variables, allow_em, _depth + 1)
        b = px(right, variables, allow_em, _depth + 1)
        if a is None or b is None:
            return None
        return a + b if op == "+" else a - b
    if re.fullmatch(r"-?0+(\.0+)?", v):
        return 0.0
    units = "px|rem|em|vw" if allow_em else "px|rem|vw"
    m = re.fullmatch(r"(-?[\d.]+)(%s)" % units, v)   # "1.5" alone is a line-height, not a length
    if not m:
        return None
    try:
        number = float(m.group(1))
    except ValueError:
        return None
    if m.group(2) in ("rem", "em"):
        return number * 16
    return number * DESKTOP_PX / 100.0 if m.group(2) == "vw" else number


MEDIA_TYPES = ("screen", "all", "only screen", "only all")
MEDIA_WIDTH = re.compile(r"\(\s*(min|max)-width\s*:\s*([^)]+)\)")
# Media Queries level 4 range syntax: (width <= 599px), (320px <= width <= 599px).
# `<` is read as `<=`; one pixel never moves a verdict at 375 or 1280.
MEDIA_RANGE = re.compile(r"\(\s*(?:([^()<>=]+?)\s*(<=|<|>=|>)\s*)?width\s*(?:(<=|<|>=|>)\s*([^()<>=]+?))?\s*\)")
MEDIA_TOUCH = re.compile(r"\(\s*(?:(?:any-)?hover\s*:\s*none|(?:any-)?pointer\s*:\s*coarse)\s*\)")
MEDIA_FINE = re.compile(r"\(\s*(?:(?:any-)?hover\s*:\s*hover|(?:any-)?pointer\s*:\s*fine)\s*\)")


def width_edges(part: str, variables: Optional[Dict[str, str]] = None):
    """(min, max) for one width condition, None when the part is not a width
    condition, or "unresolved" when it is one whose edge cannot be read."""
    m = MEDIA_WIDTH.fullmatch(part)
    if m:
        edge = px(m.group(2).strip(), variables, allow_em=True)
        if edge is None:
            return "unresolved"
        return (edge, None) if m.group(1) == "min" else (None, edge)
    r = MEDIA_RANGE.fullmatch(part)
    if not r or not (r.group(2) or r.group(3)):
        return None
    lo = hi = None
    if r.group(2):                                    # <value> op width
        edge = px(r.group(1).strip(), variables, allow_em=True)
        if edge is None:
            return "unresolved"
        if r.group(2) in ("<=", "<"):
            lo = edge
        else:
            hi = edge
    if r.group(3):                                    # width op <value>
        edge = px(r.group(4).strip(), variables, allow_em=True)
        if edge is None:
            return "unresolved"
        if r.group(3) in ("<=", "<"):
            hi = edge
        else:
            lo = edge
    return (lo, hi)


def _media_branches(query: str) -> List[List[str]]:
    body = re.sub(r"^@media\b", "", query.strip(), flags=re.I).strip().lower()
    return [[p.strip() for p in re.split(r"\s+and\s+", one.strip())]
            for one in body.split(",") if one.strip()]


def media_applies_at_desktop(query: str, variables: Optional[Dict[str, str]] = None) -> bool:
    """Whether a @media block paints at the 1280px desktop viewport.

    Every width condition in a branch must hold. A branch naming any other
    feature (prefers-color-scheme, hover, orientation) is not the default
    desktop rendering this check judges, so its rules are left out rather than
    guessed at.
    """
    for branch in _media_branches(query):
        ok = True
        for part in branch:
            if part in MEDIA_TYPES:
                continue
            edges = width_edges(part, variables)
            if edges is None or edges == "unresolved":
                ok = False
                break
            lo, hi = edges
            if (lo is not None and lo > DESKTOP_PX) or (hi is not None and hi < DESKTOP_PX):
                ok = False
                break
        if ok:
            return True
    return False


def media_covers_phone(query: str, variables: Optional[Dict[str, str]] = None) -> bool:
    """Whether a @media block paints on a phone: a branch whose width
    conditions all hold at 375px, or one naming a touch pointer (hover: none,
    pointer: coarse, or their any- forms) however it is narrowed.

    A fine pointer, a `not` prefix or a media type other than screen/all
    (print, speech) is not a phone. Any other feature (orientation,
    resolution, prefers-*) is ignored and the widths decide. Refusing a branch
    this function cannot fully read would discard a real phone-only size raise
    and report a compliant button, so the unread feature is passed over.
    """
    for branch in _media_branches(query):
        lo = hi = None
        touch, ok = False, True
        for part in branch:
            if part in MEDIA_TYPES:
                continue
            if not part.startswith("(") or MEDIA_FINE.fullmatch(part):
                ok = False
                break
            if MEDIA_TOUCH.fullmatch(part):
                touch = True
                continue
            edges = width_edges(part, variables)
            if edges is None:
                continue
            if edges == "unresolved":
                ok = False
                break
            if edges[0] is not None:
                lo = edges[0] if lo is None else max(lo, edges[0])
            if edges[1] is not None:
                hi = edges[1] if hi is None else min(hi, edges[1])
        if not ok:
            continue
        if touch:
            return True
        if (lo is None or lo <= PHONE_PX) and (hi is None or hi >= PHONE_PX):
            return True
    return False


def _at_chain_paints(prelude: str, media_test: Callable[[str, Optional[Dict[str, str]]], bool],
                     variables: Optional[Dict[str, str]]) -> bool:
    """A rule paints when every enclosing at-rule is @layer, @supports, or a
    @media the given test accepts."""
    for one in prelude.split(" | "):
        one = one.strip()
        if not one:
            continue
        m = re.match(r"@([a-zA-Z-]+)", one)
        kind = m.group(1).lower() if m else ""
        if kind in ("layer", "supports"):
            continue
        if kind != "media" or not media_test(one, variables):
            return False
    return True


def paints_at_desktop(rule: Rule, variables: Optional[Dict[str, str]] = None) -> bool:
    return _at_chain_paints(rule.at, media_applies_at_desktop, variables)


def paints_on_phone(rule: Rule, variables: Optional[Dict[str, str]] = None) -> bool:
    return _at_chain_paints(rule.at, media_covers_phone, variables)


def font_size_and_weight(decls: Dict[str, str], variables: Dict[str, str]) -> Tuple[float, bool]:
    size = px(decls.get("font-size"), variables) or 16.0
    return size, is_bold(decls.get("font-weight"))


def is_bold(weight: Optional[str]) -> bool:
    w = (weight or "").strip().lower()
    return w in ("bold", "bolder") or (w.isdigit() and int(w) >= 700)


def background_color(decls: Dict[str, str]) -> Optional[str]:
    """The background colour a rule sets, or None. A gradient or image has no
    single colour to measure against, so it reads as unknown."""
    if "background-color" in decls:
        return decls["background-color"]
    shorthand = decls.get("background")
    if shorthand is None or "gradient(" in shorthand.lower() or "url(" in shorthand.lower():
        return None
    tok = re.search(r"var\([^)]*\)|#[0-9a-fA-F]{3,8}|rgba?\([^)]*\)|[a-z]+", shorthand)
    return tok.group(0) if tok else None


def required_ratio(size: float, bold: bool) -> float:
    return 3.0 if size >= LARGE_PX or (size >= LARGE_BOLD_PX and bold) else 4.5


def _contrast_message(ratio: float, size: float, bold: bool, need: float) -> str:
    return "%.2f:1 on %.0fpx%s text, needs %.1f:1" % (ratio, size, " bold" if bold else "", need)


# ---------- markup ----------

VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta",
             "param", "source", "track", "wbr"}
UNPAINTED_TAGS = {"script", "style", "title", "noscript", "template", "head"}


class Element:
    __slots__ = ("tag", "attrs", "line", "parent", "text")

    def __init__(self, tag: str, attrs: Dict[str, Optional[str]], line: int, parent: Optional["Element"]):
        self.tag, self.attrs, self.line, self.parent, self.text = tag, attrs, line, parent, ""

    @property
    def classes(self) -> Set[str]:
        return set((self.attrs.get("class") or self.attrs.get("classname") or "").split())

    @property
    def ids(self) -> Set[str]:
        ident = (self.attrs.get("id") or "").strip()
        return {ident} if ident else set()

    def label(self) -> str:
        return self.tag + "".join(".%s" % c for c in sorted(self.classes))


class Markup(HTMLParser):
    """The element tree of one file, the text each element carries directly
    (painted text only), and its <style> blocks with their starting lines."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = Element("#document", {}, 0, None)
        self.stack = [self.root]
        self.elements: List[Element] = []
        self.styles: List[Tuple[int, str]] = []
        self._style_line: Optional[int] = None

    def _open(self, tag: str, attrs: list, push: bool) -> None:
        tag = tag.lower()
        el = Element(tag, {k.lower(): v for k, v in attrs}, self.getpos()[0], self.stack[-1])
        self.elements.append(el)
        if tag == "style" and push:
            self._style_line = self.getpos()[0]
        if push and tag not in VOID_TAGS:
            self.stack.append(el)

    def handle_starttag(self, tag: str, attrs: list) -> None:
        self._open(tag, attrs, push=True)

    def handle_startendtag(self, tag: str, attrs: list) -> None:
        self._open(tag, attrs, push=False)   # JSX self-closing: <div />

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag == "style":
            self._style_line = None
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                return

    def handle_data(self, data: str) -> None:
        if self._style_line is not None:
            self.styles.append((self._style_line, data))
        elif data.strip() and not any(e.tag in UNPAINTED_TAGS for e in self.stack):
            self.stack[-1].text += data


def mask_jsx(text: str) -> str:
    """JSX with every attribute expression made safe for an HTML parser.

    Inside a tag, a brace expression such as onClick={() => go(a, b)} carries
    a `>` that would end the tag early and spaces that would split it into
    stray attributes. Those characters are replaced with underscores inside
    tag-level braces only, at the same length and with newlines kept, so line
    numbers still match the source. Children expressions outside tags are
    left alone, so an <img> inside {items.map(...)} is still seen.
    """
    out = list(text)
    i, n = 0, len(text)
    while i < n:
        if text[i] == "<" and i + 1 < n and text[i + 1].isalpha():
            depth, quote = 0, None
            i += 1
            while i < n:
                c = text[i]
                if depth == 0:
                    if quote:
                        if c == quote:
                            quote = None
                    elif c in "\"'":
                        quote = c
                    elif c == "{":
                        depth = 1
                    elif c == ">":
                        break
                else:
                    if c == "{":
                        depth += 1
                    elif c == "}":
                        depth -= 1
                    elif c in "<> \t":
                        out[i] = "_"
                i += 1
        i += 1
    return "".join(out)


def parse_markup(path: str, text: str) -> Markup:
    if os.path.splitext(path)[1].lower() in (".jsx", ".tsx"):
        text = mask_jsx(text)
    parser = Markup()
    parser.feed(text)
    parser.close()
    return parser


def literal(value: Optional[str]) -> Optional[str]:
    """An attribute value with JSX braces and quotes removed; None when it is
    an expression whose value only exists at runtime."""
    if value is None:
        return None
    v = value.strip()
    if v.startswith("{") and v.endswith("}"):
        v = v[1:-1].strip("_ \t")
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'`":
            return v[1:-1]
        if re.fullmatch(r"-?\d+|true|false", v):
            return v
        return None
    return v


def style_blocks(markup: Markup) -> List[Tuple[int, str]]:
    """The <style> blocks that parse to at least one rule. A block holding
    only a comment or an @import is not a stylesheet for these purposes."""
    return [(line, css) for line, css in markup.styles if css_rules(strip_comments(css))]


# ---------- selector matching for the inherited contrast walk ----------

UNREADABLE_SELECTOR = re.compile(r"[>+~*\[\]:(){}]")
Compound = Tuple[Optional[str], Set[str], Set[str]]


def compound_parts(token: str) -> Optional[Compound]:
    """(tag, classes, ids) for one compound selector, or None when it uses a
    combinator, pseudo-class, attribute matcher or universal selector that
    this matcher does not read."""
    token = token.strip()
    if not token or UNREADABLE_SELECTOR.search(token):
        return None
    m = re.fullmatch(r"([a-zA-Z][a-zA-Z0-9-]*)?((?:[.#][A-Za-z0-9_-]+)*)", token)
    if not m:
        return None
    tag = m.group(1).lower() if m.group(1) else None
    classes = set(re.findall(r"\.([A-Za-z0-9_-]+)", m.group(2) or ""))
    ids = set(re.findall(r"#([A-Za-z0-9_-]+)", m.group(2) or ""))
    if not tag and not classes and not ids:
        return None
    return tag, classes, ids


def parse_selector(selector: str) -> Optional[List[Compound]]:
    """The compounds of a selector joined only by descendant combinators."""
    parts = selector.split()
    compounds = [compound_parts(p) for p in parts]
    if not compounds or any(c is None for c in compounds):
        return None
    return compounds  # type: ignore[return-value]


def _compound_matches(compound: Compound, el: Element) -> bool:
    tag, classes, ids = compound
    return (not tag or el.tag == tag) and classes <= el.classes and ids <= el.ids


def selector_matches(compounds: List[Compound], chain: List[Element]) -> bool:
    """chain runs from the outermost ancestor to the element itself."""
    if not _compound_matches(compounds[-1], chain[-1]):
        return False
    j = len(chain) - 2
    for compound in reversed(compounds[:-1]):
        while j >= 0 and not _compound_matches(compound, chain[j]):
            j -= 1
        if j < 0:
            return False
        j -= 1
    return True


# ---------- the rules ----------

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

        fg = next((parse_color(cascaded(chain, i)[0]["color"], variables)
                   for i in levels if "color" in cascaded(chain, i)[0]), None)
        if not fg:
            continue
        raw_bg = next((background_color(cascaded(chain, i)[0]) for i in levels
                       if background_color(cascaded(chain, i)[0]) is not None), None)
        bg = parse_color(raw_bg, variables)
        if not bg or bg[3] < 1.0:
            continue
        raw_size = next((cascaded(chain, i)[0]["font-size"] for i in levels
                         if "font-size" in cascaded(chain, i)[0]), None)
        size = 16.0 if raw_size is None else px(raw_size, variables)
        if size is None:
            continue                                  # 2em, 150%, runtime: unknown, never 16px
        bold = is_bold(next((cascaded(chain, i)[0]["font-weight"] for i in levels
                             if "font-weight" in cascaded(chain, i)[0]), None))
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
            if hidden or _has_attr(el, "alt", "aria-label", "aria-labelledby", "title"):
                continue
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
