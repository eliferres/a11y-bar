# Contributing

Useful contributions:

- A wrong finding. Open an issue with the smallest HTML, JSX or CSS that
  produces it, and what a browser audit (axe or Lighthouse) says about the
  same page.
- A missed finding on markup or CSS the tool should be able to read.
- A new rule, if a WCAG success criterion can be decided from files alone
  without guessing. Name the criterion and the files that decide it.
- Corrections to anything in the README that turns out not to hold.

Ground rules: `a11y_bar/` stays standard-library only and supports Python
3.9. Every rule change ships with a fixture that trips it and one that
clears it under `tests/fixtures/rules/`, and the test asserts the exact
line the report prints. When the tool cannot read something, it skips it
rather than guessing; a change that trades a missed finding for a
possible false one needs a strong case in the pull request.

Run the suite with `python -m unittest discover -s tests -v`. If a change
alters what the demo prints, regenerate the transcript with
`UPDATE_DEMO_TRANSCRIPT=1 python -m unittest tests.test_demo_transcript`
and say so in the pull request. The picture in `demo/terminal.svg` is
redrawn from the transcript by the maintainer, and its test fails until
then, which is expected on such a pull request.
