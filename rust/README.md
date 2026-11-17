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
# assert_eq!(result.method, "custom");
# assert_eq!(cleaned.text, "Thanks for the quick turnaround. Replies inline.\n\n30 seconds is too long for the mobile case. I would prefer 10, with\nthe option to negotiate a longer value.\n\nIt should say so explicitly. I can send text for that.\n\nYes.");
# assert_eq!(cleaned.ignored_lines, vec![0, 14, 15, 17, 18, 19, 21, 22, 23]);
```

The output:

```text
Thanks for the quick turnaround. Replies inline.

30 seconds is too long for the mobile case. I would prefer 10, with
the option to negotiate a longer value.

It should say so explicitly. I can send text for that.

Yes.
```

Stage 1 (`result.text`, method `custom`) removes the attribution line and both
levels of quoted text, and keeps each inline response along with the
greeting, sign-off, signature and list footer. Stage 2 removes those and
reports the removed line indices in `cleaned.ignored_lines`
(`[0, 14, 15, 17, 18, 19, 21, 22, 23]`).

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

Stage 2, `clean_for_scoring`, removes that formulaic furniture from stage-1
text: greetings, sign-offs, signature blocks, mailing-list footers, mobile
taglines ("Sent from my iPhone") and trailing legal disclaimers. Given the
sender's display name, the addressees' display names and the sender's
learned signature lines, it also removes sign-offs, name lines, greetings and
signature lines that no fixed list identifies. It reports which non-blank
input lines it removed.

The stage-1 text is the author's full novel content, for storage and display;
the stage-2 text is a strict, documented subset of it, for a downstream
consumer such as a classifier. Because stage 2 reports the lines it dropped, a
stage-1 view can mark exactly what was set aside.

No function returns an error. `extract_new_text` reports an internal failure
as `Status::Failed` with empty text, as the reference does. No function
panics.

The rules of both stages are documented in
[docs/extraction.md](https://github.com/JayDaley/email-reply-extractor/blob/main/docs/extraction.md).

## Limitations

The rules are heuristics over text, and their known limits are the same in
every port: text the author repeats from the thread parent, the evidence the
caller must supply, the languages and mail clients recognized, and the scope
of the measurements. They are listed in the
[Limitations section of the repository README](https://github.com/JayDaley/email-reply-extractor/blob/main/README.md#limitations).

## Usage

### Arguments

The reference's optional arguments (Python default `None`) are `Option<&str>`
parameters, in the reference's order. Its keyword-only `continuation` flag of
`strip_parent_content` is a `bool` (the reference's default is `false`); its
keyword-only `quoted_text` argument is taken by a separate function,
`strip_parent_content_with_quotation`, and `strip_parent_content` is that
function with no quotation.

`clean_for_scoring_with_options` takes every optional argument of the
reference's `clean_for_scoring` in one `CleanOptions` struct:
`html_signature_text` (`Option<&str>`), `part_starts` (`&[usize]`),
`author_name` (`Option<&str>`), `addressee_names` and `signature_lines`
(`&[&str]`). `CleanOptions::default()` passes none of them. Two shorter forms
remain: `clean_for_scoring(extracted_text, html_signature_text)` and
`clean_for_scoring_with_parts(extracted_text, html_signature_text,
part_starts)`. An empty slice is the reference's `None` for
`addressee_names` and `signature_lines`.

`learn_signature_lines(texts, subjects)` takes the reference's keyword-only
`subjects` as an `Option<&[&str]>`.

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
assert_eq!(result.method, "custom");
assert_eq!(
    result.text,
    "Hi Dan,\n\nTwo comments below.\n\n\nAgreed, and the draft already says so.\n\nBest,\nLucas"
);

let cleaned = clean_for_scoring(&result.text, None);
assert_eq!(cleaned.text, "Two comments below.\n\nAgreed, and the draft already says so.");
assert_eq!(cleaned.ignored_lines, vec![0, 7, 8]); // the greeting and the two sign-off lines
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
// plain part or for the plain part of an Apple Mail Share-sheet message, or to
// remove a quoted message leaked unmarked into the plain part.
let result = extract_new_text(Some(body), None, Some(html_body));

// The HTML signature container's visible text, as a stage-2 hint.
let parts = split_html_parts(html_body);
let cleaned = clean_for_scoring(&result.text, Some(&parts.signature_text));
# assert_eq!(cleaned.text, "Agreed.");
```

### Sender and addressee evidence

Three fields of `CleanOptions` carry evidence about the message:
`author_name`, the sender's display name; `addressee_names`, the display names
of the people the message answers or is addressed to (for example the thread
parent's sender and the To and Cc display names); and `signature_lines`, the
lines the sender repeats at the end of their messages, learned by
`learn_signature_lines` from the stage-1 texts of the sender's earlier
messages. Without them only the rules that need no evidence run.

```rust
use email_reply_extractor::{
    clean_for_scoring, clean_for_scoring_with_options, extract_new_text,
    learn_signature_lines, CleanOptions,
};

// (subject, plain body) of the sender's earlier messages, stored by the caller.
let earlier: Vec<(String, String)> = (0..6)
    .map(|i| {
        (
            format!("Draft comments, round {i}"),
            format!(
                "Comment {i} on the draft.\n\nThanks,\nAna\nExample Networks\nhttps://example.net/ana"
            ),
        )
    })
    .collect();
let texts: Vec<String> = earlier
    .iter()
    .map(|(_, body)| extract_new_text(Some(body), None, None).text)
    .collect();
let texts: Vec<&str> = texts.iter().map(String::as_str).collect();
let subjects: Vec<&str> = earlier.iter().map(|(subject, _)| subject.as_str()).collect();
let learned = learn_signature_lines(&texts, Some(&subjects));
assert_eq!(learned, ["ana", "example networks", "https://example.net/ana", "thanks,"]);

let body = "Joe, Priya,

The new section reads well. One change: keep the timer at 10 seconds.

Thanks,
Ana
Example Networks
https://example.net/ana
";
let result = extract_new_text(Some(body), None, None);
assert_eq!(
    clean_for_scoring(&result.text, None).text,
    "The new section reads well. One change: keep the timer at 10 seconds.\n\n\
     Thanks,\nAna\nExample Networks\nhttps://example.net/ana"
);

let signature_lines: Vec<&str> = learned.iter().map(String::as_str).collect();
let options = CleanOptions {
    author_name: Some("Ana Lima"),                     // the From display name
    addressee_names: &["Joe Example", "Priya Sharma"], // the To and Cc display names
    signature_lines: &signature_lines,
    ..CleanOptions::default()
};
let cleaned = clean_for_scoring_with_options(&result.text, &options);
assert_eq!(
    cleaned.text,
    "The new section reads well. One change: keep the timer at 10 seconds."
);
assert_eq!(cleaned.ignored_lines, vec![0, 4, 5, 6, 7]);
```

Without evidence, the greeting is removed and the sign-off is kept, because
the company line and the link below the name keep it from being the tail.
With `author_name`, "Ana" is recognized as the sender's name, and the lines
attached below it are removed with it; the learned lines would remove them
too.

### Parts of an inline reply

An inline reply interleaves the author's answers with quoted passages. Stage 1
removes the quoted passages and joins the answers into one text;
`ExtractionResult::part_starts` records where each separate run of the
author's text begins, as indices into `result.text.split('\n')`. Passing it to
`clean_for_scoring_with_parts` divides the cleaned text at the same
boundaries:

```rust
use email_reply_extractor::{clean_for_scoring_with_parts, extract_new_text};

let body = "Yes, the draft covers it.

> And the registry?

No, that is left to a later document.

Thanks,
Ann
";

let result = extract_new_text(Some(body), None, None);
assert_eq!(result.part_starts, vec![3]);

let cleaned = clean_for_scoring_with_parts(&result.text, None, &result.part_starts);
// The whole cleaned text, unchanged by the part boundaries.
assert_eq!(cleaned.text, "Yes, the draft covers it.\n\nNo, that is left to a later document.");
// One string per run of the author's text.
assert_eq!(
    cleaned.parts,
    vec!["Yes, the draft covers it.", "No, that is left to a later document."]
);
```

A top-posted reply, or any text written as one run, has an empty
`part_starts` and a single part. The boundaries are found by aligning the
text with the body it was derived from; when a line cannot be aligned, the
text is reported as one part.

## API reference

| Name | What it is |
|---|---|
| `extract_new_text(body, parent_body, html_body)` | Stage 1; `Option<&str>` arguments; returns an `ExtractionResult`. |
| `ExtractionResult` | Struct: `text: String`, `method: String`, `status: Status`, `part_starts: Vec<usize>`. |
| `Status` | Enum `Ok`, `Empty`, `Failed`; `as_str()` and `Display` give `"ok"`, `"empty"`, `"failed"`. |
| `STATUS_OK`, `STATUS_EMPTY`, `STATUS_FAILED` | The three status strings. |
| `EXTRACTION_VERSION` | `u32` generation stamp of the text-deriving routine. |
| `clean_for_scoring(extracted_text, html_signature_text)` | Stage 2; `&str`, `Option<&str>`; returns a `CleanResult`. |
| `clean_for_scoring_with_parts(extracted_text, html_signature_text, part_starts)` | Stage 2 with the stage-1 part boundaries; `&str`, `Option<&str>`, `&[usize]`; fills `CleanResult::parts` from them. |
| `clean_for_scoring_with_options(extracted_text, options)` | Stage 2 with every optional argument; `&str`, `&CleanOptions`. |
| `CleanOptions` | Struct: `html_signature_text: Option<&str>`, `part_starts: &[usize]`, `author_name: Option<&str>`, `addressee_names: &[&str]`, `signature_lines: &[&str]`; `Default` passes none of them. |
| `learn_signature_lines(texts, subjects)` | The lines one sender repeats at the end of their messages, learned from the stage-1 `texts` (`&[&str]`) of that sender's messages and their `subjects` (`Option<&[&str]>`); a `Vec<String>` of normalized lines sorted by code point. Fewer than 5 texts learn nothing. |
| `CleanResult` | Struct: `text: String`, `ignored_lines: Vec<usize>`, `parts: Vec<String>`. |
| `html_to_text(html)` | Visible text of an HTML part, block structure as newlines. |
| `split_html_parts(html)` | The same text split into `HtmlParts { novel_text, quoted_text, signature_text, has_share_container }`. |
| `HtmlParts` | Struct returned by `split_html_parts`. |
| `strip_parent_content(text, parent_body, continuation)` | Remove lines of `text` provably drawn from `parent_body`. |
| `strip_parent_content_with_quotation(text, parent_body, continuation, quoted_text)` | The same, with the message's marked quotation (`Option<&str>`): a removed run that the quotation contains is kept when its block is mostly unmatched text. |
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
- `custom`: the cleanup pass ran on the body.
- `failed`: extraction failed internally.

Three modifiers combine with the base value:

- `html-` prefix: the text was derived from the HTML part, because there was
  no usable plain body, because a flattened plain body was replaced, or
  because the HTML holds an Apple Mail Share-sheet container (for example
  `html-custom`).
- `+parent-diff` suffix: the parent-diff assist removed content (for example
  `custom+parent-diff`).
- `+html-quote` suffix: the HTML quoted-text oracle removed content from a
  plain extraction.

## Extraction version

`EXTRACTION_VERSION` is a `u32` identifying the generation of the routine that
derives the text. It is independent of the package version, only ever
increases, and is incremented whenever a change can alter the derived text. It
is currently **10**.

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
`\w`, `\d` and case-insensitive matching sets, Python's `str.lower` and
`str.upper`, the reference's name folding table, and the
`html.parser.HTMLParser` tokenizer with `html.unescape` and the HTML5
named character references. The parent-diff assist's index of the parent's
word stream works in bytes, which gives the same substring answers as the
reference's code points. The Unicode tables are generated from the Python version that records the conformance
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

The repository README describes the project's history.

## License

MIT. See [LICENSE](https://github.com/JayDaley/email-reply-extractor/blob/main/LICENSE).
