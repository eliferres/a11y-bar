"""The command line: which files to read, the report, and the exit codes."""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
from typing import List, Optional, Tuple

from . import __version__
from .markup import MarkupError, parse_markup, style_blocks
from .rules import DEFAULT_MIN_TARGET, RULES, Finding, Source, _read, scan


CSS_EXT = {".css"}
MARKUP_EXT = {".html", ".htm", ".jsx", ".tsx"}
SKIP_DIRS = {"node_modules", ".git", ".next", "coverage", "__pycache__"}
SOURCE_DIRS = ("src", "app", "components")


class UsageError(Exception):
    """A problem with what the tool was asked to read: exit 2."""


def _walk(directory: str) -> List[str]:
    found = []
    for dirpath, dirnames, filenames in os.walk(directory):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        found += [os.path.join(dirpath, f) for f in sorted(filenames)]
    return found


def collect(paths: List[str]) -> Tuple[List[Source], List[Source]]:
    """(stylesheets, markup files) to read for the given paths.

    One directory holding an out/ folder is read as a project root: out/ is
    the built site, so its CSS and HTML are read, and the source folders src/,
    app/ and components/ are read for markup too, so a positive tabindex or a
    bare <img> in a component is caught even when out/ is stale. Any other
    path is read as given, a directory recursively.
    """
    if len(paths) == 1 and os.path.isdir(os.path.join(paths[0], "out")):
        root = paths[0]
        pairs = [(f, True) for f in _walk(os.path.join(root, "out"))]
        for name in SOURCE_DIRS:
            if os.path.isdir(os.path.join(root, name)):
                pairs += [(f, False) for f in _walk(os.path.join(root, name))]
    else:
        pairs = []
        for p in paths:
            if os.path.isdir(p):
                pairs += [(f, True) for f in _walk(p)]
            elif os.path.isfile(p):
                if os.path.splitext(p)[1].lower() not in CSS_EXT | MARKUP_EXT:
                    raise UsageError("not a .css, .html, .htm, .jsx or .tsx file: %s" % p)
                pairs.append((p, True))
            else:
                raise UsageError("no such file or directory: %s" % p)
    css, markup, seen = [], [], set()
    for path, css_allowed in pairs:
        real = os.path.realpath(path)
        ext = os.path.splitext(path)[1].lower()
        if real in seen:
            continue
        if ext in MARKUP_EXT:
            markup.append(Source(path, os.path.normpath(path)))
        elif ext in CSS_EXT and css_allowed:
            css.append(Source(path, os.path.normpath(path)))
        else:
            continue
        seen.add(real)
    return css, markup


def _has_style_block(src: Source) -> bool:
    if os.path.splitext(src.path)[1].lower() not in (".html", ".htm"):
        return False
    return bool(style_blocks(parse_markup(src.path, _read(src.path))))


def check_scope(css: List[Source], markup: List[Source], paths: List[str]) -> None:
    """Refuse to report a pass on files it never read.

    A scan pointed at the wrong folder finds nothing and would otherwise exit
    0. The CSS-backed rules cannot be decided without a stylesheet (a page with
    no CSS still gets the browser's default focus ring), and no rule can be
    decided without markup, so either half missing is a usage error.
    """
    where = ", ".join(paths)
    if not markup:
        raise UsageError("no .html, .htm, .jsx or .tsx files found in %s" % where)
    if not css and not any(_has_style_block(m) for m in markup):
        raise UsageError("no CSS found in %s (no .css file and no <style> block)" % where)


def _positive_px(value: str) -> float:
    try:
        number = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError("not a number: %r" % value)
    if not math.isfinite(number) or number <= 0:
        raise argparse.ArgumentTypeError("must be a finite number greater than 0: %r" % value)
    return number


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:   # one line on stderr, exit 2
        self.exit(2, "%s: %s (see --help)\n" % (self.prog, message))


def parse_args(argv: List[str]) -> argparse.Namespace:
    parser = _Parser(
        prog="a11y-bar",
        description="Check a web project's files for the WCAG 2.2 rules that can be "
                    "decided without a browser.",
    )
    parser.add_argument("paths", nargs="*", default=["."],
                        help="files or directories to check (default: the current directory); "
                             "one directory holding out/ is read as a project root")
    parser.add_argument("--json", action="store_true", help="print the findings as JSON")
    parser.add_argument("--min-target", type=_positive_px, default=DEFAULT_MIN_TARGET, metavar="PX",
                        help="minimum height for buttons and inputs, in CSS px "
                             "(default: %(default).0f, the WCAG 2.5.8 minimum)")
    parser.add_argument("--version", action="version", version="%(prog)s " + __version__)
    return parser.parse_args(argv)


def render_text(findings: List[Finding], files: int) -> str:
    lines = ["%s:%d: [%s] %s" % (f.path, f.line, f.rule, f.message) for f in findings]
    verdict = "FAIL" if findings else "PASS"
    lines.append("%s: %d finding(s), %d file(s) read, %d rules" % (verdict, len(findings), files, len(RULES)))
    return "\n".join(lines)


def render_json(findings: List[Finding], files: List[str]) -> str:
    return json.dumps({
        "version": __version__,
        "files": files,
        "findings": [{"rule": f.rule, "wcag": RULES[f.rule][0], "file": f.path,
                      "line": f.line, "message": f.message} for f in findings],
    }, indent=2)


def main(argv: Optional[List[str]] = None) -> None:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        css, markup = collect(args.paths)
        check_scope(css, markup, args.paths)
        findings = scan(css, markup, args.min_target)
    except (UsageError, OSError, MarkupError) as e:
        print("a11y-bar: %s" % e, file=sys.stderr)
        sys.exit(2)
    shown = [s.shown for s in css + markup]
    print(render_json(findings, shown) if args.json else render_text(findings, len(shown)))
    sys.exit(1 if findings else 0)
