"""A fail-safe guard on :data:`~email_reply_extractor.extraction.EXTRACTION_VERSION`.

``EXTRACTION_VERSION`` is only meaningful if it is incremented whenever the
routine behind the derived text changes. Nothing enforces that automatically —
the constant is hand-maintained — so this module pins the routine's *real
behaviour*: a single SHA-256 over the composite output of every ``.eml``
fixture in ``tests/fixtures``, taken in stem order. Any change to
:mod:`~email_reply_extractor.extraction`, :mod:`~email_reply_extractor.cleaning`,
:mod:`~email_reply_extractor.html_text` or the vendored fragment scanner that
moves what a fixture produces moves the digest, and the developer has to
decide, deliberately, whether it was a behaviour change (bump the constant,
re-record the digest) or a mistake (revert).

This is stricter than the corpus test in ``tests/test_extraction.py``, which
compares with ``tolerant_lines()`` — blank lines dropped, each line stripped. A
whitespace-only difference passes there and still changes the exact bytes a
consumer derives from a message. The digest is taken over the exact strings.

Three values per fixture go into it: the composite (stage 1 then stage 2), plus
stage 1's ``text`` and ``method``, so a change confined to stage 1 that stage 2
happens to erase is still caught.
"""

from __future__ import annotations

import hashlib

from corpus import ALL_STEMS, fixture_bodies

from email_reply_extractor.cleaning import clean_for_scoring
from email_reply_extractor.extraction import EXTRACTION_VERSION, extract_new_text

#: SHA-256 of :func:`corpus_digest` over the fixture corpus, recorded against
#: ``EXTRACTION_VERSION = 5`` (fragment edges kept, Outlook-boundary fix
#: uncapped — see ``docs/generation-5.md``). Re-record it (and bump the
#: constant) only when the change in behaviour is intended. Under generation 4
#: the digest was ``faf5f388795897201e92a58600b345c19656df939228bd87bd47ab1578f1db5f``,
#: carried unchanged from mailing-list-ai-check, the application this package
#: was extracted from.
EXPECTED_DIGEST = "2f36f0168c2769b122a0f74c0cfa63a714da3c89f2cd1f90d0f5e072f32d6fb3"

#: The generation the digest above was recorded against. It exists so that a
#: bump without a re-record, or a re-record without a bump, is visible in the
#: diff of this file.
DIGEST_EXTRACTION_VERSION = 5


def corpus_digest() -> str:
    """Return the SHA-256 over the whole corpus's derived text.

    For each fixture, in stem order: the composite text, then stage 1's text
    and method. Every field is length-prefixed so no combination of contents
    can imitate a different split.
    """
    digest = hashlib.sha256()
    for stem in ALL_STEMS:
        body, html_body = fixture_bodies(stem)
        stage1 = extract_new_text(body, html_body=html_body)
        composite = clean_for_scoring(stage1.text).text
        for field in (stem, composite, stage1.text, stage1.method):
            encoded = field.encode("utf-8")
            digest.update(f"{len(encoded)}:".encode("ascii"))
            digest.update(encoded)
    return digest.hexdigest()


def test_the_corpus_has_fixtures_to_hash():
    """A digest over an empty corpus would pin nothing and still pass."""
    assert len(ALL_STEMS) >= 20


def test_extraction_output_matches_the_recorded_digest():
    assert corpus_digest() == EXPECTED_DIGEST, (
        "The extraction/cleaning pipeline no longer produces the text this digest "
        "was recorded against.\n"
        "If the change is intended: increment EXTRACTION_VERSION in "
        "src/email_reply_extractor/extraction.py, then re-record EXPECTED_DIGEST "
        "and DIGEST_EXTRACTION_VERSION in this file with the value printed by\n"
        "  .venv/bin/python -c \"import sys; sys.path.insert(0, 'tests'); "
        'import test_extraction_version as t; print(t.corpus_digest())"\n'
        "Downstream consumers that stamp stored text with EXTRACTION_VERSION then "
        "read that text as stale and can re-derive it.\n"
        "If the change is not intended: revert it — the exact derived bytes, and "
        "anything consumers key on them, have moved."
    )


def test_the_digest_was_recorded_against_the_current_generation():
    """Bumping the constant without re-recording the digest is incomplete work."""
    assert DIGEST_EXTRACTION_VERSION == EXTRACTION_VERSION, (
        "EXTRACTION_VERSION changed but the digest in this file was not re-recorded; "
        "record the current corpus_digest() and set DIGEST_EXTRACTION_VERSION to match."
    )
