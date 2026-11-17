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
//! 5. up to two opening greeting lines per text ([`crate::greetings`], with
//!    the addressees);
//! 6. mobile/client taglines;
//! 7. trailing confidentiality/legal disclaimer paragraphs;
//! 8. one closing sign-off (shared and tolerant forms; a thanks to an
//!    addressee is kept; an exposed one-line form needs a plain name);
//!
//! and, with a sender name ([`CleanOptions::author_name`]):
//!
//! 9. a sender-name sign-off above a non-blocking tail;
//! 10. a trailing sender name or initial;
//! 11. salutation + sender-name sign-offs anywhere;
//! 12. a lone sender-name line away from the tail.
//!
//! When a pass removes nothing, the learned-line step runs and the passes
//! resume ([`CleanOptions::signature_lines`]).

use std::collections::{HashMap, HashSet};
use std::sync::LazyLock;

use crate::extraction::{
    collapse_and_trim, dollar_line, ends_with_bracketed_address, is_signoff_name_like,
    is_signoff_name_line, normalize_line_for_diff, signoff_name_index, SIGNOFF_LINE_RE,
    SIGNOFF_SALUTATIONS,
};
use crate::greetings::is_opening_greeting;
use crate::names::{
    is_author_initial_line, is_author_name_line, matches_addressee, matches_author,
    parse_addressee_names, parse_author_name, Names, LOOSE_NAME_TOKEN_RE, NAME_DECORATION,
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

// --- salutations ------------------------------------------------------------------

/// Valedictions beyond the list stage 1 shares ([`SIGNOFF_SALUTATIONS`]): the
/// lines found directly above a sender-name line in mailing-list mail.
const EXTRA_SALUTATIONS: &str = concat!(
    r"with\s+(?:best|kind|warm(?:est)?)\s+regards|with\s+thanks|all\s+best|",
    r"thanks\s+(?:a\s+lot|in\s+advance|very\s+much|all|folks|everyone)|thankyou|",
    r"thank\s+you\s+(?:very\s+much|again|all)|many\s+thx|",
    r"take\s+care|so\s+long|see\s+you\s+soon|warmly|later|greetings|ave|",
    r"r[’']s|b\.?\s?r\.?|brgds|thanx|",
    r"(?:a|à)\s+bient(?:ô|o)t|cordialmente",
);

/// `_SALUTATION`: one salutation, or two joined ("Thanks and regards",
/// "Regards/Ngā mihi", "Thanks, Cheers").
fn salutation() -> String {
    let one = format!("(?:{EXTRA_SALUTATIONS}|{SIGNOFF_SALUTATIONS})");
    format!(r"{one}(?:[ \t]*(?:[/,&+]|and)[ \t]*{one})?")
}

/// Punctuation after a salutation: "," "，" "、" ":" a run of "!", one to
/// three "." or none.
const SALUTATION_PUNCT: &str = r"(?:[,:，、]|!+|\.{1,3})";

/// A line that is only a salutation, with the tolerant list and punctuation.
static SALUTATION_LINE_RE: LazyLock<Re> = LazyLock::new(|| {
    Re::matcher(
        &format!(
            r"^[ \t]*{}[ \t]*(?:{SALUTATION_PUNCT}[ \t]*)?$",
            salutation()
        ),
        IGNORECASE,
    )
});

/// The one-line form: salutation, punctuation, name ("Later, Jane").
static SALUTATION_INLINE_RE: LazyLock<Re> = LazyLock::new(|| {
    Re::matcher(
        &format!(
            r"^[ \t]*{}[ \t]*(?P<punct>{SALUTATION_PUNCT})[ \t]*(?P<name>\S.*)$",
            salutation()
        ),
        IGNORECASE,
    )
});

/// The one-line form with a decorated name and no punctuation ("Thanks -Jane").
static SALUTATION_DECORATED_RE: LazyLock<Re> = LazyLock::new(|| {
    Re::matcher(
        &format!(
            r"^[ \t]*{}[ \t]+(?P<name>[-–—~/]+[ \t]*\S.*)$",
            salutation()
        ),
        IGNORECASE,
    )
});

/// Any one-line form: optional punctuation, then the rest of the line.
static SALUTATION_ANY_INLINE_RE: LazyLock<Re> = LazyLock::new(|| {
    Re::matcher(
        &format!(
            r"^[ \t]*{}[ \t]*(?:[,:，!.][ \t]*)?(?P<name>\S.*)$",
            salutation()
        ),
        IGNORECASE,
    )
});

/// The one-line form with only blanks after the salutation ("Thanks Jane").
static SALUTATION_SPACE_INLINE_RE: LazyLock<Re> = LazyLock::new(|| {
    Re::matcher(
        &format!(r"^[ \t]*{}[ \t]+(?P<name>\S.*)$", salutation()),
        IGNORECASE,
    )
});

const INLINE_COMMAS: &[&str] = &[",", "，", "、"];

/// `_strip_name_decoration`: the name without decoration and trailing
/// punctuation, and whether decoration was removed.
fn strip_name_decoration(text: &str) -> (&str, bool) {
    let stripped = pystr::strip(text);
    let core = pystr::lstrip_chars(stripped, NAME_DECORATION);
    (pystr::rstrip_chars(core, " \t.,;:!"), core != stripped)
}

/// `_is_loose_signoff_name`: a sign-off name in the tolerant forms.
///
/// A name line ([`is_signoff_name_like`]) whose first four words are
/// capitalized, when decorated ("— Jane Doe") or when `allow_plain`; or a
/// single word ([`LOOSE_NAME_TOKEN_RE`]) that is decorated ("-jane"),
/// indented, lower-case ("jane"), or any word when `allow_plain`.
fn is_loose_signoff_name(text: &str, allow_plain: bool) -> bool {
    let (core, decorated) = strip_name_decoration(text);
    if core.is_empty() {
        return false;
    }
    let words = pystr::split_ws(core);
    if (decorated || allow_plain)
        && is_signoff_name_like(core)
        && words.iter().take(4).all(|w| first_is_upper(w))
    {
        return true;
    }
    let indented = text.starts_with([' ', '\t']);
    if words.len() == 1 && LOOSE_NAME_TOKEN_RE.is_match(core) {
        return decorated
            || indented
            || core.chars().next().is_some_and(pystr::is_lower)
            || allow_plain;
    }
    false
}

/// `word[:1].isupper()`.
fn first_is_upper(word: &str) -> bool {
    word.chars().next().is_some_and(pystr::is_upper)
}

/// `_tolerant_signoff_name_index`: [`signoff_name_index`] with the tolerant
/// forms.
///
/// After the shared forms: a salutation line ([`SALUTATION_LINE_RE`]) whose
/// next non-blank line is a loose name (plain names allowed); the one-line
/// form with punctuation whose name is a decorated or lower-case loose name,
/// or, after a comma, a name line; the decorated one-line form.
fn tolerant_signoff_name_index(lines: &[&str], i: usize) -> Option<usize> {
    if let Some(shared) = signoff_name_index(lines, i) {
        return Some(shared);
    }
    let line = lines[i];
    if SALUTATION_LINE_RE.is_match(line) {
        let mut j = i + 1;
        while j < lines.len() && !pystr::has_content(lines[j]) {
            j += 1;
        }
        if j < lines.len() && is_loose_signoff_name(lines[j], true) {
            return Some(j);
        }
        return None;
    }
    if let Some(spans) = SALUTATION_INLINE_RE.named_spans(line, &["punct", "name"]) {
        let (Some((ps, pe)), Some((ns, ne))) = (spans[0], spans[1]) else {
            return None;
        };
        let name = &line[ns..ne];
        if is_loose_signoff_name(name, false) {
            return Some(i);
        }
        if INLINE_COMMAS.contains(&&line[ps..pe]) && is_signoff_name_like(pystr::strip(name)) {
            return Some(i);
        }
        return None;
    }
    if let Some(name) = SALUTATION_DECORATED_RE.named(line, "name") {
        if is_loose_signoff_name(name, false) {
            return Some(i);
        }
    }
    None
}

// --- thanks to an addressee -------------------------------------------------------

static THANKS_SALUTATION_RE: LazyLock<Re> = LazyLock::new(|| {
    Re::matcher(
        r"^[ \t]*(?:many[ \t]+|with[ \t]+)?(?:thanks|thank[ \t]*you|thx|thanx)\b",
        IGNORECASE,
    )
});

/// A sentence break after a word of 2+ letters (not a title) and more text.
static FURTHER_WORDS_RE: LazyLock<Re> = LazyLock::new(|| {
    Re::searcher(
        r"(?<![\w.])(?!(?:dr|mr|mrs|ms|mx|prof|jr|sr|st)[.])[^\W\d_]{2,}[.!?;:][ \t]+\S",
        IGNORECASE,
    )
});

/// `_NAME_LIST_SPLIT_RE.split(text)` (`[\s,/&+]+`) without the empty pieces.
fn name_list_split(text: &str) -> Vec<&str> {
    text.split(|c: char| pystr::is_space(c) || ",/&+".contains(c))
        .filter(|w| !w.is_empty())
        .collect()
}

/// `_is_thanks_to_addressee`: true when the one-line thanks form `line`
/// thanks a participant.
///
/// `line` opens with a thanks salutation ([`THANKS_SALUTATION_RE`]) and
/// whitespace precedes the name ("Thanks,Jane" is a sign-off whose line break
/// was lost). It is content when a sentence follows the name ("Thanks, Jane.
/// That works.") or when no word of the name matches the sender and a word
/// of it matches an addressee.
fn is_thanks_to_addressee(line: &str, author: &Names, addressees: Option<&Names>) -> bool {
    if !THANKS_SALUTATION_RE.is_match(line) {
        return false;
    }
    let Some(Some((ns, ne))) = SALUTATION_ANY_INLINE_RE
        .named_spans(line, &["name"])
        .map(|s| s[0])
    else {
        return false;
    };
    if !line[..ns].chars().next_back().is_some_and(pystr::is_space) {
        return false;
    }
    let name = pystr::lstrip_chars(&line[ns..ne], " ,，");
    if FURTHER_WORDS_RE.is_match(name) {
        return true;
    }
    let words = name_list_split(pystr::strip_chars(name, " \t.,;:!-–—~*_"));
    if words.iter().any(|w| matches_author(w, author)) {
        return false;
    }
    words.iter().any(|w| matches_addressee(w, addressees))
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

/// `pairs` without the positions in `drop` (sorted or not).
fn without_positions<'a>(pairs: Pairs<'a>, drop: &HashSet<usize>) -> Pairs<'a> {
    if drop.is_empty() {
        return pairs;
    }
    pairs
        .into_iter()
        .enumerate()
        .filter(|(p, _)| !drop.contains(p))
        .map(|(_, pair)| pair)
        .collect()
}

/// `_strip_opening_greetings`: drop up to two opening greeting lines, within
/// the text's greeting budget.
fn strip_opening_greetings<'a>(mut pairs: Pairs<'a>, state: &mut CleanState) -> Pairs<'a> {
    for _ in 0..MAX_GREETINGS {
        if state.greetings_left == 0 {
            break;
        }
        let Some(pos) = pairs.iter().position(|(_, l)| pystr::has_content(l)) else {
            break;
        };
        let next_line = pairs.get(pos + 1).map(|(_, l)| *l);
        if !is_opening_greeting(pairs[pos].1, next_line, state.addressees.as_ref()) {
            break;
        }
        pairs.remove(pos);
        state.greetings_left -= 1;
    }
    pairs
}

static PUNCT_BEFORE_BLANK_RE: LazyLock<Re> = LazyLock::new(|| Re::searcher(r"[.,;:!?][ \t]", 0));

/// A sign-off finder: the index of the name line of a sign-off opening at a
/// line.
type NameFinder = fn(&[&str], usize) -> Option<usize>;

/// `_strip_closing_signoff`: drop a closing sign-off from the end of the text.
///
/// In order, on the last two non-blank lines: a shared sign-off
/// ([`signoff_name_index`]) whose name is the last line, then a tolerant one
/// ([`tolerant_signoff_name_index`]), each truncating at its first line; then
/// a last line that is a salutation alone ([`SALUTATION_LINE_RE`]). A one-line
/// thanks form that thanks a participant ([`is_thanks_to_addressee`], only
/// with a sender name) is kept. A one-line form on a line exposed by a
/// sender-name rule (`state.exposed`) is removed only when its name is a plain
/// name: a name line, capitalized, with no punctuation followed by a blank.
fn strip_closing_signoff<'a>(pairs: Pairs<'a>, state: &CleanState) -> Pairs<'a> {
    let lines = lines_of(&pairs);
    let nonblank: Vec<usize> = (0..lines.len())
        .filter(|&i| pystr::has_content(lines[i]))
        .collect();
    let Some(&last) = nonblank.last() else {
        return pairs;
    };
    if state.exposed.contains(&pairs[last].0) {
        if let Some(name) = SALUTATION_ANY_INLINE_RE.named(lines[last], "name") {
            if !SIGNOFF_LINE_RE.is_match(lines[last]) {
                let name = pystr::rstrip_chars(pystr::lstrip_chars(name, " ,，"), " .!");
                let plain = is_signoff_name_line(name)
                    && !PUNCT_BEFORE_BLANK_RE.is_match(name)
                    && pystr::split_ws(name).iter().all(|w| first_is_upper(w));
                if !plain {
                    return pairs;
                }
            }
        }
    }
    let finders: [NameFinder; 2] = [signoff_name_index, tolerant_signoff_name_index];
    let tail = &nonblank[nonblank.len().saturating_sub(2)..];
    for find_name in finders {
        for &i in tail {
            if find_name(&lines, i) == Some(last) {
                if let Some(author) = &state.author {
                    if i == last
                        && is_thanks_to_addressee(lines[i], author, state.addressees.as_ref())
                    {
                        return pairs;
                    }
                }
                return pairs[..i].to_vec();
            }
        }
    }
    if SALUTATION_LINE_RE.is_match(lines[last]) {
        return pairs[..last].to_vec();
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

// --- sign-offs with the sender's name ---------------------------------------------

/// A rule line drawn directly above a signature name ("—", "---", "___").
static ABOVE_NAME_DIVIDER_RE: LazyLock<Re> =
    LazyLock::new(|| Re::matcher(r"^[ \t]*[-–—_=~*]+[ \t]*$", 0));
/// A footnote or reference line ("[1] https://…").
static FOOTNOTE_RE: LazyLock<Re> = LazyLock::new(|| Re::matcher(r"^[ \t]*\[\d{1,3}\]", 0));
/// A postscript ("PS", "P.S.", "p.p.s.").
static POSTSCRIPT_RE: LazyLock<Re> =
    LazyLock::new(|| Re::matcher(r"^[ \t]*p\.?[ \t]?(?:p\.?[ \t]?)?s\b", IGNORECASE));
static SENTENCE_END_RE: LazyLock<Re> = LazyLock::new(|| Re::searcher(r"[.?!:;]\s*$", 0));
/// Lower-case words a signature line may hold besides capitalized ones.
const SIGNATURE_CONNECTORS: &[&str] = &[
    "of", "and", "for", "the", "at", "de", "in", "&", "und", "et", "y", "du", "von", "van", "der",
    "la", "le",
];
/// A rule line of three or more `-_=*~#`.
static RULE_LINE_RE: LazyLock<Re> = LazyLock::new(|| Re::matcher(r"^[ \t]*[-_=*~#]{3,}[ \t]*$", 0));
static LIST_BULLET_RE: LazyLock<Re> =
    LazyLock::new(|| Re::matcher(r"^[ \t]*(?:[*•+]|\d+[.)])[ \t]", 0));

fn is_link_line(s: &str) -> bool {
    LINK_ONLY_LINE_RE.is_match(s) || LABELED_LINK_LINE_RE.is_match(s)
}

/// `_is_attached_signature_line`: a line of a signature block under the
/// sender's name.
///
/// Signature debris, a link-only or "Label: URL" line; or a line of at most 8
/// words, not ending in sentence punctuation (`.?!:;`), that holds a digit,
/// `@` or a URL, or whose letter-initial words, connectors
/// ([`SIGNATURE_CONNECTORS`]) aside, are mostly capitalized. Never a footnote
/// line.
fn is_attached_signature_line(line: &str) -> bool {
    let s = pystr::strip(line);
    if s.is_empty() || FOOTNOTE_RE.is_match(s) {
        return false;
    }
    if is_link_line(s) || is_droppable_signature_line(s) {
        return true;
    }
    let words = pystr::split_ws(s);
    if words.len() > 8 || SENTENCE_END_RE.is_match(s) {
        return false;
    }
    if s.chars().any(pystr::is_digit) || s.contains('@') || URL_RE.is_match(s) {
        return true;
    }
    let alpha: Vec<&str> = words
        .into_iter()
        .filter(|w| {
            w.chars().next().is_some_and(pystr::is_alpha)
                && !SIGNATURE_CONNECTORS.contains(&pystr::lower(w).as_str())
        })
        .collect();
    if alpha.is_empty() {
        return false;
    }
    alpha.iter().filter(|w| first_is_upper(w)).count() * 2 > alpha.len()
}

/// `_is_nonblocking_tail_line`: a line that may follow a sign-off without
/// making it content (blank, a footnote, a link-only or "Label: URL" line,
/// signature debris, a client tagline, or a rule line).
fn is_nonblocking_tail_line(line: &str) -> bool {
    let s = pystr::strip(line);
    s.is_empty()
        || FOOTNOTE_RE.is_match(s)
        || is_link_line(s)
        || is_droppable_signature_line(s)
        || is_mobile_tagline(s)
        || CUSTOM_SIG_DIVIDER_RE.is_match(s)
        || RULE_LINE_RE.is_match(s)
}

/// `_kept_tail`: the lines kept from the non-blocking tail below a sign-off.
///
/// Blank lines and footnotes stay, and so do link lines when the tail holds a
/// footnote (they are its references); a postscript and everything after it
/// stay. Everything else (debris, taglines, rule lines, bare links) goes.
fn kept_tail<'a>(tail: &[(usize, &'a str)]) -> Pairs<'a> {
    let keep_links = tail.iter().any(|(_, l)| FOOTNOTE_RE.is_match(l));
    let mut kept: Pairs<'a> = Vec::new();
    for (pos, &(idx, line)) in tail.iter().enumerate() {
        if POSTSCRIPT_RE.is_match(line) {
            kept.extend_from_slice(&tail[pos..]);
            return kept;
        }
        let s = pystr::strip(line);
        if !s.is_empty() && !FOOTNOTE_RE.is_match(s) && !(is_link_line(s) && keep_links) {
            continue;
        }
        kept.push((idx, line));
    }
    kept
}

/// `_previous_nonblank`: the position of the last non-blank line above `pos`.
fn previous_nonblank(pairs: &[(usize, &str)], pos: usize) -> Option<usize> {
    (0..pos).rev().find(|&k| pystr::has_content(pairs[k].1))
}

/// `_mark_exposed`: record the non-blank line above `pos`, which a removal
/// exposes as the tail.
fn mark_exposed(pairs: &[(usize, &str)], pos: usize, exposed: &mut HashSet<usize>) {
    if let Some(k) = previous_nonblank(pairs, pos) {
        exposed.insert(pairs[k].0);
    }
}

/// `_divider_above`: `pos`, or the position of a rule line at most one blank
/// line above it.
fn divider_above(pairs: &[(usize, &str)], pos: usize) -> usize {
    match previous_nonblank(pairs, pos) {
        Some(k) if pos - k <= 2 && ABOVE_NAME_DIVIDER_RE.is_match(pairs[k].1) => k,
        _ => pos,
    }
}

/// `_drop_signoff_above_tail`: drop the first sender-name line followed only
/// by signature and tail lines.
///
/// The sender's name line, the attached signature lines directly below it,
/// and the salutation line above it (or else a rule line directly above the
/// name) are removed when everything below is non-blocking
/// ([`is_nonblocking_tail_line`]) up to the end or up to a postscript. Of that
/// tail, [`kept_tail`] decides what stays. A name line that is the last
/// non-blank line on its own is left to [`drop_trailing_author_name`].
fn drop_signoff_above_tail<'a>(
    pairs: Pairs<'a>,
    author: &Names,
    exposed: &mut HashSet<usize>,
) -> Pairs<'a> {
    let n = pairs.len();
    // nonblocking[p]: every line of pairs[p..] is non-blocking, or a
    // postscript comes first.
    let mut nonblocking = vec![true; n + 1];
    let mut blank_from = vec![true; n + 1];
    // attached_end[p]: the end of the run of attached signature lines at p.
    let mut attached_end: Vec<usize> = (0..=n).collect();
    for p in (0..n).rev() {
        let line = pairs[p].1;
        nonblocking[p] =
            POSTSCRIPT_RE.is_match(line) || (is_nonblocking_tail_line(line) && nonblocking[p + 1]);
        blank_from[p] = blank_from[p + 1] && !pystr::has_content(line);
        if is_attached_signature_line(line) {
            attached_end[p] = attached_end[p + 1];
        }
    }
    for i in 0..n {
        if !is_author_name_line(pairs[i].1, Some(author)) {
            continue;
        }
        let j = attached_end[i + 1];
        if !nonblocking[j] || (j == i + 1 && blank_from[j]) {
            continue;
        }
        let start = match previous_nonblank(&pairs, i) {
            Some(k) if SALUTATION_LINE_RE.is_match(pairs[k].1) => k,
            _ => divider_above(&pairs, i),
        };
        mark_exposed(&pairs, start, exposed);
        let mut out = pairs[..start].to_vec();
        out.extend(kept_tail(&pairs[j..]));
        return out;
    }
    pairs
}

/// `_drop_trailing_author_name`: drop a last non-blank line that is the
/// sender's name or decorated initial, with a rule line directly above it.
fn drop_trailing_author_name<'a>(
    pairs: Pairs<'a>,
    author: &Names,
    exposed: &mut HashSet<usize>,
) -> Pairs<'a> {
    let Some(pos) = previous_nonblank(&pairs, pairs.len()) else {
        return pairs;
    };
    let line = pairs[pos].1;
    let author = Some(author);
    if is_author_name_line(line, author) || is_author_initial_line(line, author) {
        let start = divider_above(&pairs, pos);
        mark_exposed(&pairs, start, exposed);
        return pairs[..start].to_vec();
    }
    pairs
}

/// `_drop_author_signoffs`: drop every salutation + sender-name sign-off,
/// wherever it stands.
///
/// The two-line form (a salutation line, then the sender's name as the next
/// non-blank line) and the one-line form ([`SALUTATION_INLINE_RE`], else
/// [`SALUTATION_SPACE_INLINE_RE`], whose name is the sender's).
fn drop_author_signoffs<'a>(pairs: Pairs<'a>, author: &Names) -> Pairs<'a> {
    let n = pairs.len();
    let mut drop: HashSet<usize> = HashSet::new();
    for i in 0..n {
        let line = pairs[i].1;
        if !pystr::has_content(line) {
            continue;
        }
        if SALUTATION_LINE_RE.is_match(line) {
            let mut j = i + 1;
            while j < n && !pystr::has_content(pairs[j].1) {
                j += 1;
            }
            if j < n && is_author_name_line(pairs[j].1, Some(author)) {
                drop.insert(i);
                drop.insert(j);
                continue;
            }
        }
        let name = SALUTATION_INLINE_RE
            .named(line, "name")
            .or_else(|| SALUTATION_SPACE_INLINE_RE.named(line, "name"));
        if name.is_some_and(|name| is_author_name_line(name, Some(author))) {
            drop.insert(i);
        }
    }
    without_positions(pairs, &drop)
}

/// `_without_trailing_parenthetical`: `line` without a final `(…)` of at
/// most 60 characters and the blanks around it, or `None` when it has none.
fn without_trailing_parenthetical(line: &str) -> Option<&str> {
    let s = pystr::rstrip_chars(line, " \t");
    let inner_end = s.strip_suffix(')')?.len();
    let open_paren = s.rfind('(')?;
    let inner = &s[open_paren + 1..inner_end];
    if inner.contains(')') || pystr::len(inner) > 60 {
        return None;
    }
    Some(pystr::rstrip_chars(&s[..open_paren], " \t"))
}

/// `_is_name_shaped`: a capitalized name line, a trailing parenthesis aside
/// (a list of names).
fn is_name_shaped(line: &str) -> bool {
    let s = pystr::strip(without_trailing_parenthetical(line).unwrap_or(line));
    is_signoff_name_like(s)
        && pystr::split_ws(s)
            .iter()
            .filter(|w| pystr::is_alpha_str(w))
            .all(|w| first_is_upper(w))
}

/// `_drop_lone_author_name`: drop a sender-name line away from the tail with
/// no salutation above it.
///
/// Every non-blank line but the first and the last qualifies when it is the
/// sender's name, does not end in `:` or `,`, is not a list item, and the
/// non-blank line above is not a salutation, not name-shaped
/// ([`is_name_shaped`]) and does not end in `:`. A name line with a trailing
/// parenthesis followed by a name-shaped line is part of a list too.
fn drop_lone_author_name<'a>(pairs: Pairs<'a>, author: &Names) -> Pairs<'a> {
    let nonblank: Vec<usize> = (0..pairs.len())
        .filter(|&p| pystr::has_content(pairs[p].1))
        .collect();
    let mut drop: HashSet<usize> = HashSet::new();
    for k in 1..nonblank.len().saturating_sub(1) {
        let p = nonblank[k];
        let line = pairs[p].1;
        if !is_author_name_line(line, Some(author)) {
            continue;
        }
        if pystr::rstrip(line).ends_with([':', ',']) || LIST_BULLET_RE.is_match(line) {
            continue;
        }
        let above = pairs[nonblank[k - 1]].1;
        if SALUTATION_LINE_RE.is_match(above)
            || is_name_shaped(above)
            || pystr::rstrip(above).ends_with(':')
        {
            continue;
        }
        let below = pairs[nonblank[k + 1]].1;
        if without_trailing_parenthetical(line).is_some() && is_name_shaped(below) {
            continue;
        }
        drop.insert(p);
    }
    without_positions(pairs, &drop)
}

// --- learned signature lines -------------------------------------------------------

/// [`learn_signature_lines`] thresholds.
const LEARN_MIN_MESSAGES: usize = 5;
const LEARN_MIN_COUNT: usize = 3;
const LEARN_MIN_SUBJECTS: usize = 3;
const LEARN_TAIL_LINES: usize = 10;
/// A non-learned line in the trailing zone has at most this many words.
const LEARNED_ZONE_MAX_WORDS: usize = 4;

/// The reply/forward prefixes and bracketed list tags at the start of a
/// subject, any number of them ("Re: [list] AW: Fwd[2]: …"). The pattern can
/// match the empty string, so it always matches.
static SUBJECT_PREFIXES_RE: LazyLock<Re> = LazyLock::new(|| {
    Re::matcher(
        r"(?:\s*(?:\[[^\]]*\]|(?:re|fw|fwd|aw|sv|antw|wg|tr)(?:\s*\[\d+\])?\s*:)\s*)*",
        IGNORECASE,
    )
});
const SENTENCE_PUNCT_END: [char; 3] = ['.', '?', '!'];

/// `_lower`: Python's `str.lower` with `ß` (U+00DF) written `ss`.
fn learned_lower(text: &str) -> String {
    pystr::lower(text).replace('\u{df}', "ss")
}

/// `_learned_norm`: a line as learned lines are compared: normalized for the
/// parent diff (quote markers and whitespace runs removed), then lowercased
/// with `ß` written `ss`.
fn learned_norm(line: &str) -> String {
    learned_lower(&normalize_line_for_diff(line))
}

/// `_subject_key`: a subject without its leading reply/forward prefixes and
/// list tags, whitespace collapsed, lowercased.
fn subject_key(subject: &str) -> String {
    let end = SUBJECT_PREFIXES_RE.find(subject).map_or(0, |(_, e)| e);
    learned_lower(&pystr::collapse_ws(&subject[end..]))
}

/// Learn the furniture lines one sender repeats at the end of their
/// messages.
///
/// `texts` are the stage-1 texts of one sender's messages, and `subjects`
/// (optional) their subjects, one per text. Lines are compared in a
/// normalized form: quote markers removed, whitespace runs collapsed to one
/// space, edges stripped, lowercased (Python's `str.lower`, then `ß` written
/// `ss`). A normalized line is learned when it is among the last 10 non-blank
/// lines of at least 3 of the texts and of at least one fifth of them, and,
/// with `subjects`, when those texts have at least 3 distinct subjects
/// (compared without leading "Re:"/"Fwd:"-style prefixes and "\[list\]" tags,
/// whitespace collapsed, lowercased as above). Fewer than 5 texts learn
/// nothing. A text with no corresponding subject (`subjects` shorter than
/// `texts`) has the empty subject, and subjects beyond the last text are
/// ignored.
///
/// A candidate that ends in ":" or is a sentence (at least 3 words ending in
/// `.`, `?` or `!`) is the first line of a block where the sender's own words
/// would be, and is learned only when, in at least half of its occurrences
/// among those last lines, the line just above is another candidate.
///
/// Returns the learned lines in their normalized form, sorted by code point;
/// pass them as [`CleanOptions::signature_lines`]. Pure.
///
/// ```
/// use email_reply_extractor::{
///     clean_for_scoring_with_options, learn_signature_lines, CleanOptions,
/// };
/// let texts: Vec<String> = (0..5)
///     .map(|i| format!("Body {i} text here.\n\nJane Example\nExample Corp"))
///     .collect();
/// let texts: Vec<&str> = texts.iter().map(String::as_str).collect();
/// let learned = learn_signature_lines(&texts, None);
/// assert_eq!(learned, vec!["example corp", "jane example"]);
///
/// let lines: Vec<&str> = learned.iter().map(String::as_str).collect();
/// let options = CleanOptions { signature_lines: &lines, ..CleanOptions::default() };
/// let cleaned = clean_for_scoring_with_options("New text.\n\nJane Example\nExample Corp", &options);
/// assert_eq!(cleaned.text, "New text.");
/// ```
pub fn learn_signature_lines(texts: &[&str], subjects: Option<&[&str]>) -> Vec<String> {
    if texts.len() < LEARN_MIN_MESSAGES {
        return Vec::new();
    }
    let mut tails: Vec<Vec<String>> = Vec::with_capacity(texts.len());
    let mut occurrences: HashMap<String, Vec<usize>> = HashMap::new();
    for (i, text) in texts.iter().enumerate() {
        let mut norms: Vec<String> = text
            .split('\n')
            .map(learned_norm)
            .filter(|n| !n.is_empty())
            .collect();
        let tail = norms.split_off(norms.len().saturating_sub(LEARN_TAIL_LINES));
        let distinct: HashSet<&String> = tail.iter().collect();
        for norm in distinct {
            occurrences.entry(norm.clone()).or_default().push(i);
        }
        tails.push(tail);
    }
    let mut candidates: HashSet<&str> = HashSet::new();
    for (norm, where_) in &occurrences {
        if where_.len() < LEARN_MIN_COUNT || where_.len() * 5 < texts.len() {
            continue;
        }
        if let Some(subjects) = subjects {
            let keys: HashSet<String> = where_
                .iter()
                .map(|&i| subject_key(subjects.get(i).copied().unwrap_or("")))
                .collect();
            if keys.len() < LEARN_MIN_SUBJECTS {
                continue;
            }
        }
        candidates.insert(norm);
    }
    let mut sorted: Vec<&str> = candidates.iter().copied().collect();
    // Byte order of UTF-8 is code-point order, as Python's `sorted`.
    sorted.sort_unstable();
    let mut learned = Vec::new();
    for norm in sorted {
        let opens_block = norm.ends_with(':')
            || (norm.ends_with(SENTENCE_PUNCT_END) && pystr::split_ws(norm).len() >= 3);
        if opens_block {
            let (mut continued, mut total) = (0usize, 0usize);
            for &i in &occurrences[norm] {
                let tail = &tails[i];
                for (pos, line) in tail.iter().enumerate() {
                    if line != norm {
                        continue;
                    }
                    total += 1;
                    if pos > 0 {
                        let above = tail[pos - 1].as_str();
                        if above != norm && candidates.contains(above) {
                            continued += 1;
                        }
                    }
                }
            }
            if continued * 2 < total {
                continue;
            }
        }
        learned.push(norm.to_string());
    }
    learned
}

/// `_drop_learned_signature_lines`: drop the learned lines in the trailing
/// zone.
///
/// The trailing zone is scanned from the last line up and ends at the first
/// non-blank line that is not learned and has more than
/// [`LEARNED_ZONE_MAX_WORDS`] words. Every learned line inside it is dropped,
/// unless no non-blank, non-learned line stands above the topmost of them:
/// the step never removes the whole text.
fn drop_learned_signature_lines<'a>(pairs: Pairs<'a>, learned: &HashSet<String>) -> Pairs<'a> {
    if learned.is_empty() {
        return pairs;
    }
    let norms: Vec<String> = pairs.iter().map(|(_, l)| learned_norm(l)).collect();
    let mut drop: HashSet<usize> = HashSet::new();
    for (pos, norm) in norms.iter().enumerate().rev() {
        if norm.is_empty() {
            continue;
        }
        if learned.contains(norm) {
            drop.insert(pos);
        } else if pystr::split_ws(norm).len() > LEARNED_ZONE_MAX_WORDS {
            break;
        }
    }
    let Some(&first) = drop.iter().min() else {
        return pairs;
    };
    if !norms[..first]
        .iter()
        .any(|n| !n.is_empty() && !learned.contains(n))
    {
        return pairs;
    }
    without_positions(pairs, &drop)
}

// --- the pass -----------------------------------------------------------------------

/// Greeting lines removed per pass and per text.
const MAX_GREETINGS: usize = 2;

/// `_CleanState`: evidence and bookkeeping that persist across the passes of
/// one text.
struct CleanState {
    author: Option<Names>,
    addressees: Option<Names>,
    /// Greeting lines that may still be removed (at most [`MAX_GREETINGS`]).
    greetings_left: usize,
    /// Input indices of lines that a sender-name rule exposed as the tail.
    exposed: HashSet<usize>,
}

fn clean_pass<'a>(
    pairs: Pairs<'a>,
    hint_norms: &HashSet<String>,
    state: &mut CleanState,
) -> Pairs<'a> {
    let pairs = truncate_at_signature_delimiter(pairs);
    let pairs = truncate_at_custom_signature_divider(pairs);
    // Trailing debris before the per-line debris drop: its bare-name anchor
    // needs the identifier/contact lines still present as evidence.
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
    let pairs = strip_opening_greetings(pairs, state);
    let pairs: Pairs<'a> = pairs
        .into_iter()
        .filter(|(_, l)| !is_mobile_tagline(l))
        .collect();
    let pairs = drop_trailing_disclaimer_paragraphs(pairs);
    let mut pairs = strip_closing_signoff(pairs, state);
    if let Some(author) = &state.author {
        // A salutation these rules expose at the tail goes on the next pass.
        pairs = drop_signoff_above_tail(pairs, author, &mut state.exposed);
        pairs = drop_trailing_author_name(pairs, author, &mut state.exposed);
        pairs = drop_author_signoffs(pairs, author);
        pairs = drop_lone_author_name(pairs, author);
    }
    pairs
}

/// Fixpoint cap for [`clean_for_scoring`]: a safety bound, not a tuning knob;
/// it doubles when learned lines are given.
const MAX_CLEAN_PASSES: usize = 4;

/// The optional arguments of the reference's `clean_for_scoring`, for
/// [`clean_for_scoring_with_options`]. `CleanOptions::default()` passes none
/// of them, which is [`clean_for_scoring`] with `None`.
///
/// - `html_signature_text`: the visible text of the message's HTML signature
///   container ([`crate::HtmlParts::signature_text`]); see
///   [`clean_for_scoring`].
/// - `part_starts`: the stage-1 result's
///   [`crate::ExtractionResult::part_starts`]; see
///   [`clean_for_scoring_with_parts`].
/// - `author_name`: the display name of the message's sender (the From
///   header's name, or its address when there is no name). It enables the
///   sender-name sign-off rules and the thanks-to-an-addressee exception.
/// - `addressee_names`: the display names of the people the message answers
///   or is addressed to (the parent message's sender, the To and Cc names).
///   They enable the addressee greeting rule and the thanks-to-an-addressee
///   exception; a name whose tokens equal the sender's is ignored.
/// - `signature_lines`: the sender's learned signature lines, dropped from
///   the trailing zone after the passes converge.
#[derive(Clone, Copy, Debug, Default)]
pub struct CleanOptions<'a> {
    /// The HTML signature hint.
    pub html_signature_text: Option<&'a str>,
    /// The stage-1 part boundaries.
    pub part_starts: &'a [usize],
    /// The sender's display name.
    pub author_name: Option<&'a str>,
    /// The addressees' display names.
    pub addressee_names: &'a [&'a str],
    /// The sender's learned signature lines.
    pub signature_lines: &'a [&'a str],
}

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
/// boundaries, and [`clean_for_scoring_with_options`] also takes the sender
/// and addressee evidence.
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
/// assert_eq!(stage1.part_starts, vec![3]);
/// let cleaned = clean_for_scoring_with_parts(&stage1.text, None, &stage1.part_starts);
/// assert_eq!(cleaned.parts, vec!["Yes.", "No."]);
/// ```
pub fn clean_for_scoring_with_parts(
    extracted_text: &str,
    html_signature_text: Option<&str>,
    part_starts: &[usize],
) -> CleanResult {
    clean_for_scoring_with_options(
        extracted_text,
        &CleanOptions {
            html_signature_text,
            part_starts,
            ..CleanOptions::default()
        },
    )
}

/// [`clean_for_scoring`] with every optional argument of the reference
/// ([`CleanOptions`]).
///
/// ```
/// use email_reply_extractor::{clean_for_scoring_with_options, CleanOptions};
/// let text = "The draft is ready.\n\nJane";
/// let options = CleanOptions { author_name: Some("Jane Doe"), ..CleanOptions::default() };
/// let cleaned = clean_for_scoring_with_options(text, &options);
/// assert_eq!(cleaned.text, "The draft is ready.");
/// assert_eq!(cleaned.ignored_lines, vec![2]);
/// ```
pub fn clean_for_scoring_with_options(
    extracted_text: &str,
    options: &CleanOptions<'_>,
) -> CleanResult {
    let hint_norms: HashSet<String> = options
        .html_signature_text
        .unwrap_or("")
        .split('\n')
        .map(normalize_line_for_diff)
        .filter(|n| !n.is_empty())
        .collect();
    let learned: HashSet<String> = options
        .signature_lines
        .iter()
        .map(|l| learned_norm(l))
        .filter(|n| !n.is_empty())
        .collect();
    let mut state = CleanState {
        author: parse_author_name(options.author_name),
        addressees: parse_addressee_names(
            options.addressee_names.iter().copied(),
            options.author_name,
        ),
        greetings_left: MAX_GREETINGS,
        exposed: HashSet::new(),
    };

    let original: Vec<&str> = extracted_text.split('\n').collect();
    let mut pairs: Pairs<'_> = original.iter().copied().enumerate().collect();
    // The learned-line drop runs only once the ordinary passes have converged,
    // so the other steps keep their anchors; the passes then resume, since the
    // drop can expose more furniture.
    let max_passes = if learned.is_empty() {
        MAX_CLEAN_PASSES
    } else {
        MAX_CLEAN_PASSES * 2
    };
    for _ in 0..max_passes {
        let before = pairs.len();
        pairs = clean_pass(pairs, &hint_norms, &mut state);
        if pairs.len() != before {
            continue;
        }
        pairs = drop_learned_signature_lines(pairs, &learned);
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

    let mut starts: Vec<usize> = options
        .part_starts
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
