# email-reply-extractor

[![PyPI version](https://img.shields.io/pypi/v/email-reply-extractor)](https://pypi.org/project/email-reply-extractor/)
[![Python versions](https://img.shields.io/pypi/pyversions/email-reply-extractor)](https://pypi.org/project/email-reply-extractor/)
[![License: MIT](https://img.shields.io/pypi/l/email-reply-extractor)](https://github.com/JayDaley/email-reply-extractor/blob/main/LICENSE)
[![CI](https://github.com/JayDaley/email-reply-extractor/actions/workflows/ci.yml/badge.svg)](https://github.com/JayDaley/email-reply-extractor/actions/workflows/ci.yml)

**Extract the new text from an email reply in Python: strip quoted text, quote
headers, signatures and sign-offs.**

`email-reply-extractor` is a Python email reply parser for mailing-list and
other email. It removes the quoted reply (`>` lines, Gmail-style "On … wrote:"
attribution lines, Outlook quote-header blocks, "Original message" dividers)
from top-posted, bottom-posted and inline replies, then optionally removes
greetings, signatures, sign-offs, mailing-list footers and legal disclaimers,
using the sender's and addressees' display names and the sender's repeated
signature lines as evidence when the caller has them.
It is an alternative to
[talon](https://github.com/mailgun/talon) and
[email-reply-parser](https://github.com/zapier/email-reply-parser): pure
standard library, no runtime dependencies, Python 3.10 or later, with output
pinned by a versioned test corpus of real mailing-list messages.

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

```python
from email_reply_extractor import clean_for_scoring, extract_new_text

result = extract_new_text(body)  # stage 1: quoted thread removed
cleaned = clean_for_scoring(result.text)  # stage 2: furniture removed
print(cleaned.text)
```

The output:

```text
Thanks for the quick turnaround. Replies inline.

30 seconds is too long for the mobile case. I would prefer 10, with
the option to negotiate a longer value.

It should say so explicitly. I can send text for that.

Yes.
```

Stage 1 (`result.text`, method `custom`) removes the attribution line and
both levels of quoted text, and keeps each inline response along with the
greeting, sign-off, signature and list footer. Stage 2 removes those and
reports the removed line indices in `cleaned.ignored_lines`
(`[0, 14, 15, 17, 18, 19, 21, 22, 23]`).

## Contents

- [Comparison with alternatives](#comparison-with-alternatives)
- [Install](#install)
- [What it does](#what-it-does)
- [Limitations](#limitations)
- [Usage](#usage)
  - [Quickstart](#quickstart)
  - [Thread parent and HTML part](#thread-parent-and-html-part)
  - [Sender and addressee evidence](#sender-and-addressee-evidence)
- [API reference](#api-reference)
  - [Status values](#status-values)
  - [Method strings](#method-strings)
- [Extraction version](#extraction-version)
- [Supported Python versions](#supported-python-versions)
- [Other languages](#other-languages)
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
| HTML part | Used as a structural oracle: as the body when no usable plain part exists, replacing a flattened plain part or the plain part of an Apple Mail Share-sheet message, or removing a quoted message leaked unmarked into the plain part | Not handled | Quotation extraction has an HTML mode (lxml-based) |
| Thread parent | Optional parent-diff assist removes a quoted previous message that carries no `>` markers | Not used | Not used |
| Quote headers | Outlook-style blocks, including French, Spanish, Portuguese, Italian, Dutch, German and Chinese label sets, folded and double-spaced renderings | `From:`/`Sent:`/`To:`/`Subject:` fragment detection, English only | Delimiter patterns (e.g. `-----Original Message-----`), several languages |
| Output stability | Behavior pinned by an integer generation (`EXTRACTION_VERSION`) and a corpus digest test; whitespace-tolerant equivalence helpers for consumers | Not versioned separately | Not versioned separately |
| Test corpus | Hand-labeled real mailing-list messages (`fixtures/`) | Unit-test fixture emails | Unit-test fixture emails; ML training data |
| Runtime dependencies | None (standard library) | None (standard library) | lxml, regex, numpy, scipy, scikit-learn, chardet, and others |
| Latest release | 1.6.0 (2026) | 0.5.12 (2020) | 1.4.4 (2017) |
| License | MIT | MIT | Apache 2.0 |

Through generation 9 this library used a fragment scanner derived from
email-reply-parser (see [Provenance](#provenance)). Generation 10 replaced it
with the library's own line rules.

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

It **keeps** the author's greetings, sign-offs and signature blocks, and
inline replies written between quoted blocks.

Stage 2, `clean_for_scoring`, removes that formulaic furniture from stage-1
text: greetings, sign-offs, signature blocks, mailing-list footers, mobile
taglines ("Sent from my iPhone") and trailing legal disclaimers. Given the
sender's display name, the addressees' display names and the sender's
learned signature lines, it also removes sign-offs, name lines, greetings and
signature lines that no fixed list identifies. It reports which non-blank
input lines it removed.

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

## Limitations

The rules are heuristics over text, tuned and measured on technical
mailing-list mail. The limits below are known; each follows from a rule
described in [docs/extraction.md](https://github.com/JayDaley/email-reply-extractor/blob/main/docs/extraction.md). The figures are from
the origin application's corpus of 154,394 IETF list messages and from two
audits of messages drawn from it at random (see
[docs/generation-10.md](https://github.com/JayDaley/email-reply-extractor/blob/main/docs/generation-10.md)). On the held-out audit of 160
messages, 36 (22.5%) have at least one error in generation 10, against 104
(65%) in generation 9.

### Evidence the caller must supply

Several rules act only when the caller passes the evidence they need. Without
it, the corresponding errors remain.

- **The sender's name.** Without `author_name`, a trailing line that is only
  the sender's name is removed only as part of a salutation-and-name sign-off;
  a bare name with no salutation above it ("Dino", "—Daniel") is kept. In
  generation 9, 23.8% of the corpus's cleaned texts ended in a line that is
  only the sender's name. A sender whose display name yields no name token
  (an empty name, or a name with no letters) gets no sender-name rules, and a
  sender who signs in a different script from the display name is not
  matched.
- **Learned signature lines.** `learn_signature_lines` needs at least 5
  stage-1 texts of one sender, and a line must occur among the last 10
  non-blank lines of at least 3 of them and at least one fifth of them. A
  sender with fewer messages gets no learned lines.
- **The addressees' names.** Without `addressee_names`, a greeting line that
  only names people is removed only when it opens with a greeting word, or
  has one to four capitalized words ending in `,`, `:` or `-` ("Jane, Joe,").
- **The thread parent.** Without `parent_body`, a previous message reproduced
  with no `>` markers, no attribution line, no quote-header block and no
  sign-off boundary above it is not removed. Only the direct parent is
  compared: text pasted without markers from any other message is kept.
- **The HTML part.** The Apple Mail Share-sheet case, a flattened plain part,
  and a quoted message leaked unmarked into the plain part are recovered only
  when `html_body` is passed. The HTML assist removes nothing unless it
  removes at least 3 non-blank lines.
- **Decoding.** The functions take decoded strings. MIME parsing, charset
  decoding, resolving `In-Reply-To` to the parent's body, and storing a
  sender's earlier texts for `learn_signature_lines` are the caller's.

With none of the generation-10 evidence arguments, stage 2 still removes more
than generation 9 did: the tolerant sign-off forms and the greeting rules that
need no evidence change the cleaned text of 19,910 of the 154,394 messages.

### What remains

- **Author text carrying `>` markers.** A pasted diff, an mbox `>From` line,
  or inline replies typed inside an Outlook quote block carry quote markers
  and are removed as quoted text: 9 of the 160 held-out messages.
- **Role lines and split signature blocks.** A role line written above the
  sign-off ("Speaking as IAB Chair,", "(as IAB Chair)") is kept, and so is a
  signature block or role line separated from the sign-off by a blank line,
  when it has no `-- ` delimiter, no recognizable contact shape and no learned
  or HTML-hinted lines. A role written on the name line ("Jane (as chair)",
  "Jane, as IAB Chair") or directly below it is removed with the name.
- **Long wrap remainders.** A re-wrapped quote remainder between two quote
  lines is removed only when it has at most two words and starts lowercase;
  a longer remainder is kept as the author's.
- **Text the author repeats from the thread parent.** The parent-diff assist
  cannot always tell a quotation the client left unmarked from a line the
  author wrote again. A repeated line is kept when the message also carries it
  inside its own `>`-marked quotation and the line sits in a block that is
  mostly unmatched text (generation 9), and when the client marks its
  quotation and the line stands in a block with no quote line (marking mode,
  generation 10). A message whose client marks no quotation loses such lines;
  a paragraph a revision carries over unchanged from the parent is removed as
  a whole; and a message that answers an unmarked copy of the parent point by
  point in 8 or more pieces loses its protected runs.
- **Short quotations without markers.** An unmarked quoted line of fewer than
  8 words is removed only as part of a matching run of at least 10 words, or
  by chaining from a removed neighbour when at least 3 lines were removed. A
  short quoted line that stands alone is kept.
- **Part boundaries.** `part_starts` is empty (one part) when a line of the
  text has no counterpart in the body: 10 of the 154,394 messages (72 in
  generation 9).

### Languages and clients

- **Attribution lines** are recognized in English, German, French, Spanish
  and Japanese fixed forms. Other wordings are recognized only when they end
  in a colon directly above a quote line and carry an e-mail address, a clock
  time and a date, or a speech verb from the list in
  [docs/extraction.md](https://github.com/JayDaley/email-reply-extractor/blob/main/docs/extraction.md).
- **Quote-header blocks and "Original message" dividers** are recognized with
  English, German, French, Spanish, Portuguese, Italian, Dutch and Chinese
  labels, and the short "Original" dividers of QQ Mail and Foxmail. A
  localized block is recognized only when its opener is followed by at least
  two further field lines including the language's subject label.
- **Greetings and sign-offs** are matched against fixed multilingual lists,
  and, with the evidence, against the sender's and addressees' names. A
  salutation outside the lists is kept.
- **HTML quote containers** are `<blockquote>`, the Gmail, Mozilla and Outlook
  class and id hooks listed in [docs/extraction.md](https://github.com/JayDaley/email-reply-extractor/blob/main/docs/extraction.md), and Apple Mail's
  Share-sheet wrapper. A client that marks quoted text by another means is not
  recognized, and its quoted text counts as the author's.

### Structure the rules require

- **Sign-off boundary.** A fully top-posted reply with no `>` markers is cut
  at its sign-off only when a salutation line, a name line and an attribution
  follow one another. A reply with no sign-off, or no attribution after it, is
  not cut there.
- **Pasted headers.** A `From:` block the author pasted as evidence is kept
  only when a transport or trace field (`Message-ID`, `Received`, …) sits in
  the header run directly above it; otherwise the body is truncated at the
  block.

### Scope of the measurements

The hand-labeled corpus is 28 messages (25 from the public IETF mail archive,
3 synthetic), processed without the evidence arguments; the rules that need
evidence are pinned by unit tests and conformance vectors only. All measured
effects recorded in `docs/` are taken over IETF mailing-list mail. Behaviour
on other kinds of mail (personal correspondence, customer-support threads,
newsletters) has not been measured. No minimum-length gate is applied:
whether a short extraction is worth processing is the consuming application's
decision.

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
result.method  # 'custom'
result.text  # 'Hi Dan,\n\nTwo comments below.\n\n\nAgreed, and the draft already says so.\n\nBest,\nLucas'

cleaned = clean_for_scoring(result.text)
cleaned.text  # 'Two comments below.\n\nAgreed, and the draft already says so.'
cleaned.ignored_lines  # [0, 7, 8] — the greeting and the two sign-off lines
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
# plain part or for the plain part of an Apple Mail Share-sheet message, or to
# remove a quoted message leaked unmarked into the plain part.
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

### Sender and addressee evidence

Three optional keyword arguments of `clean_for_scoring` carry evidence about
the message: `author_name`, the sender's display name; `addressee_names`, the
display names of the people the message answers or is addressed to (for
example the thread parent's sender and the To and Cc display names); and
`signature_lines`, the lines the sender repeats at the end of their messages,
learned by `learn_signature_lines` from the stage-1 texts of the sender's
earlier messages. Without them only the rules that need no evidence run.

```python
from email_reply_extractor import clean_for_scoring, extract_new_text, learn_signature_lines

# (subject, plain body) of the sender's earlier messages, stored by the caller.
earlier = [
    (
        f"Draft comments, round {i}",
        f"Comment {i} on the draft.\n\nThanks,\nAna\nExample Networks\nhttps://example.net/ana",
    )
    for i in range(6)
]
learned = learn_signature_lines(
    [extract_new_text(text).text for _subject, text in earlier],
    subjects=[subject for subject, _text in earlier],
)
learned  # ('ana', 'example networks', 'https://example.net/ana', 'thanks,')

body = """Joe, Priya,

The new section reads well. One change: keep the timer at 10 seconds.

Thanks,
Ana
Example Networks
https://example.net/ana
"""

result = extract_new_text(body)
clean_for_scoring(result.text).text
# 'The new section reads well. One change: keep the timer at 10 seconds.\n\nThanks,\nAna\nExample Networks\nhttps://example.net/ana'

cleaned = clean_for_scoring(
    result.text,
    author_name="Ana Lima",  # the From display name
    addressee_names=["Joe Example", "Priya Sharma"],  # the To and Cc display names
    signature_lines=learned,
)
cleaned.text  # 'The new section reads well. One change: keep the timer at 10 seconds.'
cleaned.ignored_lines  # [0, 4, 5, 6, 7]
```

Without evidence, the greeting is removed and the sign-off is kept, because
the company line and the link below the name keep it from being the tail.
With `author_name`, "Ana" is recognized as the sender's name, and the lines
attached below it are removed with it; the learned lines would remove them
too.

## Parts of an inline reply

An inline reply interleaves the author's answers with quoted passages. Stage 1
removes the quoted passages and joins the answers into one text;
`ExtractionResult.part_starts` records where each separate run of the author's
text begins, as indices into `result.text.split("\n")`. Passing it to
`clean_for_scoring` divides the cleaned text at the same boundaries:

```python
result = extract_new_text(body)
cleaned = clean_for_scoring(result.text, part_starts=result.part_starts)
cleaned.text  # the whole cleaned text, unchanged by part_starts
cleaned.parts  # one string per run of the author's text
```

A top-posted reply, or any text written as one run, has an empty
`part_starts` and a single part. The boundaries are found by aligning the
text with the body it was derived from; when a line cannot be aligned, the
text is reported as one part (10 of 154,394 messages measured).

## API reference

| Name | What it is |
|---|---|
| `extract_new_text(body, parent_body=None, html_body=None)` | Stage 1; returns an `ExtractionResult`. |
| `ExtractionResult` | Frozen dataclass: `text`, `method`, `status`, `part_starts`. |
| `STATUS_OK`, `STATUS_EMPTY`, `STATUS_FAILED` | The three `status` values. |
| `EXTRACTION_VERSION` | `int` generation stamp of the text-deriving routine. |
| `clean_for_scoring(extracted_text, html_signature_text=None, *, part_starts=(), author_name=None, addressee_names=None, signature_lines=None)` | Stage 2; returns a `CleanResult`. `author_name` is the sender's display name, `addressee_names` the addressees' display names, `signature_lines` the sender's learned signature lines. |
| `learn_signature_lines(texts, *, subjects=None)` | The lines one sender repeats at the end of their messages, learned from the stage-1 `texts` of that sender's messages (and their `subjects`); a tuple of normalized lines, sorted. Fewer than 5 texts learn nothing. |
| `CleanResult` | Frozen dataclass: `text`, `ignored_lines`, `parts`. |
| `html_to_text(html)` | Visible text of an HTML part, block structure as newlines. |
| `split_html_parts(html)` | The same text split into `HtmlParts(novel_text, quoted_text, signature_text, has_share_container)`. |
| `HtmlParts` | Frozen dataclass returned by `split_html_parts`. |
| `strip_parent_content(text, parent_body, *, continuation=False, quoted_text=None)` | Remove lines of `text` provably drawn from `parent_body`; `quoted_text` is the message's marked quotation. |
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
- `custom` — the cleanup pass ran on the body.
- `failed` — extraction raised.

Through generation 9 the base values `erp`, `erp+custom` and
`custom-fallback` named the paths through the vendored fragment scanner;
generation 10 replaced all three with `custom`.

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

`EXTRACTION_VERSION` is an `int` identifying the generation of the routine that
derives the text. It is independent of the package version, only ever
increases, and is incremented whenever a change to the extraction, cleaning,
HTML or name and greeting modules can alter the derived text. It is currently
**10**.

An application that stores derived text should store `EXTRACTION_VERSION`
alongside it and compare the stored stamp with the running constant using `<`
to decide whether the text needs re-deriving. The full contract, including the
digest that pins the behavior over the fixture corpus, is in
[docs/versioning.md](https://github.com/JayDaley/email-reply-extractor/blob/main/docs/versioning.md).

Generation 5 (shipped in v1.1.0) and the work it deferred to generation 10 are described in
[docs/generation-5.md](https://github.com/JayDaley/email-reply-extractor/blob/main/docs/generation-5.md).
Generation 6 (v1.2.0) adds the Latin-script localized quote-header blocks and
dividers described in
[docs/extraction.md](https://github.com/JayDaley/email-reply-extractor/blob/main/docs/extraction.md).
Generation 7 (v1.3.0) replaces the parent-diff assist's `difflib` alignment
with a linear one and makes the whole assist run in linear time; see
[docs/generation-7.md](https://github.com/JayDaley/email-reply-extractor/blob/main/docs/generation-7.md).
Generation 8 (v1.4.0) reads the text of a message sent through Apple Mail's
Share sheet, which the plain part marks as quoted, from the HTML part; see
[docs/generation-8.md](https://github.com/JayDaley/email-reply-extractor/blob/main/docs/generation-8.md).
Generation 9 (v1.5.0) keeps a line of the author's that repeats a line of the
thread parent, when the message also carries that line inside its own marked
quotation and the line sits in a block that is mostly unmatched text; see
[docs/generation-9.md](https://github.com/JayDaley/email-reply-extractor/blob/main/docs/generation-9.md).
Generation 10 (v1.6.0) removes the vendored fragment scanner, adds stage-1
rules for attribution lines, dividers, re-wrapped quote remainders and
unmarked text the author placed, and adds the sender and addressee evidence
of stage 2; see
[docs/generation-10.md](https://github.com/JayDaley/email-reply-extractor/blob/main/docs/generation-10.md).

Because a generation bump can move bytes without changing substance, the
package exports `tolerant_lines` and `texts_equivalent` so a consumer can
re-derive stored text and keep expensive downstream results (for example a paid
classifier score) when only whitespace moved.

## Supported Python versions

Python 3.10 through 3.14; each is exercised in CI.

## Other languages

This package is the reference implementation of the library. TypeScript and
Rust ports are maintained in the same repository, share its version number and
`EXTRACTION_VERSION`, and are tested against conformance data generated from
this package, so all three derive byte-identical text from the same input. See
the [repository README](https://github.com/JayDaley/email-reply-extractor#readme)
and [docs/ports.md](https://github.com/JayDaley/email-reply-extractor/blob/main/docs/ports.md).

## Documentation

- [docs/extraction.md](https://github.com/JayDaley/email-reply-extractor/blob/main/docs/extraction.md)
  — the rules of both stages.
- [docs/versioning.md](https://github.com/JayDaley/email-reply-extractor/blob/main/docs/versioning.md)
  — the `EXTRACTION_VERSION` contract.
- [docs/ports.md](https://github.com/JayDaley/email-reply-extractor/blob/main/docs/ports.md)
  — the contract between the Python, TypeScript and Rust ports.
- [conformance/README.md](https://github.com/JayDaley/email-reply-extractor/blob/main/conformance/README.md)
  — the language-neutral conformance data and the corpus digest.
- [docs/generation-5.md](https://github.com/JayDaley/email-reply-extractor/blob/main/docs/generation-5.md)
  — generation 5 and the scanner changes deferred to generation 10.
- [docs/generation-7.md](https://github.com/JayDaley/email-reply-extractor/blob/main/docs/generation-7.md)
  — generation 7: the linear parent-diff assist and its new aligned-run rule.
- [docs/generation-8.md](https://github.com/JayDaley/email-reply-extractor/blob/main/docs/generation-8.md)
  — generation 8: Apple Mail Share-sheet containers.
- [docs/generation-9.md](https://github.com/JayDaley/email-reply-extractor/blob/main/docs/generation-9.md)
  — generation 9: the parent-diff own-quotation rule.
- [docs/generation-10.md](https://github.com/JayDaley/email-reply-extractor/blob/main/docs/generation-10.md)
  — generation 10: the scanner removed, new stage-1 rules, and evidence for
  stage 2.
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
fixture corpus (messages from the public IETF mail archive) and its
provenance came with it; see
[fixtures/README.md](https://github.com/JayDaley/email-reply-extractor/blob/main/fixtures/README.md).

The extraction also absorbed the application's only runtime dependency,
`email-reply-parser` 0.5.12 (MIT, Zapier's Python port of GitHub's Email Reply
Parser). Its fragment scanner was vendored, trimmed to the scan path this
package consumed, as `_fragments.py`. Two upstream defects were preserved
through generation 4 so the output did not move, and fixed in generation 5
(see
[docs/generation-5.md](https://github.com/JayDaley/email-reply-extractor/blob/main/docs/generation-5.md)).
Generation 10 removed the scanner and replaced it with the package's own line
rules (see
[docs/generation-10.md](https://github.com/JayDaley/email-reply-extractor/blob/main/docs/generation-10.md)).
The package has no runtime dependencies.

Two pieces of evidence support the claim that v1.0.0 derives the same text as
the application it came from: the recorded corpus digest carried over
unchanged, and a differential run over 110,725 real stored messages compared
the old (`email_reply_parser`) and new (vendored) implementations on stage-1
text, method and status and on the stage-2 composite, with zero mismatches.

## License

MIT. See
[LICENSE](https://github.com/JayDaley/email-reply-extractor/blob/main/LICENSE).
