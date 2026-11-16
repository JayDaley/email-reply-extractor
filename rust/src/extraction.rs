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
//! structural oracle in four ways: HTML-only bodies, a degenerate
//! (flattened) plain body, an Apple Mail Share-sheet container (whose text
//! the plain part marks as quoted), and removal of plain-body content present
//! in the HTML's quoted text.
//!
//! Stage 1 keeps the author's own furniture (greetings, sign-offs, signature
//! blocks, list footers, mobile taglines); stage 2 ([`crate::clean_for_scoring`])
//! removes it.

use std::collections::{HashMap, HashSet};
use std::sync::LazyLock;

use crate::fragments::read_fragments;
use crate::html_text::{html_to_text, split_html_parts};
use crate::pyre::{Re, IGNORECASE};
use crate::pystr;

// --- generation stamp -----------------------------------------------------------

/// The generation of the text-deriving routine (`extraction`, `cleaning`,
/// `html_text` and `fragments` together). Consumers stamp stored derived
/// text with it and compare with `<`; it only ever increases. It is
/// independent of the package version.
pub const EXTRACTION_VERSION: u32 = 9;

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
/// - `part_starts`: the 0-based indices into `text.split('\n')` of the lines
///   that begin a new *part*: a run of the author's text separated from the
///   previous run by content that stage 1 removed (a quoted block, an
///   attribution, parent-diff or HTML-quote content). An inline reply written
///   between quoted passages has one part per answer. Empty when the text is
///   a single part, when it is empty, and when the text could not be aligned
///   with its source. The text does not depend on this field; pass it to
///   [`crate::clean_for_scoring_with_parts`] to divide the cleaned text into
///   the same parts.
#[derive(Clone, Debug, PartialEq, Eq, Hash)]
pub struct ExtractionResult {
    /// The extracted text.
    pub text: String,
    /// The path that produced the text.
    pub method: String,
    /// The outcome.
    pub status: Status,
    /// Indices of the lines of `text` that begin a new part, ascending.
    pub part_starts: Vec<usize>,
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
// - `To unsubscribe\b.*` (in cleaning) is `To unsubscribe(?:[^\w\n].*)?` by
//   the same argument;
// - a trailing `\b` in a match whose end is not used is `(?:[^\w]|$)`, and
//   a leading `\b` in a search whose position is not used is `(?:^|[^\w])`.
//
// Two forms are not run as single patterns, as in the reference, whose
// engine's backtracking over them is polynomial on adversarial lines (a line
// of "<a@" repeated, or of "schrieb:" repeated): "<who> <a@b> wrote:"
// ([`is_address_attribution`]) and the German "Am <date> schrieb <who>:"
// ([`is_german_attribution`]). The `regex` crate runs the original patterns
// in linear time, so here the helpers only keep the ports aligned; each
// accepts exactly the strings the original pattern accepts under `re.match`.
static ATTRIBUTION_RES: LazyLock<[Re; 2]> = LazyLock::new(|| {
    [
        // ^[ \t]*On\b.*wrote:[ \t]*$
        Re::matcher(r"^[ \t]*On[^\w\n].*wrote:[ \t]*$", 0),
        Re::matcher(r"^[ \t]*\d{4}(?:年|[/.-]).*のメール:[ \t]*$", 0),
    ]
});

/// `_dollar_line`: the span a newline-free pattern ending in `$` can cover,
/// or `None`.
///
/// Without `re.MULTILINE`, `$` matches at the end of the string or before a
/// final `\n`. A pattern none of whose parts can match `\n` can therefore only
/// reach `$` when the string has no `\n` (the whole string), or a single `\n`
/// as its last character (the string without it).
pub(crate) fn dollar_line(text: &str) -> Option<&str> {
    match text.find('\n') {
        None => Some(text),
        Some(nl) if nl + 1 == text.len() => Some(&text[..nl]),
        Some(_) => None,
    }
}

/// The tail of the address attribution form, matched just after the `>`.
static ADDRESS_ATTRIBUTION_TAIL_RE: LazyLock<Re> =
    LazyLock::new(|| Re::matcher(r"[ \t]+wrote:[ \t]*$", 0));

/// `_ends_with_bracketed_address`: true when `.*<[^>]+@[^>]+>` followed by
/// `tail_re` matches `text` at byte offset `start` (`re.match` semantics,
/// `.` excluding `\n`), in linear time.
///
/// `tail_re` must end in `$` and must not be able to match `>`. Then the `>`
/// of any match is the last `>` in `text`, and the `<` lies after the `>`
/// before it, at or after `start`, and before the first `\n` at or after
/// `start`. The leftmost such `<` leaves the widest span for `[^>]+@[^>]+`,
/// which needs an `@` with at least one character on each side. Every bound
/// is next to an ASCII delimiter, so the reference's code point arithmetic
/// holds in bytes; the `@` is searched in the bytes, because `lt + 2` can
/// fall inside a multi-byte character.
pub(crate) fn ends_with_bracketed_address(text: &str, start: usize, tail_re: &Re) -> bool {
    let bytes = text.as_bytes();
    let Some(gt) = text.rfind('>') else {
        return false;
    };
    if gt < start || !tail_re.is_match(&text[gt + 1..]) {
        return false;
    }
    let nl = bytes[start..]
        .iter()
        .position(|&b| b == b'\n')
        .map_or(text.len(), |p| start + p);
    let from = start.max(text[..gt].rfind('>').map_or(0, |p| p + 1));
    let to = gt.min(nl);
    if from >= to {
        return false;
    }
    let Some(lt) = bytes[from..to]
        .iter()
        .position(|&b| b == b'<')
        .map(|p| from + p)
    else {
        return false;
    };
    lt + 2 < gt - 1 && bytes[lt + 2..gt - 1].contains(&b'@')
}

/// `re.match(r"^.*<[^>]+@[^>]+>[ \t]+wrote:[ \t]*$", line)`, linear.
///
/// "<who> <a@b> wrote:" (no leading "On"); "<who> via Datatracker <a@b>
/// wrote:" and similar are covered too.
fn is_address_attribution(line: &str) -> bool {
    ends_with_bracketed_address(line, 0, &ADDRESS_ATTRIBUTION_TAIL_RE)
}

// [ \t]*Am\b (the trailing `\b` rewritten as above)
static AM_PREFIX_RE: LazyLock<Re> = LazyLock::new(|| Re::matcher(r"[ \t]*Am(?:[^\w]|$)", 0));

/// `_SCHRIEB_RE.search(text, pos)` for `\bschrieb\b`: the byte span of the
/// first "schrieb" at or after `pos` with no word character on either side.
/// `\b` at `pos` looks at the character before `pos`, as Python's does.
fn search_schrieb(text: &str, pos: usize) -> Option<(usize, usize)> {
    const WORD: &str = "schrieb";
    let mut from = pos;
    while let Some(off) = text[from..].find(WORD) {
        let start = from + off;
        let end = start + WORD.len();
        let before_ok = !text[..start]
            .chars()
            .next_back()
            .is_some_and(pystr::is_word);
        let after_ok = !text[end..].chars().next().is_some_and(pystr::is_word);
        if before_ok && after_ok {
            return Some((start, end));
        }
        from = start + 1; // "s" is one byte
    }
    None
}

/// `re.match(r"^[ \t]*Am\b.*\bschrieb\b.*:[ \t]*$", line)`, linear.
///
/// German: "Am <date> schrieb <who>:". No part of the pattern matches `\n`,
/// so it applies to [`dollar_line`]'s span as a whole. After the prefix, `.*`
/// can skip anything, so the condition is: a `\bschrieb\b` occurs (it cannot
/// start inside the prefix, which is `[ \t]*Am` followed by a non-word
/// character), and the span, with trailing blanks removed, ends in a `:` at
/// or after the end of the first occurrence.
fn is_german_attribution(line: &str) -> bool {
    let Some(span) = dollar_line(line) else {
        return false;
    };
    if !AM_PREFIX_RE.is_match(span) {
        return false;
    }
    let Some((_, end)) = search_schrieb(line, 0) else {
        return false;
    };
    let body = span.trim_end_matches([' ', '\t']);
    // The ':' at body.len() - 1 lies at or after `end`.
    body.ends_with(':') && body.len() > end
}

// ^[ \t]*(?:(?:On|Am|Le|El|El día)\b|\d{4}(?:年|[/.-]))
static ATTRIBUTION_START_RE: LazyLock<Re> = LazyLock::new(|| {
    Re::matcher(
        r"^[ \t]*(?:(?:On|Am|Le|El|El día)(?:[^\w]|$)|\d{4}(?:年|[/.-]))",
        0,
    )
});

// The terminators, except `\bschrieb\b.*:`, which [`has_attribution_end`]
// decides separately (as in the reference, where `search` retried `.*:` from
// every "schrieb", which is quadratic):
// (?:wrote:|\ba écrit\s*:|\bescribió:|のメール:)[ \t]*$
static ATTRIBUTION_END_RE: LazyLock<Re> = LazyLock::new(|| {
    Re::searcher(
        r"(?:wrote:|(?:^|[^\w])a écrit\s*:|(?:^|[^\w])escribió:|のメール:)[ \t]*$",
        0,
    )
});

/// True when `line` contains a wrapped attribution's terminator.
///
/// Equivalent to `re.search` of the original pattern
/// `(?:wrote:|\bschrieb\b.*:|\ba écrit\s*:|\bescribió:|のメール:)[ \t]*$`.
/// Its `\bschrieb\b.*:[ \t]*$` alternative ends at `$` (the end of the
/// string, or before a final `\n`), so its `:` is the last character other
/// than blanks before that point, and its "schrieb" must lie on the same
/// line as the `:`, ending at or before it. The first "schrieb" at or after
/// the start of that line is the best candidate.
fn has_attribution_end(line: &str) -> bool {
    if ATTRIBUTION_END_RE.is_match(line) {
        return true;
    }
    let body = line
        .strip_suffix('\n')
        .unwrap_or(line)
        .trim_end_matches([' ', '\t']);
    if !body.ends_with(':') {
        return false;
    }
    let colon = body.len() - 1;
    let line_start = body[..colon].rfind('\n').map_or(0, |p| p + 1);
    search_schrieb(line, line_start).is_some_and(|(_, end)| end <= colon)
}

/// True if `line` on its own is a complete attribution line.
pub fn is_attribution_line(line: &str) -> bool {
    ATTRIBUTION_RES.iter().any(|re| re.is_match(line))
        || is_address_attribution(line)
        || is_german_attribution(line)
}

fn wrapped_attribution_end(lines: &[&str], i: usize) -> Option<usize> {
    if !ATTRIBUTION_START_RE.is_match(lines[i]) || has_attribution_end(lines[i]) {
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
        if has_attribution_end(line) {
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

// The tail is `[ \t]*(?:[,.!][ \t]*)?$`, which accepts the same strings as
// the original `[ \t]*[,.!]?[ \t]*$`: written that way, two adjacent
// `[ \t]*` runs split a long blank run in quadratically many ways under a
// backtracking engine before a non-blank character fails the line. (The
// `regex` crate runs either form in linear time; the text follows the
// reference.)
pub(crate) static SIGNOFF_LINE_RE: LazyLock<Re> = LazyLock::new(|| {
    Re::matcher(
        &format!(r"^[ \t]*(?:{SIGNOFF_SALUTATIONS})[ \t]*(?:[,.!][ \t]*)?$"),
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

/// `_pasted_header_runs`: for every index `i`, whether the contiguous
/// header-field run directly above `lines[i]` contains a transport field: a
/// `From:` at `i` then sits inside pasted header evidence rather than opening
/// a client's quote header.
///
/// Computed in one forward pass (the run above `i` is `lines[i - 1]` plus,
/// when that line is a header field, the run above `i - 1`) instead of
/// walking the run upward from every `From:`, which is quadratic when many
/// `From:` lines follow a long run of fields.
fn pasted_header_runs(lines: &[&str]) -> Vec<bool> {
    let mut pasted = vec![false; lines.len()];
    for i in 1..lines.len() {
        let above = lines[i - 1];
        pasted[i] = HEADER_FIELD_RE.is_match(above)
            && (TRANSPORT_HEADER_RE.is_match(above) || pasted[i - 1]);
    }
    pasted
}

/// `(fields, has_signal)` for a walk entering at each index (see
/// [`header_walk_table`]).
type WalkTable = Vec<(usize, bool)>;

/// `_header_walk_table`: the field walk of [`find_quote_header_block`], for
/// every start. See [`WalkTable`].
///
/// The walk from an opener at `i` visits `lines[i + 1..]` and stops at the
/// first line that is neither a header field, nor a folded continuation, nor
/// (`tolerant` only) a single blank directly above a field. Whether it stops
/// at a line depends only on that line, the next one and the mode
/// (`is_localized`, `tolerant`), never on where the walk started. Entry `j`
/// (`0 <= j <= lines.len()`) holds `(fields, has_signal)` for a walk entering
/// at `j`: the number of field lines it passes and whether one of them is a
/// signal field. Built backward in one pass, so each opener is decided in
/// constant time; rewalking from every opener is quadratic when many openers
/// share one long run.
fn header_walk_table(lines: &[&str], is_localized: bool, tolerant: bool) -> WalkTable {
    let is_field = |text: &str| {
        HEADER_FIELD_RE.is_match(text) || (is_localized && LOCALIZED_FIELD_RE.is_match(text))
    };
    let signal_re: &Re = if is_localized {
        &LOCALIZED_SUBJECT_RE
    } else {
        &QUOTE_HEADER_SIGNAL_RE
    };
    let n = lines.len();
    let field: Vec<bool> = lines.iter().map(|l| is_field(l)).collect();
    let mut table = vec![(0usize, false); n + 1];
    for j in (0..n).rev() {
        let nxt = lines[j];
        let (fields, has_signal) = table[j + 1];
        if field[j] {
            table[j] = (fields + 1, has_signal || signal_re.is_match(nxt));
        } else if (pystr::has_content(nxt) && (tolerant || nxt.starts_with([' ', '\t'])))
            // Folded continuation of the preceding header field, or a
            // single blank between fields (double-spaced rendering).
            || (!pystr::has_content(nxt) && tolerant && j + 1 < n && field[j + 1])
        {
            table[j] = (fields, has_signal);
        }
        // Otherwise the walk stops at j: the entry stays (0, false).
    }
    table
}

/// The index of the `From:` line that opens a forwarded/quote-header block,
/// or `None`. `localized` enables the Latin-script localized blocks
/// (`true` in the reference's default).
///
/// The pasted-evidence flags ([`pasted_header_runs`]) and each walk mode's
/// table ([`header_walk_table`]) are built once, on first use, so the search
/// is linear in the number of lines.
pub fn find_quote_header_block(lines: &[&str], localized: bool) -> Option<usize> {
    let mut pasted: Option<Vec<bool>> = None;
    // Indexed by (is_localized, tolerant).
    let mut tables: [[Option<WalkTable>; 2]; 2] = Default::default();
    for (i, line) in lines.iter().enumerate() {
        let is_localized = if FROM_LINE_RE.is_match(line) {
            false
        } else if localized && LOCALIZED_FROM_LINE_RE.is_match(line) {
            true
        } else {
            continue;
        };
        if pasted.get_or_insert_with(|| pasted_header_runs(lines))[i] {
            continue;
        }
        let tolerant = line.contains('@');
        let table = tables[usize::from(is_localized)][usize::from(tolerant)]
            .get_or_insert_with(|| header_walk_table(lines, is_localized, tolerant));
        let (fields, has_signal) = table[i + 1];
        if has_signal && 1 + fields >= if is_localized { 3 } else { 2 } {
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

/// `re.search` of `(?<!-)` followed by `re` in `line`: the start of the first
/// match that is not preceded by a dash.
///
/// The reference prefixes both divider patterns with `(?<!-)`, so that a
/// match can start only at the first dash of a run: without it, a
/// backtracking `search` retried `-{2,}` from every dash of a long rule, which
/// is quadratic. The lookbehind changes no result, because the wording cannot
/// start with a dash: a match from inside a run has its `-{2,}` end at the
/// run's end, and the same match extended to the run's first dash is found
/// first. The `regex` crate cannot express lookbehind; the preceding byte is
/// checked here instead, which keeps the search in the linear-time engine.
/// (By the argument above, the first candidate already passes the check.)
fn search_not_after_dash(re: &Re, line: &str) -> Option<usize> {
    let mut from = 0;
    while let Some((start, _)) = re.find_at(line, from) {
        if start == 0 || line.as_bytes()[start - 1] != b'-' {
            return Some(start);
        }
        from = start + 1; // the match starts with "-", one byte
    }
    None
}

/// The byte offset of the first "Original message" divider in `line`.
fn find_divider(line: &str, localized: bool) -> Option<usize> {
    let english = search_not_after_dash(&ORIGINAL_MESSAGE_DIVIDER_RE, line);
    let local = if localized {
        search_not_after_dash(&LOCALIZED_DIVIDER_RE, line)
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

// --- suffix automaton -----------------------------------------------------------
//
// One routine serves rules (a), (b) and (c): the suffix automaton of a sequence
// of symbols (line ids for rule (a), the bytes of the parent's word-stream for
// rules (b) and (c)). It recognizes exactly the contiguous subsequences of its
// text, has at most 2n states and 3n transitions for a text of n symbols, and
// is built in O(n) transition operations. The transitions are stored by a
// [`Transitions`] implementation: a hash map for line ids (any number of
// distinct symbols), a first-edge slot and an edge list per state for bytes
// (at most 256 edges per state).

/// "No state" / "no position" (the reference's `-1`).
const NONE: usize = usize::MAX;

/// The transition store of a suffix automaton.
trait Transitions: Default {
    type Sym: Copy + Eq;
    /// Reserve room for the automaton of a text of `n` symbols (at most
    /// `2n + 1` states).
    fn reserve(&mut self, n: usize);
    /// Add a state with no transitions.
    fn push_state(&mut self);
    /// The target of `state`'s transition on `sym`.
    fn get(&self, state: usize, sym: Self::Sym) -> Option<usize>;
    /// Add or redirect `state`'s transition on `sym`.
    fn set(&mut self, state: usize, sym: Self::Sym, target: usize);
    /// Give the new, empty state `to` a copy of `from`'s transitions.
    fn copy_state(&mut self, from: usize, to: usize);
}

/// Transitions over line ids: a hash map keyed by `(state, symbol)` (the
/// standard library's randomly keyed SipHash, so adversarial ids cannot
/// collide on purpose), with a per-state list of symbols for copying a state.
#[derive(Default)]
struct LineTransitions {
    map: HashMap<(usize, usize), usize>,
    /// Per state: the index in `syms` of its last added symbol, or [`NONE`].
    head: Vec<usize>,
    /// (symbol, index of the state's previous symbol or [`NONE`]).
    syms: Vec<(usize, usize)>,
}

impl Transitions for LineTransitions {
    type Sym = usize;
    fn reserve(&mut self, n: usize) {
        self.head.reserve(2 * n + 1);
    }
    fn push_state(&mut self) {
        self.head.push(NONE);
    }
    fn get(&self, state: usize, sym: usize) -> Option<usize> {
        self.map.get(&(state, sym)).copied()
    }
    fn set(&mut self, state: usize, sym: usize, target: usize) {
        if self.map.insert((state, sym), target).is_none() {
            self.syms.push((sym, self.head[state]));
            self.head[state] = self.syms.len() - 1;
        }
    }
    fn copy_state(&mut self, from: usize, to: usize) {
        let mut at = self.head[from];
        while at != NONE {
            let (sym, next) = self.syms[at];
            if let Some(target) = self.get(from, sym) {
                self.set(to, sym, target);
            }
            at = next;
        }
    }
}

/// One transition of a [`ByteTransitions`] state.
#[derive(Clone, Copy)]
struct ByteEdge {
    sym: u8,
    /// The target state, or [`NO_EDGE`] in an empty slot.
    target: u32,
    /// Index in the arena of the state's next edge, or [`NO_EDGE`].
    next: u32,
}

const NO_EDGE: u32 = u32::MAX;

const EMPTY_EDGE: ByteEdge = ByteEdge {
    sym: 0,
    target: NO_EDGE,
    next: NO_EDGE,
};

/// Transitions over bytes. Each state has one slot holding its first edge
/// (most states of a suffix automaton have one), and its further edges form a
/// linked list in a single arena; a state has at most 256 edges, so a lookup
/// is O(1). State and edge indexes are `u32`; [`ParentStream`] builds this
/// automaton only for a stream short enough for them
/// ([`MAX_AUTOMATON_BYTES`]).
#[derive(Default)]
struct ByteTransitions {
    first: Vec<ByteEdge>,
    more: Vec<ByteEdge>,
}

impl ByteTransitions {
    /// The edge of `state` on `sym`: `(None, slot)` for the first slot,
    /// `(Some(at), _)` for an arena edge.
    fn find(&self, state: usize, sym: u8) -> Option<Option<usize>> {
        let slot = self.first.get(state)?;
        if slot.target == NO_EDGE {
            return None;
        }
        if slot.sym == sym {
            return Some(None);
        }
        let mut at = slot.next;
        while at != NO_EDGE {
            let edge = &self.more[at as usize];
            if edge.sym == sym {
                return Some(Some(at as usize));
            }
            at = edge.next;
        }
        None
    }
}

impl Transitions for ByteTransitions {
    type Sym = u8;
    fn reserve(&mut self, n: usize) {
        self.first.reserve(2 * n + 1);
        self.more.reserve(n);
    }
    fn push_state(&mut self) {
        self.first.push(EMPTY_EDGE);
    }
    fn get(&self, state: usize, sym: u8) -> Option<usize> {
        match self.find(state, sym)? {
            None => Some(self.first[state].target as usize),
            Some(at) => Some(self.more[at].target as usize),
        }
    }
    fn set(&mut self, state: usize, sym: u8, target: usize) {
        let target = target as u32; // a state index, below 2 * MAX_AUTOMATON_BYTES + 1
        match self.find(state, sym) {
            Some(None) => self.first[state].target = target,
            Some(Some(at)) => self.more[at].target = target,
            None => {
                let slot = &mut self.first[state];
                if slot.target == NO_EDGE {
                    *slot = ByteEdge {
                        sym,
                        target,
                        next: NO_EDGE,
                    };
                } else {
                    let next = slot.next;
                    slot.next = self.more.len() as u32;
                    self.more.push(ByteEdge { sym, target, next });
                }
            }
        }
    }
    fn copy_state(&mut self, from: usize, to: usize) {
        let slot = self.first[from];
        if slot.target == NO_EDGE {
            return;
        }
        self.set(to, slot.sym, slot.target as usize);
        let mut at = slot.next;
        while at != NO_EDGE {
            let edge = self.more[at as usize];
            self.set(to, edge.sym, edge.target as usize);
            at = edge.next;
        }
    }
}

/// A suffix automaton: per state, the length of its longest string, its
/// suffix link, its transitions and `first_end`.
struct Automaton<T> {
    length: Vec<usize>,
    link: Vec<usize>,
    trans: T,
    first_end: Vec<usize>,
}

/// Suffix automaton of `seq[lo..hi]`.
///
/// Per state: the length of its longest string, its suffix link, its
/// transitions and `first_end`, the smallest end position (an index of `seq`)
/// of an occurrence of the state's strings. State 0 is the root. A clone
/// copies `first_end` from the state it splits: the clone's occurrences are
/// that state's plus later positions only.
fn build_automaton<T: Transitions>(seq: &[T::Sym], lo: usize, hi: usize) -> Automaton<T> {
    // At most 2n + 1 states and 3n transitions for n symbols: reserving them
    // avoids copying the arrays as they grow.
    let n = hi.saturating_sub(lo);
    let mut length = Vec::with_capacity(2 * n + 1);
    let mut link = Vec::with_capacity(2 * n + 1);
    let mut first_end = Vec::with_capacity(2 * n + 1);
    let mut trans = T::default();
    trans.reserve(n);
    length.push(0);
    link.push(NONE);
    first_end.push(NONE);
    trans.push_state();
    let mut last = 0;
    for (pos, &sym) in seq.iter().enumerate().take(hi).skip(lo) {
        let cur = length.len();
        length.push(length[last] + 1);
        link.push(NONE);
        trans.push_state();
        first_end.push(pos);
        let mut p = last;
        let mut q = None;
        while p != NONE {
            match trans.get(p, sym) {
                Some(target) => {
                    q = Some(target);
                    break;
                }
                None => {
                    trans.set(p, sym, cur);
                    p = link[p];
                }
            }
        }
        match q {
            None => link[cur] = 0,
            Some(q) => {
                if length[p] + 1 == length[q] {
                    link[cur] = q;
                } else {
                    let clone = length.len();
                    length.push(length[p] + 1);
                    link.push(link[q]);
                    trans.push_state();
                    trans.copy_state(q, clone);
                    first_end.push(first_end[q]);
                    while p != NONE && trans.get(p, sym) == Some(q) {
                        trans.set(p, sym, clone);
                        p = link[p];
                    }
                    link[q] = clone;
                    link[cur] = clone;
                }
            }
        }
        last = cur;
    }
    Automaton {
        length,
        link,
        trans,
        first_end,
    }
}

/// For each `e` in `lo..hi`: the length of the longest run of `seq` ending at
/// `e` that occurs in the automaton's text, and the state of that run (both
/// indexed `e - lo`). Linear: each step adds at most one to the length, and
/// every suffix-link hop removes at least one.
fn walk<T: Transitions>(
    automaton: &Automaton<T>,
    seq: &[T::Sym],
    lo: usize,
    hi: usize,
) -> (Vec<usize>, Vec<usize>) {
    let Automaton {
        length,
        link,
        trans,
        ..
    } = automaton;
    let mut runs = Vec::new();
    let mut states = Vec::new();
    let mut state = 0;
    let mut matched = 0;
    for &sym in seq.iter().take(hi).skip(lo) {
        let mut next = trans.get(state, sym);
        while state != 0 && next.is_none() {
            state = link[state];
            matched = length[state];
            next = trans.get(state, sym);
        }
        match next {
            Some(target) => {
                state = target;
                matched += 1;
            }
            None => matched = 0,
        }
        runs.push(matched);
        states.push(state);
    }
    (runs, states)
}

// --- rule (a): alignment around the longest common run ------------------------------
//
// Inputs are the child's normalized non-blank lines c[0..N) and the parent's
// p[0..M); lines compare by exact string equality. A *common run* is a child
// interval c[s..s+k), k >= 1, that occurs as a contiguous interval of p. A run
// *has substance* when its lines total at least PARENT_DIFF_MIN_BLOCK_WORDS
// words or one of its lines has at least PARENT_DIFF_MIN_LINE_WORDS words.
//
// 1. Anchor. K is the greatest length, counted in lines, of any common run. If
//    K = 0 nothing is marked. The anchor is the common run of length K with
//    the smallest start in the child, c[i..i+K); its parent position j is the
//    smallest start of an occurrence of c[i..i+K) in p.
// 2. The anchor's lines are marked when the anchor has substance.
// 3. Before the anchor, a line of c[0..i) is marked when it lies inside a run
//    of c[0..i) that has substance and occurs contiguously in p[0..j). After
//    the anchor, likewise for c[i+K..N) against p[j+K..M).
//
// The anchor is normally the quoted previous message; step 3 keeps a run the
// author repeated on one side of the quote (a sign-off, say) from matching the
// parent's copy on the other side. Within a side, runs are matched wherever
// they occur, in any order.
//
// Every step uses a suffix automaton over a parent interval: O(N + M) map
// operations on small integer line ids (lines are interned once).

/// The id of a child line absent from the parent (the reference's `-1`),
/// which no transition carries.
const NO_LINE: usize = usize::MAX;

/// Map each distinct parent line to a small integer id; a child line absent
/// from the parent gets [`NO_LINE`].
fn intern_lines(child_seq: &[&str], parent_seq: &[&str]) -> (Vec<usize>, Vec<usize>) {
    let mut ids: HashMap<&str, usize> = HashMap::new();
    let mut parent_ids = Vec::with_capacity(parent_seq.len());
    for &line in parent_seq {
        let next = ids.len();
        parent_ids.push(*ids.entry(line).or_insert(next));
    }
    let child_ids = child_seq
        .iter()
        .map(|line| ids.get(line).copied().unwrap_or(NO_LINE))
        .collect();
    (child_ids, parent_ids)
}

/// The number of words of a line (`len(line.split())`). An ASCII line is
/// counted byte by byte (Python's ASCII whitespace is `\t\n\x0b\x0c\r`,
/// `\x1c`-`\x1f` and the space).
fn word_count(line: &str) -> usize {
    if !line.is_ascii() {
        return line
            .split(pystr::is_space)
            .filter(|w| !w.is_empty())
            .count();
    }
    let mut count = 0;
    let mut in_word = false;
    for &b in line.as_bytes() {
        let space = matches!(b, b'\t'..=b'\r' | 0x1c..=0x1f | b' ');
        if !space && !in_word {
            count += 1;
        }
        in_word = !space;
    }
    count
}

/// Prefix sums over the child: words, and lines of at least
/// `PARENT_DIFF_MIN_LINE_WORDS` words, before each position.
struct Substance {
    words_before: Vec<usize>,
    long_before: Vec<usize>,
}

impl Substance {
    /// Whether the child run `[start, end)` has substance.
    fn of(&self, start: usize, end: usize) -> bool {
        self.words_before[end] - self.words_before[start] >= PARENT_DIFF_MIN_BLOCK_WORDS
            || self.long_before[end] - self.long_before[start] > 0
    }
}

/// Step 3 for one side: add to `cover` (a difference array over child
/// positions) every run of `child_ids[c_lo..c_hi]` that has substance and
/// occurs in `parent_ids[p_lo..p_hi]`. Only the longest run ending at each
/// position is tested: a shorter one ending there lies inside it and has no
/// more words and no longer line.
#[allow(clippy::too_many_arguments)]
fn cover_side(
    child_ids: &[usize],
    c_lo: usize,
    c_hi: usize,
    parent_ids: &[usize],
    p_lo: usize,
    p_hi: usize,
    substance: &Substance,
    cover: &mut [isize],
) {
    if c_lo >= c_hi || p_lo >= p_hi {
        return;
    }
    let automaton = build_automaton::<LineTransitions>(parent_ids, p_lo, p_hi);
    let (runs, _states) = walk(&automaton, child_ids, c_lo, c_hi);
    for (e, &run) in (c_lo..c_hi).zip(&runs) {
        if run == 0 {
            continue;
        }
        let start = e + 1 - run;
        if substance.of(start, e + 1) {
            cover[start] += 1;
            cover[e + 1] -= 1;
        }
    }
}

/// Rule (a): which child lines (indexes of `child_seq`) are parent content.
///
/// Implements the specification in the comment above this section.
pub fn aligned_run_marks(child_seq: &[&str], parent_seq: &[&str]) -> Vec<bool> {
    let count = child_seq.len();
    let mut marks = vec![false; count];
    if count == 0 || parent_seq.is_empty() {
        return marks;
    }
    let (child_ids, parent_ids) = intern_lines(child_seq, parent_seq);

    // Prefix sums: words, and lines of at least PARENT_DIFF_MIN_LINE_WORDS
    // words, before each child position (a run's substance in O(1)).
    let mut words_before = vec![0; count + 1];
    let mut long_before = vec![0; count + 1];
    for (k, line) in child_seq.iter().enumerate() {
        let words = word_count(line);
        words_before[k + 1] = words_before[k] + words;
        long_before[k + 1] = long_before[k] + usize::from(words >= PARENT_DIFF_MIN_LINE_WORDS);
    }
    let substance = Substance {
        words_before,
        long_before,
    };

    // Step 1: the anchor. The first end position reaching the greatest run
    // length (strict comparison) is the earliest-starting longest run; the
    // automaton's first_end gives its earliest parent occurrence.
    let automaton = build_automaton::<LineTransitions>(&parent_ids, 0, parent_ids.len());
    let (runs, states) = walk(&automaton, &child_ids, 0, count);
    let mut best_len = 0;
    let mut best_end = 0;
    for (e, &run) in runs.iter().enumerate() {
        if run > best_len {
            best_len = run;
            best_end = e;
        }
    }
    if best_len == 0 {
        return marks;
    }
    let i = best_end + 1 - best_len;
    let j = automaton.first_end[states[best_end]] + 1 - best_len;
    drop(automaton);

    let mut cover = vec![0isize; count + 1]; // difference array over child positions
                                             // Step 2: the anchor itself.
    if substance.of(i, i + best_len) {
        cover[i] += 1;
        cover[i + best_len] -= 1;
    }
    // Step 3: each side against the same side of the parent.
    cover_side(&child_ids, 0, i, &parent_ids, 0, j, &substance, &mut cover);
    cover_side(
        &child_ids,
        i + best_len,
        count,
        &parent_ids,
        j + best_len,
        parent_ids.len(),
        &substance,
        &mut cover,
    );

    let mut depth = 0;
    for (mark, delta) in marks.iter_mut().zip(&cover) {
        depth += delta;
        *mark = depth > 0;
    }
    marks
}

// --- rules (b) and (c): substring tests against the parent's word-stream ---------

/// `[needle in " ".join(parent_seq) for needle in needles]`, answered by one
/// [`ParentStream`] (the queries share its lazily built table and
/// automaton). Exported for differential testing.
pub fn parent_stream_contains(parent_seq: &[&str], needles: &[&str]) -> Vec<bool> {
    let joined = parent_seq.join(" ");
    let mut stream = ParentStream::new(&joined);
    needles
        .iter()
        .map(|needle| stream.contains(needle))
        .collect()
}

/// The longest stream for which [`ParentStream`] builds its byte automaton
/// (its state and edge indexes are `u32`). A longer stream, over 2 GiB, is
/// searched with `str::contains`, which gives the same answers.
const MAX_AUTOMATON_BYTES: usize = (u32::MAX as usize - 2) / 3;

/// The longest stream for which [`ParentStream`] builds its gram table (its
/// offsets are `u32`). A longer stream, over 4 GiB, skips the table; the
/// queries it would have decided reach the fallback, with the same answers.
const MAX_TABLE_BYTES: usize = u32::MAX as usize - 1;

/// Answers `needle in stream` for the parent's word-stream (the parent's
/// normalized non-blank lines joined by single spaces), exactly, in time
/// linear in the needle.
///
/// Every needle is one or two normalized child lines: words separated by
/// single spaces, as the stream's are, and neither contains other whitespace.
/// In an occurrence, each space of the needle falls on a separator of the
/// stream, so the needle's interior words (all but its first and last) are
/// consecutive whole words of the stream, each at a fixed distance from the
/// needle's start.
///
/// A needle of at least three words is first checked against a table of the
/// stream's *grams*: its words and its adjacent word pairs (`"a b"`). The
/// needle's grams are its interior word when it has three words, else its
/// adjacent pairs of interior words; they are taken in order:
///
/// - a gram absent from the stream proves the needle absent;
/// - a gram that occurs exactly once in the stream fixes the only position at
///   which the needle can start, and one comparison there decides;
/// - when every gram occurs more than once, the first occurrence of the first
///   gram that leaves room for the needle's first word gives one candidate
///   position, and a match there is an occurrence.
///
/// Every other query is answered by the suffix automaton of the stream's
/// bytes, built on the first query that needs it (O(len(stream))); a query
/// walks the needle from the root. The reference indexes code points; for
/// well-formed strings, membership is the same in bytes, and the table's
/// offsets and the comparison at a candidate position are all in bytes.
///
/// The table maps each gram (a slice of the stream) to the byte offsets of its
/// first and last occurrence (as `u32`, to keep it small); it is built in one pass over the stream, on the
/// first query of at least three words.
struct ParentStream<'a> {
    stream: &'a str,
    table: Option<HashMap<&'a str, (u32, u32)>>,
    trans: Option<ByteTransitions>,
}

/// Whether `needle` occurs in `stream` at byte offset `start` (a byte
/// comparison, so `start` need not be a character boundary).
fn starts_at(stream: &str, start: usize, needle: &str) -> bool {
    stream
        .as_bytes()
        .get(start..)
        .is_some_and(|rest| rest.starts_with(needle.as_bytes()))
}

impl<'a> ParentStream<'a> {
    /// The index of `stream`, `" ".join(parent_seq)` (the reference joins the
    /// lines itself; the table's keys borrow the joined string).
    fn new(stream: &'a str) -> Self {
        ParentStream {
            stream,
            table: None,
            trans: None,
        }
    }

    /// The gram table of `stream`, which is at most [`MAX_TABLE_BYTES`] long.
    fn build_table(stream: &'a str) -> HashMap<&'a str, (u32, u32)> {
        let mut table: HashMap<&'a str, (u32, u32)> = HashMap::new();
        let mut words = stream.split(' ').peekable();
        let mut offset = 0;
        while let Some(word) = words.next() {
            let at = offset as u32; // offset < stream.len() <= MAX_TABLE_BYTES
            table
                .entry(word)
                .and_modify(|e| e.1 = at)
                .or_insert((at, at));
            if let Some(next) = words.peek() {
                if let Some(pair) = stream.get(offset..offset + word.len() + 1 + next.len()) {
                    table
                        .entry(pair)
                        .and_modify(|e| e.1 = at)
                        .or_insert((at, at));
                }
            }
            offset += word.len() + 1;
        }
        table
    }

    /// The answer for a needle of at least three `words` when the table
    /// decides it, else `None`.
    fn decided_by_table(&mut self, needle: &str, words: &[&str]) -> Option<bool> {
        let stream = self.stream;
        if stream.len() > MAX_TABLE_BYTES {
            return None;
        }
        let table = self
            .table
            .get_or_insert_with(|| ParentStream::build_table(stream));
        // grams: the interior word, or the interior pairs
        let mut count = words.len().saturating_sub(2);
        if count > 1 {
            count -= 1;
        }
        let mut candidate: Option<usize> = None;
        let mut offset = words.first()?.len() + 1; // the offset of words[k] in the needle
        for k in 1..1 + count {
            let word = *words.get(k)?;
            let gram = if words.len() == 3 {
                word
            } else {
                let next = words.get(k + 1)?;
                needle.get(offset..offset + word.len() + 1 + next.len())?
            };
            let Some(&(first, last)) = table.get(gram) else {
                return Some(false);
            };
            let (first, last) = (first as usize, last as usize);
            if first == last {
                return Some(first >= offset && starts_at(stream, first - offset, needle));
            }
            if candidate.is_none() && first >= offset {
                candidate = Some(first - offset);
            }
            offset += word.len() + 1;
        }
        if candidate.is_some_and(|start| starts_at(stream, start, needle)) {
            return Some(true);
        }
        None
    }

    fn contains(&mut self, needle: &str) -> bool {
        if needle.len() > self.stream.len() {
            return false;
        }
        let words: Vec<&str> = needle.split(' ').collect();
        if words.len() >= 3 {
            if let Some(decided) = self.decided_by_table(needle, &words) {
                return decided;
            }
        }
        if self.stream.len() > MAX_AUTOMATON_BYTES {
            return self.stream.contains(needle);
        }
        let stream = self.stream.as_bytes();
        let trans = self.trans.get_or_insert_with(|| {
            build_automaton::<ByteTransitions>(stream, 0, stream.len()).trans
        });
        let mut state = 0;
        for &byte in needle.as_bytes() {
            match trans.get(state, byte) {
                Some(next) => state = next,
                None => return false,
            }
        }
        true
    }
}

/// Remove lines of `text` that provably came from `parent_body`.
///
/// Both sides are normalized ([`normalize_body`], then per line: quote
/// markers stripped and whitespace collapsed); blank lines are ignored when
/// matching. A child line is removed when it is part of a run of matching
/// lines with at least 10 words in total or one line of at least 8 words,
/// the runs aligned around the longest run of child lines found contiguously
/// in the parent (each child side matched only against the same side of the
/// parent); or when it has at least 8 words and occurs verbatim in the
/// parent's word stream; or, with
/// `continuation` (the reference's keyword-only argument, default `false`),
/// when it continues an adjacent removed line contiguously in the parent's
/// word stream (once at least 3 lines were removed by the first two rules).
/// Survivors are right-stripped, runs of blank lines collapse to one, and
/// blank edges are trimmed. The call runs in time linear in the size of
/// `text` and `parent_body`. [`strip_parent_content_with_quotation`] adds the
/// reference's `quoted_text` argument.
///
/// ```
/// use email_reply_extractor::strip_parent_content;
/// let parent = "The working group last call ends on Friday at noon UTC.";
/// let text = "Agreed.\n\nThe working group last call ends on Friday at noon UTC.";
/// assert_eq!(strip_parent_content(text, parent, false), "Agreed.");
/// ```
pub fn strip_parent_content(text: &str, parent_body: &str, continuation: bool) -> String {
    strip_parent_content_with_quotation(text, parent_body, continuation, None)
}

/// Rule (c): `seeds` extended to the fixpoint of the continuation test. The
/// reference's `_continuation_marks`: an unmarked, unprotected line is marked
/// when an adjacent non-blank line is marked and the two, joined in reading
/// order, occur contiguously in the parent's word-stream. The rule is
/// monotone, so a worklist over the neighbors of newly marked lines reaches
/// the fixpoint.
fn continuation_marks(
    child_seq: &[&str],
    seeds: &[bool],
    protected: &[bool],
    parent_stream: &mut ParentStream,
) -> Vec<bool> {
    let count = child_seq.len();
    let mut is_marked: Vec<bool> = seeds.to_vec();

    // Unmarked `k` joins a marked neighbor contiguously in the parent.
    let mut chains = |k: usize, is_marked: &[bool]| {
        let norm = child_seq[k];
        if k > 0
            && is_marked[k - 1]
            && parent_stream.contains(&format!("{} {norm}", child_seq[k - 1]))
        {
            return true;
        }
        k + 1 < count
            && is_marked[k + 1]
            && parent_stream.contains(&format!("{norm} {}", child_seq[k + 1]))
    };

    let mut pending: Vec<usize> = (0..count)
        .filter(|&k| {
            !is_marked[k]
                && !protected[k]
                && ((k > 0 && is_marked[k - 1]) || (k + 1 < count && is_marked[k + 1]))
        })
        .collect();
    while let Some(k) = pending.pop() {
        if is_marked[k] || protected[k] || !chains(k, &is_marked) {
            continue;
        }
        is_marked[k] = true;
        if k > 0 && !is_marked[k - 1] {
            pending.push(k - 1);
        }
        if k + 1 < count && !is_marked[k + 1] {
            pending.push(k + 1);
        }
    }
    is_marked
}

/// The quote-marked lines of `body`, in order, joined by newlines (the
/// reference's `_marked_quotation`).
fn marked_quotation(body: &str) -> String {
    normalize_body(body)
        .split('\n')
        .filter(|line| is_quote_line(line))
        .collect::<Vec<&str>>()
        .join("\n")
}

/// Rule (d): which child lines (indexes of `child_pairs`) are protected. The
/// reference's `_restored_by_own_quotation`; the comment above it there is
/// the specification. A segment (a maximal run of marked lines within a block
/// of consecutive non-blank lines) is restored when the block's unmarked
/// lines total more words than its marked lines and the quotation stream
/// contains the segment's lines joined by single spaces.
fn restored_by_own_quotation(
    child_pairs: &[(usize, &str)],
    marks: &[bool],
    quoted_seq: &[&str],
) -> Vec<bool> {
    let count = child_pairs.len();
    let mut protected = vec![false; count];
    if count == 0 || quoted_seq.is_empty() {
        return protected;
    }

    // Blocks: consecutive indexes in the text. Words per block, by mark.
    let mut block = vec![0usize; count];
    for k in 1..count {
        block[k] = block[k - 1] + usize::from(child_pairs[k].0 != child_pairs[k - 1].0 + 1);
    }
    let blocks = block[count - 1] + 1;
    let mut marked_words = vec![0usize; blocks];
    let mut unmarked_words = vec![0usize; blocks];
    for k in 0..count {
        let words = word_count(child_pairs[k].1);
        if marks[k] {
            marked_words[block[k]] += words;
        } else {
            unmarked_words[block[k]] += words;
        }
    }

    let joined = quoted_seq.join(" ");
    let mut quotation: Option<ParentStream> = None;
    let mut k = 0;
    while k < count {
        if !marks[k] {
            k += 1;
            continue;
        }
        let start = k;
        while k + 1 < count && marks[k + 1] && block[k + 1] == block[start] {
            k += 1;
        }
        let end = k;
        k += 1;
        let b = block[start];
        if unmarked_words[b] <= marked_words[b] {
            continue; // step 2
        }
        let segment = child_pairs[start..=end]
            .iter()
            .map(|&(_, norm)| norm)
            .collect::<Vec<&str>>()
            .join(" ");
        let stream = quotation.get_or_insert_with(|| ParentStream::new(&joined));
        if !stream.contains(&segment) {
            continue; // step 1
        }
        for flag in &mut protected[start..=end] {
            *flag = true;
        }
    }
    protected
}

/// [`strip_parent_content`] with the message's marked quotation.
///
/// `quoted_text` is the reference's keyword-only argument `quoted_text`
/// (default `None`): the quote-marked lines of the message's body. When it
/// is given, the own-quotation rule applies: a run of removed lines inside a
/// block of consecutive non-blank lines is kept when `quoted_text` contains
/// the run (its lines normalized and joined by single spaces) and the block's
/// other lines total more words than its removed lines. The continuation rule
/// is then evaluated again without the kept lines. With `None`, or a
/// quotation with no non-blank line, the result equals
/// [`strip_parent_content`]. The call runs in time linear in the size of
/// `text`, `parent_body` and `quoted_text`.
///
/// ```
/// use email_reply_extractor::strip_parent_content_with_quotation;
/// let parent = "The working group last call ends on Friday at noon UTC.";
/// let text = "I propose that we extend it by a week, so that instead of now,\n\
///             The working group last call ends on Friday at noon UTC.\n\
///             it ends a week later, on the Friday after the meeting.";
/// let quoted = "> The working group last call ends on Friday at noon UTC.";
/// assert_eq!(
///     strip_parent_content_with_quotation(text, parent, true, Some(quoted)),
///     text
/// );
/// ```
pub fn strip_parent_content_with_quotation(
    text: &str,
    parent_body: &str,
    continuation: bool,
    quoted_text: Option<&str>,
) -> String {
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
    let child_seq: Vec<&str> = child_pairs.iter().map(|&(_, n)| n).collect();
    let parent_seq: Vec<&str> = parent_norm
        .iter()
        .map(String::as_str)
        .filter(|n| !n.is_empty())
        .collect();
    let count = child_pairs.len();
    let count_true = |flags: &[bool]| flags.iter().filter(|&&flag| flag).count();

    // Rule (a): runs of matching lines with enough substance, aligned around
    // the longest common run.
    let mut seeds: Vec<bool> = aligned_run_marks(&child_seq, &parent_seq);

    // Rule (b): a long child line re-wrapped from the parent (line-level match
    // missed it) but still present verbatim in the parent's word-stream.
    let joined = parent_seq.join(" ");
    let mut parent_stream = ParentStream::new(&joined);
    for k in 0..count {
        if seeds[k] {
            continue;
        }
        let norm = child_seq[k];
        if word_count(norm) >= PARENT_DIFF_MIN_LINE_WORDS && parent_stream.contains(norm) {
            seeds[k] = true;
        }
    }

    // Rule (c): chain short remainder lines off marked neighbors, to a fixpoint.
    let unprotected = vec![false; count];
    let mut marks: Vec<bool> =
        if continuation && count_true(&seeds) >= PARENT_DIFF_CONTINUATION_MIN_SEEDS {
            continuation_marks(&child_seq, &seeds, &unprotected, &mut parent_stream)
        } else {
            seeds.clone()
        };

    // Rule (d): restore segments the message also carries in its marked
    // quotation, then re-derive rule (c) without them.
    let quoted_norm: Vec<String> = match quoted_text {
        Some(quoted) => normalize_body(quoted)
            .split('\n')
            .map(normalize_line_for_diff)
            .filter(|n| !n.is_empty())
            .collect(),
        None => Vec::new(),
    };
    let quoted_seq: Vec<&str> = quoted_norm.iter().map(String::as_str).collect();
    let protected = restored_by_own_quotation(&child_pairs, &marks, &quoted_seq);
    if protected.iter().any(|&flag| flag) {
        for k in 0..count {
            seeds[k] = seeds[k] && !protected[k];
        }
        marks = if continuation && count_true(&seeds) >= PARENT_DIFF_CONTINUATION_MIN_SEEDS {
            continuation_marks(&child_seq, &seeds, &protected, &mut parent_stream)
        } else {
            seeds.clone()
        };
    }

    let marked: HashSet<usize> = (0..count)
        .filter(|&k| marks[k])
        .map(|k| child_pairs[k].0)
        .collect();
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
    let mut labels: Vec<String> = Vec::new();
    for line in survivors.split('\n') {
        if let Some((_, end)) = INLINE_REPLY_TAG_RE.find(line) {
            let tag: Vec<char> = pystr::strip(&line[..end]).chars().collect();
            let label: String = if tag.len() >= 2 {
                tag[1..tag.len() - 1].iter().collect()
            } else {
                String::new()
            };
            labels.push(label);
        }
    }
    if labels.is_empty() {
        return false;
    }
    any_author_tag(&labels, &lines[..start].join("\n"), parent_body)
}

/// The longest inline-reply tag label, in characters ([`INLINE_REPLY_TAG_RE`]).
const MAX_TAG_LABEL: usize = 20;

/// True when one of the inline-reply tag `labels` belongs to this message's
/// author.
///
/// A tag the parent never uses is new in this message. A tag the parent uses
/// — or any tag, when the parent is not available — belongs to an earlier
/// round of the thread (as in `[ML] …` answers the parent's author wrote,
/// which a re-wrapped or re-formatted quote can leave unmatched by the
/// parent-diff assist) unless its label, without a trailing round number
/// ("Linda3" → "Linda"), occurs as a word in `head`, the text above the
/// block — typically the author's sign-off ("Cheers,\nMed" for `[Med]`).
///
/// Per label, the tests are `"[" + label in parent_body` and
/// `re.search(r"(?<!\w)" + re.escape(name) + r"(?!\w)", head)`. Labels and
/// names are at most [`MAX_TAG_LABEL`] characters, so both are answered for
/// all labels together in one scan of each text (see [`found_after_bracket`]
/// and [`name_in_head`]), in time linear in the texts' length.
pub fn any_author_tag(labels: &[String], head: &str, parent_body: Option<&str>) -> bool {
    if let Some(parent) = parent_body {
        let found = found_after_bracket(parent, labels);
        for label in labels {
            if !found.contains(label.as_str()) {
                return true;
            }
        }
    }
    let mut names: Vec<&str> = Vec::new();
    for label in labels {
        let name = pystr::strip(label.trim_end_matches(|c: char| c.is_ascii_digit()));
        if !name.is_empty() {
            names.push(name);
        }
    }
    name_in_head(head, &names)
}

/// `strings` grouped by length in characters: entry `n` of the first list
/// holds those of length `n` (entry 0 is unused); the second lists the
/// lengths present, in increasing order. The strings have 1 to
/// [`MAX_TAG_LABEL`] characters; the list grows for a longer one.
fn group_by_length<'s>(strings: &[&'s str]) -> (Vec<HashSet<&'s str>>, Vec<usize>) {
    let mut groups: Vec<HashSet<&str>> = (0..=MAX_TAG_LABEL).map(|_| HashSet::new()).collect();
    for &text in strings {
        let n = pystr::len(text);
        if n >= groups.len() {
            groups.resize_with(n + 1, HashSet::new);
        }
        groups[n].insert(text);
    }
    let lengths = (1..groups.len())
        .filter(|&n| !groups[n].is_empty())
        .collect();
    (groups, lengths)
}

/// The labels `label` for which `"[" + label` occurs in `text`.
///
/// At each `[` of `text`, the slices that follow it are looked up, one per
/// label length present (lengths and slices in characters).
fn found_after_bracket<'s>(text: &str, labels: &'s [String]) -> HashSet<&'s str> {
    let label_strs: Vec<&str> = labels.iter().map(String::as_str).collect();
    let (groups, lengths) = group_by_length(&label_strs);
    let longest = lengths.last().copied().unwrap_or(0);
    let mut found: HashSet<&str> = HashSet::new();
    let mut ends: Vec<usize> = Vec::with_capacity(longest + 1);
    for (pos, _) in text.match_indices('[') {
        let rest = &text[pos + 1..];
        // ends[n]: the byte length of the first n characters of `rest`.
        ends.clear();
        ends.push(0);
        ends.extend(
            rest.char_indices()
                .skip(1)
                .map(|(at, _)| at)
                .chain(std::iter::once(rest.len()))
                .take(longest),
        );
        for &n in &lengths {
            let Some(&end) = ends.get(n) else {
                break;
            };
            if let Some(&label) = groups[n].get(&rest[..end]) {
                found.insert(label);
            }
        }
    }
    found
}

/// True when one of `names` occurs in `head` with no word character
/// (Python's `\w`) directly before or after it.
///
/// At each position not preceded by a word character, the slice of each name
/// length present is looked up when no word character follows it (positions
/// and lengths in characters).
fn name_in_head(head: &str, names: &[&str]) -> bool {
    if names.is_empty() {
        return false;
    }
    let (groups, lengths) = group_by_length(names);
    // bounds[k]: the byte offset of character k (bounds[size] = head.len()).
    let mut bounds: Vec<usize> = Vec::new();
    let mut word: Vec<bool> = Vec::new();
    for (at, c) in head.char_indices() {
        bounds.push(at);
        word.push(pystr::is_word(c));
    }
    let size = word.len();
    bounds.push(head.len());
    for start in 0..size {
        if start > 0 && word[start - 1] {
            continue;
        }
        for &n in &lengths {
            let end = start + n;
            if end > size {
                break;
            }
            if end < size && word[end] {
                continue;
            }
            if groups[n].contains(&head[bounds[start]..bounds[end]]) {
                return true;
            }
        }
    }
    false
}

// --- public entry point -------------------------------------------------------

fn core_extract(body: &str, parent_body: Option<&str>) -> (String, String) {
    let mut normalized = normalize_body(body);
    // The marked quotation is taken from the whole body, before any truncation.
    let quoted_text = marked_quotation(&normalized);

    let lines: Vec<&str> = normalized.split('\n').collect();
    let localized = !localized_inline_reply(&lines, parent_body);
    let pretruncated = find_quote_header_block(&lines, localized).is_some();
    if pretruncated {
        normalized = strip_after_quote_header_block(&lines, localized).join("\n");
    }

    let erp_out = read_fragments(&normalized)
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
            let assisted =
                strip_parent_content_with_quotation(&text, parent, true, Some(&quoted_text));
            if nonblank_line_count(&assisted) < nonblank_line_count(&text) {
                text = assisted;
                method.push_str("+parent-diff");
            }
        }
    }
    (text, method)
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

/// Wrap a resolved `(method, text)` in a result with the right empty status.
///
/// `source` is the body the text was derived from: the `text/plain` body, or
/// the full [`html_to_text`] rendering for the `html-` paths. It is used only
/// to locate the part boundaries ([`part_starts`]).
fn result(method: String, text: String, source: &str) -> ExtractionResult {
    if !pystr::has_content(&text) {
        return ExtractionResult {
            text: String::new(),
            method,
            status: Status::Empty,
            part_starts: Vec::new(),
        };
    }
    let part_starts = safe_part_starts(&text, source);
    ExtractionResult {
        text,
        method,
        status: Status::Ok,
        part_starts,
    }
}

/// The full rendering of `html_body` (novel and quoted text).
fn html_source(html_body: Option<&str>) -> String {
    html_to_text(html_body.unwrap_or(""))
}

/// [`part_starts`] over the normalized `source`; empty on any internal error.
///
/// The part boundaries never decide the text or the status, so a failure here
/// must not turn an extraction into [`Status::Failed`].
fn safe_part_starts(text: &str, source: &str) -> Vec<usize> {
    std::panic::catch_unwind(|| part_starts(text, &normalize_body(source))).unwrap_or_default()
}

/// The indices of the lines of `text` that begin a new part.
///
/// `source` is the normalized body `text` was derived from. Every non-blank
/// line of `text` is matched, in order, to the first source line at or after
/// the previous match whose stripped form is equal. A non-blank source line
/// lying between two consecutive matches is content stage 1 removed, and the
/// second line then begins a new part. Blank lines never begin a part. When a
/// line of `text` has no match, the alignment is abandoned and the result is
/// empty (a single part). The search position only moves forward and each
/// gap is inspected once, so the cost is linear in the two line counts.
fn part_starts(text: &str, source: &str) -> Vec<usize> {
    let src: Vec<&str> = source.split('\n').map(pystr::strip).collect();
    let mut starts = Vec::new();
    let mut pos = 0;
    let mut matched_before = false;
    for (i, line) in text.split('\n').enumerate() {
        let key = pystr::strip(line);
        if key.is_empty() {
            continue;
        }
        // `pos` is one past the previous match, so the lines skipped here are
        // exactly the gap between the two matches.
        let mut gap = false;
        let mut j = pos;
        while j < src.len() && src[j] != key {
            gap |= !src[j].is_empty();
            j += 1;
        }
        if j == src.len() {
            return Vec::new();
        }
        if matched_before && gap {
            starts.push(i);
        }
        matched_before = true;
        pos = j + 1;
    }
    starts
}

fn extract(
    body: Option<&str>,
    parent_body: Option<&str>,
    html_body: Option<&str>,
) -> ExtractionResult {
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
                    let (text, method) = core_extract(&p.novel_text, parent_body);
                    return result(format!("html-{method}"), text, &html_source(html_body));
                }
            }
            return ExtractionResult {
                text: String::new(),
                method: "none".to_string(),
                status: Status::Empty,
                part_starts: Vec::new(),
            };
        }
    };

    // (b) Degenerate plain: a flattened plain body the HTML can replace.
    if let Some(p) = &parts {
        if is_degenerate_plain(body, &p.novel_text) {
            let (text, method) = core_extract(&p.novel_text, parent_body);
            return result(format!("html-{method}"), text, &html_source(html_body));
        }
    }

    // (d) Share-sheet container: the plain part marks the author's own text
    // as quoted; the HTML's novel text holds it.
    if let Some(p) = &parts {
        if p.has_share_container && pystr::has_content(&p.novel_text) {
            let (text, method) = core_extract(&p.novel_text, parent_body);
            return result(format!("html-{method}"), text, &html_source(html_body));
        }
    }

    let (mut text, mut method) = core_extract(body, parent_body);

    // (c) Oracle assist: remove plain-body content present in the HTML's quoted text.
    if let Some(p) = &parts {
        if pystr::has_content(&text) && pystr::has_content(&p.quoted_text) {
            let assisted = strip_parent_content_with_quotation(
                &text,
                &p.quoted_text,
                false,
                Some(&marked_quotation(body)),
            );
            if nonblank_line_count(&text).saturating_sub(nonblank_line_count(&assisted))
                >= HTML_QUOTE_MIN_REMOVED
            {
                text = assisted;
                method.push_str("+html-quote");
            }
        }
    }
    result(method, text, body)
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
///   (HTML-only bodies, a flattened plain body, a Share-sheet container,
///   quoted-text removal).
///
/// Never panics and never fails: an internal error yields
/// `ExtractionResult { text: "", method: "failed", status: Status::Failed,
/// part_starts: vec![] }`.
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
        Ok(r) => r,
        Err(_) => ExtractionResult {
            text: String::new(),
            method: "failed".to_string(),
            status: Status::Failed,
            part_starts: Vec::new(),
        },
    }
}

#[cfg(test)]
mod tests {
    //! The parent-diff matching structures (generation 7), mirroring the
    //! reference's `tests/test_parent_diff.py`: the tie-breaks of rule (a)
    //! and a brute-force transcription of its specification, the parent
    //! word-stream index against `str::contains`, and the author-tag index
    //! against the per-label tests it replaced.

    use super::*;

    /// A small deterministic generator (xorshift64*).
    struct Rng(u64);

    impl Rng {
        fn next(&mut self) -> u64 {
            self.0 ^= self.0 >> 12;
            self.0 ^= self.0 << 25;
            self.0 ^= self.0 >> 27;
            self.0.wrapping_mul(0x2545_f491_4f6c_dd1d)
        }
        /// Uniform in `lo..=hi`.
        fn int(&mut self, lo: usize, hi: usize) -> usize {
            lo + (self.next() % (hi - lo + 1) as u64) as usize
        }
        fn choice<T: Copy>(&mut self, items: &[T]) -> T {
            items[self.int(0, items.len() - 1)]
        }
        fn chance(&mut self, p: f64) -> bool {
            (self.next() % 1_000_000) as f64 / 1_000_000.0 < p
        }
    }

    fn long(tag: &str) -> String {
        format!("{tag} long line with enough words to carry substance")
    }

    fn short(tag: &str) -> String {
        format!("{tag} short")
    }

    fn marks(c: &[String], p: &[String]) -> Vec<bool> {
        let c: Vec<&str> = c.iter().map(String::as_str).collect();
        let p: Vec<&str> = p.iter().map(String::as_str).collect();
        aligned_run_marks(&c, &p)
    }

    // --- rule (a): the specification's tie-breaks ---------------------------------

    #[test]
    fn anchor_tie_in_the_child_takes_the_earliest_start() {
        let (a, b) = (long("a"), long("b"));
        assert_eq!(marks(&[a.clone(), b.clone()], &[b, a]), vec![true, false]);
    }

    #[test]
    fn anchor_takes_the_earliest_parent_occurrence() {
        let (x, a, b, y) = (long("x"), long("A"), long("B"), long("y"));
        let child = [x.clone(), a.clone(), b.clone(), y.clone()];
        let parent = [y, a.clone(), b.clone(), x, long("z"), a, b];
        assert_eq!(marks(&child, &parent), vec![false, true, true, false]);
    }

    #[test]
    fn runs_on_one_side_match_in_any_order() {
        let (p, q) = (long("p"), long("q"));
        let tail = [short("A"), short("B"), short("C")];
        let child: Vec<String> = [p.clone(), q.clone()]
            .into_iter()
            .chain(tail.clone())
            .collect();
        let parent: Vec<String> = [q, p].into_iter().chain(tail).collect();
        assert_eq!(
            marks(&child, &parent),
            vec![true, true, false, false, false]
        );
    }

    #[test]
    fn an_anchor_without_substance_still_splits_the_sequences() {
        let (s, big) = (short("s"), long("L"));
        assert_eq!(
            marks(&[s.clone(), big.clone()], &[big, s]),
            vec![false, false]
        );
    }

    #[test]
    fn the_longest_run_is_the_anchor_even_when_it_starts_later() {
        let (a, x, b, c) = (long("a"), short("x"), long("b"), long("c"));
        assert_eq!(
            marks(&[a.clone(), x, b.clone(), c.clone()], &[b, c, a]),
            vec![false, false, true, true]
        );
    }

    #[test]
    fn a_side_run_has_substance_by_its_total_word_count() {
        let five = ["one two three four five", "six seven eight nine ten"];
        let four = ["aa bb cc dd", "ee ff gg hh"];
        let anchor = [long("m"), long("n"), long("o")];
        let s = |v: &[&str]| v.iter().map(|x| x.to_string()).collect::<Vec<_>>();
        let mut child = s(&five);
        child.extend(anchor.clone());
        child.extend(s(&four));
        let mut parent = s(&five);
        parent.push(short("x"));
        parent.extend(anchor);
        parent.push(short("y"));
        parent.extend(s(&four));
        assert_eq!(
            marks(&child, &parent),
            vec![true, true, true, true, true, false, false]
        );
    }

    #[test]
    fn nothing_is_marked_without_a_common_line() {
        assert_eq!(marks(&[long("a")], &[long("b")]), vec![false]);
        assert_eq!(marks(&[], &[long("b")]), Vec::<bool>::new());
        assert_eq!(marks(&[long("a")], &[]), vec![false]);
    }

    // --- rule (a): brute-force reference ----------------------------------------------

    /// The specification of rule (a), transcribed literally: every run and
    /// every occurrence is enumerated.
    fn reference_marks(c: &[String], p: &[String]) -> Vec<bool> {
        let (n, m) = (c.len(), p.len());
        let mut out = vec![false; n];
        let words: Vec<usize> = c.iter().map(|l| pystr::split_ws(l).len()).collect();
        let occurrences = |s: usize, e: usize, lo: usize, hi: usize| -> Vec<usize> {
            let k = e - s;
            if hi < lo + k {
                return Vec::new();
            }
            (lo..=hi - k).filter(|&j| p[j..j + k] == c[s..e]).collect()
        };
        let substance = |s: usize, e: usize| {
            words[s..e].iter().sum::<usize>() >= 10 || words[s..e].iter().any(|&w| w >= 8)
        };
        let mut runs = Vec::new();
        for s in 0..n {
            for e in s + 1..=n {
                if !occurrences(s, e, 0, m).is_empty() {
                    runs.push((s, e));
                }
            }
        }
        let Some(best) = runs.iter().map(|&(s, e)| e - s).max() else {
            return out;
        };
        let (i, end) = runs
            .iter()
            .copied()
            .filter(|&(s, e)| e - s == best)
            .min()
            .unwrap();
        let j = occurrences(i, end, 0, m)[0];
        if substance(i, end) {
            out[i..end].iter_mut().for_each(|x| *x = true);
        }
        for (c_lo, c_hi, p_lo, p_hi) in [(0, i, 0, j), (end, n, j + best, m)] {
            for s in c_lo..c_hi {
                for e in s + 1..=c_hi {
                    if substance(s, e) && !occurrences(s, e, p_lo, p_hi).is_empty() {
                        out[s..e].iter_mut().for_each(|x| *x = true);
                    }
                }
            }
        }
        out
    }

    fn random_sequences(rng: &mut Rng, max_len: usize) -> (Vec<String>, Vec<String>) {
        let size = rng.choice(&[1, 2, 3, 4, 5, 6]);
        let alphabet: Vec<String> = (0..size)
            .map(|a| {
                let words = rng.choice(&[1, 1, 2, 3, 4, 5, 7, 8, 9]);
                format!("s{a}{}", " w".repeat(words - 1))
            })
            .collect();
        let mut c: Vec<String> = (0..rng.int(0, max_len))
            .map(|_| alphabet[rng.int(0, size - 1)].clone())
            .collect();
        let p: Vec<String> = (0..rng.int(0, max_len))
            .map(|_| alphabet[rng.int(0, size - 1)].clone())
            .collect();
        if !p.is_empty() && rng.chance(0.5) {
            let a = rng.int(0, p.len() - 1);
            let b = rng.int(a + 1, p.len());
            let at = rng.int(0, c.len());
            c.splice(at..at, p[a..b].iter().cloned());
        }
        (c, p)
    }

    #[test]
    fn aligned_run_marks_match_the_reference_on_random_inputs() {
        let mut rng = Rng(20261114);
        for _ in 0..3000 {
            let (c, p) = random_sequences(&mut rng, 8);
            assert_eq!(marks(&c, &p), reference_marks(&c, &p), "{c:?} {p:?}");
        }
    }

    // --- rules (b) and (c): the parent word-stream index ------------------------------

    #[test]
    fn parent_stream_answers_equal_substring_search() {
        let mut rng = Rng(7);
        let vocabs: [&[&str]; 3] = [
            &["a", "b"],
            &["a", "ab", "ba", "b"],
            &["x", "xy", "yx", "y", "z", "\u{e9}", "\u{1f600}"],
        ];
        for _ in 0..4000 {
            let vocab = rng.choice(&vocabs);
            let words: Vec<&str> = (0..rng.int(0, 30)).map(|_| rng.choice(vocab)).collect();
            let mut parent: Vec<String> = Vec::new();
            let mut k = 0;
            while k < words.len() {
                let step = rng.int(1, 5);
                parent.push(words[k..(k + step).min(words.len())].join(" "));
                k += step;
            }
            let stream = parent.join(" ");
            let chars: Vec<char> = stream.chars().collect();
            let mut index = ParentStream::new(&stream);
            for _ in 0..12 {
                let needle = if !chars.is_empty() && rng.chance(0.5) {
                    let lo = rng.int(0, chars.len() - 1);
                    let hi = rng.int(lo + 1, chars.len());
                    let slice: String = chars[lo..hi].iter().collect();
                    let mut needle: Vec<char> = slice.trim_matches(' ').chars().collect();
                    if needle.is_empty() {
                        continue;
                    }
                    if rng.chance(0.3) {
                        let at = rng.int(0, needle.len() - 1);
                        if needle[at] != ' ' {
                            needle[at] = rng.choice(vocab).chars().next().unwrap();
                        }
                    }
                    needle.into_iter().collect::<String>()
                } else {
                    (0..rng.int(1, 8))
                        .map(|_| rng.choice(vocab))
                        .collect::<Vec<_>>()
                        .join(" ")
                };
                assert_eq!(
                    index.contains(&needle),
                    stream.contains(&needle),
                    "{parent:?} {needle:?}"
                );
            }
        }
    }

    #[test]
    fn parent_stream_each_path() {
        let stream = "so the cat sat and the cat ran";
        let mut index = ParentStream::new(stream);
        assert!(!index.contains(&format!("{}x", "x ".repeat(20))));
        assert!(!index.contains("so dog cat"));
        assert!(index.contains("t sat a"));
        assert!(!index.contains("xx so the"));
        assert!(!index.contains("so the sat and"));
        assert!(index.contains("o the cat sat a"));
        assert!(!index.contains("so the cat sat on"));
        assert!(index.contains("o the cat s"));
        assert!(index.contains("d the cat r"));
        assert!(!index.contains("d the cat s"));
        assert!(!index.contains("xso the cat"));
        assert!(index.contains("at an"));
        assert!(!index.contains("ran so"));
        assert!(index.contains("t"));
        // Multi-byte characters: a computed start may fall inside one.
        let stream = "\u{e9}\u{e9} a b \u{1f600} b c";
        let mut index = ParentStream::new(stream);
        for needle in [
            "\u{e9} a b",
            "a b \u{1f600}",
            "\u{1f600} b c",
            "x \u{1f600} b c",
            "b c d",
        ] {
            assert_eq!(
                index.contains(needle),
                stream.contains(needle),
                "{needle:?}"
            );
        }
    }

    // --- the inline-reply author-tag index --------------------------------------------

    /// The generation-6 per-label tests.
    fn reference_any_author_tag(labels: &[String], head: &str, parent_body: Option<&str>) -> bool {
        for label in labels {
            if let Some(parent) = parent_body {
                if !parent.contains(&format!("[{label}")) {
                    return true;
                }
            }
            let name = pystr::strip(label.trim_end_matches(|c: char| c.is_ascii_digit()));
            if name.is_empty() {
                continue;
            }
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
        }
        false
    }

    const TAG_ALPHABET: &[char] = &[
        'a', 'A', 'b', 'M', '1', '_', '\u{e9}', '\u{663}', ' ', '.', '-',
    ];

    #[test]
    fn any_author_tag_equals_the_per_label_tests() {
        let mut rng = Rng(11);
        let text_alphabet: Vec<char> = TAG_ALPHABET.iter().copied().chain(['[', '\n']).collect();
        for _ in 0..4000 {
            let labels: Vec<String> = (0..rng.int(1, 3))
                .map(|_| {
                    (0..rng.int(1, 4))
                        .map(|_| rng.choice(TAG_ALPHABET))
                        .collect()
                })
                .collect();
            let head: String = (0..rng.int(0, 30))
                .map(|_| rng.choice(&text_alphabet))
                .collect();
            let parent = if rng.chance(0.2) {
                None
            } else {
                let mut parent: Vec<char> = (0..rng.int(0, 30))
                    .map(|_| rng.choice(&text_alphabet))
                    .collect();
                for label in &labels {
                    if rng.chance(0.7) {
                        let at = rng.int(0, parent.len());
                        let insert: Vec<char> = std::iter::once('[').chain(label.chars()).collect();
                        parent.splice(at..at, insert);
                    }
                }
                Some(parent.into_iter().collect::<String>())
            };
            let want = reference_any_author_tag(&labels, &head, parent.as_deref());
            assert_eq!(
                any_author_tag(&labels, &head, parent.as_deref()),
                want,
                "{labels:?} {head:?} {parent:?}"
            );
        }
    }

    #[test]
    fn any_author_tag_cases() {
        let l = |s: &str| vec![s.to_string()];
        assert!(any_author_tag(&l("Med"), "", Some("no tags here")));
        assert!(any_author_tag(
            &l("Med2"),
            "Cheers,\nMed",
            Some("[Med2] earlier")
        ));
        assert!(!any_author_tag(
            &l("Med"),
            "Cheers,\nMedina",
            Some("[Med] earlier")
        ));
        assert!(!any_author_tag(
            &l("Med"),
            "Cheers,\n_Med",
            Some("[Med] earlier")
        ));
        assert!(any_author_tag(&l("J. D."), "-- J. D. x", Some("[J. D.]")));
        assert!(!any_author_tag(&l("J. D."), "-- J. D.x", Some("[J. D.]")));
        assert!(!any_author_tag(&l("J. D."), "-- xJ. D.", Some("[J. D.]")));
        assert!(!any_author_tag(&l("12"), "12", Some("[12]")));
        assert!(any_author_tag(&l("Linda3"), "Linda", None));
        assert!(!any_author_tag(&l("Linda3"), "Lindas", None));
        // Non-ASCII word characters at the boundaries, and a label longer
        // than the text after a `[` near its end.
        assert!(!any_author_tag(&l("Med"), "\u{e9}Med", None));
        assert!(!any_author_tag(&l("Med"), "Med\u{663}", None));
        assert!(any_author_tag(&l("\u{e9}d"), "x \u{e9}d", Some("[\u{e9}")));
    }
}

#[cfg(test)]
mod share_sheet_tests {
    //! The Apple Mail Share-sheet container, path (d) (generation 8),
    //! mirroring the Share-sheet tests of the reference's
    //! `tests/test_extraction.py`. The fixture
    //! `toppost-share-sheet-synthetic-01` is covered by the conformance
    //! corpus test.

    use super::*;

    const SHARE_PROSE: [&str; 2] = [
        "The registry proposal in the second draft keeps the allocation policy unchanged.",
        "I suggest we move the open question about expiry to the next interim meeting.",
    ];

    /// An HTML body shaped like Apple Mail's Share sheet around `inner`.
    fn share_html(inner: &str, before: &str, after: &str) -> String {
        format!(
            "<html><head><meta charset=\"utf-8\"></head><body><div dir=\"auto\">\
             <div class=\"Apple-Mail-URLShareUserContentTopClass\">{before}<br></div>\
             <div class=\"Apple-Mail-URLShareWrapperClass\" style=\"position: relative !important;\">\
             <blockquote type=\"cite\" style=\"border-left-style: none; color: inherit;\">\
             {inner}</blockquote></div>{after}<br><br></body></html>"
        )
    }

    /// The text/plain rendering of a Share-sheet body: every line `>`-prefixed.
    fn cite(lines: &[&str]) -> String {
        let quoted: Vec<String> = lines
            .iter()
            .map(|l| {
                if l.is_empty() {
                    ">".to_string()
                } else {
                    format!("> {l}")
                }
            })
            .collect();
        quoted.join("\n") + "\n"
    }

    fn nonblank(text: &str) -> Vec<&str> {
        text.split('\n').filter(|l| pystr::has_content(l)).collect()
    }

    #[test]
    fn share_container_recovers_text_the_plain_part_quotes() {
        let lines = ["Carol,", "", SHARE_PROSE[0], SHARE_PROSE[1], "", "Alice"];
        let html = share_html(&lines.join("<br>"), "", "");
        let body = cite(&lines);
        assert_eq!(
            extract_new_text(Some(&body), None, None).status,
            Status::Empty
        );
        let r = extract_new_text(Some(&body), None, Some(&html));
        assert_eq!(r.status, Status::Ok);
        assert_eq!(r.method, "html-erp");
        assert_eq!(
            r.text,
            format!("Carol,\n\n{}\n\nAlice", SHARE_PROSE.join("\n"))
        );
    }

    #[test]
    fn share_container_keeps_text_typed_outside_the_wrapper_in_order() {
        let html = share_html(
            &SHARE_PROSE.join("<br>"),
            "Forwarding my notes from the review.",
            "<div>Sent from my phone</div>",
        );
        let body = format!(
            "Forwarding my notes from the review.\n\n{}\nSent from my phone\n",
            cite(&SHARE_PROSE)
        );
        let r = extract_new_text(Some(&body), None, Some(&html));
        assert!(r.method.starts_with("html-"));
        assert_eq!(
            nonblank(&r.text),
            [
                "Forwarding my notes from the review.",
                SHARE_PROSE[0],
                SHARE_PROSE[1],
                "Sent from my phone"
            ]
        );
    }

    #[test]
    fn share_container_nested_blockquote_is_removed() {
        let html = share_html(
            &format!(
                "{}<blockquote type=\"cite\">An earlier message that this one quotes in full.</blockquote>",
                SHARE_PROSE.join("<br>")
            ),
            "",
            "",
        );
        let body = cite(&[
            SHARE_PROSE[0],
            SHARE_PROSE[1],
            "",
            "> An earlier message that this one quotes in full.",
        ]);
        let r = extract_new_text(Some(&body), None, Some(&html));
        assert!(r.method.starts_with("html-"));
        assert_eq!(r.text, SHARE_PROSE.join("\n"));
    }

    #[test]
    fn share_container_attribution_and_quoted_lines_are_removed_by_the_core_path() {
        let attribution = "On Mon, 2 Nov 2026 at 10:00, Bob Example <bob@example.org> wrote:";
        let inner = [
            SHARE_PROSE[0],
            "",
            attribution,
            "&gt; The allocation policy should change in the next revision.",
            "&gt; Expiry also needs discussion.",
            "",
            SHARE_PROSE[1],
        ]
        .join("<br>");
        let html = share_html(&inner, "", "");
        let body = cite(&[
            SHARE_PROSE[0],
            "",
            attribution,
            "> The allocation policy should change in the next revision.",
            "> Expiry also needs discussion.",
            "",
            SHARE_PROSE[1],
        ]);
        let r = extract_new_text(Some(&body), None, Some(&html));
        assert_eq!(r.status, Status::Ok);
        assert!(r.method.starts_with("html-"));
        assert!(!r.text.contains("wrote:"));
        assert!(!r.text.contains("allocation policy should change"));
        assert_eq!(nonblank(&r.text), SHARE_PROSE);
    }

    #[test]
    fn share_container_passes_the_parent_to_the_core_path() {
        let parent_lines = [
            "Here is the agenda for the interim meeting next week.",
            "Item one covers the registry and its allocation policy in detail.",
            "Item two covers expiry and the remaining open questions from Prague.",
        ];
        let parent = parent_lines.join("\n");
        let mut lines = vec![SHARE_PROSE[0], ""];
        lines.extend(parent_lines);
        let html = share_html(&lines.join("<br>"), "", "");
        let body = cite(&lines);
        let r = extract_new_text(Some(&body), Some(&parent), Some(&html));
        assert_eq!(r.method, "html-erp+parent-diff");
        assert_eq!(r.text, SHARE_PROSE[0]);
    }

    #[test]
    fn ordinary_cite_blockquote_does_not_take_the_share_path() {
        let html = format!(
            "<div>My reply to the agenda, written below the quote.</div>\
             <blockquote type=\"cite\">{}</blockquote>",
            SHARE_PROSE.join("<br>")
        );
        let body = format!(
            "My reply to the agenda, written below the quote.\n\n{}",
            cite(&SHARE_PROSE)
        );
        let r = extract_new_text(Some(&body), None, Some(&html));
        assert!(!r.method.starts_with("html-"));
        assert_eq!(r.text, "My reply to the agenda, written below the quote.");
    }

    #[test]
    fn share_container_with_blank_novel_text_keeps_the_plain_path() {
        let html = share_html("<br>", "", "");
        let body = cite(&SHARE_PROSE);
        let r = extract_new_text(Some(&body), None, Some(&html));
        assert_eq!(r, extract_new_text(Some(&body), None, None));
        assert_eq!(r.status, Status::Empty);
        assert!(!r.method.starts_with("html-"));
    }

    #[test]
    fn share_shaped_plain_body_without_html_is_unchanged() {
        let body = cite(&SHARE_PROSE);
        assert_eq!(
            extract_new_text(Some(&body), None, None).status,
            Status::Empty
        );
    }

    #[test]
    fn reply_quoting_a_share_sheet_message_keeps_the_plain_path() {
        let reply = "I agree with both points and will update the draft this week.";
        let html = format!(
            "<div>{reply}</div><blockquote type=\"cite\"><div>On Mon, 2 Nov 2026, Bob Example wrote:</div>{}</blockquote>",
            share_html(&SHARE_PROSE.join("<br>"), "", "")
        );
        let body = format!(
            "{reply}\n\nOn Mon, 2 Nov 2026, Bob Example wrote:\n\n{}",
            cite(&SHARE_PROSE)
        );
        let r = extract_new_text(Some(&body), None, Some(&html));
        assert!(!r.method.starts_with("html-"));
        assert_eq!(r.text, reply);
    }
}
