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

The strategy is the vendored fragment scanner as the primary extractor,
followed by a small custom cleanup pass. "ERP" below refers to that scanner,
trimmed from `email-reply-parser` into `_fragments.py`. Talon is not used; the
comparison that settled the choice is recorded in the origin application's
`docs/findings/extraction.md`.

### Pre-normalization

Before anything else, `normalize_body` converts CRLF and CR to LF and removes
the byte-order mark and zero-width characters (ZWSP, ZWNJ, ZWJ, word joiner).
This alone lets the scanner recognize a leading `>` that a BOM would otherwise
hide.

### ERP fragments, signatures kept

The primary path joins every scanned fragment that is not marked `quoted` —
including fragments the scan treats as a signature. A signature block therefore
survives stage 1, and stage 2 removes it. This is the one place stage 1 keeps
more than upstream `email-reply-parser`'s `parse_reply` would, which hides
signature fragments outright, and it is why the scanner is consumed at the
fragment level rather than through that entry point.

### The custom cleanup pass

`custom_clean` applies composable steps in this order, and the order matters:

1. Truncate at a forwarded/quote-header block.
2. Truncate at an "Original message" divider.
3. Truncate at a sign-off boundary — which must run while the attribution
   evidence that proves the boundary is still present.
4. Drop attribution lines, including wrapped forms.
5. Drop quoted lines.

Trailing whitespace is then trimmed per line and blank edge lines are removed;
the first content line's indentation is preserved, because it is significant in
digest tables.

### Attribution lines

Removed forms: the English "On \<date\>, X wrote:" and "Name \<a@b\> wrote:",
the German "Am \<date\> schrieb X:", the French and Spanish terminators, and
the Japanese "\<date\>、X のメール:" form anchored on a leading year so prose
mentioning someone's mail never matches. The English pattern requires no word
boundary before "wrote", because the scanner unwraps a line-wrapped attribution
by deleting the newline, which can glue an address straight onto it
("…example.comwrote:").

A form wrapped across two or three lines is removed as a unit: a start line
("On …", "Am …", "Le …", "El …", a leading year) whose terminator lands on one
of the next two lines with no blank line in between.

The pattern lists are structured per language so more languages are easy to
add.

### Indented quote blocks

A quoted line is optional indentation, an optional run of leader dots (the
`...>` form that survives `format=flowed` rewrapping), then one or more `>`.
The scanner misses these when the marker is not in column 0; the custom pass
drops them.

### Sign-off boundary

A salutation line ("Best," / "Cheers" / "Thanks," …) plus a name line, directly
followed by an attribution line, ends the new text. Everything after the name
is the quoted message, even when the client added no `>` markers — the
Gmail-style fully top-posted reply, which is also the typical shape of
AI-generated mail.

All three parts are required. A bare mid-thread "Thanks." never truncates,
because the name line is the strong signal; a sign-off at the true end of the
message has nothing after it to prove quoted material follows; a sign-off
followed by authored content (a "Ps." block) does not truncate either.
Interleaved replies are safe, because there the attribution precedes
`>`-quoted text and the sign-off comes last.

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
left dangling directly above it (the shape Alibaba Mail draws).

Localized label sets are recognized: German (`Gesendet`, `An`, `Betreff`) and
Chinese (发件人 / 发送时间 / 收件人 / 抄送 / 主题, with full-width colons and
the ideographic-space padding 主　题, as produced by Alibaba Mail and Chinese
Outlook).

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

This truncation runs on the intact body **before** the scan, as well as inside
`custom_clean`. Re-joining the scanner's edge-stripped fragments can glue a
signature line (`Tel:` / `Email:`) directly above the block's `From:`, which
would otherwise disguise the block as pasted header evidence; on the intact
body the blank line above the `From:` is still there, so the finder sees the
true shape.

### "Original message" dividers

The dashed `-------- Original message --------` divider (also
`-----Original Message-----`, "Forwarded message", and the Chinese 邮件原件 and
原始邮件 forms) and everything after it is dropped. Dashes are required on both
sides, so prose mentioning an original message never matches.

The divider is matched anywhere in a line rather than only at its start,
because HTML-to-text conversion sometimes flattens a whole quoted message onto
one line and glues the divider to the end of the author's text. In that case
the author's prefix before the divider is kept.

### Over-strip guard

Dashed-separator digest bodies make the scanner treat a `---` rule as a
signature boundary and truncate. The guard compares what the primary path kept
with what the body plainly holds: when fewer than 60% of the body's plainly
unquoted content lines survive, the primary output is discarded and the custom
cleanup runs on the whole body instead, producing method `custom-fallback`.

The denominator mirrors what stage 1 now keeps. It excludes quoted lines,
attribution lines, and everything after a quote-header block, an "Original
message" divider or a sign-off boundary — but it no longer excludes the
signature region, greetings or sign-offs, because stage 1 keeps those. Mirrored
this way, the guard does not misfire on ordinary signed mail while still
catching the digests.

### Parent-diff assist (optional `parent_body`)

Some clients top-post a reply above the entire previous message reproduced with
no `>` markers, no attribution and no header block, so the quote, attribution
and header filters have nothing to act on. When the caller can resolve the
thread parent from `In-Reply-To` and passes its raw body, `strip_parent_content`
removes what provably came from the parent, using `difflib` as an
evidence-based backstop. It runs after the over-strip guard has resolved the
chosen text, so it never perturbs the guard's denominator, and the assisted
text is adopted only when it actually dropped a content line — at which point
`+parent-diff` is appended to the method.

Both sides are normalized per line: a leading quote marker is stripped (so a
quoted copy of a parent line compares equal to the bare line), whitespace runs
collapse to single spaces, and blank lines are ignored. Three rules mark a
child line as parent content:

- **Aligned-run rule.** `difflib.SequenceMatcher` (with `autojunk=False`) finds
  matching blocks over the normalized non-blank line sequences. A block marks
  its child lines only when it carries substance: at least 10 words in total,
  or at least one line of at least 8 words. Short coincidental echoes fall
  below both and survive — greetings, list courtesy phrases, and the author's
  own sign-off and name, which are guaranteed to also sit inside the parent's
  nested quotes.
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

The continuation rule is safe against a parent body, which never contains the
author's new text, but not against this message's own HTML quote container,
where a client may wrap the author's inline replies. The HTML oracle therefore
leaves it off.

Marked lines are dropped; the survivors are right-stripped, runs of two or more
blank lines left by removals collapse to one, and blank edges are trimmed.

### HTML as a structural oracle (optional `html_body`)

When the caller passes the decoded `text/html` part, its structure is used in
one of three ways. The resolved method makes the HTML source visible, so the
path a result took stays auditable.

- **(a) HTML-only.** The plain body is missing or blank but an HTML part
  exists: the HTML's novel text becomes the body for the normal stage-1
  pipeline, and the method is prefixed `html-` (for example
  `html-erp+custom`).
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

`html_text.py` provides the two functions this rests on, both pure,
standard-library-only (`html.parser`), and guarded so malformed markup never
raises:

- `html_to_text(html)` renders the visible text with block structure preserved
  as newlines (`<br>` and block-level open and close), skipping `script`,
  `style`, `head` and comments, unescaping references, and collapsing
  whitespace.
- `split_html_parts(html)` renders the same text split into three streams.
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

### The removal steps, in order

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
4. **One opening greeting line**, multilingual.
5. **Mobile and client taglines** ("Sent from my iPhone", "Get Outlook for
   iOS", …).
6. **Trailing confidentiality and legal disclaimer paragraphs**. Only trailing
   paragraphs, scanned from the bottom, so a disclaimer-shaped sentence in the
   body is never touched.
7. **One closing sign-off**: a salutation with a name, where the name may carry
   a title or affiliation ("Thi Nguyen-Huu | CEO", "Louis Navarre,
   UCLouvain"); the one-line form; or a bare trailing salutation. The
   salutation list is shared with stage 1's sign-off boundary.
8. **HTML signature hint** (optional). When the caller passes
   `html_signature_text` — the visible text of the message's HTML signature
   container, from `split_html_parts` — any body line whose normalized form
   exactly equals a normalized non-blank line of that hint is dropped
   individually, provided the line carries substance: two or more words, or a
   digit, `@` or URL. The substance guard keeps a lone "Cheers" or "Thanks"
   that also appears in the signature container from being stripped out of the
   body. This catches signature furniture with no `-- ` delimiter and no
   recognizable contact shape, which only the HTML marked as a signature.

Steps 5 and 6 run before step 7 so that a sign-off with boilerplate below it
("Regards, / Bob / This email is confidential…") is exposed as the true tail
and removed in the same pass.

The whole sequence runs to a fixpoint, bounded at four passes: removing one
layer can expose another, since a closing sign-off only becomes the tail once
the contact block below it is dropped. The cap is a safety bound, not a tuning
knob.

The final text is the survivors right-stripped, runs of two or more blank lines
collapsed to one, and blank edges trimmed — the same conventions as stage 1's
tail.

### What stage 2 keeps

Anything not on the list above, including bare trailing name sign-offs with no
salutation ("Dino", "—Daniel", "Nick"), author elision markers inserted into a
quote (`[...]`, `(..)`), greetings and valedictions appearing mid-text, and
URLs pasted mid-prose. These are not distinguishable from content with enough
confidence, and the hand-labeled corpus keeps them.

---

## Where the rules are pinned

`tests/fixtures` holds 24 hand-labeled real messages whose expected files are
the composite of both stages. `tests/fixtures/README.md` records, per fixture,
the behavior it pins and where raw `email-reply-parser` diverges from the
ground truth — those divergences are exactly what the custom pass and the
over-strip guard exist to fix. `tests/test_extraction_version.py` additionally
pins the exact bytes of the whole corpus as a single SHA-256; see
[versioning.md](versioning.md).
