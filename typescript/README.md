# email-reply-extractor

[![npm version](https://img.shields.io/npm/v/email-reply-extractor)](https://www.npmjs.com/package/email-reply-extractor)
[![License: MIT](https://img.shields.io/npm/l/email-reply-extractor)](https://github.com/JayDaley/email-reply-extractor/blob/main/LICENSE)
[![CI](https://github.com/JayDaley/email-reply-extractor/actions/workflows/ci.yml/badge.svg)](https://github.com/JayDaley/email-reply-extractor/actions/workflows/ci.yml)

**Extract the new text from an email reply in TypeScript or JavaScript: strip
quoted text, quote headers, signatures and sign-offs.**

`email-reply-extractor` removes the quoted reply (`>` lines, "On … wrote:"
attribution lines, Outlook quote-header blocks, "Original message" dividers)
from top-posted, bottom-posted and inline replies, then optionally removes
greetings, signatures, sign-offs, mailing-list footers and legal disclaimers,
using the sender's and addressees' display names and the sender's repeated
signature lines as evidence when the caller has them.

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
- [Limitations](#limitations)
- [Usage](#usage)
  - [Quickstart](#quickstart)
  - [Thread parent and HTML part](#thread-parent-and-html-part)
  - [Sender and addressee evidence](#sender-and-addressee-evidence)
  - [Parts of an inline reply](#parts-of-an-inline-reply)
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
  `Am … schrieb …:` and the Japanese `…のメール:` forms, forms wrapped
  across up to five lines (including the French `a écrit :` and Spanish
  `escribió:` terminators), and other wordings above a quote that carry an
  e-mail address, a clock time and a date, or a speech verb ("Ana Lima
  said:");
- Outlook-style forwarded/quote-header blocks (`From:` / `Sent:` / `To:` /
  `Subject:`) with English, French, Spanish, Portuguese, Italian, Dutch, German
  and Chinese labels;
- "Original message" and "Forwarded message" dividers in the same languages,
  and the short "Original" dividers of QQ Mail and Foxmail;
- the short tail of a quote line that a client re-wrapped without a marker;
- the quoted thread that follows a sign-off boundary in a fully top-posted
  reply with no `>` markers;
- when the caller supplies them, content provably present in the thread parent
  or in the HTML part's blockquotes.

It keeps the author's greetings, sign-offs and signature blocks, and inline
replies written between quoted blocks.

Stage 2, `cleanForScoring`, removes that formulaic furniture from stage-1
text: greetings, sign-offs, signature blocks, mailing-list footers, mobile
taglines ("Sent from my iPhone") and trailing legal disclaimers. Given the
sender's display name, the addressees' display names and the sender's
learned signature lines, it also removes sign-offs, name lines, greetings and
signature lines that no fixed list identifies. It reports which non-blank
input lines it removed.

The stage-1 text is the author's full novel content, which is what an
application stores and displays; the stage-2 text is a strict, documented
subset of it, which is what an application passes to a downstream consumer
such as a classifier. Because stage 2 reports the lines it dropped, a stage-1
view can mark exactly what was set aside.

Both stages are pure and perform no I/O. `extractNewText` never throws: an
unexpected error is reported as `status: "failed"`.

The rules of both stages are documented in
[docs/extraction.md](https://github.com/JayDaley/email-reply-extractor/blob/main/docs/extraction.md).

## Limitations

The rules are heuristics over text, and their known limits are the same in
every port: the evidence the caller must supply, text the author repeats from
the thread parent, the languages and mail clients recognized, and the scope
of the measurements. They are listed in the
[Limitations section of the repository README](https://github.com/JayDaley/email-reply-extractor/blob/main/README.md#limitations).

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
result.method; // "custom"
result.text; // "Hi Dan,\n\nTwo comments below.\n\n\nAgreed, and the draft already says so.\n\nBest,\nLucas"

const cleaned = cleanForScoring(result.text);
cleaned.text; // "Two comments below.\n\nAgreed, and the draft already says so."
cleaned.ignoredLines; // [0, 7, 8] — the greeting and the two sign-off lines
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
// plain part or for the plain part of an Apple Mail Share-sheet message, or to
// remove a quoted message leaked unmarked into the plain part.
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

### Sender and addressee evidence

Three optional properties of `cleanForScoring`'s options carry evidence about
the message: `authorName`, the sender's display name; `addresseeNames`, the
display names of the people the message answers or is addressed to (for
example the thread parent's sender and the To and Cc display names); and
`signatureLines`, the lines the sender repeats at the end of their messages,
learned by `learnSignatureLines` from the stage-1 texts of the sender's
earlier messages. Without them only the rules that need no evidence run.

```ts
import { cleanForScoring, extractNewText, learnSignatureLines } from "email-reply-extractor";

// [subject, plain body] of the sender's earlier messages, stored by the caller.
const earlier = Array.from({ length: 6 }, (_, i) => [
  `Draft comments, round ${i}`,
  `Comment ${i} on the draft.\n\nThanks,\nAna\nExample Networks\nhttps://example.net/ana`,
]);
const learned = learnSignatureLines(
  earlier.map(([, text]) => extractNewText(text).text),
  { subjects: earlier.map(([subject]) => subject) },
);
learned; // ["ana", "example networks", "https://example.net/ana", "thanks,"]

const body = `Joe, Priya,

The new section reads well. One change: keep the timer at 10 seconds.

Thanks,
Ana
Example Networks
https://example.net/ana
`;

const result = extractNewText(body);
cleanForScoring(result.text).text;
// "The new section reads well. One change: keep the timer at 10 seconds.\n\nThanks,\nAna\nExample Networks\nhttps://example.net/ana"

const cleaned = cleanForScoring(result.text, {
  authorName: "Ana Lima", // the From display name
  addresseeNames: ["Joe Example", "Priya Sharma"], // the To and Cc display names
  signatureLines: learned,
});
cleaned.text; // "The new section reads well. One change: keep the timer at 10 seconds."
cleaned.ignoredLines; // [0, 4, 5, 6, 7]
```

Without evidence, the greeting is removed and the sign-off is kept, because
the company line and the link below the name keep it from being the tail.
With `authorName`, "Ana" is recognized as the sender's name, and the lines
attached below it are removed with it; the learned lines would remove them
too.

`learnSignatureLines` compares lines in a normalized form: quote markers
removed, whitespace runs collapsed to one space, edges stripped, lowercased
(Python's `str.lower`, then `ß` written `ss`). A line is learned when it is
among the last 10 non-blank lines of at least 3 of the texts and of at least
one fifth of them, and, when `subjects` is given, when those texts have at
least 3 distinct subjects (compared without leading "Re:"/"Fwd:"-style
prefixes and "[list]" tags). A line that ends in ":" or is a sentence of at
least 3 words ending in ".", "?" or "!" is learned only when, in at least half
of its occurrences, the line above it is another candidate. Fewer than 5 texts
learn nothing. The result is sorted by code point.

### Parts of an inline reply

An inline reply interleaves the author's answers with quoted passages. Stage 1
removes the quoted passages and joins the answers into one text;
`ExtractionResult.partStarts` records where each separate run of the author's
text begins, as indices into `result.text.split("\n")`. Passing it to
`cleanForScoring` divides the cleaned text at the same boundaries:

```ts
const result = extractNewText(body);
const cleaned = cleanForScoring(result.text, { partStarts: result.partStarts });
cleaned.text; // the whole cleaned text, unchanged by partStarts
cleaned.parts; // one string per run of the author's text
```

A top-posted reply, or any text written as one run, has an empty `partStarts`
and a single part. The boundaries are found by aligning the text with the body
it was derived from; when a line cannot be aligned, the text is reported as
one part. Indices outside `1` to the input's line count minus one are ignored;
`partStarts` changes only `parts`, never `text` or `ignoredLines`. `parts` is
`[text]` without `partStarts`, `[]` when `text` is empty, and omits a part
that cleaning emptied.

## API reference

| Name                                                                                                                 | What it is                                                                                                                                                                                                                          |
| -------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `extractNewText(body, { parentBody?, htmlBody? })`                                                                   | Stage 1; returns an `ExtractionResult`. `body` may be `null` or `undefined`.                                                                                                                                                        |
| `ExtractionResult`                                                                                                   | `{ text, method, status, partStarts }`.                                                                                                                                                                                             |
| `STATUS_OK`, `STATUS_EMPTY`, `STATUS_FAILED`                                                                         | The three `status` values; the `ExtractionStatus` type is their union.                                                                                                                                                              |
| `EXTRACTION_VERSION`                                                                                                 | Integer generation stamp of the text-deriving routine.                                                                                                                                                                              |
| `cleanForScoring(extractedText, { htmlSignatureText?, partStarts?, authorName?, addresseeNames?, signatureLines? })` | Stage 2; returns a `CleanResult`. `authorName` is the sender's display name, `addresseeNames` the addressees' display names, `signatureLines` the sender's learned signature lines.                                                 |
| `learnSignatureLines(texts, { subjects? })`                                                                          | The lines one sender repeats at the end of their messages, learned from the stage-1 `texts` of that sender's messages (and their `subjects`); an array of normalized lines, sorted by code point. Fewer than 5 texts learn nothing. |
| `CleanResult`                                                                                                        | `{ text, ignoredLines, parts }`.                                                                                                                                                                                                    |
| `htmlToText(html)`                                                                                                   | Visible text of an HTML part, block structure as newlines.                                                                                                                                                                          |
| `splitHtmlParts(html)`                                                                                               | The same text split into `HtmlParts`.                                                                                                                                                                                               |
| `HtmlParts`                                                                                                          | `{ novelText, quotedText, signatureText, hasShareContainer }`.                                                                                                                                                                      |
| `stripParentContent(text, parentBody, { continuation?, quotedText? })`                                               | Remove lines of `text` provably drawn from `parentBody`; `quotedText` is the message's marked quotation.                                                                                                                            |
| `tolerantLines(text)`                                                                                                | The non-blank lines of `text`, edge whitespace stripped and U+00A0 unified to spaces.                                                                                                                                               |
| `textsEquivalent(a, b)`                                                                                              | `true` when `a` and `b` differ at most in whitespace.                                                                                                                                                                               |
| `VERSION`                                                                                                            | The package version.                                                                                                                                                                                                                |

The result types are frozen plain objects. Their fields are the Python
package's fields in camelCase: `ignored_lines` is `ignoredLines`,
`part_starts` is `partStarts`, `novel_text` is `novelText`. Array fields
(`partStarts`, `ignoredLines`, `parts`) are frozen arrays.

### Argument convention

Required arguments are positional. Every optional argument of the Python API
(`parent_body`, `html_body`, `html_signature_text`, `part_starts`,
`author_name`, `addressee_names`, `signature_lines`, `subjects`,
`continuation`, `quoted_text`) is a
property of an options object passed last, named in camelCase. An omitted
property, `undefined` and `null` all mean the Python default (`None`, `False`
or, for `partStarts`, no boundaries).

| Python                                                  | TypeScript                                                 |
| ------------------------------------------------------- | ---------------------------------------------------------- |
| `extract_new_text(body, parent_body=p, html_body=h)`    | `extractNewText(body, { parentBody: p, htmlBody: h })`     |
| `clean_for_scoring(text, html_signature_text=s)`        | `cleanForScoring(text, { htmlSignatureText: s })`          |
| `clean_for_scoring(text, part_starts=r.part_starts)`    | `cleanForScoring(text, { partStarts: r.partStarts })`      |
| `clean_for_scoring(text, author_name=a)`                | `cleanForScoring(text, { authorName: a })`                 |
| `learn_signature_lines(texts, subjects=s)`              | `learnSignatureLines(texts, { subjects: s })`              |
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
- `custom` — the cleanup pass ran on the body.
- `failed` — extraction raised internally.

Three modifiers combine with the base value:

- `html-` prefix — the text was derived from the HTML part, because there
  was no usable plain body, because a flattened plain body was replaced, or
  because the HTML holds an Apple Mail Share-sheet container (for example
  `html-custom`).
- `+parent-diff` suffix — the parent-diff assist removed content (for example
  `custom+parent-diff`).
- `+html-quote` suffix — the HTML quoted-text oracle removed content from a
  plain extraction.

## Extraction version

`EXTRACTION_VERSION` is an integer identifying the generation of the routine
that derives the text. It is independent of the package version, only ever
increases, and is the same in the Python, TypeScript and Rust packages. It is
currently **10**.

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
whitespace set, Unicode `\w`, `\d` and `\b`, `re.IGNORECASE` matching
and the `html.parser` tokenizer) with its own code and tables generated from
Python, so the output does not depend on the Unicode version of the JavaScript
engine. The parent-diff assist's index of the parent's word stream compares
UTF-16 code units and rejects a match that starts or ends inside a surrogate
pair, which gives the same substring answers as the reference's code points.

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
- [docs/generation-10.md](https://github.com/JayDaley/email-reply-extractor/blob/main/docs/generation-10.md)
  — generation 10: new stage-1 rules and the evidence arguments of stage 2.
- [CHANGELOG.md](https://github.com/JayDaley/email-reply-extractor/blob/main/CHANGELOG.md)
  — one section per release of all three packages.

## License

MIT. See
[LICENSE](https://github.com/JayDaley/email-reply-extractor/blob/main/LICENSE).
