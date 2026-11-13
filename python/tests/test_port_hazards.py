"""Pins of Python-specific semantics that the TypeScript and Rust ports must reproduce.

Each test exercises one behaviour of the reference implementation that follows
from a Python standard-library detail rather than from an extraction rule: the
``str.isspace`` whitespace set, Unicode ``\\w`` and ``str.isdigit`` /
``str.isupper``, ``$`` and ``\\r``, ``re.sub`` replacement-template processing,
and ``html.parser`` character-reference handling. The outputs are not claims
that the behaviour is desirable; they record what the reference does, so the
recorded conformance vectors (``make vectors``) cover these inputs and a port
that diverges fails on a named case. ``docs/ports.md`` lists the hazards.
"""

from __future__ import annotations

from email_reply_extractor import (
    clean_for_scoring,
    extract_new_text,
    html_to_text,
    split_html_parts,
    texts_equivalent,
    tolerant_lines,
)

# --- whitespace: str.strip() / str.split() use the str.isspace() set -----------


def test_strip_uses_python_whitespace_set():
    # U+0085 and U+2028 are whitespace to str.strip(); U+FEFF is not. U+001C is
    # whitespace too, but only stripped at the edges, never inside a line.
    text = "a\x1cb\n\x85x\x85\n\ufeffy\ufeff\n\u2028z\n \u3000 "
    assert tolerant_lines(text) == ["a\x1cb", "x", "\ufeffy\ufeff", "z"]


def test_equivalence_treats_feff_as_content():
    assert texts_equivalent("x\x85", "x")
    assert not texts_equivalent("\ufeffx", "x")


def test_html_whitespace_collapse_uses_python_whitespace_set():
    # " ".join(line.split()): U+001C and U+00A0 both collapse to a space.
    assert html_to_text("<p>r\x1cs\xa0t</p>") == "r s t"


# --- Unicode \w, str.isdigit(), str.isupper() ------------------------------------


def test_combining_mark_is_not_a_word_character():
    # "José" spelled with U+0301: the mark is not matched by Python's \w, so the
    # name line is not a name, and the sign-off is kept.
    result = clean_for_scoring("Hello.\n\nCheers,\nJose\u0301")
    assert result.text == "Cheers,\nJose\u0301"
    assert result.ignored_lines == [0]


def test_titlecase_letter_is_not_uppercase():
    # U+01C5 is titlecase (Lt): str.isupper() is False, so no sign-off name.
    result = clean_for_scoring("Text.\n\nRegards,\n\u01c5ohn")
    assert result.text == "Text.\n\nRegards,\n\u01c5ohn"
    assert result.ignored_lines == []


def test_superscript_digit_counts_as_digit():
    # str.isdigit() is True for U+00B2, so the line is not a plausible name.
    result = clean_for_scoring("Text.\n\nRegards,\nJohn\u00b2")
    assert result.text == "Text.\n\nRegards,\nJohn\u00b2"


def test_astral_letters_in_a_sign_off_name():
    # U+1D49C is a letter outside the BMP: one code point, two UTF-16 units.
    result = clean_for_scoring("Le\u00e9 this is text.\nThanks, Bob \U0001d49c")
    assert result.text == "Le\u00e9 this is text."
    assert result.ignored_lines == [1]


# --- $ and \r ---------------------------------------------------------------------


def test_carriage_return_blocks_end_anchored_rules():
    # Stage 2 does not normalize line endings: "Cheers\r" is not a bare
    # sign-off because [ \t]*$ does not absorb \r, and the output is rstripped.
    result = clean_for_scoring("Hello there all.\nSome text\r\nCheers\r")
    assert result.text == "Some text\nCheers"
    assert result.ignored_lines == [0]


# --- re.sub replacement templates in the fragment scanner -------------------------


def test_backslash_in_attribution_is_a_template_error():
    # The scanner passes the first "On ... wrote:" match to re.sub as a
    # replacement *template*; "\U" is a bad escape, re.sub raises, and the
    # failure is reported as a result instead of raised.
    body = "My reply.\n\nOn Mon, Jan 1, 2026 at 10:00 C:\\Users\\x wrote:\n> quoted\n"
    result = extract_new_text(body)
    assert (result.text, result.method, result.status) == ("", "failed", "failed")


def test_backslash_n_in_attribution_is_a_newline_escape():
    body = "My reply.\n\nOn Mon, Jan 1, 2026 at 10:00 A\\nB wrote:\n> quoted\n"
    result = extract_new_text(body)
    assert (result.text, result.method) == ("My reply.", "erp+custom")


def test_dollar_in_attribution_is_literal():
    body = "My reply.\n\nOn Mon, Jan 1, 2026 at 10:00 $1 $& wrote:\n> quoted\n"
    result = extract_new_text(body)
    assert (result.text, result.method) == ("My reply.", "erp")


# --- html.parser with convert_charrefs=True ---------------------------------------


def test_html_character_references():
    # Named references without ";" match by longest known prefix ("&noti;" is
    # "&not" + "i;"); numeric 0x80-0x9F map through windows-1252; NUL, lone
    # surrogates and out-of-range code points become U+FFFD. Comments, script
    # and style content are dropped.
    html = (
        "<p>a&ampb &copy2026 &#128; &#x0; &#xD800; &#1114112; &notit; &noti;</p>"
        "<!-- c --><script>x<p>y</script><style>z</style>q<br>r"
    )
    assert (
        html_to_text(html) == "a&b \u00a92026 \u20ac \ufffd \ufffd \ufffd \u00acit; \u00aci;\nq\nr"
    )


def test_duplicate_attribute_last_value_wins():
    html = (
        '<div class="gmail_signature" class="x">Sig &amp; co</div><p>hi</p>'
        "<div id=Signature>S2</div>"
    )
    parts = split_html_parts(html)
    assert parts.novel_text == "Sig & co\n\nhi"
    assert parts.signature_text == "S2"
