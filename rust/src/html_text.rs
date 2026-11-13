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
//! - **Signature**: inside an element whose `class` list holds
//!   `gmail_signature`, `moz-signature` or `Signature`, or whose `id` is
//!   `Signature`, when not inside a quoted container.
//! - **Novel**: everything else.

use crate::html_parser::{self, Sink};
use crate::pystr;
use std::collections::HashSet;

#[derive(Clone, Copy, PartialEq, Eq, Debug)]
enum Category {
    Novel,
    Quoted,
    Signature,
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

struct Frame {
    tag: String,
    category: Option<Category>,
    is_skip: bool,
}

struct Segmenter {
    stack: Vec<Frame>,
    quoted_depth: usize,
    signature_depth: usize,
    skip_depth: usize,
    interleaved_wrappers: HashSet<usize>,
    wrapper_count: usize,
    segments: Vec<Segment>,
}

impl Segmenter {
    fn active_category(&self) -> Category {
        if self.quoted_depth > 0 {
            Category::Quoted
        } else if self.signature_depth > 0 {
            Category::Signature
        } else {
            Category::Novel
        }
    }

    fn container_category(
        &mut self,
        tag: &str,
        attrs: &[(String, Option<String>)],
    ) -> Option<Category> {
        let (classes, id) = class_and_id(attrs);
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
        None
    }
}

impl Sink for Segmenter {
    fn start_tag(&mut self, tag: &str, attrs: &[(String, Option<String>)]) {
        let tag = pystr::lower(tag);
        if SKIP_TAGS.contains(&tag.as_str()) {
            self.skip_depth += 1;
            self.stack.push(Frame {
                tag,
                category: None,
                is_skip: true,
            });
            return;
        }
        if VOID_TAGS.contains(&tag.as_str()) {
            if tag == "br" && self.skip_depth == 0 {
                self.segments.push(Segment::Newline);
            }
            return;
        }
        let category = self.container_category(&tag, attrs);
        match category {
            Some(Category::Quoted) => self.quoted_depth += 1,
            Some(Category::Signature) => self.signature_depth += 1,
            _ => {}
        }
        let is_block = BLOCK_TAGS.contains(&tag.as_str());
        self.stack.push(Frame {
            tag,
            category,
            is_skip: false,
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
        let Some(idx) = self.stack.iter().rposition(|f| f.tag == tag) else {
            return;
        };
        for frame in &self.stack[idx..] {
            if frame.is_skip {
                self.skip_depth -= 1;
            } else if frame.category == Some(Category::Quoted) {
                self.quoted_depth -= 1;
            } else if frame.category == Some(Category::Signature) {
                self.signature_depth -= 1;
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
    stack: Vec<(String, Option<usize>)>,
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
            for (_, index) in &self.stack {
                if let Some(i) = index {
                    self.interleaved.insert(*i);
                }
            }
            self.stack.push((tag, None));
            return;
        }
        let (classes, id) = class_and_id(attrs);
        let is_quoted_hook =
            classes.iter().any(|c| QUOTED_CLASSES.contains(c)) || QUOTED_IDS.contains(&id);
        let mut index = None;
        if !is_quoted_hook && classes.iter().any(|c| GMAIL_WRAPPER_CLASSES.contains(c)) {
            index = Some(self.wrapper_count);
            self.wrapper_count += 1;
        }
        self.stack.push((tag, index));
    }

    fn end_tag(&mut self, tag: &str) {
        let tag = pystr::lower(tag);
        if VOID_TAGS.contains(&tag.as_str()) {
            return;
        }
        if let Some(i) = self.stack.iter().rposition(|(t, _)| *t == tag) {
            self.stack.truncate(i);
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

fn segment(html: &str) -> Vec<Segment> {
    let mut seg = Segmenter {
        stack: Vec::new(),
        quoted_depth: 0,
        signature_depth: 0,
        skip_depth: 0,
        interleaved_wrappers: interleaved_wrappers(html),
        wrapper_count: 0,
        segments: Vec::new(),
    };
    let _ = html_parser::parse(html, &mut seg);
    seg.segments
}

fn render(segments: &[Segment], wanted: Option<Category>) -> String {
    let mut lines: Vec<String> = Vec::new();
    let mut buf = String::new();
    for seg in segments {
        match seg {
            Segment::Newline => lines.push(std::mem::take(&mut buf)),
            Segment::Text(category, text) => {
                if wanted.is_none() || wanted == Some(*category) {
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
    render(&segment(html), None)
}

/// Split `html` into novel / quoted / signature visible-text streams.
///
/// Rendering rules match [`html_to_text`]; the difference is which text is
/// kept in each stream, decided by the innermost enclosing container (see the
/// module documentation). Empty or blank input yields three empty strings.
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
    let segments = segment(html);
    HtmlParts {
        novel_text: render(&segments, Some(Category::Novel)),
        quoted_text: render(&segments, Some(Category::Quoted)),
        signature_text: render(&segments, Some(Category::Signature)),
    }
}
