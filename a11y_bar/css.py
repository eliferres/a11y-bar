"""Reading stylesheets: rules with their at-rule chains, lengths, and
whether a rule paints at the desktop or the phone viewport."""
from __future__ import annotations

import re
from typing import Callable, Dict, List, NamedTuple, Optional, Tuple, Union

DESKTOP_PX = 1280.0      # the viewport the contrast and focus rules are judged at
PHONE_PX = 375.0         # the viewport the touch-target rule is judged at


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


Edges = Union[None, str, Tuple[Optional[float], Optional[float]]]


def width_edges(part: str, variables: Optional[Dict[str, str]] = None) -> Edges:
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
