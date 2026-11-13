"""Fragment scanner: segment an email body into quoted / unquoted fragments.

Vendored and trimmed from ``email_reply_parser`` 0.5.12 (Zapier's Python port
of GitHub's Email Reply Parser, <https://github.com/zapier/email-reply-parser>,
MIT License, Copyright (c) 2012 Zapier LLC) when this package absorbed its only
runtime dependency. Only the ``EmailReplyParser.read()`` path the extractor
consumes is kept: the bottom-up line scan that groups lines into fragments and
labels each fragment quoted or not. The upstream ``parse_reply`` entry point,
the ``reply`` property, the hidden/visible bookkeeping and the dead
``quote_header`` method are dropped — none of them influence fragment
boundaries, fragment content, or the ``quoted`` flag.

The scan works bottom-up: lines are visited last-to-first, and a fragment grows
while successive lines share its quoted/header classification (with the quirks
encoded in :func:`_scan_line` below). A blank line directly above a
signature-looking line closes the fragment, which is a boundary effect the
extractor's output depends on even though the signature label itself is not
reported.

Two upstream defects were preserved byte-for-byte through generation 4 and
fixed in generation 5 (see ``docs/generation-5.md``):

1. Upstream capped the Outlook-boundary newline fix at 8 replacements per
   message by passing ``re.MULTILINE`` (value 8) as ``re.sub``'s positional
   ``count`` argument; the substitution now applies to every occurrence.
2. Upstream stripped each fragment's leading/trailing whitespace, so re-joining
   fragments could glue unrelated lines together — a signature line directly
   above a quote-header block's ``From:`` disguised the block from the
   extractor's detector. Fragment content now keeps its edges, preserving the
   blank lines that separate fragments.

Input is expected to be LF-normalized (:func:`.extraction.normalize_body` runs
first); the upstream CRLF replacement was dropped as unreachable.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_SIG_RE = re.compile(r"(--|__|-\w)|(^Sent from my (\w+\s*){1,3})")
_QUOTE_HDR_RE = re.compile("On.*wrote:$")
_QUOTED_RE = re.compile(r"(>+)")
_HEADER_RE = re.compile(r"^\*?(From|Sent|To|Subject):\*? .+")
#: Upstream's ``MULTI_QUOTE_HDR_REGEX`` (``re.DOTALL | re.MULTILINE``, used for
#: the substitution) and ``MULTI_QUOTE_HDR_REGEX_MULTILINE`` (``re.DOTALL``,
#: used for the search). Kept for reference only: run by the regex engine over
#: a whole body, the lookahead makes it cubic in the number of "On" + whitespace
#: positions ("On " * 800 takes over a second). :func:`_find_multi_quote_header`
#: computes the same match in linear time.
_MULTI_QUOTE_HDR = r"(?!On.*On\s.+?wrote:)(On\s(.+?)wrote:)"
#: An "On" followed by whitespace (``\s`` as in ``_MULTI_QUOTE_HDR``).
_ON_SPACE_RE = re.compile(r"On(?=\s)")
#: ``_MULTI_QUOTE_HDR`` without the lookahead, with the same two groups. Applied
#: with ``match`` at the one start position :func:`_find_multi_quote_header`
#: finds; the lazy ``.+?`` stops at the first ``wrote:``, so it is linear.
_MULTI_QUOTE_HDR_AT_RE = re.compile(r"(On\s(.+?)wrote:)", re.DOTALL)


@dataclass(frozen=True)
class Fragment:
    """One scanned fragment: its content (edges kept) and whether it is quoted."""

    content: str
    quoted: bool


class _OpenFragment:
    """A fragment still growing during the bottom-up scan."""

    __slots__ = ("quoted", "headers", "lines")

    def __init__(self, quoted: bool, first_line: str, headers: bool) -> None:
        self.quoted = quoted
        self.headers = headers
        self.lines = [first_line]

    def close(self) -> Fragment:
        # Lines were appended in bottom-up scan order; restore document order.
        # Edges are kept — see fix (2) in the module docstring.
        return Fragment(content="\n".join(reversed(self.lines)), quoted=self.quoted)


def _find_multi_quote_header(text: str) -> re.Match[str] | None:
    """The match of ``_MULTI_QUOTE_HDR`` in ``text``, found in linear time.

    The pattern matches ``On\\s`` at a position ``p``, then the first
    ``wrote:`` starting at ``p + 4`` or later (``.+?`` needs one character).
    Its lookahead rejects ``p`` when another such ``On\\s`` at ``q >= p + 2``
    also has a ``wrote:`` starting at ``q + 4`` or later. With ``W`` the start
    of the last ``wrote:``, the qualifying positions are the ``On\\s``
    positions ``p <= W - 4``, and the lookahead accepts only the last of them
    (two "On" occurrences are never one character apart, so every earlier one
    has a later one at ``q >= p + 2``). That position is the only match, both
    for ``search`` and for every match ``sub`` would replace.
    """
    last_wrote = text.rfind("wrote:")
    if last_wrote < 0:
        return None
    start = -1
    for on in _ON_SPACE_RE.finditer(text):
        if on.start() > last_wrote - 4:
            break
        start = on.start()
    if start < 0:
        return None
    return _MULTI_QUOTE_HDR_AT_RE.match(text, start)


def read_fragments(text: str) -> list[Fragment]:
    """Scan ``text`` (LF-normalized) into document-order fragments."""
    match = _find_multi_quote_header(text)
    if match:
        # Collapse a wrapped multi-line "On … wrote:" header onto one line. The
        # matched text is passed as a replacement *template*, exactly as
        # upstream did — a body containing backslash escapes can make this
        # raise, which the extractor's catch-all reports as a failed extraction.
        # Upstream ran ``re.sub`` with the pattern; it has exactly one match
        # (see :func:`_find_multi_quote_header`), so the template is expanded
        # against that match and spliced in. ``Match.expand`` parses the
        # template with the same rules and errors as ``re.sub``, and the match
        # carries the same two groups.
        template = match.group(1).replace("\n", "")
        text = text[: match.start()] + match.expand(template) + text[match.end() :]

    # Break the reply off an Outlook-style boundary line it sits directly on
    # top of — every occurrence; see fix (1) in the module docstring.
    text = re.sub("([^\n])(?=\n ?[_-]{7,})", "\\1\n", text)

    fragments: list[Fragment] = []
    open_fragment: _OpenFragment | None = None

    for line in reversed(text.split("\n")):
        open_fragment = _scan_line(fragments, open_fragment, line)

    if open_fragment is not None:
        fragments.append(open_fragment.close())

    fragments.reverse()
    return fragments


def _scan_line(
    fragments: list[Fragment], open_fragment: _OpenFragment | None, line: str
) -> _OpenFragment | None:
    """Fold one line (visited bottom-up) into the open fragment, or start a new one."""
    is_quote_header = _QUOTE_HDR_RE.match(line) is not None
    is_quoted = _QUOTED_RE.match(line) is not None
    is_header = is_quote_header or _HEADER_RE.match(line) is not None
    is_blank = len(line.strip()) == 0

    # A blank line directly above a signature-looking line closes the fragment
    # (the signature label itself was only consumed by the dropped hidden logic;
    # the boundary is what matters here).
    if open_fragment is not None and is_blank:
        if _SIG_RE.match(open_fragment.lines[-1].strip()):
            fragments.append(open_fragment.close())
            open_fragment = None

    if open_fragment is not None and (
        (open_fragment.headers == is_header and open_fragment.quoted == is_quoted)
        or (open_fragment.quoted and (is_quote_header or is_blank))
    ):
        open_fragment.lines.append(line)
        return open_fragment

    if open_fragment is not None:
        fragments.append(open_fragment.close())
    return _OpenFragment(quoted=is_quoted, first_line=line, headers=is_header)
