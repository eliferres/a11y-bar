"""Reading markup: the element tree an HTML parser builds from .html, .htm,
.jsx and .tsx files, and the selector matching the contrast walk uses."""
from __future__ import annotations

import os
import re
from html.parser import HTMLParser
from typing import Dict, List, Optional, Set, Tuple

from .css import css_rules, strip_comments

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


class MarkupError(ValueError):
    """A file the HTML parser could not read."""


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

    def error(self, message: str) -> None:
        """Python 3.9's parser base calls this on markup it cannot read and
        raises NotImplementedError when a subclass does not define it."""
        raise MarkupError(message)

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
    stray attributes. Those characters are replaced with "_" inside
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
    try:
        parser.feed(text)
        parser.close()
    except Exception as e:   # the parser's own failure modes vary by Python version
        raise MarkupError("cannot parse %s: %s" % (path, e)) from e
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
    """The compounds of a selector joined only by descendant combinators.
    :root is the html element."""
    parts = ROOT.sub("html", selector).split()
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


ROOT = re.compile(r":root\b")
# Rules for another state or a generated box do not paint the resting text.
NOT_RESTING = re.compile(r"::|:(hover|focus|focus-visible|focus-within|active|visited|checked|disabled|"
                         r"target|invalid|placeholder-shown)\b|:(before|after|selection|placeholder)\b")
FUNCTIONAL_PSEUDO = re.compile(r":[\w-]+\((?:[^()]|\([^()]*\))*\)")


def may_match(selector: str) -> Optional[Compound]:
    """For a selector parse_selector cannot evaluate, what an element must
    at least carry to be matched by it: the tag, classes and ids left in its
    last compound once combinators, pseudo-classes and attribute matchers are
    set aside. None when the selector targets another state or a pseudo
    element and so never paints the resting text."""
    selector = ROOT.sub("html", selector.strip())
    if NOT_RESTING.search(selector):
        return None
    last = re.split(r"\s*[>+~]\s*|\s+", selector)[-1]
    last = FUNCTIONAL_PSEUDO.sub("", last)
    last = re.sub(r"\[[^\]]*\]|:[\w-]+|\*", "", last)
    tag = re.match(r"[a-zA-Z][a-zA-Z0-9-]*", last)
    return (tag.group(0).lower() if tag else None,
            set(re.findall(r"\.([A-Za-z0-9_-]+)", last)), set(re.findall(r"#([A-Za-z0-9_-]+)", last)))


def could_match(compound: Compound, el: Element) -> bool:
    return _compound_matches(compound, el)
