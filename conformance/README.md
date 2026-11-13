# Conformance data

This directory holds language-neutral test data generated from the Python
reference implementation (`python/`). The TypeScript (`typescript/`) and Rust
(`rust/`) ports are tested against it: a port conforms when it reproduces every
recorded output exactly and computes the recorded corpus digest. The contract
between the ports is in [docs/ports.md](../docs/ports.md).

Everything here except this README is generated. Do not edit the JSON by hand.

```
make vectors         # regenerate conformance/ from the Python reference
make check-vectors   # regenerate into a temporary directory; fail on any difference
```

Both run `python/tools/conformance.py`. The committed data is generated with
Python 3.14, the version of CI's `conformance` job. The Python test suite
replays the same data on every supported Python version (3.10 to 3.14).

## Files

| Path | Content |
|---|---|
| `corpus.json` | Every fixture in `fixtures/`: the decoded bodies and the reference outputs. |
| `digest.json` | The corpus digest and the `EXTRACTION_VERSION` it was recorded against. |
| `vectors/<function>.json` | Call-level vectors for one public function. |
| `vectors/internal/<module>.<function>.json` | Call-level vectors for one internal helper. |

## Encoding

All files are UTF-8 JSON with a trailing newline, keys in a fixed order, and a
top-level `"format": 1`. A harness should refuse a `format` it does not know.

- Strings are exact. Every character, including `\r`, NUL, U+2028, U+2029,
  the byte-order mark, zero-width characters and lone combining marks, is part
  of the value. Control characters and invisible or space-like characters
  (DEL, C1 controls, U+00A0, U+00AD, U+2000 to U+200F, U+2028 to U+202F,
  U+205F to U+206F, U+3000, U+FEFF and others) are written as `\uXXXX`
  escapes so that they are visible in a diff; any conforming JSON parser
  restores them. No string contains a lone surrogate.
- Python `None` is `null`; `bool` is `true`/`false`; `int` is a JSON number.
- A list or tuple is an array.
- A result dataclass is an object whose keys are the Python field names, in
  declaration order:
  - `ExtractionResult`: `{"text", "method", "status"}`
  - `CleanResult`: `{"text", "ignored_lines"}` (`ignored_lines` is an array of
    integers)
  - `HtmlParts`: `{"novel_text", "quoted_text", "signature_text"}`
  - `Fragment` (internal): `{"content", "quoted"}`

  A port compares its own result type field by field under these snake_case
  names, whatever its idiomatic field names are.

## corpus.json

```
{"format": 1, "entries": [<entry>, ...]}
```

One entry per fixture, in stem order (the `.eml` file names without the
extension, sorted by code point). Each entry has these keys, in this order:

| Key | Value |
|---|---|
| `stem` | The fixture name. |
| `body` | The decoded `text/plain` part, or `null` when the message has none. |
| `html_body` | The decoded `text/html` part, or `null`. |
| `expected` | The hand-labeled ground truth, `fixtures/expected/<stem>.txt`, read as UTF-8 with CRLF and CR converted to LF. |
| `extract_new_text` | `extract_new_text(body, html_body=html_body)` (no parent body). |
| `clean_for_scoring` | `clean_for_scoring(extract_new_text.text)` (no HTML signature text). |
| `html_to_text` | `html_to_text(html_body)`, or `null` when `html_body` is `null`. |
| `split_html_parts` | `split_html_parts(html_body)`, or `null` when `html_body` is `null`. |
| `html_signature_text` | `split_html_parts.signature_text`, or `null` when that is empty or `html_body` is `null`. |
| `clean_for_scoring_with_html_signature` | `clean_for_scoring(extract_new_text.text, html_signature_text)`, or `null` when `html_body` is `null`. |

The bodies are decoded by the Python suite (`python/tests/corpus.py`) with the
standard-library `email` package, so the ports never parse MIME. The
`clean_for_scoring` value is the stage-2 call the corpus digest is taken over;
the `..._with_html_signature` value is the call consumers make when they pass
the HTML signature container's text as a hint.

The reference output satisfies
`tolerant_lines(clean_for_scoring.text) == tolerant_lines(expected)` for every
entry; a port may check that too, but the exact comparisons above are the
conformance test.

## digest.json

```
{"format": 1, "extraction_version": 6, "digest": "<64 lowercase hex digits>"}
```

This file is the single recorded value of the corpus digest for all three
ports. `extraction_version` is the `EXTRACTION_VERSION` the digest was
recorded against; every port's `EXTRACTION_VERSION` constant must equal it
(`make check-sync`).

### Algorithm

The digest is SHA-256 over a byte stream built from `corpus.json` alone:

1. Start an empty byte stream.
2. For each entry of `entries`, in file order, take four strings in this
   order:
   1. `stem`
   2. `clean_for_scoring.text` (the stage-1-then-stage-2 composite)
   3. `extract_new_text.text`
   4. `extract_new_text.method`
3. For each string: encode it as UTF-8, giving `n` bytes; append the ASCII
   decimal digits of `n` (no leading zeros, `0` for an empty string), then one
   `:` byte (0x3A), then the `n` bytes.
4. The digest is the lowercase hexadecimal SHA-256 of the whole stream.

For example, an entry with stem `a`, composite `Hi`, stage-1 text `Hi\nBob`
and method `erp` contributes the bytes `1:a2:Hi6:Hi\nBob3:erp` (where `\n` is
the single byte 0x0A). There is no separator between fields or entries other
than the length prefixes.

A port computes the digest twice: once over the recorded entries (to check
its implementation of the algorithm), and once with its own outputs in place
of the recorded `clean_for_scoring.text`, `extract_new_text.text` and
`extract_new_text.method`. Both must equal `digest`.

### When the digest changes

`make vectors` re-records `digest.json` only when `EXTRACTION_VERSION` was
incremented, when the set of fixture stems changed, or when it is run as
`python python/tools/conformance.py --rerecord-digest` (for an edited `.eml`).
A moved digest without one of those is reported as an error: the derived text
changed without a generation bump. The rules for incrementing are in
[docs/versioning.md](../docs/versioning.md).

### History

| Generation | Digest | Notes |
|---:|---|---|
| 4 | `faf5f388795897201e92a58600b345c19656df939228bd87bd47ab1578f1db5f` | Carried unchanged from mailing-list-ai-check, the application the library was extracted from. |
| 5 | `2f36f0168c2769b122a0f74c0cfa63a714da3c89f2cd1f90d0f5e072f32d6fb3` | v1.1.0. |
| 6 | `6ccdd626b598da3948b4119ae383ae02f587855c7ea1cb946ff21984dbb1a0d8` | v1.2.0: localized quote-header blocks and dividers, with the two French fixtures added. |

## vectors/

Each file describes one function:

```
{
 "format": 1,
 "function": "strip_parent_content",     Python name
 "module": "extraction",                 extraction | cleaning | html_text | fragments | equivalence
 "public": true,                         false under vectors/internal/
 "parameters": [                         Python signature, in order
  {"name": "text", "kind": "positional_or_keyword"},
  {"name": "parent_body", "kind": "positional_or_keyword"},
  {"name": "continuation", "kind": "keyword_only", "default": false}
 ],
 "returns": "str",                       Python return annotation, informative
 "vectors": [
  {"args": {"text": "...", "parent_body": "..."}, "result": "..."},
  ...
 ]
}
```

A vector holds `args` and either `result` or `error`:

- `args` maps parameter names to values, in parameter order. It holds exactly
  the arguments the recorded call passed; a parameter absent from `args` takes
  its `default` from `parameters`. A harness calls the port's function with
  those values (positionally in parameter order, or as an options object, as
  the port's API takes them) and compares the encoded result with `result`
  using deep equality.
- `error` (instead of `result`) means the reference raised. The value is the
  Python exception class name (`re.error` is always recorded as
  `PatternError`). A port passes such a vector when its function signals an
  error by its own idiom (a thrown exception, an `Err`); the name is
  informative. Public functions never raise, so error vectors occur only under
  `vectors/internal/`.

Vectors are independent of one another and may be run in any order. They are
de-duplicated and sorted by their encoded `args`, so a regeneration with
unchanged behaviour produces byte-identical files.

### How the vectors are produced

`python/tools/conformance.py` runs the Python test suite (except
`test_conformance.py` and `test_extraction_version.py`, which check this data)
with every library function that a test module imports wrapped by a recorder.
Each call's arguments and result are logged. In addition, every call the
pipeline makes to the vendored fragment scanner (`read_fragments`) is logged,
because the scanner has no unit tests of its own. The unit tests therefore
become data: a new Python unit test adds vectors at the next `make vectors`.
`python/tests/test_port_hazards.py` exists to add vectors for inputs that
expose differences between Python's standard library and other languages'
(see [docs/ports.md](../docs/ports.md)).

### Public and internal vectors

The files directly under `vectors/` cover the seven public functions:
`extract_new_text`, `clean_for_scoring`, `strip_parent_content`,
`html_to_text`, `split_html_parts`, `texts_equivalent` and `tolerant_lines`.
Every port must pass all of them.

The files under `vectors/internal/` cover helpers the Python unit tests call
directly (for example `extraction.find_quote_header_block`,
`extraction.normalize_body`, `cleaning.is_mobile_tagline`) and the fragment
scanner. A port whose internal structure mirrors the reference
(see [docs/ports.md](../docs/ports.md)) can run them to localize a failure to
one helper; they are optional. Arguments named `lines` are arrays of strings,
and helpers that return a line index return an integer or `null`.
