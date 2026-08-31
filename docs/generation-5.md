# Generation 5

Shipped in v1.1.0. Of the three defects this page originally planned as one
generation, two are fixed; the third, and one workaround tied to it, are
deferred with the measurement that deferred them.

## Fixed: the `count=8` cap in the vendored scanner

Upstream `email-reply-parser` passed `re.MULTILINE` (the integer 8) as
`re.sub`'s positional `count` argument, so the Outlook-boundary newline fix
stopped after eight replacements. The substitution now applies to every
occurrence.

Measured effect on the origin application's corpus of 110,725 real messages:
zero — no stored message has more than eight such boundary lines in a position
that changes the result. The fix is correctness for inputs not yet seen.

## Fixed: per-fragment edge stripping

Upstream stripped each fragment's leading and trailing whitespace before the
extractor re-joined fragments. Re-joining edge-stripped fragments glued
unrelated lines together — a signature line (`Tel:` / `Email:`) directly above
a quote-header block's `From:` disguised the block from the detector. Fragment
content now keeps its edges, preserving the blank lines that separate
fragments.

Measured effect on 95,319 stored extractions in the origin application:

- 37,692 stage-1 texts change; 27,673 cleaned (stage-2) texts change.
- 288 of those cleaned texts change in substance (by the `tolerant_lines`
  comparison); the rest differ only in blank-line placement. Inspection of the
  substantive changes found leaked quote-header blocks and quoted threads now
  correctly removed, with no loss of authored content in any sampled case.

Because most of the movement is whitespace, this package exports
`texts_equivalent` (see `equivalence.py`) so a consumer re-deriving stored
text can keep expensive downstream results when only whitespace moved.

## Deferred: removing the quote-header pre-truncation

The original plan removed `extraction.py`'s pre-scan quote-header truncation
as a workaround made redundant by the edge-stripping fix. Measurement showed
otherwise: with the pre-truncation removed, 10 of the 95,319 stored
extractions changed beyond the edge-stripping fix alone, none reverted, and
the inspected changes were regressions — the scanner's wrapped-attribution
collapse deletes newlines inside a quote-header block (`On\nBehalf Of
X\nSent:` becomes one line), hiding the block from the detector, which only
the pre-scan pass sees intact. The pre-truncation therefore stays. It is not a
workaround for edge stripping (that is fixed); it is a workaround for the
attribution collapse below.

## Deferred: two implementations of wrapped-attribution handling

Wrapped "On … wrote:" attributions are still handled twice: the scanner
collapses a wrapped multi-line header onto one line before the scan (passing
the matched text as a `re.sub` replacement template, upstream's
template-substitution failure mode included), and `extraction.py` recognizes
two- and three-line wrapped attributions directly.

The fix remains as originally planned: cover the forms only the collapse
currently catches with the extractor's patterns, then remove the collapse —
which also removes the failure mode and, per the section above, the need for
the pre-truncation. This needs corpus work first: fixtures for the collapse's
distinctive shapes (including the glued `…example.comwrote:` and `On Behalf
Of` renderings) recorded before the change. It is the natural core of a future
generation 6, together with the pre-truncation removal it unblocks.
