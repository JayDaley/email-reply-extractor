//! Cleaning: prepare extracted new text for scoring (stage 2 of 2).
//!
//! Port of the reference's `cleaning.py`, whose module docstring is the
//! design documentation (see also `docs/extraction.md`). Stage 1 keeps the
//! author's formulaic furniture; this stage removes it and reports which
//! input lines it removed. The removal steps, in order, run to a bounded
//! fixpoint:
//!
//! 1. the `-- ` signature delimiter (and a custom punctuation-rule divider
//!    with a blank line above and a name line below) and everything after;
//! 2. trailing sign-off debris (links and identifier lines after a sign-off);
//! 3. individually droppable signature debris ("~ Name", titled and corporate
//!    contact lines, identifier and postal-address lines, list footers, PGP);
//! 4. the optional HTML signature hint;
//! 5. one opening greeting line;
//! 6. mobile/client taglines;
//! 7. trailing confidentiality/legal disclaimer paragraphs;
//! 8. one closing sign-off.

use std::collections::HashSet;
use std::sync::LazyLock;

use crate::extraction::{
    collapse_and_trim, dollar_line, ends_with_bracketed_address, is_signoff_name_like,
    is_signoff_name_line, normalize_line_for_diff, signoff_name_index, SIGNOFF_LINE_RE,
};
use crate::pyre::{Re, IGNORECASE};
use crate::pystr;

/// The outcome of [`clean_for_scoring`] on one stage-1 extraction.
///
/// - `text`: the furniture-free text.
/// - `ignored_lines`: 0-based indices into the input text's `split('\n')` of
///   every non-blank line the clean pass removed, ascending.
/// - `parts`: `text` divided at the stage-1 part boundaries passed to
///   [`clean_for_scoring_with_parts`] (see
///   [`crate::ExtractionResult::part_starts`]). Each part holds the surviving
///   lines of one stage-1 part, formatted as `text` is; a part that cleaning
///   emptied is omitted. Without boundaries it is `[text]`, or empty when
///   `text` is empty. The non-blank lines of the parts, in order, are exactly
///   those of `text`.
#[derive(Clone, Debug, Default, PartialEq, Eq, Hash)]
pub struct CleanResult {
    /// The cleaned text.
    pub text: String,
    /// Indices of the removed non-blank input lines.
    pub ignored_lines: Vec<usize>,
    /// The cleaned text divided into the stage-1 parts.
    pub parts: Vec<String>,
}

// --- signature delimiter ------------------------------------------------------

static SIG_DELIM_RE: LazyLock<Re> = LazyLock::new(|| Re::matcher(r"^--[ \t]*$", 0));

/// True for the `-- ` signature delimiter (not "--eh." or "-markku").
pub fn is_signature_delimiter(line: &str) -> bool {
    SIG_DELIM_RE.is_match(line)
}

static CUSTOM_SIG_DIVIDER_RE: LazyLock<Re> =
    LazyLock::new(|| Re::matcher(r"^[ \t]*[=~*#_]{3,}[ \t]*$", 0));

// --- individually droppable signature-debris lines ----------------------------

static TILDE_SIGNOFF_RE: LazyLock<Re> = LazyLock::new(|| Re::matcher(r"^[ \t]*~[ \t]+\S", 0));

// A titled personal contact line that is only a name and a bracketed email.
// The full rule is `^[ \t]*(?:Dr|…|Prof\.-Dr)\.?[ \t]+\S.*<[^>]+@[^>]+>[ \t]*$`;
// as in the reference, only the prefix up to `\S` is a pattern and
// [`is_contact_line`] decides the rest, because a backtracking engine is
// cubic over `.*<[^>]+@[^>]+>` on a line of "<a@" repeated. (The `regex`
// crate runs the full rule in linear time; the split keeps the ports
// aligned.)
static CONTACT_PREFIX_RE: LazyLock<Re> = LazyLock::new(|| {
    Re::matcher(
        r"^[ \t]*(?:Dr|Prof|Mr|Mrs|Ms|Miss|PhD|Herr|Frau|Sir|Dame|Prof\.-Dr)\.?[ \t]+\S",
        0,
    )
});

/// The tail of the contact rule, matched just after the `>`.
static CONTACT_TAIL_RE: LazyLock<Re> = LazyLock::new(|| Re::matcher(r"[ \t]*$", 0));

/// True for a titled contact line (the full rule above), in linear time.
///
/// The prefix pattern has a single way to match: `[ \t]+\S` must take the
/// whole blank run, and no honorific followed by an optional `.` and a blank
/// is a prefix of another one followed the same way. The rest is
/// [`ends_with_bracketed_address`] from just after the `\S`.
fn is_contact_line(line: &str) -> bool {
    CONTACT_PREFIX_RE
        .find(line)
        .is_some_and(|(_, end)| ends_with_bracketed_address(line, end, &CONTACT_TAIL_RE))
}

static PHONE_LINE_RE: LazyLock<Re> = LazyLock::new(|| {
    Re::searcher(
        r"\b(?:tel|telephone|phone|mobile|cell|fax|toll[ -]?free)\b[.:]?[ \t]*[+(]?\d[\d \t().x/-]{5,}",
        IGNORECASE,
    )
});

static CONTACT_CUE_RE: LazyLock<Re> = LazyLock::new(|| {
    Re::searcher(
        concat!(
            r"\bwww\.|https?://",
            r"|\b(?:tel|telephone|phone|mobile|cell|fax|toll[ -]?free)\b",
            r"|\b(?:blvd|boulevard|street|ave|avenue|suite|floor|road|drive|lane)\b",
            r"|\b[A-Za-z]\d[A-Za-z][ \t]?\d[A-Za-z]\d\b",
            r"|,[ \t]*[A-Z]{2}[ \t]+\d{5}\b",
        ),
        IGNORECASE,
    )
});

static IDENTIFIER_LINE_RE: LazyLock<Re> = LazyLock::new(|| {
    Re::matcher(
        concat!(
            r"^[ \t]*(?:(?-i:[A-Z])[\w.()&-]*[ \t]+){0,2}",
            r"(?:ORCID|LinkedIn|GitHub|GitLab|Mastodon|Bluesky|Twitter|Skype|Signal",
            r"|Telegram|WhatsApp|WeChat|Matrix|IRC|Website?|Homepage|Blog|E-?mail|D-?U-?N-?S)",
            r"[ \t]*(?::[ \t]*\S|<https?://[^>]+>[ \t]*$)",
        ),
        IGNORECASE,
    )
});

static ADDRESS_LINE_RE: LazyLock<Re> = LazyLock::new(|| {
    Re::matcher(
        r"^[ \t]*(?:\S+[ \t]+){0,2}(?:Office|Address)[ \t]*:[^\n]*\d",
        IGNORECASE,
    )
});

// Mailing-list footer debris. The full rule is
// `^[ \t]*(?:_{5,}|.*\bmailing list\b.*--.*|To unsubscribe\b.*)[ \t]*$`
// (IGNORECASE); as in the reference, [`is_list_footer`] decides the middle
// alternative without the pattern, over which a backtracking engine is
// quadratic on a line of "mailing list" repeated. The remaining `\b` sits
// next to `.*` and is rewritten as in extraction's attribution patterns (a
// non-word character that `.*` consumes).
static LIST_FOOTER_RE: LazyLock<Re> = LazyLock::new(|| {
    Re::matcher(
        // ^[ \t]*(?:_{5,}|To unsubscribe\b.*)[ \t]*$
        r"^[ \t]*(?:_{5,}|To unsubscribe(?:[^\w\n].*)?)[ \t]*$",
        IGNORECASE,
    )
});

/// The literal of `\bmailing list\b` (IGNORECASE); [`search_mailing_list`]
/// checks the boundaries.
static MAILING_LIST_RE: LazyLock<Re> = LazyLock::new(|| Re::searcher(r"mailing list", IGNORECASE));

/// `_MAILING_LIST_RE.search(text)` for `\bmailing list\b`: the byte end of
/// the first case-insensitive "mailing list" with no word character on
/// either side.
fn search_mailing_list(text: &str) -> Option<usize> {
    let mut from = 0;
    while let Some((start, end)) = MAILING_LIST_RE.find_at(text, from) {
        let before_ok = !text[..start]
            .chars()
            .next_back()
            .is_some_and(pystr::is_word);
        let after_ok = !text[end..].chars().next().is_some_and(pystr::is_word);
        if before_ok && after_ok {
            return Some(end);
        }
        from = start + text[start..].chars().next().map_or(1, char::len_utf8);
    }
    None
}

/// True for a mailing-list footer line (the full rule above), in linear time.
///
/// No part of the rule can match `\n`, so it can only match within
/// [`dollar_line`]'s span; checking that first also keeps the `.*[ \t]*$` of
/// the pattern from scanning a line it cannot match. Within the span, `.*`
/// can skip anything, so the middle alternative holds exactly when a `--`
/// follows the end of the first "mailing list".
fn is_list_footer(line: &str) -> bool {
    let Some(span) = dollar_line(line) else {
        return false;
    };
    if LIST_FOOTER_RE.is_match(line) {
        return true;
    }
    search_mailing_list(span).is_some_and(|end| span[end..].contains("--"))
}

static PGP_RE: LazyLock<Re> = LazyLock::new(|| {
    Re::matcher(
        // ^[ \t]*(?:-----(?:BEGIN|END) PGP|PGP key-(?:id|fingerprint)\b)
        r"^[ \t]*(?:-----(?:BEGIN|END) PGP|PGP key-(?:id|fingerprint)(?:[^\w]|$))",
        IGNORECASE,
    )
});

fn is_contact_signature_line(line: &str) -> bool {
    if PHONE_LINE_RE.is_match(line) {
        return true;
    }
    line.contains('|') && CONTACT_CUE_RE.is_match(line)
}

fn is_droppable_signature_line(line: &str) -> bool {
    TILDE_SIGNOFF_RE.is_match(line)
        || is_contact_line(line)
        || IDENTIFIER_LINE_RE.is_match(line)
        || ADDRESS_LINE_RE.is_match(line)
        || is_list_footer(line)
        || PGP_RE.is_match(line)
        || is_contact_signature_line(line)
}

// --- trailing sign-off debris -------------------------------------------------

static LINK_ONLY_LINE_RE: LazyLock<Re> = LazyLock::new(|| {
    Re::matcher(
        concat!(
            r"^[ \t]*(?:",
            r"https?://\S+",
            r"|www\.\S+",
            r"|[\w.+-]+@[\w-]+(?:\.[\w-]+)+",
            r"|(?:[\w-]+\.){1,6}[A-Za-z]{2,24}(?:/\S*)?",
            r")[ \t]*$",
        ),
        0,
    )
});

static LABELED_LINK_LINE_RE: LazyLock<Re> = LazyLock::new(|| {
    Re::matcher(
        r"^[ \t]*[^:\n]{1,40}:[ \t]*(?:https?://|www\.)\S+[ \t]*$",
        0,
    )
});

fn strip_trailing_signoff_debris(lines: &[&str]) -> usize {
    let n = lines.len();
    // Facts about every tail lines[p..], built on the first anchor in one
    // backward pass, so each anchor is decided in constant time (rebuilding
    // and reclassifying the tail for every anchor is quadratic).
    let mut tails: Option<Vec<TailFacts>> = None;
    for i in 0..n {
        let mut need_droppable = false;
        let name_idx = match signoff_name_index(lines, i) {
            Some(idx) => idx,
            None => {
                if !is_signoff_name_line(lines[i]) {
                    continue;
                }
                need_droppable = true;
                i
            }
        };
        let facts = tails.get_or_insert_with(|| tail_debris_table(lines))[name_idx + 1];
        if !facts.nonblank {
            continue;
        }
        if facts.all_debris && (!need_droppable || facts.any_droppable) {
            return name_idx + 1;
        }
    }
    n
}

/// Facts about the non-blank lines of a tail `lines[p..]`.
#[derive(Clone, Copy)]
struct TailFacts {
    /// There is one.
    nonblank: bool,
    /// Every one is debris (droppable, link-only or labeled-link).
    all_debris: bool,
    /// One is droppable.
    any_droppable: bool,
}

/// `_tail_debris_table`: [`TailFacts`] for every `p` (`0 <= p <= len`).
fn tail_debris_table(lines: &[&str]) -> Vec<TailFacts> {
    let mut table = vec![
        TailFacts {
            nonblank: false,
            all_debris: true,
            any_droppable: false,
        };
        lines.len() + 1
    ];
    for p in (0..lines.len()).rev() {
        let line = lines[p];
        let next = table[p + 1];
        table[p] = if pystr::has_content(line) {
            let droppable = is_droppable_signature_line(line);
            let debris = droppable
                || LINK_ONLY_LINE_RE.is_match(line)
                || LABELED_LINK_LINE_RE.is_match(line);
            TailFacts {
                nonblank: true,
                all_debris: next.all_debris && debris,
                any_droppable: next.any_droppable || droppable,
            }
        } else {
            next
        };
    }
    table
}

// --- greeting / closing sign-off ----------------------------------------------

// The bare form's tail is `(?:[ \t]*[,!.])?[ \t]*$`, which accepts the same
// strings as the original `[ \t]*[,!.]?[ \t]*$`: written that way, two
// adjacent `[ \t]*` runs split a long blank run in quadratically many ways
// before a non-blank character fails the line. The `\b` makes this a
// `fancy-regex` (backtracking) pattern here, so the rewrite matters in this
// port too.
static GREETING_RE: LazyLock<Re> = LazyLock::new(|| {
    Re::matcher(
        concat!(
            r"^[ \t]*(?:",
            r"hi|hello|hey|dear|greetings|good[ \t]+(?:morning|afternoon|evening|day)",
            r"|hallo|guten[ \t]+(?:tag|morgen|abend)|liebe[rs]?|sehr[ \t]+geehrte[rs]?",
            r"|bonjour|salut|ch(?:è|e)re?s?|hola|estimad[oa]s?|buenos[ \t]+d(?:í|i)as|ciao|ol(?:á|a)",
            r"|beste|geachte|hej|hoi",
            r"|kia[ \t]+ora|t(?:ē|e)n(?:ā|a)[ \t]+koe",
            r")\b(?:[^.!?;:]{0,60}[,!.]|(?:[ \t]*[,!.])?)[ \t]*$",
        ),
        IGNORECASE,
    )
});

/// `strip_closing_signoff`: the length of the kept prefix.
fn strip_closing_signoff(lines: &[&str]) -> usize {
    let nonblank: Vec<usize> = (0..lines.len())
        .filter(|&i| pystr::has_content(lines[i]))
        .collect();
    let Some(&last) = nonblank.last() else {
        return lines.len();
    };
    for &i in &nonblank[nonblank.len().saturating_sub(2)..] {
        if signoff_name_index(lines, i) == Some(last) {
            return i;
        }
    }
    if SIGNOFF_LINE_RE.is_match(lines[last]) {
        return last;
    }
    lines.len()
}

// --- confidentiality / legal disclaimers ----------------------------------------

static DISCLAIMER_START_RE: LazyLock<Re> = LazyLock::new(|| {
    Re::matcher(
        concat!(
            r"^[ \t]*(?:",
            r"(?:CONFIDENTIALITY|LEGAL|PRIVACY)[ \t]+(?:NOTICE|NOTE|DISCLAIMER|STATEMENT)",
            r"|DISCLAIMER[ \t]*[:\-]",
            r"|This[ \t]+(?:e-?mail|message|communication)\b[^.]{0,120}?",
            r"\b(?:confidential|privileged|intended[ \t]+(?:only|solely)[ \t]+for)",
            r"|The[ \t]+information[ \t]+(?:contained|transmitted)\b[^.]{0,80}?",
            r"\b(?:confidential|privileged)",
            r"|If[ \t]+you[ \t]+(?:are[ \t]+not[ \t]+the[ \t]+intended[ \t]+recipient",
            r"|(?:have[ \t]+)?received[ \t]+this[ \t]+(?:e-?mail|message)[ \t]+in[ \t]+error)",
            r")",
        ),
        IGNORECASE,
    )
});

// --- mobile / client taglines -------------------------------------------------

static MOBILE_TAGLINE_RE: LazyLock<Re> = LazyLock::new(|| {
    Re::matcher(
        concat!(
            r"^[ \t]*(?:",
            r"Sent from my \w+",
            r"|Sent from Proton Mail",
            r"|Sent from Mail for Windows",
            r"|Sent from Outlook",
            r"|Get Outlook for \w+",
            r")",
        ),
        IGNORECASE,
    )
});

/// True for a mobile/webmail client tagline ("Sent from my iPhone").
pub fn is_mobile_tagline(line: &str) -> bool {
    MOBILE_TAGLINE_RE.is_match(line)
}

// --- the passes ---------------------------------------------------------------

type Pairs<'a> = Vec<(usize, &'a str)>;

fn truncate_at_signature_delimiter(pairs: Pairs<'_>) -> Pairs<'_> {
    match pairs.iter().position(|(_, l)| is_signature_delimiter(l)) {
        Some(pos) => pairs[..pos].to_vec(),
        None => pairs,
    }
}

fn truncate_at_custom_signature_divider(pairs: Pairs<'_>) -> Pairs<'_> {
    for (pos, (_, line)) in pairs.iter().enumerate() {
        if !CUSTOM_SIG_DIVIDER_RE.is_match(line) {
            continue;
        }
        if pos > 0 && pystr::has_content(pairs[pos - 1].1) {
            continue;
        }
        // The first non-blank line after the divider, found by stepping over
        // the blank run, as in the reference (whose slice of the rest of the
        // list made every divider cost the length of the text). Divider
        // lines are non-blank, so the runs stepped over for different
        // dividers do not overlap.
        let mut nxt_pos = pos + 1;
        while nxt_pos < pairs.len() && !pystr::has_content(pairs[nxt_pos].1) {
            nxt_pos += 1;
        }
        if nxt_pos < pairs.len() && is_signoff_name_like(pairs[nxt_pos].1) {
            return pairs[..pos].to_vec();
        }
    }
    pairs
}

fn lines_of<'a>(pairs: &Pairs<'a>) -> Vec<&'a str> {
    pairs.iter().map(|(_, l)| *l).collect()
}

fn strip_greeting(pairs: Pairs<'_>) -> Pairs<'_> {
    if let Some(pos) = pairs.iter().position(|(_, l)| pystr::has_content(l)) {
        if GREETING_RE.is_match(pairs[pos].1) {
            let mut out = pairs;
            out.remove(pos);
            return out;
        }
    }
    pairs
}

// https?://|\bwww\.  (the leading `\b` rewritten as in LIST_FOOTER_RE)
static URL_RE: LazyLock<Re> =
    LazyLock::new(|| Re::searcher(r"https?://|(?:^|[^\w])www\.", IGNORECASE));

fn is_html_signature_line(line: &str, hint_norms: &HashSet<String>) -> bool {
    let norm = normalize_line_for_diff(line);
    if norm.is_empty() || !hint_norms.contains(&norm) {
        return false;
    }
    pystr::split_ws(&norm).len() >= 2
        || norm.chars().any(pystr::is_digit)
        || norm.contains('@')
        || URL_RE.is_match(&norm)
}

fn drop_trailing_disclaimer_paragraphs(pairs: Pairs<'_>) -> Pairs<'_> {
    let mut paragraphs: Vec<usize> = Vec::new();
    let mut start: Option<usize> = None;
    for (pos, (_, line)) in pairs.iter().enumerate() {
        if pystr::has_content(line) {
            if start.is_none() {
                start = Some(pos);
            }
        } else if let Some(s) = start.take() {
            paragraphs.push(s);
        }
    }
    if let Some(s) = start {
        paragraphs.push(s);
    }
    let mut cut = None;
    for &p_start in paragraphs.iter().rev() {
        if DISCLAIMER_START_RE.is_match(pairs[p_start].1) {
            cut = Some(p_start);
            continue;
        }
        break;
    }
    match cut {
        Some(c) => pairs[..c].to_vec(),
        None => pairs,
    }
}

fn clean_pass<'a>(pairs: Pairs<'a>, hint_norms: &HashSet<String>) -> Pairs<'a> {
    let pairs = truncate_at_signature_delimiter(pairs);
    let pairs = truncate_at_custom_signature_divider(pairs);
    let keep = strip_trailing_signoff_debris(&lines_of(&pairs));
    let pairs: Pairs<'a> = pairs[..keep].to_vec();
    let pairs: Pairs<'a> = pairs
        .into_iter()
        .filter(|(_, l)| !is_droppable_signature_line(l))
        .collect();
    let pairs: Pairs<'a> = if hint_norms.is_empty() {
        pairs
    } else {
        pairs
            .into_iter()
            .filter(|(_, l)| !is_html_signature_line(l, hint_norms))
            .collect()
    };
    let pairs = strip_greeting(pairs);
    let pairs: Pairs<'a> = pairs
        .into_iter()
        .filter(|(_, l)| !is_mobile_tagline(l))
        .collect();
    let pairs = drop_trailing_disclaimer_paragraphs(pairs);
    let keep = strip_closing_signoff(&lines_of(&pairs));
    pairs[..keep].to_vec()
}

const MAX_CLEAN_PASSES: usize = 4;

/// Strip formulaic furniture from stage-1 `extracted_text` for scoring.
///
/// `html_signature_text` is the reference's optional argument (default
/// `None`): the visible text of the message's HTML signature container
/// ([`crate::HtmlParts::signature_text`]). Body lines whose normalized form
/// equals one of its non-blank lines are dropped when they carry substance
/// (two or more words, or a digit, `@` or URL).
///
/// Never panics. When there is nothing to clean, `text` is the trimmed input
/// and `ignored_lines` is empty. `parts` is `[text]`, or empty when `text` is
/// empty; [`clean_for_scoring_with_parts`] divides it at stage-1 part
/// boundaries.
///
/// ```
/// use email_reply_extractor::clean_for_scoring;
/// let cleaned = clean_for_scoring("Hi all,\n\nThe draft is ready.\n\nBest,\nAnn", None);
/// assert_eq!(cleaned.text, "The draft is ready.");
/// assert_eq!(cleaned.ignored_lines, vec![0, 4, 5]);
/// ```
pub fn clean_for_scoring(extracted_text: &str, html_signature_text: Option<&str>) -> CleanResult {
    clean_for_scoring_with_parts(extracted_text, html_signature_text, &[])
}

/// [`clean_for_scoring`] with the reference's keyword argument `part_starts`.
///
/// `part_starts` is the stage-1 result's
/// [`crate::ExtractionResult::part_starts`]. Indices outside
/// `1 ..= lines - 1` (where `lines` is the number of lines of
/// `extracted_text.split('\n')`) are ignored, and the rest are used in
/// ascending order without duplicates. It changes only
/// [`CleanResult::parts`], never `text` or `ignored_lines`.
///
/// ```
/// use email_reply_extractor::{clean_for_scoring_with_parts, extract_new_text};
/// let body = "Yes.\n\n> Second question?\n\nNo.\n\nThanks,\nAnn";
/// let stage1 = extract_new_text(Some(body), None, None);
/// assert_eq!(stage1.part_starts, vec![2]);
/// let cleaned = clean_for_scoring_with_parts(&stage1.text, None, &stage1.part_starts);
/// assert_eq!(cleaned.parts, vec!["Yes.", "No."]);
/// ```
pub fn clean_for_scoring_with_parts(
    extracted_text: &str,
    html_signature_text: Option<&str>,
    part_starts: &[usize],
) -> CleanResult {
    let hint_norms: HashSet<String> = html_signature_text
        .unwrap_or("")
        .split('\n')
        .map(normalize_line_for_diff)
        .filter(|n| !n.is_empty())
        .collect();

    let original: Vec<&str> = extracted_text.split('\n').collect();
    let mut pairs: Pairs<'_> = original.iter().copied().enumerate().collect();
    for _ in 0..MAX_CLEAN_PASSES {
        let before = pairs.len();
        pairs = clean_pass(pairs, &hint_norms);
        if pairs.len() == before {
            break;
        }
    }

    let kept: HashSet<usize> = pairs.iter().map(|(i, _)| *i).collect();
    let ignored_lines = original
        .iter()
        .enumerate()
        .filter(|(i, l)| pystr::has_content(l) && !kept.contains(i))
        .map(|(i, _)| i)
        .collect();
    let text = collapse_and_trim(pairs.iter().map(|(_, l)| pystr::rstrip(l)));

    let mut starts: Vec<usize> = part_starts
        .iter()
        .copied()
        .filter(|&s| 0 < s && s < original.len())
        .collect();
    starts.sort_unstable();
    starts.dedup();
    let parts = if text.is_empty() {
        Vec::new()
    } else if starts.is_empty() {
        vec![text.clone()]
    } else {
        let mut groups: Vec<Vec<&str>> = vec![Vec::new(); starts.len() + 1];
        for (idx, line) in &pairs {
            groups[starts.partition_point(|&s| s <= *idx)].push(line);
        }
        groups
            .iter()
            .map(|g| collapse_and_trim(g.iter().map(|l| pystr::rstrip(l))))
            .filter(|p| !p.is_empty())
            .collect()
    };
    CleanResult {
        text,
        ignored_lines,
        parts,
    }
}
