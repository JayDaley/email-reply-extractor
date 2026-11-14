"""The parent-diff assist's matching structures (generation 7).

- Rule (a), the alignment around the longest common run of lines
  (``_aligned_run_marks``): the tie-breaks of its specification, and a
  brute-force transcription of the specification compared on random inputs.
- Rules (b) and (c), the substring index over the parent's word-stream
  (``_ParentStream``): its answers equal Python's ``in`` on every path.
- The inline-reply author-tag test (``_any_author_tag``): its answers equal the
  per-label ``in`` and regular-expression tests it replaced.

``_aligned_run_marks`` and ``strip_parent_content`` are imported by name, so
the tie-break cases are recorded as conformance vectors. The randomized tests
call through the module object instead, so their many calls are not recorded.
"""

from __future__ import annotations

import random
import re

from email_reply_extractor import extraction
from email_reply_extractor.extraction import _aligned_run_marks, strip_parent_content


def _long(tag: str) -> str:
    """A line of 9 words: substance on its own."""
    return f"{tag} long line with enough words to carry substance"


def _short(tag: str) -> str:
    """A line of 2 words: no substance on its own."""
    return f"{tag} short"


# --- rule (a): the specification's tie-breaks -------------------------------------


def test_anchor_tie_in_the_child_takes_the_earliest_start():
    # Two common runs of one line each. The anchor is the one starting first in
    # the child (a, at parent 1); b lies after it in the child but before it in
    # the parent, so it is not matched.
    a, b = _long("a"), _long("b")
    assert _aligned_run_marks([a, b], [b, a]) == [True, False]


def test_anchor_takes_the_earliest_parent_occurrence():
    # The anchor A B occurs twice in the parent; the first occurrence (parent
    # 1) is used. x precedes the anchor in the child, and only y precedes the
    # first occurrence in the parent, so x is not matched. Anchored at the
    # second occurrence (parent 5), x would be.
    x, a, b, y = _long("x"), _long("A"), _long("B"), _long("y")
    child = [x, a, b, y]
    parent = [y, a, b, x, _long("z"), a, b]
    assert _aligned_run_marks(child, parent) == [False, True, True, False]


def test_runs_on_one_side_match_in_any_order():
    # The anchor (three short lines) has no substance and is not marked, but it
    # still splits the sequences. Before it, p and q appear in the opposite
    # order in the parent; both are matched.
    p, q = _long("p"), _long("q")
    tail = [_short("A"), _short("B"), _short("C")]
    assert _aligned_run_marks([p, q, *tail], [q, p, *tail]) == [True, True, False, False, False]


def test_an_anchor_without_substance_still_splits_the_sequences():
    # The tie between s and L goes to s (earliest in the child); L follows s in
    # the child but precedes it in the parent, so L is not matched.
    s, big = _short("s"), _long("L")
    assert _aligned_run_marks([s, big], [big, s]) == [False, False]


def test_the_longest_run_is_the_anchor_even_when_it_starts_later():
    # b c (two lines) outranks a (one line); a lies before the anchor in the
    # child and after it in the parent.
    a, x, b, c = _long("a"), _short("x"), _long("b"), _long("c")
    assert _aligned_run_marks([a, x, b, c], [b, c, a]) == [False, False, True, True]


def test_a_side_run_has_substance_by_its_total_word_count():
    # Two 5-word lines (10 words) before the anchor form a run with substance;
    # two 4-word lines (8 words) after it do not.
    five = ["one two three four five", "six seven eight nine ten"]
    four = ["aa bb cc dd", "ee ff gg hh"]
    anchor = [_long("m"), _long("n"), _long("o")]
    child = five + anchor + four
    parent = five + [_short("x")] + anchor + [_short("y")] + four
    assert _aligned_run_marks(child, parent) == [True, True, True, True, True, False, False]


def test_nothing_is_marked_without_a_common_line():
    assert _aligned_run_marks([_long("a")], [_long("b")]) == [False]
    assert _aligned_run_marks([], [_long("b")]) == []
    assert _aligned_run_marks([_long("a")], []) == [False]


def _unit(tag: str) -> list[str]:
    """Two 5-word lines: a run with substance (10 words) whose lines are too
    short for rule (b), so only rule (a) can mark them."""
    return [f"{tag} one two three four", f"{tag} five six seven eight"]


def _text(*units: list[str]) -> str:
    return "\n".join(line for unit in units for line in unit)


def test_tie_breaks_through_strip_parent_content():
    # The tie-break cases as whole calls, built from two-line units so that
    # rule (a) alone decides: the ports replay these vectors.
    a, b = _unit("a"), _unit("b")
    assert strip_parent_content(_text(a, b), _text(b, a)) == _text(b)
    x, aa, bb, y, z = _unit("x"), _unit("A"), _unit("B"), _unit("y"), _unit("z")
    child = _text(x, aa, bb, y)
    parent = _text(y, aa, bb, x, z, aa, bb)
    assert strip_parent_content(child, parent) == _text(x, y)
    p, q = _unit("p"), _unit("q")
    tail = [_short("A"), _short("B"), _short("C")]
    assert strip_parent_content(_text(p, q, tail), _text(q, p, tail)) == _text(tail)
    s, big = [_short("s1"), _short("s2")], _unit("L")
    assert strip_parent_content(_text(s, big), _text(big, s)) == _text(s, big)


# --- rule (a): brute-force reference ------------------------------------------------


def _reference_marks(c: list[str], p: list[str]) -> list[bool]:
    """The specification of rule (a), transcribed literally: every run and
    every occurrence is enumerated."""
    n, m = len(c), len(p)
    marks = [False] * n
    words = [len(line.split()) for line in c]

    def occurrences(s: int, e: int, lo: int, hi: int) -> list[int]:
        k = e - s
        return [j for j in range(lo, hi - k + 1) if p[j : j + k] == c[s:e]]

    def substance(s: int, e: int) -> bool:
        return sum(words[s:e]) >= 10 or any(w >= 8 for w in words[s:e])

    runs = [(s, e) for s in range(n) for e in range(s + 1, n + 1) if occurrences(s, e, 0, m)]
    if not runs:
        return marks
    best = max(e - s for s, e in runs)
    i, end = min((s, e) for s, e in runs if e - s == best)
    j = min(occurrences(i, end, 0, m))
    if substance(i, end):
        for x in range(i, end):
            marks[x] = True
    for c_lo, c_hi, p_lo, p_hi in ((0, i, 0, j), (end, n, j + best, m)):
        for s in range(c_lo, c_hi):
            for e in range(s + 1, c_hi + 1):
                if substance(s, e) and occurrences(s, e, p_lo, p_hi):
                    for x in range(s, e):
                        marks[x] = True
    return marks


def _random_sequences(rng: random.Random, max_len: int) -> tuple[list[str], list[str]]:
    """Child and parent over a small alphabet of lines of 1 to 9 words, often
    with a slice of the parent planted in the child."""
    alphabet = [
        f"s{a}" + " w" * (rng.choice((1, 1, 2, 3, 4, 5, 7, 8, 9)) - 1)
        for a in range(rng.choice((1, 2, 3, 4, 5, 6)))
    ]
    c = [rng.choice(alphabet) for _ in range(rng.randint(0, max_len))]
    p = [rng.choice(alphabet) for _ in range(rng.randint(0, max_len))]
    if p and rng.random() < 0.5:
        a = rng.randrange(len(p))
        b = rng.randint(a + 1, len(p))
        at = rng.randint(0, len(c))
        c = c[:at] + p[a:b] + c[at:]
    return c, p


def test_aligned_run_marks_match_the_reference_on_random_inputs():
    rng = random.Random(20261114)
    for _ in range(3000):
        c, p = _random_sequences(rng, 8)
        assert extraction._aligned_run_marks(c, p) == _reference_marks(c, p), (c, p)


# --- rules (b) and (c): the parent word-stream index ------------------------------


def _random_stream_case(rng: random.Random) -> tuple[list[str], list[str]]:
    """Parent lines and needles in the normalized form the rules produce: words
    of a small alphabet (so words repeat and are prefixes and suffixes of one
    another) separated by single spaces."""
    vocab = rng.choice(
        (["a", "b"], ["a", "ab", "ba", "b"], ["x", "xy", "yx", "y", "z", "é", "\U0001f600"])
    )
    words = [rng.choice(vocab) for _ in range(rng.randint(0, 30))]
    parent: list[str] = []
    k = 0
    while k < len(words):
        step = rng.randint(1, 5)
        parent.append(" ".join(words[k : k + step]))
        k += step
    stream = " ".join(parent)
    needles = []
    for _ in range(12):
        if stream and rng.random() < 0.5:
            # A slice of the stream trimmed to whole needle form.
            lo = rng.randrange(len(stream))
            hi = rng.randint(lo + 1, len(stream))
            needle = " ".join(stream[lo:hi].split(" ")).strip(" ")
            if not needle or "  " in needle:
                continue
            if rng.random() < 0.3:  # perturb one character
                at = rng.randrange(len(needle))
                if needle[at] != " ":
                    needle = needle[:at] + rng.choice(vocab)[0] + needle[at + 1 :]
        else:
            needle = " ".join(rng.choice(vocab) for _ in range(rng.randint(1, 8)))
        needles.append(needle)
    return parent, needles


def test_parent_stream_answers_equal_substring_search():
    rng = random.Random(7)
    for _ in range(4000):
        parent, needles = _random_stream_case(rng)
        index = extraction._ParentStream(parent)
        stream = " ".join(parent)
        for needle in needles:
            assert index.contains(needle) == (needle in stream), (parent, needle)


def test_parent_stream_each_path():
    # The stream is "so the cat sat and the cat ran": the words "the" and "cat"
    # and the pair "the cat" occur twice, every other word and pair once.
    index = extraction._ParentStream(["so the cat sat", "and the cat ran"])
    # Longer than the stream.
    assert not index.contains("x " * 20 + "x")
    # Three words: the interior word decides when absent or unique.
    assert not index.contains("so dog cat")
    assert index.contains("t sat a")
    assert not index.contains("xx so the")  # the only start precedes the stream
    # Four or more words: the interior pairs decide when absent or unique.
    assert not index.contains("so the sat and")
    assert index.contains("o the cat sat a")
    assert not index.contains("so the cat sat on")
    # Every interior gram repeated: a match at the first occurrence decides.
    assert index.contains("o the cat s")
    # Every interior gram repeated and no match at the first occurrence: the
    # automaton decides.
    assert index.contains("d the cat r")
    assert not index.contains("d the cat s")
    assert not index.contains("xso the cat")
    # One or two words: the automaton, including matches inside words.
    assert index.contains("at an")
    assert not index.contains("ran so")
    assert index.contains("t")
    stream = "so the cat sat and the cat ran"
    for needle in ("so dog cat", "t sat a", "so the sat and", "o the cat s", "d the cat r"):
        assert index.contains(needle) == (needle in stream)


# --- the inline-reply author-tag index --------------------------------------------


def _reference_any_author_tag(labels: list[str], head: str, parent_body: str | None) -> bool:
    """The generation-6 per-label tests."""
    for label in labels:
        if parent_body is not None and f"[{label}" not in parent_body:
            return True
        name = label.rstrip("0123456789").strip()
        if name and re.search(rf"(?<!\w){re.escape(name)}(?!\w)", head) is not None:
            return True
    return False


#: Characters for random tags and texts: word characters (letters, digits,
#: "_", a Latin letter with a diacritic, an Arabic-Indic digit) and non-word
#: characters (space, ".", "-", "[", newline).
_TAG_ALPHABET = "aAbM1_é٣ .-"
_TEXT_ALPHABET = _TAG_ALPHABET + "[\n"


def test_any_author_tag_equals_the_per_label_tests():
    rng = random.Random(11)
    for _ in range(4000):
        labels = [
            "".join(rng.choice(_TAG_ALPHABET) for _ in range(rng.randint(1, 4)))
            for _ in range(rng.randint(1, 3))
        ]
        head = "".join(rng.choice(_TEXT_ALPHABET) for _ in range(rng.randint(0, 30)))
        if rng.random() < 0.2:
            parent = None
        else:
            parent = "".join(rng.choice(_TEXT_ALPHABET) for _ in range(rng.randint(0, 30)))
            for label in labels:
                if rng.random() < 0.7:
                    at = rng.randint(0, len(parent))
                    parent = parent[:at] + "[" + label + parent[at:]
        want = _reference_any_author_tag(labels, head, parent)
        assert extraction._any_author_tag(labels, head, parent) == want, (labels, head, parent)


def test_any_author_tag_cases():
    # A label the parent never uses is the author's.
    assert extraction._any_author_tag(["Med"], "", "no tags here")
    # A label the parent uses belongs to the author only when its name (without
    # a round number) is a word of the head.
    assert extraction._any_author_tag(["Med2"], "Cheers,\nMed", "[Med2] earlier")
    assert not extraction._any_author_tag(["Med"], "Cheers,\nMedina", "[Med] earlier")
    assert not extraction._any_author_tag(["Med"], "Cheers,\n_Med", "[Med] earlier")
    # Names may begin or end with non-word characters; the boundary is tested
    # on the characters around the occurrence only.
    assert extraction._any_author_tag(["J. D."], "-- J. D. x", "[J. D.]")
    assert not extraction._any_author_tag(["J. D."], "-- J. D.x", "[J. D.]")
    assert not extraction._any_author_tag(["J. D."], "-- xJ. D.", "[J. D.]")
    # A label that is only digits has no name.
    assert not extraction._any_author_tag(["12"], "12", "[12]")
    # Without the parent only the head decides.
    assert extraction._any_author_tag(["Linda3"], "Linda", None)
    assert not extraction._any_author_tag(["Linda3"], "Lindas", None)
