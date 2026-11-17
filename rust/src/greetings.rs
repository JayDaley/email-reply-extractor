//! Opening greetings for stage 2: the word lists and the per-line decision
//! (port of `_greetings.py`).
//!
//! [`is_opening_greeting`] decides whether the first non-blank line of a text
//! is a greeting. The Python module docstring states the rules (guard,
//! terminator, opening word, base rule, addressee names, greeting word, group
//! addressees, structural) and the word classes; this port follows it
//! function by function. Lengths are code points; `str.lower`/`str.upper`
//! are Python's ([`pystr::lower`], [`pystr::upper`]); `isupper`/`isalpha`/
//! `isalnum` use Python's tables. The patterns with `\b` or `(?![\w])` run on
//! `fancy-regex`; every line they read is at most [`MAX_GREETING_LEN`]
//! characters after stripping, except [`GREETING_RE`], which reads the raw
//! line but whose repetitions are bounded.

use crate::names::{fold, Names};
use crate::pyre::{Re, IGNORECASE};
use crate::pystr;
use std::collections::HashSet;
use std::sync::LazyLock;

// --- word lists ---------------------------------------------------------------------

/// Greeting words, grouped by language; the base list (a pattern fragment).
pub(crate) const GREETING_WORDS: &str = concat!(
    // English
    r"hi|hello|hey|dear|greetings|good[ \t]+(?:morning|afternoon|evening|day)",
    // German
    r"|hallo|guten[ \t]+(?:tag|morgen|abend)|liebe[rs]?|sehr[ \t]+geehrte[rs]?",
    // French / Spanish / Italian / Portuguese
    r"|bonjour|salut|ch(?:è|e)re?s?|hola|estimad[oa]s?|buenos[ \t]+d(?:í|i)as|ciao|ol(?:á|a)",
    // Dutch / Nordic
    r"|beste|geachte|hej|hoi",
    // Māori (common in NZ English mail)
    r"|kia[ \t]+ora|t(?:ē|e)n(?:ā|a)[ \t]+koe",
);

/// Further opening words, accepted only by the rules after the base rule.
pub(crate) const EXTRA_GREETING_WORDS: &str =
    r"hiya|howdy|moin|re-|good[ \t]+(?:morning|afternoon|evening|day)[ \t]+(?:all|everyone|folks)";

/// The base greeting line (used with `Pattern.match` on the raw line).
pub(crate) static GREETING_RE: LazyLock<Re> = LazyLock::new(|| {
    Re::matcher(
        &format!(r"^[ \t]*(?:{GREETING_WORDS})\b(?:[^.!?;:]{{0,60}}[,!.]|(?:[ \t]*[,!.])?)[ \t]*$"),
        IGNORECASE,
    )
});

/// The longest stripped line that can be a greeting.
pub(crate) const MAX_GREETING_LEN: usize = 120;

/// An opening greeting word (`Pattern.match`).
pub(crate) static GREETING_PREFIX_RE: LazyLock<Re> = LazyLock::new(|| {
    Re::matcher(
        &format!(r"^(?:{GREETING_WORDS}|{EXTRA_GREETING_WORDS})(?![\w])[ \t]*,?[ \t]*"),
        IGNORECASE,
    )
});

/// An opening thanks phrase (`Pattern.match`).
pub(crate) static THANKS_PREFIX_RE: LazyLock<Re> = LazyLock::new(|| {
    Re::matcher(
        r"^(?:many[ \t]+thanks|thanks[ \t]+(?:a[ \t]+lot|again)|thanks|thank[ \t]+you)(?![\w])[ \t]*,?[ \t]*",
        IGNORECASE,
    )
});

/// The separators between addressee items (used through [`item_split`]).
pub(crate) static ITEM_SPLIT_RE: LazyLock<Re> = LazyLock::new(|| {
    Re::searcher(
        r"[ \t]*(?:,|/|&|\+|;)[ \t]*|[ \t]+(?:and|or)[ \t]+",
        IGNORECASE,
    )
});

/// An address word (`Pattern.match`).
pub(crate) static ADDRESS_RE: LazyLock<Re> =
    LazyLock::new(|| Re::matcher(r"^<?[\w.+=-]+@[\w-]+(?:\.[\w-]+)+>?$", 0));

/// A contraction at the end of a word (`Pattern.search`).
pub(crate) static CONTRACTION_RE: LazyLock<Re> =
    LazyLock::new(|| Re::searcher(r"(?:n['’]t|['’](?:s|m|re|ve|ll|d))$", 0));

/// `_SIGNOFF_ABBREVIATION_RE.fullmatch(s)` (`[A-Za-z]/[A-Za-z]`): "V/R".
pub(crate) fn signoff_abbreviation_fullmatch(s: &str) -> bool {
    let chars: Vec<char> = s.chars().collect();
    chars.len() == 3
        && chars[0].is_ascii_alphabetic()
        && chars[1] == '/'
        && chars[2].is_ascii_alphabetic()
}

/// `_ITEM_SPLIT_RE.split(text)`.
pub(crate) fn item_split(text: &str) -> Vec<&str> {
    let mut pieces = Vec::new();
    let mut last = 0;
    let mut pos = 0;
    while pos <= text.len() {
        let Some((start, end)) = ITEM_SPLIT_RE.find_at(text, pos) else {
            break;
        };
        pieces.push(&text[last..start]);
        last = end;
        // The pattern cannot match the empty string.
        pos = end.max(start + 1);
    }
    pieces.push(&text[last..]);
    pieces
}

fn folded_set(words: &str) -> HashSet<String> {
    words
        .split(' ')
        .filter(|w| !w.is_empty())
        .map(fold)
        .collect()
}

/// Titles, folded.
pub(crate) static TITLES: LazyLock<HashSet<String>> = LazyLock::new(|| {
    folded_set("dr dr. prof prof. professor mr mr. ms ms. mrs mrs. mx sir madam doctor")
});

/// Group addressees ("all", "chairs", "WG"), folded.
pub(crate) static GROUP_WORDS: LazyLock<HashSet<String>> = LazyLock::new(|| {
    folded_set(concat!(
        "all everyone everybody folks colleagues team chairs chair cochairs co-chairs authors author",
        " author(s) co-authors coauthors contributors wg wgs rg working group members iana iesg iab",
        " irsg ads reviewers editors experts shepherd community people friends gentlemen ladies",
    ))
});

/// Words that are name-like only after a greeting word ("Hi there,"), folded.
pub(crate) static FILLER_WORDS: LazyLock<HashSet<String>> =
    LazyLock::new(|| folded_set("there again both guys y'all you"));

/// First words that make a short capitalized line a discourse marker or a
/// heading rather than an addressee, folded.
pub(crate) static HEADING_STOP_WORDS: LazyLock<HashSet<String>> = LazyLock::new(|| {
    folded_set(concat!(
        "thanks thank thx regards best cheers sincerely hi hello dear yes no ok okay agreed great",
        " note notes update hmm well so also fyi inline re regarding sorry first second third finally",
        " like minor major correction corrections idea ideas off per management done ack template",
        " specifically question questions addition additionally perhaps maybe sure excellent old new",
        " right actually errata reminder see target ps p.s btw imo psa pr tl proposed proposal thoughts",
        " indeed personally instead apparently exactly correct interesting noted approved agree just",
        " considering summary comment comments issue issues review evaluation subject replace below",
        " above attention however anyway meanwhile otherwise similarly likewise basically honestly",
        " frankly oh ah wow cool fine nice good he she they it this that we i you suggestion",
        " suggestions aside current text section background example examples answer response reply",
        " nit nits typo support strawman disclaimer ref almost to from as if fwiw iirc afaik afaict",
        " imho brilliant fantastic awesome wonderful perfect splendid absolutely definitely hooray",
        " congratulations congrats welcome bingo bravo oops whoops ouch yay yikes wfm lgtm nack ptal",
        " iiuc afair br ta tx tks tnx rgds kr viele grüße gruesse gruß mfg vg lg salutations",
    ))
});

/// Words that turn a greeting line into a sentence (compared lowercased, with
/// `.,!?:;()*` stripped from the edges); not folded.
pub(crate) const SENTENCE_WORDS: &[&str] = &[
    "i",
    "we",
    "my",
    "our",
    "this",
    "that",
    "these",
    "those",
    "it",
    "is",
    "are",
    "was",
    "were",
    "be",
    "have",
    "has",
    "had",
    "do",
    "does",
    "did",
    "can",
    "could",
    "would",
    "should",
    "thanks",
    "thank",
    "apologies",
    "sorry",
    "please",
    "nice",
    "good",
    "great",
    "just",
    "see",
    "let",
    "here",
    "glad",
    "welcome",
    "for",
    "some",
    "feedback",
    "an",
    "with",
    "about",
    "as",
    "if",
    "not",
    "no",
    "yes",
];

/// Group words that do not make a greeting on their own.
pub(crate) const GENERIC_GROUP_WORDS: &[&str] = &["group", "members", "people", "community"];

/// A word class of [`classify`] (Python's kind strings).
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum Kind {
    /// "title"
    Title,
    /// "name"
    Name,
    /// "prefix"
    Prefix,
    /// "init"
    Init,
    /// "group"
    Group,
    /// "filler"
    Filler,
    /// "email"
    Email,
    /// "acro"
    Acro,
    /// "cap"
    Cap,
}

/// Kinds that may make up an addressee-name line.
pub(crate) const NAME_KINDS: &[Kind] = &[
    Kind::Name,
    Kind::Prefix,
    Kind::Title,
    Kind::Init,
    Kind::Group,
];

/// Kinds that are name-like after a greeting word.
pub(crate) const NAME_LIKE_KINDS: &[Kind] = &[
    Kind::Name,
    Kind::Prefix,
    Kind::Title,
    Kind::Init,
    Kind::Group,
    Kind::Filler,
    Kind::Email,
    Kind::Acro,
    Kind::Cap,
];

// --- classification ---------------------------------------------------------------

/// True when a word turns the line into a sentence.
pub(crate) fn is_sentence(words: &[&str]) -> bool {
    for word in words {
        let lowered = pystr::lower(word);
        let folded = pystr::strip_chars(&lowered, ".,!?:;()*");
        if SENTENCE_WORDS.contains(&folded) {
            return true;
        }
        if CONTRACTION_RE.is_match(folded) && !FILLER_WORDS.contains(folded) {
            return true;
        }
    }
    false
}

/// The body and the terminator (`"-"`, `","`, `":"`, `"!"`, `"."` or `""`).
pub(crate) fn split_terminator(s: &str) -> (&str, &'static str) {
    let s = pystr::rstrip(s);
    if s.ends_with(['-', '\u{2014}', '\u{2013}']) {
        return (
            pystr::rstrip(pystr::rstrip_chars(s, "-\u{2014}\u{2013}")),
            "-",
        );
    }
    let terminator = match s.chars().last() {
        Some(',') => ",",
        Some(':') => ":",
        Some('!') => "!",
        Some('.') => ".",
        _ => return (s, ""),
    };
    (pystr::rstrip(&s[..s.len() - 1]), terminator)
}

/// A capitalized word: an uppercase first character, not "I", and only
/// letters and `.-'’`.
pub(crate) fn is_cap_word(word: &str) -> bool {
    let Some(first) = word.chars().next() else {
        return false;
    };
    if !pystr::is_upper(first) || word == "I" {
        return false;
    }
    word.chars()
        .all(|ch| pystr::is_alpha(ch) || matches!(ch, '.' | '-' | '\'' | '\u{2019}'))
}

/// The class of each word (first match wins; `None` for none).
pub(crate) fn classify(words: &[&str], addressees: Option<&Names>) -> Vec<Option<Kind>> {
    let mut kinds = Vec::with_capacity(words.len());
    for &word in words {
        let folded_word = fold(word);
        let folded = pystr::rstrip_chars(&folded_word, ".");
        let kind = if TITLES.contains(folded) || TITLES.contains(&format!("{folded}.")) {
            Some(Kind::Title)
        } else if addressees.is_some_and(|a| a.tokens.contains(folded)) {
            Some(Kind::Name)
        } else if addressees.is_some_and(|a| {
            pystr::len(folded) >= 3 && pystr::is_alpha_str(folded) && a.has_prefix(folded)
        }) {
            Some(Kind::Prefix)
        } else if pystr::len(folded) == 1 && pystr::is_alpha_str(folded) {
            Some(Kind::Init)
        } else if GROUP_WORDS.contains(folded) {
            Some(Kind::Group)
        } else if FILLER_WORDS.contains(folded) {
            Some(Kind::Filler)
        } else if ADDRESS_RE.is_match(word) {
            Some(Kind::Email)
        } else if (2..=12).contains(&pystr::len(word))
            && pystr::is_alnum_str(word)
            && pystr::upper(word) == word
            && word.chars().any(pystr::is_alpha)
        {
            Some(Kind::Acro)
        } else if is_cap_word(word) {
            Some(Kind::Cap)
        } else {
            None
        };
        kinds.push(kind);
    }
    kinds
}

fn all_in(kinds: &[Option<Kind>], allowed: &[Kind]) -> bool {
    kinds
        .iter()
        .all(|k| k.is_some_and(|k| allowed.contains(&k)))
}

/// True when `line`, the first non-blank line, is a greeting (see the Python
/// module docstring).
pub(crate) fn is_opening_greeting(
    line: &str,
    next_line: Option<&str>,
    addressees: Option<&Names>,
) -> bool {
    let s = pystr::strip(line);
    match s.chars().next() {
        None => return false,
        Some(first) if "+>|*#[(".contains(first) => return false,
        Some(_) if pystr::len(s) > MAX_GREETING_LEN => return false,
        Some(_) => {}
    }
    let (body, terminator) = split_terminator(s);
    let greeting = GREETING_PREFIX_RE.find(body);
    let thanks = if greeting.is_some() {
        None
    } else {
        THANKS_PREFIX_RE.find(body)
    };
    let opening = greeting.or(thanks);
    let rest = match opening {
        Some((_, end)) => &body[end..],
        None => body,
    };
    let words: Vec<&str> = item_split(rest)
        .into_iter()
        .flat_map(pystr::split_ws)
        .collect();
    let kinds = classify(&words, addressees);
    let n = words.len();
    let has = |k: Kind| kinds.contains(&Some(k));

    if GREETING_RE.is_match(line) {
        return !is_sentence(&pystr::split_ws(rest));
    }

    if addressees.is_some()
        && 0 < n
        && n <= 6
        && all_in(&kinds, NAME_KINDS)
        && (has(Kind::Name) || has(Kind::Prefix))
    {
        let thanks_only = thanks.is_some() && terminator != "," && terminator != ":";
        let bare_prefix =
            opening.is_none() && matches!(terminator, "." | "!" | "") && has(Kind::Prefix);
        if !thanks_only && !bare_prefix {
            return true;
        }
    }

    if greeting.is_some() {
        if n == 0 {
            return true;
        }
        if n <= 6 && all_in(&kinds, NAME_LIKE_KINDS) && kinds.iter().any(|&k| k != Some(Kind::Acro))
        {
            return true;
        }
    }
    if opening.is_none()
        && matches!(terminator, "," | ":" | "-" | "!")
        && 0 < n
        && n <= 6
        && all_in(&kinds, &[Kind::Group, Kind::Acro, Kind::Email])
        && (has(Kind::Group) || has(Kind::Email))
    {
        let groups = kinds.iter().filter(|&&k| k == Some(Kind::Group)).count();
        let lone_generic_group = groups == 1 && !has(Kind::Acro) && {
            let index = kinds
                .iter()
                .position(|&k| k == Some(Kind::Group))
                .unwrap_or(0);
            GENERIC_GROUP_WORDS.contains(&fold(words[index]).as_str())
        };
        if !lone_generic_group {
            return true;
        }
    }

    if opening.is_none() && matches!(terminator, "," | ":" | "-") && 0 < n && n <= 4 {
        let items = item_split(rest)
            .into_iter()
            .filter(|i| pystr::has_content(i))
            .count();
        if terminator != "," && items < 2 {
            return false;
        }
        if signoff_abbreviation_fullmatch(pystr::strip(rest)) {
            return false;
        }
        let capitalized = words
            .iter()
            .all(|w| is_cap_word(w) && !(pystr::len(w) >= 7 && pystr::lower(w).ends_with("ly")));
        let heading = words.iter().any(|w| {
            let folded = fold(w);
            HEADING_STOP_WORDS.contains(pystr::rstrip_chars(&folded, "."))
        });
        if capitalized && !heading {
            return match next_line {
                None => true,
                Some(next) => {
                    !pystr::has_content(next)
                        || pystr::lstrip(next)
                            .chars()
                            .next()
                            .is_some_and(pystr::is_upper)
                }
            };
        }
    }
    false
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::names::parse_addressee_names;

    const NAMES: &[&str] = &["Jane Doe", "Joseph Other"];

    fn greeting(line: &str, addressees: Option<&[&str]>) -> bool {
        let names = addressees.and_then(|a| parse_addressee_names(a.iter().copied(), None));
        is_opening_greeting(line, Some(""), names.as_ref())
    }

    #[test]
    fn opening_greeting_rules() {
        // test_cleaning.py: test_opening_greeting_rules.
        let soren: &[&str] = &["Soren Example"];
        let soren_accented: &[&str] = &["S\u{f8}ren Example"];
        for (line, addressees) in [
            ("Jane,", Some(NAMES)),
            ("Jane", Some(NAMES)),
            ("Jane Doe:", Some(NAMES)),
            ("Jane, Joe:", Some(NAMES)),
            ("Jane, Joe and all,", Some(NAMES)),
            ("Dr. Doe,", Some(NAMES)),
            ("Jan,", Some(NAMES)),
            ("Thanks Jane,", Some(NAMES)),
            ("S\u{f8}ren,", Some(soren)),
            ("Soren,", Some(soren_accented)),
            ("Hiya Jane,", None),
            ("Moin,", None),
            ("Howdy", None),
            ("Good morning everyone,", None),
            ("All:", None),
            ("WG chairs,", None),
            ("Stefano,", None),
            ("Jane, Joe:", None),
            ("Hi!", None),
            ("Hi,", None),
            ("WG, IETF,", None),
        ] {
            assert!(greeting(line, addressees), "{line}");
        }
    }

    #[test]
    fn lines_that_are_not_greetings() {
        // test_cleaning.py: test_opening_line_that_is_not_a_greeting_is_kept.
        for (line, addressees) in [
            ("Jan.", Some(NAMES)),
            ("Thanks Jane!", Some(NAMES)),
            ("++ Jane and Joe.", Some(NAMES)),
            ("Jane", None),
            ("Actually,", None),
            ("Finally,", None),
            ("Basically,", None),
            ("Inline:", None),
            ("People:", None),
            ("V/R,", None),
            ("Hi Jane, apologies for the late reply.", None),
            ("Hi, I'm writing about the draft.", None),
            ("", None),
        ] {
            assert!(!greeting(line, addressees), "{line}");
        }
        // test_acronym_test_uses_str_upper: "STRAßE" is not an acronym.
        assert!(!is_opening_greeting(
            "STRA\u{df}E, IETF,",
            Some("the body text follows."),
            None
        ));
        assert!(is_opening_greeting(
            "WG, IETF,",
            Some("the body text follows."),
            None
        ));
        // test_structural_greeting_needs_a_capitalized_next_line.
        assert!(!is_opening_greeting(
            "Stefano,",
            Some("the body text follows."),
            None
        ));
        assert!(is_opening_greeting(
            "Stefano,",
            Some("The body text follows."),
            None
        ));
        assert!(is_opening_greeting("Stefano,", None, None));
        // test_addressee_with_the_senders_own_name_is_not_evidence.
        let own = parse_addressee_names(["Michael Example", "Bob Smith"], Some("Michael Example"));
        assert!(!is_opening_greeting("Michael.", Some(""), own.as_ref()));
        let other = parse_addressee_names(["Michael Example"], Some("Bob Smith"));
        assert!(is_opening_greeting("Michael.", Some(""), other.as_ref()));
    }

    #[test]
    fn helpers() {
        assert_eq!(split_terminator("Jane --  "), ("Jane", "-"));
        assert_eq!(split_terminator("Jane\u{2014}"), ("Jane", "-"));
        assert_eq!(split_terminator("Jane :"), ("Jane", ":"));
        assert_eq!(split_terminator("Jane"), ("Jane", ""));
        assert_eq!(item_split("a, b and c/d"), vec!["a", "b", "c", "d"]);
        assert_eq!(item_split(""), vec![""]);
        assert!(is_cap_word("O'Neill") && !is_cap_word("I") && !is_cap_word("\u{1c5}a"));
        assert!(signoff_abbreviation_fullmatch("V/R") && !signoff_abbreviation_fullmatch("V/R."));
        assert!(is_sentence(&["don't"]) && !is_sentence(&["y'all"]) && is_sentence(&["(Thanks)"]));
        assert_eq!(
            classify(&["Dr.", "ß", "IETF", "x@y.org", "Stefano", "1"], None),
            vec![
                Some(Kind::Title),
                None,
                Some(Kind::Acro),
                Some(Kind::Email),
                Some(Kind::Cap),
                None
            ]
        );
    }
}
