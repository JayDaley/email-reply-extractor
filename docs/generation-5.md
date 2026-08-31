# Generation 5 — planned changes

Planned, not yet scheduled. Nothing on this page is implemented.

Three known defects survive in the current routine because fixing any of them
changes the derived text. They are grouped here because they are best done
together: each one alone costs a generation bump, a digest re-record, and a
re-derivation of every stored extraction in every downstream application, and
doing them in one change costs that once instead of three times.

When they land, they land as a single `EXTRACTION_VERSION` 5 bump with one
digest re-record, and as one minor release of the package.

## 1. The `count=8` cap in the vendored scanner

`_fragments.py` breaks a reply off an Outlook-style boundary line it sits
directly on top of with:

```python
text = re.sub("([^\n])(?=\n ?[_-]{7,})", "\\1\n", text, count=8)
```

Upstream `email-reply-parser` passed `re.MULTILINE` as `re.sub`'s positional
`count` argument. `re.MULTILINE` is the integer 8, so the substitution stops
after eight replacements rather than applying the flag it was meant to. A
message with more than eight such boundary lines has the ninth onward left
unbroken.

The fix is to drop the `count` argument. This changes the fragment boundaries
of any message with more than eight boundary lines, which changes the derived
text.

## 2. Per-fragment edge stripping

`_fragments.py` strips each fragment's leading and trailing whitespace before
the extractor re-joins fragments:

```python
return Fragment(content="\n".join(reversed(self.lines)).strip(), quoted=self.quoted)
```

Re-joining edge-stripped fragments can glue unrelated lines together — a
signature line (`Tel:` / `Email:`) can end up directly above a quote-header
block's `From:`, which disguises a real quote header as pasted header evidence
and defeats the block detector.

`extraction.py` compensates for this today by running the quote-header
truncation on the intact body *before* the scan, where the blank line above the
`From:` is still present. That pre-truncation is the workaround, not the
feature.

The fix is in two parts, and both are needed for either to be worth doing:

- Stop stripping fragment edges, preserving the blank lines that separate
  fragments.
- Remove the compensating pre-truncation ordering in `extraction.py`, leaving
  the quote-header truncation to run once, inside `custom_clean`.

Blank-line structure in the joined output changes as a result, which changes
the derived text even where no rule's decision changes.

## 3. Two implementations of wrapped-attribution handling

Wrapped "On … wrote:" attributions are handled in two places, by two
mechanisms:

- The vendored scanner collapses a wrapped multi-line header onto one line by
  deleting the newlines, before the scan. It does this by passing the matched
  text as a replacement *template* to `re.sub`, exactly as upstream did, so a
  body containing backslash escapes can make it raise — which the extractor
  reports as a failed extraction.
- `extraction.py` recognizes an attribution wrapped across two or three lines
  directly, via `_ATTRIBUTION_START_RE` and `_ATTRIBUTION_END_RE`, and drops it
  as a unit.

The collapse also has a visible side effect the extractor already accommodates:
deleting the newline can glue an address onto the terminator
("…example.comwrote:"), which is why the English attribution pattern requires
no word boundary before "wrote".

The fix is to unify the two, keeping `extraction.py`'s handling and removing
the scanner's collapse, which also removes the template-substitution failure
mode. Attribution lines that only the collapse currently catches would have to
be covered by the extractor's patterns first, so this is the part of generation
5 that needs corpus work before it can be written.

## Sequencing

1. Add fixtures covering the cases each fix affects — more than eight boundary
   lines, a quote header directly below a signature line, and the wrapped
   attribution forms the scanner's collapse currently catches — and record the
   digest they produce under generation 4, so the change to each is visible
   rather than merged into one opaque digest move.
2. Make the three changes.
3. Increment `EXTRACTION_VERSION` to 5, re-record `EXPECTED_DIGEST` and
   `DIGEST_EXTRACTION_VERSION`, and confirm every expected file in
   `tests/fixtures/expected` still matches — the ground truth is hand-labeled
   and does not move with the routine, so any expected file that stops matching
   is a regression, not a re-record.
4. Release as a minor version, with the generation and the re-derivation it
   implies stated in `CHANGELOG.md`.
