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
_MULTI_QUOTE_HDR = r"(?!On.*On\s.+?wrote:)(On\s(.+?)wrote:)"
#: Upstream's ``MULTI_QUOTE_HDR_REGEX`` (used for the substitution).
_MULTI_QUOTE_HDR_SUB_RE = re.compile(_MULTI_QUOTE_HDR, re.DOTALL | re.MULTILINE)
#: Upstream's ``MULTI_QUOTE_HDR_REGEX_MULTILINE`` (used for the search) — the
#: names are swapped relative to their flags upstream; both are kept verbatim.
_MULTI_QUOTE_HDR_SEARCH_RE = re.compile(_MULTI_QUOTE_HDR, re.DOTALL)


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


def read_fragments(text: str) -> list[Fragment]:
    """Scan ``text`` (LF-normalized) into document-order fragments."""
    match = _MULTI_QUOTE_HDR_SEARCH_RE.search(text)
    if match:
        # Collapse a wrapped multi-line "On … wrote:" header onto one line. The
        # matched text is passed as a replacement *template*, exactly as
        # upstream did — a body containing backslash escapes can make this
        # raise, which the extractor's catch-all reports as a failed extraction.
        text = _MULTI_QUOTE_HDR_SUB_RE.sub(match.groups()[0].replace("\n", ""), text)

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
