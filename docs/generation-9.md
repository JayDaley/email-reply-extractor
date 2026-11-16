# Generation 9

Shipped in v1.5.0. The parent-diff assist keeps a matched run of lines that
the message also carries inside its own marked quotation, when the run sits
in a block that is mostly unmatched text.

## The defect

The parent-diff assist (`strip_parent_content`) removes lines of a reply that
also occur in the thread parent. Its aligned-run and rewrap rules accept a
match anywhere in the reply, including a single line inside a long stretch of
text the parent does not contain, and its continuation rule then chains short
neighbouring lines. Through generation 8 a line the author wrote that repeats
a line of the parent was therefore removed from the author's text. The
recurring forms in stored mail:

- a revised version of a document posted above the quoted thread, where the
  parent holds the earlier version: the lines the revision kept were removed
  from the middle of otherwise kept paragraphs and lists, with the bare list
  numbers that chained off them;
- a proposed-text edit (`OLD:` / `NEW:`, `Current:` / `Perhaps:`), where the
  new wording repeats lines of the old wording that the parent contains;
- a sentence that cites a phrase of the parent, which lost the line holding
  the phrase.

## The rule

The message's *marked quotation* is the quote-marked lines of its body
(`is_quote_line`), in order, taken from the whole body before any truncation.
Its *quotation stream* is those lines, normalized as the assist normalizes
every line (the marker stripped, whitespace collapsed), without the blank
ones, joined by single spaces.

A *block* is a maximal run of consecutive non-blank lines of the text handed
to the assist. A *segment* is a maximal run of consecutive lines within one
block that the aligned-run, rewrap and continuation rules marked. A segment is
restored when both hold:

1. The quotation stream contains the segment's lines joined by single spaces.
2. The unmarked lines of the segment's block total more words than its marked
   lines.

The lines of restored segments are protected. The continuation rule is then
evaluated again from the remaining marks of the aligned-run and rewrap rules,
when at least 3 remain, and never marks a protected line.

Rationale. A client that marks its quotation places its copy of the parent
inside the marked lines. A second copy of the same text outside them was
placed there by the author (condition 1). Text an author places is either
part of a passage the author wrote, or a passage placed whole: a citation
pasted from the parent, a signature. Condition 2 separates the two by the
block the segment sits in. A message with no marked quotation restores
nothing, because an unmarked copy of parent text in such a message may be the
client's own quotation.

The rule applies in both calls that have the body at hand: the assist against
the thread parent, and the HTML oracle's assist against the HTML part's
quoted text. The localized inline-reply test passes no quotation and is
unchanged. For the `html-` paths the body is the HTML part's novel text, which
holds no quote-marked lines, so the rule restores nothing there.

## API

- Python: `strip_parent_content` gained the keyword-only argument
  `quoted_text` (default `None`), the message's marked quotation.
- TypeScript: `stripParentContent` gained the option `quotedText`.
- Rust: the new function `strip_parent_content_with_quotation` takes
  `quoted_text: Option<&str>`; `strip_parent_content` is that function with
  no quotation.

A call without the argument returns what generation 8 returned.

## Measured effect

Every stored extraction of the origin application (154,429 messages) was
re-derived with generation 9 and compared with its generation-8 text.

| | Messages |
|---|---:|
| Extracted text changed | 1,146 |
| Cleaned text changed in substance (`texts_equivalent` false) | 1,117 |
| Lost the `+parent-diff` method suffix (the assist no longer removes a line) | 646 |

2,572 non-blank lines that generation 8 removed are kept. 28 lines in 16
messages that generation 8 kept are removed; in each of those messages the
HTML oracle's assist applies, and it removes a different set of lines once the
parent-diff assist removes fewer.

In the message that exposed the defect, a 263-line revised proposal posted
above the quoted thread, generation 8 removed 20 lines and generation 9
removes 7: one list item of three lines that the revision left unchanged, the
opening lines of two list items whose block is not mostly unmatched text, and
two list numbers that chain off them.

A sample of 45 changed messages, drawn at random, was read by hand:

| Restored lines are | Messages |
|---|---:|
| The author's own sentence, list, proposed text, or a phrase cited inside a sentence | 29 |
| Repeated boilerplate of the author's (file listings, an agenda, a list footer) | 9 |
| Document text pasted by the author under an `OLD:` or `Current:` label | 3 |
| One line of another participant's text, directly above or inside the author's answer | 4 |

The 5,459 stored messages whose HTML part has a quote container give an
independent label for a removed line: inside a quote container and nowhere
outside one, or outside every container. Of the generation-8 removals that
the rule restores in those messages, 1,460 lines are outside every container
and 179 are inside one only.

## What the rule does not change

- A segment whose block is entirely or mostly matched text stays removed: a
  paragraph or list item that a revision left unchanged, and a matched line
  that is longer than the unmatched remainder of its block.
- A message with no marked quotation is treated as in generation 8, so a line
  the author repeats in a reply whose client does not mark quotation is still
  removed.
- A line of the parent placed directly above a longer answer, with no blank
  line between them, is kept when the message also carries it in its marked
  quotation.

## Corpus digest

No fixture passes a thread parent together with a body whose marked quotation
contains a matched run, so the digested corpus output is unchanged and the
digest recorded for generation 9 equals that of generation 8. The rule is
pinned by the unit tests in `python/tests/test_extraction.py` and
`python/tests/test_parent_diff.py`, and through them by the recorded vectors
of `strip_parent_content` and `extract_new_text`.

## Running time

The rule adds one pass over the text for blocks, segments and word counts,
one index over the quotation stream (built only when a segment in a mostly
unmatched block exists), one query of that index per such segment, and at
most one further evaluation of the continuation rule. The segments are
disjoint, so the queries total at most the length of the text. The assist
remains linear in the size of the text, the parent and the quotation;
`test_complexity.py` in each port carries two adversarial inputs for the rule.
