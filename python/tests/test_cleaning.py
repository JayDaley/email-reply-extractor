"""Unit tests for stage-2 cleaning (``clean_for_scoring``).

Stage 2 removes the author's formulaic furniture — greetings, sign-offs,
signature blocks, mailing-list footers, mobile taglines — from the stage-1
extracted text, and reports which non-blank input lines it removed
(``ignored_lines``) so the dashboard can grey them out. These tests target that
removal directly (via :func:`clean_for_scoring` and its helpers) plus the
signature-heavy fixtures at the composite (stage-1 + stage-2) level.
"""

from __future__ import annotations

import pytest
from corpus import expected_text, fixture_body

from email_reply_extractor.equivalence import tolerant_lines
from email_reply_extractor.cleaning import (
    CleanResult,
    clean_for_scoring,
    is_mobile_tagline,
    is_signature_delimiter,
    learn_signature_lines,
)
from email_reply_extractor.extraction import extract_new_text


def scored(text: str) -> str:
    """The cleaned text alone (drops the ignored-line bookkeeping)."""
    return clean_for_scoring(text).text


# --- signature delimiter ------------------------------------------------------


@pytest.mark.parametrize("line", ["-- ", "--", "--   "])
def test_is_signature_delimiter_true(line):
    assert is_signature_delimiter(line)


@pytest.mark.parametrize("line", ["--eh.", "-markku", "- bullet", "-- eh"])
def test_is_signature_delimiter_false(line):
    assert not is_signature_delimiter(line)


def test_truncates_at_hard_delimiter():
    text = "Real content.\n-- \nSignature line\nmore signature"
    assert scored(text) == "Real content."


def test_truncates_at_custom_signature_divider():
    # The Spark custom-signature shape: a "========" rule after a blank line,
    # then a name line opening a long contact block.
    text = (
        "Real content.\n\nThank you,\n\n"
        "========\n"
        "Tokachi Kamimura\n"
        "Executive Director\n"
        "Standards Organization\n"
        "Overview: https://example.org/overview/\n"
    )
    cleaned = scored(text)
    assert cleaned == "Real content."
    assert "Kamimura" not in cleaned


def test_markdown_heading_underline_is_not_a_signature_divider():
    # A "====" rule directly under a text line is a heading underline; nothing
    # is truncated even when a name-like line follows.
    text = "Reviewers\n=========\nJohn Smith\nJane Doe\n\nBoth approved the draft."
    cleaned = scored(text)
    assert "John Smith" in cleaned
    assert "Both approved the draft." in cleaned


def test_divider_without_name_line_is_kept():
    # A rule after a blank line but followed by prose is an authored section
    # break, not a signature.
    text = "Part one.\n\n====\n\nThe second part continues the argument here."
    cleaned = scored(text)
    assert "The second part continues the argument here." in cleaned


# --- individually droppable signature debris ----------------------------------


def test_keeps_ps_block_after_tilde_signoff():
    text = "Hi John,\n\nA correction.\n\n~ Christian Veenman (NCSC-NL)\n\nPs. keep me."
    cleaned = scored(text)
    assert "~ Christian" not in cleaned
    assert "Ps. keep me." in cleaned
    assert "A correction." in cleaned


def test_drops_titled_contact_line_not_header_line():
    dropped = scored("Body.\nDr. Markku-Juhani O. Saarinen <mjos@iki.fi>")
    assert dropped == "Body."
    kept = scored("Body.\nFrom: Timo Gerke <timo.gerke@alice-dsl.net>")
    assert "From: Timo Gerke" in kept


@pytest.mark.parametrize(
    "line",
    [
        # The message-27 corporate signature block, line by line.
        "Tel: +1 905.502.7000 x 3288  |  Toll Free: 888.879.5879",
        "thi.nh@winmagic.com<mailto:thi.nh@winmagic.com> |  www.winmagic.com<http://www.winmagic.com/>",
        "WinMagic Corp. | 11-80 Galaxy Blvd.",
        "Toronto, ON  |  M9W 4Y8 |  Canada | www.winmagic.com<http://www.winmagic.com/>",
        # A telephony line needs no pipe at all.
        "Phone: (415) 555-0100",
    ],
)
def test_drops_corporate_contact_line(line):
    assert scored(f"Body.\n{line}") == "Body."


@pytest.mark.parametrize(
    "line",
    [
        # A name-and-title sign-off has a pipe but no contact cue.
        "Thi Nguyen-Huu | CEO",
        # Digest table rows: pipes plus a bare email are NOT contact furniture.
        " 2 (16.7%) |  11728 ( 3.3%) | NomCom Chair 2026 <nomcom-chair-2026@example.org>",
        "   Count    |      Bytes     |  Who",
        # A URL the author pasted on its own line (no pipe) must be kept.
        "https://mailarchive.example.org/arch/msg/wimse/d0Ua5jt7Ekkya9AiwUtTi99o1kY",
        # Prose with a phone keyword but no number tail.
        "the cell 5 in row 2 is wrong",
    ],
)
def test_keeps_non_contact_line(line):
    assert line.strip() in scored(f"Body.\n{line}")


def test_drops_identifier_line_but_not_prose():
    assert scored("Body.\nORCID: 0009-0007-4602-5624") == "Body."
    assert scored("Body.\nEmail: kunal@example.org") == "Body."
    # The bracketed-hyperlink rendering ("LinkedIn <https://…>") is also debris.
    assert scored("Body.\nLinkedIn <https://www.linkedin.com/in/kunalghosh87/>") == "Body."
    kept = scored("Happy to upload any of these as GitHub issues if the authors prefer.")
    assert "GitHub issues" in kept


def test_drops_prefixed_identifier_and_address_lines():
    # Short capitalized prefixes before an identifier keyword are furniture.
    assert scored("Body.\nVSO BLOG: https://example.org/blog/") == "Body."
    assert scored("Body.\nD-U-N-S: 698368529") == "Body."
    # An Office/Address line whose value carries a digit is a postal address.
    assert scored("Body.\nTokyo Office: Ebisu, Shibuya-ku, Tokyo 150-0021, Japan") == "Body."
    # Running prose in front of the keyword never qualifies.
    kept = scored("Body.\nyou can reach me by email: kunal@example.org anytime")
    assert "reach me by email" in kept
    # An Address line with no digit is kept.
    kept = scored("Body.\nAddress: see the wiki for directions")
    assert "see the wiki" in kept


# --- trailing sign-off debris -------------------------------------------------


def test_trailing_signoff_debris_dropped_signoff_kept():
    # The message-44 shape: sign-off, then identifier + bare-domain lines to EOF.
    text = "The review is attached.\n\nRegards,\nKunal Ghosh\nORCID: 0009-0007-4602-5624\nkghoshworkid.github.io"
    cleaned = scored(text)
    # The debris is dropped by the trailing rule; the "Regards, / Kunal Ghosh"
    # framing then goes to the closing-sign-off strip.
    assert cleaned == "The review is attached."
    assert "ORCID" not in cleaned
    assert "github.io" not in cleaned


def test_trailing_link_kept_when_authored_content_follows():
    # A Ps. after the link means the tail is not all debris — nothing is dropped.
    text = "Reply.\n\nCheers,\nAlice\nalice.example.org\n\nPs. one more authored thought."
    cleaned = scored(text)
    assert "alice.example.org" in cleaned
    assert "Ps. one more authored thought." in cleaned


def test_link_line_mid_prose_is_kept():
    # A URL pasted on its own line before the sign-off is authored content.
    text = "See the draft:\nhttps://datatracker.example.org/doc/html/draft-foo-01\n\nOlivier"
    assert "https://datatracker.example.org" in scored(text)


def test_trailing_debris_after_bare_name_needs_identifier_evidence():
    # The message-29 shape: no salutation line, a bare name, then identifier +
    # link debris to EOF — the identifier lines license dropping the whole tail.
    text = (
        "Thanks, and looking forward to participating.\n\n"
        "Kunal Ghosh\nORCID: 0009-0007-4602-5624\nkghoshworkid.github.io\n"
        "LinkedIn <https://www.linkedin.com/in/kunalghosh87/>"
    )
    cleaned = scored(text)
    assert cleaned.endswith("Kunal Ghosh")
    assert "github.io" not in cleaned
    assert "LinkedIn" not in cleaned
    # But a bare name followed ONLY by a link (no identifier/contact evidence)
    # is plausibly an authored citation — nothing is dropped.
    kept = scored("Check out this paper by\nJohn Smith\nexample.com/paper.pdf")
    assert "example.com/paper.pdf" in kept


# --- greeting / closing sign-off strip ----------------------------------------


@pytest.mark.parametrize(
    "greeting",
    ["Hi Songbo, Karthik, all,", "Hi", "Hello,", "Dear Community,", "Good morning all,"],
)
def test_opening_greeting_is_stripped(greeting):
    assert scored(f"{greeting}\n\nThe actual content.") == "The actual content."


def test_greeting_only_stripped_at_top_and_prose_kept():
    # "Hey <prose>" with no terminating comma is content, not a greeting.
    kept = scored("Hey that reminds me of the earlier draft discussion")
    assert kept.startswith("Hey that reminds")
    # A greeting-looking line mid-message is content too.
    text = "First line of content.\nHi Roy,\nMore content."
    assert "Hi Roy," in scored(text)


@pytest.mark.parametrize(
    "signoff",
    ["Best,\nSongbo", "Cheers, Peter", "Regards,\n\nTimo Gerke", "Cheers"],
)
def test_closing_signoff_is_stripped(signoff):
    assert scored(f"The actual content.\n\n{signoff}") == "The actual content."


def test_bare_trailing_name_is_kept():
    # A name with no salutation ("Dino") is indistinguishable from content.
    assert scored("Content line.\n\nDino").endswith("Dino")


# --- confidentiality / legal disclaimers ----------------------------------------


def test_trailing_disclaimer_paragraph_is_dropped():
    text = (
        "Substantive reply content here.\n\n"
        "Regards,\nBob Smith\n\n"
        "This email and any attachments are confidential and intended solely for\n"
        "the addressee. If you are not the intended recipient, please notify the\n"
        "sender and delete this message."
    )
    out = scored(text)
    assert out == "Substantive reply content here."
    assert "confidential" not in out
    # The fixpoint matters: the sign-off only becomes the tail once the
    # disclaimer below it is dropped, and is then removed too.
    assert "Bob Smith" not in out


def test_multiple_trailing_disclaimer_paragraphs_dropped():
    text = (
        "Real content.\n\n"
        "CONFIDENTIALITY NOTICE: This message contains privileged information.\n\n"
        "If you received this email in error, please destroy all copies."
    )
    assert scored(text) == "Real content."


def test_disclaimer_shaped_sentence_mid_message_is_kept():
    # Only *trailing* paragraphs are scanned — an author's own sentence about
    # confidentiality in the body must survive.
    text = (
        "This message is intended only for the design team, so please do not\n"
        "forward it beyond the WG.\n\n"
        "Here is the actual technical question about the draft."
    )
    out = scored(text)
    assert "intended only for the design team" in out
    assert "actual technical question" in out


# --- multilingual greetings / sign-offs -----------------------------------------


@pytest.mark.parametrize(
    "greeting",
    [
        "Hallo Peter,",
        "Guten Tag,",
        "Bonjour à tous,",
        "Hola Maria,",
        "Kia ora koutou,",
        "Beste Jan,",
    ],
)
def test_non_english_greeting_is_stripped(greeting):
    assert scored(f"{greeting}\n\nThe actual content.") == "The actual content."


@pytest.mark.parametrize(
    "signoff",
    [
        "Mit freundlichen Grüßen,\nDennis Jackson",
        "Viele Grüße\nTimo",
        "Cordialement,\nLouis",
        "Saludos,\nAntonio",
        "Ngā mihi\nBrian Carpenter",
        "Met vriendelijke groeten,\nJan",
    ],
)
def test_non_english_closing_signoff_is_stripped(signoff):
    assert scored(f"The actual content.\n\n{signoff}") == "The actual content."


# --- name + title/affiliation sign-offs ------------------------------------------


@pytest.mark.parametrize(
    "signoff",
    ["Cheers\n\nThi Nguyen-Huu | CEO", "Best regards,\nLouis Navarre, UCLouvain"],
)
def test_closing_signoff_with_title_or_affiliation_is_stripped(signoff):
    assert scored(f"The actual content.\n\n{signoff}") == "The actual content."


def test_name_title_line_without_salutation_is_kept():
    # A "Name | Title" line is only sign-off furniture when a salutation
    # precedes it; on its own it is indistinguishable from content.
    assert "Thi Nguyen-Huu | CEO" in scored("Body.\n\nThi Nguyen-Huu | CEO")


# --- mobile / client taglines -------------------------------------------------


@pytest.mark.parametrize(
    "tagline",
    [
        "Sent from my iPhone",
        "Sent from my iPad",
        "Sent from my Samsung Galaxy smartphone",
        "Sent from my mobile device",
        "Get Outlook for iOS",
        "Get Outlook for Android",
        "Sent from Proton Mail mobile",
    ],
)
def test_mobile_tagline_recognized_and_dropped(tagline):
    assert is_mobile_tagline(tagline)
    text = f"Here is the substance of my reply.\n{tagline}"
    assert scored(text) == "Here is the substance of my reply."


def test_mobile_tagline_does_not_match_prose():
    assert not is_mobile_tagline("Sent the draft to the working group earlier today.")
    assert "Sent the draft" in scored("Sent the draft to the working group earlier today.")


# --- ignored_lines bookkeeping ------------------------------------------------


def test_ignored_lines_point_at_correct_nonblank_input_lines():
    # Lines (0-based): 0 greeting, 1 blank, 2-3 content, 4 blank, 5-6 sign-off,
    # 7 identifier debris. The cleaner should report 0, 5, 6, 7 as ignored and
    # never a blank line (1, 4).
    text = (
        "Hi team,\n"
        "\n"
        "Here is my substantive review of the draft proposal today.\n"
        "It covers three separate points worth discussing in detail here.\n"
        "\n"
        "Best,\n"
        "Alice\n"
        "ORCID: 0000-0001-2345-6789"
    )
    result = clean_for_scoring(text)
    assert result.ignored_lines == [0, 5, 6, 7]
    # Every reported index is a non-blank input line.
    input_lines = text.split("\n")
    assert all(input_lines[i].strip() for i in result.ignored_lines)
    # The surviving text is the two content lines only.
    assert result.text == (
        "Here is my substantive review of the draft proposal today.\n"
        "It covers three separate points worth discussing in detail here."
    )


def test_nothing_to_clean_returns_identical_text_and_no_ignored_lines():
    text = "This is a plain paragraph.\nWith a second line and no furniture at all."
    result = clean_for_scoring(text)
    assert isinstance(result, CleanResult)
    assert result.text == text
    assert result.ignored_lines == []


def test_clean_never_raises_on_empty_input():
    assert clean_for_scoring("") == CleanResult(text="", ignored_lines=[])
    assert clean_for_scoring("   \n\n").ignored_lines == []


# --- HTML signature hint (step 4) ---------------------------------------------


def test_html_signature_hint_drops_matching_lines():
    # Signature furniture with no "-- " delimiter and no recognizable contact
    # shape, but the HTML marked it as a signature. The hint drops those lines.
    text = (
        "Here is my substantive review of the draft, which spans a few lines.\n"
        "It raises a couple of concrete points worth discussing on the list.\n"
        "Jane Researcher\n"
        "Principal Engineer, Example Networks\n"
        "jane@example.org"
    )
    hint = "Jane Researcher\nPrincipal Engineer, Example Networks\njane@example.org"
    result = clean_for_scoring(text, hint)
    assert "Jane Researcher" not in result.text
    assert "Principal Engineer, Example Networks" not in result.text
    assert "jane@example.org" not in result.text
    assert "substantive review of the draft" in result.text


def test_html_signature_hint_keeps_lone_salutation_word():
    # The substance guard: a lone "Cheers"/"Thanks" that happens to sit in the
    # signature container must NOT be stripped out of the body by the hint (a
    # single word, no digit/@/URL). (The closing-sign-off strip may still take a
    # true trailing sign-off, but the hint step itself must spare it.)
    text = "Thanks\nfor all the detailed feedback you shared on the draft revision today."
    hint = "Thanks\nJane Researcher"
    result = clean_for_scoring(text, hint)
    # "Thanks" is not a full sign-off here (no following name line), so it stays.
    assert result.text.startswith("Thanks")


def test_html_signature_hint_ignored_lines_include_hint_removals():
    # Hint-removed lines must be reported in ignored_lines like other furniture,
    # and blank lines are never reported.
    text = (
        "Substantive content line one of the review here for the group.\n"  # 0 kept
        "Substantive content line two of the review here for the group.\n"  # 1 kept
        "\n"  # 2 blank
        "Custom Signature Line With Details\n"  # 3 hint-dropped
        "contact: person@example.org"  # 4 hint-dropped (has @)
    )
    hint = "Custom Signature Line With Details\ncontact: person@example.org"
    result = clean_for_scoring(text, hint)
    assert result.ignored_lines == [3, 4]
    input_lines = text.split("\n")
    assert all(input_lines[i].strip() for i in result.ignored_lines)
    assert result.text == (
        "Substantive content line one of the review here for the group.\n"
        "Substantive content line two of the review here for the group."
    )


def test_html_signature_hint_none_is_a_noop():
    text = "A plain paragraph with no furniture.\nAnd a second content line here."
    assert clean_for_scoring(text, None) == clean_for_scoring(text)


# --- signature-heavy fixtures (composite stage-1 + stage-2) -------------------


def test_signature_identifier_links_fixture_extracts_clean():
    """The message-44 defect: an ORCID line and a bare personal-domain line
    trailing the "Regards, / Kunal Ghosh" sign-off must not ship to scoring.
    """
    stem = "signature-identifier-links-wimse-01"
    result = extract_new_text(fixture_body(stem))
    assert result.status == "ok"
    composite = clean_for_scoring(result.text).text
    # Sign-off framing and identifier debris both go; content is the last line.
    assert composite.strip().splitlines()[-1].startswith("Happy to upload")
    assert "ORCID" not in composite
    assert "kghoshworkid.github.io" not in composite
    assert "GitHub issues" in composite  # prose mentioning GitHub is kept
    assert tolerant_lines(composite) == tolerant_lines(expected_text(stem))


def test_signature_corporate_contact_fixture_drops_contact_block():
    """The message-27 defect: a corporate contact block (phone / piped
    address+URL lines) must not ship to scoring. With the fixpoint pass, the
    "Cheers" + "Name | Title" sign-off exposed by dropping the block is removed
    too — the scored text ends at the author's last content line.
    """
    stem = "signature-corporate-contact-wimse-01"
    result = extract_new_text(fixture_body(stem))
    assert result.status == "ok"
    composite = clean_for_scoring(result.text).text
    assert composite.strip().splitlines()[-1].startswith("You asked why the human binds")
    assert "Thi Nguyen-Huu | CEO" not in composite
    assert "Cheers" not in composite
    assert "Toll Free" not in composite
    assert "Galaxy Blvd" not in composite
    assert "M9W 4Y8" not in composite
    assert "www.winmagic.com" not in composite
    assert tolerant_lines(composite) == tolerant_lines(expected_text(stem))


# --- parts ----------------------------------------------------------------------


def test_parts_default_to_the_whole_text():
    assert clean_for_scoring("The plan works.\n\nSecond line.").parts == [
        "The plan works.\n\nSecond line."
    ]
    assert clean_for_scoring("").parts == []
    assert clean_for_scoring("\n\n").parts == []


def test_parts_split_at_part_starts():
    text = "First answer.\n\nSecond answer.\nstill second.\n\nThird answer."
    result = clean_for_scoring(text, part_starts=(2, 5))
    assert result.parts == ["First answer.", "Second answer.\nstill second.", "Third answer."]
    assert result.text == text


def test_parts_ignore_out_of_range_duplicate_and_unordered_starts():
    text = "a\nb\nc"
    expected = ["a", "b\nc"]
    assert clean_for_scoring(text, part_starts=(1,)).parts == expected
    assert clean_for_scoring(text, part_starts=(0, 1, 1, 3, 99, -1)).parts == expected
    assert clean_for_scoring("a\nb\nc", part_starts=(2, 1)).parts == ["a", "b", "c"]


def test_a_part_emptied_by_cleaning_is_omitted():
    # The greeting is stage-2 furniture; its part has nothing left.
    text = "Hi Bob,\n\nThe actual answer is here.\n\nAnother answer is here."
    result = clean_for_scoring(text, part_starts=(2, 4))
    assert result.text == "The actual answer is here.\n\nAnother answer is here."
    assert result.parts == ["The actual answer is here.", "Another answer is here."]


def test_parts_lose_trailing_signoff_with_the_text():
    text = "Answer one.\n\nAnswer two.\n\nThanks!\nDhruv"
    result = clean_for_scoring(text, part_starts=(2,))
    assert result.parts == ["Answer one.", "Answer two."]


# --- tolerant sign-off forms (no evidence) ----------------------------------------


@pytest.mark.parametrize(
    "signoff",
    [
        "Best,\n-jane",  # decorated lower-case name under a salutation
        "Best Regards,\n-xyz",
        "Regards.\n--xyz.",  # "." after the salutation, decorated name
        "Regards,\n\u2014 Jane Example",  # em-dash decorated full name
        "Cheers,\n/xyz",
        "Thanks,\n    xyz",  # indented single word
        "Thanks -Jane",  # one-line form, decorated name, no punctuation
        "Later, Jane",  # an added salutation
        "Regards\uff0cJane",  # full-width comma
        "Regards\u3001Jane",  # ideographic comma
        "Thanks and regards,\nJane",  # two salutations joined
        "Regards/Ng\u0101 mihi\nJane Example",
        "With best regards,\nJane",
        "Many thanks!!",  # a run of "!"
        "Cheers...",
        "Take care",
    ],
)
def test_tolerant_closing_signoff_is_stripped(signoff):
    result = clean_for_scoring(f"The actual content.\n\n{signoff}")
    assert result.text == "The actual content."
    assert result.ignored_lines == list(range(2, 2 + signoff.count("\n") + 1))


@pytest.mark.parametrize(
    "ending",
    [
        "Regards, John Example. I approve the change.",  # not a name line
        "Thanks,\nthe draft is fine as it stands now and can move on.",
        "Regards,\nJane\n\nPS: see the agenda.",  # no sender name: nothing exposes it
    ],
)
def test_tolerant_closing_signoff_keeps_content(ending):
    text = f"The actual content.\n\n{ending}"
    assert clean_for_scoring(text).text == text


def test_one_line_form_after_a_comma_needs_a_name_line():
    # "John²" is not a name line (a digit) and is capitalized, so the one-line
    # form after a comma does not take it; under a salutation line it is a
    # loose name word.
    assert clean_for_scoring("Text.\n\nRegards, John\u00b2").ignored_lines == []
    assert clean_for_scoring("Text.\n\nRegards,\nJohn\u00b2").ignored_lines == [2, 3]


# --- sign-offs with the sender's name -----------------------------------------------

AUTHOR = "Jane Example"


@pytest.mark.parametrize(
    "name_line",
    [
        "Jane",
        "jane",
        "Jane Example",
        "-jane",
        "\u2014 Jane",
        "/je",
        "JE",
        "/j",
        "-J.",
        "Jane (as chair)",
    ],
)
def test_trailing_sender_name_line_is_stripped(name_line):
    result = clean_for_scoring(f"Text.\n\n{name_line}", author_name=AUTHOR)
    assert result.text == "Text."
    assert result.ignored_lines == [2]


@pytest.mark.parametrize("name_line", ["Jane", "-jane", "/j"])
def test_trailing_sender_name_needs_the_author_name(name_line):
    text = f"Text.\n\n{name_line}"
    assert clean_for_scoring(text).text == text
    assert clean_for_scoring(text, author_name="Bob Other").text == text


def test_trailing_sender_name_takes_a_rule_line_above():
    result = clean_for_scoring("Text.\n\n---\nJane Example", author_name=AUTHOR)
    assert result.text == "Text."
    assert result.ignored_lines == [2, 3]


def test_initial_line_must_be_the_first_initial():
    text = "Text.\n\n/x"
    assert clean_for_scoring(text, author_name=AUTHOR).text == text
    # An undecorated single letter is not a sign-off.
    assert clean_for_scoring("Text.\n\nJ", author_name=AUTHOR).text == "Text.\n\nJ"


def test_sender_signoff_with_attached_signature_lines():
    text = "Text.\n\nCheers,\nJane Example\nExample University\nhttps://example.org/jane"
    result = clean_for_scoring(text, author_name=AUTHOR)
    assert result.text == "Text."
    assert result.ignored_lines == [2, 3, 4, 5]


def test_footnotes_below_a_sender_signoff_stay():
    text = (
        "Text with a reference [1].\n\n"
        "Regards,\nJane\n\n"
        "[1] https://example.org/a\n"
        "https://example.org/b"
    )
    result = clean_for_scoring(text, author_name=AUTHOR)
    # The sign-off goes; the footnote and its reference link stay.
    assert (
        result.text
        == "Text with a reference [1].\n\n[1] https://example.org/a\nhttps://example.org/b"
    )
    assert result.ignored_lines == [2, 3]


def test_tail_debris_below_a_sender_signoff_goes():
    text = "Text.\n\nRegards,\nJane\nhttps://example.org/b\nSent from my iPhone"
    result = clean_for_scoring(text, author_name=AUTHOR)
    assert result.text == "Text."
    assert result.ignored_lines == [2, 3, 4, 5]


def test_postscript_below_a_sender_signoff_stays():
    text = "Text.\n\nRegards,\nJane\n\nP.S. The agenda is attached.\nMore words for the note."
    result = clean_for_scoring(text, author_name=AUTHOR)
    assert result.text == "Text.\n\nP.S. The agenda is attached.\nMore words for the note."
    assert result.ignored_lines == [2, 3]


def test_sender_name_above_prose_is_not_a_tail_signoff():
    text = "Text.\n\nJane\nExample University\nThis is a longer sentence that continues the text."
    assert clean_for_scoring(text, author_name=AUTHOR).text == text


@pytest.mark.parametrize(
    "signoff, removed",
    [("Thanks,\nJane", [2, 3]), ("Cheers, Jane", [2]), ("Best -Jane", [2])],
)
def test_sender_signoff_away_from_the_tail_is_stripped(signoff, removed):
    text = f"Answer.\n\n{signoff}\n\nThe forwarded discussion continues here with more words."
    result = clean_for_scoring(text, author_name=AUTHOR)
    assert result.text == "Answer.\n\nThe forwarded discussion continues here with more words."
    assert result.ignored_lines == removed


def test_lone_sender_name_line_mid_text_is_stripped():
    text = "The answer is yes.\n\nJane\n\nThe pasted material continues here with more words."
    result = clean_for_scoring(text, author_name=AUTHOR)
    assert (
        result.text == "The answer is yes.\n\nThe pasted material continues here with more words."
    )
    assert result.ignored_lines == [2]
    assert clean_for_scoring(text).text == text


@pytest.mark.parametrize(
    "text",
    [
        # a speaker label
        "The answer is yes.\n\nJane:\nThe pasted material continues here with more words.",
        # a reviewer line
        "Notes.\nReviewer: Jane Example\nThe review text follows here.",
        # a name under a label line
        "The notes.\nPresenter:\nJane Example\nThe slides follow here.",
        # an attendee list
        "Attendees:\nJohn Doe\nJane Example\nBob Smith\n\nNotes follow.",
        # a list of names with affiliations
        "We met today.\nJane Example (Example Corp)\nJohn Doe (Other Corp)\nwere present.",
        # a list item
        "The answer is yes.\n\n* Jane Example\n\nThe pasted material continues here.",
    ],
)
def test_sender_name_in_content_is_kept(text):
    assert clean_for_scoring(text, author_name=AUTHOR).text == text


def test_greeting_to_a_namesake_is_not_a_signature():
    # The sender is also Michael: "Michael," mid-text ends in a comma, so the
    # lone-name rule leaves it; as the first line it is a greeting.
    sender = "Michael Example"
    text = "Some context first.\n\nMichael,\n\nI agree with your proposal."
    assert clean_for_scoring(text, author_name=sender).text == text
    result = clean_for_scoring(
        "Michael,\n\nI agree with your proposal.",
        author_name=sender,
        addressee_names=["Michael Other"],
    )
    assert result.text == "I agree with your proposal."
    assert result.ignored_lines == [0]


def test_sender_name_matching_forms():
    # Nicknames, initials with a middle initial, prefixes, folded accents, a
    # "Last, First" display name, an address-only display name.
    cases = [
        ("Robert Example", "Bob"),
        ("Robert Example", "RjE"),
        ("Robert Example", "Rob E."),
        ("S\u00f8ren \u00c5ngstr\u00f6m", "Soren"),
        ("Example, Jane", "JE"),
        ("jane.example@example.org", "Jane"),
        ("mail@janeexample.org", "Jane Example"),
        ("Jane Example via Example-List", "Jane"),
        ('"Jane (JJ) Example"', "JJ"),
    ]
    for author, name_line in cases:
        result = clean_for_scoring(f"Text.\n\n{name_line}", author_name=author)
        assert result.text == "Text.", (author, name_line)


@pytest.mark.parametrize(
    "author, line",
    [
        ("", "Secretariat"),
        ("IETF Secretariat", "Secretariat"),
        ("\u0930\u093e\u092e", "\u0930\u093e\u092e"),
    ],
)
def test_author_name_without_tokens_gives_no_evidence(author, line):
    # Only stop words, or no letter run of 2+ letters (the Devanagari vowel
    # sign is not \w): the sender-name rules stay off.
    text = f"Text.\n\n{line}"
    assert clean_for_scoring(text, author_name=author).text == text


# --- thanks to an addressee -----------------------------------------------------------

ADDRESSEES = ["Peter Other"]


@pytest.mark.parametrize(
    "line",
    ["Thanks, Pete, these are good suggestions.", "Thanks, Pete.", "Thank you, Peter"],
)
def test_thanks_to_an_addressee_is_content(line):
    text = f"Text.\n\n{line}"
    assert clean_for_scoring(text, author_name=AUTHOR, addressee_names=ADDRESSEES).text == text
    # Without the sender's name the exception is off.
    assert clean_for_scoring(text, addressee_names=ADDRESSEES).text == "Text."


def test_thanks_followed_by_a_sentence_is_content():
    text = "Text.\n\nThanks, Joe. That works."
    assert clean_for_scoring(text, author_name=AUTHOR).text == text


@pytest.mark.parametrize(
    "line",
    [
        "Cheers, Jane",  # the sender's own name
        "Regards, Pete",  # not a thanks salutation
        "Thanks,Pete",  # no blank before the name: a collapsed two-line sign-off
    ],
)
def test_signoff_to_or_from_the_sender_is_stripped(line):
    result = clean_for_scoring(f"Text.\n\n{line}", author_name=AUTHOR, addressee_names=ADDRESSEES)
    assert result.text == "Text."


def test_exposed_one_line_form_needs_a_plain_name():
    # The sender-name rule removes "Jane" and exposes the line above it; that
    # line goes only when its name is a plain name.
    kept = clean_for_scoring(
        "Text.\n\nThanks, Sandy. I approve the change.\nJane", author_name=AUTHOR
    )
    assert kept.text == "Text.\n\nThanks, Sandy. I approve the change."
    gone = clean_for_scoring("Text.\n\nThanks, Sandy\nJane", author_name=AUTHOR)
    assert gone.text == "Text."


# --- greetings ---------------------------------------------------------------------

BODY = "\n\nThe body text follows."
NAMES = ["Jane Doe", "Joseph Other"]


@pytest.mark.parametrize(
    "line, addressees",
    [
        ("Jane,", NAMES),  # an addressee name
        ("Jane", NAMES),  # an addressee name, no terminator
        ("Jane Doe:", NAMES),
        ("Jane, Joe:", NAMES),  # a nickname of an addressee
        ("Jane, Joe and all,", NAMES),
        ("Dr. Doe,", NAMES),  # a title
        ("Jan,", NAMES),  # a name prefix after a comma
        ("Thanks Jane,", NAMES),  # thanks + name + comma
        ("S\u00f8ren,", ["Soren Example"]),  # folded
        ("Soren,", ["S\u00f8ren Example"]),
        ("Hiya Jane,", None),  # added greeting words
        ("Moin,", None),
        ("Howdy", None),
        ("Good morning everyone,", None),
        ("All:", None),  # group addressees
        ("WG chairs,", None),
        ("Stefano,", None),  # structural: one capitalized word and a comma
        ("Jane, Joe:", None),  # structural: two items before ":"
    ],
)
def test_opening_greeting_rules(line, addressees):
    result = clean_for_scoring(line + BODY, addressee_names=addressees)
    assert result.text == "The body text follows."
    assert result.ignored_lines == [0]


@pytest.mark.parametrize(
    "line, addressees",
    [
        ("Jan.", NAMES),  # a name prefix needs "," or ":" without a greeting word
        ("Thanks Jane!", NAMES),  # thanks, not a greeting
        ("++ Jane and Joe.", NAMES),  # adds people to a thread
        ("Jane", None),  # a bare word needs name evidence
        ("Actually,", None),  # stoplist
        ("Finally,", None),
        ("Basically,", None),  # an adverb in -ly
        ("Inline:", None),  # a single label before ":"
        ("People:", None),
        ("V/R,", None),  # a sign-off abbreviation
        ("Hi Jane, apologies for the late reply.", None),  # runs into a sentence
        ("Hi, I'm writing about the draft.", None),
    ],
)
def test_opening_line_that_is_not_a_greeting_is_kept(line, addressees):
    text = line + BODY
    assert clean_for_scoring(text, addressee_names=addressees).text == text


def test_structural_greeting_needs_a_capitalized_next_line():
    text = "Stefano,\nthe body text follows."
    assert clean_for_scoring(text).text == text


def test_two_stacked_greetings_are_stripped_and_no_more():
    result = clean_for_scoring("Jane,\n\nHi!" + BODY, addressee_names=NAMES)
    assert result.text == "The body text follows."
    assert result.ignored_lines == [0, 2]
    result = clean_for_scoring("Hi,\nHello,\nHey," + BODY)
    assert result.text == "Hey,\n\nThe body text follows."
    assert result.ignored_lines == [0, 1]


def test_addressee_with_the_senders_own_name_is_not_evidence():
    # A reply-all lists the sender among the addressees; that name does not
    # make "Michael." a greeting.
    text = "Michael." + BODY
    kwargs = {"author_name": "Michael Example", "addressee_names": ["Michael Example", "Bob Smith"]}
    assert clean_for_scoring(text, **kwargs).text == text
    other = {"author_name": "Bob Smith", "addressee_names": ["Michael Example"]}
    assert clean_for_scoring(text, **other).text == "The body text follows."


# --- learned signature lines ---------------------------------------------------------

LEARNED = ["Jane Example", "Example University"]


def test_learned_lines_are_stripped_in_the_trailing_zone():
    text = "Text that is long enough.\n\nJane Example\nExample University"
    result = clean_for_scoring(text, signature_lines=LEARNED)
    assert result.text == "Text that is long enough."
    assert result.ignored_lines == [2, 3]
    assert clean_for_scoring(text).text == text


def test_learned_lines_are_compared_normalized():
    # Whitespace runs, case and quote markers do not matter.
    text = "Body text here.\n\n> EXAMPLE   University\nok then"
    result = clean_for_scoring(text, signature_lines=["example university"])
    assert result.text == "Body text here.\n\nok then"
    assert result.ignored_lines == [2]


@pytest.mark.parametrize(
    "text",
    [
        # a learned line above a line of more than four words: outside the zone
        "Body text here.\n\nExample University\nThis sentence has many more words than four.",
        # learned lines at the top of the text, prose below
        "Jane Example\nExample University\n\nThe body text follows here with more words.",
        # the whole text: never emptied
        "Jane Example\nExample University",
    ],
)
def test_learned_lines_outside_the_trailing_zone_are_kept(text):
    assert clean_for_scoring(text, signature_lines=LEARNED).text == text


def test_learned_line_four_word_boundary():
    four = "Body text here.\n\nExample University\none two three four"
    assert clean_for_scoring(four, signature_lines=LEARNED).text == (
        "Body text here.\n\none two three four"
    )
    five = "Body text here.\n\nExample University\none two three four five"
    assert clean_for_scoring(five, signature_lines=LEARNED).text == five


def test_learned_line_drop_exposes_a_signoff():
    # The ordinary passes resume after the learned-line drop.
    text = "Body.\n\nRegards,\nJane Example\nExample University"
    result = clean_for_scoring(text, signature_lines=["Example University"])
    assert result.text == "Body."
    assert result.ignored_lines == [2, 3, 4]


def _sender_texts(count, with_line, line="Jane Example", subjects=None):
    texts = [f"Message {i} body text.\n\n{line}" for i in range(with_line)]
    texts += [f"Message {i} body text." for i in range(with_line, count)]
    return texts


def test_learn_signature_lines_basic_and_sorted():
    texts = [f"Body {i}.\n\nCheers,\n  Jane   Example\n" for i in range(5)]
    assert learn_signature_lines(texts) == ("cheers,", "jane example")


@pytest.mark.parametrize(
    "count, with_line, learned",
    [
        (5, 3, True),  # 3 of 5
        (5, 2, False),  # fewer than 3 texts
        (4, 4, False),  # fewer than 5 texts in all
        (15, 3, True),  # exactly one fifth
        (16, 3, False),  # below one fifth
        (20, 4, True),
        (21, 4, False),
    ],
)
def test_learn_signature_lines_count_thresholds(count, with_line, learned):
    got = learn_signature_lines(_sender_texts(count, with_line))
    assert ("jane example" in got) is learned


def test_learn_signature_lines_subject_threshold():
    texts = _sender_texts(5, 3)
    three = ["Draft A", "Draft B", "Draft C", "x", "y"]
    assert learn_signature_lines(texts, subjects=three) == ("jane example",)
    # "Re:", "Fwd:" and "[list]" prefixes, whitespace and case are ignored.
    two = ["Draft A", "Re: [list] AW: draft   a", "Draft C", "x", "y"]
    assert learn_signature_lines(texts, subjects=two) == ()


def test_learn_signature_lines_shorter_subjects():
    # A text with no corresponding subject has the empty subject.
    texts = _sender_texts(5, 3)
    assert learn_signature_lines(texts, subjects=["Draft A", "Draft B"]) == ("jane example",)
    assert learn_signature_lines(texts, subjects=["Draft A", "Draft B", ""]) == ("jane example",)
    assert learn_signature_lines(texts, subjects=["Draft A", "Draft B", "", ""]) == (
        "jane example",
    )
    assert learn_signature_lines(texts, subjects=["Draft A"]) == ()
    assert learn_signature_lines(texts, subjects=["Draft A", "", None]) == ()
    assert learn_signature_lines(texts, subjects=[]) == ()


def test_learn_signature_lines_longer_subjects():
    # Subjects beyond the last text are ignored.
    texts = _sender_texts(5, 3)
    five = ["Draft A", "Draft A", "Draft C", "x", "y"]
    assert learn_signature_lines(texts, subjects=five) == ()
    assert learn_signature_lines(texts, subjects=[*five, "Draft D", "Draft E"]) == ()
    three = ["Draft A", "Draft B", "Draft C", "x", "y"]
    assert learn_signature_lines(texts, subjects=[*three, "z"]) == ("jane example",)


def test_learn_signature_lines_reads_the_last_ten_lines():
    def text(lines_below):
        return "Body.\nJane Example\n" + "\n".join(f"Line {k}" for k in range(lines_below))

    # The line is the 10th non-blank line from the end: counted.
    assert "jane example" in learn_signature_lines([text(9) + f" {i}" for i in range(5)])
    # The 11th: not counted.
    assert "jane example" not in learn_signature_lines([text(10) + f" {i}" for i in range(5)])


def test_learn_signature_lines_rejects_a_lone_sentence():
    sentence = "Let me know if you have questions."
    lone = [f"Body {i} text here.\n\nUnique line {i}\n{sentence}" for i in range(5)]
    assert learn_signature_lines(lone) == ()
    # Under another learned line in at least half of its occurrences, it is
    # part of the furniture.
    under = [f"Body {i} text here.\n\nJane Example\n{sentence}" for i in range(3)]
    under += [f"Body {i} text here.\n\nUnique line {i}\n{sentence}" for i in range(3, 5)]
    assert learn_signature_lines(under) == ("jane example", sentence.lower())
    # A label ending in ":" is treated the same way.
    label = [f"Body {i} text here.\n\nUnique line {i}\nContact:" for i in range(5)]
    assert learn_signature_lines(label) == ()


def test_learned_lone_sentence_is_not_stripped_without_learning():
    sentence = "Let me know if you have questions."
    text = f"Body text here.\n\n{sentence}"
    learned = learn_signature_lines([f"Body {i}.\n\nUnique {i}\n{sentence}" for i in range(5)])
    assert clean_for_scoring(text, signature_lines=learned).text == text


# --- parts and ignored_lines with evidence --------------------------------------------


def test_parts_and_ignored_lines_follow_the_evidence_rules():
    text = (
        "Jane,\n"  # 0 greeting (addressee)
        "\n"
        "First answer here.\n"  # 2
        "\n"
        "Second answer here.\n"  # 4 part 2
        "\n"
        "Cheers,\n"  # 6
        "Bob\n"  # 7 sender name
        "Example University"  # 8 learned
    )
    result = clean_for_scoring(
        text,
        part_starts=(4,),
        author_name="Bob Sender",
        addressee_names=["Jane Doe"],
        signature_lines=["Example University"],
    )
    assert result.text == "First answer here.\n\nSecond answer here."
    assert result.ignored_lines == [0, 6, 7, 8]
    assert result.parts == ["First answer here.", "Second answer here."]
    kept = [i for i, line in enumerate(text.split("\n")) if line.strip()]
    assert sorted(set(kept) - set(result.ignored_lines)) == [2, 4]


def test_no_evidence_equals_explicit_none():
    text = "Jane,\n\nText.\n\nCheers,\nBob\nExample University"
    assert clean_for_scoring(text) == clean_for_scoring(
        text, author_name=None, addressee_names=None, signature_lines=None
    )
    assert clean_for_scoring(text) == clean_for_scoring(
        text, author_name="", addressee_names=[], signature_lines=[]
    )
