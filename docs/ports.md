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
   | `_names.py` | `src/names.ts` | `src/names.rs` |
   | `_greetings.py` | `src/greetings.ts` | `src/greetings.rs` |
   | `equivalence.py` | `src/equivalence.ts` | `src/equivalence.rs` |

   The private modules `_names.py` and `_greetings.py` were added in
   generation 10; the vendored scanner `_fragments.py` and its counterparts
   `src/fragments.ts` and `src/fragments.rs` were removed in the same
   generation.

   Functions: `extract_new_text` is `extractNewText` in TypeScript and
   `extract_new_text` in Rust, and `learn_signature_lines` is
   `learnSignatureLines` and `learn_signature_lines`; result fields follow the same rule
   (`ignoredLines` / `ignored_lines`). `EXTRACTION_VERSION` is declared as
   `export const EXTRACTION_VERSION = <n>` in `typescript/src/extraction.ts`
   and `pub const EXTRACTION_VERSION: u32 = <n>;` in `rust/src/extraction.rs`;
   `make check-sync` reads it from those lines.

   Keyword arguments of the reference become an options object in
   TypeScript and an options struct or an extra function in Rust. The
   stage-2 evidence is passed as follows:

   | Python | TypeScript | Rust |
   |---|---|---|
   | `clean_for_scoring(text, html, part_starts=…, author_name=…, addressee_names=…, signature_lines=…)` | `cleanForScoring(text, { htmlSignatureText, partStarts, authorName, addresseeNames, signatureLines })` (`CleanOptions`) | `clean_for_scoring_with_options(text, &CleanOptions { html_signature_text, part_starts, author_name, addressee_names, signature_lines })` |
   | `learn_signature_lines(texts, subjects=…)` | `learnSignatureLines(texts, { subjects })` (`LearnOptions`) | `learn_signature_lines(texts, subjects)`, `subjects: Option<&[&str]>` |

   Rust keeps `clean_for_scoring(text, html)` and
   `clean_for_scoring_with_parts(text, html, part_starts)` for the shorter
   call shapes. `None` and an empty sequence mean the same for
   `addressee_names` and `signature_lines`, so the Rust fields are slices;
   `author_name` is an `Option`, and `None` and `""` both mean no sender.
6. **No MIME parsing in the ports.** The public API takes decoded strings
   (plain body, HTML body, parent body). The ports read decoded fixture bodies
   from `conformance/corpus.json`; only the Python suite decodes `.eml` files.
7. **Errors.** `extract_new_text` never raises: an internal error yields
   `ExtractionResult(text="", method="failed", status="failed")`. A port maps
   any internal failure (an exception, an `Err`) to that result; a Rust port
   must not panic on any input. `learn_signature_lines` given fewer
   `subjects` than `texts` does not raise either: it treats a text without a subject as having the empty subject
   (as `None` and `""` are), and ignores subjects beyond the last text.

## Semantic hazards

The reference relies on Python standard-library behaviour that other languages
do not share by default. Each item names where the reference depends on it.
`python/tests/test_port_hazards.py` pins several of them with explicit inputs,
and those inputs are part of the recorded vectors.

### Strings and indexing

- **Code points, not UTF-16 units.** Python `len()`, slicing and indexing
  count code points. The reference uses lengths in `is_signoff_name_line`
  (`len(s) > 40`), `_is_signoff_name_title_line` (`len(title) > 40`),
  `_is_degenerate_plain` (`len(line) > 400`), the 400-character limits of
  a wrapped attribution (`_WRAPPED_ATTRIBUTION_MAX_CHARS`) and of a generic
  attribution unit and its last line (`_GENERIC_ATTRIBUTION_MAX_CHARS`), the
  speech-verb test, which reads the last 24 code points of the head
  (`head[-_SPEECH_VERB_SPAN:]`), the wrap-remainder test (`len(tail) >= 60`),
  the stage-2 name and greeting limits (`is_author_name_line`: 30, 40;
  `_MAX_GREETING_LEN`: 120; a parenthesis of 1 to 30 characters in
  `parse_names`; token length at least 2), first-character tests
  (`tokens[0][0].isupper()`), and regex repetition counts such as
  `[^\[\]\n]{1,20}` (`_INLINE_REPLY_TAG_RE`), `[^:\n]{1,40}`
  (`_LABELED_LINK_LINE_RE`), `[^.!?;:]{0,60}` (`GREETING_RE`) and `.{0,60}` /
  `.{0,40}` (`_NAME_LINE_TAIL_RE`). In
  TypeScript, count with the string iterator and compile regexes with the `u`
  (or `v`) flag. In Rust, count `chars()`, not bytes. The author-tag index
  (`_group_by_length`, `_found_after_bracket`, `_name_in_head`) groups labels
  and names by their length in code points and slices the texts by code
  points; see "Parent-diff matching structures" below for the units the
  word-stream index may use.
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
  and `\s`: the TypeScript port uses `pyStrip`, `pyRstrip`, `pySplit` and
  `pyWordCount` (`src/pyCompat.ts`), and the Rust port `pystr::strip`,
  `pystr::rstrip`, `pystr::split_ws` and `pystr::collapse_ws`
  (`src/pystr.rs`), both over the generated set (see "Generated tables"
  below). Word counts are `len(str.split())` throughout: the generic
  attribution limits (25 and 8 words), the wrap-remainder limit (2 words),
  marking mode (4 words), the greeting and name-line word counts, the
  trailing zone of learned lines (4 words) and the sentence test of
  `learn_signature_lines` (3 words).
- **Explicit strip sets.** Where the reference passes characters to `strip`,
  `lstrip` or `rstrip`, only those characters are removed, in any order, from
  the given end: `rstrip(" \t.,;:!")` and `lstrip(NAME_DECORATION)` in
  `is_author_name_line`, `rstrip(" \t\u00a0")` on a generic attribution's
  head, `strip(".,!?:;()*")` and `rstrip("-—–")` in `_greetings.py`,
  `lstrip(" ,，").rstrip(" .!")` on a one-line sign-off's name, and
  `strip().strip("'")` in `parse_names`. These are not `str.isspace()`
  strips.
- **U+0000.** `normalize_body` removes U+0000 from every body, and stage 1
  then prefixes each line of a generic attribution unit with U+0000 as an
  internal marker (`_GENERIC_ATTRIBUTION_MARK`). The marker keeps the line's
  place for the quote-header, divider and sign-off searches and is removed
  with the attribution lines; no output contains it. A port must remove
  U+0000 before marking, or choose another marker that no input can carry.

### Regular expressions (`re`)

- **`\s`, `\w`, `\d`, `\b` are Unicode-aware.** `\s` is the `str.isspace()`
  set above. `\w` matches a code point for which `str.isalnum()` is true, or
  `_`: letters (Lu, Ll, Lt, Lm, Lo) and code points with a numeric value
  (Nd, Nl, No). It does not match combining marks (Mn, Mc), connector
  punctuation other than `_`, or ZWJ/ZWNJ. `\d` is Nd only. `\b` is defined
  by that `\w`. JavaScript's `\w`, `\d` and `\b` are ASCII-only even with the
  `u` flag; Rust's Unicode `\w` adds marks, connector punctuation and join
  controls and omits No. Both ports build these classes from the generated
  sets instead of the engine's own. Uses: `_NAME_TOKEN_RE` (`[^\W\d_][\w'’.\-]*`),
  `_ATTRIBUTION_START_RE` and `_ATTRIBUTION_END_RE` (`\b`, `\d{4}`, `\s*`),
  `_LINK_ONLY_LINE_RE` (`[\w.+-]`), `_PHONE_LINE_RE`, `_CONTACT_CUE_RE`,
  `_IDENTIFIER_LINE_RE`, `_INLINE_REPLY_TAG_RE`, and `_WORD_CHAR_RE`, the
  one-character `\w` test of `_name_in_head`. Generation 10 adds, in stage 1:
  `_EMAIL_LOCAL_CHAR_RE` and `_EMAIL_DOMAIN_RE` (`[\w.+=-]`, `[\w-]+`),
  `_CLOCK_TIME_RE` and `_DATE_CUE_RE` (`\d`, `\b`, `\s`, so an Arabic-Indic
  or full-width digit is a digit), `_ATTRIBUTION_OPENER_RE`,
  `_DOCUMENT_REFERENCE_RE`, `_SPEECH_VERB_RE`, `_CONTENT_LEAD_IN_RE`,
  `_ANSWER_WORD_RE` and `_INLINE_NOTE_RE` (`\b`); in stage 2: the name
  tokens of `_names.py`, runs of `[^\W\d_]` (letters: a CJK ideograph is a
  letter, a spacing mark (Mc) is not, and a combining mark ends a run),
  `_FURTHER_WORDS_RE` (`[^\W\d_]{2,}`, `(?<![\w.])`), and the greeting
  patterns (`(?![\w])`, `\b`).
- **`.` excludes only `\n`.** JavaScript's `.` also excludes `\r`, U+2028
  and U+2029 (use `[^\n]`, or the `s` flag where the Python pattern uses
  `re.DOTALL`). Rust's `.` matches Python's.
- **`$` without `re.MULTILINE`** matches at the end of the string or before a
  final `\n`. Every `$` in the reference is applied to a single line (no
  `\n`), so it means end of string; `[ \t]*$` does not absorb a `\r`
  (pinned by `test_carriage_return_blocks_end_anchored_rules`).
- **Case-insensitive matching** (`re.IGNORECASE`) is Unicode simple case
  folding (`À`/`à`, `ü`/`Ü`, and also `ſ`/`s`, `K` (U+212A)/`k`, and `İ`
  (U+0130) and `ı` (U+0131) for `i`). The TypeScript port does not use the
  `i` flag: `translatePattern` (`src/pyCompat.ts`) writes each cased
  character as a class of its equivalents from the generated
  `IGNORECASE_EQUIVALENTS` table. The Rust port compiles with `(?i)`, whose
  simple case folding matches Python's for the letters the reference uses
  except `İ` and `ı`, which `pyre.rs` adds explicitly. The generation-10 patterns that
  use it include `_SPEECH_VERB_RE` (`écrit`/`ÉCRIT`, `написал`/`НАПИСАЛ`),
  `_DOCUMENT_REFERENCE_RE`, `_CONTENT_LEAD_IN_RE`, `_ANSWER_WORD_RE`,
  `_LEAD_IN_TAIL_RE`, `_INLINE_NOTE_RE`, `_SHORT_ORIGINAL_DIVIDER_RE`,
  `_NAME_LINE_TAIL_RE`, `_THANKS_SALUTATION_RE`, `_FURTHER_WORDS_RE`, the
  tolerant salutation patterns and the greeting patterns.
- **Scoped flags.** `_LOCALIZED_FIELD_RE` and `_LOCALIZED_SUBJECT_RE` use
  `(?i:…)` inside a case-sensitive pattern; `_IDENTIFIER_LINE_RE` uses
  `(?-i:[A-Z])` inside a case-insensitive one. Rust's `regex` supports both.
  JavaScript supports regex modifiers only from Node 23; on Node 20 and 22 the
  groups must be rewritten, which the TypeScript port's case-equivalent
  classes do.
- **Lookaround.** `_INLINE_REPLY_TAG_RE` (`(?!…)`, `(?=…)`), the dividers'
  `(?<!-)` guard, `_CLOCK_TIME_RE` and `_DATE_CUE_RE` (`(?<!\d)`,
  `(?<![\d.])`, `(?!\d)`), `_NAME_LINE_TAIL_RE` (`(?<![ \t])`),
  `_FURTHER_WORDS_RE` (`(?<![\w.])`, `(?!…)`) and the greeting patterns
  (`(?![\w])`) use lookaround. JavaScript supports it. Rust's `regex` crate
  does not; use `fancy-regex` or equivalent hand code for these patterns. All
  patterns use leftmost-first alternation, which JavaScript and Rust share.
- **`re.split(..., maxsplit=1)`** in `_is_signoff_name_title_line`.

### Character predicates

- `str.isdigit()` (in `is_signoff_name_line` and `_is_html_signature_line`)
  is true for Nd and for code points with Numeric_Type=Digit, such as
  superscripts (`²`) and circled digits; it is not `\d`, and not Rust's
  `char::is_numeric()` (which adds Nl and No).

  The ports take every predicate in this list from the generated sets
  (`ISDIGIT_RANGES`, `ISUPPER_RANGES`, `ISLOWER_RANGES` and `ISALPHA_RANGES`
  through `hasPyDigit`, `firstIsUpper`, `isPyLower` and `isPyAlpha` in
  TypeScript; `pystr::is_digit`, `pystr::is_upper`, `pystr::is_lower` and
  `pystr::is_alpha` in Rust), not from the runtime's character properties,
  whose Unicode version can differ from the reference's.
- `str.isupper()` on one character (`is_signoff_name_line`, the
  capitalized-word tests of `_greetings.py` and `cleaning.py`) is true for
  uppercase letters (Lu and Other_Uppercase), not for titlecase letters
  (`ǅ`, U+01C5).
- `str.islower()` on one character (`_continues`, `_is_wrap_remainder`, the
  lower-case name of a tolerant sign-off) is true for Ll and
  Other_Lowercase, such as U+00AA (`ª`) and U+02B0 (`ʰ`); it is false for
  titlecase letters.
- `str.isalpha()` (`_continues`, the greeting word classes, `parse_names`) is
  true for L* (Lu, Ll, Lt, Lm, Lo) only: a combining or spacing mark is not
  alphabetic. `str.isalnum()` adds code points with a numeric value.
- `_continues` tests whether a first character lies in U+3000 to U+D7AF, by
  code point comparison, as CJK and Hangul text that continues a line.
- `str.upper()` and `str.lower()` are the full Unicode case mappings: the
  acronym test `word.upper() == word` is false for `ß` (which upper-cases to
  `SS`). Stage 2 uses `str.lower()` where the reference says lower-cased (the
  group-word tests, the learned-line normal form after writing `ß` as `ss`)
  and the name folding of `_names.py` (below) for names; it does not use
  `str.casefold()`. `str.lower()` maps `Σ` to `ς` in the Final_Sigma context
  (a cased letter before it and none after it, skipping case-ignorable code
  points) and to `σ` otherwise. The ports do not use JavaScript's
  `toLowerCase()` or Rust's `to_lowercase()` for these, because the
  runtime's Unicode version can differ from the reference's: they lower-case
  with `pyLower` (`src/pyCompat.ts`) and `pystr::lower` (`src/pystr.rs`),
  which apply the generated per-character mapping of `str.lower()` and the
  Final_Sigma rule over the generated cased and case-ignorable sets. The
  acronym test uses the generated set of code points that `str.upper()`
  changes (`isUpperInvariant`) and `pystr::upper`.

### Parent-diff matching structures

`strip_parent_content` and the inline-reply check run in time linear in
their input. Each structure below is specified by its answers, and a port
reproduces the answers exactly; the data structures are the port's choice.

- **Rule (a), `_aligned_run_marks`.** The specification is the comment above
  it in `extraction.py`. The tie-breaks are part of it: the anchor is the
  longest common run counted in lines; among equally long runs, the one with
  the smallest start in the child; its parent position is the smallest start
  of an occurrence in the parent. Lines compare by exact string equality.
  The reference finds the anchor with a suffix automaton over parent line
  ids (`_build_automaton`, `_walk`): the first child end position at which
  the longest matched run strictly exceeds every earlier one, and the
  automaton state's `first_end` (the smallest end position of its strings,
  copied to a clone from the state it splits).
- **Rules (b), (c) and (d), `_ParentStream`.** `contains(needle)` equals the
  substring test `needle in " ".join(parent_seq)`. Every needle is one or two
  normalized lines (single spaces between non-empty words, no other
  whitespace); the reference relies on that form only in its word table, and
  its suffix automaton answers any needle. Membership of one string in
  another gives the same answer whether both are taken as code points, as
  UTF-8 bytes, or as well-formed UTF-16 code units, so a Rust port may index
  bytes. A JavaScript string can hold an unpaired surrogate; keying the
  automaton by code point gives the reference's answer for such strings as
  well. The offsets in the word table and the comparison at a candidate
  position must use one unit consistently. A port that compares UTF-16 code
  units at a candidate position must also reject a match that starts or ends
  inside a surrogate pair of the stream: the stream `a b` followed by U+1F600
  does not contain the needle `a b` followed by the unpaired surrogate U+D83D.
  The TypeScript port keeps its tables in typed arrays (`hashTables.ts`),
  because a JavaScript `Map` holds at most 2^24 entries. The Rust port answers
  with `str::contains`, which gives the same result, for a stream too large
  for its 32-bit table offsets.
- **Rule (d), `_restored_by_own_quotation`.** The specification is the
  comment above it in `extraction.py`. The quotation stream is queried with
  the same substring test as the parent's word-stream (`_ParentStream` over
  the normalized, non-blank lines of `quoted_text`); each needle is one
  segment, its lines joined by single spaces. Word counts are those of
  `str.split()` on the normalized line. A block boundary is a gap in the
  line indexes of the text, so a line that is blank only after normalization
  (a bare quote marker) separates two blocks. Python and TypeScript take the
  quotation as an optional argument of `strip_parent_content`; Rust takes it
  in `strip_parent_content_with_quotation`.
- **Inline-reply tags, `_any_author_tag`.** Equals, per label, the tests
  `"[" + label in parent_body` and
  `re.search(r"(?<!\w)" + re.escape(name) + r"(?!\w)", head)`, with `\w` as
  above. Labels and names are at most 20 code points.
- **Part boundaries, `_part_starts`.** Lines are compared by `str.strip()`
  (the `str.isspace()` set above), after the source is normalized by
  `normalize_body`. `part_starts` holds indices into `text.split("\n")`,
  counted in lines, not characters. The search pointer only moves forward;
  an unmatched line returns an empty list, not a partial one. For the `html-`
  paths the source is `html_to_text(html_body)`, and an error while rendering
  it yields an empty source (one part), never a failed extraction.
- **Parts grouping, `clean_for_scoring`.** The boundaries are filtered to
  `0 < s < len(lines)`, de-duplicated and sorted; a surviving line belongs to
  the part `bisect_right(starts, index)`. Each part is formatted by the same
  steps as `text`, and empty parts are dropped.
- **No map iteration order.** No result depends on the order in which a
  hash map or set is iterated.

### Stage-2 evidence (`_names.py`, `_greetings.py`, `learn_signature_lines`)

- **Name folding.** `fold` in `_names.py` is `str.lower` (the full
  lowercase mapping: `İ` becomes `i` followed by U+0307) followed by one
  `str.translate` pass through the literal `_FOLD_TABLE`: a replacement is
  not looked up again, and a character without an entry is kept. The table
  is data, not a Unicode-database computation: a port copies it entry for
  entry rather than calling its own NFKD or case-folding routine, so the
  result does not depend on the Unicode version of the runtime. The table
  covers Latin-1 Supplement, Latin Extended-A and -B, Latin Extended
  Additional, U+2026, the combining marks U+0300 to U+036F (mapped to
  nothing) and nine letters without a decomposition (`ß` → `ss`, `ø` → `o`,
  `ł` → `l`, `đ` → `d`, `æ` → `ae`, `œ` → `oe`, `ı` → `i`, `ð` → `d`, `þ` →
  `th`); other scripts are only lower-cased. The ports lower-case with
  `pyLower` and `pystr::lower` (above), and copy the table from the
  generated block of `_names.py`: `src/foldTable.ts` (`FOLD_TABLE`) in
  TypeScript, `FOLD_TABLE` in `src/unicode_tables.rs` in Rust. Pinned by the `test_name_folding_*` tests in
  `test_port_hazards.py`.
- **Name tokens.** Tokens are the folded runs of `[^\W\d_]` (letters as
  above, so a CJK ideograph is a letter and a spacing mark is not), and runs
  joined by `'`, `’` or `-`, of at least 2 code points.
- **Learned lines are sorted by code point.** `learn_signature_lines` returns
  `sorted(...)` of the normalized lines, which orders strings by code point.
  JavaScript's default `Array.prototype.sort` compares UTF-16 code units, so
  a character above U+FFFF sorts before U+E000 to U+FFFF there; a port sorts
  by code point. Rust's `str` ordering compares UTF-8 bytes, which equals
  code-point order.
- **The share test is integer arithmetic.** A line is learned when it occurs
  in at least 3 texts and `count * 5 >= len(texts)`; a port must not compute
  a floating-point fraction. The sentence-candidate test is
  `continued * 2 >= total`.
- **Subjects.** `_SUBJECT_PREFIXES_RE` removes any number of leading `[…]`
  tags and `Re:`-style prefixes (`re`, `fw`, `fwd`, `aw`, `sv`, `antw`, `wg`,
  `tr`, with an optional `[n]`) case-insensitively, using `\s` (the
  `str.isspace()` set).

### Generated tables

The Python-specific character data the ports need is generated from the
reference Python, not computed from the runtime's Unicode database. The
committed tables are generated with Python 3.14.6 (Unicode 16.0.0), the
version that generates `conformance/`; run each tool from the repository
root with that Python and commit its output.

| Tool | Writes | Contents |
|---|---|---|
| `python/tools/gen_fold_table.py` (`--check` verifies) | the `GENERATED FOLD TABLE` block of `_names.py` | `_FOLD_TABLE` |
| `typescript/tools/generate_tables.py` | `typescript/src/unicodeTables.ts`, `typescript/src/entities.ts`, `typescript/src/foldTable.ts` | the `str.isspace()`, `\w`, `\d` and letter sets; the `str.isdigit()`, `str.isupper()`, `str.islower()` and `str.isalpha()` sets; the `str.lower()` mapping and the Final_Sigma sets; the code points `str.upper()` changes; the `re.IGNORECASE` equivalents; `html.entities.html5`; `_FOLD_TABLE` |
| `rust/tools/gen_tables.py` | `rust/src/unicode_tables.rs`, `rust/src/html_entities.rs` | the same sets, the `str.lower()` and `str.upper()` mappings and the Final_Sigma sets; `html.entities.html5`; `_FOLD_TABLE` |

The port tools read `_FOLD_TABLE` from the block in `_names.py`, so a change
to the fold table runs `gen_fold_table.py` first and then both port tools.

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
