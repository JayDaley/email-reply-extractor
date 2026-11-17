"""Pins of Python-specific semantics that the TypeScript and Rust ports must reproduce.

Each test exercises one behaviour of the reference implementation that follows
from a Python standard-library detail rather than from an extraction rule: the
``str.isspace`` whitespace set, Unicode ``\\w``, ``\\d`` and ``\\b`` and
``str.isdigit`` / ``str.isupper`` / ``str.islower`` / ``str.isalpha``, ``$``
and ``\\r``, the removal of U+0000, the display-name folding of stage 2
(``str.lower`` and a literal table in place of NFKD, ``str.upper``), and
``html.parser`` character-reference handling. The outputs are not claims that the behaviour is desirable; they
record what the reference does, so the recorded conformance vectors (``make
vectors``) cover these inputs and a port that diverges fails on a named case.
``docs/ports.md`` lists the hazards.
"""

from __future__ import annotations

from email_reply_extractor import (
    clean_for_scoring,
    extract_new_text,
    html_to_text,
    learn_signature_lines,
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
    # U+01C5 is titlecase (Lt): str.isupper() and str.islower() are both False,
    # so after "Regards, " it is neither a name line nor a lower-case loose name.
    result = clean_for_scoring("Text.\n\nRegards, \u01c5ohn")
    assert result.text == "Text.\n\nRegards, \u01c5ohn"
    assert result.ignored_lines == []
    # U+01C4 is its uppercase form.
    assert clean_for_scoring("Text.\n\nRegards, \u01c4ohn").text == "Text."


def test_superscript_digit_counts_as_digit():
    # str.isdigit() is True for U+00B2, so "John\u00b2" is not a name line; \w
    # matches it, so it is still a loose name word, which the one-line form
    # after a comma does not accept when capitalized.
    result = clean_for_scoring("Text.\n\nRegards, John\u00b2")
    assert result.text == "Text.\n\nRegards, John\u00b2"
    # Under a salutation line a loose name word is enough.
    assert clean_for_scoring("Text.\n\nRegards,\nJohn\u00b2").text == "Text."


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


# --- stage 1: Unicode \d and \b, str.islower() / str.isalpha(), U+0000 ----------


def test_generic_attribution_clock_time_uses_unicode_digits():
    # \d matches Arabic-Indic digits: "١٠:١٥" is a clock time and "٢/٣" a
    # date, so the line is a generic attribution and is removed.
    body = "Answer.\n\nAna Lima \u0662/\u0663 \u0661\u0660:\u0661\u0665:\n> quoted"
    assert extract_new_text(body).text == "Answer."


def test_speech_verb_word_boundary_is_unicode():
    # "é" is a word character, so "Josésaid" holds no word "said" and the
    # line is kept.
    assert extract_new_text("Jos\u00e9said:\n> quoted").text == "Jos\u00e9said:"
    assert extract_new_text("Jos\u00e9 said:\n> quoted").status == "empty"


def test_wrap_remainder_lowercase_includes_other_lowercase():
    # U+00AA (feminine ordinal indicator) is Lo with Other_Lowercase:
    # str.islower() is True, so the line is a wrap remainder.
    above = "> The working group discussed the registry policy and reached a rough"
    body = f"Answer.\n{above}\n\u00aand\n> more"
    assert extract_new_text(body).text == "Answer."


def test_titlecase_letter_does_not_continue_a_generic_attribution():
    # U+01C5 is a letter (str.isalpha) but not lowercase, so "Ana Lima" does
    # not join the unit; with a lowercase start it does.
    body = "Ana Lima\n\u01c5x <ana@example.org>:\n> quoted"
    assert extract_new_text(body).text == "Ana Lima"
    assert extract_new_text("Ana Lima\nx <ana@example.org>:\n> quoted").status == "empty"


def test_combining_mark_continues_a_generic_attribution():
    # U+0301 is not a letter (str.isalpha is False), so a line starting with
    # it continues the line above.
    body = "Ana Lima\n\u0301x <ana@example.org>:\n> quoted"
    assert extract_new_text(body).status == "empty"


def test_nul_is_removed_before_extraction():
    # U+0000 marks generic attribution lines internally; a body's own U+0000
    # is removed by normalization, so this line is kept.
    result = extract_new_text("\x00Hello there.\n\x00\n> q")
    assert result.text == "Hello there."


# --- stage 2: display-name folding (str.lower, then the literal _FOLD_TABLE) -------


def _sender_line_removed(author: str, line: str) -> bool:
    return clean_for_scoring(f"Text.\n\n{line}", author_name=author).text == "Text."


def test_name_folding_sharp_s():
    # str.lower() keeps "ß"; the fold table maps it to "ss".
    assert _sender_line_removed("John Preu\u00df", "Preuss")
    assert _sender_line_removed("John Preuss", "PREU\u00df")


def test_name_folding_dotted_and_dotless_i():
    # "ı" (U+0131) is mapped to "i" explicitly; "İ" (U+0130) lowercases to
    # "i" + U+0307, and the table drops U+0307.
    assert _sender_line_removed("Ay\u015fe Y\u0131ld\u0131z", "Yildiz")
    assert _sender_line_removed("\u0130smail Example", "Ismail")


def test_name_folding_drops_combining_marks():
    # Composed and decomposed "é" fold alike; a line that is not a shared
    # sign-off name (see test_combining_mark_is_not_a_word_character) still
    # matches the sender.
    assert _sender_line_removed("Jos\u00e9 Example", "Jose\u0301")
    assert _sender_line_removed("Jose\u0301 Example", "Jos\u00e9")


def test_name_folding_explicit_letters_and_compatibility_forms():
    # "ø" and "æ" have no decomposition and are mapped explicitly; "Ǆ"
    # (U+01C4) lowercases to "ǆ", which the table maps to "dz"; "ſ" (U+017F)
    # maps to "s".
    assert _sender_line_removed("Bj\u00f8rn \u00c6sir", "Bjorn")
    assert _sender_line_removed("Bj\u00f8rn \u00c6sir", "Aesir")
    assert _sender_line_removed("\u01c4oe Example", "\u01c6oe")
    assert _sender_line_removed("\u01c4oe Example", "Dzoe")
    assert _sender_line_removed("Ross Example", "Ro\u017f\u017f")


def test_name_folding_is_limited_to_the_table():
    # Characters outside the table are only lowercased: full-width letters,
    # Greek letters with tonos and the final sigma are not decomposed or
    # case-folded, so they do not match their ASCII or unaccented forms.
    assert not _sender_line_removed("Jane Example", "\uff2a\uff41\uff4e\uff45")
    alexis = "\u0391\u03bb\u03ad\u03be\u03b7\u03c2"  # "Αλέξης"
    assert _sender_line_removed(f"{alexis} Example", alexis)
    assert not _sender_line_removed(f"{alexis} Example", "\u0391\u03bb\u03b5\u03be\u03b7\u03c2")
    assert not _sender_line_removed(f"{alexis} Example", "\u0391\u03bb\u03ad\u03be\u03b7\u03c3")
    # "…" (U+2026) is in the table as "...", which a sign-off name's edges lose.
    assert _sender_line_removed("Roy Example", "\u2026Roy")


def test_name_tokens_use_unicode_letters():
    # CJK ideographs are letters to \w: the name is one token and matches.
    assert _sender_line_removed("\u5c71\u7530\u592a\u90ce", "\u5c71\u7530\u592a\u90ce")
    # Devanagari vowel signs (Mc) are not \w, so "राम" splits into one-letter
    # runs and yields no token: no sender-name evidence.
    assert not _sender_line_removed("\u0930\u093e\u092e", "\u0930\u093e\u092e")


def test_learned_lines_lowercase_with_sharp_s_as_ss():
    # learn_signature_lines compares and returns lines lowercased with str.lower
    # and "ß" written "ss"; the table is not applied ("ü" is kept).
    texts = [f"Body {i} text here.\n\nGesch\u00e4ftsf\u00fchrer: Stra\u00dfe 1" for i in range(5)]
    assert learn_signature_lines(texts) == ("gesch\u00e4ftsf\u00fchrer: strasse 1",)
    cleaned = clean_for_scoring(
        "Text.\n\nGESCH\u00c4FTSF\u00dcHRER: STRASSE 1",
        signature_lines=["Gesch\u00e4ftsf\u00fchrer: Stra\u00dfe 1"],
    )
    assert cleaned.text == "Text."


def test_addressee_folding_in_greetings():
    body = "\n\nThe body text follows."
    for line, names in [("S\u00f8ren,", ["Soren Example"]), ("Soren,", ["S\u00f8ren Example"])]:
        assert clean_for_scoring(line + body, addressee_names=names).text == body.strip()


def test_acronym_test_uses_str_upper():
    # "ß".upper() is "SS", so a word holding "ß" is never an acronym: "STRAßE,"
    # is not a group-addressee line, while "WG," is. (The lower-case next line
    # keeps the structural rule out.)
    body = "\nthe body text follows."
    assert clean_for_scoring("WG, IETF," + body).text == body.strip()
    assert clean_for_scoring("STRA\u00dfE, IETF," + body).text == "STRA\u00dfE, IETF," + body


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
