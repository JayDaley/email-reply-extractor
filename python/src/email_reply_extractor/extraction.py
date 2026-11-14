"""New-text extraction: recover the author's *novel content* (stage 1 of 2).

Strategy: **the ERP fragment scanner as the primary extractor, followed by a
small custom cleanup pass.** "ERP" throughout this package refers to the
scanner vendored from email-reply-parser into :mod:`._fragments`. Talon is not
used. The public entry point is the pure function :func:`extract_new_text`,
which takes a decoded ``text/plain`` body and returns an :class:`ExtractionResult`
(``text``, ``method``, ``status``) with no I/O — so the quality tests run without
a database.

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

The custom pass fixes, in a general way, the cases where the raw ERP scan
diverges from hand-labeled ground truth:

- **Pre-normalization** strips a byte-order mark and zero-width characters and
  converts CRLF/CR to LF *before* ERP runs. This alone lets ERP recognize a
  leading ``>`` that a BOM would otherwise hide.
- **ERP fragments, signatures kept**: the primary path joins ERP's fragments
  that are not ``quoted`` — *including* fragments ERP labels ``signature`` — so a
  signature block survives stage 1 (stage 2 removes it). This is the one place
  stage 1 keeps more than upstream email-reply-parser's ``parse_reply`` would,
  which hides signature fragments outright.
- **Attribution lines** ("On <date>, X wrote:", "Name <a@b> wrote:", and the
  German "Am <date> schrieb X:") are removed, including two-line-wrapped forms.
  The pattern lists are structured so more languages are easy to add.
- **Indented quote blocks** — lines that are optional whitespace then one or more
  ``>`` (which ERP misses when the marker is not in column 0) — are dropped.
- **Sign-off boundary**: a salutation line ("Best," / "Cheers" / "Thanks," …)
  plus a name line, directly followed by an attribution line, ends the new text —
  everything after the name is the quoted message even when the client added no
  ``>`` markers (Gmail-style fully top-posted replies, typical of AI-generated
  mail). The name is required: a bare mid-thread "Thanks." never truncates. The
  sign-off *itself* survives stage 1 (this rule removes only the quoted thread
  the sign-off precedes); stage 2 strips the sign-off.
- **Forwarded / quote-header blocks**: an Outlook-style ``From:``/``Sent:``/
  ``To:``/``Subject:`` block introducing quoted content with no ``>`` markers,
  and everything after it, is dropped. Localized label sets are recognized too
  (German; Chinese 发件人/发送时间/收件人/主题 with full-width colons, as
  produced by Alibaba Mail and Chinese Outlook), along with the dashed divider
  Alibaba Mail draws above the block. Latin-script localized blocks open with
  ``De``/``Da``/``Van``/``Von`` (French, Spanish, Portuguese, Italian, Dutch,
  German), accept U+00A0 / U+202F around the colon (``De\xa0: …``), and must
  carry their language's subject label. A message whose author answers inline,
  with tagged lines, below such a block keeps the generation-5 treatment (see
  :func:`_localized_inline_reply`). The truncation runs on the intact body
  *before* ERP: re-joining ERP's edge-stripped fragments can glue a signature
  line (``Tel:``/``Email:``) directly above the block's ``From:``, which would
  otherwise disguise the block as pasted header evidence. When the ``From:``
  line carries an address, the block is also recognized in the two mangled
  renderings archives produce — folded header lines that lost their leading
  whitespace, and a blank line between every field.
- **"Original message" dividers**: the dashed ``-------- Original message
  --------`` (or ``-----Original Message-----`` / "Forwarded message", and the
  Chinese and Latin-script localized forms such as ``-----Message d'origine-----``)
  divider and everything after it is dropped — including when HTML-to-text conversion
  glues the divider mid-line onto the author's last sentence, where only the
  author's prefix before the divider is kept.
- **Over-strip guard**: dashed-separator digest bodies make ERP treat the ``---``
  rule as a signature boundary and truncate. When ERP keeps far fewer of the
  body's plainly-unquoted lines than the body actually has, we discard the ERP
  output and run the custom cleanup on the whole body instead.
- **Parent-diff assist** (optional): when the reply's thread parent is available,
  the caller passes ``parent_body`` and — *after* the guard has resolved the
  chosen text — any content that provably came from the parent is removed. This
  catches fully top-posted replies whose quoted previous message carries no
  ``>`` markers at all (so the quote/attribution/header filters see nothing to
  drop), using a line-level alignment around the longest common run of lines
  and an exact substring index over the parent as an evidence-based backstop.
  See :func:`strip_parent_content`; it adds the ``"+parent-diff"`` method
  suffix.

HTML as a structural oracle (optional ``html_body``)
----------------------------------------------------
When the caller passes the decoded ``text/html`` part, its structure (see
:mod:`html_text`) is used three ways. The resolved method makes the HTML source
visible so it is auditable downstream:

a. **HTML-only** — the plain body is missing or blank but an HTML part exists:
   the HTML's novel text (:func:`~html_text.split_html_parts`) becomes the body
   for the normal stage-1 pipeline, and the method is prefixed ``html-`` (e.g.
   ``html-erp+custom``).
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

A word-count "too short" gate is deliberately **not** applied here — that is a
consumer's decision, and it belongs on the stage-2 *cleaned* text.

Generation stamp (:data:`EXTRACTION_VERSION`)
---------------------------------------------
:data:`EXTRACTION_VERSION` identifies the routine that derives an extraction's
text. A consumer that stores derived text should stamp each stored row with it
and treat a row as stale when its stamp is *lower* than the running value (see
``docs/versioning.md``). It is independent of the package's semantic version:
releases that do not touch the pipeline leave it alone.

Bump it by one — by hand, in the same commit — whenever a change to this module,
:mod:`cleaning`, :mod:`html_text` or :mod:`._fragments` can alter the stage-1 or
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
collapsed attribution header literally (v1.3.0).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ._fragments import read_fragments
from .html_text import split_html_parts

# --- generation stamp -----------------------------------------------------------

#: The generation of the text-deriving routine (this module, :mod:`cleaning`,
#: :mod:`html_text` and :mod:`._fragments` together). Hand-incremented; see the
#: module docstring for when and why. Consumers stamp stored derived text with
#: it and compare with ``<``, so it must only ever increase.
EXTRACTION_VERSION: int = 7

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
    - ``method``: which path produced it — ``"none"`` (no body), ``"erp"``
      (email-reply-parser output needed no cleanup), ``"erp+custom"`` (ERP plus
      the custom pass), ``"custom-fallback"`` (over-strip guard fired; custom
      pass on the whole body), or ``"failed"``. When a thread parent was supplied
      and the parent-diff assist removed content, ``"+parent-diff"`` is appended
      to whichever of the above produced the text (e.g. ``"erp+parent-diff"``).
      When the text was derived from the HTML part (HTML-only or degenerate-plain
      fallback) the method is **prefixed** ``"html-"`` (e.g. ``"html-erp+custom"``);
      when the HTML quoted-text oracle removed content from a plain extraction,
      ``"+html-quote"`` is appended.
    - ``status``: one of :data:`STATUS_OK`, :data:`STATUS_EMPTY`,
      :data:`STATUS_FAILED`.
    """

    text: str
    method: str
    status: str


# --- pre-normalization --------------------------------------------------------

#: Characters removed before parsing: BOM / zero-width space / zero-width
#: non-joiner / zero-width joiner / word-joiner.
_ZERO_WIDTH = "﻿​‌‍⁠"
_ZERO_WIDTH_RE = re.compile(f"[{_ZERO_WIDTH}]")


def normalize_body(body: str) -> str:
    """Normalize line endings and strip BOM / zero-width characters.

    Runs before email-reply-parser so a leading BOM cannot hide a ``>`` quote
    marker (the documented ``interleaved-bom`` fixture).
    """
    body = body.replace("\r\n", "\n").replace("\r", "\n")
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
    # English: "On <date>, <who> wrote:". No word boundary before "wrote" —
    # ERP unwraps a line-wrapped attribution by deleting the newline, which can
    # glue the author's address straight onto it ("…example.comwrote:").
    re.compile(r"^[ \t]*On\b.*wrote:[ \t]*$"),
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


# For two-line-wrapped attributions: a start token and a terminator that may
# appear on the following line(s). Structured per language for easy extension.
_ATTRIBUTION_START_RE = re.compile(r"^[ \t]*(?:(?:On|Am|Le|El|El día)\b|\d{4}(?:年|[/.-]))")
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
    """True if ``line`` on its own is a complete attribution line."""
    return (
        any(pat.match(line) for pat in _ATTRIBUTION_RES)
        or _is_address_attribution(line)
        or _is_german_attribution(line)
    )


def _wrapped_attribution_end(lines: list[str], i: int) -> int | None:
    """If ``lines[i]`` opens a wrapped attribution — a start line ("On …" /
    "Am …") whose terminator ("wrote:" / "schrieb …:") lands on the next line or
    two, with no blank between — return the terminator line's index, else None.
    """
    if not _ATTRIBUTION_START_RE.match(lines[i]) or _has_attribution_end(lines[i]):
        return None
    for j in range(i + 1, min(i + 3, len(lines))):
        if not lines[j].strip():
            return None
        if _has_attribution_end(lines[j]):
            return j
    return None


def strip_attribution_lines(lines: list[str]) -> list[str]:
    """Drop attribution lines, including forms wrapped across up to two lines."""
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
    followed by an attribution line, or ``None``.

    All three parts are required, so neither a mid-thread "Thanks." (no name), a
    sign-off at the true end of a message (nothing after), nor a sign-off
    followed by authored content (a "Ps." block) ever truncates. Interleaved
    replies are safe too: there the attribution precedes ``>``-quoted text and
    the sign-off comes last.
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
        if is_attribution_line(lines[k]) or _wrapped_attribution_end(lines, k) is not None:
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
    Alibaba Mail style) is client furniture, not author content, and is dropped
    with the block. ``localized`` is passed to :func:`find_quote_header_block`.
    """
    idx = find_quote_header_block(lines, localized=localized)
    if idx is None:
        return lines
    out = lines[:idx]
    while out and not out[-1].strip():
        out.pop()
    if out and _QUOTE_HEADER_DIVIDER_RE.match(out[-1]):
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


def _find_divider(line: str, *, localized: bool = True) -> re.Match[str] | None:
    """The first "Original message" divider in ``line``, or ``None``."""
    found = [m for m in (_ORIGINAL_MESSAGE_DIVIDER_RE.search(line),) if m]
    if localized:
        found += [m for m in (_LOCALIZED_DIVIDER_RE.search(line),) if m]
    return min(found, key=lambda m: m.start()) if found else None


def strip_after_original_message_divider(lines: list[str], *, localized: bool = True) -> list[str]:
    """Truncate everything from a dashed "Original/Forwarded message" divider.

    The line holding the divider is dropped too, except that a non-empty author
    prefix glued before a mid-line divider survives. ``localized=False``
    recognizes only the English and Chinese wordings (the rule before
    generation 6).
    """
    for i, line in enumerate(lines):
        m = _find_divider(line, localized=localized)
        if m is None:
            continue
        out = lines[:i]
        prefix = line[: m.start()].rstrip()
        if prefix:
            out.append(prefix)
        return out
    return lines


def custom_clean(text: str, *, localized: bool = True) -> str:
    """Apply the stage-1 quote/attribution cleanup to ``text``.

    Composable steps, in order: truncate at a forwarded/quote-header block, an
    "Original message" divider, or a sign-off boundary (which must run while the
    attribution evidence is still present), drop attribution lines (incl.
    wrapped forms), then filter out quoted lines. Signature blocks, greetings
    and sign-offs are **kept** — they are the author's own furniture and are
    removed later by :mod:`cleaning`. Blank-line structure is left as-is; the
    tolerant test comparison ignores it.

    ``localized=False`` recognizes only the quote-header blocks and dividers
    of generation 5 (English, German labels, Chinese), not the Latin-script
    localizations; :func:`_core_extract` passes it for an inline reply written
    below a localized block (see :func:`_localized_inline_reply`).
    """
    lines = text.split("\n")
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


# --- over-strip guard ---------------------------------------------------------

#: If the primary path keeps fewer than this fraction of the body's plainly
#: unquoted content lines, treat it as an over-strip and fall back. Calibrated so
#: the dashed-separator digests and the pasted-headers fixture fall back while
#: genuinely quote-heavy replies do not.
_GUARD_MIN_RETAINED = 0.6


def count_unquoted_content_lines(body: str, *, localized: bool = True) -> int:
    """Count non-blank body lines that are plainly authored content.

    This is the denominator of the over-strip guard, so it must mirror what
    stage 1 now keeps: it excludes quoted lines, attribution lines, and
    everything after a forwarded/quote-header block or a sign-off boundary — but
    it **no longer** excludes the signature region, greetings or sign-offs,
    because stage 1 keeps those (ERP's signature fragments are retained, and the
    ``-- `` truncation moved to :mod:`cleaning`). Mirroring the numerator this way
    keeps the guard from misfiring on ordinary signed mail while still catching
    the dashed-separator digests, where ERP truncates most of the body.
    """
    lines = strip_after_quote_header_block(body.split("\n"), localized=localized)
    lines = strip_after_original_message_divider(lines, localized=localized)
    lines = strip_after_signoff_boundary(lines)
    count = 0
    for line in lines:
        if not line.strip():
            continue
        if is_quote_line(line) or is_attribution_line(line):
            continue
        count += 1
    return count


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


def strip_parent_content(text: str, parent_body: str, *, continuation: bool = False) -> str:
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

    The substring tests of rules (b) and (c) are answered by
    :class:`_ParentStream`, with the same answers as Python's ``in`` and in
    time linear in the needle, so the whole function runs in time linear in
    the size of ``text`` and ``parent_body``.

    Marked lines are dropped; the survivors are ``rstrip``-ed, any run of 2+
    blank lines left by removals collapses to one, and leading/trailing blank
    lines are trimmed (matching :func:`custom_clean`'s edge handling).
    """
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

    marked: set[int] = set()

    # Rule (a): runs of matching lines with enough substance, aligned around
    # the longest common run.
    rule_a = _aligned_run_marks(child_seq, parent_seq)
    for k in range(len(child_pairs)):
        if rule_a[k]:
            marked.add(child_pairs[k][0])

    # Rule (b): a long child line re-wrapped from the parent (line-level match
    # missed it) but still present verbatim in the parent's word-stream.
    parent_stream = _ParentStream(parent_seq)
    for orig_i, norm in child_pairs:
        if orig_i in marked:
            continue
        if len(norm.split()) >= _PARENT_DIFF_MIN_LINE_WORDS and parent_stream.contains(norm):
            marked.add(orig_i)

    # Rule (c): chain short remainder lines off marked neighbors, to a fixpoint.
    # The rule is monotone (a mark only ever enables more marks), so its
    # fixpoint is the same whatever order lines are examined in. A worklist
    # re-examines only the neighbors of a newly marked line; repeating full
    # passes until nothing changed needed one pass per backward-chained mark.
    if continuation and len(marked) >= _PARENT_DIFF_CONTINUATION_MIN_SEEDS:
        count = len(child_pairs)
        is_marked = [orig_i in marked for orig_i, _norm in child_pairs]

        def chains(k: int) -> bool:
            """Unmarked ``k`` joins a marked neighbor contiguously in the parent."""
            norm = child_pairs[k][1]
            if k > 0 and is_marked[k - 1]:
                if parent_stream.contains(child_pairs[k - 1][1] + " " + norm):
                    return True
            if k + 1 < count and is_marked[k + 1]:
                if parent_stream.contains(norm + " " + child_pairs[k + 1][1]):
                    return True
            return False

        pending = [
            k
            for k in range(count)
            if not is_marked[k]
            and ((k > 0 and is_marked[k - 1]) or (k + 1 < count and is_marked[k + 1]))
        ]
        while pending:
            k = pending.pop()
            if is_marked[k] or not chains(k):
                continue
            is_marked[k] = True
            marked.add(child_pairs[k][0])
            pending.extend(j for j in (k - 1, k + 1) if 0 <= j < count and not is_marked[j])

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
        if _find_divider(lines[i], localized=localized):
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
    """Run the ERP + custom + over-strip-guard + parent-diff pipeline.

    ``body`` is a non-blank decoded body (from ``text/plain`` or, for the HTML
    paths, the HTML's novel text). Returns ``(text, method)`` where ``method`` is
    unprefixed (``extract_new_text`` adds any ``html-`` prefix). Pure; the caller
    owns the try/except and the empty/status decision.
    """
    normalized = normalize_body(body)

    # Truncate at a forwarded/quote-header block *before* ERP. The scanner's
    # wrapped-attribution collapse deletes newlines inside the block ("On\n
    # Behalf Of X\nSent:" becomes one line), which hides the block from the
    # finder; on the intact body the finder sees the true shape. Measured on a
    # 95k-message corpus when fragment edge-stripping was removed (generation
    # 5): dropping this pre-truncation leaked quote headers in 10 messages and
    # reverted none, so it stays until the collapse itself is unified away (see
    # docs/generation-5.md).
    #
    # The Latin-script localized blocks and dividers are not applied to an
    # inline reply written below one (see _localized_inline_reply): the
    # message keeps its generation-5 treatment, where the parent-diff assist
    # below removes the quoted parent around the author's tagged answers.
    lines = normalized.split("\n")
    localized = not _localized_inline_reply(lines, parent_body)
    pretruncated = find_quote_header_block(lines, localized=localized) is not None
    if pretruncated:
        normalized = "\n".join(strip_after_quote_header_block(lines, localized=localized))

    # Primary: join every ERP fragment that is not quoted — *including*
    # signature fragments, which stage 1 must keep (stage 2 removes them). This
    # is why the scanner exposes fragments directly rather than upstream
    # email-reply-parser's ``parse_reply``, which drops them. Forwarded quote-header
    # blocks that survive (ERP re-joining can reshape one out of the truncation
    # above) arrive as non-quoted "header" fragments here; the custom pass
    # (``strip_after_quote_header_block`` / sign-off boundary) truncates them.
    erp_out = "\n".join(frag.content for frag in read_fragments(normalized) if not frag.quoted)
    cleaned = custom_clean(erp_out, localized=localized)
    custom_changed = pretruncated or cleaned != erp_out.strip()

    # Over-strip guard: dashed-separator digests make ERP truncate at the
    # first "---" rule. If the primary path dropped most of the body's
    # plainly-unquoted lines, redo the cleanup on the whole body.
    unquoted = count_unquoted_content_lines(normalized, localized=localized)
    if unquoted > 0 and _nonblank_line_count(cleaned) < _GUARD_MIN_RETAINED * unquoted:
        fallback = custom_clean(normalized, localized=localized)
        if fallback.strip():
            text = fallback
            method = "custom-fallback"
        else:
            text = cleaned
            method = "erp+custom" if custom_changed else "erp"
    else:
        text = cleaned
        method = "erp+custom" if custom_changed else "erp"

    # Parent-diff assist: runs *after* guard resolution (so it never
    # perturbs the over-strip guard's denominator). We adopt the assisted text
    # (and the "+parent-diff" suffix) only when it actually dropped a content
    # line: strip_parent_content also collapses internal blank runs, so a plain
    # string comparison would fire — mutating the text and mislabeling the
    # method — on a parent that removed nothing.
    if parent_body is not None and text.strip():
        assisted = strip_parent_content(text, parent_body, continuation=True)
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
    primary/guard logic has chosen the text and removes content that provably
    came from the parent (see :func:`strip_parent_content`), appending
    ``"+parent-diff"`` to the method. The default ``None`` is a behavioral no-op.

    When ``html_body`` (the decoded ``text/html`` part) is supplied, the HTML
    structure serves as an oracle in one of three ways — HTML-only, degenerate
    plain fallback, or quoted-text oracle assist — described in the module
    docstring. The default ``None`` leaves the plain-only behavior unchanged, so
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
                return _result("html-" + method, text)
            return ExtractionResult(text="", method="none", status=STATUS_EMPTY)

        # (b) Degenerate plain: a flattened plain body the HTML can replace.
        if parts is not None and _is_degenerate_plain(body, parts.novel_text):
            text, method = _core_extract(parts.novel_text, parent_body)
            return _result("html-" + method, text)

        # Normal plain path.
        text, method = _core_extract(body, parent_body)

        # (c) Oracle assist: remove plain-body content provably present in the
        # HTML's quoted text. As with the parent-diff assist, the trigger is the
        # non-blank-line count (never a string compare, which incidental blank-run
        # reformatting would trip), but here it must clear a block threshold so an
        # isolated coincidental match never strips genuine author content.
        if parts is not None and text.strip() and parts.quoted_text.strip():
            assisted = strip_parent_content(text, parts.quoted_text)
            if (
                _nonblank_line_count(text) - _nonblank_line_count(assisted)
                >= _HTML_QUOTE_MIN_REMOVED
            ):
                text = assisted
                method += "+html-quote"

        return _result(method, text)
    except Exception:  # noqa: BLE001 - never let one message stall the pipeline
        return ExtractionResult(text="", method="failed", status=STATUS_FAILED)


def _result(method: str, text: str) -> ExtractionResult:
    """Wrap resolved ``(method, text)`` in a result with the right empty status.

    A legitimately empty extraction (e.g. an exact re-send of the parent) keeps
    its resolved ``method`` but reports :data:`STATUS_EMPTY`.
    """
    if not text.strip():
        return ExtractionResult(text="", method=method, status=STATUS_EMPTY)
    return ExtractionResult(text=text, method=method, status=STATUS_OK)
