"""Quality and unit tests for the new-text extraction pipeline (stage 1).

The corpus test runs the **composite** of both pipeline stages —
``clean_for_scoring(extract_new_text(body).text).text`` — over all hand-labeled
fixtures and compares against ``tests/fixtures/expected/*.txt`` with the
tolerant whitespace comparison documented in ``tests/fixtures/README.md``. The
expected files are exactly the final scored text, i.e. the stage-1 extraction
*after* stage-2 cleaning; they did not change when extraction was split from
cleaning. Two of the trickiest interleaved fixtures are additionally pinned to
exact composite output, and one signed fixture is pinned at the stage boundary
(kept in stage 1, removed in stage 2).

Stage-2 cleaning (signature/greeting/sign-off removal) is exercised in
``tests/test_cleaning.py``; this module covers stage-1 concerns only: quote and
attribution stripping, the sign-off *boundary*, forwarded/quote-header blocks,
generic attribution lines, wrap remainders, dividers, and the parent-diff
assist with its marking mode.
"""

from __future__ import annotations

import pytest
from corpus import ALL_STEMS, expected_text, fixture_body, fixture_html

from email_reply_extractor.equivalence import tolerant_lines
from email_reply_extractor.cleaning import clean_for_scoring
from email_reply_extractor.extraction import (
    ExtractionResult,
    custom_clean,
    extract_new_text,
    find_quote_header_block,
    find_signoff_boundary,
    is_attribution_line,
    is_quote_line,
    is_signoff_name_line,
    normalize_body,
    strip_after_original_message_divider,
    strip_attribution_lines,
    strip_parent_content,
)
from email_reply_extractor.html_text import split_html_parts

INTERLEAVED_STEMS = [s for s in ALL_STEMS if s.startswith("interleaved-")]

# --- tolerant comparison (README §"How the expected files were derived") ------


def composite_text(stem: str) -> str:
    """The exact text sent to Pangram: stage 1 (with the HTML oracle) then stage 2."""
    result = extract_new_text(fixture_body(stem), html_body=fixture_html(stem))
    return clean_for_scoring(result.text).text


def matches_fixture(stem: str) -> tuple[bool, ExtractionResult]:
    # The corpus now feeds each fixture's HTML part in as the extraction oracle,
    # exactly as the pull pipeline does (parse_message exposes html_body).
    result = extract_new_text(fixture_body(stem), html_body=fixture_html(stem))
    composite = clean_for_scoring(result.text).text
    ok = tolerant_lines(composite) == tolerant_lines(expected_text(stem))
    return ok, result


# --- corpus accuracy ----------------------------------------------------------


def test_corpus_meets_accuracy_target():
    """At most 2 tolerant mismatches, and every interleaved fixture must match."""
    results = {stem: matches_fixture(stem) for stem in ALL_STEMS}
    passed = [stem for stem, (ok, _) in results.items() if ok]
    failed = [stem for stem, (ok, _) in results.items() if not ok]

    target = len(ALL_STEMS) - 2
    assert len(passed) >= target, f"only {len(passed)}/{len(ALL_STEMS)} matched; failures: {failed}"

    interleaved_failures = [s for s in INTERLEAVED_STEMS if not results[s][0]]
    assert not interleaved_failures, f"interleaved fixtures must all pass: {interleaved_failures}"


@pytest.mark.parametrize("stem", ALL_STEMS)
def test_fixture_matches_expected(stem):
    """Per-fixture visibility: currently every fixture matches (loud on regression)."""
    ok, result = matches_fixture(stem)
    assert ok, (
        f"{stem} [{result.method}] mismatch\n"
        f"--- got ---\n{composite_text(stem)}\n--- expected ---\n{expected_text(stem)}"
    )


def test_every_result_ok_status():
    """No fixture should extract to empty or failed.

    The plain body alone must yield text too, except for an Apple Mail
    Share-sheet message, whose plain part marks the author's whole text as
    quoted; only its HTML part recovers it.
    """
    for stem in ALL_STEMS:
        html = fixture_html(stem)
        result = extract_new_text(fixture_body(stem), html_body=html)
        assert result.status == "ok", f"{stem}: {result.status}"
        plain = extract_new_text(fixture_body(stem))
        if html is not None and split_html_parts(html).has_share_container:
            assert plain.status == "empty", f"{stem}: {plain.status}"
        else:
            assert plain.status == "ok", f"{stem}: {plain.status}"


# --- pinned exact composite output for the two trickiest interleaved fixtures --


def test_pinned_pointbypoint():
    """Gold interleaved case: 6-way point-by-point reply (README's #1 guard).

    Pins the composite (stage 1 + stage 2) — the exact text scored.
    """
    result = extract_new_text(fixture_body("interleaved-pointbypoint-lastcall-01"))
    assert result.method == "custom"
    assert clean_for_scoring(result.text).text == (
        "Some quick replies.\n"
        "\n"
        "Thanks.\n"
        "\n"
        "These would be different domains that don't connect to each other. You can "
        "run separate instances, especially when there are multicast group boundaries "
        "so the well-known groups stay disjoint.\n"
        "\n"
        "If there are no group boundaries, then the packets go to all members of the "
        "well-known group but are dropped by the ones without common encryption keys.\n"
        "\n"
        "Right, a tradeoff between plug-and-play and secure communication.\n"
        "\n"
        "I thought we had something like that. I'll leave this for Mike and Stig to "
        "comment about.\n"
        "\n"
        "Dino"
    )


def test_pinned_indented_quotes():
    """Indented-``>`` quotes: pins the custom indented-quote + attribution pass."""
    result = extract_new_text(fixture_body("interleaved-indented-quotes-lastcall-01"))
    assert result.method == "custom"
    assert clean_for_scoring(result.text).text == (
        "Correct.\n"
        "We believe that we understood your point of view.\n"
        "\n"
        "We discussed the view that all profiles create non-interoperability, and\n"
        "that none of the certificate belongs, and we respectfully disagree.\n"
        "\n"
        "We think you are in the rough on this.\n"
        "But, we very much appreciated the engagement."
    )


def test_stage_split_pins_signature_kept_then_cleaned():
    """Lock the stage boundary on a signed fixture: the deep-thread reply's
    "Dr. … <mjos@iki.fi>" contact line is *kept* by stage 1 (novel content) and
    *removed* by stage 2 (furniture stripped for scoring).
    """
    stage1 = extract_new_text(fixture_body("interleaved-deep-thread-tls-01")).text
    scored = clean_for_scoring(stage1).text
    assert "Dr. Markku-Juhani O. Saarinen <mjos@iki.fi>" in stage1
    assert "mjos@iki.fi" not in scored
    # The author's substantive prose survives both stages.
    assert "consumer products" in scored


# --- unit tests: pre-normalization --------------------------------------------


def test_normalize_strips_bom_and_zero_width():
    assert normalize_body("﻿> quote") == "> quote"
    assert normalize_body("a​b‌‍⁠c") == "abc"


def test_normalize_converts_line_endings():
    assert normalize_body("a\r\nb\rc") == "a\nb\nc"


# --- unit tests: quote detection ----------------------------------------------


@pytest.mark.parametrize(
    "line",
    ["> quoted", ">> nested", "    > indented", "\t>deep", "...> flowed elision", ".. > x"],
)
def test_is_quote_line_true(line):
    assert is_quote_line(line)


@pytest.mark.parametrize("line", ["not a quote", "a > b comparison", "-markku", "  Brian"])
def test_is_quote_line_false(line):
    assert not is_quote_line(line)


# --- unit tests: attribution variants -----------------------------------------


@pytest.mark.parametrize(
    "line",
    [
        "On Tue, Jul 7, 2026, at 03:25, Dan Wing wrote:",
        "Martin Thomson via Datatracker <noreply@example.org> wrote:",
        "Am Montag, dem 20.07.2026 um 22:55 +0200 schrieb Dennis Jackson:",
        # Japanese (Spark): "<date>、<who>のメール:" — "mail from <who>".
        "2026年7月22日 18:12 +0900、Alex Konviser <ietf@gravit.space>のメール:",
        "2026/07/22 18:12、Alex Konviser <ietf@gravit.space>のメール:",
    ],
)
def test_is_attribution_line_true(line):
    assert is_attribution_line(line)


@pytest.mark.parametrize(
    "line",
    [
        "Just wrote some code.",
        "On the other hand, no.",
        "Hi Roy,",
        # A のメール: ending without the leading date never matches.
        "アレックスのメール:",
    ],
)
def test_is_attribution_line_false(line):
    assert not is_attribution_line(line)


def test_strip_attribution_handles_wrapped_form():
    lines = [
        "keep this",
        "On Tue, Jul 7, 2026, at 03:25,",
        "Dan Wing wrote:",
        "keep that",
    ]
    assert strip_attribution_lines(lines) == ["keep this", "keep that"]


def test_strip_attribution_does_not_span_blank_line():
    lines = ["On something happened", "", "wrote: this is real prose"]
    # A blank line breaks the wrapped-attribution window, so nothing is dropped.
    assert strip_attribution_lines(lines) == lines


# --- unit tests: stage-1 keeps signatures/greetings/sign-offs -----------------


def test_custom_clean_keeps_signature_and_signoff():
    # Stage 1 removes quotes/attributions but KEEPS the author's furniture; the
    # signature and sign-off are stripped later by cleaning, not here.
    text = "> old quote\n\nReal content.\n-- \nSignature line\nmore signature"
    cleaned = custom_clean(text)
    assert "old quote" not in cleaned  # quote removed
    assert "Real content." in cleaned
    # The signature delimiter line survives stage 1 (rstripped to "--").
    assert "--" in cleaned.split("\n")
    assert "Signature line" in cleaned


def test_custom_clean_keeps_greeting_and_closing():
    text = "Hi all,\n\nThe actual content.\n\nBest,\nSongbo"
    cleaned = custom_clean(text)
    assert "Hi all," in cleaned
    assert "The actual content." in cleaned
    assert "Songbo" in cleaned


# --- unit tests: dashed rules ---------------------------------------------------


def test_dashed_digest_content_is_preserved():
    # A dashed-separator "digest": the "---" rule is not a boundary, so the
    # whole table is kept.
    body = "Header row of a report\n----------+----------\n" + "\n".join(
        f"data row {i}" for i in range(10)
    )
    result = extract_new_text(body)
    assert result.method == "custom"
    assert "data row 9" in result.text
    assert "Header row of a report" in result.text


# --- unit tests: sign-off boundary --------------------------------------------


def test_find_signoff_boundary_before_wrapped_attribution():
    # The message-39 shape: salutation + name, then a wrapped attribution
    # introducing a quoted thread with no ">" markers.
    lines = (
        "The proposed outcome labels are vector vocabulary only.\n\n"
        "Best,\nSongbo\n\n"
        "On Tue, 14 Jul 2026 18:45:07 +0000, Thi Nguyen-Huu thi.nh@winmagic.com\n"
        "wrote:\n\n"
        "Karthik, Songbo, all,\n\nQuoted content with no markers.\n"
    ).split("\n")
    idx = find_signoff_boundary(lines)
    assert idx is not None
    assert lines[idx - 1] == "Songbo"
    # Stage 1 truncates the quoted thread at the boundary but keeps the sign-off;
    # stage 2 then removes the "Best, / Songbo" framing.
    stage1 = custom_clean("\n".join(lines))
    assert stage1.endswith("Songbo")
    assert clean_for_scoring(stage1).text.endswith("vector vocabulary only.")


def test_find_signoff_boundary_inline_form():
    lines = ["Done, see branch.", "", "Cheers, Peter", "", "On Mon, Bob Smith wrote:", "old text"]
    assert find_signoff_boundary(lines) == 3


def test_find_signoff_boundary_single_line_attribution():
    lines = ["Reply text.", "", "Regards", "Alice", "", "Bob <bob@example.org> wrote:", "old"]
    assert find_signoff_boundary(lines) == 4


def test_no_boundary_without_name_line():
    # A bare mid-thread "Thanks." (the pointbypoint fixture shape) must never
    # truncate — the name line is required.
    lines = ["Some quick replies.", "Thanks.", "> quoted point one", "My answer."]
    assert find_signoff_boundary(lines) is None


def test_no_boundary_without_following_attribution():
    # Sign-off at the true end, or followed by authored content: no truncation.
    assert find_signoff_boundary(["Reply.", "", "Best,", "Songbo"]) is None
    lines = ["Reply.", "", "Best,", "Songbo", "", "Ps. one more authored thought."]
    assert find_signoff_boundary(lines) is None


@pytest.mark.parametrize("text", ["Songbo", "Thi Nguyen-Huu", "Markku-Juhani O. Saarinen", "Brian"])
def test_is_signoff_name_line_true(text):
    assert is_signoff_name_line(text)


@pytest.mark.parametrize(
    "text",
    [
        "",
        "see the attached test-vector plan",
        "Row 4 needs a re-assessment lease",
        "the next governed key use must fail because status cannot be established",
        "Chair, WIMSE WG <chair@example.org>",
    ],
)
def test_is_signoff_name_line_false(text):
    assert not is_signoff_name_line(text)


def test_toppost_signoff_unquoted_fixture_extracts_author_only():
    """The message-39 defect: a Gmail-style fully top-posted reply whose quoted
    thread has no ``>`` markers must stop at the "Best, / Songbo" sign-off
    instead of shipping ~1800 lines of quoted thread.
    """
    stem = "toppost-signoff-unquoted-wimse-01"
    body = fixture_body(stem)
    result = extract_new_text(body)
    assert result.method == "custom"
    composite = clean_for_scoring(result.text).text
    # The "Best, / Songbo" framing is stripped by stage 2 along with the thread.
    assert composite.strip().splitlines()[-1].startswith("vector vocabulary only")
    assert "possession-side rows" in composite  # author's text kept
    assert "Karthik, Songbo, all," not in composite  # quoted thread dropped
    assert tolerant_lines(composite) == tolerant_lines(expected_text(stem))


# --- unit tests: forwarded / quote-header block detection ---------------------


def test_original_message_divider_truncates_from_own_line():
    lines = ["My reply.", "", "-------- Original message --------", "From: Someone", "Old text."]
    assert strip_after_original_message_divider(lines) == ["My reply.", ""]
    # Outlook's tighter form, no inner spaces.
    lines = ["My reply.", "-----Original Message-----", "From: Someone"]
    assert strip_after_original_message_divider(lines) == ["My reply."]
    # Gmail-style forwarded divider.
    lines = ["Note.", "---------- Forwarded message ---------", "From: X"]
    assert strip_after_original_message_divider(lines) == ["Note."]


def test_original_message_divider_glued_midline_keeps_prefix():
    # HTML-to-text flattening can glue the whole quoted mail onto one line
    # (the message-79 defect): the author's prefix before the divider survives.
    lines = ["All,Three quick questions:-------- Original message --------From: Steven Mih"]
    assert strip_after_original_message_divider(lines) == ["All,Three quick questions:"]


def test_original_message_divider_prose_is_safe():
    # Mentioning an "original message" without flanking dashes never truncates.
    lines = ["The original message said something else.", "-- Original message"]
    assert strip_after_original_message_divider(lines) == lines


def test_toppost_originalmsg_divider_fixture_uses_html_fallback():
    """The message-79 defect, now resolved via the HTML oracle: the plain part is
    two giant glued lines (the client dropped the newlines), so the degenerate
    plain fallback derives the properly line-broken novel text from the HTML part
    instead, and the stage-1 pipeline still truncates at the dashed "Original
    message" divider.
    """
    stem = "toppost-originalmsg-divider-agent2agent-01"
    result = extract_new_text(fixture_body(stem), html_body=fixture_html(stem))
    assert result.status == "ok"
    assert result.method.startswith("html-")
    # Now properly line-broken (not one glued line), and the quoted thread gone.
    lines = [ln for ln in result.text.split("\n") if ln.strip()]
    assert len(lines) > 1
    assert lines[0] == "All,"
    assert "Original message" not in result.text
    assert "From: Steven Mih" not in result.text
    # Word-stream equality with the pre-HTML single-line extraction is preserved:
    # only whitespace (the restored line breaks) differs.
    old_glued = extract_new_text(fixture_body(stem)).text
    assert "".join(result.text.split()) == "".join(old_glued.split())
    composite = clean_for_scoring(result.text).text
    assert tolerant_lines(composite) == tolerant_lines(expected_text(stem))


def test_find_quote_header_block_outlook_sent():
    lines = (
        "Cheers,\nNate\n\n"
        "From: Wesley Eddy via Datatracker <noreply@example.org>\n"
        "Sent: Tuesday, June 23, 2026 3:45 PM\n"
        "To: tsv-art@example.org\n"
        "Subject: [Int-area] draft review\n\n"
        "Reviewer: Wesley Eddy\n"
    ).split("\n")
    idx = find_quote_header_block(lines)
    assert idx is not None
    assert lines[idx].startswith("From: Wesley Eddy")


def test_find_quote_header_block_date_variant_with_cc():
    # "Date:" instead of "Sent:", with a Cc: line — still a quote header.
    lines = (
        "Regards,\nAlice\n\n"
        "From: Bob Smith <bob@example.org>\n"
        "Date: Mon, 20 Jul 2026 10:00:00 +0000\n"
        "To: wg@example.org\n"
        "Cc: chairs@example.org\n"
        "Subject: Re: draft comments\n\n"
        "Old quoted content.\n"
    ).split("\n")
    idx = find_quote_header_block(lines)
    assert idx is not None
    assert lines[idx].startswith("From: Bob Smith")


def test_find_quote_header_block_ignores_pasted_evidence():
    # A From: preceded by another header line is pasted evidence embedded in
    # prose (the threadstarter-rfc2047-header fixture), not a real quote header.
    lines = (
        "TECHNICAL EVIDENCE\n\n"
        "Message-ID: <a@b>\n"
        "Date: Tue, 12 May 2026 10:15:36 +0200\n"
        "From: Timo Gerke <timo.gerke@alice-dsl.net>\n"
        "To: Deb Cooley <debcooley1@gmail.com>\n"
        "Subject: Notice\n\n"
        "Best regards,\nTimo Gerke\n"
    ).split("\n")
    assert find_quote_header_block(lines) is None


def test_find_quote_header_block_requires_signal_field():
    # A lone "From:" with no Sent/Date/To/Cc/Subject following is not a block.
    lines = ["From: someone", "Just ordinary prose here.", "More prose."]
    assert find_quote_header_block(lines) is None


def test_find_quote_header_block_not_blocked_by_signature_contact_line():
    # A signature's last lines can sit directly above the quote header. "Tel:"/"Email:" are header-shaped but not transport
    # fields, so they must not disqualify the From: below them.
    lines = (
        "Thanks for clarifying.\n\n"
        "Email: dick@example.org\n"
        "Tel: +1 978-555-0100\n"
        "From: Amaury Chamayou <amaury@example.com>\n"
        "Sent: Sunday, November 16, 2025 12:16 PM\n"
        "To: someone@example.org\n"
        "Subject: Re: WGLC\n\n"
        "Hi Dick,\n"
    ).split("\n")
    idx = find_quote_header_block(lines)
    assert idx is not None
    assert lines[idx].startswith("From: Amaury")


def test_find_quote_header_block_folded_lines_without_indent():
    # Archive renderings fold long header lines with no leading whitespace
    # ("… On\nBehalf Of …"); the walk must read them as continuations rather
    # than ending the run before the signal fields.
    lines = (
        "My reply.\n\n"
        "From: wg@example.org [mailto:wg-bounces@example.org] On\n"
        "Behalf Of Jordi Ros Giralt\n"
        "Sent: Monday, June 1, 2026 9:00 AM\n"
        "To: wg@example.org\n"
        "Subject: Re: draft\n\n"
        "Quoted.\n"
    ).split("\n")
    idx = find_quote_header_block(lines)
    assert idx is not None
    assert lines[idx].startswith("From: wg@example.org")


def test_find_quote_header_block_double_spaced_fields():
    # Some renderings put a blank line between every header field.
    lines = (
        "My reply.\n\n"
        "From: Mahesh Jethanandani <mjethanandani@example.org>\n\n"
        "Sent: Tuesday, June 23, 2026 2:33 PM\n\n"
        "To: wg@example.org\n\n"
        "Subject: Re: draft\n\n"
        "Quoted content.\n"
    ).split("\n")
    idx = find_quote_header_block(lines)
    assert idx is not None
    assert lines[idx].startswith("From: Mahesh")


def test_prose_from_line_without_address_gets_strict_walk():
    # A paragraph whose first line starts "From:" but carries no address gets
    # the strict walk: an unindented prose line ends the run before a signal
    # field can qualify the paragraph as a quote header.
    lines = (
        "From: my perspective, the proposal is fine.\n"
        "It aligns with what we said earlier in the thread.\n"
        "Subject: naming is the only open question.\n"
    ).split("\n")
    assert find_quote_header_block(lines) is None


def test_glued_signature_quote_header_extracts_author_only():
    # End-to-end shape of the businesscyberguardian defect: a top-posted reply
    # whose signature ends in contact lines, then an Outlook quote header with
    # a folded To:, then the quoted thread. Only the reply and its signature
    # survive stage 1.
    body = (
        "Thanks for clarifying. I suggest adding two more data elements.\n\n"
        "Thanks,\n\nDick Brooks\n\n"
        "Email: dick@example.org\n\n"
        "Tel: +1 978-555-0100\n\n\n"
        "From: Amaury Chamayou <amaury@example.com>\n"
        "Sent: Sunday, November 16, 2025 12:16 PM\n"
        "To: 'Antoine Delignat-Lavaud' <antdl@example.com>; 'Chris\n"
        "Inacio' <inacio@example.org>; wg@example.org\n"
        "Subject: Re: [EXTERNAL] Re: WGLC for the document\n\n"
        "Hi Dick,\n\nCWT Claims are mandatory in Signed Statements.\n\n"
        "Thank you,\nAmaury\n"
    )
    result = extract_new_text(body)
    assert result.status == "ok"
    assert result.method == "custom"
    assert "Amaury Chamayou" not in result.text
    assert "CWT Claims" not in result.text
    assert "Tel: +1 978-555-0100" in result.text  # signature survives stage 1


def test_strip_after_chinese_original_message_divider():
    # The Chinese Outlook divider "-----邮件原件-----" (and QQ Mail's
    # "-----原始邮件-----") truncates like "-----Original Message-----".
    lines = ["My reply.", "-----邮件原件-----", "发件人: Li Ming <li@example.org>", "Quoted."]
    assert strip_after_original_message_divider(lines) == ["My reply."]
    lines = ["My reply.", "------------------ 原始邮件 ------------------", "Quoted."]
    assert strip_after_original_message_divider(lines) == ["My reply."]


def test_find_quote_header_block_chinese_alibaba():
    # Alibaba Mail style: CJK labels, full-width colons with no space after
    # them, 主　题 padded with an ideographic space (U+3000), and a dashed
    # divider drawn above the block.
    lines = (
        "Looking forward to the discussions.\n"
        "Regards,\nWei Zhang\n"
        "------------------------------------------------------------------\n"
        "发件人：Li Ming <li.ming@example.org>\n"
        "发送时间：2026年7月15日(周三) 10:05\n"
        "收件人：Anna Schmidt<anna.schmidt@example.org>\n"
        "主　题：[wg] Re: charter scope\n"
        "Old quoted content here.\n"
    ).split("\n")
    idx = find_quote_header_block(lines)
    assert idx is not None
    assert lines[idx].startswith("发件人")


def test_find_quote_header_block_chinese_outlook_ascii_colon():
    # Chinese Outlook style: CJK labels with an ASCII colon and a space.
    lines = (
        "Thanks for the summary.\n\n"
        "发件人: Anna Schmidt [mailto:anna.schmidt@example.org]\n"
        "发送时间: 2026年7月15日 8:40\n"
        "收件人: Li Ming <li.ming@example.org>; wg@example.org\n"
        "主题: [wg] Re: charter scope\n"
        "Old quoted content here.\n"
    ).split("\n")
    idx = find_quote_header_block(lines)
    assert idx is not None
    assert lines[idx].startswith("发件人")


def test_strip_after_chinese_quote_header_drops_dangling_divider():
    # Truncating at an Alibaba-style block also drops the dashed divider the
    # client drew above it — furniture, not author content. A dashed rule
    # elsewhere in the body is untouched.
    body = (
        "First point.\n"
        "----------\n"
        "Second point after an author-drawn rule.\n"
        "Regards,\nWei Zhang\n"
        "------------------------------------------------------------------\n"
        "发件人：Li Ming <li.ming@example.org>\n"
        "发送时间：2026年7月15日(周三) 10:05\n"
        "收件人：Anna Schmidt<anna.schmidt@example.org>\n"
        "主　题：[wg] Re: charter scope\n"
        "Old quoted content here.\n"
        " Anna Schmidt\n"
        " Mobile: +49-000000000\n"
        " Mail: anna.schmidt@example.org <mailto:anna.schmidt@example.org >\n"
        "From:Third Person <third@example.org <mailto:third@example.org >>\n"
        "To:Li Ming <li.ming@example.org <mailto:li.ming@example.org >>\n"
    )
    result = extract_new_text(body)
    lines = [ln for ln in result.text.split("\n") if ln.strip()]
    assert lines[0] == "First point."
    assert "----------" in lines  # the author's own rule survives
    assert lines[-1] == "Wei Zhang"
    assert "发件人" not in result.text
    assert "Anna Schmidt" not in result.text.split("Regards,")[-1]
    assert "Mobile:" not in result.text
    assert "From:Third Person" not in result.text


def test_toppost_outlook_quoteheader_fixture_extracts_author_only():
    """The Phase 7.2 defect: an Outlook top-post must not ship the quoted review.

    Stage 1's ``strip_after_quote_header_block`` truncates the
    header-introduced quoted review.
    """
    stem = "toppost-outlook-quoteheader-lastcall-01"
    body = fixture_body(stem)
    result = extract_new_text(body)
    assert result.method == "custom"
    composite = clean_for_scoring(result.text).text
    # Only the author's content lines survive; no framing, none of the review.
    assert "Nate" not in composite
    assert "Reviewer: Wesley Eddy" not in composite
    assert "congestion" not in composite
    assert tolerant_lines(composite) == tolerant_lines(expected_text(stem))


# --- unit tests: Latin-script localized quote-header blocks (generation 6) -----


@pytest.mark.parametrize("space", [" ", "\t", " ", " "])
def test_find_quote_header_block_french_any_space_before_colon(space):
    # French typography puts a space before the colon; stored text carries an
    # ordinary space, a tab, U+00A0 or U+202F there.
    lines = [
        "Merci pour la revue.",
        "",
        f"De{space}: Mario Rossi <mario@example.org>",
        f"Envoyé{space}: mercredi 9 septembre 2026 10:38",
        f"À{space}: Anna Schmidt <anna@example.org>; wg-chairs@example.org",
        f"Cc{space}:{space}wg@example.org",
        f"Objet{space}: Re: [wg] charter scope",
        "",
        "Quoted message.",
    ]
    assert find_quote_header_block(lines) == 2


def test_find_quote_header_block_french_unaccented_and_date_labels():
    # "Envoye" without the accent, "A" for "À", and the Mac Outlook "Date :".
    lines = [
        "De : Mario Rossi <mario@example.org>",
        "Date : mercredi 9 septembre 2026 10:38",
        "A : Anna Schmidt <anna@example.org>",
        "Objet : Re: [wg] charter scope",
    ]
    assert find_quote_header_block(lines) == 0
    lines[1] = "Envoye : mercredi 9 septembre 2026 10:38"
    assert find_quote_header_block(lines) == 0


def test_find_quote_header_block_french_requires_subject_label():
    # "De", "A" and "Date" are ordinary words: without the "Objet" subject line
    # the run is not a quote header, however many label-like lines follow.
    lines = [
        "De : nos jours, la question reste ouverte.",
        "A : premier point.",
        "Date : à confirmer.",
        "Suite du texte.",
    ]
    assert find_quote_header_block(lines) is None


def test_find_quote_header_block_french_opener_alone_is_prose():
    # A prose line opening "De : …" with no field lines below never triggers.
    lines = ["De : manière générale, je suis d'accord.", "", "Objet : la suite."]
    assert find_quote_header_block(lines) is None


def test_find_quote_header_block_french_needs_two_fields_after_opener():
    lines = ["De : Mario Rossi <mario@example.org>", "Objet : Re: [wg] scope", "Texte."]
    assert find_quote_header_block(lines) is None


@pytest.mark.parametrize(
    "block",
    [
        # Spanish
        [
            "De: Ana Pérez <ana@example.org>",
            "Enviado el: miércoles, 8 de julio de 2026 19:56",
            "Para: wg@example.org",
            "Asunto: Re: [wg] scope",
        ],
        # Portuguese
        [
            "De: João Silva <joao@example.org>",
            "Enviada em: segunda-feira, 6 de julho de 2026 16:52",
            "Para: wg@example.org",
            "Assunto: Re: [wg] scope",
        ],
        # Italian
        [
            "Da: Mario Rossi <mario@example.org>",
            "Inviato: venerdì 7 agosto 2026 14:13",
            "A: wg <wg@example.org>",
            "Oggetto: Re: [wg] scope",
        ],
        # Dutch
        [
            "Van: Jan de Vries <jan@example.org>",
            "Verzonden: maandag 27 juli 2026 14:00",
            "Aan: wg@example.org",
            "Onderwerp: Re: [wg] scope",
        ],
        # German
        [
            "Von: Anna Schmidt <anna@example.org>",
            "Gesendet: Montag, 13. Juli 2026 17:15",
            "An: wg@example.org",
            "Betreff: Re: [wg] scope",
        ],
    ],
)
def test_find_quote_header_block_latin_localizations(block):
    lines = ["My reply.", "", *block, "", "Quoted message."]
    assert find_quote_header_block(lines) == 2
    assert find_quote_header_block(lines, localized=False) is None
    assert extract_new_text("\n".join(lines)).text == "My reply."


def test_find_quote_header_block_german_an_without_subject_is_prose():
    # German "An" (and Italian "A") are ordinary words: a "Von:" line followed
    # by "An:" alone is not a block.
    lines = ["Von: der Arbeitsgruppe vorgeschlagen.", "An: alle Teilnehmer.", "Danke."]
    assert find_quote_header_block(lines) is None


@pytest.mark.parametrize(
    "divider",
    [
        "-----Message d'origine-----",
        "-----Message d’origine-----",
        "-------- Message transféré --------",
        "-----Mensaje original-----",
        "-----Mensagem original-----",
        "-----Messaggio originale-----",
        "-----Oorspronkelijk bericht-----",
        "-----Ursprüngliche Nachricht-----",
        "---------- Weitergeleitete Nachricht ----------",
    ],
)
def test_strip_after_localized_original_message_divider(divider):
    lines = ["My reply.", divider, "Quoted."]
    assert strip_after_original_message_divider(lines) == ["My reply."]
    assert strip_after_original_message_divider(lines, localized=False) == lines


def test_localized_divider_prose_is_safe():
    lines = ["Le message d'origine est en pièce jointe.", "Texte."]
    assert strip_after_original_message_divider(lines) == lines


_FRENCH_INLINE_PARENT = (
    "Hi Med,\n\n"
    "Many thanks for the review. Please see our responses below.\n\n"
    "# Consider adding a pointer to the registry cited here in the introduction\n\n"
    "Updated as suggested, with a pointer to the IANA registry group.\n\n"
    "# Early allocation is mentioned but the policy text is outdated now\n\n"
    "Updated. We have added a reference to the policy document as well.\n\n"
    "Regards,\nNat\n"
)

_FRENCH_INLINE_BODY = (
    "Hi Nat,\n\n"
    "Thank you for the follow-up. Please see inline.\n\n"
    "Cheers,\nMed\n\n"
    "De : Nat Kao <nat@example.org>\n"
    "Envoyé : vendredi 3 juillet 2026 04:16\n"
    "À : BOUCADAIR Mohamed <med@example.org>\n"
    "Objet : Re: review of draft-ietf-wg-example\n\n"
    "Hi Med,\n\n"
    "Many thanks for the review. Please see our responses below.\n\n"
    "# Consider adding a pointer to the registry cited here in the introduction\n\n"
    "Updated as suggested, with a pointer to the IANA registry group.\n"
    "[Med] ACK\n\n"
    "# Early allocation is mentioned but the policy text is outdated now\n\n"
    "Updated. We have added a reference to the policy document as well.\n"
    "[Med] I think this can be simplified further by citing the policy document alone.\n\n"
    "Regards,\nNat\n"
)


def test_inline_replies_below_french_block_survive_with_parent():
    # The author answers inside the quoted message below a French block. With
    # the parent available, the tagged answers are provable, the localized
    # truncation is not applied, and parent-diff removes the quoted parent.
    result = extract_new_text(_FRENCH_INLINE_BODY, parent_body=_FRENCH_INLINE_PARENT)
    assert "[Med] ACK" in result.text
    assert "[Med] I think this can be simplified further" in result.text
    assert "Many thanks for the review" not in result.text
    assert result.method.endswith("+parent-diff")


def test_inline_reply_guard_keeps_generation_5_behavior():
    # With the guard engaged, the output equals the generation-5 rule set
    # (localized blocks not recognized) followed by the parent-diff assist.
    lines = normalize_body(_FRENCH_INLINE_BODY).split("\n")
    assert find_quote_header_block(lines) is not None
    assert find_quote_header_block(lines, localized=False) is None
    result = extract_new_text(_FRENCH_INLINE_BODY, parent_body=_FRENCH_INLINE_PARENT)
    assert "De : Nat Kao" in result.text


def test_inline_reply_tag_from_parent_does_not_block_truncation():
    # A tag the parent's author used ("[ML]") that survives the parent-diff
    # (re-formatted in the parent) belongs to an earlier round, not to this
    # message's author: the French block still truncates.
    parent = (
        "Hi,\n\nplease find my comments below.\n\n"
        "> The new language should not be applied retroactively to adopted documents.\n\n"
        "[ML] I believe the phrase *New application transport mappings* applies\n"
        "more naturally to the adoption of additional transport mappings.\n\n"
        "Best,\nMario\n"
    )
    body = (
        "Hi Mario,\n\nThank you for sharing your thoughts on this.\n\n"
        "Cheers,\nMed\n\n"
        "De : Mario Rossi <mario@example.org>\n"
        "Envoyé : mercredi 9 septembre 2026 10:38\n"
        "À : wg-chairs@example.org\n"
        "Objet : Re: [wg] charter\n\n"
        "Hi,\n\nplease find my comments below.\n\n"
        "[ML] I believe the phrase New application transport mappings applies more "
        "naturally to the adoption of additional transport mappings.\n\n"
        "Best,\nMario\n"
    )
    result = extract_new_text(body, parent_body=parent)
    assert "[ML]" not in result.text
    assert "De : Mario Rossi" not in result.text
    assert "Thank you for sharing your thoughts on this." in result.text


def test_inline_reply_without_parent_needs_author_named_tag():
    # Without the parent, a tag counts as the author's only when its label names
    # the author above the block (the sign-off "Med" for "[Med]").
    with_author_tag = extract_new_text(_FRENCH_INLINE_BODY)
    assert "[Med] ACK" in with_author_tag.text
    other_tag = _FRENCH_INLINE_BODY.replace("[Med]", "[NK]")
    truncated = extract_new_text(other_tag)
    assert "[NK]" not in truncated.text
    assert "De : Nat Kao" not in truncated.text


def test_french_fixtures_extract_author_only():
    for stem in (
        "toppost-french-quoteheader-regext-01",
        "toppost-french-originalmsg-divider-regext-01",
    ):
        composite = composite_text(stem)
        assert "Objet" not in composite
        assert "origine" not in composite
        assert tolerant_lines(composite) == tolerant_lines(expected_text(stem))


# --- unit tests: statuses / edge cases ----------------------------------------


def test_none_body_is_empty():
    result = extract_new_text(None)
    assert result.status == "empty"
    assert result.method == "none"
    assert result.text == ""


def test_blank_body_is_empty():
    assert extract_new_text("   \n\n\t").status == "empty"


def test_quote_only_body_is_empty():
    result = extract_new_text("> just a quote\n> and more quote\n")
    assert result.status == "empty"


def test_bom_prefixed_quote_is_stripped_end_to_end():
    # Without normalization the BOM-prefixed quote line would leak.
    body = "﻿> Given the proposal\n\nMy actual reply here."
    result = extract_new_text(body)
    assert "Given the proposal" not in result.text
    assert result.text.strip() == "My actual reply here."


# --- unit tests: HTML oracle (html_body) --------------------------------------


def test_html_only_extracts_novel_text_with_html_prefix():
    # No plain body at all: the novel text is derived from the HTML part and run
    # through the normal stage-1 pipeline; the method is prefixed "html-".
    html = (
        "<div>Here is my genuinely new reply, written for the working group.</div>"
        "<blockquote><div>On some date X wrote:</div><div>the old quoted message</div>"
        "</blockquote>"
    )
    result = extract_new_text(None, html_body=html)
    assert result.status == "ok"
    assert result.method.startswith("html-")
    assert "genuinely new reply" in result.text
    assert "old quoted message" not in result.text


def test_html_only_blank_html_is_empty_none():
    # No plain body and no usable HTML falls back to the original empty result.
    result = extract_new_text(None, html_body="   ")
    assert result.status == "empty"
    assert result.method == "none"


def test_degenerate_plain_falls_back_to_html():
    # A flattened plain body (one giant glued line) with a structured HTML part:
    # the HTML novel text (properly line-broken) is used instead, "html-" prefix.
    glued = (
        "First point about the draft that runs on and on and on. " * 12
    ).strip() + "Second sentence glued here.Third sentence also glued on."
    html = (
        "<div>First point about the draft.</div>"
        "<div>Second structured line here.</div>"
        "<div>Third structured line here.</div>"
        "<div>Fourth structured line here.</div>"
    )
    result = extract_new_text(glued, html_body=html)
    assert result.status == "ok"
    assert result.method.startswith("html-")
    # Multi-line, from the HTML — not the single glued plain line.
    assert len([ln for ln in result.text.split("\n") if ln.strip()]) >= 4


def test_non_degenerate_plain_is_not_replaced_by_html():
    # A perfectly normal multi-line plain body is used as-is (no html- prefix),
    # even when an HTML part is present.
    body = "Line one of my reply.\nLine two of my reply.\nLine three.\nLine four here."
    html = "<div>totally different html novel text that should be ignored</div>"
    result = extract_new_text(body, html_body=html)
    assert not result.method.startswith("html-")
    assert "Line one of my reply." in result.text
    assert "different html novel text" not in result.text


def test_oracle_assist_removes_blockquote_matching_unmarked_quote():
    # A top-post whose quoted previous message is reproduced in the plain body
    # with NO ">" markers (so nothing is stripped), while the HTML wraps that
    # same message in a <blockquote>. The oracle removes the leaked block and
    # appends "+html-quote".
    quoted_para_1 = "The registry should remain open for new entries at all times going forward."
    quoted_para_2 = "Each entry must include a stable reference and a short human readable summary."
    quoted_para_3 = "Reviewers should be given at least two full weeks to raise any late concerns."
    body = (
        "I have revised the entire draft to address every one of your review points.\n"
        "\n"
        f"{quoted_para_1}\n{quoted_para_2}\n{quoted_para_3}\n"
    )
    html = (
        "<div>I have revised the entire draft to address every one of your review points.</div>"
        f"<blockquote><div>{quoted_para_1}</div><div>{quoted_para_2}</div>"
        f"<div>{quoted_para_3}</div></blockquote>"
    )
    result = extract_new_text(body, html_body=html)
    assert "I have revised the entire draft" in result.text
    assert quoted_para_1 not in result.text
    assert quoted_para_2 not in result.text
    assert result.method.endswith("+html-quote")


def test_oracle_assist_spares_isolated_coincidental_match():
    # A single author line that happens to echo a quoted sentence verbatim must
    # NOT be stripped — the oracle only adopts a substantial (block) removal.
    echoed = "Each entry must include a stable reference and a short human readable summary."
    body = f"I strongly agree with your point that follows below in the quoted thread.\n{echoed}\n"
    html = (
        "<div>I strongly agree with your point that follows below in the quoted thread.</div>"
        f"<div>{echoed}</div>"
        f"<blockquote><div>{echoed}</div></blockquote>"
    )
    result = extract_new_text(body, html_body=html)
    assert echoed in result.text  # the single coincidental match is kept
    assert "+html-quote" not in result.method


def test_html_body_none_is_identical_to_no_argument():
    body = "> quoted line\n\nMy genuinely new reply text here today."
    assert extract_new_text(body, html_body=None) == extract_new_text(body)


# --- unit tests: Apple Mail Share-sheet container (path (d)) -------------------

_SHARE_PROSE = (
    "The registry proposal in the second draft keeps the allocation policy unchanged.",
    "I suggest we move the open question about expiry to the next interim meeting.",
)


def _share_html(inner: str, before: str = "", after: str = "") -> str:
    """An HTML body shaped like Apple Mail's Share sheet around ``inner``."""
    return (
        '<html><head><meta charset="utf-8"></head><body><div dir="auto">'
        f'<div class="Apple-Mail-URLShareUserContentTopClass">{before}<br></div>'
        '<div class="Apple-Mail-URLShareWrapperClass" style="position: relative !important;">'
        '<blockquote type="cite" style="border-left-style: none; color: inherit;">'
        f"{inner}</blockquote></div>{after}<br><br></body></html>"
    )


def _cite(lines: list[str]) -> str:
    """The text/plain rendering of a Share-sheet body: every line ``>``-prefixed."""
    return "\n".join(f"> {line}" if line else ">" for line in lines) + "\n"


def test_share_container_recovers_text_the_plain_part_quotes():
    lines = ["Carol,", "", *_SHARE_PROSE, "", "Alice"]
    html = _share_html("<br>".join(lines))
    body = _cite(lines)
    assert extract_new_text(body).status == "empty"  # the plain part alone
    result = extract_new_text(body, html_body=html)
    assert result.status == "ok"
    assert result.method == "html-custom"
    assert result.text == "Carol,\n\n" + "\n".join(_SHARE_PROSE) + "\n\nAlice"


def test_share_container_keeps_text_typed_outside_the_wrapper_in_order():
    html = _share_html(
        "<br>".join(_SHARE_PROSE),
        before="Forwarding my notes from the review.",
        after="<div>Sent from my phone</div>",
    )
    body = (
        "Forwarding my notes from the review.\n\n"
        + _cite(list(_SHARE_PROSE))
        + "\nSent from my phone\n"
    )
    result = extract_new_text(body, html_body=html)
    assert result.method.startswith("html-")
    lines = [ln for ln in result.text.split("\n") if ln.strip()]
    assert lines == ["Forwarding my notes from the review.", *_SHARE_PROSE, "Sent from my phone"]


def test_share_container_nested_blockquote_is_removed():
    html = _share_html(
        "<br>".join(_SHARE_PROSE)
        + '<blockquote type="cite">An earlier message that this one quotes in full.</blockquote>'
    )
    body = _cite([*_SHARE_PROSE, "", "> An earlier message that this one quotes in full."])
    result = extract_new_text(body, html_body=html)
    assert result.method.startswith("html-")
    assert result.text == "\n".join(_SHARE_PROSE)


def test_share_container_attribution_and_quoted_lines_are_removed_by_the_core_path():
    # The author composed a reply in another app, with an attribution line and
    # ">"-quoted text, and shared it: inside the container they are text, and
    # the stage-1 pipeline removes them as it would in a plain body.
    inner = "<br>".join(
        [
            _SHARE_PROSE[0],
            "",
            "On Mon, 2 Nov 2026 at 10:00, Bob Example <bob@example.org> wrote:",
            "&gt; The allocation policy should change in the next revision.",
            "&gt; Expiry also needs discussion.",
            "",
            _SHARE_PROSE[1],
        ]
    )
    html = _share_html(inner)
    body = _cite(
        [
            _SHARE_PROSE[0],
            "",
            "On Mon, 2 Nov 2026 at 10:00, Bob Example <bob@example.org> wrote:",
            "> The allocation policy should change in the next revision.",
            "> Expiry also needs discussion.",
            "",
            _SHARE_PROSE[1],
        ]
    )
    result = extract_new_text(body, html_body=html)
    assert result.status == "ok"
    assert result.method.startswith("html-")
    assert "wrote:" not in result.text
    assert "allocation policy should change" not in result.text
    assert [ln for ln in result.text.split("\n") if ln.strip()] == list(_SHARE_PROSE)


def test_share_container_passes_the_parent_to_the_core_path():
    parent = "\n".join(
        [
            "Here is the agenda for the interim meeting next week.",
            "Item one covers the registry and its allocation policy in detail.",
            "Item two covers expiry and the remaining open questions from Prague.",
        ]
    )
    inner = "<br>".join([_SHARE_PROSE[0], "", *parent.split("\n")])
    html = _share_html(inner)
    body = _cite([_SHARE_PROSE[0], "", *parent.split("\n")])
    result = extract_new_text(body, parent_body=parent, html_body=html)
    assert result.method == "html-custom+parent-diff"
    assert result.text == _SHARE_PROSE[0]


def test_share_sheet_fixture_derives_text_from_html():
    stem = "toppost-share-sheet-synthetic-01"
    result = extract_new_text(fixture_body(stem), html_body=fixture_html(stem))
    assert result.method == "html-custom"
    assert result.text.startswith("Hi Carol,\n\nThank you for the review.")
    assert result.text.endswith("Best,\nAlice")
    assert "wrote:" not in result.text
    assert "I could not find it" not in result.text


def test_ordinary_cite_blockquote_does_not_take_the_share_path():
    html = (
        "<div>My reply to the agenda, written below the quote.</div>"
        '<blockquote type="cite">' + "<br>".join(_SHARE_PROSE) + "</blockquote>"
    )
    body = "My reply to the agenda, written below the quote.\n\n" + _cite(list(_SHARE_PROSE))
    result = extract_new_text(body, html_body=html)
    assert not result.method.startswith("html-")
    assert result.text == "My reply to the agenda, written below the quote."


def test_share_container_with_blank_novel_text_keeps_the_plain_path():
    html = _share_html("<br>")
    body = _cite(list(_SHARE_PROSE))
    result = extract_new_text(body, html_body=html)
    assert result == extract_new_text(body)
    assert result.status == "empty"
    assert not result.method.startswith("html-")


def test_share_shaped_plain_body_without_html_is_unchanged():
    body = _cite(list(_SHARE_PROSE))
    result = extract_new_text(body)
    assert result.status == "empty"
    assert extract_new_text(body, html_body=None) == result


def test_reply_quoting_a_share_sheet_message_keeps_the_plain_path():
    # The share container sits inside this reply's own quote, so the HTML
    # reports no share container and the plain pipeline runs as before.
    reply = "I agree with both points and will update the draft this week."
    html = (
        f"<div>{reply}</div>"
        '<blockquote type="cite"><div>On Mon, 2 Nov 2026, Bob Example wrote:</div>'
        + _share_html("<br>".join(_SHARE_PROSE))
        + "</blockquote>"
    )
    body = f"{reply}\n\nOn Mon, 2 Nov 2026, Bob Example wrote:\n\n" + _cite(list(_SHARE_PROSE))
    result = extract_new_text(body, html_body=html)
    assert not result.method.startswith("html-")
    assert result.text == reply


# --- unit tests: parent-diff assist -------------------------------------------


def test_parent_diff_removes_unquoted_toppost_thread():
    # Fully top-posted reply: author's new text, then the entire parent message
    # reproduced with no ">" markers and no attribution. The parent portion must
    # go; the authored portion must stay; the method gains "+parent-diff".
    parent = (
        "Thanks for the detailed proposal about the new key rotation scheme.\n"
        "I think the possession side needs a clearer failure mode when the status\n"
        "cannot be established within the negotiated window between the two peers.\n"
    )
    child = (
        "I have revised the entire draft to incorporate all of your suggestions.\n"
        "Every reviewer concern is now tracked in the updated issue list online.\n"
        "\n" + parent
    )
    result = extract_new_text(child, parent)
    assert "I have revised the entire draft" in result.text
    assert "possession side needs a clearer failure mode" not in result.text
    assert result.method.endswith("+parent-diff")


def test_parent_diff_removes_rewrapped_paragraph():
    # The parent paragraph is one long line; the child re-wraps it at a narrower
    # width, so no child line equals a parent line (aligned-run misses it). The
    # rewrap rule removes each long child line found in the parent word-stream.
    parent = (
        "the registry should remain open for new entries but each entry must "
        "include a stable reference and a short human readable description"
    )
    child = (
        "I strongly agree with the summary you posted to the list earlier today\n"
        "\n"
        "the registry should remain open for new entries but each entry\n"
        "must include a stable reference and a short human readable description\n"
    )
    result = extract_new_text(child, parent)
    assert "I strongly agree with the summary" in result.text
    assert "registry should remain open" not in result.text
    assert result.method.endswith("+parent-diff")


def test_parent_diff_keeps_short_signoff_present_in_parent():
    # The author's own sign-off also sits inside the parent's nested quotes, but
    # it is too short to be strong evidence, so it must survive.
    text = "Best,\nSongbo"
    parent = "Some earlier discussion here about the draft.\nBest,\nSongbo"
    assert strip_parent_content(text, parent) == "Best,\nSongbo"


def test_parent_diff_keeps_short_authored_echo():
    # A short (<8-word) echo of the parent quoted inline is coincidental, not
    # proof, so it is not deleted.
    parent = "We noted that the status cannot be established within the negotiated window."
    text = (
        "Quoting you: the status cannot be established.\n"
        "I think we should fix that in the next revision of the draft."
    )
    result = strip_parent_content(text, parent)
    assert "the status cannot be established" in result
    assert "next revision of the draft" in result


# A parent agenda of five two-line items (each item 10 words, no line of 8),
# reproduced by the child with item 4 moved to the end.
_AGENDA_ITEMS = {
    1: ["Agenda bashing and note taker", "Chairs, five minutes in total"],
    2: ["Refresh interval in section four", "Presenter Bob, twenty minutes"],
    3: ["Registry changes for the draft", "Presenter Alice, fifteen minutes"],
    4: ["Open issues on the tracker", "Presenter Carol, ten minutes total"],
    5: ["Next steps and wrap up", "Chairs, five minutes at the end"],
}


def _agenda(order: tuple[int, ...]) -> str:
    return "\n\n".join("\n".join(_AGENDA_ITEMS[k]) for k in order)


def test_parent_diff_removes_a_reproduced_block_out_of_order():
    # Items 1-3 are the longest common run (the anchor). After it the child
    # has items 5 and 4, the parent items 4 and 5: both are parent content on
    # the same side of the anchor, and both are removed (generation 7; the
    # generation-6 difflib alignment matched only one of them).
    parent = "Hi all,\n\nThe agenda for the interim:\n\n" + _agenda((1, 2, 3, 4, 5)) + "\n\nBob\n"
    child = (
        "I moved the open issues to the end, since Carol joins late.\n\n"
        + _agenda((1, 2, 3, 5, 4))
        + "\n"
    )
    result = extract_new_text(child, parent)
    assert result.text == "I moved the open issues to the end, since Carol joins late."
    assert result.method == "custom+parent-diff"


def test_parent_diff_none_is_identical_to_no_argument():
    body = "> quoted line\n\nMy genuinely new reply text here."
    assert extract_new_text(body, None) == extract_new_text(body)


def test_parent_diff_entire_parent_resend_is_empty():
    # An exact re-send of the parent legitimately reduces to nothing.
    parent = (
        "This entire message is a verbatim resend of the earlier note today.\n"
        "Every single line here comes straight from the parent message body.\n"
    )
    result = extract_new_text(parent, parent)
    assert result.status == "empty"
    assert result.text == ""
    assert result.method.endswith("+parent-diff")


def test_strip_parent_content_thresholds():
    # A block of two short lines totaling < 10 words (and no line >= 8) survives.
    short_block = "Hi all,\nBest wishes"
    parent = "Hi all,\nBest wishes\nand some other content only in the parent body."
    assert strip_parent_content(short_block, parent) == "Hi all,\nBest wishes"

    # A single line of >= 8 words is strong enough on its own to be removed.
    long_line = "This is a fairly long authored looking line with plenty of words."
    assert strip_parent_content(long_line, long_line) == ""


def test_strip_parent_content_never_raises_on_empty_input():
    assert strip_parent_content("", "") == ""
    assert strip_parent_content("   \n\n", "anything at all here") == ""


# One quoted message re-wrapped by the child at a narrower width: three long
# lines seed rules (a)/(b), and each paragraph leaves a short remainder line
# that only the continuation rule can attribute to the parent.
_CONTINUATION_PARENT = (
    "Address prefixes are orderable as a property of the objects so containment is mechanical.\n"
    "Communities are the opposite by design with semantics that stay strictly bilateral throughout.\n"
    "Both are useful and the protocol does not pretend the second case is the first.\n"
)
_CONTINUATION_CHILD = (
    "Thanks for laying this out so clearly.\n"
    "\n"
    "Address prefixes are orderable as a property of the objects so containment\n"
    "is mechanical.\n"
    "Communities are the opposite by design with semantics that stay strictly\n"
    "bilateral throughout.\n"
    "Both are useful and the protocol does not pretend the second case is\n"
    "the first.\n"
)


def test_continuation_removes_rewrapped_remainder_lines():
    # The sub-8-word remainders ("is mechanical.", "bilateral throughout.",
    # "the first.") continue the parent word-stream right after a marked line.
    result = strip_parent_content(_CONTINUATION_CHILD, _CONTINUATION_PARENT, continuation=True)
    assert result == "Thanks for laying this out so clearly."


def test_continuation_is_off_by_default():
    # Without the flag (the html-quote oracle call site) the remainders leak,
    # exactly as before generation 4.
    result = strip_parent_content(_CONTINUATION_CHILD, _CONTINUATION_PARENT)
    assert "is mechanical." in result
    assert "the first." in result


def test_continuation_needs_a_seeded_block():
    # Two matching lines are an inline citation, not a quoted block: below the
    # seed floor the continuation rule stays off and the third short line (and
    # everything else) survives.
    parent = (
        "The first cited sentence is long enough to match the rewrap rule cleanly "
        "and the second cited sentence is also long enough to match that same rule. "
        "A short tail."
    )
    text = (
        "The first cited sentence is long enough to match the rewrap rule cleanly\n"
        "and the second cited sentence is also long enough to match that same rule.\n"
        "A short tail.\n"
        "My own conclusion drawn from those two sentences follows here.\n"
    )
    result = strip_parent_content(text, parent, continuation=True)
    assert "A short tail." in result
    assert "My own conclusion" in result


def test_continuation_keeps_the_authors_own_signoff():
    # The live sign-off's neighbors are the author's new text, which is not in
    # the parent stream, so no join can mark it even while the quoted block
    # below is fully removed.
    child = (
        "My genuinely new answer to the thread goes right here in this line.\n"
        "\n"
        "Songbo\n"
        "\n" + _CONTINUATION_CHILD.split("\n", 2)[2]  # the re-wrapped quote block
    )
    result = strip_parent_content(child, _CONTINUATION_PARENT, continuation=True)
    assert "Songbo" in result
    assert "is mechanical." not in result


# --- parent-diff own-quotation rule (d) ----------------------------------------

# A parent that carries an earlier draft of a proposal. The children below are
# replies whose top-posted text is a revision of that draft, above the client's
# marked quotation of the parent.
_DRAFT_PARENT = (
    "Comments on the draft are below.\n"
    "\n"
    "The protocol should allow a receiving participant to verify a particular\n"
    "relationship across administrative and trust boundaries while minimising\n"
    "unnecessary disclosure of delegation history and participant information.\n"
    "\n"
    "The protocol should distinguish between:\n"
    "\n"
    "   - Verification of an individual parent-to-child relationship.\n"
    "   - Verification of a complete delegation or propagation chain.\n"
    "   - Verification based on partial provenance.\n"
    "   - Disclosure of the identities of all participants in the interaction\n"
    "   path.\n"
    "\n"
    "Regards,\n"
    "Alex\n"
)
_DRAFT_QUOTATION = "".join("> " + line + "\n" for line in _DRAFT_PARENT.split("\n"))


def _strip_with_quotation(child: str, parent: str = _DRAFT_PARENT) -> str:
    return strip_parent_content(child, parent, continuation=True, quoted_text=_DRAFT_QUOTATION)


def test_own_quotation_rule_keeps_a_parent_line_ending_the_authors_paragraph():
    child = (
        "Enable a receiving participant to verify a particular linked-dialog\n"
        "relationship across administrative and trust domains, while keeping\n"
        "unnecessary disclosure of delegation history and participant information.\n"
    )
    # Without the marked quotation the last line is removed (generation 8).
    assert "unnecessary disclosure" not in strip_parent_content(
        child, _DRAFT_PARENT, continuation=True
    )
    assert _strip_with_quotation(child) == child.strip()


def test_own_quotation_rule_keeps_a_parent_line_opening_the_authors_paragraph():
    # The matched line opens its block: nothing of the author's leads into it.
    child = (
        "The protocol should allow a receiving participant to verify a particular\n"
        "dialog relationship, and only that relationship, across the boundaries of\n"
        "the administrative domains that the dialog passes through on its way.\n"
    )
    assert "verify a particular" not in strip_parent_content(
        child, _DRAFT_PARENT, continuation=True
    )
    assert _strip_with_quotation(child) == child.strip()


def test_own_quotation_rule_keeps_parent_items_inside_the_authors_list():
    child = (
        "The protocol SHOULD distinguish between the following four cases:\n"
        "   - Verification of an individual parent-to-child dialog relationship.\n"
        "   - Verification of a complete delegation or propagation chain.\n"
        "   - Verification based on partial provenance.\n"
        "   - Disclosure of the identities of participants in the interaction path.\n"
        "   - Verification that the delegated authority has not been withdrawn.\n"
    )
    assert _strip_with_quotation(child) == child.strip()


def test_own_quotation_rule_removes_text_the_marked_quotation_lacks():
    # The message quotes something else with markers: the matched line is not
    # in its marked quotation, so it may be the client's unmarked copy.
    child = (
        "Enable a receiving participant to verify a particular linked-dialog\n"
        "relationship across administrative and trust domains, while keeping\n"
        "unnecessary disclosure of delegation history and participant information.\n"
    )
    result = strip_parent_content(
        child,
        _DRAFT_PARENT,
        continuation=True,
        quoted_text="> An unrelated earlier message that the reply also quotes.\n",
    )
    assert "unnecessary disclosure" not in result
    assert "Enable a receiving participant" in result


def test_own_quotation_rule_removes_a_passage_placed_whole():
    # A paragraph of the parent pasted above the answer to it: its block is
    # entirely matched text, so it stays removed although the marked
    # quotation contains it.
    child = (
        "The protocol should allow a receiving participant to verify a particular\n"
        "relationship across administrative and trust boundaries while minimising\n"
        "unnecessary disclosure of delegation history and participant information.\n"
        "\n"
        "I do not think minimising disclosure belongs in this requirement at all.\n"
    )
    result = _strip_with_quotation(child)
    assert result == "I do not think minimising disclosure belongs in this requirement at all."


def test_own_quotation_rule_removes_a_mostly_matched_block():
    # Two matched lines and a short answer directly below them: the block's
    # matched lines carry more words than its unmatched line.
    child = (
        "relationship across administrative and trust boundaries while minimising\n"
        "unnecessary disclosure of delegation history and participant information.\n"
        "Agreed, thanks.\n"
    )
    assert _strip_with_quotation(child) == "Agreed, thanks."


def test_own_quotation_rule_rederives_continuation_without_restored_lines():
    # "2." chains off the long line above it (the two are contiguous in the
    # parent), but that line is restored, so "2." is no longer removed. The
    # pasted paragraph below still seeds the continuation rule.
    parent = (
        "The scenarios are:\n"
        "1. Transport change, in which a dialog continues over another transport.\n"
        "2. Cross-domain continuation, where a dialog crosses a trust boundary.\n"
        "\n"
        "The protocol should allow a receiving participant to verify a particular\n"
        "relationship across administrative and trust boundaries while minimising\n"
        "unnecessary disclosure of delegation history and participant information.\n"
    )
    quotation = "".join("> " + line + "\n" for line in parent.split("\n"))
    child = (
        "Under my revision a dialog that first continues over another transport is\n"
        "covered by the first of the scenarios, which I have left as it was, namely\n"
        "1. Transport change, in which a dialog continues over another transport.\n"
        "\n"
        "2.\n"
        "\n"
        "The protocol should allow a receiving participant to verify a particular\n"
        "relationship across administrative and trust boundaries while minimising\n"
        "unnecessary disclosure of delegation history and participant information.\n"
    )
    result = strip_parent_content(child, parent, continuation=True, quoted_text=quotation)
    assert "1. Transport change" in result
    assert "\n2." in result
    assert "The protocol should allow" not in result
    # Generation 8 removed all three.
    old = strip_parent_content(child, parent, continuation=True)
    assert "1. Transport change" not in old and "\n2." not in old


def test_extract_new_text_keeps_a_revision_above_the_marked_quotation():
    revision = (
        "Here is the revised requirement.\n"
        "\n"
        "Enable a receiving participant to verify a particular linked-dialog\n"
        "relationship across administrative and trust domains, while keeping\n"
        "unnecessary disclosure of delegation history and participant information.\n"
    )
    body = revision + "\nOn Mon, 2 Nov 2026, Alex wrote:\n\n" + _DRAFT_QUOTATION
    result = extract_new_text(body, _DRAFT_PARENT)
    assert result.text == revision.strip()
    assert "+parent-diff" not in result.method
    # The same text with no marked quotation in the body loses the line.
    unmarked = extract_new_text(revision, _DRAFT_PARENT)
    assert "unnecessary disclosure" not in unmarked.text
    assert unmarked.method.endswith("+parent-diff")


def test_parent_diff_no_removal_leaves_text_and_method_untouched():
    # A parent that shares no substantial content must not change the output.
    # strip_parent_content collapses internal blank runs, so a naive string
    # comparison would spuriously add "+parent-diff" and reformat the text; the
    # assist must key off real content removal, not incidental reformatting.
    body = "First authored paragraph here.\n\n\nSecond authored paragraph here."
    parent = "Totally unrelated earlier message about something else entirely."
    with_parent = extract_new_text(body, parent)
    without_parent = extract_new_text(body, None)
    assert with_parent == without_parent
    assert "+parent-diff" not in with_parent.method
    # An empty parent body (a stored row with raw_body = "") is the same no-op.
    assert extract_new_text(body, "") == without_parent


# --- part boundaries ------------------------------------------------------------

_INLINE_REPLY = (
    "Hi Gyan,\n"
    "\n"
    "I am the Document Shepherd. Thanks for your review.\n"
    "\n"
    "On Thu, Oct 23, 2025 at 2:19 AM Gyan Mishra <gyan@example.com> wrote:\n"
    "\n"
    "> First question about the draft.\n"
    ">\n"
    "Dhruv: This sentence\n"
    "\n"
    "> A quoted sentence from the draft.\n"
    "\n"
    "simply clarifies that the new metric types are not limited.\n"
    "\n"
    "\n"
    "> Second question about section 5.2.\n"
    ">\n"
    "\n"
    "Dhruv: Section 5.2 only restates the existing number space.\n"
    "\n"
    "Thanks!\n"
    "Dhruv\n"
    "\n"
    "> Trailing quoted text.\n"
)


def test_inline_reply_has_one_part_per_answer():
    result = extract_new_text(_INLINE_REPLY)
    lines = result.text.split("\n")
    assert [lines[i] for i in result.part_starts] == [
        "Dhruv: This sentence",
        "simply clarifies that the new metric types are not limited.",
        "Dhruv: Section 5.2 only restates the existing number space.",
    ]


def test_inline_reply_parts_survive_cleaning():
    result = extract_new_text(_INLINE_REPLY)
    cleaned = clean_for_scoring(result.text, part_starts=result.part_starts)
    assert cleaned.parts == [
        "I am the Document Shepherd. Thanks for your review.",
        "Dhruv: This sentence",
        "simply clarifies that the new metric types are not limited.",
        "Dhruv: Section 5.2 only restates the existing number space.",
    ]
    # The text itself is unaffected by the part boundaries.
    assert cleaned.text == clean_for_scoring(result.text).text


def test_top_posted_reply_is_one_part():
    body = (
        "First paragraph of the reply.\n"
        "\n"
        "Second paragraph of the reply.\n"
        "\n"
        "On Mon, Jan 5, 2026 at 9:00 AM A Person <a@example.com> wrote:\n"
        "> quoted\n"
    )
    result = extract_new_text(body)
    assert result.status == "ok"
    assert result.part_starts == ()
    assert clean_for_scoring(result.text, part_starts=result.part_starts).parts == [result.text]


def test_blank_lines_alone_never_begin_a_part():
    result = extract_new_text("One.\n\n\n\nTwo.\n")
    assert result.part_starts == ()


def test_parent_diff_removal_begins_a_part():
    parent = (
        "The working group should adopt the proposal as written today.\n"
        "\n"
        "There are two open issues that need resolution before last call.\n"
    )
    body = (
        "The working group should adopt the proposal as written today.\n"
        "\n"
        "I agree with adopting it.\n"
        "\n"
        "There are two open issues that need resolution before last call.\n"
        "\n"
        "Both issues are already tracked.\n"
    )
    result = extract_new_text(body, parent)
    assert "+parent-diff" in result.method
    lines = result.text.split("\n")
    assert [lines[i] for i in result.part_starts] == ["Both issues are already tracked."]


def test_html_inline_reply_parts_come_from_the_full_rendering():
    html = (
        "<div>Answer one here.</div>"
        "<blockquote type=cite><div>quoted question one</div></blockquote>"
        "<div>Answer two here.</div>"
        "<blockquote><div>quoted question two</div></blockquote>"
        "<div>Answer three here.</div>"
    )
    result = extract_new_text(None, html_body=html)
    assert result.method.startswith("html-")
    lines = result.text.split("\n")
    assert [lines[i] for i in result.part_starts] == ["Answer two here.", "Answer three here."]


def test_unaligned_text_is_one_part():
    # The author's prefix cut before a mid-line divider has no equal line in
    # the body; the alignment is abandoned.
    body = "Reply text.\n\n> q\n\nMore text.-----Original Message-----From: x\n"
    result = extract_new_text(body)
    assert result.text == "Reply text.\n\n\nMore text."
    assert result.part_starts == ()


def test_empty_and_failed_results_have_no_parts():
    assert extract_new_text("").part_starts == ()
    assert extract_new_text("> only quoted\n").part_starts == ()


# --- generation 10: attribution lines and the sign-off boundary ------------------


def test_signoff_before_attribution_above_quote_keeps_inline_replies():
    # The attribution introduces ">"-quoted text: it heads an interleaved
    # quote, not an unmarked quoted thread, so the inline answers stay.
    lines = [
        "Thanks Bob.",
        "",
        "Best,",
        "Ana",
        "",
        "On Mon, 2 Mar 2026, Bob <bob@example.org> wrote:",
        "",
        "> Question one?",
        "Answer one.",
        "> Question two?",
        "Answer two.",
    ]
    assert find_signoff_boundary(lines) is None
    result = extract_new_text("\n".join(lines))
    assert result.text == "Thanks Bob.\n\nBest,\nAna\n\n\nAnswer one.\nAnswer two."


def test_signoff_before_attribution_above_unmarked_text_truncates():
    lines = [
        "Thanks Bob.",
        "",
        "Best,",
        "Ana",
        "",
        "On Mon, 2 Mar 2026, Bob <bob@example.org> wrote:",
        "Question one?",
        "> an older quote",
    ]
    assert find_signoff_boundary(lines) == 4


def test_attribution_after_dashes():
    assert is_attribution_line("--On Mon, 2 Mar 2026, Bob Smith wrote:")
    assert not is_attribution_line("-On Mon, 2 Mar 2026, Bob Smith wrote:")
    assert not is_attribution_line("---On Mon, 2 Mar 2026, Bob Smith wrote:")


def test_wrapped_attribution_after_quote_markers_and_dashes():
    assert strip_attribution_lines(
        [">> On Mon, 2 Mar 2026 at 10:15, Bob Smith", "<bob@example.org> wrote:", "kept"]
    ) == ["kept"]
    assert strip_attribution_lines(
        ["--On Monday, 2 March 2026 10:15 -0500 Bob Smith", "<bob@example.org> wrote:", "kept"]
    ) == ["kept"]


def test_wrapped_attribution_spans_at_most_four_lines():
    head = ["On Monday, 2 March 2026 10:15", "Bob Smith", "<bob@example.org>", "at the office"]
    assert strip_attribution_lines([*head, "wrote:", "kept"]) == ["kept"]
    lines = [*head, "and elsewhere", "wrote:", "kept"]
    assert strip_attribution_lines(lines) == lines


def test_wrapped_attribution_holds_at_most_400_characters():
    start = "On Monday, 2 March 2026, Bob Smith"
    filler = "x" * (400 - len(start) - len(" wrote:"))
    assert strip_attribution_lines([start, filler + " wrote:", "kept"]) == ["kept"]
    lines = [start, filler + "x wrote:", "kept"]
    assert strip_attribution_lines(lines) == lines


def test_wrapped_attribution_never_ends_at_a_complete_attribution():
    # "On Monday," does not open a wrapped attribution whose tail is a
    # complete "On … wrote:" line of its own; that line is removed by itself.
    lines = ["On Monday,", "On Mon, 2 Mar 2026, Bob Smith wrote:", "kept"]
    assert strip_attribution_lines(lines) == ["On Monday,", "kept"]


# --- generation 10: generic attribution lines ------------------------------------


@pytest.mark.parametrize(
    "attribution",
    [
        # Cue: an e-mail address.
        "Le 3 mars 2026 à 10:15, Ana Lima <ana@example.org> a écrit :",
        "Ana Lima <ana@example.org> on the list:",
        # Cue: a clock time and a date.
        "Monday 2 March 2026 at 10:15, Ana Lima:",
        "02.03. 19h30, Ana Lima:",
        "2026年3月2日 10:15 Ana Lima：",
        # Speech verbs.
        "Ana Lima said:",
        "I wrote:",
        "Bob Jones schreibt:",
        "Ana Lima and Bob Jones both clearly said:",
    ],
)
def test_generic_attribution_above_a_quote_is_removed(attribution):
    body = f"My answer.\n\n{attribution}\n> The quoted line.\nMy second answer."
    assert extract_new_text(body).text == "My answer.\n\nMy second answer."


@pytest.mark.parametrize(
    "lead_in",
    [
        "the following:",
        "Section 2.3 says:",
        "Section 2.3 of the draft said:",
        "OLD:",
        "NEW:",
        "What the draft said:",
        "It said:",
        "* Ana said:",
        "Ana Lima and Bob Jones both had clearly said:",  # 9 words
        "2 March 2026, Ana Lima:",  # a date without a clock time
        "Ana <ana@example.org> pointed to https://example.org/x:",
        "Ana <ana@example.org> proposed as follows:",
        "Ana <ana@example.org> wrote the following:",
    ],
)
def test_lead_in_above_a_quote_stays(lead_in):
    body = f"My answer.\n\n{lead_in}\n> The quoted line.\nMy second answer."
    assert extract_new_text(body).text == f"My answer.\n\n{lead_in}\nMy second answer."


def test_generic_attribution_cue_word_limit():
    words = " ".join(f"w{k}" for k in range(23))
    kept = f"{words} x <ana@example.org>:"  # 25 words
    assert extract_new_text(f"{kept}\n> q\nAnswer.").text == "Answer."
    over = f"{words} x y <ana@example.org>:"  # 26 words
    assert extract_new_text(f"{over}\n> q\nAnswer.").text == f"{over}\nAnswer."


def test_generic_attribution_character_limit():
    head = "Ana Lima <ana@example.org> "
    line = head + "x" * (400 - len(head) - 1) + ":"
    assert len(line) == 400
    assert extract_new_text(f"{line}\n> q\nAnswer.").text == "Answer."
    line = head + "x" * (400 - len(head)) + ":"
    assert extract_new_text(f"{line}\n> q\nAnswer.").text == f"{line}\nAnswer."


def test_generic_attribution_allows_one_blank_line_before_the_quote():
    assert extract_new_text("Ana Lima said:\n\n> q\nAnswer.").text == "Answer."
    body = "Ana Lima said:\n\n\n> q\nAnswer."
    assert extract_new_text(body).text == "Ana Lima said:\n\n\nAnswer."


def test_generic_attribution_without_a_quote_below_stays():
    body = "Ana Lima <ana@example.org>:\nUnmarked text."
    assert extract_new_text(body).text == body


def test_generic_attribution_unit_wraps_over_three_lines():
    # "from ana" and the two lines below it start lowercase or with a non-letter,
    # so each continues the line above it; the unit stops after three lines.
    body = (
        "Intro text.\n\nPlease see 1 reply\nfrom ana\n<ana@example.org>\non 2 March 2026:\n> quoted"
    )
    assert extract_new_text(body).text == "Intro text.\n\nPlease see 1 reply"


def test_generic_attribution_unit_joins_an_attribution_opener():
    # "Am …" without closing punctuation opens the unit; a line ending in "."
    # does not.
    body = "Text.\n\nAm Montag, 2. März 2026 um 10:15\nAna Lima <ana@example.org>:\n> q"
    assert extract_new_text(body).text == "Text."
    body = "Text.\n\nWe met on Monday.\nAna Lima <ana@example.org>:\n> q"
    assert extract_new_text(body).text == "Text.\n\nWe met on Monday."


def test_generic_attribution_ends_the_text_at_a_signoff():
    # A generic attribution with no quote line below it proves a sign-off
    # boundary.
    lines = [
        "Reply.",
        "",
        "Best,",
        "Ana",
        "",
        "Bob Jones <bob@example.org> said on Monday:",
        "Unmarked quoted text.",
    ]
    assert find_signoff_boundary(lines) == 4
    assert extract_new_text("\n".join(lines)).text == "Reply.\n\nBest,\nAna"


def test_generic_attribution_above_a_quote_does_not_end_the_text_at_a_signoff():
    body = "Reply.\n\nBest,\nAna\n\nBob Jones <bob@example.org> said on Monday:\n> q\nLate answer."
    assert extract_new_text(body).text == "Reply.\n\nBest,\nAna\n\nLate answer."


# --- generation 10: wrap remainders -----------------------------------------------

_LONG_QUOTE = "> The working group discussed the registry policy and reached a rough"


@pytest.mark.parametrize("remainder", ["consensus", "consensus on"])
def test_wrap_remainder_between_quote_lines_is_removed(remainder):
    body = f"Answer above.\n{_LONG_QUOTE}\n{remainder}\n> on the next point.\nAnswer below."
    assert extract_new_text(body).text == "Answer above.\nAnswer below."


@pytest.mark.parametrize(
    "line",
    [
        "consensus is good",  # three words
        "Consensus",  # uppercase
        "agreed",  # an answer word
        "ok then",
        "https://example.org",
    ],
)
def test_short_answer_between_quote_lines_stays(line):
    body = f"{_LONG_QUOTE}\n{line}\n> on the next point."
    assert extract_new_text(body).text == line


def test_wrap_remainder_needs_a_long_open_quote_line_above():
    above = "> " + "x" * 58  # 60 characters
    assert extract_new_text(f"{above}\nrest\n> q").status == "empty"
    above = "> " + "x" * 57  # 59 characters
    assert extract_new_text(f"{above}\nrest\n> q").text == "rest"
    assert extract_new_text(f"{_LONG_QUOTE}.\nrest\n> q").text == "rest"


def test_wrap_remainder_needs_a_quote_line_below():
    assert extract_new_text(f"{_LONG_QUOTE}\nconsensus\nMore.").text == "consensus\nMore."


# --- generation 10: dividers ---------------------------------------------------


def test_divider_inside_a_quote_does_not_truncate():
    lines = ["My reply.", "", "> -----Original Message-----", "> From: Bob", "My inline answer."]
    assert strip_after_original_message_divider(lines) == lines
    assert extract_new_text("\n".join(lines)).text == "My reply.\n\nMy inline answer."


@pytest.mark.parametrize(
    "divider",
    [
        "------------------ Original ------------------",
        "------------------&nbsp;Original&nbsp;------------------",
        "--- Original Messages ---",
        "-- Original Message --",
        "------------------ 邮件原文 ------------------",
    ],
)
def test_short_original_divider_truncates(divider):
    lines = ["My reply.", "", divider, "From: Bob", "Old text."]
    assert strip_after_original_message_divider(lines) == ["My reply.", ""]


def test_bare_original_line_above_from_truncates():
    lines = ["My reply.", "", "Original", "", "From: Bob <bob@example.org>", "Old text."]
    assert strip_after_original_message_divider(lines) == ["My reply.", ""]
    lines = ["My reply.", "", "Original", "", "Old text."]
    assert strip_after_original_message_divider(lines) == lines


def test_bare_original_line_above_a_quote_header_is_dropped():
    body = (
        "My reply.\n\nOriginal\nFrom: Bob <bob@example.org>\n"
        "Date: Mon, 2 Mar 2026 10:15\nTo: list@example.org\nSubject: Re: plan\n\nOld text."
    )
    assert extract_new_text(body).text == "My reply."


def test_short_divider_below_an_inline_note_is_ignored():
    lines = [
        "My answers are inline below, tagged [AL].",
        "",
        "------------------ Original ------------------",
        "From: Bob",
        "Question?",
        "[AL] Answer.",
    ]
    assert strip_after_original_message_divider(lines) == lines
    lines[2] = "-----Original Message-----"
    assert strip_after_original_message_divider(lines) == lines[:2]


def test_outlook_boundary_line_is_separated_from_the_line_above():
    assert custom_clean("Reply text\n__________\nmore") == "Reply text\n\n__________\nmore"
    assert custom_clean("Reply text\n -------\nmore") == "Reply text\n\n -------\nmore"
    # Six characters, two leading spaces, or a blank line above: unchanged.
    assert custom_clean("Reply text\n______\nmore") == "Reply text\n______\nmore"
    assert custom_clean("Reply text\n  _______\nmore") == "Reply text\n  _______\nmore"
    assert custom_clean("Reply text\n\n_______\nmore") == "Reply text\n\n_______\nmore"


def test_nul_is_removed_by_normalization():
    assert normalize_body("a\x00b\n\x00c") == "ab\nc"
    assert extract_new_text("\x00Hello there.\n").text == "Hello there."


# --- generation 10: parent-diff marking mode -------------------------------------

_MM_PARENT_LINES = [
    "Hi all,",
    "",
    "Here is the proposed change for the registry section of the draft:",
    "",
    "OLD: The registry is maintained by the working group chairs for now.",
    "NEW: The registry is maintained by the designated experts from now on.",
    "",
    "Please send comments before the end of the week to the list.",
    "",
    "> An earlier participant asked whether the registry needs a new policy.",
    "> The same participant also asked about the expiry of old entries.",
    "",
    "Bob",
]
_MM_PARENT = "\n".join(_MM_PARENT_LINES)


def _quoted_parent(lines: list[str]) -> str:
    return "\n".join(
        ">" + (" " + line if line and not line.startswith(">") else line) for line in lines
    )


def test_marking_mode_keeps_a_pasted_old_new_block():
    child = (
        "I support the change. For the record, the text would then read:\n\n"
        "OLD: The registry is maintained by the working group chairs for now.\n"
        "NEW: The registry is maintained by the designated experts from now on.\n\n"
        "Ana\n\nOn Mon, 2 Mar 2026, Bob <bob@example.org> wrote:\n"
        + _quoted_parent(_MM_PARENT_LINES)
    )
    result = extract_new_text(child, _MM_PARENT)
    assert "OLD: The registry" in result.text
    assert "NEW: The registry" in result.text


def test_marking_mode_is_off_with_fewer_than_three_deepened_lines():
    # Only two quote lines of at least four words appear in the parent at a
    # smaller depth: the pasted block is parent content and is removed.
    quoted = [
        "> Here is the proposed change for the registry section of the draft:",
        "> Please send comments before the end of the week to the list.",
    ]
    child = (
        "I support the change. For the record, the text would then read:\n\n"
        "OLD: The registry is maintained by the working group chairs for now.\n"
        "NEW: The registry is maintained by the designated experts from now on.\n\n"
        "Ana\n\nOn Mon, 2 Mar 2026, Bob <bob@example.org> wrote:\n" + "\n".join(quoted)
    )
    result = extract_new_text(child, _MM_PARENT)
    assert result.method == "custom+parent-diff"
    assert "OLD: The registry" not in result.text
    quoted.append("> Hi all, here is the proposed change.")  # not in the parent
    result = extract_new_text(child + "\n" + quoted[-1], _MM_PARENT)
    assert "OLD: The registry" not in result.text
    quoted.append(">> An earlier participant asked whether the registry needs a new policy.")
    result = extract_new_text(child + "\n" + "\n".join(quoted[-2:]), _MM_PARENT)
    assert "OLD: The registry" in result.text  # the third deepened line


def test_marking_mode_keeps_a_resent_paragraph():
    child = (
        "Re-sending the request, since nobody answered it:\n\n"
        "Please send comments before the end of the week to the list.\n\n"
        "On Mon, 2 Mar 2026, Bob <bob@example.org> wrote:\n" + _quoted_parent(_MM_PARENT_LINES)
    )
    result = extract_new_text(child, _MM_PARENT)
    assert result.text == (
        "Re-sending the request, since nobody answered it:\n\n"
        "Please send comments before the end of the week to the list."
    )


def test_marking_mode_still_removes_an_unmarked_line_inside_a_quoted_block():
    child = (
        "Two answers below.\n\n"
        "> Here is the proposed change for the registry section of the draft:\n"
        "OLD: The registry is maintained by the working group chairs for now.\n"
        "> NEW: The registry is maintained by the designated experts from now on.\n"
        "Fine with me.\n\n"
        "> Please send comments before the end of the week to the list.\n"
        ">> An earlier participant asked whether the registry needs a new policy.\n"
    )
    result = extract_new_text(child, _MM_PARENT)
    assert result.text == "Two answers below.\n\nFine with me."
    assert result.method == "custom+parent-diff"


def test_marking_mode_stops_at_an_attribution_above_unmarked_text():
    # The unmarked copy below "Bob wrote:" (no ">" below the attribution) is
    # the client's reproduction of the parent, not the author's.
    child = (
        "> Here is the proposed change for the registry section of the draft:\n"
        "> Please send comments before the end of the week to the list.\n"
        ">> An earlier participant asked whether the registry needs a new policy.\n"
        "Agreed.\n\n"
        "Bob Jones <bob@example.org> wrote:\n\n"
        "OLD: The registry is maintained by the working group chairs for now.\n"
        "NEW: The registry is maintained by the designated experts from now on.\n"
    )
    result = extract_new_text(child, _MM_PARENT)
    assert result.text == "Agreed."
    assert result.method == "custom+parent-diff"


def _point_by_point(paragraphs: int) -> tuple[str, str]:
    """A parent of ``paragraphs`` three-line paragraphs, and a child that
    copies each one unmarked and answers it, above a marked quotation."""
    paras = [
        [f"Paragraph {k} line {j} carries enough words to count as parent text." for j in range(3)]
        for k in range(paragraphs)
    ]
    parent = "\n\n".join("\n".join(p) for p in paras)
    child = "\n\n".join("\n".join(p) + f"\n\nAnswer {k}." for k, p in enumerate(paras))
    child += "\n\nOn Mon, 2 Mar 2026, Bob <bob@example.org> wrote:\n" + "\n".join(
        "> " + p[0] for p in paras[:3]
    )
    return child, parent


def test_marking_mode_many_runs_lose_protection():
    child, parent = _point_by_point(8)
    result = extract_new_text(child, parent)
    assert result.text == "\n\n".join(f"Answer {k}." for k in range(8))
    child, parent = _point_by_point(7)
    result = extract_new_text(child, parent)
    assert "Paragraph 6 line 2" in result.text
    assert result.method == "custom"
