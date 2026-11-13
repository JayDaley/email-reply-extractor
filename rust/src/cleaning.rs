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
    collapse_and_trim, is_signoff_name_like, is_signoff_name_line, normalize_line_for_diff,
    signoff_name_index, SIGNOFF_LINE_RE,
};
use crate::pyre::{Re, IGNORECASE};
use crate::pystr;

/// The outcome of [`clean_for_scoring`] on one stage-1 extraction.
///
/// - `text`: the furniture-free text.
/// - `ignored_lines`: 0-based indices into the input text's `split('\n')` of
///   every non-blank line the clean pass removed, ascending.
#[derive(Clone, Debug, Default, PartialEq, Eq, Hash)]
pub struct CleanResult {
    /// The cleaned text.
    pub text: String,
    /// Indices of the removed non-blank input lines.
    pub ignored_lines: Vec<usize>,
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

static CONTACT_RE: LazyLock<Re> = LazyLock::new(|| {
    Re::matcher(
        concat!(
            r"^[ \t]*(?:Dr|Prof|Mr|Mrs|Ms|Miss|PhD|Herr|Frau|Sir|Dame|Prof\.-Dr)\.?[ \t]+",
            r"\S.*<[^>]+@[^>]+>[ \t]*$",
        ),
        0,
    )
});

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
        r"^[ \t]*(?:\S+[ \t]+){0,2}(?:Office|Address)[ \t]*:[ \t]*[^\n]*\d",
        IGNORECASE,
    )
});

static LIST_FOOTER_RE: LazyLock<Re> = LazyLock::new(|| {
    Re::matcher(
        // ^[ \t]*(?:_{5,}|.*\bmailing list\b.*--.*|To unsubscribe\b.*)[ \t]*$, with
        // each `\b` next to `.*` rewritten as in extraction's attribution
        // patterns (a non-word character that `.*` consumes).
        concat!(
            r"^[ \t]*(?:_{5,}|(?:.*[^\w\n])?mailing list(?:[^\w\n].*)?--.*",
            r"|To unsubscribe(?:[^\w\n].*)?)[ \t]*$",
        ),
        IGNORECASE,
    )
});

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
        || CONTACT_RE.is_match(line)
        || IDENTIFIER_LINE_RE.is_match(line)
        || ADDRESS_LINE_RE.is_match(line)
        || LIST_FOOTER_RE.is_match(line)
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
        let tail: Vec<&str> = lines[name_idx + 1..]
            .iter()
            .copied()
            .filter(|l| pystr::has_content(l))
            .collect();
        if tail.is_empty() {
            continue;
        }
        let all_debris = tail.iter().all(|l| {
            is_droppable_signature_line(l)
                || LINK_ONLY_LINE_RE.is_match(l)
                || LABELED_LINK_LINE_RE.is_match(l)
        });
        if all_debris && (!need_droppable || tail.iter().any(|l| is_droppable_signature_line(l))) {
            return name_idx + 1;
        }
    }
    n
}

// --- greeting / closing sign-off ----------------------------------------------

static GREETING_RE: LazyLock<Re> = LazyLock::new(|| {
    Re::matcher(
        concat!(
            r"^[ \t]*(?:",
            r"hi|hello|hey|dear|greetings|good[ \t]+(?:morning|afternoon|evening|day)",
            r"|hallo|guten[ \t]+(?:tag|morgen|abend)|liebe[rs]?|sehr[ \t]+geehrte[rs]?",
            r"|bonjour|salut|ch(?:è|e)re?s?|hola|estimad[oa]s?|buenos[ \t]+d(?:í|i)as|ciao|ol(?:á|a)",
            r"|beste|geachte|hej|hoi",
            r"|kia[ \t]+ora|t(?:ē|e)n(?:ā|a)[ \t]+koe",
            r")\b(?:[^.!?;:]{0,60}[,!.]|[ \t]*[,!.]?)[ \t]*$",
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
        let next = pairs[pos + 1..]
            .iter()
            .map(|(_, l)| *l)
            .find(|l| pystr::has_content(l));
        if next.is_some_and(is_signoff_name_like) {
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
/// and `ignored_lines` is empty.
///
/// ```
/// use email_reply_extractor::clean_for_scoring;
/// let cleaned = clean_for_scoring("Hi all,\n\nThe draft is ready.\n\nBest,\nAnn", None);
/// assert_eq!(cleaned.text, "The draft is ready.");
/// assert_eq!(cleaned.ignored_lines, vec![0, 4, 5]);
/// ```
pub fn clean_for_scoring(extracted_text: &str, html_signature_text: Option<&str>) -> CleanResult {
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
    CleanResult {
        text,
        ignored_lines,
    }
}
