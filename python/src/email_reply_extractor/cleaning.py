"""Cleaning: prepare extracted new text for scoring by an AI-content detector.

This is **stage 2** of the two-stage pipeline. :mod:`extraction` (stage 1)
recovers the author's *novel content* — everything they wrote that is not quoted
or reproduced from elsewhere — but it deliberately keeps the author-typed but
formulaic *furniture*: greetings, sign-offs, signature blocks, mailing-list
footers, mobile-client taglines. This module removes that furniture so only
substantive prose reaches the detector, and — crucially — **reports which input
lines it removed** (:class:`CleanResult.ignored_lines`) so a consumer can grey
them out when displaying the extracted text.

Why furniture is removed before scoring
---------------------------------------
A scored ablation in the origin application (2026-07-22) showed that greetings,
sign-offs and signature blocks materially **mask AI-generated content** in its
Pangram AI-detection check: removing a one-line greeting flipped one Mixed verdict to AI 0.84,
and removing a "Regards, / Name" sign-off nearly doubled another message's AI
fraction, zeroing its "AI-assisted" share in both cases. These fragments are
short, formulaic and human-written by construction, so leaving them in dilutes
the very signal the detector is looking for. They are therefore stripped from the
text that is scored — but only from the *scored* text: stage 1 still preserves
them, and the detail view can show the author exactly what was set aside.

Evidence
--------
Three optional arguments of :func:`clean_for_scoring` carry evidence about the
message. Without them (all ``None``) only the rules that need no evidence run.

- ``author_name``: the sender's display name. It enables the sender-name rules
  (steps 9-12) and the thanks-to-an-addressee exception of step 8.
- ``addressee_names``: the display names of the people the message answers or
  is addressed to. It enables the addressee rule of step 5 and the exception of
  step 8. A name whose tokens equal the sender's is ignored.
- ``signature_lines``: the sender's learned signature lines
  (:func:`learn_signature_lines`), dropped after the passes converge.

Display names are folded and parsed into tokens, and a line is matched against
the sender's name, by :mod:`._names`; its docstring defines the folding, the
tokens, the initials and the word matches. The greeting rules are in
:mod:`._greetings`.

The removal steps of one pass, in order (order matters; see the notes):

1. ``-- `` signature delimiter: that line and everything after it. A custom
   punctuation-rule divider ("========" in Spark signatures) truncates the same
   way when the line above it is blank and a name line follows — the two anchors
   that distinguish a signature divider from a Markdown heading underline or an
   authored section break.
2. **Trailing sign-off debris** (bare links/domains and "Label: URL" lines after
   a sign-off, with the two-anchor rules) — run *before* step 3 because its
   bare-name anchor needs the identifier/contact lines still present as
   evidence; step 3 would erase them.
3. **Individually droppable signature debris**: "~ Name" sign-offs, titled
   contact lines, corporate contact lines (phone / piped address+URL),
   personal-identifier lines (ORCID/LinkedIn/…/D-U-N-S, with up to two prefix
   words — "VSO BLOG: …"), postal-address lines ("Tokyo Office: … 150-0021 …"),
   mailing-list footers, PGP lines.
4. **HTML signature hint** (optional): when the caller passes
   ``html_signature_text`` — the visible text of the message's HTML signature
   container (:func:`~html_text.split_html_parts`) — any body line whose
   normalized form exactly equals a normalized non-blank line of that hint is
   dropped individually, *provided* the line carries substance (≥ 2 words, or a
   digit / ``@`` / URL). The substance guard keeps a lone "Cheers"/"Thanks" that
   also appears in the signature container from being stripped out of the body.
   This catches signature furniture with no ``-- `` delimiter and no recognizable
   contact shape that only the HTML marked as a signature.
5. **Opening greetings**: the first non-blank line is removed when
   :func:`~._greetings.is_opening_greeting` accepts it (with the line below it
   and the addressees), and then the new first non-blank line is tried once
   more. At most two greeting lines are removed per pass and per text ("Jane:"
   above "Hi!"). The greeting word lists (multilingual) and the rules — the
   base rule, the addressee-name rule, the greeting-word rules, the
   group-addressee rule, the structural rule with its stoplist, and the
   exception that keeps a greeting running into a sentence — are specified in
   :mod:`._greetings`.
6. Mobile/client taglines ("Sent from my iPhone", "Get Outlook for iOS", …).
7. **Trailing confidentiality/legal disclaimer paragraphs** ("This email and any
   attachments are confidential…") — trailing paragraphs only, scanned from the
   bottom, so a disclaimer-shaped sentence in the body is never touched.
8. **One closing sign-off** (:func:`_strip_closing_signoff`), looked for on the
   last two non-blank lines; the text is truncated at its first line.

   - The shared forms (:func:`~.extraction._signoff_name_index`): a salutation
     line followed by a name line, or the one-line form "Cheers, Jane"; the
     name may carry a title or affiliation ("Jane Doe | CEO", "Jane Doe,
     Example University"). Multilingual salutations are shared with stage 1's
     boundary detection in :mod:`extraction`.
   - Then the tolerant forms (:func:`_tolerant_signoff_name_index`). The
     salutation list adds :data:`_EXTRA_SALUTATIONS` ("With best regards",
     "Take care", "Later", "R's", "BR", "À bientôt", …), two salutations may be
     joined by ``/``, ``,``, ``&``, ``+`` or "and" ("Thanks and regards"), and
     the punctuation after a salutation may be ``,``, ``，`` (full-width),
     ``、``, ``:``, a run of ``!``, one to three ``.``, or none. Under a
     salutation line the name may be a decorated, indented or lower-case single
     word ("-jane", "— Jane Doe", "/j"); the one-line form accepts a decorated
     or lower-case name after any punctuation, a name line after a comma, and
     a decorated name after blanks alone ("Thanks -Jane").
   - Otherwise a last line that is a salutation alone, in the tolerant form
     ("Many thanks!!", "Cheers...").

   With ``author_name``, a one-line thanks form ("Thanks", "Thank you", "Many
   thanks", "Thx", "Thanx" + name, with whitespace before the name) on the last
   line is kept when a sentence follows the name ("Thanks, Jane. That works.")
   or when no word of the name matches the sender and a word matches an
   addressee ("Thank you, Joe." to an addressee Joseph). When the last
   non-blank line was exposed as the tail by step 9 or 10 and is a one-line
   form (a salutation, optional punctuation, then text) rather than a shared
   salutation line, step 8 removes nothing unless that text, stripped of
   leading ``,``/``，`` and trailing `` .!``, is a name line whose words are all
   capitalized and that has no punctuation followed by a blank ("Thanks,
   Jane" goes, "Thanks, Jane. I approve." stays).

Steps 9 to 12 run only with ``author_name``; a line matches when
:func:`~._names.is_author_name_line` accepts it.

9. **Sender-name sign-off above a non-blocking tail**
   (:func:`_drop_signoff_above_tail`): the first sender-name line such that,
   after the run of attached signature lines below it
   (:func:`_is_attached_signature_line`: debris, links, short capitalized or
   contact lines), every line up to the end, or up to a postscript ("PS",
   "P.S.", "PPS"), is non-blocking (:func:`_is_nonblocking_tail_line`: blank,
   a footnote ``[n]``, a link-only or "Label: URL" line, signature debris, a
   client tagline, a rule line). A name line that is the last non-blank line
   on its own is left to step 10. The name line, its attached lines and the
   salutation line directly above it (else a rule line at most one blank line
   above the name) are removed. Of the tail, blank lines, footnotes, link lines
   (only when the tail holds a footnote: they are its references) and the
   postscript with everything after it stay; the rest is removed.
10. **Trailing sender name**: a last non-blank line that is the sender's name,
    or a decorated single initial that is the sender's first initial ("-J",
    "/j"), with a rule line at most one blank line above it.
11. **Sender-name sign-offs anywhere**: every salutation line (tolerant form)
    whose next non-blank line is the sender's name, with that line, and every
    one-line form whose name is the sender's ("Cheers, Jane" in the middle of
    the text, above a pasted message).
12. **Lone sender-name line** (:func:`_drop_lone_author_name`): a sender-name
    line that is neither the first nor the last non-blank line, does not end in
    ``:`` or ``,`` (a speaker label; "Michael," greeting a namesake), is not a
    list item, and whose previous non-blank line is not a salutation, not
    name-shaped (a list of names) and does not end in ``:`` ("Reviewer:"); a name
    line with a trailing parenthesis above another name-shaped line is part of a
    list too.

Steps 9 and 10 record the non-blank line above what they removed as exposed;
step 8 applies its plain-name condition to it on a later pass.

The passes run to a **fixpoint** (bounded): removing one layer can expose
another — a closing sign-off only becomes the tail once the contact block below
it is dropped, and a salutation becomes the tail once a sender-name rule
removes the name below it. When a pass removes nothing and ``signature_lines``
are given, the **learned-line step** (:func:`_drop_learned_signature_lines`)
runs: every learned line in the trailing zone (the lines from the end up to,
not including, the first non-blank line that is neither learned nor at most 4
words long) is dropped, unless every non-blank line above the topmost of them
is learned as well (the step never empties the text). Lines are compared normalized and lowercased. The passes then
resume. The cap is :data:`_MAX_CLEAN_PASSES` passes, twice that with learned
lines.

The final ``text`` is the survivors ``rstrip``-ed, runs of 2+ blank lines
collapsed to one, and blank edges trimmed — the same conventions as stage 1's
tail. The shared sign-off predicates live in :mod:`extraction` (stage 1 needs
them for its sign-off *boundary*); this module imports them, never the reverse.

Any change here that can alter the cleaned text — a whitespace-only difference
included — requires incrementing
:data:`~email_reply_extractor.extraction.EXTRACTION_VERSION`, because that stamp
is how stored extractions are recognized as derived by an older routine.
"""

from __future__ import annotations

import re
from bisect import bisect_right
from collections.abc import Sequence
from dataclasses import dataclass, field

from ._greetings import is_opening_greeting
from ._names import (
    LOOSE_NAME_TOKEN_RE,
    NAME_DECORATION,
    Names,
    is_author_initial_line,
    is_author_name_line,
    matches_addressee,
    matches_author,
    parse_addressee_names,
    parse_author_name,
)
from .extraction import (
    _dollar_line,
    _ends_with_bracketed_address,
    _is_signoff_name_like,
    _normalize_line_for_diff,
    _SIGNOFF_LINE_RE,
    _SIGNOFF_SALUTATIONS,
    _signoff_name_index,
    is_signoff_name_line,
)

# --- result -------------------------------------------------------------------


@dataclass(frozen=True)
class CleanResult:
    """The outcome of :func:`clean_for_scoring` on one stage-1 extraction.

    - ``text``: the furniture-free text that is sent to the detector.
    - ``ignored_lines``: 0-based indices into the *input* text's ``split("\\n")``
      of every **non-blank** line the clean pass removed. A consumer can use
      these to grey out the corresponding lines of the extracted text; the
      contract is fixed (indices into ``extracted_text.split("\\n")``).
    - ``parts``: ``text`` divided at the stage-1 part boundaries passed as
      ``part_starts`` (see :attr:`~extraction.ExtractionResult.part_starts`).
      Each part holds the surviving lines of one stage-1 part, formatted as
      ``text`` is; a part that cleaning emptied is omitted. Without
      ``part_starts`` it is ``[text]``, or ``[]`` when ``text`` is empty. The
      non-blank lines of the parts, in order, are exactly those of ``text``.
    """

    text: str
    ignored_lines: list[int]
    parts: list[str] = field(default_factory=list)


# --- signature delimiter ------------------------------------------------------

# The RFC 3676 signature delimiter, "-- " (trailing space often stripped by
# clients). Deliberately does NOT match "--eh." or "-markku".
_SIG_DELIM_RE = re.compile(r"^--[ \t]*$")


def is_signature_delimiter(line: str) -> bool:
    """True for the ``-- `` signature delimiter (not "--eh." or "-markku")."""
    return bool(_SIG_DELIM_RE.match(line))


# A punctuation-rule line some clients let users draw above a custom signature
# ("========" in Spark signatures). Dashes are deliberately excluded: ``--`` is
# the RFC 3676 delimiter handled above, and dashed rules appear as authored
# separators in digests.
_CUSTOM_SIG_DIVIDER_RE = re.compile(r"^[ \t]*[=~*#_]{3,}[ \t]*$")


# --- individually droppable signature-debris lines ----------------------------

# A "~ Name" sign-off line.
_TILDE_SIGNOFF_RE = re.compile(r"^[ \t]*~[ \t]+\S")

# A titled personal contact line that is only a name and a bracketed email,
# e.g. "Dr. Markku-Juhani O. Saarinen <mjos@iki.fi>". The leading honorific keeps
# this from matching pasted header lines like "From: Name <a@b>". The full rule
# is ``^[ \t]*(?:Dr|…|Prof\.-Dr)\.?[ \t]+\S.*<[^>]+@[^>]+>[ \t]*$``; only the
# prefix up to ``\S`` is a regex (:func:`_is_contact_line` decides the rest),
# because the engine's backtracking over ``.*<[^>]+@[^>]+>`` is cubic on a line
# of "<a@" repeated.
_CONTACT_PREFIX_RE = re.compile(
    r"^[ \t]*(?:Dr|Prof|Mr|Mrs|Ms|Miss|PhD|Herr|Frau|Sir|Dame|Prof\.-Dr)\.?[ \t]+\S"
)
#: The tail of the contact rule, applied with ``match`` just after the ``>``.
_CONTACT_TAIL_RE = re.compile(r"[ \t]*$")


def _is_contact_line(line: str) -> bool:
    """True for a titled contact line (the full rule above), in linear time.

    The prefix regex has a single way to match: ``[ \\t]+\\S`` must take the
    whole blank run, and no honorific followed by an optional ``.`` and a blank
    is a prefix of another one followed the same way. The rest is
    :func:`~.extraction._ends_with_bracketed_address` from just after the
    ``\\S``.
    """
    prefix = _CONTACT_PREFIX_RE.match(line)
    return prefix is not None and _ends_with_bracketed_address(line, prefix.end(), _CONTACT_TAIL_RE)


# A telephony contact line: a phone keyword directly followed by a number, e.g.
# "Tel: +1 905.502.7000 x 3288  |  Toll Free: 888.879.5879". The number tail
# (5+ chars of digits/punctuation) keeps prose like "the cell 5 in row 2" safe.
_PHONE_LINE_RE = re.compile(
    r"\b(?:tel|telephone|phone|mobile|cell|fax|toll[ -]?free)\b[.:]?[ \t]*[+(]?\d[\d \t().x/-]{5,}",
    re.IGNORECASE,
)

# A cue that a pipe-separated line is corporate-signature contact/address
# furniture rather than prose or a digest table row: a URL, a phone keyword, a
# street-suffix word, or a postal code (Canadian "M9W 4Y8" / US "City, ST
# 12345"). A bare email is deliberately NOT a cue — digest tables legitimately
# hold "count | bytes | Name <a@b>" rows that must be kept. Extend the
# street-suffix / postal alternations for more locales as they show up.
_CONTACT_CUE_RE = re.compile(
    r"\bwww\.|https?://"
    r"|\b(?:tel|telephone|phone|mobile|cell|fax|toll[ -]?free)\b"
    r"|\b(?:blvd|boulevard|street|ave|avenue|suite|floor|road|drive|lane)\b"
    r"|\b[A-Za-z]\d[A-Za-z][ \t]?\d[A-Za-z]\d\b"
    r"|,[ \t]*[A-Z]{2}[ \t]+\d{5}\b",
    re.IGNORECASE,
)

# A personal-identifier contact line: an identity/social/contact keyword
# followed by a colon and a handle/id ("ORCID: 0009-0007-4602-5624",
# "Email: a@b"), or by a bracketed URL and nothing else ("LinkedIn
# <https://www.linkedin.com/in/…>" — the plain-text rendering of a hyperlink).
# Up to two short capitalized prefix words are allowed before the keyword
# ("VSO BLOG: https://…", "Company Website: …"); the (?-i:…) group keeps the
# prefix requirement case-sensitive inside an otherwise IGNORECASE pattern, so
# running prose ("you can reach me by email: …") never qualifies. The colon /
# lone-bracketed-URL requirement keeps prose ("Happy to upload these as GitHub
# issues") safe. Extend the keyword alternation as new services show up.
_IDENTIFIER_LINE_RE = re.compile(
    r"^[ \t]*(?:(?-i:[A-Z])[\w.()&-]*[ \t]+){0,2}"
    r"(?:ORCID|LinkedIn|GitHub|GitLab|Mastodon|Bluesky|Twitter|Skype|Signal"
    r"|Telegram|WhatsApp|WeChat|Matrix|IRC|Website?|Homepage|Blog|E-?mail|D-?U-?N-?S)"
    r"[ \t]*(?::[ \t]*\S|<https?://[^>]+>[ \t]*$)",
    re.IGNORECASE,
)

# A postal-address contact line: an "Office"/"Address"-labelled line (with up to
# two prefix words — "Tokyo Office: …") whose value carries a digit (street
# number or postal code). The digit requirement keeps prose like "Address: see
# the wiki" safe. No ``[ \t]*`` follows the colon: ``[^\n]*`` already matches the
# blanks, and two adjacent runs backtrack quadratically when no digit follows.
_ADDRESS_LINE_RE = re.compile(
    r"^[ \t]*(?:\S+[ \t]+){0,2}(?:Office|Address)[ \t]*:[^\n]*\d",
    re.IGNORECASE,
)

# Mailing-list footer debris. The full rule is
# ``^[ \t]*(?:_{5,}|.*\bmailing list\b.*--.*|To unsubscribe\b.*)[ \t]*$``
# (IGNORECASE); :func:`_is_list_footer` decides the middle alternative without
# the regex engine, whose backtracking over ``.*….*--.*`` is quadratic on a
# line of "mailing list" repeated.
_LIST_FOOTER_RE = re.compile(
    r"^[ \t]*(?:_{5,}|To unsubscribe\b.*)[ \t]*$",
    re.IGNORECASE,
)
_MAILING_LIST_RE = re.compile(r"\bmailing list\b", re.IGNORECASE)


def _is_list_footer(line: str) -> bool:
    """True for a mailing-list footer line (the full rule above), in linear time.

    No part of the rule can match ``\\n``, so it can only match within
    :func:`~.extraction._dollar_line`'s span; checking that first also keeps
    the ``.*[ \\t]*$`` of the regex from backtracking over a line it cannot
    match. Within the span, ``.*`` can skip anything, so the middle
    alternative holds exactly when a ``--`` follows the end of the first
    "mailing list".
    """
    span = _dollar_line(line)
    if span is None:
        return False
    if _LIST_FOOTER_RE.match(line):
        return True
    found = _MAILING_LIST_RE.search(span)
    return found is not None and span.find("--", found.end()) >= 0


# A PGP armor / key line.
_PGP_RE = re.compile(
    r"^[ \t]*(?:-----(?:BEGIN|END) PGP|PGP key-(?:id|fingerprint)\b)",
    re.IGNORECASE,
)


def _is_contact_signature_line(line: str) -> bool:
    """True for a corporate-signature contact line, dropped individually.

    Two shapes: a telephony line ("Tel: +1 905.502.7000 x 3288"), and a
    pipe-separated contact/address line where at least one contact cue (URL,
    phone keyword, street suffix, postal code) marks it as signature furniture.
    A pipe alone is not enough — a "Name | Title" sign-off line and digest
    table rows ("2 (16.7%) | 11728 ( 3.3%) | Name <a@b>") survive.
    """
    if _PHONE_LINE_RE.search(line):
        return True
    return "|" in line and bool(_CONTACT_CUE_RE.search(line))


def _is_droppable_signature_line(line: str) -> bool:
    """True for a line that is itself signature debris but not a text boundary.

    These are dropped individually (not "everything after"), so authored content
    that happens to follow — a "Ps." block after a "~ Name" sign-off — survives.
    """
    return bool(
        _TILDE_SIGNOFF_RE.match(line)
        or _is_contact_line(line)
        or _IDENTIFIER_LINE_RE.match(line)
        or _ADDRESS_LINE_RE.match(line)
        or _is_list_footer(line)
        or _PGP_RE.match(line)
        or _is_contact_signature_line(line)
    )


# --- trailing sign-off debris -------------------------------------------------

# A line that is nothing but a link, bare domain, or email address — the tail
# furniture of a personal signature ("kghoshworkid.github.io"). Only consulted
# *after* a sign-off by :func:`strip_trailing_signoff_debris`; a URL the author
# pastes on its own line mid-prose is never touched.
_LINK_ONLY_LINE_RE = re.compile(
    r"^[ \t]*(?:"
    r"https?://\S+"
    r"|www\.\S+"
    r"|[\w.+-]+@[\w-]+(?:\.[\w-]+)+"
    r"|(?:[\w-]+\.){1,6}[A-Za-z]{2,24}(?:/\S*)?"
    r")[ \t]*$"
)

# "Label: URL" — a short free-form label, a colon, then only a URL ("Overview:
# https://example.org/overview/", "VCP Explorer (Demo): https://…"). Like
# :data:`_LINK_ONLY_LINE_RE`, only consulted inside a sign-off's all-debris
# tail — an authored "Draft: https://…" line mid-prose is never touched.
_LABELED_LINK_LINE_RE = re.compile(r"^[ \t]*[^:\n]{1,40}:[ \t]*(?:https?://|www\.)\S+[ \t]*$")


def strip_trailing_signoff_debris(lines: list[str]) -> list[str]:
    """Drop personal-signature furniture trailing a closing sign-off.

    When a sign-off is followed only by contact debris to the end of the
    message — identifier lines ("ORCID: …"), bare links/domains/emails — that
    tail is dropped and the sign-off itself kept. Requiring *every* remaining
    line to be debris means a "Ps." block or any authored prose after a
    sign-off keeps the whole tail intact.

    Two anchors, with different bars for the tail:

    - a full sign-off (salutation + name): any all-debris tail qualifies, so
      "Thanks, / Arun Thallapelly / OmniArx.ai" loses the bare domain;
    - a bare name line (no salutation — "Thanks, and looking forward. /
      Kunal Ghosh / ORCID: … / …"): the tail must also contain at least one
      identifier/contact line, so an authored ending of a name followed by a
      pasted link is never mistaken for a signature.
    """
    n = len(lines)
    # Facts about every tail lines[p:], built on the first anchor in one
    # backward pass, so each anchor is decided in constant time (rebuilding and
    # reclassifying the tail for every anchor is quadratic):
    # (has a non-blank line, every non-blank line is debris, some non-blank
    # line is droppable).
    tails: list[tuple[bool, bool, bool]] | None = None
    for i in range(n):
        name_idx = _signoff_name_index(lines, i)
        need_droppable = False
        if name_idx is None:
            if not is_signoff_name_line(lines[i]):
                continue
            name_idx = i
            need_droppable = True
        if tails is None:
            tails = _tail_debris_table(lines)
        nonblank, all_debris, any_droppable = tails[name_idx + 1]
        if not nonblank:
            continue
        if all_debris and (not need_droppable or any_droppable):
            return lines[: name_idx + 1]
    return lines


def _tail_debris_table(lines: list[str]) -> list[tuple[bool, bool, bool]]:
    """For every ``p`` (``0 <= p <= len(lines)``), facts about the non-blank
    lines of ``lines[p:]``: whether there is one, whether every one is debris
    (droppable, link-only or labeled-link), and whether one is droppable.
    """
    table = [(False, True, False)] * (len(lines) + 1)
    for p in range(len(lines) - 1, -1, -1):
        line = lines[p]
        nonblank, all_debris, any_droppable = table[p + 1]
        if line.strip():
            droppable = _is_droppable_signature_line(line)
            debris = (
                droppable
                or _LINK_ONLY_LINE_RE.match(line) is not None
                or _LABELED_LINK_LINE_RE.match(line) is not None
            )
            table[p] = (True, all_debris and debris, any_droppable or droppable)
        else:
            table[p] = table[p + 1]
    return table


# --- salutations ------------------------------------------------------------------

# Valedictions beyond the list stage 1 shares (:data:`~.extraction._SIGNOFF_SALUTATIONS`):
# the lines found directly above a sender-name line in mailing-list mail.
_EXTRA_SALUTATIONS = (
    r"with\s+(?:best|kind|warm(?:est)?)\s+regards|with\s+thanks|all\s+best|"
    r"thanks\s+(?:a\s+lot|in\s+advance|very\s+much|all|folks|everyone)|thankyou|"
    r"thank\s+you\s+(?:very\s+much|again|all)|many\s+thx|"
    r"take\s+care|so\s+long|see\s+you\s+soon|warmly|later|greetings|ave|"
    r"r[’']s|b\.?\s?r\.?|brgds|thanx|"
    r"(?:a|à)\s+bient(?:ô|o)t|cordialmente"
)
_ONE_SALUTATION = rf"(?:{_EXTRA_SALUTATIONS}|{_SIGNOFF_SALUTATIONS})"
# Two salutations joined: "Thanks and regards", "Regards/Ngā mihi", "Thanks, Cheers".
_SALUTATION = rf"{_ONE_SALUTATION}(?:[ \t]*(?:[/,&+]|and)[ \t]*{_ONE_SALUTATION})?"
# Punctuation after a salutation: "," "，" "、" ":" a run of "!", one to three "." or none.
_SALUTATION_PUNCT = r"(?:[,:，、]|!+|\.{1,3})"
#: A line that is only a salutation, with the tolerant list and punctuation.
_SALUTATION_LINE_RE = re.compile(
    rf"^[ \t]*{_SALUTATION}[ \t]*(?:{_SALUTATION_PUNCT}[ \t]*)?$", re.IGNORECASE
)
#: The one-line form: salutation, punctuation, name ("Later, Jane", "Regards，Jane").
_SALUTATION_INLINE_RE = re.compile(
    rf"^[ \t]*{_SALUTATION}[ \t]*(?P<punct>{_SALUTATION_PUNCT})[ \t]*(?P<name>\S.*)$",
    re.IGNORECASE,
)
#: The one-line form with a decorated name and no punctuation ("Thanks -Jane").
_SALUTATION_DECORATED_RE = re.compile(
    rf"^[ \t]*{_SALUTATION}[ \t]+(?P<name>[-–—~/]+[ \t]*\S.*)$", re.IGNORECASE
)
#: Any one-line form: optional punctuation, then the rest of the line.
_SALUTATION_ANY_INLINE_RE = re.compile(
    rf"^[ \t]*{_SALUTATION}[ \t]*(?:[,:，!.][ \t]*)?(?P<name>\S.*)$", re.IGNORECASE
)
#: The one-line form with only blanks after the salutation ("Thanks Jane").
_SALUTATION_SPACE_INLINE_RE = re.compile(
    rf"^[ \t]*{_SALUTATION}[ \t]+(?P<name>\S.*)$", re.IGNORECASE
)
_INLINE_COMMAS = (",", "，", "、")


def _strip_name_decoration(text: str) -> tuple[str, bool]:
    stripped = text.strip()
    core = stripped.lstrip(NAME_DECORATION)
    return core.rstrip(" \t.,;:!"), core != stripped


def _is_loose_signoff_name(text: str, *, allow_plain: bool) -> bool:
    """A sign-off name in the tolerant forms.

    A name line (:func:`~.extraction._is_signoff_name_like`) whose first four
    words are capitalized, when decorated ("— Jane Doe") or when
    ``allow_plain``; or a single word (:data:`~._names.LOOSE_NAME_TOKEN_RE`)
    that is decorated ("-jane"), indented, lower-case ("jane"), or any word
    when ``allow_plain``.
    """
    core, decorated = _strip_name_decoration(text)
    if not core:
        return False
    if (decorated or allow_plain) and _is_signoff_name_like(core):
        if all(word[:1].isupper() for word in core.split()[:4]):
            return True
    indented = text[:1] in (" ", "\t")
    if len(core.split()) == 1 and LOOSE_NAME_TOKEN_RE.match(core):
        return decorated or indented or core[0].islower() or allow_plain
    return False


def _tolerant_signoff_name_index(lines: list[str], i: int) -> int | None:
    """:func:`~.extraction._signoff_name_index` with the tolerant forms.

    After the shared forms: a salutation line (:data:`_SALUTATION_LINE_RE`)
    whose next non-blank line is a loose name (plain names allowed); the
    one-line form with punctuation whose name is a decorated or lower-case
    loose name, or, after a comma, a name line; the decorated one-line form.
    """
    shared = _signoff_name_index(lines, i)
    if shared is not None:
        return shared
    line = lines[i]
    if _SALUTATION_LINE_RE.match(line):
        j = i + 1
        while j < len(lines) and not lines[j].strip():
            j += 1
        if j < len(lines) and _is_loose_signoff_name(lines[j], allow_plain=True):
            return j
        return None
    inline = _SALUTATION_INLINE_RE.match(line)
    if inline:
        name = inline.group("name")
        if _is_loose_signoff_name(name, allow_plain=False):
            return i
        if inline.group("punct") in _INLINE_COMMAS and _is_signoff_name_like(name.strip()):
            return i
        return None
    decorated = _SALUTATION_DECORATED_RE.match(line)
    if decorated and _is_loose_signoff_name(decorated.group("name"), allow_plain=False):
        return i
    return None


# --- thanks to an addressee -------------------------------------------------------

_THANKS_SALUTATION_RE = re.compile(
    r"^[ \t]*(?:many[ \t]+|with[ \t]+)?(?:thanks|thank[ \t]*you|thx|thanx)\b", re.IGNORECASE
)
# A sentence break after a word of 2+ letters (not a title) and more text.
_FURTHER_WORDS_RE = re.compile(
    r"(?<![\w.])(?!(?:dr|mr|mrs|ms|mx|prof|jr|sr|st)[.])[^\W\d_]{2,}[.!?;:][ \t]+\S",
    re.IGNORECASE,
)
_NAME_LIST_SPLIT_RE = re.compile(r"[\s,/&+]+")


def _is_thanks_to_addressee(line: str, author: Names, addressees: Names | None) -> bool:
    """True when the one-line thanks form ``line`` thanks a participant.

    ``line`` opens with a thanks salutation (:data:`_THANKS_SALUTATION_RE`) and
    whitespace precedes the name ("Thanks,Jane" is a sign-off whose line break
    was lost). It is content when a sentence follows the name ("Thanks, Jane.
    That works.") or when no word of the name matches the sender and a letter
    run of one matches an addressee.
    """
    if not _THANKS_SALUTATION_RE.match(line):
        return False
    inline = _SALUTATION_ANY_INLINE_RE.match(line)
    if not inline or not line[: inline.start("name")][-1:].isspace():
        return False
    name = inline.group("name").lstrip(" ,，")
    if _FURTHER_WORDS_RE.search(name):
        return True
    words = [w for w in _NAME_LIST_SPLIT_RE.split(name.strip(" \t.,;:!-–—~*_")) if w]
    if any(matches_author(word, author) for word in words):
        return False
    return any(matches_addressee(word, addressees) for word in words)


# --- confidentiality / legal disclaimers ----------------------------------------

# The opening line of a boilerplate legal footer ("CONFIDENTIALITY NOTICE: …",
# "This email and any attachments are confidential…", "If you are not the
# intended recipient…"). Matched only at the START of a paragraph, and only for
# paragraphs in the message's trailing furniture region (see
# :func:`_drop_trailing_disclaimer_paragraphs`) — an author's own sentence like
# "This message is intended only for the design team" mid-mail is never touched.
_DISCLAIMER_START_RE = re.compile(
    r"^[ \t]*(?:"
    r"(?:CONFIDENTIALITY|LEGAL|PRIVACY)[ \t]+(?:NOTICE|NOTE|DISCLAIMER|STATEMENT)"
    r"|DISCLAIMER[ \t]*[:\-]"
    r"|This[ \t]+(?:e-?mail|message|communication)\b[^.]{0,120}?"
    r"\b(?:confidential|privileged|intended[ \t]+(?:only|solely)[ \t]+for)"
    r"|The[ \t]+information[ \t]+(?:contained|transmitted)\b[^.]{0,80}?"
    r"\b(?:confidential|privileged)"
    r"|If[ \t]+you[ \t]+(?:are[ \t]+not[ \t]+the[ \t]+intended[ \t]+recipient"
    r"|(?:have[ \t]+)?received[ \t]+this[ \t]+(?:e-?mail|message)[ \t]+in[ \t]+error)"
    r")",
    re.IGNORECASE,
)


# --- mobile / client taglines -------------------------------------------------

# A one-line client tagline appended by mobile or webmail clients. Anchored at
# line start; the alternation is deliberately extensible as new clients show up.
# ERP already treats "Sent from my …" as a signature boundary, so such a line
# often arrives inside a stage-1 signature fragment — but "Get Outlook for …"
# and "Sent from Proton Mail mobile" do not, so we drop them explicitly here.
_MOBILE_TAGLINE_RE = re.compile(
    r"^[ \t]*(?:"
    r"Sent from my \w+"  # Sent from my iPhone / iPad / Samsung / mobile device
    r"|Sent from Proton Mail"  # Sent from Proton Mail mobile
    r"|Sent from Mail for Windows"
    r"|Sent from Outlook"
    r"|Get Outlook for \w+"  # Get Outlook for iOS / Android
    r")",
    re.IGNORECASE,
)


def is_mobile_tagline(line: str) -> bool:
    """True for a mobile/webmail client tagline ("Sent from my iPhone")."""
    return bool(_MOBILE_TAGLINE_RE.match(line))


# --- public entry point -------------------------------------------------------

# Each step is a pure ``list -> list`` transform over (original-index, text)
# pairs, so the removed input-line indices fall out by comparing the surviving
# indices against the input. The truncating steps (delimiter, trailing debris,
# closing sign-off) all return a prefix of their input; the dropping steps
# (debris, greeting, taglines) drop individual lines.


def _truncate_at_signature_delimiter(pairs: list[tuple[int, str]]) -> list[tuple[int, str]]:
    for pos, (_idx, line) in enumerate(pairs):
        if is_signature_delimiter(line):
            return pairs[:pos]
    return pairs


def _truncate_at_custom_signature_divider(pairs: list[tuple[int, str]]) -> list[tuple[int, str]]:
    """Truncate at a punctuation-rule divider that opens a signature block.

    Two anchors keep authored content safe: the line directly above the divider
    must be blank (or the divider must open the text) — a Markdown-style heading
    underline sits directly UNDER its heading line and so never qualifies — and
    the first non-blank line after the divider must be a plausible name line
    (optionally with a title/affiliation tail), which is what marks the block as
    a signature rather than a section break.
    """
    for pos, (_idx, line) in enumerate(pairs):
        if not _CUSTOM_SIG_DIVIDER_RE.match(line):
            continue
        if pos > 0 and pairs[pos - 1][1].strip():
            continue  # heading underline, not a signature divider
        # The first non-blank line after the divider, found by stepping over
        # the blank run (not by slicing the rest of the list, which made every
        # divider cost the length of the text). Divider lines are non-blank,
        # so the runs stepped over for different dividers do not overlap.
        nxt_pos = pos + 1
        while nxt_pos < len(pairs) and not pairs[nxt_pos][1].strip():
            nxt_pos += 1
        if nxt_pos < len(pairs) and _is_signoff_name_like(pairs[nxt_pos][1]):
            return pairs[:pos]
    return pairs


def _strip_trailing_debris_pairs(pairs: list[tuple[int, str]]) -> list[tuple[int, str]]:
    kept = strip_trailing_signoff_debris([line for _idx, line in pairs])
    return pairs[: len(kept)]


def _drop_signature_debris(pairs: list[tuple[int, str]]) -> list[tuple[int, str]]:
    return [(idx, line) for idx, line in pairs if not _is_droppable_signature_line(line)]


def _strip_opening_greetings(
    pairs: list[tuple[int, str]], state: _CleanState
) -> list[tuple[int, str]]:
    """Drop up to two opening greeting lines, within the text's greeting budget."""
    for _ in range(_MAX_GREETINGS):
        if state.greetings_left == 0:
            break
        pos = next((p for p, (_idx, line) in enumerate(pairs) if line.strip()), None)
        if pos is None:
            break
        next_line = pairs[pos + 1][1] if pos + 1 < len(pairs) else None
        if not is_opening_greeting(pairs[pos][1], next_line, state.addressees):
            break
        pairs = pairs[:pos] + pairs[pos + 1 :]
        state.greetings_left -= 1
    return pairs


def _strip_closing_signoff(
    pairs: list[tuple[int, str]], state: _CleanState
) -> list[tuple[int, str]]:
    """Drop a closing sign-off from the end of the text.

    In order, on the last two non-blank lines: a shared sign-off
    (:func:`~.extraction._signoff_name_index`) whose name is the last line,
    then a tolerant one (:func:`_tolerant_signoff_name_index`), each truncating
    at its first line; then a last line that is a salutation alone
    (:data:`_SALUTATION_LINE_RE`). A one-line thanks form that thanks a
    participant (:func:`_is_thanks_to_addressee`, only with a sender name) is
    kept. A one-line form on a line exposed by a sender-name rule
    (``state.exposed``) is removed only when its name is a plain name: a name
    line, capitalized, with no punctuation followed by a blank ("Thanks, Jane"
    goes, "Thanks, Jane. I approve." stays).
    """
    lines = [line for _idx, line in pairs]
    nonblank = [i for i, line in enumerate(lines) if line.strip()]
    if not nonblank:
        return pairs
    last = nonblank[-1]
    if pairs[last][0] in state.exposed:
        inline = _SALUTATION_ANY_INLINE_RE.match(lines[last])
        if inline and not _SIGNOFF_LINE_RE.match(lines[last]):
            name = inline.group("name").lstrip(" ,，").rstrip(" .!")
            plain = (
                is_signoff_name_line(name)
                and not _PUNCT_BEFORE_BLANK_RE.search(name)
                and all(word[:1].isupper() for word in name.split())
            )
            if not plain:
                return pairs
    for find_name in (_signoff_name_index, _tolerant_signoff_name_index):
        for i in nonblank[-2:]:
            if find_name(lines, i) == last:
                if (
                    state.author is not None
                    and i == last
                    and _is_thanks_to_addressee(lines[i], state.author, state.addressees)
                ):
                    return pairs
                return pairs[:i]
    if _SALUTATION_LINE_RE.match(lines[last]):
        return pairs[:last]
    return pairs


_PUNCT_BEFORE_BLANK_RE = re.compile(r"[.,;:!?][ \t]")


def _drop_mobile_taglines(pairs: list[tuple[int, str]]) -> list[tuple[int, str]]:
    return [(idx, line) for idx, line in pairs if not is_mobile_tagline(line)]


# A line that carries a URL, used with the HTML-signature-hint substance guard.
_URL_RE = re.compile(r"https?://|\bwww\.", re.IGNORECASE)


def _is_html_signature_line(line: str, hint_norms: frozenset[str]) -> bool:
    """True for a body line the HTML signature container marks as furniture.

    The line's normalized form (via extraction's :func:`_normalize_line_for_diff`,
    so quote markers and whitespace do not defeat the match) must exactly equal a
    normalized non-blank line of the hint, and it must carry substance — at least
    two words, or a digit, ``@`` or URL — so a lone salutation like "Cheers" that
    happens to sit in the signature container is not stripped out of the body.
    """
    norm = _normalize_line_for_diff(line)
    if not norm or norm not in hint_norms:
        return False
    return (
        len(norm.split()) >= 2
        or any(ch.isdigit() for ch in norm)
        or "@" in norm
        or bool(_URL_RE.search(norm))
    )


def _drop_html_signature_lines(
    pairs: list[tuple[int, str]], hint_norms: frozenset[str]
) -> list[tuple[int, str]]:
    if not hint_norms:
        return pairs
    return [(idx, line) for idx, line in pairs if not _is_html_signature_line(line, hint_norms)]


def _drop_trailing_disclaimer_paragraphs(pairs: list[tuple[int, str]]) -> list[tuple[int, str]]:
    """Drop legal/confidentiality boilerplate paragraphs from the tail.

    Paragraphs (blank-line-delimited blocks) are examined from the END of the
    text upward; a paragraph whose first line matches
    :data:`_DISCLAIMER_START_RE` is dropped, and the scan continues to the
    paragraph above it. The scan stops at the first trailing paragraph that is
    NOT a disclaimer, so a disclaimer-shaped sentence in the body of the message
    is never removed — only bottom boilerplate is.
    """
    # Build paragraphs as [start, end) index ranges over `pairs`.
    paragraphs: list[tuple[int, int]] = []
    start = None
    for pos, (_idx, line) in enumerate(pairs):
        if line.strip():
            if start is None:
                start = pos
        elif start is not None:
            paragraphs.append((start, pos))
            start = None
    if start is not None:
        paragraphs.append((start, len(pairs)))

    cut = None
    for p_start, _p_end in reversed(paragraphs):
        if _DISCLAIMER_START_RE.match(pairs[p_start][1]):
            cut = p_start
            continue
        break
    return pairs if cut is None else pairs[:cut]


# --- sign-offs with the sender's name ---------------------------------------------

#: A rule line drawn directly above a signature name ("—", "---", "___").
_ABOVE_NAME_DIVIDER_RE = re.compile(r"^[ \t]*[-–—_=~*]+[ \t]*$")
#: A footnote or reference line ("[1] https://…").
_FOOTNOTE_RE = re.compile(r"^[ \t]*\[\d{1,3}\]")
#: A postscript ("PS", "P.S.", "p.p.s.").
_POSTSCRIPT_RE = re.compile(r"^[ \t]*p\.?[ \t]?(?:p\.?[ \t]?)?s\b", re.IGNORECASE)
_SENTENCE_END_RE = re.compile(r"[.?!:;]\s*$")
#: Lower-case words a signature line may hold besides capitalized ones.
_SIGNATURE_CONNECTORS = frozenset("of and for the at de in & und et y du von van der la le".split())
#: A rule line of three or more ``-_=*~#``.
_RULE_LINE_RE = re.compile(r"^[ \t]*[-_=*~#]{3,}[ \t]*$")
_LIST_BULLET_RE = re.compile(r"^[ \t]*(?:[*•+]|\d+[.)])[ \t]")


def _is_attached_signature_line(line: str) -> bool:
    """A line of a signature block under the sender's name.

    Signature debris, a link-only or "Label: URL" line; or a line of at most 8
    words, not ending in sentence punctuation (``.?!:;``), that holds a digit,
    ``@`` or a URL, or whose letter-initial words, connectors
    (:data:`_SIGNATURE_CONNECTORS`) aside, are mostly capitalized ("Example
    University", "Head of Research"). Never a footnote line.
    """
    s = line.strip()
    if not s or _FOOTNOTE_RE.match(s):
        return False
    if (
        _LINK_ONLY_LINE_RE.match(s)
        or _LABELED_LINK_LINE_RE.match(s)
        or _is_droppable_signature_line(s)
    ):
        return True
    words = s.split()
    if len(words) > 8 or _SENTENCE_END_RE.search(s):
        return False
    if any(ch.isdigit() for ch in s) or "@" in s or _URL_RE.search(s):
        return True
    alpha = [w for w in words if w[:1].isalpha() and w.lower() not in _SIGNATURE_CONNECTORS]
    if not alpha:
        return False
    return sum(1 for w in alpha if w[0].isupper()) * 2 > len(alpha)


def _is_nonblocking_tail_line(line: str) -> bool:
    """A line that may follow a sign-off without making it content.

    Blank, a footnote, a link-only or "Label: URL" line, signature debris, a
    client tagline, or a rule line.
    """
    s = line.strip()
    return (
        not s
        or _FOOTNOTE_RE.match(s) is not None
        or _LINK_ONLY_LINE_RE.match(s) is not None
        or _LABELED_LINK_LINE_RE.match(s) is not None
        or _is_droppable_signature_line(s)
        or is_mobile_tagline(s)
        or _CUSTOM_SIG_DIVIDER_RE.match(s) is not None
        or _RULE_LINE_RE.match(s) is not None
    )


def _kept_tail(tail: list[tuple[int, str]]) -> list[tuple[int, str]]:
    """The lines kept from the non-blocking tail below a sign-off.

    Blank lines and footnotes stay, and so do link lines when the tail holds a
    footnote (they are its references); a postscript and everything after it
    stay. Everything else (debris, taglines, rule lines, bare links) goes.
    """
    keep_links = any(_FOOTNOTE_RE.match(line) for _idx, line in tail)
    kept: list[tuple[int, str]] = []
    for pos, (idx, line) in enumerate(tail):
        if _POSTSCRIPT_RE.match(line):
            return kept + tail[pos:]
        s = line.strip()
        if s and not _FOOTNOTE_RE.match(s):
            is_link = _LINK_ONLY_LINE_RE.match(s) or _LABELED_LINK_LINE_RE.match(s)
            if not (is_link and keep_links):
                continue
        kept.append((idx, line))
    return kept


def _previous_nonblank(pairs: list[tuple[int, str]], pos: int) -> int:
    """The position of the last non-blank line above ``pos``, or -1."""
    k = pos - 1
    while k >= 0 and not pairs[k][1].strip():
        k -= 1
    return k


def _mark_exposed(pairs: list[tuple[int, str]], pos: int, state: _CleanState) -> None:
    """Record the non-blank line above ``pos``: a removal exposes it as the tail."""
    k = _previous_nonblank(pairs, pos)
    if k >= 0:
        state.exposed.add(pairs[k][0])


def _divider_above(pairs: list[tuple[int, str]], pos: int) -> int:
    """``pos``, or the position of a rule line at most one blank line above it."""
    k = _previous_nonblank(pairs, pos)
    if k >= 0 and pos - k <= 2 and _ABOVE_NAME_DIVIDER_RE.match(pairs[k][1]):
        return k
    return pos


def _drop_signoff_above_tail(
    pairs: list[tuple[int, str]], state: _CleanState
) -> list[tuple[int, str]]:
    """Drop the first sender-name line followed only by signature and tail lines.

    The sender's name line, the attached signature lines directly below it,
    and the salutation line above it (or else a rule line directly above the
    name) are removed when everything below is non-blocking
    (:func:`_is_nonblocking_tail_line`) up to the end or up to a postscript.
    Of that tail, :func:`_kept_tail` decides what stays. A name line that is
    the last non-blank line on its own is left to
    :func:`_drop_trailing_author_name`.
    """
    n = len(pairs)
    # nonblocking[p]: every line of pairs[p:] is non-blocking, or a postscript
    # comes first.
    nonblocking = [True] * (n + 1)
    blank_from = [True] * (n + 1)
    # attached_end[p]: the end of the run of attached signature lines at p.
    attached_end = list(range(n + 1))
    for p in range(n - 1, -1, -1):
        line = pairs[p][1]
        nonblocking[p] = _POSTSCRIPT_RE.match(line) is not None or (
            _is_nonblocking_tail_line(line) and nonblocking[p + 1]
        )
        blank_from[p] = blank_from[p + 1] and not line.strip()
        if _is_attached_signature_line(line):
            attached_end[p] = attached_end[p + 1]
    for i in range(n):
        if not is_author_name_line(pairs[i][1], state.author):
            continue
        j = attached_end[i + 1]
        if not nonblocking[j] or (j == i + 1 and blank_from[j]):
            continue
        k = _previous_nonblank(pairs, i)
        if k >= 0 and _SALUTATION_LINE_RE.match(pairs[k][1]):
            start = k
        else:
            start = _divider_above(pairs, i)
        _mark_exposed(pairs, start, state)
        return pairs[:start] + _kept_tail(pairs[j:])
    return pairs


def _drop_trailing_author_name(
    pairs: list[tuple[int, str]], state: _CleanState
) -> list[tuple[int, str]]:
    """Drop a last non-blank line that is the sender's name or decorated initial,
    with a rule line directly above it."""
    pos = len(pairs) - 1
    while pos >= 0 and not pairs[pos][1].strip():
        pos -= 1
    if pos < 0:
        return pairs
    line = pairs[pos][1]
    if is_author_name_line(line, state.author) or is_author_initial_line(line, state.author):
        start = _divider_above(pairs, pos)
        _mark_exposed(pairs, start, state)
        return pairs[:start]
    return pairs


def _drop_author_signoffs(pairs: list[tuple[int, str]], author: Names) -> list[tuple[int, str]]:
    """Drop every salutation + sender-name sign-off, wherever it stands.

    The two-line form (a salutation line, then the sender's name as the next
    non-blank line) and the one-line form (``_SALUTATION_INLINE_RE``, else
    ``_SALUTATION_SPACE_INLINE_RE``, whose name is the sender's).
    """
    n = len(pairs)
    drop: set[int] = set()
    for i in range(n):
        line = pairs[i][1]
        if not line.strip():
            continue
        if _SALUTATION_LINE_RE.match(line):
            j = i + 1
            while j < n and not pairs[j][1].strip():
                j += 1
            if j < n and is_author_name_line(pairs[j][1], author):
                drop.update((i, j))
                continue
        inline = _SALUTATION_INLINE_RE.match(line) or _SALUTATION_SPACE_INLINE_RE.match(line)
        if inline and is_author_name_line(inline.group("name"), author):
            drop.add(i)
    if not drop:
        return pairs
    return [pair for p, pair in enumerate(pairs) if p not in drop]


def _without_trailing_parenthetical(line: str) -> str | None:
    """``line`` without a final ``(…)`` of at most 60 characters and the blanks
    around it, or ``None`` when it has none."""
    s = line.rstrip(" \t")
    if not s.endswith(")"):
        return None
    open_paren = s.rfind("(")
    if open_paren < 0 or ")" in s[open_paren + 1 : -1] or len(s) - open_paren - 2 > 60:
        return None
    return s[:open_paren].rstrip(" \t")


def _is_name_shaped(line: str) -> bool:
    """A capitalized name line, a trailing parenthesis aside (a list of names)."""
    bare = _without_trailing_parenthetical(line)
    s = (line if bare is None else bare).strip()
    return _is_signoff_name_like(s) and all(w[:1].isupper() for w in s.split() if w.isalpha())


def _drop_lone_author_name(pairs: list[tuple[int, str]], author: Names) -> list[tuple[int, str]]:
    """Drop a sender-name line away from the tail with no salutation above it.

    Every non-blank line but the first and the last qualifies when it is the
    sender's name, does not end in ``:`` or ``,`` (a speaker label, a greeting
    to a namesake), is not a list item, and the non-blank line above is not a
    salutation, not name-shaped (:func:`_is_name_shaped`: a list of names) and
    does not end in ``:``. A name line with a trailing parenthesis followed by
    a name-shaped line is part of a list too.
    """
    nonblank = [p for p, (_idx, line) in enumerate(pairs) if line.strip()]
    drop: set[int] = set()
    for k in range(1, len(nonblank) - 1):
        p = nonblank[k]
        line = pairs[p][1]
        if not is_author_name_line(line, author):
            continue
        if line.rstrip().endswith((":", ",")) or _LIST_BULLET_RE.match(line):
            continue
        above = pairs[nonblank[k - 1]][1]
        if (
            _SALUTATION_LINE_RE.match(above)
            or _is_name_shaped(above)
            or above.rstrip().endswith(":")
        ):
            continue
        below = pairs[nonblank[k + 1]][1]
        if _without_trailing_parenthetical(line) is not None and _is_name_shaped(below):
            continue
        drop.add(p)
    if not drop:
        return pairs
    return [pair for p, pair in enumerate(pairs) if p not in drop]


# --- learned signature lines -------------------------------------------------------

#: :func:`learn_signature_lines` thresholds.
_LEARN_MIN_MESSAGES = 5
_LEARN_MIN_COUNT = 3
_LEARN_MIN_SUBJECTS = 3
_LEARN_TAIL_LINES = 10
#: A non-learned line in the trailing zone has at most this many words.
_LEARNED_ZONE_MAX_WORDS = 4

#: The reply/forward prefixes and bracketed list tags at the start of a
#: subject, any number of them ("Re: [list] AW: Fwd[2]: …").
_SUBJECT_PREFIXES_RE = re.compile(
    r"(?:\s*(?:\[[^\]]*\]|(?:re|fw|fwd|aw|sv|antw|wg|tr)(?:\s*\[\d+\])?\s*:)\s*)*",
    re.IGNORECASE,
)
_SENTENCE_PUNCT_END = (".", "?", "!")


def _lower(text: str) -> str:
    """``str.lower`` with ``ß`` (U+00DF) written ``ss``."""
    return text.lower().replace("\u00df", "ss")


def _learned_norm(line: str) -> str:
    """A line as learned lines are compared: normalized for the parent diff
    (quote markers and whitespace runs removed), then lowercased with ``ß``
    written ``ss`` (:func:`_lower`)."""
    return _lower(_normalize_line_for_diff(line))


def _subject_key(subject: str | None) -> str:
    rest = (subject or "")[_SUBJECT_PREFIXES_RE.match(subject or "").end() :]
    return _lower(" ".join(rest.split()))


def learn_signature_lines(
    texts: Sequence[str], *, subjects: Sequence[str] | None = None
) -> tuple[str, ...]:
    """Learn the furniture lines one sender repeats at the end of their messages.

    ``texts`` are the stage-1 texts of one sender's messages, and ``subjects``
    (optional) their subjects, one per text. Lines are compared in a
    normalized form: quote markers removed, whitespace runs collapsed to one
    space, edges stripped, lowercased (``str.lower``, then ``ß`` written
    ``ss``). A normalized line is learned when it
    is among the last 10 non-blank lines of at least 3 of the texts and of at
    least one fifth of them, and, with ``subjects``, when those texts have at
    least 3 distinct subjects (compared without leading "Re:"/"Fwd:"-style
    prefixes and "[list]" tags, whitespace collapsed, lowercased as above). Fewer than
    5 texts learn nothing. A subject that is ``None`` or empty is the empty
    subject; a text with no corresponding subject (``subjects`` shorter than
    ``texts``) has the empty subject, and subjects beyond the last text are
    ignored.

    A candidate that ends in ":" or is a sentence (at least 3 words ending in
    ``.``, ``?`` or ``!``) is the first line of a block where the sender's own
    words would be, and is learned only when, in at least half of its
    occurrences among those last lines, the line just above is another
    candidate.

    Returns the learned lines in their normalized form, sorted; pass them as
    ``signature_lines`` to :func:`clean_for_scoring`. Pure.
    """
    if len(texts) < _LEARN_MIN_MESSAGES:
        return ()
    tails: list[list[str]] = []
    occurrences: dict[str, list[int]] = {}
    for i, text in enumerate(texts):
        norms = [n for n in (_learned_norm(line) for line in (text or "").split("\n")) if n]
        tail = norms[-_LEARN_TAIL_LINES:]
        tails.append(tail)
        for norm in set(tail):
            occurrences.setdefault(norm, []).append(i)
    candidates = set()
    for norm, where in occurrences.items():
        if len(where) < _LEARN_MIN_COUNT or len(where) * 5 < len(texts):
            continue
        if subjects is not None:
            keys = {_subject_key(subjects[i] if i < len(subjects) else None) for i in where}
            if len(keys) < _LEARN_MIN_SUBJECTS:
                continue
        candidates.add(norm)
    learned = []
    for norm in sorted(candidates):
        opens_block = norm.endswith(":") or (
            norm.endswith(_SENTENCE_PUNCT_END) and len(norm.split()) >= 3
        )
        if opens_block:
            continued = total = 0
            for i in occurrences[norm]:
                tail = tails[i]
                for pos, line in enumerate(tail):
                    if line != norm:
                        continue
                    total += 1
                    if pos > 0 and tail[pos - 1] in candidates and tail[pos - 1] != norm:
                        continued += 1
            if continued * 2 < total:
                continue
        learned.append(norm)
    return tuple(learned)


def _drop_learned_signature_lines(
    pairs: list[tuple[int, str]], learned: frozenset[str]
) -> list[tuple[int, str]]:
    """Drop the learned lines in the trailing zone.

    The trailing zone is scanned from the last line up and ends at the first
    non-blank line that is not learned and has more than
    :data:`_LEARNED_ZONE_MAX_WORDS` words. Every learned line inside it is
    dropped, unless no non-blank, non-learned line stands above the topmost of
    them: the step never removes the whole text.
    """
    if not learned:
        return pairs
    norms = [_learned_norm(line) for _idx, line in pairs]
    drop: set[int] = set()
    for pos in range(len(pairs) - 1, -1, -1):
        norm = norms[pos]
        if not norm:
            continue
        if norm in learned:
            drop.add(pos)
        elif len(norm.split()) > _LEARNED_ZONE_MAX_WORDS:
            break
    if not drop:
        return pairs
    first = min(drop)
    if not any(norm and norm not in learned for norm in norms[:first]):
        return pairs
    return [pair for pos, pair in enumerate(pairs) if pos not in drop]


# --- the pass -----------------------------------------------------------------------

#: Greeting lines removed per pass and per text.
_MAX_GREETINGS = 2


@dataclass
class _CleanState:
    """Evidence and bookkeeping that persist across the passes of one text."""

    author: Names | None
    addressees: Names | None
    #: Greeting lines that may still be removed (at most :data:`_MAX_GREETINGS`).
    greetings_left: int = _MAX_GREETINGS
    #: Input indices of lines that a sender-name rule exposed as the tail.
    exposed: set[int] = field(default_factory=set)


def _clean_pass(
    pairs: list[tuple[int, str]], hint_norms: frozenset[str], state: _CleanState
) -> list[tuple[int, str]]:
    """One full pass of every removal step, in order."""
    pairs = _truncate_at_signature_delimiter(pairs)
    pairs = _truncate_at_custom_signature_divider(pairs)
    # Trailing-debris before the per-line debris drop: its bare-name anchor needs
    # the identifier/contact lines still present as evidence.
    pairs = _strip_trailing_debris_pairs(pairs)
    pairs = _drop_signature_debris(pairs)
    # The HTML signature hint drops individually, alongside the debris drop.
    pairs = _drop_html_signature_lines(pairs, hint_norms)
    pairs = _strip_opening_greetings(pairs, state)
    # Taglines and disclaimers go before the closing sign-off so a sign-off with
    # boilerplate below it ("Regards, / Bob / This email is confidential…") is
    # exposed as the true tail and removed in the same pass.
    pairs = _drop_mobile_taglines(pairs)
    pairs = _drop_trailing_disclaimer_paragraphs(pairs)
    pairs = _strip_closing_signoff(pairs, state)
    if state.author is not None:
        # A salutation these rules expose at the tail goes on the next pass.
        pairs = _drop_signoff_above_tail(pairs, state)
        pairs = _drop_trailing_author_name(pairs, state)
        pairs = _drop_author_signoffs(pairs, state.author)
        pairs = _drop_lone_author_name(pairs, state.author)
    return pairs


#: Fixpoint cap for :func:`clean_for_scoring`. One pass usually suffices; a
#: second catches furniture exposed by the first (e.g. a sign-off that becomes
#: the tail only after a contact block below it is dropped). The cap is a
#: safety bound, not a tuning knob; it doubles when learned lines are given.
_MAX_CLEAN_PASSES = 4


def clean_for_scoring(
    extracted_text: str,
    html_signature_text: str | None = None,
    *,
    part_starts: Sequence[int] = (),
    author_name: str | None = None,
    addressee_names: Sequence[str] | None = None,
    signature_lines: Sequence[str] | None = None,
) -> CleanResult:
    """Strip formulaic furniture from stage-1 ``extracted_text`` for scoring.

    Pure and never raises. Returns a :class:`CleanResult` with the scored
    ``text`` and the 0-based indices of every non-blank input line removed. When
    there is nothing to clean, ``text`` equals the trimmed input and
    ``ignored_lines`` is empty. The pass sequence runs to a fixpoint (bounded by
    :data:`_MAX_CLEAN_PASSES`): removing one layer of furniture can expose
    another (a closing sign-off only becomes the tail once the contact block
    under it is gone).

    ``html_signature_text`` is the optional visible text of the message's HTML
    signature container (see :func:`~html_text.split_html_parts`); its non-blank
    lines become a per-line drop hint (step 4 above). Lines it removes are
    reported in ``ignored_lines`` like any other furniture.

    ``part_starts`` is the stage-1 result's
    :attr:`~extraction.ExtractionResult.part_starts`. Indices outside
    ``1 .. len(lines) - 1`` are ignored, and the rest are used in ascending
    order without duplicates. It changes only ``parts``, never ``text`` or
    ``ignored_lines``.

    ``author_name`` is the display name of the message's sender (the From
    header's name, or its address when there is no name); it enables the
    sender-name sign-off rules. ``addressee_names`` are the display names of
    the people the message answers or is addressed to (the parent message's
    sender, the To and Cc names); they enable the addressee greeting rule and
    the thanks-to-an-addressee exception. ``signature_lines`` are the sender's
    learned signature lines (:func:`learn_signature_lines`). Each is optional;
    with all three left at ``None`` only the rules that need no evidence run.
    """
    hint_norms = frozenset(
        norm
        for norm in (
            _normalize_line_for_diff(line) for line in (html_signature_text or "").split("\n")
        )
        if norm
    )
    learned = frozenset(
        norm for norm in (_learned_norm(line) for line in (signature_lines or ())) if norm
    )
    state = _CleanState(
        author=parse_author_name(author_name),
        addressees=parse_addressee_names(addressee_names or (), author_name),
    )

    original = extracted_text.split("\n")
    pairs: list[tuple[int, str]] = list(enumerate(original))

    # The learned-line drop runs only once the ordinary passes have converged,
    # so the other steps keep their anchors; the passes then resume, since the
    # drop can expose more furniture.
    for _ in range(_MAX_CLEAN_PASSES * 2 if learned else _MAX_CLEAN_PASSES):
        before = len(pairs)
        pairs = _clean_pass(pairs, hint_norms, state)
        if len(pairs) != before:
            continue
        pairs = _drop_learned_signature_lines(pairs, learned)
        if len(pairs) == before:
            break

    kept_indices = {idx for idx, _line in pairs}
    ignored_lines = [i for i, line in enumerate(original) if line.strip() and i not in kept_indices]

    text = _format_lines([line for _idx, line in pairs])

    starts = sorted({s for s in part_starts if 0 < s < len(original)})
    if not text:
        parts: list[str] = []
    elif not starts:
        parts = [text]
    else:
        groups: list[list[str]] = [[] for _ in range(len(starts) + 1)]
        for idx, line in pairs:
            groups[bisect_right(starts, idx)].append(line)
        parts = [part for part in (_format_lines(group) for group in groups) if part]

    return CleanResult(text=text, ignored_lines=ignored_lines, parts=parts)


def _format_lines(lines: list[str]) -> str:
    """Final formatting of surviving lines.

    Right-strip each line, collapse runs of 2+ blank lines to one, and trim
    blank edges (matching extraction's tail handling).
    """
    collapsed: list[str] = []
    for line in (line.rstrip() for line in lines):
        if not line and collapsed and not collapsed[-1]:
            continue
        collapsed.append(line)
    while collapsed and not collapsed[0]:
        collapsed.pop(0)
    while collapsed and not collapsed[-1]:
        collapsed.pop()
    return "\n".join(collapsed)
