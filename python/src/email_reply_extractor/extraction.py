"""New-text extraction: recover the author's *novel content* (stage 1 of 2).

Strategy: **a line-based cleanup pass over the normalized body**, followed by
an optional parent-diff assist. The public entry point is the pure function
:func:`extract_new_text`, which takes a decoded ``text/plain`` body and returns
an :class:`ExtractionResult` (``text``, ``method``, ``status``) with no I/O — so
the quality tests run without a database.

This module is **stage 1** of a two-stage pipeline. Stage 1 recovers everything
the author put in the message that is *not* quoted or reproduced from elsewhere:
it removes quoted lines, attribution lines, forwarded/quote-header blocks, the
quoted thread after a sign-off boundary, and (optionally) parent-diff content —
but it **keeps** the author's own framing furniture (greetings, sign-offs,
signature blocks, mailing-list footers, mobile taglines). Removing that furniture
is **stage 2**'s job (:mod:`cleaning`, run before the text is consumed), which
also reports what it removed so a consumer can show it. Keeping furniture in stage
1 means the stored "extracted text" is the author's full novel content, and the
scored text is a strict, documented subset of it.

The cleanup pass (:func:`custom_clean`, after
:func:`_mark_generic_attributions`) applies these rules:

- **Pre-normalization** strips a byte-order mark, zero-width characters and
  U+0000 and converts CRLF/CR to LF, so a leading BOM cannot hide a ``>``
  quote marker. U+0000 is reserved for marking generic attribution lines.
- **Attribution lines** ("On <date>, X wrote:", also after "--"; "Name <a@b>
  wrote:"; the German "Am <date> schrieb X:"; the Japanese "…のメール:") are
  removed, including wrapped forms: a start line ("On", "Am", "Le", "El",
  "El día" or a leading year, optionally after ``>`` markers and a "--")
  whose terminator lands on one of the next 4 lines, with no blank line and
  no complete attribution line between, within 400 characters (see
  :func:`_wrapped_attribution_end`). The pattern lists are structured so more
  languages are easy to add.
- **Generic attribution lines**: a unit of one to three lines ending in a
  colon, directly above a quote line (one blank line allowed between), is
  removed when its text (at most 400 characters) carries a cue — an e-mail
  address, or a clock time and a date, within 25 words — or ends in a speech
  verb within 8 words ("Ana Lima said:", "I wrote:"). Document references
  ("Section 2.3 says:", "OLD:"), lead-ins ("the following:", "what the draft
  says:") and the fixed forms above are excluded (see the comment above
  :func:`_generic_attribution_start`).
- **Wrap remainders**: an unmarked line of at most 2 words that starts
  lowercase, between two quote lines, below a quote line of at least 60
  characters with no closing punctuation, is the tail of a re-wrapped quote
  line and is removed with the quote (see :func:`_is_wrap_remainder`). A line
  of 3 or more words stays.
- **Indented quote blocks** — lines that are optional whitespace then one or
  more ``>``, also after leader dots (``...>``) — are dropped.
- **Sign-off boundary**: a salutation line ("Best," / "Cheers" / "Thanks," …)
  plus a name line, directly followed by an attribution (a fixed form, a
  wrapped form or a generic attribution unit) that is *not* followed by a
  quote line, ends the new text — everything after the name is the quoted
  message even when the client added no ``>`` markers (Gmail-style fully
  top-posted replies, typical of AI-generated mail). The name is required: a
  bare mid-thread "Thanks." never truncates. An attribution followed by a
  quote line heads an interleaved quote, so the inline replies below it are
  kept. The sign-off *itself* survives stage 1 (this rule removes only the
  quoted thread the sign-off precedes); stage 2 strips the sign-off.
- **Forwarded / quote-header blocks**: an Outlook-style ``From:``/``Sent:``/
  ``To:``/``Subject:`` block introducing quoted content with no ``>`` markers,
  and everything after it, is dropped. Localized label sets are recognized too
  (German; Chinese 发件人/发送时间/收件人/主题 with full-width colons, as
  produced by Alibaba Mail and Chinese Outlook), along with the dashed divider
  Alibaba Mail draws above the block and the bare "Original" line QQ Mail
  draws there. Latin-script localized blocks open with
  ``De``/``Da``/``Van``/``Von`` (French, Spanish, Portuguese, Italian, Dutch,
  German), accept U+00A0 / U+202F around the colon (``De\xa0: …``), and must
  carry their language's subject label. A message whose author answers inline,
  with tagged lines, below such a block keeps the generation-5 treatment (see
  :func:`_localized_inline_reply`). When the ``From:`` line carries an
  address, the block is also recognized in the two mangled renderings
  archives produce — folded header lines that lost their leading whitespace,
  and a blank line between every field.
- **"Original message" dividers**: the dashed ``-------- Original message
  --------`` (or ``-----Original Message-----`` / "Forwarded message", the
  Chinese and Latin-script localized forms such as ``-----Message
  d'origine-----``, and the short ``--- Original ---`` / ``--- Original
  Messages ---`` / ``--- 邮件原文 ---`` forms) divider and everything after it
  is dropped — including when HTML-to-text conversion glues the divider
  mid-line onto the author's last sentence, where only the author's prefix
  before the divider is kept. A divider on a quote line is quoted content and
  is ignored. A bare "Original" line above a ``From:`` line counts as a
  divider. The short forms are ignored below a line that says the author
  answers "inline" (see :func:`strip_after_original_message_divider`).
- **Outlook boundary lines**: a blank line is inserted between a non-empty line
  and a ``_______`` / ``-------`` line (an optional space, then 7 or more
  underscores or hyphens) directly below it.
- **Parent-diff assist** (optional): when the reply's thread parent is available,
  the caller passes ``parent_body`` and — after the cleanup pass — any content
  that provably came from the parent is removed. This catches fully top-posted
  replies whose quoted previous message carries no ``>`` markers at all (so the
  quote/attribution/header filters see nothing to drop), using a line-level
  alignment around the longest common run of lines and an exact substring
  index over the parent as an evidence-based backstop. A matched run that the
  message also carries inside its own marked quotation, in a block that is
  mostly unmatched text, is kept. In *marking mode* — at least 3 quote-marked
  lines of at least 4 words that the parent holds at a smaller quote depth —
  unmarked parent text in a block with no quote line, above any attribution
  that introduces unmarked text, is the author's (a pasted OLD/NEW block, a
  re-sent paragraph) and is kept, unless 8 or more such kept runs of more than
  2 lines show a point-by-point answer to an unmarked copy of the parent. See
  :func:`strip_parent_content` and the marking-mode comment; it adds the
  ``"+parent-diff"`` method suffix.

HTML as a structural oracle (optional ``html_body``)
----------------------------------------------------
When the caller passes the decoded ``text/html`` part, its structure (see
:mod:`html_text`) is used four ways. The resolved method makes the HTML source
visible so it is auditable downstream:

a. **HTML-only** — the plain body is missing or blank but an HTML part exists:
   the HTML's novel text (:func:`~html_text.split_html_parts`) becomes the body
   for the normal stage-1 pipeline, and the method is prefixed ``html-`` (e.g.
   ``html-custom``).
b. **Degenerate plain fallback** — the plain body survived but is flattened
   (fewer than 4 non-blank lines, at least one over 400 chars) and the HTML
   novel text has at least 3× as many non-blank lines: the HTML path (a) is used
   instead of the mangled plain body. The ``toppost-originalmsg-divider`` fixture
   triggers this.
c. **Oracle assist** — the plain body is fine: the normal plain pipeline runs,
   then any content provably present in the HTML's quoted text
   (:func:`strip_parent_content`, same thresholds as the parent-diff assist) is
   removed. When it drops a **block** of content lines, ``+html-quote`` is
   appended. The assist only fires for a substantial removal
   (:data:`_HTML_QUOTE_MIN_REMOVED` non-blank lines): its target is a whole
   quoted message leaked into the plain part unmarked, not the odd author line
   that happens to cite the same wording as the thread (a mail client wraps
   *interleaved* author replies inside the same ``gmail_quote`` container as the
   quote, and an author quoting a proposed sentence inline matches its verbatim
   copy in a real ``<blockquote>`` — both would otherwise cost genuine content).
d. **Share-sheet container** — the HTML holds an Apple Mail Share-sheet
   container (:attr:`~html_text.HtmlParts.has_share_container`) and its novel
   text is non-blank: the HTML path (a) is used instead of the plain body,
   whatever the plain body holds. It is checked after (b) and before the plain
   pipeline, so (c) never runs on such a message. The Share sheet wraps the
   author's own text in a ``<blockquote type="cite">`` and the plain part
   prefixes every line of it with ``>``, so the plain pipeline removes it as
   quoted; the HTML segmenter treats that blockquote as novel. The
   ``toppost-share-sheet-synthetic-01`` fixture triggers this.

A word-count "too short" gate is deliberately **not** applied here — that is a
consumer's decision, and it belongs on the stage-2 *cleaned* text.

Part boundaries
---------------
:attr:`ExtractionResult.part_starts` lists the lines of the final text that
begin a new part: a run of the author's text separated from the previous run by
content stage 1 removed (a quoted block, an attribution, parent-diff or
HTML-quote content). It is found after the text is final by aligning the text
with the body it was derived from (:func:`_part_starts`), so it never affects
the text, the method or the status; an alignment that fails reports one part.
:func:`cleaning.clean_for_scoring` divides the cleaned text at the same
boundaries when given them.

Generation stamp (:data:`EXTRACTION_VERSION`)
---------------------------------------------
:data:`EXTRACTION_VERSION` identifies the routine that derives an extraction's
text. A consumer that stores derived text should stamp each stored row with it
and treat a row as stale when its stamp is *lower* than the running value (see
``docs/versioning.md``). It is independent of the package's semantic version:
releases that do not touch the pipeline leave it alone.

Bump it by one — by hand, in the same commit — whenever a change to this module,
:mod:`cleaning` or :mod:`html_text` can alter the stage-1 or
stage-2 output, including a whitespace-only difference. Do not bump it for comments,
docstrings, type annotations or refactors that provably keep every output byte
identical; ``conformance/digest.json`` pins the composite output of the whole
fixture corpus to a digest and ``tests/test_extraction_version.py`` fails when it
moves, so a change that needs a bump cannot land silently. The TypeScript and Rust
ports declare the same constant and are bumped in the same commit. The value only ever increases: ordering is what
lets an older consumer read text derived by a newer one as "not stale" rather
than offering to downgrade good text.

Known generations: **1** initial release; **2** the localized quote-header and
custom signature-block rules (v1.2.0); **3** quote-header truncation before ERP,
with folded and double-spaced header tolerance and the transport-header
pasted-evidence guard (v1.11.0); **4** the parent-diff continuation rule for
re-wrapped remainder lines, and Gmail quote wrappers holding blockquotes no
longer classifying the author's inline replies as quoted (v1.15.0); **5** the
vendored scanner keeps fragment edges and applies the Outlook-boundary fix to
every occurrence (this package's v1.1.0; generations 1-4 shipped inside
mailing-list-ai-check); **6** Latin-script localized quote-header blocks and
"Original message" dividers, with the inline-reply exception (v1.2.0); **7**
the parent-diff aligned-run rule aligns around the longest common run of lines
instead of ``difflib.SequenceMatcher``, and the vendored scanner inserts the
collapsed attribution header literally (v1.3.0); **8** a ``<blockquote>`` whose
parent carries Apple Mail's ``Apple-Mail-URLShareWrapperClass`` is novel, not
quoted, and a message holding one derives its text from the HTML part (v1.4.0);
**9** the parent-diff assist keeps a matched run that the message also carries
inside its own marked quotation, in a block that is mostly unmatched text
(v1.5.0); **10** the vendored fragment scanner, the quote-header
pre-truncation and the over-strip guard are removed and the cleanup pass runs
on the whole body, with generic attribution lines, wrap remainders, the short
"Original" dividers, the Outlook boundary rule, the parent-diff marking mode and
the composed-signature HTML containers, together with that generation's
changes to :mod:`cleaning` (v1.6.0).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .html_text import html_to_text, split_html_parts

# --- generation stamp -----------------------------------------------------------

#: The generation of the text-deriving routine (this module, :mod:`cleaning` and
#: :mod:`html_text` together). Hand-incremented; see the module docstring for
#: when and why. Consumers stamp stored derived text with it and compare with
#: ``<``, so it must only ever increase.
EXTRACTION_VERSION: int = 10

# --- result -------------------------------------------------------------------

#: Nothing left to score (blank body, HTML-only message, or a body that reduced
#: to only quotes/signatures).
STATUS_EMPTY = "empty"
#: Text was extracted.
STATUS_OK = "ok"
#: Extraction raised — the raw body is preserved so nothing is silently lost.
STATUS_FAILED = "failed"


@dataclass(frozen=True)
class ExtractionResult:
    """The outcome of running :func:`extract_new_text` on one body.

    This is the **stage-1** result: the author's full novel content, still
    including their greetings, sign-offs and signature blocks. The scored text
    is a subset produced by :func:`cleaning.clean_for_scoring`.

    - ``text``: the author's newly written text, furniture included (may be
      empty).
    - ``method``: which path produced it — ``"none"`` (no body), ``"custom"``
      (the cleanup pass), or ``"failed"``. When a thread parent was supplied
      and the parent-diff assist removed content, ``"+parent-diff"`` is
      appended (``"custom+parent-diff"``). When the text was derived from the
      HTML part (HTML-only, degenerate-plain fallback or Share-sheet container)
      the method is **prefixed** ``"html-"`` (e.g. ``"html-custom"``);
      when the HTML quoted-text oracle removed content from a plain extraction,
      ``"+html-quote"`` is appended.
    - ``status``: one of :data:`STATUS_OK`, :data:`STATUS_EMPTY`,
      :data:`STATUS_FAILED`.
    - ``part_starts``: the 0-based indices into ``text.split("\n")`` of the
      lines that begin a new *part* — a run of the author's text separated
      from the previous run by content that stage 1 removed (a quoted block, an
      attribution, parent-diff or HTML-quote content). An inline reply written
      between quoted passages has one part per answer. Empty when the text is a
      single part, when it is empty, and when the text could not be aligned
      with its source (see :func:`_part_starts`). The text itself does not
      depend on this field; pass it to :func:`cleaning.clean_for_scoring` to
      get the cleaned text divided into the same parts.
    """

    text: str
    method: str
    status: str
    part_starts: tuple[int, ...] = ()


# --- pre-normalization --------------------------------------------------------

#: Characters removed before parsing: BOM / zero-width space / zero-width
#: non-joiner / zero-width joiner / word-joiner. U+0000 is removed as well (see
#: :data:`_GENERIC_ATTRIBUTION_MARK`).
_ZERO_WIDTH = "﻿​‌‍⁠"
_ZERO_WIDTH_RE = re.compile(f"[{_ZERO_WIDTH}]")


def normalize_body(body: str) -> str:
    """Normalize line endings and strip BOM / zero-width characters and U+0000.

    Runs before the cleanup pass so a leading BOM cannot hide a ``>`` quote
    marker (the documented ``interleaved-bom`` fixture).
    """
    body = body.replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "")
    return _ZERO_WIDTH_RE.sub("", body)


# --- line classifiers ---------------------------------------------------------

# A quoted line: optional indentation, an optional run of leader dots (the
# ``...>`` form that survives format=flowed rewrapping), then one or more ``>``.
_QUOTE_RE = re.compile(r"^[ \t]*(?:\.{2,}[ \t]*)?>+")


def is_quote_line(line: str) -> bool:
    """True for a quoted line (any indentation; also the ``...>`` flowed form)."""
    return bool(_QUOTE_RE.match(line))


# Signature-debris classifiers (the ``-- `` delimiter, "~ Name" sign-offs,
# titled/corporate contact lines, personal-identifier lines, list footers, PGP)
# and the greeting / closing sign-off strippers moved to :mod:`cleaning` — they
# remove author-typed furniture for scoring, not quoted material, so they belong
# to stage 2. The sign-off *boundary* below stays: it removes the quoted thread.


# --- attribution lines --------------------------------------------------------

# Full single-line attribution forms. Add a language by adding a pattern.
#
# Two forms are not run as single regexes, because the engine's backtracking
# over them is polynomial on adversarial lines (a line of "<a@" repeated, or of
# "schrieb:" repeated): "<who> <a@b> wrote:" (:func:`_is_address_attribution`)
# and the German "Am <date> schrieb <who>:" (:func:`_is_german_attribution`).
# Each is decided by a linear helper that accepts exactly the strings the
# original pattern (given in its docstring) accepts under ``re.match``.
_ATTRIBUTION_RES = (
    # English: "On <date>, <who> wrote:", also after a "--" ("--On <date>
    # <who> wrote:"). No word boundary before "wrote": a line-wrapped
    # attribution unwrapped by deleting the newline glues the author's address
    # straight onto it ("…example.comwrote:").
    re.compile(r"^[ \t]*(?:--)?On\b.*wrote:[ \t]*$"),
    # Japanese (Spark / Apple Mail): "2026年7月22日 18:12 +0900、<who>のメール:"
    # ("mail from <who>"). Anchored on a leading year so prose mentioning
    # someone's mail never matches.
    re.compile(r"^[ \t]*\d{4}(?:年|[/.-]).*のメール:[ \t]*$"),
)


def _dollar_line(text: str) -> str | None:
    """The span a newline-free pattern ending in ``$`` can cover, or ``None``.

    Without ``re.MULTILINE``, ``$`` matches at the end of the string or before
    a final ``\\n``. A pattern none of whose parts can match ``\\n`` (``.``,
    ``[ \\t]``, literals) can therefore only reach ``$`` when the string has
    no ``\\n`` (the whole string), or a single ``\\n`` as its last character
    (the string without it). Any other ``\\n`` makes such a pattern fail.
    """
    nl = text.find("\n")
    if nl < 0:
        return text
    if nl == len(text) - 1:
        return text[:nl]
    return None


#: The tail of the address attribution form, applied with ``match`` just after
#: the closing ``>``.
_ADDRESS_ATTRIBUTION_TAIL_RE = re.compile(r"[ \t]+wrote:[ \t]*$")


def _ends_with_bracketed_address(text: str, start: int, tail_re: re.Pattern[str]) -> bool:
    """True when ``.*<[^>]+@[^>]+>`` followed by ``tail_re`` matches ``text`` at
    ``start`` (``re.match`` semantics, ``.`` excluding ``\\n``), in linear time.

    ``tail_re`` must end in ``$`` and must not be able to match ``>``. Then the
    ``>`` of any match is the last ``>`` in ``text``, and the ``<`` lies after
    the ``>`` before it (``[^>]+`` cannot cross one), at or after ``start``,
    and before the first ``\\n`` at or after ``start`` (``.*`` cannot cross
    one). The leftmost such ``<`` leaves the widest span for ``[^>]+@[^>]+``,
    which needs an ``@`` with at least one character on each side.
    """
    gt = text.rfind(">")
    if gt < start or not tail_re.match(text, gt + 1):
        return False
    nl = text.find("\n", start)
    if nl < 0:
        nl = len(text)
    lt = text.find("<", max(start, text.rfind(">", 0, gt) + 1), min(gt, nl))
    return lt >= 0 and text.find("@", lt + 2, gt - 1) >= 0


def _is_address_attribution(line: str) -> bool:
    """``re.match(r"^.*<[^>]+@[^>]+>[ \\t]+wrote:[ \\t]*$", line)``, linear.

    "<who> <a@b> wrote:" (no leading "On"); "<who> via Datatracker <a@b>
    wrote:" and similar are covered too.
    """
    return _ends_with_bracketed_address(line, 0, _ADDRESS_ATTRIBUTION_TAIL_RE)


_AM_PREFIX_RE = re.compile(r"[ \t]*Am\b")
_SCHRIEB_RE = re.compile(r"\bschrieb\b")


def _is_german_attribution(line: str) -> bool:
    """``re.match(r"^[ \\t]*Am\\b.*\\bschrieb\\b.*:[ \\t]*$", line)``, linear.

    German: "Am <date> schrieb <who>:". No part of the pattern matches ``\\n``,
    so it applies to :func:`_dollar_line`'s span as a whole. After the prefix,
    ``.*`` can skip anything, so the condition is: a ``\\bschrieb\\b`` occurs
    (it cannot start inside the prefix, which is ``[ \\t]*Am`` followed by a
    non-word character), and the span, with trailing blanks removed, ends in
    a ``:`` at or after the end of the first occurrence.
    """
    span = _dollar_line(line)
    if span is None or not _AM_PREFIX_RE.match(span):
        return False
    schrieb = _SCHRIEB_RE.search(line)
    body = span.rstrip(" \t")
    return schrieb is not None and body.endswith(":") and len(body) - 1 >= schrieb.end()


# For wrapped attributions: a start token, optionally after ``>`` markers and
# blanks and then a "--", and a terminator that may appear on the following
# lines. Structured per language for easy extension.
_ATTRIBUTION_START_RE = re.compile(r"^[ \t>]*(?:--)?(?:(?:On|Am|Le|El|El día)\b|\d{4}(?:年|[/.-]))")
#: A wrapped attribution's terminator lies on one of this many lines below its
#: start line ...
_WRAPPED_ATTRIBUTION_MAX_LINES = 4
#: ... and the lines from the start line through the terminator line hold at
#: most this many characters in total.
_WRAPPED_ATTRIBUTION_MAX_CHARS = 400
#: The terminators, except ``\bschrieb\b.*:``, which :func:`_has_attribution_end`
#: decides separately: under ``search`` the engine retried ``.*:`` from every
#: "schrieb", which is quadratic. The remaining alternatives are literals, or
#: ``\s*`` runs that follow distinct literals, and search in linear time.
_ATTRIBUTION_END_RE = re.compile(r"(?:wrote:|\ba écrit\s*:|\bescribió:|のメール:)[ \t]*$")


def _has_attribution_end(line: str) -> bool:
    """True when ``line`` contains a wrapped attribution's terminator.

    Equivalent to ``re.search`` of the original pattern
    ``(?:wrote:|\\bschrieb\\b.*:|\\ba écrit\\s*:|\\bescribió:|のメール:)[ \\t]*$``.
    Its ``\\bschrieb\\b.*:[ \\t]*$`` alternative ends at ``$`` — the end of the
    string, or before a final ``\\n`` — so its ``:`` is the last character
    other than blanks before that point, and its "schrieb" must lie on the
    same line as the ``:``, ending at or before it. The first "schrieb" at or
    after the start of that line is the best candidate.
    """
    if _ATTRIBUTION_END_RE.search(line):
        return True
    body = (line[:-1] if line.endswith("\n") else line).rstrip(" \t")
    if not body.endswith(":"):
        return False
    colon = len(body) - 1
    schrieb = _SCHRIEB_RE.search(line, body.rfind("\n", 0, colon) + 1)
    return schrieb is not None and schrieb.end() <= colon


def is_attribution_line(line: str) -> bool:
    """True if ``line`` on its own is a complete attribution line, or a line of a
    generic attribution unit marked by :func:`_mark_generic_attributions`."""
    return (
        line.startswith(_GENERIC_ATTRIBUTION_MARK)
        or any(pat.match(line) for pat in _ATTRIBUTION_RES)
        or _is_address_attribution(line)
        or _is_german_attribution(line)
    )


def _wrapped_attribution_end(lines: list[str], i: int) -> int | None:
    """If ``lines[i]`` opens a wrapped attribution, return the index of its
    terminator line, else ``None``.

    A wrapped attribution is a start line (:data:`_ATTRIBUTION_START_RE`: "On …",
    "Am …", "Le …", "El …", a leading year, optionally after ``>`` markers and a
    "--") without a terminator of its own, followed by the first line ``j``
    within :data:`_WRAPPED_ATTRIBUTION_MAX_LINES` lines that holds a terminator
    ("wrote:", "schrieb …:", "a écrit :", "escribió:", "のメール:"), where no
    line from ``i + 1`` through ``j`` is blank or is itself a complete
    attribution line that matches :data:`_ATTRIBUTION_START_RE`, and the lines
    ``i`` through ``j`` hold at most :data:`_WRAPPED_ATTRIBUTION_MAX_CHARS`
    characters in total.
    """
    if not _ATTRIBUTION_START_RE.match(lines[i]) or _has_attribution_end(lines[i]):
        return None
    total = len(lines[i])
    for j in range(i + 1, min(i + 1 + _WRAPPED_ATTRIBUTION_MAX_LINES, len(lines))):
        if not lines[j].strip():
            return None
        total += len(lines[j])
        if total > _WRAPPED_ATTRIBUTION_MAX_CHARS:
            return None
        if _ATTRIBUTION_START_RE.match(lines[j]) and is_attribution_line(lines[j]):
            return None
        if _has_attribution_end(lines[j]):
            return j
    return None


def strip_attribution_lines(lines: list[str]) -> list[str]:
    """Drop attribution lines, wrapped forms and marked generic attribution
    lines included."""
    out: list[str] = []
    i = 0
    n = len(lines)
    while i < n:
        if is_attribution_line(lines[i]):
            i += 1
            continue
        end = _wrapped_attribution_end(lines, i)
        if end is not None:
            i = end + 1
            continue
        out.append(lines[i])
        i += 1
    return out


# --- generic attribution lines ------------------------------------------------
#
# Clients introduce a quote with many wordings the fixed forms above do not
# list ("Le 3 mars 2026 à 10:15, Ana <ana@example.org> a écrit :", "Ana Lima
# said:"). A *generic attribution unit* is one to three lines, the last ending
# in a colon, whose text names its source by a cue (an e-mail address, or a
# clock time and a date) or ends in a speech verb. A unit directly above a
# quote line, with at most one blank line between, is removed with the
# attribution lines; a unit with no quote line below can end the new text at a
# sign-off boundary (see :func:`find_signoff_boundary`).
#
# The unit ending at line ``e`` (:func:`_generic_attribution_start`):
#
# 1. Line ``e``, without trailing whitespace, ends in ":" or "：" (U+FF1A); it
#    is not a quote line and holds at most _GENERIC_ATTRIBUTION_MAX_CHARS
#    characters.
# 2. Line ``e`` is neither the terminator of a wrapped attribution starting
#    on one of the _WRAPPED_ATTRIBUTION_MAX_LINES lines above it, nor a
#    complete attribution line that matches _ATTRIBUTION_START_RE. Those are
#    fixed forms.
# 3. The unit extends upward over at most two more lines. The line above the
#    unit's first line joins when it is neither blank nor a quote line and the
#    unit's first line reads as its continuation (:func:`_continues`).
# 4. A one-line unit that is an attribution line of a fixed form is left to the
#    fixed forms.
# 5. The unit's lines, stripped and joined by single spaces, are its *text*,
#    which holds at most _GENERIC_ATTRIBUTION_MAX_CHARS characters. Its *head*
#    is the text without the final colon and without trailing spaces, tabs and
#    U+00A0. The head is not empty, does not open with a document reference
#    (_DOCUMENT_REFERENCE_RE), and does not end in a URL, "as follows",
#    "following" or "below" (_LEAD_IN_TAIL_RE). The unit is an attribution when
#    either holds:
#    - cue: the head has at most _CUE_ATTRIBUTION_MAX_WORDS words and holds an
#      e-mail address (:func:`_has_email_address`), or both a clock time
#      (_CLOCK_TIME_RE) and a date (_DATE_CUE_RE);
#    - speech verb: the head has at most _VERB_ATTRIBUTION_MAX_WORDS words,
#      ends in a speech verb (_SPEECH_VERB_RE), holds no word that introduces
#      quoted content rather than a person ("it", "this", "section", …), and
#      does not open with a bullet (_CONTENT_LEAD_IN_RE). "I wrote:" and "Ana
#      wrote:" qualify; "Section 2.3 says:" and "the text said:" do not.
#
# The unit's text is tested only when it holds at most
# _GENERIC_ATTRIBUTION_MAX_CHARS characters, which bounds the backtracking of
# the patterns above. Every other step reads a bounded number of lines around
# ``e``, so testing every line of a body is linear in its size.

#: The longest generic attribution unit, and its last line, in characters.
_GENERIC_ATTRIBUTION_MAX_CHARS = 400
#: The most words a head with a cue may hold.
_CUE_ATTRIBUTION_MAX_WORDS = 25
#: The most words a head ending in a speech verb may hold.
_VERB_ATTRIBUTION_MAX_WORDS = 8
#: The character before the "@" of an e-mail address, and the domain after it.
_EMAIL_LOCAL_CHAR_RE = re.compile(r"[\w.+=-]")
_EMAIL_DOMAIN_RE = re.compile(r"[\w-]+(?:\.[\w-]+)+")
#: A clock time: "9:30", "19:30", "19h30".
_CLOCK_TIME_RE = re.compile(r"(?<!\d)\d{1,2}(?::\d{2}|h\d{2}\b)")
#: A date cue: a four-digit year from 1900 to 2099, a day and month ("3/12",
#: "3.12", "3-12"), a CJK month ("3月"), or a day and month with dots ("3. 12.").
_DATE_CUE_RE = re.compile(
    r"(?<!\d)(?:19|20)\d{2}(?!\d)|(?<![\d.])\d{1,2}[./-]\d{1,2}(?!\d)|\d{1,2}月|\d{1,2}\.\s?\d{1,2}\."
)
#: The opening of an attribution whose rest wraps onto the next line: "On",
#: "-On", "--On", "Le", "Am", "El", "Il", "Op", "Den", "Ar", "At", "In", "Em",
#: "Dne", "W dniu", "Pe", "Às", or a digit.
_ATTRIBUTION_OPENER_RE = re.compile(
    r"^[ \t]*(?:-*On|Le|Am|El|Il|Op|Den|Ar|At|In|Em|Dne|W dniu|Pe|Às|\d)\b"
)
#: A head that opens with a document reference, optionally after "(":
#: section, sec., §, figure, fig., table, appendix, RFC, OLD, NEW, CURRENT,
#: PROPOSED.
_DOCUMENT_REFERENCE_RE = re.compile(
    r"^[ \t]*(?:\(?(?:sections?|sec\.|§|figures?|fig\.|tables?|appendix|rfc|old|new|current"
    r"|proposed)\b)",
    re.IGNORECASE,
)
#: A head that ends in a URL or in a phrase announcing text below.
_LEAD_IN_TAIL_RE = re.compile(
    r"(?:https?://|www\.)\S*$|\b(?:as follows|following|below)$", re.IGNORECASE
)
#: A speech verb at the end of a head, in English, German, French, Spanish,
#: Italian, Dutch, Scandinavian, Finnish, Polish, Portuguese, Welsh, Russian,
#: Chinese and Korean. Applied to the last _SPEECH_VERB_SPAN characters of
#: the head, which hold every alternative.
_SPEECH_VERB_RE = re.compile(
    r"(?:\b(?:wrote|writes|said|schrieb|schreibt|[ée]crit|escribi[óo]|escribe|scritto|scrisse"
    r"|scrive|schreef|schrijft|skrev|skriver|kirjoitti|napisa[łl]a?|escreveu|ysgrifennodd"
    r"|написал|написала|пишет)|写道|寫道|쓴[ \t]*글)$",
    re.IGNORECASE,
)
_SPEECH_VERB_SPAN = 24
#: A head that introduces quoted content rather than a person: a word such as
#: "it", "this", "that", "what", "text", "draft", "document", "spec",
#: "section", "rfc", "paragraph" or "figure", or a leading bullet.
_CONTENT_LEAD_IN_RE = re.compile(
    r"\b(?:it|this|that|what|text|draft|document|spec|section|rfc|paragraph|figure)\b"
    r"|^[ \t]*[*•-]",
    re.IGNORECASE,
)

#: Prefixed to every line of a generic attribution unit found above a quote
#: line (see :func:`_mark_generic_attributions`). The marked line keeps its
#: place, so the searches for a quote-header block, a divider and a sign-off
#: boundary see the body's line sequence; the line rules anchored at the start
#: of a line do not match it, and :func:`is_attribution_line` accepts it, so
#: :func:`strip_attribution_lines` removes it. :func:`normalize_body` removes
#: U+0000 from every body, so no other line carries it.
_GENERIC_ATTRIBUTION_MARK = "\x00"


def _has_email_address(text: str) -> bool:
    """``re.search(r"[\\w.+=-]+@[\\w-]+(?:\\.[\\w-]+)+", text)``, linear.

    The pattern matches when some "@" has a character of ``[\\w.+=-]`` directly
    before it and the domain pattern matches directly after it. The domain
    match from one "@" stops at the next "@", so testing every "@" reads each
    character a bounded number of times; ``search`` retried the local part from
    every character of a long run of word characters, which is quadratic.
    """
    at = text.find("@")
    while at >= 0:
        if (
            at > 0
            and _EMAIL_LOCAL_CHAR_RE.match(text, at - 1)
            and _EMAIL_DOMAIN_RE.match(text, at + 1)
        ):
            return True
        at = text.find("@", at + 1)
    return False


def _continues(above: str, line: str) -> bool:
    """True when ``line`` reads as the wrapped continuation of ``above``.

    That is when the first non-blank character of ``line`` is lowercase
    (``str.islower``), is not a letter (``str.isalpha``), or lies in U+3000 to
    U+D7AF (CJK and Hangul); or when ``above``, without trailing whitespace,
    does not end in ".", "!", "?" or ":" and opens like an attribution
    (:data:`_ATTRIBUTION_OPENER_RE`).
    """
    first = line.lstrip()[:1]
    if first and (first.islower() or not first.isalpha() or "　" <= first <= "힯"):
        return True
    tail = above.rstrip()
    return bool(tail) and tail[-1] not in ".!?:" and _ATTRIBUTION_OPENER_RE.match(above) is not None


def _unit_start(lines: list[str], e: int) -> int:
    """The first line of the generic attribution unit whose last line is
    ``lines[e]``: up to two lines above it join (step 3 of the rules above)."""
    s = e
    while s > e - 2 and s > 0:
        above = lines[s - 1]
        if not above.strip() or is_quote_line(above) or not _continues(above, lines[s]):
            break
        s -= 1
    return s


def _is_generic_attribution_text(text: str) -> bool:
    """Step 5 of the rules above, for a unit's ``text`` ending in a colon."""
    if len(text) > _GENERIC_ATTRIBUTION_MAX_CHARS:
        return False
    head = text[:-1].rstrip(" \t ")
    if not head or _DOCUMENT_REFERENCE_RE.match(head) or _LEAD_IN_TAIL_RE.search(head):
        return False
    words = len(head.split())
    if words <= _CUE_ATTRIBUTION_MAX_WORDS and (
        _has_email_address(head) or (_CLOCK_TIME_RE.search(head) and _DATE_CUE_RE.search(head))
    ):
        return True
    return (
        words <= _VERB_ATTRIBUTION_MAX_WORDS
        and _SPEECH_VERB_RE.search(head[-_SPEECH_VERB_SPAN:]) is not None
        and not _CONTENT_LEAD_IN_RE.search(head)
    )


def _generic_attribution_start(lines: list[str], e: int) -> int | None:
    """The first line of the generic attribution unit whose last line is
    ``lines[e]``, or ``None`` when no unit ends there (see the rules above)."""
    line = lines[e]
    if (
        not line.rstrip().endswith((":", "："))
        or is_quote_line(line)
        or len(line) > _GENERIC_ATTRIBUTION_MAX_CHARS
    ):
        return None
    for k in range(max(0, e - _WRAPPED_ATTRIBUTION_MAX_LINES), e):
        if _wrapped_attribution_end(lines, k) == e:
            return None
    if _ATTRIBUTION_START_RE.match(line) and is_attribution_line(line):
        return None
    s = _unit_start(lines, e)
    if s == e and is_attribution_line(line):
        return None
    if not _is_generic_attribution_text(" ".join(x.strip() for x in lines[s : e + 1])):
        return None
    return s


# --- wrap remainders ----------------------------------------------------------
#
# A client that re-wraps a quote at a narrower width without marking the new
# lines leaves the tail of a long quote line as a short unmarked line between
# two quote lines ("> … the working group has" / "consensus" / "> on …"). Such a
# line is a *wrap remainder* and is treated as a quote line when all hold:
#
# - the lines directly above and below it are quote lines;
# - the line above, without trailing whitespace, holds at least
#   _WRAP_REMAINDER_MIN_ABOVE characters and does not end in ".", "?", "!",
#   ":", ";", ")" or '"';
# - the line holds at most _WRAP_REMAINDER_MAX_WORDS words, its first
#   non-blank character is lowercase (``str.islower``), and it does not open
#   with an answer word or a URL (_ANSWER_WORD_RE).
#
# A line of three or more words, or one that opens uppercase, is left as the
# author's: inline answers between quoted lines take those shapes.

_WRAP_REMAINDER_MIN_ABOVE = 60
_WRAP_REMAINDER_MAX_WORDS = 2
#: An answer word or a URL at the start of a line.
_ANSWER_WORD_RE = re.compile(
    r"^(?:ok(?:ay)?|yes|yep|no|nope|agreed?|done|sure|fine|ack|right|true|correct|see|thanks?"
    r"|https?://|www\.)\b",
    re.IGNORECASE,
)


def _is_wrap_remainder(above: str, line: str, below: str) -> bool:
    """True when ``line``, between ``above`` and ``below``, is a wrap remainder."""
    text = line.strip()
    if not text or is_quote_line(line) or not is_quote_line(above) or not is_quote_line(below):
        return False
    tail = above.rstrip()
    if len(tail) < _WRAP_REMAINDER_MIN_ABOVE or len(text.split()) > _WRAP_REMAINDER_MAX_WORDS:
        return False
    if not text[0].islower() or _ANSWER_WORD_RE.match(text):
        return False
    return tail[-1] not in '.?!:;)"'


def _mark_generic_attributions(text: str) -> str:
    """Mark the generic attribution units and wrap remainders of ``text``.

    Every line of a generic attribution unit whose last line is followed by a
    quote line, directly or after one blank line, is prefixed with
    :data:`_GENERIC_ATTRIBUTION_MARK`; a wrap remainder is prefixed with
    ``"> "`` instead (a line that is both is a wrap remainder). Each unit and
    remainder is decided on the unmarked lines. The line count is unchanged.
    """
    lines = text.split("\n")
    n = len(lines)
    prefix = [""] * n
    for e in range(n):
        j = e + 1
        if j < n and not lines[j].strip():
            j += 1
        if j >= n or not is_quote_line(lines[j]):
            continue
        start = _generic_attribution_start(lines, e)
        if start is not None:
            for k in range(start, e + 1):
                prefix[k] = _GENERIC_ATTRIBUTION_MARK
    for i in range(1, n - 1):
        if _is_wrap_remainder(lines[i - 1], lines[i], lines[i + 1]):
            prefix[i] = "> "
    return "\n".join(p + line for p, line in zip(prefix, lines))


def _attribution_end_at(lines: list[str], k: int) -> int | None:
    """The last line of an attribution that starts at ``lines[k]``, or ``None``.

    The attribution is, in order of precedence: a run of marked generic
    attribution lines (to the last consecutive marked line); a complete
    attribution line (``k`` itself); a wrapped attribution
    (:func:`_wrapped_attribution_end`); or a generic attribution unit that
    starts at ``k``, whether or not a quote line follows it. For the last, the
    first line ending in a colon among ``lines[k]`` and the two lines below it
    must be the unit's last line, and no line before it may be blank or a
    quote line.
    """
    if lines[k].startswith(_GENERIC_ATTRIBUTION_MARK):
        e = k
        while e + 1 < len(lines) and lines[e + 1].startswith(_GENERIC_ATTRIBUTION_MARK):
            e += 1
        return e
    if is_attribution_line(lines[k]):
        return k
    end = _wrapped_attribution_end(lines, k)
    if end is not None:
        return end
    for e in range(k, min(k + 3, len(lines))):
        if not lines[e].strip() or is_quote_line(lines[e]):
            return None
        if lines[e].rstrip().endswith((":", "：")):
            return e if _generic_attribution_start(lines, e) == k else None
    return None


# --- sign-off boundary ----------------------------------------------------------

# Fully top-posted replies — Gmail's default, and the typical shape of
# AI-generated mail — end with a short salutation plus the author's name, then an
# attribution line introducing the quoted message *without* ``>`` markers. The
# quote/attribution filters see nothing to drop there, so without this boundary
# the entire quoted thread ships as "new text". A salutation alone is weak (a
# mid-thread "Thanks." is common prose); the name line is the strong signal, and
# the attribution that follows is what proves quoted material comes next.

# A line that is only a sign-off salutation (optional trailing , . or !).
# Ordered longest-first; add a salutation (or a language) by extending the list.
_SIGNOFF_SALUTATIONS = (
    # English
    r"all\s+the\s+best|best\s+(?:regards|wishes)|kind\s+regards|warm(?:est)?\s+regards|"
    r"many\s+thanks|thanks\s+again|thank\s+you|yours(?:\s+(?:sincerely|truly|faithfully))?|"
    r"best|cheers|thanks|thx|regards|sincerely|respectfully|cordially|br|rgds"
    # German
    r"|mit\s+freundlichen\s+gr(?:ü|ue)(?:ß|ss)en|freundliche\s+gr(?:ü|ue)(?:ß|ss)e|"
    r"(?:viele|beste|liebe)\s+gr(?:ü|ue)(?:ß|ss)e|gr(?:ü|ue)(?:ß|ss)e?|mfg"
    # French
    r"|(?:bien\s+)?cordialement|amicalement|amiti(?:é|e)s|salutations"
    # Spanish
    r"|saludos(?:\s+cordiales)?|un\s+saludo|atentamente"
    # Dutch
    r"|met\s+vriendelijke\s+groet(?:en)?|groet(?:en|jes)?|mvg"
    # Māori (common in NZ English mail)
    r"|ng(?:ā|a)\s+mihi(?:\s+nui)?|noho\s+ora\s+mai"
    # Nordic / other
    r"|v(?:ä|a)nliga\s+h(?:ä|a)lsningar|med\s+v(?:e|ä)nlig\s+h(?:i|ä)lsen|mvh|terveisin"
    r"|cumprimentos|atenciosamente|ciao|saluti|cordiali\s+saluti"
)
# The tail is ``[ \t]*(?:[,.!][ \t]*)?$``, which accepts the same strings as
# the original ``[ \t]*[,.!]?[ \t]*$``: written that way, two adjacent
# ``[ \t]*`` runs split a long blank run in quadratically many ways before a
# non-blank character fails the line.
_SIGNOFF_LINE_RE = re.compile(
    rf"^[ \t]*(?:{_SIGNOFF_SALUTATIONS})[ \t]*(?:[,.!][ \t]*)?$", re.IGNORECASE
)

# The one-line form: "Cheers, Peter" — salutation, comma, then the name.
_SIGNOFF_INLINE_RE = re.compile(
    rf"^[ \t]*(?:{_SIGNOFF_SALUTATIONS})[ \t]*,[ \t]*(?P<name>\S.*)$", re.IGNORECASE
)

# One word of a personal name: starts with a letter, then letters and the
# joiners that appear in names ("Nguyen-Huu", "O'Brien", "J."). Digits are
# rejected separately in :func:`is_signoff_name_line`.
_NAME_TOKEN_RE = re.compile(r"^[^\W\d_][\w'’.\-]*$")


def is_signoff_name_line(text: str) -> bool:
    """True if ``text`` is plausibly just a person's name (1–4 capitalized-ish
    words, no digits, short). Case-marked scripts only — the first token must
    start uppercase, which keeps ordinary prose from qualifying.
    """
    s = text.strip()
    if not s or len(s) > 40 or any(ch.isdigit() for ch in s):
        return False
    tokens = s.split()
    if not 1 <= len(tokens) <= 4:
        return False
    if not tokens[0][0].isupper():
        return False
    return all(_NAME_TOKEN_RE.match(tok) for tok in tokens)


# A name line with a short title/affiliation tail after "|" or "," —
# "Thi Nguyen-Huu | CEO", "Louis Navarre, UCLouvain". Only meaningful where a
# sign-off is expected; on its own such a line is indistinguishable from prose.
_NAME_TITLE_SEP_RE = re.compile(r"[|,]")


def _is_signoff_name_title_line(text: str) -> bool:
    """True for a sign-off name line carrying a title/affiliation tail."""
    parts = _NAME_TITLE_SEP_RE.split(text.strip(), maxsplit=1)
    if len(parts) != 2:
        return False
    name, title = parts[0].strip(), parts[1].strip()
    if not title or len(title) > 40 or len(title.split()) > 4:
        return False
    return is_signoff_name_line(name)


def _is_signoff_name_like(text: str) -> bool:
    """A plausible sign-off name line: bare name, or name + title/affiliation."""
    return is_signoff_name_line(text) or _is_signoff_name_title_line(text)


def _signoff_name_index(lines: list[str], i: int) -> int | None:
    """If a sign-off opens at ``lines[i]``, return the index of its name line.

    Both forms are recognized: a salutation line whose name follows on the next
    non-blank line ("Best,\\n\\nSongbo" → the "Songbo" index), and the one-line
    form ("Cheers, Peter" → ``i`` itself). The name may carry a title/affiliation
    tail ("Thi Nguyen-Huu | CEO"). ``None`` when ``lines[i]`` is not a sign-off
    with a plausible name.
    """
    if _SIGNOFF_LINE_RE.match(lines[i]):
        j = i + 1
        n = len(lines)
        while j < n and not lines[j].strip():
            j += 1
        if j < n and _is_signoff_name_like(lines[j]):
            return j
        return None
    m = _SIGNOFF_INLINE_RE.match(lines[i])
    if m and _is_signoff_name_like(m.group("name")):
        return i
    return None


def find_signoff_boundary(lines: list[str]) -> int | None:
    """Return the index just past a sign-off (salutation + name) that is directly
    followed by an attribution introducing unmarked text, or ``None``.

    The attribution is the next non-blank line after the name and is recognized
    by :func:`_attribution_end_at` (a fixed form, a wrapped form or a generic
    attribution unit). It must not be followed, after any blank lines, by a
    quote line: an attribution above ``>``-quoted text heads an interleaved
    quote, and the author's inline replies may follow it.

    All three parts are required, so neither a mid-thread "Thanks." (no name), a
    sign-off at the true end of a message (nothing after), nor a sign-off
    followed by authored content (a "Ps." block) ever truncates.
    """
    n = len(lines)
    for i in range(n):
        name_idx = _signoff_name_index(lines, i)
        if name_idx is None:
            continue
        k = name_idx + 1
        while k < n and not lines[k].strip():
            k += 1
        if k >= n:
            continue
        end = _attribution_end_at(lines, k)
        if end is None:
            continue
        q = end + 1
        while q < n and not lines[q].strip():
            q += 1
        if q < n and is_quote_line(lines[q]):
            continue
        return name_idx + 1
    return None


def strip_after_signoff_boundary(lines: list[str]) -> list[str]:
    """Truncate everything after a sign-off that an attribution line follows.

    This removes the *quoted thread* the sign-off precedes — the sign-off line
    itself is kept in stage 1 and removed later by :mod:`cleaning`.
    """
    idx = find_signoff_boundary(lines)
    return lines if idx is None else lines[:idx]


# --- forwarded / top-post quote-header blocks ---------------------------------

# Outlook, and forwarding clients generally, introduce quoted content with a
# block of pseudo-RFC5322 headers instead of "> " markers — e.g.
#
#     From: Wesley Eddy via Datatracker <noreply@example.org>
#     Sent: Tuesday, June 23, 2026 3:45 PM
#     To: tsv-art@example.org
#     Subject: [Int-area] … Tsvart review
#
# followed by the entire quoted message with *no* leading ``>``. Everything from
# such a block to the end of the body is quoted content the author did not write.
#
# Chinese clients (Alibaba Mail, Chinese Outlook) localize the labels — 发件人 /
# 发送时间 / 收件人 / 抄送 / 主题 — often with a full-width colon (：) and no space
# after it, and 主题 sometimes padded with an ideographic space (主　题). Alibaba
# Mail additionally draws a long dashed divider line directly above the block.

# Latin-script Outlook localizations translate the labels, and French
# typography puts a space before the colon — in stored text often U+00A0
# (no-break space) or U+202F (narrow no-break space), as in ``De\xa0: …``:
#
#     De : Mario Loffredo <mario@example.org>
#     Envoyé : mercredi 9 septembre 2026 10:38
#     À : regext-chairs@example.org
#     Objet : Re: [regext] …
#
# The opener labels (French/Spanish/Portuguese ``De``, Italian ``Da``, Dutch
# ``Van``, German ``Von``) are ordinary words, so a localized opener qualifies
# only when the field run below it also carries its language's subject label
# (see :data:`_LOCALIZED_SUBJECT_RE`), in addition to the rules every block
# meets.

#: Whitespace accepted around a localized label's colon: space, tab, U+00A0,
#: U+202F.
_LABEL_SPACE = "[ \t\u00a0\u202f]"

#: The localized opener labels ("From" translated).
_LOCALIZED_FROM_LABELS = r"De|Da|Van|Von"

# The block always opens with a ``From:`` (or localized 发件人) line, or with a
# Latin-script localized opener (:data:`_LOCALIZED_FROM_LINE_RE`).
_FROM_LINE_RE = re.compile(r"^[ \t]*(?:From:|发件人[ \t]*[:：])[ \t]*\S")

#: A Latin-script localized opener line ("De : …", "Da: …", "Van: …", "Von: …"),
#: which qualifies only with the subject-label guard.
_LOCALIZED_FROM_LINE_RE = re.compile(
    rf"^[ \t]*(?:{_LOCALIZED_FROM_LABELS}){_LABEL_SPACE}*:{_LABEL_SPACE}*\S"
)

#: Localized field labels other than the opener: French (Envoyé/Envoye, Date,
#: À/A, Cc, Cci, Objet), Spanish (Enviado / Enviado el, Fecha, Para, CC, CCO,
#: Asunto), Portuguese (Enviada em / Enviado em, Data, Para, Cc, Assunto),
#: Italian (Inviato, Data, A, Cc, Oggetto), Dutch (Verzonden, Datum, Aan, CC,
#: Onderwerp), German (Gesendet, Datum, An, Cc, Betreff).
_LOCALIZED_FIELD_LABELS = (
    r"Envoy[ée]|Date|[ÀA]|Cci?|Objet"
    r"|Enviad[oa](?:[ \t]+(?:el|em))?|Fecha|Para|CCO|Asunto|Assunto"
    r"|Data|Inviato|Oggetto"
    r"|Verzonden|Datum|Aan|Onderwerp"
    r"|Gesendet|An|Betreff"
)

#: A localized field line, colon spacing included (``Envoyé\xa0: …``).
_LOCALIZED_FIELD_RE = re.compile(
    rf"^[ \t]*(?i:{_LOCALIZED_FIELD_LABELS}|{_LOCALIZED_FROM_LABELS})"
    rf"{_LABEL_SPACE}*:(?:{_LABEL_SPACE}|$)"
)

#: The subject label a localized block must carry: Objet (French), Asunto
#: (Spanish), Assunto (Portuguese), Oggetto (Italian), Onderwerp (Dutch),
#: Betreff (German). Every Outlook quote header carries a subject line; prose
#: that happens to open with "De :" or "Van:" followed by "A:" / "Data:" lines
#: does not carry one.
_LOCALIZED_SUBJECT_RE = re.compile(
    rf"^[ \t]*(?i:Objet|Asunto|Assunto|Oggetto|Onderwerp|Betreff)"
    rf"{_LABEL_SPACE}*:(?:{_LABEL_SPACE}|$)"
)

# A CJK header label: a CJK char, then up to 10 more CJK chars / ideographic
# spaces (U+3000, as in 主　题), then an ASCII or full-width colon. No whitespace
# is required after the colon — a full-width colon cannot occur in a URL scheme,
# and an ASCII colon is safe here because the label itself is CJK.
_CJK_HEADER_LABEL = r"[\u4e00-\u9fff][\u4e00-\u9fff\u3000]{0,10}[ \t]*[:：]"

# Any RFC5322-looking header field ("Name: …" / "Name:"), ASCII or CJK-labeled.
# Used to (a) delimit the header run above a ``From:`` that
# :func:`_pasted_header_runs` inspects for transport fields, and (b) walk the
# run once a real block has started. Requiring whitespace or end-of-line after
# an ASCII label's colon keeps "https://…" and other scheme-like prose (no
# space after the colon) from matching.
_HEADER_FIELD_RE = re.compile(
    rf"^[ \t]*(?:[A-Za-z][A-Za-z-]{{0,40}}:(?:[ \t]|$)|{_CJK_HEADER_LABEL})"
)

# The fields that mark a run of headers as an email quote header (rather than a
# stray "Foo:" line). Structured as an alternation so more clients / languages
# are easy to add (German: Gesendet/An/Betreff; Chinese: 发送时间/收件人/抄送/
# 日期/主题, the latter also as 主　题).
_QUOTE_HEADER_SIGNAL_RE = re.compile(
    r"^[ \t]*(?:(?:Sent|Date|To|Cc|Bcc|Reply-To|Subject"
    r"|Gesendet|An|Betreff)[ \t]*:(?:[ \t]|$)"
    r"|(?:发送时间|收件人|抄送人?|日期|主\u3000?题)[ \t]*[:：])",
    re.IGNORECASE,
)


# Transport/trace fields that a mail client's quote header never contains
# (Outlook and its kin emit only From/Sent/Date/To/Cc/Subject). Their presence
# in the header run directly above a ``From:`` marks the run as pasted header
# *evidence* — a message quoted with its headers included, as in the
# threadstarter-rfc2047-header fixture. A signature or banner line above the
# ``From:`` (``Tel:``, ``Email:``, ``Classification:``) is header-shaped but
# not transport, and does not disqualify the block.
_TRANSPORT_HEADER_RE = re.compile(
    r"^[ \t]*(?:Message-ID|References|In-Reply-To|Received|Return-Path"
    r"|Resent-[A-Za-z-]+)[ \t]*:",
    re.IGNORECASE,
)


def _pasted_header_runs(lines: list[str]) -> list[bool]:
    """For every index ``i``, whether the contiguous header-field run directly
    above ``lines[i]`` contains a transport field: a ``From:`` at ``i`` then
    sits inside pasted header evidence rather than opening a client's quote
    header.

    Computed in one forward pass — the run above ``i`` is ``lines[i - 1]``
    plus, when that line is a header field, the run above ``i - 1`` — instead
    of walking the run upward from every ``From:``, which is quadratic when
    many ``From:`` lines follow a long run of fields.
    """
    pasted = [False] * len(lines)
    for i in range(1, len(lines)):
        above = lines[i - 1]
        pasted[i] = _HEADER_FIELD_RE.match(above) is not None and (
            _TRANSPORT_HEADER_RE.match(above) is not None or pasted[i - 1]
        )
    return pasted


def _header_walk_table(
    lines: list[str], is_localized: bool, tolerant: bool
) -> list[tuple[int, bool]]:
    """The field walk of :func:`find_quote_header_block`, for every start.

    The walk from an opener at ``i`` visits ``lines[i + 1:]`` and stops at the
    first line that is neither a header field, nor a folded continuation, nor
    (``tolerant`` only) a single blank directly above a field. Whether it stops
    at a line depends only on that line, the next one and the mode
    (``is_localized``, ``tolerant``), never on where the walk started. Entry
    ``j`` (``0 <= j <= len(lines)``) holds ``(fields, has_signal)`` for a walk
    entering at ``j``: the number of field lines it passes and whether one of
    them is a signal field. Built backward in one pass, so each opener is
    decided in constant time; rewalking from every opener is quadratic when
    many openers share one long run.
    """

    def is_field(text: str) -> bool:
        if _HEADER_FIELD_RE.match(text):
            return True
        return is_localized and _LOCALIZED_FIELD_RE.match(text) is not None

    signal_re = _LOCALIZED_SUBJECT_RE if is_localized else _QUOTE_HEADER_SIGNAL_RE
    n = len(lines)
    field = [is_field(line) for line in lines]
    table: list[tuple[int, bool]] = [(0, False)] * (n + 1)
    for j in range(n - 1, -1, -1):
        nxt = lines[j]
        fields, has_signal = table[j + 1]
        if field[j]:
            table[j] = (fields + 1, has_signal or signal_re.match(nxt) is not None)
        elif nxt.strip() and (tolerant or nxt[:1] in (" ", "\t")):
            # Folded continuation of the preceding header field.
            table[j] = (fields, has_signal)
        elif not nxt.strip() and tolerant and j + 1 < n and field[j + 1]:
            # Double-spaced rendering: a single blank between fields.
            table[j] = (fields, has_signal)
        # Otherwise the walk stops at j: the entry stays (0, False).
    return table


def find_quote_header_block(lines: list[str], *, localized: bool = True) -> int | None:
    """Return the index of the ``From:`` line that opens a forwarded/quote-header
    block, or ``None`` if the body has no such block.

    A qualifying block: a ``From:`` line that does not sit inside pasted header
    evidence (see :func:`_pasted_header_runs`), followed by at least one more
    header line, at least one of which is a quote-header signal field
    (``Sent:``/``Date:``/``To:``/``Cc:``/``Subject:``/German/Chinese).

    A Latin-script localized block (French ``De :``, Spanish/Portuguese ``De:``,
    Italian ``Da:``, Dutch ``Van:``, German ``Von:``) qualifies when its opener
    is followed by at least two more field lines — localized labels with any
    colon spacing accepted (see :data:`_LOCALIZED_FIELD_RE`) — one of which is
    its language's subject label (:data:`_LOCALIZED_SUBJECT_RE`). The opener
    labels are ordinary words, so the subject label is required where the
    English rule accepts any signal field. ``localized=False`` recognizes only
    the English, German-label and Chinese blocks (the rule before generation 6).

    When the ``From:`` line carries an address (``@``), the walk tolerates the
    two mangled renderings mailing-list archives produce: folded header lines
    that lost their leading whitespace ("From: … On\\nBehalf Of …",
    ``<mailto:…>`` tails) read as continuations, and a single blank line
    between fields (a double-spaced rendering) does not end the run when
    another header field follows directly. Without an address the classic
    strict walk applies — header fields and whitespace-indented continuations
    only — so a prose line that happens to start "From: …" cannot reach across
    its paragraph for a signal field (see :func:`_header_walk_table`).

    The pasted-evidence flags and each walk mode's table are built once, on
    first use, so the search is linear in the number of lines.
    """
    pasted: list[bool] | None = None
    tables: dict[tuple[bool, bool], list[tuple[int, bool]]] = {}
    for i, line in enumerate(lines):
        if _FROM_LINE_RE.match(line):
            is_localized = False
        elif localized and _LOCALIZED_FROM_LINE_RE.match(line):
            is_localized = True
        else:
            continue
        if pasted is None:
            pasted = _pasted_header_runs(lines)
        if pasted[i]:
            continue
        mode = (is_localized, "@" in line)
        if mode not in tables:
            tables[mode] = _header_walk_table(lines, *mode)
        fields, has_signal = tables[mode][i + 1]
        if has_signal and 1 + fields >= (3 if is_localized else 2):
            return i
    return None


# The separator Alibaba Mail draws directly above its quote-header block. Only
# consulted for the line left dangling at the truncation point, so mid-body
# dashed rules (digest separators) are never touched.
_QUOTE_HEADER_DIVIDER_RE = re.compile(r"^[ \t]*-{6,}[ \t]*$")


def strip_after_quote_header_block(lines: list[str], *, localized: bool = True) -> list[str]:
    """Truncate everything from a forwarded/quote-header block to the end.

    A dashed divider line left dangling directly above the removed block (the
    Alibaba Mail style), or failing that a bare "Original" line (the QQ Mail
    and Foxmail style, :data:`_BARE_ORIGINAL_RE`), is client furniture, not
    author content, and is dropped with the block. ``localized`` is passed to
    :func:`find_quote_header_block`.
    """
    idx = find_quote_header_block(lines, localized=localized)
    if idx is None:
        return lines
    out = lines[:idx]
    while out and not out[-1].strip():
        out.pop()
    if out and (_QUOTE_HEADER_DIVIDER_RE.match(out[-1]) or _BARE_ORIGINAL_RE.match(out[-1])):
        out.pop()
    return out


# --- custom cleanup pass ------------------------------------------------------


# --- "Original message" / "Forwarded message" dividers -------------------------

# The old-fashioned dashed divider some clients (Android Mail, Samsung, and
# Outlook's "-----Original Message-----") put above the quoted message instead
# of ``>`` markers or a bare header block. Everything from the divider on is the
# quoted message. Matched with ``search`` rather than ``match``: HTML-to-text
# conversion sometimes flattens the whole quoted mail onto ONE line, gluing the
# divider to the end of the author's text ("…questions.-------- Original
# message --------From: …"), so the divider can sit mid-line — the author's
# prefix before it is kept. Dashes are required on BOTH sides, so prose that
# merely mentions an "original message" never matches. Chinese clients localize
# the wording: 邮件原件 (Chinese Outlook) and 原始邮件 (QQ Mail / Foxmail).
#
# The leading ``(?<!-)`` (here and in :data:`_LOCALIZED_DIVIDER_RE`) lets a
# match start only at the first dash of a run. It changes no result: the
# wording cannot start with a dash, so a match from inside a run has its
# ``-{2,}`` end at the run's end, and the same match extended to the run's
# first dash is found first. Without it, ``search`` retried ``-{2,}`` from
# every dash of a long rule, which is quadratic (a 10,000-dash line took
# seconds).
_ORIGINAL_MESSAGE_DIVIDER_RE = re.compile(
    r"(?<!-)-{2,}[ \t]*(?:(?:Original|Forwarded)[ \t]+Message|邮件原件|原始邮件)[ \t]*-{2,}",
    re.IGNORECASE,
)

# Latin-script localizations of the same divider: French "Message d'origine"
# (straight or curly apostrophe) and "Message transféré", Spanish "Mensaje
# original" / "Mensaje reenviado", Portuguese "Mensagem original" / "Mensagem
# encaminhada", Italian "Messaggio originale" / "Messaggio inoltrato", Dutch
# "Oorspronkelijk bericht" / "Doorgestuurd bericht", German "Ursprüngliche
# Nachricht" / "Weitergeleitete Nachricht". Dashes are required on both sides,
# as above; the spacing may include U+00A0 / U+202F.
_LOCALIZED_DIVIDER_RE = re.compile(
    rf"(?<!-)-{{2,}}{_LABEL_SPACE}*(?:"
    rf"Message{_LABEL_SPACE}+d['’]origine|Message{_LABEL_SPACE}+transf[ée]r[ée]"
    rf"|Mensaje{_LABEL_SPACE}+(?:original|reenviado)"
    rf"|Mensagem{_LABEL_SPACE}+(?:original|encaminhada)"
    rf"|Messaggio{_LABEL_SPACE}+(?:originale|inoltrato)"
    rf"|(?:Oorspronkelijk|Doorgestuurd){_LABEL_SPACE}+bericht"
    rf"|(?:Urspr(?:ü|ue)ngliche|Weitergeleitete){_LABEL_SPACE}+Nachricht"
    rf"){_LABEL_SPACE}*-{{2,}}",
    re.IGNORECASE,
)


# The short "Original" dividers of QQ Mail, Foxmail and similar clients:
# "------------------ Original ------------------" (the spacing often an
# HTML-rendered "&nbsp;"), "--- Original Message(s) ---" and the Chinese
# 邮件原文, with dashes on both sides as above; the spacing may include U+00A0,
# U+3000 or a literal "&nbsp;". The ``(?<!-)`` is the same linear-time guard.
_DIVIDER_SPACE = r"(?:[ \t\u00a0\u3000]|&nbsp;)"
_SHORT_ORIGINAL_DIVIDER_RE = re.compile(
    rf"(?<!-)-{{2,}}{_DIVIDER_SPACE}*(?:Original(?:{_DIVIDER_SPACE}+Messages?)?|邮件原文)"
    rf"{_DIVIDER_SPACE}*-{{2,}}",
    re.IGNORECASE,
)
#: A bare "Original" line, which QQ Mail and Foxmail draw above their quote
#: header in place of a dashed divider.
_BARE_ORIGINAL_RE = re.compile(r"^[ \t]*Original[ \t]*$")
#: The ``From:`` (or 发件人) line a bare "Original" line must stand above.
_BARE_ORIGINAL_FROM_RE = re.compile(r"^[ \t*]*(?:From|发件人)[ \t]*[:：]")
#: A note that the author answers inline ("replies inline", "in-line").
_INLINE_NOTE_RE = re.compile(r"\bin-?line\b", re.IGNORECASE)


def _is_bare_original_divider(lines: list[str], i: int) -> bool:
    """True when ``lines[i]`` is a bare "Original" line and the first non-blank
    line among the four below it is a ``From:`` / 发件人 line."""
    if not _BARE_ORIGINAL_RE.match(lines[i]):
        return False
    for line in lines[i + 1 : i + 5]:
        if line.strip():
            return _BARE_ORIGINAL_FROM_RE.match(line) is not None
    return False


def _find_divider(line: str, *, localized: bool = True, short: bool = True) -> re.Match[str] | None:
    """The first "Original message" divider in ``line``, or ``None``.

    ``short=False`` leaves out the short "Original" forms
    (:data:`_SHORT_ORIGINAL_DIVIDER_RE`).
    """
    found = [m for m in (_ORIGINAL_MESSAGE_DIVIDER_RE.search(line),) if m]
    if short:
        found += [m for m in (_SHORT_ORIGINAL_DIVIDER_RE.search(line),) if m]
    if localized:
        found += [m for m in (_LOCALIZED_DIVIDER_RE.search(line),) if m]
    return min(found, key=lambda m: m.start()) if found else None


def strip_after_original_message_divider(lines: list[str], *, localized: bool = True) -> list[str]:
    """Truncate everything from a dashed "Original/Forwarded message" divider.

    The line holding the divider is dropped too, except that a non-empty author
    prefix glued before a mid-line divider survives. ``localized=False``
    recognizes only the English and Chinese wordings of the dashed form (the
    rule before generation 6); the short "Original" forms apply either way.

    - A divider on a quote line is quoted content and never truncates.
    - A bare "Original" line above a ``From:`` line
      (:func:`_is_bare_original_divider`) truncates like a divider, the line
      itself included.
    - Once a line above holds an inline note (:data:`_INLINE_NOTE_RE`: a quote
      line, or a line without a divider), the short forms are ignored: the
      author announced replies written inside the quoted message below them.
      A divider of the other forms still truncates.
    """
    inline_note = False
    for i, line in enumerate(lines):
        if is_quote_line(line):
            if not inline_note and _INLINE_NOTE_RE.search(line):
                inline_note = True
            continue
        if not inline_note and _is_bare_original_divider(lines, i):
            return lines[:i]
        m = _find_divider(line, localized=localized)
        if m is None:
            if not inline_note and _INLINE_NOTE_RE.search(line):
                inline_note = True
            continue
        if inline_note and _find_divider(line, localized=localized, short=False) is None:
            continue
        out = lines[:i]
        prefix = line[: m.start()].rstrip()
        if prefix:
            out.append(prefix)
        return out
    return lines


# --- Outlook boundary lines -----------------------------------------------------

#: A line Outlook draws above a quoted message: an optional space, then at
#: least seven underscores or hyphens.
_OUTLOOK_BOUNDARY_RE = re.compile(r" ?[_-]{7,}")


def _separate_outlook_boundaries(lines: list[str]) -> list[str]:
    """Insert a blank line between a non-empty line and an Outlook boundary line
    (:data:`_OUTLOOK_BOUNDARY_RE`) directly below it, so the boundary never
    reads as part of the paragraph above it."""
    out: list[str] = []
    for line in lines:
        if out and out[-1] and _OUTLOOK_BOUNDARY_RE.match(line):
            out.append("")
        out.append(line)
    return out


def custom_clean(text: str, *, localized: bool = True) -> str:
    """Apply the stage-1 quote/attribution cleanup to ``text``.

    Composable steps, in order: separate Outlook boundary lines from the line
    above (:func:`_separate_outlook_boundaries`), truncate at a
    forwarded/quote-header block, an "Original message" divider, or a sign-off
    boundary (which must run while the attribution evidence is still present),
    drop attribution lines (wrapped forms and marked generic attribution lines
    included), then filter out quoted lines. Signature blocks, greetings
    and sign-offs are **kept** — they are the author's own furniture and are
    removed later by :mod:`cleaning`. Blank-line structure is left as-is; the
    tolerant test comparison ignores it.

    ``localized=False`` recognizes only the quote-header blocks and dividers
    of generation 5 (English, German labels, Chinese), not the Latin-script
    localizations; :func:`_core_extract` passes it for an inline reply written
    below a localized block (see :func:`_localized_inline_reply`).
    """
    lines = _separate_outlook_boundaries(text.split("\n"))
    lines = strip_after_quote_header_block(lines, localized=localized)
    lines = strip_after_original_message_divider(lines, localized=localized)
    lines = strip_after_signoff_boundary(lines)
    lines = strip_attribution_lines(lines)
    lines = [line for line in lines if not is_quote_line(line)]
    # Trim per-line trailing whitespace and blank edge lines, but preserve the
    # first content line's indentation (significant for digest tables).
    # (Leading blanks are sliced off at once: popping them one by one from the
    # front of the list is quadratic in their number.)
    lines = [line.rstrip() for line in lines]
    first = next((i for i, line in enumerate(lines) if line), len(lines))
    lines = lines[first:]
    while lines and not lines[-1]:
        lines.pop()
    return "\n".join(lines)


def _nonblank_line_count(text: str) -> int:
    return sum(1 for line in text.split("\n") if line.strip())


# --- parent-diff assist -------------------------------------------------------

# Some clients top-post a reply above the *entire* previous message reproduced
# with no ``>`` markers, no attribution, and no forwarded-header block — the
# quote/attribution/header filters have nothing to grab. When the thread parent
# is on hand, we can instead prove which child lines came from it by diffing.
#
# The input is untrusted, so every step runs in time linear in the size of the
# child and the parent: rule (a) aligns the line sequences around their longest
# common run (:func:`_aligned_run_marks`), and rules (b) and (c) answer their
# substring tests from an index built once per call (:class:`_ParentStream`).
# The bar for calling a line "parent content" is deliberately high: a matching
# block must carry real substance (see the two thresholds below), so short
# coincidental echoes that also appear in the parent — greetings ("Hi all,"),
# the author's own sign-off salutation and name line ("Best," / "Songbo"), list
# courtesy phrases — are NOT deleted.
# The author's sign-off in particular is guaranteed to also sit inside the
# parent's nested quotes, and it must survive.

#: An aligned run of matching lines is parent content only with this much
#: substance: the whole block totals at least this many normalized words ...
_PARENT_DIFF_MIN_BLOCK_WORDS = 10
#: ... OR the block contains at least one line of at least this many words. The
#: same per-line floor gates the rewrap rule (a single long line is strong
#: evidence on its own).
_PARENT_DIFF_MIN_LINE_WORDS = 8
#: The continuation rule chains only off at least this many already-marked
#: lines. Below the floor the marks may be an author's short inline citation
#: (1-2 legitimately matching lines), and chaining would extend the deletion
#: into the author's own text; a real re-wrapped quoted block seeds far more.
_PARENT_DIFF_CONTINUATION_MIN_SEEDS = 3


def _normalize_line_for_diff(line: str) -> str:
    """Normalize one line for parent/child comparison.

    Strip a leading quote-marker prefix (via :data:`_QUOTE_RE`, so a quoted copy
    of a parent line compares equal to the bare parent line), then collapse all
    whitespace runs to single spaces and strip the edges. Blank/whitespace-only
    lines normalize to the empty string and are ignored by callers.
    """
    return " ".join(_QUOTE_RE.sub("", line).split())


# --- suffix automaton -------------------------------------------------------------
#
# One routine serves rules (a), (b) and (c): the suffix automaton of a sequence
# of integer symbols (line ids for rule (a), character code points for rules
# (b) and (c)). It recognizes exactly the contiguous subsequences of its text,
# has at most 2n states and 3n transitions for a text of n symbols, and is
# built in O(n) map operations.


def _build_automaton(
    seq: list[int], lo: int, hi: int
) -> tuple[list[int], list[int], list[dict[int, int]], list[int]]:
    """Suffix automaton of ``seq[lo:hi]``.

    Returns, per state: the length of its longest string, its suffix link, its
    transitions (symbol -> state) and ``first_end``, the smallest end position
    (an index of ``seq``) of an occurrence of the state's strings. State 0 is
    the root. A clone copies ``first_end`` from the state it splits: the
    clone's occurrences are that state's plus later positions only.
    """
    length = [0]
    link = [-1]
    trans: list[dict[int, int]] = [{}]
    first_end = [-1]
    last = 0
    for pos in range(lo, hi):
        sym = seq[pos]
        cur = len(length)
        length.append(length[last] + 1)
        link.append(-1)
        trans.append({})
        first_end.append(pos)
        p = last
        while p != -1 and sym not in trans[p]:
            trans[p][sym] = cur
            p = link[p]
        if p == -1:
            link[cur] = 0
        else:
            q = trans[p][sym]
            if length[p] + 1 == length[q]:
                link[cur] = q
            else:
                clone = len(length)
                length.append(length[p] + 1)
                link.append(link[q])
                trans.append(dict(trans[q]))
                first_end.append(first_end[q])
                while p != -1 and trans[p].get(sym) == q:
                    trans[p][sym] = clone
                    p = link[p]
                link[q] = clone
                link[cur] = clone
        last = cur
    return length, link, trans, first_end


def _walk(
    automaton: tuple[list[int], list[int], list[dict[int, int]], list[int]],
    seq: list[int],
    lo: int,
    hi: int,
) -> tuple[list[int], list[int]]:
    """For each ``e`` in ``[lo, hi)``: the length of the longest run of ``seq``
    ending at ``e`` that occurs in the automaton's text, and the state of that
    run (both indexed ``e - lo``). Linear: each step adds at most one to the
    length, and every suffix-link hop removes at least one."""
    length, link, trans, _first_end = automaton
    runs: list[int] = []
    states: list[int] = []
    state = 0
    matched = 0
    for e in range(lo, hi):
        sym = seq[e]
        while state != 0 and sym not in trans[state]:
            state = link[state]
            matched = length[state]
        if sym in trans[state]:
            state = trans[state][sym]
            matched += 1
        else:
            matched = 0
        runs.append(matched)
        states.append(state)
    return runs, states


# --- rule (a): alignment around the longest common run ------------------------------
#
# Inputs are the child's normalized non-blank lines c[0..N) and the parent's
# p[0..M); lines compare by exact string equality. A *common run* is a child
# interval c[s..s+k), k >= 1, that occurs as a contiguous interval of p. A run
# *has substance* when its lines total at least _PARENT_DIFF_MIN_BLOCK_WORDS
# words or one of its lines has at least _PARENT_DIFF_MIN_LINE_WORDS words.
#
# 1. Anchor. K is the greatest length, counted in lines, of any common run. If
#    K = 0 nothing is marked. The anchor is the common run of length K with
#    the smallest start in the child, c[i..i+K); its parent position j is the
#    smallest start of an occurrence of c[i..i+K) in p.
# 2. The anchor's lines are marked when the anchor has substance.
# 3. Before the anchor, a line of c[0..i) is marked when it lies inside a run
#    of c[0..i) that has substance and occurs contiguously in p[0..j). After
#    the anchor, likewise for c[i+K..N) against p[j+K..M).
#
# The anchor is normally the quoted previous message; step 3 keeps a run the
# author repeated on one side of the quote (a sign-off, say) from matching the
# parent's copy on the other side. Within a side, runs are matched wherever
# they occur, in any order.
#
# Every step uses a suffix automaton over a parent interval: O(N + M) map
# operations on small integer line ids (lines are interned once).


def _intern_lines(child_seq: list[str], parent_seq: list[str]) -> tuple[list[int], list[int]]:
    """Map each distinct parent line to a small integer id; a child line absent
    from the parent gets -1, which no transition carries."""
    ids: dict[str, int] = {}
    parent_ids: list[int] = []
    for line in parent_seq:
        if line not in ids:
            ids[line] = len(ids)
        parent_ids.append(ids[line])
    child_ids: list[int] = []
    for line in child_seq:
        child_ids.append(ids.get(line, -1))
    return child_ids, parent_ids


def _cover_side(
    child_ids: list[int],
    c_lo: int,
    c_hi: int,
    parent_ids: list[int],
    p_lo: int,
    p_hi: int,
    words_before: list[int],
    long_before: list[int],
    cover: list[int],
) -> None:
    """Step 3 for one side: add to ``cover`` (a difference array over child
    positions) every run of ``child_ids[c_lo:c_hi]`` that has substance and
    occurs in ``parent_ids[p_lo:p_hi]``. Only the longest run ending at each
    position is tested: a shorter one ending there lies inside it and has no
    more words and no longer line."""
    if c_lo >= c_hi or p_lo >= p_hi:
        return
    runs, _states = _walk(_build_automaton(parent_ids, p_lo, p_hi), child_ids, c_lo, c_hi)
    for e in range(c_lo, c_hi):
        run = runs[e - c_lo]
        if run == 0:
            continue
        start = e - run + 1
        if (
            words_before[e + 1] - words_before[start] >= _PARENT_DIFF_MIN_BLOCK_WORDS
            or long_before[e + 1] - long_before[start] > 0
        ):
            cover[start] += 1
            cover[e + 1] -= 1


def _aligned_run_marks(child_seq: list[str], parent_seq: list[str]) -> list[bool]:
    """Rule (a): which child lines (indexes of ``child_seq``) are parent content.

    Implements the specification in the comment above this section.
    """
    count = len(child_seq)
    marks = [False] * count
    if count == 0 or not parent_seq:
        return marks
    child_ids, parent_ids = _intern_lines(child_seq, parent_seq)

    # Prefix sums: words, and lines of at least _PARENT_DIFF_MIN_LINE_WORDS
    # words, before each child position (a run's substance in O(1)).
    words_before = [0] * (count + 1)
    long_before = [0] * (count + 1)
    for k in range(count):
        words = len(child_seq[k].split())
        words_before[k + 1] = words_before[k] + words
        long_before[k + 1] = long_before[k] + (1 if words >= _PARENT_DIFF_MIN_LINE_WORDS else 0)

    # Step 1: the anchor. The first end position reaching the greatest run
    # length (strict comparison) is the earliest-starting longest run; the
    # automaton's first_end gives its earliest parent occurrence.
    automaton = _build_automaton(parent_ids, 0, len(parent_ids))
    runs, states = _walk(automaton, child_ids, 0, count)
    best_len = 0
    best_end = -1
    for e in range(count):
        if runs[e] > best_len:
            best_len = runs[e]
            best_end = e
    if best_len == 0:
        return marks
    i = best_end - best_len + 1
    j = automaton[3][states[best_end]] - best_len + 1

    cover = [0] * (count + 1)  # difference array over child positions
    # Step 2: the anchor itself.
    if (
        words_before[i + best_len] - words_before[i] >= _PARENT_DIFF_MIN_BLOCK_WORDS
        or long_before[i + best_len] - long_before[i] > 0
    ):
        cover[i] += 1
        cover[i + best_len] -= 1
    # Step 3: each side against the same side of the parent.
    _cover_side(child_ids, 0, i, parent_ids, 0, j, words_before, long_before, cover)
    _cover_side(
        child_ids,
        i + best_len,
        count,
        parent_ids,
        j + best_len,
        len(parent_ids),
        words_before,
        long_before,
        cover,
    )

    depth = 0
    for k in range(count):
        depth += cover[k]
        marks[k] = depth > 0
    return marks


# --- rules (b) and (c): substring tests against the parent's word-stream ---------


class _ParentStream:
    """Answers ``needle in stream`` for the parent's word-stream (the parent's
    normalized non-blank lines joined by single spaces), exactly, in time
    linear in the needle.

    Every needle is one or two normalized child lines: words separated by
    single spaces, as the stream's are, and neither contains other
    whitespace. In an occurrence, each space of the needle falls on a
    separator of the stream, so the needle's interior words (all but its
    first and last) are consecutive whole words of the stream, each at a
    fixed distance from the needle's start.

    A needle of at least three words is first checked against a table of the
    stream's *grams*: its words and its adjacent word pairs (``"a b"``). The
    needle's grams are its interior word when it has three words, else its
    adjacent pairs of interior words; they are taken in order:

    - a gram absent from the stream proves the needle absent;
    - a gram that occurs exactly once in the stream fixes the only position
      at which the needle can start, and one comparison there decides;
    - when every gram occurs more than once, the first occurrence of the
      first gram that leaves room for the needle's first word gives one
      candidate position, and a match there is an occurrence.

    Every other query is answered by the suffix automaton of the stream's code
    points, built on the first query that needs it (O(len(stream))); a query
    walks the needle from the root. On ordinary mail the table decides almost
    every query, so the automaton is seldom built.

    The table maps each gram to the character offsets of its first and last
    occurrence; it is built in one pass over the stream, on the first query of
    at least three words. A query does O(len(needle)) work besides the single
    automaton build, so the queries of one call cost time linear in the
    stream and the needles.
    """

    def __init__(self, parent_seq: list[str]) -> None:
        self._stream = " ".join(parent_seq)
        self._first: dict[str, int] | None = None
        self._last: dict[str, int] = {}
        self._trans: list[dict[int, int]] | None = None

    def _build_table(self) -> None:
        first: dict[str, int] = {}
        last: dict[str, int] = {}
        words = self._stream.split(" ")
        offset = 0
        for k in range(len(words)):
            word = words[k]
            if word not in first:
                first[word] = offset
            last[word] = offset
            if k + 1 < len(words):
                pair = word + " " + words[k + 1]
                if pair not in first:
                    first[pair] = offset
                last[pair] = offset
            offset += len(word) + 1
        self._first = first
        self._last = last

    def _decided_by_table(self, needle: str, words: list[str]) -> bool | None:
        """The answer for a needle of at least three ``words`` when the table
        decides it, else ``None``."""
        if self._first is None:
            self._build_table()
        first = self._first
        assert first is not None
        count = len(words) - 2  # grams: the interior word, or the interior pairs
        if count > 1:
            count -= 1
        candidate = -1
        offset = len(words[0]) + 1  # the offset of words[k] in the needle
        for k in range(1, 1 + count):
            gram = words[k] if len(words) == 3 else words[k] + " " + words[k + 1]
            if gram not in first:
                return False
            start = first[gram] - offset
            if first[gram] == self._last[gram]:
                return start >= 0 and self._stream.startswith(needle, start)
            if candidate < 0 <= start:
                candidate = start
            offset += len(words[k]) + 1
        if candidate >= 0 and self._stream.startswith(needle, candidate):
            return True
        return None

    def contains(self, needle: str) -> bool:
        if len(needle) > len(self._stream):
            return False
        words = needle.split(" ")
        if len(words) >= 3:
            decided = self._decided_by_table(needle, words)
            if decided is not None:
                return decided
        if self._trans is None:
            codes = [ord(ch) for ch in self._stream]
            self._trans = _build_automaton(codes, 0, len(codes))[2]
        trans = self._trans
        state = 0
        for ch in needle:
            nxt = trans[state].get(ord(ch))
            if nxt is None:
                return False
            state = nxt
        return True


def _continuation_marks(
    child_seq: list[str],
    seeds: list[bool],
    protected: list[bool],
    parent_stream: _ParentStream,
) -> list[bool]:
    """Rule (c): ``seeds`` extended to the fixpoint of the continuation test.

    An unmarked, unprotected line is marked when an adjacent non-blank line is
    marked and the two, joined in reading order, occur contiguously in the
    parent's word-stream. The rule is monotone (a mark only ever enables more
    marks), so its fixpoint is the same whatever order lines are examined in. A
    worklist re-examines only the neighbors of a newly marked line; repeating
    full passes until nothing changed needed one pass per backward-chained mark.
    """
    count = len(child_seq)
    is_marked = list(seeds)

    def chains(k: int) -> bool:
        """Unmarked ``k`` joins a marked neighbor contiguously in the parent."""
        norm = child_seq[k]
        if k > 0 and is_marked[k - 1]:
            if parent_stream.contains(child_seq[k - 1] + " " + norm):
                return True
        if k + 1 < count and is_marked[k + 1]:
            if parent_stream.contains(norm + " " + child_seq[k + 1]):
                return True
        return False

    pending = [
        k
        for k in range(count)
        if not is_marked[k]
        and not protected[k]
        and ((k > 0 and is_marked[k - 1]) or (k + 1 < count and is_marked[k + 1]))
    ]
    while pending:
        k = pending.pop()
        if is_marked[k] or protected[k] or not chains(k):
            continue
        is_marked[k] = True
        pending.extend(j for j in (k - 1, k + 1) if 0 <= j < count and not is_marked[j])
    return is_marked


# --- rule (d): text the message also quotes with markers ---------------------------
#
# Rules (a)-(c) accept a match anywhere in the child, so a line the author wrote
# that repeats a line of the parent (a revised paragraph that keeps one of the
# old lines, the NEW wording of a proposed-text edit, a phrase of the parent
# cited mid-sentence) is marked too. Rule (d) restores such lines when the
# message itself shows where its client put the quotation.
#
# The message's *marked quotation* is the quote-marked lines of its body
# (:func:`is_quote_line`), in order. Its *quotation stream* is those lines,
# each normalized by :func:`_normalize_line_for_diff` (which strips the
# marker), the blank ones dropped, joined by single spaces.
#
# A *block* is a maximal run of consecutive lines of the text that are non-blank
# after normalization. A *segment* is a maximal run of consecutive marked lines
# within one block. A segment is restored (unmarked) when both hold:
#
# 1. The quotation stream contains the segment's lines joined by single
#    spaces: the message carries this text inside its marked quotation as
#    well. The client's copy of the parent is the marked one, so the copy
#    outside it was placed there by the author.
# 2. The unmarked lines of its block total more words than the marked lines:
#    the block is mostly text the parent does not have, so the segment is part
#    of a passage the author wrote. A passage placed whole (a citation pasted
#    from the parent, a signature) keeps its marks.
#
# A message with no marked quotation restores nothing: its client did not mark
# the quotation, and an unmarked copy of parent text may be the client's.
#
# The marks tested are those of rules (a)-(c). The lines of restored segments
# are *protected*: rule (c) is then evaluated again from the rule (a)/(b) marks
# of the unprotected lines (when they number at least
# _PARENT_DIFF_CONTINUATION_MIN_SEEDS) and never marks a protected line, so a
# line that only chained off a restored line is not removed either.
#
# Linear: blocks, segments and word counts take one pass over the child; the
# segments are disjoint, so the needles of step 1 total at most the child's
# length, and each is one query of a _ParentStream over the quotation stream
# (built only when a segment reaches step 1).


def _marked_quotation(body: str) -> str:
    """The quote-marked lines of ``body``, in order, joined by newlines."""
    return "\n".join(line for line in normalize_body(body).split("\n") if is_quote_line(line))


def _restored_by_own_quotation(
    child_pairs: list[tuple[int, str]],
    marks: list[bool],
    quoted_seq: list[str],
) -> list[bool]:
    """Rule (d): which child lines (indexes of ``child_pairs``) are protected.

    ``child_pairs`` holds each non-blank normalized child line with its index
    in the text; ``marks`` are the marks of rules (a)-(c); ``quoted_seq`` is
    the message's marked quotation, normalized, without blank lines.
    """
    count = len(child_pairs)
    protected = [False] * count
    if count == 0 or not quoted_seq:
        return protected

    # Blocks: consecutive indexes in the text. Words per block, by mark.
    block = [0] * count
    for k in range(1, count):
        block[k] = block[k - 1] + (0 if child_pairs[k][0] == child_pairs[k - 1][0] + 1 else 1)
    marked_words = [0] * (block[-1] + 1)
    unmarked_words = [0] * (block[-1] + 1)
    for k in range(count):
        words = len(child_pairs[k][1].split())
        if marks[k]:
            marked_words[block[k]] += words
        else:
            unmarked_words[block[k]] += words

    quotation: _ParentStream | None = None
    k = 0
    while k < count:
        if not marks[k]:
            k += 1
            continue
        start = k
        while k + 1 < count and marks[k + 1] and block[k + 1] == block[start]:
            k += 1
        end = k
        k += 1
        b = block[start]
        if unmarked_words[b] <= marked_words[b]:  # step 2 (the cheaper test first)
            continue
        if quotation is None:
            quotation = _ParentStream(quoted_seq)
        segment = " ".join(child_pairs[j][1] for j in range(start, end + 1))
        if not quotation.contains(segment):  # step 1
            continue
        for j in range(start, end + 1):
            protected[j] = True
    return protected


# --- marking mode: unmarked parent text the author placed ---------------------
#
# Rules (a)-(c) remove every substantial unmarked copy of parent text, which is
# right when the client reproduced the parent without ">" markers. A client that
# does mark its quotation deepens the parent's own quote lines by one level, and
# then an unmarked copy of parent text outside the marked quotation was placed
# by the author: a pasted OLD/NEW block, a re-sent paragraph, a citation. In
# *marking mode* such lines are protected.
#
# 1. Evidence. A body line's *depth* is the number of ">" in its leading marker
#    run (optional indentation and leader dots, then ">" with blanks between,
#    _QUOTE_DEPTH_RE); its *content* is the rest of the line,
#    whitespace-collapsed. A body line counts when its depth is at least 1, its
#    content has at least _MARKING_MODE_MIN_WORDS words, and the parent holds a
#    line with the same content at a smaller depth (the parent's smallest depth
#    for that content is compared). Marking mode is on when at least
#    _MARKING_MODE_MIN_LINES body lines count. Both bodies are read after
#    normalize_body.
# 2. Stop line. The first attribution (_attribution_end_at, generic units
#    included) whose next non-blank line exists and is not a quote line
#    introduces an unmarked quotation; its next non-blank line is the stop line
#    (none: the end of the body). The attribution is read on the body with its
#    generic attribution units and wrap remainders marked.
# 3. Protected lines. Above the stop line, the body is divided into blocks
#    (maximal runs of non-blank lines). Every line of a block that holds no
#    quote line is protected, by its _normalize_line_for_diff form.
# 4. In strip_parent_content, a non-blank text line that is not a quote line and
#    whose normalized form is protected is protected like a rule (d) line:
#    rule (c) is evaluated again without it.
# 5. Many runs. A *run* is a maximal sequence of consecutive non-blank text
#    lines (blank lines do not end it) each protected by step 4 and not by rule
#    (d), and marked by rules (a)-(c). When at least _MARKING_MODE_MANY_RUNS runs
#    hold more than _MARKING_MODE_SHORT_RUN lines each, those runs lose the
#    protection: the message reproduces the parent unmarked in pieces and
#    answers it point by point, and the pieces are the parent's text.
#
# Linear: the evidence is one pass over each body with a dict, the blocks one
# pass, and the stop-line search tests each line with a bounded number of
# neighbors (see _attribution_end_at).

_MARKING_MODE_MIN_LINES = 3
_MARKING_MODE_MIN_WORDS = 4
_MARKING_MODE_SHORT_RUN = 2
_MARKING_MODE_MANY_RUNS = 8

#: The leading quote-marker run of a line: indentation, optional leader dots,
#: then one or more ">" each followed by optional blanks.
_QUOTE_DEPTH_RE = re.compile(r"^[ \t]*(?:\.{2,}[ \t]*)?((?:>[ \t]*)+)")


def _quote_depth(line: str) -> tuple[int, str]:
    """``(depth, content)`` of ``line`` (step 1 of the rules above)."""
    m = _QUOTE_DEPTH_RE.match(line)
    if not m:
        return 0, " ".join(line.split())
    return m.group(1).count(">"), " ".join(line[m.end() :].split())


def _marking_mode(body_lines: list[str], parent_body: str) -> bool:
    """Step 1 of the rules above: whether marking mode is on."""
    parent_depth: dict[str, int] = {}
    for line in normalize_body(parent_body).split("\n"):
        depth, content = _quote_depth(line)
        if content and (content not in parent_depth or depth < parent_depth[content]):
            parent_depth[content] = depth
    count = 0
    for line in body_lines:
        depth, content = _quote_depth(line)
        if depth == 0 or not content or content not in parent_depth:
            continue
        if len(content.split()) < _MARKING_MODE_MIN_WORDS:
            continue
        if depth > parent_depth[content]:
            count += 1
    return count >= _MARKING_MODE_MIN_LINES


def _unmarked_quote_start(lines: list[str]) -> int:
    """Step 2 of the rules above: the stop line of the marked body ``lines``."""
    n = len(lines)
    for k in range(n):
        if not lines[k].strip() or is_quote_line(lines[k]):
            continue
        end = _attribution_end_at(lines, k)
        if end is None:
            continue
        q = end + 1
        while q < n and not lines[q].strip():
            q += 1
        if q < n and not is_quote_line(lines[q]):
            return q
    return n


def _marking_protected_lines(body_lines: list[str], stop: int) -> set[str]:
    """Step 3 of the rules above: the protected normalized lines."""
    protected: set[str] = set()
    i = 0
    while i < stop:
        if not body_lines[i].strip():
            i += 1
            continue
        j = i
        while j < stop and body_lines[j].strip():
            j += 1
        if not any(is_quote_line(line) for line in body_lines[i:j]):
            for line in body_lines[i:j]:
                protected.add(_normalize_line_for_diff(line))
        i = j
    return protected


def _runs_losing_protection(own: list[bool], marks: list[bool]) -> list[tuple[int, int]]:
    """Step 5 of the rules above: the runs ``[start, end)`` that lose their
    protection. ``own[k]`` is true for a line protected by step 4 and not by
    rule (d)."""
    count = len(own)
    long_runs: list[tuple[int, int]] = []
    k = 0
    while k < count:
        if not (own[k] and marks[k]):
            k += 1
            continue
        start = k
        while k < count and own[k] and marks[k]:
            k += 1
        if k - start > _MARKING_MODE_SHORT_RUN:
            long_runs.append((start, k))
    return long_runs if len(long_runs) >= _MARKING_MODE_MANY_RUNS else []


def strip_parent_content(
    text: str,
    parent_body: str,
    *,
    continuation: bool = False,
    quoted_text: str | None = None,
) -> str:
    """Remove lines of ``text`` that provably came from ``parent_body``.

    Pure and I/O-free; never raises (empty or whitespace-only inputs yield a
    sensibly trimmed result). Both sides are run through :func:`normalize_body`
    and then :func:`_normalize_line_for_diff` per line; blank lines are ignored
    when matching. A child line is marked as parent content by these rules:

    a) **Aligned-run rule** — over the normalized non-blank line sequences,
       the longest run of child lines found contiguously in the parent splits
       both sequences in two; each child side is matched only against the
       same side of the parent (see :func:`_aligned_run_marks`). A run marks
       its child lines only when it carries substance: at least
       :data:`_PARENT_DIFF_MIN_BLOCK_WORDS` words in total, or at least one
       line of :data:`_PARENT_DIFF_MIN_LINE_WORDS` words. Short coincidental
       echoes (greetings, the author's own sign-off + name) fall below both and
       survive.
    b) **Rewrap rule** — quoting clients re-wrap long paragraphs at a different
       width, so line-level matching misses them. Any not-yet-marked child line
       of at least :data:`_PARENT_DIFF_MIN_LINE_WORDS` words whose normalized
       text is a substring of the parent's normalized word-stream (all parent
       lines joined by single spaces) is marked too.
    c) **Continuation rule** (only with ``continuation=True``) — a re-wrapped
       paragraph's short remainder line falls under rule (b)'s floor and leaks.
       An unmarked line is marked when an adjacent non-blank line is already
       marked and the two joined in reading order appear contiguously in the
       parent's word-stream; iterated to a fixpoint so runs of short lines chain
       off one long seed. Runs only when rules (a)+(b) marked at least
       :data:`_PARENT_DIFF_CONTINUATION_MIN_SEEDS` lines — a real quoted block,
       not an isolated inline-citation echo. Safe against the parent body (the
       author's new text is never in it) but not against this message's own
       HTML quote container, where a client may wrap the author's inline
       replies (so the html-quote oracle call leaves it off).
    d) **Own-quotation rule** (only with ``quoted_text``, the message's marked
       quotation: the quote-marked lines of its body) — a run of marked lines
       inside a block (a run of consecutive non-blank lines) is restored when
       ``quoted_text`` contains it and the block's unmarked lines carry more
       words than its marked lines (see :func:`_restored_by_own_quotation`).
       Rule (c) is then re-derived from the remaining rule (a)/(b) marks and
       never marks a restored line. The client's copy of the parent is the
       marked one; a second copy outside it, inside a passage that is mostly
       not the parent's, is the author's repetition of it.

    The substring tests of rules (b), (c) and (d) are answered by
    :class:`_ParentStream`, with the same answers as Python's ``in`` and in
    time linear in the needle, so the whole function runs in time linear in
    the size of ``text`` and ``parent_body``.

    Marked lines are dropped; the survivors are ``rstrip``-ed, any run of 2+
    blank lines left by removals collapses to one, and leading/trailing blank
    lines are trimmed (matching :func:`custom_clean`'s edge handling).
    """
    return _strip_parent_content(
        text, parent_body, continuation=continuation, quoted_text=quoted_text
    )


def _strip_parent_content(
    text: str,
    parent_body: str,
    *,
    continuation: bool = False,
    quoted_text: str | None = None,
    protected_lines: set[str] | None = None,
) -> str:
    """:func:`strip_parent_content` with marking mode: ``protected_lines`` is
    the set of step 3 of the marking-mode rules (``None`` or empty: marking
    mode is off)."""
    child_raw = normalize_body(text).split("\n")
    child_norm = [_normalize_line_for_diff(line) for line in child_raw]
    parent_norm = [
        _normalize_line_for_diff(line) for line in normalize_body(parent_body).split("\n")
    ]

    # Non-blank normalized sequences; child keeps its original-line index so a
    # match can be mapped back to the raw line to drop.
    child_pairs = [(i, norm) for i, norm in enumerate(child_norm) if norm]
    child_seq = [norm for _, norm in child_pairs]
    parent_seq = [norm for norm in parent_norm if norm]

    count = len(child_pairs)

    # Rule (a): runs of matching lines with enough substance, aligned around
    # the longest common run.
    seeds = _aligned_run_marks(child_seq, parent_seq)

    # Rule (b): a long child line re-wrapped from the parent (line-level match
    # missed it) but still present verbatim in the parent's word-stream.
    parent_stream = _ParentStream(parent_seq)
    for k in range(count):
        if seeds[k]:
            continue
        norm = child_seq[k]
        if len(norm.split()) >= _PARENT_DIFF_MIN_LINE_WORDS and parent_stream.contains(norm):
            seeds[k] = True

    # Rule (c): chain short remainder lines off marked neighbors, to a fixpoint.
    unprotected = [False] * count
    marks = seeds
    if continuation and sum(seeds) >= _PARENT_DIFF_CONTINUATION_MIN_SEEDS:
        marks = _continuation_marks(child_seq, seeds, unprotected, parent_stream)

    # Rule (d): restore segments the message also carries in its marked
    # quotation, then re-derive rule (c) without them.
    quoted_seq: list[str] = []
    if quoted_text is not None:
        quoted_seq = [
            norm
            for norm in (
                _normalize_line_for_diff(line) for line in normalize_body(quoted_text).split("\n")
            )
            if norm
        ]
    protected = _restored_by_own_quotation(child_pairs, marks, quoted_seq)
    if protected_lines:
        own = [False] * count
        for k in range(count):
            i, norm = child_pairs[k]
            if norm in protected_lines and not is_quote_line(child_raw[i]):
                own[k] = not protected[k]
                protected[k] = True
        for start, end in _runs_losing_protection(own, marks):
            for k in range(start, end):
                protected[k] = False
    if any(protected):
        seeds = [seeds[k] and not protected[k] for k in range(count)]
        marks = seeds
        if continuation and sum(seeds) >= _PARENT_DIFF_CONTINUATION_MIN_SEEDS:
            marks = _continuation_marks(child_seq, seeds, protected, parent_stream)

    marked = {child_pairs[k][0] for k in range(count) if marks[k]}
    survivors = [line.rstrip() for i, line in enumerate(child_raw) if i not in marked]

    # Collapse 2+ consecutive blank lines (left by removals) into one, then trim
    # blank edges the same way custom_clean does.
    collapsed: list[str] = []
    for line in survivors:
        if not line and collapsed and not collapsed[-1]:
            continue
        collapsed.append(line)
    while collapsed and not collapsed[0]:
        collapsed.pop(0)
    while collapsed and not collapsed[-1]:
        collapsed.pop()
    return "\n".join(collapsed)


# --- inline replies below a localized quote-header block --------------------------

# Some authors answer point by point *inside* the quoted message, below the
# client's quote-header block, tagging each answer ("[Med] ACK"). Generation 5
# did not recognize the Latin-script localized blocks (see
# :data:`_LOCALIZED_FROM_LINE_RE` and :data:`_LOCALIZED_DIVIDER_RE`), so such
# answers below a French block survived and the parent-diff assist removed the
# quoted parent around them. Truncating at the localized block would drop them.
# When the thread parent is on hand the answers are provable — tagged lines
# below the block that the parent does not account for — and the localized
# rules are then not applied to the message, which keeps its generation-5
# behavior. The generation-5 rules (English, German labels, Chinese) are
# unaffected: their truncation applies whether or not tagged answers follow.

#: An inline-reply tag at the start of a line: a bracketed label of 1-20
#: characters containing at least one letter ("[Med]", "[SAH]", "[Linda3]").
#: Citation forms ("[RFC8174]", "[BCP14]", "[I-D.x]") and numeric footnotes
#: ("[1]") are excluded.
_INLINE_REPLY_TAG_RE = re.compile(
    r"^[ \t]*\[(?!(?:RFC|BCP|STD|FYI|I-D|IANA|draft)\b|(?:RFC|BCP|STD)\d)"
    r"(?=[^\]\n]*[A-Za-z])[^\[\]\n]{1,20}\]"
)

#: With the thread parent on hand, the parent-diff assist must remove at least
#: this many non-blank lines below the block — evidence that the text there
#: quotes the parent — before a tagged survivor is read as an inline reply.
_INLINE_REPLY_MIN_PARENT_LINES = 1


def _first_quote_boundary(lines: list[str], *, localized: bool) -> int:
    """Index of the first quote-header block or divider, or ``len(lines)``."""
    end = find_quote_header_block(lines, localized=localized)
    end = len(lines) if end is None else end
    for i in range(end):
        if not is_quote_line(lines[i]) and _find_divider(lines[i], localized=localized):
            return i
    return end


def _localized_inline_reply(lines: list[str], parent_body: str | None) -> bool:
    """True when a localized quote-header block or divider sits above the
    author's tagged inline replies.

    Only the span the localized rules would newly remove is examined: from the
    first localized boundary to the first generation-5 boundary (or the end).

    With the thread parent, the parent-diff assist (:func:`strip_parent_content`)
    runs on that span alone; it must remove at least
    :data:`_INLINE_REPLY_MIN_PARENT_LINES` non-blank lines and leave a line
    opening with an inline-reply tag (:data:`_INLINE_REPLY_TAG_RE`) that belongs
    to the author (:func:`_any_author_tag`). Without the parent, a tag line in
    the span qualifies only when its label names the author in the text above
    the block (the sign-off "Med" for ``[Med]``).
    """
    start = _first_quote_boundary(lines, localized=True)
    end = _first_quote_boundary(lines, localized=False)
    if start >= end:
        return False
    span = "\n".join(lines[start:end])
    if parent_body is None:
        survivors = span
    else:
        survivors = strip_parent_content(span, parent_body, continuation=True)
        removed = _nonblank_line_count(span) - _nonblank_line_count(survivors)
        if removed < _INLINE_REPLY_MIN_PARENT_LINES:
            return False
    labels: list[str] = []
    for line in survivors.split("\n"):
        m = _INLINE_REPLY_TAG_RE.match(line)
        if m:
            labels.append(m.group(0).strip()[1:-1])
    if not labels:
        return False
    return _any_author_tag(labels, "\n".join(lines[:start]), parent_body)


#: The longest inline-reply tag label, in characters (:data:`_INLINE_REPLY_TAG_RE`).
_MAX_TAG_LABEL = 20

#: Python's ``\w`` for ``str`` patterns, applied to one character.
_WORD_CHAR_RE = re.compile(r"\w")


def _any_author_tag(labels: list[str], head: str, parent_body: str | None) -> bool:
    """True when one of the inline-reply tag ``labels`` belongs to this
    message's author.

    A tag the parent never uses is new in this message. A tag the parent uses
    — or any tag, when the parent is not available — belongs to an earlier
    round of the thread (as in ``[ML] …`` answers the parent's author wrote,
    which a re-wrapped or re-formatted quote can leave unmatched by the
    parent-diff assist) unless its label, without a trailing round number
    ("Linda3" → "Linda"), occurs as a word in ``head``, the text above the
    block — typically the author's sign-off ("Cheers,\\nMed" for ``[Med]``).

    Per label, the tests are ``"[" + label in parent_body`` and
    ``re.search(r"(?<!\\w)" + re.escape(name) + r"(?!\\w)", head)``. Labels and
    names are at most :data:`_MAX_TAG_LABEL` characters, so both are answered
    for all labels together in one scan of each text (see
    :func:`_found_after_bracket` and :func:`_name_in_head`), in time linear in
    the texts' length.
    """
    if parent_body is not None:
        found = _found_after_bracket(parent_body, labels)
        for label in labels:
            if label not in found:
                return True
    names: list[str] = []
    for label in labels:
        name = label.rstrip("0123456789").strip()
        if name:
            names.append(name)
    return _name_in_head(head, names)


def _group_by_length(strings: list[str]) -> tuple[list[set[str]], list[int]]:
    """``strings`` (each of 1 to :data:`_MAX_TAG_LABEL` characters) grouped by
    length: entry ``n`` of the first list holds those of length ``n`` (entry 0
    is unused); the second lists the lengths present, in increasing order."""
    groups: list[set[str]] = [set() for _n in range(_MAX_TAG_LABEL + 1)]
    for text in strings:
        groups[len(text)].add(text)
    lengths: list[int] = []
    for n in range(1, _MAX_TAG_LABEL + 1):
        if groups[n]:
            lengths.append(n)
    return groups, lengths


def _found_after_bracket(text: str, labels: list[str]) -> set[str]:
    """The labels ``label`` for which ``"[" + label`` occurs in ``text``.

    At each ``[`` of ``text``, the slices that follow it are looked up, one
    per label length present.
    """
    groups, lengths = _group_by_length(labels)
    found: set[str] = set()
    pos = text.find("[")
    while pos >= 0:
        for n in lengths:
            piece = text[pos + 1 : pos + 1 + n]
            if piece in groups[n]:
                found.add(piece)
        pos = text.find("[", pos + 1)
    return found


def _name_in_head(head: str, names: list[str]) -> bool:
    """True when one of ``names`` occurs in ``head`` with no word character
    (Python's ``\\w``) directly before or after it.

    At each position not preceded by a word character, the slice of each name
    length present is looked up when no word character follows it.
    """
    if not names:
        return False
    groups, lengths = _group_by_length(names)
    size = len(head)
    for start in range(size):
        if start > 0 and _WORD_CHAR_RE.match(head, start - 1):
            continue
        for n in lengths:
            end = start + n
            if end > size:
                break
            if end < size and _WORD_CHAR_RE.match(head, end):
                continue
            if head[start:end] in groups[n]:
                return True
    return False


# --- public entry point -------------------------------------------------------


def _core_extract(body: str, parent_body: str | None) -> tuple[str, str]:
    """Run the cleanup pass and the parent-diff assist.

    ``body`` is a non-blank decoded body (from ``text/plain`` or, for the HTML
    paths, the HTML's novel text). Returns ``(text, method)`` where ``method`` is
    unprefixed (``extract_new_text`` adds any ``html-`` prefix). Pure; the caller
    owns the try/except and the empty/status decision.
    """
    normalized = normalize_body(body)
    # The marked quotation and the marking-mode reading are taken from the
    # whole body, before any truncation or marking.
    quoted_text = _marked_quotation(normalized)
    body_lines = normalized.split("\n")
    marked = _mark_generic_attributions(normalized)

    # The Latin-script localized blocks and dividers are not applied to an
    # inline reply written below one (see _localized_inline_reply): the
    # message keeps its generation-5 treatment, where the parent-diff assist
    # below removes the quoted parent around the author's tagged answers.
    lines = marked.split("\n")
    localized = not _localized_inline_reply(lines, parent_body)
    text = custom_clean(marked, localized=localized)
    method = "custom"

    # Parent-diff assist. The assisted text (and the "+parent-diff" suffix) is
    # adopted only when it dropped a content line: strip_parent_content also
    # collapses internal blank runs, so a plain string comparison would fire —
    # mutating the text and mislabeling the method — on a parent that removed
    # nothing.
    if parent_body is not None and text.strip():
        protected_lines = None
        if _marking_mode(body_lines, parent_body):
            stop = _unmarked_quote_start(lines)
            protected_lines = _marking_protected_lines(body_lines, stop)
        assisted = _strip_parent_content(
            text,
            parent_body,
            continuation=True,
            quoted_text=quoted_text,
            protected_lines=protected_lines,
        )
        if _nonblank_line_count(assisted) < _nonblank_line_count(text):
            text = assisted
            method += "+parent-diff"

    return text, method


#: Minimum non-blank lines the HTML quoted-text oracle must remove from a plain
#: extraction before its result is adopted (path (c) of :func:`extract_new_text`).
#: The oracle targets an entire quoted message leaked into the plain part with no
#: ``>`` markers — a large removal. A 1–2 line removal is almost always a
#: coincidental match (an author's interleaved reply that a client wrapped inside
#: the quote container, or an inline citation of a sentence that also appears
#: verbatim in a real ``<blockquote>``), so requiring a block keeps such author
#: content — content the hand-labeled corpus keeps — from being stripped.
_HTML_QUOTE_MIN_REMOVED = 3


def _is_degenerate_plain(body: str, html_novel: str) -> bool:
    """True when ``body`` looks like a flattened plain part best replaced by HTML.

    The heuristic (see path (b) of :func:`extract_new_text`): fewer than 4
    non-blank lines, at least one line over 400 chars (a client that dropped the
    newlines glued paragraphs into one giant line), and the HTML's novel text has
    at least 3× as many non-blank lines to fall back to.
    """
    lines = [line for line in body.split("\n") if line.strip()]
    if not lines or len(lines) >= 4:
        return False
    if not any(len(line) > 400 for line in lines):
        return False
    return _nonblank_line_count(html_novel) >= 3 * len(lines)


def extract_new_text(
    body: str | None,
    parent_body: str | None = None,
    html_body: str | None = None,
) -> ExtractionResult:
    """Extract the author's newly written text from a decoded ``text/plain`` body.

    Pure and I/O-free. ``None`` or a blank body with no usable HTML yields an
    empty result. Any unexpected error is caught and reported as
    :data:`STATUS_FAILED` rather than raised, so a single bad message never
    stalls the pipeline.

    When ``parent_body`` (the thread parent's raw body, resolved by the caller
    from ``In-Reply-To``) is supplied, the parent-diff assist runs after the
    cleanup pass and removes content that provably
    came from the parent (see :func:`strip_parent_content`), appending
    ``"+parent-diff"`` to the method. The default ``None`` is a behavioral no-op.

    When ``html_body`` (the decoded ``text/html`` part) is supplied, the HTML
    structure serves as an oracle in one of four ways — HTML-only, degenerate
    plain fallback, Share-sheet container, or quoted-text oracle assist —
    described in the module docstring. The default ``None`` leaves the plain-only behavior unchanged, so
    every existing caller and the whole no-HTML corpus are unaffected.
    """
    try:
        parts = None
        if html_body is not None and html_body.strip():
            parts = split_html_parts(html_body)

        # (a) HTML-only: no usable plain body, but an HTML part to derive from.
        if body is None or not body.strip():
            if parts is not None and parts.novel_text.strip():
                text, method = _core_extract(parts.novel_text, parent_body)
                return _result("html-" + method, text, _html_source(html_body))
            return ExtractionResult(text="", method="none", status=STATUS_EMPTY)

        # (b) Degenerate plain: a flattened plain body the HTML can replace.
        if parts is not None and _is_degenerate_plain(body, parts.novel_text):
            text, method = _core_extract(parts.novel_text, parent_body)
            return _result("html-" + method, text, _html_source(html_body))

        # (d) Share-sheet container: the plain part marks the author's own text
        # as quoted; the HTML's novel text holds it.
        if parts is not None and parts.has_share_container and parts.novel_text.strip():
            text, method = _core_extract(parts.novel_text, parent_body)
            return _result("html-" + method, text, _html_source(html_body))

        # Normal plain path.
        text, method = _core_extract(body, parent_body)

        # (c) Oracle assist: remove plain-body content provably present in the
        # HTML's quoted text. As with the parent-diff assist, the trigger is the
        # non-blank-line count (never a string compare, which incidental blank-run
        # reformatting would trip), but here it must clear a block threshold so an
        # isolated coincidental match never strips genuine author content.
        if parts is not None and text.strip() and parts.quoted_text.strip():
            assisted = strip_parent_content(
                text, parts.quoted_text, quoted_text=_marked_quotation(body)
            )
            if (
                _nonblank_line_count(text) - _nonblank_line_count(assisted)
                >= _HTML_QUOTE_MIN_REMOVED
            ):
                text = assisted
                method += "+html-quote"

        return _result(method, text, body)
    except Exception:  # noqa: BLE001 - never let one message stall the pipeline
        return ExtractionResult(text="", method="failed", status=STATUS_FAILED)


def _result(method: str, text: str, source: str) -> ExtractionResult:
    """Wrap resolved ``(method, text)`` in a result with the right empty status.

    A legitimately empty extraction (e.g. an exact re-send of the parent) keeps
    its resolved ``method`` but reports :data:`STATUS_EMPTY`. ``source`` is the
    body the text was derived from — the ``text/plain`` body, or the full
    :func:`~html_text.html_to_text` rendering for the ``html-`` paths — and is
    used only to locate the part boundaries (:func:`_part_starts`).
    """
    if not text.strip():
        return ExtractionResult(text="", method=method, status=STATUS_EMPTY)
    return ExtractionResult(
        text=text,
        method=method,
        status=STATUS_OK,
        part_starts=_safe_part_starts(text, source),
    )


def _html_source(html_body: str) -> str:
    """The full rendering of ``html_body`` (novel and quoted text); ``""`` on error.

    An empty source leaves the alignment unmatched, so the text is one part.
    """
    try:
        return html_to_text(html_body)
    except Exception:  # noqa: BLE001 - boundaries are advisory
        return ""


def _safe_part_starts(text: str, source: str) -> tuple[int, ...]:
    """:func:`_part_starts` over the normalized ``source``; ``()`` on any error.

    The part boundaries never decide the text or the status, so a failure here
    must not turn an extraction into :data:`STATUS_FAILED`.
    """
    try:
        return _part_starts(text, normalize_body(source))
    except Exception:  # noqa: BLE001 - boundaries are advisory
        return ()


def _part_starts(text: str, source: str) -> tuple[int, ...]:
    """Return the indices of the lines of ``text`` that begin a new part.

    ``source`` is the normalized body ``text`` was derived from. Every non-blank
    line of ``text`` is matched, in order, to the first source line at or after
    the previous match whose stripped form is equal. A non-blank source line
    lying between two consecutive matches is content stage 1 removed, and the
    second line then begins a new part. Blank lines never begin a part.

    The rule is deliberately conservative: when a line of ``text`` has no match
    (an author prefix cut before a mid-line divider has no equal source line;
    a future rule might reflow lines), the alignment is abandoned and
    an empty tuple — a single part — is returned. The search pointer only moves
    forward and each gap is inspected once, so the cost is linear in the two
    line counts.
    """
    src = [line.strip() for line in source.split("\n")]
    starts: list[int] = []
    pos = 0
    prev = -1
    for i, line in enumerate(text.split("\n")):
        key = line.strip()
        if not key:
            continue
        j = pos
        while j < len(src) and src[j] != key:
            j += 1
        if j == len(src):
            return ()
        if prev >= 0 and any(src[k] for k in range(prev + 1, j)):
            starts.append(i)
        prev = j
        pos = j + 1
    return tuple(starts)
