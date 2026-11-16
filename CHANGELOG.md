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

## [1.5.0] - 2026-11-16

Summary: Generation 9: the parent-diff assist keeps a line the author repeated from the thread parent when the message also carries it inside its own marked quotation.

- Bump `EXTRACTION_VERSION` to 9; the corpus digest is unchanged, because no fixture exercises the new rule, and is re-recorded against generation 9.
- `strip_parent_content` restores a run of matched lines when the message's marked quotation (the quote-marked lines of its body) contains the run and the run's block of consecutive non-blank lines has more unmatched than matched words (the own-quotation rule).
- After a restoration the continuation rule is evaluated again from the remaining aligned-run and rewrap marks and never marks a restored line.
- `extract_new_text` passes the body's marked quotation to the parent-diff assist and to the HTML oracle's assist; the localized inline-reply test passes none.
- Python: `strip_parent_content` gained the keyword-only argument `quoted_text` (default `None`).
- TypeScript: `stripParentContent` gained the option `quotedText`.
- Rust: added `strip_parent_content_with_quotation`, which takes `quoted_text: Option<&str>`; `strip_parent_content` is that function with no quotation.
- Over the 154,429 stored extractions of the origin application, the extracted text of 1,146 messages changes and 2,572 lines that generation 8 removed are kept.
- Added two adversarial inputs for the rule to each port's complexity tests, and a specification transcription compared with the implementation on random inputs to the Python suite.
- Added `docs/generation-9.md`.
- Added a "Limitations" section to the repository README and the Python README, with a pointer to it in the TypeScript and Rust READMEs.

## [1.4.0] - 2026-11-15

Summary: Generation 8: text sent through Apple Mail's Share sheet, which the plain part marks as quoted, is read from the HTML part; extraction results report the boundaries between the separate runs of an inline reply.

- Bump `EXTRACTION_VERSION` to 8 and re-record the corpus digest.
- `split_html_parts` treats a `<blockquote>` whose parent element carries the class `Apple-Mail-URLShareWrapperClass` as a Share-sheet container: its direct text is not quoted; a `<blockquote>` nested inside it is quoted as before.
- Added the field `has_share_container` to `HtmlParts` (default `False`): true when a Share-sheet container opens outside every quoted container and outside `head`.
- `extract_new_text` derives the text from the HTML part's novel text, with the `html-` method prefix, whenever the HTML holds a Share-sheet container and its novel text is not blank (path (d)).
- Over 99 stored messages of the origin application that carry the Share-sheet marker, all 76 empty and all 10 below-floor extractions yield text.
- Added the synthetic fixture `toppost-share-sheet-synthetic-01`: a reply sent through the Share sheet, with an attribution line and a nested quoted message inside the container.
- Added `docs/generation-8.md`.
- Added the field `part_starts` to `ExtractionResult` (default empty): the indices of the lines of `text` that begin a new part, where content removed by stage 1 (a quoted block, an attribution, parent-diff or HTML-quote content) separated two runs of the author's text.
- Part boundaries are found by aligning each non-blank line of `text`, in order, with the body it was derived from (the full `html_to_text` rendering for the `html-` paths); a line with no counterpart abandons the alignment and the text is reported as one part.
- Added the keyword argument `part_starts` to `clean_for_scoring` (`partStarts` in the TypeScript options; the new function `clean_for_scoring_with_parts` in Rust) and the field `parts` to `CleanResult`: the cleaned text divided at those boundaries, omitting parts that cleaning emptied.
- The Rust differential example reports `has_share_container`, `part_starts` and `parts`.
- `part_starts` and `parts` do not alter `text`, `method`, `status` or `ignored_lines`; the corpus digest is unchanged by them.
- Over 29,895 stage-1 extractions sampled from the origin application, the alignment succeeds for all but 11, and 26% of the extractions have more than one part.

## [1.3.0] - 2026-11-14

Summary: Generation 7: the parent-diff assist runs in time linear in its input, with a new aligned-run rule; the fragment scanner inserts the collapsed attribution header literally.

- Bump `EXTRACTION_VERSION` to 7 and re-record the corpus digest.
- Replaced `difflib.SequenceMatcher` in the parent-diff aligned-run rule with an alignment around the longest common run of lines: the run's lines are marked when it has substance, and each side of it is matched only against the same side of the parent, in any order; ties go to the earliest start in the child, then the earliest occurrence in the parent.
- Over 179,566 stored messages of the origin application the new aligned-run rule changes the output of 34 (stage-1 text of 26, cleaned text of 13, method of 11, status of none).
- The rewrap and continuation rules answer their substring tests from an index over the parent's word-stream (a table of words and adjacent word pairs, then a suffix automaton built on first need) instead of searching the stream once per line; the answers are unchanged.
- The inline-reply author-tag check tests all tags in one pass over the parent and one over the text above the quote header instead of searching both once per tag; the answers are unchanged.
- The fragment scanner inserts the collapsed "On … wrote:" header literally instead of passing it to `re.sub` as a replacement template: backslash sequences in a body are no longer rewritten, an invalid escape no longer fails the extraction, and repeated `\1` no longer expands the output quadratically.
- Added the synthetic fixture `toppost-repeated-signature-synthetic-01`: a quoted signature block repeated in the plain part and held once in the HTML quote.
- Added `python/tests/test_parent_diff.py`: the aligned-run rule's tie-breaks, a brute-force reference of its specification, and exactness tests of the word-stream index and the author-tag check.
- Added complexity tests for the aligned-run, rewrap and continuation rules and the author-tag check.
- Added `docs/generation-7.md`.
- Removed the hand-ported `difflib` modules from the TypeScript and Rust ports.
- The TypeScript port keeps the parent-diff tables in typed arrays (`hashTables.ts`), because a JavaScript `Map` holds at most 2^24 entries.
- The TypeScript port reads code points through its own helper: in Node 26.5 an optimised `String.prototype.codePointAt` returns a full code point for an unpaired high surrogate at the end of a sliced string.

## [1.2.3] - 2026-11-13

Summary: Processing time made linear in input size for inputs that previously took quadratic or cubic time; release workflows hardened.

- Replaced the wrapped-attribution search in the fragment scanner with a linear matcher; a body of repeated `On ` without `wrote:` took cubic time.
- Replaced the `Name <a@b> wrote:` attribution pattern and the titled contact-line pattern with linear matchers; a line of repeated `<a@` took cubic time.
- Replaced the German `Am … schrieb …:` attribution patterns and the mailing-list footer pattern with linear matchers.
- Anchored the "Original message" divider patterns so that a run of dashes is tried once; a line of 10,000 dashes took 14 seconds in the Python port.
- Rewrote the tails of the sign-off, greeting and postal-address line patterns to remove adjacent overlapping repetitions.
- Made the quote-header block search, the pasted-header check, the trailing sign-off debris scan and the custom signature divider scan single-pass.
- Made the parent-diff continuation rule a worklist that re-examines only the neighbors of newly marked lines.
- Made stray HTML end tags and the Gmail wrapper scan constant-time per tag by tracking open-element counts and open wrappers.
- TypeScript: the inline-reply tag check no longer compiles a regular expression per tagged line.
- Added a complexity test file to each port: adversarial inputs that must complete within a fixed time.
- Pinned every GitHub Action to a commit SHA, set `permissions: contents: read` on every workflow, disabled credential persistence on checkout and pinned the npm version used for publishing.
- Added `SECURITY.md` and a Dependabot configuration for GitHub Actions.
- No change to the derived text: output is byte-identical for every input; `EXTRACTION_VERSION` remains 6.

## [1.2.2] - 2026-11-13

Summary: Release-process corrections; the first release published to npm through trusted publishing.

- Corrected the first-release instruction for npm in `CONTRIBUTING.md`: a release published by hand cannot carry provenance.
- Configured npm trusted publishing for `publish-npm.yml`; this release is the first published to npm by that workflow.
- No change to the derived text; `EXTRACTION_VERSION` remains 6.

## [1.2.1] - 2026-11-13

Summary: TypeScript and Rust ports added; the repository restructured to maintain three ports against one conformance suite.

- Added a TypeScript port in `typescript/` (npm package `email-reply-extractor`, ESM, no runtime dependencies, Node 20 or later) with the same public API in camelCase.
- Added a Rust port in `rust/` (crate `email-reply-extractor`, MSRV 1.80, runtime dependencies `regex` and `fancy-regex`) with the same public API.
- Moved the Python package, its tests and its tools into `python/`; the published Python package and its API are unchanged.
- Moved the fixture corpus from `tests/fixtures/` to the top-level `fixtures/`.
- Added `conformance/`: the decoded corpus with the reference outputs (`corpus.json`) and call-level vectors recorded from the Python test suite (`vectors/`), which all three ports are tested against.
- Moved the recorded corpus digest from `test_extraction_version.py` to `conformance/digest.json`, the single source for all three ports; the digest value is unchanged.
- Added `python/tests/test_port_hazards.py`, which exercises the Python string, regular-expression, `difflib` and HTML-parser semantics that a port must reproduce.
- Added `make vectors`, `make check-vectors` and `make check-sync`; the last verifies that the version, `EXTRACTION_VERSION` and the per-port `LICENSE` copies agree across the ports.
- Added `docs/ports.md`, the port contract: Python is the reference implementation, and a behaviour change is complete only when all three ports pass the regenerated conformance suite.
- Added TypeScript, Rust and conformance jobs to CI, and publish workflows for npm and crates.io.
- `EXTRACTION_VERSION` remains 6.

## [1.2.0] - 2026-11-13

Summary: Generation 6 of the extraction routine — Latin-script localized quote-header blocks and "Original message" dividers are recognized.

- Bump `EXTRACTION_VERSION` to 6 and re-record the corpus digest.
- Recognize French Outlook quote-header blocks (`De :` / `Envoyé :` or `Envoye :` / `Date :` / `À :` or `A :` / `Cc :` / `Objet :`), accepting a space, tab, U+00A0 or U+202F before and after the colon.
- Recognize Spanish (`De:` … `Asunto:`), Portuguese (`De:` … `Assunto:`), Italian (`Da:` … `Oggetto:`), Dutch (`Van:` … `Onderwerp:`) and German (`Von:` … `Betreff:`) quote-header blocks.
- Require a localized block to carry at least two field lines after its opener, one of them its language's subject label, because the opener and several field labels are ordinary words.
- Recognize the localized "Original message" dividers: French `Message d'origine` (straight or curly apostrophe) and `Message transféré`, Spanish `Mensaje original` / `Mensaje reenviado`, Portuguese `Mensagem original` / `Mensagem encaminhada`, Italian `Messaggio originale` / `Messaggio inoltrato`, Dutch `Oorspronkelijk bericht` / `Doorgestuurd bericht`, German `Ursprüngliche Nachricht` / `Weitergeleitete Nachricht`.
- Keep the generation-5 treatment for a message whose author answers inline with tagged lines (`[Med] …`) below a localized block, so the parent-diff assist keeps removing the quoted parent around those answers.
- Add an optional `localized` keyword to `find_quote_header_block`, `strip_after_quote_header_block`, `strip_after_original_message_divider`, `custom_clean` and `count_unquoted_content_lines`; the default recognizes the localized forms.
- Add two French fixtures, `toppost-french-quoteheader-regext-01` and `toppost-french-originalmsg-divider-regext-01`, reconstructed from delivered list copies.

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
