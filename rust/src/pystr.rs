//! Python `str` semantics used by the reference implementation.
//!
//! The reference relies on Python's definitions of whitespace (`str.isspace`,
//! used by `strip`, `rstrip` and argument-less `split`), `str.isdigit` and
//! `str.isupper`. Rust's `char` predicates and `str::trim` use different sets,
//! so the port uses these helpers everywhere instead. Lengths and indices are
//! in code points (`chars()`), as in Python.

use crate::unicode_tables::{PY_ISDIGIT, PY_ISUPPER, PY_SPACE, PY_WORD};

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

/// Python `str.lower()`.
pub(crate) fn lower(s: &str) -> String {
    s.to_lowercase()
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
    }
}
