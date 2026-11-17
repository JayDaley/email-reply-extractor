//! Python `str` semantics used by the reference implementation.
//!
//! The reference relies on Python's definitions of whitespace (`str.isspace`,
//! used by `strip`, `rstrip` and argument-less `split`), `str.isdigit`,
//! `str.isupper`, `str.islower` and `str.isalpha`. Rust's `char` predicates
//! and `str::trim` use different sets (or a different Unicode version), so
//! the port uses these helpers everywhere instead. Lengths and indices are in
//! code points (`chars()`), as in Python.

use crate::unicode_tables::{
    PY_CASED, PY_CASE_IGNORABLE, PY_DECIMAL, PY_ISALPHA, PY_ISDIGIT, PY_ISLOWER, PY_ISUPPER,
    PY_LOWER, PY_SPACE, PY_UPPER, PY_WORD,
};

fn in_ranges(table: &[(u32, u32)], c: char) -> bool {
    let cp = c as u32;
    table
        .binary_search_by(|&(lo, hi)| {
            if hi < cp {
                std::cmp::Ordering::Less
            } else if lo > cp {
                std::cmp::Ordering::Greater
            } else {
                std::cmp::Ordering::Equal
            }
        })
        .is_ok()
}

/// Python `str.isspace()` for one character (also `re` `\s`).
pub(crate) fn is_space(c: char) -> bool {
    in_ranges(PY_SPACE, c)
}

/// Python `re` `\w` for one character.
pub(crate) fn is_word(c: char) -> bool {
    in_ranges(PY_WORD, c)
}

/// Python `str.isdigit()` for one character.
pub(crate) fn is_digit(c: char) -> bool {
    in_ranges(PY_ISDIGIT, c)
}

/// Python `str.isupper()` for one character.
pub(crate) fn is_upper(c: char) -> bool {
    in_ranges(PY_ISUPPER, c)
}

/// Python `str.islower()` for one character.
pub(crate) fn is_lower(c: char) -> bool {
    in_ranges(PY_ISLOWER, c)
}

/// Python `str.isalpha()` for one character.
pub(crate) fn is_alpha(c: char) -> bool {
    in_ranges(PY_ISALPHA, c)
}

/// Python `str.strip()` with no argument.
pub(crate) fn strip(s: &str) -> &str {
    s.trim_matches(is_space)
}

/// Python `str.rstrip()` with no argument.
pub(crate) fn rstrip(s: &str) -> &str {
    s.trim_end_matches(is_space)
}

/// Python `str.split()` with no argument: runs of whitespace separate words,
/// and leading/trailing whitespace yields no empty words.
pub(crate) fn split_ws(s: &str) -> Vec<&str> {
    s.split(is_space).filter(|w| !w.is_empty()).collect()
}

/// `" ".join(s.split())`.
pub(crate) fn collapse_ws(s: &str) -> String {
    split_ws(s).join(" ")
}

/// True when `s.strip()` is non-empty.
pub(crate) fn has_content(s: &str) -> bool {
    s.chars().any(|c| !is_space(c))
}

/// Python `len(s)`: the number of code points.
pub(crate) fn len(s: &str) -> usize {
    s.chars().count()
}

/// Python `re` `\d` for one character (Unicode Nd).
pub(crate) fn is_decimal(c: char) -> bool {
    in_ranges(PY_DECIMAL, c)
}

/// Python `str.isalnum()` for one character (`\w` without `_`).
pub(crate) fn is_alnum(c: char) -> bool {
    c != '_' && is_word(c)
}

/// Python `str.isalpha()` for a string: non-empty and every character
/// alphabetic.
pub(crate) fn is_alpha_str(s: &str) -> bool {
    !s.is_empty() && s.chars().all(is_alpha)
}

/// Python `str.isalnum()` for a string.
pub(crate) fn is_alnum_str(s: &str) -> bool {
    !s.is_empty() && s.chars().all(is_alnum)
}

/// Python `str.strip(chars)` with an explicit character set.
pub(crate) fn strip_chars<'a>(s: &'a str, chars: &str) -> &'a str {
    s.trim_matches(|c| chars.contains(c))
}

/// Python `str.lstrip(chars)` with an explicit character set.
pub(crate) fn lstrip_chars<'a>(s: &'a str, chars: &str) -> &'a str {
    s.trim_start_matches(|c| chars.contains(c))
}

/// Python `str.rstrip(chars)` with an explicit character set.
pub(crate) fn rstrip_chars<'a>(s: &'a str, chars: &str) -> &'a str {
    s.trim_end_matches(|c| chars.contains(c))
}

/// Python `str.lstrip()` with no argument.
pub(crate) fn lstrip(s: &str) -> &str {
    s.trim_start_matches(is_space)
}

fn mapped(table: &'static [(char, &'static str)], c: char) -> Option<&'static str> {
    table
        .binary_search_by(|&(k, _)| k.cmp(&c))
        .ok()
        .map(|i| table[i].1)
}

/// Whether the capital sigma at `chars[i]` is word-final, by the rule of
/// CPython's `str.lower()`: the nearest character before it that is not
/// case-ignorable is cased, and the nearest one after it that is not
/// case-ignorable is absent or not cased.
fn final_sigma(chars: &[char], i: usize) -> bool {
    let cased = |c: char| in_ranges(PY_CASED, c);
    let ignorable = |c: char| in_ranges(PY_CASE_IGNORABLE, c);
    let before = chars[..i].iter().rev().find(|&&c| !ignorable(c));
    if !before.is_some_and(|&c| cased(c)) {
        return false;
    }
    let after = chars[i + 1..].iter().find(|&&c| !ignorable(c));
    !after.is_some_and(|&c| cased(c))
}

/// Python `str.lower()`: the full lowercase mapping of the Unicode version
/// the tables were generated from (Rust's `to_lowercase` follows the Rust
/// toolchain's Unicode version instead), with CPython's final-sigma rule.
pub(crate) fn lower(s: &str) -> String {
    if s.is_ascii() {
        return s.to_ascii_lowercase();
    }
    let chars: Vec<char> = s.chars().collect();
    let mut out = String::with_capacity(s.len());
    for (i, &c) in chars.iter().enumerate() {
        if c == '\u{3a3}' {
            out.push(if final_sigma(&chars, i) {
                '\u{3c2}'
            } else {
                '\u{3c3}'
            });
        } else if let Some(m) = mapped(PY_LOWER, c) {
            out.push_str(m);
        } else {
            out.push(c);
        }
    }
    out
}

/// Python `str.upper()`: the full uppercase mapping of the tables' Unicode
/// version.
pub(crate) fn upper(s: &str) -> String {
    if s.is_ascii() {
        return s.to_ascii_uppercase();
    }
    let mut out = String::with_capacity(s.len());
    for c in s.chars() {
        match mapped(PY_UPPER, c) {
            Some(m) => out.push_str(m),
            None => out.push(c),
        }
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn whitespace_set_is_pythons() {
        for c in [
            '\u{1c}', '\u{1d}', '\u{1e}', '\u{1f}', '\u{85}', '\u{a0}', '\u{3000}',
        ] {
            assert!(is_space(c), "{c:?}");
        }
        assert!(!is_space('\u{feff}'));
        assert!(!is_space('\u{180e}'));
        assert_eq!(strip("\u{1c} a \u{1f}"), "a");
        assert_eq!(split_ws("\u{1c}a\u{85}b "), vec!["a", "b"]);
    }

    #[test]
    fn predicates() {
        assert!(is_digit('\u{b2}'));
        assert!(!is_digit('\u{bd}'));
        assert!(is_upper('A'));
        assert!(!is_upper('\u{1c5}'));
        assert!(is_word('\u{b2}'));
        assert!(!is_word('\u{301}'));
        assert!(is_lower('\u{aa}'));
        assert!(is_lower('\u{2b0}'));
        assert!(!is_lower('\u{1c5}'));
        assert!(is_alpha('\u{1c5}'));
        assert!(!is_alpha('\u{301}'));
        assert!(!is_alpha('\u{903}'));
        assert!(is_alpha('\u{4e00}'));
        assert!(is_decimal('\u{663}'));
        assert!(!is_decimal('\u{b2}'));
        assert!(is_alnum('\u{b2}'));
        assert!(!is_alnum('_'));
    }

    #[test]
    fn case_mappings_are_pythons() {
        assert_eq!(lower("\u{130}"), "i\u{307}");
        assert_eq!(lower("\u{391}\u{3a3}"), "\u{3b1}\u{3c2}");
        assert_eq!(lower("\u{391}\u{3a3}."), "\u{3b1}\u{3c2}.");
        assert_eq!(lower("\u{3a3}"), "\u{3c3}");
        assert_eq!(lower("\u{391}\u{3a3}\u{391}"), "\u{3b1}\u{3c3}\u{3b1}");
        assert_eq!(lower("a\u{3a3}'b"), "a\u{3c3}'b");
        assert_eq!(lower("a\u{3a3}' "), "a\u{3c2}' ");
        assert_eq!(lower("ABC\u{c9}"), "abc\u{e9}");
        assert_eq!(upper("stra\u{df}e"), "STRASSE");
        assert_eq!(upper("\u{1c6}"), "\u{1c4}");
        assert_eq!(strip_chars("..a.b..", "."), "a.b");
        assert_eq!(lstrip_chars("-~ x", "-~ "), "x");
        assert_eq!(rstrip_chars("x -\u{2014}", "-\u{2014}"), "x ");
    }
}
