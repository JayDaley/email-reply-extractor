"""Worst-case running time: adversarial inputs complete in linear time.

Each test feeds one input that drove a superlinear path of an earlier
implementation — regex backtracking or a quadratic scan — and asserts that the
call completes within :data:`_BOUND` seconds. The inputs are sized so that the
earlier implementation needed well over a minute on each of them; the current
one needs a small fraction of a second, so the bound is generous and not
sensitive to the speed of the machine.

The tests pin running time, not output: equivalence of the output with the
earlier implementation was established by differential testing when the paths
were rewritten. ``python/tools/conformance.py`` leaves this module out of the
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
]


@pytest.mark.parametrize("call", [call for _name, call in _CASES], ids=[n for n, _c in _CASES])
def test_completes_in_linear_time(call: Callable[[], object]) -> None:
    start = time.perf_counter()
    call()
    elapsed = time.perf_counter() - start
    assert elapsed < _BOUND, f"took {elapsed:.2f} s"


def test_seed_chain_marks_every_line() -> None:
    # The C4 input exercises the rule it is meant to: every line chains.
    child, parent = _seed_chain(50)
    assert strip_parent_content(child, parent, continuation=True) == ""
