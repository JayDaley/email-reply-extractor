//! Whitespace-tolerant equivalence between two derived texts.
//!
//! Port of the reference's `equivalence.py`. A generation bump can move the
//! exact bytes of derived text without changing its substance. A consumer
//! that re-derives stored text can use [`texts_equivalent`] to tell the two
//! apart and, for instance, keep an expensive downstream result when only
//! whitespace moved.
//!
//! The comparison: non-breaking spaces (U+00A0) unified to spaces, each
//! line's edge whitespace stripped (Python's `str.isspace()` set), blank
//! lines dropped. Everything else is compared exactly.

use crate::pystr;

/// Normalize `text` for whitespace-tolerant comparison.
///
/// Returns the non-blank lines of `text` (split on `\n` only), each with
/// U+00A0 replaced by a space and leading/trailing whitespace stripped.
///
/// ```
/// use email_reply_extractor::tolerant_lines;
/// assert_eq!(tolerant_lines("  a\u{a0}b \n\n c"), vec!["a b", "c"]);
/// ```
pub fn tolerant_lines(text: &str) -> Vec<String> {
    text.split('\n')
        .filter_map(|line| {
            let line = line.replace('\u{a0}', " ");
            let stripped = pystr::strip(&line);
            (!stripped.is_empty()).then(|| stripped.to_string())
        })
        .collect()
}

/// True when `a` and `b` differ at most in whitespace:
/// `tolerant_lines(a) == tolerant_lines(b)`.
///
/// ```
/// use email_reply_extractor::texts_equivalent;
/// assert!(texts_equivalent("Hi\n\nthere ", "Hi\nthere"));
/// assert!(!texts_equivalent("Hi there", "Hi\nthere"));
/// ```
pub fn texts_equivalent(a: &str, b: &str) -> bool {
    tolerant_lines(a) == tolerant_lines(b)
}
