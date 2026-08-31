"""Whitespace-tolerant equivalence between two derived texts.

A generation bump can move the exact bytes of derived text without changing
its substance: generation 5, for example, changed blank-line placement in most
outputs while changing the words of very few. A consumer that re-derives
stored text after upgrading (see ``docs/versioning.md``) can use
:func:`texts_equivalent` to tell the two apart — and, for instance, keep an
expensive downstream result (a paid classifier score) when only whitespace
moved, re-deriving it only when the substance did.

The comparison is the one the corpus quality tests in this repository use:
non-breaking spaces unified to spaces, each line's edge whitespace stripped,
blank lines dropped. Everything else — case, punctuation, non-ASCII content —
is compared exactly.
"""

from __future__ import annotations


def tolerant_lines(text: str) -> list[str]:
    """Normalize ``text`` for whitespace-tolerant comparison.

    Returns the non-blank lines of ``text``, each with non-breaking spaces
    (U+00A0) unified to ordinary spaces and leading/trailing whitespace
    stripped. Two texts whose ``tolerant_lines`` are equal differ at most in
    whitespace.
    """
    out: list[str] = []
    for line in text.split("\n"):
        line = line.replace("\xa0", " ").strip()
        if line:
            out.append(line)
    return out


def texts_equivalent(a: str, b: str) -> bool:
    """True when ``a`` and ``b`` differ at most in whitespace.

    ``tolerant_lines(a) == tolerant_lines(b)``, packaged for the common
    consumer decision: does re-derived text need re-processing, or did only
    its whitespace move?
    """
    return tolerant_lines(a) == tolerant_lines(b)
