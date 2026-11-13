"""A fail-safe guard on :data:`~email_reply_extractor.extraction.EXTRACTION_VERSION`.

``EXTRACTION_VERSION`` is only meaningful if it is incremented whenever the
routine behind the derived text changes. Nothing enforces that automatically —
the constant is hand-maintained — so the repository pins the routine's *real
behaviour*: a single SHA-256 over the composite output of every ``.eml``
fixture in ``fixtures/``, taken in stem order. Any change to
:mod:`~email_reply_extractor.extraction`, :mod:`~email_reply_extractor.cleaning`,
:mod:`~email_reply_extractor.html_text` or the vendored fragment scanner that
moves what a fixture produces moves the digest, and the developer has to
decide, deliberately, whether it was a behaviour change (bump the constant,
re-record the digest) or a mistake (revert).

The digest and the generation it was recorded against live in
``conformance/digest.json``, the single record shared by every port of the
library. ``make vectors`` re-records it, and refuses to when the digest moved
without an ``EXTRACTION_VERSION`` increment or a change to the fixture set.
``conformance/README.md`` specifies the algorithm and lists the digest of
every earlier generation.

This is stricter than the corpus test in ``tests/test_extraction.py``, which
compares with ``tolerant_lines()`` — blank lines dropped, each line stripped. A
whitespace-only difference passes there and still changes the exact bytes a
consumer derives from a message. The digest is taken over the exact strings.

Four values per fixture go into it: the stem, the composite (stage 1 then
stage 2), and stage 1's ``text`` and ``method``, so a change confined to
stage 1 that stage 2 happens to erase is still caught.
"""

from __future__ import annotations

from conformance_support import DIGEST_PATH, corpus_entries, digest_of_entries, load
from corpus import ALL_STEMS

from email_reply_extractor.extraction import EXTRACTION_VERSION

_RERECORD_HINT = (
    "If the change is intended: increment EXTRACTION_VERSION in "
    "python/src/email_reply_extractor/extraction.py, then run `make vectors` from the "
    "repository root, which re-records conformance/digest.json together with the rest "
    "of the conformance data. Downstream consumers that stamp stored text with "
    "EXTRACTION_VERSION then read that text as stale and can re-derive it.\n"
    "If the change is not intended: revert it — the exact derived bytes, and anything "
    "consumers key on them, have moved."
)


def corpus_digest() -> str:
    """Return the SHA-256 over the whole corpus's derived text, computed live."""
    return digest_of_entries(corpus_entries())


def test_the_corpus_has_fixtures_to_hash():
    """A digest over an empty corpus would pin nothing and still pass."""
    assert len(ALL_STEMS) >= 20


def test_extraction_output_matches_the_recorded_digest():
    recorded = load(DIGEST_PATH)
    assert corpus_digest() == recorded["digest"], (
        "The extraction/cleaning pipeline no longer produces the text the digest in "
        "conformance/digest.json was recorded against.\n" + _RERECORD_HINT
    )


def test_the_digest_was_recorded_against_the_current_generation():
    """Bumping the constant without re-recording the digest is incomplete work."""
    recorded = load(DIGEST_PATH)
    assert recorded["extraction_version"] == EXTRACTION_VERSION, (
        "EXTRACTION_VERSION changed but conformance/digest.json was not re-recorded; "
        "run `make vectors` from the repository root."
    )
