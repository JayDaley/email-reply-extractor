//! New-text extraction: recover the author's novel content (stage 1 of 2).
//!
//! Port of the reference's `extraction.py`, whose module docstring is the
//! design documentation (see also `docs/extraction.md`). The fragment
//! scanner vendored from email-reply-parser (the crate's `fragments` module,
//! "ERP" below) is the primary extractor, followed by a custom cleanup pass:
//! pre-normalization (BOM, zero-width characters, CRLF/CR), attribution
//! lines, indented quote blocks, the sign-off boundary, forwarded /
//! quote-header blocks (English, German labels, Chinese, and Latin-script
//! localized blocks), "Original message" dividers, the over-strip guard, and
//! the optional parent-diff assist. The optional HTML part serves as a
//! structural oracle in three ways: HTML-only bodies, a degenerate
//! (flattened) plain body, and removal of plain-body content present in the
//! HTML's quoted text.
//!
//! Stage 1 keeps the author's own furniture (greetings, sign-offs, signature
//! blocks, list footers, mobile taglines); stage 2 ([`crate::clean_for_scoring`])
//! removes it.

use std::collections::HashSet;
use std::sync::LazyLock;

use crate::difflib;
use crate::fragments::{read_fragments, PatternError};
use crate::html_text::split_html_parts;
use crate::pyre::{Re, IGNORECASE};
use crate::pystr;

// --- generation stamp -----------------------------------------------------------

/// The generation of the text-deriving routine (`extraction`, `cleaning`,
/// `html_text` and `fragments` together). Consumers stamp stored derived
/// text with it and compare with `<`; it only ever increases. It is
/// independent of the package version.
pub const EXTRACTION_VERSION: u32 = 6;

// --- result -------------------------------------------------------------------

/// Status string: nothing left to score (blank body, or a body that reduced
/// to only quotes/signatures).
pub const STATUS_EMPTY: &str = "empty";
/// Status string: text was extracted.
pub const STATUS_OK: &str = "ok";
/// Status string: extraction failed internally.
pub const STATUS_FAILED: &str = "failed";

/// The status of an [`ExtractionResult`]. [`Status::as_str`] (and `Display`)
/// render the reference's strings: `"ok"`, `"empty"`, `"failed"`.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub enum Status {
    /// Text was extracted ([`STATUS_OK`]).
    Ok,
    /// Nothing left to score ([`STATUS_EMPTY`]).
    Empty,
    /// Extraction failed internally ([`STATUS_FAILED`]).
    Failed,
}

impl Status {
    /// The reference's status string.
    pub fn as_str(self) -> &'static str {
        match self {
            Status::Ok => STATUS_OK,
            Status::Empty => STATUS_EMPTY,
            Status::Failed => STATUS_FAILED,
        }
    }
}

impl std::fmt::Display for Status {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(self.as_str())
    }
}

/// The outcome of running [`extract_new_text`] on one body.
///
/// - `text`: the author's newly written text, furniture included (may be
///   empty).
/// - `method`: which path produced it: `"none"`, `"erp"`, `"erp+custom"`,
///   `"custom-fallback"` or `"failed"`, with an `"html-"` prefix when the
///   text came from the HTML part, and a `"+parent-diff"` or `"+html-quote"`
///   suffix when those assists removed content.
/// - `status`: [`Status::Ok`], [`Status::Empty`] or [`Status::Failed`].
#[derive(Clone, Debug, PartialEq, Eq, Hash)]
pub struct ExtractionResult {
    /// The extracted text.
    pub text: String,
    /// The path that produced the text.
    pub method: String,
    /// The outcome.
    pub status: Status,
}

// --- pre-normalization --------------------------------------------------------

/// BOM / zero-width space / ZWNJ / ZWJ / word joiner.
const ZERO_WIDTH: [char; 5] = ['\u{feff}', '\u{200b}', '\u{200c}', '\u{200d}', '\u{2060}'];

/// Normalize line endings and strip BOM / zero-width characters.
pub fn normalize_body(body: &str) -> String {
    body.replace("\r\n", "\n")
        .replace('\r', "\n")
        .chars()
        .filter(|c| !ZERO_WIDTH.contains(c))
        .collect()
}

// --- line classifiers ---------------------------------------------------------

static QUOTE_RE: LazyLock<Re> = LazyLock::new(|| Re::matcher(r"^[ \t]*(?:\.{2,}[ \t]*)?>+", 0));

/// True for a quoted line (any indentation; also the `...>` flowed form).
pub fn is_quote_line(line: &str) -> bool {
    QUOTE_RE.is_match(line)
}

// --- attribution lines --------------------------------------------------------

// Python's `\b` needs lookaround in Rust, and `fancy-regex` backtracks
// slowly on long lines through `.*`. Where a `\b` sits next to `.*` (or at
// the end of a pattern used only for a yes/no answer), it is rewritten as an
// equivalent class over Python's `\w`, which keeps the pattern in the
// linear-time `regex` engine. Each rewrite is listed with the reference
// pattern it replaces:
//
// - `On\b.*wrote:` is `On[^\w\n].*wrote:`: the character after "On" must be
//   a non-word character, and `.*` (no newline) consumes it, because
//   "wrote:" starts with a word character;
// - `Am\b.*\bschrieb\b.*:` is `Am[^\w\n](?:.*[^\w\n])?schrieb(?:[^\w\n].*)?:`
//   by the same argument on both sides of each `\b`;
// - a trailing `\b` in a match whose end is not used is `(?:[^\w]|$)`, and
//   a leading `\b` in a search whose position is not used is `(?:^|[^\w])`.
static ATTRIBUTION_RES: LazyLock<[Re; 4]> = LazyLock::new(|| {
    [
        // ^[ \t]*On\b.*wrote:[ \t]*$
        Re::matcher(r"^[ \t]*On[^\w\n].*wrote:[ \t]*$", 0),
        Re::matcher(r"^.*<[^>]+@[^>]+>[ \t]+wrote:[ \t]*$", 0),
        // ^[ \t]*Am\b.*\bschrieb\b.*:[ \t]*$
        Re::matcher(
            r"^[ \t]*Am[^\w\n](?:.*[^\w\n])?schrieb(?:[^\w\n].*)?:[ \t]*$",
            0,
        ),
        Re::matcher(r"^[ \t]*\d{4}(?:年|[/.-]).*のメール:[ \t]*$", 0),
    ]
});

// ^[ \t]*(?:(?:On|Am|Le|El|El día)\b|\d{4}(?:年|[/.-]))
static ATTRIBUTION_START_RE: LazyLock<Re> = LazyLock::new(|| {
    Re::matcher(
        r"^[ \t]*(?:(?:On|Am|Le|El|El día)(?:[^\w]|$)|\d{4}(?:年|[/.-]))",
        0,
    )
});

// (?:wrote:|\bschrieb\b.*:|\ba écrit\s*:|\bescribió:|のメール:)[ \t]*$
static ATTRIBUTION_END_RE: LazyLock<Re> = LazyLock::new(|| {
    Re::searcher(
        concat!(
            r"(?:wrote:|(?:^|[^\w])schrieb(?:[^\w\n].*)?:|(?:^|[^\w])a écrit\s*:",
            r"|(?:^|[^\w])escribió:|のメール:)[ \t]*$",
        ),
        0,
    )
});

/// True if `line` on its own is a complete attribution line.
pub fn is_attribution_line(line: &str) -> bool {
    ATTRIBUTION_RES.iter().any(|re| re.is_match(line))
}

fn wrapped_attribution_end(lines: &[&str], i: usize) -> Option<usize> {
    if !ATTRIBUTION_START_RE.is_match(lines[i]) || ATTRIBUTION_END_RE.is_match(lines[i]) {
        return None;
    }
    for (j, line) in lines
        .iter()
        .enumerate()
        .take((i + 3).min(lines.len()))
        .skip(i + 1)
    {
        if !pystr::has_content(line) {
            return None;
        }
        if ATTRIBUTION_END_RE.is_match(line) {
            return Some(j);
        }
    }
    None
}

/// Drop attribution lines, including forms wrapped across up to two lines.
pub fn strip_attribution_lines<'a>(lines: &[&'a str]) -> Vec<&'a str> {
    let mut out = Vec::new();
    let n = lines.len();
    let mut i = 0;
    while i < n {
        if is_attribution_line(lines[i]) {
            i += 1;
            continue;
        }
        if let Some(end) = wrapped_attribution_end(lines, i) {
            i = end + 1;
            continue;
        }
        out.push(lines[i]);
        i += 1;
    }
    out
}

// --- sign-off boundary ----------------------------------------------------------

const SIGNOFF_SALUTATIONS: &str = concat!(
    // English
    r"all\s+the\s+best|best\s+(?:regards|wishes)|kind\s+regards|warm(?:est)?\s+regards|",
    r"many\s+thanks|thanks\s+again|thank\s+you|yours(?:\s+(?:sincerely|truly|faithfully))?|",
    r"best|cheers|thanks|thx|regards|sincerely|respectfully|cordially|br|rgds",
    // German
    r"|mit\s+freundlichen\s+gr(?:ü|ue)(?:ß|ss)en|freundliche\s+gr(?:ü|ue)(?:ß|ss)e|",
    r"(?:viele|beste|liebe)\s+gr(?:ü|ue)(?:ß|ss)e|gr(?:ü|ue)(?:ß|ss)e?|mfg",
    // French
    r"|(?:bien\s+)?cordialement|amicalement|amiti(?:é|e)s|salutations",
    // Spanish
    r"|saludos(?:\s+cordiales)?|un\s+saludo|atentamente",
    // Dutch
    r"|met\s+vriendelijke\s+groet(?:en)?|groet(?:en|jes)?|mvg",
    // Māori
    r"|ng(?:ā|a)\s+mihi(?:\s+nui)?|noho\s+ora\s+mai",
    // Nordic / other
    r"|v(?:ä|a)nliga\s+h(?:ä|a)lsningar|med\s+v(?:e|ä)nlig\s+h(?:i|ä)lsen|mvh|terveisin",
    r"|cumprimentos|atenciosamente|ciao|saluti|cordiali\s+saluti",
);

pub(crate) static SIGNOFF_LINE_RE: LazyLock<Re> = LazyLock::new(|| {
    Re::matcher(
        &format!(r"^[ \t]*(?:{SIGNOFF_SALUTATIONS})[ \t]*[,.!]?[ \t]*$"),
        IGNORECASE,
    )
});

static SIGNOFF_INLINE_RE: LazyLock<Re> = LazyLock::new(|| {
    Re::matcher(
        &format!(r"^[ \t]*(?:{SIGNOFF_SALUTATIONS})[ \t]*,[ \t]*(?P<name>\S.*)$"),
        IGNORECASE,
    )
});

static NAME_TOKEN_RE: LazyLock<Re> = LazyLock::new(|| Re::matcher(r"^[^\W\d_][\w'’.\-]*$", 0));

/// True if `text` is plausibly just a person's name (1–4 capitalized-ish
/// words, no digits, at most 40 characters).
pub fn is_signoff_name_line(text: &str) -> bool {
    let s = pystr::strip(text);
    if s.is_empty() || pystr::len(s) > 40 || s.chars().any(pystr::is_digit) {
        return false;
    }
    let tokens = pystr::split_ws(s);
    if !(1..=4).contains(&tokens.len()) {
        return false;
    }
    if !tokens[0].chars().next().is_some_and(pystr::is_upper) {
        return false;
    }
    tokens.iter().all(|tok| NAME_TOKEN_RE.is_match(tok))
}

/// `_NAME_TITLE_SEP_RE.split(text.strip(), maxsplit=1)` then the title rules.
fn is_signoff_name_title_line(text: &str) -> bool {
    let s = pystr::strip(text);
    let Some(pos) = s.find(['|', ',']) else {
        return false;
    };
    let name = pystr::strip(&s[..pos]);
    let title = pystr::strip(&s[pos + 1..]);
    if title.is_empty() || pystr::len(title) > 40 || pystr::split_ws(title).len() > 4 {
        return false;
    }
    is_signoff_name_line(name)
}

/// A plausible sign-off name line: bare name, or name + title/affiliation.
pub(crate) fn is_signoff_name_like(text: &str) -> bool {
    is_signoff_name_line(text) || is_signoff_name_title_line(text)
}

/// If a sign-off opens at `lines[i]`, the index of its name line.
pub(crate) fn signoff_name_index(lines: &[&str], i: usize) -> Option<usize> {
    if SIGNOFF_LINE_RE.is_match(lines[i]) {
        let n = lines.len();
        let mut j = i + 1;
        while j < n && !pystr::has_content(lines[j]) {
            j += 1;
        }
        if j < n && is_signoff_name_like(lines[j]) {
            return Some(j);
        }
        return None;
    }
    if let Some(name) = SIGNOFF_INLINE_RE.named(lines[i], "name") {
        if is_signoff_name_like(name) {
            return Some(i);
        }
    }
    None
}

/// The index just past a sign-off (salutation + name) that is directly
/// followed by an attribution line, or `None`.
pub fn find_signoff_boundary(lines: &[&str]) -> Option<usize> {
    let n = lines.len();
    for i in 0..n {
        let Some(name_idx) = signoff_name_index(lines, i) else {
            continue;
        };
        let mut k = name_idx + 1;
        while k < n && !pystr::has_content(lines[k]) {
            k += 1;
        }
        if k >= n {
            continue;
        }
        if is_attribution_line(lines[k]) || wrapped_attribution_end(lines, k).is_some() {
            return Some(name_idx + 1);
        }
    }
    None
}

fn strip_after_signoff_boundary(lines: Vec<&str>) -> Vec<&str> {
    match find_signoff_boundary(&lines) {
        None => lines,
        Some(idx) => lines[..idx].to_vec(),
    }
}

// --- forwarded / top-post quote-header blocks ---------------------------------

static FROM_LINE_RE: LazyLock<Re> =
    LazyLock::new(|| Re::matcher(r"^[ \t]*(?:From:|发件人[ \t]*[:：])[ \t]*\S", 0));

static LOCALIZED_FROM_LINE_RE: LazyLock<Re> =
    LazyLock::new(|| Re::matcher(r"^[ \t]*(?:De|Da|Van|Von)[ \t  ]*:[ \t  ]*\S", 0));

static LOCALIZED_FIELD_RE: LazyLock<Re> = LazyLock::new(|| {
    Re::matcher(
        concat!(
            r"^[ \t]*(?i:Envoy[ée]|Date|[ÀA]|Cci?|Objet",
            r"|Enviad[oa](?:[ \t]+(?:el|em))?|Fecha|Para|CCO|Asunto|Assunto",
            r"|Data|Inviato|Oggetto",
            r"|Verzonden|Datum|Aan|Onderwerp",
            r"|Gesendet|An|Betreff|De|Da|Van|Von)",
            r"[ \t  ]*:(?:[ \t  ]|$)",
        ),
        0,
    )
});

static LOCALIZED_SUBJECT_RE: LazyLock<Re> = LazyLock::new(|| {
    Re::matcher(
        r"^[ \t]*(?i:Objet|Asunto|Assunto|Oggetto|Onderwerp|Betreff)[ \t  ]*:(?:[ \t  ]|$)",
        0,
    )
});

static HEADER_FIELD_RE: LazyLock<Re> = LazyLock::new(|| {
    Re::matcher(
        r"^[ \t]*(?:[A-Za-z][A-Za-z-]{0,40}:(?:[ \t]|$)|[一-鿿][一-鿿　]{0,10}[ \t]*[:：])",
        0,
    )
});

static QUOTE_HEADER_SIGNAL_RE: LazyLock<Re> = LazyLock::new(|| {
    Re::matcher(
        concat!(
            r"^[ \t]*(?:(?:Sent|Date|To|Cc|Bcc|Reply-To|Subject",
            r"|Gesendet|An|Betreff)[ \t]*:(?:[ \t]|$)",
            r"|(?:发送时间|收件人|抄送人?|日期|主　?题)[ \t]*[:：])",
        ),
        IGNORECASE,
    )
});

static TRANSPORT_HEADER_RE: LazyLock<Re> = LazyLock::new(|| {
    Re::matcher(
        r"^[ \t]*(?:Message-ID|References|In-Reply-To|Received|Return-Path|Resent-[A-Za-z-]+)[ \t]*:",
        IGNORECASE,
    )
});

fn in_pasted_header_run(lines: &[&str], i: usize) -> bool {
    let mut j = i;
    while j > 0 && HEADER_FIELD_RE.is_match(lines[j - 1]) {
        if TRANSPORT_HEADER_RE.is_match(lines[j - 1]) {
            return true;
        }
        j -= 1;
    }
    false
}

/// The index of the `From:` line that opens a forwarded/quote-header block,
/// or `None`. `localized` enables the Latin-script localized blocks
/// (`true` in the reference's default).
pub fn find_quote_header_block(lines: &[&str], localized: bool) -> Option<usize> {
    let n = lines.len();
    for (i, line) in lines.iter().enumerate() {
        let is_localized = if FROM_LINE_RE.is_match(line) {
            false
        } else if localized && LOCALIZED_FROM_LINE_RE.is_match(line) {
            true
        } else {
            continue;
        };
        if i > 0 && in_pasted_header_run(lines, i) {
            continue;
        }
        let is_field = |text: &str| {
            HEADER_FIELD_RE.is_match(text) || (is_localized && LOCALIZED_FIELD_RE.is_match(text))
        };
        let tolerant = line.contains('@');
        let mut header_lines = 1;
        let mut has_signal = false;
        let mut j = i + 1;
        while j < n {
            let nxt = lines[j];
            if is_field(nxt) {
                header_lines += 1;
                let signal = if is_localized {
                    LOCALIZED_SUBJECT_RE.is_match(nxt)
                } else {
                    QUOTE_HEADER_SIGNAL_RE.is_match(nxt)
                };
                if signal {
                    has_signal = true;
                }
                j += 1;
            } else if (pystr::has_content(nxt) && (tolerant || nxt.starts_with([' ', '\t'])))
                // Folded continuation of the preceding header field, or a
                // single blank between fields (double-spaced rendering).
                || (!pystr::has_content(nxt) && tolerant && j + 1 < n && is_field(lines[j + 1]))
            {
                j += 1;
            } else {
                break;
            }
        }
        if has_signal && header_lines >= if is_localized { 3 } else { 2 } {
            return Some(i);
        }
    }
    None
}

static QUOTE_HEADER_DIVIDER_RE: LazyLock<Re> =
    LazyLock::new(|| Re::matcher(r"^[ \t]*-{6,}[ \t]*$", 0));

fn strip_after_quote_header_block<'a>(lines: &[&'a str], localized: bool) -> Vec<&'a str> {
    let Some(idx) = find_quote_header_block(lines, localized) else {
        return lines.to_vec();
    };
    let mut out = lines[..idx].to_vec();
    while out.last().is_some_and(|l| !pystr::has_content(l)) {
        out.pop();
    }
    if out
        .last()
        .is_some_and(|l| QUOTE_HEADER_DIVIDER_RE.is_match(l))
    {
        out.pop();
    }
    out
}

// --- "Original message" / "Forwarded message" dividers -------------------------

static ORIGINAL_MESSAGE_DIVIDER_RE: LazyLock<Re> = LazyLock::new(|| {
    Re::searcher(
        r"-{2,}[ \t]*(?:(?:Original|Forwarded)[ \t]+Message|邮件原件|原始邮件)[ \t]*-{2,}",
        IGNORECASE,
    )
});

static LOCALIZED_DIVIDER_RE: LazyLock<Re> = LazyLock::new(|| {
    Re::searcher(
        concat!(
            r"-{2,}[ \t  ]*(?:",
            r"Message[ \t  ]+d['’]origine|Message[ \t  ]+transf[ée]r[ée]",
            r"|Mensaje[ \t  ]+(?:original|reenviado)",
            r"|Mensagem[ \t  ]+(?:original|encaminhada)",
            r"|Messaggio[ \t  ]+(?:originale|inoltrato)",
            r"|(?:Oorspronkelijk|Doorgestuurd)[ \t  ]+bericht",
            r"|(?:Urspr(?:ü|ue)ngliche|Weitergeleitete)[ \t  ]+Nachricht",
            r")[ \t  ]*-{2,}",
        ),
        IGNORECASE,
    )
});

/// The byte offset of the first "Original message" divider in `line`.
fn find_divider(line: &str, localized: bool) -> Option<usize> {
    let english = ORIGINAL_MESSAGE_DIVIDER_RE.find(line).map(|m| m.0);
    let local = if localized {
        LOCALIZED_DIVIDER_RE.find(line).map(|m| m.0)
    } else {
        None
    };
    match (english, local) {
        (Some(a), Some(b)) => Some(a.min(b)),
        (a, b) => a.or(b),
    }
}

/// Truncate everything from a dashed "Original/Forwarded message" divider,
/// keeping a non-empty author prefix glued before a mid-line divider.
pub fn strip_after_original_message_divider<'a>(
    lines: &[&'a str],
    localized: bool,
) -> Vec<&'a str> {
    for (i, line) in lines.iter().enumerate() {
        let Some(start) = find_divider(line, localized) else {
            continue;
        };
        let mut out = lines[..i].to_vec();
        let prefix = pystr::rstrip(&line[..start]);
        if !prefix.is_empty() {
            out.push(prefix);
        }
        return out;
    }
    lines.to_vec()
}

/// Apply the stage-1 quote/attribution cleanup to `text`.
pub fn custom_clean(text: &str, localized: bool) -> String {
    let lines: Vec<&str> = text.split('\n').collect();
    let lines = strip_after_quote_header_block(&lines, localized);
    let lines = strip_after_original_message_divider(&lines, localized);
    let lines = strip_after_signoff_boundary(lines);
    let lines = strip_attribution_lines(&lines);
    let mut lines: Vec<&str> = lines
        .into_iter()
        .filter(|l| !is_quote_line(l))
        .map(pystr::rstrip)
        .collect();
    let start = lines
        .iter()
        .position(|l| !l.is_empty())
        .unwrap_or(lines.len());
    lines.drain(..start);
    while lines.last().is_some_and(|l| l.is_empty()) {
        lines.pop();
    }
    lines.join("\n")
}

// --- over-strip guard ---------------------------------------------------------

const GUARD_MIN_RETAINED: f64 = 0.6;

/// Count non-blank body lines that are plainly authored content (the
/// denominator of the over-strip guard).
pub fn count_unquoted_content_lines(body: &str, localized: bool) -> usize {
    let lines: Vec<&str> = body.split('\n').collect();
    let lines = strip_after_quote_header_block(&lines, localized);
    let lines = strip_after_original_message_divider(&lines, localized);
    let lines = strip_after_signoff_boundary(lines);
    lines
        .iter()
        .filter(|l| pystr::has_content(l) && !is_quote_line(l) && !is_attribution_line(l))
        .count()
}

fn nonblank_line_count(text: &str) -> usize {
    text.split('\n').filter(|l| pystr::has_content(l)).count()
}

// --- parent-diff assist -------------------------------------------------------

const PARENT_DIFF_MIN_BLOCK_WORDS: usize = 10;
const PARENT_DIFF_MIN_LINE_WORDS: usize = 8;
const PARENT_DIFF_CONTINUATION_MIN_SEEDS: usize = 3;

/// Strip a leading quote-marker prefix, collapse whitespace runs to single
/// spaces and strip the edges.
pub(crate) fn normalize_line_for_diff(line: &str) -> String {
    let rest = match QUOTE_RE.find(line) {
        Some((0, end)) => &line[end..],
        _ => line,
    };
    pystr::collapse_ws(rest)
}

/// Remove lines of `text` that provably came from `parent_body`.
///
/// Both sides are normalized ([`normalize_body`], then per line: quote
/// markers stripped and whitespace collapsed); blank lines are ignored when
/// matching. A child line is removed when it is part of an aligned run of
/// matching lines (`difflib.SequenceMatcher`, `autojunk=False`) with at least
/// 10 words in total or one line of at least 8 words; or when it has at
/// least 8 words and occurs verbatim in the parent's word stream; or, with
/// `continuation` (the reference's keyword-only argument, default `false`),
/// when it continues an adjacent removed line contiguously in the parent's
/// word stream (once at least 3 lines were removed by the first two rules).
/// Survivors are right-stripped, runs of blank lines collapse to one, and
/// blank edges are trimmed.
///
/// ```
/// use email_reply_extractor::strip_parent_content;
/// let parent = "The working group last call ends on Friday at noon UTC.";
/// let text = "Agreed.\n\nThe working group last call ends on Friday at noon UTC.";
/// assert_eq!(strip_parent_content(text, parent, false), "Agreed.");
/// ```
pub fn strip_parent_content(text: &str, parent_body: &str, continuation: bool) -> String {
    let normalized_child = normalize_body(text);
    let child_raw: Vec<&str> = normalized_child.split('\n').collect();
    let child_norm: Vec<String> = child_raw
        .iter()
        .map(|l| normalize_line_for_diff(l))
        .collect();
    let parent_norm: Vec<String> = normalize_body(parent_body)
        .split('\n')
        .map(normalize_line_for_diff)
        .collect();

    let child_pairs: Vec<(usize, &str)> = child_norm
        .iter()
        .enumerate()
        .filter(|(_, n)| !n.is_empty())
        .map(|(i, n)| (i, n.as_str()))
        .collect();
    let child_seq: Vec<String> = child_pairs.iter().map(|(_, n)| n.to_string()).collect();
    let parent_seq: Vec<String> = parent_norm.into_iter().filter(|n| !n.is_empty()).collect();

    let mut marked: HashSet<usize> = HashSet::new();

    // Rule (a): aligned runs of matching lines with enough substance.
    for block in difflib::matching_blocks(&child_seq, &parent_seq) {
        if block.size == 0 {
            continue;
        }
        let lines = &child_seq[block.a..block.a + block.size];
        let total_words: usize = lines.iter().map(|l| pystr::split_ws(l).len()).sum();
        let max_line_words = lines
            .iter()
            .map(|l| pystr::split_ws(l).len())
            .max()
            .unwrap_or(0);
        if total_words >= PARENT_DIFF_MIN_BLOCK_WORDS
            || max_line_words >= PARENT_DIFF_MIN_LINE_WORDS
        {
            for pair in &child_pairs[block.a..block.a + block.size] {
                marked.insert(pair.0);
            }
        }
    }

    // Rule (b): a long child line re-wrapped from the parent.
    let parent_stream = parent_seq.join(" ");
    for &(orig_i, norm) in &child_pairs {
        if marked.contains(&orig_i) {
            continue;
        }
        if pystr::split_ws(norm).len() >= PARENT_DIFF_MIN_LINE_WORDS && parent_stream.contains(norm)
        {
            marked.insert(orig_i);
        }
    }

    // Rule (c): chain short remainder lines off marked neighbors, to a fixpoint.
    let mut changed = continuation && marked.len() >= PARENT_DIFF_CONTINUATION_MIN_SEEDS;
    while changed {
        changed = false;
        for k in 0..child_pairs.len() {
            let (orig_i, norm) = child_pairs[k];
            if marked.contains(&orig_i) {
                continue;
            }
            if k > 0 {
                let (prev_i, prev_norm) = child_pairs[k - 1];
                if marked.contains(&prev_i)
                    && parent_stream.contains(&format!("{prev_norm} {norm}"))
                {
                    marked.insert(orig_i);
                    changed = true;
                    continue;
                }
            }
            if k + 1 < child_pairs.len() {
                let (next_i, next_norm) = child_pairs[k + 1];
                if marked.contains(&next_i)
                    && parent_stream.contains(&format!("{norm} {next_norm}"))
                {
                    marked.insert(orig_i);
                    changed = true;
                }
            }
        }
    }

    let survivors = child_raw
        .iter()
        .enumerate()
        .filter(|(i, _)| !marked.contains(i))
        .map(|(_, l)| pystr::rstrip(l));
    collapse_and_trim(survivors)
}

/// Collapse runs of 2+ blank lines to one, trim blank edges, join with `\n`.
pub(crate) fn collapse_and_trim<'a>(lines: impl Iterator<Item = &'a str>) -> String {
    let mut collapsed: Vec<&str> = Vec::new();
    for line in lines {
        if line.is_empty() && collapsed.last().is_some_and(|l| l.is_empty()) {
            continue;
        }
        collapsed.push(line);
    }
    let start = collapsed
        .iter()
        .position(|l| !l.is_empty())
        .unwrap_or(collapsed.len());
    collapsed.drain(..start);
    while collapsed.last().is_some_and(|l| l.is_empty()) {
        collapsed.pop();
    }
    collapsed.join("\n")
}

// --- inline replies below a localized quote-header block --------------------------

static INLINE_REPLY_TAG_RE: LazyLock<Re> = LazyLock::new(|| {
    Re::matcher(
        concat!(
            r"^[ \t]*\[(?!(?:RFC|BCP|STD|FYI|I-D|IANA|draft)\b|(?:RFC|BCP|STD)\d)",
            r"(?=[^\]\n]*[A-Za-z])[^\[\]\n]{1,20}\]",
        ),
        0,
    )
});

const INLINE_REPLY_MIN_PARENT_LINES: usize = 1;

fn first_quote_boundary(lines: &[&str], localized: bool) -> usize {
    let end = find_quote_header_block(lines, localized).unwrap_or(lines.len());
    for (i, line) in lines.iter().enumerate().take(end) {
        if find_divider(line, localized).is_some() {
            return i;
        }
    }
    end
}

fn localized_inline_reply(lines: &[&str], parent_body: Option<&str>) -> bool {
    let start = first_quote_boundary(lines, true);
    let end = first_quote_boundary(lines, false);
    if start >= end {
        return false;
    }
    let span = lines[start..end].join("\n");
    let survivors = match parent_body {
        None => span.clone(),
        Some(parent) => {
            let survivors = strip_parent_content(&span, parent, true);
            let removed =
                nonblank_line_count(&span) as isize - nonblank_line_count(&survivors) as isize;
            if removed < INLINE_REPLY_MIN_PARENT_LINES as isize {
                return false;
            }
            survivors
        }
    };
    let head = lines[..start].join("\n");
    for line in survivors.split('\n') {
        if let Some((_, end)) = INLINE_REPLY_TAG_RE.find(line) {
            let tag: Vec<char> = pystr::strip(&line[..end]).chars().collect();
            let label: String = if tag.len() >= 2 {
                tag[1..tag.len() - 1].iter().collect()
            } else {
                String::new()
            };
            if is_author_tag(&label, &head, parent_body) {
                return true;
            }
        }
    }
    false
}

fn is_author_tag(label: &str, head: &str, parent_body: Option<&str>) -> bool {
    if let Some(parent) = parent_body {
        if !parent.contains(&format!("[{label}")) {
            return true;
        }
    }
    let name = pystr::strip(label.trim_end_matches(|c: char| c.is_ascii_digit()));
    if name.is_empty() {
        return false;
    }
    // re.search(rf"(?<!\w){re.escape(name)}(?!\w)", head)
    let mut from = 0;
    while let Some(off) = head[from..].find(name) {
        let start = from + off;
        let end = start + name.len();
        let before_ok = !head[..start]
            .chars()
            .next_back()
            .is_some_and(pystr::is_word);
        let after_ok = !head[end..].chars().next().is_some_and(pystr::is_word);
        if before_ok && after_ok {
            return true;
        }
        from = start + head[start..].chars().next().map_or(1, char::len_utf8);
    }
    false
}

// --- public entry point -------------------------------------------------------

fn core_extract(body: &str, parent_body: Option<&str>) -> Result<(String, String), PatternError> {
    let mut normalized = normalize_body(body);

    let lines: Vec<&str> = normalized.split('\n').collect();
    let localized = !localized_inline_reply(&lines, parent_body);
    let pretruncated = find_quote_header_block(&lines, localized).is_some();
    if pretruncated {
        normalized = strip_after_quote_header_block(&lines, localized).join("\n");
    }

    let erp_out = read_fragments(&normalized)?
        .into_iter()
        .filter(|f| !f.quoted)
        .map(|f| f.content)
        .collect::<Vec<_>>()
        .join("\n");
    let cleaned = custom_clean(&erp_out, localized);
    let custom_changed = pretruncated || cleaned != pystr::strip(&erp_out);
    let plain_method = if custom_changed { "erp+custom" } else { "erp" };

    let unquoted = count_unquoted_content_lines(&normalized, localized);
    let (mut text, mut method);
    if unquoted > 0 && (nonblank_line_count(&cleaned) as f64) < GUARD_MIN_RETAINED * unquoted as f64
    {
        let fallback = custom_clean(&normalized, localized);
        if pystr::has_content(&fallback) {
            text = fallback;
            method = "custom-fallback".to_string();
        } else {
            text = cleaned;
            method = plain_method.to_string();
        }
    } else {
        text = cleaned;
        method = plain_method.to_string();
    }

    if let Some(parent) = parent_body {
        if pystr::has_content(&text) {
            let assisted = strip_parent_content(&text, parent, true);
            if nonblank_line_count(&assisted) < nonblank_line_count(&text) {
                text = assisted;
                method.push_str("+parent-diff");
            }
        }
    }
    Ok((text, method))
}

const HTML_QUOTE_MIN_REMOVED: usize = 3;

fn is_degenerate_plain(body: &str, html_novel: &str) -> bool {
    let lines: Vec<&str> = body.split('\n').filter(|l| pystr::has_content(l)).collect();
    if lines.is_empty() || lines.len() >= 4 {
        return false;
    }
    if !lines.iter().any(|l| pystr::len(l) > 400) {
        return false;
    }
    nonblank_line_count(html_novel) >= 3 * lines.len()
}

fn result(method: String, text: String) -> ExtractionResult {
    if !pystr::has_content(&text) {
        return ExtractionResult {
            text: String::new(),
            method,
            status: Status::Empty,
        };
    }
    ExtractionResult {
        text,
        method,
        status: Status::Ok,
    }
}

fn extract(
    body: Option<&str>,
    parent_body: Option<&str>,
    html_body: Option<&str>,
) -> Result<ExtractionResult, PatternError> {
    let parts = match html_body {
        Some(html) if pystr::has_content(html) => Some(split_html_parts(html)),
        _ => None,
    };

    // (a) HTML-only: no usable plain body, but an HTML part to derive from.
    let body = match body {
        Some(b) if pystr::has_content(b) => b,
        _ => {
            if let Some(p) = &parts {
                if pystr::has_content(&p.novel_text) {
                    let (text, method) = core_extract(&p.novel_text, parent_body)?;
                    return Ok(result(format!("html-{method}"), text));
                }
            }
            return Ok(ExtractionResult {
                text: String::new(),
                method: "none".to_string(),
                status: Status::Empty,
            });
        }
    };

    // (b) Degenerate plain: a flattened plain body the HTML can replace.
    if let Some(p) = &parts {
        if is_degenerate_plain(body, &p.novel_text) {
            let (text, method) = core_extract(&p.novel_text, parent_body)?;
            return Ok(result(format!("html-{method}"), text));
        }
    }

    let (mut text, mut method) = core_extract(body, parent_body)?;

    // (c) Oracle assist: remove plain-body content present in the HTML's quoted text.
    if let Some(p) = &parts {
        if pystr::has_content(&text) && pystr::has_content(&p.quoted_text) {
            let assisted = strip_parent_content(&text, &p.quoted_text, false);
            if nonblank_line_count(&text).saturating_sub(nonblank_line_count(&assisted))
                >= HTML_QUOTE_MIN_REMOVED
            {
                text = assisted;
                method.push_str("+html-quote");
            }
        }
    }
    Ok(result(method, text))
}

/// Extract the author's newly written text from a decoded `text/plain` body.
///
/// Arguments follow the reference's signature in order; Python's optional
/// arguments (default `None`) are `Option<&str>`:
///
/// - `body`: the decoded `text/plain` part, or `None` when the message has
///   none;
/// - `parent_body`: the thread parent's body. When given, content that
///   provably came from the parent is removed after the primary pass
///   (method suffix `"+parent-diff"`);
/// - `html_body`: the decoded `text/html` part, used as a structural oracle
///   (HTML-only bodies, a flattened plain body, quoted-text removal).
///
/// Never panics and never fails: an internal error yields
/// `ExtractionResult { text: "", method: "failed", status: Status::Failed }`.
///
/// ```
/// use email_reply_extractor::{extract_new_text, Status};
/// let body = "Sounds good to me.\n\nOn Tue, 1 Jul 2026, Ann wrote:\n> Shall we merge?\n";
/// let result = extract_new_text(Some(body), None, None);
/// assert_eq!(result.text, "Sounds good to me.");
/// assert_eq!(result.method, "erp");
/// assert_eq!(result.status, Status::Ok);
/// ```
pub fn extract_new_text(
    body: Option<&str>,
    parent_body: Option<&str>,
    html_body: Option<&str>,
) -> ExtractionResult {
    let outcome = std::panic::catch_unwind(|| extract(body, parent_body, html_body));
    match outcome {
        Ok(Ok(r)) => r,
        _ => ExtractionResult {
            text: String::new(),
            method: "failed".to_string(),
            status: Status::Failed,
        },
    }
}
