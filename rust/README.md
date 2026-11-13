# email-reply-extractor

[![crates.io](https://img.shields.io/crates/v/email-reply-extractor)](https://crates.io/crates/email-reply-extractor)
[![docs.rs](https://img.shields.io/docsrs/email-reply-extractor)](https://docs.rs/email-reply-extractor)
[![License: MIT](https://img.shields.io/crates/l/email-reply-extractor)](https://github.com/JayDaley/email-reply-extractor/blob/main/LICENSE)
[![CI](https://github.com/JayDaley/email-reply-extractor/actions/workflows/ci.yml/badge.svg)](https://github.com/JayDaley/email-reply-extractor/actions/workflows/ci.yml)

**Extract the new text from an email reply in Rust: strip quoted text, quote
headers, signatures and sign-offs.**

`email-reply-extractor` removes the quoted reply (`>` lines, "On … wrote:"
attribution lines, Outlook quote-header blocks, "Original message" dividers)
from top-posted, bottom-posted and inline replies, then optionally removes
greetings, signatures, sign-offs, mailing-list footers and legal disclaimers.

This crate is the Rust port of the Python reference implementation
([`email-reply-extractor` on PyPI](https://pypi.org/project/email-reply-extractor/)).
It shares the reference's version number and `EXTRACTION_VERSION`, and its
test suite replays the reference's conformance data, so it derives
byte-identical text from the same input.

A reply with threaded inline responses, a signature and a mailing-list footer:

```text
Hi Dan,

Thanks for the quick turnaround. Replies inline.

On Tue, Jul 7, 2026 at 3:25 AM Dan Wing <dan@example.com> wrote:
> On Mon, Jul 6, 2026 at 9:10 PM Maria Lopez <maria@example.org> wrote:
>> Section 3 still needs a timeout.
>
> I have added one in section 3.2, with a default of 30 seconds.

30 seconds is too long for the mobile case. I would prefer 10, with
the option to negotiate a longer value.

>> Should the retry counter reset after a successful exchange?
>
> I think so, but the draft does not say.

It should say so explicitly. I can send text for that.

> Are you happy with the new title?

Yes.

Thanks,
Maria

--
Maria Lopez | Network Engineer
+1 555 0100 | example.com

_______________________________________________
wg mailing list -- wg@example.org
To unsubscribe send an email to wg-leave@example.org
```

The code, with that message in `body`:

```rust
use email_reply_extractor::{clean_for_scoring, extract_new_text};
# let body = "Hi Dan,\n\nThanks for the quick turnaround. Replies inline.\n\nOn Tue, Jul 7, 2026 at 3:25 AM Dan Wing <dan@example.com> wrote:\n> On Mon, Jul 6, 2026 at 9:10 PM Maria Lopez <maria@example.org> wrote:\n>> Section 3 still needs a timeout.\n>\n> I have added one in section 3.2, with a default of 30 seconds.\n\n30 seconds is too long for the mobile case. I would prefer 10, with\nthe option to negotiate a longer value.\n\n>> Should the retry counter reset after a successful exchange?\n>\n> I think so, but the draft does not say.\n\nIt should say so explicitly. I can send text for that.\n\n> Are you happy with the new title?\n\nYes.\n\nThanks,\nMaria\n\n--\nMaria Lopez | Network Engineer\n+1 555 0100 | example.com\n\n_______________________________________________\nwg mailing list -- wg@example.org\nTo unsubscribe send an email to wg-leave@example.org\n";

let result = extract_new_text(Some(body), None, None); // stage 1: quoted thread removed
let cleaned = clean_for_scoring(&result.text, None); // stage 2: furniture removed
println!("{}", cleaned.text);
# assert_eq!(result.method, "erp");
# assert_eq!(cleaned.text, "Thanks for the quick turnaround. Replies inline.\n\n30 seconds is too long for the mobile case. I would prefer 10, with\nthe option to negotiate a longer value.\n\nIt should say so explicitly. I can send text for that.\n\nYes.");
# assert_eq!(cleaned.ignored_lines, vec![0, 11, 12, 14, 15, 16, 18, 19, 20]);
```

The output:

```text
Thanks for the quick turnaround. Replies inline.

30 seconds is too long for the mobile case. I would prefer 10, with
the option to negotiate a longer value.

It should say so explicitly. I can send text for that.

Yes.
```

Stage 1 (`result.text`, method `erp`) removes the attribution line and both
levels of quoted text, and keeps each inline response along with the
greeting, sign-off, signature and list footer. Stage 2 removes those and
reports the removed line indices in `cleaned.ignored_lines`
(`[0, 11, 12, 14, 15, 16, 18, 19, 20]`).

## Install

```sh
cargo add email-reply-extractor
```

The crate requires Rust 1.80 or later. Its runtime dependencies are `regex`
and `fancy-regex` (for the patterns that use lookaround). It contains no
`unsafe` code and does no I/O or MIME parsing: the functions take decoded
strings.

## What it does

Stage 1, `extract_new_text`, removes everything the author did not write in
this message:

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

Stage 2, `clean_for_scoring`, removes that formulaic furniture from stage-1
text: greetings, sign-offs, signature blocks, mailing-list footers, mobile
taglines ("Sent from my iPhone") and trailing legal disclaimers. It also
reports which non-blank input lines it removed.

The stage-1 text is the author's full novel content, for storage and display;
the stage-2 text is a strict, documented subset of it, for a downstream
consumer such as a classifier. Because stage 2 reports the lines it dropped, a
stage-1 view can mark exactly what was set aside.

No function panics or returns an error. `extract_new_text` reports an
internal failure as `Status::Failed` with empty text, as the reference does.

The rules of both stages are documented in
[docs/extraction.md](https://github.com/JayDaley/email-reply-extractor/blob/main/docs/extraction.md).

## Usage

### Arguments

The reference's optional arguments (Python default `None`) are `Option<&str>`
parameters, in the reference's order. Its keyword-only `continuation` flag of
`strip_parent_content` is a `bool` (the reference's default is `false`).

### Quickstart

```rust
use email_reply_extractor::{clean_for_scoring, extract_new_text, Status};

let body = "Hi Dan,

Two comments below.

On Tue, Jul 7, 2026, at 03:25, Dan Wing wrote:
> I would lean heavily towards solution (2).

Agreed, and the draft already says so.

Best,
Lucas
";

let result = extract_new_text(Some(body), None, None);
assert_eq!(result.status, Status::Ok);
assert_eq!(result.method, "erp");
assert_eq!(
    result.text,
    "Hi Dan,\n\nTwo comments below.\n\nAgreed, and the draft already says so.\n\nBest,\nLucas"
);

let cleaned = clean_for_scoring(&result.text, None);
assert_eq!(cleaned.text, "Two comments below.\n\nAgreed, and the draft already says so.");
assert_eq!(cleaned.ignored_lines, vec![0, 6, 7]); // the greeting and the two sign-off lines
```

The quote and its attribution line are gone from `result.text`, and the reply
below the quote is kept; the greeting and the sign-off are still there, and
`clean_for_scoring` is what removes them.

### Thread parent and HTML part

Two optional arguments improve the result when the caller can supply them, and
change nothing when they are `None`:

```rust
use email_reply_extractor::{clean_for_scoring, extract_new_text, split_html_parts};
# let body = "Agreed.\n";
# let parent_body = "Shall we merge?\n";
# let html_body = "<p>Agreed.</p><div class=\"gmail_signature\">Ann Lee | Example Org</div>";

// The thread parent's raw body, resolved by the caller from In-Reply-To.
// Removes a quoted previous message that carries no '>' markers at all.
let result = extract_new_text(Some(body), Some(parent_body), None);

// The message's decoded text/html part. Used as a structural oracle: as the
// body when there is no usable plain part, as a replacement for a flattened
// plain part, or to remove a quoted message leaked unmarked into the plain part.
let result = extract_new_text(Some(body), None, Some(html_body));

// The HTML signature container's visible text, as a stage-2 hint.
let parts = split_html_parts(html_body);
let cleaned = clean_for_scoring(&result.text, Some(&parts.signature_text));
# assert_eq!(cleaned.text, "Agreed.");
```

## API reference

| Name | What it is |
|---|---|
| `extract_new_text(body, parent_body, html_body)` | Stage 1; `Option<&str>` arguments; returns an `ExtractionResult`. |
| `ExtractionResult` | Struct: `text: String`, `method: String`, `status: Status`. |
| `Status` | Enum `Ok`, `Empty`, `Failed`; `as_str()` and `Display` give `"ok"`, `"empty"`, `"failed"`. |
| `STATUS_OK`, `STATUS_EMPTY`, `STATUS_FAILED` | The three status strings. |
| `EXTRACTION_VERSION` | `u32` generation stamp of the text-deriving routine. |
| `clean_for_scoring(extracted_text, html_signature_text)` | Stage 2; `&str`, `Option<&str>`; returns a `CleanResult`. |
| `CleanResult` | Struct: `text: String`, `ignored_lines: Vec<usize>`. |
| `html_to_text(html)` | Visible text of an HTML part, block structure as newlines. |
| `split_html_parts(html)` | The same text split into `HtmlParts { novel_text, quoted_text, signature_text }`. |
| `HtmlParts` | Struct returned by `split_html_parts`. |
| `strip_parent_content(text, parent_body, continuation)` | Remove lines of `text` provably drawn from `parent_body`. |
| `tolerant_lines(text)` | The non-blank lines of `text`, edge whitespace stripped and U+00A0 unified to spaces. |
| `texts_equivalent(a, b)` | `true` when `a` and `b` differ at most in whitespace. |
| `VERSION` | The package version. |

### Status values

- `ok`: text was extracted.
- `empty`: nothing was left: a blank body, an HTML-only message with no usable
  HTML text, or a body that reduced to quotes alone.
- `failed`: extraction failed internally; `text` is empty.

### Method strings

`ExtractionResult::method` records which path produced the text. The base
value is one of:

- `none`: no body to work from.
- `erp`: the vendored fragment scan needed no cleanup.
- `erp+custom`: the fragment scan plus the custom cleanup pass.
- `custom-fallback`: the over-strip guard fired, and the custom pass ran on
  the whole body.
- `failed`: extraction failed internally.

Three modifiers combine with the base value:

- `html-` prefix: the text was derived from the HTML part (for example
  `html-erp+custom`).
- `+parent-diff` suffix: the parent-diff assist removed content.
- `+html-quote` suffix: the HTML quoted-text oracle removed content from a
  plain extraction.

## Extraction version

`EXTRACTION_VERSION` is a `u32` identifying the generation of the routine that
derives the text. It is independent of the package version, only ever
increases, and is incremented whenever a change can alter the derived text. It
is currently **6**.

An application that stores derived text should store `EXTRACTION_VERSION`
alongside it and compare the stored stamp with the running constant using `<`
to decide whether the text needs re-deriving. The full contract is in
[docs/versioning.md](https://github.com/JayDaley/email-reply-extractor/blob/main/docs/versioning.md).
Because a generation bump can move bytes without changing substance,
`tolerant_lines` and `texts_equivalent` let a consumer keep downstream results
when only whitespace moved.

## Fidelity to the reference

The port reproduces the Python standard-library behavior the reference relies
on: code point (not byte) lengths and indices, Python's whitespace,
`\w`, `\d` and case-insensitive matching sets, `re.sub` replacement-template
rules, `difflib.SequenceMatcher`, and the `html.parser.HTMLParser` tokenizer
with `html.unescape` and the HTML5 named character references. The Unicode
tables are generated from the Python version that records the conformance
data (`tools/gen_tables.py`). The hazards are listed in
[docs/ports.md](https://github.com/JayDaley/email-reply-extractor/blob/main/docs/ports.md).

The crate's tests read the shared conformance data from the repository
checkout (`../conformance`), so they run from the repository, not from the
published package.

## Documentation

- [docs/extraction.md](https://github.com/JayDaley/email-reply-extractor/blob/main/docs/extraction.md)
  — the rules of both stages.
- [docs/versioning.md](https://github.com/JayDaley/email-reply-extractor/blob/main/docs/versioning.md)
  — the `EXTRACTION_VERSION` contract.
- [docs/ports.md](https://github.com/JayDaley/email-reply-extractor/blob/main/docs/ports.md)
  — the contract between the Python, TypeScript and Rust ports.
- [conformance/README.md](https://github.com/JayDaley/email-reply-extractor/blob/main/conformance/README.md)
  — the language-neutral conformance data and the corpus digest.
- [CHANGELOG.md](https://github.com/JayDaley/email-reply-extractor/blob/main/CHANGELOG.md)
  — one section per release.

## Provenance

The fragment scanner is ported from the reference's vendored copy of
`email-reply-parser` 0.5.12 (MIT, Copyright (c) 2012 Zapier LLC). The
repository README describes the project's history.

## License

MIT. See [LICENSE](https://github.com/JayDaley/email-reply-extractor/blob/main/LICENSE).
