"""Tests for the whitespace-tolerant equivalence helpers."""

from __future__ import annotations

from email_reply_extractor.equivalence import texts_equivalent, tolerant_lines


def test_tolerant_lines_drops_blank_lines_and_edge_whitespace():
    assert tolerant_lines("a\n\n  b  \n\n\nc\n") == ["a", "b", "c"]


def test_tolerant_lines_unifies_non_breaking_spaces():
    assert tolerant_lines("a\xa0b") == ["a b"]


def test_whitespace_only_difference_is_equivalent():
    assert texts_equivalent("One.\nTwo.\nThree.", "One.\n\n  Two.\n\nThree.\n")


def test_substance_difference_is_not_equivalent():
    assert not texts_equivalent("One.\nTwo.", "One.\nTwo. Three.")


def test_case_and_punctuation_are_compared_exactly():
    assert not texts_equivalent("one.", "One.")
    assert not texts_equivalent("a — b", "a - b")


def test_empty_and_blank_are_equivalent():
    assert texts_equivalent("", "\n  \n")
