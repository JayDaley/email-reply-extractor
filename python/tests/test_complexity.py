"""Worst-case running time: adversarial inputs complete in linear time.

Each test feeds one input that drove a superlinear path of an earlier
implementation — regex backtracking or a quadratic scan — and asserts that the
call completes within :data:`_BOUND` seconds. The inputs are sized so that the
earlier implementation needed well over a minute on each of them; the current
one needs at most about two seconds, so the bound is not sensitive to the
speed of the machine. The larger inputs of the parent-diff cases are built
before the clock starts (:data:`_PREPARED_CASES`).

The tests pin running time, not output: equivalence of the output with the
earlier implementation was established by differential testing when the paths
were rewritten, except for the parent-diff aligned-run rule, whose output
changed by design in generation 7 (``docs/generation-7.md``). ``python/tools/conformance.py`` leaves this module out of the
vector recording, because its inputs run to megabytes.
"""

from __future__ import annotations

import time
from typing import Callable

import pytest

from email_reply_extractor._fragments import read_fragments
from email_reply_extractor.cleaning import clean_for_scoring
from email_reply_extractor.extraction import (
    custom_clean,
    extract_new_text,
    find_quote_header_block,
    is_attribution_line,
    strip_attribution_lines,
    strip_parent_content,
)
from email_reply_extractor.html_text import split_html_parts

#: Seconds each call may take.
_BOUND = 5.0


def _seed_chain(n: int) -> tuple[str, str]:
    """A child whose ``n`` short lines chain backward, one at a time, off three
    parent-diff seeds, and the parent that proves every link (rule (c))."""
    seeds = ["s1 s2 s3 s4 s5 s6 s7 s8", "t1 t2 t3 t4 t5 t6 t7 t8", "u1 u2 u3 u4 u5 u6 u7 u8"]
    child = "\n".join(["a"] * n + seeds)
    parent = "a a\na " + "\n".join(seeds)
    return child, parent


def _distinct_long_lines(n: int) -> tuple[str, str]:
    """A child and a parent of ``n`` lines of 30 words each, no word shared:
    every child line is a rewrap-rule (b) query that is not in the parent."""
    child = "\n".join(" ".join(f"c{k}x{j}" for j in range(30)) for k in range(n))
    parent = "\n".join(" ".join(f"p{k}x{j}" for j in range(30)) for k in range(n))
    return child, parent


def _remainder_chain(n: int) -> tuple[str, str]:
    """A re-wrapped paragraph: three 9-word seed lines, then ``n`` 3-word
    remainder lines that the continuation rule (c) chains off the seeds, each
    join a query against a parent of 10-word lines."""
    words = [f"w{k}" for k in range(3 * n + 27)]
    parent = "\n".join(" ".join(words[k : k + 10]) for k in range(0, len(words), 10))
    child = [" ".join(words[k : k + 9]) for k in range(0, 27, 9)]
    child += [" ".join(words[k : k + 3]) for k in range(27, len(words), 3)]
    return "\n".join(child), parent


def _own_quotation_segments(n: int) -> tuple[str, str]:
    """``n`` blocks, each a line of the parent between two longer lines of the
    author's; the parent doubles as the marked quotation, so rule (d) queries
    the quotation stream once per block and restores every segment."""
    child = "".join(
        f"x{k} y{k} z{k} q{k} r{k} s{k} t{k} u{k} v{k} w{k} o{k}\n"
        f"p{k} a{k} b{k} c{k} d{k} e{k} f{k} g{k}\n"
        f"m{k} n{k} o{k} p{k} q{k} r{k} s{k} t{k} u{k}\n\n"
        for k in range(n)
    )
    parent = "\n".join(f"> p{k} a{k} b{k} c{k} d{k} e{k} f{k} g{k}" for k in range(n))
    return child, parent


def _own_quotation_automaton(n: int) -> tuple[str, str]:
    """``n`` blocks whose segment the quotation stream's table cannot decide:
    every interior word pair of the segment occurs in the quotation (the
    parent without its last two words), which never contains the segment."""
    return "b b b b b b b b b b\na a a a a a a c\n\n" * n, "> " + "a " * 200000 + "c a"


def _localized_tags(n: int) -> tuple[str, str]:
    """A body with ``n`` tagged lines below a French quote header, and a
    parent that uses every tag: each tag is tested against the parent and
    the text above the block, and none belongs to the author."""
    quoted = "The quoted message carries enough words to be matched in the parent."
    body = (
        "Thanks for the review.\n" + "zz " * 100000 + "\nCheers,\nMed\n"
        "De : Someone <someone@example.org>\n"
        "Envoyé : lundi 1 janvier 2026 10:00\n"
        "À : list@example.org\n"
        "Objet : Re: review\n" + quoted + "\n" + "".join(f"[Q{k}] ok\n" for k in range(n))
    )
    parent = "zz " * 100000 + "\n" + quoted + "\n" + " ".join(f"[Q{k}]" for k in range(n))
    return body, parent


# (finding id, call). Comments give the earlier implementation's cost.
_CASES: list[tuple[str, Callable[[], object]]] = [
    # _MULTI_QUOTE_HDR lookahead: cubic (800 repeats took 1.2 s).
    ("A1-read_fragments", lambda: read_fragments("On " * 4000)),
    ("A1-extract", lambda: extract_new_text("hello\n" + "On " * 4000)),
    # Attribution collapse as a replacement template: every "\1" expanded to
    # the whole match, so the output was quadratic in size (4,000 repeats
    # built 32 MB in 0.34 s; 60,000 would build 7.2 GB).
    ("A8-read_fragments", lambda: read_fragments("On " + "\\1" * 60000 + " wrote:")),
    ("A8-extract", lambda: extract_new_text("hello\nOn " + "\\1" * 60000 + " wrote:\n> q")),
    # "<who> <a@b> wrote:": cubic (800 repeats took 0.36 s).
    ("A2-is_attribution_line", lambda: is_attribution_line("x" + "<a@" * 6400)),
    ("A2-extract", lambda: extract_new_text("hello\n" + "<a@" * 6400)),
    # "Am … schrieb …:" and the wrapped-attribution terminator: quadratic.
    ("A3-is_attribution_line", lambda: is_attribution_line("Am " + "schrieb: " * 60000 + "x")),
    ("A3-wrapped", lambda: strip_attribution_lines(["Am x", "schrieb: " * 60000 + "x"])),
    ("A3-extract", lambda: extract_new_text("hello\nAm " + "schrieb: " * 60000 + "x")),
    # "Original message" divider search over a dashed rule: quadratic
    # (10,000 dashes took 14 s).
    ("A4-extract", lambda: extract_new_text("hello\n" + "-" * 40000 + "\nbye")),
    # Sign-off line tail: quadratic (20,000 blanks took 2.1 s).
    ("A5-extract", lambda: extract_new_text("hello\nbest" + " " * 150000 + "x")),
    # Greeting tail, the same shape as A5.
    ("A5-greeting", lambda: clean_for_scoring("hi" + " " * 150000 + "x")),
    # Titled contact line: cubic, the shape of A2.
    ("A6-clean", lambda: clean_for_scoring("hello\nDr x" + "<a@" * 6400)),
    # Mailing-list footer: quadratic (8,000 repeats took 0.57 s).
    ("A7-clean", lambda: clean_for_scoring("hello\n" + "mailing list " * 100000)),
    # Address line: two adjacent blank-matching runs before a digit that never comes.
    ("address-line-clean", lambda: clean_for_scoring("hello\nx Office:" + " " * 400000)),
    # A stray close tag scanned the whole open-element stack: quadratic.
    ("B1-split_html_parts", lambda: split_html_parts("<a>" * 60000 + "</b>" * 60000)),
    # Every <blockquote> walked the whole stack for open Gmail wrappers.
    (
        "B2-split_html_parts",
        lambda: split_html_parts("<div class=gmail_quote>" * 80000 + "<blockquote>" * 80000),
    ),
    # Continuation rule: one full pass per backward-chained mark.
    (
        "C4-strip_parent_content",
        lambda: strip_parent_content(*_seed_chain(50000), continuation=True),
    ),
    # Quote-header walk from every From: line over one long field run.
    ("D1-find_quote_header_block", lambda: find_quote_header_block(["From: a@b x"] * 14000)),
    ("D1-extract", lambda: extract_new_text("hello\n" + "From: a@b x\n" * 10000)),
    # Pasted-evidence walk up a long field run from every From: line.
    ("D2-extract", lambda: extract_new_text("hello\n\n" + "X: y\n" * 8000 + "From: z\n" * 8000)),
    # Trailing sign-off debris: the whole tail reclassified per name line.
    ("E1-clean", lambda: clean_for_scoring("hello\n" + "A.io\n" * 10000)),
    # Custom signature divider: the rest of the text sliced per divider.
    ("E2-clean", lambda: clean_for_scoring("hello\n" + "\n===\n" * 120000)),
    # custom_clean dropped leading blank lines one pop(0) at a time.
    ("custom_clean-leading-blanks", lambda: custom_clean("\n" * 1000000 + "hello")),
    # Part boundaries: an inline reply of many short answers between quoted
    # lines; the alignment must not rescan the source per line.
    ("parts-extract", lambda: extract_new_text("a\n> q\n" * 100000)),
    # Part grouping in stage 2 over many boundaries.
    (
        "parts-clean",
        lambda: clean_for_scoring("a\n" * 200000, part_starts=range(1, 200000)),
    ),
]


# The G7 inputs are sized so that a slow CI interpreter (about three times the
# development machine's time) stays under the bound.
# (finding id, input builder, call). The input is built before timing starts.
# Comments give the earlier implementation's cost on the same input.
_PREPARED_CASES: list[tuple[str, Callable[[], tuple[str, str]], Callable[[str, str], object]]] = [
    # Aligned-run rule (a): difflib.SequenceMatcher, quadratic in the number of
    # lines (over 90 s).
    (
        "G7a-strip_parent_content",
        lambda: ("a\n" * 40000, "a\n" * 40000),
        strip_parent_content,
    ),
    # Rewrap rule (b): one substring search of the parent per child line
    # (93 s at 60,000 lines, quadratic: about 23 s here).
    ("G7b-strip_parent_content", lambda: _distinct_long_lines(30000), strip_parent_content),
    # Rule (b) queries that only the suffix automaton decides: every interior
    # word occurs in the parent, which never contains the line (77 s
    # at 1,000,000 lines, linear in the lines: about 31 s here).
    (
        "G7b-automaton-strip_parent_content",
        lambda: ("a a a a a a a b\n" * 400000, "a " * 200000),
        strip_parent_content,
    ),
    # Continuation rule (c): one substring search of the parent per join
    # (155 s at 300,000 lines, quadratic: about 39 s here).
    (
        "G7c-strip_parent_content",
        lambda: _remainder_chain(150000),
        lambda child, parent: strip_parent_content(child, parent, continuation=True),
    ),
    # Own-quotation rule (d): one quotation-stream query per segment, then the
    # continuation rule again (added with the rule; no earlier cost).
    (
        "own-quotation-strip_parent_content",
        lambda: _own_quotation_segments(50000),
        lambda child, parent: strip_parent_content(
            child, parent, continuation=True, quoted_text=parent
        ),
    ),
    (
        "own-quotation-automaton-strip_parent_content",
        lambda: _own_quotation_automaton(100000),
        lambda child, parent: strip_parent_content(
            child, parent, continuation=True, quoted_text=parent[:-4]
        ),
    ),
    # Inline-reply author tags: per tag, a search of the parent and of the
    # text above the quote header (99 s).
    ("G7tag-extract", lambda: _localized_tags(25000), extract_new_text),
]


@pytest.mark.parametrize("call", [call for _name, call in _CASES], ids=[n for n, _c in _CASES])
def test_completes_in_linear_time(call: Callable[[], object]) -> None:
    start = time.perf_counter()
    call()
    elapsed = time.perf_counter() - start
    assert elapsed < _BOUND, f"took {elapsed:.2f} s"


@pytest.mark.parametrize(
    ("build", "call"),
    [(build, call) for _name, build, call in _PREPARED_CASES],
    ids=[name for name, _b, _c in _PREPARED_CASES],
)
def test_prepared_input_completes_in_linear_time(
    build: Callable[[], tuple[str, str]], call: Callable[[str, str], object]
) -> None:
    child, parent = build()
    start = time.perf_counter()
    call(child, parent)
    elapsed = time.perf_counter() - start
    assert elapsed < _BOUND, f"took {elapsed:.2f} s"


def test_seed_chain_marks_every_line() -> None:
    # The C4 input exercises the rule it is meant to: every line chains.
    child, parent = _seed_chain(50)
    assert strip_parent_content(child, parent, continuation=True) == ""


def test_remainder_chain_marks_every_line() -> None:
    # The G7c input exercises the continuation rule: every remainder chains.
    assert strip_parent_content(*_remainder_chain(50), continuation=True) == ""


def test_own_quotation_input_restores_every_segment() -> None:
    # The own-quotation input exercises rule (d): every segment is restored.
    child, parent = _own_quotation_segments(20)
    assert strip_parent_content(child, parent, continuation=True) != child.strip()
    result = strip_parent_content(child, parent, continuation=True, quoted_text=parent)
    assert result == child.strip()


def test_own_quotation_automaton_input_reaches_the_quotation_query() -> None:
    # Every block is mostly unmatched and its segment is marked, so each one
    # queries the quotation stream, which does not contain it.
    child, parent = _own_quotation_automaton(20)
    plain = strip_parent_content(child, parent, continuation=True)
    assert "a a a a a a a c" not in plain
    quoted = parent[:-4]
    assert strip_parent_content(child, parent, continuation=True, quoted_text=quoted) == plain


def test_localized_tags_reach_every_tag() -> None:
    # The G7tag input keeps the localized treatment: no tag is the author's,
    # so the quote header truncates and only the text above it is left.
    body, parent = _localized_tags(50)
    result = extract_new_text(body, parent)
    assert result.text.endswith("Cheers,\nMed")
    assert "[Q" not in result.text
