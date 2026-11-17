# Extraction rules

The pipeline has two stages. Stage 1 (`extract_new_text`, `extraction.py`)
recovers the author's novel content — everything in the message that is not
quoted or otherwise reproduced from elsewhere — and keeps the author's own
framing furniture. Stage 2 (`clean_for_scoring`, `cleaning.py`) removes that
furniture and reports which lines it removed.

Both stages are pure and I/O-free. `extract_new_text` catches every unexpected
error and reports it as `status="failed"`, so one malformed message cannot
stall a pipeline.

This page restates the rules recorded in the modules' own docstrings, which
remain authoritative.

---

## Stage 1 — recovering novel content

Stage 1 is a line-based cleanup pass over the normalized body, followed by an
optional parent-diff assist. Through generation 9 the primary extractor was a
fragment scanner vendored from `email-reply-parser`, with the cleanup pass
applied to its output; generation 10 removed the scanner and the two
workarounds tied to it (see [generation-10.md](generation-10.md)).

The order of the steps:

1. Pre-normalization of the whole body.
2. The message's marked quotation is taken from the normalized body (for the
   parent-diff own-quotation rule), before anything is removed.
3. Generic attribution units and wrap remainders are marked.
4. The localized inline-reply test decides whether the Latin-script localized
   quote-header blocks and dividers apply.
5. The cleanup pass (`custom_clean`).
6. The parent-diff assist, when the thread parent is given.

The HTML part, when given, selects the body this sequence runs on, or removes
content after it; see "HTML as a structural oracle" below.

### Pre-normalization

`normalize_body` converts CRLF and CR to LF and removes the byte-order mark,
the zero-width characters (ZWSP, ZWNJ, ZWJ, word joiner) and U+0000. A
leading BOM therefore cannot hide a `>` quote marker. U+0000 is removed
because stage 1 uses it internally to mark the lines of a generic attribution
unit; after normalization no other line carries it.

### The cleanup pass

`custom_clean` applies composable steps in this order, and the order matters:

1. Separate Outlook boundary lines from the line above.
2. Truncate at a forwarded/quote-header block.
3. Truncate at an "Original message" divider.
4. Truncate at a sign-off boundary — which must run while the attribution
   evidence that proves the boundary is still present.
5. Drop attribution lines: the fixed forms, wrapped forms and marked generic
   attribution units.
6. Drop quoted lines, wrap remainders included.

Trailing whitespace is then trimmed per line and blank edge lines are removed;
the first content line's indentation is preserved, because it is significant in
digest tables. Blank lines inside the text are kept as they are in the body.

### Attribution lines

Removed forms: the English "On \<date\>, X wrote:" (also after `--`) and
"Name \<a@b\> wrote:", the German "Am \<date\> schrieb X:", the French and
Spanish terminators, and the Japanese "\<date\>、X のメール:" form anchored on
a leading year so prose mentioning someone's mail never matches.

A wrapped form is removed as a unit: a start line ("On …", "Am …", "Le …",
"El …", "El día …" or a leading year, optionally after `>` markers and a `--`)
without a terminator of its own, whose terminator ("wrote:", "schrieb …:",
"a écrit :", "escribió:", "のメール:") lands on one of the next 4 lines. No
line from the start line through the terminator line may be blank or be a
complete attribution line of its own, and those lines hold at most 400
characters in total.

The pattern lists are structured per language so more languages are easy to
add.

### Generic attribution lines

Clients introduce a quote with many wordings the fixed forms do not list
("Le 3 mars 2026 à 10:15, Ana \<ana@example.org\> a écrit :", "Ana Lima
said:"). A *generic attribution unit* is one to three lines, the last ending
in `:` or `：` (U+FF1A). The unit ending at a line is found as follows:

1. The last line is not a quote line and holds at most 400 characters.
2. The last line is neither the terminator of a wrapped attribution that
   starts on one of the 4 lines above it, nor a complete attribution line of a
   fixed form that matches the start pattern. Those are fixed forms.
3. The unit extends upward over at most two more lines. A line joins when it
   is neither blank nor a quote line, and the unit's first line reads as its
   continuation: the first non-blank character of that first line is
   lowercase, is not a letter, or lies in U+3000 to U+D7AF (CJK and Hangul);
   or the joining line does not end in `.`, `!`, `?` or `:` and opens like an
   attribution (`On`, `-On`, `--On`, `Le`, `Am`, `El`, `Il`, `Op`, `Den`,
   `Ar`, `At`, `In`, `Em`, `Dne`, `W dniu`, `Pe`, `Às`, or a digit).
4. A one-line unit that is an attribution line of a fixed form is left to the
   fixed forms.
5. The unit's lines, stripped and joined by single spaces, are its *text*, at
   most 400 characters. Its *head* is the text without the final colon and
   trailing blanks. The head must not be empty, must not open with a document
   reference (section, sec., §, figure, fig., table, appendix, RFC, OLD, NEW,
   CURRENT, PROPOSED, optionally after `(`), and must not end in a URL, "as
   follows", "following" or "below". The unit is an attribution when either
   holds:
   - **cue**: the head has at most 25 words and holds an e-mail address, or
     both a clock time ("9:30", "19h30") and a date (a year from 1900 to 2099,
     a day and month such as "3/12" or "3. 12.", or a CJK month "3月");
   - **speech verb**: the head has at most 8 words, ends in a speech verb
     ("wrote", "said", "schrieb", "écrit", "escribió", "scritto", "schreef",
     "skrev", "kirjoitti", "napisał", "escreveu", "ysgrifennodd", "написал",
     "写道", "쓴 글" and further forms; the test reads the last 24 characters
     of the head), holds no word that introduces quoted content rather than a
     person ("it", "this", "that", "what", "text", "draft", "document",
     "spec", "section", "rfc", "paragraph", "figure"), and does not open with
     a bullet. "I wrote:" and "Ana wrote:" qualify; "Section 2.3 says:" and
     "the text said:" do not.

A unit whose last line is followed by a quote line, directly or after one
blank line, is marked line by line and removed with the attribution lines. A
unit with no quote line below is not removed on its own, but it counts as the
attribution of a sign-off boundary (below).

### Wrap remainders

A client that re-wraps a quote at a narrower width without marking the new
lines leaves the tail of a long quote line as a short unmarked line between
two quote lines ("\> … the working group has" / "consensus" / "\> on …"). Such
a line is treated as a quote line, and removed with the quote, when all hold:

- the lines directly above and below it are quote lines;
- the line above, without trailing whitespace, holds at least 60 characters
  and does not end in `.`, `?`, `!`, `:`, `;`, `)` or `"`;
- the line holds at most 2 words, its first non-blank character is lowercase,
  and it does not open with an answer word ("ok", "yes", "no", "agreed",
  "done", "sure", "fine", "ack", "right", "true", "correct", "see", "thanks"
  and their variants) or a URL.

A line of three or more words, or one that opens uppercase, is left as the
author's: inline answers between quoted lines take those shapes.

### Indented quote blocks

A quoted line is optional indentation, an optional run of leader dots (the
`...>` form that survives `format=flowed` rewrapping), then one or more `>`.
All such lines are dropped.

### Sign-off boundary

A salutation line ("Best," / "Cheers" / "Thanks," …) plus a name line,
directly followed by an attribution that introduces unmarked text, ends the new
text. Everything after the name is the quoted message, even when the client
added no `>` markers — the Gmail-style fully top-posted reply, which is also
the typical shape of AI-generated mail.

The attribution is the next non-blank line after the name: a fixed form, a
wrapped form, or a generic attribution unit, whether or not a quote line
follows the unit. It must not be followed, after any blank lines, by a quote
line: an attribution above `>`-quoted text heads an interleaved quote, and the
author's inline replies below it are kept.

All three parts are required. A bare mid-thread "Thanks." never truncates,
because the name line is the strong signal; a sign-off at the true end of the
message has nothing after it to prove quoted material follows; a sign-off
followed by authored content (a "Ps." block) does not truncate either.

Both sign-off shapes are recognized: a salutation whose name follows on the
next non-blank line ("Best," then "Songbo"), and the one-line form ("Cheers,
Peter"). The name may carry a short title or affiliation tail after `|` or `,`
("Thi Nguyen-Huu | CEO", "Louis Navarre, UCLouvain"). The salutation list is
multilingual (English, German, French, Spanish, Dutch, Māori, Nordic and
others) and is shared with stage 2's closing-sign-off rule.

The sign-off itself survives stage 1: this rule removes only the quoted thread
the sign-off precedes.

### Forwarded / quote-header blocks

Outlook and forwarding clients generally introduce quoted content with a block
of pseudo-RFC 5322 headers instead of `>` markers. A qualifying block is a
`From:` line followed by at least one further header line, at least one of
which is a quote-header signal field (`Sent:`, `Date:`, `To:`, `Cc:`, `Bcc:`,
`Reply-To:`, `Subject:`, or a localized equivalent). Everything from that
`From:` line to the end of the body is dropped, along with a dashed divider
left dangling directly above it (the shape Alibaba Mail draws) or a bare
"Original" line (the shape QQ Mail draws).

Localized label sets are recognized: German (`Gesendet`, `An`, `Betreff`) and
Chinese (发件人 / 发送时间 / 收件人 / 抄送 / 主题, with full-width colons and
the ideographic-space padding 主　题, as produced by Alibaba Mail and Chinese
Outlook).

From generation 6, Latin-script localizations are recognized with a localized
opener as well:

| Language | Opener | Other fields | Subject label |
|---|---|---|---|
| French | `De` | `Envoyé` / `Envoye`, `Date`, `À` / `A`, `Cc`, `Cci` | `Objet` |
| Spanish | `De` | `Enviado` / `Enviado el`, `Fecha`, `Para`, `CC`, `CCO` | `Asunto` |
| Portuguese | `De` | `Enviada em` / `Enviado em`, `Data`, `Para`, `Cc` | `Assunto` |
| Italian | `Da` | `Inviato`, `Data`, `A`, `Cc` | `Oggetto` |
| Dutch | `Van` | `Verzonden`, `Datum`, `Aan`, `CC` | `Onderwerp` |
| German | `Von` | `Gesendet`, `Datum`, `An`, `Cc` | `Betreff` |

French typography puts a space before the colon (`De : …`); in stored text that
space is often U+00A0 (no-break space) or U+202F (narrow no-break space). For
the localized labels, a space, tab, U+00A0 or U+202F is accepted before and
after the colon. The opener labels (`De`, `Da`, `Van`, `Von`) and several field
labels (`A`, `An`, `Data`, `Date`) are ordinary words, so a localized block
qualifies only when its opener is followed by at least two further field lines,
one of which is its language's subject label. A prose line beginning "De : …",
or a run such as "Von: …" / "An: …" without a subject line, is not a block.

Two guards keep the rule from firing on the wrong thing:

- **Pasted header evidence.** When the contiguous header run directly above the
  `From:` contains a transport or trace field (`Message-ID`, `References`,
  `In-Reply-To`, `Received`, `Return-Path`, `Resent-*`) the `From:` sits inside
  headers the author pasted as evidence, not inside a client's quote header,
  and the block is not recognized. A signature or banner line above the
  `From:` (`Tel:`, `Email:`, `Classification:`) is header-shaped but not
  transport, and does not disqualify the block.
- **Strict versus tolerant walking.** When the `From:` line carries an address,
  the walk tolerates the two mangled renderings mail archives produce: folded
  header lines that lost their leading whitespace, and a single blank line
  between fields (a double-spaced rendering). Without an address the strict
  walk applies — header fields and whitespace-indented continuations only — so
  a prose line that happens to begin "From: …" cannot reach across its
  paragraph to find a signal field.

Through generation 9 this truncation also ran on the intact body before the
fragment scan, because the scanner's line joining could hide a block. With the
scanner removed, the cleanup pass runs on the intact body and the truncation
runs once.

### "Original message" dividers

The dashed `-------- Original message --------` divider (also
`-----Original Message-----`, "Forwarded message", and the Chinese 邮件原件 and
原始邮件 forms) and everything after it is dropped. Dashes are required on both
sides, so prose mentioning an original message never matches.

From generation 6 the Latin-script localizations are recognized too: French
`Message d'origine` (straight or curly apostrophe) and `Message transféré`,
Spanish `Mensaje original` / `Mensaje reenviado`, Portuguese `Mensagem original`
/ `Mensagem encaminhada`, Italian `Messaggio originale` / `Messaggio inoltrato`,
Dutch `Oorspronkelijk bericht` / `Doorgestuurd bericht`, and German
`Ursprüngliche Nachricht` / `Weitergeleitete Nachricht`.

From generation 10:

- The short forms of QQ Mail, Foxmail and similar clients are recognized:
  `------------------ Original ------------------`, `--- Original Message ---`
  / `--- Original Messages ---` and `--- 邮件原文 ---`, with dashes on both
  sides; the spacing may include U+00A0, U+3000 or a literal `&nbsp;`.
- A bare "Original" line is a divider when the first non-blank line among the
  four below it is a `From:` or 发件人 line.
- A divider on a quote line is quoted content and never truncates.
- Once a line above holds a note that the author answers inline ("inline",
  "in-line", in a quote line or in a line without a divider), the short forms
  and the bare "Original" line are ignored: the author announced replies
  written inside the quoted message below them. A divider of the other forms
  still truncates.

The divider is matched anywhere in a line rather than only at its start,
because HTML-to-text conversion sometimes flattens a whole quoted message onto
one line and glues the divider to the end of the author's text. In that case
the author's prefix before the divider is kept.

### Inline replies below a localized block

Some authors answer point by point inside the quoted message, below the
client's quote-header block, and tag each answer (`[Med] ACK`). Generation 5 did
not recognize the Latin-script localized blocks and dividers, so such answers
below a French block survived, and the parent-diff assist removed the quoted
parent around them. Truncating at the localized block would remove the
answers.

The localized blocks and dividers are therefore not applied to a message whose
span between the first localized boundary and the first generation-5 boundary
(or the end of the body) holds the author's tagged answers. A tag is a
bracketed label of 1-20 characters at the start of a line containing a letter;
citation forms (`[RFC8174]`, `[BCP14]`, `[I-D.x]`) and numeric footnotes are
excluded. A divider on a quote line is not a boundary here either.

- With the thread parent, the parent-diff assist runs on that span alone. It
  must remove at least one non-blank line, and a tagged line must survive whose
  tag belongs to the author: the parent never uses the tag, or the tag's label
  without a trailing round number (`Linda3` → `Linda`) occurs as a word above
  the block, as the author's sign-off usually does (`Med` for `[Med]`). A tag
  the parent does use belongs to an earlier round of the thread; such a line
  can survive the parent-diff when the parent's copy is re-wrapped or
  re-formatted.
- Without the thread parent, a tagged line qualifies only when its label occurs
  as a word above the block.

Such a message keeps its generation-5 treatment of the localized forms. The
generation-5 rules (English, German labels, Chinese) and the short "Original"
forms are not affected: they truncate whether or not tagged answers follow.

### Outlook boundary lines

A blank line is inserted between a non-empty line and a line Outlook draws
above a quoted message (an optional space, then 7 or more underscores or
hyphens) directly below it, so the boundary never reads as part of the
paragraph above it. Through generation 9 the fragment scanner applied the same
rule.

### Parent-diff assist (optional `parent_body`)

Some clients top-post a reply above the entire previous message reproduced with
no `>` markers, no attribution and no header block, so the quote, attribution
and header filters have nothing to act on. When the caller can resolve the
thread parent from `In-Reply-To` and passes its raw body, `strip_parent_content`
removes what provably came from the parent, as an evidence-based backstop. It
runs after the cleanup pass, and the assisted text is adopted only when it
actually dropped a content line — at which point `+parent-diff` is appended to
the method.

Both sides are normalized per line: a leading quote marker is stripped (so a
quoted copy of a parent line compares equal to the bare line), whitespace runs
collapse to single spaces, and blank lines are ignored. Three rules mark a
child line as parent content:

- **Aligned-run rule.** Over the normalized non-blank line sequences (lines
  compared by exact equality), a *common run* is a run of consecutive child
  lines that also occurs as consecutive parent lines. A run has *substance*
  when its lines total at least 10 words, or one of its lines has at least 8
  words. The longest common run (counted in lines) is the *anchor*; among
  runs of that length, the one starting earliest in the child, placed at its
  earliest occurrence in the parent. The anchor's lines are marked when it
  has substance. Before the anchor, a child line is marked when it lies in a
  run with substance that occurs in the parent before the anchor's
  occurrence; after the anchor, likewise against the parent after the
  anchor's occurrence. Within a side, runs match in any order. Short
  coincidental echoes fall below the substance bar and survive — greetings,
  list courtesy phrases, and the author's own sign-off and name, which are
  guaranteed to also sit inside the parent's nested quotes. The anchor is
  normally the quoted previous message; splitting at it keeps a block the
  author repeated on one side of the quote from matching the parent's copy
  on the other side. Through generation 6 this rule used
  `difflib.SequenceMatcher`, whose running time is quadratic in the number of
  lines; generation 7 replaced it (see [generation-7.md](generation-7.md)).
- **Rewrap rule.** Quoting clients re-wrap long paragraphs at a different
  width, which line-level matching misses. Any unmarked child line of at least
  8 words whose normalized text is a substring of the parent's normalized
  word-stream (all parent lines joined by single spaces) is marked too.
- **Continuation rule.** A re-wrapped paragraph's short remainder line falls
  under the rewrap rule's floor and leaks. An unmarked line is marked when an
  adjacent non-blank line is already marked and the two, joined in reading
  order, appear contiguously in the parent's word-stream; this iterates to a
  fixpoint so runs of short lines chain off one long seed. It runs only when
  the first two rules marked at least 3 lines, so an isolated inline-citation
  echo cannot seed a chain into the author's own text.

- **Own-quotation rule.** The first three rules accept a match anywhere in the
  child, so a line the author wrote that repeats a line of the parent is
  marked too: a line kept in a revised paragraph, the new wording of a
  proposed-text edit, a phrase of the parent cited inside a sentence. This
  rule restores such lines when the message shows where its client put the
  quotation. The message's *marked quotation* is the quote-marked lines of
  its body; its *quotation stream* is those lines, normalized (the marker
  stripped), without the blank ones, joined by single spaces. A *block* is a
  maximal run of consecutive non-blank lines of the text, and a *segment* is a
  maximal run of consecutive marked lines within one block. A segment is
  restored when both hold:
  1. the quotation stream contains the segment's lines joined by single
     spaces. The client's copy of the parent is the marked one, so a second
     copy outside it was placed there by the author;
  2. the unmarked lines of the segment's block total more words than its
     marked lines. The block is mostly text the parent does not have, so the
     segment is part of a passage the author wrote. A passage placed whole (a
     citation pasted from the parent, a signature) keeps its marks.

  A message with no marked quotation restores nothing. The lines of restored
  segments are protected: the continuation rule is evaluated again from the
  remaining marks of the first two rules (when at least 3 remain) and never
  marks a protected line, so a line that only chained off a restored line is
  kept too. The rule applies wherever the assist runs with the body at hand:
  against the thread parent and against the HTML oracle's quoted text. It is
  not applied by the localized inline-reply test, which passes no quotation.

- **Marking mode** (from generation 10). The first three rules remove every
  substantial unmarked copy of parent text, which is right when the client
  reproduced the parent without `>` markers. A client that does mark its
  quotation deepens the parent's own quote lines by one level; an unmarked
  copy of parent text outside the marked quotation was then placed by the
  author (a pasted `OLD:` / `NEW:` block, a re-sent paragraph, a citation).
  It applies only in the call against the thread parent from
  `extract_new_text`, not to the HTML oracle's assist and not to a direct
  call of `strip_parent_content`.
  1. *Evidence.* A body line's depth is the number of `>` in its leading
     marker run (optional indentation and leader dots, then `>` with blanks
     between); its content is the rest of the line, whitespace-collapsed. A
     body line counts when its depth is at least 1, its content has at least
     4 words, and the parent's smallest depth for a line with the same content
     is smaller. Marking mode is on when at least 3 body lines count.
  2. *Stop line.* The first attribution (a fixed form, a wrapped form or a
     generic attribution unit) whose next non-blank line exists and is not a
     quote line introduces an unmarked quotation; that next non-blank line is
     the stop line (otherwise the end of the body).
  3. *Protected lines.* Above the stop line, every line of a block (a maximal
     run of non-blank lines) that holds no quote line is protected.
  4. A non-blank, unquoted text line whose normalized form is protected is
     treated like a line restored by the own-quotation rule: it is not removed,
     and the continuation rule is evaluated again without it.
  5. *Many runs.* A run is a maximal sequence of consecutive non-blank text
     lines (blank lines do not end it), each protected by step 4 and not by
     the own-quotation rule, and marked by the first three rules. When at least
     8 runs hold more than 2 lines each, those runs lose the protection: the
     message reproduces the parent unmarked in pieces and answers it point by
     point, and the pieces are the parent's text.

The substring tests of the rewrap, continuation and own-quotation rules give
the same answers as a plain substring search, but are answered from an index
over the parent's word-stream (for the own-quotation rule, the quotation
stream) built once per call. The segments the own-quotation rule tests are
disjoint, so their total length is at most the child's. Marking mode reads
each body once with a dictionary, and its stop-line search tests each line
against a bounded number of neighbours. The whole function runs in time linear
in the size of the child, the parent and the quotation.

The continuation rule is safe against a parent body, which never contains the
author's new text, but not against this message's own HTML quote container,
where a client may wrap the author's inline replies. The HTML oracle therefore
leaves it off.

Marked lines are dropped; the survivors are right-stripped, runs of two or more
blank lines left by removals collapse to one, and blank edges are trimmed.

### HTML as a structural oracle (optional `html_body`)

When the caller passes the decoded `text/html` part, its structure is used in
one of four ways. The resolved method makes the HTML source visible, so the
path a result took stays auditable.

- **(a) HTML-only.** The plain body is missing or blank but an HTML part
  exists: the HTML's novel text becomes the body for the normal stage-1
  pipeline, and the method is prefixed `html-` (for example `html-custom`).
- **(b) Degenerate plain fallback.** The plain body survived but is flattened —
  fewer than 4 non-blank lines, at least one over 400 characters — and the
  HTML's novel text has at least three times as many non-blank lines: path (a)
  is used instead of the mangled plain body.
- **(c) Oracle assist.** The plain body is fine: the normal plain pipeline
  runs, then content provably present in the HTML's quoted text is removed
  (`strip_parent_content`, with the same thresholds as the parent-diff assist
  but without the continuation rule). The assist fires only for a substantial
  removal — at least 3 non-blank lines — and appends `+html-quote`. Its target
  is a whole quoted message leaked unmarked into the plain part, not the odd
  author line that happens to cite the same wording as the thread: a mail
  client wraps interleaved author replies inside the same `gmail_quote`
  container as the quote, and an author quoting a proposed sentence inline
  matches its verbatim copy in a real `<blockquote>`. Both would otherwise cost
  genuine content.
- **(d) Share-sheet container.** The HTML holds an Apple Mail Share-sheet
  container outside every quoted container (`HtmlParts.has_share_container`,
  below) and its novel text is non-blank: path (a) is used instead of the plain
  body, whatever the plain body holds. It is checked after (b) and before the
  plain pipeline, so (c) does not run on such a message. Apple Mail's Share
  sheet (text composed in another app and sent with Share → Mail) wraps the
  author's own text in a `<blockquote type="cite">`, and the plain part
  prefixes every line of it with `>`, so the plain pipeline removes it as
  quoted. From generation 8 (see [generation-8.md](generation-8.md)).

`html_text.py` provides the two functions this rests on, both pure,
standard-library-only (`html.parser`), and guarded so malformed markup never
raises:

- `html_to_text(html)` renders the visible text with block structure preserved
  as newlines (`<br>` and block-level open and close), skipping `script`,
  `style`, `head` and comments, unescaping references, and collapsing
  whitespace.
- `split_html_parts(html)` renders the same text split into three streams,
  and reports whether the document holds a Share-sheet container.
  **Quoted** is everything inside a `<blockquote>`, an element whose `class`
  holds `gmail_attr`, `moz-cite-prefix` or `OutlookMessageHeader`, or an
  element whose `id` is `divRplyFwdMsg` or `appendonsend`; quoted containers
  nest, so a quoted message's own signature counts as quoted. **Signature** is
  everything inside an element whose `class` holds `gmail_signature`,
  `moz-signature` or `Signature` (Outlook matches on the id), but only when it
  is not inside a quoted container. **Novel** is everything else.

  Gmail's `gmail_quote` / `gmail_quote_container` wrapper is treated
  conditionally, because what it holds depends on how the reply was written. A
  wrapper containing no `<blockquote>` is the forward shape and is a quoted
  container. A wrapper containing blockquotes carries the author's inline
  replies as direct children between them, so the wrapper itself is
  transparent: only its blockquotes and its attribution line are quoted.

  **Composed-signature containers** (from generation 10) are the signature
  containers an author can type into: an element whose `class` holds
  `protonmail_signature_block` (Proton Mail), or whose `id` is
  `ms-outlook-mobile-signature` (Outlook mobile) or `composer_signature`
  (Samsung and other Android clients), when it is inside neither a quoted nor
  a signature container. Such a container usually holds a signature, but a
  reply typed into it lands inside it. The text of all such containers in a
  document is rendered as signature text when its non-blank lines number no
  more than the non-blank lines of novel text before the first of them (the
  author's text above the signature), and as novel text otherwise.

  A `<blockquote>` whose parent element carries the class token
  `Apple-Mail-URLShareWrapperClass` is a Share-sheet container, not a quote: it
  contributes no category of its own, so its direct text is novel (or quoted
  or signature, when an enclosing container makes it so). The parent element
  is the innermost element open when the blockquote's start tag is read; an
  element between the wrapper and the blockquote (an unclosed `<p>`, a `<div>`)
  makes the blockquote an ordinary quote. Every blockquote that is a direct
  child of the wrapper is a container; a `<blockquote>` nested inside one is
  quoted as usual. `HtmlParts.has_share_container` is true when such a
  container opens outside every quoted container and outside `head`; a
  Share-sheet message quoted in a later reply therefore does not set it.

### Method labels

`ExtractionResult.method` records the path that produced the text. The base
value is `none` (no body), `custom` (the cleanup pass) or `failed`. The prefix
`html-` marks a text derived from the HTML part (paths (a), (b) and (d)); the
suffix `+parent-diff` marks a text from which the parent-diff assist removed
content, and `+html-quote` one from which the HTML oracle's assist removed
content. Through generation 9 the base value was `erp` (the scanner's output
needed no cleanup), `erp+custom` or `custom-fallback` (the over-strip guard
fired); generation 10 replaces all three with `custom`.

### Part boundaries

`ExtractionResult.part_starts` lists the indices of the lines of `text` that
begin a new *part*: a run of the author's text separated from the previous run
by content stage 1 removed — a quoted block, an attribution line,
parent-diff or HTML-quote content. An inline reply written between
quoted passages has one part per run; a top-posted reply has one part. The
field does not affect `text`, `method` or `status`, and the corpus digest does
not cover it.

The boundaries are found after the text is final, by aligning it with the
body it was derived from: the `text/plain` body, or for the `html-` paths the
full `html_to_text` rendering (novel and quoted text). Both are normalized as
in pre-normalization. Each non-blank line of `text`, in order, is matched to
the first source line at or after the previous match whose stripped form is
equal. A non-blank source line between two consecutive matches is removed
content, and the second line begins a part. Blank lines never begin a part.

When a line of `text` has no match, the alignment is abandoned and the text is
reported as one part. The search only moves forward, so the cost is linear in
the two line counts. Over the 154,394 messages of the origin application's
corpus, generation 9 abandoned the alignment for 72 messages, most of them
because the fragment scanner had joined wrapped lines into a line the body
does not hold; generation 10 abandons it for 10.

### What stage 1 does not do

No word-count "too short" gate is applied. Whether a short extraction is worth
processing is the consuming application's decision, and it applies to the
stage-2 text.

---

## Stage 2 — cleaning for a downstream consumer

`clean_for_scoring` removes the author-typed but formulaic furniture stage 1
kept, and reports the 0-based indices of every non-blank input line it removed
in `CleanResult.ignored_lines` — indices into `extracted_text.split("\n")`, a
fixed contract, so a stage-1 view can grey out exactly what was set aside.

The rationale for removing furniture at all is measured, not assumed. A scored
ablation in the origin application (2026-07-22, messages 42 and 44) showed that
greetings, sign-offs and signature blocks materially mask AI-generated content
in the detector used there: removing a one-line greeting flipped one Mixed
verdict to AI 0.84, and removing a "Regards, / Name" sign-off nearly doubled
another message's AI fraction, zeroing its "AI-assisted" share in both cases.
These fragments are short, formulaic and human-written by construction, so
leaving them in dilutes the signal a classifier is looking for.

Passing the stage-1 `part_starts` as `clean_for_scoring(..., part_starts=...)`
fills `CleanResult.parts`: the surviving lines of each stage-1 part, formatted
as `text` is (right-stripped, blank runs collapsed, blank edges trimmed). A
part that cleaning emptied — a greeting alone, a sign-off alone — is omitted.
Indices outside `1 .. len(lines) - 1` are ignored, and the rest are used in
ascending order without duplicates. Without `part_starts`, `parts` is
`[text]`, or `[]` when `text` is empty. The non-blank lines of the parts, in
order, are exactly those of `text`; `text` and `ignored_lines` do not depend on
`part_starts`.

### Evidence

From generation 10, three optional keyword arguments carry evidence about the
message. Without them (all `None`) only the rules that need no evidence run.

- `author_name` — the sender's display name (the From header's display name,
  or its address when it has none). It enables the sender-name rules (steps
  9 to 12) and the thanks-to-an-addressee exception of step 8.
- `addressee_names` — the display names of the people the message answers or
  is addressed to (for example the thread parent's sender and the To and Cc
  display names). It enables the addressee rule of step 5 and the exception
  of step 8. A name whose tokens equal the sender's is ignored.
- `signature_lines` — the sender's learned signature lines, from
  `learn_signature_lines` (below). They are dropped after the passes converge.

Display names are folded and parsed into tokens by the private module
`_names.py`, and the greeting rules are in `_greetings.py`; see "Names" and
"Opening greetings" below.

### The removal steps of one pass, in order

1. **`-- ` signature delimiter**: that line and everything after it. A custom
   punctuation-rule divider (`========`, as Spark signatures draw) truncates
   the same way when the line above it is blank and a name line follows — the
   two anchors that distinguish a signature divider from a Markdown heading
   underline or an authored section break. Dashes are excluded from that
   divider pattern, because `--` is the RFC 3676 delimiter handled first and
   dashed rules appear as authored separators in digests.
2. **Trailing sign-off debris**: bare links and domains, and "Label: URL"
   lines, after a sign-off. This runs before step 3 because its bare-name
   anchor needs the identifier and contact lines still present as evidence;
   step 3 would erase them.
3. **Individually droppable signature debris**: "~ Name" sign-off lines, titled
   contact lines ("Dr. … \<a@b\>"), corporate contact lines (phone numbers,
   piped address and URL lines), personal-identifier lines (ORCID, LinkedIn,
   D-U-N-S and similar, with up to two prefix words), postal-address lines,
   mailing-list footers, and PGP lines. The cue rules for pipe-separated lines
   require a URL, a phone keyword, a street-suffix word or a postal code; a
   bare email address is deliberately not a cue, because digest tables
   legitimately hold "count | bytes | Name \<a@b\>" rows.
4. **HTML signature hint** (optional). When the caller passes
   `html_signature_text` — the visible text of the message's HTML signature
   container, from `split_html_parts` — any body line whose normalized form
   exactly equals a normalized non-blank line of that hint is dropped
   individually, provided the line carries substance: two or more words, or a
   digit, `@` or URL. The substance guard keeps a lone "Cheers" or "Thanks"
   that also appears in the signature container from being stripped out of the
   body. This catches signature furniture with no `-- ` delimiter and no
   recognizable contact shape, which only the HTML marked as a signature.
5. **Opening greetings**: the first non-blank line is removed when the
   greeting rules accept it (with the line below it and the addressees), and
   then the new first non-blank line is tried once more. At most two greeting
   lines are removed per pass and per text ("Jane:" above "Hi!").
6. **Mobile and client taglines** ("Sent from my iPhone", "Get Outlook for
   iOS", …).
7. **Trailing confidentiality and legal disclaimer paragraphs**. Only trailing
   paragraphs, scanned from the bottom, so a disclaimer-shaped sentence in the
   body is never touched.
8. **One closing sign-off**, looked for on the last two non-blank lines; the
   text is truncated at its first line.
   - The shared forms: a salutation line followed by a name line, or the
     one-line form "Cheers, Jane". The name may carry a title or affiliation
     ("Thi Nguyen-Huu | CEO", "Louis Navarre, UCLouvain"). The salutation list
     is shared with stage 1's sign-off boundary.
   - Then the tolerant forms (from generation 10, no evidence needed). The
     salutation list adds "With best regards", "With thanks", "All best",
     "Thanks a lot", "Thanks in advance", "Thank you very much", "Take care",
     "Later", "Greetings", "R's", "BR", "Brgds", "Thanx", "À bientôt",
     "Cordialmente" and others; two salutations may be joined by `/`, `,`,
     `&`, `+` or "and" ("Thanks and regards", "Regards/Ngā mihi"); the
     punctuation after a salutation may be `,`, `，` (full-width), `、`, `:`, a
     run of `!`, one to three `.`, or none. Under a salutation line the name
     may be a decorated, indented or lower-case single word ("-jane",
     "— Jane Doe", "/j"); the one-line form accepts a decorated or lower-case
     name after any punctuation, a name line after a comma, and a decorated
     name after blanks alone ("Thanks -Jane").
   - Otherwise a last line that is a salutation alone, in the tolerant form
     ("Many thanks!!", "Cheers...").

   With `author_name`, a one-line thanks form ("Thanks", "Thank you", "Many
   thanks", "Thx", "Thanx" + name, with whitespace before the name) on the
   last line is kept when a sentence follows the name ("Thanks, Jane. That
   works.") or when no word of the name matches the sender and a word matches
   an addressee ("Thank you, Joe." to an addressee Joseph). When the last
   non-blank line was exposed as the tail by step 9 or 10 and is a one-line
   form (a salutation, optional punctuation, then text) rather than a shared
   salutation line, step 8 removes nothing unless that text, stripped of
   leading `,` / `，` and trailing ` .!`, is a name line whose words are all
   capitalized and that has no punctuation followed by a blank ("Thanks,
   Jane" goes, "Thanks, Jane. I approve." stays).

Steps 9 to 12 run only with `author_name`. A line is the sender's name when
the name-line test (see "Names" below) accepts it.

9. **Sender-name sign-off above a non-blocking tail.** The first sender-name
   line such that, after the run of attached signature lines below it
   (signature debris, links, short capitalized or contact lines), every line
   up to the end, or up to a postscript ("PS", "P.S.", "PPS"), is
   non-blocking. A non-blocking line is blank, a footnote `[n]`, a link-only
   or "Label: URL" line, signature debris, a client tagline, or a rule line. A
   name line that is the last non-blank line on its own is left to step 10.
   The name line, its attached lines and the salutation line directly above it
   (else a rule line at most one blank line above the name) are removed. Of
   the tail, blank lines, footnotes, link lines (only when the tail holds a
   footnote: they are its references) and the postscript with everything after
   it stay; the rest is removed.
10. **Trailing sender name.** A last non-blank line that is the sender's name,
    or a decorated single initial that is the sender's first initial ("-J",
    "/j"), with a rule line at most one blank line above it.
11. **Sender-name sign-offs anywhere.** Every salutation line (tolerant form)
    whose next non-blank line is the sender's name, with that line, and every
    one-line form whose name is the sender's ("Cheers, Jane" in the middle of
    the text, above a pasted message).
12. **Lone sender-name line.** A sender-name line that is neither the first
    nor the last non-blank line, does not end in `:` or `,` (a speaker label;
    "Michael," greeting a namesake), is not a list item, and whose previous
    non-blank line is not a salutation, not name-shaped (a list of names) and
    does not end in `:` ("Reviewer:"). A name line with a trailing parenthesis
    above another name-shaped line is part of a list too.

Steps 9 and 10 record the non-blank line above what they removed as exposed;
step 8 applies its plain-name condition to it on a later pass.

The passes run to a fixpoint: removing one layer can expose another — a
closing sign-off only becomes the tail once the contact block below it is
dropped, and a salutation becomes the tail once a sender-name rule removes the
name below it. When a pass removes nothing and `signature_lines` are given,
the **learned-line step** runs: every learned line in the *trailing zone* is
dropped. The trailing zone is the lines from the end up to, not including, the
first non-blank line that is neither learned nor at most 4 words long. The
step removes nothing when every non-blank line above the topmost learned line
in the zone is learned as well, so it never empties the text. Lines are
compared in the normalized form `learn_signature_lines` uses. The passes then
resume. The cap is 4 passes, and 8 when learned lines are given; the cap is a
safety bound, not a tuning knob.

The final text is the survivors right-stripped, runs of two or more blank lines
collapsed to one, and blank edges trimmed — the same conventions as stage 1's
tail.

### Opening greetings

The greeting test reads the first non-blank line, of at most 120 characters
after stripping, and the line below it. The rules, tried in this order on the
stripped line:

- **Guard.** An empty line, a line longer than 120 characters, and a line that
  starts with one of `+ > | * # [ (` are not greetings ("++ Jane and Joe."
  adds people to a thread).
- **Terminator.** A trailing run of `-`, `–`, `—` is the terminator `-`;
  otherwise a trailing `,`, `:`, `!` or `.` is; otherwise there is none. The
  body is the line without it.
- **Opening word.** The body may open with a greeting word (the multilingual
  list, extended in generation 10 by "Hiya", "Howdy", "Moin", "Re-" and "Good
  morning/afternoon/evening/day all/everyone/folks"), not followed by a word
  character, then optional blanks and one optional `,`; or, failing that,
  with a thanks phrase ("Thanks", "Thank you", "Many thanks", "Thanks a lot",
  "Thanks again"). The rest of the body is split into words at `,`, `/`,
  `&`, `+`, `;`, " and ", " or " and whitespace, and each word is classified.
- **Base rule.** A line the base greeting pattern matches (a greeting word,
  then either a short run of names ending in `,`, `!` or `.`, or nothing
  else) is a greeting unless the rest holds a sentence word (or a contraction
  such as "I'm", "don't"); then it is the opening of a sentence ("Hi Jane,
  apologies for the delay.") and the whole line is kept, whatever the rules
  below would say.
- **Addressee names** (only with `addressee_names`). One to six words, every
  one a name, name prefix, title, initial or group word, at least one a name
  or name prefix: a greeting ("Jane, Joe:" / "Dr. Doe,"). Not when the body
  opens with a thanks phrase and the terminator is not `,` or `:` ("Thanks
  Jane!" is thanks), and not when there is no opening word, the terminator is
  `.`, `!` or none, and a word is only a name prefix.
- **Greeting word.** An opening greeting word with nothing after it; or with
  one to six words, all name-like (any class but none) and not all acronyms.
- **Group addressees.** No opening word, terminator `,`, `:`, `-` or `!`, one
  to six words, all group words, acronyms or addresses, at least one a group
  word or address, and not a single generic group word ("group", "members",
  "people", "community") on its own ("WG chairs,", "All:").
- **Structural.** No opening word, terminator `,`, `:` or `-`, one to four
  words. With `:` or `-` there must be at least two items between the
  separators ("Inline:" and "Proposal -" are labels). "V/R" (one letter, `/`,
  one letter) is a sign-off. Every word is capitalized, none is 7 or more
  characters ending in "ly", none is in the heading stoplist, and the next
  line is blank, absent, or starts with an uppercase letter ("Jane, Joe,"
  above "Please see…").

Word classes, on the folded word with trailing dots removed, first match wins:
title, name (a token of the addressees), name prefix (an alphabetic word of at
least 3 letters that starts some token), initial (one letter), group word,
filler, address, acronym (2 to 12 characters, alphanumeric, unchanged by
upper-casing, with a letter), capitalized word, none.

Only the addressee-name rule needs evidence. Through generation 9 stage 2
removed one greeting line, matched by the base pattern alone, including a
greeting line that runs into a sentence; generation 10 added the
greeting-word, group-addressee and structural rules, the extended word list,
the second greeting line and the exception that keeps a greeting running into
a sentence.

### Names

`author_name` and `addressee_names` go through the same parsing. Each display
name is reduced in this order:

1. `"` and backquotes become spaces; the edges are stripped of whitespace and
   then of `'`.
2. A name that contains `@` and no space is an address: its local part, with
   `.`, `_`, `+`, `=` and `-` read as spaces, stands for the name. When the
   local part is a generic mailbox and the first label of the domain is
   alphabetic and at least 6 letters long, that label adds tokens
   ("mail@janedoe.example" → `janedoe`).
3. The contents of each parenthesis of 1 to 30 characters with no nested
   parenthesis add tokens (a nickname: "Andrew (Andy) Example"); then every
   `(…)`, `[…]` and `<…>` group is removed. Any whitespace-separated word
   that still holds `@` is replaced by the part before its first `@`, read as
   in step 2 ("Jane Doe jdoe@example.org" → "Jane Doe jdoe").
4. From the first whitespace-delimited word `via` (ASCII letters, any case)
   with whitespace on both sides, the rest of the name is dropped ("Jane Doe
   via Example-List").
5. A name with exactly one comma is read as "Last, First".

The tokens are the folded runs of letters, and the folded runs of letters
joined by `'`, `’` or `-` ("jean-luc", "o'neill"), of at least 2 characters,
that are not stop words; a token with common short forms adds them ("robert"
adds "bob", "rob", …). The words of a single name (its letter runs, in order,
without stop words) give its initials: when there are at least two, the first
letters of all words, of all words in reverse order, and of the first and last
word in both orders, each of at least 2 letters, and the first-and-last pair
on its own. A name that yields no token provides no evidence: a name written
only in a script without letters, or only of stop words.

Names and words are compared folded. Folding has two steps: `str.lower` (the
full lowercase mapping, so `İ` (U+0130) becomes `i` followed by U+0307), then
one pass through a fixed table, in which a replacement is not looked up again
and a character without an entry is kept. The table is a literal in
`_names.py`; no Unicode database is consulted at run time. It holds:

- for every code point of Latin-1 Supplement (U+0080 to U+00FF), Latin
  Extended-A (U+0100 to U+017F), Latin Extended-B (U+0180 to U+024F), Latin
  Extended Additional (U+1E00 to U+1EFF) and U+2026 (`…`) that is unchanged by
  `str.lower`: its compatibility decomposition (NFKD) without combining marks,
  when that differs from the code point (`é` → `e`, `ǆ` → `dz`, `ǿ` → `ø`,
  `…` → `...`, U+00A0 → space);
- every combining diacritical mark (U+0300 to U+036F) → nothing;
- the letters without a decomposition: `ß` → `ss`, `ø` → `o`, `ł` → `l`,
  `đ` → `d`, `æ` → `ae`, `œ` → `oe`, `ı` → `i`, `ð` → `d`, `þ` → `th`.

Characters of other scripts (Greek, Cyrillic, CJK and others) are only
lower-cased.

A line is the sender's name when, stripped, with a leading parenthesis of at
most 30 characters ("(personal) Jane") dropped, then leading decoration, a
role tail ("Jane on behalf of the chairs", "Jane // as an individual", "Jane,
for the authors"), trailing ` .,;:!` and a trailing parenthesis of at most 40
characters that starts after the first character ("Jane (as chair)"), what
remains is 1 to 40 characters and 1 to 4 words. Each word matches the sender,
is an initial or name particle, is a group word (only when the line has "and"
or "&"), or is a capitalized word; at least one word matches the sender, and
at most one capitalized word is unmatched, and only in a group line or beside
two matching words ("Jane Q. Middle Doe").

A word matches the sender when, folded, with `.` stripped from its edges and
`.`, `'`, `’` removed inside, it equals a token or an initials string, is a
prefix or a suffix of a token and at least 3 letters long, or is 3 letters
whose first and last letter are the first-and-last pair ("RjS"). A hyphenated
word matches when every part does. A single letter and a name particle that is
one of the tokens are allowed but do not count as a match.

### Learned signature lines

`learn_signature_lines(texts, *, subjects=None)` takes the stage-1 texts of
one sender's messages and, optionally, their subjects, and returns the lines
the sender repeats at the end of their messages, for `signature_lines`.

- Lines are compared in a normalized form: quote markers removed, whitespace
  runs collapsed to one space, edges stripped, lower-cased with `str.lower`
  and `ß` written `ss`.
- A normalized line is learned when it is among the last 10 non-blank lines of
  at least 3 of the texts and of at least one fifth of them.
- With `subjects`, those texts must also have at least 3 distinct subjects,
  compared without leading "Re:" / "Fwd:"-style prefixes and `[list]` tags,
  whitespace collapsed, lower-cased in the same way. A `None` or empty
  subject is the empty subject; a text with no corresponding subject
  (`subjects` shorter than `texts`) has the empty subject, and subjects
  beyond the last text are ignored.
- Fewer than 5 texts learn nothing.
- A candidate that ends in `:` or is a sentence (at least 3 words ending in
  `.`, `?` or `!`) is the first line of a block where the sender's own words
  would be. It is learned only when, in at least half of its occurrences
  among those last lines, the line just above is another candidate.

The result is the learned lines in their normalized form, sorted. The
function is pure; storing the texts per sender and deciding when to learn
again is the caller's.

### What stage 2 keeps

Anything not on the lists above. In particular:

- Without `author_name`, a bare trailing name with no salutation ("Dino",
  "—Daniel", "Nick") is kept: a name line cannot be told from content without
  knowing the sender's name.
- A signature block separated from the sign-off by a blank line, with no
  `-- ` delimiter and no recognizable contact shape, is kept unless its lines
  are in the HTML signature hint or are learned lines in the trailing zone.
- Greetings and valedictions appearing mid-text (except a sender-name sign-off
  under step 11), author elision markers inserted into a quote (`[...]`,
  `(..)`), URLs pasted mid-prose, and a disclaimer-shaped paragraph that is
  not at the end.

---

## Where the rules are pinned

`fixtures/` holds 28 hand-labeled messages (25 real, 3 synthetic) whose
expected files are the composite of both stages, called without the
generation-10 evidence arguments. `fixtures/README.md` records, per fixture,
the behavior it pins. `conformance/digest.json` additionally pins the exact
bytes of the whole corpus as a single SHA-256 (checked by
`python/tests/test_extraction_version.py`); see [versioning.md](versioning.md).

The rules that need evidence (`author_name`, `addressee_names`,
`signature_lines`, `learn_signature_lines`) and most generation-10 stage-1
rules are not exercised by any fixture. They are pinned by the unit tests in
`python/tests/test_extraction.py`, `python/tests/test_cleaning.py` and
`python/tests/test_html_text.py`, and through them by the recorded
call-level vectors. `python/tests/test_port_hazards.py` pins the
Python-specific semantics the rules rely on, and
`python/tests/test_complexity.py` holds adversarial inputs that must complete
in linear time.

`conformance/` holds the reference outputs and call-level vectors that the
TypeScript and Rust ports are tested against; see [ports.md](ports.md).
