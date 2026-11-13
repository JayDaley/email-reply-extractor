# Versioning

Two numbers are maintained in this repository, and they are independent.
Each is shared by the Python, TypeScript and Rust ports (see
[ports.md](ports.md)); `make check-sync` fails when the ports disagree.

- The package version is the semantic version of all three packages:
  `__version__` in `python/src/email_reply_extractor/__init__.py` (which
  `python/pyproject.toml` reads dynamically), `version` in
  `typescript/package.json`, and `version` in `rust/Cargo.toml`. The bump
  policy is in `CLAUDE.md`.
- `EXTRACTION_VERSION` is the generation stamp of the routine that derives an
  extraction's text: an `int` in `python/src/email_reply_extractor/extraction.py`,
  `typescript/src/extraction.ts` and `rust/src/extraction.rs`, recorded with
  the corpus digest in `conformance/digest.json`. This page is its contract.

A release that changes the derived text bumps both: the generation by one, and
the package's minor component. A release that does not touch the derived text
bumps only the package version.

## What EXTRACTION_VERSION identifies

The generation of the text-deriving routine, meaning `extraction.py`,
`cleaning.py`, `html_text.py` and the vendored `_fragments.py` taken together
(and their counterparts in the TypeScript and Rust ports). Two runs of the
library, in any of the three languages, that report the same
`EXTRACTION_VERSION` derive the
same text from the same input.

It is currently **6**. Six generations exist; the first four shipped in the
origin application, `mailing-list-ai-check`:

| Generation | First shipped | What changed |
|---:|---|---|
| 1 | initial release | The initial routine. |
| 2 | v1.2.0 | The localized quote-header and custom signature-block rules. |
| 3 | v1.11.0 | Quote-header truncation before the fragment scan, with folded and double-spaced header tolerance and the transport-header pasted-evidence guard. |
| 4 | v1.15.0 | The parent-diff continuation rule for re-wrapped remainder lines; Gmail quote wrappers holding blockquotes no longer classify the author's inline replies as quoted. |
| 5 | email-reply-extractor v1.1.0 | Fragment edges kept (blank lines between fragments preserved); the Outlook-boundary fix applies to every occurrence instead of the first eight. |
| 6 | email-reply-extractor v1.2.0 | Latin-script localized quote-header blocks (French, Spanish, Portuguese, Italian, Dutch, German openers, with U+00A0 / U+202F colon spacing) and "Original message" dividers; a message with the author's tagged inline replies below such a block keeps its generation-5 treatment. |

`email-reply-extractor` 1.0.0 ships generation 4 unchanged: vendoring the
fragment scanner left every corpus output byte-identical, and a differential
run over 110,725 real stored messages found zero mismatches against the old
implementation.

## The rules

- **Increment by one, by hand, in the same commit** as any change to those four
  modules that could alter the extracted text, the cleaned text, or an
  extraction's status — a whitespace-only difference included.
- **Do not increment** for comments, docstrings, type annotations, or refactors
  that keep every output byte identical, and not for changes outside those four
  modules.
- **It only ever increases.** Consumers compare with `<` (see below), so
  ordering is what lets an older consumer read a store written by a newer
  routine as current rather than offering to re-derive text it cannot
  reproduce.
- **The digest moves with it.** `conformance/digest.json` (the digest and
  the generation it was recorded against) is re-recorded by `make vectors` in
  the same commit as the increment, and every port's constant is incremented
  in that commit too.

## How the behavior is pinned

Nothing can automatically decide whether a change altered the derived text, so
the repository pins the routine's real behavior instead: a single SHA-256 over
the composite output of every `.eml` fixture in `fixtures/`, in stem order,
recorded in `conformance/digest.json`. Four values go into the hash per
fixture — the stem, the stage-1-then-stage-2 composite, and stage 1's `text`
and its `method` — so a change confined to stage 1 that stage 2 happens to
erase is still caught. Every field is length-prefixed, so no combination of
contents can imitate a different split. The exact algorithm, the digest of
each earlier generation, and the rest of the conformance data are specified in
[conformance/README.md](../conformance/README.md).

This is stricter than the corpus test in `python/tests/test_extraction.py`,
which compares tolerantly (blank lines dropped, each line stripped). A
whitespace-only difference passes there and still changes the exact bytes a
consumer derives. The digest is taken over the exact strings.

The contract is enforced in several places:

- `python/tests/test_extraction_version.py` requires at least 20 fixtures (so
  a digest over an empty corpus cannot pin nothing and still pass), requires
  the live corpus digest to equal the recorded one, and requires the recorded
  `extraction_version` to equal `EXTRACTION_VERSION`, so a bump without a
  re-record and a re-record without a bump both fail.
- `make vectors` re-records the digest only when `EXTRACTION_VERSION` was
  incremented or the fixture set changed; a moved digest without either is
  reported as an error.
- The TypeScript and Rust test suites compute the same digest from their own
  output, so a port that diverges from the reference fails.
- `make check-sync` requires every port's `EXTRACTION_VERSION` to equal the
  recorded `extraction_version`.

The re-recording procedure is in [CONTRIBUTING.md](../CONTRIBUTING.md).

## How a consumer should use the constant

An application that stores derived text should treat `EXTRACTION_VERSION` as
the provenance of that text.

1. **Store it alongside the derived text**, in the same row, when the text is
   written. Store it as an integer.
2. **Compare with `<`.** Stored text is stale when its stamp is *lower* than
   the constant the running library reports. It is not stale when the stamp is
   equal, and it is not stale when the stamp is higher — a store written by a
   newer routine is text the running library cannot reproduce, and treating it
   as stale would replace good text with worse.
3. **Treat a missing stamp as older than every generation.** A NULL or absent
   value reads as stale, because text derived before the stamp existed is text
   whose generation is unknown.

A sketch:

```python
from email_reply_extractor import EXTRACTION_VERSION


def is_stale(stored_stamp: int | None) -> bool:
    return stored_stamp is None or stored_stamp < EXTRACTION_VERSION
```

What to do about stale rows is the application's decision: re-derive them
eagerly, re-derive on read, or report the count and let an operator choose.
Re-deriving is always safe, because both stages are pure functions of the
message body.

The package version is not a substitute for this. It moves for reasons that
have nothing to do with the derived text — a documentation change, a packaging
change, a new export — and comparing it would report text as stale that is
byte-identical to what the running library would produce.
