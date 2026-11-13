# Ports

The library is maintained in three languages in this repository:

| Port | Directory | Package | Role |
|---|---|---|---|
| Python | `python/` | `email-reply-extractor` on PyPI | Reference implementation |
| TypeScript | `typescript/` | `email-reply-extractor` on npm | Port |
| Rust | `rust/` | `email-reply-extractor` on crates.io | Port |

This page is the contract between them.

## Rules

1. **Python is the reference.** The Python package defines the behaviour. The
   other ports reproduce its output byte for byte; they do not add, drop or
   reinterpret rules.
2. **Conformance is data.** `conformance/` holds the decoded fixture corpus,
   the reference outputs, call-level vectors recorded from the Python unit
   tests, and the corpus digest (format in
   [conformance/README.md](../conformance/README.md)). A port conforms when it
   passes every public vector, reproduces every `corpus.json` output, and
   computes the digest in `digest.json`.
3. **Change procedure.** A change to extraction behaviour is made in this
   order:
   1. Change the Python implementation and its unit tests; increment
      `EXTRACTION_VERSION` in every port if the derived text can change (see
      [versioning.md](versioning.md)).
   2. Run `make vectors` to regenerate `conformance/`, including the digest.
   3. Change the TypeScript and Rust ports until `make test` passes for all
      three.
   4. Commit all three ports, the regenerated `conformance/` and the
      `CHANGELOG.md` section together, and release them under one version.

   A change to extraction behaviour is incomplete until all three ports pass
   the regenerated conformance suite.
4. **One version, one generation, one changelog.** The three packages share
   the package version and `EXTRACTION_VERSION`; `make check-sync` (also run
   by CI and by the Python test suite) fails when they differ, or when a
   port's `LICENSE` copy differs from the root `LICENSE`. `CHANGELOG.md` at the
   root records releases of all three.
5. **Parallel structure.** Each port keeps the reference's module split and
   function names, in the language's own casing, so a failing internal vector
   or a diff between ports points to the same unit in each:

   | Python module | TypeScript | Rust |
   |---|---|---|
   | `extraction.py` | `src/extraction.ts` | `src/extraction.rs` |
   | `cleaning.py` | `src/cleaning.ts` | `src/cleaning.rs` |
   | `html_text.py` | `src/htmlText.ts` | `src/html_text.rs` |
   | `_fragments.py` | `src/fragments.ts` | `src/fragments.rs` |
   | `equivalence.py` | `src/equivalence.ts` | `src/equivalence.rs` |

   Functions: `extract_new_text` is `extractNewText` in TypeScript and
   `extract_new_text` in Rust; result fields follow the same rule
   (`ignoredLines` / `ignored_lines`). `EXTRACTION_VERSION` is declared as
   `export const EXTRACTION_VERSION = <n>` in `typescript/src/extraction.ts`
   and `pub const EXTRACTION_VERSION: u32 = <n>;` in `rust/src/extraction.rs`;
   `make check-sync` reads it from those lines.
6. **No MIME parsing in the ports.** The public API takes decoded strings
   (plain body, HTML body, parent body). The ports read decoded fixture bodies
   from `conformance/corpus.json`; only the Python suite decodes `.eml` files.
7. **Errors.** `extract_new_text` never raises: an internal error yields
   `ExtractionResult(text="", method="failed", status="failed")`. A port maps
   any internal failure (an exception, an `Err`) to that result; a Rust port
   must not panic on any input.

## Semantic hazards

The reference relies on Python standard-library behaviour that other languages
do not share by default. Each item names where the reference depends on it.
`python/tests/test_port_hazards.py` pins several of them with explicit inputs,
and those inputs are part of the recorded vectors.

### Strings and indexing

- **Code points, not UTF-16 units.** Python `len()`, slicing and indexing
  count code points. The reference uses lengths in `is_signoff_name_line`
  (`len(s) > 40`), `_is_signoff_name_title_line` (`len(title) > 40`),
  `_is_degenerate_plain` (`len(line) > 400`), first-character tests
  (`tokens[0][0].isupper()`), and regex repetition counts such as
  `[^\[\]\n]{1,20}` (`_INLINE_REPLY_TAG_RE`), `[^:\n]{1,40}`
  (`_LABELED_LINK_LINE_RE`) and `[^.!?;:]{0,60}` (`_GREETING_RE`). In
  TypeScript, count with the string iterator and compile regexes with the `u`
  (or `v`) flag. In Rust, count `chars()`, not bytes.
- **Line splitting is `split("\n")` only.** Every function splits on LF
  alone; `\r`, U+2028 and other separators stay inside lines, and a trailing
  newline yields a final empty line. `str.splitlines()` is never used. Rust's
  `str::lines()` (which also strips `\r` and drops the final empty line) and
  JavaScript splits on `/\r?\n/` are not equivalent. Only
  `normalize_body` (stage 1, and inside `strip_parent_content`) converts CRLF
  and CR to LF; `clean_for_scoring`, `tolerant_lines` and the HTML functions
  do not.
- **Whitespace is the `str.isspace()` set.** `str.strip()`, `str.rstrip()`
  and `str.split()` with no argument (used for word counts, the parent-diff
  line normalization, the HTML whitespace collapse and the equivalence
  helpers) treat as whitespace: `\t \n \v \f \r`, U+001C to U+001F, space,
  U+0085, U+00A0, U+1680, U+2000 to U+200A, U+2028, U+2029, U+202F, U+205F
  and U+3000. JavaScript's `trim()` and `\s` omit U+001C to U+001F and U+0085
  and add U+FEFF; Rust's `trim()` and `split_whitespace()` omit U+001C to
  U+001F. A port defines this set once and uses it for every strip, split
  and `\s`.

### Regular expressions (`re`)

- **`\s`, `\w`, `\d`, `\b` are Unicode-aware.** `\s` is the `str.isspace()`
  set above. `\w` matches a code point for which `str.isalnum()` is true, or
  `_`: letters (Lu, Ll, Lt, Lm, Lo) and code points with a numeric value
  (Nd, Nl, No). It does not match combining marks (Mn, Mc), connector
  punctuation other than `_`, or ZWJ/ZWNJ. `\d` is Nd only. `\b` is defined
  by that `\w`. JavaScript's `\w`, `\d` and `\b` are ASCII-only even with the
  `u` flag; Rust's Unicode `\w` adds marks, connector punctuation and join
  controls and omits No. Uses: `_NAME_TOKEN_RE` (`[^\W\d_][\w'’.\-]*`),
  `_SIG_RE` (`-\w`, `\w+\s*`), `_ATTRIBUTION_START_RE` and
  `_ATTRIBUTION_END_RE` (`\b`, `\d{4}`, `\s*`), `_MULTI_QUOTE_HDR` (`\s`),
  `_LINK_ONLY_LINE_RE` (`[\w.+-]`), `_PHONE_LINE_RE`, `_CONTACT_CUE_RE`,
  `_IDENTIFIER_LINE_RE`, `_INLINE_REPLY_TAG_RE` and `_is_author_tag`
  (`(?<!\w)…(?!\w)`).
- **`.` excludes only `\n`.** JavaScript's `.` also excludes `\r`, U+2028
  and U+2029 (use `[^\n]`, or the `s` flag where the Python pattern uses
  `re.DOTALL`). Rust's `.` matches Python's.
- **`$` without `re.MULTILINE`** matches at the end of the string or before a
  final `\n`. Every `$` in the reference is applied to a single line (no
  `\n`), so it means end of string; `[ \t]*$` does not absorb a `\r`
  (pinned by `test_carriage_return_blocks_end_anchored_rules`).
- **Case-insensitive matching** (`re.IGNORECASE`) is Unicode simple case
  folding (`À`/`à`, `ü`/`Ü`, and also `ſ`/`s`, `K` (U+212A)/`k`). Use the `u`
  flag in JavaScript; Rust's `(?i)` matches.
- **Scoped flags.** `_LOCALIZED_FIELD_RE` and `_LOCALIZED_SUBJECT_RE` use
  `(?i:…)` inside a case-sensitive pattern; `_IDENTIFIER_LINE_RE` uses
  `(?-i:[A-Z])` inside a case-insensitive one. Rust's `regex` supports both.
  JavaScript supports regex modifiers only from Node 23; on Node 20 and 22 the
  groups must be rewritten (for example as explicit `[Ss]` classes, or
  `[A-Z]` checked separately).
- **Lookaround.** `_MULTI_QUOTE_HDR` (`(?!On.*On\s.+?wrote:)` with a lazy
  `.+?` under `re.DOTALL`), the scanner's Outlook-boundary substitution
  (`(?=\n ?[_-]{7,})`), `_INLINE_REPLY_TAG_RE` (`(?!…)`, `(?=…)`) and
  `_is_author_tag` (`(?<!\w)`, `(?!\w)`) use lookaround. JavaScript supports
  it. Rust's `regex` crate does not; use `fancy-regex` or equivalent hand
  code for these patterns. All patterns use leftmost-first alternation, which
  JavaScript and Rust share.
- **`re.sub` replacement templates.** `read_fragments` (`_fragments.py`)
  replaces every match of `_MULTI_QUOTE_HDR` with the *first* match's text
  (newlines removed), passed as a replacement template. Python processes
  backslash escapes in it: `\n`, `\t` and similar become control characters,
  `\\` becomes `\`, `\1`/`\g<1>` insert groups, and an unknown escape of an
  ASCII letter (`\U` in `C:\Users`) raises `re.error`, which
  `extract_new_text` turns into the `failed` result. `$` is literal. A
  JavaScript port must not pass the text to `String.prototype.replace` as a
  pattern string (which expands `$&`, `$1`); a Rust port must not use the
  `regex` crate's `$` expansion. Both implement Python's template rules.
  Pinned by the three `test_*_in_attribution_*` tests.
- **`re.split(..., maxsplit=1)`** in `_is_signoff_name_title_line`, and
  `re.escape` for the dynamic pattern in `_is_author_tag`.

### Character predicates

- `str.isdigit()` (in `is_signoff_name_line` and `_is_html_signature_line`)
  is true for Nd and for code points with Numeric_Type=Digit, such as
  superscripts (`²`) and circled digits; it is not `\d`, and not Rust's
  `char::is_numeric()` (which adds Nl and No).
- `str.isupper()` on one character (`is_signoff_name_line`) is true for
  uppercase letters (Lu and Other_Uppercase), not for titlecase letters
  (`ǅ`, U+01C5). Rust's `char::is_uppercase()` matches; in JavaScript use
  `/\p{Uppercase}/u`.

### `difflib.SequenceMatcher`

`strip_parent_content` runs `SequenceMatcher(a=child, b=parent,
autojunk=False)` over lists of normalized lines and uses
`get_matching_blocks()`. The blocks depend on the algorithm's tie-breaking:
`find_longest_match` returns the longest block, and among equally long
blocks the one starting earliest in `a`, then earliest in `b`;
`get_matching_blocks` recurses on the regions left and right of each block
and merges adjacent blocks. With `autojunk=False` and no junk function there
is no popularity pruning. A port reimplements these two methods literally;
a different LCS or diff algorithm produces different blocks and different
output.

### `html.parser.HTMLParser`

`html_text.py` subclasses `HTMLParser(convert_charrefs=True)` and overrides
only `handle_starttag`, `handle_endtag` and `handle_data`. A port
reproduces its tokenizer, not a browser's DOM:

- Text is unescaped with `html.unescape`: the HTML5 named references, some of
  which match without a trailing `;` by longest known prefix (`&amp`, `&copy`,
  `&noti;` → `¬i;`); numeric references 0x80 to 0x9F map through
  windows-1252; 0, surrogates and values above 0x10FFFF become U+FFFD; other
  invalid code points are dropped. Attribute values (`class`, `id`) are
  unescaped too.
- Comments, `<!DOCTYPE>`, processing instructions and CDATA sections produce
  no data and are dropped. `script` and `style` content is raw text up to the
  matching end tag; `head` content is parsed as markup and dropped by the
  segmenter.
- Tag and attribute names are lowercased; an attribute without a value is
  `None`; with duplicate attributes, `dict(attrs)` keeps the last value.
- There is no tree construction: no implied end tags, no foster parenting.
  The segmenter's own stack handles unclosed and stray tags.
- A `<` that does not start a tag is text.
- `HTMLParser`'s handling of malformed comments, CDATA and raw-text end tags
  has changed in recent Python patch releases. The reference output is the
  one produced by the Python version that generates `conformance/` (3.14);
  the recorded vectors also pass on 3.10 to 3.13.

The HTML whitespace collapse (`" ".join(line.split())`) uses the
`str.isspace()` set, so U+00A0 from `&nbsp;` collapses to a space.

## Adding a port function or test

- A new public function is added to all three ports in the same change, and
  gets Python unit tests so that its vectors are recorded.
- A port may add tests of its own (idiomatic API tests, property tests), but
  behaviour is asserted through the shared vectors, so a test of extraction
  behaviour belongs in the Python suite.
