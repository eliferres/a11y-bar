# Changelog

All notable changes to this project are documented in this file. The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2026-10-02

### Added

- `a11y-bar` command that checks a web project's CSS and HTML, JSX and TSX files for six WCAG 2.2 rules without a browser.
- `contrast` rule (1.4.3): text contrast from rules that set both colours, and from colours and backgrounds inherited through the page's element tree.
- `focus-visible` rule (2.4.7): a focus outline removed with no `:focus` or `:focus-visible` rule drawing a replacement ring.
- `touch-target` rule (2.5.8): buttons, inputs and button-styled links shorter than 24px as a 375px phone renders them, with unstyled buttons and inputs exempt.
- `reduced-motion` rule (2.3.3): CSS with a transition or animation longer than zero, including durations set through custom properties, and no `prefers-reduced-motion` media query.
- `focus-order` rule (2.4.3): any `tabindex` greater than zero, in HTML or JSX.
- `alt` rule (1.1.1): images and image buttons with no text alternative, and videos with no accessible name.
- Project root reading: a directory holding `out/` is checked from its built output plus its `src/`, `app/` and `components/` markup.
- `--json` output with each finding's rule, WCAG criterion, file, line and message.
- `--min-target PX` to hold touch targets to a different height, such as 44.
- Exit codes 0 for clean, 1 for findings, and 2 for usage errors, including a scan that found no markup or no CSS to check.
