# Generation 8

Shipped in v1.4.0. The HTML segmenter recognizes the container in which
Apple Mail's Share sheet places the author's own text, and stage 1 derives the
text of a message that holds one from the HTML part.

## The Share-sheet container

Apple Mail's Share sheet sends text composed in another application (Share →
Mail). The HTML part places that text inside a `<blockquote type="cite">`
whose parent `<div>` carries the class `Apple-Mail-URLShareWrapperClass`;
text the author types in Mail itself sits outside that `<div>`, in or after a
sibling `<div class="Apple-Mail-URLShareUserContentTopClass">`. The
`text/plain` alternative renders the blockquote with `> ` before every line.

Through generation 7 both renderings marked the author's text as quoted: the
plain pipeline removed every `>` line, and the HTML segmenter classified every
`<blockquote>` as quoted. The result was an empty extraction, or only a
greeting or signature that sat outside the wrapper.

## The rule

1. **Segmenter (`html_text.py`).** When a `<blockquote>` start tag is read,
   its parent element is the innermost element open at that point: the top
   of the segmenter's stack of open elements. Void elements (`<br>`, `<img>`,
   …) are never on the stack. The stack keeps the segmenter's existing
   malformed-markup handling: there are no implied end tags, and a close tag
   pops the nearest open element with that tag together with every element
   opened inside it. When the parent's `class` attribute, split on
   whitespace, contains the token `Apple-Mail-URLShareWrapperClass` (an exact,
   case-sensitive match), the blockquote is a *share container*. It
   contributes no category of its own, so its direct text takes the category
   of its enclosing containers: novel at the top level. The rule is checked
   before every other container rule, so it applies even when the blockquote
   itself carries a quoted class or id. A `<blockquote>` nested inside a share
   container has the container, not the wrapper, as its parent and is quoted
   as usual. Every blockquote that is a direct child of a wrapper is a share
   container.
2. **`HtmlParts.has_share_container`.** It is true when a share container
   opens while no quoted container is open and outside `head`. A message
   that quotes an earlier Share-sheet message holds the container inside its
   own quote, so the field stays false and that message is processed as
   before.
3. **Stage 1 (`extract_new_text`, path (d)).** When an HTML part is supplied,
   `has_share_container` is true and the HTML's novel text is not blank, the
   text is derived from the HTML's novel text by the normal stage-1 pipeline,
   with the thread parent when one is supplied, and the method is prefixed
   `html-`. This is the same derivation as the HTML-only path (a). The check
   follows paths (a) and (b) and precedes the plain pipeline; it does not
   depend on what the plain pipeline would produce. When the novel text is
   blank, the plain pipeline runs as before.

Inside the container, the normal stage-1 rules apply to the HTML's novel
text: an attribution line ("On … wrote:", "Le … a écrit :") and `>`-quoted
lines that are text inside the container are removed, and a nested
`<blockquote>` is already excluded by the segmenter.

The fixture `toppost-share-sheet-synthetic-01` reproduces the shape: a whole
reply, an attribution line and a nested quoted message inside the share
container, and a plain part with every line `>`-prefixed.

## Measured effect

Over the messages stored by the origin application (about 154,000), 99 carry
the Share-sheet marker, from 21 senders. Before generation 8, 76 of them had
an empty extraction, 10 were below the application's 50-word scoring floor, 9
were scored and 4 were excluded by the application before extraction.

- All 76 empty and all 10 below-floor messages yield text with the rule. The
  54 empty messages of one sender have a median cleaned length of 3,121
  characters.
- Of the 9 scored messages, 7 are unchanged and 2 lose 20 to 35 characters,
  from differences between the HTML and plain renderings of the same text.

These figures were measured with a prototype of the rule that took the HTML
path whenever the marker occurred in the HTML. The rule as shipped excludes a
share container inside a quoted container (rule 2 above); in a re-run over
the same 99 messages, with each thread parent resolved as the application
resolves it:

- 8 messages hold the container only inside a quote; their output is
  unchanged.
- The 76 empty messages yield text; after stage 2, 73 are at or above the
  50-word floor and 3 below it.
- Of the 10 below-floor messages, 9 are at or above the floor and 1 remains
  below it with a longer text.
- Of the 9 scored messages, 6 are unchanged (all 6 hold the container only
  inside a quote), 2 lose 8 and 33 cleaned characters, and 1, whose plain
  extraction held only the signature block, gains the author's text.
- Of the 4 excluded messages, 2 hold the container only inside a quote and
  are unchanged; the other 2 extracted to empty in generation 7 and yield
  text.

## Part boundaries

Generation 8 also reports, for the first time, where an extraction's text is
divided into separate runs of the author's writing:
`ExtractionResult.part_starts`, and `CleanResult.parts` when the boundaries
are passed to `clean_for_scoring` (see "Part boundaries" in
`docs/extraction.md`). The boundaries do not alter any text, method or status,
so the corpus digest is unchanged by them; they are recorded in the
conformance vectors.

The motivating case is an inline reply. An AI-content detector in the origin
application rated a 118-word reply, made of two answers written between quoted
passages, 0.93 AI as one text and human-written as two separate answers.
Scoring each part separately requires the boundaries, which stage 1 discarded
when it joined the remaining lines.

Over 29,895 stage-1 extractions sampled from the origin application:

| Source | Extractions | One part | More than one part | Alignment abandoned |
|---|---|---|---|---|
| `text/plain` body | 27,681 | 21,335 | 6,335 | 11 |
| `text/plain` body, parent-diff applied | 1,968 | 473 | 1,495 | 0 |
| HTML rendering | 240 | 209 | 31 | 0 |
| HTML rendering, parent-diff applied | 6 | 3 | 3 | 0 |

In an abandoned alignment a line of the text has no counterpart in the body;
in the cases inspected, the scanner had joined wrapped lines into one. Those
extractions are reported as one part.
