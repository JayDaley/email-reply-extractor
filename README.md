# email-reply-extractor

Recover the author's newly written text from an email — typically mailing-list
mail — in two stages. Pure standard library, no runtime dependencies,
Python 3.10 or later.

## What it does

Stage 1, `extract_new_text`, removes everything the author did not write in
this message: quoted lines (any indentation, including the `...>` form that
survives `format=flowed` rewrapping), attribution lines ("On … wrote:", the
German "Am … schrieb", the Chinese Outlook and Alibaba Mail forms),
forwarded/quote-header blocks, "Original message" dividers, the quoted thread
that follows a sign-off boundary, and — when the caller supplies them —
content provably present in the thread parent or in the HTML part's
blockquotes. It **keeps** the author's greetings, sign-offs and signature
blocks.

Stage 2, `clean_for_scoring`, removes that formulaic furniture from stage-1
text: greetings, sign-offs, signature blocks, mailing-list footers, mobile
taglines and trailing legal disclaimers. It also reports which non-blank input
lines it removed.

The split is deliberate. The stage-1 text is the author's full novel content,
which is what an application should store and display; the stage-2 text is a
strict, documented subset of it, which is what an application should feed to a
downstream consumer such as a classifier. Because stage 2 reports the lines it
dropped, a stage-1 view can mark exactly what was set aside.

Both stages are pure and I/O-free. `extract_new_text` never raises: an
unexpected error is reported as `status="failed"` so a single bad message
cannot stall a pipeline.

The rules of both stages are documented in [docs/extraction.md](docs/extraction.md).

## Install

Not yet published on PyPI. Install from the repository:

```
pip install git+https://github.com/JayDaley/email-reply-extractor
```

## Quickstart

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
result.text  # greeting + both prose lines + the "Best, / Lucas" sign-off

cleaned = clean_for_scoring(result.text)
cleaned.text  # 'Two comments below.\nAgreed, and the draft already says so.'
cleaned.ignored_lines  # [0, 5, 6] — the greeting and the two sign-off lines
```

The quote and its attribution line are gone from `result.text`; the greeting
and the sign-off are still there, and `clean_for_scoring` is what removes them.

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

## Public API

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
| `strip_parent_content(text, parent_text)` | Remove lines of `text` provably drawn from `parent_text`. |
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
**4**.

An application that stores derived text should store `EXTRACTION_VERSION`
alongside it and compare the stored stamp with the running constant using `<`
to decide whether the text needs re-deriving. The full contract, including the
digest that pins the behavior, is in [docs/versioning.md](docs/versioning.md).
Work planned for generation 5 is listed in
[docs/generation-5.md](docs/generation-5.md).

## Supported Python versions

Python 3.10 through 3.14; each is exercised in CI.

## Provenance

The code was extracted from
[mailing-list-ai-check](https://github.com/JayDaley/mailing-list-ai-check), an
application that checks mailing-list mail with an AI detector, where it shipped
as four extraction generations: 1 (initial release), 2 (from that
application's v1.2.0), 3 (from v1.11.0) and 4 (from v1.15.0). The hand-labeled
fixture corpus and its provenance came with it; see
[tests/fixtures/README.md](tests/fixtures/README.md).

The extraction also absorbed the application's only runtime dependency,
`email-reply-parser` 0.5.12 (MIT, Zapier's Python port of GitHub's Email Reply
Parser). Its fragment scanner is vendored, trimmed to the scan path this
package consumes, as `src/email_reply_extractor/_fragments.py`; two upstream
defects are preserved deliberately so the output does not move (see
[docs/generation-5.md](docs/generation-5.md)). The package therefore has no
runtime dependencies.

Two pieces of evidence support the claim that v1.0.0 derives the same text as
the application it came from: the recorded corpus digest carried over
unchanged, and a differential run over 110,725 real stored messages compared
the old (`email_reply_parser`) and new (vendored) implementations on stage-1
text, method and status and on the stage-2 composite, with zero mismatches.

## License

MIT. See [LICENSE](LICENSE).
