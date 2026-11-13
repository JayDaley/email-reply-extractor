//! Python `re` patterns compiled with Python's semantics.
//!
//! The reference's patterns are written in Python `re` syntax. [`Re`] takes a
//! pattern verbatim (as Python's `Pattern.pattern` prints it) and translates
//! the constructs whose meaning differs between Python and the Rust regex
//! crates:
//!
//! - `\s`, `\S`, `\w`, `\W`, `\d`, `\D` become explicit classes built from
//!   Python's own code point sets (`unicode_tables`), inside and outside
//!   character classes;
//! - `\b` becomes a lookaround over Python's `\w`, which requires
//!   `fancy-regex`;
//! - `$` (no `re.MULTILINE` anywhere in the reference) matches at the end of
//!   the string or before a final `\n`; it becomes `(?:\n?\z)`. It is only
//!   ever the last element of a pattern, so consuming the newline does not
//!   change which strings match;
//! - under `re.IGNORECASE`, Python also matches `İ` (U+0130) and `ı` (U+0131)
//!   for `i`/`I` (literals and classes covering them); Rust's simple case
//!   folding does not, so they are added explicitly. The remaining letters
//!   the reference uses fold identically in both;
//! - `\uXXXX` becomes `\x{XXXX}`.
//!
//! Patterns that use lookaround (or `\b`) are compiled with `fancy-regex`,
//! the rest with `regex`. Both use leftmost-first (backtracking-order)
//! semantics, as Python does. `fancy-regex` runs with no practical
//! backtracking limit, so a match never fails where Python's would succeed.

use crate::unicode_tables::{PY_DECIMAL, PY_SPACE, PY_WORD};
use std::fmt::Write as _;

/// `re.IGNORECASE`.
pub(crate) const IGNORECASE: u8 = 1;
/// `re.DOTALL`.
pub(crate) const DOTALL: u8 = 2;

enum Inner {
    Plain(regex::Regex),
    Fancy(fancy_regex::Regex),
}

/// A compiled Python pattern. Built either for `re.match` (anchored at the
/// start of the haystack) or for `re.search`.
pub(crate) struct Re {
    inner: Inner,
}

fn class_body(table: &[(u32, u32)]) -> String {
    let mut out = String::new();
    for &(lo, hi) in table {
        if lo == hi {
            let _ = write!(out, "\\x{{{lo:X}}}");
        } else {
            let _ = write!(out, "\\x{{{lo:X}}}-\\x{{{hi:X}}}");
        }
    }
    out
}

struct Classes {
    space: String,
    word: String,
    digit: String,
}

fn classes() -> &'static Classes {
    static CLASSES: std::sync::OnceLock<Classes> = std::sync::OnceLock::new();
    CLASSES.get_or_init(|| Classes {
        space: class_body(PY_SPACE),
        word: class_body(PY_WORD),
        digit: class_body(PY_DECIMAL),
    })
}

fn hex4(chars: &[char], i: usize) -> Option<u32> {
    if i + 4 > chars.len() {
        return None;
    }
    let s: String = chars[i..i + 4].iter().collect();
    u32::from_str_radix(&s, 16).ok()
}

/// Translate a Python pattern; returns the Rust pattern and whether it needs
/// `fancy-regex`.
pub(crate) fn translate(py: &str, flags: u8) -> (String, bool) {
    let cls = classes();
    let chars: Vec<char> = py.chars().collect();
    let n = chars.len();
    let mut out = String::new();
    let mut fancy = false;
    let mut ic = flags & IGNORECASE != 0;
    let mut ic_stack: Vec<bool> = Vec::new();
    let mut i = 0;
    while i < n {
        let c = chars[i];
        match c {
            '\\' => {
                let e = chars[i + 1];
                i += 2;
                // Python's category classes ignore re.IGNORECASE; Rust's
                // case-insensitive mode would close them under case folding
                // (adding U+0345 to \w), so they are scoped case-sensitive.
                let (open, close) = if ic { ("(?-i:", ")") } else { ("", "") };
                match e {
                    's' => {
                        let _ = write!(out, "{open}[{}]{close}", cls.space);
                    }
                    'S' => {
                        let _ = write!(out, "{open}[^{}]{close}", cls.space);
                    }
                    'w' => {
                        let _ = write!(out, "{open}[{}]{close}", cls.word);
                    }
                    'W' => {
                        let _ = write!(out, "{open}[^{}]{close}", cls.word);
                    }
                    'd' => {
                        let _ = write!(out, "{open}[{}]{close}", cls.digit);
                    }
                    'D' => {
                        let _ = write!(out, "{open}[^{}]{close}", cls.digit);
                    }
                    'b' => {
                        let w = &cls.word;
                        let _ = write!(out, "(?-i:(?:(?<=[{w}])(?![{w}])|(?<![{w}])(?=[{w}])))");
                        fancy = true;
                    }
                    'u' => {
                        let cp = hex4(&chars, i).expect("\\u escape");
                        i += 4;
                        let _ = write!(out, "\\x{{{cp:X}}}");
                    }
                    other => {
                        out.push('\\');
                        out.push(other);
                    }
                }
            }
            '[' => {
                i += 1;
                let negated = i < n && chars[i] == '^';
                if negated {
                    i += 1;
                }
                let mut body = String::new();
                let mut covers_i = false;
                let mut has_category = false;
                let mut letters: Vec<char> = Vec::new();
                let mut letter_range = false;
                let mut prev: Option<char> = None;
                while i < n && chars[i] != ']' {
                    let d = chars[i];
                    if d == '\\' {
                        let e = chars[i + 1];
                        i += 2;
                        prev = None;
                        has_category |= matches!(e, 's' | 'S' | 'w' | 'W' | 'd' | 'D');
                        match e {
                            's' => body.push_str(&cls.space),
                            'S' => {
                                let _ = write!(body, "[^{}]", cls.space);
                            }
                            'w' => body.push_str(&cls.word),
                            'W' => {
                                let _ = write!(body, "[^{}]", cls.word);
                            }
                            'd' => body.push_str(&cls.digit),
                            'D' => {
                                let _ = write!(body, "[^{}]", cls.digit);
                            }
                            'u' => {
                                let cp = hex4(&chars, i).expect("\\u escape");
                                i += 4;
                                let _ = write!(body, "\\x{{{cp:X}}}");
                                prev = char::from_u32(cp);
                            }
                            other => {
                                body.push('\\');
                                body.push(other);
                                prev = Some(other);
                            }
                        }
                        continue;
                    }
                    if d == '-' && i + 1 < n && chars[i + 1] != ']' {
                        if let Some(lo) = prev {
                            let hi = chars[i + 1];
                            if hi != '\\' && ((lo..=hi).contains(&'i') || (lo..=hi).contains(&'I'))
                            {
                                covers_i = true;
                            }
                            letter_range |= lo.is_alphabetic() || hi.is_alphabetic();
                        }
                    }
                    if d == 'i' || d == 'I' {
                        covers_i = true;
                    }
                    if d.is_alphabetic() {
                        letters.push(d);
                    }
                    match d {
                        '[' | '&' | '~' => {
                            body.push('\\');
                            body.push(d);
                        }
                        _ => body.push(d),
                    }
                    prev = Some(d);
                    i += 1;
                }
                i += 1; // ']'
                        // A class holding a category is scoped case-sensitive under
                        // re.IGNORECASE; its literal letters then get their case
                        // variants (as Python matches them) spelled out. Letter
                        // ranges and negation do not occur in such classes in the
                        // reference.
                let scoped = ic && has_category;
                if scoped {
                    assert!(!letter_range && !(negated && !letters.is_empty()), "{py:?}");
                    // Prepended, so that a trailing literal '-' stays literal.
                    let mut variants = String::new();
                    for &l in &letters {
                        variants.extend(l.to_lowercase().chain(l.to_uppercase()));
                        match l.to_ascii_lowercase() {
                            'k' => variants.push('\u{212a}'),
                            's' => variants.push('\u{17f}'),
                            _ => {}
                        }
                    }
                    body.insert_str(0, &variants);
                    out.push_str("(?-i:");
                }
                out.push('[');
                if negated {
                    out.push('^');
                }
                out.push_str(&body);
                if ic && !negated && covers_i {
                    out.push_str("\u{130}\u{131}");
                }
                out.push(']');
                if scoped {
                    out.push(')');
                }
            }
            '(' => {
                ic_stack.push(ic);
                let rest: String = chars[i..].iter().take(8).collect();
                let opener = if rest.starts_with("(?i:") {
                    ic = true;
                    "(?i:"
                } else if rest.starts_with("(?-i:") {
                    ic = false;
                    "(?-i:"
                } else if rest.starts_with("(?:") {
                    "(?:"
                } else if rest.starts_with("(?=") {
                    fancy = true;
                    "(?="
                } else if rest.starts_with("(?!") {
                    fancy = true;
                    "(?!"
                } else if rest.starts_with("(?<=") {
                    fancy = true;
                    "(?<="
                } else if rest.starts_with("(?<!") {
                    fancy = true;
                    "(?<!"
                } else if rest.starts_with("(?P<") {
                    let close = chars[i..].iter().position(|&x| x == '>').expect("group");
                    let name: String = chars[i..=i + close].iter().collect();
                    out.push_str(&name);
                    i += close + 1;
                    continue;
                } else if rest.starts_with("(?") {
                    panic!("unsupported group in pattern {py:?}");
                } else {
                    "("
                };
                out.push_str(opener);
                i += opener.chars().count();
            }
            ')' => {
                ic = ic_stack.pop().unwrap_or(flags & IGNORECASE != 0);
                out.push(')');
                i += 1;
            }
            '$' => {
                out.push_str("(?:\\n?\\z)");
                i += 1;
            }
            'i' | 'I' if ic => {
                out.push_str("[iI\u{130}\u{131}]");
                i += 1;
            }
            _ => {
                out.push(c);
                i += 1;
            }
        }
    }
    let mut prefix = String::new();
    if flags & IGNORECASE != 0 {
        prefix.push_str("(?i)");
    }
    if flags & DOTALL != 0 {
        prefix.push_str("(?s)");
    }
    (prefix + &out, fancy)
}

impl Re {
    fn build(py: &str, flags: u8, anchored: bool) -> Re {
        let (body, fancy) = translate(py, flags);
        let pattern = if anchored {
            // Flags first, so they apply inside the anchored group.
            let mut prefix_len = 0;
            while body[prefix_len..].starts_with("(?i)") || body[prefix_len..].starts_with("(?s)") {
                prefix_len += 4;
            }
            format!("{}\\A(?:{})", &body[..prefix_len], &body[prefix_len..])
        } else {
            body
        };
        let inner = if fancy {
            Inner::Fancy(
                fancy_regex::RegexBuilder::new(&pattern)
                    .backtrack_limit(usize::MAX)
                    .delegate_size_limit(1 << 26)
                    .build()
                    .unwrap_or_else(|e| panic!("bad pattern {py:?}: {e}")),
            )
        } else {
            Inner::Plain(
                regex::RegexBuilder::new(&pattern)
                    .size_limit(1 << 26)
                    .build()
                    .unwrap_or_else(|e| panic!("bad pattern {py:?}: {e}")),
            )
        };
        Re { inner }
    }

    /// A pattern used with Python's `Pattern.match` (anchored at the start).
    pub(crate) fn matcher(py: &str, flags: u8) -> Re {
        Re::build(py, flags, true)
    }

    /// A pattern used with Python's `Pattern.search`.
    pub(crate) fn searcher(py: &str, flags: u8) -> Re {
        Re::build(py, flags, false)
    }

    /// Whether the pattern matches (at the start for a matcher, anywhere for
    /// a searcher).
    pub(crate) fn is_match(&self, s: &str) -> bool {
        match &self.inner {
            Inner::Plain(r) => r.is_match(s),
            Inner::Fancy(r) => r.is_match(s).unwrap_or(false),
        }
    }

    /// The byte span of the first match.
    pub(crate) fn find(&self, s: &str) -> Option<(usize, usize)> {
        match &self.inner {
            Inner::Plain(r) => r.find(s).map(|m| (m.start(), m.end())),
            Inner::Fancy(r) => r.find(s).ok().flatten().map(|m| (m.start(), m.end())),
        }
    }

    /// The text of the named group in the first match.
    pub(crate) fn named<'t>(&self, s: &'t str, name: &str) -> Option<&'t str> {
        match &self.inner {
            Inner::Plain(r) => r.captures(s).and_then(|c| c.name(name)).map(|m| m.as_str()),
            Inner::Fancy(r) => r
                .captures(s)
                .ok()
                .flatten()
                .and_then(|c| c.name(name))
                .map(|m| m.as_str()),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn ignorecase_dotted_i() {
        let r = Re::matcher(r"^hi\b", IGNORECASE);
        assert!(r.is_match("H\u{130} there"));
        assert!(r.is_match("h\u{131}"));
        let r = Re::matcher(r"(?-i:[A-Z])x", IGNORECASE);
        assert!(!r.is_match("\u{130}x"));
        let r = Re::matcher(r"[A-Za-z]", IGNORECASE);
        assert!(r.is_match("\u{131}"));
        assert!(r.is_match("\u{212a}"));
    }

    #[test]
    fn categories_ignore_ignorecase() {
        // U+0345 folds to U+03B9 (a letter) but is not \w in Python, with or
        // without re.IGNORECASE.
        assert!(Re::matcher(r"^a\b", IGNORECASE).is_match("a\u{345}"));
        assert!(!Re::matcher(r"^\w", IGNORECASE).is_match("\u{345}"));
        assert!(!Re::matcher(r"^[\w.]", IGNORECASE).is_match("\u{345}"));
        assert!(Re::matcher(r"^[^\w\n]", IGNORECASE).is_match("\u{345}"));
        let r = Re::matcher(r"^[\d x-]+$", IGNORECASE);
        assert!(r.is_match("1 X x"));
        assert!(!r.is_match("\u{345}"));
    }

    #[test]
    fn dollar_allows_final_newline() {
        let r = Re::matcher(r"^ab[ \t]*$", 0);
        assert!(r.is_match("ab \n"));
        assert!(!r.is_match("ab \n\n"));
        assert!(!r.is_match("ab\r"));
    }

    #[test]
    fn word_boundary_uses_python_word() {
        // U+0301 (combining acute) is not \w in Python.
        let r = Re::matcher(r"^Le\b", 0);
        assert!(r.is_match("Le\u{301}a"));
        // U+00B2 (superscript two, No) is \w in Python.
        assert!(!r.is_match("Le\u{b2}"));
    }
}
