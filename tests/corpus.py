"""Shared helpers for the fixture corpus: decode a ``.eml`` into its bodies.

The decoding mirrors, byte for byte, the message parsing of the application
this package was extracted from (mailing-list-ai-check's ``fetcher``): headers
parsed with ``policy=default``, the ``text/plain`` and ``text/html`` parts
decoded leniently — an unknown or broken charset never raises, falling back to
a UTF-8 decode with ``errors="replace"``. The recorded corpus digest in
``test_extraction_version.py`` was carried over from that application
unchanged, which is only valid while this decoding matches.
"""

from __future__ import annotations

import email
import pathlib
from email import policy
from email.message import EmailMessage

FIXTURE_DIR = pathlib.Path(__file__).parent / "fixtures"
EXPECTED_DIR = FIXTURE_DIR / "expected"

ALL_STEMS = sorted(p.stem for p in FIXTURE_DIR.glob("*.eml"))


def _decode_part(part: EmailMessage) -> str | None:
    """Decode one MIME part to text, falling back on a lenient UTF-8 decode."""
    try:
        content = part.get_content()
    except (LookupError, ValueError, UnicodeDecodeError):
        payload = part.get_payload(decode=True) or b""
        content = payload.decode("utf-8", errors="replace")
    return content if content else None


def fixture_bodies(stem: str) -> tuple[str | None, str | None]:
    """Return the decoded ``(text/plain, text/html)`` bodies of a fixture."""
    raw = (FIXTURE_DIR / f"{stem}.eml").read_bytes()
    msg = email.message_from_bytes(raw, policy=policy.default)
    html_part = msg.get_body(preferencelist=("html",))
    html_body = _decode_part(html_part) if html_part is not None else None
    plain_part = msg.get_body(preferencelist=("plain",))
    body = _decode_part(plain_part) if plain_part is not None else None
    return body, html_body


def fixture_body(stem: str) -> str | None:
    return fixture_bodies(stem)[0]


def fixture_html(stem: str) -> str | None:
    """The message's decoded ``text/html`` part (the extraction oracle), if any."""
    return fixture_bodies(stem)[1]


def expected_text(stem: str) -> str:
    return (EXPECTED_DIR / f"{stem}.txt").read_text(encoding="utf-8")
