//! HTML as an oracle: recover visible text and quote/signature structure from
//! the `text/html` part of a message.
//!
//! Port of the reference's `html_text.py`. [`html_to_text`] renders the
//! visible text with block structure as newlines; [`split_html_parts`]
//! renders the same text split into novel, quoted and signature streams by
//! the container each run of text falls inside. Both are built on a port of
//! Python's `html.parser.HTMLParser` tokenizer (the crate's `html_parser`
//! module) and never fail: malformed markup is handled the way the reference
//! handles it.
//!
//! Container rules:
//!
//! - **Quoted**: inside a `<blockquote>`; an element whose `class` list holds
//!   `gmail_attr`, `moz-cite-prefix` or `OutlookMessageHeader`; or whose `id`
//!   is `divRplyFwdMsg` or `appendonsend`. Quoted containers nest. Gmail's
//!   `gmail_quote` / `gmail_quote_container` wrapper is quoted only when it
//!   holds no `<blockquote>`; a wrapper holding blockquotes carries the
//!   author's inline replies as direct children.
//!   A `<blockquote>` whose parent element (the innermost open element when
//!   its start tag is read) carries the class `Apple-Mail-URLShareWrapperClass`
//!   (Apple Mail's Share sheet) is not a quoted container: it holds the
//!   author's own text. Blockquotes nested inside it are quoted.
//! - **Signature**: inside an element whose `class` list holds
//!   `gmail_signature`, `moz-signature` or `Signature`, or whose `id` is
//!   `Signature`, when not inside a quoted container.
//! - **Composed signature**: inside an element whose `class` list holds
//!   `protonmail_signature_block`, or whose `id` is
//!   `ms-outlook-mobile-signature` or `composer_signature`, when inside
//!   neither a quoted nor a signature container. The author can type into
//!   these containers, so [`split_html_parts`] renders their text as
//!   signature text when its non-blank lines number no more than the
//!   non-blank lines of novel text before the first of them, and as novel
//!   text otherwise.
//! - **Novel**: everything else.

use crate::html_parser::{self, Sink};
use crate::pystr;
use std::collections::{HashMap, HashSet};

#[derive(Clone, Copy, PartialEq, Eq, Debug)]
enum Category {
    Novel,
    Quoted,
    Signature,
    /// Inside a composed-signature container (see
    /// [`COMPOSED_SIGNATURE_CLASSES`]) and inside no quoted or signature one.
    Composed,
}

const BLOCK_TAGS: [&str; 15] = [
    "p",
    "div",
    "li",
    "tr",
    "table",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "blockquote",
    "pre",
    "ul",
    "ol",
];

const VOID_TAGS: [&str; 14] = [
    "area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source",
    "track", "wbr",
];

const SKIP_TAGS: [&str; 3] = ["script", "style", "head"];

const QUOTED_CLASSES: [&str; 3] = ["gmail_attr", "moz-cite-prefix", "OutlookMessageHeader"];
const GMAIL_WRAPPER_CLASSES: [&str; 2] = ["gmail_quote", "gmail_quote_container"];
const QUOTED_IDS: [&str; 2] = ["divRplyFwdMsg", "appendonsend"];
const SIGNATURE_CLASSES: [&str; 3] = ["gmail_signature", "moz-signature", "Signature"];
const SIGNATURE_ID: &str = "Signature";
/// `class` tokens and `id` values of signature containers the author can
/// type into: Proton Mail's `protonmail_signature_block`, Outlook mobile's
/// `ms-outlook-mobile-signature` and the `composer_signature` of Samsung and
/// other Android clients. Their category is decided per document (see
/// [`split_html_parts`]).
const COMPOSED_SIGNATURE_CLASSES: [&str; 1] = ["protonmail_signature_block"];
const COMPOSED_SIGNATURE_IDS: [&str; 2] = ["ms-outlook-mobile-signature", "composer_signature"];
/// `class` tokens of Apple Mail's Share-sheet wrapper. A `<blockquote>` whose
/// parent element (the innermost open element when its start tag is read)
/// carries one of these tokens is a *share container*: it contributes no
/// category of its own, so its direct text is novel. A `<blockquote>` nested
/// inside it is quoted as usual.
const SHARE_WRAPPER_CLASSES: [&str; 1] = ["Apple-Mail-URLShareWrapperClass"];

/// The visible text of an HTML part split by container category.
///
/// Each field is rendered with the same block-structure and whitespace rules
/// as [`html_to_text`], over only the runs of text in that category.
#[derive(Clone, Debug, Default, PartialEq, Eq, Hash)]
pub struct HtmlParts {
    /// Everything outside any quoted or signature container.
    pub novel_text: String,
    /// Everything inside a quoted container (nesting included).
    pub quoted_text: String,
    /// Everything inside a signature container that is not itself inside a
    /// quoted container.
    pub signature_text: String,
    /// `true` when the document holds an Apple Mail Share-sheet container (a
    /// `<blockquote>` whose parent carries `Apple-Mail-URLShareWrapperClass`)
    /// that is not inside a quoted container or a skipped (`head`) region.
    pub has_share_container: bool,
}

enum Segment {
    Newline,
    Text(Category, String),
}

/// `dict(attrs)`: the last value of a duplicated attribute wins.
fn attr<'a>(attrs: &'a [(String, Option<String>)], name: &str) -> Option<&'a str> {
    attrs
        .iter()
        .rev()
        .find(|(k, _)| k == name)
        .and_then(|(_, v)| v.as_deref())
}

/// `(d.get("class") or "").split()` and `d.get("id") or ""`.
fn class_and_id(attrs: &[(String, Option<String>)]) -> (Vec<&str>, &str) {
    let classes = attr(attrs, "class")
        .map(pystr::split_ws)
        .unwrap_or_default();
    let id = attr(attrs, "id").unwrap_or("");
    (classes, id)
}

// Both parsers keep a stack of open elements, and a close tag pops the nearest
// open frame with its tag together with every frame above it. Scanning the
// stack for that frame costs no more than the frames it pops, except for a
// stray close tag with no open frame, which would scan the whole stack and pop
// nothing (quadratic on "<a>" * n + "</b>" * n). Each parser therefore also
// counts its open frames per tag, and a close tag whose count is zero is
// ignored without a scan.

/// Add `delta` (+1 or -1) to the open-frame count of `tag` (`_count_open`).
/// A frame is only closed after it was opened, so a count never goes below
/// zero.
fn count_open(counts: &mut HashMap<String, usize>, tag: &str, delta: isize) {
    match counts.get_mut(tag) {
        Some(count) => *count = count.saturating_add_signed(delta),
        None => {
            counts.insert(tag.to_string(), delta.max(0).unsigned_abs());
        }
    }
}

/// `_close_frame`: index of the nearest frame on `stack` whose tag is `tag`,
/// or `None`.
fn close_frame<F>(
    stack: &[F],
    counts: &HashMap<String, usize>,
    tag: &str,
    frame_tag: impl Fn(&F) -> &str,
) -> Option<usize> {
    if counts.get(tag).copied().unwrap_or(0) == 0 {
        return None;
    }
    // Always found while the counts mirror the stack.
    stack.iter().rposition(|f| frame_tag(f) == tag)
}

struct Frame {
    tag: String,
    category: Option<Category>,
    is_skip: bool,
    is_share_wrapper: bool,
}

struct Segmenter {
    stack: Vec<Frame>,
    /// Number of frames on `stack` per tag (see [`close_frame`]).
    open_counts: HashMap<String, usize>,
    quoted_depth: usize,
    signature_depth: usize,
    composed_depth: usize,
    skip_depth: usize,
    interleaved_wrappers: HashSet<usize>,
    wrapper_count: usize,
    /// Set when a share container opens outside any quoted container and
    /// outside a skipped region (see [`SHARE_WRAPPER_CLASSES`]).
    has_share_container: bool,
    segments: Vec<Segment>,
}

impl Segmenter {
    fn active_category(&self) -> Category {
        if self.quoted_depth > 0 {
            Category::Quoted
        } else if self.signature_depth > 0 {
            Category::Signature
        } else if self.composed_depth > 0 {
            Category::Composed
        } else {
            Category::Novel
        }
    }

    fn container_category(&mut self, tag: &str, classes: &[&str], id: &str) -> Option<Category> {
        if tag == "blockquote" && self.stack.last().is_some_and(|f| f.is_share_wrapper) {
            // A Share-sheet container: the blockquote is the author's own
            // text, not a quote. Only the blockquote whose parent is the
            // wrapper is exempt; one nested inside it is quoted below.
            if self.quoted_depth == 0 && self.skip_depth == 0 {
                self.has_share_container = true;
            }
            return None;
        }
        if tag == "blockquote"
            || classes.iter().any(|c| QUOTED_CLASSES.contains(c))
            || QUOTED_IDS.contains(&id)
        {
            return Some(Category::Quoted);
        }
        if classes.iter().any(|c| GMAIL_WRAPPER_CLASSES.contains(c)) {
            let index = self.wrapper_count;
            self.wrapper_count += 1;
            return if self.interleaved_wrappers.contains(&index) {
                None
            } else {
                Some(Category::Quoted)
            };
        }
        if classes.iter().any(|c| SIGNATURE_CLASSES.contains(c)) || id == SIGNATURE_ID {
            return Some(Category::Signature);
        }
        if classes
            .iter()
            .any(|c| COMPOSED_SIGNATURE_CLASSES.contains(c))
            || COMPOSED_SIGNATURE_IDS.contains(&id)
        {
            return Some(Category::Composed);
        }
        None
    }
}

impl Sink for Segmenter {
    fn start_tag(&mut self, tag: &str, attrs: &[(String, Option<String>)]) {
        let tag = pystr::lower(tag);
        if SKIP_TAGS.contains(&tag.as_str()) {
            self.skip_depth += 1;
            count_open(&mut self.open_counts, &tag, 1);
            self.stack.push(Frame {
                tag,
                category: None,
                is_skip: true,
                is_share_wrapper: false,
            });
            return;
        }
        if VOID_TAGS.contains(&tag.as_str()) {
            if tag == "br" && self.skip_depth == 0 {
                self.segments.push(Segment::Newline);
            }
            return;
        }
        let (classes, id) = class_and_id(attrs);
        let category = self.container_category(&tag, &classes, id);
        let is_share_wrapper = classes.iter().any(|c| SHARE_WRAPPER_CLASSES.contains(c));
        match category {
            Some(Category::Quoted) => self.quoted_depth += 1,
            Some(Category::Signature) => self.signature_depth += 1,
            Some(Category::Composed) => self.composed_depth += 1,
            _ => {}
        }
        let is_block = BLOCK_TAGS.contains(&tag.as_str());
        count_open(&mut self.open_counts, &tag, 1);
        self.stack.push(Frame {
            tag,
            category,
            is_skip: false,
            is_share_wrapper,
        });
        if is_block && self.skip_depth == 0 {
            self.segments.push(Segment::Newline);
        }
    }

    fn end_tag(&mut self, tag: &str) {
        let tag = pystr::lower(tag);
        if VOID_TAGS.contains(&tag.as_str()) {
            return;
        }
        // Find the nearest matching open frame; ignore a stray close with none.
        let Some(idx) = close_frame(&self.stack, &self.open_counts, &tag, |f| &f.tag) else {
            return;
        };
        // Pop that frame and any unclosed inner frames, adjusting the counters.
        for frame in &self.stack[idx..] {
            count_open(&mut self.open_counts, &frame.tag, -1);
            if frame.is_skip {
                self.skip_depth -= 1;
            } else if frame.category == Some(Category::Quoted) {
                self.quoted_depth -= 1;
            } else if frame.category == Some(Category::Signature) {
                self.signature_depth -= 1;
            } else if frame.category == Some(Category::Composed) {
                self.composed_depth -= 1;
            }
        }
        self.stack.truncate(idx);
        if BLOCK_TAGS.contains(&tag.as_str()) && self.skip_depth == 0 {
            self.segments.push(Segment::Newline);
        }
    }

    fn data(&mut self, data: &str) {
        if self.skip_depth > 0 {
            return;
        }
        let category = self.active_category();
        self.segments
            .push(Segment::Text(category, data.to_string()));
    }
}

/// First pass: which Gmail wrapper instances hold a `<blockquote>`.
#[derive(Default)]
struct WrapperScanner {
    /// (tag, wrapper-instance-index-or-None) for every open element.
    stack: Vec<(String, Option<usize>)>,
    /// Number of frames on `stack` per tag (see [`close_frame`]).
    open_counts: HashMap<String, usize>,
    /// (stack position, wrapper index) of every open wrapper, bottom first.
    open_wrappers: Vec<(usize, usize)>,
    wrapper_count: usize,
    interleaved: HashSet<usize>,
}

impl Sink for WrapperScanner {
    fn start_tag(&mut self, tag: &str, attrs: &[(String, Option<String>)]) {
        let tag = pystr::lower(tag);
        if VOID_TAGS.contains(&tag.as_str()) {
            return;
        }
        if tag == "blockquote" {
            // Mark every open wrapper, from the innermost out. A marked
            // wrapper was marked by an earlier blockquote while every wrapper
            // below it (opened before it and still open) was open too, so
            // those are marked already and the walk stops there. Walking the
            // whole stack on every blockquote is quadratic.
            for &(_, index) in self.open_wrappers.iter().rev() {
                if !self.interleaved.insert(index) {
                    break;
                }
            }
            count_open(&mut self.open_counts, &tag, 1);
            self.stack.push((tag, None));
            return;
        }
        let (classes, id) = class_and_id(attrs);
        let is_quoted_hook =
            classes.iter().any(|c| QUOTED_CLASSES.contains(c)) || QUOTED_IDS.contains(&id);
        let mut index = None;
        if !is_quoted_hook && classes.iter().any(|c| GMAIL_WRAPPER_CLASSES.contains(c)) {
            index = Some(self.wrapper_count);
            self.open_wrappers
                .push((self.stack.len(), self.wrapper_count));
            self.wrapper_count += 1;
        }
        count_open(&mut self.open_counts, &tag, 1);
        self.stack.push((tag, index));
    }

    fn end_tag(&mut self, tag: &str) {
        let tag = pystr::lower(tag);
        if VOID_TAGS.contains(&tag.as_str()) {
            return;
        }
        let Some(idx) = close_frame(&self.stack, &self.open_counts, &tag, |f| &f.0) else {
            return;
        };
        for (frame_tag, _) in &self.stack[idx..] {
            count_open(&mut self.open_counts, frame_tag, -1);
        }
        self.stack.truncate(idx);
        while self
            .open_wrappers
            .last()
            .is_some_and(|&(pos, _)| pos >= idx)
        {
            self.open_wrappers.pop();
        }
    }

    fn data(&mut self, _data: &str) {}
}

fn interleaved_wrappers(html: &str) -> HashSet<usize> {
    let mut scanner = WrapperScanner::default();
    // The reference ignores a parser exception and keeps what it collected.
    let _ = html_parser::parse(html, &mut scanner);
    scanner.interleaved
}

/// Parse `html` into category-tagged segments; returns the segmenter, whose
/// `segments` and `has_share_container` hold the result.
fn segment(html: &str) -> Segmenter {
    let mut seg = Segmenter {
        stack: Vec::new(),
        open_counts: HashMap::new(),
        quoted_depth: 0,
        signature_depth: 0,
        composed_depth: 0,
        skip_depth: 0,
        interleaved_wrappers: interleaved_wrappers(html),
        wrapper_count: 0,
        has_share_container: false,
        segments: Vec::new(),
    };
    let _ = html_parser::parse(html, &mut seg);
    seg
}

/// Render `segments` to text, keeping only text runs whose category is in
/// `wanted` (`None` keeps every category).
fn render(segments: &[Segment], wanted: Option<&[Category]>) -> String {
    let mut lines: Vec<String> = Vec::new();
    let mut buf = String::new();
    for seg in segments {
        match seg {
            Segment::Newline => lines.push(std::mem::take(&mut buf)),
            Segment::Text(category, text) => {
                if wanted.map_or(true, |w| w.contains(category)) {
                    buf.push_str(text);
                }
            }
        }
    }
    lines.push(buf);

    let mut collapsed: Vec<String> = Vec::new();
    for line in lines {
        let line = pystr::collapse_ws(&line);
        if line.is_empty() && collapsed.last().is_some_and(|l| l.is_empty()) {
            continue;
        }
        collapsed.push(line);
    }
    trim_blank_edges(&mut collapsed);
    collapsed.join("\n")
}

fn nonblank_line_count(text: &str) -> usize {
    text.split('\n').filter(|l| pystr::has_content(l)).count()
}

pub(crate) fn trim_blank_edges(lines: &mut Vec<String>) {
    let start = lines
        .iter()
        .position(|l| !l.is_empty())
        .unwrap_or(lines.len());
    lines.drain(..start);
    while lines.last().is_some_and(|l| l.is_empty()) {
        lines.pop();
    }
}

/// Return the visible text of `html` with block structure as newlines.
///
/// `<br>` and block-level element open/close (`p`, `div`, `li`, `tr`,
/// `table`, `h1`–`h6`, `blockquote`, `pre`, `ul`, `ol`) become line breaks;
/// `script` / `style` / `head` content and comments are dropped;
/// character/entity references are unescaped; each line has its internal
/// whitespace collapsed, blank runs collapse to one, and blank edges are
/// trimmed. Empty or blank input yields `""`.
///
/// ```
/// use email_reply_extractor::html_to_text;
/// assert_eq!(html_to_text("<p>Hello&nbsp;world</p><p>Bye</p>"), "Hello world\n\nBye");
/// assert_eq!(html_to_text("a<br>b &amp; c"), "a\nb & c");
/// ```
pub fn html_to_text(html: &str) -> String {
    if !pystr::has_content(html) {
        return String::new();
    }
    render(&segment(html).segments, None)
}

/// Split `html` into novel / quoted / signature visible-text streams.
///
/// Rendering rules match [`html_to_text`]; the difference is which text is
/// kept in each stream, decided by the innermost enclosing container (see the
/// module documentation). `has_share_container` reports whether an Apple Mail
/// Share-sheet container opened outside every quoted container (and outside
/// `head`). Empty or blank input yields three empty strings and
/// `has_share_container == false`.
///
/// ```
/// use email_reply_extractor::split_html_parts;
/// let parts = split_html_parts("<p>Yes.</p><blockquote>Ready?</blockquote>");
/// assert_eq!(parts.novel_text, "Yes.");
/// assert_eq!(parts.quoted_text, "Ready?");
/// ```
pub fn split_html_parts(html: &str) -> HtmlParts {
    if !pystr::has_content(html) {
        return HtmlParts::default();
    }
    let seg = segment(html);
    let segments = &seg.segments;
    let mut novel: &[Category] = &[Category::Novel];
    let mut signature: &[Category] = &[Category::Signature];
    let first = segments
        .iter()
        .position(|s| matches!(s, Segment::Text(Category::Composed, _)));
    if let Some(first) = first {
        let composed = nonblank_line_count(&render(segments, Some(&[Category::Composed])));
        let above = nonblank_line_count(&render(&segments[..first], Some(&[Category::Novel])));
        if composed > above {
            novel = &[Category::Novel, Category::Composed];
        } else {
            signature = &[Category::Signature, Category::Composed];
        }
    }
    HtmlParts {
        novel_text: render(segments, Some(novel)),
        quoted_text: render(segments, Some(&[Category::Quoted])),
        signature_text: render(segments, Some(signature)),
        has_share_container: seg.has_share_container,
    }
}

#[cfg(test)]
mod tests {
    //! The Apple Mail Share-sheet container (generation 8), mirroring the
    //! Share-sheet tests of the reference's `tests/test_html_text.py`.

    use super::*;

    const SHARE_TOP: &str = r#"<div class="Apple-Mail-URLShareUserContentTopClass"><br></div>"#;
    const SHARE_OPEN: &str = concat!(
        r#"<div class="Apple-Mail-URLShareWrapperClass" style="position: relative !important;">"#,
        r#"<blockquote type="cite" style="border-left-style: none;">"#,
    );
    const SHARE_CLOSE: &str = "</blockquote></div>";

    #[test]
    fn share_container_blockquote_is_novel() {
        let html = format!(
            r#"<div dir="auto">{SHARE_TOP}{SHARE_OPEN}Shared text.<br>Second line.{SHARE_CLOSE}</div>"#
        );
        let parts = split_html_parts(&html);
        assert_eq!(parts.novel_text, "Shared text.\nSecond line.");
        assert_eq!(parts.quoted_text, "");
        assert!(parts.has_share_container);
    }

    #[test]
    fn share_container_nested_blockquote_stays_quoted() {
        let html = format!(
            "{SHARE_OPEN}My answer.<br>On Monday, Someone wrote:\
             <blockquote type=\"cite\">their earlier text</blockquote>\
             More of my answer.{SHARE_CLOSE}"
        );
        let parts = split_html_parts(&html);
        assert_eq!(
            parts.novel_text,
            "My answer.\nOn Monday, Someone wrote:\n\nMore of my answer."
        );
        assert_eq!(parts.quoted_text, "their earlier text");
        assert!(parts.has_share_container);
    }

    #[test]
    fn share_wrapper_with_several_blockquote_children() {
        let parts = split_html_parts(
            r#"<div class="Apple-Mail-URLShareWrapperClass"><blockquote>first</blockquote><blockquote>second</blockquote></div>"#,
        );
        assert_eq!(parts.novel_text, "first\n\nsecond");
        assert_eq!(parts.quoted_text, "");
    }

    #[test]
    fn share_wrapper_class_among_other_tokens() {
        let parts = split_html_parts(
            r#"<div class="x Apple-Mail-URLShareWrapperClass y"><blockquote>mine</blockquote></div>"#,
        );
        assert_eq!(parts.novel_text, "mine");
        assert!(parts.has_share_container);
    }

    #[test]
    fn blockquote_not_directly_under_share_wrapper_is_quoted() {
        for html in [
            // Not the parent: an element sits between the wrapper and the blockquote.
            r#"<div class="Apple-Mail-URLShareWrapperClass"><div><blockquote>q</blockquote></div></div>"#,
            // An unclosed element is still open, so it is the parent.
            r#"<div class="Apple-Mail-URLShareWrapperClass"><p>intro<blockquote>q</blockquote></div>"#,
            // The class token must match exactly.
            r#"<div class="apple-mail-urlsharewrapperclass"><blockquote>q</blockquote></div>"#,
            r#"<div class="Apple-Mail-URLShareWrapperClassX"><blockquote>q</blockquote></div>"#,
            // The user-content class is not the wrapper class.
            r#"<div class="Apple-Mail-URLShareUserContentTopClass"><blockquote>q</blockquote></div>"#,
            // The wrapper has closed before the blockquote opens.
            r#"<div class="Apple-Mail-URLShareWrapperClass"></div><blockquote>q</blockquote>"#,
        ] {
            let parts = split_html_parts(html);
            assert_eq!(parts.quoted_text, "q", "{html}");
            assert!(!parts.has_share_container, "{html}");
        }
    }

    #[test]
    fn share_wrapper_void_tag_between_does_not_break_parenthood() {
        let parts = split_html_parts(
            r#"<div class="Apple-Mail-URLShareWrapperClass"><br><blockquote>mine</blockquote></div>"#,
        );
        assert_eq!(parts.novel_text, "mine");
        assert!(parts.has_share_container);
    }

    #[test]
    fn ordinary_cite_blockquote_is_still_quoted() {
        let parts = split_html_parts(
            r#"<div>my reply</div><blockquote type="cite">their text</blockquote>"#,
        );
        assert_eq!(parts.novel_text, "my reply");
        assert_eq!(parts.quoted_text, "their text");
        assert!(!parts.has_share_container);
    }

    #[test]
    fn quoted_share_container_stays_quoted_and_is_not_reported() {
        let html = format!(
            "<div>my reply</div><blockquote type=\"cite\">On Monday, Someone wrote:\
             {SHARE_OPEN}their shared text{SHARE_CLOSE}</blockquote>"
        );
        let parts = split_html_parts(&html);
        assert_eq!(parts.novel_text, "my reply");
        assert!(parts.quoted_text.contains("their shared text"));
        assert!(!parts.has_share_container);
    }

    #[test]
    fn share_container_in_head_is_not_reported() {
        let html = format!(
            "<html><head>{SHARE_OPEN}hidden{SHARE_CLOSE}</head><body><div>visible</div></body></html>"
        );
        let parts = split_html_parts(&html);
        assert_eq!(parts.novel_text, "visible");
        assert!(!parts.has_share_container);
    }

    #[test]
    fn unclosed_share_container_does_not_fail() {
        let html = format!("<div>typed in Mail</div>{SHARE_OPEN}shared text with no close");
        let parts = split_html_parts(&html);
        assert_eq!(
            parts.novel_text,
            "typed in Mail\n\nshared text with no close"
        );
        assert!(parts.has_share_container);
    }

    #[test]
    fn empty_input_yields_empty_parts() {
        assert_eq!(split_html_parts(""), HtmlParts::default());
        assert!(!split_html_parts("").has_share_container);
    }
}
