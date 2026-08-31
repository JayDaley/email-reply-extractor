# Versioning

Two numbers are maintained in this repository, and they are independent.

- `__version__` (in `src/email_reply_extractor/__init__.py`) is the package's
  semantic version. `pyproject.toml` reads it dynamically, so the two never
  drift. The bump policy is in `CLAUDE.md`.
- `EXTRACTION_VERSION` (an `int` in `src/email_reply_extractor/extraction.py`)
  is the generation stamp of the routine that derives an extraction's text.
  This page is its contract.

A release that changes the derived text bumps both: the generation by one, and
the package's minor component. A release that does not touch the derived text
bumps only the package version.

## What EXTRACTION_VERSION identifies

The generation of the text-deriving routine, meaning `extraction.py`,
`cleaning.py`, `html_text.py` and the vendored `_fragments.py` taken together.
Two runs of the library that report the same `EXTRACTION_VERSION` derive the
same text from the same input.

It is currently **4**. Four generations exist, all first shipped in the origin
application, `mailing-list-ai-check`:

| Generation | First shipped | What changed |
|---:|---|---|
| 1 | initial release | The initial routine. |
| 2 | v1.2.0 | The localized quote-header and custom signature-block rules. |
| 3 | v1.11.0 | Quote-header truncation before the fragment scan, with folded and double-spaced header tolerance and the transport-header pasted-evidence guard. |
| 4 | v1.15.0 | The parent-diff continuation rule for re-wrapped remainder lines; Gmail quote wrappers holding blockquotes no longer classify the author's inline replies as quoted. |

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
- **The digest moves with it.** `EXPECTED_DIGEST` and
  `DIGEST_EXTRACTION_VERSION` in `tests/test_extraction_version.py` are
  re-recorded in the same commit as the increment.

## How the behavior is pinned

Nothing can automatically decide whether a change altered the derived text, so
`tests/test_extraction_version.py` pins the routine's real behavior instead: a
single SHA-256 over the composite output of every `.eml` fixture in
`tests/fixtures`, in stem order. Three values go into the hash per fixture —
the stage-1-then-stage-2 composite, plus stage 1's `text` and its `method` — so
a change confined to stage 1 that stage 2 happens to erase is still caught.
Every field is length-prefixed, so no combination of contents can imitate a
different split.

This is stricter than the corpus test in `tests/test_extraction.py`, which
compares tolerantly (blank lines dropped, each line stripped). A whitespace-only
difference passes there and still changes the exact bytes a consumer derives.
The digest is taken over the exact strings.

Three tests hold the contract together:

- The corpus must contain at least 20 fixtures, so a digest over an empty
  corpus cannot pin nothing and still pass.
- `corpus_digest()` must equal `EXPECTED_DIGEST`.
- `DIGEST_EXTRACTION_VERSION` must equal `EXTRACTION_VERSION`, so a bump
  without a re-record and a re-record without a bump are both visible in the
  diff of that one file.

The re-recording procedure, including the exact command, is in
[CONTRIBUTING.md](../CONTRIBUTING.md).

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
