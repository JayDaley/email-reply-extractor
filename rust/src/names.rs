//! Display names for stage 2: folding, tokens, and lines that name a person
//! (port of `_names.py`).
//!
//! `clean_for_scoring` accepts the sender's display name (`author_name`) and
//! the display names of the people the message answers or is addressed to
//! (`addressee_names`). This module turns those names into a [`Names`] value
//! and answers the questions the sign-off rules (`cleaning`) and the greeting
//! rules (`greetings`) ask of it. The Python module docstring states the
//! rules; this port follows it function by function.
//!
//! Python semantics kept here: [`fold`] is Python's `str.lower` (via
//! [`pystr::lower`], generated tables) followed by one pass through the literal
//! `_FOLD_TABLE` ([`FOLD_TABLE`], generated from the Python source by
//! `tools/gen_tables.py`); letters are `[^\W\d_]` with Python's `\w` and `\d`;
//! lengths and indices are code points; `strip` with explicit character sets
//! removes only those characters. The regexes without lookaround are
//! hand-coded scanners (each linear); `_NAME_LINE_TAIL_RE` keeps its
//! lookbehinds and runs on `fancy-regex`.

use crate::pyre::{Re, IGNORECASE};
use crate::pystr;
pub(crate) use crate::unicode_tables::FOLD_TABLE;
use std::collections::BTreeSet;
use std::sync::LazyLock;

// --- folding --------------------------------------------------------------------

/// Lowercase `text` (Python `str.lower`) and map every character through
/// [`FOLD_TABLE`] in one pass; a character without an entry is kept.
pub(crate) fn fold(text: &str) -> String {
    let lowered = pystr::lower(text);
    if lowered.is_ascii() {
        // The table has no ASCII keys.
        return lowered;
    }
    let mut out = String::with_capacity(lowered.len());
    for c in lowered.chars() {
        match FOLD_TABLE.binary_search_by(|&(k, _)| k.cmp(&c)) {
            Ok(i) => out.push_str(FOLD_TABLE[i].1),
            Err(_) => out.push(c),
        }
    }
    out
}

// --- parsing --------------------------------------------------------------------

/// Words that are never name tokens: titles, generation suffixes, connectors,
/// and the words of list and role display names.
pub(crate) const STOP_WORDS: &[&str] = &[
    "dr",
    "prof",
    "mr",
    "mrs",
    "ms",
    "mx",
    "sir",
    "dipl",
    "ing",
    "phd",
    "jr",
    "sr",
    "ii",
    "iii",
    "iv",
    "the",
    "of",
    "and",
    "via",
    "team",
    "on",
    "behalf",
    "for",
    "list",
    "mailing",
    "ietf",
    "iesg",
    "iab",
    "irtf",
    "secretariat",
];

/// Local parts of an address that name a mailbox, not a person.
pub(crate) const GENERIC_LOCAL_PARTS: &[&str] = &[
    "mail", "email", "me", "info", "contact", "hello", "ietf", "post", "office",
];

/// Common English short forms of given names.
pub(crate) const NICKNAMES: &[(&str, &[&str])] = &[
    ("robert", &["bob", "rob", "bobby", "robby"]),
    ("thomas", &["tom", "tommy"]),
    ("susan", &["sue", "susie"]),
    ("william", &["bill", "will", "billy"]),
    ("richard", &["dick", "rich", "rick"]),
    ("james", &["jim", "jimmy", "jamie"]),
    ("michael", &["mike", "mick"]),
    ("andrew", &["andy", "drew"]),
    ("edward", &["ed", "ted", "eddie"]),
    ("elizabeth", &["liz", "beth", "betsy"]),
    ("margaret", &["peggy", "maggie", "meg"]),
    ("charles", &["chuck", "charlie"]),
    ("joseph", &["joe"]),
    ("jonathan", &["jon"]),
    ("anthony", &["tony"]),
    ("david", &["dave"]),
    ("stephen", &["steve"]),
    ("steven", &["steve"]),
    ("matthew", &["matt"]),
    ("benjamin", &["ben"]),
    ("nicholas", &["nick"]),
    ("patrick", &["pat"]),
    ("peter", &["pete"]),
    ("samuel", &["sam"]),
    ("alexander", &["alex"]),
    ("kenneth", &["ken"]),
    ("donald", &["don"]),
    ("ronald", &["ron"]),
    ("lawrence", &["larry"]),
    ("gregory", &["greg"]),
    ("timothy", &["tim"]),
    ("jeffrey", &["jeff"]),
    ("frederick", &["fred"]),
    ("theodore", &["ted"]),
    ("katherine", &["kate", "kathy"]),
    ("catherine", &["kate", "cathy"]),
    ("deborah", &["deb"]),
    ("rebecca", &["becky"]),
    ("jennifer", &["jen", "jenny"]),
    ("victoria", &["vicky"]),
    ("christopher", &["chris"]),
    ("christine", &["chris"]),
    ("daniel", &["dan", "danny"]),
    ("philip", &["phil"]),
    ("phillip", &["phil"]),
    ("raymond", &["ray"]),
    ("gerald", &["gerry"]),
    ("leonard", &["len"]),
];

/// `[^\W\d_]`: Python's `\w` without `\d` and `_`. Letters, and also the
/// numeric characters that are not decimal digits (`²`, `Ⅻ`); not combining
/// or spacing marks.
pub(crate) fn is_name_letter(c: char) -> bool {
    c != '_' && pystr::is_word(c) && !pystr::is_decimal(c)
}

fn is_joiner(c: char) -> bool {
    matches!(c, '\'' | '\u{2019}' | '-')
}

/// `_LETTER_RUN_RE.findall(text)`: the runs of `[^\W\d_]+`.
pub(crate) fn letter_runs(text: &str) -> Vec<&str> {
    let mut runs = Vec::new();
    let mut start: Option<usize> = None;
    for (i, c) in text.char_indices() {
        match (is_name_letter(c), start) {
            (true, None) => start = Some(i),
            (false, Some(s)) => {
                runs.push(&text[s..i]);
                start = None;
            }
            _ => {}
        }
    }
    if let Some(s) = start {
        runs.push(&text[s..]);
    }
    runs
}

/// `_JOINED_RUN_RE.findall(text)`: `[^\W\d_]+(?:['’-][^\W\d_]+)*`.
pub(crate) fn joined_runs(text: &str) -> Vec<&str> {
    let chars: Vec<(usize, char)> = text.char_indices().collect();
    let n = chars.len();
    let byte = |i: usize| if i < n { chars[i].0 } else { text.len() };
    let mut runs = Vec::new();
    let mut i = 0;
    while i < n {
        if !is_name_letter(chars[i].1) {
            i += 1;
            continue;
        }
        let start = i;
        while i < n && is_name_letter(chars[i].1) {
            i += 1;
        }
        while i + 1 < n && is_joiner(chars[i].1) && is_name_letter(chars[i + 1].1) {
            i += 1;
            while i < n && is_name_letter(chars[i].1) {
                i += 1;
            }
        }
        runs.push(&text[byte(start)..byte(i)]);
    }
    runs
}

/// `_ADDRESS_PUNCT_RE.sub(" ", text)`: `.`, `_`, `+`, `=`, `-` become spaces.
pub(crate) fn address_punct_to_spaces(text: &str) -> String {
    text.chars()
        .map(|c| {
            if matches!(c, '.' | '_' | '+' | '=' | '-') {
                ' '
            } else {
                c
            }
        })
        .collect()
}

/// `_NICKNAME_GROUP_RE.findall(text)`: the contents of each `(…)` of 1 to 30
/// characters with no parenthesis inside (`\(([^()]{1,30})\)`).
pub(crate) fn nickname_groups(text: &str) -> Vec<String> {
    let chars: Vec<char> = text.chars().collect();
    let n = chars.len();
    let mut out = Vec::new();
    let mut i = 0;
    while i < n {
        if chars[i] == '(' {
            let mut j = i + 1;
            while j < n && chars[j] != '(' && chars[j] != ')' {
                j += 1;
            }
            let len = j - i - 1;
            if j < n && chars[j] == ')' && (1..=30).contains(&len) {
                out.push(chars[i + 1..j].iter().collect());
                i = j + 1;
                continue;
            }
        }
        i += 1;
    }
    out
}

/// `_BRACKETED_RE.sub(" ", text)`: every `(…)`, `[…]` and `<…>` group with
/// no bracket of its kind inside becomes a space.
pub(crate) fn remove_bracketed(text: &str) -> String {
    let chars: Vec<char> = text.chars().collect();
    let n = chars.len();
    let mut out = String::with_capacity(text.len());
    let mut i = 0;
    while i < n {
        let pair = match chars[i] {
            '(' => Some(('(', ')')),
            '[' => Some(('[', ']')),
            '<' => Some(('<', '>')),
            _ => None,
        };
        if let Some((open, close)) = pair {
            let mut j = i + 1;
            while j < n && chars[j] != open && chars[j] != close {
                j += 1;
            }
            if j < n && chars[j] == close {
                out.push(' ');
                i = j + 1;
                continue;
            }
        }
        out.push(chars[i]);
        i += 1;
    }
    out
}

/// `_WORD_SPAN_RE.finditer(text)` (`\S+`): byte spans of the words.
pub(crate) fn word_spans(text: &str) -> Vec<(usize, usize)> {
    let mut spans = Vec::new();
    let mut start: Option<usize> = None;
    for (i, c) in text.char_indices() {
        match (pystr::is_space(c), start) {
            (false, None) => start = Some(i),
            (true, Some(s)) => {
                spans.push((s, i));
                start = None;
            }
            _ => {}
        }
    }
    if let Some(s) = start {
        spans.push((s, text.len()));
    }
    spans
}

/// The tokens of one or more display names (see the Python module
/// docstring). `initials` and `first_last` are set only for a single name.
#[derive(Clone, Debug, Default)]
pub(crate) struct Names {
    pub(crate) tokens: BTreeSet<String>,
    pub(crate) initials: BTreeSet<String>,
    pub(crate) first_last: String,
    sorted: Vec<String>,
    sorted_reversed: Vec<String>,
}

/// As the Python dataclass: the sorted lists do not take part in equality.
impl PartialEq for Names {
    fn eq(&self, other: &Self) -> bool {
        self.tokens == other.tokens
            && self.initials == other.initials
            && self.first_last == other.first_last
    }
}

impl Eq for Names {}

fn reversed(text: &str) -> String {
    text.chars().rev().collect()
}

impl Names {
    /// True when `text` is a prefix of some token (binary search; `str`
    /// ordering is code-point order, as Python's).
    pub(crate) fn has_prefix(&self, text: &str) -> bool {
        let pos = self.sorted.partition_point(|t| t.as_str() < text);
        pos < self.sorted.len() && self.sorted[pos].starts_with(text)
    }

    /// True when `text` is a suffix of some token.
    pub(crate) fn has_suffix(&self, text: &str) -> bool {
        let rev = reversed(text);
        let pos = self
            .sorted_reversed
            .partition_point(|t| t.as_str() < rev.as_str());
        pos < self.sorted_reversed.len() && self.sorted_reversed[pos].starts_with(&rev)
    }
}

/// `text` up to the first word `via` with whitespace on both sides.
pub(crate) fn drop_via(text: &str) -> &str {
    for (start, end) in word_spans(text) {
        if start > 0 && end < text.len() && pystr::lower(&text[start..end]) == "via" {
            return pystr::rstrip(&text[..start]);
        }
    }
    text
}

/// Steps 1-5 of the Python module docstring: the name text and the extra
/// texts.
pub(crate) fn reduce(name: &str) -> (String, Vec<String>) {
    let replaced = name.replace(['"', '`'], " ");
    let mut text = pystr::strip_chars(pystr::strip(&replaced), "'").to_string();
    let mut extra: Vec<String> = Vec::new();
    if text.contains('@') && !text.contains(' ') {
        let (local, domain) = text.split_once('@').unwrap_or((&text, ""));
        let label = domain.split('.').next().unwrap_or("");
        if GENERIC_LOCAL_PARTS.contains(&fold(local).as_str())
            && pystr::is_alpha_str(label)
            && pystr::len(label) >= 6
        {
            extra.push(label.to_string());
        }
        text = address_punct_to_spaces(local);
    }
    extra.extend(nickname_groups(&text));
    text = remove_bracketed(&text);
    if text.contains('@') {
        text = pystr::split_ws(&text)
            .into_iter()
            .map(|word| match word.split_once('@') {
                Some((before, _)) => address_punct_to_spaces(before),
                None => word.to_string(),
            })
            .collect::<Vec<_>>()
            .join(" ");
    }
    text = drop_via(&text).to_string();
    if text.matches(',').count() == 1 {
        if let Some((last, first)) = text.split_once(',') {
            text = format!("{first} {last}");
        }
    }
    (text, extra)
}

/// The tokens of the given texts: folded letter runs and joined runs of at
/// least 2 code points that are not stop words, plus nickname short forms.
pub(crate) fn tokens<S: AsRef<str>>(texts: &[S]) -> BTreeSet<String> {
    let mut tokens: BTreeSet<String> = BTreeSet::new();
    for text in texts {
        let folded = fold(text.as_ref());
        for run in letter_runs(&folded).into_iter().chain(joined_runs(&folded)) {
            if pystr::len(run) >= 2 && !STOP_WORDS.contains(&run) {
                tokens.insert(run.to_string());
            }
        }
    }
    let originals: Vec<String> = tokens.iter().cloned().collect();
    for token in originals {
        if let Some((_, shorts)) = NICKNAMES.iter().find(|(full, _)| *full == token) {
            tokens.extend(shorts.iter().map(|s| s.to_string()));
        }
    }
    tokens
}

fn reduced_texts(name: &str) -> (String, Vec<String>) {
    let (text, extra) = reduce(name);
    let mut texts = vec![text.clone()];
    texts.extend(extra);
    (text, texts)
}

/// The tokens of one display name.
pub(crate) fn name_tokens(name: &str) -> BTreeSet<String> {
    tokens(&reduced_texts(name).1)
}

/// `_make`: a [`Names`] with its sorted lookup lists.
pub(crate) fn make(
    tokens: BTreeSet<String>,
    initials: BTreeSet<String>,
    first_last: String,
) -> Names {
    // A BTreeSet iterates in code-point order, which is Python's `sorted`.
    let sorted: Vec<String> = tokens.iter().cloned().collect();
    let mut sorted_reversed: Vec<String> = tokens.iter().map(|t| reversed(t)).collect();
    sorted_reversed.sort();
    Names {
        tokens,
        initials,
        first_last,
        sorted,
        sorted_reversed,
    }
}

fn first_char(word: &str) -> char {
    word.chars().next().unwrap_or_default()
}

/// The sender's [`Names`], with initials, or `None` without tokens.
pub(crate) fn parse_author_name(name: Option<&str>) -> Option<Names> {
    let name = name.filter(|n| !n.is_empty())?;
    let (text, texts) = reduced_texts(name);
    let tokens = tokens(&texts);
    if tokens.is_empty() {
        return None;
    }
    let folded = fold(&text);
    let words: Vec<&str> = letter_runs(&folded)
        .into_iter()
        .filter(|w| !STOP_WORDS.contains(w))
        .collect();
    let mut initials: BTreeSet<String> = BTreeSet::new();
    let mut first_last = String::new();
    if words.len() >= 2 {
        let backwards: Vec<&str> = words.iter().rev().copied().collect();
        for seq in [&words, &backwards] {
            initials.insert(seq.iter().map(|w| first_char(w)).collect());
            initials.insert(
                [first_char(seq[0]), first_char(seq[seq.len() - 1])]
                    .iter()
                    .collect(),
            );
        }
        first_last = [first_char(words[0]), first_char(words[words.len() - 1])]
            .iter()
            .collect();
    }
    initials.retain(|i| pystr::len(i) >= 2);
    Some(make(tokens, initials, first_last))
}

/// The addressees' [`Names`], or `None` without tokens. A name whose tokens
/// equal the sender's own (`author_name`) is skipped.
pub(crate) fn parse_addressee_names<'a, I>(names: I, author_name: Option<&str>) -> Option<Names>
where
    I: IntoIterator<Item = &'a str>,
{
    let own = match author_name {
        Some(a) if !a.is_empty() => name_tokens(a),
        _ => BTreeSet::new(),
    };
    let mut tokens: BTreeSet<String> = BTreeSet::new();
    for name in names {
        let theirs = name_tokens(name);
        if own.is_empty() || theirs != own {
            tokens.extend(theirs);
        }
    }
    if tokens.is_empty() {
        None
    } else {
        Some(make(tokens, BTreeSet::new(), String::new()))
    }
}

// --- name lines -------------------------------------------------------------------

/// Name particles: allowed in a name line when they are tokens of the name,
/// never the word that matches it.
pub(crate) const NAME_PARTICLES: &[&str] = &[
    "de", "da", "di", "del", "della", "der", "den", "van", "von", "la", "le", "du", "dos", "das",
    "bin", "al", "el",
];

/// Characters that decorate a sign-off name ("-Jane", "— Jane", "~jane", "/J").
pub(crate) const NAME_DECORATION: &str = "-\u{2013}\u{2014}~/*_ \t";

/// A role or proxy tail after a name. The `(?<![ \t])` lookbehinds keep a
/// search from restarting inside a blank run; they need `fancy-regex`.
pub(crate) static NAME_LINE_TAIL_RE: LazyLock<Re> = LazyLock::new(|| {
    Re::searcher(
        concat!(
            r"(?:(?<![ \t])[ \t]+(?:on[ \t]+behalf[ \t]+of|for[ \t]+the)\b.{0,60}",
            r"|(?<![ \t])[ \t]*(?://|,)[ \t]*(?:as|for|on|speaking|with|wearing|no|contributor|individual)\b.{0,40})$",
        ),
        IGNORECASE,
    )
});

/// One word of a name line after folding.
pub(crate) static FOLDED_NAME_WORD_RE: LazyLock<Re> =
    LazyLock::new(|| Re::matcher(r"^[^\W\d_](?:[^\W\d_]|[.'’\-])*$", 0));

/// A capitalized word that is not the sender's (a co-signer, a middle name).
/// Used with `Pattern.match` (`LOOSE_NAME_TOKEN_RE.is_match(word)`).
pub(crate) static LOOSE_NAME_TOKEN_RE: LazyLock<Re> =
    LazyLock::new(|| Re::matcher(r"^[^\W\d_][\w'’\-]{0,24}$", 0));

/// Words that join co-signers in a group sign-off ("Jane and the co-authors").
pub(crate) const GROUP_SIGNOFF_WORDS: &[&str] = &[
    "and",
    "&",
    "co-authors",
    "coauthors",
    "authors",
    "chairs",
    "co-chairs",
    "editors",
    "team",
];

/// 2: `word` matches the sender; 1: an initial or particle; 0: neither.
pub(crate) fn name_word_status(word: &str, author: &Names) -> u8 {
    let folded_word = fold(word);
    let folded = pystr::strip_chars(&folded_word, ".");
    if folded.is_empty() || !FOLDED_NAME_WORD_RE.is_match(folded) {
        return 0;
    }
    if folded.contains('-') {
        let parts: Vec<&str> = folded.split('-').filter(|p| !p.is_empty()).collect();
        let all = !parts.is_empty() && parts.iter().all(|p| name_word_status(p, author) == 2);
        return if all { 2 } else { 0 };
    }
    let core: String = folded
        .chars()
        .filter(|&c| !matches!(c, '.' | '\'' | '\u{2019}'))
        .collect();
    if core.is_empty() {
        return 0;
    }
    if NAME_PARTICLES.contains(&core.as_str()) {
        return u8::from(author.tokens.contains(&core));
    }
    if author.tokens.contains(&core) || author.initials.contains(&core) {
        return 2;
    }
    let chars: Vec<char> = core.chars().collect();
    if chars.len() >= 3 && (author.has_prefix(&core) || author.has_suffix(&core)) {
        return 2;
    }
    if chars.len() == 3 && !author.first_last.is_empty() {
        let ends: String = [chars[0], chars[2]].iter().collect();
        if ends == author.first_last {
            return 2;
        }
    }
    if chars.len() == 1 {
        return 1;
    }
    0
}

/// True when `word` matches the sender's name (status 2 above).
pub(crate) fn matches_author(word: &str, author: &Names) -> bool {
    name_word_status(word, author) == 2
}

/// True when a folded letter run of `word` is an addressee token.
pub(crate) fn matches_addressee(word: &str, addressees: Option<&Names>) -> bool {
    let Some(addressees) = addressees else {
        return false;
    };
    letter_runs(&fold(word))
        .into_iter()
        .any(|run| addressees.tokens.contains(run))
}

/// Python `s.find(ch)` / `s.rfind(ch)` in code points.
fn char_index(s: &str, ch: char, from_end: bool) -> Option<usize> {
    if from_end {
        let n = pystr::len(s);
        s.chars().rev().position(|c| c == ch).map(|p| n - 1 - p)
    } else {
        s.chars().position(|c| c == ch)
    }
}

/// `s[:k]` / `s[k:]` in code points: the byte offset of code point `k`.
fn byte_at(s: &str, k: usize) -> usize {
    s.char_indices().nth(k).map_or(s.len(), |(i, _)| i)
}

/// True when `line` is the sender's own name (see the Python docstring for
/// the rule).
pub(crate) fn is_author_name_line(line: &str, author: Option<&Names>) -> bool {
    let Some(author) = author else {
        return false;
    };
    let mut s: &str = pystr::strip(line);
    if s.starts_with('(') {
        if let Some(close) = char_index(s, ')', false) {
            if close > 0 && close <= 30 {
                s = &s[byte_at(s, close + 1)..];
            }
        }
    }
    s = pystr::lstrip_chars(s, NAME_DECORATION);
    if let Some((start, _)) = NAME_LINE_TAIL_RE.find(s) {
        s = &s[..start];
    }
    s = pystr::rstrip_chars(s, " \t.,;:!");
    if let Some(open_paren) = char_index(s, '(', true) {
        if s.ends_with(')') && open_paren > 0 && pystr::len(s) - open_paren <= 40 {
            s = pystr::rstrip_chars(&s[..byte_at(s, open_paren)], " \t.,;:!");
        }
    }
    if s.is_empty() || pystr::len(s) > 40 {
        return false;
    }
    let words = pystr::split_ws(s);
    if words.len() > 4 {
        return false;
    }
    let group = words.iter().any(|w| {
        let lowered = pystr::lower(w);
        lowered == "and" || lowered == "&"
    });
    let mut statuses: Vec<u8> = Vec::with_capacity(words.len());
    for word in &words {
        if group && GROUP_SIGNOFF_WORDS.contains(&pystr::lower(word).as_str()) {
            statuses.push(1);
            continue;
        }
        let status = name_word_status(word, author);
        if status == 0 && !(pystr::is_upper(first_char(word)) && LOOSE_NAME_TOKEN_RE.is_match(word))
        {
            return false;
        }
        statuses.push(status);
    }
    let unknown = statuses.iter().filter(|&&s| s == 0).count();
    let matched = statuses.iter().filter(|&&s| s == 2).count();
    if matched == 0 {
        return false;
    }
    unknown == 0 || (unknown == 1 && (group || matched >= 2))
}

/// A decorated one-letter sign-off ("/J", "-J.") that is the sender's first
/// initial.
pub(crate) fn is_author_initial_line(line: &str, author: Option<&Names>) -> bool {
    let Some(author) = author else {
        return false;
    };
    let s = pystr::strip(line);
    let core = pystr::rstrip_chars(pystr::lstrip_chars(s, NAME_DECORATION), " .");
    if pystr::len(core) != 1 || !pystr::is_alpha_str(core) || core == s {
        return false;
    }
    let folded = fold(core);
    author.initials.iter().any(|initials| {
        initials
            .chars()
            .next()
            .is_some_and(|c| folded == c.to_string())
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    fn author(name: &str) -> Names {
        parse_author_name(Some(name)).expect("tokens")
    }

    /// The `_sender_line_removed` pins of `test_port_hazards.py`, at the level
    /// of the name-line rule.
    fn sender_line(author_name: &str, line: &str) -> bool {
        let a = parse_author_name(Some(author_name));
        is_author_name_line(line, a.as_ref()) || is_author_initial_line(line, a.as_ref())
    }

    #[test]
    fn fold_table_is_complete() {
        assert_eq!(FOLD_TABLE.len(), 396);
        assert!(FOLD_TABLE.windows(2).all(|w| w[0].0 < w[1].0));
    }

    #[test]
    fn folding() {
        assert_eq!(fold("Preu\u{df}"), "preuss");
        assert_eq!(fold("PREU\u{df}"), "preuss");
        assert_eq!(fold("Y\u{131}ld\u{131}z"), "yildiz");
        assert_eq!(fold("\u{130}smail"), "ismail");
        assert_eq!(fold("Jose\u{301}"), "jose");
        assert_eq!(fold("Jos\u{e9}"), "jose");
        assert_eq!(fold("Bj\u{f8}rn \u{c6}sir"), "bjorn aesir");
        assert_eq!(fold("\u{1c4}oe"), "dzoe");
        assert_eq!(fold("Ro\u{17f}\u{17f}"), "ross");
        assert_eq!(fold("\u{2026}Roy"), "...roy");
        assert_eq!(fold("\u{1fe}"), "\u{f8}"); // one pass: not looked up again
        assert_eq!(fold("\u{ff2a}\u{ff41}"), "\u{ff4a}\u{ff41}");
        assert_eq!(
            fold("\u{391}\u{3bb}\u{3ad}\u{3be}\u{3b7}\u{3a3}"),
            "\u{3b1}\u{3bb}\u{3ad}\u{3be}\u{3b7}\u{3c2}"
        );
        assert_eq!(fold("\u{bc}\u{a0}\u{b5}"), "1\u{2044}4 \u{3bc}");
    }

    #[test]
    fn port_hazard_name_folding_pins() {
        assert!(sender_line("John Preu\u{df}", "Preuss"));
        assert!(sender_line("John Preuss", "PREU\u{df}"));
        assert!(sender_line("Ay\u{15f}e Y\u{131}ld\u{131}z", "Yildiz"));
        assert!(sender_line("\u{130}smail Example", "Ismail"));
        assert!(sender_line("Jos\u{e9} Example", "Jose\u{301}"));
        assert!(sender_line("Jose\u{301} Example", "Jos\u{e9}"));
        assert!(sender_line("Bj\u{f8}rn \u{c6}sir", "Bjorn"));
        assert!(sender_line("Bj\u{f8}rn \u{c6}sir", "Aesir"));
        assert!(sender_line("\u{1c4}oe Example", "\u{1c6}oe"));
        assert!(sender_line("\u{1c4}oe Example", "Dzoe"));
        assert!(sender_line("Ross Example", "Ro\u{17f}\u{17f}"));
        assert!(!sender_line(
            "Jane Example",
            "\u{ff2a}\u{ff41}\u{ff4e}\u{ff45}"
        ));
        let alexis = "\u{391}\u{3bb}\u{3ad}\u{3be}\u{3b7}\u{3c2}";
        let author_name = format!("{alexis} Example");
        assert!(sender_line(&author_name, alexis));
        assert!(!sender_line(
            &author_name,
            "\u{391}\u{3bb}\u{3b5}\u{3be}\u{3b7}\u{3c2}"
        ));
        assert!(!sender_line(
            &author_name,
            "\u{391}\u{3bb}\u{3ad}\u{3be}\u{3b7}\u{3c3}"
        ));
        assert!(sender_line("Roy Example", "\u{2026}Roy"));
        // CJK ideographs are letters; Devanagari vowel signs (Mc) are not.
        assert!(sender_line(
            "\u{5c71}\u{7530}\u{592a}\u{90ce}",
            "\u{5c71}\u{7530}\u{592a}\u{90ce}"
        ));
        assert!(parse_author_name(Some("\u{930}\u{93e}\u{92e}")).is_none());
    }

    #[test]
    fn sender_name_line_forms() {
        // test_cleaning.py: test_trailing_sender_name_line_is_stripped.
        for line in [
            "Jane",
            "jane",
            "Jane Example",
            "-jane",
            "\u{2014} Jane",
            "/je",
            "JE",
        ] {
            assert!(sender_line("Jane Example", line), "{line}");
            assert!(
                is_author_name_line(line, Some(&author("Jane Example"))),
                "{line}"
            );
        }
        assert!(is_author_name_line(
            "Jane (as chair)",
            Some(&author("Jane Example"))
        ));
        for line in ["/j", "-J."] {
            assert!(
                !is_author_name_line(line, Some(&author("Jane Example"))),
                "{line}"
            );
            assert!(
                is_author_initial_line(line, Some(&author("Jane Example"))),
                "{line}"
            );
        }
        assert!(!is_author_initial_line("/x", Some(&author("Jane Example"))));
        assert!(!is_author_initial_line("J", Some(&author("Jane Example"))));
        assert!(!sender_line("Bob Other", "Jane"));
        assert!(!is_author_name_line("Jane", None));
        // test_sender_name_matching_forms.
        for (name, line) in [
            ("Robert Example", "Bob"),
            ("Robert Example", "RjE"),
            ("Robert Example", "Rob E."),
            ("S\u{f8}ren \u{c5}ngstr\u{f6}m", "Soren"),
            ("Example, Jane", "JE"),
            ("jane.example@example.org", "Jane"),
            ("mail@janeexample.org", "Jane Example"),
            ("Jane Example via Example-List", "Jane"),
            ("\"Jane (JJ) Example\"", "JJ"),
        ] {
            assert!(sender_line(name, line), "{name} / {line}");
        }
        // test_author_name_without_tokens_gives_no_evidence.
        assert!(parse_author_name(Some("")).is_none());
        assert!(parse_author_name(None).is_none());
        assert!(parse_author_name(Some("IETF Secretariat")).is_none());
        // Role tails and group lines.
        let jane = author("Jane Example");
        assert!(is_author_name_line("Jane, for the authors", Some(&jane)));
        assert!(is_author_name_line(
            "Jane on behalf of the chairs",
            Some(&jane)
        ));
        assert!(is_author_name_line("Jane // as an individual", Some(&jane)));
        assert!(!is_author_name_line("Jane and the co-authors", Some(&jane)));
        assert!(is_author_name_line("Jane and co-authors", Some(&jane)));
        assert!(is_author_name_line("Jane Q. Middle Example", Some(&jane)));
        assert!(!is_author_name_line("Jane Middle", Some(&jane)));
        assert!(!is_author_name_line("Jane says hello to you", Some(&jane)));
    }

    #[test]
    fn parsing() {
        let (text, extra) = reduce("Andrew (Andy) Example <andy@example.org>");
        assert_eq!(extra, vec!["Andy".to_string()]);
        assert_eq!(pystr::collapse_ws(&text), "Andrew Example");
        assert_eq!(reduce("Doe, Jane").0, " Jane Doe");
        assert_eq!(reduce("Jane Doe jdoe@example.org").0, "Jane Doe jdoe");
        assert_eq!(reduce("Jane Doe via Example-List").0, "Jane Doe");
        assert_eq!(reduce("via Jane").0, "via Jane");
        let a = author("Robert Example");
        assert!(a.tokens.contains("bob") && a.tokens.contains("example"));
        assert_eq!(a.first_last, "re");
        assert!(a.initials.contains("re") && a.initials.contains("er"));
        assert!(a.has_prefix("rob") && a.has_suffix("ple") && !a.has_suffix("xyz"));
        assert_eq!(
            joined_runs("jean-luc o'neill x-"),
            vec!["jean-luc", "o'neill", "x"]
        );
        assert_eq!(letter_runs("ab1cd_e\u{b2}"), vec!["ab", "cd", "e\u{b2}"]);
        assert_eq!(nickname_groups("(a)(b(c)d)"), vec!["a", "c"]);
        assert_eq!(remove_bracketed("a(b)c[d]e<f>g(h"), "a c e g(h");
        // Addressees: the sender's own name is skipped.
        let names =
            parse_addressee_names(["Michael Example", "Bob Smith"], Some("Michael Example"))
                .expect("tokens");
        assert!(!names.tokens.contains("michael") && names.tokens.contains("bob"));
        assert!(parse_addressee_names(["Michael Example"], Some("Michael Example")).is_none());
        assert!(matches_addressee(
            "S\u{f8}ren,",
            parse_addressee_names(["Soren Example"], None).as_ref()
        ));
        assert!(!matches_addressee("Soren", None));
    }

    fn names_json(names: Option<&Names>) -> serde_json::Value {
        match names {
            None => serde_json::Value::Null,
            Some(n) => serde_json::json!({
                "tokens": n.tokens.iter().collect::<Vec<_>>(),
                "initials": n.initials.iter().collect::<Vec<_>>(),
                "first_last": n.first_last,
            }),
        }
    }

    /// Replays the JSON lines written by
    /// `research/work/port-rust/gen_names_diff.py` (path in the environment
    /// variable `NAMES_DIFF_FILE`); see `research/work/port-rust/phase3.txt`.
    #[test]
    #[ignore = "needs NAMES_DIFF_FILE from gen_names_diff.py"]
    fn differential() {
        use crate::greetings;
        use serde_json::{json, Value};
        let path = std::env::var("NAMES_DIFF_FILE").expect("NAMES_DIFF_FILE");
        let data = std::fs::read_to_string(path).expect("read NAMES_DIFF_FILE");
        let str_list = |v: &Value| -> Vec<String> {
            v.as_array()
                .map(|a| {
                    a.iter()
                        .map(|x| x.as_str().unwrap_or("").to_string())
                        .collect()
                })
                .unwrap_or_default()
        };
        let mut total = 0;
        let mut failures: std::collections::BTreeMap<String, (usize, Vec<String>)> =
            std::collections::BTreeMap::new();
        for line in data.lines() {
            let case: Value = serde_json::from_str(line).expect("json line");
            let f = case["f"].as_str().unwrap_or("");
            let a = &case["a"];
            let s0 = a[0].as_str().unwrap_or("");
            let opt = |v: &Value| v.as_str().map(str::to_string);
            let got: Value = match f {
                "fold" => json!(fold(s0)),
                "lower" => json!(pystr::lower(s0)),
                "upper" => json!(pystr::upper(s0)),
                "name_tokens" => json!(name_tokens(s0).into_iter().collect::<Vec<_>>()),
                "parse_author_name" => names_json(parse_author_name(Some(s0)).as_ref()),
                "reduce" => {
                    let (text, extra) = reduce(s0);
                    json!([text, extra])
                }
                "parse_addressee_names" => {
                    let names = str_list(&a[0]);
                    let author = opt(&a[1]);
                    names_json(
                        parse_addressee_names(names.iter().map(String::as_str), author.as_deref())
                            .as_ref(),
                    )
                }
                "is_author_name_line" | "is_author_initial_line" | "matches_author" => {
                    let author = parse_author_name(a[1].as_str());
                    match f {
                        "is_author_name_line" => json!(is_author_name_line(s0, author.as_ref())),
                        "is_author_initial_line" => {
                            json!(is_author_initial_line(s0, author.as_ref()))
                        }
                        _ => match author {
                            Some(author) => json!(matches_author(s0, &author)),
                            None => Value::Null,
                        },
                    }
                }
                "matches_addressee" => {
                    let names = str_list(&a[1]);
                    let parsed = parse_addressee_names(names.iter().map(String::as_str), None);
                    json!(matches_addressee(s0, parsed.as_ref()))
                }
                "is_opening_greeting" => {
                    let next = opt(&a[1]);
                    let parsed = if a[2].is_null() {
                        None
                    } else {
                        let names = str_list(&a[2]);
                        if names.is_empty() {
                            None
                        } else {
                            parse_addressee_names(names.iter().map(String::as_str), None)
                        }
                    };
                    json!(greetings::is_opening_greeting(
                        s0,
                        next.as_deref(),
                        parsed.as_ref()
                    ))
                }
                "split_terminator" => {
                    let (body, terminator) = greetings::split_terminator(s0);
                    json!([body, terminator])
                }
                "classify" => {
                    let words = str_list(&a[0]);
                    let names = str_list(&a[1]);
                    let parsed = if names.is_empty() {
                        None
                    } else {
                        parse_addressee_names(names.iter().map(String::as_str), None)
                    };
                    let refs: Vec<&str> = words.iter().map(String::as_str).collect();
                    let kinds: Vec<Value> = greetings::classify(&refs, parsed.as_ref())
                        .into_iter()
                        .map(|k| match k {
                            None => Value::Null,
                            Some(k) => json!(match k {
                                greetings::Kind::Title => "title",
                                greetings::Kind::Name => "name",
                                greetings::Kind::Prefix => "prefix",
                                greetings::Kind::Init => "init",
                                greetings::Kind::Group => "group",
                                greetings::Kind::Filler => "filler",
                                greetings::Kind::Email => "email",
                                greetings::Kind::Acro => "acro",
                                greetings::Kind::Cap => "cap",
                            }),
                        })
                        .collect();
                    json!(kinds)
                }
                "is_sentence" => {
                    let words = str_list(&a[0]);
                    let refs: Vec<&str> = words.iter().map(String::as_str).collect();
                    json!(greetings::is_sentence(&refs))
                }
                other => panic!("unknown function {other}"),
            };
            total += 1;
            if got != case["r"] {
                let entry = failures.entry(f.to_string()).or_default();
                entry.0 += 1;
                if entry.1.len() < 5 {
                    entry
                        .1
                        .push(format!("{a} -> python {} rust {got}", case["r"]));
                }
            }
        }
        for (f, (count, examples)) in &failures {
            eprintln!("{f}: {count} mismatches");
            for e in examples {
                eprintln!("    {e}");
            }
        }
        let mismatches: usize = failures.values().map(|v| v.0).sum();
        eprintln!("differential: {total} cases, {mismatches} mismatches");
        assert_eq!(mismatches, 0);
    }
}
