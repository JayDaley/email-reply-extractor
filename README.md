# email-reply-extractor

[![PyPI version](https://img.shields.io/pypi/v/email-reply-extractor)](https://pypi.org/project/email-reply-extractor/)
[![Python versions](https://img.shields.io/pypi/pyversions/email-reply-extractor)](https://pypi.org/project/email-reply-extractor/)
[![License: MIT](https://img.shields.io/pypi/l/email-reply-extractor)](https://github.com/JayDaley/email-reply-extractor/blob/main/LICENSE)
[![CI](https://github.com/JayDaley/email-reply-extractor/actions/workflows/ci.yml/badge.svg)](https://github.com/JayDaley/email-reply-extractor/actions/workflows/ci.yml)

**Extract the new text from an email reply in Python: strip quoted text, quote
headers, signatures and sign-offs.**

`email-reply-extractor` is a Python email reply parser for mailing-list and
other email. It removes the quoted reply (`>` lines, Gmail-style "On … wrote:"
attribution lines, Outlook quote-header blocks, "Original message" dividers) from top-posted, bottom-posted and inline replies, then optionally
removes greetings, signatures, sign-offs, mailing-list footers and legal
disclaimers. It is an alternative to
[talon](https://github.com/mailgun/talon) and
[email-reply-parser](https://github.com/zapier/email-reply-parser): pure
standard library, no runtime dependencies, Python 3.10 or later, with output
pinned by a versioned test corpus of real mailing-list messages.

```python
from email_reply_extractor import clean_for_scoring, extract_new_text

body = """Hi Dan,

The revised text in section 3 works for me. One question: does the
timeout apply per hop or end to end?

Thanks,
Maria

--
Maria Lopez | Network Engineer
+1 555 0100 | example.com

Sent from my iPhone

From: Dan Wing <dan@example.com>
Sent: Tuesday, July 7, 2026 3:25 AM
To: wg@example.org
Subject: Re: Timeout handling

I have updated section 3 as discussed.

> On Mon, Jul 6, 2026, Maria Lopez wrote:
> Section 3 still needs a timeout.
"""

result = extract_new_text(body)           # stage 1: quoted thread removed
cleaned = clean_for_scoring(result.text)  # stage 2: furniture removed
print(cleaned.text)
```

Output:

```
The revised text in section 3 works for me. One question: does the
timeout apply per hop or end to end?
```

Stage 1 (`result.text`, method `erp+custom`) drops the Outlook quote-header
block and everything below it, and keeps the greeting, sign-off, signature and
mobile tagline. Stage 2 removes those and reports the removed line indices in
`cleaned.ignored_lines` (`[0, 5, 6, 8, 9, 10, 12]`).

## Contents

- [Comparison with alternatives](#comparison-with-alternatives)
- [Install](#install)
- [What it does](#what-it-does)
- [Usage](#usage)
  - [Quickstart](#quickstart)
  - [Thread parent and HTML part](#thread-parent-and-html-part)
- [API reference](#api-reference)
  - [Status values](#status-values)
  - [Method strings](#method-strings)
- [Extraction version](#extraction-version)
- [Supported Python versions](#supported-python-versions)
- [Documentation](#documentation)
- [Provenance](#provenance)
- [License](#license)

## Comparison with alternatives

The two most widely used Python libraries for this task are
[email-reply-parser](https://github.com/zapier/email-reply-parser) (Zapier's
port of GitHub's Email Reply Parser) and
[talon](https://github.com/mailgun/talon) (Mailgun). The table states each
project's documented behavior; release data is from PyPI as of November 2026.

|  | email-reply-extractor | email-reply-parser | talon |
|---|---|---|---|
| Extracts | The author's full novel content (stage 1), then the substantive prose alone (stage 2) | The latest reply, quoted text and signatures hidden | The reply with quotations removed; signatures separately |
| Signature handling | Kept in stage 1, removed and reported line-by-line in stage 2 | Hidden along with the reply's quoted fragments; not reported | Regex ("brute force") extractor, plus an SVM machine-learning extractor |
| HTML part | Used as a structural oracle: as the body when no usable plain part exists, replacing a flattened plain part, or removing a quoted message leaked unmarked into the plain part | Not handled | Quotation extraction has an HTML mode (lxml-based) |
| Thread parent | Optional parent-diff assist removes a quoted previous message that carries no `>` markers | Not used | Not used |
| Quote headers | Outlook-style blocks, including French, Spanish, Portuguese, Italian, Dutch, German and Chinese label sets, folded and double-spaced renderings | `From:`/`Sent:`/`To:`/`Subject:` fragment detection, English only | Delimiter patterns (e.g. `-----Original Message-----`), several languages |
| Output stability | Behavior pinned by an integer generation (`EXTRACTION_VERSION`) and a corpus digest test; whitespace-tolerant equivalence helpers for consumers | Not versioned separately | Not versioned separately |
| Test corpus | Hand-labeled real mailing-list messages (`tests/fixtures`) | Unit-test fixture emails | Unit-test fixture emails; ML training data |
| Runtime dependencies | None (standard library) | None (standard library) | lxml, regex, numpy, scipy, scikit-learn, chardet, and others |
| Latest release | 1.2.0 (2026) | 0.5.12 (2020) | 1.4.4 (2017) |
| License | MIT | MIT | Apache 2.0 |

The fragment scanner at this library's core derives from email-reply-parser
(see [Provenance](#provenance)), so on a plain-text body with English quoting
the two produce related results; the differences above are what this library
adds on top of that scan.

## Install

```
pip install email-reply-extractor
```

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

It **keeps** the author's greetings, sign-offs and signature blocks, and
inline replies written between quoted blocks.

Stage 2, `clean_for_scoring`, removes that formulaic furniture from stage-1
text: greetings, sign-offs, signature blocks, mailing-list footers, mobile
taglines ("Sent from my iPhone") and trailing legal disclaimers. It also
reports which non-blank input lines it removed.

The split is deliberate. The stage-1 text is the author's full novel content,
which is what an application should store and display; the stage-2 text is a
strict, documented subset of it, which is what an application should feed to a
downstream consumer such as a classifier. Because stage 2 reports the lines it
dropped, a stage-1 view can mark exactly what was set aside.

Both stages are pure and I/O-free. `extract_new_text` never raises: an
unexpected error is reported as `status="failed"` so a single bad message
cannot stall a pipeline.

The rules of both stages are documented in
[docs/extraction.md](https://github.com/JayDaley/email-reply-extractor/blob/main/docs/extraction.md).

## Usage

### Quickstart

```python
from email_reply_extractor import clean_for_scoring, extract_new_text

body = """Hi Dan,

Two comments below.

On Tue, Jul 7, 2026, at 03:25, Dan Wing wrote:
> I would lean heavily towards solution (2).

Agreed, and the draft already says so.

Best,
Lucas
"""

result = extract_new_text(body)
result.status  # 'ok'
result.method  # 'erp'
result.text  # 'Hi Dan,\n\nTwo comments below.\n\nAgreed, and the draft already says so.\n\nBest,\nLucas'

cleaned = clean_for_scoring(result.text)
cleaned.text  # 'Two comments below.\n\nAgreed, and the draft already says so.'
cleaned.ignored_lines  # [0, 6, 7] — the greeting and the two sign-off lines
```

The quote and its attribution line are gone from `result.text`, and the reply
below the quote is kept; the greeting and the sign-off are still there, and
`clean_for_scoring` is what removes them.

### Thread parent and HTML part

Two optional arguments improve the result when the caller can supply them, and
change nothing when they are omitted:

```python
# The thread parent's raw body, resolved by the caller from In-Reply-To.
# Removes a quoted previous message that carries no '>' markers at all.
result = extract_new_text(body, parent_body=parent_body)

# The message's decoded text/html part. Used as a structural oracle: as the
# body when there is no usable plain part, as a replacement for a flattened
# plain part, or to remove a quoted message leaked unmarked into the plain part.
result = extract_new_text(body, html_body=html_body)
```

`clean_for_scoring` takes the HTML signature container's visible text as an
optional hint, which removes signature furniture that carries no `-- `
delimiter and no recognizable contact shape:

```python
from email_reply_extractor import split_html_parts

parts = split_html_parts(html_body)
cleaned = clean_for_scoring(result.text, html_signature_text=parts.signature_text)
```

## API reference

| Name | What it is |
|---|---|
| `extract_new_text(body, parent_body=None, html_body=None)` | Stage 1; returns an `ExtractionResult`. |
| `ExtractionResult` | Frozen dataclass: `text`, `method`, `status`. |
| `STATUS_OK`, `STATUS_EMPTY`, `STATUS_FAILED` | The three `status` values. |
| `EXTRACTION_VERSION` | `int` generation stamp of the text-deriving routine. |
| `clean_for_scoring(extracted_text, html_signature_text=None)` | Stage 2; returns a `CleanResult`. |
| `CleanResult` | Frozen dataclass: `text`, `ignored_lines`. |
| `html_to_text(html)` | Visible text of an HTML part, block structure as newlines. |
| `split_html_parts(html)` | The same text split into `HtmlParts(novel_text, quoted_text, signature_text)`. |
| `HtmlParts` | Frozen dataclass returned by `split_html_parts`. |
| `strip_parent_content(text, parent_body, *, continuation=False)` | Remove lines of `text` provably drawn from `parent_body`. |
| `tolerant_lines(text)` | The non-blank lines of `text`, edge whitespace stripped and U+00A0 unified to spaces. |
| `texts_equivalent(a, b)` | `True` when `a` and `b` differ at most in whitespace. |
| `__version__` | The package version. |

### Status values

- `ok` — text was extracted.
- `empty` — nothing was left: a blank body, an HTML-only message with no usable
  HTML text, or a body that reduced to quotes alone.
- `failed` — extraction raised; `text` is empty.

### Method strings

`ExtractionResult.method` records which path produced the text, so a result is
auditable after the fact. The base value is one of:

- `none` — no body to work from.
- `erp` — the vendored fragment scan needed no cleanup.
- `erp+custom` — the fragment scan plus the custom cleanup pass.
- `custom-fallback` — the over-strip guard fired, and the custom pass ran on
  the whole body.
- `failed` — extraction raised.

Three modifiers combine with the base value:

- `html-` prefix — the text was derived from the HTML part, either because
  there was no usable plain body or because a flattened plain body was
  replaced (for example `html-erp+custom`).
- `+parent-diff` suffix — the parent-diff assist removed content (for example
  `erp+parent-diff`).
- `+html-quote` suffix — the HTML quoted-text oracle removed content from a
  plain extraction.

## Extraction version

`EXTRACTION_VERSION` is an `int` identifying the generation of the routine that
derives the text. It is independent of the package version, only ever
increases, and is incremented whenever a change to the extraction, cleaning,
HTML or vendored-scanner modules can alter the derived text. It is currently
**6**.

An application that stores derived text should store `EXTRACTION_VERSION`
alongside it and compare the stored stamp with the running constant using `<`
to decide whether the text needs re-deriving. The full contract, including the
digest that pins the behavior over the fixture corpus, is in
[docs/versioning.md](https://github.com/JayDaley/email-reply-extractor/blob/main/docs/versioning.md).

Generation 5 (shipped in v1.1.0) and the work deferred from it are described in
[docs/generation-5.md](https://github.com/JayDaley/email-reply-extractor/blob/main/docs/generation-5.md).
Generation 6 (v1.2.0) adds the Latin-script localized quote-header blocks and
dividers described in
[docs/extraction.md](https://github.com/JayDaley/email-reply-extractor/blob/main/docs/extraction.md).

Because a generation bump can move bytes without changing substance, the
package exports `tolerant_lines` and `texts_equivalent` so a consumer can
re-derive stored text and keep expensive downstream results (for example a paid
classifier score) when only whitespace moved.

## Supported Python versions

Python 3.10 through 3.14; each is exercised in CI.

## Documentation

- [docs/extraction.md](https://github.com/JayDaley/email-reply-extractor/blob/main/docs/extraction.md)
  — the rules of both stages.
- [docs/versioning.md](https://github.com/JayDaley/email-reply-extractor/blob/main/docs/versioning.md)
  — the `EXTRACTION_VERSION` contract.
- [docs/generation-5.md](https://github.com/JayDaley/email-reply-extractor/blob/main/docs/generation-5.md)
  — generation 5 and the deferred scanner changes.
- [CONTRIBUTING.md](https://github.com/JayDaley/email-reply-extractor/blob/main/CONTRIBUTING.md)
  — development setup and contributor contracts.
- [CHANGELOG.md](https://github.com/JayDaley/email-reply-extractor/blob/main/CHANGELOG.md)
  — one section per release.

## Provenance

The code was extracted from
[mailing-list-ai-check](https://github.com/JayDaley/mailing-list-ai-check), an
application that checks mailing-list mail with an AI detector, where it shipped
as four extraction generations: 1 (initial release), 2 (from that
application's v1.2.0), 3 (from v1.11.0) and 4 (from v1.15.0). The hand-labeled
fixture corpus (26 real messages from the public IETF mail archive) and its
provenance came with it; see
[tests/fixtures/README.md](https://github.com/JayDaley/email-reply-extractor/blob/main/tests/fixtures/README.md).

The extraction also absorbed the application's only runtime dependency,
`email-reply-parser` 0.5.12 (MIT, Zapier's Python port of GitHub's Email Reply
Parser). Its fragment scanner is vendored, trimmed to the scan path this
package consumes, as `src/email_reply_extractor/_fragments.py`. Two upstream
defects were preserved through generation 4 so the output did not move, and
fixed in generation 5 (see
[docs/generation-5.md](https://github.com/JayDaley/email-reply-extractor/blob/main/docs/generation-5.md)).
The package therefore has no runtime dependencies.

Two pieces of evidence support the claim that v1.0.0 derives the same text as
the application it came from: the recorded corpus digest carried over
unchanged, and a differential run over 110,725 real stored messages compared
the old (`email_reply_parser`) and new (vendored) implementations on stage-1
text, method and status and on the stage-2 composite, with zero mismatches.

## License

MIT. See
[LICENSE](https://github.com/JayDaley/email-reply-extractor/blob/main/LICENSE).
