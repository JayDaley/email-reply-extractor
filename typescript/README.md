# email-reply-extractor

[![npm version](https://img.shields.io/npm/v/email-reply-extractor)](https://www.npmjs.com/package/email-reply-extractor)
[![License: MIT](https://img.shields.io/npm/l/email-reply-extractor)](https://github.com/JayDaley/email-reply-extractor/blob/main/LICENSE)
[![CI](https://github.com/JayDaley/email-reply-extractor/actions/workflows/ci.yml/badge.svg)](https://github.com/JayDaley/email-reply-extractor/actions/workflows/ci.yml)

**Extract the new text from an email reply in TypeScript or JavaScript: strip
quoted text, quote headers, signatures and sign-offs.**

`email-reply-extractor` removes the quoted reply (`>` lines, "On … wrote:"
attribution lines, Outlook quote-header blocks, "Original message" dividers)
from top-posted, bottom-posted and inline replies, then optionally removes
greetings, signatures, sign-offs, mailing-list footers and legal disclaimers.

This package is the TypeScript port of the Python reference implementation
([`email-reply-extractor` on PyPI](https://pypi.org/project/email-reply-extractor/)).
It carries the same version number and `EXTRACTION_VERSION`, and derives
byte-identical text from the same input: its test suite replays the
conformance data generated from the Python package. It has no runtime
dependencies and runs on Node.js 20 or later and in any runtime with ES2022
regular expressions.

## Contents

- [Install](#install)
- [What it does](#what-it-does)
- [Usage](#usage)
  - [Quickstart](#quickstart)
  - [Thread parent and HTML part](#thread-parent-and-html-part)
- [API reference](#api-reference)
  - [Argument convention](#argument-convention)
  - [Status values](#status-values)
  - [Method strings](#method-strings)
- [Extraction version](#extraction-version)
- [Supported runtimes](#supported-runtimes)
- [Documentation](#documentation)
- [License](#license)

## Install

```
npm install email-reply-extractor
```

The package is published as an ES module with TypeScript declarations.

## What it does

Stage 1, `extractNewText`, removes everything the author did not write in this
message:

- quoted lines at any indentation, including the `...>` form that survives
  `format=flowed` rewrapping;
- attribution lines: `On … wrote:`, `Name <a@b> wrote:`, the German
  `Am … schrieb …:` and the Japanese `…のメール:` forms, and forms wrapped
  across two or three lines (including the French `a écrit :` and Spanish
  `escribió:` terminators);
- Outlook-style forwarded/quote-header blocks (`From:` / `Sent:` / `To:` /
  `Subject:`) with English, French, Spanish, Portuguese, Italian, Dutch, German
  and Chinese labels;
- "Original message" and "Forwarded message" dividers in the same languages;
- the quoted thread that follows a sign-off boundary in a fully top-posted
  reply with no `>` markers;
- when the caller supplies them, content provably present in the thread parent
  or in the HTML part's blockquotes.

It keeps the author's greetings, sign-offs and signature blocks, and inline
replies written between quoted blocks.

Stage 2, `cleanForScoring`, removes that formulaic furniture from stage-1
text: greetings, sign-offs, signature blocks, mailing-list footers, mobile
taglines ("Sent from my iPhone") and trailing legal disclaimers. It also
reports which non-blank input lines it removed.

The stage-1 text is the author's full novel content, which is what an
application stores and displays; the stage-2 text is a strict, documented
subset of it, which is what an application passes to a downstream consumer
such as a classifier. Because stage 2 reports the lines it dropped, a stage-1
view can mark exactly what was set aside.

Both stages are pure and perform no I/O. `extractNewText` never throws: an
unexpected error is reported as `status: "failed"`.

The rules of both stages are documented in
[docs/extraction.md](https://github.com/JayDaley/email-reply-extractor/blob/main/docs/extraction.md).

## Usage

### Quickstart

```ts
import { cleanForScoring, extractNewText } from "email-reply-extractor";

const body = `Hi Dan,

Two comments below.

On Tue, Jul 7, 2026, at 03:25, Dan Wing wrote:
> I would lean heavily towards solution (2).

Agreed, and the draft already says so.

Best,
Lucas
`;

const result = extractNewText(body);
result.status; // "ok"
result.method; // "erp"
result.text; // "Hi Dan,\n\nTwo comments below.\n\nAgreed, and the draft already says so.\n\nBest,\nLucas"

const cleaned = cleanForScoring(result.text);
cleaned.text; // "Two comments below.\n\nAgreed, and the draft already says so."
cleaned.ignoredLines; // [0, 6, 7] — the greeting and the two sign-off lines
```

The quote and its attribution line are removed from `result.text`, and the
reply below the quote is kept; the greeting and the sign-off remain, and
`cleanForScoring` removes them.

### Thread parent and HTML part

Two options improve the result when the caller can supply them, and change
nothing when they are omitted:

```ts
// The thread parent's raw body, resolved by the caller from In-Reply-To.
// Removes a quoted previous message that carries no '>' markers at all.
const withParent = extractNewText(body, { parentBody });

// The message's decoded text/html part. Used as a structural oracle: as the
// body when there is no usable plain part, as a replacement for a flattened
// plain part, or to remove a quoted message leaked unmarked into the plain part.
const withHtml = extractNewText(body, { htmlBody });
```

`cleanForScoring` takes the HTML signature container's visible text as an
optional hint, which removes signature furniture that carries no `-- `
delimiter and no recognizable contact shape:

```ts
import { cleanForScoring, splitHtmlParts } from "email-reply-extractor";

const parts = splitHtmlParts(htmlBody);
const cleaned = cleanForScoring(result.text, { htmlSignatureText: parts.signatureText });
```

The package does not parse MIME. Its functions take decoded strings: the
`text/plain` body, the `text/html` body and the parent's body.

## API reference

| Name                                                      | What it is                                                                            |
| --------------------------------------------------------- | ------------------------------------------------------------------------------------- |
| `extractNewText(body, { parentBody?, htmlBody? })`        | Stage 1; returns an `ExtractionResult`. `body` may be `null` or `undefined`.          |
| `ExtractionResult`                                        | `{ text, method, status }`.                                                           |
| `STATUS_OK`, `STATUS_EMPTY`, `STATUS_FAILED`              | The three `status` values; the `ExtractionStatus` type is their union.                |
| `EXTRACTION_VERSION`                                      | Integer generation stamp of the text-deriving routine.                                |
| `cleanForScoring(extractedText, { htmlSignatureText? })`  | Stage 2; returns a `CleanResult`.                                                     |
| `CleanResult`                                             | `{ text, ignoredLines }`.                                                             |
| `htmlToText(html)`                                        | Visible text of an HTML part, block structure as newlines.                            |
| `splitHtmlParts(html)`                                    | The same text split into `HtmlParts`.                                                 |
| `HtmlParts`                                               | `{ novelText, quotedText, signatureText }`.                                           |
| `stripParentContent(text, parentBody, { continuation? })` | Remove lines of `text` provably drawn from `parentBody`.                              |
| `tolerantLines(text)`                                     | The non-blank lines of `text`, edge whitespace stripped and U+00A0 unified to spaces. |
| `textsEquivalent(a, b)`                                   | `true` when `a` and `b` differ at most in whitespace.                                 |
| `VERSION`                                                 | The package version.                                                                  |

The result types are frozen plain objects. Their fields are the Python
package's fields in camelCase: `ignored_lines` is `ignoredLines`,
`novel_text` is `novelText`.

### Argument convention

Required arguments are positional. Every optional argument of the Python API
(`parent_body`, `html_body`, `html_signature_text`, `continuation`) is a
property of an options object passed last, named in camelCase. An omitted
property, `undefined` and `null` all mean the Python default (`None` or
`False`).

| Python                                                  | TypeScript                                                 |
| ------------------------------------------------------- | ---------------------------------------------------------- |
| `extract_new_text(body, parent_body=p, html_body=h)`    | `extractNewText(body, { parentBody: p, htmlBody: h })`     |
| `clean_for_scoring(text, html_signature_text=s)`        | `cleanForScoring(text, { htmlSignatureText: s })`          |
| `strip_parent_content(text, parent, continuation=True)` | `stripParentContent(text, parent, { continuation: true })` |

### Status values

- `ok` — text was extracted.
- `empty` — nothing was left: a blank body, an HTML-only message with no usable
  HTML text, or a body that reduced to quotes alone.
- `failed` — extraction raised internally; `text` is empty.

### Method strings

`ExtractionResult.method` records which path produced the text. The base value
is one of:

- `none` — no body to work from.
- `erp` — the vendored fragment scan needed no cleanup.
- `erp+custom` — the fragment scan plus the custom cleanup pass.
- `custom-fallback` — the over-strip guard fired, and the custom pass ran on
  the whole body.
- `failed` — extraction raised internally.

Three modifiers combine with the base value:

- `html-` prefix — the text was derived from the HTML part, either because
  there was no usable plain body or because a flattened plain body was
  replaced (for example `html-erp+custom`).
- `+parent-diff` suffix — the parent-diff assist removed content (for example
  `erp+parent-diff`).
- `+html-quote` suffix — the HTML quoted-text oracle removed content from a
  plain extraction.

## Extraction version

`EXTRACTION_VERSION` is an integer identifying the generation of the routine
that derives the text. It is independent of the package version, only ever
increases, and is the same in the Python, TypeScript and Rust packages. It is
currently **6**.

An application that stores derived text stores `EXTRACTION_VERSION` alongside
it and compares the stored stamp with the running constant using `<` to decide
whether the text needs re-deriving. The contract is in
[docs/versioning.md](https://github.com/JayDaley/email-reply-extractor/blob/main/docs/versioning.md).

Because a generation change can move bytes without changing substance, the
package exports `tolerantLines` and `textsEquivalent`, so a consumer can
re-derive stored text and keep expensive downstream results when only
whitespace moved.

## Supported runtimes

Node.js 20, 22 and 24; each is exercised in CI. The port reproduces the Python
standard-library behaviour the reference relies on (the `str.isspace()`
whitespace set, Unicode `\w`, `\d` and `\b`, `re.IGNORECASE` matching,
`difflib.SequenceMatcher` and the `html.parser` tokenizer) with its own code
and tables generated from Python, so the output does not depend on the Unicode
version of the JavaScript engine.

## Documentation

- [docs/extraction.md](https://github.com/JayDaley/email-reply-extractor/blob/main/docs/extraction.md)
  — the rules of both stages.
- [docs/versioning.md](https://github.com/JayDaley/email-reply-extractor/blob/main/docs/versioning.md)
  — the `EXTRACTION_VERSION` contract.
- [docs/ports.md](https://github.com/JayDaley/email-reply-extractor/blob/main/docs/ports.md)
  — the contract between the Python, TypeScript and Rust ports.
- [conformance/README.md](https://github.com/JayDaley/email-reply-extractor/blob/main/conformance/README.md)
  — the conformance data and the corpus digest.
- [python/README.md](https://github.com/JayDaley/email-reply-extractor/blob/main/python/README.md)
  — the reference implementation, including a comparison with other libraries
  and the provenance of the code.
- [CHANGELOG.md](https://github.com/JayDaley/email-reply-extractor/blob/main/CHANGELOG.md)
  — one section per release of all three packages.

## License

MIT. See
[LICENSE](https://github.com/JayDaley/email-reply-extractor/blob/main/LICENSE).
The fragment scanner derives from `email-reply-parser` 0.5.12 (MIT, Copyright
(c) 2012 Zapier LLC).
