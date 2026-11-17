# Generation 10

Shipped in v1.6.0. Stage 1 no longer uses the fragment scanner vendored from
`email-reply-parser`: a line-based cleanup pass runs on the whole body, with
new rules for attribution lines, dividers, re-wrapped quote remainders and
unmarked text the author placed. Stage 2 accepts evidence about the sender
and the addressees — their display names and the sender's learned signature
lines — and removes sign-offs, greetings and signature lines that the fixed
lists could not identify.

## Why

An audit of 240 messages drawn at random from the origin application's corpus
(154,394 non-auto-generated IETF list messages) classified every body line of
each message as kept, removed by stage 1 or removed by stage 2, and recorded
where generation 9 differed:

| Error in generation 9 | Messages |
|---|---:|
| Any error | 161 (67%) |
| Furniture kept by stage 2 | 149 (62%): bare name 101, sign-off 64, greeting 34, signature 17, disclaimer 9 |
| Reproduced text kept by stage 1 | 34 (14%): attribution line 14, unmarked quote 10, divider or header 6 |
| Author text removed | 13 (5%): parent-diff 8, author text marked `>` 4, signature over-reach 2 |

The audit recorded 281 errors in those messages. Over the whole corpus,
36,585 cleaned texts (23.8%) of generation 9 end in a line that is only the
sender's name.

## Stage 1

### The fragment scanner removed

Through generation 9 the primary extractor was the fragment scanner of
`email-reply-parser` 0.5.12, vendored as `_fragments.py`, and the cleanup pass
ran on the fragments it did not mark as quoted. Three parts of stage 1
existed because of it:

- the scanner collapsed a wrapped "On … wrote:" attribution onto one line,
  which could hide a quote-header block, so the quote-header truncation also
  ran on the intact body before the scan (the pre-truncation);
- the scanner treated a `---` rule in a dashed-separator digest as a
  signature boundary and truncated there, so the over-strip guard discarded
  its output when fewer than 60% of the body's plainly unquoted lines
  survived, and ran the cleanup pass on the whole body instead (method
  `custom-fallback`);
- the joined lines had no counterpart in the body, so the part-boundary
  alignment was abandoned for them.

[generation-5.md](generation-5.md) deferred the removal of the collapse and
the pre-truncation until the forms only the collapse caught were covered by
the extractor's own patterns. Generation 10 removes the scanner, the
pre-truncation and the over-strip guard. The cleanup pass runs once, on the
whole normalized body. Five per-line rules reproduce what the scanner did
that the cleanup pass did not:

1. An attribution line may follow `--` ("--On … wrote:"), and a wrapped
   attribution's start line may follow `>` markers and a `--`.
2. A wrapped attribution's terminator may land on any of the 4 lines below its
   start line (through generation 9: the next 2), within 400 characters in
   total, and the wrap stops at a line that is a complete attribution line of
   its own.
3. A blank line is inserted between a non-empty line and an Outlook boundary
   line (7 or more `_` or `-`) directly below it, as the scanner did.
4. An "Original message" divider on a quote line is quoted content and does
   not truncate.
5. An attribution followed, after any blank lines, by a quote line heads an
   interleaved quote and does not end the text at a sign-off boundary.

The base method label is now `custom` for every extraction. `erp`,
`erp+custom` and `custom-fallback` no longer occur; the `html-` prefix and the
`+parent-diff` and `+html-quote` suffixes are unchanged (`html-custom`,
`custom+parent-diff`).

### New rules

- **Generic attribution lines.** A unit of one to three lines ending in a
  colon, above a quote line, is removed when it names its source by an e-mail
  address or a clock time and a date within 25 words, or ends in a speech
  verb within 8 words ("Ana Lima said:", "I wrote:"). Document references
  ("Section 2.3 says:", "OLD:") and lead-ins ("the following:") are excluded.
- **Sign-off boundary.** A generic attribution unit with no quote line below
  it counts as the attribution that ends the text after a sign-off.
- **Dividers.** The short "Original" dividers of QQ Mail, Foxmail and similar
  clients (`--- Original ---`, `--- Original Messages ---`, `--- 邮件原文 ---`)
  and a bare "Original" line above a `From:` line truncate like the dashed
  dividers. Below a line that says the author answers inline, they are
  ignored.
- **Wrap remainders.** An unmarked line of at most two words that starts
  lowercase, between two quote lines, below a quote line of at least 60
  characters with no closing punctuation, is removed with the quote.
- **Parent-diff marking mode.** When at least 3 quote-marked lines of at
  least 4 words occur in the thread parent at a smaller quote depth, the
  client marks its quotation, and unmarked parent text in a block with no
  quote line, above any attribution that introduces unmarked text, is the
  author's and is kept. When 8 or more such kept runs of more than 2 lines
  occur, the message answers an unmarked copy of the parent point by point,
  and those runs are removed.
- **Composed-signature containers.** `split_html_parts` recognizes the
  signature containers of Proton Mail (`protonmail_signature_block`), Outlook
  mobile (`ms-outlook-mobile-signature`) and Samsung and other Android
  clients (`composer_signature`). An author can type into these containers;
  their text is signature text when it has no more non-blank lines than the
  novel text above the first of them, and novel text otherwise.
- **U+0000.** `normalize_body` also removes U+0000, which stage 1 uses
  internally to mark generic attribution lines.

The rules are specified in [extraction.md](extraction.md).

## Stage 2

### Evidence

`clean_for_scoring` takes three new optional keyword arguments:

- `author_name` — the sender's display name;
- `addressee_names` — the display names of the people the message answers or
  is addressed to;
- `signature_lines` — the sender's learned signature lines.

`learn_signature_lines(texts, *, subjects=None)` learns those lines from the
stage-1 texts of one sender's messages: a line among the last 10 non-blank
lines of at least 3 texts and of at least one fifth of them, with at least 3
distinct subjects when subjects are given. Fewer than 5 texts learn nothing.

With the evidence, stage 2 adds these rules:

- a sign-off whose name line is the sender's, removed with the attached
  signature lines below it when everything after them is non-blocking
  (footnotes, links, signature debris, taglines);
- a last line that is the sender's name or first initial;
- a salutation and sender-name pair, or a one-line sender-name sign-off,
  anywhere in the text;
- a lone sender-name line between paragraphs;
- a greeting line that names an addressee ("Jane, Joe:");
- a one-line thanks form kept when a sentence follows the name, or when the
  name is an addressee's and not the sender's ("Thank you, Joe.");
- after the passes converge, the learned lines in the trailing zone of the
  text.

### Rules that need no evidence

- The closing sign-off accepts more salutations ("With best regards", "Take
  care", "BR", "R's", "À bientôt", …), two joined salutations ("Thanks and
  regards", "Regards/Ngā mihi"), more punctuation after the salutation (a
  full-width comma, `、`, `:`, `!!`, `...`, or none), and a decorated, indented
  or lower-case name ("-jane", "— Jane Doe").
- Up to two opening greeting lines are removed. A greeting word followed by
  name-like words, a line of group addressees ("WG chairs,", "All:"), and a
  short capitalized line of one to four words ending in `,`, `:` or `-`
  ("Stefano,") are greetings. A greeting line that runs into a sentence ("Hi
  Jane, apologies for the delay.") is kept.

## API

- Python: `clean_for_scoring(extracted_text, html_signature_text=None, *,
  part_starts=(), author_name=None, addressee_names=None,
  signature_lines=None)` gained the keyword-only arguments `author_name`,
  `addressee_names` and `signature_lines`. The new export
  `learn_signature_lines(texts, *, subjects=None)` returns the learned
  lines, normalized and sorted, as a tuple.
- TypeScript: `cleanForScoring` gained the options `authorName`,
  `addresseeNames` and `signatureLines` (`CleanOptions`). The new export
  `learnSignatureLines(texts, { subjects })` returns the learned lines as an
  array; its options type `LearnOptions` is exported.
- Rust: the new struct `CleanOptions` holds every optional argument of
  `clean_for_scoring` (`html_signature_text`, `part_starts`, `author_name`,
  `addressee_names`, `signature_lines`), and the new function
  `clean_for_scoring_with_options(extracted_text, &options)` takes it;
  `clean_for_scoring` and `clean_for_scoring_with_parts` are unchanged. The
  new function `learn_signature_lines(texts, subjects)` takes `subjects` as
  `Option<&[&str]>` and returns a `Vec<String>`.
- All ports: a text without a corresponding subject (`subjects` shorter
  than `texts`) has the empty subject, and subjects beyond the last text are
  ignored.
- All ports: `normalize_body` removes U+0000.
- All ports: `ExtractionResult.method` has the base value `custom` in place
  of `erp`, `erp+custom` and `custom-fallback`.

A call of `clean_for_scoring` without the new arguments runs only the rules
that need no evidence.

## Measured effect

Every message of the origin application's corpus (154,394 non-auto-generated
IETF list messages) was re-derived with generation 10 and compared with its
generation-9 text. The thread parent and the HTML part were passed as the
application passes them. `author_name` was the From display name;
`addressee_names` were the thread parent's From display name and the To and
Cc display names; `signature_lines` were learned per sender address from the
generation-9 stage-1 texts of that sender's messages in the corpus, with their
subjects.

| | Messages |
|---|---:|
| Cleaned text changed in substance (`texts_equivalent` false) | 81,747 (53%) |
| Non-blank cleaned lines removed that generation 9 kept | 152,846 |
| Non-blank cleaned lines kept that generation 9 removed | 13,715 |
| Status `ok` → `empty` | 9 |
| Status `empty` → `ok` | 32 |
| Cleaned text newly empty | 239 |
| Part-boundary alignment abandoned | 72 → 10 |

### Per rule

Each group of rules was measured on its own against generation 9, and the
messages it changes were sampled and read.

| Rules | Messages changed | Sampled changes correct | Audit errors fixed (of 281) |
|---|---:|---|---:|
| Sender-name sign-off rules | 63,588 | 232 of 232 removals | 144 |
| Learned signature lines | 58,261 | 239 of 240 | 116 |
| Greeting rules | 14,998 | 360 of 360 | 28 of 34 greeting errors |
| Generic attributions, dividers, wrap remainders | 8,435 | 40 of 40 per rule | 31 |
| Parent-diff marking mode | 3,014 | see below | 5 of 8 parent-diff errors |
| Scanner, pre-truncation and over-strip guard removed | 793 | about 740 better, 30 worse, 25 neutral | – |

The groups overlap, so the rows do not add up to the combined count. Within
the attribution group, the cue rule changed the stage-1 text of 5,544
messages, the speech-verb rule 3,632, the dividers 1,383 and the wrap
remainders 296. Of 1,322 lines of the shape the wrap-remainder rule removes,
counted in messages with a thread parent before the rule was adopted, 1,276
(96.5%) form, with the quote line above them, a contiguous run of the
parent's text. Removing the scanner alone reduced the abandoned part-boundary
alignments from 72 to 18.

Of 50 messages that marking mode changes, drawn at random: 33 keep author
text or material the author pasted, 1 keeps a real quotation, 6 keep the
author's own signature (which the stage-2 sender-name rules then remove), and
10 show no visible change. The guard against many protected runs applies to
17 messages; all were read: 14 are point-by-point answers to an unmarked copy
of the parent, correctly removed, and 3 lose the author's own re-sent text.

The thanks-to-an-addressee exception keeps 40 lines in 40 messages; all were
read: 36 thank a participant, 4 are the parent's sign-off reproduced in the
text.

With none of the new arguments, the stage-2 rules alone change the cleaned
text of 19,910 messages: the tolerant sign-off forms and the greeting rules
that need no evidence. By the evidence passed:

| Evidence passed | Cleaned texts changed by the stage-2 rules |
|---|---:|
| None | 19,910 |
| `author_name` | 69,717 |
| `author_name`, `addressee_names` | 70,362 |
| All three | 78,034 |

These counts were taken with the stage-1 rules of generation 9 and before the
thanks-to-an-addressee exception and the many-runs guard, which together
change 57 further messages.

### Held-out audit

A second audit of 160 further messages drawn at random, not used to choose any
rule, was made once on generation 9 and once on generation 10, by auditors
given identical instructions:

| | Generation 9 | Generation 10 |
|---|---:|---:|
| Messages with any error | 104 (65%) | 36 (22.5%) |
| Furniture kept | 93 | 20 |
| Reproduced text kept | 19 | 9 |
| Author text removed | 16 | 11 |
| Clean in generation 9, an error in generation 10 | – | 0 |

On the first audit, generation 10 fixes 226 of the 281 errors.

Of the messages whose cleaned text loses more than half of its non-blank
lines, 20 drawn at random were read: every removed line is a greeting,
sign-off, name or attribution line.

### Strategies measured and not adopted

| Strategy | Messages changed | Finding |
|---|---:|---|
| Compare against the grandparent and other messages of the thread | 3,614 | Of 24 sampled, about 7 correct; the rest are the author's own signature or repeated text. |
| Recognize more HTML quote containers | about 600 | The lines they would remove are mostly inline replies typed inside the container. The three composed-signature containers were adopted instead. |
| Truncate at an attribution with no `>` below it, without a thread parent | 193 gained | Precision 0.57 against the output with the parent. |
| A structural rule for replies with no evidence at all | 545 candidates | 5 of 15 read hold a reproduced message; no common cue. |
| A length limit on runs kept by marking mode | 78 | Long runs are mostly the author's own re-sent text. The many-runs guard (17 messages) was adopted instead. |

## What the rules do not change

- Author text that carries `>` markers (a pasted diff, an mbox `>From` line,
  inline replies inside an Outlook quote block) is removed: 9 of the 160
  held-out messages.
- Without `author_name`, or when the display name yields no name token, the
  sender-name rules do not run. A sender who signs with a name in a different
  script from the display name is not matched.
- A sender with fewer than 5 texts passed to `learn_signature_lines` has no
  learned lines.
- A role line above the sign-off ("Speaking as IAB Chair,"), and a signature
  block or role line separated from the sender's name by a blank line, with
  no contact shape and no learned or hinted lines, are kept.
- A re-wrapped quote remainder of three or more words between two quote lines
  is kept.
- The parent-diff assist compares the direct thread parent only.
- All measurements are on IETF list mail; no other kind of mail was measured.

## Corpus digest

The method of every fixture changes (`erp` and `erp+custom` become `custom`;
`erp+html-quote` becomes `custom+html-quote`; `html-erp+custom` becomes
`html-custom`), and the method is part of the digest, so the digest moves.
The stage-1 text of 7 fixtures changes in blank lines only. The cleaned text
of 5 fixtures changes in substance, because the rules that need no evidence
remove a greeting or sign-off their expected files kept; the expected files
of `bottompost-clean-quic-01`, `interleaved-deep-thread-tls-01`,
`signature-pgp-german-attribution-tls-01`, `toppost-flowed-ietf-01` and
`toppost-originalmsg-divider-agent2agent-01` were updated. The cleaned text of
`interleaved-elision-lastcall-01` changes in blank lines only. The digest
recorded for generation 10 is
`69cb4e6c378885d0bc29043026c5ac8194c4bddef81009b9a373c457de3ad725`.

The fixture corpus is processed without the evidence arguments, so the rules
that need evidence, and most of the new stage-1 rules, are pinned by the unit
tests in `python/tests/test_extraction.py`, `python/tests/test_cleaning.py`
and `python/tests/test_html_text.py`, and through them by the recorded
vectors, not by the fixture corpus.

## Running time

Over the corpus, the summed per-message CPU time rises from 892 s to 1,025 s.
On 6,000 messages in a single process, stage 1 takes 16.7 s (generation 9:
16.2 s) and stage 2, with the HTML signature hint and the evidence, 11.7 s
(7.2 s). Every new rule reads a bounded number of lines around each line, or
one pass with a dictionary; `test_complexity.py` carries adversarial inputs
for the new code paths, and every function remains linear in its input.
