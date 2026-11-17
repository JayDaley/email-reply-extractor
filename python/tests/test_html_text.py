"""Unit tests for the HTML-as-oracle helper (:mod:`email_reply_extractor.html_text`).

Cover the block-structure conversion, entity handling, skipped script/style/head,
the blockquote / gmail_quote / signature container splits (including a signature
nested inside a quote, which counts as quoted), the composed-signature
containers, the Apple Mail Share-sheet container, and defensive handling of malformed markup — unclosed tags, stray
closes, and empty/garbage input never raise.
"""

from __future__ import annotations

import pytest

from email_reply_extractor.html_text import HtmlParts, html_to_text, split_html_parts


# --- block conversion ---------------------------------------------------------


def test_paragraphs_and_divs_become_separate_lines():
    # A block's open and close each break the line, so adjacent blocks are
    # separated by a blank line (collapsed blank runs). ``<br>`` breaks singly.
    html = "<p>First para.</p><div>Second block.</div>"
    assert html_to_text(html) == "First para.\n\nSecond block."


def test_br_forces_a_single_line_break():
    assert html_to_text("<div>one<br>two<br>three</div>") == "one\ntwo\nthree"


def test_list_items_and_table_rows_break():
    html = "<ul><li>alpha</li><li>beta</li></ul><table><tr>r1</tr><tr>r2</tr></table>"
    assert html_to_text(html) == "alpha\n\nbeta\n\nr1\n\nr2"


def test_inline_whitespace_is_collapsed_per_line():
    html = "<div>  lots   of\n\tspace   here </div>"
    assert html_to_text(html) == "lots of space here"


def test_blank_line_runs_collapse_and_edges_trimmed():
    html = "<div><br><br></div><p>content</p><div><br></div>"
    assert html_to_text(html) == "content"


# --- entities -----------------------------------------------------------------


def test_entities_are_unescaped():
    assert html_to_text("<p>a &amp; b &lt; c &gt; d &nbsp;e</p>") == "a & b < c > d e"


def test_numeric_char_refs_unescaped():
    assert html_to_text("<p>&#233;&#x2014;</p>") == "é—"


# --- skipped regions ----------------------------------------------------------


def test_script_and_style_content_is_dropped():
    html = "<style>.x{color:red}</style><div>visible</div><script>var y = 1 < 2;</script>"
    assert html_to_text(html) == "visible"


def test_head_content_is_dropped():
    html = "<head><title>hidden title</title></head><body><p>body text</p></body>"
    assert html_to_text(html) == "body text"


# --- split: blockquote / gmail_quote / signature ------------------------------


def test_blockquote_is_quoted_rest_is_novel():
    html = "<div>my reply</div><blockquote><div>their words</div></blockquote>"
    parts = split_html_parts(html)
    assert parts.novel_text == "my reply"
    assert parts.quoted_text == "their words"
    assert parts.signature_text == ""


@pytest.mark.parametrize(
    "attr",
    [
        'class="gmail_quote"',
        'class="gmail_quote gmail_quote_container"',
        'class="gmail_attr"',
        'class="moz-cite-prefix"',
        'class="OutlookMessageHeader"',
        'id="divRplyFwdMsg"',
        'id="appendonsend"',
    ],
)
def test_quote_container_hooks(attr):
    html = f"<div>mine</div><div {attr}>quoted stuff</div>"
    parts = split_html_parts(html)
    assert parts.novel_text == "mine"
    assert "quoted stuff" in parts.quoted_text


@pytest.mark.parametrize(
    "html",
    [
        '<div>body</div><div class="gmail_signature">my sig</div>',
        '<div>body</div><div class="moz-signature">my sig</div>',
        '<div>body</div><div id="Signature">my sig</div>',
    ],
)
def test_signature_container_hooks(html):
    parts = split_html_parts(html)
    assert parts.novel_text == "body"
    assert parts.signature_text == "my sig"
    assert parts.quoted_text == ""


def test_signature_inside_quote_counts_as_quoted():
    # A quoted message's own signature belongs to the quote, not to this author.
    html = (
        "<div>my reply</div>"
        "<blockquote><div>their words</div>"
        '<div class="gmail_signature">their sig</div></blockquote>'
        '<div class="gmail_signature">my sig</div>'
    )
    parts = split_html_parts(html)
    assert parts.novel_text == "my reply"
    assert "their words" in parts.quoted_text
    assert "their sig" in parts.quoted_text
    assert parts.signature_text == "my sig"


def test_nested_quotes_all_count_as_quoted():
    html = "<blockquote>outer<blockquote>inner</blockquote>more outer</blockquote>"
    parts = split_html_parts(html)
    assert parts.novel_text == ""
    for token in ("outer", "inner", "more outer"):
        assert token in parts.quoted_text


def test_gmail_wrapper_with_blockquote_keeps_inline_replies_novel():
    # Gmail's inline-reply shape: the author's paragraphs are direct children of
    # the wrapper, between the blockquotes. Only the blockquotes and the
    # attribution line are quoted.
    html = (
        '<div dir="ltr">'
        '<div class="gmail_attr">On Mon, X wrote:</div>'
        '<div class="gmail_quote gmail_quote_container">'
        '<blockquote class="gmail_quote">their first point</blockquote>'
        "<div>my inline answer</div>"
        '<blockquote class="gmail_quote">their second point</blockquote>'
        "<div>my other answer</div>"
        "<div>-Me</div>"
        "</div></div>"
    )
    parts = split_html_parts(html)
    for token in ("my inline answer", "my other answer", "-Me"):
        assert token in parts.novel_text
    for token in ("their first point", "their second point", "On Mon, X wrote:"):
        assert token in parts.quoted_text


def test_gmail_wrapper_without_blockquote_stays_quoted():
    # Gmail's forward shape: the forwarded body sits directly in the wrapper.
    html = (
        "<div>see below</div>"
        '<div class="gmail_quote">---------- Forwarded message ---------'
        "<br>From: someone<br>the forwarded body</div>"
    )
    parts = split_html_parts(html)
    assert parts.novel_text == "see below"
    assert "the forwarded body" in parts.quoted_text


def test_gmail_wrapper_inside_a_blockquote_stays_quoted():
    # A deeper reply's wrapper is inside this message's blockquote: all quoted.
    html = (
        "<div>mine</div>"
        "<blockquote>"
        '<div class="gmail_quote"><blockquote>deep</blockquote><div>theirs inline</div></div>'
        "</blockquote>"
    )
    parts = split_html_parts(html)
    assert parts.novel_text == "mine"
    for token in ("deep", "theirs inline"):
        assert token in parts.quoted_text


def test_independent_gmail_wrappers_are_classified_per_instance():
    # One interleaved wrapper (transparent) and one forward wrapper (quoted).
    html = (
        '<div class="gmail_quote"><blockquote>quoted</blockquote><div>inline reply</div></div>'
        '<div class="gmail_quote">forwarded body</div>'
    )
    parts = split_html_parts(html)
    assert "inline reply" in parts.novel_text
    assert "quoted" in parts.quoted_text
    assert "forwarded body" in parts.quoted_text


# --- Apple Mail Share-sheet container -----------------------------------------

_SHARE_TOP = '<div class="Apple-Mail-URLShareUserContentTopClass"><br></div>'
_SHARE_OPEN = (
    '<div class="Apple-Mail-URLShareWrapperClass" style="position: relative !important;">'
    '<blockquote type="cite" style="border-left-style: none;">'
)
_SHARE_CLOSE = "</blockquote></div>"


def test_share_container_blockquote_is_novel():
    # The Share sheet wraps the author's own text in a cite blockquote whose
    # parent carries the wrapper class: that text is novel, not quoted.
    html = (
        f'<div dir="auto">{_SHARE_TOP}{_SHARE_OPEN}Shared text.<br>Second line.{_SHARE_CLOSE}</div>'
    )
    parts = split_html_parts(html)
    assert parts.novel_text == "Shared text.\nSecond line."
    assert parts.quoted_text == ""
    assert parts.has_share_container is True


def test_share_container_nested_blockquote_stays_quoted():
    html = (
        f"{_SHARE_OPEN}My answer.<br>On Monday, Someone wrote:"
        '<blockquote type="cite">their earlier text</blockquote>'
        f"More of my answer.{_SHARE_CLOSE}"
    )
    parts = split_html_parts(html)
    assert parts.novel_text == "My answer.\nOn Monday, Someone wrote:\n\nMore of my answer."
    assert parts.quoted_text == "their earlier text"
    assert parts.has_share_container is True


def test_share_wrapper_with_several_blockquote_children():
    # Every blockquote that is a direct child of the wrapper is a container.
    html = (
        '<div class="Apple-Mail-URLShareWrapperClass">'
        "<blockquote>first</blockquote><blockquote>second</blockquote></div>"
    )
    parts = split_html_parts(html)
    assert parts.novel_text == "first\n\nsecond"
    assert parts.quoted_text == ""


def test_share_wrapper_class_among_other_tokens():
    html = '<div class="x Apple-Mail-URLShareWrapperClass y"><blockquote>mine</blockquote></div>'
    parts = split_html_parts(html)
    assert parts.novel_text == "mine"
    assert parts.has_share_container is True


@pytest.mark.parametrize(
    "html",
    [
        # Not the parent: an element sits between the wrapper and the blockquote.
        '<div class="Apple-Mail-URLShareWrapperClass"><div><blockquote>q</blockquote></div></div>',
        # An unclosed element is still open, so it is the parent.
        '<div class="Apple-Mail-URLShareWrapperClass"><p>intro<blockquote>q</blockquote></div>',
        # The class token must match exactly.
        '<div class="apple-mail-urlsharewrapperclass"><blockquote>q</blockquote></div>',
        '<div class="Apple-Mail-URLShareWrapperClassX"><blockquote>q</blockquote></div>',
        # The user-content class is not the wrapper class.
        '<div class="Apple-Mail-URLShareUserContentTopClass"><blockquote>q</blockquote></div>',
        # The wrapper has closed before the blockquote opens.
        '<div class="Apple-Mail-URLShareWrapperClass"></div><blockquote>q</blockquote>',
    ],
)
def test_blockquote_not_directly_under_share_wrapper_is_quoted(html):
    parts = split_html_parts(html)
    assert parts.quoted_text == "q"
    assert parts.has_share_container is False


def test_share_wrapper_void_tag_between_does_not_break_parenthood():
    # Void elements are never open, so a <br> before the blockquote leaves the
    # wrapper as its parent.
    html = '<div class="Apple-Mail-URLShareWrapperClass"><br><blockquote>mine</blockquote></div>'
    parts = split_html_parts(html)
    assert parts.novel_text == "mine"
    assert parts.has_share_container is True


def test_ordinary_cite_blockquote_is_still_quoted():
    html = '<div>my reply</div><blockquote type="cite">their text</blockquote>'
    parts = split_html_parts(html)
    assert parts.novel_text == "my reply"
    assert parts.quoted_text == "their text"
    assert parts.has_share_container is False


def test_quoted_share_container_stays_quoted_and_is_not_reported():
    # A reply quoting a Share-sheet message: the share container sits inside
    # this message's own quote, so its text is quoted and the flag is unset.
    html = (
        "<div>my reply</div>"
        f'<blockquote type="cite">On Monday, Someone wrote:{_SHARE_OPEN}their shared text'
        f"{_SHARE_CLOSE}</blockquote>"
    )
    parts = split_html_parts(html)
    assert parts.novel_text == "my reply"
    assert "their shared text" in parts.quoted_text
    assert parts.has_share_container is False


def test_share_container_in_head_is_not_reported():
    html = (
        f"<html><head>{_SHARE_OPEN}hidden{_SHARE_CLOSE}</head>"
        "<body><div>visible</div></body></html>"
    )
    parts = split_html_parts(html)
    assert parts.novel_text == "visible"
    assert parts.has_share_container is False


def test_unclosed_share_container_does_not_raise():
    html = f"<div>typed in Mail</div>{_SHARE_OPEN}shared text with no close"
    parts = split_html_parts(html)
    assert parts.novel_text == "typed in Mail\n\nshared text with no close"
    assert parts.has_share_container is True


# --- composed-signature containers --------------------------------------------


def test_composed_signature_below_the_reply_is_a_signature():
    html = (
        "<div>Thanks, see below.</div>"
        '<div id="ms-outlook-mobile-signature">Sent from Outlook for iOS</div>'
    )
    parts = split_html_parts(html)
    assert parts.novel_text == "Thanks, see below."
    assert parts.signature_text == "Sent from Outlook for iOS"


def test_reply_typed_into_a_composed_signature_is_novel():
    html = (
        '<div id="composer_signature"><div>Line one of the reply.</div>'
        "<div>Line two of the reply.</div><div>Sent from my phone</div></div>"
    )
    parts = split_html_parts(html)
    assert (
        parts.novel_text == "Line one of the reply.\n\nLine two of the reply.\n\nSent from my phone"
    )
    assert parts.signature_text == ""


@pytest.mark.parametrize(("above", "category"), [(2, "signature"), (1, "novel")])
def test_composed_signature_compares_with_the_text_above_it(above, category):
    # Two signature lines: a signature when the author's text above holds at
    # least as many lines, novel text when it holds fewer.
    reply = "".join(f"<div>Reply line {k}.</div>" for k in range(above))
    html = (
        reply + '<div class="protonmail_signature_block"><div>Ana Lima</div>'
        "<div>Sent with Proton Mail</div></div><div>Text after.</div><div>More after.</div>"
    )
    parts = split_html_parts(html)
    signature = "Ana Lima\n\nSent with Proton Mail"
    if category == "signature":
        assert parts.signature_text == signature
        assert "Ana Lima" not in parts.novel_text
    else:
        assert parts.signature_text == ""
        assert signature in parts.novel_text


def test_composed_signature_inside_a_quote_or_signature_keeps_that_category():
    html = '<blockquote><div id="composer_signature">Quoted sig</div></blockquote><p>Hi</p>'
    parts = split_html_parts(html)
    assert parts.quoted_text == "Quoted sig"
    assert parts.novel_text == "Hi"
    html = '<p>Hi</p><div class="gmail_signature"><div id="composer_signature">S1</div></div>'
    parts = split_html_parts(html)
    assert parts.signature_text == "S1"
    assert parts.novel_text == "Hi"


# --- malformed markup ---------------------------------------------------------


def test_unclosed_tags_do_not_raise_and_still_split():
    html = "<div>novel text<blockquote>quoted text with no close"
    parts = split_html_parts(html)
    assert "novel text" in parts.novel_text
    assert "quoted text with no close" in parts.quoted_text


def test_stray_close_tags_are_ignored():
    # A close with no matching open must not corrupt the depth counters.
    html = "</blockquote></div><div>still novel</div></blockquote>"
    parts = split_html_parts(html)
    assert parts.novel_text == "still novel"
    assert parts.quoted_text == ""


def test_text_across_a_quote_does_not_glue():
    # Novel text on both sides of a quote stays on separate lines (the quote's
    # structural break flushes the line even though its text is a different part).
    html = "<div>before</div><blockquote>middle</blockquote><div>after</div>"
    parts = split_html_parts(html)
    assert [ln for ln in parts.novel_text.split("\n") if ln] == ["before", "after"]
    assert "before" not in parts.quoted_text and "after" not in parts.quoted_text


@pytest.mark.parametrize("bad", ["", "   ", "<<<>>> & & &", "<b><i>no closes", "plain no tags"])
def test_empty_or_garbage_never_raises(bad):
    assert isinstance(html_to_text(bad), str)
    result = split_html_parts(bad)
    assert isinstance(result, HtmlParts)


def test_empty_input_yields_empty_parts():
    assert split_html_parts("") == HtmlParts("", "", "")
    assert split_html_parts("").has_share_container is False
    assert html_to_text("") == ""
