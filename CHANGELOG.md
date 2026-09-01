# Changelog

All notable changes to `email-reply-extractor`, newest release first. The
project follows [semantic versioning](https://semver.org/); the bump policy is
recorded in `CLAUDE.md`.

`EXTRACTION_VERSION`, the generation stamp of the text-deriving routine, is a
separate number with its own contract (`docs/versioning.md`); each release
section states the generation it ships when that generation changes.

## Format

The file is machine-readable. Every release is one section with exactly three
kinds of line, in this order:

```
## [<version>] - <date>

Summary: <one-line summary of the release>

- <one-line description of an individual change>
- <one-line description of an individual change>
```

Extraction rules (skip lines inside fenced code blocks — the template above is
itself a fenced block, and its placeholder header would otherwise parse as a
release):

- Release header: `^## \[(?P<version>[^\]]+)\] - (?P<date>\S+)$`
  `version` is a semantic version (`MAJOR.MINOR.PATCH`). `date` is either an
  ISO-8601 date (`YYYY-MM-DD`) or the literal `unreleased` for a version that
  is bumped in the source but not yet committed as a release.
- Summary: `^Summary: (?P<summary>.+)$` — exactly one per release, the first
  non-blank line after the release header.
- Change: `^- (?P<change>.+)$` — zero or more per release, each a single line
  (no wrapped continuation lines, no nested bullets).

Any other line (headings above level 2, blank lines, prose in this Format
section) is not part of a release record and can be ignored. Outside fenced
code blocks, no release section appears before the first `## [` header.

## [1.1.1] - 2026-09-01

Summary: The copyright owner in LICENSE is the IETF Intellectual Property Management Corporation.

- Change the LICENSE copyright owner from Jay Daley to IETF Intellectual Property Management Corporation; the license remains MIT.
- Update the install instructions to the published PyPI package.

## [1.1.0] - 2026-09-01

Summary: Generation 5 of the extraction routine — fragment edges kept and the Outlook-boundary fix uncapped — plus whitespace-tolerant equivalence helpers for consumers.

- Bump `EXTRACTION_VERSION` to 5 and re-record the corpus digest.
- Keep fragment edges in the vendored scanner instead of stripping each fragment's leading and trailing whitespace; blank lines between fragments are preserved and quote-header blocks are no longer disguised by glued signature lines.
- Apply the Outlook-boundary newline fix to every occurrence instead of the first eight, removing upstream's `re.MULTILINE`-as-count defect.
- Keep the pre-scan quote-header truncation: measurement over 95,319 stored extractions showed removing it leaks quote headers glued by the scanner's wrapped-attribution collapse (see docs/generation-5.md).
- Add `tolerant_lines` and `texts_equivalent` (module `equivalence`) so consumers re-deriving stored text can distinguish whitespace-only movement from substantive change.
- Update docs/generation-5.md with the shipped fixes, the measured effects, and the deferred attribution-collapse unification.

## [1.0.0] - 2026-09-01

Summary: First release of the two-stage new-text extraction pipeline as a standalone library, extracted from mailing-list-ai-check with byte-identical output and no runtime dependencies.

- Extract the extraction, cleaning and HTML-oracle modules from `mailing-list-ai-check` into the `email_reply_extractor` package, with the two-stage design unchanged: `extract_new_text` recovers the author's novel content, `clean_for_scoring` strips formulaic furniture from it.
- Publish the API as `extract_new_text`, `ExtractionResult`, `EXTRACTION_VERSION`, `STATUS_OK`, `STATUS_EMPTY`, `STATUS_FAILED`, `clean_for_scoring`, `CleanResult`, `html_to_text`, `split_html_parts`, `HtmlParts`, `strip_parent_content` and `__version__`.
- Vendor the fragment scanner of `email-reply-parser` 0.5.12 (MIT, Zapier) as `_fragments.py`, trimmed to the `read()` scan path the extractor consumes, which leaves the package with zero runtime dependencies.
- Preserve two upstream defects of that scanner byte for byte — the Outlook-boundary newline fix capped at 8 replacements, and per-fragment edge stripping before re-joining — because fixing either changes the derived text and so requires a generation bump.
- Carry `EXTRACTION_VERSION` over at 4, with the recorded corpus digest unchanged from the origin application: the vendoring left every fixture's derived bytes identical.
- Confirm equivalence beyond the corpus with a differential run over 110,725 real stored messages, comparing the old and vendored implementations on stage-1 text, method and status and on the stage-2 composite, with zero mismatches.
- Widen the supported Python range to 3.10 or later, from the origin application's 3.14, and exercise 3.10 through 3.14 in CI.
- Move the test suite over unchanged in substance: 226 tests, including the 24-message hand-labeled fixture corpus and the SHA-256 digest that pins the routine's exact output.
- Add the documentation set: `README.md`, `CONTRIBUTING.md`, `CLAUDE.md`, `docs/extraction.md`, `docs/versioning.md` and `docs/generation-5.md`.
